"""Extension Port 3: Market EOD Prices contract & PIT forward alignment (ARCH-09 / B-06)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Literal, Protocol, runtime_checkable

import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.storage.schemas import (
    MARKET_PRICES_KEY,
    MARKET_PRICES_SCHEMA,
    MARKET_PRICES_TABLE,
    canonicalize_table,
)

PIT_PRICE_JOIN_STRATEGY: Literal["forward"] = "forward"


def validate_market_prices(frame: pl.DataFrame) -> pl.DataFrame:
    """Canonicalize and validate a Port 3 EOD prices table against ``MARKET_PRICES_SCHEMA``."""
    return canonicalize_table(frame, MARKET_PRICES_SCHEMA)


@runtime_checkable
class MarketPriceProvider(Protocol):
    """Port 3 provider contract for dual raw/adjusted EOD market prices."""

    def fetch_prices(
        self,
        *,
        tickers: Sequence[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pl.DataFrame:
        """Return EOD price bars conforming to ``MARKET_PRICES_SCHEMA``."""
        ...


def align_events_to_market_prices_pit(
    events: pl.DataFrame,
    prices: pl.DataFrame,
    *,
    event_date_col: str = "published_at",
    price_date_col: str = "trade_date",
    tolerance: str | timedelta | None = None,
) -> pl.DataFrame:
    """Point-in-time forward-align filing/event dates onto trading sessions (B-06).

    Uses ``join_asof(..., strategy="forward")`` grouped by ``issuer_id`` so weekend
    or holiday disclosures map strictly to the first subsequent trading session
    without look-ahead onto prior sessions.
    """
    if "issuer_id" not in events.columns or event_date_col not in events.columns:
        raise ContractViolationError(
            f"Events table must contain 'issuer_id' and {event_date_col!r} for PIT price alignment."
        )
    if price_date_col != "trade_date":
        raise ContractViolationError(
            "Market price alignment requires price_date_col='trade_date'."
        )
    if events.schema.get(event_date_col) != pl.Date:
        raise ContractViolationError(
            f"Column {event_date_col!r} must have Polars Date dtype for PIT alignment."
        )
    validated_prices = validate_market_prices(prices)
    if events.filter(pl.col(event_date_col).is_null()).height > 0:
        raise ContractViolationError(
            f"Events table contains null {event_date_col!r} values."
        )
    sorted_events = events.sort(["issuer_id", event_date_col])
    sorted_prices = validated_prices.sort(["issuer_id", price_date_col])
    return sorted_events.join_asof(
        sorted_prices,
        left_on=event_date_col,
        right_on=price_date_col,
        by="issuer_id",
        strategy=PIT_PRICE_JOIN_STRATEGY,
        tolerance=tolerance,
        check_sortedness=False,
    )


__all__ = [
    "MARKET_PRICES_KEY",
    "MARKET_PRICES_SCHEMA",
    "MARKET_PRICES_TABLE",
    "MarketPriceProvider",
    "PIT_PRICE_JOIN_STRATEGY",
    "align_events_to_market_prices_pit",
    "validate_market_prices",
]
