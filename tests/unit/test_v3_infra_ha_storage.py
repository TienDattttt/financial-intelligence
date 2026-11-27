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
