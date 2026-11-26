"""TASK-09: Serving p95 latency budgets, Composite ETag, Forward Pointer, Freshness/DTD & OTel spans.

Verified Sources:
- CONSTRAINTS.md (Staged -> Enforced p95 latency budgets: Warm <= 50ms, Cold DuckLake <= 200ms, Stress <= 300ms).
- spec/SPEC-v2-autonomous-institutional-finintel.md (§3.D Merton DTD, §3.E Composite ETag & Forward Pointer, §3.F Observability).
"""

from __future__ import annotations

import math
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import polars as pl
import pytest

from finintel.pipeline.state_store import StateStore
from finintel.serving.observability import (
    SPAN_INGEST_FETCH,
    SPAN_SERVING_QUERY,
    SPAN_STORAGE_PUBLISH,
    SPAN_TRANSFORM_INCREMENTAL,
    bind_telemetry_context,
    record_snapshot_freshness_age,
    record_wap_publish,
    trace_span,
)
from finintel.serving.queries import clear_query_caches
from finintel.storage import DuckLakeStore, repository as repo_mod
from finintel.storage.ducklake_store import (
    POSTGRES_SERVING_POINTER_TABLE,
    PostgresActiveSnapshotPointer,
    ensure_ducklake_attached,
)
from finintel.storage.repository import clear_repository_caches
from tests.unit.test_arch07_08_postgres_pointer import (
    _build_minimal_serving_tables,
)


