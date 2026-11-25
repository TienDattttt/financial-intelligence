"""SQLite persistence for runs, steps, the active snapshot and key metadata.

Verified Sources:
    - SQLite Official Documentation — Write-Ahead Logging (WAL):
      https://www.sqlite.org/wal.html
    - SQLite Official Documentation — PRAGMA synchronous:
      https://www.sqlite.org/pragma.html#pragma_synchronous
      In WAL mode, ``PRAGMA synchronous = NORMAL`` is corruption-safe across
      process and OS crashes while avoiding an ``fsync()`` on every commit
      (syncing on WAL checkpoints instead; only the very latest committed
      transactions before an abrupt power loss could roll back).
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

FHSC_CONNECTOR_VERSION = "0.1.2"
FHSC_KEY_LIFETIME = timedelta(days=30)

_FHSC_WATERMARK_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS fhsc_pagination_watermarks (
        ticker TEXT NOT NULL,
        statement_type TEXT NOT NULL,
        period_type TEXT NOT NULL,
        last_status TEXT NOT NULL,
        completed_at TEXT,
        last_run_id TEXT,
        PRIMARY KEY (ticker, statement_type, period_type)
    )
    """

_FHSC_DISCLOSURE_COOLDOWN_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS fhsc_disclosure_cooldowns (
        provider_key TEXT PRIMARY KEY,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        cooldown_until TEXT,
        last_http_status INTEGER,
        last_error_type TEXT,
        last_failure_at TEXT,
        updated_at TEXT NOT NULL
    )
    """

_PROVIDER_RATE_LIMITS_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS provider_rate_limits (
        host TEXT PRIMARY KEY,
        next_allowed_at_epoch REAL NOT NULL
    )
    """

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS pipeline_runs (
        run_id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL CHECK (
            status IN ('RUNNING', 'SUCCEEDED', 'PARTIAL_SUCCESS', 'FAILED', 'ABORTED_CIRCUIT_OPEN')
        ),
        trigger_mode TEXT NOT NULL,
        cli_args_json TEXT NOT NULL,
        git_commit_sha TEXT,
        published_snapshot_id TEXT,
        snapshot_publish_order INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pipeline_steps (
        step_id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id),
        stage_name TEXT NOT NULL,
        issuer_id TEXT,
        filing_id TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL CHECK (
            status IN ('RUNNING', 'SUCCEEDED', 'QUARANTINED', 'INCOMPLETE', 'SKIPPED_UNCHANGED', 'FAILED')
        ),
        attempt_count INTEGER NOT NULL DEFAULT 1,
        error_type TEXT,
        error_message TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS pipeline_steps_by_run ON pipeline_steps(run_id, step_id)",
    "CREATE INDEX IF NOT EXISTS pipeline_steps_by_stage_status ON pipeline_steps(stage_name, status, step_id)",
    """
    CREATE TABLE IF NOT EXISTS api_key_registry (
        provider_name TEXT PRIMARY KEY,
        key_fingerprint TEXT NOT NULL,
        provisioned_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        package_version TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS active_snapshot (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        snapshot_id TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id),
        published_at TEXT NOT NULL
    )
    """,
    _FHSC_WATERMARK_TABLE_DDL,
    _FHSC_DISCLOSURE_COOLDOWN_TABLE_DDL,
    _PROVIDER_RATE_LIMITS_TABLE_DDL,
)
SCHEMA = ";\n".join(statement.strip() for statement in SCHEMA_STATEMENTS) + ";\n"


