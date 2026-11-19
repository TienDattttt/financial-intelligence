import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from threading import Barrier, Event, Thread

import polars as pl
import pytest
from filelock import ReadWriteLock, Timeout

from finintel.exceptions import ContractViolationError
from finintel.pipeline.runner import (
    Runner,
    RunRequest,
    RunStatus,
    Stage,
    StepContext,
    StepOutcome,
)
from finintel.pipeline.state_store import StateStore
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import (
    ISSUERS_SCHEMA,
    UNMAPPED_METRICS_SCHEMA,
    canonicalize_financial_facts,
    canonicalize_table,
)
from finintel.storage.snapshot_publisher import SnapshotPublisher


def _facts() -> pl.DataFrame:
    common = {
        "issuer_id": ["FPT"] * 4,
        "statement_scope": ["CONSOLIDATED", "CONSOLIDATED", "SEPARATE", "CONSOLIDATED"],
        "period_type": ["QUARTER"] * 4,
        "fiscal_year_start_date": [date(2025, 1, 1)] * 4,
        "period_end_date": [date(2025, 12, 31)] * 4,
        "metric_id": [
            "IS_NET_INCOME",
            "IS_NET_INCOME",
            "IS_NET_INCOME",
            "IS_NET_REVENUE",
        ],
        "taxonomy_version": ["VAS_TT200_2014"] * 4,
        "revision_no": [1, 2, 1, 1],
        "filing_id": [
            "filing-original",
            "filing-audited",
            "filing-separate",
            "filing-estimate",
        ],
        "fiscal_year": [2025] * 4,
        "fiscal_quarter": [4] * 4,
        "period_start_date": [date(2025, 10, 1)] * 4,
        "published_at": [date(2026, 1, 25), date(2026, 3, 28), date(2026, 1, 26), None],
        "published_at_basis": [
            "EXACT_FILING",
            "EXACT_FILING",
            "EXACT_FILING",
            "STATUTORY_LAG",
        ],
        "effective_available_at": [
            date(2026, 1, 25),
            date(2026, 3, 28),
            date(2026, 1, 26),
            date(2026, 1, 30),
        ],
        "revision_reason": ["ORIGINAL", "AUDITED_UPDATE", "ORIGINAL", "ORIGINAL"],
        "derivation_method": ["REPORTED_DIRECTLY"] * 4,
        "audit_status": ["UNAUDITED", "AUDITED", "UNAUDITED", "UNAUDITED"],
        "audit_opinion": ["UNKNOWN", "UNMODIFIED", "UNKNOWN", "UNKNOWN"],
        "value": [
            100_000_000_000.0,
            80_000_000_000.0,
            50_000_000_000.0,
            7_000_000_000.0,
        ],
        "ttm_value": [None] * 4,
        "computation_note": [None] * 4,
    }
    return pl.DataFrame(common)


def test_point_in_time_revisions(tmp_path: Path) -> None:
    path = tmp_path / "financial_facts.parquet"
    _facts().write_parquet(path)
    repository = ParquetRepository(path)

    before_audit = repository.get_facts_as_of(date(2026, 2, 15))
    after_audit = repository.get_facts_as_of(date(2026, 4, 1))
    assert before_audit.filter(pl.col("metric_id") == "IS_NET_INCOME").filter(
        pl.col("statement_scope") == "CONSOLIDATED"
    )["value"].to_list() == [100_000_000_000.0]
    assert after_audit.filter(pl.col("metric_id") == "IS_NET_INCOME").filter(
        pl.col("statement_scope") == "CONSOLIDATED"
    )["value"].to_list() == [80_000_000_000.0]
    assert before_audit.filter(pl.col("statement_scope") == "SEPARATE")[
        "value"
    ].to_list() == [50_000_000_000.0]
    assert before_audit.filter(pl.col("metric_id") == "IS_NET_REVENUE")[
        "value"
    ].to_list() == [7_000_000_000.0]
    assert after_audit.filter(pl.col("metric_id") == "IS_NET_REVENUE")[
        "value"
    ].to_list() == [7_000_000_000.0]

    exact = repository.get_facts_as_of(
        date(2026, 2, 15), require_exact_filing_date=True
    )
    assert set(exact["filing_id"].to_list()) == {
        "filing-original",
        "filing-separate",
    }


def test_atomic_publish_and_garbage_collection(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    publisher = SnapshotPublisher(root)
    for index in range(1, 5):
        publisher.publish(f"snap_{index}", {"latest_facts.parquet": _facts().head(1)})
    assert sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and path.name.startswith("snap_")
    ) == ["snap_3", "snap_4"]
    assert not list(root.glob("_tmp_*"))