def _percentile(samples_ms: list[float], pct: float) -> float:
    ordered = sorted(samples_ms)
    idx = max(0, min(len(ordered) - 1, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return ordered[idx]


def test_serving_p95_latency_budgets_warm_cold_ducklake_and_stress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enforce CONSTRAINTS.md p95 latency budgets:
    - L1/Valkey warm <= 50 ms
    - DuckLake cold <= 200 ms
    - Stress-Test <= 300 ms
    """
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from finintel.serving.api import create_app
    from tests.unit.test_ui09b_serving_overlay import _publish_fixture

    clear_repository_caches()
    clear_query_caches()

    # 1. Build real overlay + stress snapshot fixture on tmp_path AND publish into DuckLake
    parquet_root, _, _ = _publish_fixture(tmp_path / "pq_src", include_port_files=True)
    snap_dir = next((parquet_root / "published" / "snapshots").glob("snap_*"))
    tables: dict[str, pl.DataFrame] = {
        pq.name: pl.read_parquet(pq) for pq in sorted(snap_dir.glob("*.parquet"))
    }

    lake_root = tmp_path / "lake_root"
    state = StateStore(lake_root / "metadata" / "state.sqlite")
    state.initialize()
    store = DuckLakeStore(
        catalog_path=lake_root / "metadata" / "ducklake_catalog.sqlite",
        data_path=lake_root / "lakehouse" / "data",
        state_store=state,
    )
    state.start_run("run-lat-001", "CLI_MANUAL", {"source": "fhsc"}, "abc1234")
    store.publish_snapshot("snap_lat_001", tables, run_id="run-lat-001")
    state.finish_run("run-lat-001", "SUCCEEDED", "snap_lat_001")

    kv_store: dict[str, bytes] = {}

    class _FastValkeyStub:
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
            classmethod(lambda _cls, *_a, **_kw: _FastValkeyStub()),
        )
    except ImportError:

        class _StubRedis:
            @classmethod
            def from_url(cls, *_a: object, **_kw: object) -> _FastValkeyStub:
                return _FastValkeyStub()

        monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=_StubRedis))

    app = create_app(
        data_root=lake_root,
        duckdb_threads=2,
        valkey_url="redis://127.0.0.1:6379/0",
    )
    with TestClient(app) as client:
        # Measure DuckLake cold reads (clearing L1 + L1B + Valkey before each sample)
        cold_routes = [
            "/api/v1/snapshot/overview",
            "/api/v1/screener",
            "/api/v1/companies/FPT/profile",
            "/api/v2/sectors/risk-summary",
        ]
        # Prime DuckLake catalog attachment once before measuring cold table scan latency
        assert client.get("/api/v1/health").status_code == 200
        cold_samples_ms: list[float] = []
        for idx in range(12):
            repo_mod._SNAPSHOT_TABLE_DATA_CACHE.clear()
            clear_query_caches()
            kv_store.clear()
            route = cold_routes[idx % len(cold_routes)]
            t0 = time.perf_counter()
            resp = client.get(route)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            assert resp.status_code == 200
            cold_samples_ms.append(elapsed_ms)

        cold_p95 = _percentile(cold_samples_ms, 95.0)
        assert cold_p95 <= 200.0, f"DuckLake cold p95={cold_p95:.2f}ms exceeded 200ms"

        # Measure L1/Valkey warm reads (20 samples)
        warm_samples_ms: list[float] = []
        for idx in range(20):
            route = cold_routes[idx % len(cold_routes)]
            t0 = time.perf_counter()
            resp = client.get(route)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            assert resp.status_code == 200
            warm_samples_ms.append(elapsed_ms)

        warm_p95 = _percentile(warm_samples_ms, 95.0)
        assert warm_p95 <= 50.0, f"L1/Valkey warm p95={warm_p95:.2f}ms exceeded 50ms"

        # Measure Stress-Test uncached computation p95 (10 distinct scenarios so Valkey misses)
        stress_samples_ms: list[float] = []
        for idx in range(10):
            t0 = time.perf_counter()
            resp = client.post(
                "/api/v1/stress-test",
                json={
                    "scenario_name": f"Shock_{idx}",
                    "revenue_shock_pct": -0.05 - (idx * 0.01),
                    "interest_rate_shock_bps": 100.0 + (idx * 10.0),
                },
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            assert resp.status_code == 200
            stress_samples_ms.append(elapsed_ms)

        stress_p95 = _percentile(stress_samples_ms, 95.0)
        assert stress_p95 <= 300.0, f"Stress-Test p95={stress_p95:.2f}ms exceeded 300ms"


def test_composite_etag_freshness_dtd_query_guard_and_sanitizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Composite ETag '<snap_id>:<port_digest>', Valkey rotation on EOD update,
    Merton DTD fields on market-overlay, 3-port freshness metadata, ?snapshot_id= 422 guard,
    and strict server error sanitization."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from finintel.serving.api import create_app
    from finintel.serving.freshness import (
        compute_extension_ports_digest,
        format_composite_etag,
        sanitize_error_detail,
    )
    from finintel.serving.schemas import (
        CompanyMarketOverlayResponse,
        MetaPeriodsResponse,
    )
    from tests.unit.test_ui09b_serving_overlay import _publish_fixture

    data_root, snapshot_id, _ = _publish_fixture(tmp_path, include_port_files=True)

    kv_store: dict[str, bytes] = {}

    class _InMemValkey:
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
            classmethod(lambda _cls, *_a, **_kw: _InMemValkey()),
        )
    except ImportError:

        class _StubRedis2:
            @classmethod
            def from_url(cls, *_a: object, **_kw: object) -> _InMemValkey:
                return _InMemValkey()

        monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=_StubRedis2))

    digest_1 = compute_extension_ports_digest(data_root)
    expected_etag_1 = format_composite_etag(snapshot_id, data_root)
    assert expected_etag_1 == f'"{snapshot_id}:{digest_1}"'

    app = create_app(data_root=data_root, valkey_url="redis://127.0.0.1:6379/0")
    with TestClient(app) as client:
        # 1. Public ?snapshot_id= query parameter (plain or percent-encoded) MUST be rejected with 422
        r_forbid = client.get(
            "/api/v1/snapshot/overview", params={"snapshot_id": "snap_old"}
        )
        assert r_forbid.status_code == 422
        assert "snap_old" not in r_forbid.text
        r_forbid_enc = client.get("/api/v1/snapshot/overview?snapshot%5Fid=snap_old")
        assert r_forbid_enc.status_code == 422

        # 1b. Scrape /metrics BEFORE /api/v2/meta/periods is ever called -> freshness gauges live!
        boot_metrics = client.get("/metrics").text
        assert (
            'finintel_snapshot_freshness_age_seconds{port="bctc_snapshot"}'
            in boot_metrics
        )
        assert (
            'finintel_snapshot_freshness_age_seconds{port="market_prices"}'
            in boot_metrics
        )

        # 2. Market overlay exposes Merton DTD fields AND Composite ETag while X-Snapshot-ID stays pure
        r_ov = client.get("/api/v2/companies/FPT/market-overlay")
        assert r_ov.status_code == 200
        assert r_ov.headers.get("X-Snapshot-ID") == snapshot_id
        assert r_ov.headers.get("ETag") == expected_etag_1

        overlay = CompanyMarketOverlayResponse(**r_ov.json())
        assert overlay.latest_valuation is not None
        assert overlay.latest_valuation.dtd_flag == "VALID"
        assert overlay.latest_valuation.merton_dtd is not None
        assert overlay.latest_valuation.merton_dtd > 0.0
        assert overlay.latest_valuation.equity_volatility_1y is not None
        assert overlay.latest_valuation.naive_edf is not None

        # 3. 3-tier Freshness metadata on /api/v2/meta/periods
        r_meta = client.get("/api/v2/meta/periods")
        assert r_meta.status_code == 200
        meta_body = MetaPeriodsResponse(**r_meta.json())
        assert meta_body.freshness is not None
        assert meta_body.freshness.bctc_snapshot_id == snapshot_id
        assert meta_body.freshness.market_prices_latest_trade_date is not None
        assert meta_body.freshness.regulatory_latest_event_date == date(2023, 9, 4)
        assert meta_body.freshness.extension_ports_digest == digest_1

        # 4. Mutating Port 3 EOD parquet under the SAME snapshot_id rotates ETag & invalidates Valkey!
        mp_path = data_root / "market_prices" / "market_prices.parquet"
        mp_df = pl.read_parquet(mp_path)
        extra_day = mp_df.tail(1).with_columns(
            (pl.col("trade_date") + timedelta(days=3)).alias("trade_date")
        )
        pl.concat([mp_df, extra_day]).write_parquet(mp_path)

        digest_2 = compute_extension_ports_digest(data_root)
        assert digest_2 != digest_1
        r_after = client.get(
            "/api/v2/companies/FPT/market-overlay",
            headers={"If-None-Match": expected_etag_1},
        )
        assert r_after.status_code == 200
        assert r_after.headers.get("X-Cache") == "MISS"
        assert r_after.headers.get("ETag") == f'"{snapshot_id}:{digest_2}"'

    # 5. Server sanitizer strips internal schema tokens, DSNs, SQL, and file paths
    for leaked in (
        "postgresql://finintel:secret@127.0.0.1:5432/ducklake_catalog failed",
        "Column BS_TOTAL_ASSETS missing in quality_summary",
        "Peer fallback REGIME_FALLBACK for issuer_id=FPT",
        "C:\\Users\\dat\\data\\issuers.parquet not found",
    ):
        assert (
            sanitize_error_detail(leaked, fallback="Safe fallback.") == "Safe fallback."
        )


def test_forward_only_postgres_serving_pointer_and_otel_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify forward-only ducklake_version reconcile never regresses a newer Postgres pointer,
    and verify V2 OTel spans + Prometheus freshness/WAP metrics."""
    data_root = tmp_path / "pg_fwd"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    backing_sqlite = data_root / "metadata" / "backing.sqlite"

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

    pg_box: dict[str, PostgresActiveSnapshotPointer | None] = {"ptr": None}
    upsert_calls: list[tuple[str, int]] = []

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
        upsert_calls.append((snapshot_id, ducklake_version))
        pg_box["ptr"] = PostgresActiveSnapshotPointer(
            snapshot_id=snapshot_id,
            ducklake_version=ducklake_version,
            run_id=run_id,
            published_at=published_at,
        )

    monkeypatch.setattr(
        "finintel.storage.ducklake_store.ensure_ducklake_attached", mocked_attach
    )
    monkeypatch.setattr(
        "finintel.storage.ducklake_store.upsert_postgres_active_snapshot", fake_upsert
    )
    monkeypatch.setattr(
        "finintel.storage.ducklake_store.read_postgres_active_snapshot",
        lambda **_kw: pg_box["ptr"],
    )

    store = DuckLakeStore(
        catalog_path=backing_sqlite,
        data_path=data_root / "lakehouse" / "data",
        state_store=state,
        catalog_driver="postgres",
        postgres_dsn_env="FININTEL_FWD_TEST_DSN",
    )

    state.start_run("run-1", "CLI_MANUAL", {"source": "fhsc"}, None)
    v1 = store.publish_snapshot(
        "snap_fwd_001",
        _build_minimal_serving_tables(marker="v1", model_version="v1.0.0"),
        run_id="run-1",
    )
    state.finish_run("run-1", "SUCCEEDED", "snap_fwd_001")

    state.start_run("run-2", "CLI_MANUAL", {"source": "fhsc"}, None)
    v2 = store.publish_snapshot(
        "snap_fwd_002",
        _build_minimal_serving_tables(marker="v2", model_version="v2.0.0"),
        run_id="run-2",
    )
    state.finish_run("run-2", "SUCCEEDED", "snap_fwd_002")
    assert v2 > v1
    assert POSTGRES_SERVING_POINTER_TABLE == "public.finintel_serving_pointer"

    # Simulate stale local state.sqlite pointing at snap_fwd_001 while Postgres is already at v2:
    # reconcile_unpromoted_snapshots MUST NOT regress Postgres from v2 back to v1!
    with state.connection() as sconn:
        sconn.execute(
            "UPDATE active_snapshot SET snapshot_id = 'snap_fwd_001' WHERE singleton = 1"
        )
    before_upserts = len(upsert_calls)
    store.reconcile_unpromoted_snapshots()
    store.maintain(keep_snapshots=2, min_snapshot_age_seconds=0)
    assert len(upsert_calls) == before_upserts
    assert pg_box["ptr"] is not None and pg_box["ptr"].ducklake_version == v2

    # Verify parent-data/ Extension Port fallback for nested universe_600_800 roots
    import finintel.serving.freshness as freshness_mod

    fake_default_data = tmp_path / "parent_data"
    nested_root = fake_default_data / "universe_600_800"
    nested_root.mkdir(parents=True, exist_ok=True)
    parent_mp = fake_default_data / "market_prices" / "market_prices.parquet"
    parent_mp.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"trade_date": [date(2026, 11, 24)]}).write_parquet(parent_mp)
    monkeypatch.setattr(freshness_mod, "DEFAULT_DATA_ROOT", fake_default_data)
    nested_fresh = freshness_mod.collect_data_freshness(
        snapshot_id="snap_fwd_002",
        published_at="2026-11-25T00:00:00Z",
        data_root=nested_root,
    )
    assert nested_fresh["market_prices_latest_trade_date"] == date(2026, 11, 24)
    assert nested_fresh["extension_ports_digest"] != freshness_mod._EMPTY_PORTS_DIGEST

    # Verify OTel canonical spans + context propagation + Prometheus freshness/WAP metrics
    pytest.importorskip("opentelemetry")
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    monkeypatch.setenv("FININTEL_OTEL_ENABLED", "1")
    exporter = InMemorySpanExporter()
    provider = TracerProvider(
        resource=Resource.create({"service.name": "finintel-test"})
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)

    with bind_telemetry_context(
        entry_point="dagster_sensor", run_id="run-otel-1", snapshot_id="snap_fwd_002"
    ):
        for span_name in (
            SPAN_INGEST_FETCH,
            SPAN_TRANSFORM_INCREMENTAL,
            SPAN_STORAGE_PUBLISH,
            SPAN_SERVING_QUERY,
        ):
            with trace_span(span_name):
                pass

    finished = exporter.get_finished_spans()
    names = {s.name for s in finished}
    assert {
        "finintel.ingest.fetch",
        "finintel.transform.incremental",
        "finintel.storage.publish_snapshot",
        "finintel.serving.query",
    } <= names
    sample_attrs: dict[str, Any] = dict(finished[0].attributes or {})
    assert sample_attrs.get("finintel.entry_point") == "dagster_sensor"
    assert sample_attrs.get("finintel.run_id") == "run-otel-1"

    record_snapshot_freshness_age("bctc_snapshot", 120.5)
    record_wap_publish("published")
    record_wap_publish("quarantined")

    monkeypatch.setenv("FININTEL_OTEL_ENABLED", "0")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from finintel.serving.api import create_app

    app = create_app(data_root=data_root)
    with TestClient(app) as client:
        metrics_text = client.get("/metrics").text
        assert (
            'finintel_snapshot_freshness_age_seconds{port="bctc_snapshot"}'
            in metrics_text
        )
        assert 'finintel_wap_publish_total{outcome="published"}' in metrics_text
        assert 'finintel_wap_publish_total{outcome="quarantined"}' in metrics_text
