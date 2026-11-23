"""TDD tests for Layer 3 DuckLake storage, ReadWriteLock, pushdown, recovery, and Curated GC.

Verified Sources:
- DuckLake Official Documentation: https://ducklake.select/docs/stable/
- DuckLake Snapshot Expiration Docs:
  https://ducklake.select/docs/stable/duckdb/maintenance/expire_snapshots
- DuckLake File Cleanup Docs:
  https://ducklake.select/docs/stable/duckdb/maintenance/cleanup_old_files
- DuckDB Parquet Tips & Secrets Manager Docs:
  https://duckdb.org/docs/stable/data/parquet/tips
  https://duckdb.org/docs/stable/configuration/secrets_manager
- filelock ReadWriteLock Docs: https://py-filelock.readthedocs.io/
- Gate B0 Measured Evidence: docs/evidence/option_b_spike_report.json
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest
from filelock import FileLock

from finintel.exceptions import ContractViolationError
from finintel.pipeline.cli import load_settings
from finintel.pipeline.publish import PublishHandler
from finintel.pipeline.runner import (
    Runner,
    RunRequest,
    RunStatus,
    Stage,
    StepContext,
    StepOutcome,
)
from finintel.pipeline.state_store import StateStore
from finintel.storage import (
    KNOWN_TABLES,
    TABLE_PHYSICAL_LAYOUT,
    DuckLakeStore,
    FilterRange,
    ParquetRepository,
    SnapshotPublisher,
    StorageConfig,
    collect_curated_garbage,
)
from finintel.storage.ducklake_store import (
    build_postgres_secret_statements,
    ensure_ducklake_attached,
)
from finintel.storage.manifest_export import build_latest_facts_table
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_SCHEMA,
    AUDIT_QUARANTINE_TABLE,
    FILINGS_MANIFEST_SCHEMA,
    FILINGS_MANIFEST_TABLE,
    FINANCIAL_RATIOS_SCHEMA,
    RATIOS_TABLE,
)


def _sample_issuers(scale_marker: str = "A") -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issuer_id": ["VNM", "FPT", "HPG"],
            "ticker": ["VNM", "FPT", "HPG"],
            "exchange": ["HOSE", "HOSE", "HOSE"],
            "company_name_vn": [
                f"Vinamilk {scale_marker}",
                f"FPT Corp {scale_marker}",
                f"Hoa Phat {scale_marker}",
            ],
            "accounting_regime": ["CORPORATE", "CORPORATE", "CORPORATE"],
            "icb_code_level2": ["3500", "9500", "1700"],
            "icb_name_level2": ["Food", "Tech", "Materials"],
            "icb_code_level3": ["3570", "9530", None],
            "icb_name_level3": ["Dairy", "Software", None],
            "industry_snapshot_date": [date(2026, 1, 1)] * 3,
            "delisted_date": [date(2025, 12, 31), None, None],
        }
    )


def _sample_facts(
    *, value_offset: float = 0.0, source_provider: str = "fhsc"
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issuer_id": ["VNM", "FPT", "HPG"],
            "statement_scope": ["CONSOLIDATED"] * 3,
            "period_type": ["QUARTER"] * 3,
            "fiscal_year_start_date": [date(2025, 1, 1)] * 3,
            "period_end_date": [
                date(2025, 12, 31),
                date(2025, 9, 30),
                date(2025, 12, 31),
            ],
            "metric_id": ["IS_NET_REVENUE", "IS_NET_REVENUE", "IS_NET_INCOME"],
            "taxonomy_version": ["VAS_TT200_2014"] * 3,
            "revision_no": [1, 1, 1],
            "filing_id": [
                f"filing-vnm-{source_provider}",
                f"filing-fpt-{source_provider}",
                f"filing-hpg-{source_provider}",
            ],
            "fiscal_year": [2025, 2025, 2025],
            "fiscal_quarter": pl.Series([4, 3, 4], dtype=pl.Int8),
            "period_start_date": [
                date(2025, 10, 1),
                date(2025, 7, 1),
                date(2025, 10, 1),
            ],
            "published_at": [
                date(2026, 1, 25),
                date(2025, 10, 25),
                date(2026, 1, 26),
            ],
            "published_at_basis": ["EXACT_FILING"] * 3,
            "effective_available_at": [
                date(2026, 1, 25),
                date(2025, 10, 25),
                date(2026, 1, 26),
            ],
            "revision_reason": ["ORIGINAL"] * 3,
            "derivation_method": ["REPORTED_DIRECTLY"] * 3,
            "audit_status": ["UNAUDITED"] * 3,
            "audit_opinion": ["UNKNOWN"] * 3,
            "value": [
                100.0 + value_offset,
                200.0 + value_offset,
                300.0 + value_offset,
            ],
            "ttm_value": [None, None, None],
            "computation_note": [None, None, None],
            "source_provider": [source_provider] * 3,
            "source_filing_ids": [
                [f"filing-vnm-{source_provider}"],
                [f"filing-fpt-{source_provider}"],
                [f"filing-hpg-{source_provider}"],
            ],
            "comparable_across_taxonomy": [True, False, True],
            "is_filed_late": [False, True, False],
        }
    )


def _sample_ratios(row_count: int = 3, *, value_offset: float = 0.0) -> pl.DataFrame:
    issuers = ["VNM", "FPT", "HPG"]
    rows: dict[str, list[object]] = {name: [] for name in FINANCIAL_RATIOS_SCHEMA}
    base_date = date(2020, 3, 31)
    for idx in range(row_count):
        # Intentionally unsorted issuer & period order to verify physical sorting.
        issuer = issuers[idx % 3]
        p_end = base_date + timedelta(days=(row_count - idx))
        for name, dtype in FINANCIAL_RATIOS_SCHEMA.items():
            if name == "issuer_id":
                rows[name].append(issuer)
            elif name == "ticker":
                rows[name].append(issuer)
            elif name == "accounting_regime":
                rows[name].append("CORPORATE")
            elif name == "statement_scope":
                rows[name].append("CONSOLIDATED")
            elif name == "period_type":
                rows[name].append("QUARTER")
            elif name == "fiscal_year":
                rows[name].append(p_end.year)
            elif name == "fiscal_quarter":
                rows[name].append(((p_end.month - 1) // 3) + 1)
            elif name == "period_end_date":
                rows[name].append(p_end)
            elif name == "filing_id":
                rows[name].append(f"f-ratio-{idx}")
            elif name == "taxonomy_version":
                rows[name].append("VAS_TT200_2014")
            elif name == "source_filing_ids":
                rows[name].append([f"f-ratio-{idx}"])
            elif name == "eps_estimate_basis":
                rows[name].append("REPORTED")
            elif name == "roe_total":
                rows[name].append(0.15 + value_offset + (idx % 5) * 0.01)
            elif name == "dupont_valid_flag":
                rows[name].append(True)
            elif dtype == pl.Float64:
                rows[name].append(None)
            elif dtype == pl.String:
                rows[name].append("VALID" if name.endswith("_flag") else None)
            else:
                rows[name].append(None)
    return pl.DataFrame(rows, schema=FINANCIAL_RATIOS_SCHEMA)


def _sample_manifest(marker: str = "v1") -> pl.DataFrame:
    return pl.DataFrame(
        {
            "filing_id": [f"filing-fpt-{marker}", f"filing-vnm-{marker}"],
            "issuer_id": ["FPT", "VNM"],
            "ticker_at_filing": ["FPT", "VNM"],
            "source_provider": ["fhsc", "fhsc"],
            "connector_name": ["fhsc_oapi_v1", "fhsc_oapi_v1"],
            "statement_type": ["INCOME_STATEMENT", "INCOME_STATEMENT"],
            "statement_scope": ["CONSOLIDATED", "CONSOLIDATED"],
            "period_type": ["QUARTER", "QUARTER"],
            "period_end_date": [date(2025, 9, 30), date(2025, 12, 31)],
            "published_at": [date(2025, 10, 25), date(2026, 1, 25)],
            "published_at_basis": ["EXACT_FILING", "EXACT_FILING"],
            "effective_available_at": [date(2025, 10, 25), date(2026, 1, 25)],
            "audit_status": ["UNAUDITED", "UNAUDITED"],
            "audit_opinion": ["UNKNOWN", "UNKNOWN"],
            "revision_no": [1, 1],
            "revision_reason": ["ORIGINAL", "ORIGINAL"],
            "canonical_content_sha256": ["a" * 64, "b" * 64],
            "payload_file_sha256": ["c" * 64, "d" * 64],
        },
        schema=FILINGS_MANIFEST_SCHEMA,
    )


def _make_ducklake_env(
    tmp_path: Path,
    *,
    keep_snapshots: int = 2,
    min_snapshot_age_seconds: int = 0,
) -> tuple[Path, StateStore, DuckLakeStore]:
    data_root = tmp_path / "data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    store = DuckLakeStore(
        catalog_path=data_root / "metadata" / "ducklake_catalog.sqlite",
        data_path=data_root / "lakehouse" / "data",
        state_store=state,
        keep_snapshots=keep_snapshots,
        min_snapshot_age_seconds=min_snapshot_age_seconds,
    )
    return data_root, state, store


def _publish_ducklake_version(
    state: StateStore,
    store: DuckLakeStore,
    idx: int,
    *,
    include_unmapped: bool = False,
) -> tuple[str, int]:
    run_id = f"run-{idx}"
    snap_id = f"snap_{idx:03d}"
    state.start_run(run_id, "CLI_MANUAL", {"source": "fhsc"}, None)
    tables: dict[str, pl.DataFrame] = {
        "issuers.parquet": _sample_issuers(scale_marker=f"v{idx}"),
        "financial_facts.parquet": _sample_facts(value_offset=float(idx * 10)),
        "latest_facts.parquet": _sample_facts(value_offset=float(idx * 10)),
        RATIOS_TABLE: _sample_ratios(row_count=3, value_offset=float(idx)),
        FILINGS_MANIFEST_TABLE: _sample_manifest(marker=f"v{idx}"),
    }
    if include_unmapped:
        tables["unmapped_metrics.parquet"] = pl.DataFrame(
            {
                "issuer_id": ["FPT"],
                "filing_id": [f"f-unmapped-{idx}"],
                "raw_metric_key": ["999"],
                "raw_value": ["123"],
                "statement_scope": ["CONSOLIDATED"],
                "period_type": ["QUARTER"],
                "period_end_date": [date(2025, 12, 31)],
                "effective_available_at": [date(2026, 1, 25)],
            }
        )
    version = store.publish_snapshot(snap_id, tables, run_id=run_id)
    return snap_id, version


# ---------------------------------------------------------------------------
# 1. Zero Reader-Reader Blocking (8 threads + Windows subprocess ReadWriteLock)
# ---------------------------------------------------------------------------


def test_parquet_snapshot_session_allows_8_concurrent_readers_without_blocking(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    state.start_run("run-1", "CLI_MANUAL", {}, None)
    publisher = SnapshotPublisher(
        data_root / "published" / "snapshots", state_store=state
    )
    publisher.publish(
        "snap_001",
        {
            "issuers.parquet": _sample_issuers(),
            "financial_facts.parquet": _sample_facts(),
        },
        run_id="run-1",
    )

    repo = ParquetRepository(data_root=data_root)
    barrier = threading.Barrier(8)
    results: list[str | None] = []
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            with repo.snapshot_session() as pinned:
                barrier.wait(timeout=5.0)
                df = pinned.read_table("issuers.parquet", columns=["issuer_id"])
                assert df.height == 3
                results.append(pinned.snapshot_id)
        except BaseException as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(reader) for _ in range(8)]
        for fut in futures:
            fut.result(timeout=10.0)

    assert not errors
    assert results == ["snap_001"] * 8


def test_ducklake_snapshot_session_allows_8_concurrent_readers_without_blocking(
    tmp_path: Path,
) -> None:
    data_root, state, store = _make_ducklake_env(tmp_path)
    _publish_ducklake_version(state, store, 1)

    repo = ParquetRepository(data_root=data_root)
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            with repo.snapshot_session() as pinned:
                barrier.wait(timeout=5.0)
                df = pinned.read_table("issuers", columns=["issuer_id", "ticker"])
                assert df.height == 3
        except BaseException as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(reader) for _ in range(8)]
        for fut in futures:
            fut.result(timeout=10.0)

    assert not errors


def test_rwlock_windows_subprocess_concurrent_readers_and_writer_exclusion(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / ".snapshots.publish.lock"
    ready_dir = tmp_path / "ready"
    ready_dir.mkdir()
    release_flag = tmp_path / "release.flag"

    reader_code = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from filelock import ReadWriteLock\n"
        "lock_p, ready_p, rel_p = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])\n"
        "rw = ReadWriteLock(lock_p, is_singleton=False)\n"
        "try:\n"
        "    with rw.read_lock(timeout=10.0):\n"
        "        ready_p.write_text('ok', encoding='utf-8')\n"
        "        deadline = time.monotonic() + 15.0\n"
        "        while not rel_p.exists():\n"
        "            if time.monotonic() > deadline:\n"
        "                raise TimeoutError('release_flag timeout')\n"
        "            time.sleep(0.02)\n"
        "finally:\n"
        "    rw.close()\n"
    )
    writer_code = (
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "from filelock import ReadWriteLock, Timeout\n"
        "lock_p, probed_p = Path(sys.argv[1]), Path(sys.argv[2])\n"
        "rw = ReadWriteLock(lock_p, is_singleton=False)\n"
        "try:\n"
        "    blocked = False\n"
        "    try:\n"
        "        with rw.write_lock(timeout=0.0):\n"
        "            pass\n"
        "    except Timeout:\n"
        "        blocked = True\n"
        "    probed_p.write_text('ok', encoding='utf-8')\n"
        "    t0 = time.monotonic()\n"
        "    with rw.write_lock(timeout=10.0):\n"
        "        elapsed = time.monotonic() - t0\n"
        "    print(json.dumps({'blocked': blocked, 'elapsed': elapsed}), flush=True)\n"
        "finally:\n"
        "    rw.close()\n"
    )

    readers: list[subprocess.Popen[str]] = []
    writer: subprocess.Popen[str] | None = None
    try:
        for idx in range(3):
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    reader_code,
                    str(lock_path),
                    str(ready_dir / f"r{idx}"),
                    str(release_flag),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            readers.append(proc)

        deadline = time.monotonic() + 15.0
        while not all((ready_dir / f"r{idx}").exists() for idx in range(3)):
            assert time.monotonic() < deadline, "Readers failed to enter read_lock"
            time.sleep(0.02)

        probed_flag = ready_dir / "writer_probed"
        writer = subprocess.Popen(
            [
                sys.executable,
                "-c",
                writer_code,
                str(lock_path),
                str(probed_flag),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        while not probed_flag.exists():
            assert time.monotonic() < deadline, "Writer failed to probe write_lock"
            time.sleep(0.02)

        release_flag.write_text("go", encoding="utf-8")
        w_out, w_err = writer.communicate(timeout=15.0)
        assert writer.returncode == 0, w_err
        payload = json.loads(w_out.strip())
        assert payload["blocked"] is True
        assert payload["elapsed"] >= 0.0

        for proc in readers:
            _, r_err = proc.communicate(timeout=15.0)
            assert proc.returncode == 0, r_err
    finally:
        for proc in readers:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        if writer is not None and writer.poll() is None:
            writer.kill()
            writer.wait(timeout=5)


# ---------------------------------------------------------------------------
# 2. Concurrent Publish + Maintain + Zero Read-Skew + Exact versions Expiry
# ---------------------------------------------------------------------------


def test_ducklake_concurrent_readers_zero_skew_with_non_owning_cursor_and_maintain(
    tmp_path: Path,
) -> None:
    data_root, state, store = _make_ducklake_env(
        tmp_path, keep_snapshots=2, min_snapshot_age_seconds=0
    )
    _publish_ducklake_version(state, store, 1)

    ro_base = duckdb.connect(":memory:")
    ro_base.execute("LOAD ducklake; LOAD sqlite;")
    cat_sql = store.catalog_path.as_posix().replace("'", "''")
    data_sql = store.data_path.as_posix().replace("'", "''")
    ro_base.execute(
        f"ATTACH 'ducklake:sqlite:{cat_sql}' AS finintel_lake "
        f"(DATA_PATH '{data_sql}', READ_ONLY)"
    )

    repo = ParquetRepository(data_root=data_root)
    stop_event = threading.Event()
    errors: list[str] = []
    reads_completed = [0, 0, 0, 0]

    def reader_loop(worker_idx: int) -> None:
        cursor = ro_base.cursor()
        try:
            while not stop_event.is_set() or reads_completed[worker_idx] < 5:
                with repo.snapshot_session(connection=cursor) as pinned:
                    meta = pinned.get_snapshot_metadata(connection=cursor)
                    snap_id = str(meta["snapshot_id"])
                    expected_idx = int(snap_id.split("_")[1])

                    issuers = pinned.read_table(
                        "issuers.parquet",
                        columns=["issuer_id", "company_name_vn"],
                        filters={"issuer_id": "FPT"},
                        connection=cursor,
                    )
                    facts = pinned.read_table(
                        "financial_facts",
                        columns=["issuer_id", "value"],
                        filters={"issuer_id": "FPT"},
                        connection=cursor,
                    )
                    latest = pinned.read_table(
                        "latest_facts.parquet",
                        columns=["issuer_id", "value"],
                        filters={"issuer_id": "FPT"},
                        connection=cursor,
                    )
                    ratios = pinned.read_table(
                        RATIOS_TABLE,
                        columns=["issuer_id", "roe_total"],
                        filters={"issuer_id": "FPT"},
                        connection=cursor,
                    )
                    manifest = pinned.read_table(
                        FILINGS_MANIFEST_TABLE,
                        columns=["filing_id", "issuer_id"],
                        filters={"issuer_id": "FPT"},
                        connection=cursor,
                    )

                    assert issuers["company_name_vn"].to_list() == [
                        f"FPT Corp v{expected_idx}"
                    ]
                    assert facts["value"].to_list() == [200.0 + expected_idx * 10.0]
                    assert latest["value"].to_list() == [200.0 + expected_idx * 10.0]
                    assert pytest.approx(ratios["roe_total"].to_list()[0]) == (
                        0.15 + expected_idx + 0.01
                    )
                    assert manifest["filing_id"].to_list() == [
                        f"filing-fpt-v{expected_idx}"
                    ]
                # Non-owning cursor MUST remain open after snapshot_session exits.
                assert cursor.execute("SELECT 1").fetchone() == (1,)
                reads_completed[worker_idx] += 1
        except BaseException as exc:
            errors.append(f"reader-{worker_idx}:{type(exc).__name__}: {exc}")
        finally:
            cursor.close()

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            reader_futures = [pool.submit(reader_loop, idx) for idx in range(4)]
            for ver_idx in range(2, 7):
                _publish_ducklake_version(state, store, ver_idx)
                time.sleep(0.02)
            # Keep grace period large enough during active readers, then expire after stop.
            store.maintain(keep_snapshots=2, min_snapshot_age_seconds=3600)
            stop_event.set()
            for fut in reader_futures:
                fut.result(timeout=20.0)
    finally:
        ro_base.close()

    assert not errors, f"Concurrent reader errors: {errors}"
    assert all(count >= 5 for count in reads_completed)

    # Now verify explicit versions expiry with min_snapshot_age_seconds=0 protects active & keep_snapshots=2.
    maint = store.maintain(keep_snapshots=2, min_snapshot_age_seconds=0)
    expired_versions = maint["expired_versions"]
    remaining_versions = maint["remaining_versions"]
    assert isinstance(expired_versions, list)
    assert isinstance(remaining_versions, list)
    assert len(expired_versions) >= 1

    # Active snapshot (snap_006) and previous rollback snapshot (snap_005) must still be readable.
    meta = repo.get_snapshot_metadata()
    assert meta["snapshot_id"] == "snap_006"
    assert meta["storage_backend"] == "ducklake"
    assert meta["ducklake_version"] in remaining_versions

    # Rollback to snap_005 MUST still succeed because keep_snapshots=2 protected it.
    v5 = store.rollback_snapshot("snap_005", state)
    assert v5 in remaining_versions
    assert repo.get_snapshot_metadata()["snapshot_id"] == "snap_005"


def test_ducklake_expire_respects_min_snapshot_age_and_active_protection(
    tmp_path: Path,
) -> None:
    data_root, state, store = _make_ducklake_env(
        tmp_path, keep_snapshots=2, min_snapshot_age_seconds=3600
    )
    versions: dict[str, int] = {}
    for idx in range(1, 5):
        snap_id, ver = _publish_ducklake_version(state, store, idx)
        versions[snap_id] = ver

    # Young snapshots (< 3600s) MUST NOT be expired even when > keep_snapshots.
    young_result = store.maintain(keep_snapshots=2, min_snapshot_age_seconds=3600)
    assert young_result["expired_versions"] == []

    # Roll back active_snapshot to snap_001 (oldest), then maintain with min_age=0.
    # Active snapshot (snap_001) AND newest 2 published snapshots (snap_004, snap_003) MUST survive.
    store.rollback_snapshot("snap_001", state)
    result = store.maintain(keep_snapshots=2, min_snapshot_age_seconds=0)
    raw_expired = result["expired_versions"]
    raw_remaining = result["remaining_versions"]
    assert isinstance(raw_expired, list)
    assert isinstance(raw_remaining, list)
    expired = set(raw_expired)
    remaining = set(raw_remaining)
    assert versions["snap_001"] not in expired
    assert versions["snap_001"] in remaining
    assert versions["snap_003"] in remaining
    assert versions["snap_004"] in remaining
    assert versions["snap_002"] in expired
    # Verify snap_001 data is still readable via ParquetRepository.
    assert ParquetRepository(data_root).read_table(
        "issuers", filters={"issuer_id": "FPT"}
    )["company_name_vn"].to_list() == ["FPT Corp v1"]


def test_ducklake_writer_serialization_with_pipeline_lock_and_runner_no_deadlock(
    tmp_path: Path,
) -> None:
    data_root, state, store = _make_ducklake_env(tmp_path)
    _publish_ducklake_version(state, store, 1)

    # Holding pipeline.lock in another thread MUST block DuckLakeStore.maintain().
    lock_path = state.path.with_name("pipeline.lock")
    entered = threading.Event()
    release = threading.Event()
    maintain_done = threading.Event()

    def hold_pipeline_lock() -> None:
        with FileLock(lock_path, timeout=5.0):
            entered.set()
            assert release.wait(timeout=5.0)

    def run_maintain() -> None:
        store.maintain(keep_snapshots=2, min_snapshot_age_seconds=3600)
        maintain_done.set()

    holder = threading.Thread(target=hold_pipeline_lock)
    holder.start()
    assert entered.wait(timeout=5.0)
    worker = threading.Thread(target=run_maintain)
    worker.start()
    time.sleep(0.15)
    assert not maintain_done.is_set(), (
        "maintain() must wait for pipeline.lock serialization"
    )
    release.set()
    holder.join(timeout=5.0)
    worker.join(timeout=10.0)
    assert maintain_done.is_set()

    # Running PublishHandler inside Runner.run() (which already holds pipeline.lock on same thread)
    # MUST NOT self-deadlock on Windows.
    curated = data_root / "curated"
    curated.mkdir(parents=True, exist_ok=True)
    _sample_issuers("runner").write_parquet(curated / "issuers.parquet")

    def prep_and_publish(context: StepContext) -> StepOutcome:
        q_dir = curated / "quality" / context.run_id
        q_dir.mkdir(parents=True, exist_ok=True)
        _sample_facts(value_offset=99.0).write_parquet(
            q_dir / "clean_financial_facts.parquet"
        )
        handler = PublishHandler(
            state,
            tmp_path / "config",
            {},
            storage_settings={
                "backend": "ducklake",
                "catalog": {
                    "driver": "sqlite",
                    "sqlite_path": "../data/metadata/ducklake_catalog.sqlite",
                },
                "data_path": "../data/lakehouse/data",
                "retention": {"keep_snapshots": 2, "min_snapshot_age_seconds": 0},
            },
        )
        return handler(context)

    result = Runner(
        state,
        {Stage.PUBLISH: prep_and_publish},
        min_free_bytes=0,
    ).run(RunRequest(source="file", stage=Stage.PUBLISH))
    assert result.status == RunStatus.SUCCEEDED
    meta = ParquetRepository(data_root).get_snapshot_metadata()
    assert meta["storage_backend"] == "ducklake"
    assert meta["snapshot_id"] is not None


# ---------------------------------------------------------------------------
# 3. Crash Recovery & Rollback + Optional Table Drop Across Versions
# ---------------------------------------------------------------------------


def test_ducklake_crash_between_phase1_and_phase2_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root, state, store = _make_ducklake_env(tmp_path)
    snap_1, _ = _publish_ducklake_version(state, store, 1, include_unmapped=True)
    snap_2, _ = _publish_ducklake_version(state, store, 2, include_unmapped=False)

    # Verify optional table dropped in snap_2 raises FileNotFoundError at snap_2.
    repo = ParquetRepository(data_root)
    with pytest.raises(FileNotFoundError):
        repo.read_table("unmapped_metrics.parquet")

    # Simulate crash right after Phase 1 (DuckLake commit) before Phase 2 (state.sqlite promotion).
    def crash_promotion(run_id: str, snapshot_id: str) -> None:
        raise RuntimeError("simulated crash before state.sqlite promotion")

    monkeypatch.setattr(state, "publish_snapshot", crash_promotion)
    state.start_run("run-crash", "CLI_MANUAL", {}, None)
    with pytest.raises(RuntimeError, match="simulated crash"):
        store.publish_snapshot(
            "snap_003_orphan",
            {
                "issuers.parquet": _sample_issuers("ORPHAN"),
                "financial_facts.parquet": _sample_facts(value_offset=999.0),
            },
            run_id="run-crash",
        )
    monkeypatch.undo()

    # Readers MUST still see snap_2 untouched (zero impact from unpromoted Phase 1 commit).
    assert repo.snapshot_id == snap_2
    assert repo.read_table("issuers", filters={"issuer_id": "FPT"})[
        "company_name_vn"
    ].to_list() == ["FPT Corp v2"]

    # Reconcile MUST detect the orphan unpromoted snapshot in _snapshot_audit.
    orphans = store.reconcile_unpromoted_snapshots()
    assert [str(o["snapshot_id"]) for o in orphans] == ["snap_003_orphan"]

    # Rollback to snap_1 atomically switches readers back (and restores unmapped_metrics visibility).
    store.rollback_snapshot(snap_1, state)
    assert repo.snapshot_id == snap_1
    assert repo.read_table("issuers", filters={"issuer_id": "FPT"})[
        "company_name_vn"
    ].to_list() == ["FPT Corp v1"]
    assert repo.read_table("unmapped_metrics").height == 1

    # Rolling back to unknown snapshot raises ValueError; rolling back to expired raises ContractViolationError.
    with pytest.raises(ValueError, match="not found"):
        store.rollback_snapshot("snap_does_not_exist", state)

    # Publish snap_4 & snap_5 then expire snap_1 & snap_003_orphan -> rollback to snap_1
    # must fail closed AND reconcile_unpromoted_snapshots() must stop reporting expired orphans.
    _publish_ducklake_version(state, store, 4)
    _publish_ducklake_version(state, store, 5)
    store.maintain(keep_snapshots=2, min_snapshot_age_seconds=0)
    with pytest.raises(ContractViolationError, match="expired"):
        store.rollback_snapshot(snap_1, state)
    assert store.reconcile_unpromoted_snapshots() == []


# ---------------------------------------------------------------------------
# 4. Predicate Pushdown, str/bytes/None/FilterRange Filters & SQLi Guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["parquet", "ducklake"])
def test_repository_filters_projection_pushdown_and_sqli_guards(
    tmp_path: Path, backend: str
) -> None:
    data_root = tmp_path / "data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    state.start_run("run-1", "CLI_MANUAL", {}, None)

    tables: dict[str, pl.DataFrame] = {
        "issuers.parquet": _sample_issuers("Pushdown"),
        "financial_facts.parquet": _sample_facts(),
        RATIOS_TABLE: _sample_ratios(row_count=9),
    }
    if backend == "ducklake":
        store = DuckLakeStore(
            catalog_path=data_root / "metadata" / "ducklake_catalog.sqlite",
            data_path=data_root / "lakehouse" / "data",
            state_store=state,
        )
        store.publish_snapshot("snap_001", tables, run_id="run-1")
    else:
        SnapshotPublisher(
            data_root / "published" / "snapshots", state_store=state
        ).publish("snap_001", tables, run_id="run-1")

    repo = ParquetRepository(data_root=data_root)
    meta = repo.get_snapshot_metadata()
    assert meta["storage_backend"] == backend
    assert meta["snapshot_id"] == "snap_001"
    assert isinstance(meta["published_at"], str)

    # Table normalization: both stem and .parquet work.
    by_stem = repo.read_table("issuers", filters={"ticker": "FPT"})
    by_ext = repo.read_table("issuers.parquet", filters={"ticker": "FPT"})
    # str MUST produce `= ?`, NOT `IN ('F', 'P', 'T')`.
    assert by_stem["ticker"].to_list() == ["FPT"]
    assert by_ext["ticker"].to_list() == ["FPT"]

    # None MUST produce `IS NULL`.
    active_issuers = repo.read_table(
        "issuers",
        columns=["issuer_id", "delisted_date"],
        filters={"delisted_date": None},
    )
    assert set(active_issuers["issuer_id"].to_list()) == {"FPT", "HPG"}

    # Collection IN filter & empty collection returning typed empty DataFrame.
    two = repo.read_table("issuers", filters={"issuer_id": ["FPT", "VNM"]})
    assert set(two["issuer_id"].to_list()) == {"FPT", "VNM"}
    empty_df = repo.read_table(
        "issuers",
        columns=["issuer_id", "delisted_date"],
        filters={"issuer_id": []},
    )
    assert empty_df.is_empty()
    assert empty_df.columns == ["issuer_id", "delisted_date"]
    assert empty_df.schema["delisted_date"] == pl.Date

    # FilterRange on dates & floats.
    ranged = repo.read_table(
        "financial_facts",
        columns=["issuer_id", "period_end_date", "value"],
        filters={
            "period_end_date": FilterRange(ge=date(2025, 10, 1), le=date(2025, 12, 31)),
            "value": FilterRange(gt=100.0, lt=350.0),
        },
    )
    assert ranged["issuer_id"].to_list() == ["HPG"]

    # EXPLAIN verifies scan-level filter pushdown in DuckLake.
    if backend == "ducklake":
        con = duckdb.connect(":memory:")
        try:
            con.execute("LOAD ducklake; LOAD sqlite;")
            cat_sql = (data_root / "metadata" / "ducklake_catalog.sqlite").as_posix()
            lake_sql = (data_root / "lakehouse" / "data").as_posix()
            con.execute(
                f"ATTACH 'ducklake:sqlite:{cat_sql}' AS finintel_lake "
                f"(DATA_PATH '{lake_sql}', READ_ONLY)"
            )
            ver = meta["ducklake_version"]
            assert isinstance(ver, int)
            plan_rows = con.execute(
                f'EXPLAIN SELECT "issuer_id", "value" FROM finintel_lake.financial_facts '
                f'AT (VERSION => {ver}) WHERE "issuer_id" = ?',
                ["FPT"],
            ).fetchall()
            plan_text = "\n".join(str(row[1]) for row in plan_rows)
            assert "DUCKLAKE_SCAN" in plan_text
            assert "issuer_id='FPT'" in plan_text
        finally:
            con.close()

    # SQLi & allowlist rejection tests.
    with pytest.raises(ValueError):
        repo.read_table("evil_table.parquet")
    with pytest.raises(ValueError):
        repo.read_table("../issuers.parquet")
    with pytest.raises(ValueError):
        repo.read_table("issuers", columns=[])
    with pytest.raises(ValueError):
        repo.read_table("issuers", columns="issuer_id")
    with pytest.raises(ValueError):
        repo.read_table("issuers", columns=["issuer_id", "issuer_id"])
    with pytest.raises(ValueError):
        repo.read_table("issuers", columns=["issuer_id; DROP TABLE issuers"])
    with pytest.raises(ValueError):
        repo.read_table("issuers", columns=["nonexistent_col"])
    with pytest.raises(ValueError):
        repo.read_table("issuers", filters={"issuer_id; DROP TABLE issuers": "FPT"})
    with pytest.raises(ValueError):
        repo.read_table("issuers", filters={"nonexistent_col": "FPT"})
    with pytest.raises(ValueError):
        FilterRange()
    # Verify bytes scalar is NOT expanded into IN (...) clauses.
    from finintel.storage.repository import _build_where_clause

    where_sql, where_params, force_empty = _build_where_clause(
        {"ticker": b"FPT"}, {"ticker"}, "issuers.parquet"
    )
    assert where_sql == ' WHERE "ticker" = ?'
    assert where_params == [b"FPT"]
    assert force_empty is False


# ---------------------------------------------------------------------------
# 5. Sorted Parquet + Row Group Size, Grace Period & Postgres Secrets
# ---------------------------------------------------------------------------


def test_snapshot_publisher_sorts_path_inputs_with_row_group_size_and_grace_period(
    tmp_path: Path,
) -> None:
    # Write an unsorted 6,000-row financial_ratios.parquet (3x row_group_size=2_000)
    # as a Path input with 1 giant row group to prove shutil.copy2 was replaced.
    unsorted_path = tmp_path / "unsorted_ratios.parquet"
    ratios = _sample_ratios(row_count=6_000)
    ratios.write_parquet(unsorted_path, row_group_size=100_000)

    root = tmp_path / "published" / "snapshots"
    publisher = SnapshotPublisher(root, keep_snapshots=2, min_snapshot_age_seconds=3600)
    snap_dir = publisher.publish("snap_1", {RATIOS_TABLE: unsorted_path})

    written_path = snap_dir / RATIOS_TABLE
    with duckdb.connect(":memory:") as con:
        rg_rows = con.execute(
            "SELECT DISTINCT row_group_id, row_group_num_rows "
            "FROM parquet_metadata(?) ORDER BY row_group_id",
            [str(written_path)],
        ).fetchall()
    assert rg_rows == [(0, 2_000), (1, 2_000), (2, 2_000)]

    loaded = pl.read_parquet(written_path)
    sort_cols = list(TABLE_PHYSICAL_LAYOUT[RATIOS_TABLE].sort_by)
    assert loaded.select(sort_cols).equals(loaded.select(sort_cols).sort(sort_cols))

    # Verify TABLE_PHYSICAL_LAYOUT includes statement_scope on industry_benchmarks.parquet
    assert (
        TABLE_PHYSICAL_LAYOUT["industry_benchmarks.parquet"].sort_by[0]
        == "statement_scope"
    )
    assert "financial_ratios.parquet" in KNOWN_TABLES

    # Verify grace period protects young snapshots beyond keep_snapshots=2.
    publisher.publish("snap_2", {RATIOS_TABLE: ratios.head(3)})
    publisher.publish("snap_3", {RATIOS_TABLE: ratios.head(3)})
    assert (root / "snap_1").is_dir(), "Young snap_1 must be retained by grace period"

    # Backdate snap_1 mtime beyond 3600s and verify collect_garbage deletes it.
    old_ts = time.time() - 7200
    os.utime(root / "snap_1", (old_ts, old_ts))
    publisher.collect_garbage()
    assert not (root / "snap_1").exists()
    assert (root / "snap_2").is_dir()
    assert (root / "snap_3").is_dir()


def test_postgres_secret_builder_uses_create_secret_without_leaking_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret_pw = "SuperSecret_Pass!99"
    monkeypatch.setenv(
        "FININTEL_TEST_PG_DSN",
        f"postgresql://lake_user:{secret_pw}@db.internal:5432/finintel_cat",
    )
    stmts = build_postgres_secret_statements(
        postgres_dsn_env="FININTEL_TEST_PG_DSN",
        data_path=tmp_path / "lake_data",
        catalog_alias="finintel_lake",
        read_only=True,
    )
    assert any("CREATE OR REPLACE SECRET" in s and "TYPE postgres" in s for s in stmts)
    assert any("TYPE ducklake" in s for s in stmts)
    attach_stmt = stmts[-1]
    assert attach_stmt.startswith("ATTACH 'ducklake:")
    assert secret_pw not in attach_stmt
    assert "READ_ONLY" in attach_stmt

    monkeypatch.delenv("FININTEL_TEST_PG_DSN", raising=False)
    with pytest.raises(ContractViolationError) as exc_info:
        build_postgres_secret_statements(
            postgres_dsn_env="FININTEL_TEST_PG_DSN",
            data_path=tmp_path / "lake_data",
            catalog_alias="finintel_lake",
        )
    assert secret_pw not in str(exc_info.value)


# ---------------------------------------------------------------------------
# 6. Curated GC Hardened Safety — All 7 Fail-Closed Invariants
# ---------------------------------------------------------------------------


def _seed_curated_run(
    data_root: Path,
    state: StateStore,
    run_id: str,
    *,
    source: str = "fhsc",
    status: str = "SUCCEEDED",
    full_history: bool = True,
    age_seconds: int = 7200,
    quarantine_rows: int = 0,
    corrupt_quarantine: bool = False,
    publish_snapshot_id: str | None = None,
) -> None:
    state.start_run(run_id, "CLI_MANUAL", {"source": source}, None)
    if publish_snapshot_id is not None:
        state.publish_snapshot(run_id, publish_snapshot_id)
    if status != "RUNNING":
        state.finish_run(run_id, status)

    curated = data_root / "curated"
    ff_dir = curated / "financial_facts" / run_id
    q_dir = curated / "quality" / run_id
    a_dir = curated / "analytics" / run_id
    for d in (ff_dir, q_dir, a_dir):
        d.mkdir(parents=True, exist_ok=True)

    _sample_facts().write_parquet(ff_dir / "financial_facts.parquet")
    (ff_dir / "_normalize_meta.json").write_text(
        json.dumps({"source": source, "full_history": full_history}),
        encoding="utf-8",
    )
    (a_dir / "_analytics_meta.json").write_text(
        json.dumps({"source": source, "as_of": "2026-06-01"}),
        encoding="utf-8",
    )
    if corrupt_quarantine:
        (q_dir / AUDIT_QUARANTINE_TABLE).write_bytes(b"NOT_A_VALID_PARQUET_FILE")
    elif quarantine_rows > 0:
        pl.DataFrame(
            {
                "issuer_id": ["FPT"] * quarantine_rows,
                "filing_id": [f"f-q-{i}" for i in range(quarantine_rows)],
                "statement_scope": ["CONSOLIDATED"] * quarantine_rows,
                "period_type": ["ANNUAL"] * quarantine_rows,
                "period_end_date": [date(2025, 12, 31)] * quarantine_rows,
                "taxonomy_version": ["VAS_TT200_2014"] * quarantine_rows,
                "rule_code": ["BS_IDENTITY_MISMATCH"] * quarantine_rows,
                "severity": ["CRITICAL"] * quarantine_rows,
                "details_json": ["{}"] * quarantine_rows,
                "quarantined_at": [datetime.now(UTC).isoformat()] * quarantine_rows,
            },
            schema=AUDIT_QUARANTINE_SCHEMA,
        ).write_parquet(q_dir / AUDIT_QUARANTINE_TABLE)
    else:
        pl.DataFrame(schema=AUDIT_QUARANTINE_SCHEMA).write_parquet(
            q_dir / AUDIT_QUARANTINE_TABLE
        )

    old_ts = time.time() - age_seconds
    for d in (ff_dir, q_dir, a_dir):
        os.utime(d, (old_ts, old_ts))


def test_curated_gc_enforces_all_7_fail_closed_safety_invariants(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    curated = data_root / "curated"
    curated.mkdir(parents=True, exist_ok=True)

    # Invariant 1: invalid parameters fail fast.
    with pytest.raises(ValueError, match="keep_runs"):
        collect_curated_garbage(data_root, state, keep_runs=0, min_age_seconds=0)
    with pytest.raises(ValueError, match="min_age_seconds"):
        collect_curated_garbage(data_root, state, keep_runs=1, min_age_seconds=-1)

    # Invariant 2: top-level curated/issuers.parquet MUST NEVER be touched.
    issuers_file = curated / "issuers.parquet"
    _sample_issuers().write_parquet(issuers_file)

    # Seed 4 aged fhsc runs -> oldest 2 eligible unless protected by another invariant.
    _seed_curated_run(data_root, state, "fhsc-1-deletable", source="fhsc")
    _seed_curated_run(
        data_root,
        state,
        "fhsc-2-quarantine-rows",
        source="fhsc",
        quarantine_rows=1,
    )
    _seed_curated_run(
        data_root,
        state,
        "fhsc-3-quarantine-corrupt",
        source="fhsc",
        corrupt_quarantine=True,
    )
    _seed_curated_run(
        data_root,
        state,
        "fhsc-4-failed-active",
        source="fhsc",
        status="FAILED",
        publish_snapshot_id="snap_failed_active",
    )
    _seed_curated_run(
        data_root, state, "fhsc-5-running", source="fhsc", status="RUNNING"
    )
    _seed_curated_run(data_root, state, "fhsc-6-young", source="fhsc", age_seconds=10)
    _seed_curated_run(data_root, state, "fhsc-7-protected-arg", source="fhsc")
    _seed_curated_run(data_root, state, "fhsc-8-keep-newest-2", source="fhsc")
    _seed_curated_run(data_root, state, "fhsc-9-keep-newest-1", source="fhsc")

    # Independent source ("file") with only 1 aged run MUST be preserved by keep_runs=2 per (subdir, source).
    _seed_curated_run(data_root, state, "file-only-1", source="file")

    # Active staging directories younger than min_age_seconds MUST be preserved.
    active_staging = curated / "analytics" / ".analytics-active123"
    active_staging.mkdir(parents=True, exist_ok=True)

    # Symlink outside curated MUST be skipped without deleting target (skip gracefully if Windows denies symlink creation).
    outside_target = tmp_path / "outside_secret_dir"
    outside_target.mkdir()
    (outside_target / "precious.txt").write_text("keep", encoding="utf-8")
    symlink_entry = curated / "financial_facts" / "symlink-escape"
    symlink_created = False
    try:
        os.symlink(outside_target, symlink_entry, target_is_directory=True)
        symlink_created = True
    except OSError:
        symlink_created = False

    removed = collect_curated_garbage(
        data_root,
        state,
        keep_runs=2,
        min_age_seconds=3600,
        protected_run_ids=("fhsc-7-protected-arg",),
    )

    # Top-level curated/issuers.parquet untouched.
    assert issuers_file.is_file()
    # Active staging dir untouched.
    assert active_staging.is_dir()
    # Deletable aged run removed across all 3 subdirs.
    assert not (curated / "financial_facts" / "fhsc-1-deletable").exists()
    assert not (curated / "quality" / "fhsc-1-deletable").exists()
    assert not (curated / "analytics" / "fhsc-1-deletable").exists()
    assert len(removed) >= 3

    # Fail-closed forensic quarantine preservation (both non-empty & corrupt quality dirs survive).
    assert (curated / "quality" / "fhsc-2-quarantine-rows").is_dir()
    assert (curated / "quality" / "fhsc-3-quarantine-corrupt").is_dir()

    # Active FAILED snapshot run, RUNNING run, young run, protected_run_ids, newest 2 fhsc, and only file run ALL survive.
    for must_keep in (
        "fhsc-4-failed-active",
        "fhsc-5-running",
        "fhsc-6-young",
        "fhsc-7-protected-arg",
        "fhsc-8-keep-newest-2",
        "fhsc-9-keep-newest-1",
        "file-only-1",
    ):
        assert (curated / "financial_facts" / must_keep).is_dir(), must_keep
        assert (curated / "quality" / must_keep).is_dir(), must_keep
        assert (curated / "analytics" / must_keep).is_dir(), must_keep

    if symlink_created:
        assert (outside_target / "precious.txt").is_file()


# ---------------------------------------------------------------------------
# 7. Single-Read PublishHandler, Default Parquet Backend & Multi-Source TTM
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["parquet", "ducklake"])
def test_publish_handler_reads_clean_facts_once_and_respects_publish_unmapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    data_root = tmp_path / "data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    curated = data_root / "curated"
    curated.mkdir(parents=True, exist_ok=True)
    _sample_issuers().write_parquet(curated / "issuers.parquet")

    run_id = "run-single-read"
    state.start_run(run_id, "CLI_MANUAL", {"source": "fhsc"}, None)
    q_dir = curated / "quality" / run_id
    q_dir.mkdir(parents=True, exist_ok=True)
    clean_facts_path = q_dir / "clean_financial_facts.parquet"
    _sample_facts().write_parquet(clean_facts_path)

    norm_dir = curated / "financial_facts" / run_id
    norm_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "issuer_id": ["FPT"],
            "filing_id": ["f-u-1"],
            "raw_metric_key": ["999"],
            "raw_value": ["1"],
            "statement_scope": ["CONSOLIDATED"],
            "period_type": ["QUARTER"],
            "period_end_date": [date(2025, 12, 31)],
            "effective_available_at": [date(2026, 1, 25)],
        }
    ).write_parquet(norm_dir / "unmapped_metrics.parquet")

    real_read_parquet = pl.read_parquet
    clean_reads = 0

    def spy_read_parquet(
        source: object, *args: object, **kwargs: object
    ) -> pl.DataFrame:
        nonlocal clean_reads
        if (
            isinstance(source, (str, Path))
            and Path(source).resolve() == clean_facts_path.resolve()
        ):
            clean_reads += 1
        return real_read_parquet(source, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pl, "read_parquet", spy_read_parquet)

    storage_cfg: dict[str, object] = (
        {}
        if backend == "parquet"
        else {
            "backend": "ducklake",
            "catalog": {
                "driver": "sqlite",
                "sqlite_path": "../data/metadata/ducklake_catalog.sqlite",
            },
            "data_path": "../data/lakehouse/data",
        }
    )
    handler = PublishHandler(
        state,
        tmp_path / "config",
        {},
        storage_settings=storage_cfg,
    )
    ctx = StepContext(
        run_id=run_id,
        stage=Stage.PUBLISH,
        request=RunRequest(source="fhsc", stage=Stage.PUBLISH),
        tickers=(),
        active_snapshot_id=None,
    )
    outcome = handler(ctx)
    assert outcome.snapshot_id is not None
    assert clean_reads == 1, (
        f"Expected 1 read of clean_financial_facts, got {clean_reads}"
    )

    repo = ParquetRepository(data_root=data_root)
    assert repo.get_snapshot_metadata()["storage_backend"] == backend
    # Default publish_unmapped=False must omit unmapped_metrics.parquet.
    with pytest.raises(FileNotFoundError):
        repo.read_table("unmapped_metrics.parquet")

    # Explicit publish_unmapped=True publishes unmapped_metrics.parquet.
    run_id_2 = "run-with-unmapped"
    state.start_run(run_id_2, "CLI_MANUAL", {"source": "fhsc"}, None)
    q_dir_2 = curated / "quality" / run_id_2
    q_dir_2.mkdir(parents=True, exist_ok=True)
    _sample_facts().write_parquet(q_dir_2 / "clean_financial_facts.parquet")
    norm_dir_2 = curated / "financial_facts" / run_id_2
    norm_dir_2.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "issuer_id": ["FPT"],
            "filing_id": ["f-u-2"],
            "raw_metric_key": ["999"],
            "raw_value": ["1"],
            "statement_scope": ["CONSOLIDATED"],
            "period_type": ["QUARTER"],
            "period_end_date": [date(2025, 12, 31)],
            "effective_available_at": [date(2026, 1, 25)],
        }
    ).write_parquet(norm_dir_2 / "unmapped_metrics.parquet")
    handler_unmapped = PublishHandler(
        state,
        tmp_path / "config",
        {},
        storage_settings={**storage_cfg, "publish_unmapped": True},
    )
    ctx_2 = StepContext(
        run_id=run_id_2,
        stage=Stage.PUBLISH,
        request=RunRequest(source="fhsc", stage=Stage.PUBLISH),
        tickers=(),
        active_snapshot_id=outcome.snapshot_id,
    )
    outcome_2 = handler_unmapped(ctx_2)
    assert outcome_2.snapshot_id is not None
    assert repo.read_table("unmapped_metrics.parquet").height == 1


def test_settings_default_to_parquet_backend_when_storage_omitted(
    tmp_path: Path,
) -> None:
    cfg = tmp_path / "config" / "settings.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("state_path: ../data/metadata/state.sqlite\n", encoding="utf-8")
    settings = load_settings(cfg)
    assert settings.storage == {}
    resolved = StorageConfig.from_mapping(
        settings.storage,
        config_dir=cfg.parent,
        data_root=tmp_path / "data",
    )
    assert resolved.backend == "parquet"
    assert resolved.publish_unmapped is False


def test_build_latest_facts_table_preserves_multi_source_ttm_and_provenance() -> None:
    # Build 4 contiguous quarters for TWO distinct sources ("fhsc" and "hose_pdf_extract")
    # with different values so TTM differs per source; verify source_provider join_keys
    # prevents cross-source overwrite and preserves provenance columns.
    quarters = [
        (1, date(2025, 1, 1), date(2025, 3, 31), date(2025, 4, 20)),
        (2, date(2025, 4, 1), date(2025, 6, 30), date(2025, 7, 20)),
        (3, date(2025, 7, 1), date(2025, 9, 30), date(2025, 10, 20)),
        (4, date(2025, 10, 1), date(2025, 12, 31), date(2026, 1, 25)),
    ]
    rows: list[dict[str, object]] = []
    for provider, q_val, rev in (("fhsc", 10.0, 2), ("hose_pdf_extract", 25.0, 1)):
        for q_num, p_start, p_end, avail in quarters:
            rows.append(
                {
                    "issuer_id": "FPT",
                    "statement_scope": "CONSOLIDATED",
                    "period_type": "QUARTER",
                    "fiscal_year_start_date": date(2025, 1, 1),
                    "period_end_date": p_end,
                    "metric_id": "IS_NET_REVENUE",
                    "taxonomy_version": "VAS_TT200_2014",
                    "revision_no": rev,
                    "filing_id": f"{provider}-q{q_num}",
                    "fiscal_year": 2025,
                    "fiscal_quarter": q_num,
                    "period_start_date": p_start,
                    "published_at": avail,
                    "published_at_basis": "EXACT_FILING",
                    "effective_available_at": avail,
                    "revision_reason": "ORIGINAL",
                    "derivation_method": "REPORTED_DIRECTLY",
                    "audit_status": "UNAUDITED",
                    "audit_opinion": "UNKNOWN",
                    "value": q_val,
                    "ttm_value": None,
                    "computation_note": None,
                    "source_provider": provider,
                    "source_filing_ids": [f"{provider}-q{q_num}"],
                    "comparable_across_taxonomy": True,
                    "is_filed_late": False,
                }
            )
    facts_df = pl.DataFrame(rows).with_columns(
        pl.col("fiscal_quarter").cast(pl.Int8),
        pl.col("ttm_value").cast(pl.Float64),
        pl.col("computation_note").cast(pl.String),
    )

    latest = build_latest_facts_table(facts_df, as_of=date(2026, 2, 1))
    q4_row = latest.filter(pl.col("period_end_date") == date(2025, 12, 31))
    assert q4_row.height == 1
    # revision_no=2 ("fhsc") wins PIT resolution, so its TTM MUST be 4*10=40.0
    # (NOT overwritten by "hose_pdf_extract"'s 4*25=100.0!).
    assert q4_row["source_provider"].to_list() == ["fhsc"]
    assert q4_row["ttm_value"].to_list() == [40.0]
    assert q4_row["comparable_across_taxonomy"].to_list() == [True]
    assert q4_row["source_filing_ids"].to_list() == [["fhsc-q4"]]
    assert q4_row["is_filed_late"].to_list() == [False]


# ---------------------------------------------------------------------------
# 8. Independent Review Regressions (P1 #1, P1 #2, P2 & P3 Hardening)
# ---------------------------------------------------------------------------


def test_curated_gc_preserves_asymmetric_analytics_and_quality_for_ticker_scoped_run(
    tmp_path: Path,
) -> None:
    """P1 #1 regression: deleting financial_facts/r1 must NOT erase _normalize_meta.json before
    evaluating quality/r1 and analytics/r1 when cli_args_json contains configured tickers."""
    data_root = tmp_path / "data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    curated = data_root / "curated"
    old_ts = time.time() - 7200

    # r1: full 6-stage run with configured tickers=["FPT"] (db_legacy_full == False),
    # while _normalize_meta.json explicitly records full_history=True.
    state.start_run("r1", "CLI_MANUAL", {"source": "fhsc", "tickers": ["FPT"]}, None)
    state.finish_run("r1", "SUCCEEDED")
    for sub in ("financial_facts", "quality", "analytics"):
        d = curated / sub / "r1"
        d.mkdir(parents=True, exist_ok=True)
    _sample_facts().write_parquet(
        curated / "financial_facts" / "r1" / "financial_facts.parquet"
    )
    (curated / "financial_facts" / "r1" / "_normalize_meta.json").write_text(
        json.dumps({"source": "fhsc", "full_history": True}), encoding="utf-8"
    )
    pl.DataFrame(schema=AUDIT_QUARANTINE_SCHEMA).write_parquet(
        curated / "quality" / "r1" / AUDIT_QUARANTINE_TABLE
    )
    (curated / "analytics" / "r1" / "_analytics_meta.json").write_text(
        json.dumps({"source": "fhsc", "as_of": "2026-06-01"}), encoding="utf-8"
    )
    for sub in ("financial_facts", "quality", "analytics"):
        os.utime(curated / sub / "r1", (old_ts, old_ts))

    # r2 and r3: only completed NORMALIZE (present only under financial_facts/).
    for run_id in ("r2", "r3"):
        state.start_run(
            run_id, "CLI_MANUAL", {"source": "fhsc", "tickers": ["FPT"]}, None
        )
        state.finish_run(run_id, "FAILED")
        ff_dir = curated / "financial_facts" / run_id
        ff_dir.mkdir(parents=True, exist_ok=True)
        _sample_facts().write_parquet(ff_dir / "financial_facts.parquet")
        (ff_dir / "_normalize_meta.json").write_text(
            json.dumps({"source": "fhsc", "full_history": True}), encoding="utf-8"
        )
        os.utime(ff_dir, (old_ts, old_ts))

    removed = collect_curated_garbage(
        data_root, state, keep_runs=2, min_age_seconds=3600
    )

    # financial_facts/r1 is pruned (r2 & r3 are newest 2), but quality/r1 and analytics/r1
    # MUST survive because r1 is the sole full_history run for fhsc in those subdirs.
    assert not (curated / "financial_facts" / "r1").exists()
    assert (curated / "financial_facts" / "r2").is_dir()
    assert (curated / "financial_facts" / "r3").is_dir()
    assert (curated / "quality" / "r1").is_dir()
    assert (curated / "analytics" / "r1").is_dir()
    assert [p.resolve() for p in removed] == [
        (curated / "financial_facts" / "r1").resolve()
    ]


def test_curated_gc_waits_for_pipeline_lock_and_skips_symlinks_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify curated_gc serializes on pipeline.lock and skips symlinked entries."""
    data_root = tmp_path / "data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    curated = data_root / "curated"

    # Seed an aged directory marked as a symlink via Path.is_symlink monkeypatch so
    # symlink protection is deterministically verified even on non-privileged Windows.
    symlink_dir = curated / "financial_facts" / "run-symlink-fake"
    symlink_dir.mkdir(parents=True, exist_ok=True)
    (symlink_dir / "keep.txt").write_text("precious", encoding="utf-8")
    old_ts = time.time() - 7200
    os.utime(symlink_dir, (old_ts, old_ts))

    real_is_symlink = Path.is_symlink

    def fake_is_symlink(self: Path) -> bool:
        if self.name == "run-symlink-fake":
            return True
        return real_is_symlink(self)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)

    lock_path = state.path.with_name("pipeline.lock")
    entered = threading.Event()
    release = threading.Event()
    gc_done = threading.Event()
    removed_out: list[Path] = []

    def hold_lock() -> None:
        with FileLock(lock_path, timeout=5.0):
            entered.set()
            assert release.wait(timeout=5.0)

    def run_gc() -> None:
        removed_out.extend(
            collect_curated_garbage(data_root, state, keep_runs=1, min_age_seconds=0)
        )
        gc_done.set()

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert entered.wait(timeout=5.0)
    worker = threading.Thread(target=run_gc)
    worker.start()
    time.sleep(0.15)
    assert not gc_done.is_set(), (
        "collect_curated_garbage must wait for pipeline.lock serialization"
    )
    release.set()
    holder.join(timeout=5.0)
    worker.join(timeout=10.0)
    assert gc_done.is_set()
    assert not removed_out
    assert (symlink_dir / "keep.txt").is_file()


