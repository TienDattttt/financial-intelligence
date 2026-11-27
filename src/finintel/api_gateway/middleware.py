"""Fail-closed license gate, X-API-Key authentication, and header enforcement (V3-25)."""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from finintel.api_gateway.keys import (
    ApiGatewayStoreProtocol,
    ApiKeyRecord,
    hash_api_key,
    is_well_formed_api_key,
    verify_api_key_hash,
)
from finintel.api_gateway.rate_limiter import (
    RateLimitDecision,
    SlidingWindowRateLimiter,
    rate_limit_headers,
)

if TYPE_CHECKING:
    from fastapi import Request, Response
else:
    try:
        from fastapi import Request, Response
    except ImportError:  # pragma: no cover - mvp-checks without platform deps
        Request = Any
        Response = Any

logger = logging.getLogger("finintel.api_gateway.middleware")

API_KEY_HEADER_NAME = "X-API-Key"
LICENSE_SCOPE_HEADER_NAME = "X-FinIntel-License-Scope"
SNAPSHOT_ID_HEADER_NAME = "X-Snapshot-ID"
ENV_RAW_REDISTRIBUTION = "FININTEL_ENABLE_RAW_REDISTRIBUTION"
DERIVED_RISK_LICENSE_SCOPE = "read:derived_risk"
RAW_REDISTRIBUTION_LICENSE_SCOPE = "read:raw_statements"


def is_raw_redistribution_enabled() -> bool:
    """Fail-closed check for raw BCTC/price redistribution (defaults strictly ``False``)."""
    raw = os.getenv(ENV_RAW_REDISTRIBUTION, "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class ApiKeyContext:
    """Authenticated API key principal and rate-limit decision for one request."""

    key: ApiKeyRecord
    decision: RateLimitDecision

    def headers(
        self,
        *,
        snapshot_id: str | None = None,
        etag: str | None = None,
        license_scope: str = DERIVED_RISK_LICENSE_SCOPE,
    ) -> dict[str, str]:
        """Return standard B2B response headers (rate limit, snapshot, ETag, scope)."""
        out = rate_limit_headers(self.decision)
        out[LICENSE_SCOPE_HEADER_NAME] = license_scope
        if snapshot_id is not None:
            out[SNAPSHOT_ID_HEADER_NAME] = snapshot_id
        if etag is not None:
            out["ETag"] = etag
        return out


def apply_api_headers(
    response: Response,
    ctx: ApiKeyContext,
    *,
    snapshot_id: str | None = None,
    etag: str | None = None,
    license_scope: str = DERIVED_RISK_LICENSE_SCOPE,
) -> None:
    """Apply standardized B2B headers onto a FastAPI ``Response`` object."""
    for name, value in ctx.headers(
        snapshot_id=snapshot_id, etag=etag, license_scope=license_scope
    ).items():
        response.headers[name] = value


def authenticate_api_key_request(
    *,
    raw_api_key: str | None,
    store: ApiGatewayStoreProtocol | None,
    rate_limiter: SlidingWindowRateLimiter,
    endpoint_label: str,
) -> ApiKeyContext:
    """Validate ``X-API-Key``, enforce instant revocation, and apply sliding-window rate limit.

    Security invariant: never logs ``raw_api_key`` in exceptions or log records.
    """
    from fastapi import HTTPException

    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Cổng Open Research API chưa được cấu hình kho lưu trữ khóa.",
        )

    plaintext = (raw_api_key or "").strip()
    if not plaintext:
        raise HTTPException(
            status_code=401,
            detail="Thiếu header X-API-Key hợp lệ.",
        )
    if not is_well_formed_api_key(plaintext):
        raise HTTPException(
            status_code=401,
            detail="API Key không đúng định dạng hoặc không hợp lệ.",
        )

    candidate_hash = hash_api_key(plaintext)
    if rate_limiter.is_key_revoked_cached(candidate_hash):
        raise HTTPException(
            status_code=401,
            detail="API Key đã bị thu hồi.",
        )

    record = store.find_key_by_hash(candidate_hash)
    if record is None or not verify_api_key_hash(plaintext, record.key_hash):
        raise HTTPException(
            status_code=401,
            detail="API Key không tồn tại hoặc đã bị vô hiệu hóa.",
        )

    now = datetime.now(UTC)
    if not record.is_usable(now=now):
        raise HTTPException(
            status_code=401,
            detail="API Key đã bị thu hồi hoặc đã hết hạn sử dụng.",
        )

    decision = rate_limiter.check_and_consume(
        key_id=record.key_id,
        rate_limit_per_min=record.rate_limit_per_min,
        daily_limit=record.daily_limit,
    )
    if not decision.allowed:
        headers = rate_limit_headers(decision)
        store.record_usage(
            usage_date=now.date(),
            key_id=record.key_id,
            user_id=record.user_id,
            endpoint=endpoint_label,
            status_code=429,
            cache_hit=False,
            now=now,
        )
        logger.info(
            "api_gateway_rate_limited",
            extra={
                "event": "api_gateway_rate_limited",
                "key_prefix": record.key_prefix,
                "endpoint": endpoint_label,
                "reason": decision.reason,
            },
        )
        raise HTTPException(
            status_code=429,
            detail=(
                "Vượt ngưỡng tần suất Open Research API. "
                "Vui lòng tôn trọng Retry-After và sử dụng If-None-Match (ETag)."
            ),
            headers=headers,
        )

    store.touch_key_last_used(record.key_id, used_at=now)
    return ApiKeyContext(key=record, decision=decision)