@pytest.mark.parametrize("explicit_file", [False, True])
def test_reader_can_read_while_publishing(tmp_path: Path, explicit_file: bool) -> None:
    facts = _facts().head(1)
    root = tmp_path / "data" / "published" / "snapshots"
    # The legacy project-root layout is used until the direct snapshot root exists.
    (tmp_path / "published").mkdir()
    publisher = SnapshotPublisher(root)
    publisher.publish("snap_1", {"financial_facts.parquet": facts})
    query_started = Event()
    release_query = Event()
    as_of = date(2026, 2, 15)

    class CoordinatedRepository(ParquetRepository):
        def _query_facts(
            self, path: Path, as_of_date: date, require_exact_filing_date: bool
        ) -> pl.DataFrame:
            query_started.set()
            assert release_query.wait(timeout=5)
            return super()._query_facts(path, as_of_date, require_exact_filing_date)

    path = root / "snap_1" / "financial_facts.parquet" if explicit_file else tmp_path
    repository = CoordinatedRepository(path)

    def publish_new_snapshots() -> None:
        updated = facts.with_columns(pl.lit(1.0).alias("value"))
        publisher.publish("snap_2", {"financial_facts.parquet": updated})
        publisher.publish("snap_3", {"financial_facts.parquet": updated})

    with ThreadPoolExecutor(max_workers=2) as executor:
        read = executor.submit(repository.get_facts_as_of, as_of)
        try:
            assert query_started.wait(timeout=5)
            rw_lock = ReadWriteLock(
                root.parent / ".snapshots.publish.lock", is_singleton=False
            )
            try:
                with pytest.raises(Timeout), rw_lock.write_lock(timeout=0):
                    pytest.fail("The reader must hold the publisher and GC lock")
            finally:
                rw_lock.close()
            publish = executor.submit(publish_new_snapshots)
        finally:
            release_query.set()
        assert read.result(timeout=5)["value"].to_list() == [100_000_000_000.0]
        publish.result(timeout=5)
    assert not (root / "snap_1").exists()
    if explicit_file:
        with pytest.raises(FileNotFoundError):
            repository.get_facts_as_of(as_of)
    else:
        assert repository.get_facts_as_of(as_of)["value"].to_list() == [1.0]


def test_storage_contract_rejects_duplicates_cast_failures_and_partial_snapshots(
    tmp_path: Path,
) -> None:
    duplicate = _facts().head(1).vstack(_facts().head(1))
    with pytest.raises(ContractViolationError):
        canonicalize_financial_facts(duplicate)

    malformed = (
        _facts()
        .head(1)
        .with_columns(pl.lit("not-a-date").alias("effective_available_at"))
    )
    with pytest.raises(ContractViolationError):
        canonicalize_financial_facts(malformed)

    missing_availability = (
        _facts()
        .head(1)
        .with_columns(pl.lit(None, dtype=pl.Date).alias("effective_available_at"))
    )
    with pytest.raises(ContractViolationError):
        canonicalize_financial_facts(missing_availability)

    non_finite = (
        _facts()
        .head(1)
        .with_columns(
            pl.lit(float("nan")).alias("value"), pl.lit(float("inf")).alias("ttm_value")
        )
    )
    with pytest.raises(ContractViolationError):
        canonicalize_financial_facts(non_finite)

    invalid_enum = (
        _facts().head(1).with_columns(pl.lit("INVALID_SCOPE").alias("statement_scope"))
    )
    with pytest.raises(ContractViolationError):
        canonicalize_financial_facts(invalid_enum)

    invalid_path = tmp_path / "invalid.parquet"
    pl.DataFrame({"value": [1.0]}).write_parquet(invalid_path)
    publisher = SnapshotPublisher(tmp_path / "snapshots")
    with pytest.raises(ContractViolationError):
        publisher.publish("snap_invalid", {"financial_facts.parquet": invalid_path})
    assert not (tmp_path / "snapshots" / "snap_invalid").exists()

    class FailingPublisher(SnapshotPublisher):
        def _write_table(
            self, staging: Path, filename: str, table: pl.DataFrame | Path
        ) -> None:
            super()._write_table(staging, filename, table)
            raise OSError("injected staging failure")

    failing = FailingPublisher(tmp_path / "failed-snapshots")
    with pytest.raises(OSError, match="injected staging failure"):
        failing.publish("snap_failed", {"latest_facts.parquet": _facts().head(1)})
    assert not (tmp_path / "failed-snapshots" / "snap_failed").exists()
    assert not list((tmp_path / "failed-snapshots").glob("_tmp_*"))