@pytest.mark.parametrize("backend_mode", ["ducklake", "auto"])
def test_repository_supports_postgres_catalog_driver_without_local_sqlite_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend_mode: str,
) -> None:
    """P1 #2 regression: ParquetRepository with catalog_driver='postgres' MUST NOT require
    a local ducklake_catalog.sqlite file and must invoke ensure_ducklake_attached."""
    # Publish snap_001 into an isolated backing DuckLake catalog simulating remote Postgres.
    remote_root = tmp_path / "remote_pg_sim"
    remote_state = StateStore(remote_root / "metadata" / "state.sqlite")
    remote_state.initialize()
    remote_store = DuckLakeStore(
        catalog_path=remote_root / "metadata" / "backing_catalog.sqlite",
        data_path=remote_root / "lakehouse" / "data",
        state_store=remote_state,
    )
    snap_id, ver = _publish_ducklake_version(remote_state, remote_store, 1)

    # Local deployment data_root has ONLY state.sqlite (NO local ducklake_catalog.sqlite).
    local_root = tmp_path / "local_pg_deploy"
    local_state = StateStore(local_root / "metadata" / "state.sqlite")
    local_state.initialize()
    local_state.start_run("run-1", "CLI_MANUAL", {"source": "fhsc"}, None)
    local_state.publish_snapshot("run-1", snap_id)
    assert not (local_root / "metadata" / "ducklake_catalog.sqlite").exists()

    attach_calls: list[tuple[str, str, bool]] = []
    real_attach = ensure_ducklake_attached

    def mocked_attach(
        connection: duckdb.DuckDBPyConnection,
        *,
        catalog_path: Path,
        data_path: Path,
        catalog_alias: str = "finintel_lake",
        catalog_driver: str = "sqlite",
        postgres_dsn_env: str = "FININTEL_DUCKLAKE_PG_DSN",
        read_only: bool = True,
    ) -> None:
        _ = (catalog_path, data_path)
        attach_calls.append((catalog_driver, postgres_dsn_env, read_only))
        real_attach(
            connection,
            catalog_path=remote_store.catalog_path,
            data_path=remote_store.data_path,
            catalog_alias=catalog_alias,
            catalog_driver="sqlite",
            read_only=read_only,
        )

    monkeypatch.setattr(
        "finintel.storage.repository.ensure_ducklake_attached", mocked_attach
    )

    repo = ParquetRepository(
        data_root=local_root,
        catalog_driver="postgres",
        postgres_dsn_env="FININTEL_TEST_PG_DSN",
        backend="ducklake" if backend_mode == "ducklake" else "auto",
    )
    meta = repo.get_snapshot_metadata()
    assert meta["storage_backend"] == "ducklake"
    assert meta["snapshot_id"] == snap_id
    assert meta["ducklake_version"] == ver
    assert attach_calls
    assert all(
        drv == "postgres" and env == "FININTEL_TEST_PG_DSN" and ro is True
        for drv, env, ro in attach_calls
    )

    with repo.snapshot_session() as pinned:
        issuers = pinned.read_table("issuers", filters={"issuer_id": "FPT"})
        assert issuers["company_name_vn"].to_list() == ["FPT Corp v1"]


