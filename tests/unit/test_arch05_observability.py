"""Unit tests for ARCH-05 Observability Stack, OTel custom spans, M-01 & M-03 guardrails."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]
from prometheus_client import CollectorRegistry, Counter
from prometheus_client.mmap_dict import MmapedDict, mmap_key

pytest.importorskip("fastapi")

from fastapi import FastAPI, Response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from finintel.serving import observability  # noqa: E402
from finintel.serving.observability import (  # noqa: E402
    DEFAULT_OTEL_EXPORTER_OTLP_ENDPOINT,
    DEFAULT_OTEL_SERVICE_NAME,
    SPAN_ANALYTICS_STRESS_TEST,
    SPAN_DUCKDB_QUERY,
    SPAN_RAG_INVESTIGATE,
    SPAN_VALKEY_CACHE,
    _normalize_metric_path,
    attach_observability,
    configure_otel_tracing,
    generate_metrics_payload,
    resolve_otlp_traces_endpoint,
    trace_span,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_observability_module_has_zero_top_level_platform_imports() -> None:
    """M-03: observability.py must keep platform-group packages strictly lazy-imported."""
    source_path = REPO_ROOT / "src" / "finintel" / "serving" / "observability.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    forbidden_roots = {
        "opentelemetry",
        "fastapi",
        "starlette",
        "structlog",
        "redis",
        "dagster",
        "psycopg",
        "granian",
    }

    top_level_imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level_imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level_imports.add(node.module.split(".")[0])

    assert top_level_imports.isdisjoint(forbidden_roots), (
        f"Top-level platform imports found in observability.py: "
        f"{sorted(top_level_imports & forbidden_roots)}"
    )


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        ("/api/v1/companies/VNM/profile", "/api/v1/companies/{ticker}/profile"),
        ("/api/v2/companies/FPT/statements", "/api/v2/companies/{ticker}/statements"),
        (
            "/api/v2/documents/VNM/investigate",
            "/api/v2/documents/{ticker}/investigate",
        ),
        ("/api/v2/documents/HPG", "/api/v2/documents/{ticker}"),
        ("/api/v2/documents/MWG/filings", "/api/v2/documents/{ticker}/filings"),
        ("/api/v2/documents/", "/api/v2/documents/"),
        ("/api/v2/screener", "/api/v2/screener"),
        ("/health/ready", "/health/ready"),
    ],
)
def test_normalize_metric_path_collapses_companies_and_documents_tickers(
    raw_path: str, expected: str
) -> None:
    assert _normalize_metric_path(raw_path) == expected


def test_generate_metrics_payload_single_process_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    registry = CollectorRegistry()
    counter = Counter("finintel_unit_test_total", "Test counter", registry=registry)
    counter.inc(3)

    payload = generate_metrics_payload(registry).decode("utf-8")
    assert "finintel_unit_test_total 3.0" in payload


def test_generate_metrics_payload_multiprocess_aggregates_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M-01: MultiProcessCollector aggregates counter shards across worker PIDs."""
    mp_dir = tmp_path / "prom_multiproc"
    mp_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(mp_dir))

    # Simulate two Granian worker shards writing counter mmap files.
    key = mmap_key(
        "http_requests_total",
        "http_requests_total",
        ["handler", "method", "status"],
        ["/api/v2/screener", "GET", "200"],
        "Total HTTP requests served by FinIntel analytical API.",
    )
    shard_1: Any = MmapedDict(str(mp_dir / "counter_101.db"))  # type: ignore[no-untyped-call]
    try:
        shard_1.write_value(key, 4.0, 0.0)
    finally:
        shard_1.close()

    shard_2: Any = MmapedDict(str(mp_dir / "counter_102.db"))  # type: ignore[no-untyped-call]
    try:
        shard_2.write_value(key, 6.0, 0.0)
    finally:
        shard_2.close()

    unused_local_registry = CollectorRegistry()
    payload = generate_metrics_payload(unused_local_registry).decode("utf-8")
    assert (
        'http_requests_total{handler="/api/v2/screener",method="GET",status="200"} 10.0'
        in payload
    )


