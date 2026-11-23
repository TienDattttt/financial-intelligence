"""Unit and integration tests for Wave 5 Serving API: GET /api/v2/meta/model-card and POST /api/v1/stress-test tickers filter."""

from __future__ import annotations

import shutil
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from fastapi.testclient import TestClient

from finintel.ml.tables import build_ml_artifacts
from finintel.pipeline.state_store import StateStore
from finintel.serving.api import create_app
from finintel.serving.schemas import ModelCardResponse, StressScenarioRequest
from finintel.storage.ducklake_store import DuckLakeStore
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import (
    BENCHMARKS_TABLE,
    ML_SCORES_TABLE,
    MODEL_CARD_META_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)
from finintel.storage.snapshot_publisher import SnapshotPublisher
from tests.unit.test_ml08_model_card import build_ml08_real_shape_fixture


@pytest.fixture()
def ml08_ducklake_root(tmp_path: Path) -> tuple[Path, str, str]:
    """Build a DuckLake snapshot containing real analytics + ML + model_card_meta (no data/metadata/ml_runs)."""
    from finintel.analytics.tables import build_analytics_tables

    facts, issuers, regulatory_events = build_ml08_real_shape_fixture()
    as_of = date(2026, 8, 15)
    run_id = "run_serving_ml08_001"
    snapshot_id = "snap_serving_ml08_001"

    analytics = build_analytics_tables(facts, issuers, as_of=as_of)
    ml_artifacts = build_ml_artifacts(
        facts,
        issuers,
        as_of=as_of,
        validation_start=None,
        test_start=date(2024, 1, 1),
        approved_target=True,
        require_exact_filing=True,
        feature_set="sector_adjusted",
        target_mode="REGULATORY_DISTRESS",
        regulatory_events=regulatory_events,
        min_unique_positive_issuers=2,
        peer_benchmarks=analytics[BENCHMARKS_TABLE],
        run_id=run_id,
        walk_forward_n_bootstrap=60,
    )
    assert ml_artifacts.model_card_meta is not None

    data_root = tmp_path / "lake_data_root"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    state.start_run(run_id, "MANUAL", {"source": "test"}, "abcdef123456")

    store = DuckLakeStore(
        catalog_path=data_root / "metadata" / "ducklake_catalog.sqlite",
        data_path=data_root / "lakehouse" / "data",
        state_store=state,
    )
    tables = {
        "issuers.parquet": issuers,
        "financial_facts.parquet": facts,
        RATIOS_TABLE: analytics[RATIOS_TABLE],
        BENCHMARKS_TABLE: analytics[BENCHMARKS_TABLE],
        RED_FLAGS_TABLE: analytics[RED_FLAGS_TABLE],
        ML_SCORES_TABLE: ml_artifacts.scores_table,
        MODEL_CARD_META_TABLE: ml_artifacts.model_card_meta,
    }
    try:
        store.publish_snapshot(
            snapshot_id,
            tables,
            run_id=run_id,
            metadata={"dagster_run_id": run_id, "git_commit_sha": "abcdef123456"},
        )
    except duckdb.Error as exc:
        pytest.skip(f"DuckLake extension unavailable in this environment: {exc}")
    state.finish_run(run_id, "SUCCEEDED", snapshot_id)

    # Confirm no data/metadata/ml_runs or ml_models exist on disk (H-04)
    assert not (data_root / "metadata" / "ml_runs").exists()
    assert not (data_root / "metadata" / "ml_models").exists()
    return data_root, snapshot_id, run_id