def test_postgres_attach_redacts_password_on_duckdb_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2 security regression: DuckDB errors during Postgres CREATE SECRET must not leak password."""
    secret_pw = "UltraSecret_PgPass!77"
    monkeypatch.setenv(
        "FININTEL_TEST_PG_ERR_DSN",
        f"postgresql://lake_user:{secret_pw}@db.internal:5432/finintel_cat",
    )

    class _FakeResult:
        def fetchone(self) -> None:
            return None

    class _FailingDuckDBConn:
        def execute(self, sql: str, params: object = None) -> _FakeResult:
            _ = params
            if "CREATE OR REPLACE SECRET" in sql:
                raise duckdb.BinderException(f"Simulated error in SQL: {sql}")
            return _FakeResult()

    fake_conn: object = _FailingDuckDBConn()
    with pytest.raises(ContractViolationError) as exc_info:
        ensure_ducklake_attached(
            fake_conn,  # type: ignore[arg-type]
            catalog_path=tmp_path / "unused.sqlite",
            data_path=tmp_path / "lake_data",
            catalog_driver="postgres",
            postgres_dsn_env="FININTEL_TEST_PG_ERR_DSN",
        )
    assert secret_pw not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


def test_parquet_snapshot_session_get_facts_as_of_reuses_session_connection(
    tmp_path: Path,
) -> None:
    """P3 regression: get_facts_as_of() in Parquet snapshot_session reuses non-owning connection."""
    data_root = tmp_path / "data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    state.start_run("run-1", "CLI_MANUAL", {}, None)
    SnapshotPublisher(data_root / "published" / "snapshots", state_store=state).publish(
        "snap_001", {"financial_facts.parquet": _sample_facts()}, run_id="run-1"
    )

    repo = ParquetRepository(data_root=data_root)
    base_conn = duckdb.connect(":memory:")
    cursor = base_conn.cursor()
    try:
        with repo.snapshot_session(connection=cursor) as pinned:
            facts = pinned.get_facts_as_of(date(2026, 2, 1))
            assert facts.height == 3
        # Non-owning cursor MUST remain open after session exit.
        assert cursor.execute("SELECT 42").fetchone() == (42,)
    finally:
        cursor.close()
        base_conn.close()


def test_ducklake_snapshot_cache_hit_eliminates_catalog_and_where_1_eq_0_roundtrips_and_invalidates_on_publish_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEFECT-B6-01 regression: cached DuckLake reads do zero redundant WHERE 1=0 / catalog queries and immediately observe publish + rollback."""
    data_root, state, store = _make_ducklake_env(tmp_path, keep_snapshots=3)
    _publish_ducklake_version(state, store, 1)

    repo = ParquetRepository(data_root=data_root)

    # 1. Cold pass warms binding, table resolution, and table data caches.
    with repo.snapshot_session() as pinned1:
        assert pinned1.snapshot_id == "snap_001"
        meta1 = pinned1.get_snapshot_metadata()
        assert meta1 is not None
        assert meta1["snapshot_id"] == "snap_001"
        cols1 = pinned1.get_table_columns("issuers.parquet")
        assert cols1 is not None and "ticker" in cols1
        df1 = pinned1.read_table(
            "issuers.parquet",
            columns=["issuer_id", "ticker", "company_name_vn"],
            filters={"ticker": "FPT"},
        )
        assert df1["company_name_vn"].to_list() == ["FPT Corp v1"]

    # 2. Warm pass: spy on ensure_ducklake_attached and connection.execute to prove zero
    # redundant catalog / _snapshot_audit / WHERE 1=0 queries run on cache hits.
    import finintel.storage.repository as repo_mod

    attach_calls: list[str] = []
    orig_attach = ensure_ducklake_attached

    def _spy_attach(
        connection: duckdb.DuckDBPyConnection,
        *,
        catalog_path: Path,
        data_path: Path,
        catalog_alias: str = "finintel_lake",
        read_only: bool = True,
        catalog_driver: str = "sqlite",
        postgres_dsn_env: str = "FININTEL_DUCKLAKE_PG_DSN",
    ) -> None:
        attach_calls.append("attach")
        orig_attach(
            connection,
            catalog_path=catalog_path,
            data_path=data_path,
            catalog_alias=catalog_alias,
            read_only=read_only,
            catalog_driver="postgres" if catalog_driver == "postgres" else "sqlite",
            postgres_dsn_env=postgres_dsn_env,
        )

    monkeypatch.setattr(repo_mod, "ensure_ducklake_attached", _spy_attach)

    executed_sql: list[str] = []

    class _SpyCursor:
        def __init__(self, inner: duckdb.DuckDBPyConnection) -> None:
            self._inner = inner

        def execute(self, sql: str, params: object = None) -> duckdb.DuckDBPyConnection:
            executed_sql.append(sql)
            if params is not None:
                return self._inner.execute(sql, params)
            return self._inner.execute(sql)

    base_conn = duckdb.connect(":memory:")
    spy_conn = _SpyCursor(base_conn)
    try:
        with repo.snapshot_session(
            connection=spy_conn  # type: ignore[arg-type]
        ) as pinned_warm:
            assert pinned_warm.snapshot_id == "snap_001"
            warm_meta = pinned_warm.get_snapshot_metadata()
            assert warm_meta is not None and warm_meta["snapshot_id"] == "snap_001"
            assert pinned_warm.get_table_columns("issuers.parquet") == cols1
            warm_df = pinned_warm.read_table(
                "issuers.parquet",
                columns=["issuer_id", "ticker", "company_name_vn"],
                filters={"ticker": "FPT"},
            )
            assert warm_df["company_name_vn"].to_list() == ["FPT Corp v1"]
    finally:
        base_conn.close()

    assert attach_calls == []
    assert not any("WHERE 1 = 0" in sql for sql in executed_sql)
    assert not any("_snapshot_audit" in sql for sql in executed_sql)

    # Restore real ensure_ducklake_attached before publishing snap_002.
    monkeypatch.setattr(repo_mod, "ensure_ducklake_attached", orig_attach)

    # 3. Publish snap_002 -> repository must immediately observe snap_002 with zero stale reads.
    _publish_ducklake_version(state, store, 2)
    with repo.snapshot_session() as pinned2:
        assert pinned2.snapshot_id == "snap_002"
        meta2 = pinned2.get_snapshot_metadata()
        assert meta2 is not None and meta2["snapshot_id"] == "snap_002"
        df2 = pinned2.read_table(
            "issuers.parquet",
            columns=["issuer_id", "ticker", "company_name_vn"],
            filters={"ticker": "FPT"},
        )
        assert df2["company_name_vn"].to_list() == ["FPT Corp v2"]

    # 4. Rollback to snap_001 -> repository must immediately observe snap_001 again.
    store.rollback_snapshot("snap_001", state)
    with repo.snapshot_session() as pinned_rb:
        assert pinned_rb.snapshot_id == "snap_001"
        meta_rb = pinned_rb.get_snapshot_metadata()
        assert meta_rb is not None and meta_rb["snapshot_id"] == "snap_001"
        df_rb = pinned_rb.read_table(
            "issuers.parquet",
            columns=["issuer_id", "ticker", "company_name_vn"],
            filters={"ticker": "FPT"},
        )
        assert df_rb["company_name_vn"].to_list() == ["FPT Corp v1"]


