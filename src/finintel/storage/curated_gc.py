"""Fail-closed garbage collection for ``data/curated/`` run directories.

Verified Sources:
- Michael, M. M. (2004). Hazard Pointers: Safe Memory Reclamation for Lock-Free Objects
  (IEEE TPDS, Vol. 15, No. 6) — grace-period deferred reclamation.
- Hart, T. E. et al. (2007). Performance of Memory Reclamation for Lockless
  Synchronization (JPDC, Vol. 67, No. 12).
- filelock Documentation: https://py-filelock.readthedocs.io/
- Internal two-phase Windows-safe removal: ``SnapshotPublisher._remove_snapshot``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Collection
from pathlib import Path

import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.pipeline.state_store import StateStore
from finintel.storage.schemas import AUDIT_QUARANTINE_TABLE
from finintel.storage.snapshot_publisher import SnapshotPublisher, pipeline_write_lock

logger = logging.getLogger("finintel.storage.curated_gc")
_ALLOWED_CURATED_SUBDIRS: tuple[str, ...] = ("financial_facts", "quality", "analytics")
_STAGING_PREFIXES: tuple[str, ...] = (".normalize-", ".quality-", ".analytics-")


def _load_json_dict(path: Path) -> dict[str, object] | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _read_state_protection(
    state: StateStore, *, keep_snapshots: int
) -> tuple[set[str], dict[str, tuple[str, bool, str]]]:
    """Read RUNNING runs, active/retained snapshot runs, and run provenance from SQLite.

    Returns ``(protected_run_ids, run_info)`` where ``run_info[run_id]`` is
    ``(source, legacy_full_history, started_at)``.
    """
    if not state.path.is_file():
        raise ContractViolationError(
            f"StateStore database is missing; refusing curated GC: {state.path}"
        )
    try:
        with state.connection() as conn:
            running_rows = conn.execute(
                "SELECT run_id FROM pipeline_runs WHERE status = 'RUNNING'"
            ).fetchall()
            protected: set[str] = {str(row[0]) for row in running_rows}

            active_row = conn.execute(
                "SELECT run_id FROM active_snapshot WHERE singleton = 1"
            ).fetchone()
            if active_row is not None and active_row[0] is not None:
                protected.add(str(active_row[0]))

            retained_rows = conn.execute(
                """
                SELECT r.run_id
                FROM pipeline_runs AS r
                LEFT JOIN active_snapshot AS a ON a.run_id = r.run_id
                WHERE r.published_snapshot_id IS NOT NULL OR a.snapshot_id IS NOT NULL
                ORDER BY r.snapshot_publish_order DESC,
                         r.finished_at DESC,
                         r.rowid DESC
                LIMIT ?
                """,
                (keep_snapshots,),
            ).fetchall()
            protected.update(str(row[0]) for row in retained_rows)

            all_runs = conn.execute(
                """
                SELECT run_id,
                       COALESCE(json_extract(cli_args_json, '$.source'), 'unknown') AS src,
                       (
                           json_extract(cli_args_json, '$.period') IS NULL
                           AND COALESCE(json_array_length(cli_args_json, '$.tickers'), 0) = 0
                       ) AS legacy_full,
                       started_at
                FROM pipeline_runs
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise ContractViolationError(
            "Unable to query StateStore for curated GC safety checks."
        ) from exc

    run_info: dict[str, tuple[str, bool, str]] = {
        str(row[0]): (str(row[1]), bool(row[2]), str(row[3])) for row in all_runs
    }
    return protected, run_info


def _preload_normalize_metadata(
    curated_root: Path,
) -> dict[str, dict[str, object]]:
    """Preload all ``financial_facts/<run_id>/_normalize_meta.json`` prior to any deletion."""
    ff_root = curated_root / "financial_facts"
    if ff_root.is_symlink() or not ff_root.is_dir():
        return {}
    try:
        if not ff_root.resolve().is_relative_to(curated_root):
            return {}
    except OSError:
        return {}

    cache: dict[str, dict[str, object]] = {}
    for entry in ff_root.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:
            if not entry.resolve().is_relative_to(curated_root):
                continue
        except OSError:
            continue
        meta = _load_json_dict(entry / "_normalize_meta.json")
        if meta is not None:
            cache[entry.name] = meta
    return cache


