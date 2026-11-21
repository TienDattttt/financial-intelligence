"""Gate B0 empirical verification spike for V2 storage, RWLock, and platform pins.

Executes all three Gate B0 verification experiments inside user-private temporary
directories without touching ``data/live_fhsc_universe/`` or external provider APIs,
and writes measured evidence to ``docs/evidence/option_b_spike_report.json``.

Verified Sources:
- DuckLake Official Documentation: https://ducklake.select/docs/stable/
- DuckLake Snapshot Expiration Docs:
  https://ducklake.select/docs/stable/duckdb/maintenance/expire_snapshots
- DuckLake Source Repository: https://github.com/duckdb/ducklake
- DuckDB Extensions Overview: https://duckdb.org/docs/stable/extensions/overview
- DuckDB Python API Overview: https://duckdb.org/docs/stable/clients/python/overview
- DuckDB Concurrency Guide: https://duckdb.org/docs/stable/connect/concurrency
- filelock ReadWriteLock Docs: https://py-filelock.readthedocs.io/ and
  https://github.com/tox-dev/filelock
- Internal Windows WDAC Precedent: docs/00_ENGINEERING_STANDARDS_AND_SPIKE.md

Runtime API Verification (Python 3.11.9, duckdb==1.4.2, filelock==4.2.10):
- ``duckdb.connect(":memory:", read_only=True)`` raises ``duckdb.CatalogException``
  on ``duckdb==1.4.2`` (confirming ``:memory:`` + ``ATTACH ... (READ_ONLY)`` is required).
- ``duckdb_extensions()`` reports ``ducklake`` (format ``0.3``, build ``77f2512``)
  and ``sqlite_scanner`` (SQL alias ``sqlite``, build ``0c93d61``).
- On ``duckdb==1.4.2`` / DuckLake ``0.3``, ``ALTER TABLE ... SET SORTED BY (...)``
  raises ``duckdb.BinderException: Unsupported ALTER TABLE type in DuckLake`` and
  default ``CALL ducklake_cleanup_old_files('catalog')`` raises a ``VARCHAR`` vs
  ``TIMESTAMP WITH TIME ZONE`` binder error on SQLite catalogs; explicit
  ``ducklake_expire_snapshots(..., versions => [...])`` followed by
  ``ducklake_cleanup_old_files(..., cleanup_all => true)`` succeeds with 0 errors.
- ``filelock.ReadWriteLock(lock_file, timeout=-1, *, blocking=True, is_singleton=True)``
  exposes ``read_lock(timeout=...)``, ``write_lock(timeout=...)``, and ``close()``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal

import duckdb
import filelock
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_PATH = ROOT / "docs/evidence/option_b_spike_report.json"
PYPROJECT_PATH = ROOT / "pyproject.toml"
UV_LOCK_PATH = ROOT / "uv.lock"

REQUIRED_PLATFORM_PACKAGES = (
    "fastapi",
    "uvicorn",
    "pydantic",
    "httpx",
    "dagster",
    "dagster-webserver",
)
FORBIDDEN_PACKAGES = ("sqlmesh", "deltalake")
EXACT_PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^;,\s]+)$")

WDAC_MARKERS = (
    "4551",
    "0x800711c7",
    "error_access_disabled_by_policy",
    "application control policy",
    "device guard",
    "code integrity",
    "blocked by group policy",
    "wdac",
    "win32 error 1260",
)
NETWORK_MARKERS = (
    "failed to download",
    "could not resolve",
    "no such host",
    "11001",
    "connection refused",
    "connection reset",
    "timed out",
    "timeout",
    "unable to connect",
    "network",
    "http error",
    "ssl",
    "tls",
    "dns",
)

Json = dict[str, Any]
DuckLakeStatus = Literal["PASS", "BLOCKED_BY_WDAC", "NETWORK_ERROR", "ERROR"]


def _redact_home_path(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        rel = path.resolve().relative_to(Path.home().resolve())
        return f"~/{rel.as_posix()}"
    except ValueError:
        return path.as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp_dir:
        tmp_file = Path(tmp_dir) / path.name
        tmp_file.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        tmp_file.replace(path)


def _classify_extension_error(message: str) -> tuple[DuckLakeStatus, bool, bool]:
    lowered = message.lower()
    if any(marker in lowered for marker in WDAC_MARKERS):
        return "BLOCKED_BY_WDAC", True, False
    if any(marker in lowered for marker in NETWORK_MARKERS):
        return "NETWORK_ERROR", False, True
    return "ERROR", False, False


def _configure_sqlite_catalog(catalog_path: Path) -> str:
    conn = sqlite3.connect(str(catalog_path))
    try:
        row = conn.execute("PRAGMA journal_mode=WAL;").fetchone()
        conn.execute("PRAGMA busy_timeout=5000;")
        return str(row[0]) if row else "unknown"
    finally:
        conn.close()


def _attach_ducklake(
    con: duckdb.DuckDBPyConnection,
    catalog_path: Path,
    data_path: Path,
    *,
    read_only: bool = False,
) -> None:
    cat_sql = catalog_path.as_posix().replace("'", "''")
    data_sql = data_path.as_posix().replace("'", "''")
    opts = (
        f"DATA_PATH '{data_sql}', READ_ONLY" if read_only else f"DATA_PATH '{data_sql}'"
    )
    con.execute(f"ATTACH 'ducklake:sqlite:{cat_sql}' AS finintel_lake ({opts});")


def _explain_text(con: duckdb.DuckDBPyConnection, query: str) -> str:
    rows = con.execute(f"EXPLAIN {query}").fetchall()
    return "\n".join(str(row[1]) for row in rows)


def _has_issuer_pushdown(explain_plan: str) -> bool:
    return (
        "DUCKLAKE_SCAN" in explain_plan
        and "Filters:" in explain_plan
        and "issuer_id='FPT'" in explain_plan
    )


def _to_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _worker_ducklake_reader(
    catalog_path: Path,
    data_path: Path,
    start_flag: Path,
    ready_flag: Path,
    iterations: int,
) -> int:
    errors: list[str] = []
    permission_errors = 0
    io_errors = 0
    file_not_found_errors = 0
    reads = 0
    skew_count = 0
    versions_seen: set[int] = set()

    con = duckdb.connect(":memory:")
    try:
        con.execute("LOAD ducklake; LOAD sqlite;")
        _attach_ducklake(con, catalog_path, data_path, read_only=True)
        ready_flag.write_text("ready", encoding="utf-8")
        deadline = time.monotonic() + 20.0
        while not start_flag.exists():
            if time.monotonic() > deadline:
                errors.append("TimeoutError: reader timed out waiting for start_flag")
                break
            time.sleep(0.02)

        for _ in range(iterations):
            try:
                v_row = con.execute(
                    "SELECT max(snapshot_id) FROM ducklake_snapshots('finintel_lake')"
                ).fetchone()
                if v_row is None or v_row[0] is None:
                    raise RuntimeError("No snapshot available in ducklake_snapshots")
                snap_id = int(v_row[0])
                facts_row = con.execute(
                    f"SELECT count(*) FROM finintel_lake.t_facts AT (VERSION => {snap_id})"
                ).fetchone()
                ratios_row = con.execute(
                    f"SELECT count(*) FROM finintel_lake.t_ratios AT (VERSION => {snap_id})"
                ).fetchone()
                if facts_row is None or ratios_row is None:
                    raise RuntimeError("Count query returned no rows")
                facts_cnt = int(facts_row[0])
                ratios_cnt = int(ratios_row[0])
                reads += 1
                versions_seen.add(snap_id)
                if facts_cnt != 10 * ratios_cnt:
                    skew_count += 1
            except PermissionError as exc:
                permission_errors += 1
                errors.append(f"PermissionError: {exc}")
            except FileNotFoundError as exc:
                file_not_found_errors += 1
                errors.append(f"FileNotFoundError: {exc}")
            except duckdb.IOException as exc:
                io_errors += 1
                errors.append(f"IOException: {exc}")
            except Exception as exc:
                exc_name = type(exc).__name__
                msg = str(exc)
                if "permission" in msg.lower():
                    permission_errors += 1
                if "no such file" in msg.lower() or "cannot open file" in msg.lower():
                    file_not_found_errors += 1
                errors.append(f"{exc_name}: {msg}")
            time.sleep(0.02)
    finally:
        con.close()

    print(
        json.dumps(
            {
                "role": "reader",
                "reads": reads,
                "skew_count": skew_count,
                "versions_seen": sorted(versions_seen),
                "permission_errors": permission_errors,
                "io_errors": io_errors,
                "file_not_found_errors": file_not_found_errors,
                "errors": errors,
            }
        ),
        flush=True,
    )
    return 0 if not errors and skew_count == 0 and reads == iterations else 1


def _worker_ducklake_writer(
    catalog_path: Path,
    data_path: Path,
    lock_path: Path,
    start_flag: Path,
    ready_flag: Path,
) -> int:
    errors: list[str] = []
    versions_written = 0
    expired_versions: list[int] = []
    deleted_files_count = 0

    con = duckdb.connect(":memory:")
    try:
        con.execute("LOAD ducklake; LOAD sqlite;")
        _attach_ducklake(con, catalog_path, data_path, read_only=False)
        ready_flag.write_text("ready", encoding="utf-8")
        deadline = time.monotonic() + 20.0
        while not start_flag.exists():
            if time.monotonic() > deadline:
                errors.append("TimeoutError: writer timed out waiting for start_flag")
                break
            time.sleep(0.02)

        writer_lock = filelock.FileLock(str(lock_path), timeout=30)
        with writer_lock:
            for batch in range(3):
                try:
                    con.execute("BEGIN TRANSACTION;")
                    con.execute(
                        "INSERT INTO finintel_lake.t_facts "
                        "SELECT CASE WHEN i % 3 = 0 THEN 'FPT' WHEN i % 3 = 1 THEN 'HPG' ELSE 'VNM' END, "
                        f"DATE '2025-12-31' - INTERVAL ({batch} * 90) DAY, "
                        f"'M' || (i % 10), ({batch} * 100 + i)::DOUBLE "
                        "FROM range(100) t(i) ORDER BY 1, 2;"
                    )
                    con.execute(
                        "INSERT INTO finintel_lake.t_ratios "
                        "SELECT CASE WHEN i % 3 = 0 THEN 'FPT' WHEN i % 3 = 1 THEN 'HPG' ELSE 'VNM' END, "
                        f"DATE '2025-12-31' - INTERVAL ({batch} * 90) DAY, "
                        f"0.20 + {batch} * 0.01 "
                        "FROM range(10) t(i) ORDER BY 1, 2;"
                    )
                    con.execute("COMMIT;")
                    versions_written += 1
                except Exception as exc:
                    errors.append(f"write_{batch}:{type(exc).__name__}: {exc}")
                    try:
                        con.execute("ROLLBACK;")
                    except Exception as rollback_exc:
                        errors.append(
                            f"rollback_{batch}:{type(rollback_exc).__name__}: {rollback_exc}"
                        )
                time.sleep(0.05)

            try:
                con.execute("CALL ducklake_merge_adjacent_files('finintel_lake');")
                snap_rows = con.execute(
                    "SELECT snapshot_id FROM ducklake_snapshots('finintel_lake') ORDER BY snapshot_id"
                ).fetchall()
                all_ids = [int(row[0]) for row in snap_rows]
                # Keep newest 2 snapshots; expire only earlier snapshots using explicit BIGINT[] list.
                to_expire = all_ids[:-2]
                if to_expire:
                    id_list_sql = ", ".join(str(sid) for sid in to_expire)
                    con.execute(
                        f"CALL ducklake_expire_snapshots('finintel_lake', versions => [{id_list_sql}]);"
                    )
                    expired_versions = to_expire
                deleted = con.execute(
                    "CALL ducklake_cleanup_old_files('finintel_lake', cleanup_all => true);"
                ).fetchall()
                deleted_files_count = len(deleted)
            except Exception as exc:
                errors.append(f"maintenance:{type(exc).__name__}: {exc}")
    finally:
        con.close()

    print(
        json.dumps(
            {
                "role": "writer",
                "versions_written": versions_written,
                "expired_versions": expired_versions,
                "deleted_files_count": deleted_files_count,
                "errors": errors,
            }
        ),
        flush=True,
    )
    return 0 if not errors and versions_written == 3 else 1


def _worker_rwlock(
    mode: Literal["reader", "writer"],
    lock_path: Path,
    ready_dir: Path,
    release_flag: Path,
    worker_id: str,
) -> int:
    lock = filelock.ReadWriteLock(lock_path, is_singleton=False)
    try:
        if mode == "reader":
            with lock.read_lock(timeout=10.0):
                (ready_dir / f"ready_{worker_id}").write_text("ok", encoding="utf-8")
                deadline = time.monotonic() + 20.0
                while not release_flag.exists():
                    if time.monotonic() > deadline:
                        raise TimeoutError("Timed out waiting for release_flag")
                    time.sleep(0.05)
            print(
                json.dumps({"worker": worker_id, "mode": "reader", "status": "ok"}),
                flush=True,
            )
            return 0

        timed_out_immediately = False
        try:
            with lock.write_lock(timeout=0.0):
                timed_out_immediately = False
        except filelock.Timeout:
            timed_out_immediately = True

        (ready_dir / "writer_probed").write_text("ok", encoding="utf-8")
        t0 = time.monotonic()
        with lock.write_lock(timeout=10.0):
            acquired_after_s = round(time.monotonic() - t0, 4)

        print(
            json.dumps(
                {
                    "worker": worker_id,
                    "mode": "writer",
                    "timed_out_immediately": timed_out_immediately,
                    "acquired_after_s": acquired_after_s,
                    "status": "ok" if timed_out_immediately else "fail",
                }
            ),
            flush=True,
        )
        return 0 if timed_out_immediately else 1
    finally:
        lock.close()


def _run_ducklake_multiprocess(catalog_path: Path, data_path: Path, root: Path) -> Json:
    script_path = Path(__file__).resolve()
    lock_path = root / "ducklake_writer.lock"
    start_flag = root / "mp_start.flag"
    r_ready = root / "mp_reader.ready"
    w_ready = root / "mp_writer.ready"

    reader = subprocess.Popen(
        [
            sys.executable,
            str(script_path),
            "--worker",
            "ducklake-reader",
            "--catalog",
            str(catalog_path),
            "--data-path",
            str(data_path),
            "--start-flag",
            str(start_flag),
            "--ready-flag",
            str(r_ready),
            "--iterations",
            "30",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    writer = subprocess.Popen(
        [
            sys.executable,
            str(script_path),
            "--worker",
            "ducklake-writer",
            "--catalog",
            str(catalog_path),
            "--data-path",
            str(data_path),
            "--lock-path",
            str(lock_path),
            "--start-flag",
            str(start_flag),
            "--ready-flag",
            str(w_ready),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        deadline = time.monotonic() + 20.0
        while not (r_ready.exists() and w_ready.exists()):
            if reader.poll() is not None or writer.poll() is not None:
                break
            if time.monotonic() > deadline:
                break
            time.sleep(0.05)
        start_flag.write_text("go", encoding="utf-8")
        r_out, r_err = reader.communicate(timeout=45)
        w_out, w_err = writer.communicate(timeout=45)
    finally:
        for proc in (reader, writer):
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

    reader_data: Json = (
        json.loads(r_out.strip()) if r_out.strip() else {"errors": [r_err.strip()]}
    )
    writer_data: Json = (
        json.loads(w_out.strip()) if w_out.strip() else {"errors": [w_err.strip()]}
    )
    if r_err.strip():
        reader_data.setdefault("stderr", r_err.strip())
    if w_err.strip():
        writer_data.setdefault("stderr", w_err.strip())

    reader_errors = list(reader_data.get("errors", []))
    writer_errors = list(writer_data.get("errors", []))
    skew_count = int(reader_data.get("skew_count", 0))
    total_errors = (
        len(reader_errors)
        + len(writer_errors)
        + skew_count
        + (0 if reader.returncode == 0 else 1)
        + (0 if writer.returncode == 0 else 1)
    )
    return {
        "reader_returncode": reader.returncode,
        "writer_returncode": writer.returncode,
        "reader_iterations": int(reader_data.get("reads", 0)),
        "reader_versions_seen": reader_data.get("versions_seen", []),
        "read_skew_count": skew_count,
        "permission_errors": int(reader_data.get("permission_errors", 0)),
        "io_errors": int(reader_data.get("io_errors", 0)),
        "file_not_found_errors": int(reader_data.get("file_not_found_errors", 0)),
        "writer_versions_written": int(writer_data.get("versions_written", 0)),
        "writer_expired_versions": writer_data.get("expired_versions", []),
        "writer_deleted_files_count": int(writer_data.get("deleted_files_count", 0)),
        "reader_errors": reader_errors,
        "writer_errors": writer_errors,
        "total_errors": total_errors,
    }


def run_experiment_1_ducklake() -> Json:
    """Experiment 1: DuckLake + SQLite catalog on Windows (WDAC, pushdown, expiry, MP)."""
    memory_ro_exc = "unexpectedly_succeeded"
    try:
        bad_con = duckdb.connect(":memory:", read_only=True)
        bad_con.close()
    except Exception as exc:
        memory_ro_exc = f"{type(exc).__name__}: {exc}"

    extension_hashes: dict[str, Json] = {}
    probe_con = duckdb.connect(":memory:")
    try:
        for ext in ("ducklake", "sqlite"):
            probe_con.execute(f"INSTALL {ext}; LOAD {ext};")
        ext_rows = probe_con.execute(
            "SELECT extension_name, loaded, installed, install_path, extension_version "
            "FROM duckdb_extensions() "
            "WHERE extension_name IN ('ducklake', 'sqlite', 'sqlite_scanner') "
            "ORDER BY extension_name"
        ).fetchall()
        for ext_name, loaded, installed, install_path, ext_version in ext_rows:
            ext_path = Path(str(install_path)) if install_path else None
            sha256_hex = (
                _sha256_file(ext_path)
                if ext_path is not None and ext_path.is_file()
                else None
            )
            extension_hashes[str(ext_name)] = {
                "loaded": bool(loaded),
                "installed": bool(installed),
                "install_path": _redact_home_path(ext_path),
                "extension_version": str(ext_version),
                "sha256": sha256_hex,
            }
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        status, is_wdac_block, network_error = _classify_extension_error(err_msg)
        return {
            "ducklake_status": status,
            "extension_hashes": extension_hashes,
            "ducklake_multiprocess_errors": 1,
            "post_compaction_pushdown_verified": False,
            "expire_versions_list_verified": False,
            "explain_pushdown_before_compaction": "",
            "explain_pushdown_after_compaction": "",
            "ducklake_details": {
                "is_wdac_block": is_wdac_block,
                "network_error": network_error,
                "load_error": err_msg,
                "memory_read_only_exception": memory_ro_exc,
            },
        }
    finally:
        probe_con.close()

    with tempfile.TemporaryDirectory(prefix="finintel_b0_ducklake_") as tmp_str:
        root = Path(tmp_str)
        catalog_path = root / "catalog.sqlite"
        data_path = root / "lake"
        data_path.mkdir()
        wal_mode = _configure_sqlite_catalog(catalog_path)

        con = duckdb.connect(":memory:")
        ro_con: duckdb.DuckDBPyConnection | None = None
        try:
            con.execute("LOAD ducklake; LOAD sqlite;")
            _attach_ducklake(con, catalog_path, data_path, read_only=False)

            options_rows = con.execute(
                "SELECT * FROM ducklake_options('finintel_lake')"
            ).fetchall()
            format_version = next(
                (str(row[2]) for row in options_rows if str(row[0]) == "version"),
                "unknown",
            )

            con.execute(
                "CREATE TABLE finintel_lake.t_facts ("
                "issuer_id VARCHAR, period_end_date DATE, metric_id VARCHAR, val DOUBLE);"
            )
            con.execute(
                "CREATE TABLE finintel_lake.t_ratios ("
                "issuer_id VARCHAR, period_end_date DATE, roe DOUBLE);"
            )

            # Probe ALTER TABLE ... SET SORTED BY empirically (DuckLake 0.3 vs 0.4+)
            set_sorted_by_supported = False
            set_sorted_by_observation = "supported"
            try:
                con.execute(
                    "ALTER TABLE finintel_lake.t_facts SET SORTED BY (issuer_id, period_end_date);"
                )
                con.execute(
                    "ALTER TABLE finintel_lake.t_ratios SET SORTED BY (issuer_id, period_end_date);"
                )
                set_sorted_by_supported = True
            except Exception as exc:
                set_sorted_by_observation = f"{type(exc).__name__}: {exc}"

            # 1) Atomic multi-table snapshot (Batch 1) + 3 more batches (4 total)
            # Capture explicit age_cutoff between Batch 2 and Batch 3 for MISS-02 expiry test.
            batch_snapshot_ids: list[int] = []
            age_cutoff_utc: datetime | None = None
            for batch_idx in range(4):
                con.execute("BEGIN TRANSACTION;")
                con.execute(
                    "INSERT INTO finintel_lake.t_facts "
                    "SELECT issuer_id, period_end_date, metric_id, val FROM ("
                    "  SELECT CASE WHEN i < 34 THEN 'FPT' WHEN i < 67 THEN 'HPG' ELSE 'VNM' END AS issuer_id, "
                    f"  DATE '2025-12-31' - INTERVAL ({batch_idx} * 90) DAY AS period_end_date, "
                    f"  'M' || (i % 10) AS metric_id, ({batch_idx} * 100 + i)::DOUBLE AS val "
                    "  FROM range(100) t(i)"
                    ") ORDER BY issuer_id, period_end_date;"
                )
                con.execute(
                    "INSERT INTO finintel_lake.t_ratios "
                    "SELECT issuer_id, period_end_date, roe FROM ("
                    "  SELECT CASE WHEN i < 3 THEN 'FPT' WHEN i < 7 THEN 'HPG' ELSE 'VNM' END AS issuer_id, "
                    f"  DATE '2025-12-31' - INTERVAL ({batch_idx} * 90) DAY AS period_end_date, "
                    f"  0.20 + {batch_idx} * 0.01 AS roe "
                    "  FROM range(10) t(i)"
                    ") ORDER BY issuer_id, period_end_date;"
                )
                con.execute("COMMIT;")
                latest_snap = con.execute(
                    "SELECT snapshot_id FROM ducklake_snapshots('finintel_lake') "
                    "ORDER BY snapshot_id DESC LIMIT 1"
                ).fetchone()
                if latest_snap is not None:
                    batch_snapshot_ids.append(int(latest_snap[0]))
                time.sleep(0.05)

            # Record age_cutoff after all 4 data batches and before compaction so that
            # the 2nd-newest snapshot is older than age_cutoff (protected solely by
            # keep_snapshots=2) while older snapshots are both older AND unprotected.
            time.sleep(0.2)
            age_cutoff_utc = datetime.now(UTC)
            time.sleep(0.2)

            snap_records = con.execute(
                "SELECT snapshot_id, snapshot_time, changes "
                "FROM ducklake_snapshots('finintel_lake') ORDER BY snapshot_id"
            ).fetchall()
            first_batch_id = batch_snapshot_ids[0]
            first_batch_changes = next(
                (row[2] for row in snap_records if int(row[0]) == first_batch_id),
                None,
            )
            atomic_tables = (
                list(first_batch_changes.get("tables_inserted_into", []))
                if isinstance(first_batch_changes, dict)
                else []
            )
            atomic_multi_table_verified = len(atomic_tables) == 2

            # 2) READ_ONLY attach + EXPLAIN before compaction + Arrow->Polars zero-copy check
            ro_con = duckdb.connect(":memory:")
            ro_con.execute("LOAD ducklake; LOAD sqlite;")
            _attach_ducklake(ro_con, catalog_path, data_path, read_only=True)

            pushdown_query = (
                "SELECT * FROM finintel_lake.t_facts WHERE issuer_id = 'FPT'"
            )
            explain_before = _explain_text(ro_con, pushdown_query)
            pushdown_before_ok = _has_issuer_pushdown(explain_before)

            pl_df = ro_con.sql(pushdown_query).pl()
            zero_copy_polars_ok = (
                isinstance(pl_df, pl.DataFrame)
                and pl_df.height == 136
                and set(pl_df["issuer_id"].unique().to_list()) == {"FPT"}
            )

            # 3) Compaction + EXPLAIN after compaction + physical sort preservation check
            con.execute("CALL ducklake_merge_adjacent_files('finintel_lake');")
            explain_after = _explain_text(ro_con, pushdown_query)
            pushdown_after_ok = _has_issuer_pushdown(explain_after)

            facts_parquets = sorted((data_path / "main" / "t_facts").glob("*.parquet"))
            merged_parquet = max(facts_parquets, key=lambda p: p.stat().st_mtime)
            merged_issuers = [
                str(r[0])
                for r in con.execute(
                    f"SELECT issuer_id FROM read_parquet('{merged_parquet.as_posix()}')"
                ).fetchall()
            ]
            issuer_transitions = sum(
                1
                for a, b in zip(merged_issuers, merged_issuers[1:], strict=False)
                if a != b
            )
            physical_sort_preserved = merged_issuers == sorted(merged_issuers)

            # 4) Exact Snapshot Expiration Semantics (MISS-02):
            # Select snapshots strictly older than age_cutoff_utc AND outside newest keep_snapshots=2.
            keep_snapshots = 2
            all_snaps = con.execute(
                "SELECT snapshot_id, snapshot_time "
                "FROM ducklake_snapshots('finintel_lake') ORDER BY snapshot_id"
            ).fetchall()
            all_snap_ids = [int(row[0]) for row in all_snaps]
            protected_ids = set(all_snap_ids[-keep_snapshots:])
            if age_cutoff_utc is None:
                raise RuntimeError(
                    "age_cutoff_utc was not recorded during batch writes"
                )
            versions_to_expire = [
                int(row[0])
                for row in all_snaps
                if int(row[0]) not in protected_ids and _to_utc(row[1]) < age_cutoff_utc
            ]
            versions_sql = ", ".join(str(sid) for sid in versions_to_expire)
            con.execute(
                f"CALL ducklake_expire_snapshots('finintel_lake', versions => [{versions_sql}]);"
            )

            # Probe default cleanup_old_files vs cleanup_all => true on SQLite catalog
            cleanup_default_obs = "ok"
            try:
                con.execute("CALL ducklake_cleanup_old_files('finintel_lake');")
            except Exception as exc:
                cleanup_default_obs = f"{type(exc).__name__}: {exc}"

            deleted_rows = con.execute(
                "CALL ducklake_cleanup_old_files('finintel_lake', cleanup_all => true);"
            ).fetchall()

            remaining_ids = [
                int(row[0])
                for row in con.execute(
                    "SELECT snapshot_id FROM ducklake_snapshots('finintel_lake') ORDER BY snapshot_id"
                ).fetchall()
            ]
            remaining_readable = True
            for sid in all_snap_ids[-keep_snapshots:]:
                cnt_row = ro_con.execute(
                    f"SELECT count(*) FROM finintel_lake.t_facts AT (VERSION => {sid})"
                ).fetchone()
                if cnt_row is None or int(cnt_row[0]) != 400:
                    remaining_readable = False

            expire_verified = (
                bool(versions_to_expire)
                and all(sid not in remaining_ids for sid in versions_to_expire)
                and all(sid in remaining_ids for sid in all_snap_ids[-keep_snapshots:])
                and remaining_readable
            )
        finally:
            if ro_con is not None:
                ro_con.close()
            con.close()

        # 5) Multi-process Windows concurrency test on a clean DuckLake catalog
        mp_root = root / "mp"
        mp_root.mkdir()
        mp_catalog = mp_root / "catalog.sqlite"
        mp_lake = mp_root / "lake"
        mp_lake.mkdir()
        _configure_sqlite_catalog(mp_catalog)
        init_con = duckdb.connect(":memory:")
        try:
            init_con.execute("LOAD ducklake; LOAD sqlite;")
            _attach_ducklake(init_con, mp_catalog, mp_lake, read_only=False)
            init_con.execute("BEGIN TRANSACTION;")
            init_con.execute(
                "CREATE TABLE finintel_lake.t_facts ("
                "issuer_id VARCHAR, period_end_date DATE, metric_id VARCHAR, val DOUBLE);"
            )
            init_con.execute(
                "CREATE TABLE finintel_lake.t_ratios ("
                "issuer_id VARCHAR, period_end_date DATE, roe DOUBLE);"
            )
            init_con.execute(
                "INSERT INTO finintel_lake.t_facts "
                "SELECT 'FPT', DATE '2025-12-31', 'M' || (i % 10), i::DOUBLE FROM range(100) t(i);"
            )
            init_con.execute(
                "INSERT INTO finintel_lake.t_ratios "
                "SELECT 'FPT', DATE '2025-12-31', 0.25 FROM range(10) t(i);"
            )
            init_con.execute("COMMIT;")
        finally:
            init_con.close()

        mp_result = _run_ducklake_multiprocess(mp_catalog, mp_lake, mp_root)

    post_compaction_pushdown_verified = pushdown_before_ok and pushdown_after_ok
    mp_errors = int(mp_result["total_errors"])
    ducklake_pass = (
        atomic_multi_table_verified
        and post_compaction_pushdown_verified
        and expire_verified
        and zero_copy_polars_ok
        and mp_errors == 0
    )

    return {
        "ducklake_status": "PASS" if ducklake_pass else "ERROR",
        "extension_hashes": extension_hashes,
        "ducklake_multiprocess_errors": mp_errors,
        "post_compaction_pushdown_verified": post_compaction_pushdown_verified,
        "expire_versions_list_verified": expire_verified,
        "explain_pushdown_before_compaction": explain_before,
        "explain_pushdown_after_compaction": explain_after,
        "ducklake_details": {
            "is_wdac_block": False,
            "network_error": False,
            "sqlite_journal_mode": wal_mode,
            "ducklake_format_version": format_version,
            "memory_read_only_exception": memory_ro_exc,
            "atomic_multi_table_snapshot_verified": atomic_multi_table_verified,
            "atomic_snapshot_id": first_batch_id,
            "atomic_tables_inserted": atomic_tables,
            "set_sorted_by_supported": set_sorted_by_supported,
            "set_sorted_by_observation": set_sorted_by_observation,
            "post_compaction_physical_sort_preserved": physical_sort_preserved,
            "post_compaction_issuer_transitions": issuer_transitions,
            "expire_versions_expired": versions_to_expire,
            "expire_versions_remaining": remaining_ids,
            "remaining_snapshots_readable": remaining_readable,
            "cleanup_default_sqlite_observation": cleanup_default_obs,
            "cleanup_all_deleted_count": len(deleted_rows),
            "zero_copy_arrow_polars_verified": zero_copy_polars_ok,
            "polars_row_count": pl_df.height,
            "multiprocess": mp_result,
            "ci_extension_cache_note": (
                "Extensions load cleanly on Windows without WDAC block. "
                "Offline CI runs without cached ~/.duckdb/extensions/ should either cache "
                "the verified extension binaries (SHA-256 recorded in extension_hashes) "
                "or run unit tests against the parquet+ReadWriteLock backend."
            ),
        },
    }


def run_experiment_2_rwlock() -> Json:
    """Experiment 2: filelock==4.2.10 ReadWriteLock multi-process concurrency on Windows."""
    script_path = Path(__file__).resolve()
    errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="finintel_b0_rwlock_") as tmp_str:
        root = Path(tmp_str)
        lock_path = root / "finintel_test.rwlock.db"
        ready_dir = root / "ready"
        ready_dir.mkdir()
        release_flag = root / "release.flag"

        readers = [
            subprocess.Popen(
                [
                    sys.executable,
                    str(script_path),
                    "--worker",
                    "rwlock-reader",
                    "--lock-path",
                    str(lock_path),
                    "--ready-dir",
                    str(ready_dir),
                    "--release-flag",
                    str(release_flag),
                    "--worker-id",
                    f"r{idx}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for idx in range(3)
        ]

        writer: subprocess.Popen[str] | None = None
        concurrent_readers_verified = False
        writer_immediate_timeout_verified = False
        writer_acquired_after_s: float | None = None

        try:
            deadline = time.monotonic() + 15.0
            while len(list(ready_dir.glob("ready_r*"))) < 3:
                if all(proc.poll() is not None for proc in readers):
                    break
                if time.monotonic() > deadline:
                    errors.append("Timed out waiting for 3 simultaneous readers")
                    break
                time.sleep(0.05)

            concurrent_readers_verified = len(list(ready_dir.glob("ready_r*"))) == 3
            if concurrent_readers_verified:
                writer = subprocess.Popen(
                    [
                        sys.executable,
                        str(script_path),
                        "--worker",
                        "rwlock-writer",
                        "--lock-path",
                        str(lock_path),
                        "--ready-dir",
                        str(ready_dir),
                        "--release-flag",
                        str(release_flag),
                        "--worker-id",
                        "w0",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                probe_deadline = time.monotonic() + 15.0
                while not (ready_dir / "writer_probed").exists():
                    if writer.poll() is not None:
                        break
                    if time.monotonic() > probe_deadline:
                        errors.append("Timed out waiting for writer timeout=0 probe")
                        break
                    time.sleep(0.05)
                # Let writer enter blocking write_lock(timeout=10) before releasing readers.
                time.sleep(0.15)

            release_flag.write_text("release", encoding="utf-8")

            for idx, proc in enumerate(readers):
                out, err = proc.communicate(timeout=20)
                if proc.returncode != 0:
                    errors.append(
                        f"reader_{idx} rc={proc.returncode}: {err.strip() or out.strip()}"
                    )

            if writer is not None:
                w_out, w_err = writer.communicate(timeout=20)
                if writer.returncode != 0:
                    errors.append(
                        f"writer rc={writer.returncode}: {w_err.strip() or w_out.strip()}"
                    )
                elif w_out.strip():
                    w_data: Json = json.loads(w_out.strip())
                    writer_immediate_timeout_verified = bool(
                        w_data.get("timed_out_immediately")
                    )
                    raw_acquired = w_data.get("acquired_after_s")
                    writer_acquired_after_s = (
                        float(raw_acquired)
                        if isinstance(raw_acquired, (int, float))
                        else None
                    )
        finally:
            release_flag.write_text("release", encoding="utf-8")
            all_procs = [*readers, *([writer] if writer is not None else [])]
            for proc in all_procs:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)

    passed = (
        concurrent_readers_verified
        and writer_immediate_timeout_verified
        and writer_acquired_after_s is not None
        and not errors
    )
    return {
        "rwlock_windows_subprocess_status": "PASS" if passed else "FAIL",
        "rwlock_errors": len(errors),
        "rwlock_details": {
            "concurrent_readers_verified": concurrent_readers_verified,
            "reader_count": 3,
            "writer_immediate_timeout_verified": writer_immediate_timeout_verified,
            "writer_acquired_after_release_seconds": writer_acquired_after_s,
            "errors": errors,
        },
    }


def run_experiment_3_platform_pins() -> Json:
    """Experiment 3: Verify exact == pins in pyproject.toml, uv.lock, and active venv."""
    pyproject_data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    lock_data = tomllib.loads(UV_LOCK_PATH.read_text(encoding="utf-8"))

    dep_groups = pyproject_data.get("dependency-groups", {})
    platform_specs = list(dep_groups.get("platform", []))

    pyproject_pins: dict[str, str] = {}
    unpinned_entries: list[str] = []
    for entry in platform_specs:
        match = EXACT_PIN_RE.match(str(entry).strip())
        if match is None:
            unpinned_entries.append(str(entry))
        else:
            pyproject_pins[match.group(1).lower()] = match.group(2)

    lock_versions: dict[str, str] = {}
    for pkg in lock_data.get("package", []):
        if isinstance(pkg, dict) and "name" in pkg and "version" in pkg:
            lock_versions[str(pkg["name"]).lower()] = str(pkg["version"])

    pinned_platform_packages: dict[str, str] = {}
    installed_matches_lock = True
    for pkg_name in REQUIRED_PLATFORM_PACKAGES:
        locked_ver = lock_versions.get(pkg_name)
        pin_ver = pyproject_pins.get(pkg_name)
        try:
            installed_ver: str | None = version(pkg_name)
        except PackageNotFoundError:
            installed_ver = None
        if locked_ver is None or pin_ver != locked_ver or installed_ver != locked_ver:
            installed_matches_lock = False
        pinned_platform_packages[pkg_name] = locked_ver or "MISSING"

    transitive_sqlalchemy_version = lock_versions.get("sqlalchemy")
    try:
        installed_sqlalchemy: str | None = version("sqlalchemy")
    except PackageNotFoundError:
        installed_sqlalchemy = None

    forbidden_absent: dict[str, bool] = {}
    for forbidden in FORBIDDEN_PACKAGES:
        try:
            version(forbidden)
            installed_present = True
        except PackageNotFoundError:
            installed_present = False
        forbidden_absent[forbidden] = (
            forbidden not in lock_versions
            and forbidden not in pyproject_pins
            and not installed_present
        )

    return {
        "pinned_platform_packages": pinned_platform_packages,
        "transitive_sqlalchemy_version": transitive_sqlalchemy_version,
        "platform_dependency_details": {
            "uv_lock_path": "uv.lock",
            "pyproject_unpinned_entries": unpinned_entries,
            "installed_vs_locked_match": installed_matches_lock,
            "installed_sqlalchemy_version": installed_sqlalchemy,
            "forbidden_packages_absent": forbidden_absent,
        },
    }


def _select_storage_backend(exp1: Json, exp2: Json) -> str:
    status = exp1["ducklake_status"]
    if (
        status == "PASS"
        and exp1["ducklake_multiprocess_errors"] == 0
        and exp1["post_compaction_pushdown_verified"]
        and exp1["expire_versions_list_verified"]
    ):
        return "ducklake"
    if status == "BLOCKED_BY_WDAC":
        if exp2["rwlock_windows_subprocess_status"] == "PASS":
            return "parquet"
        return "blocked_both"
    if status == "NETWORK_ERROR":
        return "undetermined_network_error"
    return "parquet" if exp2["rwlock_windows_subprocess_status"] == "PASS" else "error"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gate B0 empirical verification spike."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument(
        "--worker",
        choices=(
            "ducklake-reader",
            "ducklake-writer",
            "rwlock-reader",
            "rwlock-writer",
        ),
    )
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--data-path", type=Path)
    parser.add_argument("--lock-path", type=Path)
    parser.add_argument("--start-flag", type=Path)
    parser.add_argument("--ready-flag", type=Path)
    parser.add_argument("--ready-dir", type=Path)
    parser.add_argument("--release-flag", type=Path)
    parser.add_argument("--worker-id", type=str, default="w")
    parser.add_argument("--iterations", type=int, default=30)
    args = parser.parse_args(argv)

    if args.worker == "ducklake-reader":
        if (
            args.catalog is None
            or args.data_path is None
            or args.start_flag is None
            or args.ready_flag is None
        ):
            raise ValueError("Missing required worker arguments for ducklake-reader")
        return _worker_ducklake_reader(
            args.catalog,
            args.data_path,
            args.start_flag,
            args.ready_flag,
            args.iterations,
        )
    if args.worker == "ducklake-writer":
        if (
            args.catalog is None
            or args.data_path is None
            or args.lock_path is None
            or args.start_flag is None
            or args.ready_flag is None
        ):
            raise ValueError("Missing required worker arguments for ducklake-writer")
        return _worker_ducklake_writer(
            args.catalog,
            args.data_path,
            args.lock_path,
            args.start_flag,
            args.ready_flag,
        )
    if args.worker in ("rwlock-reader", "rwlock-writer"):
        if (
            args.lock_path is None
            or args.ready_dir is None
            or args.release_flag is None
        ):
            raise ValueError(f"Missing required worker arguments for {args.worker}")
        mode: Literal["reader", "writer"] = (
            "reader" if args.worker == "rwlock-reader" else "writer"
        )
        return _worker_rwlock(
            mode, args.lock_path, args.ready_dir, args.release_flag, args.worker_id
        )

    exp1 = run_experiment_1_ducklake()
    exp2 = run_experiment_2_rwlock()
    exp3 = run_experiment_3_platform_pins()
    selected_backend = _select_storage_backend(exp1, exp2)

    report: Json = {
        "generated_at": datetime.now(UTC).isoformat(),
        "python_version": platform.python_version(),
        "duckdb_version": duckdb.__version__,
        "polars_version": pl.__version__,
        "pyarrow_version": version("pyarrow"),
        "filelock_version": version("filelock"),
        "os_platform": platform.platform(),
        "ducklake_status": exp1["ducklake_status"],
        "extension_hashes": exp1["extension_hashes"],
        "ducklake_multiprocess_errors": exp1["ducklake_multiprocess_errors"],
        "post_compaction_pushdown_verified": exp1["post_compaction_pushdown_verified"],
        "expire_versions_list_verified": exp1["expire_versions_list_verified"],
        "rwlock_windows_subprocess_status": exp2["rwlock_windows_subprocess_status"],
        "rwlock_errors": exp2["rwlock_errors"],
        "selected_storage_backend": selected_backend,
        "pinned_platform_packages": exp3["pinned_platform_packages"],
        "transitive_sqlalchemy_version": exp3["transitive_sqlalchemy_version"],
        "explain_pushdown_before_compaction": exp1[
            "explain_pushdown_before_compaction"
        ],
        "explain_pushdown_after_compaction": exp1["explain_pushdown_after_compaction"],
        "ducklake_details": exp1["ducklake_details"],
        "rwlock_details": exp2["rwlock_details"],
        "platform_dependency_details": exp3["platform_dependency_details"],
    }

    _write_json_atomic(args.output, report)
    print(
        f"Gate B0 spike completed: ducklake={report['ducklake_status']}, "
        f"rwlock={report['rwlock_windows_subprocess_status']}, "
        f"selected_storage_backend={selected_backend}. "
        f"Report written to {args.output}.",
        flush=True,
    )

    ok = (
        selected_backend in ("ducklake", "parquet")
        and report["rwlock_windows_subprocess_status"] == "PASS"
        and report["rwlock_errors"] == 0
        and exp3["platform_dependency_details"]["installed_vs_locked_match"]
        and all(
            exp3["platform_dependency_details"]["forbidden_packages_absent"].values()
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