def key_fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[-8:]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def acquire_host_pacing_slot(
    db_path: Path,
    host: str,
    *,
    min_interval_seconds: float,
    jitter_seconds: float = 0.0,
    sleeper: Callable[[float], None] | None = None,
    now_fn: Callable[[], float] | None = None,
) -> float:
    """Reserve the next polite request slot for ``host`` across OS processes (<1ms lock hold)."""
    normalized_host = host.strip().lower()
    if not normalized_host:
        raise ValueError("host must be a non-empty hostname.")
    if (
        isinstance(min_interval_seconds, bool)
        or not math.isfinite(min_interval_seconds)
        or min_interval_seconds < 0.0
    ):
        raise ValueError("min_interval_seconds must be a finite non-negative float.")
    if (
        isinstance(jitter_seconds, bool)
        or not math.isfinite(jitter_seconds)
        or jitter_seconds < 0.0
    ):
        raise ValueError("jitter_seconds must be a finite non-negative float.")

    clock = now_fn if now_fn is not None else time.time
    sleep_fn = sleeper if sleeper is not None else time.sleep
    interval = float(min_interval_seconds) + float(jitter_seconds)
    resolved_db = db_path.resolve()
    resolved_db.parent.mkdir(parents=True, exist_ok=True)

    with closing(
        sqlite3.connect(resolved_db, timeout=10.0, isolation_level=None)
    ) as conn:
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("BEGIN IMMEDIATE;")
        now_epoch = float(clock())
        try:
            conn.execute(_PROVIDER_RATE_LIMITS_TABLE_DDL)
            row = conn.execute(
                "SELECT next_allowed_at_epoch FROM provider_rate_limits WHERE host = ?",
                (normalized_host,),
            ).fetchone()
            earliest = float(row[0]) if row is not None else now_epoch
            scheduled_at = max(now_epoch, earliest)
            conn.execute(
                "INSERT INTO provider_rate_limits(host, next_allowed_at_epoch) VALUES (?, ?) "
                "ON CONFLICT(host) DO UPDATE SET next_allowed_at_epoch = excluded.next_allowed_at_epoch",
                (normalized_host, scheduled_at + interval),
            )
            conn.execute("COMMIT;")
        except BaseException:
            conn.execute("ROLLBACK;")
            raise

    wait_seconds = max(0.0, scheduled_at - float(clock()))
    if wait_seconds > 0.0:
        sleep_fn(wait_seconds)
    return wait_seconds


