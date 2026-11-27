"""Lazy-imported Pydantic HTTP schema loader for ``/api/v3/b2b/*`` and ``/api/v3/open/*`` (V3-25)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ApiScope = Literal[
    "read:derived_risk",
    "write:webhooks",
    "read:rag",
    "read:raw_statements",
]
KeyPrefixMode = Literal["res", "live"]


@dataclass(frozen=True, slots=True)
class ApiGatewaySchemas:
    """Container of lazily-imported Pydantic v2 schema classes."""

    ApiKeyCreateRequest: type[Any]
    ApiKeyMetadataItem: type[Any]
    ApiKeyCreateResponse: type[Any]
    ApiKeyListResponse: type[Any]
    ApiKeyRevokeResponse: type[Any]
    ApiUsageDailyPoint: type[Any]
    ApiUsageSummaryResponse: type[Any]
    B2BWebhookCreateRequest: type[Any]
    B2BWebhookItem: type[Any]
    B2BWebhookCreateResponse: type[Any]
    B2BWebhookListResponse: type[Any]
    B2BWebhookDeleteResponse: type[Any]
    B2BWebhookTestResponse: type[Any]
    OpenDerivedRiskResponse: type[Any]
    OpenScreenerResponse: type[Any]
    OpenStressTestRequest: type[Any]
    OpenStressTestResponse: type[Any]
    OpenRawStatementsResponse: type[Any]


def build_api_gateway_schemas() -> ApiGatewaySchemas:
    """Return Pydantic schema bundle lazily so offline ``mvp-checks`` stays green."""
    from finintel.api_gateway import _http_models as hm

    return ApiGatewaySchemas(
        ApiKeyCreateRequest=hm.ApiKeyCreateRequest,
        ApiKeyMetadataItem=hm.ApiKeyMetadataItem,
        ApiKeyCreateResponse=hm.ApiKeyCreateResponse,
        ApiKeyListResponse=hm.ApiKeyListResponse,
        ApiKeyRevokeResponse=hm.ApiKeyRevokeResponse,
        ApiUsageDailyPoint=hm.ApiUsageDailyPoint,
        ApiUsageSummaryResponse=hm.ApiUsageSummaryResponse,
        B2BWebhookCreateRequest=hm.B2BWebhookCreateRequest,
        B2BWebhookItem=hm.B2BWebhookItem,
        B2BWebhookCreateResponse=hm.B2BWebhookCreateResponse,
        B2BWebhookListResponse=hm.B2BWebhookListResponse,
        B2BWebhookDeleteResponse=hm.B2BWebhookDeleteResponse,
        B2BWebhookTestResponse=hm.B2BWebhookTestResponse,
        OpenDerivedRiskResponse=hm.OpenDerivedRiskResponse,
        OpenScreenerResponse=hm.OpenScreenerResponse,
        OpenStressTestRequest=hm.OpenStressTestRequest,
        OpenStressTestResponse=hm.OpenStressTestResponse,
        OpenRawStatementsResponse=hm.OpenRawStatementsResponse,
    )


__all__ = [
    "ApiGatewaySchemas",
    "ApiScope",
    "KeyPrefixMode",
    "build_api_gateway_schemas",
]
