"""Structured logging, correlation headers, pure-ASGI Prometheus /metrics, and OTel hooks.

Verified Sources:
- structlog Official Documentation (``structlog==26.1.0``, JSONRenderer & contextvars):
  https://www.structlog.org/en/stable/
- Prometheus Python Client & MultiProcessCollector (``prometheus-client==0.26.0``):
  https://prometheus.github.io/client_python/multiprocess/
- OpenTelemetry Python SDK, OTLP HTTP Exporter & FastAPI Instrumentation
  (``opentelemetry-sdk==1.45.2``, ``opentelemetry-exporter-otlp-proto-http==1.45.2``,
  ``opentelemetry-instrumentation-fastapi==0.66b2``):
  https://opentelemetry.io/docs/languages/python/
- Starlette Pure ASGI Middleware Specification (zero ``BaseHTTPMiddleware`` stream
  copying or task-group overhead under high concurrency):
  https://www.starlette.io/middleware/#pure-asgi-middleware

Guardrails:
- All ``platform``-group packages (``structlog``, ``fastapi``, ``starlette``,
  ``opentelemetry.*``) are strictly lazy-imported so core CI ``mvp-checks``
  (``--group ml --dev``) can import ``trace_span`` and helpers without error (M-03).
"""

from __future__ import annotations

import contextvars
import logging
import math
import os
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

if TYPE_CHECKING:
    from fastapi import FastAPI, Response
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_OTEL_SERVICE_NAME = "finintel-api"
DEFAULT_OTEL_EXPORTER_OTLP_ENDPOINT = "http://127.0.0.1:4318/v1/traces"
TRACER_NAME = "finintel.serving"

SPAN_VALKEY_CACHE = "valkey.cache"
SPAN_DUCKDB_QUERY = "duckdb.query"
SPAN_ANALYTICS_STRESS_TEST = "analytics.stress_test"
SPAN_RAG_INVESTIGATE = "rag.investigate"
SPAN_INGEST_FETCH = "finintel.ingest.fetch"
SPAN_TRANSFORM_INCREMENTAL = "finintel.transform.incremental"
SPAN_STORAGE_PUBLISH = "finintel.storage.publish_snapshot"
SPAN_SERVING_QUERY = "finintel.serving.query"

VALID_ENTRY_POINTS: frozenset[str] = frozenset(
    {"http_api", "dagster_sensor", "dagster_schedule", "dagster_asset", "cli"}
)
VALID_INGESTION_OUTCOMES: frozenset[str] = frozenset(
    {"success", "quarantined", "rate_limited", "error"}
)
VALID_INGESTION_PROVIDERS: frozenset[str] = frozenset(
    {"fhsc", "vietcap_iq", "cafef", "ssc", "hsx", "hnx", "vndirect", "other"}
)
VALID_FRESHNESS_PORTS: frozenset[str] = frozenset(
    {"bctc_snapshot", "market_prices", "regulatory_events", "curated_pdfs"}
)
VALID_WAP_OUTCOMES: frozenset[str] = frozenset(
    {"published", "quarantined", "skipped_unchanged", "error"}
)

_TELEMETRY_CONTEXT: contextvars.ContextVar[dict[str, str | None]] = (
    contextvars.ContextVar(
        "finintel_telemetry_context",
        default={"entry_point": None, "run_id": None, "snapshot_id": None},
    )
)

_INGESTION_METRICS_REGISTRY = CollectorRegistry()
_INGESTION_REQUESTS_TOTAL = Counter(
    "finintel_ingestion_requests_total",
    "Total upstream provider ingestion requests partitioned by provider and outcome.",
    labelnames=("provider", "outcome"),
    registry=_INGESTION_METRICS_REGISTRY,
)
_INGESTION_DURATION_SECONDS = Histogram(
    "finintel_ingestion_duration_seconds",
    "Upstream provider ingestion request duration in seconds.",
    labelnames=("provider",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0),
    registry=_INGESTION_METRICS_REGISTRY,
)
_SNAPSHOT_FRESHNESS_AGE_SECONDS = Gauge(
    "finintel_snapshot_freshness_age_seconds",
    "Age in seconds of the active BCTC snapshot and Extension Port datasets.",
    labelnames=("port",),
    registry=_INGESTION_METRICS_REGISTRY,
    multiprocess_mode="max",
)
_WAP_PUBLISH_TOTAL = Counter(
    "finintel_wap_publish_total",
    "Total Write-Audit-Publish snapshot publication outcomes.",
    labelnames=("outcome",),
    registry=_INGESTION_METRICS_REGISTRY,
)