def test_ducklake_hash_skip_reuses_unchanged_parquet_files_and_preserves_extension_ports(
    tmp_path: Path,
) -> None:
    """ARCH-07a (B-03): unchanged mart tables skip CREATE OR REPLACE TABLE, reuse Parquet files,
    remain in new_catalog_tables (never dropped), preserve Extension Ports, and survive maintain/rollback."""
    data_root, state, store = _make_ducklake_env(
        tmp_path, keep_snapshots=2, min_snapshot_age_seconds=0
    )
    issuers_v1 = _sample_issuers("steady")
    facts_v1 = _sample_facts(value_offset=0.0)
    latest_v1 = build_latest_facts_table(facts_v1, as_of=date(2026, 2, 1))
    manifest_v1 = _sample_manifest("steady")
    ratios_v1 = _sample_ratios(row_count=6, value_offset=0.0)

    state.start_run("run-hs-1", "CLI_MANUAL", {"source": "fhsc"}, None)
    ver1 = store.publish_snapshot(
        "snap_hs_001",
        {
            "issuers.parquet": issuers_v1,
            "financial_facts.parquet": facts_v1,
            "latest_facts.parquet": latest_v1,
            FILINGS_MANIFEST_TABLE: manifest_v1,
            RATIOS_TABLE: ratios_v1,
        },
        run_id="run-hs-1",
    )
    state.finish_run("run-hs-1", "SUCCEEDED", "snap_hs_001")

    def _list_files(table: str, version: int) -> tuple[str, ...]:
        con = duckdb.connect(":memory:")
        try:
            ensure_ducklake_attached(
                con,
                catalog_path=store.catalog_path,
                data_path=store.data_path,
                catalog_alias=store.catalog_alias,
                read_only=True,
            )
            rows = con.execute(
                "SELECT data_file FROM ducklake_list_files(?, ?, snapshot_version => ?) ORDER BY 1",
                [store.catalog_alias, table, version],
            ).fetchall()
            return tuple(str(r[0]) for r in rows)
        finally:
            con.close()

    issuers_files_v1 = _list_files("issuers", ver1)
    facts_files_v1 = _list_files("financial_facts", ver1)
    manifest_files_v1 = _list_files("filings_manifest", ver1)
    ratios_files_v1 = _list_files("financial_ratios", ver1)
    assert issuers_files_v1 and facts_files_v1 and manifest_files_v1 and ratios_files_v1

    # Publish snap_hs_002: ONLY financial_ratios changes; issuers is passed in reversed row order
    # to verify canonical hash determinism.
    ratios_v2 = _sample_ratios(row_count=6, value_offset=0.05)
    state.start_run("run-hs-2", "CLI_MANUAL", {"source": "fhsc"}, None)
    ver2 = store.publish_snapshot(
        "snap_hs_002",
        {
            "issuers.parquet": issuers_v1.reverse(),
            "financial_facts.parquet": facts_v1,
            "latest_facts.parquet": latest_v1,
            FILINGS_MANIFEST_TABLE: manifest_v1,
            RATIOS_TABLE: ratios_v2,
        },
        run_id="run-hs-2",
    )
    state.finish_run("run-hs-2", "SUCCEEDED", "snap_hs_002")
    assert ver2 == ver1 + 1

    # Unchanged tables MUST reuse exact Parquet file paths (zero write amplification).
    assert _list_files("issuers", ver2) == issuers_files_v1
    assert _list_files("financial_facts", ver2) == facts_files_v1
    assert _list_files("filings_manifest", ver2) == manifest_files_v1
    # Changed table MUST write a fresh Parquet file.
    ratios_files_v2 = _list_files("financial_ratios", ver2)
    assert ratios_files_v2 != ratios_files_v1

    # Verify audit metadata records skipped_tables accurately.
    con = duckdb.connect(":memory:")
    try:
        ensure_ducklake_attached(
            con,
            catalog_path=store.catalog_path,
            data_path=store.data_path,
            catalog_alias=store.catalog_alias,
            read_only=True,
        )
        raw_meta = con.execute(
            f'SELECT metadata_json FROM "{store.catalog_alias}"."_snapshot_audit" '
            "WHERE snapshot_id = 'snap_hs_002'"
        ).fetchone()
        assert raw_meta is not None and raw_meta[0] is not None
        parsed_meta = json.loads(str(raw_meta[0]))
        assert set(parsed_meta["skipped_tables"]) == {
            "issuers",
            "financial_facts",
            "latest_facts",
            "filings_manifest",
        }
    finally:
        con.close()

    # Add an Extension Port table outside MANAGED_MART_TABLES, then publish snap_hs_003.
    rw_con = store._open_rw_connection()
    try:
        rw_con.execute(
            f'CREATE TABLE "{store.catalog_alias}"."regulatory_events" '
            "(issuer_id VARCHAR, event_type VARCHAR);"
        )
        rw_con.execute(
            f'INSERT INTO "{store.catalog_alias}"."regulatory_events" '
            "VALUES ('FPT', 'WARNING');"
        )
    finally:
        rw_con.close()

    ratios_v3 = _sample_ratios(row_count=6, value_offset=0.10)
    state.start_run("run-hs-3", "CLI_MANUAL", {"source": "fhsc"}, None)
    ver3 = store.publish_snapshot(
        "snap_hs_003",
        {
            "issuers.parquet": issuers_v1,
            "financial_facts.parquet": facts_v1,
            "latest_facts.parquet": latest_v1,
            FILINGS_MANIFEST_TABLE: manifest_v1,
            RATIOS_TABLE: ratios_v3,
        },
        run_id="run-hs-3",
    )
    state.finish_run("run-hs-3", "SUCCEEDED", "snap_hs_003")
    assert ver3 > ver2

    # Extension Port table MUST still exist and be readable.
    ro_con = duckdb.connect(":memory:")
    try:
        ensure_ducklake_attached(
            ro_con,
            catalog_path=store.catalog_path,
            data_path=store.data_path,
            catalog_alias=store.catalog_alias,
            read_only=True,
        )
        port_rows = ro_con.execute(
            f'SELECT issuer_id, event_type FROM "{store.catalog_alias}"."regulatory_events"'
        ).fetchall()
        assert port_rows == [("FPT", "WARNING")]
    finally:
        ro_con.close()

    # Run maintain(keep_snapshots=2, min_snapshot_age_seconds=0) -> expires ver1,
    # while unchanged Parquet files referenced by ver2/ver3 remain intact & readable.
    store.maintain(keep_snapshots=2, min_snapshot_age_seconds=0)
    repo = ParquetRepository(data_root=data_root)
    with repo.snapshot_session() as pinned3:
        assert pinned3.snapshot_id == "snap_hs_003"
        assert pinned3.read_table("issuers.parquet").height == 3
        assert pinned3.read_table(RATIOS_TABLE).height == 6

    store.rollback_snapshot("snap_hs_002", state)
    with repo.snapshot_session() as pinned2:
        assert pinned2.snapshot_id == "snap_hs_002"
        assert pinned2.read_table("issuers.parquet").height == 3


