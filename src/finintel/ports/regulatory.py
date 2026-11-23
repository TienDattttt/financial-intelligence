"""Extension Port 1: Regulatory & Audit Distress Events contract (ARCH-09 / H-05)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol, runtime_checkable

import polars as pl

from finintel.storage.schemas import (
    REGULATORY_DELISTING_EXCHANGES,
    REGULATORY_EVENT_STAGES,
    REGULATORY_EVENT_TYPES,
    REGULATORY_EVENTS_KEY,
    REGULATORY_EVENTS_SCHEMA,
    REGULATORY_EVENTS_TABLE,
    canonicalize_table,
)


def validate_regulatory_events(frame: pl.DataFrame) -> pl.DataFrame:
    """Canonicalize and validate a Port 1 regulatory events table against ``REGULATORY_EVENTS_SCHEMA``."""
    return canonicalize_table(frame, REGULATORY_EVENTS_SCHEMA)


@runtime_checkable
class RegulatoryEventProvider(Protocol):
    """Port 1 provider contract for point-in-time regulatory and audit events."""

    def fetch_events(
        self,
        *,
        tickers: Sequence[str] | None = None,
        as_of: date | None = None,
    ) -> pl.DataFrame:
        """Return regulatory events conforming to ``REGULATORY_EVENTS_SCHEMA``."""
        ...


__all__ = [
    "REGULATORY_DELISTING_EXCHANGES",
    "REGULATORY_EVENTS_KEY",
    "REGULATORY_EVENTS_SCHEMA",
    "REGULATORY_EVENTS_TABLE",
    "REGULATORY_EVENT_STAGES",
    "REGULATORY_EVENT_TYPES",
    "RegulatoryEventProvider",
    "validate_regulatory_events",
]