def _resolve_source_and_full_history(
    subdir: str,
    run_id: str,
    entry: Path,
    run_info: dict[str, tuple[str, bool, str]],
    norm_meta_cache: dict[str, dict[str, object]],
) -> tuple[str, bool]:
    """Determine ``(source, is_full_history)`` for a curated run directory."""
    db_source, db_legacy_full, _ = run_info.get(run_id, ("unknown", True, ""))

    norm_meta = norm_meta_cache.get(run_id)
    if norm_meta is not None:
        raw_src = norm_meta.get("source")
        source = str(raw_src) if isinstance(raw_src, str) and raw_src else db_source
        full_history = bool(norm_meta.get("full_history") is True)
        return source, full_history

    if subdir == "analytics":
        analytics_meta = _load_json_dict(entry / "_analytics_meta.json")
        if analytics_meta is not None and isinstance(analytics_meta.get("source"), str):
            return str(analytics_meta["source"]), db_legacy_full

    return db_source, db_legacy_full


def _has_forensic_quarantine_evidence(quality_run_dir: Path) -> bool:
    """Fail-closed check: return True if ``audit_quarantine.parquet`` has rows or is unreadable."""
    quarantine_path = quality_run_dir / AUDIT_QUARANTINE_TABLE
    if quarantine_path.is_symlink():
        logger.warning(
            "Skipping curated quality directory with symlinked audit_quarantine.parquet",
            extra={
                "event": "curated_gc_quarantine_symlink",
                "details": {"path": str(quarantine_path)},
            },
        )
        return True
    if not quarantine_path.exists():
        return False
    try:
        frame = pl.read_parquet(quarantine_path)
    except (OSError, pl.exceptions.PolarsError, Exception) as exc:
        logger.warning(
            "Fail-closed preservation of unreadable audit_quarantine.parquet",
            extra={
                "event": "curated_gc_quarantine_unreadable",
                "details": {
                    "path": str(quarantine_path),
                    "error_type": type(exc).__name__,
                },
            },
        )
        return True
    return frame.height > 0


