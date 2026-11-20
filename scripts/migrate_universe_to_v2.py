"""WAL-safe read-only universe migration, rollback verification, and 8-gate V2 benchmark runner.

Migrates an archived raw universe (strictly read-only, zero mutations to ``--source-dir``)
into a target V2 workspace, independently verifies dual SHA-256 integrity
(``payload_file_sha256`` and ``canonical_content_sha256``), executes the complete
V2 pipeline (Layer 1 disclosure enrichment, Layer 2 batch/incremental normalization,
Native Polars incremental transform + Dagster ``published_financial_marts`` + blocking
QG1/QG2 asset checks, ``rollback_snapshot()`` verification, 3-run ``lakehouse_maintenance_job``
+ Curated GC), and runs the reproducible 8-gate benchmark and ablation suite.

Verified Sources:
- Python ``sqlite3.Connection.backup`` Official Documentation (WAL-safe online backup):
  https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup
- SQLite Write-Ahead Logging (WAL) & Online Backup API:
  https://www.sqlite.org/wal.html
  https://www.sqlite.org/backup.html
- DuckLake Official Documentation (``AT (VERSION => ...)``, ``ducklake_list_files``,
  ``READ_ONLY`` attach, explicit snapshot expiry):
  https://ducklake.select/docs/stable/
- DuckDB Parquet Metadata & ``EXPLAIN ANALYZE`` Documentation:
  https://duckdb.org/docs/stable/data/parquet/metadata
- Circular 96/2020/TT-BTC (Articles 10 & 14), Vietnam Civil Code 2015 (Articles 147 &
  148(5)), and Labor Code 2019 (Article 112) via ``finintel.domain.schemas.FilingMetadata``
  and ``finintel.ingestion.disclosure_enricher``.
- Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
  IMF Working Paper WP/15/216 via ``finintel.analytics.stress_test``.
- Platt, J. C. (1999) & Niculescu-Mizil, A., & Caruana, R. (2005) (ICML 2005) for
  ``MarginPlattCalibrator`` slope verification (``0.0 < platt_slope <= 10.0``).
- RFC 9110 HTTP Semantics (``ETag`` / ``If-None-Match`` -> ``304``) via
  ``finintel.serving.api``.
- Runtime API Verification (Python 3.11.9, ``duckdb==1.4.2``, DuckLake ``0.3``,
  ``polars==1.35.2``, ``dagster==1.13.30``, ``fastapi==0.165.0``, ``uvicorn==0.54.0``,
  ``httpx==0.28.1``):
  - ``sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)`` +
    ``src.backup(dst, pages=256)`` captures un-checkpointed active WAL entries safely.
  - ``dg.DagsterInstance.from_config(str(home))`` supports context-manager cleanup.
  - ``ducklake_list_files(alias, table, snapshot_version => ver)`` returns ``data_file``;
    ``parquet_metadata(file)`` exposes ``row_group_id``, ``path_in_schema``,
    ``row_group_num_rows``, ``stats_min_value``, ``stats_max_value``.
  - On 64-bit Windows, ``GetProcessMemoryInfo`` requires explicit ``wintypes.HANDLE``
    ``restype`` / ``argtypes`` to avoid 64-bit handle truncation.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal, cast
from urllib.parse import parse_qs, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import dagster as dg  # noqa: E402
import duckdb  # noqa: E402
import httpx  # noqa: E402
import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

from finintel.analytics.stress_test import (  # noqa: E402
    StressScenario,
    simulate_financial_stress,
    summarize_stress_by_sector,
)
from finintel.domain.schemas import (  # noqa: E402
    AuditStatus,
    FilingMetadata,
    PeriodType,
    StatementScope,
)
from finintel.exceptions import (  # noqa: E402
    ArchiveIntegrityError,
    ContractViolationError,
)
from finintel.ingestion.archive import RawArchive  # noqa: E402
from finintel.ingestion.disclosure_enricher import (  # noqa: E402
    DisclosureRecord,
    DisclosureSlot,
    enrich_fhsc_manifest_disclosures,
    merge_disclosure_records,
    parse_cafef_bctc_disclosures,
    parse_vietcap_iq_disclosures,
)
from finintel.ingestion.file_adapter import FileAdapter  # noqa: E402
from finintel.ingestion.hasher import (  # noqa: E402
    canonical_content_sha256,
    payload_file_sha256,
)
from finintel.ml.supervised.temporal_split import build_pit_ml_splits  # noqa: E402
from finintel.ml.tables import build_ml_artifacts  # noqa: E402
from finintel.normalization.archive import (  # noqa: E402
    normalize_archive_incremental,
)
from finintel.orchestration import (  # noqa: E402
    ALL_ASSET_CHECKS,
    FinIntelPathsResource,
    PublishMartsConfig,
    StateStoreResource,
    StorageResource,
    configure_concurrency_pools,
    lakehouse_maintenance_job,
    normalized_financial_facts,
    published_financial_marts,
    sync_corporate_ticker_partitions,
)
from finintel.pipeline.ingest import (  # noqa: E402
    DEFAULT_CAFEF_URL_TEMPLATE,
    DEFAULT_VIETCAP_URL_TEMPLATE,
)
from finintel.pipeline.state_store import StateStore  # noqa: E402
from finintel.quality.gate import evaluate_quality_gate  # noqa: E402
from finintel.serving.queries import DashboardQueryService  # noqa: E402
from finintel.serving.schemas import parse_shap_top_drivers  # noqa: E402
from finintel.storage.ducklake_store import (  # noqa: E402
    DEFAULT_CATALOG_ALIAS,
    SNAPSHOT_AUDIT_TABLE,
    DuckLakeStore,
    ensure_ducklake_attached,
)
from finintel.storage.repository import ParquetRepository  # noqa: E402
from finintel.storage.schemas import (  # noqa: E402
    AUDIT_QUARANTINE_TABLE,
    ISSUERS_SCHEMA,
    ML_SCORES_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
    canonicalize_table,
)
from finintel.storage.snapshot_publisher import (  # noqa: E402
    pipeline_write_lock,
    snapshot_write_lock,
)
from finintel.transform.audits import DUPONT_TOLERANCE  # noqa: E402

DEFAULT_AS_OF = date(2026, 11, 20)
DEFAULT_VALIDATION_START = date(2021, 12, 1)
DEFAULT_TEST_START = date(2023, 9, 1)

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_FHSC_LOCATOR_RE = re.compile(r"^/data/(0|[1-9][0-9]{0,5})$")
_SAFE_ISSUER_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_VCI_COMPANY_URL_RE = re.compile(
    r"/company/([A-Z0-9]{1,16})/financial-statement", re.IGNORECASE
)
_SQLITE_IGNORE_PATTERNS = shutil.ignore_patterns(
    "*.sqlite",
    "*.sqlite-wal",
    "*.sqlite-shm",
    "*.sqlite-journal",
    ".archive.lock",
)

_GATE3_INDICATORS: tuple[str, ...] = (
    "ebit_ttm",
    "ebitda_ttm",
    "roic",
    "interest_coverage",
    "dupont_valid_flag",
    "altman_z_double_prime",
    "piotroski_f_score",
    "calibrated_distress_prob",
)

_BLUECHIP_SPOTLIGHT: tuple[str, ...] = ("GMD", "FPT", "VNM", "MWG", "HPG", "GAS")

_IMF_STRESS_SCENARIOS: tuple[tuple[str, StressScenario], ...] = (
    (
        "S1_rate_shock_300bps",
        StressScenario(
            name="IMF S1: +300 bps Interest Rate Shock",
            interest_rate_shock_bps=300.0,
        ),
    ),
    (
        "S2_revenue_margin_shock",
        StressScenario(
            name="IMF S2: -20% Revenue & -3 pp Gross Margin",
            revenue_shock_pct=-0.20,
            gross_margin_shock_pp=-0.03,
        ),
    ),
    (
        "S3_combined_liquidity_shock",
        StressScenario(
            name="IMF S3: Combined Rate + Revenue/Margin + Working Capital Liquidity Shock",
            interest_rate_shock_bps=300.0,
            revenue_shock_pct=-0.20,
            gross_margin_shock_pp=-0.03,
            dso_shock_days=30.0,
            dio_shock_days=30.0,
        ),
    ),
)


# ---------------------------------------------------------------------------
# Generic utility helpers
# ---------------------------------------------------------------------------


def _get_memory_mb() -> tuple[float, float]:
    """Return ``(current_rss_mb, peak_rss_mb)`` for the current Python process."""
    if sys.platform == "win32":
        from ctypes import wintypes

        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
        ok = bool(
            psapi.GetProcessMemoryInfo(
                kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
            )
        )
        if not ok:
            return 0.0, 0.0
        mib = 1024.0 * 1024.0
        return (
            round(float(counters.WorkingSetSize) / mib, 3),
            round(float(counters.PeakWorkingSetSize) / mib, 3),
        )

    import resource

    usage = resource.getrusage(resource.RUSAGE_SELF)
    # macOS reports ru_maxrss in bytes; Linux reports kilobytes.
    divisor = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
    peak_mb = round(float(usage.ru_maxrss) / divisor, 3)
    return peak_mb, peak_mb


def _sanitize_json_value(value: object) -> object:
    """Recursively convert dates, Paths, and NumPy scalars into strict JSON values."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, np.floating):
        fval = float(value)
        return fval if math.isfinite(fval) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, Mapping):
        return {str(k): _sanitize_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_json_value(item) for item in value]
    return str(value)


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    """Atomically write strict JSON (``allow_nan=False``) using temp file + ``os.replace``."""
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    sanitized = _sanitize_json_value(payload)
    serialized = (
        json.dumps(
            sanitized,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=resolved.parent,
        prefix=f".{resolved.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp_handle:
        tmp_handle.write(serialized)
        tmp_path = Path(tmp_handle.name)
    try:
        os.replace(tmp_path, resolved)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def _dir_size_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for entry in root.rglob("*"):
        if entry.is_file() and not entry.is_symlink():
            try:
                total += entry.stat().st_size
            except OSError:
                continue
    return total


def _read_state_active_snapshot_ro(state_db: Path) -> str | None:
    if not state_db.is_file():
        return None
    try:
        with closing(
            sqlite3.connect(
                f"{state_db.resolve().as_uri()}?mode=ro", uri=True, timeout=10.0
            )
        ) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'active_snapshot'"
            ).fetchone()
            if exists is None:
                return None
            row = conn.execute(
                "SELECT snapshot_id FROM active_snapshot WHERE singleton = 1"
            ).fetchone()
            return str(row[0]) if row is not None and row[0] is not None else None
    except sqlite3.Error:
        return None


def _ensure_dagster_home(dagster_home: Path) -> Path:
    resolved = dagster_home.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    yaml_path = resolved / "dagster.yaml"
    base_posix = resolved.as_posix()
    yaml_content = (
        "storage:\n"
        "  sqlite:\n"
        f"    base_dir: {json.dumps(base_posix)}\n"
        "local_artifact_storage:\n"
        "  module: dagster._core.storage.root\n"
        "  class: LocalArtifactStorage\n"
        "  config:\n"
        f"    base_dir: {json.dumps(base_posix)}\n"
        "concurrency:\n"
        "  pools:\n"
        '    granularity: "op"\n'
        "    default_limit: 4\n"
        "telemetry:\n"
        "  enabled: false\n"
    )
    yaml_path.write_text(yaml_content, encoding="utf-8")
    return resolved


def _copy_tree_no_sqlite(src: Path, dst: Path) -> int:
    """Read-only recursive copy for non-SQLite artifacts (rejects symlinks & ``*.sqlite*``)."""
    src_resolved = src.resolve()
    dst_resolved = dst.resolve()
    copied = 0
    for entry in src_resolved.rglob("*"):
        if entry.is_symlink():
            raise ContractViolationError(
                f"Refusing to copy symbolic link during migration: {entry}"
            )
        rel = entry.relative_to(src_resolved)
        name_lower = entry.name.lower()
        if (
            name_lower.endswith(
                (".sqlite", ".sqlite-wal", ".sqlite-shm", ".sqlite-journal")
            )
            or name_lower == ".archive.lock"
        ):
            continue
        target_entry = dst_resolved / rel
        if entry.is_dir():
            target_entry.mkdir(parents=True, exist_ok=True)
        elif entry.is_file():
            target_entry.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, target_entry)
            copied += 1
    return copied


def _find_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _percentiles_ms(values_ms: Sequence[float]) -> dict[str, float | int | None]:
    if not values_ms:
        return {
            "count": 0,
            "mean_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "p99_ms": None,
            "max_ms": None,
        }
    arr = np.asarray(values_ms, dtype=np.float64)
    p50, p95, p99 = np.percentile(arr, [50.0, 95.0, 99.0])
    return {
        "count": int(arr.size),
        "mean_ms": round(float(np.mean(arr)), 3),
        "p50_ms": round(float(p50), 3),
        "p95_ms": round(float(p95), 3),
        "p99_ms": round(float(p99), 3),
        "max_ms": round(float(np.max(arr)), 3),
    }


def _ducklake_extensions_available() -> bool:
    con = duckdb.connect(":memory:")
    try:
        con.execute("LOAD ducklake; LOAD sqlite;")
        return True
    except duckdb.Error:
        return False
    finally:
        con.close()


def _ducklake_has_snapshot_ro(
    catalog_path: Path,
    data_path: Path,
    snapshot_id: str,
    *,
    catalog_alias: str = DEFAULT_CATALOG_ALIAS,
) -> bool:
    """Return True iff ``catalog_path`` contains ``snapshot_id`` in ``_snapshot_audit`` (READ_ONLY)."""
    if not catalog_path.is_file():
        return False
    con = duckdb.connect(":memory:")
    try:
        ensure_ducklake_attached(
            con,
            catalog_path=catalog_path,
            data_path=data_path,
            catalog_alias=catalog_alias,
            read_only=True,
        )
        row = con.execute(
            f'SELECT 1 FROM "{catalog_alias}"."{SNAPSHOT_AUDIT_TABLE}" '
            "WHERE snapshot_id = ? LIMIT 1",
            [snapshot_id],
        ).fetchone()
        return row is not None
    except (duckdb.Error, ContractViolationError, OSError):
        return False
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API #1: WAL-safe SQLite online backup
# ---------------------------------------------------------------------------


def backup_sqlite_wal_safe(
    source_db: Path,
    dest_db: Path,
    *,
    read_only_source: bool = True,
) -> Path:
    """Back up a SQLite database safely across WAL mode using ``sqlite3.Connection.backup()``.

    Opens ``source_db`` read-only via SQLite URI ``?mode=ro`` when ``read_only_source=True``,
    streams pages into a sibling temporary file, closes both connections cleanly, removes
    any stale destination ``-wal``/``-shm`` sidecars, and atomically replaces ``dest_db``.
    Never uses ``shutil.copy*`` on ``.sqlite`` files.
    """
    src_resolved = source_db.resolve()
    dst_resolved = dest_db.resolve()
    if not src_resolved.is_file():
        raise FileNotFoundError(
            f"Source SQLite database does not exist: {src_resolved}"
        )
    if src_resolved == dst_resolved:
        raise ValueError("Source and destination SQLite paths must be distinct.")

    dst_resolved.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=dst_resolved.parent,
        prefix=f".{dst_resolved.name}.",
        suffix=".backup.tmp",
        delete=False,
    ) as tmp_handle:
        tmp_path = Path(tmp_handle.name)

    try:
        src_uri = (
            f"{src_resolved.as_uri()}?mode=ro"
            if read_only_source
            else src_resolved.as_uri()
        )
        with (
            closing(sqlite3.connect(src_uri, uri=True, timeout=10.0)) as src_conn,
            closing(sqlite3.connect(str(tmp_path), timeout=10.0)) as dst_conn,
        ):
            src_conn.backup(dst_conn, pages=256)

        for sidecar_suffix in ("-wal", "-shm"):
            Path(f"{tmp_path}{sidecar_suffix}").unlink(missing_ok=True)

        # Remove stale WAL/SHM sidecars at destination before atomic replacement so SQLite
        # never replays pre-cutover WAL frames onto the newly backed-up database image.
        for sidecar_suffix in ("-wal", "-shm"):
            Path(f"{dst_resolved}{sidecar_suffix}").unlink(missing_ok=True)
        os.replace(tmp_path, dst_resolved)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        for sidecar_suffix in ("-wal", "-shm"):
            Path(f"{tmp_path}{sidecar_suffix}").unlink(missing_ok=True)
        raise
    return dst_resolved


# ---------------------------------------------------------------------------
# Public API #2: Pre-Cutover Backup
# ---------------------------------------------------------------------------


