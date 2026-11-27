"""V3 router mounting and application middleware wiring for FastAPI serving.

Verified Sources:
- FastAPI Official Documentation (``APIRouter``, ``add_middleware``, ``CORSMiddleware``):
  https://fastapi.tiangolo.com/tutorial/bigger-applications/
  https://fastapi.tiangolo.com/tutorial/cors/
- Starlette ASGI Middleware Ordering (LIFO execution semantics):
  https://www.starlette.io/middleware/
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from finintel.serving._api_helpers import (
    RagAvailabilityMiddleware,
    StressBulkheadMiddleware,
)
from finintel.serving.dependencies import (
    SecurityHeadersMiddleware,
    resolve_cors_allow_credentials,
    resolve_cors_origins,
)
from finintel.serving.observability import attach_observability


def configure_app_middlewares(
    api: FastAPI, *, cors_origins: Sequence[str] | None = None
) -> None:
    """Attach security, bulkhead, RAG availability, observability, and CORS middlewares."""
    allow_cors_credentials = resolve_cors_allow_credentials()
    allowed_origins = resolve_cors_origins(
        cors_origins, allow_credentials=allow_cors_credentials
    )

    api.add_middleware(SecurityHeadersMiddleware)
    api.add_middleware(StressBulkheadMiddleware)
    api.add_middleware(RagAvailabilityMiddleware)
    attach_observability(api)

    # Deny-by-default CORS: only attach CORSMiddleware when explicit origins are configured.
    if allowed_origins:
        api.add_middleware(
            CORSMiddleware,
            allow_origins=list(allowed_origins),
            allow_credentials=allow_cors_credentials,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=[
                "If-None-Match",
                "Content-Type",
                "X-Request-ID",
                "X-Requested-With",
            ],
            expose_headers=[
                "ETag",
                "Cache-Control",
                "X-Request-ID",
                "X-Snapshot-ID",
                "X-Dagster-Run-ID",
                "X-Git-Commit-SHA",
                "X-Cache",
            ],
        )


def mount_v3_routers(api: FastAPI, *, valkey_url: str | None = None) -> None:
    """Lazy-import and mount all V3 domain routers onto the FastAPI application."""
    from finintel.alerts.router import mount_alerts_router
    from finintel.api_gateway.router import mount_api_gateway_router
    from finintel.auth.router import mount_identity_workspace_routers
    from finintel.ops.router import mount_ops_routers
    from finintel.private_credit.router import mount_private_hitl_routers
    from finintel.quota.router import mount_quota_router
    from finintel.serving.exports.router import create_exports_router
    from finintel.serving.routers.bonds_v3 import create_bonds_v3_router

    mount_identity_workspace_routers(api, valkey_url=valkey_url)
    mount_alerts_router(api)
    api.include_router(create_exports_router())
    api.include_router(create_bonds_v3_router())
    mount_quota_router(api, valkey_url=valkey_url)
    mount_ops_routers(api)
    mount_api_gateway_router(api, valkey_url=valkey_url)
    mount_private_hitl_routers(api)