def test_gc_uses_state_store_and_rejects_failed_promotion(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "metadata" / "state.sqlite")
    store.initialize()
    root = tmp_path / "snapshots"
    publisher = SnapshotPublisher(root, state_store=store)
    for index in range(1, 4):
        run_id = f"run-{index}"
        store.start_run(run_id, "manual", {}, None)
        publisher.publish(
            f"snap_{index}",
            {"latest_facts.parquet": _facts().head(1)},
            run_id=run_id,
        )
    assert sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and path.name.startswith("snap_")
    ) == ["snap_2", "snap_3"]

    uncommitted = root / "snap_uncommitted"
    uncommitted.mkdir()
    (uncommitted / "marker").write_text("uncommitted", encoding="utf-8")
    publisher.collect_garbage()
    assert not uncommitted.exists()

    failed_run = "failed-run"
    store.start_run(failed_run, "manual", {}, None)
    store.finish_run(failed_run, "FAILED", "snap_failed")
    with pytest.raises(ValueError, match="not publishable"):
        publisher.publish(
            "snap_failed",
            {"latest_facts.parquet": _facts().head(1)},
            run_id=failed_run,
        )
    assert not (root / "snap_failed").exists()
    assert not list(root.glob("_tmp_*"))
    active = store.active_snapshot()
    assert active is not None and active["snapshot_id"] == "snap_3"


@pytest.mark.parametrize("late_status", ["FAILED", "SUCCEEDED"])
def test_gc_keeps_publication_order_after_late_run_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, late_status: str
) -> None:
    store = StateStore(tmp_path / "metadata" / "state.sqlite")
    store.initialize()
    root = tmp_path / "snapshots"

    class PublisherWithoutGC(SnapshotPublisher):
        def _collect_garbage_unlocked(self) -> None:
            pass

    publisher = PublisherWithoutGC(root, state_store=store)
    for index in (1, 2, 3):
        store.start_run(f"run-{index}", "manual", {}, None)
    # Publication order follows their commits even with ties or a clock correction.
    times = iter(
        (
            "2026-01-01T03:00:00+00:00",
            "2026-01-01T02:00:00+00:00",
            "2026-01-01T02:00:00+00:00",
            "2026-01-01T04:00:00+00:00",
        )
    )
    monkeypatch.setattr("finintel.pipeline.state_store._now", lambda: next(times))
    tables = {"latest_facts.parquet": _facts().head(1)}
    for index in (1, 2, 3):
        publisher.publish(f"snap_{index}", tables, run_id=f"run-{index}")

    store.finish_run(
        "run-1", late_status, "snap_1"
    )  # Finalize a previously committed run.
    os.utime(root / "snap_1", (2_000_000_000, 2_000_000_000))
    SnapshotPublisher(root, state_store=store).collect_garbage()

    assert sorted(path.name for path in root.glob("snap_*")) == ["snap_2", "snap_3"]
    assert store.published_snapshot_ids() == ["snap_3", "snap_2", "snap_1"]
    active = store.active_snapshot()
    assert active is not None and active["snapshot_id"] == "snap_3"
    run = store.get_run("run-1")
    assert run is not None and run["status"] == late_status
    assert run["finished_at"] == "2026-01-01T04:00:00+00:00"


def test_state_store_migrates_legacy_publication_order(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    for index in (1, 2, 3):
        store.start_run(f"run-{index}", "manual", {}, None)
        store.publish_snapshot(f"run-{index}", f"snap_{index}")
    with store.connection() as conn:
        conn.execute("ALTER TABLE pipeline_runs DROP COLUMN snapshot_publish_order")
        conn.execute(
            "UPDATE pipeline_runs SET published_snapshot_id = NULL WHERE run_id = 'run-3'"
        )

    store.initialize()
    store.initialize()
    store.finish_run("run-1", "FAILED")
    store.start_run("run-4", "manual", {}, None)
    store.publish_snapshot("run-4", "snap_4")

    assert store.published_snapshot_ids() == ["snap_4", "snap_3", "snap_2", "snap_1"]
    active_before_upgrade = store.get_run("run-3")
    assert active_before_upgrade is not None
    assert active_before_upgrade["published_snapshot_id"] == "snap_3"


def test_state_store_serializes_concurrent_snapshot_promotion(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "metadata" / "state.sqlite")
    store.initialize()
    store.start_run("run", "manual", {}, None)
    barrier = Barrier(2)
    errors: list[BaseException] = []

    def promote(snapshot_id: str) -> None:
        try:
            barrier.wait(timeout=5)
            store.publish_snapshot("run", snapshot_id)
        except BaseException as exc:
            errors.append(exc)

    first = Thread(target=promote, args=("snap_a",))
    second = Thread(target=promote, args=("snap_b",))
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)
    assert not first.is_alive()
    assert not second.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert store.active_snapshot() is not None


