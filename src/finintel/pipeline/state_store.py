"""SQLite persistence for runs, steps, the active snapshot and key metadata."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

FHSC_CONNECTOR_VERSION = "0.1.2"
FHSC_KEY_LIFETIME = timedelta(days=30)

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
)
SCHEMA = ";\n".join(statement.strip() for statement in SCHEMA_STATEMENTS) + ";\n"


def key_fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[-8:]


def _now() -> str:
    return datetime.now(UTC).isoformat()


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

    def recover_interrupted_runs(self) -> None:
        """Close abandoned RUNNING records while holding the pipeline file lock."""
        now = _now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE pipeline_steps SET status = 'FAILED', finished_at = ?,
                   error_type = 'InterruptedRun',
                   error_message = 'The pipeline process stopped before recording an outcome.'
                   WHERE status = 'RUNNING'""",
                (now,),
            )
            conn.execute(
                "UPDATE pipeline_runs SET status = 'FAILED', finished_at = ? WHERE status = 'RUNNING'",
                (now,),
            )

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

    def _one(
        self, query: str, parameters: tuple[object, ...]
    ) -> dict[str, object] | None:
        with self.connection() as conn:
            row = conn.execute(query, parameters).fetchone()
            return dict(row) if row is not None else None