def collect_curated_garbage(
    data_root: Path,
    state: StateStore,
    *,
    keep_runs: int = 2,
    min_age_seconds: int = 3600,
    protected_run_ids: Collection[str] = (),
    keep_snapshots: int = 2,
) -> list[Path]:
    """Safely prune aged, unreferenced run directories under ``data/curated/``.

    Enforces all 7 fail-closed invariants:
    1. Rejects ``keep_runs < 1`` or ``min_age_seconds < 0`` with ``ValueError``.
    2. Inspects ONLY ``{"financial_facts", "quality", "analytics"}`` (preserving
       ``curated/issuers.parquet``), verifies path confinement, and rejects symlinks.
    3. Serializes under ``pipeline.lock`` and protects active staging directories
       (``.normalize-*``, ``.quality-*``, ``.analytics-*``) newer than ``min_age_seconds``.
    4. Protects ``RUNNING`` runs, ``active_snapshot`` and ``keep_snapshots`` promoted
       runs (even if ``FAILED``), caller-supplied ``protected_run_ids``, and young dirs.
    5. Retains at least ``keep_runs`` newest ``full_history`` runs independently per
       ``(subdir, source)`` pair.
    6. Fail-closed forensic protection on ``audit_quarantine.parquet`` (non-empty or
       any read/corrupt error preserves the directory and logs a warning).
    7. Uses atomic rename + deferred cleanup via ``SnapshotPublisher._remove_snapshot``.
    """
    if type(keep_runs) is not int or keep_runs < 1:
        raise ValueError("keep_runs must be an integer >= 1.")
    if type(min_age_seconds) is not int or min_age_seconds < 0:
        raise ValueError("min_age_seconds must be a non-negative integer.")
    if type(keep_snapshots) is not int or keep_snapshots < 1:
        raise ValueError("keep_snapshots must be an integer >= 1.")

    curated_root = (data_root / "curated").resolve()
    if not curated_root.is_dir():
        return []

    removed: list[Path] = []
    lock_path = state.path.with_name("pipeline.lock")

    with pipeline_write_lock(lock_path):
        state_protected, run_info = _read_state_protection(
            state, keep_snapshots=keep_snapshots
        )
        protected: set[str] = set(state_protected) | {
            str(rid) for rid in protected_run_ids
        }
        now = time.time()

        # Phase 1: Preload normalization metadata and plan all deletions across
        # every curated subdir before mutating disk so deleting financial_facts/<run_id>
        # never erases _normalize_meta.json needed by quality/ or analytics/.
        norm_meta_cache = _preload_normalize_metadata(curated_root)
        planned_removals: list[Path] = []

        for subdir in _ALLOWED_CURATED_SUBDIRS:
            subdir_path = curated_root / subdir
            if subdir_path.is_symlink() or not subdir_path.is_dir():
                continue
            try:
                if not subdir_path.resolve().is_relative_to(curated_root):
                    continue
            except OSError:
                continue

            run_entries: list[tuple[str, Path, int, str, bool]] = []
            for entry in subdir_path.iterdir():
                if entry.is_symlink():
                    logger.warning(
                        "Skipping symlink inside curated storage",
                        extra={
                            "event": "curated_gc_symlink_skipped",
                            "details": {"path": str(entry)},
                        },
                    )
                    continue
                if not entry.is_dir():
                    continue
                try:
                    resolved_entry = entry.resolve()
                    if not resolved_entry.is_relative_to(curated_root):
                        logger.warning(
                            "Skipping path outside curated_root",
                            extra={
                                "event": "curated_gc_path_escape_skipped",
                                "details": {"path": str(entry)},
                            },
                        )
                        continue
                    stat_result = entry.stat()
                except OSError:
                    continue

                age_seconds = now - stat_result.st_mtime
                if entry.name.startswith(_STAGING_PREFIXES):
                    if age_seconds >= min_age_seconds:
                        planned_removals.append(entry)
                    continue
                if entry.name.startswith("_tmp_gc_"):
                    continue

                run_id = entry.name
                source, is_full_history = _resolve_source_and_full_history(
                    subdir, run_id, entry, run_info, norm_meta_cache
                )
                run_entries.append(
                    (run_id, entry, stat_result.st_mtime_ns, source, is_full_history)
                )

            # Retain newest keep_runs full_history batches per (subdir, source).
            by_source: dict[str, list[tuple[str, str, int]]] = {}
            for run_id, _entry, mtime_ns, source, is_full in run_entries:
                if is_full:
                    started_at = run_info.get(run_id, ("", False, ""))[2]
                    by_source.setdefault(source, []).append(
                        (started_at, run_id, mtime_ns)
                    )

            keep_by_history: set[str] = set()
            for source_runs in by_source.values():
                ordered = sorted(
                    source_runs,
                    key=lambda item: (item[0], item[2], item[1]),
                    reverse=True,
                )
                for _started, run_id, _mtime_ns in ordered[:keep_runs]:
                    keep_by_history.add(run_id)

            for run_id, entry, mtime_ns, _source, _is_full in run_entries:
                if run_id in protected or run_id in keep_by_history:
                    continue
                age_seconds = now - (mtime_ns / 1_000_000_000)
                if age_seconds < min_age_seconds:
                    continue
                if subdir == "quality" and _has_forensic_quarantine_evidence(entry):
                    continue
                planned_removals.append(entry)

        # Phase 2: Execute planned removals atomically.
        for target in planned_removals:
            if SnapshotPublisher._remove_snapshot(target):
                removed.append(target)

    return removed
