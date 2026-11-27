"""Open Research & B2B API Gateway package (V3-25) with lazy platform imports."""

from finintel.api_gateway._memory_store import InMemoryApiGatewayStore
from finintel.api_gateway.keys import (
    DEFAULT_DAILY_LIMIT,
    DEFAULT_RATE_LIMIT_PER_MIN,
    VALID_API_SCOPES,
    ApiGatewayStoreProtocol,
    ApiKeyRecord,
    ApiUsageRecord,
    B2BWebhookRecord,
    generate_api_key,
    hash_api_key,
    is_well_formed_api_key,
    normalize_scopes,
    rotate_api_key_record,
    verify_api_key_hash,
)
from finintel.api_gateway.middleware import (
    API_KEY_HEADER_NAME,
    DERIVED_RISK_LICENSE_SCOPE,
    ENV_RAW_REDISTRIBUTION,
    LICENSE_SCOPE_HEADER_NAME,
    RAW_REDISTRIBUTION_LICENSE_SCOPE,
    SNAPSHOT_ID_HEADER_NAME,
    ApiKeyContext,
    is_raw_redistribution_enabled,
)
from finintel.api_gateway.rate_limiter import (
    RateLimitDecision,
    SlidingWindowRateLimiter,
    rate_limit_headers,
)

__all__ = [
    "API_KEY_HEADER_NAME",
    "DEFAULT_DAILY_LIMIT",
    "DEFAULT_RATE_LIMIT_PER_MIN",
    "DERIVED_RISK_LICENSE_SCOPE",
    "ENV_RAW_REDISTRIBUTION",
    "LICENSE_SCOPE_HEADER_NAME",
    "RAW_REDISTRIBUTION_LICENSE_SCOPE",
    "SNAPSHOT_ID_HEADER_NAME",
    "VALID_API_SCOPES",
    "ApiGatewayStoreProtocol",
    "ApiKeyContext",
    "ApiKeyRecord",
    "ApiUsageRecord",
    "B2BWebhookRecord",
    "InMemoryApiGatewayStore",
    "RateLimitDecision",
    "SlidingWindowRateLimiter",
    "generate_api_key",
    "hash_api_key",
    "is_raw_redistribution_enabled",
    "is_well_formed_api_key",
    "normalize_scopes",
    "rate_limit_headers",
    "rotate_api_key_record",
    "verify_api_key_hash",
]
