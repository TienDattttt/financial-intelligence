"""FastAPI router for V3 corporate bond maturity & payment-delay signals (Port 4).

Verified Sources:
    - spec/v3/SPEC-v3-fx-stress-and-bond-signals.md (§4, §5.1, §8)
    - FastAPI Official Documentation (``APIRouter``, ``Response``, ``Path``, ``Query``):
      https://fastapi.tiangolo.com/tutorial/bigger-applications/
    - RFC 9110 HTTP Conditional Requests (``ETag`` & ``If-None-Match`` -> ``304``):
      https://httpwg.org/specs/rfc9110.html#field.etag
"""

from __future__ import annotations

import math
from datetime import date
from typing import Literal

import duckdb
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi import Path as PathParam
from pydantic import BaseModel

from finintel.analytics.stress_fx import (
    BondPressureStatus,
    evaluate_bond_liquidity_signal,
)
from finintel.serving._api_helpers import _extract_snapshot_id, _finite_scalar
from finintel.serving.cache import ValkeyResponseCache, build_snapshot_cache_key
from finintel.serving.dependencies import (
    get_db_cursor,
    get_query_service,
    get_response_cache,
)
from finintel.serving.freshness import (
    build_composite_cache_token,
    format_composite_etag,
    if_none_match_matches,
    resolve_serving_data_root,
)
from finintel.serving.queries import DashboardQueryService

_TICKER_PATTERN = r"^[A-Za-z0-9]{1,10}$"
_CACHE_CONTROL = "private, max-age=0, must-revalidate"


class CompanyBondSignalsResponse(BaseModel):
    """V3 Company route response: Extension Port 4 corporate bond maturity & delay signals."""

    snapshot_id: str | None = None
    issuer_id: str
    ticker: str
    has_bond_data: bool = False
    has_outstanding_bonds: bool = False
    as_of_date: date | None = None
    outstanding_bond_vnd: float | None = None
    bond_maturity_12m_vnd: float | None = None
    total_cash_and_st_investments_vnd: float | None = None
    cash_period_end_date: date | None = None
    bond_maturity_to_cash_ratio: float | None = None
    bond_payment_delay_flag: bool = False
    liquidity_pressure_status: BondPressureStatus = "NO_BONDS"
    source_provider: str | None = None
    source_url: str | None = None
    payload_sha256: str | None = None


def _opt_float(row: dict[str, object] | None, key: str) -> float | None:
    if row is None:
        return None
    raw = row.get(key)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        val = float(raw)
        return _finite_scalar(val) if math.isfinite(val) else None
    return None


def _opt_str(row: dict[str, object] | None, key: str) -> str | None:
    if row is None:
        return None
    raw = row.get(key)
    return str(raw) if raw is not None else None


def create_bonds_v3_router() -> APIRouter:
    """Build the ``/api/v3/companies/{ticker}/bond-signals`` router."""
    router = APIRouter(tags=["v3-bonds"])

    @router.get(
        "/api/v3/companies/{ticker}/bond-signals",
        response_model=CompanyBondSignalsResponse,
    )
    def get_company_bond_signals(
        request: Request,
        response: Response,
        ticker: str = PathParam(..., pattern=_TICKER_PATTERN),
        statement_scope: Literal["CONSOLIDATED", "SEPARATE"] = Query("CONSOLIDATED"),
        as_of: date | None = Query(None),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        cursor: duckdb.DuckDBPyConnection = Depends(get_db_cursor),
        service: DashboardQueryService = Depends(get_query_service),
        cache: ValkeyResponseCache = Depends(get_response_cache),
    ) -> CompanyBondSignalsResponse | Response:
        clean_ticker = ticker.strip().upper()
        with service.repository.snapshot_session(connection=cursor) as pinned:
            snap_id = _extract_snapshot_id(
                pinned.get_snapshot_metadata(connection=cursor)
            )
            data_root = resolve_serving_data_root(
                getattr(service.repository, "_source", None)
            )
            if isinstance(snap_id, str) and snap_id:
                request.state.snapshot_id = snap_id
            etag = format_composite_etag(snap_id, data_root) if snap_id else None
            if etag and if_none_match_matches(if_none_match, etag):
                return Response(
                    status_code=304,
                    headers={"ETag": etag, "Cache-Control": _CACHE_CONTROL},
                )

            cache_key: str | None = None
            if cache.enabled and snap_id is not None:
                comp = build_composite_cache_token(snap_id, data_root)
                cache_key = build_snapshot_cache_key(
                    comp or snap_id,
                    "v3/companies/bond-signals",
                    {
                        "ticker": clean_ticker,
                        "statement_scope": statement_scope,
                        "as_of": as_of.isoformat() if as_of else None,
                    },
                )
                cached = cache.get_bytes(cache_key)
                if cached is not None:
                    hdrs = {
                        "Cache-Control": _CACHE_CONTROL,
                        "X-Cache": "HIT",
                        **({"ETag": etag} if etag else {}),
                    }
                    return Response(
                        content=cached, media_type="application/json", headers=hdrs
                    )

            try:
                issuer_id, resolved_ticker, cash_vnd, cash_period_end, bond_row = (
                    DashboardQueryService(pinned).get_company_bond_signals_context(
                        clean_ticker,
                        statement_scope=statement_scope,
                        as_of=as_of,
                        connection=cursor,
                    )
                )
            except LookupError:
                raise HTTPException(
                    status_code=404, detail="Issuer not found."
                ) from None

        out_bond = _opt_float(bond_row, "outstanding_bond_vnd")
        mat_12m = _opt_float(bond_row, "bond_maturity_12m_vnd")
        delay_flag = bool(
            bond_row.get("bond_payment_delay_flag", False) if bond_row else False
        )
        assessment = evaluate_bond_liquidity_signal(
            outstanding_bond_vnd=out_bond,
            bond_maturity_12m_vnd=mat_12m,
            bond_payment_delay_flag=delay_flag,
            total_cash_and_st_investments_vnd=cash_vnd,
        )
        raw_as_of = bond_row.get("as_of_date") if bond_row else None
        body = CompanyBondSignalsResponse(
            snapshot_id=snap_id,
            issuer_id=issuer_id,
            ticker=resolved_ticker,
            has_bond_data=bond_row is not None,
            has_outstanding_bonds=assessment.has_outstanding_bonds,
            as_of_date=raw_as_of if isinstance(raw_as_of, date) else None,
            outstanding_bond_vnd=out_bond,
            bond_maturity_12m_vnd=mat_12m,
            total_cash_and_st_investments_vnd=cash_vnd,
            cash_period_end_date=cash_period_end,
            bond_maturity_to_cash_ratio=assessment.bond_maturity_to_cash_ratio,
            bond_payment_delay_flag=delay_flag,
            liquidity_pressure_status=assessment.liquidity_pressure_status,
            source_provider=_opt_str(bond_row, "source_provider"),
            source_url=_opt_str(bond_row, "source_url"),
            payload_sha256=_opt_str(bond_row, "payload_sha256"),
        )
        response.headers["Cache-Control"] = _CACHE_CONTROL
        if etag:
            response.headers["ETag"] = etag
        if cache_key is not None and cache.enabled:
            raw = body.model_dump_json().encode("utf-8")
            cache.set_bytes(cache_key, raw)
            hdrs = {
                "Cache-Control": _CACHE_CONTROL,
                "X-Cache": "MISS",
                **({"ETag": etag} if etag else {}),
            }
            return Response(content=raw, media_type="application/json", headers=hdrs)
        return body

    return router


__all__ = ["CompanyBondSignalsResponse", "create_bonds_v3_router"]
