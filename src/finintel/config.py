"""Unified runtime configuration resolver for FinIntel V2 storage, serving & orchestration.

Precedence order:
    explicit argument > environment variable > ``config/settings.yaml`` > local SQLite/auto fallback.

Guardrails:
    - Keeps all ``platform``-group dependencies (``dagster``, ``psycopg``, ``redis``,
      ``opentelemetry``, ``pgvector``, ``granian``) completely absent at module import time
      so core CI ``mvp-checks`` (``--group ml --dev``) never breaks (M-03).
    - Never embeds or logs raw database/cache DSN credentials.

Verified Sources:
    - PyYAML ``safe_load`` (``pyyaml==6.0.3``): https://pyyaml.org/wiki/PyYAMLDocumentation
    - 12-Factor Configuration & DuckLake Postgres DSN environment contract:
      ``src/finintel/storage/ducklake_store.py`` (``FININTEL_DUCKLAKE_PG_DSN``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml  # type: ignore[import-untyped]

from finintel.exceptions import ContractViolationError
from finintel.storage.ducklake_store import (
    DEFAULT_CATALOG_ALIAS,
    DEFAULT_POSTGRES_DSN_ENV,
    CatalogDriver,
    validate_sql_identifier,
)

StorageBackendMode = Literal["auto", "parquet", "ducklake"]

DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = DEFAULT_PROJECT_ROOT / "data"
DEFAULT_SETTINGS_PATH = DEFAULT_PROJECT_ROOT / "config" / "settings.yaml"


@dataclass(frozen=True)
class FinIntelRuntimeConfig:
    """Resolved storage, catalog, cache, and CORS configuration for FinIntel runtime."""

    project_root: Path
    config_dir: Path
    data_root: Path
    settings_path: Path | None
    state_path: Path
    backend: StorageBackendMode
    catalog_driver: CatalogDriver
    catalog_sqlite_path: Path
    postgres_dsn_env: str
    postgres_dsn_configured: bool
    lakehouse_data_path: Path
    catalog_alias: str
    valkey_url: str | None = field(default=None, repr=False)
    cors_origins: tuple[str, ...] = ()


def parse_cors_origins(raw_items: Sequence[str]) -> tuple[str, ...]:
    """Validate and deduplicate explicit HTTP(S) CORS origins."""
    validated: list[str] = []
    for item in raw_items:
        origin = item.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Invalid CORS origin configured: {origin!r}")
        if origin not in validated:
            validated.append(origin)
    return tuple(validated)


def load_settings_mapping(settings_path: Path | None) -> dict[str, object]:
    """Load a YAML settings mapping if ``settings_path`` is present, else return ``{}``."""
    if settings_path is None or not settings_path.is_file():
        return {}
    try:
        loaded = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ContractViolationError(
            f"Unable to parse settings YAML at {settings_path}."
        ) from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, Mapping):
        raise ContractViolationError("settings.yaml root must be a YAML mapping.")
    return {str(k): v for k, v in loaded.items()}


def _resolve_under(base: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    return (candidate if candidate.is_absolute() else base / candidate).resolve()


def resolve_runtime_config(
    *,
    data_root: Path | None = None,
    config_path: Path | None = None,
    backend: StorageBackendMode | None = None,
    catalog_driver: CatalogDriver | None = None,
    postgres_dsn_env: str | None = None,
    valkey_url: str | None = None,
    cors_origins: Sequence[str] | None = None,
) -> FinIntelRuntimeConfig:
    """Resolve unified FinIntel runtime config (``explicit > env > settings.yaml > fallback``)."""
    env_data_root = os.getenv("FININTEL_DATA_ROOT", "").strip()
    if data_root is not None:
        resolved_data_root = data_root.resolve()
        data_root_overridden = True
    elif env_data_root:
        resolved_data_root = Path(env_data_root).resolve()
        data_root_overridden = True
    else:
        resolved_data_root = DEFAULT_DATA_ROOT.resolve()
        data_root_overridden = False

    env_settings = (
        os.getenv("FININTEL_SETTINGS_PATH", "").strip()
        or os.getenv("FININTEL_CONFIG_PATH", "").strip()
    )
    resolved_settings_path: Path | None
    if config_path is not None:
        resolved_settings_path = config_path.resolve()
        if not resolved_settings_path.is_file():
            raise FileNotFoundError(resolved_settings_path)
    elif env_settings:
        resolved_settings_path = Path(env_settings).resolve()
        if not resolved_settings_path.is_file():
            raise FileNotFoundError(resolved_settings_path)
    elif data_root is not None:
        candidate = (resolved_data_root.parent / "config" / "settings.yaml").resolve()
        resolved_settings_path = candidate if candidate.is_file() else None
    elif data_root_overridden:
        candidate = (resolved_data_root.parent / "config" / "settings.yaml").resolve()
        if candidate.is_file():
            resolved_settings_path = candidate
        else:
            default_cfg = DEFAULT_SETTINGS_PATH.resolve()
            resolved_settings_path = default_cfg if default_cfg.is_file() else None
    else:
        default_cfg = DEFAULT_SETTINGS_PATH.resolve()
        resolved_settings_path = default_cfg if default_cfg.is_file() else None

    if resolved_settings_path is not None:
        config_dir = resolved_settings_path.parent
        project_root = config_dir.parent
    else:
        project_root = resolved_data_root.parent
        config_dir = (project_root / "config").resolve()

    raw_settings = load_settings_mapping(resolved_settings_path)
    raw_storage_obj = raw_settings.get("storage")
    if raw_storage_obj is not None and not isinstance(raw_storage_obj, Mapping):
        raise ContractViolationError("settings.yaml 'storage' must be a mapping.")
    raw_storage: Mapping[str, object] = (
        raw_storage_obj if isinstance(raw_storage_obj, Mapping) else {}
    )
    raw_catalog_obj = raw_storage.get("catalog")
    if raw_catalog_obj is not None and not isinstance(raw_catalog_obj, Mapping):
        raise ContractViolationError(
            "settings.yaml 'storage.catalog' must be a mapping."
        )
    raw_catalog: Mapping[str, object] = (
        raw_catalog_obj if isinstance(raw_catalog_obj, Mapping) else {}
    )
    raw_serving_obj = raw_settings.get("serving")
    if raw_serving_obj is not None and not isinstance(raw_serving_obj, Mapping):
        raise ContractViolationError("settings.yaml 'serving' must be a mapping.")
    raw_serving: Mapping[str, object] = (
        raw_serving_obj if isinstance(raw_serving_obj, Mapping) else {}
    )

    # 1. Postgres DSN environment variable name
    env_pg_dsn_var = os.getenv("FININTEL_DUCKLAKE_PG_DSN_ENV", "").strip()
    yaml_pg_dsn_var = raw_catalog.get("postgres_dsn_env") or raw_serving.get(
        "ducklake_pg_dsn_env"
    )
    if postgres_dsn_env is not None:
        resolved_pg_env = postgres_dsn_env.strip()
    elif env_pg_dsn_var:
        resolved_pg_env = env_pg_dsn_var
    elif isinstance(yaml_pg_dsn_var, str) and yaml_pg_dsn_var.strip():
        resolved_pg_env = yaml_pg_dsn_var.strip()
    else:
        resolved_pg_env = DEFAULT_POSTGRES_DSN_ENV
    if not resolved_pg_env:
        raise ContractViolationError("postgres_dsn_env must be non-empty.")

    postgres_dsn_configured = bool(os.getenv(resolved_pg_env, "").strip())

    # 2. Catalog driver (explicit > env driver > active Postgres DSN > settings.yaml > sqlite)
    env_driver = os.getenv("FININTEL_DUCKLAKE_CATALOG_DRIVER", "").strip().lower()
    yaml_driver = raw_catalog.get("driver")
    raw_driver_val: str
    if catalog_driver is not None:
        raw_driver_val = catalog_driver
    elif env_driver:
        raw_driver_val = env_driver
    elif postgres_dsn_configured:
        raw_driver_val = "postgres"
    elif isinstance(yaml_driver, str) and yaml_driver.strip():
        raw_driver_val = yaml_driver.strip().lower()
    else:
        raw_driver_val = "sqlite"

    if raw_driver_val not in ("sqlite", "postgres"):
        raise ContractViolationError(
            f"catalog_driver must be 'sqlite' or 'postgres', got {raw_driver_val!r}."
        )
    resolved_driver: CatalogDriver = (
        "postgres" if raw_driver_val == "postgres" else "sqlite"
    )

    # 3. Storage backend (explicit > env > postgres implies ducklake > settings.yaml > auto)
    env_backend = os.getenv("FININTEL_STORAGE_BACKEND", "").strip().lower()
    yaml_backend = raw_storage.get("backend")
    raw_backend_val: str
    if backend is not None:
        raw_backend_val = backend
    elif env_backend:
        raw_backend_val = env_backend
    elif resolved_driver == "postgres":
        raw_backend_val = "ducklake"
    elif isinstance(yaml_backend, str) and yaml_backend.strip():
        candidate_backend = yaml_backend.strip().lower()
        if (
            data_root_overridden
            and candidate_backend == "ducklake"
            and not (
                resolved_data_root / "metadata" / "ducklake_catalog.sqlite"
            ).is_file()
            and (resolved_data_root / "published" / "snapshots").is_dir()
        ):
            raw_backend_val = "auto"
        else:
            raw_backend_val = candidate_backend
    else:
        raw_backend_val = "auto"

    if raw_backend_val not in ("auto", "parquet", "ducklake"):
        raise ContractViolationError(
            f"storage backend must be 'auto', 'parquet', or 'ducklake', got {raw_backend_val!r}."
        )
    resolved_backend: StorageBackendMode = (
        "ducklake"
        if raw_backend_val == "ducklake"
        else ("parquet" if raw_backend_val == "parquet" else "auto")
    )

    # 4. Paths & alias (explicit data_root or FININTEL_DATA_ROOT always wins over YAML ../data defaults)
    use_yaml_paths = resolved_settings_path is not None and not data_root_overridden
    raw_state = raw_settings.get("state_path")
    if isinstance(raw_state, str) and raw_state.strip() and use_yaml_paths:
        state_path = _resolve_under(config_dir, raw_state.strip())
    else:
        state_path = (resolved_data_root / "metadata" / "state.sqlite").resolve()

    raw_sqlite = raw_catalog.get("sqlite_path")
    if isinstance(raw_sqlite, str) and raw_sqlite.strip() and use_yaml_paths:
        catalog_sqlite_path = _resolve_under(config_dir, raw_sqlite.strip())
    else:
        catalog_sqlite_path = (
            resolved_data_root / "metadata" / "ducklake_catalog.sqlite"
        ).resolve()

    raw_lake_data = raw_storage.get("data_path")
    if isinstance(raw_lake_data, str) and raw_lake_data.strip() and use_yaml_paths:
        lakehouse_data_path = _resolve_under(config_dir, raw_lake_data.strip())
    else:
        lakehouse_data_path = (resolved_data_root / "lakehouse" / "data").resolve()

    raw_alias = raw_storage.get("catalog_alias", DEFAULT_CATALOG_ALIAS)
    if not isinstance(raw_alias, str):
        raise ContractViolationError("storage.catalog_alias must be a string.")
    catalog_alias = validate_sql_identifier(raw_alias, label="catalog_alias")

    # 5. Valkey L2 URL
    if valkey_url is not None:
        resolved_valkey = valkey_url.strip() or None
    else:
        valkey_env_name = raw_serving.get("valkey_url_env")
        env_valkey = (
            os.getenv("FININTEL_VALKEY_URL")
            or os.getenv("FININTEL_REDIS_URL")
            or (
                os.getenv(valkey_env_name.strip())
                if isinstance(valkey_env_name, str) and valkey_env_name.strip()
                else None
            )
        )
        if env_valkey is not None and env_valkey.strip():
            resolved_valkey = env_valkey.strip()
        else:
            yaml_valkey = raw_serving.get("valkey_url")
            resolved_valkey = (
                yaml_valkey.strip() or None if isinstance(yaml_valkey, str) else None
            )

    # 6. CORS origins
    if cors_origins is not None:
        resolved_cors = parse_cors_origins(cors_origins)
    else:
        env_cors = os.getenv("FININTEL_CORS_ORIGINS")
        if env_cors is not None and env_cors.strip():
            resolved_cors = parse_cors_origins(
                [item for item in env_cors.split(",") if item.strip()]
            )
        else:
            yaml_cors = raw_serving.get("cors_origins")
            if isinstance(yaml_cors, Sequence) and not isinstance(
                yaml_cors, (str, bytes)
            ):
                resolved_cors = parse_cors_origins(
                    [str(item) for item in yaml_cors if isinstance(item, str)]
                )
            else:
                resolved_cors = ()

    return FinIntelRuntimeConfig(
        project_root=project_root,
        config_dir=config_dir,
        data_root=resolved_data_root,
        settings_path=resolved_settings_path,
        state_path=state_path,
        backend=resolved_backend,
        catalog_driver=resolved_driver,
        catalog_sqlite_path=catalog_sqlite_path,
        postgres_dsn_env=resolved_pg_env,
        postgres_dsn_configured=postgres_dsn_configured,
        lakehouse_data_path=lakehouse_data_path,
        catalog_alias=catalog_alias,
        valkey_url=resolved_valkey,
        cors_origins=resolved_cors,
    )
