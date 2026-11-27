"""Sync & async FinIntel Open Research SDK clients with ETag 304, retry, and HMAC verify."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, TypeVar
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from finintel_sdk.models import (
    CompanyRiskReport,
    RawStatementsResponse,
    ScreenerPage,
    StressScenarioInput,
    StressTestResult,
)

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class FinIntelSDKError(Exception):
    """Base error raised for FinIntel API failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = message
        self.retry_after = retry_after


class AuthenticationError(FinIntelSDKError):
    """Raised on HTTP 401 (missing, invalid, or revoked API key)."""


class PermissionDeniedError(FinIntelSDKError):
    """Raised on HTTP 403 (missing scope or raw redistribution disabled)."""


class NotFoundError(FinIntelSDKError):
    """Raised on HTTP 404 (ticker or resource not found)."""


class RateLimitError(FinIntelSDKError):
    """Raised on HTTP 429 after retry attempts are exhausted."""


def sign_webhook_payload(raw_body: bytes | str, secret: str) -> str:
    """Compute ``X-FinIntel-Signature`` header value ``sha256=<hex>``."""
    body_bytes = raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body
    digest = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_webhook_signature(
    raw_body: bytes | str,
    secret: str,
    signature_header: str,
) -> bool:
    """Verify ``X-FinIntel-Signature: sha256=...`` against raw HTTP body bytes."""
    if not secret or not signature_header:
        return False
    clean_sig = signature_header.strip()
    if not clean_sig.startswith("sha256="):
        return False
    expected = sign_webhook_payload(raw_body, secret)
    return hmac.compare_digest(expected, clean_sig)


def _parse_retry_after(headers: Mapping[str, str], attempt: int) -> float:
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is not None:
        try:
            val = float(raw.strip())
            if val > 0:
                return min(val, 60.0)
        except ValueError:
            pass
    return float(min(0.5 * (2**attempt), 10.0))


def _extract_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
            return str(payload["detail"])
    except Exception:
        pass
    return f"FinIntel API returned HTTP {response.status_code}"


def _raise_for_error(response: httpx.Response) -> None:
    detail = _extract_error_detail(response)
    code = response.status_code
    retry_after = _parse_retry_after(response.headers, 0) if code == 429 else None
    if code == 401:
        raise AuthenticationError(detail, status_code=code)
    if code == 403:
        raise PermissionDeniedError(detail, status_code=code)
    if code == 404:
        raise NotFoundError(detail, status_code=code)
    if code == 429:
        raise RateLimitError(detail, status_code=code, retry_after=retry_after)
    raise FinIntelSDKError(detail, status_code=code)


def _cache_key(method: str, path: str, params: Mapping[str, Any] | None) -> str:
    if not params:
        return f"{method}:{path}"
    items = sorted((str(k), str(v)) for k, v in params.items() if v is not None)
    return f"{method}:{path}?{urlencode(items)}"


