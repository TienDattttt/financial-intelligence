from datetime import date
from pathlib import Path
from threading import Event, Thread

import polars as pl
import pytest

from finintel.exceptions import ContractViolationError
from finintel.pipeline.state_store import StateStore
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import canonicalize_financial_facts
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
        "taxonomy_version": ["TT200"] * 4,
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
            "PROVIDER_ESTIMATE",
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
        "audit_opinion": ["NONE", "UNQUALIFIED", "NONE", "NONE"],
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


def test_reader_can_read_while_publishing(tmp_path: Path) -> None:
    facts = _facts()
    source = tmp_path / "facts.parquet"
    facts.write_parquet(source)
    root = tmp_path / "snapshots"
    publisher = SnapshotPublisher(root)
    publisher.publish("snap_1", {"latest_facts.parquet": facts})
    repository = ParquetRepository(root / "snap_1" / "latest_facts.parquet")
    publication_started = Event()
    query_started = Event()
    query_in_progress = Event()
    allow_read_complete = Event()
    reader_finished = Event()
    garbage_started = Event()
    read_values: list[float] = []
    read_errors: list[BaseException] = []

    class CoordinatedPublisher(SnapshotPublisher):
        def _write_table(
            self, staging: Path, filename: str, table: pl.DataFrame | Path
        ) -> None:
            publication_started.set()
            assert query_started.wait(timeout=5)
            super()._write_table(staging, filename, table)

        def _collect_garbage_unlocked(self) -> None:
            garbage_started.set()
            assert query_in_progress.wait(timeout=5)
            allow_read_complete.set()
            assert reader_finished.wait(timeout=5)
            super()._collect_garbage_unlocked()

    def read_worker() -> None:
        try:
            assert publication_started.wait(timeout=5)
            query_started.set()
            query_in_progress.set()
            result = (
                repository.get_facts_as_of(date(2026, 2, 15))
                .filter(pl.col("metric_id") == "IS_NET_INCOME")
                .filter(pl.col("statement_scope") == "CONSOLIDATED")["value"]
            )
            assert allow_read_complete.wait(timeout=5)
            read_values.extend(result.to_list())
            reader_finished.set()
        except BaseException as exc:
            read_errors.append(exc)

    reader = Thread(target=read_worker)
    reader.start()
    writer_errors: list[BaseException] = []

    def write_worker() -> None:
        try:
            CoordinatedPublisher(root).publish(
                "snap_2", {"latest_facts.parquet": source}
            )
        except BaseException as exc:
            writer_errors.append(exc)

    writer = Thread(target=write_worker)
    writer.start()
    writer.join(timeout=5)
    reader.join(timeout=5)
    assert not writer.is_alive()
    assert not reader.is_alive()
    assert garbage_started.is_set()
    assert not writer_errors
    assert not read_errors
    assert read_values == [100_000_000_000.0]


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
    with pytest.raises(ValueError):
        publisher.publish(
            "snap_failed",
            {"latest_facts.parquet": _facts().head(1)},
            run_id=failed_run,
        )


def test_repository_discovery_ignores_staging_and_uses_active_snapshot(
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
