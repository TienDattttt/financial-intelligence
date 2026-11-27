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