class FinIntelClient:
    """Synchronous client for FinIntel Open Research API (``ETag`` + retry built-in)."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 15.0,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("api_key must be a non-empty string.")
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._max_retries = max(0, int(max_retries))
        self._sleep = sleep_fn or time.sleep
        self._etag_cache: dict[str, tuple[str, dict[str, Any]]] = {}
        self.etag_cache_hits = 0
        self.last_headers: dict[str, str] = {}
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            transport=transport,
            headers={"X-API-Key": self._api_key},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> FinIntelClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _request_model(
        self,
        method: str,
        path: str,
        model_cls: type[_ModelT],
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> _ModelT:
        clean_params = (
            {k: v for k, v in params.items() if v is not None} if params else None
        )
        key = _cache_key(method, path, clean_params)
        cached = self._etag_cache.get(key) if method == "GET" else None

        for attempt in range(self._max_retries + 1):
            req_headers: dict[str, str] = {}
            if cached is not None:
                req_headers["If-None-Match"] = cached[0]

            response = self._client.request(
                method,
                path,
                params=clean_params,
                json=json_body,
                headers=req_headers,
            )
            self.last_headers = dict(response.headers)

            if response.status_code == 304 and cached is not None:
                self.etag_cache_hits += 1
                return model_cls.model_validate(cached[1])

            if response.status_code in _RETRYABLE_STATUS_CODES:
                if attempt < self._max_retries:
                    delay = _parse_retry_after(response.headers, attempt)
                    self._sleep(delay)
                    continue
                _raise_for_error(response)

            if response.status_code >= 400:
                _raise_for_error(response)

            payload = response.json()
            if not isinstance(payload, dict):
                raise FinIntelSDKError(
                    "Unexpected non-object JSON payload from FinIntel API.",
                    status_code=response.status_code,
                )
            etag = response.headers.get("ETag") or response.headers.get("etag")
            if method == "GET" and etag:
                self._etag_cache[key] = (etag, payload)
            return model_cls.model_validate(payload)

        raise FinIntelSDKError("Request exhausted retry budget.")

    def get_company_risk(
        self,
        ticker: str,
        *,
        statement_scope: Literal["CONSOLIDATED", "SEPARATE"] = "CONSOLIDATED",
        period_view: Literal["TTM", "Annual", "Quarterly"] = "TTM",
    ) -> CompanyRiskReport:
        clean = ticker.strip().upper()
        return self._request_model(
            "GET",
            f"/api/v3/open/companies/{clean}/risk",
            CompanyRiskReport,
            params={"statement_scope": statement_scope, "period_view": period_view},
        )

    def screen_market(
        self,
        *,
        statement_scope: Literal["CONSOLIDATED", "SEPARATE"] = "CONSOLIDATED",
        period_type: Literal["QUARTER", "ANNUAL"] = "QUARTER",
        period_end_date: str | None = None,
        exchange: str | None = None,
        risk_tier: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ScreenerPage:
        return self._request_model(
            "GET",
            "/api/v3/open/screener",
            ScreenerPage,
            params={
                "statement_scope": statement_scope,
                "period_type": period_type,
                "period_end_date": period_end_date,
                "exchange": exchange,
                "risk_tier": risk_tier,
                "limit": limit,
                "offset": offset,
            },
        )

    def run_stress_test(
        self,
        scenario: StressScenarioInput,
    ) -> StressTestResult:
        return self._request_model(
            "POST",
            "/api/v3/open/stress-test",
            StressTestResult,
            json_body=scenario.model_dump(mode="json", exclude_none=True),
        )

    def get_company_statements(
        self,
        ticker: str,
        *,
        statement_type: str = "INCOME_STATEMENT",
        statement_scope: Literal["CONSOLIDATED", "SEPARATE"] = "CONSOLIDATED",
        period_type: Literal["QUARTER", "ANNUAL"] = "QUARTER",
        periods_limit: int = 8,
    ) -> RawStatementsResponse:
        clean = ticker.strip().upper()
        return self._request_model(
            "GET",
            f"/api/v3/open/companies/{clean}/statements",
            RawStatementsResponse,
            params={
                "statement_type": statement_type,
                "statement_scope": statement_scope,
                "period_type": period_type,
                "periods_limit": periods_limit,
            },
        )


class AsyncFinIntelClient:
    """Asynchronous client for FinIntel Open Research API (``ETag`` + retry built-in)."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 15.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("api_key must be a non-empty string.")
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._max_retries = max(0, int(max_retries))
        self._sleep = sleep_fn or asyncio.sleep
        self._etag_cache: dict[str, tuple[str, dict[str, Any]]] = {}
        self.etag_cache_hits = 0
        self.last_headers: dict[str, str] = {}
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            transport=transport,
            headers={"X-API-Key": self._api_key},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncFinIntelClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def _request_model(
        self,
        method: str,
        path: str,
        model_cls: type[_ModelT],
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> _ModelT:
        clean_params = (
            {k: v for k, v in params.items() if v is not None} if params else None
        )
        key = _cache_key(method, path, clean_params)
        cached = self._etag_cache.get(key) if method == "GET" else None

        for attempt in range(self._max_retries + 1):
            req_headers: dict[str, str] = {}
            if cached is not None:
                req_headers["If-None-Match"] = cached[0]

            response = await self._client.request(
                method,
                path,
                params=clean_params,
                json=json_body,
                headers=req_headers,
            )
            self.last_headers = dict(response.headers)

            if response.status_code == 304 and cached is not None:
                self.etag_cache_hits += 1
                return model_cls.model_validate(cached[1])

            if response.status_code in _RETRYABLE_STATUS_CODES:
                if attempt < self._max_retries:
                    delay = _parse_retry_after(response.headers, attempt)
                    await self._sleep(delay)
                    continue
                _raise_for_error(response)

            if response.status_code >= 400:
                _raise_for_error(response)

            payload = response.json()
            if not isinstance(payload, dict):
                raise FinIntelSDKError(
                    "Unexpected non-object JSON payload from FinIntel API.",
                    status_code=response.status_code,
                )
            etag = response.headers.get("ETag") or response.headers.get("etag")
            if method == "GET" and etag:
                self._etag_cache[key] = (etag, payload)
            return model_cls.model_validate(payload)

        raise FinIntelSDKError("Request exhausted retry budget.")

    async def get_company_risk(
        self,
        ticker: str,
        *,
        statement_scope: Literal["CONSOLIDATED", "SEPARATE"] = "CONSOLIDATED",
        period_view: Literal["TTM", "Annual", "Quarterly"] = "TTM",
    ) -> CompanyRiskReport:
        clean = ticker.strip().upper()
        return await self._request_model(
            "GET",
            f"/api/v3/open/companies/{clean}/risk",
            CompanyRiskReport,
            params={"statement_scope": statement_scope, "period_view": period_view},
        )

    async def screen_market(
        self,
        *,
        statement_scope: Literal["CONSOLIDATED", "SEPARATE"] = "CONSOLIDATED",
        period_type: Literal["QUARTER", "ANNUAL"] = "QUARTER",
        period_end_date: str | None = None,
        exchange: str | None = None,
        risk_tier: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ScreenerPage:
        return await self._request_model(
            "GET",
            "/api/v3/open/screener",
            ScreenerPage,
            params={
                "statement_scope": statement_scope,
                "period_type": period_type,
                "period_end_date": period_end_date,
                "exchange": exchange,
                "risk_tier": risk_tier,
                "limit": limit,
                "offset": offset,
            },
        )

    async def run_stress_test(
        self,
        scenario: StressScenarioInput,
    ) -> StressTestResult:
        return await self._request_model(
            "POST",
            "/api/v3/open/stress-test",
            StressTestResult,
            json_body=scenario.model_dump(mode="json", exclude_none=True),
        )

    async def get_company_statements(
        self,
        ticker: str,
        *,
        statement_type: str = "INCOME_STATEMENT",
        statement_scope: Literal["CONSOLIDATED", "SEPARATE"] = "CONSOLIDATED",
        period_type: Literal["QUARTER", "ANNUAL"] = "QUARTER",
        periods_limit: int = 8,
    ) -> RawStatementsResponse:
        clean = ticker.strip().upper()
        return await self._request_model(
            "GET",
            f"/api/v3/open/companies/{clean}/statements",
            RawStatementsResponse,
            params={
                "statement_type": statement_type,
                "statement_scope": statement_scope,
                "period_type": period_type,
                "periods_limit": periods_limit,
            },
        )


__all__ = [
    "AsyncFinIntelClient",
    "AuthenticationError",
    "FinIntelClient",
    "FinIntelSDKError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
    "sign_webhook_payload",
    "verify_webhook_signature",
]
