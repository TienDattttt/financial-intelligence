"""Tests for ARCH-07b (Unified DBSP Transform Flow) and ARCH-08 (Postgres Active Snapshot Pointer)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl

from finintel.pipeline.analytics import AnalyticsHandler
from finintel.pipeline.runner import RunRequest, Stage, StepContext
from finintel.pipeline.state_store import StateStore
from finintel.storage.schemas import BENCHMARKS_TABLE, RATIOS_TABLE, RED_FLAGS_TABLE
from finintel.transform.incremental import run_incremental_analytics_transform
from tests.unit.test_layer4_analytics import facts, issuer


def test_arch07b_cli_analytics_handler_matches_dbsp_incremental_engine(
    tmp_path: Path,
) -> None:
    """ARCH-07b: CLI AnalyticsHandler delegates to run_incremental_analytics_transform
    and produces identical ratios, benchmarks, and red_flags as Dagster's DBSP engine."""
    history = pl.concat(
        [
            facts(
                {
                    (2024, None): {
                        "BS_TOTAL_ASSETS": 1000.0,
                        "BS_TOTAL_LIABILITIES": 400.0,
                        "BS_TOTAL_EQUITY": 600.0,
                        "IS_NET_REVENUE": 500.0,
                        "IS_NET_INCOME": 80.0,
                    },
                    (2025, None): {
                        "BS_TOTAL_ASSETS": 1100.0,
                        "BS_TOTAL_LIABILITIES": 420.0,
                        "BS_TOTAL_EQUITY": 680.0,
                        "IS_NET_REVENUE": 550.0,
                        "IS_NET_INCOME": 95.0,
                    },
                },
                issuer_id="FPT",
            ),
            facts(
                {
                    (2024, None): {
                        "BS_TOTAL_ASSETS": 900.0,
                        "BS_TOTAL_LIABILITIES": 350.0,
                        "BS_TOTAL_EQUITY": 550.0,
                        "IS_NET_REVENUE": 450.0,
                        "IS_NET_INCOME": 65.0,
                    },
                    (2025, None): {
                        "BS_TOTAL_ASSETS": 950.0,
                        "BS_TOTAL_LIABILITIES": 360.0,
                        "BS_TOTAL_EQUITY": 590.0,
                        "IS_NET_REVENUE": 480.0,
                        "IS_NET_INCOME": 72.0,
                    },
                },
                issuer_id="CMG",
            ),
        ]
    )
    issuers_df = pl.concat([issuer("FPT"), issuer("CMG")])

    curated = tmp_path / "curated"
    curated.mkdir()
    issuers_df.write_parquet(curated / "issuers.parquet")
    history.write_parquet(curated / "financial_facts.parquet")

    state = StateStore(tmp_path / "metadata" / "state.sqlite")
    state.initialize()
    req = RunRequest(source="file")
    state.start_run("run-arch07b", "CLI_MANUAL", req.log_arguments(), None)

    AnalyticsHandler(state, tmp_path, {"as_of": "2026-06-01"})(
        StepContext("run-arch07b", Stage.ANALYTICS, req, (), None)
    )

    batch = curated / "analytics" / "run-arch07b"
    meta = json.loads((batch / "_analytics_meta.json").read_text(encoding="utf-8"))
    assert meta["transform_engine"] == "dbsp_incremental"
    assert meta["ml_enabled"] is False

    # Compare against direct DBSP engine output (same engine used by Dagster assets.py)
    bundle = run_incremental_analytics_transform(
        history,
        issuers_df,
        as_of=date(2026, 6, 1),
        run_id="run-arch07b-direct",
        persist_state=False,
    )
    assert pl.read_parquet(batch / RATIOS_TABLE).equals(bundle.financial_ratios)
    assert pl.read_parquet(batch / BENCHMARKS_TABLE).equals(bundle.industry_benchmarks)
    assert pl.read_parquet(batch / RED_FLAGS_TABLE).equals(bundle.accounting_red_flags)