class StateStore:
    """Use short transactions so readers can inspect progress during a run."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=5)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            with conn:
                yield conn

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA_STATEMENTS:
                conn.execute(statement)
            columns = conn.execute("PRAGMA table_info(pipeline_runs)").fetchall()
            if not any(
                column["name"] == "snapshot_publish_order" for column in columns
            ):
                conn.execute(
                    "ALTER TABLE pipeline_runs ADD COLUMN snapshot_publish_order INTEGER"
                )
                conn.execute(
                    """WITH ranked AS (
                           SELECT r.run_id, COALESCE(a.snapshot_id, r.published_snapshot_id) AS snapshot_id,
                                  ROW_NUMBER() OVER (
                                      ORDER BY (a.run_id IS NOT NULL), r.finished_at, r.rowid
                                  ) AS publish_order
                           FROM pipeline_runs AS r LEFT JOIN active_snapshot AS a ON a.run_id = r.run_id
                           WHERE r.published_snapshot_id IS NOT NULL OR a.snapshot_id IS NOT NULL
                       )
                       UPDATE pipeline_runs SET published_snapshot_id = ranked.snapshot_id,
                           snapshot_publish_order = ranked.publish_order
                       FROM ranked WHERE pipeline_runs.run_id = ranked.run_id"""
                )

    def recover_interrupted_runs(self) -> dict[str, int]:
        """Close abandoned RUNNING records while holding the pipeline file lock."""
        now = _now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            steps_cur = conn.execute(
                """UPDATE pipeline_steps SET status = 'FAILED', finished_at = ?,
                   error_type = 'InterruptedRun',
                   error_message = 'The pipeline process stopped before recording an outcome.'
                   WHERE status = 'RUNNING'""",
                (now,),
            )
            runs_cur = conn.execute(
                "UPDATE pipeline_runs SET status = 'FAILED', finished_at = ? WHERE status = 'RUNNING'",
                (now,),
            )
        return {
            "runs_recovered": int(runs_cur.rowcount if runs_cur.rowcount >= 0 else 0),
            "steps_recovered": int(
                steps_cur.rowcount if steps_cur.rowcount >= 0 else 0
            ),
        }

    def start_run(
        self,
        run_id: str,
        trigger_mode: str,
        arguments: Mapping[str, object],
        git_commit_sha: str | None,
    ) -> None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO pipeline_runs
                   (run_id, started_at, status, trigger_mode, cli_args_json, git_commit_sha)
                   VALUES (?, ?, 'RUNNING', ?, ?, ?)""",
                (
                    run_id,
                    _now(),
                    trigger_mode,
                    json.dumps(dict(arguments)),
                    git_commit_sha,
                ),
            )

    def finish_run(
        self, run_id: str, status: str, snapshot_id: str | None = None
    ) -> None:
        if status not in {"SUCCEEDED", "PARTIAL_SUCCESS"}:
            snapshot_id = None
        now = _now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Publication can commit before GC fails and the runner marks the run failed.
            conn.execute(
                "UPDATE pipeline_runs SET status = ?, finished_at = ? WHERE run_id = ?",
                (status, now, run_id),
            )
            if snapshot_id is not None:
                self._set_active_snapshot(conn, snapshot_id, run_id, now)

    def publish_snapshot(self, run_id: str, snapshot_id: str) -> None:
        """Atomically mark a completed run and its snapshot as active."""

        now = _now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM pipeline_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown pipeline run: {run_id}")
            if row["status"] != "RUNNING":
                raise ValueError(
                    f"Pipeline run {run_id} is not publishable from status {row['status']}."
                )
            conn.execute(
                "UPDATE pipeline_runs SET status = 'SUCCEEDED', finished_at = ? WHERE run_id = ?",
                (now, run_id),
            )
            self._set_active_snapshot(conn, snapshot_id, run_id, now)

    @staticmethod
    def _set_active_snapshot(
        conn: sqlite3.Connection, snapshot_id: str, run_id: str, published_at: str
    ) -> None:
        # Record the previous active promotion before replacing its pointer.
        conn.execute(
            """UPDATE pipeline_runs
               SET published_snapshot_id = COALESCE(published_snapshot_id, (
                   SELECT snapshot_id FROM active_snapshot WHERE singleton = 1
               )), snapshot_publish_order = COALESCE(snapshot_publish_order, (
                   SELECT COALESCE(MAX(snapshot_publish_order), 0) + 1 FROM pipeline_runs
               ))
               WHERE run_id = (SELECT run_id FROM active_snapshot WHERE singleton = 1)
                 AND (published_snapshot_id IS NULL OR snapshot_publish_order IS NULL)"""
        )
        previous = conn.execute(
            "SELECT published_snapshot_id FROM pipeline_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if previous is not None and previous["published_snapshot_id"] == snapshot_id:
            # Sync timestamp if this run is still active without reactivating an older publication.
            conn.execute(
                """UPDATE active_snapshot SET published_at = ?
                   WHERE singleton = 1 AND run_id = ? AND snapshot_id = ?""",
                (published_at, run_id, snapshot_id),
            )
            return
        conn.execute(
            """UPDATE pipeline_runs SET published_snapshot_id = ?, snapshot_publish_order = (
                   SELECT COALESCE(MAX(snapshot_publish_order), 0) + 1 FROM pipeline_runs
               )
               WHERE run_id = ?""",
            (snapshot_id, run_id),
        )
        conn.execute(
            """INSERT INTO active_snapshot (singleton, snapshot_id, run_id, published_at)
               VALUES (1, ?, ?, ?)
               ON CONFLICT(singleton) DO UPDATE SET snapshot_id = excluded.snapshot_id,
               run_id = excluded.run_id, published_at = excluded.published_at""",
            (snapshot_id, run_id, published_at),
        )

    def published_snapshot_ids(self) -> list[str]:
        """Return promoted snapshot IDs, newest first."""

        with self.connection() as conn:
            # Legacy rows without a recorded order fall back to their stored timestamps.
            rows = conn.execute(
                """SELECT COALESCE(a.snapshot_id, r.published_snapshot_id)
                   FROM pipeline_runs AS r LEFT JOIN active_snapshot AS a ON a.run_id = r.run_id
                   WHERE r.published_snapshot_id IS NOT NULL OR a.snapshot_id IS NOT NULL
                   ORDER BY (a.run_id IS NOT NULL) DESC, r.snapshot_publish_order DESC,
                            r.finished_at DESC, r.rowid DESC"""
            ).fetchall()
        return list(dict.fromkeys(str(row[0]) for row in rows))

    def start_step(self, run_id: str, stage: str) -> int:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute(
                """INSERT INTO pipeline_steps (run_id, stage_name, started_at, status)
                   VALUES (?, ?, ?, 'RUNNING')""",
                (run_id, stage, _now()),
            )
            assert result.lastrowid is not None
            return result.lastrowid

    def record_attempt(self, step_id: int, attempt: int) -> None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE pipeline_steps SET attempt_count = ? WHERE step_id = ?",
                (attempt, step_id),
            )

    def finish_step(
        self,
        step_id: int,
        status: str,
        *,
        issuer_id: str | None = None,
        filing_id: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE pipeline_steps SET status = ?, finished_at = ?, issuer_id = ?,
                   filing_id = ?, error_type = ?, error_message = ? WHERE step_id = ?""",
                (
                    status,
                    _now(),
                    issuer_id,
                    filing_id,
                    error_type,
                    error_message,
                    step_id,
                ),
            )

    def register_fhsc_key(self, key: str, provisioned_at: datetime) -> None:
        if not key.strip() or provisioned_at.utcoffset() is None:
            raise ValueError(
                "Provide a key and a timezone-aware provisioning timestamp."
            )
        provisioned_at = provisioned_at.astimezone(UTC)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO api_key_registry VALUES ('fhsc', ?, ?, ?, ?)
                   ON CONFLICT(provider_name) DO UPDATE SET
                   key_fingerprint = excluded.key_fingerprint, provisioned_at = excluded.provisioned_at,
                   expires_at = excluded.expires_at, package_version = excluded.package_version""",
                (
                    key_fingerprint(key),
                    provisioned_at.isoformat(),
                    (provisioned_at + FHSC_KEY_LIFETIME).isoformat(),
                    f"vnstock-oapi=={FHSC_CONNECTOR_VERSION}",
                ),
            )

    def get_key(self, provider: str) -> dict[str, object] | None:
        if not self.path.exists():
            return None
        try:
            return self._one(
                "SELECT * FROM api_key_registry WHERE provider_name = ?", (provider,)
            )
        except sqlite3.OperationalError as error:
            if "no such table" in str(error).lower():
                return None
            raise

    def get_run(self, run_id: str) -> dict[str, object] | None:
        return self._one("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,))

    def completed_normalizations(self, source: str) -> list[tuple[str, bool]]:
        """List succeeded normalization steps for a source, newest first.

        The boolean identifies unfiltered legacy batches without metadata.
        """
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT r.run_id, json_extract(r.cli_args_json, '$.period') IS NULL
                       AND COALESCE(json_array_length(r.cli_args_json, '$.tickers'), 0) = 0
                FROM pipeline_runs AS r
                JOIN pipeline_steps AS s ON s.run_id = r.run_id
                WHERE s.stage_name = 'NORMALIZE' AND s.status = 'SUCCEEDED'
                  AND json_extract(r.cli_args_json, '$.source') = ?
                ORDER BY s.step_id DESC
                """,
                (source,),
            ).fetchall()
        return [(str(row[0]), bool(row[1])) for row in rows]

    def list_steps(self, run_id: str) -> list[dict[str, object]]:
        with self.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM pipeline_steps WHERE run_id = ? ORDER BY step_id",
                    (run_id,),
                )
            ]

    def active_snapshot(self) -> dict[str, object] | None:
        return self._one("SELECT * FROM active_snapshot WHERE singleton = 1", ())

    def record_fhsc_watermark(
        self,
        ticker: str,
        statement_type: str,
        period_type: str,
        status: str,
        *,
        run_id: str | None = None,
    ) -> None:
        """Persist the latest full-history FHSC pagination status for early-stop gating."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = _now()
        completed_at = now if status == "COMPLETE" else None
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(_FHSC_WATERMARK_TABLE_DDL)
            conn.execute(
                """INSERT INTO fhsc_pagination_watermarks
                   (ticker, statement_type, period_type, last_status, completed_at, last_run_id)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(ticker, statement_type, period_type) DO UPDATE SET
                       last_status = excluded.last_status,
                       completed_at = CASE
                           WHEN excluded.last_status = 'COMPLETE' THEN excluded.completed_at
                           WHEN excluded.last_status = 'EARLY_STOPPED' THEN fhsc_pagination_watermarks.completed_at
                           ELSE NULL
                       END,
                       last_run_id = excluded.last_run_id""",
                (ticker, statement_type, period_type, status, completed_at, run_id),
            )

    def has_complete_fhsc_watermark(
        self,
        ticker: str,
        statement_type: str,
        period_type: str,
    ) -> bool:
        """Return True only when full-history FHSC pagination completed cleanly."""
        if not self.path.exists():
            return False
        try:
            row = self._one(
                """SELECT last_status, completed_at
                   FROM fhsc_pagination_watermarks
                   WHERE ticker = ? AND statement_type = ? AND period_type = ?""",
                (ticker, statement_type, period_type),
            )
        except sqlite3.OperationalError as error:
            if "no such table" in str(error).lower():
                return False
            raise
        if row is None:
            return False
        return row.get("completed_at") is not None and row.get("last_status") in {
            "COMPLETE",
            "EARLY_STOPPED",
        }

    @staticmethod
    def _normalize_utc(value: datetime) -> datetime:
        if value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _validate_provider_key(provider_key: str) -> str:
        clean = provider_key.strip().lower()
        if not clean:
            raise ValueError("provider_key must be a non-empty string.")
        return clean

    def _read_one(
        self, query: str, parameters: tuple[object, ...]
    ) -> dict[str, object] | None:
        if not self.path.exists():
            return None
        try:
            with closing(
                sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True, timeout=5)
            ) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout = 5000")
                row = conn.execute(query, parameters).fetchone()
                return dict(row) if row is not None else None
        except sqlite3.OperationalError as error:
            if "no such table" in str(error).lower():
                return None
            raise

    def get_disclosure_cooldown(self, provider_key: str) -> dict[str, object] | None:
        """Return persisted cooldown state for ``provider_key``, or None if absent/uninitialized."""
        clean_key = self._validate_provider_key(provider_key)
        return self._read_one(
            """SELECT provider_key, consecutive_failures, cooldown_until,
                      last_http_status, last_error_type, last_failure_at, updated_at
               FROM fhsc_disclosure_cooldowns
               WHERE provider_key = ?""",
            (clean_key,),
        )

    def active_disclosure_cooldown_until(
        self, provider_key: str, *, now: datetime | None = None
    ) -> datetime | None:
        """Return active UTC cooldown expiry if still in the future, else None."""
        row = self.get_disclosure_cooldown(provider_key)
        if row is None:
            return None
        raw_until = row.get("cooldown_until")
        if not isinstance(raw_until, str) or not raw_until.strip():
            return None
        try:
            cooldown_dt = self._normalize_utc(datetime.fromisoformat(raw_until))
        except ValueError:
            return None
        check_now = self._normalize_utc(now) if now is not None else datetime.now(UTC)
        return cooldown_dt if cooldown_dt > check_now else None

    def record_disclosure_failure(
        self,
        provider_key: str,
        *,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        max_cooldown_seconds: float = 900.0,
        retry_after_seconds: float | None = None,
        http_status: int | None = None,
        error_type: str | None = None,
        force_cooldown: bool = False,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Increment failure count for ``provider_key`` and open cooldown when breached."""
        clean_key = self._validate_provider_key(provider_key)
        if type(failure_threshold) is not int or failure_threshold < 1:
            raise ValueError("failure_threshold must be an integer >= 1.")
        if cooldown_seconds < 0 or max_cooldown_seconds < 0:
            raise ValueError("Cooldown durations must be non-negative.")
        if retry_after_seconds is not None and retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be non-negative.")

        check_now = self._normalize_utc(now) if now is not None else datetime.now(UTC)
        now_iso = check_now.isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(_FHSC_DISCLOSURE_COOLDOWN_TABLE_DDL)
            existing = conn.execute(
                """SELECT consecutive_failures, cooldown_until
                   FROM fhsc_disclosure_cooldowns
                   WHERE provider_key = ?""",
                (clean_key,),
            ).fetchone()
            prev_failures = (
                int(existing["consecutive_failures"]) if existing is not None else 0
            )
            new_failures = prev_failures + 1

            existing_until: datetime | None = None
            if existing is not None and isinstance(existing["cooldown_until"], str):
                try:
                    existing_until = self._normalize_utc(
                        datetime.fromisoformat(existing["cooldown_until"])
                    )
                except ValueError:
                    existing_until = None

            effective_cooldown = 0.0
            if force_cooldown or (
                new_failures >= failure_threshold and cooldown_seconds > 0
            ):
                base_dur = (
                    cooldown_seconds if new_failures >= failure_threshold else 0.0
                )
                if retry_after_seconds is not None and retry_after_seconds > 0:
                    base_dur = max(base_dur, retry_after_seconds)
                if force_cooldown and base_dur <= 0 and cooldown_seconds > 0:
                    base_dur = cooldown_seconds
                if max_cooldown_seconds > 0:
                    effective_cooldown = min(max_cooldown_seconds, base_dur)
                else:
                    effective_cooldown = base_dur

            candidate_until = (
                check_now + timedelta(seconds=effective_cooldown)
                if effective_cooldown > 0
                else None
            )
            if existing_until is not None and existing_until > check_now:
                if candidate_until is None or existing_until > candidate_until:
                    candidate_until = existing_until

            cooldown_until_iso = (
                candidate_until.isoformat() if candidate_until is not None else None
            )
            conn.execute(
                """INSERT INTO fhsc_disclosure_cooldowns
                   (provider_key, consecutive_failures, cooldown_until,
                    last_http_status, last_error_type, last_failure_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(provider_key) DO UPDATE SET
                       consecutive_failures = excluded.consecutive_failures,
                       cooldown_until = excluded.cooldown_until,
                       last_http_status = excluded.last_http_status,
                       last_error_type = excluded.last_error_type,
                       last_failure_at = excluded.last_failure_at,
                       updated_at = excluded.updated_at""",
                (
                    clean_key,
                    new_failures,
                    cooldown_until_iso,
                    http_status,
                    error_type,
                    now_iso,
                    now_iso,
                ),
            )
        return {
            "provider_key": clean_key,
            "consecutive_failures": new_failures,
            "cooldown_until": cooldown_until_iso,
            "last_http_status": http_status,
            "last_error_type": error_type,
            "last_failure_at": now_iso,
            "updated_at": now_iso,
        }

    def reset_disclosure_cooldown(self, provider_key: str) -> None:
        """Reset failure counter and cooldown if an active/non-zero row exists."""
        clean_key = self._validate_provider_key(provider_key)
        hit = self._read_one(
            """SELECT 1 AS hit
               FROM fhsc_disclosure_cooldowns
               WHERE provider_key = ?
                 AND (consecutive_failures > 0 OR cooldown_until IS NOT NULL)""",
            (clean_key,),
        )
        if hit is None:
            return
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE fhsc_disclosure_cooldowns
                   SET consecutive_failures = 0,
                       cooldown_until = NULL,
                       last_http_status = NULL,
                       last_error_type = NULL,
                       updated_at = ?
                   WHERE provider_key = ?""",
                (_now(), clean_key),
            )

    def acquire_host_pacing_slot(
        self,
        host: str,
        *,
        min_interval_seconds: float = 1.75,
        jitter_seconds: float = 0.0,
        sleeper: Callable[[float], None] | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> float:
        """Reserve the next polite request slot for ``host`` in this ``StateStore``."""
        return acquire_host_pacing_slot(
            self.path,
            host,
            min_interval_seconds=min_interval_seconds,
            jitter_seconds=jitter_seconds,
            sleeper=sleeper,
            now_fn=now_fn,
        )

    def _one(
        self, query: str, parameters: tuple[object, ...]
    ) -> dict[str, object] | None:
        with self.connection() as conn:
            row = conn.execute(query, parameters).fetchone()
            return dict(row) if row is not None else None