def test_ducklake_hash_skip_all_tables_unchanged_still_commits_audit_snapshot(
    tmp_path: Path,
) -> None:
    """ARCH-07a: publishing 100% identical tables still commits a new audited snapshot version
    while writing zero duplicate Parquet files for mart tables."""
    data_root, state, store = _make_ducklake_env(tmp_path)
    issuers_df = _sample_issuers("same")
    facts_df = _sample_facts(value_offset=0.0)
    latest_df = build_latest_facts_table(facts_df, as_of=date(2026, 2, 1))
    tables = {
        "issuers.parquet": issuers_df,
        "financial_facts.parquet": facts_df,
        "latest_facts.parquet": latest_df,
    }

    state.start_run("run-same-1", "CLI_MANUAL", {"source": "fhsc"}, None)
    ver1 = store.publish_snapshot("snap_same_001", tables, run_id="run-same-1")
    state.finish_run("run-same-1", "SUCCEEDED", "snap_same_001")

    state.start_run("run-same-2", "CLI_MANUAL", {"source": "fhsc"}, None)
    ver2 = store.publish_snapshot("snap_same_002", tables, run_id="run-same-2")
    state.finish_run("run-same-2", "SUCCEEDED", "snap_same_002")
    assert ver2 == ver1 + 1

    con = duckdb.connect(":memory:")
    try:
        ensure_ducklake_attached(
            con,
            catalog_path=store.catalog_path,
            data_path=store.data_path,
            catalog_alias=store.catalog_alias,
            read_only=True,
        )
        for tbl in ("issuers", "financial_facts", "latest_facts"):
            f1 = con.execute(
                "SELECT data_file FROM ducklake_list_files(?, ?, snapshot_version => ?) ORDER BY 1",
                [store.catalog_alias, tbl, ver1],
            ).fetchall()
            f2 = con.execute(
                "SELECT data_file FROM ducklake_list_files(?, ?, snapshot_version => ?) ORDER BY 1",
                [store.catalog_alias, tbl, ver2],
            ).fetchall()
            assert f1 == f2
    finally:
        con.close()

    repo = ParquetRepository(data_root=data_root)
    with repo.snapshot_session() as pinned:
        assert pinned.snapshot_id == "snap_same_002"
        assert pinned.read_table("issuers.parquet").height == 3
        assert pinned.read_table("financial_facts.parquet").height == 3