def pre_cutover_backup(target_dir: Path, backup_dir: Path) -> dict[str, object]:
    """Back up existing target SQLite metadata and active snapshot outside ``target_dir``.

    Rejects any ``backup_dir`` placed inside ``target_dir`` to preserve rollback isolation.
    """
    target_resolved = target_dir.resolve()
    backup_resolved = backup_dir.resolve()
    if backup_resolved.is_relative_to(target_resolved):
        raise ValueError(
            f"backup_dir ({backup_resolved}) must be located outside target_dir ({target_resolved})."
        )

    state_src = target_resolved / "metadata" / "state.sqlite"
    ducklake_cat_src = target_resolved / "metadata" / "ducklake_catalog.sqlite"
    snapshots_root = target_resolved / "published" / "snapshots"
    lakehouse_data_src = target_resolved / "lakehouse" / "data"

    active_snapshot_id = _read_state_active_snapshot_ro(state_src)
    state_backup_path: str | None = None
    ducklake_backup_path: str | None = None
    snapshot_backup_path: str | None = None
    lakehouse_backup_path: str | None = None

    has_existing = (
        state_src.is_file()
        or ducklake_cat_src.is_file()
        or (
            snapshots_root.is_dir()
            and any(
                p.is_dir() and p.name.startswith("snap_")
                for p in snapshots_root.iterdir()
            )
        )
    )
    if not has_existing:
        return {
            "backed_up": False,
            "backup_dir": backup_resolved.as_posix(),
            "active_snapshot_id": None,
            "state_db_backup": None,
            "ducklake_catalog_backup": None,
            "snapshot_dir_backup": None,
            "lakehouse_data_backup": None,
        }

    backup_resolved.mkdir(parents=True, exist_ok=True)
    if state_src.is_file():
        backed_state = backup_sqlite_wal_safe(
            state_src,
            backup_resolved / "metadata" / "state.sqlite",
            read_only_source=True,
        )
        state_backup_path = backed_state.as_posix()

    if ducklake_cat_src.is_file():
        backed_cat = backup_sqlite_wal_safe(
            ducklake_cat_src,
            backup_resolved / "metadata" / "ducklake_catalog.sqlite",
            read_only_source=True,
        )
        ducklake_backup_path = backed_cat.as_posix()
        if lakehouse_data_src.is_dir():
            dst_lake = backup_resolved / "lakehouse" / "data"
            if dst_lake.exists():
                shutil.rmtree(dst_lake)
            shutil.copytree(
                lakehouse_data_src, dst_lake, ignore=_SQLITE_IGNORE_PATTERNS
            )
            lakehouse_backup_path = dst_lake.as_posix()

    snap_to_copy: Path | None = None
    if (
        active_snapshot_id is not None
        and (snapshots_root / active_snapshot_id).is_dir()
    ):
        snap_to_copy = snapshots_root / active_snapshot_id
    elif snapshots_root.is_dir():
        snap_dirs = sorted(
            (
                p
                for p in snapshots_root.iterdir()
                if p.is_dir() and p.name.startswith("snap_")
            ),
            key=lambda p: (p.stat().st_mtime_ns, p.name),
            reverse=True,
        )
        if snap_dirs:
            snap_to_copy = snap_dirs[0]
            if active_snapshot_id is None:
                active_snapshot_id = snap_to_copy.name

    if snap_to_copy is not None:
        dst_snap = backup_resolved / "published" / "snapshots" / snap_to_copy.name
        if dst_snap.exists():
            shutil.rmtree(dst_snap)
        shutil.copytree(snap_to_copy, dst_snap, ignore=_SQLITE_IGNORE_PATTERNS)
        snapshot_backup_path = dst_snap.as_posix()

    return {
        "backed_up": True,
        "backup_dir": backup_resolved.as_posix(),
        "active_snapshot_id": active_snapshot_id,
        "state_db_backup": state_backup_path,
        "ducklake_catalog_backup": ducklake_backup_path,
        "snapshot_dir_backup": snapshot_backup_path,
        "lakehouse_data_backup": lakehouse_backup_path,
    }


# ---------------------------------------------------------------------------
# Public API #3 & #4: Source Integrity Verification (Dual SHA-256)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceIntegrityReport:
    """Complete read-only verification report of source universe progress and dual SHA-256 hashes."""

    total_candidates: int
    done_count: int
    skipped_count: int
    failed_count: int
    pending_count: int
    done_tickers: tuple[str, ...]
    skipped_tickers: dict[str, str]
    failed_tickers: dict[str, str]
    manifest_rows_checked: int
    raw_sha256_files_checked: int
    raw_provenance_files_checked: int
    unreferenced_raw_files: int
    missing_files: tuple[str, ...]
    payload_sha_mismatches: tuple[str, ...]
    canonical_sha_mismatches: tuple[str, ...]
    provenance_mismatches: tuple[str, ...]
    passed: bool
    progress_source: str = "ingestion_progress.json"


def _parse_progress_and_candidates(
    source_dir: Path,
    manifest_tickers: Sequence[str],
) -> tuple[int, tuple[str, ...], dict[str, str], dict[str, str], int, str]:
    """Read ``ingestion_progress.json`` and ``universe_candidates.json`` read-only."""
    progress_path = source_dir / "ingestion_progress.json"
    candidates_path = source_dir / "universe_candidates.json"

    done_tickers: tuple[str, ...]
    skipped_tickers: dict[str, str] = {}
    failed_tickers: dict[str, str] = {}
    progress_source = "ingestion_progress.json"

    if progress_path.is_file():
        prog_raw = json.loads(progress_path.read_text(encoding="utf-8"))
        if not isinstance(prog_raw, dict):
            raise ContractViolationError(
                "ingestion_progress.json must be a JSON object."
            )
        raw_done = prog_raw.get("done", {})
        if isinstance(raw_done, dict):
            done_tickers = tuple(sorted(str(k).strip().upper() for k in raw_done))
        elif isinstance(raw_done, list):
            done_tickers = tuple(sorted(str(k).strip().upper() for k in raw_done))
        else:
            raise ContractViolationError(
                "ingestion_progress.json 'done' must be dict or list."
            )
        raw_skipped = prog_raw.get("skipped", {})
        if isinstance(raw_skipped, dict):
            skipped_tickers = {
                str(k).strip().upper(): str(v) for k, v in sorted(raw_skipped.items())
            }
        raw_failed = prog_raw.get("failed", {})
        if isinstance(raw_failed, dict):
            failed_tickers = {
                str(k).strip().upper(): str(v) for k, v in sorted(raw_failed.items())
            }
    else:
        done_tickers = tuple(sorted(set(manifest_tickers)))
        progress_source = "inferred_from_interim_archive"

    total_candidates = len(done_tickers) + len(skipped_tickers) + len(failed_tickers)
    if candidates_path.is_file():
        cand_raw = json.loads(candidates_path.read_text(encoding="utf-8"))
        if isinstance(cand_raw, dict):
            raw_total = cand_raw.get("total_verified_eligible")
            if isinstance(raw_total, int) and not isinstance(raw_total, bool):
                total_candidates = raw_total
            elif isinstance(cand_raw.get("candidates"), list):
                total_candidates = len(cand_raw["candidates"])

    pending_count = max(
        0,
        total_candidates
        - len(done_tickers)
        - len(skipped_tickers)
        - len(failed_tickers),
    )
    return (
        total_candidates,
        done_tickers,
        skipped_tickers,
        failed_tickers,
        pending_count,
        progress_source,
    )