def get_ingestion_metrics_registry() -> CollectorRegistry:
    """Return the dedicated bounded-cardinality registry for ingestion metrics."""
    return _INGESTION_METRICS_REGISTRY


@contextmanager
def bind_telemetry_context(
    *,
    entry_point: str,
    run_id: str | None = None,
    snapshot_id: str | None = None,
) -> Iterator[dict[str, str | None]]:
    """Bind ``entry_point``, ``run_id``, and ``snapshot_id`` for correlated telemetry."""
    if entry_point not in VALID_ENTRY_POINTS:
        raise ValueError(
            f"entry_point must be one of {sorted(VALID_ENTRY_POINTS)}, got {entry_point!r}."
        )
    next_ctx: dict[str, str | None] = {
        "entry_point": entry_point,
        "run_id": run_id,
        "snapshot_id": snapshot_id,
    }
    token = _TELEMETRY_CONTEXT.set(next_ctx)
    try:
        yield dict(next_ctx)
    finally:
        _TELEMETRY_CONTEXT.reset(token)


def get_telemetry_context() -> dict[str, str | None]:
    """Return a copy of the active correlated telemetry context."""
    return dict(_TELEMETRY_CONTEXT.get())


def record_ingestion_request(
    provider: str,
    outcome: str,
    duration_seconds: float,
) -> None:
    """Record bounded-cardinality provider request metrics (never attaches ticker/filing_id)."""
    clean_provider = provider.strip().lower()
    if clean_provider not in VALID_INGESTION_PROVIDERS:
        clean_provider = "other"
    clean_outcome = outcome.strip().lower()
    if clean_outcome not in VALID_INGESTION_OUTCOMES:
        raise ValueError(
            f"outcome must be one of {sorted(VALID_INGESTION_OUTCOMES)}, got {outcome!r}."
        )
    safe_dur = (
        max(0.0, float(duration_seconds))
        if not isinstance(duration_seconds, bool) and math.isfinite(duration_seconds)
        else 0.0
    )
    _INGESTION_REQUESTS_TOTAL.labels(
        provider=clean_provider, outcome=clean_outcome
    ).inc()
    _INGESTION_DURATION_SECONDS.labels(provider=clean_provider).observe(safe_dur)


def record_snapshot_freshness_age(port: str, age_seconds: float) -> None:
    """Record bounded-cardinality dataset freshness age in seconds for ``port``."""
    clean_port = port.strip().lower()
    if clean_port not in VALID_FRESHNESS_PORTS:
        raise ValueError(
            f"port must be one of {sorted(VALID_FRESHNESS_PORTS)}, got {port!r}."
        )
    if not isinstance(age_seconds, bool) and math.isfinite(age_seconds):
        _SNAPSHOT_FRESHNESS_AGE_SECONDS.labels(port=clean_port).set(
            max(0.0, float(age_seconds))
        )


def record_wap_publish(outcome: str) -> None:
    """Increment bounded-cardinality Write-Audit-Publish counter for ``outcome``."""
    clean_outcome = outcome.strip().lower()
    if clean_outcome not in VALID_WAP_OUTCOMES:
        raise ValueError(
            f"outcome must be one of {sorted(VALID_WAP_OUTCOMES)}, got {outcome!r}."
        )
    _WAP_PUBLISH_TOTAL.labels(outcome=clean_outcome).inc()


_STRUCTLOG_CONFIGURED = False
_OTEL_TRACING_CONFIGURED = False
_logger = logging.getLogger("finintel.serving.observability")