def test_v2_meta_model_card_valkey_etag_lineage_and_metadata_independence(
    ml08_ducklake_root: tuple[Path, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/v2/meta/model-card serves full real DuckLake payload with Valkey L2, ETag 304, and lineage."""
    data_root, snapshot_id, run_id = ml08_ducklake_root

    kv_store: dict[str, bytes] = {}

    class _InMemoryRedisClient:
        def ping(self) -> bool:
            return True

        def get(self, key: str) -> bytes | None:
            return kv_store.get(key)

        def setex(self, key: str, _ttl: int, payload: bytes) -> bool:
            kv_store[key] = bytes(payload)
            return True

        def close(self) -> None:
            return None

    try:
        import redis

        monkeypatch.setattr(
            redis.Redis,
            "from_url",
            classmethod(lambda _cls, *_args, **_kwargs: _InMemoryRedisClient()),
        )
    except ImportError:

        class _StubRedis:
            @classmethod
            def from_url(
                cls, *_args: object, **_kwargs: object
            ) -> _InMemoryRedisClient:
                return _InMemoryRedisClient()

        monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=_StubRedis))

    repo = ParquetRepository(data_root=data_root, backend="ducklake")
    app = create_app(repository=repo, valkey_url="redis://127.0.0.1:6379/0")

    with TestClient(app) as client:
        r1 = client.get("/api/v2/meta/model-card")
        assert r1.status_code == 200
        assert r1.headers.get("X-Cache") == "MISS"
        assert r1.headers.get("X-Snapshot-ID") == snapshot_id
        assert r1.headers.get("X-Dagster-Run-ID") == run_id
        assert r1.headers.get("X-Git-Commit-SHA") == "abcdef123456"
        etag = r1.headers.get("ETag")
        assert etag is not None

        # Second call hits Valkey L2
        r2 = client.get("/api/v2/meta/model-card")
        assert r2.status_code == 200
        assert r2.headers.get("X-Cache") == "HIT"
        assert r2.json() == r1.json()

        # Conditional ETag -> 304
        r304 = client.get("/api/v2/meta/model-card", headers={"If-None-Match": etag})
        assert r304.status_code == 304

        parsed = ModelCardResponse(**r1.json())
        assert parsed.snapshot_id == snapshot_id
        assert parsed.run_id == run_id
        assert parsed.feature_count == 23
        assert parsed.walk_forward.test_years == [2021, 2022, 2023, 2024, 2025]
        assert {m.model for m in parsed.walk_forward.summary_all_oos} == {
            "Baseline_1_AltmanZ",
            "Baseline_2_Logit",
            "XGBoost",
        }
        assert {m.model for m in parsed.walk_forward.summary_true_unseen} == {
            "Baseline_1_AltmanZ",
            "Baseline_2_Logit",
            "XGBoost",
        }
        assert len(parsed.risk_tier_reliability.tiers) == 5
        assert parsed.taxonomy_drift_psi.reference_taxonomy == "VAS_TT200_2014"
        assert parsed.taxonomy_drift_psi.target_taxonomy == "VAS_TT99_2025"
        assert parsed.taxonomy_drift_psi.reference_rows > 0
        assert parsed.taxonomy_drift_psi.target_rows > 0
        assert len(parsed.case_studies) >= 6
        assert any(
            cs.ticker == "HBC" and cs.top_3_shap_drivers for cs in parsed.case_studies
        )


def test_v2_meta_model_card_returns_404_when_table_absent(tmp_path: Path) -> None:
    """Snapshots without model_card_meta.parquet return 404 cleanly without reading data/metadata/."""
    from finintel.analytics.tables import build_analytics_tables

    facts, issuers, _ = build_ml08_real_shape_fixture()
    analytics = build_analytics_tables(facts, issuers, as_of=date(2026, 8, 15))
    data_root = tmp_path / "no_card_root"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    state.start_run("run_nocard", "MANUAL", {"source": "test"}, None)
    publisher = SnapshotPublisher(
        data_root / "published" / "snapshots", state_store=state
    )
    publisher.publish(
        "snap_nocard_001",
        {
            "issuers.parquet": issuers,
            "financial_facts.parquet": facts,
            RATIOS_TABLE: analytics[RATIOS_TABLE],
            BENCHMARKS_TABLE: analytics[BENCHMARKS_TABLE],
            RED_FLAGS_TABLE: analytics[RED_FLAGS_TABLE],
        },
        run_id="run_nocard",
    )
    state.finish_run("run_nocard", "SUCCEEDED", "snap_nocard_001")

    app = create_app(data_root=data_root)
    with TestClient(app) as client:
        resp = client.get("/api/v2/meta/model-card")
        assert resp.status_code == 404


def test_stress_test_tickers_filter_single_watchlist_nonexistent_and_backward_compat(
    ml08_ducklake_root: tuple[Path, str, str],
) -> None:
    """UI-06: POST /api/v1/stress-test supports tickers=None, 1 company, multi-ticker watchlist, empty, non-existent, and 422 invalid."""
    data_root, _, _ = ml08_ducklake_root
    # Also ensure removing any extra metadata dirs doesn't affect stress test
    ml_dir = data_root / "metadata" / "ml_models"
    if ml_dir.exists():
        shutil.rmtree(ml_dir)

    repo = ParquetRepository(data_root=data_root, backend="ducklake")
    app = create_app(repository=repo)

    # Request DTO normalization & validation
    req_norm = StressScenarioRequest(
        scenario_name="Watchlist Test",
        revenue_shock_pct=-0.25,
        gross_margin_shock_pp=-0.05,
        interest_rate_shock_bps=300.0,
        dso_shock_days=15.0,
        dio_shock_days=10.0,
        top_n=10,
        tickers=[" hbc ", "safe01", "HBC"],
    )
    assert req_norm.tickers == ["HBC", "SAFE01"]

    with TestClient(app) as client:
        base_payload = {
            "scenario_name": "Macro Shock",
            "statement_scope": "CONSOLIDATED",
            "period_type": "QUARTER",
            "period_end_date": "2026-06-30",
            "revenue_shock_pct": -0.25,
            "gross_margin_shock_pp": -0.05,
            "interest_rate_shock_bps": 300.0,
            "dso_shock_days": 15.0,
            "dio_shock_days": 10.0,
            "top_n": 50,
        }

        # 1. Backward compatibility (tickers omitted / None) -> evaluates full universe (24 issuers)
        r_all = client.post("/api/v1/stress-test", json=base_payload)
        assert r_all.status_code == 200
        all_json = r_all.json()
        assert all_json["total_issuers_evaluated"] == 24
        r_none = client.post(
            "/api/v1/stress-test", json={**base_payload, "tickers": None}
        )
        assert r_none.status_code == 200
        assert r_none.json() == all_json

        # 2. Single company (normalized lowercase -> uppercase)
        r_one = client.post(
            "/api/v1/stress-test",
            json={**base_payload, "tickers": ["  hbc "]},
        )
        assert r_one.status_code == 200
        one_json = r_one.json()
        assert one_json["total_issuers_evaluated"] == 1
        assert len(one_json["top_stressed_issuers"]) == 1
        assert one_json["top_stressed_issuers"][0]["ticker"] == "HBC"
        hbc_debt = one_json["top_stressed_issuers"][0]["total_debt"]
        assert one_json["total_debt_sum"] == pytest.approx(hbc_debt)
        assert len(one_json["sector_summary"]) == 1
        assert one_json["sector_summary"][0]["issuer_count"] == 1

        # 3. Multi-ticker watchlist (mixed case + duplicate)
        r_watch = client.post(
            "/api/v1/stress-test",
            json={**base_payload, "tickers": ["hbc", "safe01", "HBC", "POM"]},
        )
        assert r_watch.status_code == 200
        watch_json = r_watch.json()
        assert watch_json["total_issuers_evaluated"] == 3
        watch_tickers = {row["ticker"] for row in watch_json["top_stressed_issuers"]}
        assert watch_tickers == {"HBC", "SAFE01", "POM"}
        expected_sum = sum(
            row["total_debt"]
            for row in watch_json["top_stressed_issuers"]
            if row["total_debt"] is not None
        )
        assert watch_json["total_debt_sum"] == pytest.approx(expected_sum)

        # 4. Non-existent ticker -> clean 200 OK with 0 issuers evaluated
        r_missing = client.post(
            "/api/v1/stress-test",
            json={**base_payload, "tickers": ["NOPE999"]},
        )
        assert r_missing.status_code == 200
        missing_json = r_missing.json()
        assert missing_json["total_issuers_evaluated"] == 0
        assert missing_json["total_debt_sum"] == 0.0
        assert missing_json["baseline_debt_at_risk_pct"] == 0.0
        assert missing_json["stressed_debt_at_risk_pct"] == 0.0
        assert missing_json["newly_distressed_count"] == 0
        assert missing_json["sector_summary"] == []
        assert missing_json["newly_distressed_issuers"] == []
        assert missing_json["top_stressed_issuers"] == []

        # 5. Explicit empty list -> clean 200 OK with 0 issuers evaluated
        r_empty = client.post(
            "/api/v1/stress-test",
            json={**base_payload, "tickers": []},
        )
        assert r_empty.status_code == 200
        assert r_empty.json()["total_issuers_evaluated"] == 0

        # 6. Invalid ticker format -> 422 validation error
        for bad_tickers in ([""], ["BAD$!"], ["TOOLONGTICKER123"]):
            r_bad = client.post(
                "/api/v1/stress-test",
                json={**base_payload, "tickers": bad_tickers},
            )
            assert r_bad.status_code == 422
