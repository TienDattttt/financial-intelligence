"""Offline TDD tests for V3-07..V3-09 (v3-infra-ha-storage).

Verified Sources:
- DuckDB Secrets Manager & httpfs S3/R2 API:
  https://duckdb.org/docs/stable/configuration/secrets_manager
  https://duckdb.org/docs/stable/core_extensions/httpfs/s3api
- OWASP Secure Headers & CORS Cheat Sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Headers_Cheat_Sheet.html
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from finintel.exceptions import ContractViolationError
from finintel.pipeline.state_store import StateStore
from finintel.storage.ducklake_store import (
    DuckLakeStore,
    StorageConfig,
)
from finintel.storage.object_store import ObjectStoreConfig


def test_v3_07_object_store_local_default_and_validation() -> None:
    cfg = ObjectStoreConfig()
    assert cfg.backend == "local"
    assert cfg.is_remote is False
    assert cfg.region == "auto"
    assert cfg.use_ssl is True
    assert cfg.duckdb_data_path(Path("/tmp/lakehouse/data")).endswith("lakehouse/data")

    with pytest.raises(ValueError, match="Cannot build DuckDB remote secret SQL"):
        cfg.build_duckdb_secret_sql()

    with pytest.raises(ValueError, match="bucket"):
        ObjectStoreConfig(
            backend="r2",
            bucket=None,
            endpoint_url="https://acc123.r2.cloudflarestorage.com",
        )

    with pytest.raises(ValueError, match="endpoint_url"):
        ObjectStoreConfig(backend="r2", bucket="finintel-lake", endpoint_url=None)

    with pytest.raises(ValueError, match="HTTPS"):
        ObjectStoreConfig(
            backend="r2",
            bucket="finintel-lake",
            endpoint_url="http://acc123.r2.cloudflarestorage.com",
            use_ssl=True,
        )

    with pytest.raises(ValueError, match="userinfo"):
        ObjectStoreConfig(
            backend="r2",
            bucket="finintel-lake",
            endpoint_url="https://key:secret@acc123.r2.cloudflarestorage.com",
        )

    with pytest.raises(ValueError, match="region"):
        ObjectStoreConfig(
            backend="r2",
            bucket="finintel-lake",
            endpoint_url="https://acc123.r2.cloudflarestorage.com",
            region="us-east-1",
        )

    with pytest.raises(ValueError, match="prefix"):
        ObjectStoreConfig(
            backend="s3",
            bucket="finintel-lake",
            prefix="../escape",
            region="ap-southeast-1",
        )


def test_v3_07_object_store_r2_and_s3_duckdb_secret_and_redaction(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_key_id = "R2_ACCESS_KEY_DO_NOT_LEAK_9911"
    secret_key_val = "R2_SECRET_KEY_DO_NOT_LEAK_7733"
    monkeypatch.setenv("FININTEL_S3_ACCESS_KEY_ID", secret_key_id)
    monkeypatch.setenv("FININTEL_S3_SECRET_ACCESS_KEY", secret_key_val)

    r2_cfg = ObjectStoreConfig(
        backend="r2",
        bucket="finintel-prod",
        prefix="/finintel/lakehouse/",
        endpoint_url="https://acct998877.r2.cloudflarestorage.com/",
        region="auto",
        use_ssl=True,
    )
    assert r2_cfg.is_remote is True
    assert r2_cfg.prefix == "finintel/lakehouse"
    assert r2_cfg.r2_account_id() == "acct998877"
    assert r2_cfg.duckdb_data_path() == "s3://finintel-prod/finintel/lakehouse"

    with caplog.at_level(logging.DEBUG):
        s3_sql = r2_cfg.build_duckdb_secret_sql(secret_name="_finintel_r2_s3")
        r2_sql = r2_cfg.build_duckdb_secret_sql(
            secret_name="_finintel_r2_native", secret_type="r2"
        )

    assert "CREATE OR REPLACE SECRET _finintel_r2_s3" in s3_sql
    assert "TYPE S3" in s3_sql
    assert "REGION 'auto'" in s3_sql
    assert "ENDPOINT 'acct998877.r2.cloudflarestorage.com'" in s3_sql
    assert "URL_STYLE 'path'" in s3_sql
    assert "USE_SSL true" in s3_sql
    assert "SCOPE 's3://finintel-prod'" in s3_sql
    assert f"KEY_ID '{secret_key_id}'" in s3_sql
    assert f"SECRET '{secret_key_val}'" in s3_sql

    assert "CREATE OR REPLACE SECRET _finintel_r2_native" in r2_sql
    assert "TYPE R2" in r2_sql
    assert "ACCOUNT_ID 'acct998877'" in r2_sql
    assert f"KEY_ID '{secret_key_id}'" in r2_sql
    assert f"SECRET '{secret_key_val}'" in r2_sql

    # 100% credential redaction in repr, str, summary, and logs.
    rendered = f"{repr(r2_cfg)} {str(r2_cfg)} {r2_cfg.redacted_summary()}"
    assert secret_key_id not in rendered
    assert secret_key_val not in rendered
    assert secret_key_id not in caplog.text
    assert secret_key_val not in caplog.text

    # Missing credentials fail closed without leaking env var values.
    monkeypatch.delenv("FININTEL_S3_SECRET_ACCESS_KEY", raising=False)
    with pytest.raises(ContractViolationError, match="FININTEL_S3_SECRET_ACCESS_KEY"):
        r2_cfg.resolve_credentials()


def test_v3_07_ducklake_store_keeps_local_default_and_supports_remote_object_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = StateStore(tmp_path / "metadata" / "state.sqlite")
    state.initialize()
    local_cfg = StorageConfig.from_mapping(
        {"backend": "ducklake"},
        config_dir=tmp_path,
        data_root=tmp_path,
    )
    assert local_cfg.object_store.backend == "local"

    store = DuckLakeStore.from_config(local_cfg, state_store=state)
    assert store.object_store.backend == "local"

    # Remote R2 config integrates cleanly into Postgres DuckLake secret statements.
    monkeypatch.setenv(
        "FININTEL_TEST_PG_DSN",
        "postgresql://finintel:pg_secret@127.0.0.1:5432/ducklake_catalog",
    )
    r2_cfg = ObjectStoreConfig(
        backend="r2",
        bucket="finintel-lake",
        prefix="v3/lakehouse",
        endpoint_url="https://acct12345.r2.cloudflarestorage.com",
    )
    pg_stmt, dl_stmt, attach_stmt = r2_cfg.build_remote_postgres_secret_statements(
        postgres_dsn_env="FININTEL_TEST_PG_DSN",
        catalog_alias="finintel_lake",
        read_only=True,
    )
    assert "TYPE postgres" in pg_stmt
    assert "DATA_PATH 's3://finintel-lake/v3/lakehouse'" in dl_stmt
    assert "READ_ONLY" in attach_stmt
    _ = (datetime.now(UTC) - timedelta(days=1)).isoformat()


def test_v3_07_object_store_from_env_and_mapping_and_s3_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FININTEL_OBJECT_STORE_BACKEND", "s3")
    monkeypatch.setenv("FININTEL_OBJECT_STORE_BUCKET", "aws-finintel-lake")
    monkeypatch.setenv("FININTEL_OBJECT_STORE_PREFIX", "prod/v3")
    monkeypatch.setenv("FININTEL_S3_REGION", "ap-southeast-1")
    monkeypatch.setenv("FININTEL_S3_ACCESS_KEY_ID", "AWS_KEY_ID_SECRET")
    monkeypatch.setenv("FININTEL_S3_SECRET_ACCESS_KEY", "AWS_SECRET_KEY_VALUE")

    s3_cfg = ObjectStoreConfig.from_env()
    assert s3_cfg.backend == "s3"
    assert s3_cfg.endpoint_url is None
    assert s3_cfg.region == "ap-southeast-1"
    assert s3_cfg.s3_key("snapshots/snap_1/issuers.parquet") == (
        "prod/v3/snapshots/snap_1/issuers.parquet"
    )
    sql = s3_cfg.build_duckdb_secret_sql()
    assert "TYPE S3" in sql
    assert "REGION 'ap-southeast-1'" in sql
    assert "ENDPOINT" not in sql

    parsed = ObjectStoreConfig.from_mapping(
        {
            "backend": "r2",
            "bucket": "finintel-r2",
            "endpoint_url": "https://acct777.r2.cloudflarestorage.com",
            "region": "auto",
        }
    )
    assert parsed.backend == "r2"
    assert parsed.r2_account_id() == "acct777"
    assert "AWS_SECRET_KEY_VALUE" not in repr(s3_cfg)


def test_v3_08_security_headers_and_strict_vercel_cors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from finintel.serving.api import create_app
    from finintel.serving.dependencies import (
        SecurityHeadersMiddleware,
        resolve_cors_origins,
        validate_cors_origins,
    )
    from finintel.storage.snapshot_publisher import SnapshotPublisher
    from tests.unit.test_arch07_08_postgres_pointer import _build_minimal_serving_tables

    _ = SecurityHeadersMiddleware

    # Wildcard '*' or wildcard subdomain MUST be rejected, especially with credentials.
    with pytest.raises(ValueError, match="wildcard|\\*"):
        validate_cors_origins(["*"], allow_credentials=True)
    with pytest.raises(ValueError, match="wildcard|\\*"):
        validate_cors_origins(["https://*.vercel.app"], allow_credentials=True)
    with pytest.raises(ValueError, match="allow_credentials"):
        validate_cors_origins([], allow_credentials=True)
    with pytest.raises(ValueError):
        resolve_cors_origins(["*"], allow_credentials=True)

    # Explicit Vercel origin is accepted cleanly.
    vercel_origins = resolve_cors_origins(
        ["https://finintel-app.vercel.app/"],
        allow_credentials=True,
    )
    assert vercel_origins == ("https://finintel-app.vercel.app",)

    data_root = tmp_path / "sec_data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    state.start_run("run-sec-1", "CLI_MANUAL", {"source": "fhsc"}, None)
    publisher = SnapshotPublisher(
        data_root / "published" / "snapshots", state_store=state
    )
    publisher.publish_snapshot(
        "snap_sec_001",
        _build_minimal_serving_tables(marker="v1", model_version="v1.0.0"),
        run_id="run-sec-1",
    )
    state.finish_run("run-sec-1", "SUCCEEDED", "snap_sec_001")

    monkeypatch.setenv("FININTEL_CORS_ORIGINS", "https://finintel-app.vercel.app")
    monkeypatch.setenv("FININTEL_CORS_ALLOW_CREDENTIALS", "1")
    app = create_app(data_root=data_root)
    with TestClient(app) as client:
        resp = client.get(
            "/api/v1/health",
            headers={"Origin": "https://finintel-app.vercel.app"},
        )
        assert resp.status_code == 200
        hdrs = {k.lower(): v for k, v in resp.headers.items()}
        assert "default-src 'self'" in hdrs.get("content-security-policy", "")
        assert "max-age=" in hdrs.get("strict-transport-security", "")
        assert hdrs.get("x-content-type-options") == "nosniff"
        assert hdrs.get("referrer-policy") == "strict-origin-when-cross-origin"
        assert hdrs.get("x-frame-options") == "DENY"
        assert (
            hdrs.get("access-control-allow-origin") == "https://finintel-app.vercel.app"
        )
        assert hdrs.get("access-control-allow-credentials") == "true"

        evil = client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in {k.lower() for k in evil.headers}


def test_v3_08_two_query_service_instances_evict_l1b_on_pg_snapshot_rollover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fastapi")
    import duckdb
    from fastapi.testclient import TestClient

    from finintel.serving.api import create_app
    from finintel.serving.dependencies import SnapshotPointerSync
    from finintel.serving.queries import DashboardQueryService
    from finintel.serving.queries._cache import (
        _ACTIVE_L1B_SNAPSHOT_BY_ROOT,
        _SNAPSHOT_OVERVIEW_CACHE,
        clear_query_caches,
    )
    from finintel.storage.ducklake_store import (
        PostgresActiveSnapshotPointer,
        ensure_ducklake_attached,
    )
    from finintel.storage.repository import ParquetRepository, clear_repository_caches
    from tests.unit.test_arch07_08_postgres_pointer import _build_minimal_serving_tables

    clear_repository_caches()
    clear_query_caches()

    writer_root = tmp_path / "writer"
    writer_state = StateStore(writer_root / "metadata" / "state.sqlite")
    writer_state.initialize()
    writer_store = DuckLakeStore(
        catalog_path=writer_root / "metadata" / "ducklake_catalog.sqlite",
        data_path=writer_root / "lakehouse" / "data",
        state_store=writer_state,
    )
    writer_state.start_run("run-r-1", "CLI_MANUAL", {"source": "fhsc"}, None)
    ver1 = writer_store.publish_snapshot(
        "snap_roll_001",
        _build_minimal_serving_tables(marker="v1", model_version="v1.0.0"),
        run_id="run-r-1",
    )
    writer_state.finish_run("run-r-1", "SUCCEEDED", "snap_roll_001")

    writer_state.start_run("run-r-2", "CLI_MANUAL", {"source": "fhsc"}, None)
    ver2 = writer_store.publish_snapshot(
        "snap_roll_002",
        _build_minimal_serving_tables(marker="v2", model_version="v2.0.0"),
        run_id="run-r-2",
    )
    writer_state.finish_run("run-r-2", "SUCCEEDED", "snap_roll_002")

    monkeypatch.setenv(
        "FININTEL_TEST_V308_PG_DSN",
        "postgresql://finintel:secret@127.0.0.1:5432/ducklake_catalog",
    )
    pointer_box: dict[str, PostgresActiveSnapshotPointer | None] = {
        "ptr": PostgresActiveSnapshotPointer(
            snapshot_id="snap_roll_001",
            ducklake_version=ver1,
            run_id="run-r-1",
            published_at="2026-02-01T00:00:00+00:00",
        )
    }

    def fake_pg_read(
        *, postgres_dsn_env: str, catalog_alias: str
    ) -> PostgresActiveSnapshotPointer | None:
        _ = (postgres_dsn_env, catalog_alias)
        return pointer_box["ptr"]

    real_attach = ensure_ducklake_attached

    def mocked_attach(
        connection: duckdb.DuckDBPyConnection,
        *,
        catalog_path: Path,
        data_path: Path,
        catalog_alias: str = "finintel_lake",
        catalog_driver: str = "sqlite",
        postgres_dsn_env: str = "FININTEL_DUCKLAKE_PG_DSN",
        read_only: bool = True,
        override_data_path: bool = False,
    ) -> None:
        _ = (
            catalog_path,
            data_path,
            catalog_driver,
            postgres_dsn_env,
            override_data_path,
        )
        real_attach(
            connection,
            catalog_path=writer_store.catalog_path,
            data_path=writer_store.data_path,
            catalog_alias=catalog_alias,
            catalog_driver="sqlite",
            read_only=read_only,
        )

    monkeypatch.setattr(
        "finintel.storage.repository.read_postgres_active_snapshot", fake_pg_read
    )
    monkeypatch.setattr(
        "finintel.storage.repository.ensure_ducklake_attached", mocked_attach
    )
    monkeypatch.setattr(
        "finintel.serving.dependencies.ensure_ducklake_attached", mocked_attach
    )

    serving_root = tmp_path / "serving_node"
    serving_root.mkdir()
    repo_1 = ParquetRepository(
        data_root=serving_root,
        lakehouse_data_path=writer_store.data_path,
        catalog_driver="postgres",
        postgres_dsn_env="FININTEL_TEST_V308_PG_DSN",
        backend="ducklake",
    )
    repo_2 = ParquetRepository(
        data_root=serving_root,
        lakehouse_data_path=writer_store.data_path,
        catalog_driver="postgres",
        postgres_dsn_env="FININTEL_TEST_V308_PG_DSN",
        backend="ducklake",
    )

    # 1. Two DashboardQueryService instances warm up on snap_roll_001.
    svc_1 = DashboardQueryService(repo_1)
    svc_2 = DashboardQueryService(repo_2)
    sync_1 = SnapshotPointerSync(svc_1.repository, sync_interval_seconds=0.0)
    sync_2 = SnapshotPointerSync(svc_2.repository, sync_interval_seconds=0.0)

    assert sync_1.sync().active_snapshot_id == "snap_roll_001"
    assert sync_2.sync().active_snapshot_id == "snap_roll_001"
    assert svc_1.get_snapshot_overview().snapshot_id == "snap_roll_001"
    assert svc_2.get_snapshot_overview().snapshot_id == "snap_roll_001"
    assert any("snap_roll_001" in str(k) for k in _SNAPSHOT_OVERVIEW_CACHE)

    # 2. Two FastAPI apps sharing the same pointer also serve snap_roll_001.
    monkeypatch.setenv("FININTEL_SNAPSHOT_SYNC_INTERVAL_SECONDS", "0")
    app_1 = create_app(repository=repo_1, duckdb_threads=1)
    app_2 = create_app(repository=repo_2, duckdb_threads=1)
    with TestClient(app_1) as client_1, TestClient(app_2) as client_2:
        r1 = client_1.get("/api/v1/snapshot/overview")
        r2 = client_2.get("/api/v1/snapshot/overview")
        assert (
            r1.status_code == 200 and r1.headers.get("X-Snapshot-ID") == "snap_roll_001"
        )
        assert (
            r2.status_code == 200 and r2.headers.get("X-Snapshot-ID") == "snap_roll_001"
        )

        # 3. Rollover finintel_active_snapshot pointer: snap_roll_001 -> snap_roll_002.
        pointer_box["ptr"] = PostgresActiveSnapshotPointer(
            snapshot_id="snap_roll_002",
            ducklake_version=ver2,
            run_id="run-r-2",
            published_at="2026-02-02T00:00:00+00:00",
        )

        res_1 = sync_1.sync()
        res_2 = sync_2.sync()
        assert res_1.changed is True and res_1.active_snapshot_id == "snap_roll_002"
        assert res_2.changed is True and res_2.active_snapshot_id == "snap_roll_002"
        assert not any("snap_roll_001" in str(k) for k in _SNAPSHOT_OVERVIEW_CACHE)
        assert all(
            val != (0, "snap_roll_001", ver1)
            for val in _ACTIVE_L1B_SNAPSHOT_BY_ROOT.values()
        )

        assert svc_1.get_snapshot_overview().snapshot_id == "snap_roll_002"
        assert svc_2.get_snapshot_overview().snapshot_id == "snap_roll_002"

        r1_after = client_1.get("/api/v1/snapshot/overview")
        r2_after = client_2.get("/api/v1/snapshot/overview")
        assert r1_after.status_code == 200
        assert r2_after.status_code == 200
        assert r1_after.headers.get("X-Snapshot-ID") == "snap_roll_002"
        assert r2_after.headers.get("X-Snapshot-ID") == "snap_roll_002"


def _parse_mem_limit_bytes(raw: str) -> int:
    text = raw.strip().lower()
    if text.endswith("g") or text.endswith("gb"):
        num = text.removesuffix("gb").removesuffix("g")
        return int(float(num) * (1024**3))
    if text.endswith("m") or text.endswith("mb"):
        num = text.removesuffix("mb").removesuffix("m")
        return int(float(num) * (1024**2))
    raise ValueError(f"Unsupported mem_limit unit: {raw!r}")


def test_v3_09_docker_compose_free_budget_and_network_isolation() -> None:
    import importlib
    from typing import Any

    yaml_mod: Any = importlib.import_module("yaml")

    repo_root = Path(__file__).resolve().parents[2]
    compose_path = repo_root / "docker-compose.free.yml"
    caddy_path = repo_root / "config" / "gateway" / "Caddyfile.free"
    workflow_path = repo_root / ".github" / "workflows" / "free-etl-fallback.yml"
    runbook_path = repo_root / "docs" / "runbooks" / "free_cloud_deploy.md"

    assert compose_path.is_file()
    assert caddy_path.is_file()
    assert workflow_path.is_file()
    assert runbook_path.is_file()

    data = yaml_mod.safe_load(compose_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    services = data["services"]
    networks = data["networks"]

    assert networks["internal_net"]["internal"] is True
    assert "egress_net" in networks
    assert networks["egress_net"].get("internal") is not True
    assert set(services.keys()) >= {
        "caddy",
        "api",
        "postgres",
        "valkey",
        "dagster-daemon",
        "dagster-webserver",
    }
    assert services["postgres"]["image"] == "pgvector/pgvector:pg16"
    assert services["valkey"]["image"] == "valkey/valkey:8-alpine"

    total_mem_bytes = 0
    for svc_name, svc in services.items():
        assert "mem_limit" in svc, f"{svc_name} missing mem_limit"
        total_mem_bytes += _parse_mem_limit_bytes(str(svc["mem_limit"]))
        svc_networks = set(svc.get("networks", []))
        if svc_name == "caddy":
            ports = [str(p) for p in svc.get("ports", [])]
            assert any(":80" in p for p in ports)
            assert any(":443" in p for p in ports)
        else:
            assert "ports" not in svc, f"{svc_name} must not expose public ports"
            assert "internal_net" in svc_networks
            if svc_name in {"api", "dagster-daemon"}:
                assert svc_networks == {"internal_net", "egress_net"}
            else:
                assert svc_networks == {"internal_net"}

    # Total container mem_limit MUST remain strictly < 8 GB for OCI 12 GB RAM.
    assert total_mem_bytes < 8 * (1024**3)
    assert str(services["api"]["environment"]["FININTEL_WORKERS"]) == "1"

    caddy_text = caddy_path.read_text(encoding="utf-8")
    assert "respond /metrics 403" in caddy_text
    assert "reverse_proxy api:8000" in caddy_text
    assert "Strict-Transport-Security" in caddy_text

    wf = yaml_mod.safe_load(workflow_path.read_text(encoding="utf-8"))
    assert wf["permissions"]["contents"] == "read"
    on_cfg = wf["on"] if "on" in wf else wf[True]
    assert "workflow_dispatch" in on_cfg
    assert "schedule" not in on_cfg  # Cron disabled by default per spec.

    runbook_text = runbook_path.read_text(encoding="utf-8")
    assert "OCI" in runbook_text and "Vercel" in runbook_text and "R2" in runbook_text
    assert (
        "Hugging Face" in runbook_text
        and "Neon" in runbook_text
        and "Upstash" in runbook_text
    )


def test_v3_09_backup_to_r2_retention_and_dry_run_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    from scripts.backup_to_r2 import (
        R2_WARN_BYTES,
        BackupArtifact,
        main as backup_main,
        plan_backup_retention,
    )

    now = datetime(2026, 11, 27, 0, 0, tzinfo=UTC)
    gb = 1024**3
    existing = [
        BackupArtifact(
            key="backups/2026-11-18/pg.dump",
            size_bytes=3 * gb,
            created_at=now - timedelta(days=9),
        ),
        BackupArtifact(
            key="backups/2026-11-24/pg.dump",
            size_bytes=3 * gb,
            created_at=now - timedelta(days=3),
        ),
        BackupArtifact(
            key="backups/2026-11-26/pg.dump",
            size_bytes=3 * gb,
            created_at=now - timedelta(days=1),
        ),
    ]
    new_artifact = BackupArtifact(
        key="backups/2026-11-27/pg.dump",
        size_bytes=2 * gb,
        created_at=now,
    )
    plan = plan_backup_retention(
        existing_objects=existing,
        new_artifact=new_artifact,
        retention_days=7,
        max_total_bytes=R2_WARN_BYTES,
        now=now,
    )
    assert "backups/2026-11-18/pg.dump" in plan.delete_keys
    assert (
        "backups/2026-11-24/pg.dump" in plan.delete_keys
    )  # Exceeds 8 GB cap with new 2 GB!
    assert "backups/2026-11-26/pg.dump" in plan.keep_keys
    assert plan.projected_total_bytes <= R2_WARN_BYTES

    data_root = tmp_path / "data"
    (data_root / "metadata").mkdir(parents=True)
    (data_root / "metadata" / "state.sqlite").write_bytes(b"sqlite-dummy")
    monkeypatch.setenv("FININTEL_S3_ACCESS_KEY_ID", "SECRET_KEY_MUST_NOT_PRINT")
    monkeypatch.setenv("FININTEL_S3_SECRET_ACCESS_KEY", "SECRET_VAL_MUST_NOT_PRINT")

    rc = backup_main(
        [
            "--dry-run",
            "--data-root",
            str(data_root),
            "--bucket",
            "finintel-r2-free",
            "--endpoint-url",
            "https://acct123.r2.cloudflarestorage.com",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "SECRET_KEY_MUST_NOT_PRINT" not in out
    assert "SECRET_VAL_MUST_NOT_PRINT" not in out
    payload = json.loads(out)
    assert payload["dry_run"] is True
    assert payload["target_bucket"] == "finintel-r2-free"
    assert payload["max_total_bytes"] == R2_WARN_BYTES


def test_v3_09_r2_env_wiring_in_storage_resource_and_serving_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("dagster")
    import duckdb

    from finintel.orchestration.resources import FinIntelPathsResource, StorageResource
    from finintel.serving.dependencies import _attach_ducklake_read_only_if_applicable
    from finintel.storage.repository import ParquetRepository

    data_root = tmp_path / "data"
    data_root.mkdir(parents=True)
    paths = FinIntelPathsResource(
        project_root=str(tmp_path),
        data_root=str(data_root),
    )
    resource = StorageResource()

    # Default local mode when FININTEL_OBJECT_STORE_BACKEND is unset
    monkeypatch.delenv("FININTEL_OBJECT_STORE_BACKEND", raising=False)
    local_cfg = resource.resolve_storage_config(paths)
    assert local_cfg.object_store.backend == "local"
    assert local_cfg.object_store.is_remote is False

    # Remote R2 mode wired automatically from environment variables
    monkeypatch.setenv("FININTEL_OBJECT_STORE_BACKEND", "r2")
    monkeypatch.setenv("FININTEL_OBJECT_STORE_BUCKET", "finintel-r2-prod")
    monkeypatch.setenv("FININTEL_OBJECT_STORE_PREFIX", "v3/lakehouse")
    monkeypatch.setenv(
        "FININTEL_R2_ENDPOINT_URL", "https://acct999.r2.cloudflarestorage.com"
    )
    monkeypatch.setenv("FININTEL_S3_REGION", "auto")

    r2_cfg = resource.resolve_storage_config(paths)
    assert r2_cfg.object_store.backend == "r2"
    assert r2_cfg.object_store.is_remote is True
    assert r2_cfg.object_store.bucket == "finintel-r2-prod"
    assert r2_cfg.object_store.r2_account_id() == "acct999"

    # Serving read-only attach routes through ObjectStoreConfig.attach_remote_ducklake when remote
    captured: list[dict[str, object]] = []

    def fake_attach_remote(
        self: ObjectStoreConfig,
        connection: duckdb.DuckDBPyConnection,
        *,
        catalog_path: Path,
        catalog_alias: str,
        catalog_driver: str,
        postgres_dsn_env: str,
        read_only: bool = True,
        override_data_path: bool = False,
    ) -> None:
        _ = (connection, override_data_path)
        captured.append(
            {
                "backend": self.backend,
                "bucket": self.bucket,
                "catalog_path": catalog_path,
                "catalog_alias": catalog_alias,
                "catalog_driver": catalog_driver,
                "postgres_dsn_env": postgres_dsn_env,
                "read_only": read_only,
            }
        )

    monkeypatch.setattr(ObjectStoreConfig, "attach_remote_ducklake", fake_attach_remote)
    repo = ParquetRepository(
        data_root=data_root,
        catalog_driver="postgres",
        postgres_dsn_env="FININTEL_TEST_R2_PG_DSN",
        backend="ducklake",
    )
    con = duckdb.connect(":memory:")
    try:
        _attach_ducklake_read_only_if_applicable(con, repo)
    finally:
        con.close()

    assert len(captured) == 1
    assert captured[0]["backend"] == "r2"
    assert captured[0]["bucket"] == "finintel-r2-prod"
    assert captured[0]["catalog_driver"] == "postgres"
    assert captured[0]["postgres_dsn_env"] == "FININTEL_TEST_R2_PG_DSN"
    assert captured[0]["read_only"] is True