def test_runner_preserves_snapshot_after_post_promotion_gc_failure(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    root = data_root / "published" / "snapshots"
    store = StateStore(data_root / "metadata" / "state.sqlite")

    class FailingPublisher(SnapshotPublisher):
        def _collect_garbage_unlocked(self) -> None:
            raise OSError("injected garbage collection failure")

    publisher = FailingPublisher(root, state_store=store)
    tables = {"financial_facts.parquet": _facts().head(1)}

    def publish(context: StepContext) -> StepOutcome:
        destination = publisher.publish("snap_committed", tables, run_id=context.run_id)
        return StepOutcome(snapshot_id=destination.name)

    result = Runner(store, {Stage.PUBLISH: publish}, min_free_bytes=0).run(
        RunRequest(source="file", stage=Stage.PUBLISH)
    )
    assert result.status == RunStatus.FAILED
    assert result.run_id is not None
    run = store.get_run(result.run_id)
    assert run is not None and run["status"] == RunStatus.FAILED
    assert run["published_snapshot_id"] == "snap_committed"
    assert store.published_snapshot_ids() == ["snap_committed"]
    active = store.active_snapshot()
    assert active is not None and active["snapshot_id"] == run["published_snapshot_id"]
    assert active["run_id"] == result.run_id

    healthy_publisher = SnapshotPublisher(root, state_store=store)
    healthy_publisher.collect_garbage()
    repository = ParquetRepository(data_root)
    as_of = date(2026, 2, 15)
    assert repository.get_facts_as_of(as_of)["value"].to_list() == [100_000_000_000.0]

    store.start_run("next", "manual", {}, None)
    healthy_publisher.publish(
        "snap_next",
        {
            "financial_facts.parquet": _facts()
            .head(1)
            .with_columns(pl.lit(1.0).alias("value"))
        },
        run_id="next",
    )
    assert (root / "snap_committed" / "financial_facts.parquet").is_file()
    assert repository.get_facts_as_of(as_of)["value"].to_list() == [1.0]


@pytest.mark.parametrize("finish_run", [False, True])
def test_gc_retains_legacy_snapshot_after_next_promotion(
    tmp_path: Path,
    finish_run: bool,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    store.start_run("run", "manual", {}, None)
    root = tmp_path / "snapshots"
    publisher = SnapshotPublisher(root, state_store=store)
    tables = {"financial_facts.parquet": _facts().head(1)}
    published = publisher.publish("snap_committed", tables, run_id="run")
    with store.connection() as conn:
        conn.execute(
            "UPDATE pipeline_runs SET status = 'FAILED', published_snapshot_id = NULL WHERE run_id = 'run'"
        )

    publisher.collect_garbage()
    assert (published / "financial_facts.parquet").is_file()

    store.start_run("next", "manual", {}, None)
    if finish_run:
        SnapshotPublisher(root).publish("snap_next", tables)
        store.finish_run("next", "SUCCEEDED", "snap_next")
        publisher.collect_garbage()
    else:
        publisher.publish("snap_next", tables, run_id="next")

    assert (published / "financial_facts.parquet").is_file()
    assert store.published_snapshot_ids() == ["snap_next", "snap_committed"]
    legacy_run = store.get_run("run")
    assert legacy_run is not None and legacy_run["status"] == "FAILED"
    assert legacy_run["published_snapshot_id"] == "snap_committed"

    store.start_run("latest", "manual", {}, None)
    publisher.publish("snap_latest", tables, run_id="latest")
    assert not published.exists()


def test_repository_discovery_ignores_staging_without_state(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    snapshot_root = data_root / "published" / "snapshots"
    (snapshot_root / "_tmp_snap_2").mkdir(parents=True)
    (snapshot_root / "snap_1").mkdir()
    _facts().head(1).write_parquet(
        snapshot_root / "_tmp_snap_2" / "financial_facts.parquet"
    )
    _facts().tail(1).write_parquet(snapshot_root / "snap_1" / "financial_facts.parquet")
    repository = ParquetRepository(data_root)
    assert (
        repository.path
        == (snapshot_root / "snap_1" / "financial_facts.parquet").resolve()
    )


@pytest.mark.parametrize("invalid_state", ["corrupt", "empty", "directory"])
def test_repository_surfaces_sqlite_lookup_errors(
    tmp_path: Path, invalid_state: str
) -> None:
    root = tmp_path / "published" / "snapshots"
    SnapshotPublisher(root).publish("snap_1", {"financial_facts.parquet": _facts()})
    state_path = tmp_path / "metadata" / "state.sqlite"
    state_path.parent.mkdir()
    if invalid_state == "directory":
        state_path.mkdir()
    else:
        state_path.write_bytes(b"invalid SQLite" if invalid_state == "corrupt" else b"")

    with pytest.raises(ContractViolationError, match="Unable to read active snapshot"):
        ParquetRepository(tmp_path).get_facts_as_of(date(2026, 2, 15))


@pytest.mark.parametrize("has_active_snapshot", [False, True])
def test_repository_uses_curated_facts_when_state_has_no_active_facts(
    tmp_path: Path,
    has_active_snapshot: bool,
) -> None:
    root = tmp_path / "published" / "snapshots"
    store = StateStore(tmp_path / "metadata" / "state.sqlite")
    store.initialize()
    facts = _facts().head(1)
    if has_active_snapshot:
        store.start_run("active", "manual", {}, None)
        SnapshotPublisher(root, state_store=store).publish(
            "snap_active", {"latest_facts.parquet": facts}, run_id="active"
        )
    SnapshotPublisher(root).publish(
        "snap_uncommitted", {"financial_facts.parquet": facts}
    )
    curated = tmp_path / "curated"
    curated.mkdir()
    facts.with_columns(pl.lit(1.0).alias("value")).write_parquet(
        curated / "financial_facts.parquet"
    )

    repository = ParquetRepository(tmp_path)
    result = repository.get_facts_as_of(date(2026, 2, 15))

    assert result["value"].to_list() == [1.0]
    if has_active_snapshot:
        assert repository.get_latest_facts()["value"].to_list() == [100_000_000_000.0]


@pytest.mark.parametrize("has_snapshot_root", [False, True])
def test_repository_rejects_missing_active_snapshot(
    tmp_path: Path, has_snapshot_root: bool
) -> None:
    root = tmp_path / "published" / "snapshots"
    if has_snapshot_root:
        root.mkdir(parents=True)
    store = StateStore(tmp_path / "metadata" / "state.sqlite")
    store.initialize()
    store.start_run("active", "manual", {}, None)
    store.publish_snapshot("active", "snap_missing")
    curated = tmp_path / "curated"
    curated.mkdir()
    _facts().head(1).write_parquet(curated / "financial_facts.parquet")
    _facts().head(1).write_parquet(curated / "latest_facts.parquet")
    repository = ParquetRepository(tmp_path)

    with pytest.raises(
        ContractViolationError, match="Active snapshot directory is missing"
    ):
        repository.get_facts_as_of(date(2026, 2, 15))
    with pytest.raises(
        ContractViolationError, match="Active snapshot directory is missing"
    ):
        repository.get_latest_facts()


def _issuers() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issuer_id": ["FPT", "VNM"],
            "ticker": ["FPT", "VNM"],
            "exchange": ["HOSE", "DELISTED"],
            "company_name_vn": ["CTCP FPT", "CTCP Sua Viet Nam"],
            "accounting_regime": ["CORPORATE", "CORPORATE"],
            "icb_code_level2": ["9500", "3500"],
            "icb_name_level2": ["Cong nghe", "Thuc pham"],
            "icb_code_level3": ["9530", None],
            "icb_name_level3": ["Phan mem", None],
            "industry_snapshot_date": [date(2026, 1, 1), date(2026, 1, 1)],
            "delisted_date": [None, date(2025, 12, 31)],
        }
    )


def test_schema_enforces_domain_temporal_and_issuer_unmapped_invariants(
    tmp_path: Path,
) -> None:
    base = _facts().head(1)
    for bad_revision in (0, -3):
        with pytest.raises(ContractViolationError, match="revision_no >= 1"):
            canonicalize_financial_facts(
                base.with_columns(pl.lit(bad_revision).alias("revision_no"))
            )
    for bad_quarter in (0, 5, -1, None):
        with pytest.raises(ContractViolationError, match="invalid fiscal_quarter"):
            canonicalize_financial_facts(
                base.with_columns(
                    pl.lit(bad_quarter, dtype=pl.Int8).alias("fiscal_quarter")
                )
            )
    with pytest.raises(ContractViolationError, match="invalid fiscal_quarter"):
        canonicalize_financial_facts(
            base.with_columns(
                pl.lit("ANNUAL").alias("period_type"),
                pl.lit(4, dtype=pl.Int8).alias("fiscal_quarter"),
            )
        )
    with pytest.raises(ContractViolationError, match="out-of-order"):
        canonicalize_financial_facts(
            base.with_columns(
                pl.lit(date(2026, 1, 2)).alias("period_start_date"),
                pl.lit(date(2025, 12, 31)).alias("period_end_date"),
            )
        )
    with pytest.raises(ContractViolationError, match="out-of-order"):
        canonicalize_financial_facts(
            base.with_columns(
                pl.lit(date(2025, 12, 30)).alias("effective_available_at")
            )
        )
    with pytest.raises(ContractViolationError, match="inconsistent published_at"):
        canonicalize_financial_facts(
            base.with_columns(
                pl.lit("EXACT_FILING").alias("published_at_basis"),
                pl.lit(None, dtype=pl.Date).alias("published_at"),
            )
        )
    with pytest.raises(ContractViolationError, match="inconsistent published_at"):
        canonicalize_financial_facts(
            base.with_columns(
                pl.lit(date(2026, 3, 28)).alias("published_at"),
                pl.lit(date(2026, 1, 25)).alias("effective_available_at"),
            )
        )
    with pytest.raises(ContractViolationError, match="inconsistent published_at"):
        canonicalize_financial_facts(
            base.with_columns(
                pl.lit("STATUTORY_LAG").alias("published_at_basis"),
                pl.lit(date(2026, 1, 25)).alias("published_at"),
            )
        )

    valid_issuers = canonicalize_table(_issuers(), ISSUERS_SCHEMA)
    assert valid_issuers["delisted_date"].to_list() == [None, date(2025, 12, 31)]
    with pytest.raises(ContractViolationError, match="duplicate issuer_id"):
        canonicalize_table(
            _issuers().head(1).vstack(_issuers().head(1)), ISSUERS_SCHEMA
        )
    with pytest.raises(ContractViolationError, match="non-empty issuer_id"):
        canonicalize_table(
            _issuers().head(1).with_columns(pl.lit("   ").alias("ticker")),
            ISSUERS_SCHEMA,
        )
    with pytest.raises(ContractViolationError, match="invalid categorical values"):
        canonicalize_table(
            _issuers().head(1).with_columns(pl.lit("OTC").alias("exchange")),
            ISSUERS_SCHEMA,
        )

    unmapped = pl.DataFrame(
        {
            "issuer_id": ["FPT"],
            "filing_id": ["filing-1"],
            "raw_metric_key": ["999"],
            "raw_value": [None],
            "statement_scope": ["CONSOLIDATED"],
            "period_type": ["QUARTER"],
            "period_end_date": [date(2025, 12, 31)],
            "effective_available_at": [date(2026, 1, 25)],
        }
    )
    assert canonicalize_table(unmapped, UNMAPPED_METRICS_SCHEMA).height == 1
    with pytest.raises(ContractViolationError, match="invalid categorical values"):
        canonicalize_table(
            unmapped.with_columns(pl.lit("BAD").alias("period_type")),
            UNMAPPED_METRICS_SCHEMA,
        )

    published = SnapshotPublisher(tmp_path / "snapshots").publish(
        "snap_issuers",
        {"issuers.parquet": _issuers(), "unmapped_metrics.parquet": unmapped},
    )
    repo = ParquetRepository(published)
    assert repo.read_table("issuers.parquet")["issuer_id"].to_list() == ["FPT", "VNM"]


def test_canonicalize_drops_superseded_cumulative_ytd_quarter_without_masking_true_duplicates() -> (
    None
):
    cumulative_q2 = (
        _facts()
        .head(1)
        .with_columns(
            pl.lit(2, dtype=pl.Int8).alias("fiscal_quarter"),
            pl.lit(date(2025, 1, 1)).alias("period_start_date"),
            pl.lit(date(2025, 6, 30)).alias("period_end_date"),
            pl.lit("REPORTED_DIRECTLY").alias("derivation_method"),
            pl.lit(260.0).alias("value"),
        )
    )
    derived_q2 = cumulative_q2.with_columns(
        pl.lit(date(2025, 4, 1)).alias("period_start_date"),
        pl.lit("DERIVED_YTD_DIFF").alias("derivation_method"),
        pl.lit(160.0).alias("value"),
    )
    resolved = canonicalize_financial_facts(pl.concat([cumulative_q2, derived_q2]))
    assert resolved.height == 1
    assert resolved.select("derivation_method", "value", "period_start_date").row(
        0
    ) == (
        "DERIVED_YTD_DIFF",
        160.0,
        date(2025, 4, 1),
    )

    with pytest.raises(
        ContractViolationError, match="duplicate composite business keys"
    ):
        canonicalize_financial_facts(pl.concat([derived_q2, derived_q2]))


def test_pit_query_deterministic_tie_breaking_and_cross_taxonomy_isolation(
    tmp_path: Path,
) -> None:
    base = _facts().head(1)
    filing_a = base.with_columns(
        pl.lit("filing-a").alias("filing_id"), pl.lit(10.0).alias("value")
    )
    filing_b = base.with_columns(
        pl.lit("filing-b").alias("filing_id"), pl.lit(20.0).alias("value")
    )
    tt99_comparative = base.with_columns(
        pl.lit("VAS_TT99_2025").alias("taxonomy_version"),
        pl.lit("RESTATED_REGIME_CHANGE").alias("revision_reason"),
        pl.lit("filing-2026-comparative").alias("filing_id"),
        pl.lit(date(2026, 4, 20)).alias("published_at"),
        pl.lit(date(2026, 4, 20)).alias("effective_available_at"),
        pl.lit(99.0).alias("value"),
    )
    for order_idx, frame in enumerate(
        (
            pl.concat([filing_a, filing_b, tt99_comparative]),
            pl.concat([filing_b, filing_a, tt99_comparative]),
        )
    ):
        path = tmp_path / f"facts_{order_idx}.parquet"
        frame.write_parquet(path)
        repo = ParquetRepository(path)
        early = repo.get_facts_as_of(date(2026, 2, 1))
        assert early.select("taxonomy_version", "filing_id", "value").rows() == [
            ("VAS_TT200_2014", "filing-b", 20.0)
        ]
        later = repo.get_facts_as_of(date(2026, 5, 1)).sort("taxonomy_version")
        assert later.select("taxonomy_version", "filing_id", "value").rows() == [
            ("VAS_TT200_2014", "filing-b", 20.0),
            ("VAS_TT99_2025", "filing-2026-comparative", 99.0),
        ]

    repo = ParquetRepository(tmp_path / "facts_0.parquet")
    for bad_date in (None, "2026-02-01", datetime(2026, 2, 1, 12, 0)):
        with pytest.raises(TypeError, match="as_of_date"):
            repo.get_facts_as_of(bad_date)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="require_exact_filing_date"):
        repo.get_facts_as_of(date(2026, 2, 1), require_exact_filing_date="yes")  # type: ignore[arg-type]