def test_ducklake_maintain_and_reconcile_repair_stale_postgres_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2-7: maintain() and reconcile_unpromoted_snapshots() repair stale non-None Postgres pointer."""
    from finintel.storage.ducklake_store import PostgresActiveSnapshotPointer

    data_root = tmp_path / "pg_reconcile_data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    backing_sqlite = data_root / "metadata" / "backing_catalog.sqlite"
    real_attach = ensure_ducklake_attached

    def mocked_attach(
        connection: duckdb.DuckDBPyConnection,
        *,
        catalog_path: Path,
        data_path: Path,
        catalog_alias: str = "finintel_lake",
        catalog_driver: str = "sqlite",
        postgres_dsn_env: str = "FININTEL_DUCKLAKE_PG_DSN",
        read_only: bool = True,
        override_data_path: bool = False,
    ) -> None:
        _ = (catalog_path, catalog_driver, postgres_dsn_env, override_data_path)
        real_attach(
            connection,
            catalog_path=backing_sqlite,
            data_path=data_path,
            catalog_alias=catalog_alias,
            catalog_driver="sqlite",
            read_only=read_only,
        )

    pg_box: dict[str, PostgresActiveSnapshotPointer | None] = {"ptr": None}

    def fake_upsert(
        *,
        postgres_dsn_env: str,
        catalog_alias: str,
        snapshot_id: str,
        ducklake_version: int,
        run_id: str,
        published_at: str,
    ) -> None:
        _ = (postgres_dsn_env, catalog_alias)
        pg_box["ptr"] = PostgresActiveSnapshotPointer(
            snapshot_id=snapshot_id,
            ducklake_version=ducklake_version,
            run_id=run_id,
            published_at=published_at,
        )

    def fake_read(
        *, postgres_dsn_env: str, catalog_alias: str
    ) -> PostgresActiveSnapshotPointer | None:
        _ = (postgres_dsn_env, catalog_alias)
        return pg_box["ptr"]

    monkeypatch.setattr(
        "finintel.storage.ducklake_store.ensure_ducklake_attached", mocked_attach
    )
    monkeypatch.setattr(
        "finintel.storage.ducklake_store.upsert_postgres_active_snapshot", fake_upsert
    )
    monkeypatch.setattr(
        "finintel.storage.ducklake_store.read_postgres_active_snapshot", fake_read
    )

    store = DuckLakeStore(
        catalog_path=backing_sqlite,
        data_path=data_root / "lakehouse" / "data",
        state_store=state,
        catalog_driver="postgres",
        postgres_dsn_env="FININTEL_P27_PG_DSN",
        keep_snapshots=2,
        min_snapshot_age_seconds=0,
    )

    state.start_run("run-1", "CLI_MANUAL", {"source": "fhsc"}, None)
    ver1 = store.publish_snapshot(
        "snap_p27_001",
        {
            "issuers.parquet": _sample_issuers("v1"),
            "financial_facts.parquet": _sample_facts(value_offset=1.0),
        },
        run_id="run-1",
    )
    state.finish_run("run-1", "SUCCEEDED", "snap_p27_001")

    state.start_run("run-2", "CLI_MANUAL", {"source": "fhsc"}, None)
    ver2 = store.publish_snapshot(
        "snap_p27_002",
        {
            "issuers.parquet": _sample_issuers("v2"),
            "financial_facts.parquet": _sample_facts(value_offset=2.0),
        },
        run_id="run-2",
    )
    state.finish_run("run-2", "SUCCEEDED", "snap_p27_002")

    # Simulate Postgres pointer left stuck at snap_p27_001 while state.sqlite promoted snap_p27_002.
    stale_ptr = PostgresActiveSnapshotPointer(
        snapshot_id="snap_p27_001",
        ducklake_version=ver1,
        run_id="run-1",
        published_at="2026-01-01T00:00:00+00:00",
    )
    pg_box["ptr"] = stale_ptr
    orphans = store.reconcile_unpromoted_snapshots()
    assert orphans == []
    assert pg_box["ptr"] is not None
    assert pg_box["ptr"].snapshot_id == "snap_p27_002"
    assert pg_box["ptr"].ducklake_version == ver2

    # Simulate stale Postgres pointer again and verify maintain() reconciles it to snap_p27_002.
    pg_box["ptr"] = stale_ptr
    maint = store.maintain(keep_snapshots=2, min_snapshot_age_seconds=0)
    assert pg_box["ptr"] is not None
    assert pg_box["ptr"].snapshot_id == "snap_p27_002"
    assert pg_box["ptr"].ducklake_version == ver2
    assert ver1 in maint["protected_versions"]  # type: ignore[operator]
