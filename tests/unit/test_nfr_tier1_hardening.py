"""Regression tests for Tier 1 Go-Live NFR hardening (NFR-01 through NFR-11)."""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

import polars as pl
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from finintel.pipeline.state_store import StateStore  # noqa: E402
from finintel.serving.api import create_app  # noqa: E402
from finintel.serving.cache import (  # noqa: E402
    ValkeyResponseCache,
    build_snapshot_cache_key,
)
from finintel.storage import repository as repo_mod  # noqa: E402
from finintel.storage.repository import ParquetRepository, clear_repository_caches  # noqa: E402


def _make_minimal_snapshot(root: Path, snapshot_id: str) -> Path:
    snap_dir = root / "published" / "snapshots" / snapshot_id
    snap_dir.mkdir(parents=True, exist_ok=True)
    issuers = pl.DataFrame(
        {
            "issuer_id": ["ISS_FPT"],
            "ticker": ["FPT"],
            "company_name_vn": ["FPT Corp"],
            "exchange": ["HOSE"],
            "accounting_regime": ["CORPORATE"],
            "icb_code_level2": ["9500"],
            "icb_name_level2": ["Technology"],
            "icb_code_level3": ["9530"],
            "icb_name_level3": ["Software"],
            "industry_snapshot_date": [date(2025, 12, 31)],
            "is_delisted": [False],
            "delisted_date": [None],
        }
    )
    ratios = pl.DataFrame(
        {
            "issuer_id": ["ISS_FPT"],
            "ticker": ["FPT"],
            "accounting_regime": ["CORPORATE"],
            "statement_scope": ["CONSOLIDATED"],
            "period_type": ["QUARTER"],
            "fiscal_year": [2025],
            "fiscal_quarter": [4],
            "period_end_date": [date(2025, 12, 31)],
            "taxonomy_version": ["TT200"],
            "filing_id": ["F_1"],
            "roe_total": [0.22],
        }
    )
    facts = pl.DataFrame(
        {
            "issuer_id": ["ISS_FPT"],
            "metric_id": ["net_revenue"],
            "statement_scope": ["CONSOLIDATED"],
            "period_type": ["QUARTER"],
            "period_end_date": [date(2025, 12, 31)],
            "value": [1000.0],
        }
    )
    issuers.write_parquet(snap_dir / "issuers.parquet")
    ratios.write_parquet(snap_dir / "financial_ratios.parquet")
    facts.write_parquet(snap_dir / "financial_facts.parquet")
    (snap_dir / "_snapshot_meta.json").write_text(
        json.dumps(
            {"snapshot_id": snapshot_id, "published_at": "2026-01-01T00:00:00Z"}
        ),
        encoding="utf-8",
    )
    state = StateStore(root / "metadata" / "state.sqlite")
    state.initialize()
    run_id = f"run_{snapshot_id}"
    state.start_run(run_id, "MANUAL", {"test": True}, None)
    state.finish_run(run_id, "SUCCEEDED", snapshot_id)
    return snap_dir


def test_nfr01_large_tables_not_cached_and_rollover_evicts_old_snapshot(
    tmp_path: Path,
) -> None:
    clear_repository_caches()
    snap1 = _make_minimal_snapshot(tmp_path, "snap_nfr01_001")
    repo = ParquetRepository(data_root=tmp_path)

    # Small serving tables enter bounded L1 cache; large facts table stays off-heap via DuckDB.
    _ = repo.read_table("issuers.parquet")
    _ = repo.read_table("financial_facts.parquet")
    cached_paths = [
        str(k[1])
        for k in repo_mod._SNAPSHOT_TABLE_DATA_CACHE
        if len(k) > 1 and isinstance(k[1], str)
    ]
    assert any("issuers.parquet" in p for p in cached_paths)
    assert not any("financial_facts.parquet" in p for p in cached_paths)

    # Publishing a new snapshot immediately evicts prior snapshot entries for the same root.
    _ = _make_minimal_snapshot(tmp_path, "snap_nfr01_002")
    _ = repo.read_table("issuers.parquet")
    remaining_paths = [
        str(k[1])
        for k in repo_mod._SNAPSHOT_TABLE_DATA_CACHE
        if len(k) > 1 and isinstance(k[1], str)
    ]
    assert any("snap_nfr01_002" in p for p in remaining_paths)
    assert not any(str(snap1) in p for p in remaining_paths)