def test_snapshot_session_and_pinning_prevent_cross_table_read_skew(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    root = data_root / "published" / "snapshots"
    store = StateStore(data_root / "metadata" / "state.sqlite")
    store.initialize()
    publisher = SnapshotPublisher(root, state_store=store)

    store.start_run("run-1", "manual", {}, None)
    publisher.publish(
        "snap_1",
        {
            "issuers.parquet": _issuers().head(1),
            "latest_facts.parquet": _facts().head(1),
        },
        run_id="run-1",
    )

    repo = ParquetRepository(data_root)
    assert repo.snapshot_id == "snap_1"
    pinned = repo.pin_active_snapshot()
    assert pinned.snapshot_id == "snap_1"

    store.start_run("run-2", "manual", {}, None)
    publisher.publish(
        "snap_2",
        {
            "issuers.parquet": _issuers().tail(1),
            "latest_facts.parquet": _facts()
            .head(1)
            .with_columns(pl.lit(1.0).alias("value")),
        },
        run_id="run-2",
    )

    assert repo.snapshot_id == "snap_2"
    assert pinned.read_table("issuers.parquet")["issuer_id"].to_list() == ["FPT"]
    assert pinned.get_latest_facts()["value"].to_list() == [100_000_000_000.0]

    with repo.snapshot_session() as session:
        assert session.snapshot_id == "snap_2"
        assert session.read_table("issuers.parquet")["issuer_id"].to_list() == ["VNM"]
        assert session.get_latest_facts()["value"].to_list() == [1.0]


def test_post_commit_interrupt_preserves_destination_and_locked_gc_preserves_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path / "metadata" / "state.sqlite")
    store.initialize()
    root = tmp_path / "snapshots"
    publisher = SnapshotPublisher(root, state_store=store)

    # An uncommitted orphan directory with the same snapshot_id does not block retry.
    orphan = root / "snap_committed"
    orphan.mkdir(parents=True)
    (orphan / "stale.txt").write_text("stale", encoding="utf-8")

    original_publish_snapshot = store.publish_snapshot

    def commit_then_interrupt(run_id: str, snapshot_id: str) -> None:
        original_publish_snapshot(run_id, snapshot_id)
        raise KeyboardInterrupt("simulated interrupt after SQLite commit")

    monkeypatch.setattr(store, "publish_snapshot", commit_then_interrupt)
    store.start_run("run-1", "manual", {}, None)
    with pytest.raises(KeyboardInterrupt, match="simulated interrupt"):
        publisher.publish(
            "snap_committed",
            {"latest_facts.parquet": _facts().head(1)},
            run_id="run-1",
        )
    assert (root / "snap_committed" / "latest_facts.parquet").is_file()
    active = store.active_snapshot()
    assert active is not None and active["snapshot_id"] == "snap_committed"

    # In stateless mtime mode, a locked old snapshot on Windows must not lose half
    # its files or bump its mtime above newer valid snapshots.
    stateless_root = tmp_path / "stateless_snapshots"
    stateless_pub = SnapshotPublisher(stateless_root)
    for idx in (1, 2):
        stateless_pub.publish(
            f"snap_{idx}",
            {
                "issuers.parquet": _issuers().head(1),
                "latest_facts.parquet": _facts()
                .head(1)
                .with_columns(pl.lit(float(idx)).alias("value")),
            },
        )
        os.utime(stateless_root / f"snap_{idx}", (1_000_000 + idx, 1_000_000 + idx))

    real_replace = os.replace
    snap_1_dir = (stateless_root / "snap_1").resolve()

    def replace_locking_snap_1(
        src: str | os.PathLike[str], dst: str | os.PathLike[str]
    ) -> None:
        if Path(src).resolve() == snap_1_dir:
            raise PermissionError("simulated Windows open file handle on snap_1")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace_locking_snap_1)
    stateless_pub.publish(
        "snap_3",
        {
            "issuers.parquet": _issuers().head(1),
            "latest_facts.parquet": _facts()
            .head(1)
            .with_columns(pl.lit(3.0).alias("value")),
        },
    )
    assert (snap_1_dir / "issuers.parquet").is_file()
    assert (snap_1_dir / "latest_facts.parquet").is_file()
    assert snap_1_dir.stat().st_mtime < (stateless_root / "snap_3").stat().st_mtime


