"""Structured logging, correlation headers, pure-ASGI Prometheus /metrics, and OTel hooks.

Verified Sources:
- structlog Official Documentation (``structlog==26.1.0``, JSONRenderer & contextvars):
  https://www.structlog.org/en/stable/
- Prometheus Python Client & RED Metrics (``prometheus_client``, bundled with
  ``prometheus-fastapi-instrumentator==8.1.0``):
  https://prometheus.github.io/client_python/
- OpenTelemetry Python SDK & FastAPI Instrumentation (``opentelemetry-sdk==1.45.2``,
  ``opentelemetry-instrumentation-fastapi==0.66b2``):
  https://opentelemetry.io/docs/languages/python/
- Starlette Pure ASGI Middleware Specification (zero ``BaseHTTPMiddleware`` stream
  copying or task-group overhead under high concurrency):
  https://www.starlette.io/middleware/#pure-asgi-middleware
"""

from __future__ import annotations

import logging
import os
import time
import uuid

import structlog
from fastapi import FastAPI, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_STRUCTLOG_CONFIGURED = False


def configure_structured_logging() -> None:
    """Configure structlog once for JSON-friendly structured request telemetry."""
    global _STRUCTLOG_CONFIGURED
    if _STRUCTLOG_CONFIGURED:
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
    if len(parts) >= 5 and parts[1] == "api" and parts[3] == "companies":
        parts[4] = "{ticker}"
        return "/".join(parts)
    return path


class RequestCorrelationMiddleware:
    """Pure ASGI middleware attaching correlation headers, RED metrics & structured logs."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        requests_total: Counter | None = None,
        request_duration: Histogram | None = None,
        requests_in_progress: Gauge | None = None,
    ) -> None:
        self.app = app
        self._requests_total = requests_total
        self._request_duration = request_duration
        self._requests_in_progress = requests_in_progress
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

        path = str(scope.get("path", ""))
        method = str(scope.get("method", "GET"))
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
        if track_metrics and self._requests_in_progress is not None:
            self._requests_in_progress.inc()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Request-ID", req_id)
                headers["X-Process-Time-Ms"] = f"{duration_ms:.2f}"
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
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_s = time.perf_counter() - start
            if track_metrics:
                if self._requests_in_progress is not None:
                    self._requests_in_progress.dec()
                handler = _normalize_metric_path(path)
                if self._requests_total is not None:
                    self._requests_total.labels(
                        method=method, handler=handler, status=str(status_code)
                    ).inc()
                if self._request_duration is not None:
                    self._request_duration.labels(
                        method=method, handler=handler
                    ).observe(elapsed_s)

        if self._access_log_json:
            log = structlog.get_logger("finintel.serving.http")
            log.info(
                "http_request_completed",
                request_id=req_id,
                method=method,
                path=path,
                status_code=status_code,
                duration_ms=round(elapsed_s * 1000.0, 2),
                snapshot_id=state_dict.get("snapshot_id"),
                dagster_run_id=state_dict.get("dagster_run_id"),
                git_commit_sha=state_dict.get("git_commit_sha"),
            )


def attach_observability(api: FastAPI) -> None:
    """Attach pure-ASGI correlation + RED metrics middleware, ``/metrics``, and OTel."""
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
        )

        @api.get("/metrics", include_in_schema=False)
        async def prometheus_metrics() -> Response:
            return Response(
                content=generate_latest(registry),
                media_type=CONTENT_TYPE_LATEST,
            )

    api.add_middleware(
        RequestCorrelationMiddleware,
        requests_total=requests_total,
        request_duration=request_duration,
        requests_in_progress=requests_in_progress,
    )

    if os.getenv("FININTEL_OTEL_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(api)
        except Exception as exc:
            logging.getLogger("finintel.serving.observability").warning(
                "OpenTelemetry instrumentation skipped: %s", type(exc).__name__
            )