def test_arch08_lazy_import_psycopg_never_loaded_for_sqlite(
    monkeypatch: object,
) -> None:
    """ARCH-08 (M-03): psycopg is lazily imported only when catalog_driver='postgres'."""
    import importlib

    import pytest

    from finintel.exceptions import ContractViolationError
    from finintel.storage import ducklake_store

    mp = monkeypatch
    assert isinstance(mp, pytest.MonkeyPatch)
    mp.setenv(
        "FININTEL_TEST_LAZY_PG_DSN",
        "postgresql://user:pass@127.0.0.1:5432/ducklake_catalog",
    )

    real_import = importlib.import_module

    def fake_import(name: str, package: str | None = None) -> object:
        if name == "psycopg":
            raise ImportError("simulated missing platform group")
        return real_import(name, package)

    mp.setattr("importlib.import_module", fake_import)
    with pytest.raises(ContractViolationError, match="platform"):
        ducklake_store.read_postgres_active_snapshot(
            postgres_dsn_env="FININTEL_TEST_LAZY_PG_DSN",
            catalog_alias="finintel_lake",
        )


def _build_minimal_serving_tables(
    *, marker: str, model_version: str
) -> dict[str, pl.DataFrame]:
    from finintel.storage.manifest_export import build_latest_facts_table
    from finintel.storage.schemas import (
        ANALYTICS_SCHEMAS,
        AUDIT_QUARANTINE_SCHEMA,
        AUDIT_QUARANTINE_TABLE,
        BENCHMARKS_TABLE,
        FILINGS_MANIFEST_TABLE,
        ML_SCORES_SCHEMA,
        ML_SCORES_TABLE,
        MODEL_CARD_META_SCHEMA,
        MODEL_CARD_META_TABLE,
        QUALITY_SUMMARY_SCHEMA,
        QUALITY_SUMMARY_TABLE,
        RED_FLAGS_TABLE,
    )
    from tests.unit.test_layer3_ducklake_storage import (
        _sample_facts,
        _sample_issuers,
        _sample_manifest,
        _sample_ratios,
    )

    issuers_df = _sample_issuers(marker)
    facts_df = _sample_facts(value_offset=0.0 if marker == "v1" else 10.0)
    latest_df = build_latest_facts_table(facts_df, as_of=date(2026, 2, 1))
    manifest_df = _sample_manifest(marker)
    ratios_df = _sample_ratios(
        row_count=3, value_offset=0.0 if marker == "v1" else 0.05
    )
    cutoffs = {
        "R2_min_pd": 0.02,
        "R3_min_pd": 0.05,
        "R4_min_pd": 0.12,
        "R5_min_pd": 0.25,
    }
    model_card_payload = {
        "schema_version": 1,
        "run_id": f"run-{marker}",
        "model_version": model_version,
        "model_role": "early_warning_regulatory_distress",
        "target_mode": "REGULATORY_DISTRESS",
        "statement_scope": "CONSOLIDATED",
        "feature_set": "sector_adjusted",
        "feature_count": 2,
        "selected_features": ["roa", "debt_to_equity"],
        "peer_standardization_mode": "sector_z",
        "trained_as_of": "2026-02-01",
        "generated_at": "2026-02-01T00:00:00+00:00",
        "artifact_sha256": "a" * 64,
        "platt_slope": 1.0,
        "platt_intercept": 0.0,
        "train_prevalence": 0.04,
        "risk_tier_cutoffs": cutoffs,
        "f2_thresholds": {"xgboost": 0.15},
        "walk_forward": {
            "test_years": [2025],
            "skipped_years": [],
            "per_year": [],
            "summary_all_oos": [],
            "summary_true_unseen": [],
        },
        "risk_tier_reliability": {
            "cutoffs": cutoffs,
            "tiers": [],
            "is_monotone_non_decreasing": True,
        },
        "taxonomy_drift_psi": {
            "reference_taxonomy": "VAS_TT200_2014",
            "target_taxonomy": "VAS_TT99_2025",
            "reference_rows": 100,
            "target_rows": 50,
            "features_psi_gt_0_25": [],
            "feature_drift": [],
        },
        "case_studies": [],
        "limitations": ["Corporate issuers only."],
    }
    model_card_df = pl.DataFrame(
        {
            "model_card_id": ["active"],
            "schema_version": [1],
            "run_id": [f"run-{marker}"],
            "model_version": [model_version],
            "feature_set": ["sector_adjusted"],
            "feature_count": [24],
            "target_mode": ["REGULATORY_DISTRESS"],
            "statement_scope": ["CONSOLIDATED"],
            "peer_standardization_mode": ["sector_z"],
            "trained_as_of": [date(2026, 2, 1)],
            "artifact_sha256": ["a" * 64],
            "model_card_json": [json.dumps(model_card_payload)],
            "generated_at": ["2026-02-01T00:00:00+00:00"],
        },
        schema=MODEL_CARD_META_SCHEMA,
    )
    return {
        "issuers.parquet": issuers_df,
        "financial_facts.parquet": facts_df,
        "latest_facts.parquet": latest_df,
        FILINGS_MANIFEST_TABLE: manifest_df,
        RATIOS_TABLE: ratios_df,
        BENCHMARKS_TABLE: pl.DataFrame(schema=ANALYTICS_SCHEMAS[BENCHMARKS_TABLE]),
        RED_FLAGS_TABLE: pl.DataFrame(schema=ANALYTICS_SCHEMAS[RED_FLAGS_TABLE]),
        ML_SCORES_TABLE: pl.DataFrame(schema=ML_SCORES_SCHEMA),
        QUALITY_SUMMARY_TABLE: pl.DataFrame(schema=QUALITY_SUMMARY_SCHEMA),
        AUDIT_QUARANTINE_TABLE: pl.DataFrame(schema=AUDIT_QUARANTINE_SCHEMA),
        MODEL_CARD_META_TABLE: model_card_df,
    }


