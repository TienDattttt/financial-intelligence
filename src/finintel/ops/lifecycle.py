"""Issuer listing lifecycle tracking service for INTERNAL_OPS (V3-23).

Verified Sources:
    spec/v3/SPEC-v3-analyst-depth-and-ops-qa.md (§1, §4, §5.2)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import polars as pl

from finintel.auth._store import AppStoreProtocol
from finintel.auth.dependencies import AuthenticatedUser
from finintel.ops.qa_service import (
    IssuerLifecycleEventRecord,
    OpsQaError,
    OpsQaStoreProtocol,
)
from finintel.workspace.audit import record_audit_event

_TICKER_RE = re.compile(r"^[A-Z0-9]{1,10}$")
_VALID_EXCHANGES = frozenset({"HOSE", "HNX", "UPCOM", "DELISTED"})
_VALID_EVENT_TYPES = frozenset(
    {
        "IPO_LISTING",
        "EXCHANGE_TRANSFER",
        "DELISTING",
        "FY_END_CHANGE",
        "METADATA_SYNC",
    }
)


@dataclass(frozen=True)
class IssuerLifecycleView:
    issuer_id: str
    ticker: str
    company_name_vn: str | None
    exchange: str
    accounting_regime: str
    icb_name_level2: str | None
    icb_name_level3: str | None
    delisted_date: date | None
    fy_end_month: int
    recent_events: tuple[IssuerLifecycleEventRecord, ...]


class OpsLifecycleService:
    """Tracks IPO listings, exchange transfers, delistings, and FY-end months."""

    def __init__(
        self,
        qa_store: OpsQaStoreProtocol,
        *,
        audit_store: AppStoreProtocol | None = None,
    ) -> None:
        self._qa = qa_store
        self._audit = audit_store

    def list_lifecycle(
        self,
        snapshot_issuers: pl.DataFrame,
        *,
        exchange: str | None = None,
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[IssuerLifecycleView], int]:
        events = self._qa.list_lifecycle_events()
        events_by_ticker: dict[str, list[IssuerLifecycleEventRecord]] = {}
        for ev in events:
            events_by_ticker.setdefault(ev.ticker, []).append(ev)

        rows: list[dict[str, object]] = (
            snapshot_issuers.sort("ticker").to_dicts()
            if not snapshot_issuers.is_empty()
            else []
        )
        seen_tickers: set[str] = set()
        items: list[IssuerLifecycleView] = []

        for row in rows:
            ticker = str(row.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            seen_tickers.add(ticker)
            ev_list = events_by_ticker.get(ticker, [])
            latest_ev = ev_list[0] if ev_list else None
            eff_exchange = (
                latest_ev.exchange
                if latest_ev is not None
                else str(row.get("exchange") or "HOSE")
            )
            fy_month = latest_ev.fy_end_month if latest_ev is not None else 12
            delisted = row.get("delisted_date")
            eff_delisted = (
                latest_ev.effective_date
                if latest_ev is not None and latest_ev.event_type == "DELISTING"
                else (delisted if isinstance(delisted, date) else None)
            )
            items.append(
                IssuerLifecycleView(
                    issuer_id=str(row.get("issuer_id") or ticker),
                    ticker=ticker,
                    company_name_vn=(
                        str(row["company_name_vn"])
                        if row.get("company_name_vn") is not None
                        else None
                    ),
                    exchange=eff_exchange,
                    accounting_regime=str(row.get("accounting_regime") or "CORPORATE"),
                    icb_name_level2=(
                        str(row["icb_name_level2"])
                        if row.get("icb_name_level2") is not None
                        else None
                    ),
                    icb_name_level3=(
                        str(row["icb_name_level3"])
                        if row.get("icb_name_level3") is not None
                        else None
                    ),
                    delisted_date=eff_delisted,
                    fy_end_month=fy_month,
                    recent_events=tuple(ev_list[:5]),
                )
            )

        for ticker, ev_list in sorted(events_by_ticker.items()):
            if ticker in seen_tickers:
                continue
            latest = ev_list[0]
            items.append(
                IssuerLifecycleView(
                    issuer_id=latest.issuer_id,
                    ticker=ticker,
                    company_name_vn=None,
                    exchange=latest.exchange,
                    accounting_regime="CORPORATE",
                    icb_name_level2=None,
                    icb_name_level3=None,
                    delisted_date=(
                        latest.effective_date
                        if latest.event_type == "DELISTING"
                        else None
                    ),
                    fy_end_month=latest.fy_end_month,
                    recent_events=tuple(ev_list[:5]),
                )
            )

        if exchange is not None and exchange.strip():
            ex_upper = exchange.strip().upper()
            items = [i for i in items if i.exchange.upper() == ex_upper]
        if q is not None and q.strip():
            needle = q.strip().lower()
            items = [
                i
                for i in items
                if needle in i.ticker.lower()
                or (
                    i.company_name_vn is not None
                    and needle in i.company_name_vn.lower()
                )
            ]
        total = len(items)
        return items[offset : offset + limit], total

    def record_sync_request(
        self,
        *,
        actor: AuthenticatedUser,
        ticker: str,
        issuer_id: str | None,
        exchange: str,
        event_type: str,
        effective_date: date,
        fy_end_month: int,
        previous_exchange: str | None,
        source_url: str,
        notes: str,
        client_ip: str = "",
    ) -> IssuerLifecycleEventRecord:
        clean_ticker = ticker.strip().upper()
        if not _TICKER_RE.fullmatch(clean_ticker):
            raise OpsQaError(422, "Mã chứng khoán chỉ gồm 1–10 ký tự chữ hoặc số.")
        clean_exchange = exchange.strip().upper()
        if clean_exchange not in _VALID_EXCHANGES:
            raise OpsQaError(422, f"Sàn giao dịch không hợp lệ: {exchange!r}")
        clean_type = event_type.strip().upper()
        if clean_type not in _VALID_EVENT_TYPES:
            raise OpsQaError(422, f"Loại sự kiện vòng đời không hợp lệ: {event_type!r}")
        if not (1 <= fy_end_month <= 12):
            raise OpsQaError(422, "Tháng kết thúc niên độ phải từ 1 đến 12.")

        rec = self._qa.insert_lifecycle_event(
            issuer_id=(
                issuer_id.strip() if issuer_id and issuer_id.strip() else clean_ticker
            ),
            ticker=clean_ticker,
            exchange=clean_exchange,
            event_type=clean_type,
            effective_date=effective_date,
            fy_end_month=fy_end_month,
            previous_exchange=(
                previous_exchange.strip().upper()
                if previous_exchange and previous_exchange.strip()
                else None
            ),
            source_url=source_url.strip(),
            notes=notes.strip(),
            status="QUEUED_FOR_SYNC",
            recorded_by_user_id=actor.user_id,
        )
        if self._audit is not None:
            record_audit_event(
                self._audit,
                actor_user_id=actor.user_id,
                actor_role=actor.role.value,
                action="OPS_ISSUER_SYNC_REQUESTED",
                resource_type="issuer_lifecycle",
                resource_id=clean_ticker,
                outcome="SUCCESS",
                client_ip=client_ip,
                metadata={
                    "event_type": clean_type,
                    "exchange": clean_exchange,
                    "fy_end_month": fy_end_month,
                },
            )
        return rec


__all__ = ["IssuerLifecycleEventRecord", "IssuerLifecycleView", "OpsLifecycleService"]
