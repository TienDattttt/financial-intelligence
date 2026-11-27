"""Cloudflare R2 / S3 object store configuration and DuckDB httpfs adapter.

Verified Sources:
- DuckDB Secrets Manager & httpfs S3/R2 API (``duckdb==1.4.2``):
  https://duckdb.org/docs/stable/configuration/secrets_manager
  https://duckdb.org/docs/stable/core_extensions/httpfs/s3api
- Cloudflare R2 S3 API Compatibility (``region="auto"``, ``https://<account_id>.r2.cloudflarestorage.com``):
  https://developers.cloudflare.com/r2/api/s3/api/

Guardrails:
- Pure standard-library + ``duckdb``; never requires ``boto3`` at import time so CI
  ``mvp-checks`` (``--group ml --dev``) and offline unit tests run with zero network.
- Never stores, returns in ``__repr__``, logs, or leaks credentials in exceptions.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import duckdb

from finintel.exceptions import ContractViolationError

logger = logging.getLogger("finintel.storage.object_store")

ObjectStoreBackend = Literal["local", "r2", "s3"]
ObjectStoreUrlStyle = Literal["path", "vhost"]
DuckDBSecretType = Literal["auto", "r2", "s3"]

DEFAULT_S3_ACCESS_KEY_ENV = "FININTEL_S3_ACCESS_KEY_ID"
DEFAULT_S3_SECRET_KEY_ENV = "FININTEL_S3_SECRET_ACCESS_KEY"
DEFAULT_OBJECT_PREFIX = "finintel/lakehouse"

_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_PREFIX_RE = re.compile(r"^[a-zA-Z0-9._/-]*$")
_REGION_RE = re.compile(r"^[a-zA-Z0-9-]+$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_R2_HOST_RE = re.compile(r"^(?P<account_id>[a-zA-Z0-9-]+)\.r2\.cloudflarestorage\.com$")


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _validate_identifier(value: str, *, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"Invalid SQL {label}: {value!r}")
    return value


def _normalize_prefix(raw_prefix: str) -> str:
    if not isinstance(raw_prefix, str):
        raise ValueError("object_store.prefix must be a string.")
    cleaned = raw_prefix.strip().strip("/")
    if not cleaned:
        return ""
    if ".." in cleaned.split("/") or _PREFIX_RE.fullmatch(cleaned) is None:
        raise ValueError(
            "object_store.prefix must be a safe relative path without '..' or quotes."
        )
    return cleaned


def _normalize_endpoint_url(raw_url: str | None, *, use_ssl: bool) -> str | None:
    if raw_url is None:
        return None
    stripped = raw_url.strip().rstrip("/")
    if not stripped:
        return None
    parsed = urlparse(stripped)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("object_store.endpoint_url must not contain userinfo.")
    allowed_schemes = {"https"} if use_ssl else {"http", "https"}
    if parsed.scheme not in allowed_schemes:
        expected = "HTTPS" if use_ssl else "HTTP/HTTPS"
        raise ValueError(
            f"object_store.endpoint_url must use {expected} when use_ssl={use_ssl}."
        )
    if (
        not parsed.netloc
        or (parsed.path and parsed.path != "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "object_store.endpoint_url must be a clean scheme://host[:port] URL."
        )
    return f"{parsed.scheme}://{parsed.netloc}"


def _parse_bool_env(raw: str | None, *, default: bool, name: str) -> bool:
    if raw is None or not raw.strip():
        return default
    norm = raw.strip().lower()
    if norm in {"1", "true", "yes", "on"}:
        return True
    if norm in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value, got {raw!r}.")


@dataclass(frozen=True)
class ObjectStoreConfig:
    """Typed object storage configuration for Cloudflare R2, S3, or local disk."""

    backend: ObjectStoreBackend = "local"
    bucket: str | None = None
    prefix: str = DEFAULT_OBJECT_PREFIX
    endpoint_url: str | None = None
    region: str = "auto"
    use_ssl: bool = True
    access_key_id_env: str = DEFAULT_S3_ACCESS_KEY_ENV
    secret_access_key_env: str = DEFAULT_S3_SECRET_KEY_ENV
    url_style: ObjectStoreUrlStyle = "path"

    def __post_init__(self) -> None:
        if self.backend not in ("local", "r2", "s3"):
            raise ValueError("object_store.backend must be 'local', 'r2', or 's3'.")
        if type(self.use_ssl) is not bool:
            raise ValueError("object_store.use_ssl must be a boolean.")
        if self.url_style not in ("path", "vhost"):
            raise ValueError("object_store.url_style must be 'path' or 'vhost'.")

        norm_prefix = _normalize_prefix(self.prefix)
        object.__setattr__(self, "prefix", norm_prefix)

        if (
            not isinstance(self.access_key_id_env, str)
            or _ENV_NAME_RE.fullmatch(self.access_key_id_env.strip()) is None
        ):
            raise ValueError(
                "object_store.access_key_id_env must be a valid env var name."
            )
        if (
            not isinstance(self.secret_access_key_env, str)
            or _ENV_NAME_RE.fullmatch(self.secret_access_key_env.strip()) is None
        ):
            raise ValueError(
                "object_store.secret_access_key_env must be a valid env var name."
            )
        object.__setattr__(self, "access_key_id_env", self.access_key_id_env.strip())
        object.__setattr__(
            self, "secret_access_key_env", self.secret_access_key_env.strip()
        )

        if (
            not isinstance(self.region, str)
            or _REGION_RE.fullmatch(self.region.strip()) is None
        ):
            raise ValueError(
                "object_store.region must be a non-empty alphanumeric slug."
            )
        clean_region = self.region.strip()
        object.__setattr__(self, "region", clean_region)

        norm_endpoint = _normalize_endpoint_url(self.endpoint_url, use_ssl=self.use_ssl)
        object.__setattr__(self, "endpoint_url", norm_endpoint)

        if self.backend == "local":
            if self.bucket is not None:
                clean_bucket = self.bucket.strip() or None
                object.__setattr__(self, "bucket", clean_bucket)
            return

        if (
            not isinstance(self.bucket, str)
            or _BUCKET_RE.fullmatch(self.bucket.strip()) is None
        ):
            raise ValueError(
                "object_store.bucket must be a valid S3/R2 bucket name when backend is 'r2' or 's3'."
            )
        object.__setattr__(self, "bucket", self.bucket.strip())

        if self.backend == "r2":
            if norm_endpoint is None:
                raise ValueError(
                    "object_store.endpoint_url is required when backend='r2'."
                )
            if clean_region != "auto":
                raise ValueError(
                    "object_store.region must be 'auto' for Cloudflare R2 (backend='r2')."
                )

    def __repr__(self) -> str:
        return (
            "ObjectStoreConfig("
            f"backend={self.backend!r}, "
            f"bucket={self.bucket!r}, "
            f"prefix={self.prefix!r}, "
            f"endpoint_url={self.endpoint_url!r}, "
            f"region={self.region!r}, "
            f"use_ssl={self.use_ssl!r}, "
            f"access_key_id_env={self.access_key_id_env!r}, "
            f"secret_access_key_env={self.secret_access_key_env!r}, "
            f"url_style={self.url_style!r}, "
            "credentials='***REDACTED***')"
        )

    @property
    def is_remote(self) -> bool:
        """Return True when DuckLake/Parquet objects live on R2 or S3."""
        return self.backend in ("r2", "s3")

    def r2_account_id(self) -> str | None:
        """Extract the Cloudflare R2 account ID from ``endpoint_url`` if present."""
        if self.endpoint_url is None:
            return None
        host = (urlparse(self.endpoint_url).hostname or "").lower()
        match = _R2_HOST_RE.fullmatch(host)
        if match is None:
            return None
        return match.group("account_id")

    def duckdb_data_path(self, local_data_path: Path | None = None) -> str:
        """Return the DuckLake ``DATA_PATH`` (``s3://bucket/prefix`` or POSIX local path)."""
        if not self.is_remote:
            if local_data_path is None:
                raise ValueError("local_data_path is required when backend='local'.")
            return local_data_path.resolve().as_posix()
        assert self.bucket is not None
        if self.prefix:
            return f"s3://{self.bucket}/{self.prefix}"
        return f"s3://{self.bucket}"

    def s3_key(self, relative_path: str) -> str:
        """Build a normalized object key under ``prefix`` without leading slashes."""
        clean_rel = relative_path.strip().lstrip("/")
        if ".." in clean_rel.split("/"):
            raise ValueError("relative_path must not contain '..'.")
        if not self.prefix:
            return clean_rel
        if not clean_rel:
            return self.prefix
        return f"{self.prefix}/{clean_rel}"

    def resolve_credentials(self) -> tuple[str, str]:
        """Read S3/R2 credentials strictly from environment variables (never logged)."""
        key_id = os.environ.get(self.access_key_id_env, "").strip()
        if not key_id:
            raise ContractViolationError(
                f"Environment variable {self.access_key_id_env} is not set for object store access."
            )
        secret_key = os.environ.get(self.secret_access_key_env, "").strip()
        if not secret_key:
            raise ContractViolationError(
                f"Environment variable {self.secret_access_key_env} is not set for object store access."
            )
        return key_id, secret_key

    def redacted_summary(self) -> dict[str, object]:
        """Return a log-safe dictionary representation with all credentials redacted."""
        return {
            "backend": self.backend,
            "bucket": self.bucket,
            "prefix": self.prefix,
            "endpoint_url": self.endpoint_url,
            "region": self.region,
            "use_ssl": self.use_ssl,
            "url_style": self.url_style,
            "access_key_id_env": self.access_key_id_env,
            "secret_access_key_env": self.secret_access_key_env,
            "credentials_configured": bool(
                os.environ.get(self.access_key_id_env, "").strip()
                and os.environ.get(self.secret_access_key_env, "").strip()
            ),
            "credentials": "***REDACTED***",
        }

    def build_duckdb_secret_sql(
        self,
        *,
        secret_name: str = "_finintel_object_store",
        secret_type: DuckDBSecretType = "auto",
    ) -> str:
        """Generate DuckDB ``CREATE OR REPLACE SECRET`` SQL for R2 or S3 without logging secrets."""
        if not self.is_remote:
            raise ValueError(
                "Cannot build DuckDB remote secret SQL for local object store."
            )
        assert self.bucket is not None
        safe_name = _validate_identifier(secret_name, label="secret_name")
        if secret_type not in ("auto", "r2", "s3"):
            raise ValueError("secret_type must be 'auto', 'r2', or 's3'.")

        key_id, secret_key = self.resolve_credentials()
        esc_key_id = _escape_sql_literal(key_id)
        esc_secret = _escape_sql_literal(secret_key)

        if secret_type == "r2":
            if self.backend != "r2":
                raise ValueError("secret_type='r2' requires backend='r2'.")
            account_id = self.r2_account_id()
            if not account_id:
                raise ValueError(
                    "Unable to derive R2 account_id from endpoint_url for TYPE R2 secret."
                )
            fields = [
                "TYPE R2",
                f"KEY_ID '{esc_key_id}'",
                f"SECRET '{esc_secret}'",
                f"ACCOUNT_ID '{_escape_sql_literal(account_id)}'",
                f"SCOPE 'r2://{_escape_sql_literal(self.bucket)}'",
            ]
            return f"CREATE OR REPLACE SECRET {safe_name} ({', '.join(fields)});"

        fields = [
            "TYPE S3",
            f"KEY_ID '{esc_key_id}'",
            f"SECRET '{esc_secret}'",
            f"REGION '{_escape_sql_literal(self.region)}'",
            f"URL_STYLE '{self.url_style}'",
            f"USE_SSL {'true' if self.use_ssl else 'false'}",
            f"SCOPE 's3://{_escape_sql_literal(self.bucket)}'",
        ]
        if self.endpoint_url is not None:
            parsed = urlparse(self.endpoint_url)
            fields.append(f"ENDPOINT '{_escape_sql_literal(parsed.netloc)}'")
        return f"CREATE OR REPLACE SECRET {safe_name} ({', '.join(fields)});"

    def configure_duckdb_httpfs(
        self,
        connection: duckdb.DuckDBPyConnection,
        *,
        secret_name: str = "_finintel_object_store",
        secret_type: DuckDBSecretType = "auto",
    ) -> None:
        """Load DuckDB ``httpfs`` and register the R2/S3 secret without exposing credentials."""
        if not self.is_remote:
            return
        stmt = self.build_duckdb_secret_sql(
            secret_name=secret_name,
            secret_type=secret_type,
        )
        try:
            connection.execute("LOAD httpfs;")
            connection.execute(stmt)
        except duckdb.Error:
            # Never propagate DuckDB SQL/traceback text that might echo CREATE SECRET fields.
            raise ContractViolationError(
                f"Failed to configure DuckDB httpfs secret for object_store backend={self.backend!r}."
            ) from None

    def build_remote_postgres_secret_statements(
        self,
        *,
        postgres_dsn_env: str,
        catalog_alias: str,
        read_only: bool = False,
        override_data_path: bool = False,
    ) -> tuple[str, str, str]:
        """Build DuckDB Postgres + DuckLake secret statements targeting ``s3://bucket/prefix``."""
        if not self.is_remote:
            raise ValueError(
                "build_remote_postgres_secret_statements requires backend 'r2' or 's3'."
            )
        from finintel.storage.ducklake_store import build_postgres_secret_statements

        sentinel_path = Path("__finintel_remote_data_path__")
        sentinel_sql = _escape_sql_literal(sentinel_path.resolve().as_posix())
        pg_stmt, dl_stmt, attach_stmt = build_postgres_secret_statements(
            postgres_dsn_env=postgres_dsn_env,
            data_path=sentinel_path,
            catalog_alias=catalog_alias,
            read_only=read_only,
            override_data_path=override_data_path,
        )
        remote_sql = _escape_sql_literal(self.duckdb_data_path())
        dl_stmt_remote = dl_stmt.replace(
            f"DATA_PATH '{sentinel_sql}'",
            f"DATA_PATH '{remote_sql}'",
            1,
        )
        return pg_stmt, dl_stmt_remote, attach_stmt

    def attach_remote_ducklake(
        self,
        connection: duckdb.DuckDBPyConnection,
        *,
        catalog_path: Path,
        catalog_alias: str,
        catalog_driver: str,
        postgres_dsn_env: str,
        read_only: bool = True,
        override_data_path: bool = False,
    ) -> None:
        """Attach DuckLake catalog backed by Cloudflare R2 / S3 object storage."""
        safe_alias = _validate_identifier(catalog_alias, label="catalog_alias")
        existing = connection.execute(
            "SELECT database_name FROM duckdb_databases() WHERE database_name = ?",
            [safe_alias],
        ).fetchone()
        if existing is not None:
            return
        self.configure_duckdb_httpfs(
            connection, secret_name=f"_finintel_obj_{safe_alias}"
        )
        connection.execute("LOAD ducklake;")
        if catalog_driver == "postgres":
            connection.execute("LOAD postgres;")
            stmts = self.build_remote_postgres_secret_statements(
                postgres_dsn_env=postgres_dsn_env,
                catalog_alias=safe_alias,
                read_only=read_only,
                override_data_path=override_data_path,
            )
            try:
                for stmt in stmts:
                    connection.execute(stmt)
            except duckdb.Error:
                raise ContractViolationError(
                    f"Failed to attach PostgreSQL DuckLake catalog configured via {postgres_dsn_env}."
                ) from None
            return
        connection.execute("LOAD sqlite;")
        cat_sql = _escape_sql_literal(catalog_path.resolve().as_posix())
        data_sql = _escape_sql_literal(self.duckdb_data_path())
        opts = [f"DATA_PATH '{data_sql}'"]
        if override_data_path:
            opts.append("OVERRIDE_DATA_PATH true")
        if read_only:
            opts.append("READ_ONLY")
        connection.execute(
            f"ATTACH 'ducklake:sqlite:{cat_sql}' AS \"{safe_alias}\" ({', '.join(opts)});"
        )

    def open_rw_connection(self, store: object) -> duckdb.DuckDBPyConnection:
        """Open a read-write DuckDB connection for a ``DuckLakeStore`` using R2/S3."""
        from finintel.storage.ducklake_store import (
            DuckLakeStore,
            _resolve_override_data_path,
            configure_sqlite_catalog,
        )

        if not isinstance(store, DuckLakeStore):
            raise TypeError("store must be a DuckLakeStore instance.")
        if store.catalog_driver == "sqlite":
            configure_sqlite_catalog(store.catalog_path)
        con = duckdb.connect(":memory:")
        try:
            self.attach_remote_ducklake(
                con,
                catalog_path=store.catalog_path,
                catalog_alias=store.catalog_alias,
                catalog_driver=store.catalog_driver,
                postgres_dsn_env=store.postgres_dsn_env,
                read_only=False,
                override_data_path=_resolve_override_data_path(
                    store.override_data_path
                ),
            )
            return con
        except BaseException:
            con.close()
            raise

    @classmethod
    def from_mapping(cls, raw: object) -> ObjectStoreConfig:
        """Parse an ``object_store:`` mapping from ``settings.yaml``, defaulting to ``local``."""
        if raw is None:
            return cls()
        if not isinstance(raw, Mapping):
            raise ValueError("object_store must be a YAML mapping.")
        if not raw:
            return cls()
        raw_backend = raw.get("backend", "local")
        if raw_backend not in ("local", "r2", "s3"):
            raise ValueError("object_store.backend must be 'local', 'r2', or 's3'.")
        backend: ObjectStoreBackend = (
            "r2" if raw_backend == "r2" else ("s3" if raw_backend == "s3" else "local")
        )
        raw_bucket = raw.get("bucket")
        bucket = str(raw_bucket) if raw_bucket is not None else None
        raw_prefix = raw.get("prefix", DEFAULT_OBJECT_PREFIX)
        if not isinstance(raw_prefix, str):
            raise ValueError("object_store.prefix must be a string.")
        raw_endpoint = raw.get("endpoint_url")
        endpoint_url = str(raw_endpoint) if raw_endpoint is not None else None
        raw_region = raw.get("region", "auto")
        if not isinstance(raw_region, str):
            raise ValueError("object_store.region must be a string.")
        use_ssl = raw.get("use_ssl", True)
        if type(use_ssl) is not bool:
            raise ValueError("object_store.use_ssl must be a boolean.")
        raw_url_style = raw.get("url_style", "path")
        if raw_url_style not in ("path", "vhost"):
            raise ValueError("object_store.url_style must be 'path' or 'vhost'.")
        url_style: ObjectStoreUrlStyle = "vhost" if raw_url_style == "vhost" else "path"
        access_env = str(raw.get("access_key_id_env", DEFAULT_S3_ACCESS_KEY_ENV))
        secret_env = str(raw.get("secret_access_key_env", DEFAULT_S3_SECRET_KEY_ENV))
        return cls(
            backend=backend,
            bucket=bucket,
            prefix=raw_prefix,
            endpoint_url=endpoint_url,
            region=raw_region,
            use_ssl=use_ssl,
            access_key_id_env=access_env,
            secret_access_key_env=secret_env,
            url_style=url_style,
        )

    @classmethod
    def from_env(cls) -> ObjectStoreConfig:
        """Resolve ``ObjectStoreConfig`` from ``FININTEL_OBJECT_STORE_*`` env vars."""
        raw_backend = (
            os.environ.get("FININTEL_OBJECT_STORE_BACKEND", "local").strip().lower()
        )
        if raw_backend not in ("local", "r2", "s3"):
            raise ValueError(
                f"FININTEL_OBJECT_STORE_BACKEND must be 'local', 'r2', or 's3', got {raw_backend!r}."
            )
        backend: ObjectStoreBackend = (
            "r2" if raw_backend == "r2" else ("s3" if raw_backend == "s3" else "local")
        )
        bucket = os.environ.get("FININTEL_OBJECT_STORE_BUCKET", "").strip() or None
        prefix = os.environ.get("FININTEL_OBJECT_STORE_PREFIX", DEFAULT_OBJECT_PREFIX)
        endpoint_url = (
            os.environ.get("FININTEL_R2_ENDPOINT_URL", "").strip()
            or os.environ.get("FININTEL_S3_ENDPOINT_URL", "").strip()
            or None
        )
        region = os.environ.get("FININTEL_S3_REGION", "auto").strip() or "auto"
        use_ssl = _parse_bool_env(
            os.environ.get("FININTEL_S3_USE_SSL"),
            default=True,
            name="FININTEL_S3_USE_SSL",
        )
        return cls(
            backend=backend,
            bucket=bucket,
            prefix=prefix,
            endpoint_url=endpoint_url,
            region=region,
            use_ssl=use_ssl,
        )
