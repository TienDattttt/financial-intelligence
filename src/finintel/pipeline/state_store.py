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

SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    trigger_mode TEXT NOT NULL,
    cli_args_json TEXT NOT NULL,
    git_commit_sha TEXT,
    published_snapshot_id TEXT
);
CREATE TABLE IF NOT EXISTS pipeline_steps (
    step_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    stage_name TEXT NOT NULL,
    issuer_id TEXT,
    filing_id TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    error_type TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS pipeline_steps_by_run ON pipeline_steps(run_id, step_id);
CREATE TABLE IF NOT EXISTS api_key_registry (
    provider_name TEXT PRIMARY KEY,
    key_fingerprint TEXT NOT NULL,
    provisioned_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    package_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS active_snapshot (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    snapshot_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    published_at TEXT NOT NULL
);
"""


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
            conn.executescript(SCHEMA)

    def recover_interrupted_runs(self) -> None:
        """Close abandoned RUNNING records while holding the pipeline file lock."""
        now = _now()
        with self.connection() as conn:
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
            conn.execute(
                """UPDATE pipeline_runs SET status = ?, finished_at = ?, published_snapshot_id = ?
                   WHERE run_id = ?""",
                (status, now, snapshot_id, run_id),
            )
            if snapshot_id is not None:
                conn.execute(
                    """INSERT INTO active_snapshot (singleton, snapshot_id, run_id, published_at)
                       VALUES (1, ?, ?, ?)
                       ON CONFLICT(singleton) DO UPDATE SET snapshot_id = excluded.snapshot_id,
                       run_id = excluded.run_id, published_at = excluded.published_at""",
                    (snapshot_id, run_id, now),
                )

    def start_step(self, run_id: str, stage: str) -> int:
        with self.connection() as conn:
            result = conn.execute(
                """INSERT INTO pipeline_steps (run_id, stage_name, started_at, status)
                   VALUES (?, ?, ?, 'RUNNING')""",
                (run_id, stage, _now()),
            )
            assert result.lastrowid is not None
            return result.lastrowid

    def record_attempt(self, step_id: int, attempt: int) -> None:
        with self.connection() as conn:
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
        return self._one(
            "SELECT * FROM api_key_registry WHERE provider_name = ?", (provider,)
        )

    def get_run(self, run_id: str) -> dict[str, object] | None:
        return self._one("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,))

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