def verify_source_integrity(
    source_dir: Path,
    *,
    source_db: Path | None = None,
    raise_on_error: bool = True,
) -> SourceIntegrityReport:
    """Strictly read-only dual SHA-256 verification of raw artifacts, provenance, and manifest.

    Checks:
      1. Every file under ``raw/sha256/`` matches its filename SHA-256 stem and bucket prefix.
      2. Every receipt under ``raw/provenance/`` matches its filename SHA-256 stem AND its
         referenced ``raw_archive_path`` exists with matching ``payload_file_sha256``.
      3. Every row in ``filings_manifest`` (opened strictly via SQLite ``mode=ro``) references
         an existing raw file whose bytes match ``payload_file_sha256`` AND whose extracted
         record at ``raw_record_locator`` matches ``canonical_content_sha256``.
    """
    src_resolved = source_dir.resolve()
    raw_root = (src_resolved / "raw").resolve()
    state_db = (
        source_db.resolve()
        if source_db is not None
        else (src_resolved / "metadata" / "state.sqlite").resolve()
    )
    if not state_db.is_file():
        raise FileNotFoundError(f"Source SQLite database not found: {state_db}")
    if not raw_root.is_dir():
        raise FileNotFoundError(f"Source raw archive directory not found: {raw_root}")

    missing_files: list[str] = []
    payload_sha_mismatches: list[str] = []
    canonical_sha_mismatches: list[str] = []
    provenance_mismatches: list[str] = []

    # (i) Verify every raw/sha256/*/* artifact against its SHA-256 filename stem.
    verified_file_sha: dict[str, str] = {}
    sha256_dir = raw_root / "sha256"
    raw_sha256_files_checked = 0
    if sha256_dir.is_dir():
        for entry in sorted(sha256_dir.rglob("*")):
            if entry.is_symlink():
                rel_posix = entry.relative_to(raw_root).as_posix()
                raw_sha256_files_checked += 1
                payload_sha_mismatches.append(f"{rel_posix}:symlink_disallowed")
                continue
            if not entry.is_file():
                continue
            raw_sha256_files_checked += 1
            rel_posix = entry.relative_to(raw_root).as_posix()
            try:
                actual_sha = payload_file_sha256(entry.read_bytes())
            except OSError as exc:
                missing_files.append(f"{rel_posix}:{type(exc).__name__}")
                continue
            verified_file_sha[rel_posix] = actual_sha
            stem = entry.stem.lower()
            if _SHA256_HEX_RE.fullmatch(stem) is None or actual_sha != stem:
                payload_sha_mismatches.append(
                    f"{rel_posix}:stem={stem[:12]}..actual={actual_sha[:12]}.."
                )
            elif entry.parent.name.lower() != actual_sha[:2]:
                payload_sha_mismatches.append(
                    f"{rel_posix}:bucket={entry.parent.name}!=prefix={actual_sha[:2]}"
                )

    # (ii) Verify every raw/provenance/*.json receipt against its stem and payload reference.
    provenance_dir = raw_root / "provenance"
    raw_provenance_files_checked = 0
    if provenance_dir.is_dir():
        for receipt_path in sorted(provenance_dir.glob("*.json")):
            if receipt_path.is_symlink():
                rel_receipt = receipt_path.relative_to(raw_root).as_posix()
                raw_provenance_files_checked += 1
                provenance_mismatches.append(f"{rel_receipt}:symlink_disallowed")
                continue
            if not receipt_path.is_file():
                continue
            raw_provenance_files_checked += 1
            rel_receipt = receipt_path.relative_to(raw_root).as_posix()
            try:
                receipt_bytes = receipt_path.read_bytes()
            except OSError as exc:
                provenance_mismatches.append(f"{rel_receipt}:{type(exc).__name__}")
                continue
            actual_receipt_sha = payload_file_sha256(receipt_bytes)
            if actual_receipt_sha != receipt_path.stem.lower():
                provenance_mismatches.append(f"{rel_receipt}:receipt_sha_mismatch")
                continue
            try:
                receipt_obj = json.loads(receipt_bytes)
            except (ValueError, UnicodeError):
                provenance_mismatches.append(f"{rel_receipt}:invalid_json")
                continue
            if not isinstance(receipt_obj, dict):
                provenance_mismatches.append(f"{rel_receipt}:not_object")
                continue
            ref_rel = receipt_obj.get("raw_archive_path")
            ref_sha = receipt_obj.get("payload_file_sha256")
            if not isinstance(ref_rel, str) or not isinstance(ref_sha, str):
                provenance_mismatches.append(f"{rel_receipt}:missing_payload_ref")
                continue
            ref_full = (raw_root / ref_rel).resolve()
            if not ref_full.is_relative_to(raw_root):
                provenance_mismatches.append(f"{rel_receipt}:path_escape:{ref_rel}")
                continue
            ref_posix = ref_full.relative_to(raw_root).as_posix()
            actual_ref_sha = verified_file_sha.get(ref_posix)
            if actual_ref_sha is None:
                if not ref_full.is_file():
                    missing_files.append(f"provenance_ref:{ref_rel}")
                    provenance_mismatches.append(
                        f"{rel_receipt}:missing_target:{ref_rel}"
                    )
                    continue
                actual_ref_sha = payload_file_sha256(ref_full.read_bytes())
                verified_file_sha[ref_posix] = actual_ref_sha
            if actual_ref_sha != ref_sha:
                provenance_mismatches.append(
                    f"{rel_receipt}:target_sha_mismatch:{ref_rel}"
                )

    # (iii) Verify every filings_manifest row against BOTH payload_file_sha256 & canonical_content_sha256.
    with closing(
        sqlite3.connect(f"{state_db.as_uri()}?mode=ro", uri=True, timeout=10.0)
    ) as conn:
        conn.row_factory = sqlite3.Row
        manifest_rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT filing_id, issuer_id, ticker_at_filing, source_provider,
                       connector_name, statement_type, statement_scope, period_type,
                       fiscal_year, fiscal_quarter, fiscal_year_start_date,
                       period_start_date, period_end_date, payload_file_sha256,
                       canonical_content_sha256, raw_archive_path, raw_record_locator,
                       provider_attrs_json
                FROM filings_manifest
                ORDER BY raw_archive_path, filing_id
                """
            ).fetchall()
        ]

    manifest_tickers = [
        str(row["ticker_at_filing"]).strip().upper() for row in manifest_rows
    ]
    (
        total_candidates,
        done_tickers,
        skipped_tickers,
        failed_tickers,
        pending_count,
        progress_source,
    ) = _parse_progress_and_candidates(src_resolved, manifest_tickers)

    referenced_raw_paths: set[str] = set()
    cached_rel_path: str | None = None
    cached_raw_bytes: bytes | None = None
    cached_json_body: dict[str, object] | None = None

    for row in manifest_rows:
        filing_id = str(row["filing_id"])
        raw_rel = row.get("raw_archive_path")
        expected_payload_sha = row.get("payload_file_sha256")
        expected_canonical_sha = row.get("canonical_content_sha256")
        if (
            not isinstance(raw_rel, str)
            or not isinstance(expected_payload_sha, str)
            or not isinstance(expected_canonical_sha, str)
        ):
            payload_sha_mismatches.append(f"{filing_id}:missing_manifest_hash_fields")
            continue

        full_path = (raw_root / raw_rel).resolve()
        if not full_path.is_relative_to(raw_root):
            missing_files.append(f"{filing_id}:path_escape:{raw_rel}")
            continue
        rel_posix = full_path.relative_to(raw_root).as_posix()
        referenced_raw_paths.add(rel_posix)

        if not full_path.is_file():
            missing_files.append(f"{filing_id}:{rel_posix}")
            continue

        actual_payload_sha = verified_file_sha.get(rel_posix)
        if actual_payload_sha is None:
            raw_bytes = full_path.read_bytes()
            actual_payload_sha = payload_file_sha256(raw_bytes)
            verified_file_sha[rel_posix] = actual_payload_sha
            cached_rel_path = rel_posix
            cached_raw_bytes = raw_bytes
            cached_json_body = None

        if actual_payload_sha != expected_payload_sha:
            payload_sha_mismatches.append(
                f"{filing_id}:{rel_posix}:expected={expected_payload_sha[:12]}..actual={actual_payload_sha[:12]}.."
            )
            continue

        if cached_rel_path != rel_posix:
            cached_rel_path = rel_posix
            cached_raw_bytes = full_path.read_bytes()
            cached_json_body = None
        assert cached_raw_bytes is not None

        try:
            scope = StatementScope(str(row["statement_scope"]))
            period_end = date.fromisoformat(str(row["period_end_date"]))
            source_provider = str(row["source_provider"])
            connector_name = str(row["connector_name"])

            if source_provider == "fhsc":
                if cached_json_body is None:
                    parsed_obj = json.loads(cached_raw_bytes)
                    if not isinstance(parsed_obj, dict):
                        raise ContractViolationError(
                            "FHSC raw payload must be a JSON object."
                        )
                    cached_json_body = parsed_obj
                locator = row.get("raw_record_locator")
                loc_match = (
                    _FHSC_LOCATOR_RE.fullmatch(locator)
                    if isinstance(locator, str)
                    else None
                )
                records = cached_json_body.get("data")
                if (
                    loc_match is None
                    or cached_json_body.get("error_code") != "0"
                    or not isinstance(records, list)
                    or int(loc_match[1]) >= len(records)
                ):
                    canonical_sha_mismatches.append(
                        f"{filing_id}:invalid_fhsc_locator_or_body"
                    )
                    continue
                raw_entry = records[int(loc_match[1])]
                if not isinstance(raw_entry, dict):
                    canonical_sha_mismatches.append(
                        f"{filing_id}:fhsc_record_not_object"
                    )
                    continue
                metrics = dict(raw_entry)
                rec_year = metrics.pop("year", None)
                rec_quarter = metrics.pop("quarter", None)
                if (
                    rec_year != row["fiscal_year"]
                    or rec_quarter != row["fiscal_quarter"]
                ):
                    canonical_sha_mismatches.append(f"{filing_id}:fhsc_period_mismatch")
                    continue
            elif connector_name == "file_adapter_v1":
                attrs = json.loads(str(row.get("provider_attrs_json") or "{}"))
                if not isinstance(attrs, dict):
                    raise ContractViolationError("Invalid provider_attrs_json.")
                meta = FilingMetadata.from_mapping(row)
                sheet_val = attrs.get("sheet")
                metrics = FileAdapter(
                    full_path,
                    meta,
                    source_provider=source_provider,
                    metric_column=str(attrs["metric_column"]),
                    value_column=str(attrs["value_column"]),
                    sheet=str(sheet_val) if sheet_val is not None else None,
                    header_row=int(attrs["header_row"]),
                ).read_metrics(cached_raw_bytes)
            else:
                canonical_sha_mismatches.append(
                    f"{filing_id}:unsupported_connector:{connector_name}"
                )
                continue

            computed_canonical = canonical_content_sha256(metrics, scope, period_end)
            if computed_canonical != expected_canonical_sha:
                canonical_sha_mismatches.append(
                    f"{filing_id}:expected={expected_canonical_sha[:12]}..actual={computed_canonical[:12]}.."
                )
        except Exception as exc:
            canonical_sha_mismatches.append(f"{filing_id}:{type(exc).__name__}:{exc}")

    unreferenced_raw_files = len(set(verified_file_sha) - referenced_raw_paths)
    passed = (
        len(manifest_rows) > 0
        and len(missing_files) == 0
        and len(payload_sha_mismatches) == 0
        and len(canonical_sha_mismatches) == 0
        and len(provenance_mismatches) == 0
    )

    report = SourceIntegrityReport(
        total_candidates=total_candidates,
        done_count=len(done_tickers),
        skipped_count=len(skipped_tickers),
        failed_count=len(failed_tickers),
        pending_count=pending_count,
        done_tickers=done_tickers,
        skipped_tickers=skipped_tickers,
        failed_tickers=failed_tickers,
        manifest_rows_checked=len(manifest_rows),
        raw_sha256_files_checked=raw_sha256_files_checked,
        raw_provenance_files_checked=raw_provenance_files_checked,
        unreferenced_raw_files=unreferenced_raw_files,
        missing_files=tuple(missing_files),
        payload_sha_mismatches=tuple(payload_sha_mismatches),
        canonical_sha_mismatches=tuple(canonical_sha_mismatches),
        provenance_mismatches=tuple(provenance_mismatches),
        passed=passed,
        progress_source=progress_source,
    )
    if not passed and raise_on_error:
        raise ArchiveIntegrityError(
            "Source universe dual SHA-256 integrity verification failed: "
            f"manifest_rows={report.manifest_rows_checked}, "
            f"missing_files={len(report.missing_files)}, "
            f"payload_sha_mismatches={len(report.payload_sha_mismatches)}, "
            f"canonical_sha_mismatches={len(report.canonical_sha_mismatches)}, "
            f"provenance_mismatches={len(report.provenance_mismatches)}; "
            f"samples={list(report.missing_files[:2] + report.payload_sha_mismatches[:2] + report.canonical_sha_mismatches[:2] + report.provenance_mismatches[:2])}"
        )
    return report


# ---------------------------------------------------------------------------
# Public API #5: Read-Only Universe Migration
# ---------------------------------------------------------------------------


def migrate_universe_read_only(
    source_dir: Path,
    target_dir: Path,
    *,
    issuers_path: Path | None = None,
    backup_dir: Path | None = None,
    dagster_home: Path | None = None,
    source_db: Path | None = None,
) -> dict[str, object]:
    """Verify source archive integrity, perform pre-cutover backup, and migrate read-only.

    Never writes to ``source_dir`` or opens source SQLite in read-write mode.
    """
    src_resolved = source_dir.resolve()
    dst_resolved = target_dir.resolve()
    if src_resolved == dst_resolved:
        raise ValueError("source_dir and target_dir must be distinct directories.")
    if dst_resolved.is_relative_to(src_resolved) or src_resolved.is_relative_to(
        dst_resolved
    ):
        raise ValueError("source_dir and target_dir must not nest inside each other.")

    src_state_db = (
        source_db.resolve()
        if source_db is not None
        else (src_resolved / "metadata" / "state.sqlite").resolve()
    )
    integrity = verify_source_integrity(
        src_resolved, source_db=src_state_db, raise_on_error=True
    )

    resolved_issuers_src = (
        issuers_path.resolve()
        if issuers_path is not None
        else (src_resolved / "curated" / "issuers.parquet").resolve()
    )
    if not resolved_issuers_src.is_file():
        raise FileNotFoundError(
            f"Issuers reference Parquet not found: {resolved_issuers_src}"
        )
    issuers_df = canonicalize_table(
        pl.read_parquet(resolved_issuers_src), ISSUERS_SCHEMA
    )

    # Pre-cutover backup when target already has state or explicit backup_dir is given.
    target_state_path = dst_resolved / "metadata" / "state.sqlite"
    target_ducklake_path = dst_resolved / "metadata" / "ducklake_catalog.sqlite"
    target_snaps_root = dst_resolved / "published" / "snapshots"
    target_has_prior = (
        target_state_path.is_file()
        or target_ducklake_path.is_file()
        or (
            target_snaps_root.is_dir()
            and any(
                p.is_dir() and p.name.startswith("snap_")
                for p in target_snaps_root.iterdir()
            )
        )
    )
    effective_backup_dir = backup_dir
    if effective_backup_dir is None and target_has_prior:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        effective_backup_dir = (
            dst_resolved.parent / f"{dst_resolved.name}_backup_{stamp}"
        )

    backup_report: dict[str, object]
    if effective_backup_dir is not None:
        backup_report = pre_cutover_backup(dst_resolved, effective_backup_dir)
    else:
        backup_report = {"backed_up": False, "backup_dir": None}

    dst_resolved.mkdir(parents=True, exist_ok=True)
    raw_copied = _copy_tree_no_sqlite(src_resolved / "raw", dst_resolved / "raw")

    disclosures_copied = 0
    src_disclosures = src_resolved / "raw_disclosures"
    if src_disclosures.is_dir():
        disclosures_copied = _copy_tree_no_sqlite(
            src_disclosures, dst_resolved / "raw_disclosures"
        )

    src_disc_manifest = src_resolved / "disclosure_manifest.json"
    if src_disc_manifest.is_symlink():
        raise ContractViolationError(
            f"Refusing to copy symbolic link during migration: {src_disc_manifest}"
        )
    if src_disc_manifest.is_file():
        shutil.copy2(src_disc_manifest, dst_resolved / "disclosure_manifest.json")

    dst_issuers = dst_resolved / "curated" / "issuers.parquet"
    dst_issuers.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=dst_issuers.parent,
        prefix=".issuers_",
        suffix=".parquet.tmp",
        delete=False,
    ) as tmp_handle:
        tmp_issuers = Path(tmp_handle.name)
    try:
        issuers_df.write_parquet(tmp_issuers)
        os.replace(tmp_issuers, dst_issuers)
    except BaseException:
        tmp_issuers.unlink(missing_ok=True)
        raise

    backup_sqlite_wal_safe(
        src_state_db,
        target_state_path,
        read_only_source=True,
    )

    # Target DB hygiene (target ONLY — never source_dir!):
    target_state = StateStore(target_state_path)
    target_state.initialize()
    target_state.recover_interrupted_runs()

    dangling_snapshot_cleared: str | None = None
    surviving_active_sid: str | None = None
    active_row = target_state.active_snapshot()
    if active_row is not None and active_row.get("snapshot_id") is not None:
        active_sid = str(active_row["snapshot_id"])
        parquet_exists = (
            dst_resolved / "published" / "snapshots" / active_sid
        ).is_dir()
        ducklake_has_snapshot = _ducklake_has_snapshot_ro(
            target_ducklake_path,
            dst_resolved / "lakehouse" / "data",
            active_sid,
        )
        if not parquet_exists and not ducklake_has_snapshot:
            with target_state.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM active_snapshot WHERE singleton = 1")
            dangling_snapshot_cleared = active_sid
        else:
            surviving_active_sid = active_sid

    stale_transform_state_cleared = False
    transform_state_path = dst_resolved / "metadata" / "_transform_state.json"
    if transform_state_path.is_file():
        ts_matches = False
        if surviving_active_sid is not None:
            try:
                ts_payload = json.loads(
                    transform_state_path.read_text(encoding="utf-8")
                )
                ts_matches = (
                    isinstance(ts_payload, dict)
                    and ts_payload.get("snapshot_id") == surviving_active_sid
                )
            except (OSError, ValueError):
                ts_matches = False
        if not ts_matches:
            transform_state_path.unlink(missing_ok=True)
            stale_transform_state_cleared = True

    resolved_dagster_home = _ensure_dagster_home(
        dagster_home
        if dagster_home is not None
        else dst_resolved / "metadata" / "dagster"
    )
    with dg.DagsterInstance.from_config(str(resolved_dagster_home)) as instance:
        synced_tickers = sync_corporate_ticker_partitions(
            instance, dst_issuers, prune_removed=True
        )
        pool_limits = configure_concurrency_pools(instance)

    issuers_tickers = set(issuers_df["ticker"].drop_nulls().cast(pl.String).to_list())
    done_set = set(integrity.done_tickers)
    return {
        "source_dir": src_resolved.as_posix(),
        "source_db": src_state_db.as_posix(),
        "target_dir": dst_resolved.as_posix(),
        "issuers_source": resolved_issuers_src.as_posix(),
        "integrity": asdict(integrity),
        "pre_cutover_backup": backup_report,
        "copied_files": {
            "raw_files": raw_copied,
            "raw_disclosure_files": disclosures_copied,
            "issuers_rows": issuers_df.height,
        },
        "ticker_alignment": {
            "done_count": len(done_set),
            "issuers_parquet_count": len(issuers_tickers),
            "in_done_not_issuers": sorted(done_set - issuers_tickers),
            "in_issuers_not_done": sorted(issuers_tickers - done_set),
        },
        "target_hygiene": {
            "dangling_active_snapshot_cleared": dangling_snapshot_cleared,
            "stale_transform_state_cleared": stale_transform_state_cleared,
            "dagster_home": resolved_dagster_home.as_posix(),
            "synced_corporate_partitions_count": len(synced_tickers),
            "dagster_pool_limits": pool_limits,
        },
    }


# ---------------------------------------------------------------------------
# Phase A: Layer 1 Disclosures & Gate 4 (RF_07 Before/After)
# ---------------------------------------------------------------------------


def _raw_statutory_deadline_no_holidays(meta: FilingMetadata) -> date:
    """Compute Pre-V2 statutory deadline without Vietnam holiday or weekend rollover."""
    days = 30
    if meta.period_type == PeriodType.ANNUAL:
        days = 90
    elif meta.period_type == PeriodType.SEMI_ANNUAL or (
        meta.fiscal_quarter == 2
        and meta.audit_status in (AuditStatus.REVIEWED, AuditStatus.UNKNOWN)
    ):
        days = 60
    elif meta.audit_status == AuditStatus.REVIEWED:
        days = 45
    return meta.period_end_date + timedelta(days=days)


def _extract_ticker_from_disclosure_url(
    disclosure_source: str, source_url: str
) -> str | None:
    if disclosure_source == "vietcap_iq":
        match = _VCI_COMPANY_URL_RE.search(source_url)
        return match.group(1).strip().upper() if match is not None else None
    if disclosure_source == "cafef_bctc":
        parsed = urlsplit(source_url)
        for key, vals in parse_qs(parsed.query).items():
            if key.lower() in {"sym", "symbol"} and vals:
                cleaned = vals[0].strip().upper()
                if cleaned:
                    return cleaned
    return None


def _unwrap_cafef_archive_payload(raw_bytes: bytes) -> bytes:
    """Unwrap ``_json_evidence_bytes`` JSON envelope ``{"raw_utf8": ...}`` when present."""
    try:
        parsed = json.loads(raw_bytes)
    except (ValueError, UnicodeError):
        return raw_bytes
    if isinstance(parsed, dict):
        raw_utf8 = parsed.get("raw_utf8")
        if isinstance(raw_utf8, str):
            return raw_utf8.encode("utf-8")
    return raw_bytes


def _load_local_disclosures(
    disclosures_root: Path,
    tickers: Sequence[str],
    *,
    max_date: date,
    raw_root: Path | None = None,
    disclosure_manifest_path: Path | None = None,
) -> tuple[
    list[DisclosureRecord], list[DisclosureRecord], dict[str, bytes], dict[str, int]
]:
    vietcap_records: list[DisclosureRecord] = []
    cafef_records: list[DisclosureRecord] = []
    evidence_payloads: dict[str, bytes] = {}
    counts: dict[str, int] = {
        "vietcap_files_loaded": 0,
        "cafef_files_loaded": 0,
        "vietcap_from_dir": 0,
        "cafef_from_dir": 0,
        "vietcap_from_archive_fallback": 0,
        "cafef_from_archive_fallback": 0,
        "vietcap_records_parsed": 0,
        "cafef_records_parsed": 0,
    }
    target_tickers = {t.strip().upper() for t in tickers}
    loaded_vci: set[str] = set()
    loaded_cafef: set[str] = set()

    if disclosures_root.is_dir():
        for ticker in tickers:
            vci_candidates = (
                disclosures_root / "vietcap_iq" / f"{ticker}.json",
                disclosures_root / f"{ticker}.json",
            )
            vci_path = next(
                (p for p in vci_candidates if not p.is_symlink() and p.is_file()), None
            )
            if vci_path is not None:
                vci_bytes = vci_path.read_bytes()
                vci_url = DEFAULT_VIETCAP_URL_TEMPLATE.format(ticker=ticker)
                parsed_vci = parse_vietcap_iq_disclosures(
                    ticker, vci_bytes, source_url=vci_url, max_date=max_date
                )
                counts["vietcap_files_loaded"] += 1
                counts["vietcap_from_dir"] += 1
                counts["vietcap_records_parsed"] += len(parsed_vci)
                loaded_vci.add(ticker)
                if parsed_vci:
                    vietcap_records.extend(parsed_vci)
                    evidence_payloads[vci_url] = vci_bytes

            cafef_candidates = (
                disclosures_root / "cafef_bctc" / f"{ticker}.html",
                disclosures_root / f"{ticker}.html",
            )
            cafef_path = next(
                (p for p in cafef_candidates if not p.is_symlink() and p.is_file()),
                None,
            )
            if cafef_path is not None:
                cafef_bytes = cafef_path.read_bytes()
                cafef_url = DEFAULT_CAFEF_URL_TEMPLATE.format(ticker=ticker)
                parsed_cafef = parse_cafef_bctc_disclosures(
                    ticker, cafef_bytes, source_url=cafef_url, max_date=max_date
                )
                counts["cafef_files_loaded"] += 1
                counts["cafef_from_dir"] += 1
                counts["cafef_records_parsed"] += len(parsed_cafef)
                loaded_cafef.add(ticker)
                if parsed_cafef:
                    cafef_records.extend(parsed_cafef)
                    evidence_payloads[cafef_url] = cafef_bytes

    # Fallback: resolve archived disclosure evidence from raw/provenance/ + raw/sha256/
    # (and optional disclosure_manifest.json) when raw_disclosures/ is absent or partial.
    missing_vci = target_tickers - loaded_vci
    missing_cafef = target_tickers - loaded_cafef
    if raw_root is not None and raw_root.is_dir() and (missing_vci or missing_cafef):
        resolved_raw = raw_root.resolve()
        sha_to_vci_ticker: dict[str, str] = {}
        sha_to_cafef_ticker: dict[str, str] = {}
        ticker_to_vci_sha: dict[str, str] = {}
        if (
            disclosure_manifest_path is not None
            and not disclosure_manifest_path.is_symlink()
            and disclosure_manifest_path.is_file()
        ):
            try:
                dm_raw = json.loads(
                    disclosure_manifest_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                dm_raw = None
            if isinstance(dm_raw, list):
                for entry in dm_raw:
                    if not isinstance(entry, dict):
                        continue
                    tkr = str(entry.get("ticker") or "").strip().upper()
                    if not tkr or tkr not in target_tickers:
                        continue
                    v_sha = entry.get("vietcap_sha256")
                    if isinstance(v_sha, str) and _SHA256_HEX_RE.fullmatch(v_sha):
                        sha_to_vci_ticker[v_sha] = tkr
                        ticker_to_vci_sha[tkr] = v_sha
                    c_sha = entry.get("cafef_sha256")
                    if isinstance(c_sha, str) and _SHA256_HEX_RE.fullmatch(c_sha):
                        sha_to_cafef_ticker[c_sha] = tkr

        # Index newest receipt per (source, ticker) under raw/provenance/
        archived_candidates: dict[tuple[str, str], tuple[str, Path]] = {}
        prov_dir = resolved_raw / "provenance"
        if prov_dir.is_dir():
            for receipt_path in sorted(prov_dir.glob("*.json")):
                if receipt_path.is_symlink() or not receipt_path.is_file():
                    continue
                try:
                    receipt_obj = json.loads(receipt_path.read_bytes())
                except (OSError, ValueError, UnicodeError):
                    continue
                if not isinstance(receipt_obj, dict):
                    continue
                provider_attrs = receipt_obj.get("provider_attrs")
                if not isinstance(provider_attrs, dict):
                    continue
                disc_source = provider_attrs.get("disclosure_source")
                if disc_source not in ("vietcap_iq", "cafef_bctc"):
                    continue
                raw_rel = receipt_obj.get("raw_archive_path")
                if not isinstance(raw_rel, str):
                    continue
                payload_path = (resolved_raw / raw_rel).resolve()
                if (
                    not payload_path.is_relative_to(resolved_raw)
                    or payload_path.is_symlink()
                    or not payload_path.is_file()
                ):
                    continue

                source_url_raw = provider_attrs.get("source_url")
                tkr_found: str | None = (
                    _extract_ticker_from_disclosure_url(disc_source, source_url_raw)
                    if isinstance(source_url_raw, str)
                    else None
                )
                if tkr_found is None:
                    ev_sha = provider_attrs.get("raw_evidence_sha256")
                    if isinstance(ev_sha, str):
                        tkr_found = (
                            sha_to_vci_ticker.get(ev_sha)
                            if disc_source == "vietcap_iq"
                            else sha_to_cafef_ticker.get(ev_sha)
                        )
                if tkr_found is None or tkr_found not in target_tickers:
                    continue
                if disc_source == "vietcap_iq" and tkr_found in loaded_vci:
                    continue
                if disc_source == "cafef_bctc" and tkr_found in loaded_cafef:
                    continue

                fetched_str = str(receipt_obj.get("fetched_at") or "")
                cand_key = (disc_source, tkr_found)
                prev_cand = archived_candidates.get(cand_key)
                if prev_cand is None or fetched_str >= prev_cand[0]:
                    archived_candidates[cand_key] = (fetched_str, payload_path)

        # Direct sha256 lookup fallback for Vietcap JSON via disclosure_manifest.json
        for tkr in sorted(missing_vci):
            if ("vietcap_iq", tkr) in archived_candidates:
                continue
            v_sha = ticker_to_vci_sha.get(tkr)
            if v_sha is None:
                continue
            direct_path = (
                resolved_raw / "sha256" / v_sha[:2] / f"{v_sha}.json"
            ).resolve()
            if (
                direct_path.is_relative_to(resolved_raw)
                and not direct_path.is_symlink()
                and direct_path.is_file()
            ):
                archived_candidates[("vietcap_iq", tkr)] = ("", direct_path)

        for (disc_source, ticker), (_, payload_path) in sorted(
            archived_candidates.items()
        ):
            raw_bytes = payload_path.read_bytes()
            if disc_source == "vietcap_iq":
                vci_url = DEFAULT_VIETCAP_URL_TEMPLATE.format(ticker=ticker)
                parsed_vci = parse_vietcap_iq_disclosures(
                    ticker, raw_bytes, source_url=vci_url, max_date=max_date
                )
                counts["vietcap_files_loaded"] += 1
                counts["vietcap_from_archive_fallback"] += 1
                counts["vietcap_records_parsed"] += len(parsed_vci)
                loaded_vci.add(ticker)
                if parsed_vci:
                    vietcap_records.extend(parsed_vci)
                    evidence_payloads[vci_url] = raw_bytes
            else:
                cafef_url = DEFAULT_CAFEF_URL_TEMPLATE.format(ticker=ticker)
                html_bytes = _unwrap_cafef_archive_payload(raw_bytes)
                parsed_cafef = parse_cafef_bctc_disclosures(
                    ticker, html_bytes, source_url=cafef_url, max_date=max_date
                )
                counts["cafef_files_loaded"] += 1
                counts["cafef_from_archive_fallback"] += 1
                counts["cafef_records_parsed"] += len(parsed_cafef)
                loaded_cafef.add(ticker)
                if parsed_cafef:
                    cafef_records.extend(parsed_cafef)
                    evidence_payloads[cafef_url] = html_bytes

    return vietcap_records, cafef_records, evidence_payloads, counts


def _evaluate_rf07_before_after(
    manifest_rows: Sequence[Mapping[str, object]],
    vietcap_records: Sequence[DisclosureRecord],
    cafef_records: Sequence[DisclosureRecord],
    merged: Mapping[DisclosureSlot, DisclosureRecord],
    period_end_dates: Mapping[DisclosureSlot, date],
) -> dict[str, object]:
    """Compare Pre-V2 (Baseline A & B) vs Post-V2 RF_07 late-filing flags and collect examples."""
    vci_by_slot: dict[DisclosureSlot, DisclosureRecord] = {
        (
            r.ticker,
            r.period_type,
            r.fiscal_year,
            r.fiscal_quarter,
            r.statement_scope,
        ): r
        for r in vietcap_records
    }
    cafef_by_slot: dict[DisclosureSlot, DisclosureRecord] = {
        (
            r.ticker,
            r.period_type,
            r.fiscal_year,
            r.fiscal_quarter,
            r.statement_scope,
        ): r
        for r in cafef_records
    }

    total_filings = len(manifest_rows)
    base_a_matched = 0
    base_a_late = 0
    base_b_matched = 0
    base_b_late = 0
    v2_matched = 0
    v2_late = 0

    base_a_late_periods: set[tuple[str, str, str, str]] = set()
    base_b_late_periods: set[tuple[str, str, str, str]] = set()
    v2_late_periods: set[tuple[str, str, str, str]] = set()
    all_matched_periods: set[tuple[str, str, str, str]] = set()

    tet_examples: dict[tuple[str, str], dict[str, object]] = {}
    apr30_examples: dict[tuple[str, str], dict[str, object]] = {}
    q4_cafef_examples: dict[tuple[str, str], dict[str, object]] = {}
    audit_guard_examples: dict[tuple[str, str], dict[str, object]] = {}
    other_rollover_examples: dict[tuple[str, str], dict[str, object]] = {}

    for row in manifest_rows:
        meta = FilingMetadata.from_mapping(row)
        slot: DisclosureSlot = (
            meta.ticker_at_filing,
            meta.period_type,
            meta.fiscal_year,
            meta.fiscal_quarter,
            meta.statement_scope,
        )
        period_key = (
            meta.issuer_id,
            str(meta.statement_scope),
            str(meta.period_type),
            meta.period_end_date.isoformat(),
        )
        ex_key = (
            meta.ticker_at_filing,
            f"{meta.fiscal_year}-Q{meta.fiscal_quarter}"
            if meta.fiscal_quarter is not None
            else f"{meta.fiscal_year}-FY",
        )

        # Baseline A: Vietcap-only + raw calendar (no VN holidays, no CafeF merge)
        vci_rec = vci_by_slot.get(slot)
        vci_late_raw = False
        if vci_rec is not None and vci_rec.published_at >= meta.period_end_date:
            base_a_matched += 1
            vci_audit = (
                vci_rec.audit_status
                if meta.audit_status == AuditStatus.UNKNOWN
                and vci_rec.audit_status not in (None, AuditStatus.UNKNOWN)
                else meta.audit_status
            )
            assert vci_audit is not None
            meta_a = replace(
                meta, published_at=vci_rec.published_at, audit_status=vci_audit
            )
            raw_dl_a = _raw_statutory_deadline_no_holidays(meta_a)
            if vci_rec.published_at > raw_dl_a:
                base_a_late += 1
                vci_late_raw = True
                base_a_late_periods.add(period_key)

        # Baseline B (Merged + raw calendar) & V2 Patched (Merged + 2016-2026 VN holidays)
        rec = merged.get(slot)
        if rec is not None and rec.published_at >= meta.period_end_date:
            base_b_matched += 1
            v2_matched += 1
            all_matched_periods.add(period_key)

            next_audit = (
                rec.audit_status
                if meta.audit_status == AuditStatus.UNKNOWN
                and rec.audit_status not in (None, AuditStatus.UNKNOWN)
                else meta.audit_status
            )
            assert next_audit is not None
            enriched_meta = replace(
                meta, published_at=rec.published_at, audit_status=next_audit
            )
            raw_dl = _raw_statutory_deadline_no_holidays(enriched_meta)
            late_raw = rec.published_at > raw_dl
            late_v2 = bool(enriched_meta.is_filed_late)
            if late_raw:
                base_b_late += 1
                base_b_late_periods.add(period_key)
            if late_v2:
                v2_late += 1
                v2_late_periods.add(period_key)

            # Capture real case examples (deduplicated per ticker-period)
            if late_raw and not late_v2:
                days_after_raw = (rec.published_at - raw_dl).days
                sample = {
                    "ticker": meta.ticker_at_filing,
                    "period": ex_key[1],
                    "period_end_date": meta.period_end_date.isoformat(),
                    "published_at": rec.published_at.isoformat(),
                    "raw_deadline": raw_dl.isoformat(),
                    "days_after_raw_deadline": days_after_raw,
                    "source_name": rec.source_name,
                    "audit_status": str(next_audit),
                }
                if (
                    meta.period_type == PeriodType.QUARTER
                    and meta.fiscal_year == 2024
                    and meta.fiscal_quarter == 4
                    and ex_key not in tet_examples
                ):
                    tet_examples[ex_key] = sample
                elif (
                    meta.period_type == PeriodType.QUARTER
                    and meta.fiscal_quarter == 1
                    and raw_dl.month == 4
                    and raw_dl.day == 30
                    and ex_key not in apr30_examples
                ):
                    apr30_examples[ex_key] = sample
                elif ex_key not in other_rollover_examples:
                    other_rollover_examples[ex_key] = sample

            if (
                vci_late_raw
                and not late_v2
                and rec.source_name == "cafef_bctc"
                and vci_rec is not None
                and rec.published_at < vci_rec.published_at
                and ex_key not in q4_cafef_examples
            ):
                q4_cafef_examples[ex_key] = {
                    "ticker": meta.ticker_at_filing,
                    "period": ex_key[1],
                    "period_end_date": meta.period_end_date.isoformat(),
                    "vietcap_public_date": vci_rec.published_at.isoformat(),
                    "cafef_filing_date": rec.published_at.isoformat(),
                    "days_earlier": (vci_rec.published_at - rec.published_at).days,
                    "audit_status": str(next_audit),
                }

    # Audit-tier PIT guard blocks: count slots where UNAUDITED CafeF was strictly earlier
    # than AUDITED Annual or REVIEWED Q2 Vietcap and therefore blocked by merge_disclosure_records.
    audit_guard_blocked_slots = 0
    for slot, vci_rec in vci_by_slot.items():
        cafef_rec = cafef_by_slot.get(slot)
        if cafef_rec is None:
            continue
        slot_end = period_end_dates.get(slot)
        if slot_end is None:
            continue
        if (
            vci_rec.published_at >= slot_end
            and cafef_rec.published_at >= slot_end
            and cafef_rec.published_at < vci_rec.published_at
            and cafef_rec.audit_status == AuditStatus.UNAUDITED
            and (
                (
                    vci_rec.period_type == PeriodType.ANNUAL
                    and vci_rec.audit_status == AuditStatus.AUDITED
                )
                or (
                    vci_rec.period_type == PeriodType.QUARTER
                    and vci_rec.fiscal_quarter == 2
                    and vci_rec.audit_status == AuditStatus.REVIEWED
                )
            )
        ):
            chosen = merged.get(slot)
            if chosen is not None and chosen.source_name == "vietcap_iq":
                audit_guard_blocked_slots += 1
                ex_key = (
                    vci_rec.ticker,
                    f"{vci_rec.fiscal_year}-Q{vci_rec.fiscal_quarter}"
                    if vci_rec.fiscal_quarter is not None
                    else f"{vci_rec.fiscal_year}-FY",
                )
                if ex_key not in audit_guard_examples:
                    audit_guard_examples[ex_key] = {
                        "ticker": vci_rec.ticker,
                        "period": ex_key[1],
                        "period_end_date": slot_end.isoformat(),
                        "kept_vietcap_date": vci_rec.published_at.isoformat(),
                        "kept_vietcap_audit_status": str(vci_rec.audit_status),
                        "blocked_cafef_date": cafef_rec.published_at.isoformat(),
                        "blocked_cafef_audit_status": str(cafef_rec.audit_status),
                    }

    def _rate(num: int, den: int) -> float:
        return round(100.0 * num / den, 4) if den > 0 else 0.0

    n_periods = len(all_matched_periods)
    return {
        "total_manifest_filings": total_filings,
        "matched_periods_total": n_periods,
        "baseline_a_vietcap_raw_calendar": {
            "matched_filings": base_a_matched,
            "late_filings": base_a_late,
            "late_filing_rate_pct": _rate(base_a_late, base_a_matched),
            "late_periods": len(base_a_late_periods),
            "late_period_rate_pct": _rate(len(base_a_late_periods), n_periods),
        },
        "baseline_b_merged_raw_calendar": {
            "matched_filings": base_b_matched,
            "late_filings": base_b_late,
            "late_filing_rate_pct": _rate(base_b_late, base_b_matched),
            "late_periods": len(base_b_late_periods),
            "late_period_rate_pct": _rate(len(base_b_late_periods), n_periods),
        },
        "v2_patched_merged_vn_holidays": {
            "matched_filings": v2_matched,
            "late_filings": v2_late,
            "late_filing_rate_pct": _rate(v2_late, v2_matched),
            "late_periods": len(v2_late_periods),
            "late_period_rate_pct": _rate(len(v2_late_periods), n_periods),
        },
        "filings_saved_vs_baseline_a": base_a_late - v2_late,
        "filings_saved_vs_baseline_b_holidays": base_b_late - v2_late,
        "periods_saved_vs_baseline_a": len(base_a_late_periods) - len(v2_late_periods),
        "periods_saved_vs_baseline_b_holidays": len(base_b_late_periods)
        - len(v2_late_periods),
        "reduction_vs_baseline_a_pct": _rate(base_a_late - v2_late, base_a_late),
        "reduction_vs_baseline_b_pct": _rate(base_b_late - v2_late, base_b_late),
        "audit_tier_guard_blocked_slots": audit_guard_blocked_slots,
        "case_examples": {
            "tet_2024_q4_rollover_total": len(tet_examples),
            "tet_2024_q4_rollover": list(tet_examples.values())[:10],
            "april30_may1_q1_rollover_total": len(apr30_examples),
            "april30_may1_q1_rollover": list(apr30_examples.values())[:10],
            "other_holiday_or_weekend_rollover_total": len(other_rollover_examples),
            "other_holiday_or_weekend_rollover": list(other_rollover_examples.values())[
                :10
            ],
            "vietcap_q4_fixed_by_cafef_total": len(q4_cafef_examples),
            "vietcap_q4_fixed_by_cafef": list(q4_cafef_examples.values())[:10],
            "audit_tier_guard_blocked_total": len(audit_guard_examples),
            "audit_tier_guard_blocked": list(audit_guard_examples.values())[:10],
        },
    }


def _run_layer1_disclosures(
    target_dir: Path,
    target_state: StateStore,
    tickers: Sequence[str],
    *,
    as_of: date,
    stamp: str,
) -> dict[str, object]:
    """Execute Layer 1 disclosure merge/enrichment and capture Gate 4 before/after metrics."""
    with target_state.connection() as conn:
        manifest_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM filings_manifest WHERE source_provider = 'fhsc' "
                "ORDER BY ticker_at_filing, period_end_date, statement_type, revision_no"
            ).fetchall()
        ]

    period_end_dates: dict[DisclosureSlot, date] = {}
    for row in manifest_rows:
        try:
            scope = StatementScope(str(row["statement_scope"]))
            ptype = PeriodType(str(row["period_type"]))
            if scope == StatementScope.UNKNOWN or ptype == PeriodType.SEMI_ANNUAL:
                continue
            slot: DisclosureSlot = (
                str(row["ticker_at_filing"]),
                ptype,
                int(row["fiscal_year"]),
                int(row["fiscal_quarter"])
                if row["fiscal_quarter"] is not None
                else None,
                scope,
            )
            period_end_dates[slot] = date.fromisoformat(str(row["period_end_date"]))
        except (ValueError, TypeError):
            continue

    vci_records, cafef_records, evidence_payloads, file_counts = (
        _load_local_disclosures(
            target_dir / "raw_disclosures",
            tickers,
            max_date=as_of,
            raw_root=target_dir / "raw",
            disclosure_manifest_path=target_dir / "disclosure_manifest.json",
        )
    )
    merged = merge_disclosure_records(
        vci_records, cafef_records, period_end_dates=period_end_dates
    )
    rf07_comparison = _evaluate_rf07_before_after(
        manifest_rows, vci_records, cafef_records, merged, period_end_dates
    )

    archive = RawArchive(target_dir / "raw", target_state)
    t0 = time.perf_counter()
    summary = enrich_fhsc_manifest_disclosures(
        archive,
        list(merged.values()),
        evidence_payloads=evidence_payloads,
        fetched_at=datetime.now(UTC),
        run_id=f"b6_enrich_{stamp}",
        tickers=tickers,
    )
    elapsed_s = round(time.perf_counter() - t0, 4)

    with target_state.connection() as conn:
        basis_rows = conn.execute(
            "SELECT published_at_basis, COUNT(*) FROM filings_manifest "
            "WHERE source_provider = 'fhsc' GROUP BY published_at_basis"
        ).fetchall()
        late_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM filings_manifest "
                "WHERE source_provider = 'fhsc' AND is_filed_late = 1"
            ).fetchone()[0]
        )

    basis_counts = {str(r[0]): int(r[1]) for r in basis_rows}
    total_fhsc = sum(basis_counts.values())
    exact_count = basis_counts.get("EXACT_FILING", 0)

    return {
        "elapsed_seconds": elapsed_s,
        "disclosure_files": file_counts,
        "merged_slots_count": len(merged),
        "enrichment_summary": asdict(summary),
        "post_enrichment_manifest": {
            "total_fhsc_filings": total_fhsc,
            "published_at_basis_counts": basis_counts,
            "exact_filing_ratio_pct": (
                round(100.0 * exact_count / total_fhsc, 4) if total_fhsc > 0 else 0.0
            ),
            "is_filed_late_true_count": late_count,
        },
        "rf07_before_after": rf07_comparison,
    }


# ---------------------------------------------------------------------------
# Phase B: Layer 2 Full + Incremental (0-dirty & 1-dirty) Normalize Benchmark
# ---------------------------------------------------------------------------


def _run_layer2_normalize_benchmark(
    target_dir: Path,
    config_dir: Path,
    target_state: StateStore,
    *,
    stamp: str,
) -> tuple[dict[str, object], Path]:
    """Measure Full Normalize + Incremental 0-dirty + isolated Incremental 1-dirty."""
    curated_ff = target_dir / "curated" / "financial_facts"
    curated_ff.mkdir(parents=True, exist_ok=True)
    raw_root = target_dir / "raw"

    # 1. Full Normalize
    full_run_id = f"b6_norm_full_{stamp}"
    full_dir = curated_ff / full_run_id
    target_state.start_run(
        full_run_id, "MANUAL", {"source": "fhsc", "phase": "full"}, None
    )
    full_step_id = target_state.start_step(full_run_id, "NORMALIZE")

    rss_before_mb, _ = _get_memory_mb()
    tracemalloc.start()
    t0 = time.perf_counter()
    try:
        full_res = normalize_archive_incremental(
            target_state,
            raw_root,
            config_dir,
            full_dir,
            source="fhsc",
            previous_batch_dir=None,
            batch_issuers=50,
        )
    except BaseException as exc:
        tracemalloc.stop()
        target_state.finish_step(
            full_step_id,
            "FAILED",
            error_type=type(exc).__name__,
            error_message=str(exc)[:500],
        )
        target_state.finish_run(full_run_id, "FAILED")
        raise
    full_seconds = round(time.perf_counter() - t0, 4)
    _, tm_peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after_mb, peak_rss_mb = _get_memory_mb()
    tm_peak_mb = round(tm_peak_bytes / (1024.0 * 1024.0), 3)

    target_state.finish_step(full_step_id, "SUCCEEDED")
    target_state.finish_run(full_run_id, "SUCCEEDED")

    # 2. Incremental Normalize — 0 dirty issuers
    inc0_run_id = f"b6_norm_inc0_{stamp}"
    inc0_dir = curated_ff / inc0_run_id
    target_state.start_run(
        inc0_run_id, "MANUAL", {"source": "fhsc", "phase": "inc0"}, None
    )
    inc0_step_id = target_state.start_step(inc0_run_id, "NORMALIZE")
    t1 = time.perf_counter()
    try:
        inc0_res = normalize_archive_incremental(
            target_state,
            raw_root,
            config_dir,
            inc0_dir,
            source="fhsc",
            previous_batch_dir=full_dir,
            batch_issuers=50,
        )
    except BaseException as exc:
        target_state.finish_step(
            inc0_step_id,
            "FAILED",
            error_type=type(exc).__name__,
            error_message=str(exc)[:500],
        )
        target_state.finish_run(inc0_run_id, "FAILED")
        raise
    inc0_seconds = round(time.perf_counter() - t1, 4)
    target_state.finish_step(inc0_step_id, "SUCCEEDED")
    target_state.finish_run(inc0_run_id, "SUCCEEDED")

    # 3. Incremental Normalize — 1 dirty issuer (isolated temp copy of state.sqlite!)
    inc1_seconds: float | None = None
    inc1_recomputed = 0
    dirty_issuer_id: str | None = None
    with tempfile.TemporaryDirectory(prefix="finintel_b6_inc1_") as tmp_str:
        tmp_root = Path(tmp_str)
        tmp_state_path = tmp_root / "state.sqlite"
        backup_sqlite_wal_safe(target_state.path, tmp_state_path, read_only_source=True)
        tmp_state = StateStore(tmp_state_path)
        with tmp_state.connection() as conn:
            first_row = conn.execute(
                "SELECT filing_id, issuer_id, provider_attrs_json "
                "FROM filings_manifest WHERE source_provider = 'fhsc' "
                "ORDER BY issuer_id, filing_id LIMIT 1"
            ).fetchone()
            if first_row is not None:
                dirty_issuer_id = str(first_row["issuer_id"])
                attrs = json.loads(str(first_row["provider_attrs_json"] or "{}"))
                if not isinstance(attrs, dict):
                    attrs = {}
                attrs["_b6_inc1_dirty_probe"] = stamp
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE filings_manifest SET provider_attrs_json = ? WHERE filing_id = ?",
                    (
                        json.dumps(attrs, ensure_ascii=False, sort_keys=True),
                        str(first_row["filing_id"]),
                    ),
                )
        if dirty_issuer_id is not None:
            t2 = time.perf_counter()
            inc1_res = normalize_archive_incremental(
                tmp_state,
                raw_root,
                config_dir,
                tmp_root / "out_inc1",
                source="fhsc",
                previous_batch_dir=full_dir,
                batch_issuers=50,
            )
            inc1_seconds = round(time.perf_counter() - t2, 4)
            inc1_recomputed = inc1_res.recomputed_issuers

    report: dict[str, object] = {
        "full_run_id": full_run_id,
        "full_normalize_seconds": full_seconds,
        "full_recomputed_issuers": full_res.recomputed_issuers,
        "full_rebuild": full_res.full_rebuild,
        "facts_rows": full_res.facts_rows,
        "unmapped_rows": full_res.unmapped_rows,
        "taxonomy_sha256": full_res.taxonomy_sha256,
        "tracemalloc_peak_mb": tm_peak_mb,
        "rss_before_mb": rss_before_mb,
        "rss_after_mb": rss_after_mb,
        "peak_rss_mb": peak_rss_mb,
        "rss_delta_mb": round(max(0.0, rss_after_mb - rss_before_mb), 3),
        "inc0_run_id": inc0_run_id,
        "inc0_normalize_seconds": inc0_seconds,
        "inc0_recomputed_issuers": inc0_res.recomputed_issuers,
        "inc0_full_rebuild": inc0_res.full_rebuild,
        "inc1_dirty_issuer_id": dirty_issuer_id,
        "inc1_normalize_seconds": inc1_seconds,
        "inc1_recomputed_issuers": inc1_recomputed,
        "gates": {
            "full_lt_35s": full_seconds < 35.0,
            "inc0_lt_1_5s": inc0_seconds < 1.5,
            "inc1_lt_1_5s": inc1_seconds is not None and inc1_seconds < 1.5,
            "peak_rss_lt_500mb": peak_rss_mb < 500.0,
            "tracemalloc_lt_500mb": tm_peak_mb < 500.0,
        },
    }
    return report, full_res.facts_path


# ---------------------------------------------------------------------------
# Phase C: Dagster Publish + Checks + Rollback + 3-Run Curated GC
# ---------------------------------------------------------------------------


def _snapshot_curated_state(target_dir: Path) -> dict[str, object]:
    curated_root = target_dir / "curated"
    subdirs_info: dict[str, object] = {}
    quarantine_preserved: list[str] = []
    for subdir in ("financial_facts", "quality", "analytics"):
        sub_path = curated_root / subdir
        if not sub_path.is_dir():
            subdirs_info[subdir] = {
                "run_count": 0,
                "run_ids": [],
                "total_bytes": 0,
            }
            continue
        run_dirs = sorted(
            p
            for p in sub_path.iterdir()
            if p.is_dir()
            and not p.is_symlink()
            and not p.name.startswith((".", "_tmp_gc_"))
        )
        if subdir == "quality":
            for run_dir in run_dirs:
                q_path = run_dir / AUDIT_QUARANTINE_TABLE
                if q_path.is_file():
                    try:
                        if (
                            int(
                                pl.scan_parquet(q_path)
                                .select(pl.len())
                                .collect()
                                .item()
                            )
                            > 0
                        ):
                            quarantine_preserved.append(run_dir.name)
                    except Exception:
                        quarantine_preserved.append(run_dir.name)
        subdirs_info[subdir] = {
            "run_count": len(run_dirs),
            "run_ids": [p.name for p in run_dirs],
            "total_bytes": sum(_dir_size_bytes(p) for p in run_dirs),
        }
    return {
        "subdirs": subdirs_info,
        "issuers_parquet_exists": (curated_root / "issuers.parquet").is_file(),
        "invariant_6_quarantine_nonempty_dirs": quarantine_preserved,
    }


def _extract_materialization_meta(
    result: dg.ExecuteInProcessResult,
    asset_key: dg.AssetKey,
) -> dict[str, object]:
    for ev in result.get_asset_materialization_events():
        mat = ev.step_materialization_data.materialization
        if mat.asset_key == asset_key:
            return {k: v.value for k, v in mat.metadata.items()}
    return {}


def _extract_check_results(result: dg.ExecuteInProcessResult) -> dict[str, object]:
    checks: dict[str, object] = {}
    for ev in result.get_asset_check_evaluations():
        checks[ev.check_name] = {
            "passed": bool(ev.passed),
            "description": ev.description,
            "metadata": {k: v.value for k, v in ev.metadata.items()},
        }
    return checks


def _rollback_parquet_snapshot(
    target_dir: Path,
    state: StateStore,
    target_snapshot_id: str,
) -> None:
    """Atomically roll back ``active_snapshot`` in Parquet mode under ``pipeline.lock`` + RWLock."""
    snapshots_root = (target_dir / "published" / "snapshots").resolve()
    snap_dir = snapshots_root / target_snapshot_id
    if not snap_dir.is_dir():
        raise FileNotFoundError(f"Target Parquet snapshot missing: {snap_dir}")
    with (
        pipeline_write_lock(state.path.with_name("pipeline.lock")),
        snapshot_write_lock(snapshots_root),
    ):
        with state.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT run_id FROM pipeline_runs WHERE published_snapshot_id = ? "
                "ORDER BY snapshot_publish_order DESC LIMIT 1",
                (target_snapshot_id,),
            ).fetchone()
            if row is None:
                raise ContractViolationError(
                    f"No pipeline_runs entry found for snapshot {target_snapshot_id!r}."
                )
            run_id = str(row["run_id"])
            conn.execute(
                """
                INSERT INTO active_snapshot (singleton, snapshot_id, run_id, published_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    snapshot_id = excluded.snapshot_id,
                    run_id = excluded.run_id,
                    published_at = excluded.published_at
                """,
                (target_snapshot_id, run_id, datetime.now(UTC).isoformat()),
            )


def _run_dagster_publish_rollback_and_gc(
    target_dir: Path,
    config_dir: Path,
    target_state: StateStore,
    *,
    as_of: date,
    validation_start: date,
    test_start: date,
    backend: Literal["ducklake", "parquet"],
    ml_enabled: bool,
) -> dict[str, object]:
    """Run Dagster materializations (snap_1, snap_2 -> rollback test, snap_3 -> maintenance/GC)."""
    dagster_home = _ensure_dagster_home(target_dir / "metadata" / "dagster")
    paths_res = FinIntelPathsResource(
        project_root=str(REPO_ROOT),
        config_dir=str(config_dir.resolve()),
        data_root=str(target_dir.resolve()),
        issuers_path=str((target_dir / "curated" / "issuers.parquet").resolve()),
    )
    state_res = StateStoreResource(state_path=str(target_state.path))
    storage_res = StorageResource(
        backend=backend,
        catalog_sqlite_path=str(
            (target_dir / "metadata" / "ducklake_catalog.sqlite").resolve()
        ),
        lakehouse_data_path=str((target_dir / "lakehouse" / "data").resolve()),
        keep_snapshots=2,
        min_snapshot_age_seconds=0,
        curated_keep_runs=2,
    )
    resources: dict[str, object] = {
        "paths": paths_res,
        "state": state_res,
        "storage": storage_res,
    }
    pub_cfg = PublishMartsConfig(
        as_of=as_of.isoformat(),
        source="fhsc",
        ml_enabled=ml_enabled,
        ml_validation_start=validation_start.isoformat() if ml_enabled else None,
        ml_test_start=test_start.isoformat() if ml_enabled else None,
        ml_approved_target=ml_enabled,
    )

    with dg.DagsterInstance.from_config(str(dagster_home)) as instance:
        # 1. Materialize snap_1 + blocking QG1/QG2 asset checks
        t_pub1 = time.perf_counter()
        res_1 = dg.materialize(
            [published_financial_marts, *ALL_ASSET_CHECKS],
            instance=instance,
            resources=resources,
            run_config=dg.RunConfig(ops={"published_financial_marts": pub_cfg}),
        )
        pub1_seconds = round(time.perf_counter() - t_pub1, 4)
        if not res_1.success:
            raise RuntimeError(
                "Dagster materialization #1 (published_financial_marts) failed."
            )
        snap_1_meta = _extract_materialization_meta(
            res_1, published_financial_marts.key
        )
        snap_1_checks = _extract_check_results(res_1)
        snap_1_id = str(snap_1_meta["snapshot_id"])
        snap_1_run_id = str(snap_1_meta["run_id"])

        # 2. Materialize snap_2 (incremental DBSP transform: dirty_issuers == 0)
        t_pub2 = time.perf_counter()
        res_2 = dg.materialize(
            [published_financial_marts, *ALL_ASSET_CHECKS],
            instance=instance,
            resources=resources,
            run_config=dg.RunConfig(ops={"published_financial_marts": pub_cfg}),
        )
        pub2_seconds = round(time.perf_counter() - t_pub2, 4)
        if not res_2.success:
            raise RuntimeError(
                "Dagster materialization #2 (published_financial_marts) failed."
            )
        snap_2_meta = _extract_materialization_meta(
            res_2, published_financial_marts.key
        )
        snap_2_id = str(snap_2_meta["snapshot_id"])

        # 3. Rollback Verification: snap_2 -> snap_1 -> snap_2
        repo = storage_res.build_repository(paths_res)
        before_rb_id = repo.snapshot_id
        if before_rb_id != snap_2_id:
            raise ContractViolationError(
                f"Expected active snapshot {snap_2_id!r} before rollback, got {before_rb_id!r}."
            )
        storage_cfg = storage_res.resolve_storage_config(paths_res)
        rolled_version: int | None = None
        restored_version: int | None = None
        if backend == "ducklake":
            dl_store = DuckLakeStore.from_config(storage_cfg, state_store=target_state)
            rolled_version = dl_store.rollback_snapshot(snap_1_id, target_state)
        else:
            _rollback_parquet_snapshot(target_dir, target_state, snap_1_id)

        after_rb_id = repo.snapshot_id
        snap_1_ratios_rows = repo.read_table(RATIOS_TABLE).height
        if after_rb_id != snap_1_id or snap_1_ratios_rows <= 0:
            raise ContractViolationError(
                f"Rollback verification failed: snapshot_id={after_rb_id!r}, rows={snap_1_ratios_rows}"
            )

        if backend == "ducklake":
            dl_store = DuckLakeStore.from_config(storage_cfg, state_store=target_state)
            restored_version = dl_store.rollback_snapshot(snap_2_id, target_state)
        else:
            _rollback_parquet_snapshot(target_dir, target_state, snap_2_id)

        after_restore_id = repo.snapshot_id
        snap_2_ratios_rows = repo.read_table(RATIOS_TABLE).height
        if after_restore_id != snap_2_id or snap_2_ratios_rows <= 0:
            raise ContractViolationError(
                f"Rollback forward-restore failed: snapshot_id={after_restore_id!r}, rows={snap_2_ratios_rows}"
            )

        rollback_proof: dict[str, object] = {
            "backend": backend,
            "snap_1": snap_1_id,
            "snap_2": snap_2_id,
            "before_rollback_snapshot_id": before_rb_id,
            "after_rollback_snapshot_id": after_rb_id,
            "after_restore_snapshot_id": after_restore_id,
            "rolled_back_ducklake_version": rolled_version,
            "restored_ducklake_version": restored_version,
            "snap_1_ratios_rows": snap_1_ratios_rows,
            "snap_2_ratios_rows": snap_2_ratios_rows,
            "passed": (
                before_rb_id == snap_2_id
                and after_rb_id == snap_1_id
                and after_restore_id == snap_2_id
            ),
        }

        # 4. Run 3rd normalize + publish cycle -> 3 curated batches -> run lakehouse_maintenance_job
        t_pub3 = time.perf_counter()
        res_3 = dg.materialize(
            [normalized_financial_facts, published_financial_marts, *ALL_ASSET_CHECKS],
            instance=instance,
            resources=resources,
            run_config=dg.RunConfig(ops={"published_financial_marts": pub_cfg}),
        )
        pub3_seconds = round(time.perf_counter() - t_pub3, 4)
        if not res_3.success:
            raise RuntimeError(
                "Dagster materialization #3 (normalize + publish) failed."
            )
        snap_3_meta = _extract_materialization_meta(
            res_3, published_financial_marts.key
        )
        snap_3_checks = _extract_check_results(res_3)
        snap_3_id = str(snap_3_meta["snapshot_id"])
        snap_3_run_id = str(snap_3_meta["run_id"])

        curated_before_gc = _snapshot_curated_state(target_dir)
        maint_res = lakehouse_maintenance_job.execute_in_process(
            instance=instance,
            resources=resources,
        )
        if not maint_res.success:
            raise RuntimeError("lakehouse_maintenance_job execution failed.")
        maint_output = cast(
            dict[str, object], maint_res.output_for_node("run_lakehouse_maintenance")
        )
        curated_after_gc = _snapshot_curated_state(target_dir)

    return {
        "snap_1": {
            "snapshot_id": snap_1_id,
            "run_id": snap_1_run_id,
            "elapsed_seconds": pub1_seconds,
            "metadata": snap_1_meta,
            "asset_checks": snap_1_checks,
        },
        "snap_2": {
            "snapshot_id": snap_2_id,
            "elapsed_seconds": pub2_seconds,
            "metadata": snap_2_meta,
        },
        "snap_3": {
            "snapshot_id": snap_3_id,
            "run_id": snap_3_run_id,
            "elapsed_seconds": pub3_seconds,
            "metadata": snap_3_meta,
            "asset_checks": snap_3_checks,
        },
        "rollback_verification": rollback_proof,
        "maintenance_and_curated_gc": {
            "curated_before_gc": curated_before_gc,
            "curated_after_gc": curated_after_gc,
            "maintenance_output": maint_output,
        },
    }


# ---------------------------------------------------------------------------
# Gate 2: Layer 3 EXPLAIN ANALYZE Row-Group Skipping
# ---------------------------------------------------------------------------


def _benchmark_gate2_data_skipping(
    target_dir: Path,
    *,
    backend: Literal["ducklake", "parquet"],
    maintenance_gc_report: Mapping[str, object],
) -> dict[str, object]:
    """Measure EXPLAIN ANALYZE and Parquet zone-map row-group skipping on facts and ratios."""
    repo = ParquetRepository(data_root=target_dir, backend=backend)
    meta = repo.get_snapshot_metadata()
    issuers_df = repo.read_table("issuers.parquet", columns=["issuer_id", "ticker"])
    sorted_issuers = sorted(
        issuers_df["issuer_id"].drop_nulls().cast(pl.String).unique().to_list()
    )
    if not sorted_issuers:
        raise ContractViolationError("No issuers found in active snapshot for Gate 2.")

    # Prefer VN_FPT as the primary single-issuer probe when present.
    fpt_matches = issuers_df.filter(pl.col("ticker") == "FPT")["issuer_id"].to_list()
    primary_issuer = str(fpt_matches[0]) if fpt_matches else sorted_issuers[0]
    if _SAFE_ISSUER_ID_RE.fullmatch(primary_issuer) is None:
        raise ContractViolationError(f"Unsafe probe issuer_id: {primary_issuer!r}")

    # Up to 10 evenly spaced issuers across the sorted issuer spectrum.
    if len(sorted_issuers) <= 10:
        sample_issuers = list(sorted_issuers)
    else:
        idxs = [int(round(i * (len(sorted_issuers) - 1) / 9)) for i in range(10)]
        sample_issuers = list(dict.fromkeys(sorted_issuers[i] for i in idxs))

    con = duckdb.connect(":memory:")
    table_reports: dict[str, object] = {}
    try:
        storage_backend = str(meta.get("storage_backend", backend))
        dl_version = meta.get("ducklake_version")
        alias = "finintel_lake"
        if storage_backend == "ducklake":
            ensure_ducklake_attached(
                con,
                catalog_path=target_dir / "metadata" / "ducklake_catalog.sqlite",
                data_path=target_dir / "lakehouse" / "data",
                catalog_alias=alias,
                read_only=True,
            )

        for table_name, configured_rg in (
            ("financial_facts", 25_000),
            ("financial_ratios", 2_000),
        ):
            active_files: list[str]
            if storage_backend == "ducklake":
                assert isinstance(dl_version, int)
                file_rows = con.execute(
                    f"SELECT data_file FROM ducklake_list_files('{alias}', '{table_name}', "
                    f"snapshot_version => {dl_version})"
                ).fetchall()
                active_files = [str(r[0]) for r in file_rows]
                explain_sql = (
                    f'EXPLAIN ANALYZE SELECT * FROM "{alias}"."{table_name}" '
                    f"AT (VERSION => {dl_version}) WHERE \"issuer_id\" = '{primary_issuer}'"
                )
            else:
                snap_id = str(meta.get("snapshot_id") or "")
                pq_path = (
                    target_dir
                    / "published"
                    / "snapshots"
                    / snap_id
                    / f"{table_name}.parquet"
                ).resolve()
                active_files = [pq_path.as_posix()]
                explain_sql = (
                    f"EXPLAIN ANALYZE SELECT * FROM read_parquet('{pq_path.as_posix()}') "
                    f"WHERE \"issuer_id\" = '{primary_issuer}'"
                )

            ea_rows = con.execute(explain_sql).fetchall()
            explain_analyze_text = "\n".join(str(r[1]) for r in ea_rows)

            rg_stats: list[tuple[str, int, int, str | None, str | None]] = []
            for fpath in active_files:
                rows = con.execute(
                    "SELECT row_group_id, row_group_num_rows, stats_min_value, stats_max_value "
                    "FROM parquet_metadata(?) WHERE path_in_schema = 'issuer_id' "
                    "ORDER BY row_group_id",
                    [fpath],
                ).fetchall()
                for rg_id, rg_rows, smin, smax in rows:
                    rg_stats.append(
                        (
                            Path(fpath).name,
                            int(rg_id),
                            int(rg_rows),
                            str(smin) if smin is not None else None,
                            str(smax) if smax is not None else None,
                        )
                    )

            total_rgs = len(rg_stats)
            total_rows = sum(item[2] for item in rg_stats)

            def _skip_for(target_iid: str) -> tuple[int, int, float]:
                if total_rgs == 0:
                    return 0, 0, 0.0
                matched = sum(
                    1
                    for _, _, _, smin, smax in rg_stats
                    if smin is None or smax is None or (smin <= target_iid <= smax)
                )
                skipped = total_rgs - matched
                return matched, skipped, round(100.0 * skipped / total_rgs, 4)

            p_matched, p_skipped, p_skip_pct = _skip_for(primary_issuer)
            per_issuer_samples = []
            for iid in sample_issuers:
                m_i, s_i, pct_i = _skip_for(iid)
                per_issuer_samples.append(
                    {
                        "issuer_id": iid,
                        "matched_row_groups": m_i,
                        "skipped_row_groups": s_i,
                        "total_row_groups": total_rgs,
                        "skip_pct": pct_i,
                    }
                )
            mean_10_skip_pct = (
                round(
                    float(np.mean([float(x["skip_pct"]) for x in per_issuer_samples])),
                    4,
                )
                if per_issuer_samples
                else 0.0
            )
            rg_sizes = [item[2] for item in rg_stats]
            table_reports[table_name] = {
                "configured_row_group_size": configured_rg,
                "active_file_count": len(active_files),
                "total_rows": total_rows,
                "total_row_groups": total_rgs,
                "observed_row_group_rows_min": min(rg_sizes) if rg_sizes else 0,
                "observed_row_group_rows_max": max(rg_sizes) if rg_sizes else 0,
                "primary_probe_issuer_id": primary_issuer,
                "primary_matched_row_groups": p_matched,
                "primary_skipped_row_groups": p_skipped,
                "primary_skip_pct": p_skip_pct,
                "passes_85pct_gate": p_skip_pct > 85.0,
                "theoretical_max_skip_pct": (
                    round(100.0 * (total_rgs - 1) / total_rgs, 4)
                    if total_rgs > 0
                    else 0.0
                ),
                "mean_10_issuer_skip_pct": mean_10_skip_pct,
                "sample_10_issuers": per_issuer_samples,
                "explain_analyze": explain_analyze_text,
            }
    finally:
        con.close()

    return {
        "snapshot_metadata": meta,
        "tables": table_reports,
        "curated_gc_and_maintenance": dict(maintenance_gc_report),
    }


# ---------------------------------------------------------------------------
# Gate 3: 2026 TT99 QUARTER Non-Null Coverage
# ---------------------------------------------------------------------------


def _benchmark_gate3_tt99_2026_coverage(
    target_dir: Path,
    *,
    backend: Literal["ducklake", "parquet"],
) -> dict[str, object]:
    """Compute exact 2026 TT99 QUARTER coverage (numerator/denominator) for all 8 indicators."""
    repo = ParquetRepository(data_root=target_dir, backend=backend)
    with repo.snapshot_session() as pinned:
        ratios = pinned.read_table(RATIOS_TABLE)
        ml_scores = pinned.read_table(ML_SCORES_TABLE)

    q2026 = ratios.filter(
        (pl.col("period_type") == "QUARTER") & (pl.col("fiscal_year") == 2026)
    )
    ml_cols = [
        "issuer_id",
        "statement_scope",
        "period_type",
        "period_end_date",
        "altman_z_double_prime",
        "piotroski_f_score",
        "calibrated_distress_prob",
    ]
    available_ml_cols = [c for c in ml_cols if c in ml_scores.columns]
    joined = q2026.join(
        ml_scores.select(available_ml_cols),
        on=["issuer_id", "statement_scope", "period_type", "period_end_date"],
        how="left",
        validate="1:1",
    )

    def _summarize_slice(frame: pl.DataFrame) -> dict[str, object]:
        denom = frame.height
        metrics_out: dict[str, object] = {}
        if denom == 0:
            for col in _GATE3_INDICATORS:
                metrics_out[col] = {
                    "non_null": 0,
                    "denominator": 0,
                    "coverage_pct": 0.0,
                }
            return {"row_count": 0, "metrics": metrics_out}

        agg_exprs = [
            (
                pl.col(col).is_not_null().sum().cast(pl.Int64).alias(f"_nn_{col}")
                if col in frame.columns
                else pl.lit(0, dtype=pl.Int64).alias(f"_nn_{col}")
            )
            for col in _GATE3_INDICATORS
        ]
        if "dupont_valid_flag" in frame.columns:
            agg_exprs.append(
                pl.col("dupont_valid_flag")
                .eq(True)
                .fill_null(False)
                .sum()
                .cast(pl.Int64)
                .alias("_dupont_true")
            )
        else:
            agg_exprs.append(pl.lit(0, dtype=pl.Int64).alias("_dupont_true"))

        counts = frame.select(agg_exprs).row(0, named=True)
        for col in _GATE3_INDICATORS:
            nn = int(counts[f"_nn_{col}"])
            entry: dict[str, object] = {
                "non_null": nn,
                "denominator": denom,
                "coverage_pct": round(100.0 * nn / denom, 4),
                "passes_80pct_gate": (nn / denom) >= 0.80,
            }
            if col == "dupont_valid_flag":
                true_cnt = int(counts["_dupont_true"])
                entry["true_count"] = true_cnt
                entry["true_pct"] = round(100.0 * true_cnt / denom, 4)
            metrics_out[col] = entry
        return {"row_count": denom, "metrics": metrics_out}

    sufficient = (
        joined.filter(pl.col("net_revenue_ttm").is_not_null())
        if "net_revenue_ttm" in joined.columns
        else joined
    )

    taxonomy_counts = (
        joined.group_by("taxonomy_version")
        .agg(pl.len().alias("rows"))
        .sort("taxonomy_version")
        .to_dicts()
        if not joined.is_empty()
        else []
    )
    per_quarter: dict[str, object] = {}
    for quarter in (1, 2, 3, 4):
        q_slice = joined.filter(pl.col("fiscal_quarter") == quarter)
        if q_slice.height > 0:
            q_suf = (
                q_slice.filter(pl.col("net_revenue_ttm").is_not_null())
                if "net_revenue_ttm" in q_slice.columns
                else q_slice
            )
            per_quarter[f"2026-Q{quarter}"] = {
                "all_rows": _summarize_slice(q_slice),
                "sufficient_ttm_input_rows": _summarize_slice(q_suf),
            }

    return {
        "total_2026_quarter_rows": joined.height,
        "sufficient_ttm_input_rows": sufficient.height,
        "taxonomy_breakdown": taxonomy_counts,
        "overall_all_rows": _summarize_slice(joined),
        "overall_sufficient_input_rows": _summarize_slice(sufficient),
        "by_quarter": per_quarter,
    }


# ---------------------------------------------------------------------------
# Gate 5: Full ML Validation + OOF + Test + Mandatory Ablations + Top 15 Test
# ---------------------------------------------------------------------------


def _summarize_ml_arm(
    eval_report: Mapping[str, object],
    run_manifest: Mapping[str, object],
) -> dict[str, object]:
    platt_slope = eval_report.get("platt_slope")
    slope_float = float(platt_slope) if isinstance(platt_slope, (int, float)) else None
    return {
        "peer_standardization_mode": eval_report.get("peer_standardization_mode"),
        "feature_set": eval_report.get("feature_set"),
        "require_exact_filing": run_manifest.get("require_exact_filing"),
        "platt_slope": slope_float,
        "platt_intercept": eval_report.get("platt_intercept"),
        "platt_slope_gate_passed": (
            slope_float is not None and 0.0 < slope_float <= 10.0
        ),
        "f2_thresholds": eval_report.get("f2_thresholds"),
        "split_counts": run_manifest.get("split_counts"),
        "train_summary": eval_report.get("train_summary"),
        "validation": eval_report.get("validation"),
        "test": eval_report.get("test"),
    }


def _benchmark_gate5_ml_and_ablations(
    target_dir: Path,
    config_dir: Path,
    *,
    backend: Literal["ducklake", "parquet"],
    as_of: date,
    validation_start: date,
    test_start: date,
    primary_run_id: str,
    pre_enrichment_state_path: Path | None,
) -> dict[str, object]:
    """Extract primary ML metrics, K-Means checks, Top 15 Test predictions, and both ablations."""
    repo = ParquetRepository(data_root=target_dir, backend=backend)
    with repo.snapshot_session() as pinned:
        clean_facts = pinned.read_table("financial_facts.parquet")
        issuers = pinned.read_table("issuers.parquet")
        ml_scores = pinned.read_table(ML_SCORES_TABLE)

    # Load primary evaluation_report & ml_run_manifest persisted by published_financial_marts
    ml_meta_dir = target_dir / "metadata" / "ml_runs" / primary_run_id
    eval_path = ml_meta_dir / "evaluation_report.json"
    manifest_path = ml_meta_dir / "ml_run_manifest.json"
    if eval_path.is_file() and manifest_path.is_file():
        primary_eval = cast(
            dict[str, object], json.loads(eval_path.read_text(encoding="utf-8"))
        )
        primary_manifest = cast(
            dict[str, object], json.loads(manifest_path.read_text(encoding="utf-8"))
        )
    else:
        primary_art = build_ml_artifacts(
            clean_facts,
            issuers,
            as_of=as_of,
            validation_start=validation_start,
            test_start=test_start,
            approved_target=True,
            require_exact_filing=True,
            feature_set="sector_adjusted",
        )
        primary_eval = primary_art.evaluation_report
        primary_manifest = primary_art.run_manifest

    primary_summary = _summarize_ml_arm(primary_eval, primary_manifest)

    # K-Means gate check (0 duplicate names, max share <= 85%)
    clustered = ml_scores.filter(pl.col("cluster_id").is_not_null())
    total_clustered = clustered.height
    cluster_rows = (
        clustered.group_by(["cluster_id", "cluster_archetype_name"])
        .agg(pl.len().alias("count"))
        .sort("count", descending=True)
        .with_columns(
            (pl.col("count") * 100.0 / max(1, total_clustered))
            .round(4)
            .alias("share_pct")
        )
        .to_dicts()
        if total_clustered > 0
        else []
    )
    unique_names = {str(r["cluster_archetype_name"]) for r in cluster_rows}
    duplicate_name_count = len(cluster_rows) - len(unique_names)
    max_cluster_share_pct = (
        max(float(r["share_pct"]) for r in cluster_rows) if cluster_rows else 0.0
    )
    kmeans_gate = {
        "total_clustered_rows": total_clustered,
        "cluster_count": len(cluster_rows),
        "unique_archetype_names": len(unique_names),
        "duplicate_name_count": duplicate_name_count,
        "max_cluster_share_pct": max_cluster_share_pct,
        "passes_zero_duplicates": duplicate_name_count == 0,
        "passes_max_share_le_85pct": max_cluster_share_pct <= 85.0,
        "clusters": cluster_rows,
    }

    # Top 15 Highest-Risk Test Predictions + Blue-Chip Spotlight
    splits = build_pit_ml_splits(
        clean_facts,
        issuers,
        validation_start=validation_start,
        test_start=test_start,
        as_of=as_of,
        require_exact_filing=True,
        statement_scope="CONSOLIDATED",
        taxonomy_version="VAS_TT200_2014",
    )
    test_fold = splits["test"]
    if "already_distressed_at_t" in test_fold.columns:
        test_eval_fold = test_fold.filter(
            (~pl.col("already_distressed_at_t").fill_null(True))
            & pl.col("distress_target").is_not_null()
        )
    else:
        test_eval_fold = test_fold.filter(pl.col("distress_target").is_not_null())

    test_joined = (
        test_eval_fold.select(
            "issuer_id",
            "statement_scope",
            "period_type",
            "period_end_date",
            "distress_target",
        )
        .join(
            ml_scores.select(
                "issuer_id",
                "statement_scope",
                "period_type",
                "period_end_date",
                "calibrated_distress_prob",
                "distress_risk_score",
                "altman_z_double_prime",
                "piotroski_f_score",
                "shap_top_drivers_json",
            ),
            on=["issuer_id", "statement_scope", "period_type", "period_end_date"],
            how="inner",
        )
        .join(
            issuers.select(
                "issuer_id",
                "ticker",
                "company_name_vn",
                "icb_code_level2",
                "icb_name_level2",
            ),
            on="issuer_id",
            how="left",
        )
        .filter(pl.col("calibrated_distress_prob").is_not_null())
    )

    top15_raw = (
        test_joined.sort(
            ["calibrated_distress_prob", "distress_risk_score", "ticker"],
            descending=[True, True, False],
            nulls_last=True,
        )
        .head(15)
        .to_dicts()
    )
    top15_predictions: list[dict[str, object]] = []
    for row in top15_raw:
        shap_parsed = parse_shap_top_drivers(
            str(row["shap_top_drivers_json"])
            if row.get("shap_top_drivers_json") is not None
            else None
        )
        top_driver: dict[str, object] | None = None
        if shap_parsed is not None and shap_parsed.top_risk_increasing:
            first_drv = shap_parsed.top_risk_increasing[0]
            top_driver = {
                "feature": first_drv.feature,
                "display_name_vn": first_drv.display_name_vn,
                "value": first_drv.value,
                "shap_delta": round(first_drv.shap_delta, 4),
            }
        top15_predictions.append(
            {
                "ticker": row["ticker"],
                "issuer_id": row["issuer_id"],
                "icb_code_level2": row["icb_code_level2"],
                "icb_name_level2": row["icb_name_level2"],
                "period_end_date": row["period_end_date"],
                "distress_target": int(row["distress_target"]),
                "calibrated_distress_prob": round(
                    float(row["calibrated_distress_prob"]), 6
                ),
                "distress_risk_score": round(float(row["distress_risk_score"]), 4),
                "altman_z_double_prime": (
                    round(float(row["altman_z_double_prime"]), 4)
                    if row.get("altman_z_double_prime") is not None
                    else None
                ),
                "piotroski_f_score": (
                    int(row["piotroski_f_score"])
                    if row.get("piotroski_f_score") is not None
                    else None
                ),
                "top_shap_driver": top_driver,
            }
        )

    bluechip_df = (
        test_joined.filter(pl.col("ticker").is_in(list(_BLUECHIP_SPOTLIGHT)))
        .sort(["ticker", "period_end_date"], descending=[False, True])
        .group_by("ticker", maintain_order=True)
        .agg(
            pl.col("period_end_date").first().alias("latest_test_period"),
            pl.col("calibrated_distress_prob").first().alias("latest_prob"),
            pl.col("calibrated_distress_prob").max().alias("max_test_prob"),
            pl.col("altman_z_double_prime").first().alias("latest_altman_z"),
            pl.col("piotroski_f_score").first().alias("latest_f_score"),
            pl.col("distress_target").max().alias("ever_distressed_in_test"),
        )
        .sort("ticker")
    )
    bluechip_spotlight = bluechip_df.to_dicts()

    # Ablation (a): STATUTORY_LAG vs EXACT_FILING
    ablation_a: dict[str, object] = {
        "exact_filing_v2_primary": primary_summary,
    }
    try:
        arm_enriched_relaxed = build_ml_artifacts(
            clean_facts,
            issuers,
            as_of=as_of,
            validation_start=validation_start,
            test_start=test_start,
            approved_target=True,
            require_exact_filing=False,
            feature_set="sector_adjusted",
        )
        ablation_a["statutory_lag_enriched_relaxed"] = _summarize_ml_arm(
            arm_enriched_relaxed.evaluation_report, arm_enriched_relaxed.run_manifest
        )
    except Exception as exc:
        ablation_a["statutory_lag_enriched_relaxed"] = {
            "error": f"{type(exc).__name__}: {exc}"
        }

    if pre_enrichment_state_path is not None and pre_enrichment_state_path.is_file():
        try:
            with tempfile.TemporaryDirectory(
                prefix="finintel_b6_ablation_a_"
            ) as tmp_str:
                tmp_root = Path(tmp_str)
                pre_state = StateStore(pre_enrichment_state_path)
                pre_norm = normalize_archive_incremental(
                    pre_state,
                    target_dir / "raw",
                    config_dir,
                    tmp_root / "pre_norm",
                    source="fhsc",
                    previous_batch_dir=None,
                    batch_issuers=50,
                )
                pre_facts = pl.read_parquet(pre_norm.facts_path)
                pre_qg1 = evaluate_quality_gate(
                    pre_facts, issuers=issuers, run_id="b6_ablation_a_unenriched"
                )
                arm_unenriched = build_ml_artifacts(
                    pre_qg1.clean_facts,
                    issuers,
                    as_of=as_of,
                    validation_start=validation_start,
                    test_start=test_start,
                    approved_target=True,
                    require_exact_filing=False,
                    feature_set="sector_adjusted",
                )
                ablation_a["statutory_lag_unenriched"] = _summarize_ml_arm(
                    arm_unenriched.evaluation_report, arm_unenriched.run_manifest
                )
        except Exception as exc:
            ablation_a["statutory_lag_unenriched"] = {
                "error": f"{type(exc).__name__}: {exc}"
            }

    # Ablation (b): BASE_FEATURES vs SECTOR_ADJUSTED_FEATURES (+ PIT_REGIME_SYMMETRIC sensitivity)
    snap_dates = (
        issuers["industry_snapshot_date"]
        .drop_nulls()
        .cast(pl.String)
        .unique()
        .sort()
        .to_list()
        if "industry_snapshot_date" in issuers.columns
        else []
    )
    ablation_b: dict[str, object] = {
        "issuers_industry_snapshot_dates_observed": snap_dates,
        "sector_adjusted_as_is": primary_summary,
    }
    try:
        arm_base = build_ml_artifacts(
            clean_facts,
            issuers,
            as_of=as_of,
            validation_start=validation_start,
            test_start=test_start,
            approved_target=True,
            require_exact_filing=True,
            feature_set="base",
        )
        ablation_b["base_features"] = _summarize_ml_arm(
            arm_base.evaluation_report, arm_base.run_manifest
        )
    except Exception as exc:
        ablation_b["base_features"] = {"error": f"{type(exc).__name__}: {exc}"}

    try:
        issuers_pit_today = issuers.with_columns(
            pl.lit(as_of).cast(pl.Date).alias("industry_snapshot_date")
        )
        arm_regime_sym = build_ml_artifacts(
            clean_facts,
            issuers_pit_today,
            as_of=as_of,
            validation_start=validation_start,
            test_start=test_start,
            approved_target=True,
            require_exact_filing=True,
            feature_set="sector_adjusted",
        )
        ablation_b["sector_adjusted_pit_regime_symmetric_sensitivity"] = (
            _summarize_ml_arm(
                arm_regime_sym.evaluation_report, arm_regime_sym.run_manifest
            )
        )
    except Exception as exc:
        ablation_b["sector_adjusted_pit_regime_symmetric_sensitivity"] = {
            "error": f"{type(exc).__name__}: {exc}"
        }

    return {
        "primary_v2_run": primary_summary,
        "kmeans_gate": kmeans_gate,
        "top_15_test_predictions": top15_predictions,
        "bluechip_spotlight_test": bluechip_spotlight,
        "ablation_a_statutory_lag_vs_exact_filing": ablation_a,
        "ablation_b_base_vs_sector_adjusted": ablation_b,
    }


# ---------------------------------------------------------------------------
# Gate 6: Real Uvicorn 20-User Load Test + Live Concurrent Snapshot Publish
# ---------------------------------------------------------------------------


def _benchmark_gate6_uvicorn_serving(
    target_dir: Path,
    config_dir: Path,
    target_state: StateStore,
    *,
    backend: Literal["ducklake", "parquet"],
    as_of: date,
    validation_start: date,
    test_start: date,
    ml_enabled: bool,
    uvicorn_port: int = 0,
    concurrent_users: int = 20,
    requests_per_user: int = 20,
) -> dict[str, object]:
    """Run real uvicorn subprocess load test (20 users) + live publish ETag refresh test."""
    port = uvicorn_port if uvicorn_port > 0 else _find_free_local_port()
    base_url = f"http://127.0.0.1:{port}"

    # Select valid tickers that have QUARTERLY consolidated ratios so load mix hits real data.
    repo = ParquetRepository(data_root=target_dir, backend=backend)
    with repo.snapshot_session() as pinned:
        ratios_q = pinned.read_table(
            RATIOS_TABLE,
            columns=["ticker", "statement_scope", "period_type"],
            filters={"statement_scope": "CONSOLIDATED", "period_type": "QUARTER"},
        )
    valid_tickers = sorted(
        ratios_q["ticker"].drop_nulls().cast(pl.String).unique().to_list()
    )
    if not valid_tickers:
        raise ContractViolationError(
            "No CONSOLIDATED QUARTER tickers available for Gate 6."
        )
    probe_tickers = valid_tickers[: min(20, len(valid_tickers))]

    env = dict(os.environ)
    existing_py = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{SRC_DIR}{os.pathsep}{existing_py}" if existing_py else str(SRC_DIR)
    )
    env["FININTEL_DATA_ROOT"] = str(target_dir.resolve())
    env["PYTHONUTF8"] = "1"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "finintel.serving.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # Wait for real uvicorn readiness
        ready_start = time.perf_counter()
        ready = False
        with httpx.Client(base_url=base_url, timeout=10.0) as health_client:
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    out, err = proc.communicate(timeout=5)
                    raise RuntimeError(
                        f"uvicorn exited prematurely (code={proc.returncode}): {err or out}"
                    )
                try:
                    resp = health_client.get("/api/v1/health")
                    if resp.status_code == 200 and resp.json().get("status") == "ok":
                        ready = True
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.15)
        if not ready:
            raise TimeoutError(
                f"uvicorn failed to become healthy on {base_url} within 30s."
            )
        startup_seconds = round(time.perf_counter() - ready_start, 4)

        # Warmup 1 request per route group
        t_sample = probe_tickers[0]
        with httpx.Client(base_url=base_url, timeout=30.0) as warmup:
            warmup.get(f"/api/v1/companies/{t_sample}/profile")
            warmup.get(
                f"/api/v1/companies/{t_sample}/benchmarks",
                params={"metric_name": "roe_total"},
            )
            warmup.get(
                f"/api/v1/companies/{t_sample}/lineage",
                params={"ratio_name": "roe_total"},
            )
            warmup.get("/api/v1/screener", params={"limit": 50, "offset": 0})
            warmup.post(
                "/api/v1/stress-test",
                json={
                    "scenario_name": "Warmup +300bps",
                    "interest_rate_shock_bps": 300.0,
                    "top_n": 20,
                },
            )

        # Exact 20-request weighted mix per user:
        # 40% profile (8), 20% benchmarks (4), 15% lineage (3), 15% screener (3), 10% stress-test (2)
        base_pattern: tuple[str, ...] = (
            "profile",
            "profile",
            "benchmarks",
            "profile",
            "lineage",
            "screener",
            "profile",
            "benchmarks",
            "profile",
            "stress_test",
            "profile",
            "benchmarks",
            "lineage",
            "screener",
            "profile",
            "benchmarks",
            "lineage",
            "screener",
            "profile",
            "stress_test",
        )

        def _user_worker(user_idx: int) -> list[tuple[str, float, int, str | None]]:
            results: list[tuple[str, float, int, str | None]] = []
            with httpx.Client(base_url=base_url, timeout=30.0) as client:
                for step in range(requests_per_user):
                    route_group = base_pattern[(step + user_idx) % len(base_pattern)]
                    ticker = probe_tickers[(user_idx + step) % len(probe_tickers)]
                    t0 = time.perf_counter()
                    try:
                        if route_group == "profile":
                            resp = client.get(f"/api/v1/companies/{ticker}/profile")
                        elif route_group == "benchmarks":
                            resp = client.get(
                                f"/api/v1/companies/{ticker}/benchmarks",
                                params={"metric_name": "roe_total"},
                            )
                        elif route_group == "lineage":
                            resp = client.get(
                                f"/api/v1/companies/{ticker}/lineage",
                                params={"ratio_name": "roe_total"},
                            )
                        elif route_group == "screener":
                            resp = client.get(
                                "/api/v1/screener",
                                params={"limit": 50, "offset": 0},
                            )
                        else:
                            resp = client.post(
                                "/api/v1/stress-test",
                                json={
                                    "scenario_name": "LoadTest +300bps",
                                    "interest_rate_shock_bps": 300.0,
                                    "top_n": 20,
                                },
                            )
                        elapsed_ms = (time.perf_counter() - t0) * 1000.0
                        err = (
                            None
                            if resp.status_code == 200
                            else f"HTTP {resp.status_code}"
                        )
                        results.append((route_group, elapsed_ms, resp.status_code, err))
                    except Exception as exc:
                        elapsed_ms = (time.perf_counter() - t0) * 1000.0
                        results.append(
                            (route_group, elapsed_ms, 0, f"{type(exc).__name__}: {exc}")
                        )
            return results

        load_start = time.perf_counter()
        all_samples: list[tuple[str, float, int, str | None]] = []
        with ThreadPoolExecutor(max_workers=concurrent_users) as pool:
            futures = [
                pool.submit(_user_worker, idx) for idx in range(concurrent_users)
            ]
            for fut in futures:
                all_samples.extend(fut.result())
        load_wall_s = round(time.perf_counter() - load_start, 4)

        by_route: dict[str, list[float]] = {
            "profile": [],
            "benchmarks": [],
            "lineage": [],
            "screener": [],
            "stress_test": [],
        }
        route_errors: dict[str, list[str]] = {k: [] for k in by_route}
        all_latencies: list[float] = []
        total_errors = 0
        for rgroup, lat_ms, status_code, err_msg in all_samples:
            by_route[rgroup].append(lat_ms)
            all_latencies.append(lat_ms)
            if status_code != 200 or err_msg is not None:
                total_errors += 1
                route_errors[rgroup].append(err_msg or f"HTTP {status_code}")

        route_metrics: dict[str, object] = {}
        for rgroup, lats in by_route.items():
            stats = _percentiles_ms(lats)
            errs = route_errors[rgroup]
            route_metrics[rgroup] = {
                **stats,
                "error_count": len(errs),
                "error_rate_pct": round(100.0 * len(errs) / max(1, len(lats)), 4),
                "sample_errors": errs[:5],
            }

        overall_stats = _percentiles_ms(all_latencies)
        total_requests = len(all_samples)
        overall_error_rate = round(100.0 * total_errors / max(1, total_requests), 4)

        # Live Concurrent Snapshot Publish + READ_ONLY ETag Refresh Test
        with httpx.Client(base_url=base_url, timeout=15.0) as check_client:
            ov_before = check_client.get("/api/v1/snapshot/overview")
            ov_before.raise_for_status()
            etag_before = ov_before.headers.get("etag", "")
            snap_before = str(ov_before.json().get("snapshot_id") or "")
            cond_304_before = check_client.get(
                "/api/v1/snapshot/overview",
                headers={"If-None-Match": etag_before},
            ).status_code

        stop_polling = threading.Event()
        poll_lock = threading.Lock()
        poll_counts = {"requests": 0, "errors": 0, "read_skew": 0}
        poll_etags_seen: list[str] = []
        poll_error_samples: list[str] = []

        def _live_reader(reader_idx: int) -> None:
            ticker = probe_tickers[reader_idx % len(probe_tickers)]
            endpoints = (
                "/api/v1/snapshot/overview",
                f"/api/v1/companies/{ticker}/profile",
                "/api/v1/screener?limit=20&offset=0",
            )
            with httpx.Client(base_url=base_url, timeout=30.0) as client:
                step = 0
                while not stop_polling.is_set():
                    url = endpoints[step % len(endpoints)]
                    step += 1
                    try:
                        resp = client.get(url)
                        etag = resp.headers.get("etag", "")
                        if resp.status_code != 200:
                            with poll_lock:
                                poll_counts["requests"] += 1
                                poll_counts["errors"] += 1
                                if len(poll_error_samples) < 10:
                                    poll_error_samples.append(
                                        f"{url}:HTTP {resp.status_code}"
                                    )
                        else:
                            body_snap = str(resp.json().get("snapshot_id") or "")
                            expected_etag = f'"{body_snap}"'
                            skew = etag != expected_etag
                            with poll_lock:
                                poll_counts["requests"] += 1
                                if skew:
                                    poll_counts["read_skew"] += 1
                                if etag and etag not in poll_etags_seen:
                                    poll_etags_seen.append(etag)
                    except Exception as exc:
                        with poll_lock:
                            poll_counts["requests"] += 1
                            poll_counts["errors"] += 1
                            if len(poll_error_samples) < 10:
                                poll_error_samples.append(
                                    f"{url}:{type(exc).__name__}: {exc}"
                                )
                    time.sleep(0.02)

        dagster_home = _ensure_dagster_home(target_dir / "metadata" / "dagster")
        paths_res = FinIntelPathsResource(
            project_root=str(REPO_ROOT),
            config_dir=str(config_dir.resolve()),
            data_root=str(target_dir.resolve()),
            issuers_path=str((target_dir / "curated" / "issuers.parquet").resolve()),
        )
        state_res = StateStoreResource(state_path=str(target_state.path))
        storage_res = StorageResource(
            backend=backend,
            catalog_sqlite_path=str(
                (target_dir / "metadata" / "ducklake_catalog.sqlite").resolve()
            ),
            lakehouse_data_path=str((target_dir / "lakehouse" / "data").resolve()),
            keep_snapshots=2,
            min_snapshot_age_seconds=0,
            curated_keep_runs=2,
        )
        pub_cfg = PublishMartsConfig(
            as_of=as_of.isoformat(),
            source="fhsc",
            ml_enabled=ml_enabled,
            ml_validation_start=validation_start.isoformat() if ml_enabled else None,
            ml_test_start=test_start.isoformat() if ml_enabled else None,
            ml_approved_target=ml_enabled,
        )

        with ThreadPoolExecutor(max_workers=8) as reader_pool:
            reader_futs = [reader_pool.submit(_live_reader, i) for i in range(8)]
            try:
                time.sleep(0.25)
                with dg.DagsterInstance.from_config(str(dagster_home)) as instance:
                    live_res = dg.materialize(
                        [published_financial_marts],
                        instance=instance,
                        resources={
                            "paths": paths_res,
                            "state": state_res,
                            "storage": storage_res,
                        },
                        run_config=dg.RunConfig(
                            ops={"published_financial_marts": pub_cfg}
                        ),
                    )
                    if not live_res.success:
                        raise RuntimeError("Live concurrent snapshot publish failed.")
                time.sleep(0.5)
            finally:
                stop_polling.set()
                for rf in reader_futs:
                    rf.result()

        with httpx.Client(base_url=base_url, timeout=15.0) as check_client:
            ov_after = check_client.get("/api/v1/snapshot/overview")
            ov_after.raise_for_status()
            etag_after = ov_after.headers.get("etag", "")
            snap_after = str(ov_after.json().get("snapshot_id") or "")
            cond_304_after = check_client.get(
                "/api/v1/snapshot/overview",
                headers={"If-None-Match": etag_after},
            ).status_code
            stale_inm_resp = check_client.get(
                "/api/v1/snapshot/overview",
                headers={"If-None-Match": etag_before},
            )
            stale_inm_status = stale_inm_resp.status_code
            stale_inm_etag = stale_inm_resp.headers.get("etag", "")

        overall_p95 = overall_stats.get("p95_ms")
        p95_pass = isinstance(overall_p95, (int, float)) and float(overall_p95) < 100.0
        live_refresh_pass = (
            snap_after != snap_before
            and etag_after == f'"{snap_after}"'
            and cond_304_before == 304
            and cond_304_after == 304
            and stale_inm_status == 200
            and stale_inm_etag == etag_after
            and poll_counts["errors"] == 0
            and poll_counts["read_skew"] == 0
        )

        return {
            "uvicorn_url": base_url,
            "startup_seconds": startup_seconds,
            "concurrent_users": concurrent_users,
            "requests_per_user": requests_per_user,
            "total_requests": total_requests,
            "load_wall_seconds": load_wall_s,
            "throughput_rps": (
                round(total_requests / load_wall_s, 2) if load_wall_s > 0 else 0.0
            ),
            "overall": {
                **overall_stats,
                "error_count": total_errors,
                "error_rate_pct": overall_error_rate,
            },
            "by_route": route_metrics,
            "live_publish_refresh": {
                "snapshot_id_before": snap_before,
                "etag_before": etag_before,
                "conditional_304_before_status": cond_304_before,
                "snapshot_id_after": snap_after,
                "etag_after": etag_after,
                "conditional_304_after_status": cond_304_after,
                "stale_if_none_match_status": stale_inm_status,
                "stale_if_none_match_etag": stale_inm_etag,
                "concurrent_reader_threads": 8,
                "concurrent_requests_served": poll_counts["requests"],
                "concurrent_errors": poll_counts["errors"],
                "read_skew_count": poll_counts["read_skew"],
                "etags_observed_during_publish": poll_etags_seen,
                "sample_errors": poll_error_samples,
                "passed": live_refresh_pass,
            },
            "gates": {
                "overall_p95_lt_100ms": p95_pass,
                "zero_error_rate": total_errors == 0,
                "live_refresh_zero_skew_zero_errors": live_refresh_pass,
            },
        }
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# Gate 7: 3 IMF Stress Scenarios + RF_08 / RF_09 Distribution by ICB2
# ---------------------------------------------------------------------------


def _benchmark_gate7_stress_and_forensics(
    target_dir: Path,
    *,
    backend: Literal["ducklake", "parquet"],
) -> dict[str, object]:
    """Run all 3 IMF WP/15/216 stress scenarios and summarize RF_08 / RF_09 by ICB2."""
    repo = ParquetRepository(data_root=target_dir, backend=backend)
    with repo.snapshot_session() as pinned:
        svc = DashboardQueryService(pinned)
        stress_inputs = svc.get_stress_test_snapshot_inputs(
            statement_scope="CONSOLIDATED",
            period_type="QUARTER",
        )
        red_flags = pinned.read_table(RED_FLAGS_TABLE)
        issuers = pinned.read_table(
            "issuers.parquet",
            columns=["issuer_id", "ticker", "icb_code_level2", "icb_name_level2"],
        )
        cip_facts = pinned.read_table(
            "financial_facts.parquet",
            columns=["issuer_id", "taxonomy_version", "metric_id", "period_end_date"],
            filters={"metric_id": "BS_CIP_LONG_TERM"},
        )

    slice_date = (
        stress_inputs["period_end_date"][0] if not stress_inputs.is_empty() else None
    )
    scenarios_out: dict[str, object] = {}
    for key, scenario in _IMF_STRESS_SCENARIOS:
        if stress_inputs.is_empty():
            scenarios_out[key] = {
                "scenario_name": scenario.name,
                "total_issuers_evaluated": 0,
                "sector_summary": [],
                "top_newly_distressed": [],
            }
            continue
        stressed = simulate_financial_stress(stress_inputs, scenario)
        sector_df = summarize_stress_by_sector(stressed)
        market_row = summarize_stress_by_sector(
            stressed.with_columns(
                pl.lit("MARKET").alias("icb_code_level2"),
                pl.lit("MARKET").alias("icb_name_level2"),
            )
        ).row(0, named=True)
        newly_df = (
            stressed.filter(
                pl.col("newly_distressed_under_shock").eq(True).fill_null(False)
            )
            .select(
                "issuer_id",
                "ticker",
                "icb_code_level2",
                "icb_name_level2",
                "total_debt",
                "interest_coverage",
                "stressed_interest_coverage",
                "ebit_ttm",
                "stressed_ebit_ttm",
                "cfo_ttm",
                "stressed_cfo_ttm",
            )
            .sort(
                ["total_debt", "ticker"],
                descending=[True, False],
                nulls_last=True,
            )
            .head(15)
        )
        scenarios_out[key] = {
            "scenario_name": scenario.name,
            "period_end_date": slice_date,
            "total_issuers_evaluated": int(market_row["issuer_count"]),
            "total_debt_sum": float(market_row["total_debt_sum"]),
            "baseline_debt_at_risk_pct": round(
                100.0 * float(market_row["baseline_debt_at_risk_pct"]), 4
            ),
            "stressed_debt_at_risk_pct": round(
                100.0 * float(market_row["stressed_debt_at_risk_pct"]), 4
            ),
            "newly_distressed_count": int(market_row["newly_distressed_count"]),
            "sector_summary": sector_df.with_columns(
                (pl.col("baseline_debt_at_risk_pct") * 100.0).round(4),
                (pl.col("stressed_debt_at_risk_pct") * 100.0).round(4),
            ).to_dicts(),
            "top_newly_distressed": newly_df.to_dicts(),
        }

    # RF_08 & RF_09 distribution by ICB2
    rf_with_icb = (
        red_flags.filter(
            pl.col("flag_code").is_in(
                [
                    "RF_08_NON_CORE_EARNINGS_DEPENDENCE",
                    "RF_09_FROZEN_CIP_CAPITALIZATION",
                ]
            )
        )
        .join(issuers, on="issuer_id", how="left")
        .with_columns(
            pl.col("icb_code_level2").fill_null("UNCLASSIFIED"),
            pl.col("icb_name_level2").fill_null("UNCLASSIFIED"),
        )
    )
    forensics_by_flag: dict[str, object] = {}
    for flag_code in (
        "RF_08_NON_CORE_EARNINGS_DEPENDENCE",
        "RF_09_FROZEN_CIP_CAPITALIZATION",
    ):
        sub = rf_with_icb.filter(pl.col("flag_code") == flag_code)
        by_icb2 = (
            sub.group_by(["icb_code_level2", "icb_name_level2"])
            .agg(
                pl.len().alias("flag_rows"),
                pl.col("issuer_id").n_unique().alias("unique_issuers"),
                pl.col("ticker")
                .drop_nulls()
                .unique()
                .sort()
                .head(5)
                .alias("sample_tickers"),
            )
            .sort(["flag_rows", "icb_code_level2"], descending=[True, False])
            .to_dicts()
            if not sub.is_empty()
            else []
        )
        forensics_by_flag[flag_code] = {
            "total_flag_rows": sub.height,
            "unique_issuers": (
                int(sub["issuer_id"].n_unique()) if not sub.is_empty() else 0
            ),
            "by_icb2": by_icb2,
        }

    cip_by_taxonomy = (
        cip_facts.group_by("taxonomy_version")
        .agg(
            pl.len().alias("fact_rows"),
            pl.col("issuer_id").n_unique().alias("unique_issuers"),
        )
        .sort("taxonomy_version")
        .to_dicts()
        if not cip_facts.is_empty()
        else []
    )
    rf07_published_rows = int(
        red_flags.filter(pl.col("flag_code") == "RF_07_AUDIT_OR_FILING_WARNING").height
    )

    return {
        "stress_slice_period_end_date": slice_date,
        "stress_scenarios": scenarios_out,
        "forensics_rf08_rf09": forensics_by_flag,
        "rf07_published_rows": rf07_published_rows,
        "bs_cip_long_term_taxonomy_coverage": cip_by_taxonomy,
    }


# ---------------------------------------------------------------------------
# Gate 8: Audit Integrity (Zero Orphan Filings & DuPont Residuals <= 1e-9)
# ---------------------------------------------------------------------------


def _benchmark_gate8_audit_integrity(
    target_dir: Path,
    *,
    backend: Literal["ducklake", "parquet"],
    rollback_verification: Mapping[str, object],
    asset_checks: Mapping[str, object],
) -> dict[str, object]:
    """Verify 0 orphan filing_ids and exact 5/6-factor DuPont residuals on active snapshot."""
    repo = ParquetRepository(data_root=target_dir, backend=backend)
    with repo.snapshot_session() as pinned:
        facts = pinned.read_table("financial_facts.parquet", columns=["filing_id"])
        ratios = pinned.read_table(RATIOS_TABLE)
        red_flags = pinned.read_table(RED_FLAGS_TABLE, columns=["filing_id"])
        ml_scores = pinned.read_table(ML_SCORES_TABLE, columns=["filing_id"])
        quarantine = pinned.read_table(AUDIT_QUARANTINE_TABLE)

    valid_filing_ids = set(facts["filing_id"].drop_nulls().cast(pl.String).to_list())
    referenced: set[str] = set()
    for frame in (ratios, red_flags, ml_scores):
        if "filing_id" in frame.columns and not frame.is_empty():
            referenced.update(frame["filing_id"].drop_nulls().cast(pl.String).to_list())
    if "source_filing_ids" in ratios.columns and not ratios.is_empty():
        referenced.update(
            ratios.select(
                pl.col("source_filing_ids").explode().drop_nulls().cast(pl.String)
            )["source_filing_ids"].to_list()
        )
    orphans = sorted(referenced - valid_filing_ids)

    c = pl.col
    valid_dp = ratios.filter(c("dupont_valid_flag").eq(True).fill_null(False))
    max_5f: float | None = None
    max_6f: float | None = None
    if not valid_dp.is_empty():
        prod_5 = (
            c("tax_burden")
            * c("interest_burden")
            * c("ebit_margin")
            * c("asset_turnover")
            * c("equity_multiplier_total")
        )
        diff_5 = (prod_5 - c("roe_total")).abs()
        has_6 = (
            c("parent_profit_share").is_not_null()
            & c("equity_multiplier_parent").is_not_null()
        )
        prod_6 = (
            c("tax_burden")
            * c("interest_burden")
            * c("ebit_margin")
            * c("asset_turnover")
            * c("parent_profit_share")
            * c("equity_multiplier_parent")
        )
        diff_6 = pl.when(has_6).then((prod_6 - c("roe_parent")).abs()).otherwise(None)
        res_row = valid_dp.select(
            diff_5.max().alias("m5"), diff_6.max().alias("m6")
        ).row(0, named=True)
        if isinstance(res_row["m5"], (int, float)):
            max_5f = float(res_row["m5"])
        if isinstance(res_row["m6"], (int, float)):
            max_6f = float(res_row["m6"])

    dupont_pass = (max_5f is None or max_5f <= DUPONT_TOLERANCE) and (
        max_6f is None or max_6f <= DUPONT_TOLERANCE
    )
    return {
        "active_snapshot_id": repo.snapshot_id,
        "valid_fact_filing_ids_count": len(valid_filing_ids),
        "referenced_filing_ids_count": len(referenced),
        "orphan_filing_ids_count": len(orphans),
        "orphan_filing_ids_sample": orphans[:10],
        "quarantined_rows": quarantine.height,
        "dupont_valid_rows": valid_dp.height,
        "dupont_max_five_factor_residual": max_5f,
        "dupont_max_six_factor_residual": max_6f,
        "dupont_tolerance": DUPONT_TOLERANCE,
        "dupont_passed": dupont_pass,
        "dagster_blocking_checks": dict(asset_checks),
        "rollback_verification": dict(rollback_verification),
        "passed": (
            len(orphans) == 0
            and dupont_pass
            and bool(rollback_verification.get("passed"))
        ),
    }


# ---------------------------------------------------------------------------
# Public API #6: End-to-End V2 Pipeline + 8-Gate Benchmark Runner
# ---------------------------------------------------------------------------


def run_v2_pipeline_and_benchmarks(
    target_dir: Path,
    config_dir: Path,
    *,
    as_of: date,
    validation_start: date,
    test_start: date,
    backend: Literal["ducklake", "parquet"] = "ducklake",
    uvicorn_port: int = 0,
    run_benchmarks: bool = True,
    ml_enabled: bool = True,
) -> dict[str, object]:
    """Execute Layer 1 -> Layer 2 -> Dagster Publish/Rollback/GC -> 8-Gate Benchmarks."""
    dst_resolved = target_dir.resolve()
    cfg_resolved = config_dir.resolve()
    state_path = dst_resolved / "metadata" / "state.sqlite"
    issuers_path = dst_resolved / "curated" / "issuers.parquet"
    if not state_path.is_file():
        raise FileNotFoundError(f"Target state.sqlite not found: {state_path}")
    if not issuers_path.is_file():
        raise FileNotFoundError(f"Target issuers.parquet not found: {issuers_path}")

    effective_backend: Literal["ducklake", "parquet"] = backend
    if effective_backend == "ducklake" and not _ducklake_extensions_available():
        raise ContractViolationError(
            "Requested backend='ducklake', but DuckDB ducklake/sqlite extensions are unavailable."
        )

    target_state = StateStore(state_path)
    target_state.initialize()
    issuers_df = pl.read_parquet(issuers_path)
    tickers = tuple(
        sorted(issuers_df["ticker"].drop_nulls().cast(pl.String).unique().to_list())
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    with tempfile.TemporaryDirectory(prefix="finintel_b6_pre_enrich_") as pre_tmp:
        pre_state_backup: Path | None = None
        if run_benchmarks and ml_enabled:
            pre_state_backup = backup_sqlite_wal_safe(
                state_path,
                Path(pre_tmp) / "pre_enrichment_state.sqlite",
                read_only_source=True,
            )

        # Phase A: Layer 1 Disclosures + Gate 4 (RF_07 Before/After)
        layer1_report = _run_layer1_disclosures(
            dst_resolved, target_state, tickers, as_of=as_of, stamp=stamp
        )

        # Phase B: Layer 2 Full + Incremental (0-dirty & 1-dirty) Normalize
        layer2_report, _ = _run_layer2_normalize_benchmark(
            dst_resolved, cfg_resolved, target_state, stamp=stamp
        )

        # Phase C: Dagster Publish + Blocking Checks + Rollback Test + 3-Run Maintenance/GC
        dagster_report = _run_dagster_publish_rollback_and_gc(
            dst_resolved,
            cfg_resolved,
            target_state,
            as_of=as_of,
            validation_start=validation_start,
            test_start=test_start,
            backend=effective_backend,
            ml_enabled=ml_enabled,
        )

        evidence: dict[str, object] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "target_dir": dst_resolved.as_posix(),
            "config_dir": cfg_resolved.as_posix(),
            "backend": effective_backend,
            "parameters": {
                "as_of": as_of.isoformat(),
                "validation_start": validation_start.isoformat(),
                "test_start": test_start.isoformat(),
                "ml_enabled": ml_enabled,
                "run_benchmarks": run_benchmarks,
            },
            "layer1_disclosures": layer1_report,
            "gate_1_layer2_performance": layer2_report,
            "dagster_publish_and_rollback": dagster_report,
        }

        if run_benchmarks:
            snap_1_info = cast(dict[str, object], dagster_report["snap_1"])
            snap_3_info = cast(dict[str, object], dagster_report["snap_3"])
            maint_gc_info = cast(
                dict[str, object], dagster_report["maintenance_and_curated_gc"]
            )
            rb_info = cast(dict[str, object], dagster_report["rollback_verification"])

            evidence["gate_2_data_skipping_and_gc"] = _benchmark_gate2_data_skipping(
                dst_resolved,
                backend=effective_backend,
                maintenance_gc_report=maint_gc_info,
            )
            evidence["gate_3_tt99_2026_coverage"] = _benchmark_gate3_tt99_2026_coverage(
                dst_resolved,
                backend=effective_backend,
            )
            evidence["gate_4_rf07_before_after"] = layer1_report["rf07_before_after"]
            if ml_enabled:
                evidence["gate_5_ml_and_ablations"] = _benchmark_gate5_ml_and_ablations(
                    dst_resolved,
                    cfg_resolved,
                    backend=effective_backend,
                    as_of=as_of,
                    validation_start=validation_start,
                    test_start=test_start,
                    primary_run_id=str(snap_1_info["run_id"]),
                    pre_enrichment_state_path=pre_state_backup,
                )
            evidence["gate_6_serving_benchmark"] = _benchmark_gate6_uvicorn_serving(
                dst_resolved,
                cfg_resolved,
                target_state,
                backend=effective_backend,
                as_of=as_of,
                validation_start=validation_start,
                test_start=test_start,
                ml_enabled=ml_enabled,
                uvicorn_port=uvicorn_port,
            )
            evidence["gate_7_stress_and_forensics"] = (
                _benchmark_gate7_stress_and_forensics(
                    dst_resolved,
                    backend=effective_backend,
                )
            )
            evidence["gate_8_audit_and_rollback"] = _benchmark_gate8_audit_integrity(
                dst_resolved,
                backend=effective_backend,
                rollback_verification=rb_info,
                asset_checks=cast(dict[str, object], snap_3_info["asset_checks"]),
            )

    return evidence


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only WAL-safe universe migration to FinIntel V2 with dual SHA-256 "
            "integrity verification, rollback testing, and 8-gate benchmark runner."
        )
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        required=True,
        help="Read-only path to archived universe directory (never modified).",
    )
    parser.add_argument(
        "--source-db",
        type=Path,
        default=None,
        help=(
            "Optional explicit source SQLite database path "
            "(defaults to <source-dir>/metadata/state.sqlite; e.g. state_statutory_baseline.sqlite)."
        ),
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path("data"),
        help="Target V2 data directory (default: data).",
    )
    parser.add_argument(
        "--issuers-path",
        type=Path,
        default=None,
        help="Optional explicit path to issuers.parquet (defaults to <source-dir>/curated/issuers.parquet).",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Optional backup directory strictly outside --target-dir for pre-cutover backup.",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path("config"),
        help="Taxonomy and settings config directory (default: config).",
    )
    parser.add_argument(
        "--backend",
        choices=("ducklake", "parquet"),
        default="ducklake",
        help="Target Layer 3 storage backend (default: ducklake per Gate B0).",
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=DEFAULT_AS_OF,
        help=f"Point-in-Time as-of cutoff date YYYY-MM-DD (default: {DEFAULT_AS_OF.isoformat()}).",
    )
    parser.add_argument(
        "--validation-start",
        type=date.fromisoformat,
        default=DEFAULT_VALIDATION_START,
        help=f"ML validation split start YYYY-MM-DD (default: {DEFAULT_VALIDATION_START.isoformat()}).",
    )
    parser.add_argument(
        "--test-start",
        type=date.fromisoformat,
        default=DEFAULT_TEST_START,
        help=f"ML test split start YYYY-MM-DD (default: {DEFAULT_TEST_START.isoformat()}).",
    )
    parser.add_argument(
        "--evidence-json",
        type=Path,
        default=None,
        help="Optional path to write complete structured JSON verification evidence.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only run read-only dual SHA-256 verification on --source-dir and exit.",
    )
    parser.add_argument(
        "--skip-benchmarks",
        action="store_true",
        help="Run migration + V2 pipeline + rollback + GC without the 8-gate benchmark suite.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.verify_only:
        report = verify_source_integrity(
            args.source_dir, source_db=args.source_db, raise_on_error=True
        )
        payload: dict[str, object] = {
            "mode": "verify_only",
            "source_dir": args.source_dir.resolve().as_posix(),
            "source_db": (
                args.source_db.resolve().as_posix()
                if args.source_db is not None
                else (args.source_dir / "metadata" / "state.sqlite")
                .resolve()
                .as_posix()
            ),
            "integrity": asdict(report),
        }
        if args.evidence_json is not None:
            _write_json_atomic(args.evidence_json, payload)
        print(json.dumps(_sanitize_json_value(payload), indent=2, ensure_ascii=False))
        return 0

    migration_report = migrate_universe_read_only(
        args.source_dir,
        args.target_dir,
        issuers_path=args.issuers_path,
        backup_dir=args.backup_dir,
        source_db=args.source_db,
    )
    pipeline_report = run_v2_pipeline_and_benchmarks(
        args.target_dir,
        args.config_dir,
        as_of=args.as_of,
        validation_start=args.validation_start,
        test_start=args.test_start,
        backend=cast(Literal["ducklake", "parquet"], args.backend),
        run_benchmarks=not args.skip_benchmarks,
    )
    combined: dict[str, object] = {
        "mode": "migrate_and_verify" if not args.skip_benchmarks else "migrate_only",
        "migration": migration_report,
        "pipeline_and_benchmarks": pipeline_report,
    }
    if args.evidence_json is not None:
        _write_json_atomic(args.evidence_json, combined)

    summary = {
        "mode": combined["mode"],
        "source_integrity_passed": cast(
            dict[str, object], migration_report["integrity"]
        )["passed"],
        "done_count": cast(dict[str, object], migration_report["integrity"])[
            "done_count"
        ],
        "manifest_rows_checked": cast(dict[str, object], migration_report["integrity"])[
            "manifest_rows_checked"
        ],
        "active_snapshot_id": cast(
            dict[str, object],
            cast(dict[str, object], pipeline_report["dagster_publish_and_rollback"])[
                "snap_3"
            ],
        )["snapshot_id"],
        "rollback_passed": cast(
            dict[str, object],
            cast(dict[str, object], pipeline_report["dagster_publish_and_rollback"])[
                "rollback_verification"
            ],
        )["passed"],
        "evidence_json": (
            args.evidence_json.resolve().as_posix()
            if args.evidence_json is not None
            else None
        ),
    }
    print(json.dumps(_sanitize_json_value(summary), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
