"""V3 storage schemas for PIT Valuation Mart and V3 financial ratios.

Verified Sources:
- spec/v3/SPEC-v3-valuation-mart-and-screener.md (§5.1)
- Circular 96/2020/TT-BTC (PIT disclosure timing)
- Circular 200/2014/TT-BTC & Circular 99/2025/TT-BTC (BS_TOTAL_LIABILITIES)
"""

from __future__ import annotations

import polars as pl

PIT_VALUATION_MART_TABLE = "pit_valuation_mart.parquet"
OPTIONAL_MANAGED_MART_TABLES: frozenset[str] = frozenset(
    {
        "unmapped_metrics.parquet",
        "model_card_meta.parquet",
        PIT_VALUATION_MART_TABLE,
    }
)
V3_RATIOS = ("ocf_to_total_liabilities",)

PIT_VALUATION_MART_KEY = (
    "issuer_id",
    "statement_scope",
    "period_type",
    "period_end_date",
)
PIT_VALUATION_MART_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "ticker": pl.String,
        "statement_scope": pl.String,
        "period_type": pl.String,
        "period_end_date": pl.Date,
        "effective_trade_date": pl.Date,
        "close_raw_vnd": pl.Float64,
        "pit_shares_outstanding": pl.Float64,
        "market_cap_vnd": pl.Float64,
        "pe_ttm": pl.Float64,
        "pb_ratio": pl.Float64,
        "pe_flag": pl.String,
        "pb_flag": pl.String,
        "market_cap_flag": pl.String,
    }
)
