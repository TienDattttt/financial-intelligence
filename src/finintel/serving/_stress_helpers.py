"""Extracted stress-test response assembly helpers for Layer 5 FastAPI serving.

Verified Sources:
- Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
  IMF Working Paper WP/15/216.
- Runtime API Verification (Python 3.11.9, fastapi==0.165.0, pydantic==2.14.4,
  polars==1.35.2).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl

from finintel.serving._api_helpers import _frame_to_records
from finintel.serving.schemas import StressIssuerItem, StressSectorSummaryItem


def build_empty_stress_response_kwargs(
    *,
    snapshot_id: str | None,
    scenario_name: str,
    statement_scope: str,
    period_type: str,
    period_end_date: date | None,
) -> dict[str, Any]:
    """Assemble ``StressTestResponse`` kwargs when no issuers match the slice."""
    return {
        "snapshot_id": snapshot_id,
        "scenario_name": scenario_name,
        "statement_scope": statement_scope,
        "period_type": period_type,
        "period_end_date": period_end_date,
        "total_issuers_evaluated": 0,
        "total_debt_sum": 0.0,
        "baseline_debt_at_risk_pct": 0.0,
        "stressed_debt_at_risk_pct": 0.0,
        "newly_distressed_count": 0,
        "sector_summary": [],
        "newly_distressed_issuers": [],
        "top_stressed_issuers": [],
    }


def build_stress_response_kwargs(
    *,
    snapshot_id: str | None,
    scenario_name: str,
    statement_scope: str,
    period_type: str,
    resolved_end: object,
    fallback_period_end_date: date | None,
    stressed_df: pl.DataFrame,
    sector_df: pl.DataFrame,
    market_df: pl.DataFrame,
    top_n: int,
    include_all_newly_distressed: bool | None,
) -> dict[str, Any]:
    """Assemble ``StressTestResponse`` kwargs from simulated Polars DataFrames."""
    market_row = market_df.row(0, named=True)
    issuer_cols = [
        "issuer_id",
        "ticker",
        "company_name_vn",
        "icb_code_level2",
        "icb_name_level2",
        "total_debt",
        "interest_coverage",
        "stressed_interest_coverage",
        "stressed_interest_coverage_flag",
        "ebit_ttm",
        "stressed_ebit_ttm",
        "borrowing_cost_ttm",
        "stressed_borrowing_cost_ttm",
        "cfo_ttm",
        "stressed_cfo_ttm",
        "baseline_debt_at_risk",
        "stressed_debt_at_risk",
        "newly_distressed_under_shock",
    ]
    newly_limit = 1000 if include_all_newly_distressed else top_n
    newly_df = (
        stressed_df.filter(
            pl.col("newly_distressed_under_shock").eq(True).fill_null(False)
        )
        .select(issuer_cols)
        .sort(
            ["total_debt", "ticker"],
            descending=[True, False],
            nulls_last=True,
        )
        .head(newly_limit)
    )
    top_df = (
        stressed_df.select(issuer_cols)
        .sort(
            ["stressed_debt_at_risk", "total_debt", "ticker"],
            descending=[True, True, False],
            nulls_last=True,
        )
        .head(top_n)
    )
    return {
        "snapshot_id": snapshot_id,
        "scenario_name": scenario_name,
        "statement_scope": statement_scope,
        "period_type": period_type,
        "period_end_date": (
            resolved_end if isinstance(resolved_end, date) else fallback_period_end_date
        ),
        "total_issuers_evaluated": int(market_row["issuer_count"]),
        "total_debt_sum": float(market_row["total_debt_sum"]),
        "baseline_debt_at_risk_pct": float(market_row["baseline_debt_at_risk_pct"]),
        "stressed_debt_at_risk_pct": float(market_row["stressed_debt_at_risk_pct"]),
        "newly_distressed_count": int(market_row["newly_distressed_count"]),
        "sector_summary": [
            StressSectorSummaryItem(**row) for row in _frame_to_records(sector_df)
        ],
        "newly_distressed_issuers": [
            StressIssuerItem(**row) for row in _frame_to_records(newly_df)
        ],
        "top_stressed_issuers": [
            StressIssuerItem(**row) for row in _frame_to_records(top_df)
        ],
    }