def test_repository_separates_published_tables_from_curated_and_disambiguates_batches(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    store = StateStore(data_root / "metadata" / "state.sqlite")
    store.initialize()
    curated = data_root / "curated"
    curated.mkdir(parents=True)
    _issuers().write_parquet(curated / "issuers.parquet")

    repo = ParquetRepository(data_root)
    with pytest.raises(FileNotFoundError):
        repo.read_table("issuers.parquet")

    # Multiple curated batches are disambiguated using StateStore normalization history.
    for idx, value in ((1, 10.0), (2, 20.0)):
        run_id = f"norm-{idx}"
        store.start_run(run_id, "CLI_MANUAL", {"source": "fhsc"}, None)
        step_id = store.start_step(run_id, "NORMALIZE")
        batch_dir = curated / "financial_facts" / run_id
        batch_dir.mkdir(parents=True)
        _facts().head(1).with_columns(pl.lit(value).alias("value")).write_parquet(
            batch_dir / "financial_facts.parquet"
        )
        store.finish_step(step_id, "SUCCEEDED")
        store.finish_run(run_id, "SUCCEEDED")

    assert repo.get_facts_as_of(date(2026, 2, 15))["value"].to_list() == [20.0]

    # A snapshot containing only latest_facts.parquet is not mistaken for full PiT history.
    snap_dir = data_root / "published" / "snapshots"
    store.start_run("pub-1", "CLI_MANUAL", {}, None)
    published = SnapshotPublisher(snap_dir, state_store=store).publish(
        "snap_latest_only",
        {"latest_facts.parquet": _facts().head(1)},
        run_id="pub-1",
    )
    snap_repo = ParquetRepository(published)
    assert snap_repo.get_latest_facts()["value"].to_list() == [100_000_000_000.0]
    with pytest.raises(FileNotFoundError):
        snap_repo.get_facts_as_of(date(2026, 2, 15))