def test_attach_observability_exposes_red_and_valkey_cache_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    monkeypatch.setenv("FININTEL_OTEL_ENABLED", "0")
    monkeypatch.setenv("FININTEL_METRICS_ENABLED", "1")

    app = FastAPI()
    attach_observability(app)

    @app.get("/api/v2/documents/{ticker}/investigate")
    def investigate_stub(ticker: str, response: Response) -> dict[str, str]:
        response.headers["X-Cache"] = "HIT" if ticker == "VNM" else "MISS"
        return {"ticker": ticker}

    with TestClient(app) as client:
        r1 = client.get("/api/v2/documents/VNM/investigate")
        r2 = client.get("/api/v2/documents/FPT/investigate")
        assert r1.status_code == 200
        assert r2.status_code == 200

        metrics_resp = client.get("/metrics")
        assert metrics_resp.status_code == 200
        body = metrics_resp.text
        assert (
            'http_requests_total{handler="/api/v2/documents/{ticker}/investigate",'
            'method="GET",status="200"} 2.0' in body
        )
        assert "http_request_duration_seconds_bucket" in body
        assert "http_requests_in_progress" in body
        assert (
            'finintel_valkey_cache_requests_total{handler="/api/v2/documents/{ticker}/investigate",'
            'result="hit"} 1.0' in body
        )
        assert (
            'finintel_valkey_cache_requests_total{handler="/api/v2/documents/{ticker}/investigate",'
            'result="miss"} 1.0' in body
        )


def test_trace_span_noop_when_otel_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FININTEL_OTEL_ENABLED", "0")
    with trace_span(SPAN_VALKEY_CACHE, {"cache.operation": "get"}) as span:
        assert span is None


def test_trace_span_records_active_otel_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        resource=Resource.create({"service.name": "finintel-api"})
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)

    with trace_span(SPAN_VALKEY_CACHE, {"cache.operation": "get", "cache.hit": True}):
        with trace_span(SPAN_DUCKDB_QUERY, {"db.table": "financial_ratios.parquet"}):
            pass
    with trace_span(SPAN_ANALYTICS_STRESS_TEST, {"stress.issuer_count": 42}):
        pass
    with trace_span(SPAN_RAG_INVESTIGATE, {"rag.ticker": "VNM"}):
        pass

    spans = exporter.get_finished_spans()
    names = [s.name for s in spans]
    assert SPAN_DUCKDB_QUERY in names
    assert SPAN_VALKEY_CACHE in names
    assert SPAN_ANALYTICS_STRESS_TEST in names
    assert SPAN_RAG_INVESTIGATE in names

    by_name = {s.name: dict(s.attributes or {}) for s in spans}
    assert by_name[SPAN_VALKEY_CACHE]["cache.hit"] is True
    assert by_name[SPAN_DUCKDB_QUERY]["db.table"] == "financial_ratios.parquet"
    assert by_name[SPAN_ANALYTICS_STRESS_TEST]["stress.issuer_count"] == 42
    assert by_name[SPAN_RAG_INVESTIGATE]["rag.ticker"] == "VNM"


def test_resolve_otlp_traces_endpoint_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert resolve_otlp_traces_endpoint() == DEFAULT_OTEL_EXPORTER_OTLP_ENDPOINT

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://tempo:4318")
    assert resolve_otlp_traces_endpoint() == "http://tempo:4318/v1/traces"

    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://custom:4318/v1/traces"
    )
    assert resolve_otlp_traces_endpoint() == "http://custom:4318/v1/traces"
    assert (
        resolve_otlp_traces_endpoint("http://explicit:4318/v1/traces")
        == "http://explicit:4318/v1/traces"
    )