def build_api_key_dependency(
    *,
    get_store: Callable[[], ApiGatewayStoreProtocol | None],
    get_limiter: Callable[[], SlidingWindowRateLimiter],
    required_scope: str = DERIVED_RISK_LICENSE_SCOPE,
    requires_raw_redistribution: bool = False,
) -> Callable[[Request], ApiKeyContext]:
    """Create a FastAPI dependency enforcing auth, rate limit, raw gate, and scopes."""

    def _dependency(request: Request) -> ApiKeyContext:
        from fastapi import HTTPException

        raw_key = request.headers.get(API_KEY_HEADER_NAME)
        endpoint_label = str(request.url.path)
        ctx = authenticate_api_key_request(
            raw_api_key=raw_key,
            store=get_store(),
            rate_limiter=get_limiter(),
            endpoint_label=endpoint_label,
        )
        now = datetime.now(UTC)
        store = get_store()

        if requires_raw_redistribution and not is_raw_redistribution_enabled():
            if store is not None:
                store.record_usage(
                    usage_date=now.date(),
                    key_id=ctx.key.key_id,
                    user_id=ctx.key.user_id,
                    endpoint=endpoint_label,
                    status_code=403,
                    now=now,
                )
            raise HTTPException(
                status_code=403,
                detail=(
                    "Truy cập dữ liệu BCTC/giá gốc đang bị khóa mặc định "
                    "(FININTEL_ENABLE_RAW_REDISTRIBUTION=False). "
                    "Open Research API chỉ cung cấp các chỉ báo rủi ro phái sinh (read:derived_risk)."
                ),
                headers=ctx.headers(license_scope=DERIVED_RISK_LICENSE_SCOPE),
            )

        if required_scope not in ctx.key.scopes:
            if store is not None:
                store.record_usage(
                    usage_date=now.date(),
                    key_id=ctx.key.key_id,
                    user_id=ctx.key.user_id,
                    endpoint=endpoint_label,
                    status_code=403,
                    now=now,
                )
            raise HTTPException(
                status_code=403,
                detail=f"API Key thiếu phạm vi quyền bắt buộc: {required_scope}.",
                headers=ctx.headers(),
            )

        return ctx

    return _dependency


def record_open_api_hit(
    store: ApiGatewayStoreProtocol | None,
    ctx: ApiKeyContext,
    *,
    endpoint: str,
    status_code: int,
    cache_hit: bool = False,
) -> None:
    """Persist daily usage counter without logging secrets."""
    if store is None:
        return
    now = datetime.now(UTC)
    store.record_usage(
        usage_date=now.date(),
        key_id=ctx.key.key_id,
        user_id=ctx.key.user_id,
        endpoint=endpoint,
        status_code=status_code,
        cache_hit=cache_hit,
        now=now,
    )


def build_snapshot_etag(snapshot_id: str | None) -> str | None:
    """Format RFC 9110 strong quoted ETag for Open Research API responses."""
    if not snapshot_id:
        return None
    clean = snapshot_id.strip().replace('"', "")
    return f'"{clean}"' if clean else None


def matches_if_none_match(header_val: str | None, quoted_etag: str | None) -> bool:
    """Compare ``If-None-Match`` header (exact, weak ``W/``, or ``*``) against ETag."""
    if not header_val or not quoted_etag:
        return False
    target = quoted_etag.strip()
    for raw_tag in header_val.split(","):
        candidate = raw_tag.strip()
        if candidate == "*":
            return True
        if candidate.startswith("W/"):
            candidate = candidate[2:].strip()
        if candidate == target:
            return True
    return False


__all__ = [
    "API_KEY_HEADER_NAME",
    "DERIVED_RISK_LICENSE_SCOPE",
    "ENV_RAW_REDISTRIBUTION",
    "LICENSE_SCOPE_HEADER_NAME",
    "RAW_REDISTRIBUTION_LICENSE_SCOPE",
    "SNAPSHOT_ID_HEADER_NAME",
    "ApiKeyContext",
    "apply_api_headers",
    "authenticate_api_key_request",
    "build_api_key_dependency",
    "build_snapshot_etag",
    "is_raw_redistribution_enabled",
    "matches_if_none_match",
    "record_open_api_hit",
]
