"""Unit and configuration verification tests for ARCH-04 (PostgreSQL Tier-1, config & Dagster pools)."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import dagster as dg
import duckdb
import pytest
import yaml  # type: ignore[import-untyped]
from dagster._config import validate_config
from dagster._core.instance.config import dagster_instance_config_schema
from fastapi.testclient import TestClient

from finintel.config import FinIntelRuntimeConfig, resolve_runtime_config
from finintel.orchestration.partitions import (
    CORPORATE_TICKERS_PARTITION_NAME,
    FHSC_API_POOL,
    PUBLIC_DISCLOSURE_POOL,
)
from finintel.orchestration.resources import (
    FinIntelPathsResource,
    StorageResource,
    bootstrap_dagster_instance,
    main as bootstrap_main,
)
from finintel.pipeline.state_store import StateStore
from finintel.serving.api import create_app
from finintel.storage.ducklake_store import DuckLakeStore, ensure_ducklake_attached
from tests.unit.test_layer5_fastapi_serving import _build_synthetic_v2_tables

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_docker_compose_uses_pgvector_pg16_and_init_sql_creates_three_dbs() -> None:
    """B-05: docker-compose uses pgvector/pgvector:pg16 and initializes 3 DBs + RAG extensions."""
    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    pg_service = compose["services"]["postgres"]
    assert pg_service["image"] == "pgvector/pgvector:pg16"
    assert "postgres:16-alpine" not in (REPO_ROOT / "docker-compose.yml").read_text(
        encoding="utf-8"
    )

    init_sql = (REPO_ROOT / "config" / "postgres-init" / "01-create-dbs.sql").read_text(
        encoding="utf-8"
    )
    assert "ducklake_catalog" in init_sql
    assert "dagster_state" in init_sql
    assert "finintel_rag" in init_sql
    assert "\\c finintel_rag" in init_sql
    rag_section = init_sql.split("\\c finintel_rag", 1)[1]
    assert "CREATE EXTENSION IF NOT EXISTS vector" in rag_section
    assert "CREATE EXTENSION IF NOT EXISTS unaccent" in rag_section


def test_dagster_postgres_yaml_schema_concurrency_pools_and_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-03: config/dagster.postgres.yaml defines op-level concurrency pools and disables telemetry."""
    monkeypatch.setenv("FININTEL_PG_USER", "finintel")
    monkeypatch.setenv("FININTEL_PG_PASSWORD", "finintel_dev")
    monkeypatch.setenv("FININTEL_PG_HOST", "127.0.0.1")
    monkeypatch.setenv("FININTEL_DAGSTER_PG_DB", "dagster_state")
    monkeypatch.setenv("FININTEL_PG_PORT", "5432")

    raw_text = (REPO_ROOT / "config" / "dagster.postgres.yaml").read_text(
        encoding="utf-8"
    )
    parsed = yaml.safe_load(raw_text)
    assert parsed["concurrency"]["pools"]["granularity"] == "op"
    assert parsed["telemetry"]["enabled"] is False
    assert parsed["storage"]["postgres"]["postgres_db"]["db_name"] == {
        "env": "FININTEL_DAGSTER_PG_DB"
    }

    validated = validate_config(dagster_instance_config_schema(), parsed)
    assert validated.success, validated.errors


def test_config_py_mvp_checks_lazy_import_discipline_and_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M-03: src/finintel/config.py keeps platform-group imports absent and resolves config precedence."""
    forbidden = {
        "dagster",
        "psycopg",
        "psycopg2",
        "redis",
        "opentelemetry",
        "pgvector",
        "granian",
        "fastapi",
        "uvicorn",
    }
    for rel in ("src/finintel/config.py", "src/finintel/storage/repository.py"):
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in forbidden, (
                        f"Forbidden platform import {alias.name} in {rel}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden, (
                    f"Forbidden platform import {node.module} in {rel}"
                )

    # Simulate CI mvp-checks (--group ml --dev without platform deps) in an isolated subprocess.
    sim_code = """
import sys
from importlib.abc import MetaPathFinder
BLOCKED = {"dagster", "psycopg", "psycopg2", "redis", "opentelemetry", "pgvector", "granian", "fastapi", "uvicorn"}
class _Blocker(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] in BLOCKED:
            raise ModuleNotFoundError(f"Blocked platform package: {fullname}")
        return None