def is_otel_enabled() -> bool:
    """Return True when OpenTelemetry tracing is enabled via ``FININTEL_OTEL_ENABLED``."""
    return os.getenv("FININTEL_OTEL_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def resolve_otlp_traces_endpoint(explicit: str | None = None) -> str:
    """Resolve OTLP HTTP trace collector endpoint from arg, env, or local Tempo default."""
    if explicit is not None and explicit.strip():
        return explicit.strip()
    traces_ep = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
    if traces_ep:
        return traces_ep
    base_ep = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if base_ep:
        cleaned = base_ep.rstrip("/")
        if cleaned.endswith("/v1/traces"):
            return cleaned
        return f"{cleaned}/v1/traces"
    return DEFAULT_OTEL_EXPORTER_OTLP_ENDPOINT


def configure_otel_tracing(
    *,
    service_name: str | None = None,
    endpoint: str | None = None,
    force: bool = False,
) -> bool:
    """Lazily initialize OpenTelemetry ``TracerProvider`` with OTLP HTTP batch export."""
    global _OTEL_TRACING_CONFIGURED
    if _OTEL_TRACING_CONFIGURED and not force:
        return True
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resolved_service = (
            (service_name or "").strip()
            or os.getenv("OTEL_SERVICE_NAME", "").strip()
            or DEFAULT_OTEL_SERVICE_NAME
        )
        resolved_endpoint = resolve_otlp_traces_endpoint(endpoint)

        resource = Resource.create({"service.name": resolved_service})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=resolved_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _OTEL_TRACING_CONFIGURED = True
        return True
    except Exception as exc:
        _logger.warning(
            "OpenTelemetry TracerProvider setup skipped: %s", type(exc).__name__
        )
        return False


def _coerce_span_attribute(value: object) -> bool | int | float | str | None:
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


@contextmanager
def trace_span(
    name: str,
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[Any]:
    """Create a custom OpenTelemetry span, degrading safely to a zero-overhead no-op.

    Supports standard FinIntel spans including ``valkey.cache``, ``duckdb.query``,
    ``analytics.stress_test``, and ``rag.investigate``.
    """
    if not is_otel_enabled():
        yield None
        return

    try:
        from opentelemetry import trace
    except ImportError:
        yield None
        return

    if not _OTEL_TRACING_CONFIGURED:
        current_tp = trace.get_tracer_provider()
        if (
            isinstance(current_tp, trace.ProxyTracerProvider)
            and getattr(current_tp, "_real_tracer_provider", None) is None
        ):
            configure_otel_tracing()

    tracer = trace.get_tracer(TRACER_NAME)
    ctx_fields = _TELEMETRY_CONTEXT.get()
    with tracer.start_as_current_span(name) as span:
        for ctx_key, attr_key in (
            ("entry_point", "finintel.entry_point"),
            ("run_id", "finintel.run_id"),
            ("snapshot_id", "finintel.snapshot_id"),
        ):
            ctx_val = ctx_fields.get(ctx_key)
            if ctx_val and (not attributes or attr_key not in attributes):
                try:
                    span.set_attribute(attr_key, ctx_val)
                except Exception:
                    pass
        if attributes:
            for key, raw_value in attributes.items():
                coerced = _coerce_span_attribute(raw_value)
                if coerced is not None:
                    try:
                        span.set_attribute(key, coerced)
                    except Exception:
                        pass
        yield span


def configure_structured_logging() -> None:
    """Configure structlog once for JSON-friendly structured request telemetry."""
    global _STRUCTLOG_CONFIGURED
    if _STRUCTLOG_CONFIGURED:
        return
    try:
        import structlog
    except ImportError:
        return
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )
    _STRUCTLOG_CONFIGURED = True


def _sanitize_header_token(raw: str | None, *, max_len: int = 64) -> str | None:
    if not raw:
        return None
    cleaned = "".join(ch for ch in raw.strip() if ch.isascii() and ch.isprintable())
    return cleaned[:max_len] if cleaned else None


def _normalize_metric_path(path: str) -> str:
    """Collapse dynamic ticker segments to prevent Prometheus label cardinality explosion."""
    parts = path.split("/")
    if (
        len(parts) >= 5
        and parts[1] == "api"
        and parts[3] in {"companies", "documents"}
        and parts[4]
    ):
        parts[4] = "{ticker}"
        return "/".join(parts)
    return path


def _resolve_multiproc_dir() -> Path | None:
    """Return ``PROMETHEUS_MULTIPROC_DIR`` as a ``Path`` when set to an existing directory."""
    raw = os.getenv("PROMETHEUS_MULTIPROC_DIR", "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_dir():
        return candidate
    return None


def generate_metrics_payload(registry: CollectorRegistry) -> bytes:
    """Render Prometheus exposition bytes, aggregating across workers when configured (M-01)."""
    multiproc_dir = _resolve_multiproc_dir()
    if multiproc_dir is not None and any(multiproc_dir.glob("*.db")):
        from prometheus_client import multiprocess

        mp_registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(mp_registry, path=str(multiproc_dir))  # type: ignore[no-untyped-call]
        rendered = bytes(generate_latest(mp_registry))
        if rendered:
            return rendered
    primary = bytes(generate_latest(registry))
    extra = (
        b""
        if registry is _INGESTION_METRICS_REGISTRY
        else bytes(generate_latest(_INGESTION_METRICS_REGISTRY))
    )
    reconcile_extra = (
        b""
        if registry is REGISTRY
        else bytes(
            generate_latest(
                REGISTRY.restricted_registry(["finintel_provider_discrepancy_total"])
            )
        )
    )
    return primary + extra + reconcile_extra


def mark_current_process_dead(live_gauges: Sequence[Any] = ()) -> None:
    """Mark the current worker PID dead for Prometheus multiprocess live gauges (P2-8)."""
    multiproc_dir = _resolve_multiproc_dir()
    if multiproc_dir is None:
        return
    for gauge in live_gauges:
        value_obj = getattr(gauge, "_value", None)
        mmap_file = getattr(value_obj, "_file", None)
        close_fn = getattr(mmap_file, "close", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception:
                pass
    try:
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(os.getpid())  # type: ignore[no-untyped-call]
    except OSError as exc:
        _logger.debug("Prometheus mark_process_dead skipped (%s).", type(exc).__name__)
    except Exception as exc:
        _logger.warning("Prometheus mark_process_dead failed (%s).", type(exc).__name__)


class RequestCorrelationMiddleware:
    """Pure ASGI middleware attaching correlation headers, RED metrics & structured logs."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        requests_total: Counter | None = None,
        request_duration: Histogram | None = None,
        requests_in_progress: Gauge | None = None,
        cache_requests_total: Counter | None = None,
    ) -> None:
        self.app = app
        self._requests_total = requests_total
        self._request_duration = request_duration
        self._requests_in_progress = requests_in_progress
        self._cache_requests_total = cache_requests_total
        self._access_log_json = os.getenv(
            "FININTEL_ACCESS_LOG_JSON", "0"
        ).strip().lower() in {
            "1",
            "true",
            "yes",
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from starlette.datastructures import MutableHeaders

        path = str(scope.get("path", ""))
        method = str(scope.get("method", "GET"))
        if path.startswith("/api/"):
            from finintel.serving.freshness import has_forbidden_public_snapshot_query

            if has_forbidden_public_snapshot_query(scope.get("query_string", b"")):
                from fastapi.responses import JSONResponse

                forbid_resp = JSONResponse(
                    status_code=422,
                    content={"detail": "Invalid query parameters."},
                )
                await forbid_resp(scope, receive, send)
                return
        track_metrics = self._requests_total is not None and path not in (
            "/metrics",
            "/health/live",
        )

        req_id_raw: str | None = None
        for k, v in scope.get("headers") or ():
            if k == b"x-request-id":
                req_id_raw = v.decode("latin-1", errors="ignore")
                break
        req_id = _sanitize_header_token(req_id_raw) or uuid.uuid4().hex[:16]
        state_dict = scope.setdefault("state", {})
        state_dict["request_id"] = req_id

        start = time.perf_counter()
        status_code = 500
        cache_result: str | None = None
        if track_metrics and self._requests_in_progress is not None:
            self._requests_in_progress.inc()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, cache_result
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Request-ID", req_id)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                headers["X-Process-Time-Ms"] = f"{duration_ms:.2f}"
                x_cache = headers.get("x-cache")
                if x_cache:
                    normalized_cache = x_cache.strip().lower()
                    if normalized_cache in {"hit", "miss"}:
                        cache_result = normalized_cache
                snap_id = state_dict.get("snapshot_id")
                if isinstance(snap_id, str) and snap_id:
                    headers.setdefault("X-Snapshot-ID", snap_id)
                dagster_run_id = state_dict.get("dagster_run_id")
                if isinstance(dagster_run_id, str) and dagster_run_id:
                    headers.setdefault("X-Dagster-Run-ID", dagster_run_id)
                git_sha = state_dict.get("git_commit_sha")
                if isinstance(git_sha, str) and git_sha:
                    headers.setdefault("X-Git-Commit-SHA", git_sha)
            await send(message)

        try:
            if path.startswith("/api/"):
                with (
                    bind_telemetry_context(entry_point="http_api", run_id=req_id),
                    trace_span(
                        SPAN_SERVING_QUERY,
                        {
                            "http.method": method,
                            "http.route": _normalize_metric_path(path),
                        },
                    ),
                ):
                    await self.app(scope, receive, send_wrapper)
            else:
                await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_s = time.perf_counter() - start
            if track_metrics:
                if self._requests_in_progress is not None:
                    self._requests_in_progress.dec()
                matched_route = scope.get("route")
                if matched_route is None and status_code in (404, 405):
                    handler = "unmatched"
                elif matched_route is not None and isinstance(
                    getattr(matched_route, "path", None), str
                ):
                    handler = str(matched_route.path)
                else:
                    handler = _normalize_metric_path(path)
                if self._requests_total is not None:
                    self._requests_total.labels(
                        method=method, handler=handler, status=str(status_code)
                    ).inc()
                if self._request_duration is not None:
                    self._request_duration.labels(
                        method=method, handler=handler
                    ).observe(elapsed_s)
                if cache_result is not None and self._cache_requests_total is not None:
                    self._cache_requests_total.labels(
                        result=cache_result, handler=handler
                    ).inc()

        if self._access_log_json:
            import structlog

            outcome = (
                "not_modified"
                if status_code == 304
                else (
                    "ok"
                    if status_code < 400
                    else ("client_error" if status_code < 500 else "server_error")
                )
            )
            log = structlog.get_logger("finintel.serving.http")
            log.info(
                "http_request_completed",
                entry_point="http_api",
                request_id=req_id,
                run_id=state_dict.get("dagster_run_id"),
                method=method,
                path=path,
                status_code=status_code,
                outcome=outcome,
                duration_ms=round(elapsed_s * 1000.0, 2),
                snapshot_id=state_dict.get("snapshot_id"),
                dagster_run_id=state_dict.get("dagster_run_id"),
                git_commit_sha=state_dict.get("git_commit_sha"),
            )


def attach_observability(api: FastAPI) -> None:
    """Attach pure-ASGI correlation + RED metrics middleware, ``/metrics``, and OTel."""
    from fastapi import Response

    configure_structured_logging()

    metrics_enabled = os.getenv(
        "FININTEL_METRICS_ENABLED", "1"
    ).strip().lower() not in {
        "0",
        "false",
        "no",
    }
    requests_total: Counter | None = None
    request_duration: Histogram | None = None
    requests_in_progress: Gauge | None = None
    cache_requests_total: Counter | None = None

    if metrics_enabled:
        registry = CollectorRegistry()
        requests_total = Counter(
            "http_requests_total",
            "Total HTTP requests served by FinIntel analytical API.",
            labelnames=("method", "handler", "status"),
            registry=registry,
        )
        request_duration = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency histogram in seconds.",
            labelnames=("method", "handler"),
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
            registry=registry,
        )
        requests_in_progress = Gauge(
            "http_requests_in_progress",
            "In-flight HTTP requests currently executing.",
            registry=registry,
            multiprocess_mode="livesum",
        )
        cache_requests_total = Counter(
            "finintel_valkey_cache_requests_total",
            "Total Valkey L2 cache outcomes observed on HTTP responses.",
            labelnames=("result", "handler"),
            registry=registry,
        )

        @api.get("/metrics", include_in_schema=False)
        async def prometheus_metrics() -> Response:
            repo = getattr(api.state, "repository", None)
            conn = getattr(api.state, "duckdb_conn", None)
            if repo is not None:
                try:
                    from finintel.serving.freshness import collect_data_freshness

                    cursor = conn.cursor() if conn is not None else None
                    try:
                        meta = repo.get_snapshot_metadata(connection=cursor)
                    finally:
                        if cursor is not None:
                            cursor.close()
                    raw_sid = meta.get("snapshot_id")
                    raw_pub = meta.get("published_at")
                    collect_data_freshness(
                        snapshot_id=str(raw_sid) if isinstance(raw_sid, str) else None,
                        published_at=str(raw_pub) if isinstance(raw_pub, str) else None,
                        data_root=getattr(repo, "_source", None),
                    )
                except Exception:
                    pass
            return Response(
                content=generate_metrics_payload(registry),
                media_type=CONTENT_TYPE_LATEST,
            )

        api.state._prometheus_live_gauges = (requests_in_progress,)

    api.add_middleware(
        RequestCorrelationMiddleware,
        requests_total=requests_total,
        request_duration=request_duration,
        requests_in_progress=requests_in_progress,
        cache_requests_total=cache_requests_total,
    )

    if is_otel_enabled():
        configure_otel_tracing()
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(api)
        except Exception as exc:
            _logger.warning(
                "OpenTelemetry instrumentation skipped: %s", type(exc).__name__
            )
