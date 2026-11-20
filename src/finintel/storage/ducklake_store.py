"""DuckLake Lakehouse storage backend with 2-phase atomic publish, crash recovery, and maintenance.

Verified Sources:
- DuckLake Official Documentation: https://ducklake.select/docs/stable/
- DuckLake Snapshot Expiration Docs (explicit BIGINT[] versions, never mixed with older_than):
  https://ducklake.select/docs/stable/duckdb/maintenance/expire_snapshots
- DuckLake File Cleanup Docs:
  https://ducklake.select/docs/stable/duckdb/maintenance/cleanup_old_files
- DuckDB Parquet Tips & Secrets Manager Docs:
  https://duckdb.org/docs/stable/data/parquet/tips
  https://duckdb.org/docs/stable/configuration/secrets_manager
- Gate B0 Measured Evidence: docs/evidence/option_b_spike_report.json

Runtime API Verification (Python 3.11.9, duckdb==1.4.2, DuckLake format 0.3):
- ``duckdb.connect(":memory:", read_only=True)`` raises ``CatalogException``; read-only
  sessions MUST connect to ``":memory:"`` and ``ATTACH ... (READ_ONLY)``.
- DuckLake 0.3 raises ``NotImplementedException`` on ``PRIMARY KEY`` constraints;
  ``_snapshot_audit`` uses ``NOT NULL`` columns with Python uniqueness enforcement under
  ``pipeline.lock``.
- DuckLake 0.3 raises ``BinderException`` on ``ALTER TABLE ... SET SORTED BY`` and
  ``NotImplementedException`` on table-scoped ``set_option`` for transaction-local tables;
  tables are physically sorted via SQL ``ORDER BY`` with catalog-scoped
  ``set_option('parquet_row_group_size', ...)``, while ``SET SORTED BY`` is probed outside
  the publish transaction so it activates automatically on DuckLake 0.4+.
- On SQLite catalogs, 1-arg ``ducklake_cleanup_old_files(alias)`` raises ``BinderException``
  and ``older_than`` compares local-offset VARCHAR timestamps against UTC; ``maintain()``
  uses ``cleanup_all => true`` when ``min_snapshot_age_seconds == 0`` and explicit
  ``older_than => now() - INTERVAL '<N> seconds'`` when ``min_snapshot_age_seconds > 0``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

import duckdb
import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.pipeline.state_store import StateStore
from finintel.storage.snapshot_publisher import (
    _ALLOW_EMPTY_TABLES,
    KNOWN_CATALOG_TABLES,
    KNOWN_TABLES,
    TABLE_PHYSICAL_LAYOUT,
    TableInput,
    pipeline_write_lock,
    prepare_storage_table,
    row_group_size_for_table,
)

logger = logging.getLogger("finintel.storage.ducklake")
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
SNAPSHOT_AUDIT_TABLE = "_snapshot_audit"
DEFAULT_CATALOG_ALIAS = "finintel_lake"
DEFAULT_POSTGRES_DSN_ENV = "FININTEL_DUCKLAKE_PG_DSN"
DEFAULT_OVERRIDE_DATA_PATH_ENV = "FININTEL_DUCKLAKE_OVERRIDE_DATA_PATH"

StorageBackend = Literal["parquet", "ducklake"]
CatalogDriver = Literal["sqlite", "postgres"]


def _resolve_override_data_path(explicit: bool = False) -> bool:
    if explicit:
        return True
    raw = os.environ.get(DEFAULT_OVERRIDE_DATA_PATH_ENV)
    if raw is None or not raw.strip():
        return False
    norm = raw.strip().lower()
    if norm in {"1", "true", "yes", "on"}:
        return True
    if norm in {"0", "false", "no", "off"}:
        return False
    raise ContractViolationError(
        f"{DEFAULT_OVERRIDE_DATA_PATH_ENV} must be a boolean value, got {raw!r}."
    )


def validate_sql_identifier(name: str, *, label: str = "identifier") -> str:
    """Validate that a SQL identifier contains only safe ASCII identifier characters."""
    if not isinstance(name, str) or _IDENTIFIER_RE.fullmatch(name) is None:
        raise ValueError(f"Invalid SQL {label}: {name!r}")
    return name


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _to_utc(value: object) -> datetime:
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        )
    parsed = datetime.fromisoformat(str(value))
    return (
        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    )


@dataclass(frozen=True)
class StorageCatalogConfig:
    """Catalog backend settings for DuckLake."""

    driver: CatalogDriver
    sqlite_path: Path
    postgres_dsn_env: str = DEFAULT_POSTGRES_DSN_ENV


@dataclass(frozen=True)
class StorageRetentionConfig:
    """Snapshot and curated-batch retention settings."""

    keep_snapshots: int = 2
    min_snapshot_age_seconds: int = 3600
    curated_keep_runs: int = 2


@dataclass(frozen=True)
class StorageConfig:
    """Typed storage configuration parsed from ``config/settings.yaml``."""

    backend: StorageBackend
    catalog: StorageCatalogConfig
    data_path: Path
    catalog_alias: str = DEFAULT_CATALOG_ALIAS
    retention: StorageRetentionConfig = StorageRetentionConfig()
    publish_unmapped: bool = False
    override_data_path: bool = False

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, object] | None,
        *,
        config_dir: Path,
        data_root: Path,
    ) -> StorageConfig:
        """Parse ``storage:`` config, defaulting to ``backend='parquet'`` when omitted."""
        default_sqlite = (data_root / "metadata" / "ducklake_catalog.sqlite").resolve()
        default_data_path = (data_root / "lakehouse" / "data").resolve()
        if not raw:
            return cls(
                backend="parquet",
                catalog=StorageCatalogConfig(
                    driver="sqlite",
                    sqlite_path=default_sqlite,
                    postgres_dsn_env=DEFAULT_POSTGRES_DSN_ENV,
                ),
                data_path=default_data_path,
                catalog_alias=DEFAULT_CATALOG_ALIAS,
                retention=StorageRetentionConfig(),
                publish_unmapped=False,
                override_data_path=False,
            )

        raw_backend = raw.get("backend", "parquet")
        if raw_backend not in ("parquet", "ducklake"):
            raise ValueError("storage.backend must be 'parquet' or 'ducklake'.")
        backend: StorageBackend = "ducklake" if raw_backend == "ducklake" else "parquet"

        raw_catalog = raw.get("catalog", {})
        if not isinstance(raw_catalog, Mapping):
            raise ValueError("storage.catalog must be a YAML mapping.")
        raw_driver = raw_catalog.get("driver", "sqlite")
        if raw_driver not in ("sqlite", "postgres"):
            raise ValueError("storage.catalog.driver must be 'sqlite' or 'postgres'.")
        driver: CatalogDriver = "postgres" if raw_driver == "postgres" else "sqlite"

        raw_sqlite_path = raw_catalog.get("sqlite_path")
        if raw_sqlite_path is None:
            sqlite_path = default_sqlite
        elif isinstance(raw_sqlite_path, str) and raw_sqlite_path.strip():
            sqlite_path = (config_dir / raw_sqlite_path).resolve()
        else:
            raise ValueError("storage.catalog.sqlite_path must be a nonempty path.")

        raw_pg_env = raw_catalog.get("postgres_dsn_env", DEFAULT_POSTGRES_DSN_ENV)
        if not isinstance(raw_pg_env, str) or not raw_pg_env.strip():
            raise ValueError(
                "storage.catalog.postgres_dsn_env must be a nonempty env var name."
            )

        raw_data_path = raw.get("data_path")
        if raw_data_path is None:
            data_path = default_data_path
        elif isinstance(raw_data_path, str) and raw_data_path.strip():
            data_path = (config_dir / raw_data_path).resolve()
        else:
            raise ValueError("storage.data_path must be a nonempty path.")

        raw_alias = raw.get("catalog_alias", DEFAULT_CATALOG_ALIAS)
        if not isinstance(raw_alias, str):
            raise ValueError("storage.catalog_alias must be a SQL identifier string.")
        catalog_alias = validate_sql_identifier(raw_alias, label="catalog_alias")

        raw_retention = raw.get("retention", {})
        if not isinstance(raw_retention, Mapping):
            raise ValueError("storage.retention must be a YAML mapping.")
        keep_snapshots = raw_retention.get("keep_snapshots", 2)
        min_age = raw_retention.get("min_snapshot_age_seconds", 3600)
        curated_keep_runs = raw_retention.get("curated_keep_runs", 2)
        if type(keep_snapshots) is not int or keep_snapshots < 2:
            raise ValueError(
                "storage.retention.keep_snapshots must be an integer >= 2."
            )
        if type(min_age) is not int or min_age < 0:
            raise ValueError(
                "storage.retention.min_snapshot_age_seconds must be a non-negative integer."
            )
        if type(curated_keep_runs) is not int or curated_keep_runs < 1:
            raise ValueError(
                "storage.retention.curated_keep_runs must be an integer >= 1."
            )

        publish_unmapped = raw.get("publish_unmapped", False)
        if type(publish_unmapped) is not bool:
            raise ValueError("storage.publish_unmapped must be a boolean.")

        override_data_path = raw.get("override_data_path", False)
        if type(override_data_path) is not bool:
            raise ValueError("storage.override_data_path must be a boolean.")

        return cls(
            backend=backend,
            catalog=StorageCatalogConfig(
                driver=driver,
                sqlite_path=sqlite_path,
                postgres_dsn_env=raw_pg_env.strip(),
            ),
            data_path=data_path,
            catalog_alias=catalog_alias,
            retention=StorageRetentionConfig(
                keep_snapshots=keep_snapshots,
                min_snapshot_age_seconds=min_age,
                curated_keep_runs=curated_keep_runs,
            ),
            publish_unmapped=publish_unmapped,
            override_data_path=override_data_path,
        )


def configure_sqlite_catalog(catalog_path: Path) -> None:
    """Initialize SQLite catalog PRAGMAs (WAL, NORMAL, busy_timeout=5000)."""
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(catalog_path), timeout=5)
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
    finally:
        conn.close()


def _parse_postgres_dsn(dsn: str, *, env_name: str) -> dict[str, str]:
    """Parse a PostgreSQL URI or libpq key-value DSN without exposing secrets in errors."""
    stripped = dsn.strip()
    if not stripped:
        raise ContractViolationError(
            f"Environment variable {env_name} is empty; cannot attach Postgres DuckLake catalog."
        )
    params: dict[str, str] = {}
    if stripped.startswith(("postgres://", "postgresql://")):
        parsed = urlparse(stripped)
        if parsed.hostname:
            params["HOST"] = parsed.hostname
        if parsed.port is not None:
            params["PORT"] = str(parsed.port)
        database = parsed.path.lstrip("/")
        if database:
            params["DATABASE"] = unquote(database)
        if parsed.username is not None:
            params["USER"] = unquote(parsed.username)
        if parsed.password is not None:
            params["PASSWORD"] = unquote(parsed.password)
    else:
        key_map = {
            "host": "HOST",
            "port": "PORT",
            "dbname": "DATABASE",
            "database": "DATABASE",
            "user": "USER",
            "password": "PASSWORD",
        }
        try:
            tokens = shlex.split(stripped)
        except ValueError as exc:
            raise ContractViolationError(
                f"Malformed PostgreSQL DSN in environment variable {env_name}."
            ) from exc
        for token in tokens:
            if "=" not in token:
                raise ContractViolationError(
                    f"Malformed PostgreSQL DSN token in environment variable {env_name}."
                )
            key, val = token.split("=", 1)
            mapped = key_map.get(key.strip().lower())
            if mapped is not None and val:
                params[mapped] = val

    required = ("HOST", "DATABASE", "USER")
    missing = [k for k in required if not params.get(k)]
    if missing:
        raise ContractViolationError(
            f"PostgreSQL DSN in {env_name} is missing required fields: {', '.join(missing)}."
        )
    return params


def build_postgres_secret_statements(
    *,
    postgres_dsn_env: str,
    data_path: Path,
    catalog_alias: str = DEFAULT_CATALOG_ALIAS,
    read_only: bool = False,
    override_data_path: bool = False,
) -> tuple[str, str, str]:
    """Build DuckDB ``CREATE SECRET`` + ``ATTACH`` SQL for a PostgreSQL DuckLake catalog.

    Credentials are read strictly from ``postgres_dsn_env`` and never embedded into the
    ``ATTACH`` string or logged on failure.
    """
    alias = validate_sql_identifier(catalog_alias, label="catalog_alias")
    raw_dsn = os.environ.get(postgres_dsn_env)
    if not raw_dsn:
        raise ContractViolationError(
            f"Environment variable {postgres_dsn_env} is not set for PostgreSQL DuckLake catalog."
        )
    params = _parse_postgres_dsn(raw_dsn, env_name=postgres_dsn_env)

    pg_secret_name = f"_finintel_pg_{alias}"
    dl_secret_name = f"_finintel_dl_{alias}"

    secret_fields = [
        "TYPE postgres",
        f"HOST '{_escape_sql_literal(params['HOST'])}'",
        f"DATABASE '{_escape_sql_literal(params['DATABASE'])}'",
        f"USER '{_escape_sql_literal(params['USER'])}'",
    ]
    if "PORT" in params:
        try:
            port_int = int(params["PORT"])
        except ValueError as exc:
            raise ContractViolationError(
                f"Invalid port in PostgreSQL DSN environment variable {postgres_dsn_env}."
            ) from exc
        secret_fields.append(f"PORT {port_int}")
    if "PASSWORD" in params:
        secret_fields.append(f"PASSWORD '{_escape_sql_literal(params['PASSWORD'])}'")

    create_pg_secret = (
        f"CREATE OR REPLACE SECRET {pg_secret_name} ({', '.join(secret_fields)});"
    )
    data_sql = _escape_sql_literal(data_path.resolve().as_posix())
    create_dl_secret = (
        f"CREATE OR REPLACE SECRET {dl_secret_name} ("
        "TYPE ducklake, "
        "METADATA_PATH '', "
        f"DATA_PATH '{data_sql}', "
        f"METADATA_PARAMETERS MAP {{'TYPE': 'postgres', 'SECRET': '{pg_secret_name}'}}"
        ");"
    )
    attach_options: list[str] = []
    if override_data_path:
        attach_options.append("OVERRIDE_DATA_PATH true")
    if read_only:
        attach_options.append("READ_ONLY")
    attach_opts = f" ({', '.join(attach_options)})" if attach_options else ""
    attach_stmt = f"ATTACH 'ducklake:{dl_secret_name}' AS \"{alias}\"{attach_opts};"
    return create_pg_secret, create_dl_secret, attach_stmt


def ensure_ducklake_attached(
    connection: duckdb.DuckDBPyConnection,
    *,
    catalog_path: Path,
    data_path: Path,
    catalog_alias: str = DEFAULT_CATALOG_ALIAS,
    catalog_driver: CatalogDriver = "sqlite",
    postgres_dsn_env: str = DEFAULT_POSTGRES_DSN_ENV,
    read_only: bool = True,
    override_data_path: bool = False,
) -> None:
    """Attach the DuckLake catalog onto ``connection`` if not already attached."""
    alias = validate_sql_identifier(catalog_alias, label="catalog_alias")
    existing = connection.execute(
        "SELECT database_name FROM duckdb_databases() WHERE database_name = ?",
        [alias],
    ).fetchone()
    if existing is not None:
        return

    effective_override = _resolve_override_data_path(override_data_path)
    connection.execute("LOAD ducklake;")
    if catalog_driver == "postgres":
        connection.execute("LOAD postgres;")
        stmts = build_postgres_secret_statements(
            postgres_dsn_env=postgres_dsn_env,
            data_path=data_path,
            catalog_alias=alias,
            read_only=read_only,
            override_data_path=effective_override,
        )
        try:
            for stmt in stmts:
                connection.execute(stmt)
        except duckdb.Error:
            # Suppress DuckDB error text so CREATE SECRET credentials never appear in tracebacks.
            raise ContractViolationError(
                f"Failed to attach PostgreSQL DuckLake catalog configured via {postgres_dsn_env}."
            ) from None
        return

    connection.execute("LOAD sqlite;")
    cat_sql = _escape_sql_literal(catalog_path.resolve().as_posix())
    data_sql = _escape_sql_literal(data_path.resolve().as_posix())
    sqlite_attach_options = [f"DATA_PATH '{data_sql}'"]
    if effective_override:
        sqlite_attach_options.append("OVERRIDE_DATA_PATH true")
    if read_only:
        sqlite_attach_options.append("READ_ONLY")
    opts = ", ".join(sqlite_attach_options)
    connection.execute(f"ATTACH 'ducklake:sqlite:{cat_sql}' AS \"{alias}\" ({opts});")


class DuckLakeStore:
    """ACID multi-table DuckLake publisher with cross-DB consistency, rollback, and maintenance."""

    def __init__(
        self,
        catalog_path: Path,
        data_path: Path,
        *,
        state_store: StateStore,
        catalog_alias: str = DEFAULT_CATALOG_ALIAS,
        catalog_driver: CatalogDriver = "sqlite",
        postgres_dsn_env: str = DEFAULT_POSTGRES_DSN_ENV,
        keep_snapshots: int = 2,
        min_snapshot_age_seconds: int = 3600,
        lock_path: Path | None = None,
        override_data_path: bool = False,
    ) -> None:
        if type(keep_snapshots) is not int or keep_snapshots < 2:
            raise ValueError("keep_snapshots must be an integer >= 2.")
        if type(min_snapshot_age_seconds) is not int or min_snapshot_age_seconds < 0:
            raise ValueError("min_snapshot_age_seconds must be a non-negative integer.")
        self.catalog_path = catalog_path.resolve()
        self.data_path = data_path.resolve()
        self.state_store = state_store
        self.catalog_alias = validate_sql_identifier(
            catalog_alias, label="catalog_alias"
        )
        self.catalog_driver = catalog_driver
        self.postgres_dsn_env = postgres_dsn_env
        self.keep_snapshots = keep_snapshots
        self.min_snapshot_age_seconds = min_snapshot_age_seconds
        self.lock_path = (
            lock_path.resolve()
            if lock_path is not None
            else state_store.path.with_name("pipeline.lock").resolve()
        )
        self.override_data_path = override_data_path
        self._set_sorted_by_supported: bool | None = None

    @classmethod
    def from_config(
        cls, config: StorageConfig, *, state_store: StateStore
    ) -> DuckLakeStore:
        """Build a ``DuckLakeStore`` directly from a ``StorageConfig``."""
        return cls(
            catalog_path=config.catalog.sqlite_path,
            data_path=config.data_path,
            state_store=state_store,
            catalog_alias=config.catalog_alias,
            catalog_driver=config.catalog.driver,
            postgres_dsn_env=config.catalog.postgres_dsn_env,
            keep_snapshots=config.retention.keep_snapshots,
            min_snapshot_age_seconds=config.retention.min_snapshot_age_seconds,
            override_data_path=config.override_data_path,
        )

    def _open_rw_connection(self) -> duckdb.DuckDBPyConnection:
        self.data_path.mkdir(parents=True, exist_ok=True)
        if self.catalog_driver == "sqlite":
            configure_sqlite_catalog(self.catalog_path)
        con = duckdb.connect(":memory:")
        try:
            ensure_ducklake_attached(
                con,
                catalog_path=self.catalog_path,
                data_path=self.data_path,
                catalog_alias=self.catalog_alias,
                catalog_driver=self.catalog_driver,
                postgres_dsn_env=self.postgres_dsn_env,
                read_only=False,
                override_data_path=self.override_data_path,
            )
            return con
        except BaseException:
            con.close()
            raise

    def _ensure_audit_table(self, con: duckdb.DuckDBPyConnection) -> None:
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{self.catalog_alias}"."{SNAPSHOT_AUDIT_TABLE}" (
                snapshot_id VARCHAR NOT NULL,
                ducklake_version BIGINT NOT NULL,
                run_id VARCHAR NOT NULL,
                published_at VARCHAR NOT NULL,
                tables_json VARCHAR NOT NULL,
                metadata_json VARCHAR
            )
            """
        )
        col_rows = con.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_catalog = ? AND table_schema = 'main' AND table_name = ?
            """,
            [self.catalog_alias, SNAPSHOT_AUDIT_TABLE],
        ).fetchall()
        existing_cols = {str(row[0]) for row in col_rows}
        if "metadata_json" not in existing_cols:
            con.execute(
                f'ALTER TABLE "{self.catalog_alias}"."{SNAPSHOT_AUDIT_TABLE}" '
                "ADD COLUMN metadata_json VARCHAR;"
            )

    def _existing_user_tables(self, con: duckdb.DuckDBPyConnection) -> set[str]:
        rows = con.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_catalog = ? AND table_schema = 'main'
            """,
            [self.catalog_alias],
        ).fetchall()
        return {
            str(row[0])
            for row in rows
            if str(row[0]) != SNAPSHOT_AUDIT_TABLE
            and str(row[0]) in KNOWN_CATALOG_TABLES
        }

    def _apply_set_sorted_by_best_effort(
        self,
        con: duckdb.DuckDBPyConnection,
        tables: Mapping[str, pl.DataFrame],
    ) -> None:
        """Apply ``ALTER TABLE ... SET SORTED BY`` outside the publish tx if supported.

        DuckLake 0.3 raises ``BinderException`` on ``SET SORTED BY``; probing once
        outside the atomic publish transaction ensures we never abort a commit while
        automatically enabling native sorted compaction on DuckLake 0.4+.
        """
        if self._set_sorted_by_supported is False:
            return
        for filename, frame in tables.items():
            layout = TABLE_PHYSICAL_LAYOUT.get(filename)
            if layout is None:
                continue
            sort_cols = [col for col in layout.sort_by if col in frame.columns]
            if not sort_cols:
                continue
            tbl = filename.removesuffix(".parquet")
            cols_sql = ", ".join(f'"{col}"' for col in sort_cols)
            try:
                con.execute(
                    f'ALTER TABLE "{self.catalog_alias}"."{tbl}" SET SORTED BY ({cols_sql});'
                )
                self._set_sorted_by_supported = True
            except (duckdb.BinderException, duckdb.NotImplementedException):
                self._set_sorted_by_supported = False
                logger.debug(
                    "DuckLake catalog does not support ALTER TABLE SET SORTED BY; "
                    "relying on explicit SQL ORDER BY sorting.",
                    extra={"event": "ducklake_set_sorted_by_unsupported"},
                )
                return

    def _prepare_all_tables(
        self, tables: Mapping[str, TableInput]
    ) -> dict[str, pl.DataFrame]:
        if not tables:
            raise ValueError("A snapshot must contain at least one table.")
        prepared: dict[str, pl.DataFrame] = {}
        for filename, raw_table in tables.items():
            if filename not in KNOWN_TABLES:
                raise ValueError(
                    f"Table {filename!r} is not in KNOWN_TABLES allowlist."
                )
            if isinstance(raw_table, pl.DataFrame):
                frame = raw_table
            else:
                source = raw_table.resolve()
                if not source.is_file():
                    raise FileNotFoundError(source)
                try:
                    frame = pl.read_parquet(source)
                except (OSError, pl.exceptions.PolarsError) as exc:
                    raise ContractViolationError(
                        f"Unable to read Parquet table {filename}."
                    ) from exc

            validated = prepare_storage_table(filename, frame)
            if validated.is_empty() and filename not in _ALLOW_EMPTY_TABLES:
                raise ValueError(f"Snapshot table is empty: {filename}")
            prepared[filename] = validated
        return prepared

    def publish_snapshot(
        self,
        snapshot_id: str,
        tables: Mapping[str, TableInput],
        *,
        run_id: str,
        metadata: Mapping[str, object] | None = None,
    ) -> int:
        """Atomically publish all tables in 1 DuckLake transaction, then promote in ``state.sqlite``."""
        if (
            not snapshot_id.startswith("snap_")
            or Path(snapshot_id).name != snapshot_id
            or ":" in snapshot_id
        ):
            raise ValueError(
                "snapshot_id must be a path-safe value beginning with 'snap_'."
            )
        if not run_id or not isinstance(run_id, str):
            raise ValueError("run_id is required when publishing to DuckLake.")

        # Pre-validation BEFORE opening any DuckLake transaction.
        prepared = self._prepare_all_tables(tables)
        run_row = self.state_store.get_run(run_id)
        if run_row is None:
            raise ValueError(f"Unknown pipeline run: {run_id}")
        if run_row.get("status") != "RUNNING":
            raise ValueError(
                f"Pipeline run {run_id} is not publishable from status {run_row.get('status')}."
            )

        with pipeline_write_lock(self.lock_path):
            # Re-verify run status inside the write lock to avoid TOCTOU orphan commits.
            locked_run_row = self.state_store.get_run(run_id)
            if locked_run_row is None:
                raise ValueError(f"Unknown pipeline run: {run_id}")
            if locked_run_row.get("status") != "RUNNING":
                raise ValueError(
                    f"Pipeline run {run_id} is not publishable from status {locked_run_row.get('status')}."
                )

            audit_meta: dict[str, object] = (
                dict(metadata) if metadata is not None else {}
            )
            audit_meta.setdefault("run_id", run_id)
            if audit_meta.get("git_commit_sha") is None and isinstance(
                locked_run_row.get("git_commit_sha"), str
            ):
                audit_meta["git_commit_sha"] = locked_run_row["git_commit_sha"]
            raw_cli_args = locked_run_row.get("cli_args_json")
            if audit_meta.get("dagster_run_id") is None and isinstance(
                raw_cli_args, str
            ):
                try:
                    parsed_args = json.loads(raw_cli_args)
                except ValueError:
                    parsed_args = None
                if isinstance(parsed_args, dict) and isinstance(
                    parsed_args.get("dagster_run_id"), str
                ):
                    audit_meta["dagster_run_id"] = parsed_args["dagster_run_id"]
            metadata_json = json.dumps(
                audit_meta, ensure_ascii=False, sort_keys=True, default=str
            )

            con = self._open_rw_connection()
            registered_views: list[str] = []
            try:
                self._ensure_audit_table(con)

                duplicate = con.execute(
                    f'SELECT 1 FROM "{self.catalog_alias}"."{SNAPSHOT_AUDIT_TABLE}" '
                    "WHERE snapshot_id = ? LIMIT 1",
                    [snapshot_id],
                ).fetchone()
                if duplicate is not None:
                    raise FileExistsError(
                        f"Snapshot already exists in DuckLake audit table: {snapshot_id}"
                    )

                existing_tables = self._existing_user_tables(con)
                pre_row = con.execute(
                    f"SELECT max(snapshot_id) FROM ducklake_snapshots('{self.catalog_alias}')"
                ).fetchone()
                if pre_row is None or pre_row[0] is None:
                    raise ContractViolationError(
                        "Unable to determine current DuckLake snapshot version."
                    )
                expected_version = int(pre_row[0]) + 1
                published_at = datetime.now(UTC).isoformat()
                tables_json = json.dumps(sorted(prepared), ensure_ascii=False)

                for filename, frame in prepared.items():
                    tbl = filename.removesuffix(".parquet")
                    view_name = f"_finintel_stage_{tbl}"
                    con.register(view_name, frame)
                    registered_views.append(view_name)

                # Phase 1: Single Atomic DuckLake Multi-Table Commit.
                con.execute("BEGIN TRANSACTION;")
                try:
                    new_catalog_tables: set[str] = set()
                    for filename, frame in prepared.items():
                        tbl = filename.removesuffix(".parquet")
                        new_catalog_tables.add(tbl)
                        view_name = f"_finintel_stage_{tbl}"
                        rg_size = row_group_size_for_table(filename)
                        con.execute(
                            f'CALL "{self.catalog_alias}".set_option('
                            f"'parquet_row_group_size', {rg_size});"
                        )
                        if frame.is_empty():
                            con.execute(
                                f'CREATE OR REPLACE TABLE "{self.catalog_alias}"."{tbl}" '
                                f'AS SELECT * FROM "{view_name}" WHERE 1 = 0;'
                            )
                        else:
                            layout = TABLE_PHYSICAL_LAYOUT.get(filename)
                            sort_cols = (
                                [c for c in layout.sort_by if c in frame.columns]
                                if layout is not None
                                else []
                            )
                            order_sql = (
                                " ORDER BY "
                                + ", ".join(f'"{c}" NULLS LAST' for c in sort_cols)
                                if sort_cols
                                else ""
                            )
                            con.execute(
                                f'CREATE OR REPLACE TABLE "{self.catalog_alias}"."{tbl}" '
                                f'AS SELECT * FROM "{view_name}"{order_sql};'
                            )

                    stale_tables = sorted(existing_tables - new_catalog_tables)
                    for stale_tbl in stale_tables:
                        con.execute(
                            f'DROP TABLE IF EXISTS "{self.catalog_alias}"."{stale_tbl}";'
                        )

                    con.execute(
                        f'INSERT INTO "{self.catalog_alias}"."{SNAPSHOT_AUDIT_TABLE}" '
                        "(snapshot_id, ducklake_version, run_id, published_at, tables_json, metadata_json) "
                        "VALUES (?, ?, ?, ?, ?, ?);",
                        [
                            snapshot_id,
                            expected_version,
                            run_id,
                            published_at,
                            tables_json,
                            metadata_json,
                        ],
                    )
                    con.execute("COMMIT;")
                except BaseException:
                    try:
                        con.execute("ROLLBACK;")
                    except Exception:
                        pass
                    raise

                actual_row = con.execute(
                    f"SELECT * FROM ducklake_last_committed_snapshot('{self.catalog_alias}')"
                ).fetchone()
                if actual_row is None or int(actual_row[0]) != expected_version:
                    raise ContractViolationError(
                        "DuckLake committed snapshot version diverged from audited version."
                    )

                self._apply_set_sorted_by_best_effort(con, prepared)
            finally:
                for view_name in registered_views:
                    try:
                        con.unregister(view_name)
                    except Exception:
                        pass
                con.close()

            # Phase 2: Promote active snapshot pointer in state.sqlite.
            self.state_store.publish_snapshot(run_id, snapshot_id)
            return expected_version

    publish = publish_snapshot

    def reconcile_unpromoted_snapshots(self) -> list[dict[str, object]]:
        """Detect orphan ``_snapshot_audit`` rows committed in Phase 1 but not promoted in ``state.sqlite``.

        Logs audit warnings and leaves physical cleanup to ``maintain()`` after the grace period.
        """
        if self.catalog_driver == "sqlite" and not self.catalog_path.is_file():
            return []
        with pipeline_write_lock(self.lock_path):
            promoted = set(self.state_store.published_snapshot_ids())
            active = self.state_store.active_snapshot()
            if active is not None and active.get("snapshot_id") is not None:
                promoted.add(str(active["snapshot_id"]))

            con = self._open_rw_connection()
            try:
                self._ensure_audit_table(con)
                live_versions = {
                    int(row[0])
                    for row in con.execute(
                        f"SELECT snapshot_id FROM ducklake_snapshots('{self.catalog_alias}')"
                    ).fetchall()
                }
                rows = con.execute(
                    f"""
                    SELECT snapshot_id, ducklake_version, run_id, published_at, tables_json, metadata_json
                    FROM "{self.catalog_alias}"."{SNAPSHOT_AUDIT_TABLE}"
                    ORDER BY ducklake_version ASC
                    """
                ).fetchall()
            finally:
                con.close()

            orphans: list[dict[str, object]] = []
            for (
                snap_id,
                version,
                run_id,
                published_at,
                tables_json,
                metadata_json,
            ) in rows:
                sid = str(snap_id)
                ver_int = int(version)
                if sid not in promoted and ver_int in live_versions:
                    record: dict[str, object] = {
                        "snapshot_id": sid,
                        "ducklake_version": ver_int,
                        "run_id": str(run_id),
                        "published_at": str(published_at),
                        "tables_json": str(tables_json),
                        "metadata_json": (
                            str(metadata_json) if metadata_json is not None else None
                        ),
                    }
                    orphans.append(record)
                    logger.warning(
                        "Detected unpromoted DuckLake snapshot in _snapshot_audit",
                        extra={
                            "event": "ducklake_unpromoted_snapshot",
                            "run_id": str(run_id),
                            "details": {
                                "snapshot_id": sid,
                                "ducklake_version": ver_int,
                            },
                        },
                    )
            return orphans

    def rollback_snapshot(
        self, target_snapshot_id: str, state: StateStore | None = None
    ) -> int:
        """Atomically roll ``state.sqlite.active_snapshot`` back to a retained DuckLake snapshot."""
        if (
            not target_snapshot_id.startswith("snap_")
            or Path(target_snapshot_id).name != target_snapshot_id
            or ":" in target_snapshot_id
        ):
            raise ValueError(
                "target_snapshot_id must be a path-safe value beginning with 'snap_'."
            )
        target_state = state if state is not None else self.state_store
        with pipeline_write_lock(self.lock_path):
            con = self._open_rw_connection()
            try:
                self._ensure_audit_table(con)
                audit_row = con.execute(
                    f"""
                    SELECT ducklake_version, run_id, published_at
                    FROM "{self.catalog_alias}"."{SNAPSHOT_AUDIT_TABLE}"
                    WHERE snapshot_id = ?
                    ORDER BY ducklake_version DESC
                    LIMIT 1
                    """,
                    [target_snapshot_id],
                ).fetchone()
                if audit_row is None:
                    raise ValueError(
                        f"Target snapshot {target_snapshot_id!r} not found in DuckLake audit table."
                    )
                target_version = int(audit_row[0])
                target_run_id = str(audit_row[1])
                live_versions = {
                    int(row[0])
                    for row in con.execute(
                        f"SELECT snapshot_id FROM ducklake_snapshots('{self.catalog_alias}')"
                    ).fetchall()
                }
                if target_version not in live_versions:
                    raise ContractViolationError(
                        f"Target snapshot {target_snapshot_id!r} (version {target_version}) "
                        "has already expired in DuckLake."
                    )
            finally:
                con.close()

            now = datetime.now(UTC).isoformat()
            with target_state.connection() as sconn:
                sconn.execute("BEGIN IMMEDIATE")
                run_exists = sconn.execute(
                    "SELECT 1 FROM pipeline_runs WHERE run_id = ?",
                    (target_run_id,),
                ).fetchone()
                if run_exists is None:
                    raise ContractViolationError(
                        f"Target snapshot run_id {target_run_id!r} is missing from pipeline_runs."
                    )
                sconn.execute(
                    """
                    INSERT INTO active_snapshot (singleton, snapshot_id, run_id, published_at)
                    VALUES (1, ?, ?, ?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        snapshot_id = excluded.snapshot_id,
                        run_id = excluded.run_id,
                        published_at = excluded.published_at
                    """,
                    (target_snapshot_id, target_run_id, now),
                )
            return target_version

    def maintain(
        self,
        *,
        keep_snapshots: int | None = None,
        min_snapshot_age_seconds: int | None = None,
    ) -> dict[str, object]:
        """Run DuckLake flush, adjacent file merge, explicit ``versions`` expiry, and file cleanup."""
        keep = self.keep_snapshots if keep_snapshots is None else keep_snapshots
        min_age = (
            self.min_snapshot_age_seconds
            if min_snapshot_age_seconds is None
            else min_snapshot_age_seconds
        )
        if type(keep) is not int or keep < 2:
            raise ValueError("keep_snapshots must be an integer >= 2.")
        if type(min_age) is not int or min_age < 0:
            raise ValueError("min_snapshot_age_seconds must be a non-negative integer.")

        with pipeline_write_lock(self.lock_path):
            con = self._open_rw_connection()
            try:
                self._ensure_audit_table(con)

                # 1. Flush inlined data if supported by the attached catalog DBMS.
                try:
                    con.execute(
                        f"CALL ducklake_flush_inlined_data('{self.catalog_alias}');"
                    )
                except (duckdb.NotImplementedException, duckdb.CatalogException):
                    logger.debug(
                        "ducklake_flush_inlined_data skipped for catalog driver %s",
                        self.catalog_driver,
                    )

                # 2. Compact small adjacent Parquet files.
                con.execute(
                    f"CALL ducklake_merge_adjacent_files('{self.catalog_alias}');"
                )

                # 3. Compute explicit BIGINT[] versions list to expire (MISS-02):
                #    NEVER pass keep_snapshots to versions; NEVER combine versions + older_than.
                snap_rows = con.execute(
                    f"SELECT snapshot_id, snapshot_time "
                    f"FROM ducklake_snapshots('{self.catalog_alias}') "
                    "ORDER BY snapshot_id ASC"
                ).fetchall()
                audit_rows = con.execute(
                    f"SELECT snapshot_id, ducklake_version "
                    f'FROM "{self.catalog_alias}"."{SNAPSHOT_AUDIT_TABLE}" '
                    "ORDER BY ducklake_version DESC"
                ).fetchall()

                audit_by_snap_id = {str(row[0]): int(row[1]) for row in audit_rows}
                promoted_set = set(self.state_store.published_snapshot_ids())
                active = self.state_store.active_snapshot()

                protected_versions: set[int] = set()
                if active is not None and active.get("snapshot_id") is not None:
                    active_sid = str(active["snapshot_id"])
                    if active_sid in audit_by_snap_id:
                        protected_versions.add(audit_by_snap_id[active_sid])

                promoted_audit_versions = [
                    int(row[1]) for row in audit_rows if str(row[0]) in promoted_set
                ]
                protected_versions.update(promoted_audit_versions[:keep])

                all_snap_ids = [int(row[0]) for row in snap_rows]
                if all_snap_ids:
                    # DuckLake always requires keeping the current HEAD snapshot.
                    protected_versions.add(all_snap_ids[-1])
                if not promoted_audit_versions:
                    protected_versions.update(all_snap_ids[-keep:])

                cutoff_utc = datetime.now(UTC) - timedelta(seconds=min_age)
                versions_to_expire = [
                    int(row[0])
                    for row in snap_rows
                    if int(row[0]) not in protected_versions
                    and _to_utc(row[1]) < cutoff_utc
                ]

                if versions_to_expire:
                    versions_sql = ", ".join(str(v) for v in versions_to_expire)
                    con.execute(
                        f"CALL ducklake_expire_snapshots("
                        f"'{self.catalog_alias}', versions => [{versions_sql}]);"
                    )

                # 4. Clean up scheduled-for-deletion files.
                if min_age == 0:
                    cleaned_rows = con.execute(
                        f"CALL ducklake_cleanup_old_files("
                        f"'{self.catalog_alias}', cleanup_all => true);"
                    ).fetchall()
                else:
                    cleaned_rows = con.execute(
                        f"CALL ducklake_cleanup_old_files("
                        f"'{self.catalog_alias}', "
                        f"older_than => now() - INTERVAL '{min_age} seconds');"
                    ).fetchall()

                remaining_versions = [
                    int(row[0])
                    for row in con.execute(
                        f"SELECT snapshot_id FROM ducklake_snapshots('{self.catalog_alias}') "
                        "ORDER BY snapshot_id ASC"
                    ).fetchall()
                ]
            finally:
                con.close()

            return {
                "expired_versions": versions_to_expire,
                "cleaned_files": [str(row[0]) for row in cleaned_rows],
                "remaining_versions": remaining_versions,
                "protected_versions": sorted(protected_versions),
            }