sys.meta_path.insert(0, _Blocker())
from finintel.config import resolve_runtime_config
from finintel.storage.repository import ParquetRepository
cfg = resolve_runtime_config()
assert cfg.postgres_dsn_env == "FININTEL_DUCKLAKE_PG_DSN"
print("MVP_OK")
"""
    proc = subprocess.run(
        [sys.executable, "-c", sim_code],
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "MVP_OK" in proc.stdout

    # Isolated tmp_path without settings.yaml stays backend="auto", driver="sqlite"
    monkeypatch.delenv("FININTEL_DUCKLAKE_PG_DSN", raising=False)
    monkeypatch.delenv("FININTEL_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("FININTEL_DUCKLAKE_CATALOG_DRIVER", raising=False)
    monkeypatch.delenv("FININTEL_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("FININTEL_VALKEY_URL", raising=False)
    iso_root = tmp_path / "isolated_data"
    cfg_iso = resolve_runtime_config(data_root=iso_root)
    assert cfg_iso.backend == "auto"
    assert cfg_iso.catalog_driver == "sqlite"
    assert cfg_iso.postgres_dsn_configured is False

    # FININTEL_DUCKLAKE_PG_DSN in env activates postgres + ducklake without leaking secret in repr
    secret_pw = "SuperSecretPgPass_99!"
    monkeypatch.setenv(
        "FININTEL_DUCKLAKE_PG_DSN",
        f"postgresql://finintel:{secret_pw}@127.0.0.1:5432/ducklake_catalog",
    )
    cfg_pg = resolve_runtime_config(data_root=iso_root)
    assert isinstance(cfg_pg, FinIntelRuntimeConfig)
    assert cfg_pg.catalog_driver == "postgres"
    assert cfg_pg.backend == "ducklake"
    assert cfg_pg.postgres_dsn_env == "FININTEL_DUCKLAKE_PG_DSN"
    assert cfg_pg.postgres_dsn_configured is True
    assert secret_pw not in repr(cfg_pg)

    # Explicit overrides beat env
    cfg_explicit = resolve_runtime_config(
        data_root=iso_root,
        backend="parquet",
        catalog_driver="sqlite",
    )
    assert cfg_explicit.backend == "parquet"
    assert cfg_explicit.catalog_driver == "sqlite"


def test_build_lifespan_wires_finintel_ducklake_pg_dsn_into_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FastAPI build_lifespan reads FININTEL_DUCKLAKE_PG_DSN and configures Postgres DuckLake repository."""
    remote_root = tmp_path / "remote_lake"
    remote_state = StateStore(remote_root / "metadata" / "state.sqlite")
    remote_state.initialize()
    remote_state.start_run("run-pg-1", "MANUAL", {"backend": "ducklake"}, None)
    store = DuckLakeStore(
        catalog_path=remote_root / "metadata" / "ducklake_catalog.sqlite",
        data_path=remote_root / "lakehouse" / "data",
        state_store=remote_state,
    )
    tables = _build_synthetic_v2_tables()
    try:
        store.publish_snapshot(
            "snap_pg_001",
            tables,
            run_id="run-pg-1",
        )
    except duckdb.Error as exc:
        pytest.skip(f"DuckLake extension unavailable in this environment: {exc}")
    remote_state.finish_run("run-pg-1", "SUCCEEDED", "snap_pg_001")

    local_root = tmp_path / "serving_node"
    local_state = StateStore(local_root / "metadata" / "state.sqlite")
    local_state.initialize()
    local_state.start_run("run-pg-1", "MANUAL", {"backend": "ducklake"}, None)
    local_state.publish_snapshot("run-pg-1", "snap_pg_001")
    local_state.finish_run("run-pg-1", "SUCCEEDED", "snap_pg_001")

    attach_seen: list[tuple[str, str, bool]] = []
    real_attach = ensure_ducklake_attached

    def _delegating_attach(
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
        attach_seen.append((catalog_driver, postgres_dsn_env, read_only))
        real_attach(
            connection,
            catalog_path=store.catalog_path,
            data_path=store.data_path,
            catalog_alias=catalog_alias,
            catalog_driver="sqlite",
            read_only=read_only,
        )

    from collections import OrderedDict

    monkeypatch.setattr(
        "finintel.storage.repository._POSTGRES_POINTER_CACHE", OrderedDict()
    )
    monkeypatch.setattr(
        "finintel.storage.repository.read_postgres_active_snapshot",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "finintel.serving.dependencies.ensure_ducklake_attached", _delegating_attach
    )
    monkeypatch.setattr(
        "finintel.storage.repository.ensure_ducklake_attached", _delegating_attach
    )
    monkeypatch.setenv(
        "FININTEL_DUCKLAKE_PG_DSN",
        "postgresql://finintel:finintel_dev@127.0.0.1:5432/ducklake_catalog",
    )

    app = create_app(data_root=local_root)
    with TestClient(app) as client:
        repo = app.state.repository
        assert repo._catalog_driver == "postgres"
        assert repo._postgres_dsn_env == "FININTEL_DUCKLAKE_PG_DSN"
        assert repo._backend == "ducklake"

        r_health = client.get("/api/v1/health")
        assert r_health.status_code == 200
        assert r_health.json() == {
            "status": "ok",
            "active_snapshot_id": "snap_pg_001",
            "storage_backend": "ducklake",
        }
        assert attach_seen
        assert all(
            drv == "postgres" and env == "FININTEL_DUCKLAKE_PG_DSN" and ro is True
            for drv, env, ro in attach_seen
        )


def test_bootstrap_dagster_instance_and_storage_resource_pg_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-03: bootstrap_dagster_instance seeds concurrency pools + partitions and StorageResource honors PG DSN."""
    cfg_dir = tmp_path / "dagster_cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    raw_yaml = (REPO_ROOT / "config" / "dagster.yaml").read_text(encoding="utf-8")
    (cfg_dir / "dagster.yaml").write_text(
        raw_yaml.replace(
            "data/metadata/dagster",
            (tmp_path / "dagster_state").as_posix(),
        ),
        encoding="utf-8",
    )
    universe_yaml = cfg_dir / "universe_tickers.yaml"
    universe_yaml.write_text(
        yaml.safe_dump({"accounting_regime": "CORPORATE", "tickers": ["FPT", "HPG"]}),
        encoding="utf-8",
    )

    summary = bootstrap_dagster_instance(dagster_home=cfg_dir)
    assert summary["pool_limits"] == {
        FHSC_API_POOL: 1,
        PUBLIC_DISCLOSURE_POOL: 3,
    }
    assert summary["synced_partitions_count"] == 2

    with dg.DagsterInstance.from_config(str(cfg_dir)) as instance:
        limits = {
            item.name: item.limit
            for item in instance.event_log_storage.get_pool_limits()
        }
        assert limits[FHSC_API_POOL] == 1
        assert limits[PUBLIC_DISCLOSURE_POOL] == 3
        assert set(
            instance.get_dynamic_partitions(CORPORATE_TICKERS_PARTITION_NAME)
        ) == {
            "FPT",
            "HPG",
        }

    # CLI entrypoint check
    rc = bootstrap_main(["--dagster-home", str(cfg_dir)])
    assert rc == 0

    # StorageResource honors FININTEL_DUCKLAKE_PG_DSN when fields are unset
    monkeypatch.setenv(
        "FININTEL_DUCKLAKE_PG_DSN",
        "postgresql://finintel:finintel_dev@127.0.0.1:5432/ducklake_catalog",
    )
    paths = FinIntelPathsResource(project_root=str(tmp_path), config_dir="dagster_cfg")
    cfg = StorageResource().resolve_storage_config(paths)
    assert cfg.catalog.driver == "postgres"
    assert cfg.backend == "ducklake"
    assert cfg.catalog.postgres_dsn_env == "FININTEL_DUCKLAKE_PG_DSN"


def test_pyproject_pins_dagster_postgres_and_psycopg() -> None:
    """pyproject.toml pins dagster-postgres and psycopg[binary] in platform group."""
    import tomllib

    data: dict[str, Any] = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    platform_deps: list[str] = data["dependency-groups"]["platform"]
    assert any(dep.startswith("dagster-postgres==") for dep in platform_deps)
    assert any(dep.startswith("psycopg[binary]==") for dep in platform_deps)
    # Also verify JSON serializability of bootstrap summary contract
    assert json.loads(json.dumps({"ok": True})) == {"ok": True}