def test_nfr02_no_sys_getframe_hack_in_serving() -> None:
    queries_src = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "finintel"
        / "serving"
        / "queries.py"
    ).read_text(encoding="utf-8")
    assert "_getframe" not in queries_src
    assert "_FastStressInputDataFrame" not in queries_src
    assert "_STRESS_SIM_CACHE" not in queries_src


def test_nfr04_health_live_ready_metrics_and_deleted_parquet_returns_503(
    tmp_path: Path,
) -> None:
    clear_repository_caches()
    snap_dir = _make_minimal_snapshot(tmp_path, "snap_nfr04_001")
    app = create_app(data_root=tmp_path)

    with TestClient(app) as client:
        r_live = client.get("/health/live")
        assert r_live.status_code == 200
        assert r_live.json() == {"status": "alive"}
        assert "X-Request-ID" in r_live.headers
        assert "X-Process-Time-Ms" in r_live.headers

        r_ready = client.get("/health/ready")
        assert r_ready.status_code == 200
        ready_body = r_ready.json()
        assert ready_body["status"] == "ready"
        assert ready_body["active_snapshot_id"] == "snap_nfr04_001"
        assert ready_body["duckdb_ok"] is True
        assert ready_body["snapshot_readable"] is True
        assert r_ready.headers.get("X-Snapshot-ID") == "snap_nfr04_001"

        r_metrics = client.get("/metrics")
        assert r_metrics.status_code == 200
        assert "http_requests_total" in r_metrics.text

        # Delete live Parquet files on disk (Phase 5 Chaos scenario):
        # /health/live stays 200 alive, while /health/ready and /api/v1/health MUST return 503!
        for pq in snap_dir.glob("*.parquet"):
            pq.unlink()

        assert client.get("/health/live").status_code == 200
        r_ready_down = client.get("/health/ready")
        assert r_ready_down.status_code == 503
        assert r_ready_down.json()["status"] == "unready"

        r_health_down = client.get("/api/v1/health")
        assert r_health_down.status_code == 503
        assert r_health_down.json()["status"] == "degraded"


def test_nfr03_stress_bulkhead_middleware_and_liveness_non_blocking(
    tmp_path: Path,
) -> None:
    clear_repository_caches()
    _make_minimal_snapshot(tmp_path, "snap_nfr03_001")
    app = create_app(
        data_root=tmp_path,
        stress_max_concurrency=2,
        stress_semaphore_timeout=0.05,
    )
    with TestClient(app) as client:
        sem: threading.Semaphore = app.state.stress_semaphore
        assert sem.acquire(blocking=False)
        assert sem.acquire(blocking=False)
        try:
            r_busy = client.post("/api/v1/stress-test", json={"scenario_name": "Flood"})
            assert r_busy.status_code == 503
            assert r_busy.headers.get("Retry-After") == "1"
            assert client.get("/health/live").status_code == 200
        finally:
            sem.release()
            sem.release()


def test_valkey_response_cache_key_determinism_and_fail_open() -> None:
    k1 = build_snapshot_cache_key("snap_1", "/api/v1/screener", {"b": 2, "a": 1})
    k2 = build_snapshot_cache_key("snap_1", "api/v1/screener", {"a": 1, "b": 2})
    k3 = build_snapshot_cache_key("snap_2", "api/v1/screener", {"a": 1, "b": 2})
    assert k1 == k2
    assert k1 != k3

    noop_cache = ValkeyResponseCache(url="")
    assert noop_cache.enabled is False
    assert noop_cache.get_bytes(k1) is None
    assert noop_cache.set_bytes(k1, b"{}") is False

    # Unreachable endpoint must fail-open without raising.
    unreachable = ValkeyResponseCache(
        url="redis://127.0.0.1:1/0",
        socket_timeout=0.05,
        circuit_cooldown_seconds=5.0,
    )
    try:
        assert unreachable.get_bytes(k1) is None
        assert unreachable.set_bytes(k1, b"{}") is False
    finally:
        unreachable.close()