def test_arch08_serving_without_state_sqlite_or_metadata_dir_ttl_cache_and_rollback(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """ARCH-08 (H-04): FastAPI serving in Postgres mode works with ZERO state.sqlite and
    ZERO data/metadata/, caches pointer reads for 500 ms, serves /api/v2/meta/model-card
    from DuckLake model_card_meta, and observes rollback cleanly."""
    import duckdb
    import pytest
    from fastapi.testclient import TestClient

    from finintel.serving.api import create_app
    from finintel.storage import DuckLakeStore, ParquetRepository
    from finintel.storage.ducklake_store import (
        PostgresActiveSnapshotPointer,
        ensure_ducklake_attached,
    )
    from finintel.storage.repository import clear_repository_caches

    mp = monkeypatch
    assert isinstance(mp, pytest.MonkeyPatch)
    clear_repository_caches()

    # 1. Publish two snapshots into a backing DuckLake catalog (simulating remote Postgres catalog).
    writer_root = tmp_path / "writer_data"
    writer_state = StateStore(writer_root / "metadata" / "state.sqlite")
    writer_state.initialize()
    writer_store = DuckLakeStore(
        catalog_path=writer_root / "metadata" / "ducklake_catalog.sqlite",
        data_path=writer_root / "lakehouse" / "data",
        state_store=writer_state,
    )
    writer_state.start_run("run-pg-1", "CLI_MANUAL", {"source": "fhsc"}, None)
    ver1 = writer_store.publish_snapshot(
        "snap_pg_001",
        _build_minimal_serving_tables(marker="v1", model_version="v1.0.0"),
        run_id="run-pg-1",
    )
    writer_state.finish_run("run-pg-1", "SUCCEEDED", "snap_pg_001")

    writer_state.start_run("run-pg-2", "CLI_MANUAL", {"source": "fhsc"}, None)
    ver2 = writer_store.publish_snapshot(
        "snap_pg_002",
        _build_minimal_serving_tables(marker="v2", model_version="v2.0.0"),
        run_id="run-pg-2",
    )
    writer_state.finish_run("run-pg-2", "SUCCEEDED", "snap_pg_002")

    # 2. Serving node has NO data/metadata/ and NO state.sqlite at all!
    serving_root = tmp_path / "serving_node_data"
    serving_root.mkdir()
    assert not (serving_root / "metadata").exists()
    assert not (serving_root / "metadata" / "state.sqlite").exists()

    mp.setenv(
        "FININTEL_ARCH08_PG_DSN",
        "postgresql://finintel:secret@127.0.0.1:5432/ducklake_catalog",
    )

    active_pointer_box: dict[str, PostgresActiveSnapshotPointer | None] = {
        "ptr": PostgresActiveSnapshotPointer(
            snapshot_id="snap_pg_002",
            ducklake_version=ver2,
            run_id="run-pg-2",
            published_at="2026-02-02T00:00:00+00:00",
        )
    }
    pg_read_calls: list[tuple[str, str]] = []

    def fake_pg_read(
        *, postgres_dsn_env: str, catalog_alias: str
    ) -> PostgresActiveSnapshotPointer | None:
        pg_read_calls.append((postgres_dsn_env, catalog_alias))
        return active_pointer_box["ptr"]

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

    mp.setattr(
        "finintel.storage.repository.read_postgres_active_snapshot", fake_pg_read
    )
    mp.setattr("finintel.storage.repository.ensure_ducklake_attached", mocked_attach)
    mp.setattr("finintel.serving.dependencies.ensure_ducklake_attached", mocked_attach)

    repo = ParquetRepository(
        data_root=serving_root,
        lakehouse_data_path=writer_store.data_path,
        catalog_driver="postgres",
        postgres_dsn_env="FININTEL_ARCH08_PG_DSN",
        backend="ducklake",
    )
    app = create_app(repository=repo, duckdb_threads=1)
    with TestClient(app) as client:
        # Serve health, companies, and /api/v2/meta/model-card without state.sqlite or metadata/.
        r_ready = client.get("/health/ready")
        assert r_ready.status_code == 200
        assert r_ready.json()["active_snapshot_id"] == "snap_pg_002"

        r_card = client.get("/api/v2/meta/model-card")
        assert r_card.status_code == 200
        assert r_card.json()["model_version"] == "v2.0.0"

        for _ in range(4):
            r_ov = client.get("/api/v1/snapshot/overview")
            assert r_ov.status_code == 200

        # TTL cache (500 ms) must coalesce all requests within the window to 1 Postgres read.
        assert len(pg_read_calls) == 1

        # Rollback pointer to snap_pg_001 and expire cache -> serving immediately switches to v1.
        active_pointer_box["ptr"] = PostgresActiveSnapshotPointer(
            snapshot_id="snap_pg_001",
            ducklake_version=ver1,
            run_id="run-pg-1",
            published_at="2026-02-01T00:00:00+00:00",
        )
        clear_repository_caches()

        r_ready_rb = client.get("/health/ready")
        assert r_ready_rb.status_code == 200
        assert r_ready_rb.json()["active_snapshot_id"] == "snap_pg_001"

        r_card_rb = client.get("/api/v2/meta/model-card")
        assert r_card_rb.status_code == 200
        assert r_card_rb.json()["model_version"] == "v1.0.0"
        assert len(pg_read_calls) == 2

    assert not (serving_root / "metadata").exists()


def test_arch08_split_brain_postgres_pointer_returns_503(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """ARCH-08: split-brain between Postgres pointer and DuckLake catalog returns HTTP 503."""
    import duckdb
    import pytest
    from fastapi.testclient import TestClient

    from finintel.serving.api import create_app
    from finintel.storage import DuckLakeStore, ParquetRepository
    from finintel.storage.ducklake_store import (
        PostgresActiveSnapshotPointer,
        ensure_ducklake_attached,
    )
    from finintel.storage.repository import clear_repository_caches

    mp = monkeypatch
    assert isinstance(mp, pytest.MonkeyPatch)
    clear_repository_caches()

    writer_root = tmp_path / "writer_sb"
    writer_state = StateStore(writer_root / "metadata" / "state.sqlite")
    writer_state.initialize()
    writer_store = DuckLakeStore(
        catalog_path=writer_root / "metadata" / "ducklake_catalog.sqlite",
        data_path=writer_root / "lakehouse" / "data",
        state_store=writer_state,
    )
    writer_state.start_run("run-sb-1", "CLI_MANUAL", {"source": "fhsc"}, None)
    ver1 = writer_store.publish_snapshot(
        "snap_sb_001",
        _build_minimal_serving_tables(marker="v1", model_version="v1.0.0"),
        run_id="run-sb-1",
    )
    writer_state.finish_run("run-sb-1", "SUCCEEDED", "snap_sb_001")

    serving_root = tmp_path / "serving_sb"
    serving_root.mkdir()
    mp.setenv(
        "FININTEL_ARCH08_SB_DSN",
        "postgresql://finintel:secret@127.0.0.1:5432/ducklake_catalog",
    )

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

    mp.setattr("finintel.storage.repository.ensure_ducklake_attached", mocked_attach)
    mp.setattr("finintel.serving.dependencies.ensure_ducklake_attached", mocked_attach)

    # Case A: Postgres pointer version diverges from DuckLake _snapshot_audit version.
    mp.setattr(
        "finintel.storage.repository.read_postgres_active_snapshot",
        lambda **_kw: PostgresActiveSnapshotPointer(
            snapshot_id="snap_sb_001",
            ducklake_version=ver1 + 99,
            run_id="run-sb-1",
            published_at="2026-02-01T00:00:00+00:00",
        ),
    )

    repo = ParquetRepository(
        data_root=serving_root,
        lakehouse_data_path=writer_store.data_path,
        catalog_driver="postgres",
        postgres_dsn_env="FININTEL_ARCH08_SB_DSN",
        backend="ducklake",
    )
    app = create_app(repository=repo, duckdb_threads=1)
    with TestClient(app) as client:
        r_ready = client.get("/health/ready")
        assert r_ready.status_code == 503
        assert r_ready.json()["status"] == "unready"

        r_health = client.get("/api/v1/health")
        assert r_health.status_code == 503
        assert r_health.json()["status"] == "degraded"

        # Case B: Postgres pointer references a non-existent snapshot_id.
        clear_repository_caches()
        mp.setattr(
            "finintel.storage.repository.read_postgres_active_snapshot",
            lambda **_kw: PostgresActiveSnapshotPointer(
                snapshot_id="snap_ghost_999",
                ducklake_version=999,
                run_id="run-ghost",
                published_at="2026-02-01T00:00:00+00:00",
            ),
        )
        r_ready_ghost = client.get("/health/ready")
        assert r_ready_ghost.status_code == 503


def test_arch08_ducklake_store_syncs_postgres_pointer_across_publish_rollback_maintain(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """ARCH-08 (H-04): DuckLakeStore updates Postgres pointer in publish_snapshot() and
    rollback_snapshot(), and protects the Postgres-active version in maintain()."""
    import duckdb
    import pytest

    from finintel.storage import DuckLakeStore
    from finintel.storage.ducklake_store import (
        PostgresActiveSnapshotPointer,
        ensure_ducklake_attached,
    )
    from tests.unit.test_layer3_ducklake_storage import (
        _sample_facts,
        _sample_issuers,
    )

    mp = monkeypatch
    assert isinstance(mp, pytest.MonkeyPatch)

    data_root = tmp_path / "pg_sync_data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    backing_sqlite = data_root / "metadata" / "backing_catalog.sqlite"

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
        _ = (catalog_path, catalog_driver, postgres_dsn_env, override_data_path)
        real_attach(
            connection,
            catalog_path=backing_sqlite,
            data_path=data_path,
            catalog_alias=catalog_alias,
            catalog_driver="sqlite",
            read_only=read_only,
        )

    pg_pointer_history: list[PostgresActiveSnapshotPointer] = []

    def fake_upsert(
        *,
        postgres_dsn_env: str,
        catalog_alias: str,
        snapshot_id: str,
        ducklake_version: int,
        run_id: str,
        published_at: str,
    ) -> None:
        _ = (postgres_dsn_env, catalog_alias)
        pg_pointer_history.append(
            PostgresActiveSnapshotPointer(
                snapshot_id=snapshot_id,
                ducklake_version=ducklake_version,
                run_id=run_id,
                published_at=published_at,
            )
        )

    def fake_read(
        *, postgres_dsn_env: str, catalog_alias: str
    ) -> PostgresActiveSnapshotPointer | None:
        _ = (postgres_dsn_env, catalog_alias)
        return pg_pointer_history[-1] if pg_pointer_history else None

    mp.setattr(
        "finintel.storage.ducklake_store.ensure_ducklake_attached", mocked_attach
    )
    mp.setattr(
        "finintel.storage.ducklake_store.upsert_postgres_active_snapshot", fake_upsert
    )
    mp.setattr(
        "finintel.storage.ducklake_store.read_postgres_active_snapshot", fake_read
    )

    store = DuckLakeStore(
        catalog_path=backing_sqlite,
        data_path=data_root / "lakehouse" / "data",
        state_store=state,
        catalog_driver="postgres",
        postgres_dsn_env="FININTEL_ARCH08_SYNC_DSN",
        keep_snapshots=2,
        min_snapshot_age_seconds=0,
    )

    versions: list[int] = []
    for idx in (1, 2, 3):
        rid = f"run-sync-{idx}"
        sid = f"snap_sync_00{idx}"
        state.start_run(rid, "CLI_MANUAL", {"source": "fhsc"}, None)
        ver = store.publish_snapshot(
            sid,
            {
                "issuers.parquet": _sample_issuers(f"v{idx}"),
                "financial_facts.parquet": _sample_facts(value_offset=float(idx)),
            },
            run_id=rid,
        )
        state.finish_run(rid, "SUCCEEDED", sid)
        versions.append(ver)

    assert [p.snapshot_id for p in pg_pointer_history] == [
        "snap_sync_001",
        "snap_sync_002",
        "snap_sync_003",
    ]
    assert [p.ducklake_version for p in pg_pointer_history] == versions

    # Rollback to snap_sync_001 -> Postgres pointer MUST update to snap_sync_001.
    rb_ver = store.rollback_snapshot("snap_sync_001", state)
    assert rb_ver == versions[0]
    assert pg_pointer_history[-1].snapshot_id == "snap_sync_001"
    assert pg_pointer_history[-1].ducklake_version == versions[0]

    # Even if state.sqlite were pointing at snap_sync_003, maintain() MUST protect
    # the Postgres-active snapshot version (versions[0]) from expiration.
    with state.connection() as sconn:
        sconn.execute(
            "UPDATE active_snapshot SET snapshot_id = 'snap_sync_003' WHERE singleton = 1"
        )
    maint = store.maintain(keep_snapshots=2, min_snapshot_age_seconds=0)
    assert versions[0] in maint["protected_versions"]  # type: ignore[operator]
    assert versions[0] in maint["remaining_versions"]  # type: ignore[operator]


def test_arch08_live_docker_postgres_pointer_roundtrip_and_zero_ducklake_versions(
    monkeypatch: object,
) -> None:
    """Live PostgreSQL 16 integration test (cleanly skipped if Docker Postgres is offline)."""
    import pytest

    from finintel.storage.ducklake_store import (
        POSTGRES_ACTIVE_SNAPSHOT_TABLE,
        read_postgres_active_snapshot,
        upsert_postgres_active_snapshot,
    )
    from finintel.storage.repository import (
        _read_postgres_active_snapshot_pointer_cached,
        clear_repository_caches,
    )

    try:
        import psycopg
    except ImportError:
        pytest.skip("psycopg not installed (mvp-checks environment)")

    dsn = "postgresql://finintel:finintel_dev@127.0.0.1:5432/ducklake_catalog"
    try:
        with psycopg.connect(dsn, connect_timeout=2) as probe_conn:
            probe_conn.execute("SELECT 1")
    except Exception:
        pytest.skip("Local Docker PostgreSQL is not reachable on 127.0.0.1:5432")

    mp = monkeypatch
    assert isinstance(mp, pytest.MonkeyPatch)
    mp.setenv("FININTEL_LIVE_PG_TEST_DSN", dsn)
    clear_repository_caches()

    test_alias = "arch08_live_test_alias"
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            before_dl_snaps = conn.execute(
                "SELECT COUNT(*) FROM public.ducklake_snapshot"
            ).fetchone()
            before_count = int(before_dl_snaps[0]) if before_dl_snaps else 0

        upsert_postgres_active_snapshot(
            postgres_dsn_env="FININTEL_LIVE_PG_TEST_DSN",
            catalog_alias=test_alias,
            snapshot_id="snap_live_001",
            ducklake_version=10,
            run_id="run-live-1",
            published_at="2026-02-01T00:00:00+00:00",
        )
        ptr1 = read_postgres_active_snapshot(
            postgres_dsn_env="FININTEL_LIVE_PG_TEST_DSN",
            catalog_alias=test_alias,
        )
        assert ptr1 is not None
        assert ptr1.snapshot_id == "snap_live_001"
        assert ptr1.ducklake_version == 10

        # Upsert again (simulating publish v2 / rollback) -> zero new DuckLake versions!
        upsert_postgres_active_snapshot(
            postgres_dsn_env="FININTEL_LIVE_PG_TEST_DSN",
            catalog_alias=test_alias,
            snapshot_id="snap_live_002",
            ducklake_version=11,
            run_id="run-live-2",
            published_at="2026-02-02T00:00:00+00:00",
        )
        ptr2 = _read_postgres_active_snapshot_pointer_cached(
            "FININTEL_LIVE_PG_TEST_DSN", test_alias
        )
        assert ptr2 is not None
        assert ptr2.snapshot_id == "snap_live_002"
        assert ptr2.ducklake_version == 11

        with psycopg.connect(dsn, connect_timeout=5) as conn:
            after_dl_snaps = conn.execute(
                "SELECT COUNT(*) FROM public.ducklake_snapshot"
            ).fetchone()
            after_count = int(after_dl_snaps[0]) if after_dl_snaps else 0
            assert after_count == before_count
    finally:
        with psycopg.connect(dsn, connect_timeout=5) as cleanup_conn:
            cleanup_conn.execute(
                f"DELETE FROM {POSTGRES_ACTIVE_SNAPSHOT_TABLE} WHERE catalog_alias = %s",
                (test_alias,),
            )
            cleanup_conn.commit()
        clear_repository_caches()