def test_configure_otel_tracing_initializes_provider_and_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    captured: list[Any] = []
    monkeypatch.setattr(observability, "_OTEL_TRACING_CONFIGURED", False)
    monkeypatch.setattr(trace, "set_tracer_provider", captured.append)

    ok = configure_otel_tracing(
        service_name=DEFAULT_OTEL_SERVICE_NAME,
        endpoint="http://127.0.0.1:4318/v1/traces",
        force=True,
    )
    assert ok is True
    assert len(captured) == 1
    provider = captured[0]
    assert isinstance(provider, TracerProvider)
    assert provider.resource.attributes.get("service.name") == "finintel-api"
    provider.shutdown()


def test_observability_config_files_are_valid_and_complete() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text("utf-8"))
    services = compose["services"]
    expected_containers = {
        "postgres": "finintel-postgres",
        "valkey": "finintel-valkey",
        "prometheus": "finintel-prometheus",
        "tempo": "finintel-tempo",
        "grafana": "finintel-grafana",
    }
    for svc, cname in expected_containers.items():
        assert svc in services
        assert services[svc]["container_name"] == cname

    assert services["tempo"]["image"] == "grafana/tempo:2.7.2"
    tempo_ports = " ".join(services["tempo"]["ports"])
    assert "3200}:3200" in tempo_ports
    assert "4317}:4317" in tempo_ports
    assert "4318}:4318" in tempo_ports

    assert services["grafana"]["image"] == "grafana/grafana:11.5.2"
    grafana_ports = " ".join(services["grafana"]["ports"])
    assert "3001}:3000" in grafana_ports
    assert services["grafana"]["environment"]["GF_AUTH_ANONYMOUS_ENABLED"] == "true"

    prom_cfg = yaml.safe_load(
        (REPO_ROOT / "config" / "prometheus.yml").read_text("utf-8")
    )
    assert any(
        job["job_name"] == "finintel_serving_api" for job in prom_cfg["scrape_configs"]
    )

    tempo_cfg = yaml.safe_load((REPO_ROOT / "config" / "tempo.yml").read_text("utf-8"))
    assert tempo_cfg["server"]["http_listen_port"] == 3200
    otlp_protocols = tempo_cfg["distributor"]["receivers"]["otlp"]["protocols"]
    assert otlp_protocols["grpc"]["endpoint"] == "0.0.0.0:4317"
    assert otlp_protocols["http"]["endpoint"] == "0.0.0.0:4318"

    ds_cfg = yaml.safe_load(
        (
            REPO_ROOT
            / "config"
            / "grafana"
            / "provisioning"
            / "datasources"
            / "datasources.yml"
        ).read_text("utf-8")
    )
    ds_by_uid = {ds["uid"]: ds for ds in ds_cfg["datasources"]}
    assert "prometheus" in ds_by_uid
    assert "tempo" in ds_by_uid
    assert (
        ds_by_uid["tempo"]["jsonData"]["tracesToMetrics"]["datasourceUid"]
        == "prometheus"
    )

    dash_prov = yaml.safe_load(
        (
            REPO_ROOT
            / "config"
            / "grafana"
            / "provisioning"
            / "dashboards"
            / "dashboards.yml"
        ).read_text("utf-8")
    )
    assert dash_prov["providers"][0]["options"]["path"] == "/var/lib/grafana/dashboards"

    dashboard = json.loads(
        (
            REPO_ROOT / "config" / "grafana" / "dashboards" / "finintel-overview.json"
        ).read_text("utf-8")
    )
    assert dashboard["title"] == "FinIntel V2 Serving & Lakehouse Overview"
    exprs = [
        target.get("expr", "")
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    ]
    joined_exprs = "\n".join(exprs)
    assert "http_requests_total" in joined_exprs
    assert "http_request_duration_seconds_bucket" in joined_exprs
    assert "histogram_quantile(0.50" in joined_exprs
    assert "histogram_quantile(0.95" in joined_exprs
    assert "histogram_quantile(0.99" in joined_exprs
    assert "finintel_valkey_cache_requests_total" in joined_exprs
    assert "http_requests_in_progress" in joined_exprs
