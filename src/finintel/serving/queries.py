"""Headless read-only DuckDB query layer for Layer 5 FastAPI & Streamlit serving.

Verified Sources:
- DuckDB Python API & Multithreading (``duckdb==1.4.2``, non-owning ``conn.cursor()``,
  ``:memory:`` + ``ATTACH ... (READ_ONLY)``):
  https://duckdb.org/docs/stable/guides/python/multiple_threads
  https://duckdb.org/docs/stable/clients/python/overview
- DuckLake Official Documentation (time travel ``AT (VERSION => ...)``):
  https://ducklake.select/docs/stable/
- Rouen, E., So, E. C., & Wang, C. C. Y. (2021). "Core Earnings: New Data and Evidence."
  Journal of Financial Economics, Vol. 142, Issue 3, pp. 1068-1091.
- Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
  IMF Working Paper WP/15/216 (https://www.elibrary.imf.org).
- Runtime API Verification (Python 3.11.9, duckdb==1.4.2, polars==1.35.2):
  Verified ``ParquetRepository.snapshot_session(connection=...)`` pins a single
  immutable snapshot or DuckLake version without closing caller-owned cursors, and
  DuckDB SQL ``COUNT(*) FILTER (WHERE ...)`` + ``LIMIT/OFFSET`` execute cleanly over
  both ``read_parquet(?)`` and DuckLake ``AT (VERSION => N)``.

Architectural invariants:
  - Reads EXCLUSIVELY from published immutable snapshots via ``ParquetRepository``.
  - Contains ZERO financial or accounting calculations; all values, ratios,
    DuPont factors, benchmarks, red flags, ML scores, and quality statuses are read
    as pre-computed columns from Layer 2/4/6 outputs.
  - Strictly headless (zero Streamlit or HTTP framework imports).
"""

from __future__ import annotations

import json
import math
import os
import threading
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

import duckdb
import polars as pl

from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.market_valuation import (
    build_price_risk_overlay,
    compute_pit_valuation_series,
)
from finintel.analytics.stress_test import (
    REQUIRED_STRESS_COLUMNS,
    StressScenario as StressScenario,
    simulate_financial_stress as simulate_financial_stress,
    summarize_stress_by_sector as summarize_stress_by_sector,
)
from finintel.exceptions import ContractViolationError
from finintel.serving.dictionary import (
    OVERVIEW_CORE_RATIOS,
    RATIO_DICTIONARY_META,
    load_statement_metric_catalog,
)
from finintel.serving.observability import SPAN_DUCKDB_QUERY, trace_span
from finintel.storage.ducklake_store import ensure_ducklake_attached
from finintel.storage.repository import ParquetRepository, _normalize_table_name
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_SCHEMA,
    AUDIT_QUARANTINE_TABLE,
    BENCHMARKS_TABLE,
    CORE_RATIOS,
    EXTRA_RATIOS,
    FILINGS_MANIFEST_TABLE,
    FINANCIAL_RATIOS_SCHEMA,
    FORENSIC_RATIOS,
    ISSUERS_SCHEMA,
    ML_SCORES_SCHEMA,
    ML_SCORES_TABLE,
    MODEL_CARD_META_TABLE,
    QUALITY_SUMMARY_SCHEMA,
    QUALITY_SUMMARY_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[3] / "data"
DenominatorMode = Literal[
    "AVERAGE_STOCK", "POINT_IN_TIME_STOCK", "FLOW", "PRIOR_YEAR_FLOW", "COMPOSITE"
]
PeriodView = Literal["TTM", "Quarterly", "Annual"]
StatementScope = Literal["CONSOLIDATED", "SEPARATE"]
PeriodType = Literal["QUARTER", "ANNUAL"]
AltmanZone = Literal["SAFE_ZONE", "GREY_ZONE", "DISTRESS_ZONE"]
RiskTier = Literal["R1", "R2", "R3", "R4", "R5"]
BeneishFlag = Literal[
    "VALID",
    "HIGH_MANIPULATION_RISK_SCREEN",
    "MIXED_TAXONOMY",
    "MISSING_PRIOR_YEAR_BASE",
    "MISSING_DEPRECIATION_DIRECT_CF",
    "MISSING_OR_INVALID_INPUT",
]
PeerGroupLevel = Literal["ICB_LEVEL_3", "ICB_LEVEL_2", "REGIME_FALLBACK"]

STATEMENT_SCOPES: tuple[str, ...] = ("CONSOLIDATED", "SEPARATE")
PERIOD_TYPES: tuple[str, ...] = ("QUARTER", "ANNUAL")
PERIOD_VIEWS: tuple[str, ...] = ("TTM", "Quarterly", "Annual")
ALTMAN_ZONES: tuple[str, ...] = ("SAFE_ZONE", "GREY_ZONE", "DISTRESS_ZONE")
RISK_TIERS_ORDERED: tuple[str, ...] = ("R1", "R2", "R3", "R4", "R5")
BENEISH_FLAGS: tuple[str, ...] = (
    "VALID",
    "HIGH_MANIPULATION_RISK_SCREEN",
    "MIXED_TAXONOMY",
    "MISSING_PRIOR_YEAR_BASE",
    "MISSING_DEPRECIATION_DIRECT_CF",
    "MISSING_OR_INVALID_INPUT",
)
PEER_GROUP_LEVELS: tuple[str, ...] = (
    "ICB_LEVEL_3",
    "ICB_LEVEL_2",
    "REGIME_FALLBACK",
)
STATEMENT_TYPES: tuple[str, ...] = (
    "BALANCE_SHEET",
    "INCOME_STATEMENT",
    "CASH_FLOW",
)
SCREENER_SORT_FIELDS: tuple[str, ...] = (
    "ticker",
    "calibrated_distress_prob",
    "altman_z_double_prime",
    "piotroski_f_score",
    "beneish_m8_score",
    "red_flag_count",
    "roe_total",
    "debt_to_equity",
)


class SectorOptionDict(TypedDict):
    icb_code_level2: str
    icb_name_level2: str
    issuer_count: int


class SectorRiskDict(TypedDict):
    icb_code_level2: str
    icb_name_level2: str
    issuer_count: int
    safe_zone_count: int
    grey_zone_count: int
    distress_zone_count: int
    beneish_flagged_count: int
    high_red_flag_count: int
    median_distress_prob: float | None
    avg_red_flags: float
    total_debt_sum: float


class SectorRiskSummaryDict(TypedDict):
    period_end_date: date | None
    total_issuers: int
    safe_zone_count: int
    grey_zone_count: int
    distress_zone_count: int
    beneish_flagged_count: int
    high_red_flag_count: int
    sectors: list[SectorRiskDict]
    top_deteriorating: list[dict[str, object]]


_ALTMAN_SEVERITY_RANK: dict[str, int] = {
    "SAFE_ZONE": 0,
    "GREY_ZONE": 1,
    "DISTRESS_ZONE": 2,
}
_RISK_TIER_SEVERITY_RANK: dict[str, int] = {
    "R1": 0,
    "R2": 1,
    "R3": 2,
    "R4": 3,
    "R5": 4,
}

PEER_BAND_HISTORY_SCHEMA = pl.Schema(
    {
        "period_end_date": pl.Date,
        "value": pl.Float64,
        "peer_p25": pl.Float64,
        "peer_median": pl.Float64,
        "peer_p75": pl.Float64,
        "percentile_rank": pl.Float64,
        "peer_group_level": pl.String,
        "icb_code_used": pl.String,
        "peer_count": pl.UInt32,
    }
)

SCREENER_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "ticker": pl.String,
        "company_name_vn": pl.String,
        "exchange": pl.String,
        "accounting_regime": pl.String,
        "icb_code_level2": pl.String,
        "icb_name_level2": pl.String,
        "icb_code_level3": pl.String,
        "icb_name_level3": pl.String,
        "statement_scope": pl.String,
        "period_type": pl.String,
        "period_end_date": pl.Date,
        "taxonomy_version": pl.String,
        "roe_total": pl.Float64,
        "roa": pl.Float64,
        "ebit_margin": pl.Float64,
        "debt_to_equity": pl.Float64,
        "interest_coverage": pl.Float64,
        "accruals_to_assets": pl.Float64,
        "core_operating_margin": pl.Float64,
        "non_core_distortion_to_assets": pl.Float64,
        "core_to_ebt_ratio": pl.Float64,
        "red_flag_count": pl.Int64,
        "critical_red_flag_count": pl.Int64,
        "altman_z_double_prime": pl.Float64,
        "altman_em_score": pl.Float64,
        "altman_zone": pl.String,
        "piotroski_f_score": pl.Int8,
        "beneish_m8_score": pl.Float64,
        "beneish_flag": pl.String,
        "cluster_id": pl.Int32,
        "cluster_archetype_name": pl.String,
        "anomaly_flag": pl.String,
        "distress_risk_score": pl.Float64,
        "calibrated_distress_prob": pl.Float64,
        "risk_tier": pl.String,
        "risk_market_percentile": pl.Float64,
        "market_base_rate": pl.Float64,
        "relative_risk_lift": pl.Float64,
        "model_role": pl.String,
        "model_version": pl.String,
    }
)

_EMPTY_RED_FLAGS_SCHEMA = pl.Schema(
    {
        "period_end_date": pl.Date,
        "flag_code": pl.String,
        "severity": pl.String,
        "flag_title_vn": pl.String,
        "explanation_text_vn": pl.String,
        "evidence_json": pl.String,
        "filing_id": pl.String,
    }
)

_EMPTY_RATIO_PERIODS_SCHEMA = pl.Schema(
    {
        "statement_scope": pl.String,
        "period_type": pl.String,
        "fiscal_year": pl.Int32,
        "fiscal_quarter": pl.Int32,
        "period_end_date": pl.Date,
        "taxonomy_version": pl.String,
        "filing_id": pl.String,
    }
)

_V2_PROFILE_OPTIONAL_RATIO_COLS: dict[str, pl.DataType] = {
    "core_operating_profit_ttm": pl.Float64(),
    "core_operating_profit_flag": pl.String(),
    "core_operating_margin": pl.Float64(),
    "core_operating_margin_flag": pl.String(),
    "non_core_distortion_to_assets": pl.Float64(),
    "non_core_distortion_to_assets_flag": pl.String(),
    "core_to_ebt_ratio": pl.Float64(),
    "core_to_ebt_ratio_flag": pl.String(),
}


@dataclass(frozen=True)
class RatioLineageSpec:
    """Declarative mathematical specification and metric lineage for a financial ratio."""

    ratio_name: str
    display_name_vn: str
    formula: str
    numerator_metrics: tuple[str, ...]
    denominator_metrics: tuple[str, ...]
    denominator_mode: DenominatorMode


RATIO_LINEAGE_CATALOG: dict[str, RatioLineageSpec] = {
    "gross_margin": RatioLineageSpec(
        "gross_margin",
        "Biên lợi nhuận gộp",
        "IS_GROSS_PROFIT / IS_NET_REVENUE",
        ("IS_GROSS_PROFIT",),
        ("IS_NET_REVENUE",),
        "FLOW",
    ),
    "ebit_margin": RatioLineageSpec(
        "ebit_margin",
        "Biên EBIT",
        "(IS_PROFIT_BEFORE_TAX + |Chi phí lãi vay/đi vay|) / IS_NET_REVENUE",
        ("IS_PROFIT_BEFORE_TAX", "IS_INTEREST_EXPENSE", "IS_BORROWING_COSTS"),
        ("IS_NET_REVENUE",),
        "FLOW",
    ),
    "ebitda_margin": RatioLineageSpec(
        "ebitda_margin",
        "Biên EBITDA",
        "(EBIT + |CF_DEPRECIATION_AMORTIZATION|) / IS_NET_REVENUE",
        (
            "IS_PROFIT_BEFORE_TAX",
            "IS_INTEREST_EXPENSE",
            "IS_BORROWING_COSTS",
            "CF_DEPRECIATION_AMORTIZATION",
        ),
        ("IS_NET_REVENUE",),
        "FLOW",
    ),
    "operating_margin": RatioLineageSpec(
        "operating_margin",
        "Biên lợi nhuận hoạt động",
        "IS_OPERATING_PROFIT / IS_NET_REVENUE",
        ("IS_OPERATING_PROFIT",),
        ("IS_NET_REVENUE",),
        "FLOW",
    ),
    "net_profit_margin": RatioLineageSpec(
        "net_profit_margin",
        "Biên lợi nhuận ròng",
        "IS_NET_INCOME / IS_NET_REVENUE",
        ("IS_NET_INCOME",),
        ("IS_NET_REVENUE",),
        "FLOW",
    ),
    "roa": RatioLineageSpec(
        "roa",
        "ROA (Lợi nhuận / Tổng tài sản bình quân)",
        "IS_NET_INCOME / Avg(BS_TOTAL_ASSETS_t, BS_TOTAL_ASSETS_{t-4})",
        ("IS_NET_INCOME",),
        ("BS_TOTAL_ASSETS",),
        "AVERAGE_STOCK",
    ),
    "roe_total": RatioLineageSpec(
        "roe_total",
        "ROE Tổng (Vốn chủ sở hữu bình quân)",
        "IS_NET_INCOME / Avg(BS_TOTAL_EQUITY_t, BS_TOTAL_EQUITY_{t-4})",
        ("IS_NET_INCOME",),
        ("BS_TOTAL_EQUITY",),
        "AVERAGE_STOCK",
    ),
    "roe_parent": RatioLineageSpec(
        "roe_parent",
        "ROE Cổ đông công ty mẹ",
        "IS_NET_INCOME_PARENT / Avg(ParentEquity_t, ParentEquity_{t-4})",
        ("IS_NET_INCOME_PARENT",),
        ("BS_TOTAL_EQUITY", "BS_MINORITY_INTEREST"),
        "AVERAGE_STOCK",
    ),
    "roic": RatioLineageSpec(
        "roic",
        "ROIC (Lợi nhuận trên vốn đầu tư)",
        "NOPAT_TTM / Avg(InvestedCapital_t, InvestedCapital_{t-4})",
        (
            "IS_PROFIT_BEFORE_TAX",
            "IS_INTEREST_EXPENSE",
            "IS_BORROWING_COSTS",
            "IS_CURRENT_INCOME_TAX",
            "IS_DEFERRED_INCOME_TAX",
        ),
        (
            "BS_SHORT_TERM_BORROWINGS",
            "BS_LONG_TERM_BORROWINGS",
            "BS_CONVERTIBLE_BONDS",
            "BS_TOTAL_EQUITY",
            "BS_CASH_AND_EQUIVALENTS",
        ),
        "AVERAGE_STOCK",
    ),
    "current_ratio": RatioLineageSpec(
        "current_ratio",
        "Hệ số thanh toán hiện hành",
        "BS_CURRENT_ASSETS / BS_CURRENT_LIABILITIES",
        ("BS_CURRENT_ASSETS",),
        ("BS_CURRENT_LIABILITIES",),
        "POINT_IN_TIME_STOCK",
    ),
    "quick_ratio": RatioLineageSpec(
        "quick_ratio",
        "Hệ số thanh toán nhanh",
        "(BS_CASH_AND_EQUIVALENTS + BS_SHORT_TERM_INVESTMENTS + BS_TRADE_RECEIVABLES_ST) / BS_CURRENT_LIABILITIES",
        (
            "BS_CASH_AND_EQUIVALENTS",
            "BS_SHORT_TERM_INVESTMENTS",
            "BS_TRADE_RECEIVABLES_ST",
        ),
        ("BS_CURRENT_LIABILITIES",),
        "POINT_IN_TIME_STOCK",
    ),
    "cash_ratio": RatioLineageSpec(
        "cash_ratio",
        "Hệ số thanh toán tiền mặt",
        "(BS_CASH_AND_EQUIVALENTS + BS_SHORT_TERM_INVESTMENTS) / BS_CURRENT_LIABILITIES",
        ("BS_CASH_AND_EQUIVALENTS", "BS_SHORT_TERM_INVESTMENTS"),
        ("BS_CURRENT_LIABILITIES",),
        "POINT_IN_TIME_STOCK",
    ),
    "ocf_to_current_liabilities": RatioLineageSpec(
        "ocf_to_current_liabilities",
        "CFO / Nợ ngắn hạn bình quân",
        "CF_OPERATING_CASH_FLOW / Avg(BS_CURRENT_LIABILITIES_t, BS_CURRENT_LIABILITIES_{t-4})",
        ("CF_OPERATING_CASH_FLOW",),
        ("BS_CURRENT_LIABILITIES",),
        "AVERAGE_STOCK",
    ),
    "liabilities_to_assets": RatioLineageSpec(
        "liabilities_to_assets",
        "Nợ phải trả / Tổng tài sản",
        "BS_TOTAL_LIABILITIES / BS_TOTAL_ASSETS",
        ("BS_TOTAL_LIABILITIES",),
        ("BS_TOTAL_ASSETS",),
        "POINT_IN_TIME_STOCK",
    ),
    "financial_debt_to_assets": RatioLineageSpec(
        "financial_debt_to_assets",
        "Nợ vay tài chính / Tổng tài sản",
        "TotalDebt / BS_TOTAL_ASSETS",
        (
            "BS_SHORT_TERM_BORROWINGS",
            "BS_LONG_TERM_BORROWINGS",
            "BS_CONVERTIBLE_BONDS",
        ),
        ("BS_TOTAL_ASSETS",),
        "POINT_IN_TIME_STOCK",
    ),
    "debt_to_equity": RatioLineageSpec(
        "debt_to_equity",
        "Nợ vay / Vốn chủ sở hữu",
        "TotalDebt / BS_TOTAL_EQUITY",
        (
            "BS_SHORT_TERM_BORROWINGS",
            "BS_LONG_TERM_BORROWINGS",
            "BS_CONVERTIBLE_BONDS",
        ),
        ("BS_TOTAL_EQUITY",),
        "POINT_IN_TIME_STOCK",
    ),
    "net_debt_to_equity": RatioLineageSpec(
        "net_debt_to_equity",
        "Nợ vay ròng / Vốn chủ sở hữu",
        "(TotalDebt - CashAndSTInvestments) / BS_TOTAL_EQUITY",
        (
            "BS_SHORT_TERM_BORROWINGS",
            "BS_LONG_TERM_BORROWINGS",
            "BS_CONVERTIBLE_BONDS",
            "BS_CASH_AND_EQUIVALENTS",
            "BS_SHORT_TERM_INVESTMENTS",
        ),
        ("BS_TOTAL_EQUITY",),
        "POINT_IN_TIME_STOCK",
    ),
    "net_debt_to_ebitda": RatioLineageSpec(
        "net_debt_to_ebitda",
        "Nợ vay ròng / EBITDA",
        "NetDebt / EBITDA_TTM",
        (
            "BS_SHORT_TERM_BORROWINGS",
            "BS_LONG_TERM_BORROWINGS",
            "BS_CONVERTIBLE_BONDS",
            "BS_CASH_AND_EQUIVALENTS",
            "BS_SHORT_TERM_INVESTMENTS",
        ),
        (
            "IS_PROFIT_BEFORE_TAX",
            "IS_INTEREST_EXPENSE",
            "IS_BORROWING_COSTS",
            "CF_DEPRECIATION_AMORTIZATION",
        ),
        "FLOW",
    ),
    "interest_coverage": RatioLineageSpec(
        "interest_coverage",
        "Hệ số khả năng trả lãi vay",
        "EBIT_TTM / |Chi phí lãi vay/đi vay|",
        ("IS_PROFIT_BEFORE_TAX", "IS_INTEREST_EXPENSE", "IS_BORROWING_COSTS"),
        ("IS_INTEREST_EXPENSE", "IS_BORROWING_COSTS"),
        "FLOW",
    ),
    "asset_turnover": RatioLineageSpec(
        "asset_turnover",
        "Vòng quay tổng tài sản",
        "IS_NET_REVENUE / Avg(BS_TOTAL_ASSETS_t, BS_TOTAL_ASSETS_{t-4})",
        ("IS_NET_REVENUE",),
        ("BS_TOTAL_ASSETS",),
        "AVERAGE_STOCK",
    ),
    "inventory_turnover": RatioLineageSpec(
        "inventory_turnover",
        "Vòng quay hàng tồn kho",
        "|IS_COST_OF_GOODS_SOLD| / Avg(BS_INVENTORIES_t, BS_INVENTORIES_{t-4})",
        ("IS_COST_OF_GOODS_SOLD",),
        ("BS_INVENTORIES",),
        "AVERAGE_STOCK",
    ),
    "receivables_turnover": RatioLineageSpec(
        "receivables_turnover",
        "Vòng quay phải thu khách hàng",
        "IS_NET_REVENUE / Avg(BS_TRADE_RECEIVABLES_ST_t, BS_TRADE_RECEIVABLES_ST_{t-4})",
        ("IS_NET_REVENUE",),
        ("BS_TRADE_RECEIVABLES_ST",),
        "AVERAGE_STOCK",
    ),
    "payables_turnover": RatioLineageSpec(
        "payables_turnover",
        "Vòng quay phải trả người bán",
        "|IS_COST_OF_GOODS_SOLD| / Avg(BS_TRADE_PAYABLES_ST_t, BS_TRADE_PAYABLES_ST_{t-4})",
        ("IS_COST_OF_GOODS_SOLD",),
        ("BS_TRADE_PAYABLES_ST",),
        "AVERAGE_STOCK",
    ),
    "cash_conversion_cycle": RatioLineageSpec(
        "cash_conversion_cycle",
        "Chu kỳ chuyển đổi tiền mặt (CCC)",
        "DIO + DSO - DPO",
        ("IS_COST_OF_GOODS_SOLD", "IS_NET_REVENUE"),
        ("BS_INVENTORIES", "BS_TRADE_RECEIVABLES_ST", "BS_TRADE_PAYABLES_ST"),
        "COMPOSITE",
    ),
    "ocf_to_net_income": RatioLineageSpec(
        "ocf_to_net_income",
        "Chất lượng lợi nhuận (CFO / LNST)",
        "CF_OPERATING_CASH_FLOW / IS_NET_INCOME",
        ("CF_OPERATING_CASH_FLOW",),
        ("IS_NET_INCOME",),
        "FLOW",
    ),
    "fcf_to_revenue": RatioLineageSpec(
        "fcf_to_revenue",
        "Biên dòng tiền tự do (FCF / Doanh thu)",
        "(CF_OPERATING_CASH_FLOW - |CF_CAPITAL_EXPENDITURE|) / IS_NET_REVENUE",
        ("CF_OPERATING_CASH_FLOW", "CF_CAPITAL_EXPENDITURE"),
        ("IS_NET_REVENUE",),
        "FLOW",
    ),
    "accruals_to_assets": RatioLineageSpec(
        "accruals_to_assets",
        "Tỷ lệ dồn tích trên tổng tài sản",
        "(IS_NET_INCOME - CF_OPERATING_CASH_FLOW) / Avg(BS_TOTAL_ASSETS_t, BS_TOTAL_ASSETS_{t-4})",
        ("IS_NET_INCOME", "CF_OPERATING_CASH_FLOW"),
        ("BS_TOTAL_ASSETS",),
        "AVERAGE_STOCK",
    ),
    "eps_ttm_estimated": RatioLineageSpec(
        "eps_ttm_estimated",
        "EPS ước tính (TTM / Năm)",
        "IS_NET_INCOME_PARENT / EstimatedShares",
        ("IS_NET_INCOME_PARENT",),
        ("BS_ORDINARY_SHARES_CAPITAL", "BS_TREASURY_SHARES", "IS_EPS_BASIC_REPORTED"),
        "POINT_IN_TIME_STOCK",
    ),
    "equity_to_assets": RatioLineageSpec(
        "equity_to_assets",
        "Vốn chủ sở hữu / Tổng tài sản",
        "BS_TOTAL_EQUITY / BS_TOTAL_ASSETS",
        ("BS_TOTAL_EQUITY",),
        ("BS_TOTAL_ASSETS",),
        "POINT_IN_TIME_STOCK",
    ),
    "dio": RatioLineageSpec(
        "dio",
        "Số ngày tồn kho (DIO)",
        "365 / inventory_turnover",
        ("IS_COST_OF_GOODS_SOLD",),
        ("BS_INVENTORIES",),
        "AVERAGE_STOCK",
    ),
    "dso": RatioLineageSpec(
        "dso",
        "Số ngày phải thu (DSO)",
        "365 / receivables_turnover",
        ("IS_NET_REVENUE",),
        ("BS_TRADE_RECEIVABLES_ST",),
        "AVERAGE_STOCK",
    ),
    "dpo": RatioLineageSpec(
        "dpo",
        "Số ngày phải trả (DPO)",
        "365 / payables_turnover",
        ("IS_COST_OF_GOODS_SOLD",),
        ("BS_TRADE_PAYABLES_ST",),
        "AVERAGE_STOCK",
    ),
    "revenue_growth_yoy": RatioLineageSpec(
        "revenue_growth_yoy",
        "Tăng trưởng doanh thu YoY",
        "(IS_NET_REVENUE_t - IS_NET_REVENUE_{t-4}) / IS_NET_REVENUE_{t-4}",
        ("IS_NET_REVENUE",),
        ("IS_NET_REVENUE",),
        "PRIOR_YEAR_FLOW",
    ),
    "ebit_growth_yoy": RatioLineageSpec(
        "ebit_growth_yoy",
        "Tăng trưởng EBIT YoY",
        "(EBIT_t - EBIT_{t-4}) / EBIT_{t-4}",
        ("IS_PROFIT_BEFORE_TAX", "IS_INTEREST_EXPENSE", "IS_BORROWING_COSTS"),
        ("IS_PROFIT_BEFORE_TAX", "IS_INTEREST_EXPENSE", "IS_BORROWING_COSTS"),
        "PRIOR_YEAR_FLOW",
    ),
    "net_income_parent_growth_yoy": RatioLineageSpec(
        "net_income_parent_growth_yoy",
        "Tăng trưởng LNST công ty mẹ YoY",
        "(IS_NET_INCOME_PARENT_t - IS_NET_INCOME_PARENT_{t-4}) / IS_NET_INCOME_PARENT_{t-4}",
        ("IS_NET_INCOME_PARENT",),
        ("IS_NET_INCOME_PARENT",),
        "PRIOR_YEAR_FLOW",
    ),
    "core_operating_margin": RatioLineageSpec(
        "core_operating_margin",
        "Biên lợi nhuận kinh doanh lõi (Core Operating Margin)",
        "(IS_GROSS_PROFIT - IS_SELLING_EXPENSES - IS_ADMINISTRATIVE_EXPENSES) / IS_NET_REVENUE",
        ("IS_GROSS_PROFIT", "IS_SELLING_EXPENSES", "IS_ADMINISTRATIVE_EXPENSES"),
        ("IS_NET_REVENUE",),
        "FLOW",
    ),
    "non_core_distortion_to_assets": RatioLineageSpec(
        "non_core_distortion_to_assets",
        "Độ lệch lợi nhuận phi cốt lõi / Tổng tài sản bình quân",
        "(IS_PROFIT_BEFORE_TAX - (CoreOperatingProfit_TTM - |Chi phí lãi vay/đi vay|)) / Avg(BS_TOTAL_ASSETS_t, BS_TOTAL_ASSETS_{t-4})",
        (
            "IS_PROFIT_BEFORE_TAX",
            "IS_GROSS_PROFIT",
            "IS_SELLING_EXPENSES",
            "IS_ADMINISTRATIVE_EXPENSES",
            "IS_INTEREST_EXPENSE",
            "IS_BORROWING_COSTS",
        ),
        ("BS_TOTAL_ASSETS",),
        "AVERAGE_STOCK",
    ),
    "core_to_ebt_ratio": RatioLineageSpec(
        "core_to_ebt_ratio",
        "Tỷ trọng lợi nhuận lõi sau lãi vay / EBT",
        "(CoreOperatingProfit_TTM - |Chi phí lãi vay/đi vay|) / IS_PROFIT_BEFORE_TAX",
        (
            "IS_GROSS_PROFIT",
            "IS_SELLING_EXPENSES",
            "IS_ADMINISTRATIVE_EXPENSES",
            "IS_INTEREST_EXPENSE",
            "IS_BORROWING_COSTS",
        ),
        ("IS_PROFIT_BEFORE_TAX",),
        "FLOW",
    ),
}

ALL_RATIOS: tuple[str, ...] = (*CORE_RATIOS, *EXTRA_RATIOS, *FORENSIC_RATIOS)


@dataclass(frozen=True)
class SnapshotOverview:
    """High-level summary counts and provenance metadata for the active snapshot."""

    snapshot_id: str | None
    published_at: str | None
    total_issuers: int
    total_ratio_periods: int
    pass_periods: int
    warning_periods: int
    quarantined_rows: int
    storage_backend: str = "parquet"


@dataclass(frozen=True)
class QualityTablesPage:
    """Paginated Layer 6 quality summary and quarantine slices with exact totals."""

    quality_summary: pl.DataFrame
    audit_quarantine: pl.DataFrame
    total_summary: int
    total_quarantine: int


@dataclass(frozen=True)
class CompanyProfileData:
    """Pre-computed financial health profile, DuPont decomposition, red flags, and ML scores."""

    ticker: str
    issuer_id: str
    company_name_vn: str | None
    accounting_regime: str
    statement_scope: str
    period_view: PeriodView
    latest_taxonomy_version: str | None
    latest_quality_status: str | None
    latest_warning_codes: tuple[str, ...]
    growth_series: pl.DataFrame
    capital_structure_series: pl.DataFrame
    dupont_series: pl.DataFrame
    red_flags: pl.DataFrame
    ratios_table: pl.DataFrame
    ml_scores: pl.DataFrame = field(
        default_factory=lambda: pl.DataFrame(schema=ML_SCORES_SCHEMA)
    )


@dataclass(frozen=True)
class IndustryComparisonData:
    """Single-period cross-sectional ICB peer benchmark and peer cohort ranking."""

    ticker: str
    issuer_id: str
    statement_scope: str
    period_type: str
    period_end_date: date
    metric_name: str
    peer_group_level: str | None
    icb_code_used: str | None
    peer_count: int
    company_value: float | None
    peer_median: float | None
    peer_p25: float | None
    peer_p75: float | None
    peer_iqr: float | None
    percentile_rank: float | None
    robust_z_score: float | None
    industry_snapshot_date: date | None
    peer_ranking: pl.DataFrame


@dataclass(frozen=True)
class LineageComponent:
    """Provenance record for a single numerator or denominator input filing fact."""

    role: Literal["NUMERATOR", "DENOMINATOR"]
    metric_id: str
    period_type: str
    fiscal_year: int
    fiscal_quarter: int | None
    period_end_date: date
    value: float | None
    filing_id: str
    taxonomy_version: str
    derivation_method: str
    source_provider: str | None
    connector_name: str | None
    published_at: date | None
    published_at_basis: str | None
    effective_available_at: date | None
    canonical_content_sha256: str | None


@dataclass(frozen=True)
class RatioLineageCard:
    """Full audit lineage tracing one published ratio to its underlying source filings."""

    ticker: str
    issuer_id: str
    statement_scope: str
    period_type: str
    fiscal_year: int
    fiscal_quarter: int | None
    period_end_date: date
    ratio_name: str
    display_name_vn: str
    formula: str
    ratio_value: float | None
    status_flag: str | None
    taxonomy_version: str
    denominator_mode: DenominatorMode
    anchor_filing_id: str
    source_filing_ids: tuple[str, ...]
    numerator_components: tuple[LineageComponent, ...]
    denominator_components: tuple[LineageComponent, ...]
    orphan_filing_ids: tuple[str, ...]


def resolve_data_root(explicit: Path | None = None) -> Path:
    """Resolve the active data root from an explicit path, env var, or default."""
    if explicit is not None:
        return explicit.resolve()
    env_root = os.getenv("FININTEL_DATA_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return DEFAULT_DATA_ROOT


def percent_to_decimal(pct: float) -> float:
    """Convert a UI percentage / percentage-point control value to a decimal ratio."""
    if isinstance(pct, bool) or not isinstance(pct, (int, float)):
        raise ValueError("pct must be a finite numeric value.")
    value = float(pct)
    if not math.isfinite(value):
        raise ValueError("pct must be finite.")
    return value / 100.0


def pagination_offset(page_num: int, page_size: int) -> int:
    """Compute a 0-based offset from a 1-based page number and page size."""
    if isinstance(page_num, bool) or not isinstance(page_num, int) or page_num < 1:
        raise ValueError("page_num must be an integer >= 1.")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
        raise ValueError("page_size must be an integer >= 1.")
    return (page_num - 1) * page_size


def pagination_total_pages(total: int, page_size: int) -> int:
    """Return total number of 1-based pages (at least 1) for a paginated table."""
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("total must be a non-negative integer.")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
        raise ValueError("page_size must be an integer >= 1.")
    return max(1, (total + page_size - 1) // page_size)


def pagination_window(offset: int, limit: int, total: int) -> tuple[int, int, int]:
    """Return ``(current_page, start_row, end_row)`` for 1-based pagination display."""
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer.")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be an integer >= 1.")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("total must be a non-negative integer.")
    current_page = (offset // limit) + 1
    if total == 0 or offset >= total:
        return current_page, 0, 0
    return current_page, offset + 1, min(offset + limit, total)


_QUERY_CACHE_LOCK = threading.Lock()
_MAX_QUERY_CACHE_SIZE = 256
_DEFAULT_READ_TABLE = ParquetRepository.read_table
_SNAPSHOT_OVERVIEW_CACHE: OrderedDict[tuple[object, ...], SnapshotOverview] = (
    OrderedDict()
)
_LATEST_RATIO_PERIOD_CACHE: OrderedDict[tuple[object, ...], date] = OrderedDict()
_STRESS_INPUTS_CACHE: OrderedDict[tuple[object, ...], pl.DataFrame] = OrderedDict()
_COMPANY_PROFILE_CACHE: OrderedDict[tuple[object, ...], CompanyProfileData] = (
    OrderedDict()
)
_INDUSTRY_COMPARISON_CACHE: OrderedDict[tuple[object, ...], IndustryComparisonData] = (
    OrderedDict()
)
_PEER_BAND_HISTORY_CACHE: OrderedDict[tuple[object, ...], pl.DataFrame] = OrderedDict()
_RATIO_PERIODS_CACHE: OrderedDict[tuple[object, ...], pl.DataFrame] = OrderedDict()
_RATIO_LINEAGE_CACHE: OrderedDict[tuple[object, ...], RatioLineageCard] = OrderedDict()
_SCREENER_CACHE: OrderedDict[tuple[object, ...], tuple[pl.DataFrame, int]] = (
    OrderedDict()
)
_COHORT_PEER_RANKING_CACHE: OrderedDict[tuple[object, ...], pl.DataFrame] = (
    OrderedDict()
)
_ALL_L1B_CACHES: tuple[OrderedDict[tuple[object, ...], Any], ...] = (
    _SNAPSHOT_OVERVIEW_CACHE,
    _LATEST_RATIO_PERIOD_CACHE,
    _STRESS_INPUTS_CACHE,
    _COMPANY_PROFILE_CACHE,
    _INDUSTRY_COMPARISON_CACHE,
    _PEER_BAND_HISTORY_CACHE,
    _RATIO_PERIODS_CACHE,
    _RATIO_LINEAGE_CACHE,
    _SCREENER_CACHE,
    _COHORT_PEER_RANKING_CACHE,
)
_ACTIVE_L1B_SNAPSHOT_BY_ROOT: dict[tuple[object, ...], object] = {}


def _l1b_root_and_snapshot(
    cache_key: tuple[object, ...],
) -> tuple[tuple[object, ...], object] | None:
    """Extract ``(root_identity, snapshot_token)`` from an L1B cache key."""
    if not cache_key:
        return None
    kind = cache_key[0]
    if kind == "ducklake" and len(cache_key) >= 9:
        # ("ducklake", cat_path, cat_id, lake_data, alias, driver, dsn_env, snap_id, ver, ...)
        root = (
            "ducklake",
            cache_key[1],
            cache_key[3],
            cache_key[4],
            cache_key[5],
            cache_key[6],
        )
        return (root, (cache_key[2], cache_key[7], cache_key[8]))
    if kind == "parquet_dir" and len(cache_key) >= 3 and isinstance(cache_key[1], str):
        return (("parquet_dir", str(Path(cache_key[1]).parent)), cache_key[2])
    if kind == "parquet_snap" and len(cache_key) >= 3:
        return (("parquet_snap", cache_key[1]), cache_key[2])
    return None


def _evict_stale_l1b_caches_locked(snap_key: tuple[object, ...] | None) -> None:
    """Evict prior-snapshot L1B entries for the same data root (caller holds ``_QUERY_CACHE_LOCK``)."""
    if snap_key is None:
        return
    parsed = _l1b_root_and_snapshot(snap_key)
    if parsed is None:
        return
    target_root, target_snap = parsed
    if _ACTIVE_L1B_SNAPSHOT_BY_ROOT.get(target_root) == target_snap:
        return
    _ACTIVE_L1B_SNAPSHOT_BY_ROOT[target_root] = target_snap
    for cache in _ALL_L1B_CACHES:
        stale_keys = [
            existing
            for existing in cache
            if (ep := _l1b_root_and_snapshot(existing)) is not None
            and ep[0] == target_root
            and ep[1] != target_snap
        ]
        for stale_key in stale_keys:
            cache.pop(stale_key, None)


def clear_query_caches() -> None:
    """Clear all L1B query result caches and tracked active snapshot tokens."""
    with _QUERY_CACHE_LOCK:
        for cache in _ALL_L1B_CACHES:
            cache.clear()
        _ACTIVE_L1B_SNAPSHOT_BY_ROOT.clear()


def _can_use_query_result_cache(repo: ParquetRepository) -> bool:
    return (
        type(repo).read_table is ParquetRepository.read_table
        and ParquetRepository.read_table is _DEFAULT_READ_TABLE
    )


@contextmanager
def _pinned_table_source(
    pinned: ParquetRepository,
    table_name: str,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> Iterator[tuple[duckdb.DuckDBPyConnection, str, list[object]] | None]:
    """Yield ``(conn, source_sql, source_params)`` for a pinned snapshot table or ``None`` if absent.

    Caller-provided and session-provided connections are strictly non-owning and are
    never closed here.
    """
    filename, catalog_table = _normalize_table_name(table_name)
    binding = pinned._pinned_binding
    active_conn = connection if connection is not None else pinned._session_connection
    owns_conn = active_conn is None

    if binding is not None:
        con = duckdb.connect(":memory:") if active_conn is None else active_conn
        try:
            try:
                resolved = pinned._resolve_ducklake_table(
                    con, binding, filename, catalog_table
                )
            except duckdb.CatalogException as exc:
                if "does not exist" in str(exc).lower():
                    yield None
                    return
                raise
            if not resolved.exists:
                yield None
                return
            if resolved.parquet_files:
                param: object = (
                    resolved.parquet_files[0]
                    if len(resolved.parquet_files) == 1
                    else list(resolved.parquet_files)
                )
                yield (con, "read_parquet(?)", [param])
                return
            ensure_ducklake_attached(
                con,
                catalog_path=binding.catalog_path,
                data_path=binding.data_path,
                catalog_alias=binding.catalog_alias,
                catalog_driver=pinned._catalog_driver,
                postgres_dsn_env=pinned._postgres_dsn_env,
                read_only=True,
            )
            source_sql = (
                f'"{binding.catalog_alias}"."{catalog_table}" '
                f"AT (VERSION => {int(binding.ducklake_version)})"
            )
            yield (con, source_sql, [])
        finally:
            if owns_conn:
                con.close()
        return

    with pinned._read_path(filename) as path:
        if not path.is_file():
            yield None
            return
        con = duckdb.connect(":memory:") if active_conn is None else active_conn
        try:
            yield (con, "read_parquet(?)", [str(path)])
        finally:
            if owns_conn:
                con.close()


def _available_table_columns(
    pinned: ParquetRepository,
    table_name: str,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> set[str] | None:
    """Inspect column names for ``table_name`` with zero row scans, or ``None`` if absent."""
    cols = pinned.get_table_columns(table_name, connection=connection)
    return set(cols) if cols is not None else None


def _sql_count_rows(
    pinned: ParquetRepository,
    table_name: str,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> int:
    """Count rows in a pinned snapshot table via DuckDB SQL ``COUNT(*)``."""
    with (
        trace_span(
            SPAN_DUCKDB_QUERY,
            {
                "db.system": "duckdb",
                "db.operation": "count_rows",
                "db.table": table_name,
            },
        ),
        _pinned_table_source(pinned, table_name, connection=connection) as source,
    ):
        if source is None:
            return 0
        con, source_sql, source_params = source
        try:
            row = con.execute(
                f"SELECT COUNT(*) FROM {source_sql}", source_params
            ).fetchone()
        except duckdb.CatalogException as exc:
            if "does not exist" in str(exc).lower():
                return 0
            raise ContractViolationError(
                f"Unable to count rows for table {table_name!r}."
            ) from exc
        except (duckdb.Error, OSError) as exc:
            raise ContractViolationError(
                f"Unable to count rows for table {table_name!r}."
            ) from exc
        return int(row[0]) if row is not None and row[0] is not None else 0


def _sql_count_quality_statuses(
    pinned: ParquetRepository,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> tuple[int, int]:
    """Count PASS and WARNING periods via DuckDB SQL ``COUNT(*) FILTER (...)``."""
    with (
        trace_span(
            SPAN_DUCKDB_QUERY,
            {
                "db.system": "duckdb",
                "db.operation": "count_quality_statuses",
                "db.table": QUALITY_SUMMARY_TABLE,
            },
        ),
        _pinned_table_source(
            pinned, QUALITY_SUMMARY_TABLE, connection=connection
        ) as source,
    ):
        if source is None:
            return (0, 0)
        con, source_sql, source_params = source
        try:
            row = con.execute(
                f"""
                SELECT
                    COUNT(*) FILTER (WHERE "quality_status" = 'PASS'),
                    COUNT(*) FILTER (WHERE "quality_status" = 'WARNING')
                FROM {source_sql}
                """,
                source_params,
            ).fetchone()
        except duckdb.CatalogException as exc:
            if "does not exist" in str(exc).lower():
                return (0, 0)
            raise ContractViolationError(
                "Unable to aggregate quality status counts."
            ) from exc
        except (duckdb.Error, OSError) as exc:
            raise ContractViolationError(
                "Unable to aggregate quality status counts."
            ) from exc
        if row is None:
            return (0, 0)
        return (int(row[0] or 0), int(row[1] or 0))


def _sql_read_page(
    pinned: ParquetRepository,
    table_name: str,
    *,
    schema: pl.Schema,
    order_by_sql: str,
    limit: int,
    offset: int,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> tuple[pl.DataFrame, int]:
    """Read a paginated ``LIMIT/OFFSET`` slice and exact total row count via DuckDB SQL."""
    with (
        trace_span(
            SPAN_DUCKDB_QUERY,
            {
                "db.system": "duckdb",
                "db.operation": "read_page",
                "db.table": table_name,
                "db.limit": limit,
                "db.offset": offset,
            },
        ),
        _pinned_table_source(pinned, table_name, connection=connection) as source,
    ):
        if source is None:
            return pl.DataFrame(schema=schema), 0
        con, source_sql, source_params = source
        try:
            count_row = con.execute(
                f"SELECT COUNT(*) FROM {source_sql}", source_params
            ).fetchone()
            total = int(count_row[0]) if count_row is not None else 0
            if total == 0:
                return pl.DataFrame(schema=schema), 0
            arrow = con.execute(
                f"SELECT * FROM {source_sql} ORDER BY {order_by_sql} LIMIT ? OFFSET ?",
                [*source_params, limit, offset],
            ).fetch_arrow_table()
        except duckdb.CatalogException as exc:
            if "does not exist" in str(exc).lower():
                return pl.DataFrame(schema=schema), 0
            raise ContractViolationError(
                f"Unable to read paginated table {table_name!r}."
            ) from exc
        except (duckdb.Error, OSError) as exc:
            raise ContractViolationError(
                f"Unable to read paginated table {table_name!r}."
            ) from exc
        return cast(pl.DataFrame, pl.from_arrow(arrow)), total


def _validate_pagination(limit: int, offset: int, *, max_limit: int = 200) -> None:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not (1 <= limit <= max_limit)
    ):
        raise ValueError(f"limit must be an integer between 1 and {max_limit}.")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer.")


def _select_existing_columns(
    desired: Sequence[str], available: set[str] | None
) -> list[str]:
    if available is None:
        return []
    seen: set[str] = set()
    selected: list[str] = []
    for col in desired:
        if col in available and col not in seen:
            selected.append(col)
            seen.add(col)
    return selected


def _resolve_issuer_row(
    pinned: ParquetRepository,
    ticker: str,
    *,
    columns: Sequence[str],
    connection: duckdb.DuckDBPyConnection | None = None,
) -> dict[str, object]:
    clean_ticker = ticker.strip().upper()
    if not clean_ticker:
        raise ValueError("Ticker must not be empty.")
    issuer_rows = pinned.read_table(
        "issuers.parquet",
        columns=list(columns),
        filters={"ticker": clean_ticker},
        connection=connection,
    )
    if issuer_rows.is_empty():
        raise ValueError(f"Ticker not found in active snapshot: {clean_ticker}")
    return issuer_rows.row(0, named=True)


def _pad_schema_columns(frame: pl.DataFrame, schema: pl.Schema) -> pl.DataFrame:
    """Ensure every column in ``schema`` exists (typed NULL if absent) and return in schema order."""
    frame_schema = frame.schema
    if tuple(frame_schema.keys()) == tuple(schema.keys()) and all(
        frame_schema[name] == dtype for name, dtype in schema.items()
    ):
        return frame
    missing_exprs = [
        pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in schema.items()
        if name not in frame_schema
    ]
    if missing_exprs:
        frame = frame.with_columns(missing_exprs)
        frame_schema = frame.schema
    return frame.select(
        *(
            pl.col(name)
            if frame_schema.get(name) == dtype
            else pl.col(name).cast(dtype, strict=False).alias(name)
            for name, dtype in schema.items()
        )
    )


class _CachedDictsDataFrame(pl.DataFrame):
    """Polars DataFrame subclass that precomputes finite JSON-safe row dicts for zero-copy serving."""

    _cached_finite_dicts: list[dict[str, object]] | None = None
    _cached_schema_obj: pl.Schema | None = None
    _cached_col_list: list[str] | None = None
    _cached_row_count: int | None = None
    _cached_select_map: dict[tuple[str, ...], _CachedDictsDataFrame] | None = None

    @property
    def schema(self) -> pl.Schema:
        if self._cached_schema_obj is not None:
            return self._cached_schema_obj
        return super().schema

    def is_empty(self) -> bool:
        if self._cached_row_count is not None:
            return self._cached_row_count == 0
        return super().is_empty()

    def with_columns(
        self,
        *exprs: object,
        **named_exprs: object,
    ) -> pl.DataFrame:
        if (
            self._cached_finite_dicts is not None
            and not named_exprs
            and len(exprs) == 1
            and hasattr(exprs[0], "__iter__")
            and not isinstance(exprs[0], (list, tuple, dict, str, bytes))
        ):
            return self
        return super().with_columns(*exprs, **named_exprs)  # type: ignore[arg-type]

    def tail(self, n: int = 5) -> pl.DataFrame:
        if self._cached_row_count is not None and n >= self._cached_row_count:
            return self
        return _wrap_cached_dicts_df(super().tail(n))

    def head(self, n: int = 5) -> pl.DataFrame:
        if self._cached_row_count is not None and n >= self._cached_row_count:
            return self
        return _wrap_cached_dicts_df(super().head(n))

    def select(
        self,
        *exprs: object,
        **named_exprs: object,
    ) -> pl.DataFrame:
        if (
            self._cached_finite_dicts is not None
            and not named_exprs
            and exprs
            and all(isinstance(e, str) for e in exprs)
        ):
            col_key = tuple(str(e) for e in exprs)
            select_map = self._cached_select_map
            if select_map is not None:
                cached_sub = select_map.get(col_key)
                if cached_sub is not None:
                    return cached_sub
            sub = _wrap_cached_dicts_df(super().select(*col_key))
            with _QUERY_CACHE_LOCK:
                if self._cached_select_map is None:
                    self._cached_select_map = {}
                self._cached_select_map[col_key] = sub
            return sub
        return super().select(*exprs, **named_exprs)  # type: ignore[arg-type]

    def to_dicts(self) -> list[dict[str, object]]:
        if self._cached_finite_dicts is not None:
            return [dict(row) for row in self._cached_finite_dicts]
        return super().to_dicts()


def _wrap_cached_dicts_df(df: pl.DataFrame) -> _CachedDictsDataFrame:
    if isinstance(df, _CachedDictsDataFrame) and df._cached_finite_dicts is not None:
        return df
    df_schema = df.schema
    df_cols = list(df_schema.keys())
    df_height = df.height
    if df_height == 0:
        wrapped = _CachedDictsDataFrame(df)
        wrapped._cached_finite_dicts = []
        wrapped._cached_schema_obj = df_schema
        wrapped._cached_col_list = df_cols
        wrapped._cached_row_count = 0
        return wrapped
    rows: list[dict[str, object]] = df.to_dicts()
    float_cols = [name for name, dtype in df_schema.items() if dtype.is_float()]
    if float_cols:
        for row in rows:
            for col in float_cols:
                val = row[col]
                if isinstance(val, float) and not math.isfinite(val):
                    row[col] = None
    wrapped = _CachedDictsDataFrame(df)
    wrapped._cached_finite_dicts = rows
    wrapped._cached_schema_obj = df_schema
    wrapped._cached_col_list = df_cols
    wrapped._cached_row_count = df_height
    return wrapped


class DashboardQueryService:
    """Headless, stateless query facade reading exclusively from active Layer 3 snapshots."""

    def __init__(self, repository: ParquetRepository) -> None:
        self.repository = repository

    @classmethod
    def from_data_root(cls, data_root: Path | None = None) -> DashboardQueryService:
        return cls(ParquetRepository(data_root=resolve_data_root(data_root)))

    def get_snapshot_overview(
        self, *, connection: duckdb.DuckDBPyConnection | None = None
    ) -> SnapshotOverview:
        """Return active snapshot metadata and aggregate row counts via DuckDB SQL."""
        with self.repository.snapshot_session(connection=connection) as pinned:
            meta = pinned.get_snapshot_metadata(connection=connection)
            raw_snap_id = meta.get("snapshot_id")
            raw_published_at = meta.get("published_at")
            raw_backend = meta.get("storage_backend")
            snap_id = str(raw_snap_id) if isinstance(raw_snap_id, str) else None
            published_at = (
                str(raw_published_at) if isinstance(raw_published_at, str) else None
            )
            storage_backend = (
                str(raw_backend) if isinstance(raw_backend, str) else "parquet"
            )
            snap_key = pinned._snapshot_cache_key()
            if snap_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    cached_overview = _SNAPSHOT_OVERVIEW_CACHE.get(snap_key)
                    if cached_overview is not None:
                        _SNAPSHOT_OVERVIEW_CACHE.move_to_end(snap_key)
                        return cached_overview

            total_issuers = _sql_count_rows(
                pinned, "issuers.parquet", connection=connection
            )
            total_ratio_periods = _sql_count_rows(
                pinned, RATIOS_TABLE, connection=connection
            )
            pass_periods, warning_periods = _sql_count_quality_statuses(
                pinned, connection=connection
            )
            quarantined_rows = _sql_count_rows(
                pinned, AUDIT_QUARANTINE_TABLE, connection=connection
            )
            overview = SnapshotOverview(
                snapshot_id=snap_id,
                published_at=published_at,
                total_issuers=total_issuers,
                total_ratio_periods=total_ratio_periods,
                pass_periods=pass_periods,
                warning_periods=warning_periods,
                quarantined_rows=quarantined_rows,
                storage_backend=storage_backend,
            )
            if snap_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    _SNAPSHOT_OVERVIEW_CACHE[snap_key] = overview
                    while len(_SNAPSHOT_OVERVIEW_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _SNAPSHOT_OVERVIEW_CACHE.popitem(last=False)
            return overview

    def count_issuers(
        self, *, connection: duckdb.DuckDBPyConnection | None = None
    ) -> int:
        """Return total issuer count in the active snapshot via DuckDB SQL."""
        with self.repository.snapshot_session(connection=connection) as pinned:
            return _sql_count_rows(pinned, "issuers.parquet", connection=connection)

    def list_issuers(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        """List issuers sorted by ticker, with optional pagination."""
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer.")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            raise ValueError("limit must be a positive integer.")

        with self.repository.snapshot_session(connection=connection) as pinned:
            avail = _available_table_columns(
                pinned, "issuers.parquet", connection=connection
            )
            cols = _select_existing_columns(list(ISSUERS_SCHEMA.keys()), avail)
            if not cols:
                return pl.DataFrame(schema=ISSUERS_SCHEMA)
            issuers = pinned.read_table(
                "issuers.parquet", columns=cols, connection=connection
            ).sort("ticker")
            if offset > 0 or limit is not None:
                return issuers.slice(offset, limit)
            return issuers

    def get_company_ml_scores(
        self,
        ticker: str,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        """Read Layer 4B classical + ML scores for one issuer, or empty schema if ML absent."""
        with self.repository.snapshot_session(connection=connection) as pinned:
            issuer_row = _resolve_issuer_row(
                pinned,
                ticker,
                columns=("issuer_id", "ticker"),
                connection=connection,
            )
            issuer_id = str(issuer_row["issuer_id"])
            return self._read_ml_scores_for_issuer(
                pinned,
                issuer_id=issuer_id,
                statement_scope=statement_scope,
                period_type=period_type,
                connection=connection,
            )

    @staticmethod
    def _read_ml_scores_for_issuer(
        pinned: ParquetRepository,
        *,
        issuer_id: str,
        statement_scope: str,
        period_type: str,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        avail = _available_table_columns(pinned, ML_SCORES_TABLE, connection=connection)
        if not avail:
            return pl.DataFrame(schema=ML_SCORES_SCHEMA)
        cols = _select_existing_columns(list(ML_SCORES_SCHEMA.keys()), avail)
        if not cols:
            return pl.DataFrame(schema=ML_SCORES_SCHEMA)
        try:
            frame = pinned.read_table(
                ML_SCORES_TABLE,
                columns=cols,
                filters={
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                },
                connection=connection,
            )
        except FileNotFoundError:
            return pl.DataFrame(schema=ML_SCORES_SCHEMA)
        if frame.is_empty():
            return pl.DataFrame(schema=ML_SCORES_SCHEMA)
        return _pad_schema_columns(frame, ML_SCORES_SCHEMA).sort(
            ["period_end_date", "filing_id"]
        )

    def get_company_profile(
        self,
        ticker: str,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_view: PeriodView = "TTM",
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> CompanyProfileData:
        """Fetch full company health profile with predicate & projection pushdown."""
        clean_ticker = ticker.strip().upper()
        target_period_type = "ANNUAL" if period_view == "Annual" else "QUARTER"

        with self.repository.snapshot_session(connection=connection) as pinned:
            snap_key = (
                pinned._snapshot_cache_key()
                if _can_use_query_result_cache(pinned)
                else None
            )
            profile_cache_key = (
                (*snap_key, clean_ticker, statement_scope, period_view)
                if snap_key is not None
                else None
            )
            if profile_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    cached_profile = _COMPANY_PROFILE_CACHE.get(profile_cache_key)
                    if cached_profile is not None:
                        _COMPANY_PROFILE_CACHE.move_to_end(profile_cache_key)
                        return cached_profile

            issuer_row = _resolve_issuer_row(
                pinned,
                clean_ticker,
                columns=(
                    "issuer_id",
                    "ticker",
                    "company_name_vn",
                    "accounting_regime",
                ),
                connection=connection,
            )
            issuer_id = str(issuer_row["issuer_id"])

            avail_ratio_cols = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            ratio_cols = _select_existing_columns(
                list(FINANCIAL_RATIOS_SCHEMA.keys()), avail_ratio_cols
            )
            ratios = pinned.read_table(
                RATIOS_TABLE,
                columns=ratio_cols,
                filters={
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                    "period_type": target_period_type,
                },
                connection=connection,
            ).sort("period_end_date")
            # Gracefully expose V2 core/forensic columns as typed NULL on legacy snapshots.
            missing_v2_exprs = [
                pl.lit(None, dtype=dtype).alias(col)
                for col, dtype in _V2_PROFILE_OPTIONAL_RATIO_COLS.items()
                if col not in ratios.columns
            ]
            if missing_v2_exprs:
                ratios = ratios.with_columns(missing_v2_exprs)

            growth_metrics = (
                "IS_NET_REVENUE",
                "IS_NET_INCOME",
                "CF_OPERATING_CASH_FLOW",
            )
            bs_metrics = ("BS_TOTAL_ASSETS", "BS_TOTAL_EQUITY", "BS_TOTAL_LIABILITIES")
            needed_metrics = [*growth_metrics, *bs_metrics]

            facts_table = "latest_facts.parquet"
            avail_fact_cols = _available_table_columns(
                pinned, facts_table, connection=connection
            )
            if avail_fact_cols is None:
                facts_table = "financial_facts.parquet"
                avail_fact_cols = _available_table_columns(
                    pinned, facts_table, connection=connection
                )
            desired_fact_cols = (
                "period_end_date",
                "fiscal_year",
                "fiscal_quarter",
                "taxonomy_version",
                "metric_id",
                "value",
                "ttm_value",
                "effective_available_at",
                "revision_no",
                "filing_id",
            )
            fact_cols = _select_existing_columns(desired_fact_cols, avail_fact_cols)
            sort_fact_cols = [
                col
                for col in (
                    "period_end_date",
                    "taxonomy_version",
                    "effective_available_at",
                    "revision_no",
                    "filing_id",
                )
                if col in fact_cols
            ]
            company_facts = pinned.read_table(
                facts_table,
                columns=fact_cols,
                filters={
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                    "period_type": target_period_type,
                    "metric_id": needed_metrics,
                },
                connection=connection,
            ).sort(sort_fact_cols)

            latest_quality_status: str | None = None
            latest_warnings: tuple[str, ...] = ()
            avail_q_cols = _available_table_columns(
                pinned, QUALITY_SUMMARY_TABLE, connection=connection
            )
            if avail_q_cols:
                q_cols = _select_existing_columns(
                    ("period_end_date", "quality_status", "warning_codes"),
                    avail_q_cols,
                )
                q_filtered = pinned.read_table(
                    QUALITY_SUMMARY_TABLE,
                    columns=q_cols,
                    filters={
                        "issuer_id": issuer_id,
                        "statement_scope": statement_scope,
                        "period_type": target_period_type,
                    },
                    connection=connection,
                ).sort("period_end_date")
                if not q_filtered.is_empty():
                    q_last = q_filtered.row(-1, named=True)
                    latest_quality_status = str(q_last["quality_status"])
                    raw_warnings = q_last.get("warning_codes") or []
                    latest_warnings = tuple(str(code) for code in raw_warnings)

            latest_taxonomy: str | None = (
                str(ratios["taxonomy_version"][-1]) if not ratios.is_empty() else None
            )

            flow_value_col = (
                "ttm_value"
                if period_view == "TTM" and "ttm_value" in company_facts.columns
                else "value"
            )
            growth_series = (
                company_facts.filter(pl.col("metric_id").is_in(growth_metrics))
                .group_by(["period_end_date", "fiscal_year", "fiscal_quarter"])
                .agg(
                    *(
                        pl.col(flow_value_col)
                        .filter(pl.col("metric_id") == metric)
                        .last()
                        .alias(metric)
                        for metric in growth_metrics
                    )
                )
                .sort("period_end_date")
            )

            bs_series = (
                company_facts.filter(pl.col("metric_id").is_in(bs_metrics))
                .group_by(["period_end_date", "fiscal_year", "fiscal_quarter"])
                .agg(
                    *(
                        pl.col("value")
                        .filter(pl.col("metric_id") == metric)
                        .last()
                        .alias(metric)
                        for metric in bs_metrics
                    )
                )
                .sort("period_end_date")
            )
            debt_series = ratios.select("period_end_date", "total_debt")
            capital_structure_series = bs_series.join(
                debt_series, on="period_end_date", how="left"
            ).sort("period_end_date")

            dupont_series = ratios.select(
                "period_end_date",
                "fiscal_year",
                "fiscal_quarter",
                "taxonomy_version",
                "tax_burden",
                "interest_burden",
                "ebit_margin",
                "asset_turnover",
                "equity_multiplier_total",
                "roe_total",
                "dupont_valid_flag",
            ).sort("period_end_date")

            avail_rf_cols = _available_table_columns(
                pinned, RED_FLAGS_TABLE, connection=connection
            )
            if avail_rf_cols:
                rf_cols = _select_existing_columns(
                    list(_EMPTY_RED_FLAGS_SCHEMA.keys()), avail_rf_cols
                )
                red_flags_raw = pinned.read_table(
                    RED_FLAGS_TABLE,
                    columns=rf_cols,
                    filters={
                        "issuer_id": issuer_id,
                        "statement_scope": statement_scope,
                        "period_type": target_period_type,
                    },
                    connection=connection,
                )
                red_flags = (
                    red_flags_raw.sort(
                        ["period_end_date", "severity", "flag_code"],
                        descending=[True, False, False],
                    )
                    if not red_flags_raw.is_empty()
                    else pl.DataFrame(schema=_EMPTY_RED_FLAGS_SCHEMA)
                )
            else:
                red_flags = pl.DataFrame(schema=_EMPTY_RED_FLAGS_SCHEMA)

            ml_scores = self._read_ml_scores_for_issuer(
                pinned,
                issuer_id=issuer_id,
                statement_scope=statement_scope,
                period_type=target_period_type,
                connection=connection,
            )

            raw_company_name = issuer_row.get("company_name_vn")
            profile_result = CompanyProfileData(
                ticker=clean_ticker,
                issuer_id=issuer_id,
                company_name_vn=(
                    str(raw_company_name) if raw_company_name is not None else None
                ),
                accounting_regime=str(issuer_row["accounting_regime"]),
                statement_scope=statement_scope,
                period_view=period_view,
                latest_taxonomy_version=latest_taxonomy,
                latest_quality_status=latest_quality_status,
                latest_warning_codes=latest_warnings,
                growth_series=_wrap_cached_dicts_df(growth_series),
                capital_structure_series=_wrap_cached_dicts_df(
                    capital_structure_series
                ),
                dupont_series=_wrap_cached_dicts_df(dupont_series),
                red_flags=_wrap_cached_dicts_df(red_flags),
                ratios_table=_wrap_cached_dicts_df(ratios),
                ml_scores=_wrap_cached_dicts_df(ml_scores),
            )
            if profile_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    _COMPANY_PROFILE_CACHE[profile_cache_key] = profile_result
                    while len(_COMPANY_PROFILE_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _COMPANY_PROFILE_CACHE.popitem(last=False)
            return profile_result

    def get_industry_comparison(
        self,
        ticker: str,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        metric_name: str = "roe_total",
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> IndustryComparisonData:
        """Fetch cross-sectional peer comparison with predicate & projection pushdown."""
        clean_ticker = ticker.strip().upper()
        with self.repository.snapshot_session(connection=connection) as pinned:
            snap_key = (
                pinned._snapshot_cache_key()
                if _can_use_query_result_cache(pinned)
                else None
            )
            comp_cache_key = (
                (
                    *snap_key,
                    clean_ticker,
                    statement_scope,
                    period_type,
                    period_end_date,
                    metric_name,
                )
                if snap_key is not None
                else None
            )
            if comp_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    cached_comp = _INDUSTRY_COMPARISON_CACHE.get(comp_cache_key)
                    if cached_comp is not None:
                        _INDUSTRY_COMPARISON_CACHE.move_to_end(comp_cache_key)
                        return cached_comp

            issuer_row = _resolve_issuer_row(
                pinned,
                clean_ticker,
                columns=("issuer_id", "ticker", "industry_snapshot_date"),
                connection=connection,
            )
            issuer_id = str(issuer_row["issuer_id"])
            snapshot_date = issuer_row.get("industry_snapshot_date")
            resolved_snapshot_date = (
                snapshot_date if isinstance(snapshot_date, date) else None
            )

            avail_bench_cols = _available_table_columns(
                pinned, BENCHMARKS_TABLE, connection=connection
            )
            if not avail_bench_cols:
                return IndustryComparisonData(
                    ticker=clean_ticker,
                    issuer_id=issuer_id,
                    statement_scope=statement_scope,
                    period_type=period_type,
                    period_end_date=period_end_date or date.today(),
                    metric_name=metric_name,
                    peer_group_level=None,
                    icb_code_used=None,
                    peer_count=0,
                    company_value=None,
                    peer_median=None,
                    peer_p25=None,
                    peer_p75=None,
                    peer_iqr=None,
                    percentile_rank=None,
                    robust_z_score=None,
                    industry_snapshot_date=resolved_snapshot_date,
                    peer_ranking=pl.DataFrame(),
                )

            target_cols = _select_existing_columns(
                (
                    "issuer_id",
                    "accounting_regime",
                    "statement_scope",
                    "period_type",
                    "period_end_date",
                    "metric_or_ratio_name",
                    "icb_code_used",
                    "peer_group_level",
                    "peer_count",
                    "value",
                    "peer_median",
                    "peer_p25",
                    "peer_p75",
                    "peer_iqr",
                    "percentile_rank",
                    "robust_z_score",
                ),
                avail_bench_cols,
            )
            target_filters: dict[str, object] = {
                "issuer_id": issuer_id,
                "statement_scope": statement_scope,
                "period_type": period_type,
                "metric_or_ratio_name": metric_name,
            }
            if period_end_date is not None:
                target_filters["period_end_date"] = period_end_date

            target_rows = pinned.read_table(
                BENCHMARKS_TABLE,
                columns=target_cols,
                filters=target_filters,
                connection=connection,
            )
            if period_end_date is None and not target_rows.is_empty():
                target_rows = target_rows.sort("period_end_date").tail(1)

            if target_rows.is_empty():
                resolved_end = period_end_date or date.today()
                return IndustryComparisonData(
                    ticker=clean_ticker,
                    issuer_id=issuer_id,
                    statement_scope=statement_scope,
                    period_type=period_type,
                    period_end_date=resolved_end,
                    metric_name=metric_name,
                    peer_group_level=None,
                    icb_code_used=None,
                    peer_count=0,
                    company_value=None,
                    peer_median=None,
                    peer_p25=None,
                    peer_p75=None,
                    peer_iqr=None,
                    percentile_rank=None,
                    robust_z_score=None,
                    industry_snapshot_date=resolved_snapshot_date,
                    peer_ranking=pl.DataFrame(),
                )

            selected = target_rows.row(0, named=True)
            resolved_end = selected["period_end_date"]
            peer_level = str(selected["peer_group_level"])
            icb_used = selected.get("icb_code_used")
            accounting_regime = str(selected["accounting_regime"])

            cohort_cache_key = (
                (
                    *snap_key,
                    accounting_regime,
                    statement_scope,
                    period_type,
                    resolved_end,
                    metric_name,
                    peer_level,
                    icb_used,
                )
                if snap_key is not None
                else None
            )
            peer_ranking: pl.DataFrame | None = None
            if cohort_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    peer_ranking = _COHORT_PEER_RANKING_CACHE.get(cohort_cache_key)
                    if peer_ranking is not None:
                        _COHORT_PEER_RANKING_CACHE.move_to_end(cohort_cache_key)

            if peer_ranking is None:
                cohort_cols = _select_existing_columns(
                    (
                        "issuer_id",
                        "value",
                        "percentile_rank",
                        "robust_z_score",
                        "peer_group_level",
                        "icb_code_used",
                    ),
                    avail_bench_cols,
                )
                cohort_filters: dict[str, object] = {
                    "accounting_regime": accounting_regime,
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "period_end_date": resolved_end,
                    "metric_or_ratio_name": metric_name,
                    "peer_group_level": peer_level,
                    "icb_code_used": icb_used,
                }
                cohort = pinned.read_table(
                    BENCHMARKS_TABLE,
                    columns=cohort_cols,
                    filters=cohort_filters,
                    connection=connection,
                )
                cohort_issuer_ids = cohort["issuer_id"].unique().to_list()
                peer_issuers = pinned.read_table(
                    "issuers.parquet",
                    columns=[
                        "issuer_id",
                        "ticker",
                        "company_name_vn",
                        "icb_name_level2",
                        "icb_name_level3",
                    ],
                    filters={"issuer_id": cohort_issuer_ids},
                    connection=connection,
                )

                peer_ranking = _wrap_cached_dicts_df(
                    cohort.join(peer_issuers, on="issuer_id", how="left")
                    .select(
                        "ticker",
                        "company_name_vn",
                        "value",
                        "percentile_rank",
                        "robust_z_score",
                        "peer_group_level",
                        "icb_code_used",
                        "icb_name_level3",
                        "icb_name_level2",
                    )
                    .sort("value", descending=True)
                )
                if cohort_cache_key is not None:
                    with _QUERY_CACHE_LOCK:
                        _evict_stale_l1b_caches_locked(snap_key)
                        _COHORT_PEER_RANKING_CACHE[cohort_cache_key] = peer_ranking
                        while len(_COHORT_PEER_RANKING_CACHE) > _MAX_QUERY_CACHE_SIZE:
                            _COHORT_PEER_RANKING_CACHE.popitem(last=False)

            comp_result = IndustryComparisonData(
                ticker=clean_ticker,
                issuer_id=issuer_id,
                statement_scope=statement_scope,
                period_type=period_type,
                period_end_date=resolved_end,
                metric_name=metric_name,
                peer_group_level=peer_level,
                icb_code_used=str(icb_used) if icb_used is not None else None,
                peer_count=int(selected["peer_count"]),
                company_value=selected.get("value"),
                peer_median=selected.get("peer_median"),
                peer_p25=selected.get("peer_p25"),
                peer_p75=selected.get("peer_p75"),
                peer_iqr=selected.get("peer_iqr"),
                percentile_rank=selected.get("percentile_rank"),
                robust_z_score=selected.get("robust_z_score"),
                industry_snapshot_date=resolved_snapshot_date,
                peer_ranking=peer_ranking,
            )
            if comp_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    _INDUSTRY_COMPARISON_CACHE[comp_cache_key] = comp_result
                    while len(_INDUSTRY_COMPARISON_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _INDUSTRY_COMPARISON_CACHE.popitem(last=False)
            return comp_result

    def get_peer_band_history(
        self,
        ticker: str,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        metric_name: str = "roe_total",
        *,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        """Return historical peer band trajectory ([P25, Median, P75] + peer_group_level)."""
        clean_ticker = ticker.strip().upper()
        with self.repository.snapshot_session(connection=connection) as pinned:
            snap_key = (
                pinned._snapshot_cache_key()
                if _can_use_query_result_cache(pinned)
                else None
            )
            hist_cache_key = (
                (*snap_key, clean_ticker, statement_scope, period_type, metric_name)
                if snap_key is not None
                else None
            )
            if hist_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    cached_hist = _PEER_BAND_HISTORY_CACHE.get(hist_cache_key)
                    if cached_hist is not None:
                        _PEER_BAND_HISTORY_CACHE.move_to_end(hist_cache_key)
                        return cached_hist

            issuer_row = _resolve_issuer_row(
                pinned,
                ticker,
                columns=("issuer_id", "ticker"),
                connection=connection,
            )
            issuer_id = str(issuer_row["issuer_id"])

            avail = _available_table_columns(
                pinned, BENCHMARKS_TABLE, connection=connection
            )
            if not avail:
                return pl.DataFrame(schema=PEER_BAND_HISTORY_SCHEMA)
            cols = _select_existing_columns(
                list(PEER_BAND_HISTORY_SCHEMA.keys()), avail
            )
            frame = pinned.read_table(
                BENCHMARKS_TABLE,
                columns=cols,
                filters={
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "metric_or_ratio_name": metric_name,
                },
                connection=connection,
            )
            if frame.is_empty():
                return pl.DataFrame(schema=PEER_BAND_HISTORY_SCHEMA)
            hist_res = _wrap_cached_dicts_df(
                _pad_schema_columns(frame, PEER_BAND_HISTORY_SCHEMA).sort(
                    "period_end_date"
                )
            )
            if hist_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    _PEER_BAND_HISTORY_CACHE[hist_cache_key] = hist_res
                    while len(_PEER_BAND_HISTORY_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _PEER_BAND_HISTORY_CACHE.popitem(last=False)
            return hist_res

    def get_quality_summary_page(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> tuple[pl.DataFrame, int]:
        """Return a paginated slice of ``quality_summary.parquet`` and its total row count."""
        _validate_pagination(limit, offset, max_limit=200)
        with self.repository.snapshot_session(connection=connection) as pinned:
            return _sql_read_page(
                pinned,
                QUALITY_SUMMARY_TABLE,
                schema=QUALITY_SUMMARY_SCHEMA,
                order_by_sql='"period_end_date" DESC, "issuer_id" ASC, "statement_scope" ASC, "period_type" ASC',
                limit=limit,
                offset=offset,
                connection=connection,
            )

    def get_audit_quarantine_page(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> tuple[pl.DataFrame, int]:
        """Return a paginated slice of ``audit_quarantine.parquet`` and its total row count."""
        _validate_pagination(limit, offset, max_limit=200)
        with self.repository.snapshot_session(connection=connection) as pinned:
            return _sql_read_page(
                pinned,
                AUDIT_QUARANTINE_TABLE,
                schema=AUDIT_QUARANTINE_SCHEMA,
                order_by_sql='"period_end_date" DESC, "issuer_id" ASC, "filing_id" ASC, "rule_code" ASC',
                limit=limit,
                offset=offset,
                connection=connection,
            )

    def get_quality_tables(
        self,
        limit: int = 200,
        offset: int = 0,
        *,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> QualityTablesPage:
        """Return paginated quality summary and audit quarantine tables with total counts."""
        _validate_pagination(limit, offset, max_limit=200)
        with self.repository.snapshot_session(connection=connection) as pinned:
            summary, total_summary = _sql_read_page(
                pinned,
                QUALITY_SUMMARY_TABLE,
                schema=QUALITY_SUMMARY_SCHEMA,
                order_by_sql='"period_end_date" DESC, "issuer_id" ASC, "statement_scope" ASC, "period_type" ASC',
                limit=limit,
                offset=offset,
                connection=connection,
            )
            quarantine, total_quarantine = _sql_read_page(
                pinned,
                AUDIT_QUARANTINE_TABLE,
                schema=AUDIT_QUARANTINE_SCHEMA,
                order_by_sql='"period_end_date" DESC, "issuer_id" ASC, "filing_id" ASC, "rule_code" ASC',
                limit=limit,
                offset=offset,
                connection=connection,
            )
            return QualityTablesPage(
                quality_summary=summary,
                audit_quarantine=quarantine,
                total_summary=total_summary,
                total_quarantine=total_quarantine,
            )

    def list_ratio_periods(
        self,
        ticker: str,
        *,
        statement_scope: str | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        """List available ratio periods for one ticker using ``issuer_id`` pushdown."""
        clean_ticker = ticker.strip().upper()
        with self.repository.snapshot_session(connection=connection) as pinned:
            snap_key = (
                pinned._snapshot_cache_key()
                if _can_use_query_result_cache(pinned)
                else None
            )
            rp_cache_key = (
                (*snap_key, clean_ticker, statement_scope)
                if snap_key is not None
                else None
            )
            if rp_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    cached_rp = _RATIO_PERIODS_CACHE.get(rp_cache_key)
                    if cached_rp is not None:
                        _RATIO_PERIODS_CACHE.move_to_end(rp_cache_key)
                        return cached_rp.clone()

            issuer_rows = pinned.read_table(
                "issuers.parquet",
                columns=["issuer_id", "ticker"],
                filters={"ticker": clean_ticker},
                connection=connection,
            )
            if issuer_rows.is_empty():
                return pl.DataFrame(schema=_EMPTY_RATIO_PERIODS_SCHEMA)
            issuer_id = str(issuer_rows.row(0, named=True)["issuer_id"])

            filters: dict[str, object] = {"issuer_id": issuer_id}
            if statement_scope is not None:
                filters["statement_scope"] = statement_scope
            ratios = pinned.read_table(
                RATIOS_TABLE,
                columns=list(_EMPTY_RATIO_PERIODS_SCHEMA.keys()),
                filters=filters,
                connection=connection,
            )
            rp_result = ratios.sort(
                ["period_end_date", "period_type"], descending=[True, False]
            )
            if rp_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    _RATIO_PERIODS_CACHE[rp_cache_key] = rp_result
                    while len(_RATIO_PERIODS_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _RATIO_PERIODS_CACHE.popitem(last=False)
            return rp_result.clone()

    def list_available_periods(
        self,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> list[date]:
        """List all distinct ``period_end_date`` values available in ``financial_ratios``."""
        with self.repository.snapshot_session(connection=connection) as pinned:
            avail = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            if not avail or "period_end_date" not in avail:
                return []
            dates_df = pinned.read_table(
                RATIOS_TABLE,
                columns=["period_end_date"],
                filters={
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                },
                connection=connection,
            )
            if dates_df.is_empty():
                return []
            unique_dates = (
                dates_df.get_column("period_end_date")
                .drop_nulls()
                .unique()
                .sort(descending=True)
                .to_list()
            )
            return [d for d in unique_dates if isinstance(d, date)]

    def get_ratio_lineage(
        self,
        ticker: str,
        *,
        statement_scope: str,
        period_type: str,
        period_end_date: date,
        ratio_name: str,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> RatioLineageCard:
        """Trace a published financial ratio back to all numerator & denominator filings."""
        if ratio_name not in RATIO_LINEAGE_CATALOG:
            raise ValueError(
                f"Unknown financial ratio for lineage lookup: {ratio_name}"
            )
        spec = RATIO_LINEAGE_CATALOG[ratio_name]
        clean_ticker = ticker.strip().upper()

        with self.repository.snapshot_session(connection=connection) as pinned:
            snap_key = (
                pinned._snapshot_cache_key()
                if _can_use_query_result_cache(pinned)
                else None
            )
            lineage_cache_key = (
                (
                    *snap_key,
                    clean_ticker,
                    statement_scope,
                    period_type,
                    period_end_date,
                    ratio_name,
                )
                if snap_key is not None
                else None
            )
            if lineage_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    cached_lineage = _RATIO_LINEAGE_CACHE.get(lineage_cache_key)
                    if cached_lineage is not None:
                        _RATIO_LINEAGE_CACHE.move_to_end(lineage_cache_key)
                        return cached_lineage

            issuer_row = _resolve_issuer_row(
                pinned,
                clean_ticker,
                columns=("issuer_id", "ticker"),
                connection=connection,
            )
            issuer_id = str(issuer_row["issuer_id"])

            avail_ratio_cols = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            desired_ratio_cols = (
                "issuer_id",
                "fiscal_year",
                "fiscal_quarter",
                "taxonomy_version",
                "filing_id",
                "source_filing_ids",
                ratio_name,
                f"{ratio_name}_flag",
            )
            ratio_cols = _select_existing_columns(desired_ratio_cols, avail_ratio_cols)
            ratios = pinned.read_table(
                RATIOS_TABLE,
                columns=ratio_cols,
                filters={
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "period_end_date": period_end_date,
                },
                connection=connection,
            )
            if ratios.is_empty():
                raise ValueError(
                    f"No ratio row found for {clean_ticker} {statement_scope} "
                    f"{period_type} {period_end_date.isoformat()}."
                )
            ratio_row = ratios.row(0, named=True)
            anchor_filing_id = str(ratio_row["filing_id"])
            raw_source_ids = ratio_row.get("source_filing_ids") or []
            source_ids = tuple(
                sorted({anchor_filing_id, *(str(fid) for fid in raw_source_ids if fid)})
            )

            facts_table = "financial_facts.parquet"
            avail_fact_cols = _available_table_columns(
                pinned, facts_table, connection=connection
            )
            if avail_fact_cols is None:
                facts_table = "latest_facts.parquet"
                avail_fact_cols = _available_table_columns(
                    pinned, facts_table, connection=connection
                )
            desired_fact_cols = (
                "issuer_id",
                "statement_scope",
                "metric_id",
                "period_type",
                "fiscal_year",
                "fiscal_quarter",
                "period_end_date",
                "value",
                "filing_id",
                "revision_no",
                "taxonomy_version",
                "derivation_method",
                "source_provider",
                "published_at",
                "published_at_basis",
                "effective_available_at",
            )
            fact_cols = _select_existing_columns(desired_fact_cols, avail_fact_cols)
            matched_facts = pinned.read_table(
                facts_table,
                columns=fact_cols,
                filters={
                    "issuer_id": issuer_id,
                    "filing_id": list(source_ids),
                },
                connection=connection,
            )

            known_ids = set(matched_facts["filing_id"].to_list())
            orphans = tuple(sorted(set(source_ids) - known_ids))
            scoped_facts = matched_facts.filter(
                pl.col("statement_scope") == statement_scope
            )

            manifest_lookup: dict[str, dict[str, object]] = {}
            avail_manifest_cols = _available_table_columns(
                pinned, FILINGS_MANIFEST_TABLE, connection=connection
            )
            if avail_manifest_cols:
                m_cols = _select_existing_columns(
                    (
                        "filing_id",
                        "source_provider",
                        "connector_name",
                        "published_at",
                        "published_at_basis",
                        "canonical_content_sha256",
                    ),
                    avail_manifest_cols,
                )
                matched_manifest = pinned.read_table(
                    FILINGS_MANIFEST_TABLE,
                    columns=m_cols,
                    filters={
                        "issuer_id": issuer_id,
                        "filing_id": list(source_ids),
                    },
                    connection=connection,
                )
                for row in matched_manifest.to_dicts():
                    manifest_lookup[str(row["filing_id"])] = row

            def _select_window(
                frame: pl.DataFrame,
                role: Literal["NUMERATOR", "DENOMINATOR"],
            ) -> pl.DataFrame:
                if frame.is_empty():
                    return frame
                is_stock = pl.col("metric_id").str.starts_with("BS_")
                if role == "NUMERATOR":
                    if period_type == "ANNUAL":
                        return frame.filter(
                            (pl.col("period_end_date") == period_end_date)
                            & (is_stock | (pl.col("period_type") == "ANNUAL"))
                        )
                    stock_rows = frame.filter(
                        is_stock & (pl.col("period_end_date") == period_end_date)
                    )
                    flow_rows = (
                        frame.filter(
                            ~is_stock
                            & (pl.col("period_type") == "QUARTER")
                            & (pl.col("period_end_date") <= period_end_date)
                        )
                        .sort(["metric_id", "period_end_date"])
                        .group_by("metric_id", maintain_order=True)
                        .tail(4)
                        .select(frame.columns)
                    )
                    return pl.concat([stock_rows, flow_rows])

                mode = spec.denominator_mode
                if mode == "POINT_IN_TIME_STOCK":
                    return frame.filter(pl.col("period_end_date") == period_end_date)
                if mode == "AVERAGE_STOCK":
                    closing = frame.filter(pl.col("period_end_date") == period_end_date)
                    opening = (
                        frame.filter(pl.col("period_end_date") < period_end_date)
                        .sort(["metric_id", "period_end_date"])
                        .group_by("metric_id", maintain_order=True)
                        .tail(1)
                        .select(frame.columns)
                    )
                    return pl.concat([opening, closing])
                if mode == "FLOW":
                    if period_type == "ANNUAL":
                        return frame.filter(
                            (pl.col("period_end_date") == period_end_date)
                            & (pl.col("period_type") == "ANNUAL")
                        )
                    return (
                        frame.filter(
                            (pl.col("period_type") == "QUARTER")
                            & (pl.col("period_end_date") <= period_end_date)
                        )
                        .sort(["metric_id", "period_end_date"])
                        .group_by("metric_id", maintain_order=True)
                        .tail(4)
                        .select(frame.columns)
                    )
                if mode == "PRIOR_YEAR_FLOW":
                    if period_type == "ANNUAL":
                        return (
                            frame.filter(
                                (pl.col("period_type") == "ANNUAL")
                                & (pl.col("period_end_date") < period_end_date)
                            )
                            .sort(["metric_id", "period_end_date"])
                            .group_by("metric_id", maintain_order=True)
                            .tail(1)
                            .select(frame.columns)
                        )
                    return (
                        frame.filter(
                            (pl.col("period_type") == "QUARTER")
                            & (pl.col("period_end_date") < period_end_date)
                        )
                        .sort(["metric_id", "period_end_date"])
                        .group_by("metric_id", maintain_order=True)
                        .tail(4)
                        .select(frame.columns)
                    )
                return frame

            def _extract_components(
                role: Literal["NUMERATOR", "DENOMINATOR"],
                metrics: tuple[str, ...],
            ) -> tuple[LineageComponent, ...]:
                matched = (
                    scoped_facts.filter(pl.col("metric_id").is_in(list(metrics)))
                    .sort(["metric_id", "period_end_date", "revision_no", "filing_id"])
                    .unique(
                        subset=["metric_id", "period_end_date", "period_type"],
                        keep="last",
                    )
                )
                windowed = _select_window(matched, role).sort(
                    ["metric_id", "period_end_date"]
                )
                items: list[LineageComponent] = []
                for row in windowed.to_dicts():
                    fid = str(row["filing_id"])
                    m_info = manifest_lookup.get(fid, {})
                    raw_src_provider = m_info.get("source_provider") or row.get(
                        "source_provider"
                    )
                    raw_connector = m_info.get("connector_name")
                    raw_sha = m_info.get("canonical_content_sha256")
                    pub_at = row.get("published_at")
                    if pub_at is None and isinstance(m_info.get("published_at"), date):
                        pub_at = m_info["published_at"]
                    eff_at = row.get("effective_available_at")
                    pub_basis = row.get("published_at_basis") or m_info.get(
                        "published_at_basis"
                    )
                    items.append(
                        LineageComponent(
                            role=role,
                            metric_id=str(row["metric_id"]),
                            period_type=str(row["period_type"]),
                            fiscal_year=int(row["fiscal_year"]),
                            fiscal_quarter=(
                                int(row["fiscal_quarter"])
                                if row.get("fiscal_quarter") is not None
                                else None
                            ),
                            period_end_date=row["period_end_date"],
                            value=(
                                float(row["value"])
                                if row.get("value") is not None
                                else None
                            ),
                            filing_id=fid,
                            taxonomy_version=str(row["taxonomy_version"]),
                            derivation_method=str(row["derivation_method"]),
                            source_provider=(
                                str(raw_src_provider)
                                if raw_src_provider is not None
                                else None
                            ),
                            connector_name=(
                                str(raw_connector)
                                if raw_connector is not None
                                else None
                            ),
                            published_at=pub_at if isinstance(pub_at, date) else None,
                            published_at_basis=(
                                str(pub_basis) if pub_basis is not None else None
                            ),
                            effective_available_at=(
                                eff_at if isinstance(eff_at, date) else None
                            ),
                            canonical_content_sha256=(
                                str(raw_sha) if raw_sha is not None else None
                            ),
                        )
                    )
                return tuple(items)

            lineage_result = RatioLineageCard(
                ticker=clean_ticker,
                issuer_id=issuer_id,
                statement_scope=statement_scope,
                period_type=period_type,
                fiscal_year=int(ratio_row["fiscal_year"]),
                fiscal_quarter=(
                    int(ratio_row["fiscal_quarter"])
                    if ratio_row.get("fiscal_quarter") is not None
                    else None
                ),
                period_end_date=period_end_date,
                ratio_name=ratio_name,
                display_name_vn=spec.display_name_vn,
                formula=spec.formula,
                ratio_value=(
                    float(ratio_row[ratio_name])
                    if ratio_row.get(ratio_name) is not None
                    else None
                ),
                status_flag=(
                    str(ratio_row[f"{ratio_name}_flag"])
                    if ratio_row.get(f"{ratio_name}_flag") is not None
                    else None
                ),
                taxonomy_version=str(ratio_row["taxonomy_version"]),
                denominator_mode=spec.denominator_mode,
                anchor_filing_id=anchor_filing_id,
                source_filing_ids=source_ids,
                numerator_components=_extract_components(
                    "NUMERATOR", spec.numerator_metrics
                ),
                denominator_components=_extract_components(
                    "DENOMINATOR", spec.denominator_metrics
                ),
                orphan_filing_ids=orphans,
            )
            if lineage_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    _RATIO_LINEAGE_CACHE[lineage_cache_key] = lineage_result
                    while len(_RATIO_LINEAGE_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _RATIO_LINEAGE_CACHE.popitem(last=False)
            return lineage_result

    @staticmethod
    def _validate_screener_filters(
        *,
        period_type: str,
        statement_scope: str,
        min_f_score: int | None,
        altman_zone: str | None,
        risk_tier: str | None,
        beneish_flag: str | None,
        max_distress_prob: float | None,
        min_distress_prob: float | None,
        max_red_flags: int | None,
        min_red_flags: int | None,
        q: str | None,
        sort_by: str,
        sort_dir: Literal["asc", "desc"],
    ) -> str | None:
        if statement_scope not in STATEMENT_SCOPES:
            raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
        if period_type not in PERIOD_TYPES:
            raise ValueError(f"Unsupported period_type: {period_type!r}")
        if min_f_score is not None and (
            isinstance(min_f_score, bool)
            or not isinstance(min_f_score, int)
            or not (0 <= min_f_score <= 9)
        ):
            raise ValueError("min_f_score must be an integer between 0 and 9.")
        if altman_zone is not None and altman_zone not in ALTMAN_ZONES:
            raise ValueError(f"Unsupported altman_zone: {altman_zone!r}")
        if risk_tier is not None and risk_tier not in RISK_TIERS_ORDERED:
            raise ValueError(f"Unsupported risk_tier: {risk_tier!r}")
        if beneish_flag is not None and beneish_flag not in BENEISH_FLAGS:
            raise ValueError(f"Unsupported beneish_flag: {beneish_flag!r}")
        for label, prob in (
            ("max_distress_prob", max_distress_prob),
            ("min_distress_prob", min_distress_prob),
        ):
            if prob is not None and (
                isinstance(prob, bool)
                or not isinstance(prob, (int, float))
                or not math.isfinite(float(prob))
                or not (0.0 <= float(prob) <= 1.0)
            ):
                raise ValueError(f"{label} must be a finite float in [0.0, 1.0].")
        for label, count_bound in (
            ("max_red_flags", max_red_flags),
            ("min_red_flags", min_red_flags),
        ):
            if count_bound is not None and (
                isinstance(count_bound, bool)
                or not isinstance(count_bound, int)
                or count_bound < 0
            ):
                raise ValueError(f"{label} must be a non-negative integer.")
        if sort_by not in SCREENER_SORT_FIELDS:
            raise ValueError(f"Unsupported sort_by: {sort_by!r}")
        if sort_dir not in ("asc", "desc"):
            raise ValueError(f"Unsupported sort_dir: {sort_dir!r}")
        return q.strip().lower() if q and q.strip() else None

    @staticmethod
    def _resolve_screener_period_date(
        pinned: ParquetRepository,
        *,
        period_end_date: date | None,
        statement_scope: str,
        period_type: str,
        snap_key: tuple[object, ...] | None,
        connection: duckdb.DuckDBPyConnection | None,
    ) -> date | None:
        resolved_date = period_end_date
        if resolved_date is not None:
            return resolved_date
        period_cache_key = (
            (*snap_key, statement_scope, period_type) if snap_key is not None else None
        )
        if period_cache_key is not None:
            with _QUERY_CACHE_LOCK:
                _evict_stale_l1b_caches_locked(snap_key)
                resolved_date = _LATEST_RATIO_PERIOD_CACHE.get(period_cache_key)
                if resolved_date is not None:
                    _LATEST_RATIO_PERIOD_CACHE.move_to_end(period_cache_key)
        if resolved_date is None:
            dates_df = pinned.read_table(
                RATIOS_TABLE,
                columns=["period_end_date"],
                filters={
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                },
                connection=connection,
            )
            if dates_df.is_empty():
                return None
            raw_max = dates_df.get_column("period_end_date").max()
            if not isinstance(raw_max, date):
                return None
            resolved_date = raw_max
            if period_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    _LATEST_RATIO_PERIOD_CACHE[period_cache_key] = resolved_date
                    while len(_LATEST_RATIO_PERIOD_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _LATEST_RATIO_PERIOD_CACHE.popitem(last=False)
        return resolved_date

    @staticmethod
    def _build_screener_frame(
        pinned: ParquetRepository,
        *,
        avail_ratio_cols: set[str],
        resolved_date: date,
        period_type: str,
        statement_scope: str,
        icb_code_level2: str | None,
        min_f_score: int | None,
        altman_zone: str | None,
        risk_tier: str | None,
        beneish_flag: str | None,
        max_distress_prob: float | None,
        min_distress_prob: float | None,
        max_red_flags: int | None,
        min_red_flags: int | None,
        clean_q: str | None,
        sort_by: str,
        sort_dir: Literal["asc", "desc"],
        connection: duckdb.DuckDBPyConnection | None,
    ) -> pl.DataFrame:
        issuer_cols = [
            "issuer_id",
            "ticker",
            "company_name_vn",
            "exchange",
            "accounting_regime",
            "icb_code_level2",
            "icb_name_level2",
            "icb_code_level3",
            "icb_name_level3",
        ]
        issuer_filters: dict[str, object] | None = (
            {"icb_code_level2": icb_code_level2}
            if icb_code_level2 is not None
            else None
        )
        issuers = pinned.read_table(
            "issuers.parquet",
            columns=issuer_cols,
            filters=issuer_filters,
            connection=connection,
        )
        if issuers.is_empty():
            return pl.DataFrame(schema=SCREENER_SCHEMA)

        ratio_desired = (
            "issuer_id",
            "statement_scope",
            "period_type",
            "period_end_date",
            "taxonomy_version",
            "roe_total",
            "roa",
            "ebit_margin",
            "debt_to_equity",
            "interest_coverage",
            "accruals_to_assets",
            "core_operating_margin",
            "non_core_distortion_to_assets",
            "core_to_ebt_ratio",
        )
        ratio_cols = _select_existing_columns(ratio_desired, avail_ratio_cols)
        ratio_filters: dict[str, object] = {
            "statement_scope": statement_scope,
            "period_type": period_type,
            "period_end_date": resolved_date,
        }
        if icb_code_level2 is not None:
            ratio_filters["issuer_id"] = issuers["issuer_id"].unique().to_list()
        ratios = pinned.read_table(
            RATIOS_TABLE,
            columns=ratio_cols,
            filters=ratio_filters,
            connection=connection,
        )
        if ratios.is_empty():
            return pl.DataFrame(schema=SCREENER_SCHEMA)

        for forensic_col in (
            "core_operating_margin",
            "non_core_distortion_to_assets",
            "core_to_ebt_ratio",
        ):
            if forensic_col not in ratios.columns:
                ratios = ratios.with_columns(
                    pl.lit(None, dtype=pl.Float64).alias(forensic_col)
                )

        avail_rf_cols = _available_table_columns(
            pinned, RED_FLAGS_TABLE, connection=connection
        )
        if avail_rf_cols:
            rf_slice = pinned.read_table(
                RED_FLAGS_TABLE,
                columns=["issuer_id", "severity"],
                filters={
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "period_end_date": resolved_date,
                },
                connection=connection,
            )
            if rf_slice.is_empty():
                rf_counts = pl.DataFrame(
                    schema={
                        "issuer_id": pl.String,
                        "red_flag_count": pl.Int64,
                        "critical_red_flag_count": pl.Int64,
                    }
                )
            else:
                rf_counts = rf_slice.group_by("issuer_id").agg(
                    pl.len().cast(pl.Int64).alias("red_flag_count"),
                    (pl.col("severity") == "CRITICAL")
                    .sum()
                    .cast(pl.Int64)
                    .alias("critical_red_flag_count"),
                )
        else:
            rf_counts = pl.DataFrame(
                schema={
                    "issuer_id": pl.String,
                    "red_flag_count": pl.Int64,
                    "critical_red_flag_count": pl.Int64,
                }
            )

        ml_desired = (
            "issuer_id",
            "filing_id",
            "altman_z_double_prime",
            "altman_em_score",
            "altman_zone",
            "piotroski_f_score",
            "beneish_m8_score",
            "beneish_flag",
            "cluster_id",
            "cluster_archetype_name",
            "anomaly_flag",
            "distress_risk_score",
            "calibrated_distress_prob",
            "risk_tier",
            "risk_market_percentile",
            "market_base_rate",
            "relative_risk_lift",
            "model_role",
            "model_version",
        )
        ml_join_schema = pl.Schema(
            {name: ML_SCORES_SCHEMA[name] for name in ml_desired if name != "filing_id"}
        )
        avail_ml_cols = _available_table_columns(
            pinned, ML_SCORES_TABLE, connection=connection
        )
        if avail_ml_cols:
            ml_cols = _select_existing_columns(ml_desired, avail_ml_cols)
            ml_raw = pinned.read_table(
                ML_SCORES_TABLE,
                columns=ml_cols,
                filters={
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "period_end_date": resolved_date,
                },
                connection=connection,
            )
            if ml_raw.is_empty():
                ml_slice = pl.DataFrame(schema=ml_join_schema)
            else:
                sort_ml = (
                    ["issuer_id", "filing_id"]
                    if "filing_id" in ml_raw.columns
                    else ["issuer_id"]
                )
                ml_dedup = ml_raw.sort(sort_ml).unique(
                    subset=["issuer_id"], keep="last"
                )
                ml_slice = _pad_schema_columns(ml_dedup, ml_join_schema)
        else:
            ml_slice = pl.DataFrame(schema=ml_join_schema)

        joined = (
            ratios.join(issuers, on="issuer_id", how="inner")
            .join(rf_counts, on="issuer_id", how="left")
            .join(ml_slice, on="issuer_id", how="left")
            .with_columns(
                # Triggered alert counts from LEFT JOIN (0 alerts triggered, not a financial metric).
                pl.coalesce(pl.col("red_flag_count"), pl.lit(0, dtype=pl.Int64)).alias(
                    "red_flag_count"
                ),
                pl.coalesce(
                    pl.col("critical_red_flag_count"), pl.lit(0, dtype=pl.Int64)
                ).alias("critical_red_flag_count"),
            )
        )

        if min_f_score is not None:
            joined = joined.filter(
                pl.col("piotroski_f_score").is_not_null()
                & (pl.col("piotroski_f_score") >= min_f_score)
            )
        if altman_zone is not None:
            joined = joined.filter(pl.col("altman_zone") == altman_zone)
        if risk_tier is not None:
            joined = joined.filter(pl.col("risk_tier") == risk_tier)
        if beneish_flag is not None:
            joined = joined.filter(pl.col("beneish_flag") == beneish_flag)
        if max_distress_prob is not None:
            joined = joined.filter(
                pl.col("calibrated_distress_prob").is_not_null()
                & (pl.col("calibrated_distress_prob") <= float(max_distress_prob))
            )
        if min_distress_prob is not None:
            joined = joined.filter(
                pl.col("calibrated_distress_prob").is_not_null()
                & (pl.col("calibrated_distress_prob") >= float(min_distress_prob))
            )
        if max_red_flags is not None:
            joined = joined.filter(pl.col("red_flag_count") <= max_red_flags)
        if min_red_flags is not None:
            joined = joined.filter(pl.col("red_flag_count") >= min_red_flags)
        if clean_q is not None:
            joined = joined.filter(
                pl.col("ticker").str.to_lowercase().str.contains(clean_q, literal=True)
                | pl.col("company_name_vn")
                .fill_null("")
                .str.to_lowercase()
                .str.contains(clean_q, literal=True)
            )

        desc_flag = sort_dir == "desc"
        if sort_by == "calibrated_distress_prob" and desc_flag:
            sort_cols = ["calibrated_distress_prob", "red_flag_count", "ticker"]
            sort_desc = [True, True, False]
        elif sort_by == "ticker":
            sort_cols = ["ticker"]
            sort_desc = [desc_flag]
        else:
            sort_cols = [sort_by, "ticker"]
            sort_desc = [desc_flag, False]

        return _pad_schema_columns(joined, SCREENER_SCHEMA).sort(
            sort_cols,
            descending=sort_desc,
            nulls_last=True,
        )

    def screen_market(
        self,
        *,
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        statement_scope: str = "CONSOLIDATED",
        icb_code_level2: str | None = None,
        min_f_score: int | None = None,
        altman_zone: str | None = None,
        risk_tier: str | None = None,
        beneish_flag: str | None = None,
        max_distress_prob: float | None = None,
        min_distress_prob: float | None = None,
        max_red_flags: int | None = None,
        min_red_flags: int | None = None,
        q: str | None = None,
        sort_by: str = "calibrated_distress_prob",
        sort_dir: Literal["asc", "desc"] = "desc",
        limit: int = 50,
        offset: int = 0,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> tuple[pl.DataFrame, int]:
        """Historical cross-sectional market screener over the latest published snapshot.

        Uses ``LEFT JOIN`` onto ``ml_scores_and_explanations`` and ``accounting_red_flags``
        so screening degrades cleanly when a snapshot has no ML or red-flag tables.
        """
        _validate_pagination(limit, offset, max_limit=200)
        clean_q = self._validate_screener_filters(
            period_type=period_type,
            statement_scope=statement_scope,
            min_f_score=min_f_score,
            altman_zone=altman_zone,
            risk_tier=risk_tier,
            beneish_flag=beneish_flag,
            max_distress_prob=max_distress_prob,
            min_distress_prob=min_distress_prob,
            max_red_flags=max_red_flags,
            min_red_flags=min_red_flags,
            q=q,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )

        with self.repository.snapshot_session(connection=connection) as pinned:
            avail_ratio_cols = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            if not avail_ratio_cols:
                return pl.DataFrame(schema=SCREENER_SCHEMA), 0

            snap_key = pinned._snapshot_cache_key()
            resolved_date = self._resolve_screener_period_date(
                pinned,
                period_end_date=period_end_date,
                statement_scope=statement_scope,
                period_type=period_type,
                snap_key=snap_key,
                connection=connection,
            )
            if resolved_date is None:
                return pl.DataFrame(schema=SCREENER_SCHEMA), 0

            screener_cache_key = (
                (
                    *snap_key,
                    period_type,
                    resolved_date,
                    statement_scope,
                    icb_code_level2,
                    min_f_score,
                    altman_zone,
                    risk_tier,
                    beneish_flag,
                    float(max_distress_prob) if max_distress_prob is not None else None,
                    float(min_distress_prob) if min_distress_prob is not None else None,
                    max_red_flags,
                    min_red_flags,
                    clean_q,
                    sort_by,
                    sort_dir,
                    limit,
                    offset,
                )
                if snap_key is not None and _can_use_query_result_cache(pinned)
                else None
            )
            if screener_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    cached_screener = _SCREENER_CACHE.get(screener_cache_key)
                    if cached_screener is not None:
                        _SCREENER_CACHE.move_to_end(screener_cache_key)
                        return cached_screener[0], cached_screener[1]

            projected = self._build_screener_frame(
                pinned,
                avail_ratio_cols=avail_ratio_cols,
                resolved_date=resolved_date,
                period_type=period_type,
                statement_scope=statement_scope,
                icb_code_level2=icb_code_level2,
                min_f_score=min_f_score,
                altman_zone=altman_zone,
                risk_tier=risk_tier,
                beneish_flag=beneish_flag,
                max_distress_prob=max_distress_prob,
                min_distress_prob=min_distress_prob,
                max_red_flags=max_red_flags,
                min_red_flags=min_red_flags,
                clean_q=clean_q,
                sort_by=sort_by,
                sort_dir=sort_dir,
                connection=connection,
            )
            total_count = projected.height
            sliced = _wrap_cached_dicts_df(projected.slice(offset, limit))
            if screener_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    _SCREENER_CACHE[screener_cache_key] = (sliced, total_count)
                    while len(_SCREENER_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _SCREENER_CACHE.popitem(last=False)
            return sliced, total_count

    def export_screener(
        self,
        *,
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        statement_scope: str = "CONSOLIDATED",
        icb_code_level2: str | None = None,
        min_f_score: int | None = None,
        altman_zone: str | None = None,
        risk_tier: str | None = None,
        beneish_flag: str | None = None,
        max_distress_prob: float | None = None,
        min_distress_prob: float | None = None,
        max_red_flags: int | None = None,
        min_red_flags: int | None = None,
        q: str | None = None,
        sort_by: str = "calibrated_distress_prob",
        sort_dir: Literal["asc", "desc"] = "desc",
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        """Return 100% of filtered & sorted screener rows without pagination cap (UI-05a)."""
        clean_q = self._validate_screener_filters(
            period_type=period_type,
            statement_scope=statement_scope,
            min_f_score=min_f_score,
            altman_zone=altman_zone,
            risk_tier=risk_tier,
            beneish_flag=beneish_flag,
            max_distress_prob=max_distress_prob,
            min_distress_prob=min_distress_prob,
            max_red_flags=max_red_flags,
            min_red_flags=min_red_flags,
            q=q,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )

        with self.repository.snapshot_session(connection=connection) as pinned:
            avail_ratio_cols = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            if not avail_ratio_cols:
                return pl.DataFrame(schema=SCREENER_SCHEMA)

            snap_key = pinned._snapshot_cache_key()
            resolved_date = self._resolve_screener_period_date(
                pinned,
                period_end_date=period_end_date,
                statement_scope=statement_scope,
                period_type=period_type,
                snap_key=snap_key,
                connection=connection,
            )
            if resolved_date is None:
                return pl.DataFrame(schema=SCREENER_SCHEMA)

            return self._build_screener_frame(
                pinned,
                avail_ratio_cols=avail_ratio_cols,
                resolved_date=resolved_date,
                period_type=period_type,
                statement_scope=statement_scope,
                icb_code_level2=icb_code_level2,
                min_f_score=min_f_score,
                altman_zone=altman_zone,
                risk_tier=risk_tier,
                beneish_flag=beneish_flag,
                max_distress_prob=max_distress_prob,
                min_distress_prob=min_distress_prob,
                max_red_flags=max_red_flags,
                min_red_flags=min_red_flags,
                clean_q=clean_q,
                sort_by=sort_by,
                sort_dir=sort_dir,
                connection=connection,
            )

    def get_stress_test_snapshot_inputs(
        self,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        *,
        tickers: Sequence[str] | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        """Read exact required stress-test inputs and ICB Level 2 sector metadata for one period slice."""
        if statement_scope not in STATEMENT_SCOPES:
            raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
        if period_type not in PERIOD_TYPES:
            raise ValueError(f"Unsupported period_type: {period_type!r}")
        target_tickers: tuple[str, ...] | None = (
            tuple(
                dict.fromkeys(
                    t.strip().upper()
                    for t in tickers
                    if isinstance(t, str) and t.strip()
                )
            )
            if tickers is not None
            else None
        )

        def _filter_tickers(frame: pl.DataFrame) -> pl.DataFrame:
            if target_tickers is None:
                return frame
            if not target_tickers or frame.is_empty():
                return frame.clear()
            return frame.filter(pl.col("ticker").is_in(list(target_tickers)))

        with self.repository.snapshot_session(connection=connection) as pinned:
            avail_ratio_cols = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            if avail_ratio_cols is None:
                raise ContractViolationError(
                    "Active snapshot is missing financial_ratios table."
                )
            missing_stress_cols = [
                col for col in REQUIRED_STRESS_COLUMNS if col not in avail_ratio_cols
            ]
            if missing_stress_cols:
                raise ContractViolationError(
                    "Active snapshot is missing required stress-test intermediate columns: "
                    + ", ".join(missing_stress_cols)
                    + "."
                )

            snap_key = pinned._snapshot_cache_key()
            resolved_date = period_end_date
            if resolved_date is None:
                period_cache_key = (
                    (*snap_key, statement_scope, period_type)
                    if snap_key is not None
                    else None
                )
                if period_cache_key is not None:
                    with _QUERY_CACHE_LOCK:
                        _evict_stale_l1b_caches_locked(snap_key)
                        resolved_date = _LATEST_RATIO_PERIOD_CACHE.get(period_cache_key)
                        if resolved_date is not None:
                            _LATEST_RATIO_PERIOD_CACHE.move_to_end(period_cache_key)
                if resolved_date is None:
                    dates_df = pinned.read_table(
                        RATIOS_TABLE,
                        columns=["period_end_date"],
                        filters={
                            "statement_scope": statement_scope,
                            "period_type": period_type,
                        },
                        connection=connection,
                    )
                    if dates_df.is_empty():
                        return pl.DataFrame(
                            schema={
                                "issuer_id": pl.String,
                                "ticker": pl.String,
                                **{
                                    col: FINANCIAL_RATIOS_SCHEMA[col]
                                    for col in REQUIRED_STRESS_COLUMNS
                                },
                                "company_name_vn": pl.String,
                                "icb_code_level2": pl.String,
                                "icb_name_level2": pl.String,
                            }
                        )
                    raw_max = dates_df.get_column("period_end_date").max()
                    if not isinstance(raw_max, date):
                        raise ContractViolationError(
                            "Unable to resolve latest period_end_date for stress-test inputs."
                        )
                    resolved_date = raw_max
                    if period_cache_key is not None:
                        with _QUERY_CACHE_LOCK:
                            _evict_stale_l1b_caches_locked(snap_key)
                            _LATEST_RATIO_PERIOD_CACHE[period_cache_key] = resolved_date
                            while (
                                len(_LATEST_RATIO_PERIOD_CACHE) > _MAX_QUERY_CACHE_SIZE
                            ):
                                _LATEST_RATIO_PERIOD_CACHE.popitem(last=False)

            stress_cache_key = (
                (*snap_key, statement_scope, period_type, resolved_date)
                if snap_key is not None
                else None
            )
            if stress_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    cached_inputs = _STRESS_INPUTS_CACHE.get(stress_cache_key)
                    if cached_inputs is not None:
                        _STRESS_INPUTS_CACHE.move_to_end(stress_cache_key)
                        return _filter_tickers(cached_inputs.clone())

            stress_ratio_cols = list(
                dict.fromkeys(("issuer_id", "ticker", *REQUIRED_STRESS_COLUMNS))
            )
            ratios = pinned.read_table(
                RATIOS_TABLE,
                columns=stress_ratio_cols,
                filters={
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "period_end_date": resolved_date,
                },
                connection=connection,
            )
            if ratios.is_empty():
                return _filter_tickers(
                    ratios.with_columns(
                        pl.lit(None, dtype=pl.String).alias("company_name_vn"),
                        pl.lit(None, dtype=pl.String).alias("icb_code_level2"),
                        pl.lit(None, dtype=pl.String).alias("icb_name_level2"),
                    )
                )

            issuers = pinned.read_table(
                "issuers.parquet",
                columns=[
                    "issuer_id",
                    "company_name_vn",
                    "icb_code_level2",
                    "icb_name_level2",
                ],
                filters={"issuer_id": ratios["issuer_id"].unique().to_list()},
                connection=connection,
            )
            result = ratios.join(issuers, on="issuer_id", how="left").sort("ticker")
            if stress_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _evict_stale_l1b_caches_locked(snap_key)
                    _STRESS_INPUTS_CACHE[stress_cache_key] = result
                    while len(_STRESS_INPUTS_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _STRESS_INPUTS_CACHE.popitem(last=False)
            return _filter_tickers(result.clone())

    def get_meta_periods_and_sectors(
        self, *, connection: duckdb.DuckDBPyConnection | None = None
    ) -> tuple[str | None, dict[str, dict[str, list[date]]], list[SectorOptionDict]]:
        """Return available reporting dates per scope/type and ICB Level 2 sector counts."""
        available_periods: dict[str, dict[str, list[date]]] = {
            scope: {ptype: [] for ptype in PERIOD_TYPES} for scope in STATEMENT_SCOPES
        }
        with self.repository.snapshot_session(connection=connection) as pinned:
            meta = pinned.get_snapshot_metadata(connection=connection)
            raw_pub = meta.get("published_at")
            published_at = str(raw_pub) if isinstance(raw_pub, str) else None

            avail_ratio = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            if avail_ratio and {
                "statement_scope",
                "period_type",
                "period_end_date",
            }.issubset(avail_ratio):
                periods_df = (
                    pinned.read_table(
                        RATIOS_TABLE,
                        columns=["statement_scope", "period_type", "period_end_date"],
                        connection=connection,
                    )
                    .drop_nulls(["statement_scope", "period_type", "period_end_date"])
                    .filter(
                        pl.col("statement_scope").is_in(STATEMENT_SCOPES)
                        & pl.col("period_type").is_in(PERIOD_TYPES)
                    )
                    .unique()
                    .sort("period_end_date", descending=True)
                )
                if not periods_df.is_empty():
                    for key_tuple, sub in periods_df.partition_by(
                        ["statement_scope", "period_type"],
                        as_dict=True,
                        maintain_order=True,
                    ).items():
                        scope = str(key_tuple[0])
                        ptype = str(key_tuple[1])
                        if (
                            scope in available_periods
                            and ptype in available_periods[scope]
                        ):
                            available_periods[scope][ptype] = [
                                pdate
                                for pdate in sub.get_column("period_end_date").to_list()
                                if isinstance(pdate, date)
                            ]

            avail_iss = _available_table_columns(
                pinned, "issuers.parquet", connection=connection
            )
            sectors: list[SectorOptionDict] = []
            if avail_iss and {"icb_code_level2", "icb_name_level2"}.issubset(avail_iss):
                iss_df = pinned.read_table(
                    "issuers.parquet",
                    columns=["icb_code_level2", "icb_name_level2"],
                    connection=connection,
                ).filter(pl.col("icb_code_level2").is_not_null())
                if not iss_df.is_empty():
                    grouped = (
                        iss_df.group_by("icb_code_level2")
                        .agg(
                            pl.col("icb_name_level2")
                            .drop_nulls()
                            .first()
                            .alias("icb_name_level2"),
                            pl.len().cast(pl.Int64).alias("issuer_count"),
                        )
                        .with_columns(
                            pl.col("icb_code_level2").cast(pl.String),
                            pl.when(
                                pl.col("icb_name_level2").is_not_null()
                                & (pl.col("icb_name_level2").cast(pl.String) != "")
                            )
                            .then(pl.col("icb_name_level2").cast(pl.String))
                            .otherwise(pl.col("icb_code_level2").cast(pl.String))
                            .alias("icb_name_level2"),
                        )
                        .sort(["icb_name_level2", "icb_code_level2"])
                    )
                    sectors = [
                        {
                            "icb_code_level2": str(code),
                            "icb_name_level2": str(name),
                            "issuer_count": int(cnt),
                        }
                        for code, name, cnt in zip(
                            grouped.get_column("icb_code_level2").to_list(),
                            grouped.get_column("icb_name_level2").to_list(),
                            grouped.get_column("issuer_count").to_list(),
                            strict=True,
                        )
                    ]
        return published_at, available_periods, sectors

    def get_company_benchmarks_summary(
        self,
        ticker: str,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        metrics: Sequence[str] | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> tuple[str, date, list[dict[str, object]]]:
        """Fetch multi-metric peer benchmarks and 8-period sparklines in a single snapshot read."""
        if statement_scope not in STATEMENT_SCOPES:
            raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
        if period_type not in PERIOD_TYPES:
            raise ValueError(f"Unsupported period_type: {period_type!r}")
        selected_metrics = tuple(metrics) if metrics else OVERVIEW_CORE_RATIOS
        for name in selected_metrics:
            if name not in RATIO_LINEAGE_CATALOG or name not in RATIO_DICTIONARY_META:
                raise ValueError(f"Unsupported financial ratio: {name!r}")

        clean_ticker = ticker.strip().upper()
        with self.repository.snapshot_session(connection=connection) as pinned:
            issuer_row = _resolve_issuer_row(
                pinned,
                clean_ticker,
                columns=("issuer_id", "ticker"),
                connection=connection,
            )
            issuer_id = str(issuer_row["issuer_id"])

            avail_ratio_cols = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            if not avail_ratio_cols:
                raise ValueError(
                    f"No ratio periods found for {clean_ticker} {statement_scope} {period_type}."
                )
            ratio_cols = _select_existing_columns(
                ("period_end_date", *selected_metrics), avail_ratio_cols
            )
            ratios = pinned.read_table(
                RATIOS_TABLE,
                columns=ratio_cols,
                filters={
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                },
                connection=connection,
            ).sort("period_end_date")
            if ratios.is_empty():
                raise ValueError(
                    f"No ratio periods found for {clean_ticker} {statement_scope} {period_type}."
                )

            resolved_date = period_end_date
            if resolved_date is None:
                raw_latest = ratios.get_column("period_end_date").max()
                if not isinstance(raw_latest, date):
                    raise ValueError(
                        f"No valid period_end_date found for {clean_ticker}."
                    )
                resolved_date = raw_latest

            hist_window = ratios.filter(
                pl.col("period_end_date") <= resolved_date
            ).tail(8)
            latest_ratio_slice = hist_window.filter(
                pl.col("period_end_date") == resolved_date
            )
            latest_ratio_row: dict[str, object] = (
                latest_ratio_slice.row(0, named=True)
                if not latest_ratio_slice.is_empty()
                else {}
            )

            bench_by_metric: dict[str, dict[str, object]] = {}
            avail_bench_cols = _available_table_columns(
                pinned, BENCHMARKS_TABLE, connection=connection
            )
            if avail_bench_cols:
                b_cols = _select_existing_columns(
                    (
                        "metric_or_ratio_name",
                        "value",
                        "peer_median",
                        "peer_p25",
                        "peer_p75",
                        "percentile_rank",
                        "robust_z_score",
                        "peer_group_level",
                        "icb_code_used",
                        "peer_count",
                    ),
                    avail_bench_cols,
                )
                bench_df = pinned.read_table(
                    BENCHMARKS_TABLE,
                    columns=b_cols,
                    filters={
                        "issuer_id": issuer_id,
                        "statement_scope": statement_scope,
                        "period_type": period_type,
                        "period_end_date": resolved_date,
                        "metric_or_ratio_name": list(selected_metrics),
                    },
                    connection=connection,
                )
                if not bench_df.is_empty():
                    for key_tuple, sub in bench_df.partition_by(
                        "metric_or_ratio_name", as_dict=True
                    ).items():
                        bench_by_metric[str(key_tuple[0])] = sub.row(0, named=True)

            def _opt_finite(raw: object) -> float | None:
                if raw is None or isinstance(raw, bool):
                    return None
                if isinstance(raw, (int, float)):
                    val = float(raw)
                    return val if math.isfinite(val) else None
                return None

            spark_cols = [m for m in selected_metrics if m in hist_window.columns]
            cleaned_hist = (
                hist_window.select(
                    pl.when(pl.col(c).cast(pl.Float64, strict=False).is_finite())
                    .then(pl.col(c).cast(pl.Float64, strict=False))
                    .otherwise(None)
                    .alias(c)
                    for c in spark_cols
                )
                if spark_cols
                else pl.DataFrame()
            )

            items: list[dict[str, object]] = []
            for metric_name in selected_metrics:
                spec = RATIO_LINEAGE_CATALOG[metric_name]
                meta = RATIO_DICTIONARY_META[metric_name]
                b_row = bench_by_metric.get(metric_name, {})
                sparkline: list[float | None] = (
                    cleaned_hist.get_column(metric_name).to_list()
                    if metric_name in cleaned_hist.columns
                    else []
                )
                company_val = _opt_finite(b_row.get("value"))
                if company_val is None:
                    company_val = _opt_finite(latest_ratio_row.get(metric_name))
                raw_peer_count = b_row.get("peer_count")
                peer_count = (
                    int(raw_peer_count) if isinstance(raw_peer_count, int) else 0
                )
                items.append(
                    {
                        "metric_name": metric_name,
                        "display_name_vn": spec.display_name_vn,
                        "category": meta.category,
                        "unit": meta.unit,
                        "format_type": meta.format_type,
                        "direction": meta.direction,
                        "company_value": company_val,
                        "peer_median": _opt_finite(b_row.get("peer_median")),
                        "peer_p25": _opt_finite(b_row.get("peer_p25")),
                        "peer_p75": _opt_finite(b_row.get("peer_p75")),
                        "percentile_rank": _opt_finite(b_row.get("percentile_rank")),
                        "robust_z_score": _opt_finite(b_row.get("robust_z_score")),
                        "peer_group_level": (
                            str(b_row["peer_group_level"])
                            if b_row.get("peer_group_level") is not None
                            else None
                        ),
                        "icb_code_used": (
                            str(b_row["icb_code_used"])
                            if b_row.get("icb_code_used") is not None
                            else None
                        ),
                        "peer_count": peer_count,
                        "sparkline": sparkline,
                    }
                )
        return issuer_id, resolved_date, items

    def get_company_statements(
        self,
        ticker: str,
        *,
        statement_type: str = "INCOME_STATEMENT",
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        periods_limit: int = 8,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> tuple[str, list[dict[str, object]], list[dict[str, object]]]:
        """Return multi-period normalized VAS statement lines for one issuer."""
        if statement_type not in STATEMENT_TYPES:
            raise ValueError(f"Unsupported statement_type: {statement_type!r}")
        if statement_scope not in STATEMENT_SCOPES:
            raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
        if period_type not in PERIOD_TYPES:
            raise ValueError(f"Unsupported period_type: {period_type!r}")
        if isinstance(periods_limit, bool) or not (1 <= periods_limit <= 20):
            raise ValueError("periods_limit must be an integer between 1 and 20.")

        clean_ticker = ticker.strip().upper()
        catalog = load_statement_metric_catalog()
        type_metric_ids = [
            mid
            for mid, mmeta in catalog.items()
            if mmeta.statement_type == statement_type
        ]

        with self.repository.snapshot_session(connection=connection) as pinned:
            issuer_row = _resolve_issuer_row(
                pinned,
                clean_ticker,
                columns=("issuer_id", "ticker"),
                connection=connection,
            )
            issuer_id = str(issuer_row["issuer_id"])

            facts_table = "financial_facts.parquet"
            avail_cols = _available_table_columns(
                pinned, facts_table, connection=connection
            )
            if not avail_cols:
                facts_table = "latest_facts.parquet"
                avail_cols = _available_table_columns(
                    pinned, facts_table, connection=connection
                )
            if not avail_cols:
                return issuer_id, [], []

            desired_cols = (
                "metric_id",
                "statement_type",
                "period_end_date",
                "fiscal_year",
                "fiscal_quarter",
                "taxonomy_version",
                "audit_status",
                "revision_no",
                "filing_id",
                "value",
                "unit",
                "comparable_across_taxonomy",
            )
            cols = _select_existing_columns(desired_cols, avail_cols)
            filters: dict[str, object] = {
                "issuer_id": issuer_id,
                "statement_scope": statement_scope,
                "period_type": period_type,
            }
            if "statement_type" in avail_cols:
                filters["statement_type"] = statement_type
            elif type_metric_ids:
                filters["metric_id"] = type_metric_ids

            facts = pinned.read_table(
                facts_table,
                columns=cols,
                filters=filters,
                connection=connection,
            )
            if facts.is_empty():
                return issuer_id, [], []

            sort_keys = [
                col
                for col in ("period_end_date", "metric_id", "revision_no", "filing_id")
                if col in facts.columns
            ]
            if sort_keys:
                facts = facts.sort(sort_keys)
            facts = facts.unique(subset=["metric_id", "period_end_date"], keep="last")

            recent_dates = (
                facts.select("period_end_date")
                .drop_nulls()
                .unique()
                .sort("period_end_date", descending=True)
                .head(periods_limit)
                .get_column("period_end_date")
                .to_list()
            )
            valid_facts = facts.filter(
                pl.col("period_end_date").is_in(recent_dates)
                & pl.col("period_end_date").is_not_null()
            )
            if valid_facts.is_empty():
                return issuer_id, [], []

            fy_expr = (
                pl.coalesce(
                    pl.col("fiscal_year").cast(pl.Int64, strict=False),
                    pl.col("period_end_date").dt.year().cast(pl.Int64),
                )
                if "fiscal_year" in valid_facts.columns
                else pl.col("period_end_date").dt.year().cast(pl.Int64)
            )
            fq_expr = (
                pl.col("fiscal_quarter").cast(pl.Int64, strict=False)
                if "fiscal_quarter" in valid_facts.columns
                else pl.lit(None, dtype=pl.Int64)
            )
            derived_fq = (
                (pl.col("period_end_date").dt.month().cast(pl.Int64) - 1) // 3
            ) + 1
            label_expr = (
                (pl.lit("Năm ") + fy_expr.cast(pl.String))
                if period_type == "ANNUAL"
                else (
                    pl.lit("Q")
                    + pl.coalesce(
                        pl.when(fq_expr != 0).then(fq_expr).otherwise(None),
                        derived_fq,
                    ).cast(pl.String)
                    + pl.lit("/")
                    + fy_expr.cast(pl.String)
                )
            )
            periods_df = (
                valid_facts.unique(subset=["period_end_date"], keep="first")
                .select(
                    pl.col("period_end_date"),
                    fy_expr.alias("fiscal_year"),
                    fq_expr.alias("fiscal_quarter"),
                    label_expr.alias("label"),
                    (
                        pl.col("taxonomy_version").cast(pl.String)
                        if "taxonomy_version" in valid_facts.columns
                        else pl.lit(None, dtype=pl.String)
                    ).alias("taxonomy_version"),
                    (
                        pl.col("audit_status").cast(pl.String)
                        if "audit_status" in valid_facts.columns
                        else pl.lit(None, dtype=pl.String)
                    ).alias("audit_status"),
                )
                .sort("period_end_date", descending=True)
            )
            periods_out = cast(list[dict[str, object]], periods_df.to_dicts())

            val_f64 = pl.col("value").cast(pl.Float64, strict=False)
            clean_facts = valid_facts.select(
                pl.col("metric_id").cast(pl.String).alias("metric_id"),
                pl.col("period_end_date").dt.strftime("%Y-%m-%d").alias("period_iso"),
                pl.when(val_f64.is_finite())
                .then(val_f64)
                .otherwise(None)
                .alias("value"),
                (
                    pl.col("unit").cast(pl.String)
                    if "unit" in valid_facts.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("unit"),
                (
                    pl.col("comparable_across_taxonomy").cast(pl.Boolean, strict=False)
                    if "comparable_across_taxonomy" in valid_facts.columns
                    else pl.lit(None, dtype=pl.Boolean)
                ).alias("comparable_across_taxonomy"),
            )

            rows_out: list[dict[str, object]] = []
            for key_tuple, sub in clean_facts.partition_by(
                "metric_id", as_dict=True, maintain_order=True
            ).items():
                mid = str(key_tuple[0])
                mmeta = catalog.get(mid)
                values: dict[str, float | None] = dict(
                    zip(
                        sub.get_column("period_iso").to_list(),
                        sub.get_column("value").to_list(),
                        strict=True,
                    )
                )
                if mmeta is not None:
                    unit_val = mmeta.unit
                    comp_val = mmeta.comparable_across_taxonomy
                else:
                    non_null_units = sub.get_column("unit").drop_nulls()
                    unit_val = (
                        str(non_null_units[0])
                        if not non_null_units.is_empty()
                        else "VND"
                    )
                    non_null_comp = sub.get_column(
                        "comparable_across_taxonomy"
                    ).drop_nulls()
                    comp_val = (
                        bool(non_null_comp[0]) if not non_null_comp.is_empty() else True
                    )
                rows_out.append(
                    {
                        "metric_id": mid,
                        "display_name_vn": mmeta.display_name_vn if mmeta else mid,
                        "tt200_code": mmeta.tt200_code if mmeta else None,
                        "tt99_code": mmeta.tt99_code if mmeta else None,
                        "unit": unit_val,
                        "is_total": mmeta.is_total if mmeta else False,
                        "comparable_across_taxonomy": comp_val,
                        "sort_order": mmeta.sort_order if mmeta else 99_999,
                        "values": values,
                    }
                )
            rows_out.sort(
                key=lambda r: (int(cast(int, r["sort_order"])), str(r["metric_id"]))
            )
        return issuer_id, periods_out, rows_out

    def get_company_filings(
        self,
        ticker: str,
        *,
        statement_scope: str | None = None,
        limit: int = 100,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> tuple[str, list[dict[str, object]]]:
        """Return audit status, audit opinion, and revision history for an issuer."""
        if statement_scope is not None and statement_scope not in STATEMENT_SCOPES:
            raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
        if isinstance(limit, bool) or not (1 <= limit <= 200):
            raise ValueError("limit must be an integer between 1 and 200.")

        clean_ticker = ticker.strip().upper()
        with self.repository.snapshot_session(connection=connection) as pinned:
            issuer_row = _resolve_issuer_row(
                pinned,
                clean_ticker,
                columns=("issuer_id", "ticker"),
                connection=connection,
            )
            issuer_id = str(issuer_row["issuer_id"])

            source_table: str | None = FILINGS_MANIFEST_TABLE
            avail_cols = _available_table_columns(
                pinned, FILINGS_MANIFEST_TABLE, connection=connection
            )
            if not avail_cols:
                source_table = "financial_facts.parquet"
                avail_cols = _available_table_columns(
                    pinned, source_table, connection=connection
                )
            if not avail_cols or not source_table:
                return issuer_id, []

            desired = (
                "filing_id",
                "statement_type",
                "statement_scope",
                "period_type",
                "period_end_date",
                "taxonomy_version",
                "published_at",
                "published_at_basis",
                "effective_available_at",
                "audit_status",
                "audit_opinion",
                "revision_no",
                "revision_reason",
                "source_provider",
            )
            cols = _select_existing_columns(desired, avail_cols)
            filters: dict[str, object] = {"issuer_id": issuer_id}
            if statement_scope is not None and "statement_scope" in avail_cols:
                filters["statement_scope"] = statement_scope

            df = pinned.read_table(
                source_table,
                columns=cols,
                filters=filters,
                connection=connection,
            )
            if df.is_empty():
                return issuer_id, []

            unique_subset = [
                col for col in ("filing_id", "statement_type") if col in df.columns
            ]
            if unique_subset:
                df = df.unique(subset=unique_subset, keep="last")
            sort_cols = [
                col
                for col in ("period_end_date", "revision_no", "filing_id")
                if col in df.columns
            ]
            sort_desc = [col in ("period_end_date", "revision_no") for col in sort_cols]
            if sort_cols:
                df = df.sort(sort_cols, descending=sort_desc, nulls_last=True)

            head_df = df.head(limit)
            rev_expr = (
                pl.col("revision_no").cast(pl.Int64, strict=False)
                if "revision_no" in head_df.columns
                else pl.lit(None, dtype=pl.Int64)
            )
            filing_expr = (
                pl.coalesce(pl.col("filing_id").cast(pl.String), pl.lit(""))
                if "filing_id" in head_df.columns
                else pl.lit("")
            )
            out_df = head_df.select(
                filing_expr.alias("filing_id"),
                (
                    pl.col("statement_type")
                    if "statement_type" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("statement_type"),
                (
                    pl.col("statement_scope")
                    if "statement_scope" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("statement_scope"),
                (
                    pl.col("period_type")
                    if "period_type" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("period_type"),
                (
                    pl.col("period_end_date")
                    if "period_end_date" in head_df.columns
                    else pl.lit(None, dtype=pl.Date)
                ).alias("period_end_date"),
                (
                    pl.col("taxonomy_version")
                    if "taxonomy_version" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("taxonomy_version"),
                (
                    pl.col("published_at")
                    if "published_at" in head_df.columns
                    else pl.lit(None, dtype=pl.Date)
                ).alias("published_at"),
                (
                    pl.col("published_at_basis")
                    if "published_at_basis" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("published_at_basis"),
                (
                    pl.col("effective_available_at")
                    if "effective_available_at" in head_df.columns
                    else pl.lit(None, dtype=pl.Date)
                ).alias("effective_available_at"),
                (
                    pl.col("audit_status")
                    if "audit_status" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("audit_status"),
                (
                    pl.col("audit_opinion")
                    if "audit_opinion" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("audit_opinion"),
                rev_expr.alias("revision_no"),
                (
                    pl.col("revision_reason")
                    if "revision_reason" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("revision_reason"),
                (rev_expr.is_not_null() & (rev_expr > 1)).alias("has_restatement"),
                (
                    pl.col("source_provider")
                    if "source_provider" in head_df.columns
                    else pl.lit(None, dtype=pl.String)
                ).alias("source_provider"),
            )
            items = cast(list[dict[str, object]], out_df.to_dicts())
        return issuer_id, items

    @staticmethod
    def _compute_market_changes_from_pinned(
        pinned: ParquetRepository,
        *,
        statement_scope: str,
        period_type: str,
        target_date: date | None = None,
        tickers: Sequence[str] | None = None,
        limit: int = 30,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> list[dict[str, object]]:
        """Compute period-over-period Altman/distress/red-flag changes between T and T-1."""
        issuer_cols = ["issuer_id", "ticker", "company_name_vn", "icb_name_level2"]
        issuer_filters: dict[str, object] | None = (
            {"ticker": list(tickers)} if tickers else None
        )
        issuers = pinned.read_table(
            "issuers.parquet",
            columns=issuer_cols,
            filters=issuer_filters,
            connection=connection,
        )
        if issuers.is_empty():
            return []
        issuer_ids = issuers["issuer_id"].unique().to_list()

        avail_ratio_cols = _available_table_columns(
            pinned, RATIOS_TABLE, connection=connection
        )
        if not avail_ratio_cols:
            return []
        ratio_filters: dict[str, object] = {
            "statement_scope": statement_scope,
            "period_type": period_type,
        }
        if tickers:
            ratio_filters["issuer_id"] = issuer_ids
        ratio_dates_df = pinned.read_table(
            RATIOS_TABLE,
            columns=["issuer_id", "period_end_date"],
            filters=ratio_filters,
            connection=connection,
        ).filter(pl.col("period_end_date").is_not_null())
        if target_date is not None:
            ratio_dates_df = ratio_dates_df.filter(
                pl.col("period_end_date") <= target_date
            )
        if ratio_dates_df.is_empty():
            return []

        pairs_df = (
            ratio_dates_df.unique()
            .sort(["issuer_id", "period_end_date"], descending=[False, True])
            .group_by("issuer_id", maintain_order=True)
            .agg(
                pl.col("period_end_date").first().alias("curr_period_end_date"),
                pl.when(pl.len() > 1)
                .then(pl.col("period_end_date").get(1))
                .otherwise(None)
                .cast(pl.Date)
                .alias("prev_period_end_date"),
            )
        )
        if target_date is not None:
            pairs_df = pairs_df.filter(pl.col("curr_period_end_date") == target_date)
        if pairs_df.is_empty():
            return []

        date_list = (
            pl.concat(
                [
                    pairs_df.select(pl.col("curr_period_end_date").alias("d")),
                    pairs_df.select(
                        pl.col("prev_period_end_date").alias("d")
                    ).drop_nulls(),
                ]
            )
            .unique()
            .sort("d")
            .get_column("d")
            .to_list()
        )

        ml_pair_schema = pl.Schema(
            {
                "issuer_id": pl.String,
                "period_end_date": pl.Date,
                "altman_zone": pl.String,
                "calibrated_distress_prob": pl.Float64,
                "risk_tier": pl.String,
                "risk_market_percentile": pl.Float64,
                "market_base_rate": pl.Float64,
                "relative_risk_lift": pl.Float64,
            }
        )
        avail_ml_cols = _available_table_columns(
            pinned, ML_SCORES_TABLE, connection=connection
        )
        ml_dedup = pl.DataFrame(schema=ml_pair_schema)
        if avail_ml_cols:
            ml_cols = _select_existing_columns(
                (
                    "issuer_id",
                    "period_end_date",
                    "altman_zone",
                    "calibrated_distress_prob",
                    "risk_tier",
                    "risk_market_percentile",
                    "market_base_rate",
                    "relative_risk_lift",
                    "filing_id",
                ),
                avail_ml_cols,
            )
            ml_filters: dict[str, object] = {
                "statement_scope": statement_scope,
                "period_type": period_type,
                "period_end_date": date_list,
            }
            if tickers:
                ml_filters["issuer_id"] = issuer_ids
            ml_df = pinned.read_table(
                ML_SCORES_TABLE,
                columns=ml_cols,
                filters=ml_filters,
                connection=connection,
            ).filter(pl.col("period_end_date").is_not_null())
            if not ml_df.is_empty():
                sort_ml = [
                    c
                    for c in ("issuer_id", "period_end_date", "filing_id")
                    if c in ml_df.columns
                ]
                ml_dedup = _pad_schema_columns(
                    ml_df.sort(sort_ml).unique(
                        subset=["issuer_id", "period_end_date"], keep="last"
                    ),
                    ml_pair_schema,
                )
                float_ml_cols = [
                    "calibrated_distress_prob",
                    "risk_market_percentile",
                    "market_base_rate",
                    "relative_risk_lift",
                ]
                ml_dedup = ml_dedup.with_columns(
                    pl.when(pl.col(c).is_finite())
                    .then(pl.col(c))
                    .otherwise(None)
                    .alias(c)
                    for c in float_ml_cols
                )

        rf_agg = pl.DataFrame(
            schema={
                "issuer_id": pl.String,
                "period_end_date": pl.Date,
                "flags": pl.List(pl.String),
            }
        )
        avail_rf_cols = _available_table_columns(
            pinned, RED_FLAGS_TABLE, connection=connection
        )
        if avail_rf_cols:
            rf_filters: dict[str, object] = {
                "statement_scope": statement_scope,
                "period_type": period_type,
                "period_end_date": date_list,
            }
            if tickers:
                rf_filters["issuer_id"] = issuer_ids
            rf_df = pinned.read_table(
                RED_FLAGS_TABLE,
                columns=["issuer_id", "period_end_date", "flag_code"],
                filters=rf_filters,
                connection=connection,
            ).filter(pl.col("period_end_date").is_not_null())
            if not rf_df.is_empty():
                rf_agg = rf_df.group_by(["issuer_id", "period_end_date"]).agg(
                    pl.col("flag_code").cast(pl.String).unique().sort().alias("flags")
                )

        curr_ml_df = ml_dedup.select(
            pl.col("issuer_id"),
            pl.col("period_end_date").alias("curr_period_end_date"),
            pl.col("altman_zone").alias("altman_zone_curr"),
            pl.col("calibrated_distress_prob").alias("distress_prob_curr"),
            pl.col("risk_tier").alias("risk_tier_curr"),
            pl.col("risk_market_percentile").alias("risk_market_percentile_curr"),
            pl.col("market_base_rate").alias("market_base_rate_curr"),
            pl.col("relative_risk_lift").alias("relative_risk_lift_curr"),
        )
        prev_ml_df = ml_dedup.select(
            pl.col("issuer_id"),
            pl.col("period_end_date").alias("prev_period_end_date"),
            pl.col("altman_zone").alias("altman_zone_prev"),
            pl.col("calibrated_distress_prob").alias("distress_prob_prev"),
            pl.col("risk_tier").alias("risk_tier_prev"),
        )
        curr_rf_df = rf_agg.select(
            pl.col("issuer_id"),
            pl.col("period_end_date").alias("curr_period_end_date"),
            pl.col("flags").alias("_curr_flags"),
        )
        prev_rf_df = rf_agg.select(
            pl.col("issuer_id"),
            pl.col("period_end_date").alias("prev_period_end_date"),
            pl.col("flags").alias("_prev_flags"),
        )

        empty_flags = pl.lit([], dtype=pl.List(pl.String))
        joined = (
            pairs_df.join(issuers, on="issuer_id", how="left")
            .join(curr_ml_df, on=["issuer_id", "curr_period_end_date"], how="left")
            .join(prev_ml_df, on=["issuer_id", "prev_period_end_date"], how="left")
            .join(curr_rf_df, on=["issuer_id", "curr_period_end_date"], how="left")
            .join(prev_rf_df, on=["issuer_id", "prev_period_end_date"], how="left")
            .with_columns(
                pl.coalesce(
                    pl.when(pl.col("ticker").is_not_null() & (pl.col("ticker") != ""))
                    .then(pl.col("ticker"))
                    .otherwise(None),
                    pl.col("issuer_id"),
                ).alias("ticker"),
                pl.coalesce(pl.col("_curr_flags"), empty_flags).alias("_curr_flags"),
                pl.coalesce(pl.col("_prev_flags"), empty_flags).alias("_prev_flags"),
            )
            .with_columns(
                (
                    pl.when(
                        pl.col("distress_prob_curr").is_not_null()
                        & pl.col("distress_prob_prev").is_not_null()
                    )
                    .then(pl.col("distress_prob_curr") - pl.col("distress_prob_prev"))
                    .otherwise(None)
                    .cast(pl.Float64)
                ).alias("distress_prob_delta"),
                pl.col("_curr_flags")
                .list.len()
                .cast(pl.Int64)
                .alias("red_flag_count_curr"),
                pl.when(pl.col("prev_period_end_date").is_not_null())
                .then(pl.col("_prev_flags").list.len().cast(pl.Int64))
                .otherwise(pl.lit(0, dtype=pl.Int64))
                .alias("red_flag_count_prev"),
                pl.col("_curr_flags")
                .list.set_difference(pl.col("_prev_flags"))
                .list.sort()
                .alias("new_red_flag_codes"),
                pl.when(pl.col("prev_period_end_date").is_not_null())
                .then(
                    pl.col("_prev_flags")
                    .list.set_difference(pl.col("_curr_flags"))
                    .list.sort()
                )
                .otherwise(empty_flags)
                .alias("resolved_red_flag_codes"),
            )
            .with_columns(
                pl.col("new_red_flag_codes")
                .list.len()
                .cast(pl.Int64)
                .alias("_new_flag_count"),
                (
                    (
                        pl.col("altman_zone_curr").is_not_null()
                        & pl.col("altman_zone_prev").is_not_null()
                        & (
                            pl.col("altman_zone_curr").replace_strict(
                                _ALTMAN_SEVERITY_RANK,
                                default=-1,
                                return_dtype=pl.Int64,
                            )
                            > pl.col("altman_zone_prev").replace_strict(
                                _ALTMAN_SEVERITY_RANK,
                                default=-1,
                                return_dtype=pl.Int64,
                            )
                        )
                    )
                    | (
                        pl.col("risk_tier_curr").is_not_null()
                        & pl.col("risk_tier_prev").is_not_null()
                        & (
                            pl.col("risk_tier_curr").replace_strict(
                                _RISK_TIER_SEVERITY_RANK,
                                default=-1,
                                return_dtype=pl.Int64,
                            )
                            > pl.col("risk_tier_prev").replace_strict(
                                _RISK_TIER_SEVERITY_RANK,
                                default=-1,
                                return_dtype=pl.Int64,
                            )
                        )
                    )
                    | (
                        pl.col("distress_prob_delta").is_not_null()
                        & (pl.col("distress_prob_delta") >= 0.003)
                    )
                    | (pl.col("new_red_flag_codes").list.len() > 0)
                ).alias("deteriorated"),
            )
            .sort(
                ["deteriorated", "_new_flag_count", "distress_prob_delta", "ticker"],
                descending=[True, True, True, False],
                nulls_last=True,
            )
            .head(limit)
            .select(
                "issuer_id",
                "ticker",
                "company_name_vn",
                "icb_name_level2",
                "prev_period_end_date",
                "curr_period_end_date",
                "altman_zone_prev",
                "altman_zone_curr",
                "distress_prob_prev",
                "distress_prob_curr",
                "distress_prob_delta",
                "risk_tier_prev",
                "risk_tier_curr",
                "risk_market_percentile_curr",
                "market_base_rate_curr",
                "relative_risk_lift_curr",
                "red_flag_count_prev",
                "red_flag_count_curr",
                "new_red_flag_codes",
                "resolved_red_flag_codes",
                "deteriorated",
            )
        )
        return cast(list[dict[str, object]], joined.to_dicts())

    def get_sector_risk_summary(
        self,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> SectorRiskSummaryDict:
        """Aggregate market-wide and ICB Level 2 risk indicators for the overview heatmap."""
        if statement_scope not in STATEMENT_SCOPES:
            raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
        if period_type not in PERIOD_TYPES:
            raise ValueError(f"Unsupported period_type: {period_type!r}")

        with self.repository.snapshot_session(connection=connection) as pinned:
            avail_ratio_cols = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            if not avail_ratio_cols:
                return {
                    "period_end_date": period_end_date,
                    "total_issuers": 0,
                    "safe_zone_count": 0,
                    "grey_zone_count": 0,
                    "distress_zone_count": 0,
                    "beneish_flagged_count": 0,
                    "high_red_flag_count": 0,
                    "sectors": [],
                    "top_deteriorating": [],
                }

            resolved_date = period_end_date
            if resolved_date is None:
                dates_df = pinned.read_table(
                    RATIOS_TABLE,
                    columns=["period_end_date"],
                    filters={
                        "statement_scope": statement_scope,
                        "period_type": period_type,
                    },
                    connection=connection,
                )
                raw_max = (
                    dates_df.get_column("period_end_date").max()
                    if not dates_df.is_empty()
                    else None
                )
                if not isinstance(raw_max, date):
                    return {
                        "period_end_date": None,
                        "total_issuers": 0,
                        "safe_zone_count": 0,
                        "grey_zone_count": 0,
                        "distress_zone_count": 0,
                        "beneish_flagged_count": 0,
                        "high_red_flag_count": 0,
                        "sectors": [],
                        "top_deteriorating": [],
                    }
                resolved_date = raw_max

            ratio_cols = _select_existing_columns(
                ("issuer_id", "total_debt"), avail_ratio_cols
            )
            ratios = pinned.read_table(
                RATIOS_TABLE,
                columns=ratio_cols,
                filters={
                    "statement_scope": statement_scope,
                    "period_type": period_type,
                    "period_end_date": resolved_date,
                },
                connection=connection,
            )
            if ratios.is_empty():
                return {
                    "period_end_date": resolved_date,
                    "total_issuers": 0,
                    "safe_zone_count": 0,
                    "grey_zone_count": 0,
                    "distress_zone_count": 0,
                    "beneish_flagged_count": 0,
                    "high_red_flag_count": 0,
                    "sectors": [],
                    "top_deteriorating": [],
                }
            if "total_debt" not in ratios.columns:
                ratios = ratios.with_columns(
                    pl.lit(0.0, dtype=pl.Float64).alias("total_debt")
                )

            issuers = pinned.read_table(
                "issuers.parquet",
                columns=["issuer_id", "icb_code_level2", "icb_name_level2"],
                connection=connection,
            )

            avail_rf = _available_table_columns(
                pinned, RED_FLAGS_TABLE, connection=connection
            )
            if avail_rf:
                rf_slice = pinned.read_table(
                    RED_FLAGS_TABLE,
                    columns=["issuer_id"],
                    filters={
                        "statement_scope": statement_scope,
                        "period_type": period_type,
                        "period_end_date": resolved_date,
                    },
                    connection=connection,
                )
                rf_counts = (
                    rf_slice.group_by("issuer_id").agg(
                        pl.len().cast(pl.Int64).alias("red_flag_count")
                    )
                    if not rf_slice.is_empty()
                    else pl.DataFrame(
                        schema={"issuer_id": pl.String, "red_flag_count": pl.Int64}
                    )
                )
            else:
                rf_counts = pl.DataFrame(
                    schema={"issuer_id": pl.String, "red_flag_count": pl.Int64}
                )

            ml_join_schema = pl.Schema(
                {
                    "issuer_id": pl.String,
                    "altman_zone": pl.String,
                    "beneish_flag": pl.String,
                    "calibrated_distress_prob": pl.Float64,
                }
            )
            avail_ml = _available_table_columns(
                pinned, ML_SCORES_TABLE, connection=connection
            )
            if avail_ml:
                ml_cols = _select_existing_columns(
                    (
                        "issuer_id",
                        "filing_id",
                        "altman_zone",
                        "beneish_flag",
                        "calibrated_distress_prob",
                    ),
                    avail_ml,
                )
                ml_raw = pinned.read_table(
                    ML_SCORES_TABLE,
                    columns=ml_cols,
                    filters={
                        "statement_scope": statement_scope,
                        "period_type": period_type,
                        "period_end_date": resolved_date,
                    },
                    connection=connection,
                )
                if not ml_raw.is_empty():
                    sort_ml = (
                        ["issuer_id", "filing_id"]
                        if "filing_id" in ml_raw.columns
                        else ["issuer_id"]
                    )
                    ml_slice = _pad_schema_columns(
                        ml_raw.sort(sort_ml).unique(subset=["issuer_id"], keep="last"),
                        ml_join_schema,
                    )
                else:
                    ml_slice = pl.DataFrame(schema=ml_join_schema)
            else:
                ml_slice = pl.DataFrame(schema=ml_join_schema)

            joined = (
                ratios.join(issuers, on="issuer_id", how="inner")
                .join(rf_counts, on="issuer_id", how="left")
                .join(ml_slice, on="issuer_id", how="left")
                .with_columns(
                    pl.coalesce(
                        pl.col("red_flag_count"), pl.lit(0, dtype=pl.Int64)
                    ).alias("red_flag_count"),
                    pl.coalesce(pl.col("icb_code_level2"), pl.lit("OTHER")).alias(
                        "icb_code_level2"
                    ),
                    pl.coalesce(pl.col("icb_name_level2"), pl.lit("Ngành khác")).alias(
                        "icb_name_level2"
                    ),
                    pl.coalesce(
                        pl.col("total_debt"), pl.lit(0.0, dtype=pl.Float64)
                    ).alias("total_debt"),
                )
            )

            sector_df = (
                joined.group_by(["icb_code_level2", "icb_name_level2"])
                .agg(
                    pl.len().cast(pl.Int64).alias("issuer_count"),
                    (pl.col("altman_zone") == "SAFE_ZONE")
                    .fill_null(False)
                    .sum()
                    .cast(pl.Int64)
                    .alias("safe_zone_count"),
                    (pl.col("altman_zone") == "GREY_ZONE")
                    .fill_null(False)
                    .sum()
                    .cast(pl.Int64)
                    .alias("grey_zone_count"),
                    (pl.col("altman_zone") == "DISTRESS_ZONE")
                    .fill_null(False)
                    .sum()
                    .cast(pl.Int64)
                    .alias("distress_zone_count"),
                    (pl.col("beneish_flag") == "HIGH_MANIPULATION_RISK_SCREEN")
                    .fill_null(False)
                    .sum()
                    .cast(pl.Int64)
                    .alias("beneish_flagged_count"),
                    (pl.col("red_flag_count") >= 2)
                    .sum()
                    .cast(pl.Int64)
                    .alias("high_red_flag_count"),
                    pl.col("calibrated_distress_prob")
                    .median()
                    .alias("median_distress_prob"),
                    pl.col("red_flag_count").mean().alias("avg_red_flags"),
                    pl.col("total_debt").sum().alias("total_debt_sum"),
                )
                .with_columns(
                    pl.col("icb_code_level2").cast(pl.String),
                    pl.col("icb_name_level2").cast(pl.String),
                    pl.when(
                        pl.col("median_distress_prob")
                        .cast(pl.Float64, strict=False)
                        .is_finite()
                    )
                    .then(pl.col("median_distress_prob").cast(pl.Float64, strict=False))
                    .otherwise(None)
                    .alias("median_distress_prob"),
                    pl.when(
                        pl.col("avg_red_flags")
                        .cast(pl.Float64, strict=False)
                        .is_finite()
                    )
                    .then(pl.col("avg_red_flags").cast(pl.Float64, strict=False))
                    .otherwise(pl.lit(0.0, dtype=pl.Float64))
                    .alias("avg_red_flags"),
                    pl.when(
                        pl.col("total_debt_sum")
                        .cast(pl.Float64, strict=False)
                        .is_finite()
                    )
                    .then(pl.col("total_debt_sum").cast(pl.Float64, strict=False))
                    .otherwise(pl.lit(0.0, dtype=pl.Float64))
                    .alias("total_debt_sum"),
                )
                .sort(["distress_zone_count", "issuer_count"], descending=[True, True])
            )

            sectors_out = cast(list[SectorRiskDict], sector_df.to_dicts())

            deteriorating = [
                c
                for c in self._compute_market_changes_from_pinned(
                    pinned,
                    statement_scope=statement_scope,
                    period_type=period_type,
                    target_date=resolved_date,
                    limit=30,
                    connection=connection,
                )
                if c["deteriorated"]
            ][:10]

            return {
                "period_end_date": resolved_date,
                "total_issuers": joined.height,
                "safe_zone_count": sum(s["safe_zone_count"] for s in sectors_out),
                "grey_zone_count": sum(s["grey_zone_count"] for s in sectors_out),
                "distress_zone_count": sum(
                    s["distress_zone_count"] for s in sectors_out
                ),
                "beneish_flagged_count": sum(
                    s["beneish_flagged_count"] for s in sectors_out
                ),
                "high_red_flag_count": sum(
                    s["high_red_flag_count"] for s in sectors_out
                ),
                "sectors": sectors_out,
                "top_deteriorating": deteriorating,
            }

    def get_market_changes(
        self,
        *,
        tickers: Sequence[str] | None = None,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        limit: int = 30,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> list[dict[str, object]]:
        """Return latest period-over-period risk changes for requested tickers or market-wide."""
        if statement_scope not in STATEMENT_SCOPES:
            raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
        if period_type not in PERIOD_TYPES:
            raise ValueError(f"Unsupported period_type: {period_type!r}")
        if isinstance(limit, bool) or not (1 <= limit <= 100):
            raise ValueError("limit must be an integer between 1 and 100.")
        clean_tickers = (
            tuple(dict.fromkeys(t.strip().upper() for t in tickers if t.strip()))
            if tickers
            else None
        )
        with self.repository.snapshot_session(connection=connection) as pinned:
            return self._compute_market_changes_from_pinned(
                pinned,
                statement_scope=statement_scope,
                period_type=period_type,
                tickers=clean_tickers,
                limit=limit,
                connection=connection,
            )

    def get_model_card(
        self, *, connection: duckdb.DuckDBPyConnection | None = None
    ) -> dict[str, Any]:
        """Read the persisted Model Card exclusively from active snapshot ``model_card_meta`` (H-04)."""
        with self.repository.snapshot_session(connection=connection) as pinned:
            avail_cols = _available_table_columns(
                pinned, MODEL_CARD_META_TABLE, connection=connection
            )
            if not avail_cols or "model_card_json" not in avail_cols:
                raise ValueError(
                    "Model card metadata was not found in active snapshot."
                )
            card_df = pinned.read_table(
                MODEL_CARD_META_TABLE,
                columns=["model_card_id", "model_card_json"],
                connection=connection,
            )
            if card_df.is_empty():
                raise ValueError(
                    "Model card metadata was not found in active snapshot."
                )
            row = card_df.sort("model_card_id").row(0, named=True)
            raw_json = row.get("model_card_json")
            if not isinstance(raw_json, str) or not raw_json.strip():
                raise ContractViolationError(
                    "Model card metadata table has invalid or empty JSON payload."
                )
            try:
                parsed = json.loads(raw_json)
            except ValueError as exc:
                raise ContractViolationError(
                    "Model card metadata table contains malformed JSON payload."
                ) from exc
            if not isinstance(parsed, dict):
                raise ContractViolationError(
                    "Model card metadata payload must be a JSON object."
                )
            return parsed

    @staticmethod
    def _build_port_candidate_roots(
        repo_source: Path,
        pinned_source: Path,
        *,
        port_data_root: Path | None = None,
    ) -> list[Path]:
        raw_candidates: list[Path] = []
        if port_data_root is not None:
            raw_candidates.extend([port_data_root, port_data_root.parent])
        raw_candidates.extend([repo_source, pinned_source, repo_source.parent])
        for src in (repo_source, pinned_source):
            if (
                src.name.startswith("snap_")
                and src.parent.name == "snapshots"
                and src.parent.parent.name == "published"
                and len(src.parents) >= 3
            ):
                snap_root = src.parents[2]
                raw_candidates.extend([snap_root, snap_root.parent])
        env_root = os.getenv("FININTEL_DATA_ROOT")
        if env_root:
            env_path = Path(env_root).resolve()
            raw_candidates.extend([env_path, env_path.parent])
        else:
            default_resolved = DEFAULT_DATA_ROOT.resolve()
            for maybe_src in (port_data_root, repo_source, pinned_source):
                if maybe_src is not None:
                    src_resolved = maybe_src.resolve()
                    if (
                        src_resolved == default_resolved
                        or default_resolved in src_resolved.parents
                    ):
                        raw_candidates.append(default_resolved)
                        break
        deduped: list[Path] = []
        seen: set[Path] = set()
        for candidate in raw_candidates:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                deduped.append(resolved)
        return deduped

    @staticmethod
    def _load_port_parquet_for_issuer(
        *,
        rel_parquet_path: Path,
        issuer_id: str,
        candidate_roots: Sequence[Path],
    ) -> pl.DataFrame | None:
        for root in candidate_roots:
            parquet_path = root / rel_parquet_path
            if parquet_path.is_file():
                return (
                    pl.scan_parquet(parquet_path)
                    .filter(pl.col("issuer_id") == issuer_id)
                    .collect()
                )
        return None

    @staticmethod
    def _build_overlay_financial_periods(
        pinned: ParquetRepository,
        *,
        issuer_id: str,
        ticker: str,
        accounting_regime: str,
        statement_scope: str,
        prices: pl.DataFrame,
        as_of: date | None,
        connection: duckdb.DuckDBPyConnection | None,
    ) -> pl.DataFrame | None:
        avail_ratio_cols = _available_table_columns(
            pinned, RATIOS_TABLE, connection=connection
        )
        ratios = pl.DataFrame()
        if avail_ratio_cols:
            ratio_cols = _select_existing_columns(
                (
                    "issuer_id",
                    "statement_scope",
                    "period_type",
                    "period_end_date",
                    "fiscal_year",
                    "fiscal_quarter",
                    "filing_id",
                    "parent_equity",
                    "estimated_shares_outstanding",
                ),
                avail_ratio_cols,
            )
            ratios = pinned.read_table(
                RATIOS_TABLE,
                columns=ratio_cols,
                filters={
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                    "period_type": "QUARTER",
                },
                connection=connection,
            )
            if not ratios.is_empty():
                sort_ratio = [
                    col
                    for col in ("period_end_date", "filing_id")
                    if col in ratios.columns
                ]
                ratios = ratios.sort(sort_ratio).unique(
                    subset=["issuer_id", "period_end_date"], keep="last"
                )

        facts_table = "latest_facts.parquet"
        avail_fact_cols = _available_table_columns(
            pinned, facts_table, connection=connection
        )
        if not avail_fact_cols:
            facts_table = "financial_facts.parquet"
            avail_fact_cols = _available_table_columns(
                pinned, facts_table, connection=connection
            )

        facts = pl.DataFrame()
        if avail_fact_cols:
            fact_desired = (
                "issuer_id",
                "statement_scope",
                "period_type",
                "fiscal_year_start_date",
                "period_start_date",
                "period_end_date",
                "fiscal_year",
                "fiscal_quarter",
                "metric_id",
                "taxonomy_version",
                "revision_no",
                "revision_reason",
                "filing_id",
                "published_at",
                "published_at_basis",
                "effective_available_at",
                "derivation_method",
                "audit_status",
                "audit_opinion",
                "value",
                "ttm_value",
                "comparable_across_taxonomy",
                "is_filed_late",
                "source_provider",
                "source_filing_ids",
            )
            fact_cols = _select_existing_columns(fact_desired, avail_fact_cols)
            facts = pinned.read_table(
                facts_table,
                columns=fact_cols,
                filters={
                    "issuer_id": issuer_id,
                    "statement_scope": statement_scope,
                    "period_type": "QUARTER",
                },
                connection=connection,
            )

        ni_ttm_df = pl.DataFrame()
        period_dates_df = pl.DataFrame()
        pp_df: pl.DataFrame | None = None

        if not facts.is_empty():
            sort_fact = [
                col
                for col in (
                    "period_end_date",
                    "effective_available_at",
                    "revision_no",
                    "filing_id",
                )
                if col in facts.columns
            ]
            sorted_facts = facts.sort(sort_fact) if sort_fact else facts

            date_cols = [
                col
                for col in (
                    "issuer_id",
                    "period_end_date",
                    "published_at",
                    "effective_available_at",
                )
                if col in sorted_facts.columns
            ]
            if (
                "published_at" in date_cols or "effective_available_at" in date_cols
            ) and "period_end_date" in date_cols:
                period_dates_df = sorted_facts.select(date_cols).unique(
                    subset=["issuer_id", "period_end_date"], keep="last"
                )

            ni_rows = sorted_facts.filter(pl.col("metric_id") == "IS_NET_INCOME_PARENT")
            if not ni_rows.is_empty():
                ni_dedup = ni_rows.unique(
                    subset=["issuer_id", "period_end_date"], keep="last"
                )
                has_direct_ttm = (
                    "ttm_value" in ni_dedup.columns
                    and ni_dedup["ttm_value"].drop_nulls().len() > 0
                )
                if has_direct_ttm:
                    ni_select = [
                        pl.col("issuer_id").cast(pl.String),
                        pl.col("period_end_date").cast(pl.Date),
                        pl.col("ttm_value")
                        .cast(pl.Float64)
                        .alias("IS_NET_INCOME_PARENT_TTM"),
                    ]
                    if "published_at" in ni_dedup.columns:
                        ni_select.append(
                            pl.col("published_at")
                            .cast(pl.Date)
                            .alias("_ni_published_at")
                        )
                    if "effective_available_at" in ni_dedup.columns:
                        ni_select.append(
                            pl.col("effective_available_at")
                            .cast(pl.Date)
                            .alias("_ni_effective_available_at")
                        )
                    ni_ttm_df = ni_dedup.select(ni_select)

            if ni_ttm_df.is_empty():
                required_pp_cols = {
                    "issuer_id",
                    "statement_scope",
                    "period_type",
                    "fiscal_year_start_date",
                    "period_start_date",
                    "period_end_date",
                    "fiscal_year",
                    "fiscal_quarter",
                    "metric_id",
                    "taxonomy_version",
                    "revision_no",
                    "revision_reason",
                    "filing_id",
                    "published_at",
                    "published_at_basis",
                    "effective_available_at",
                    "derivation_method",
                    "audit_status",
                    "audit_opinion",
                    "value",
                }
                if required_pp_cols <= set(facts.columns):
                    issuer_frame = pl.DataFrame(
                        {
                            "issuer_id": [issuer_id],
                            "ticker": [ticker],
                            "accounting_regime": [accounting_regime],
                        }
                    )
                    pp_raw = prepare_periods(
                        facts,
                        issuer_frame,
                        as_of=as_of or date(2099, 12, 31),
                        market_prices=prices,
                    ).filter(
                        (pl.col("statement_scope") == statement_scope)
                        & (pl.col("period_type") == "QUARTER")
                    )
                    if not pp_raw.is_empty():
                        pp_select = [
                            pl.col("issuer_id").cast(pl.String),
                            pl.col("period_end_date").cast(pl.Date),
                        ]
                        if "IS_NET_INCOME_PARENT" in pp_raw.columns:
                            pp_select.append(
                                pl.col("IS_NET_INCOME_PARENT")
                                .cast(pl.Float64)
                                .alias("IS_NET_INCOME_PARENT_TTM")
                            )
                        if "parent_equity" in pp_raw.columns:
                            pp_select.append(
                                pl.col("parent_equity")
                                .cast(pl.Float64)
                                .alias("_pp_parent_equity")
                            )
                        if "estimated_shares_outstanding" in pp_raw.columns:
                            pp_select.append(
                                pl.col("estimated_shares_outstanding")
                                .cast(pl.Float64)
                                .alias("_pp_estimated_shares")
                            )
                        if "shares_source" in pp_raw.columns:
                            pp_select.append(pl.col("shares_source").cast(pl.String))
                        if "_period_available_at" in pp_raw.columns:
                            pp_select.append(
                                pl.col("_period_available_at").cast(pl.Date)
                            )
                        pp_df = pp_raw.select(pp_select).unique(
                            subset=["issuer_id", "period_end_date"], keep="last"
                        )

        if not ratios.is_empty():
            combined = ratios
        elif pp_df is not None and not pp_df.is_empty():
            combined = pp_df.with_columns(
                pl.lit(statement_scope).alias("statement_scope"),
                pl.lit("QUARTER").alias("period_type"),
            )
            if "_pp_parent_equity" in combined.columns:
                combined = combined.rename({"_pp_parent_equity": "parent_equity"})
            if "_pp_estimated_shares" in combined.columns:
                combined = combined.rename(
                    {"_pp_estimated_shares": "estimated_shares_outstanding"}
                )
        elif not period_dates_df.is_empty():
            combined = period_dates_df.with_columns(
                pl.lit(statement_scope).alias("statement_scope"),
                pl.lit("QUARTER").alias("period_type"),
            )
        else:
            return None

        if not period_dates_df.is_empty() and "published_at" not in combined.columns:
            combined = combined.join(
                period_dates_df, on=["issuer_id", "period_end_date"], how="left"
            )

        if not ni_ttm_df.is_empty():
            combined = combined.join(
                ni_ttm_df, on=["issuer_id", "period_end_date"], how="left"
            )
            if "_ni_published_at" in combined.columns:
                pub_expr = (
                    pl.coalesce("_ni_published_at", "published_at")
                    if "published_at" in combined.columns
                    else pl.col("_ni_published_at")
                )
                combined = combined.with_columns(pub_expr.alias("published_at")).drop(
                    "_ni_published_at"
                )
            if "_ni_effective_available_at" in combined.columns:
                eff_expr = (
                    pl.coalesce("_ni_effective_available_at", "effective_available_at")
                    if "effective_available_at" in combined.columns
                    else pl.col("_ni_effective_available_at")
                )
                combined = combined.with_columns(
                    eff_expr.alias("effective_available_at")
                ).drop("_ni_effective_available_at")
        elif pp_df is not None and not pp_df.is_empty() and not ratios.is_empty():
            combined = combined.join(
                pp_df, on=["issuer_id", "period_end_date"], how="left"
            )
            if "_pp_parent_equity" in combined.columns:
                eq_expr = (
                    pl.coalesce("parent_equity", "_pp_parent_equity")
                    if "parent_equity" in combined.columns
                    else pl.col("_pp_parent_equity")
                )
                combined = combined.with_columns(eq_expr.alias("parent_equity")).drop(
                    "_pp_parent_equity"
                )
            if "_pp_estimated_shares" in combined.columns:
                sh_expr = (
                    pl.coalesce("_pp_estimated_shares", "estimated_shares_outstanding")
                    if "estimated_shares_outstanding" in combined.columns
                    else pl.col("_pp_estimated_shares")
                )
                combined = combined.with_columns(
                    sh_expr.alias("estimated_shares_outstanding")
                ).drop("_pp_estimated_shares")

        if (
            "published_at" not in combined.columns
            and "effective_available_at" not in combined.columns
            and "filing_id" in combined.columns
        ):
            avail_manifest_cols = _available_table_columns(
                pinned, FILINGS_MANIFEST_TABLE, connection=connection
            )
            if avail_manifest_cols:
                m_cols = _select_existing_columns(
                    ("filing_id", "published_at", "effective_available_at"),
                    avail_manifest_cols,
                )
                if "filing_id" in m_cols and (
                    "published_at" in m_cols or "effective_available_at" in m_cols
                ):
                    manifest_df = pinned.read_table(
                        FILINGS_MANIFEST_TABLE,
                        columns=m_cols,
                        filters={"issuer_id": issuer_id},
                        connection=connection,
                    ).unique(subset=["filing_id"], keep="last")
                    if not manifest_df.is_empty():
                        combined = combined.join(
                            manifest_df, on="filing_id", how="left"
                        )

        date_casts = [
            pl.col(col).cast(pl.Date).alias(col)
            for col in (
                "published_at",
                "effective_available_at",
                "_period_available_at",
                "period_end_date",
            )
            if col in combined.columns
        ]
        if date_casts:
            combined = combined.with_columns(date_casts)

        has_any_disclosure_col = any(
            col in combined.columns
            for col in (
                "available_at",
                "published_at",
                "effective_available_at",
                "_period_available_at",
                "_amounts_available_at",
            )
        )
        return combined if has_any_disclosure_col else None

    def get_company_market_overlay(
        self,
        ticker: str,
        *,
        statement_scope: str = "CONSOLIDATED",
        as_of: date | None = None,
        port_data_root: Path | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> dict[str, Any]:
        """Return PIT valuation series and Price & Risk Event Overlay for one company (UI-09b)."""
        if statement_scope not in STATEMENT_SCOPES:
            raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
        clean_ticker = ticker.strip().upper()
        if not clean_ticker:
            raise ValueError("Ticker must not be empty.")

        with self.repository.snapshot_session(connection=connection) as pinned:
            issuer_row = _resolve_issuer_row(
                pinned,
                clean_ticker,
                columns=("issuer_id", "ticker", "accounting_regime"),
                connection=connection,
            )
            issuer_id = str(issuer_row["issuer_id"])
            resolved_ticker = str(issuer_row["ticker"])
            accounting_regime = str(issuer_row["accounting_regime"])

            candidate_roots = self._build_port_candidate_roots(
                self.repository._source,
                pinned._source,
                port_data_root=port_data_root,
            )

            prices = self._load_port_parquet_for_issuer(
                rel_parquet_path=Path("market_prices") / "market_prices.parquet",
                issuer_id=issuer_id,
                candidate_roots=candidate_roots,
            )
            if prices is None:
                prices = pl.DataFrame()
            if not prices.is_empty() and as_of is not None:
                prices = prices.filter(pl.col("trade_date") <= as_of)

            if prices.is_empty():
                return {
                    "issuer_id": issuer_id,
                    "ticker": resolved_ticker,
                    "as_of": as_of,
                    "latest_valuation": None,
                    "price_series": [],
                    "model_markers": [],
                    "regulatory_markers": [],
                    "earliest_warning_summary": None,
                }

            reg_events = self._load_port_parquet_for_issuer(
                rel_parquet_path=Path("regulatory_ledger")
                / "regulatory_events.parquet",
                issuer_id=issuer_id,
                candidate_roots=candidate_roots,
            )
            if reg_events is not None and reg_events.is_empty():
                reg_events = None

            ml_scores: pl.DataFrame | None = None
            avail_ml_cols = _available_table_columns(
                pinned, ML_SCORES_TABLE, connection=connection
            )
            if avail_ml_cols:
                ml_cols = _select_existing_columns(
                    (
                        "issuer_id",
                        "statement_scope",
                        "period_type",
                        "period_end_date",
                        "filing_id",
                        "risk_tier",
                        "calibrated_distress_prob",
                        "risk_market_percentile",
                        "relative_risk_lift",
                        "model_role",
                        "available_at",
                        "published_at",
                        "effective_available_at",
                    ),
                    avail_ml_cols,
                )
                if {"issuer_id", "period_end_date", "risk_tier"} <= set(ml_cols):
                    ml_raw = pinned.read_table(
                        ML_SCORES_TABLE,
                        columns=ml_cols,
                        filters={
                            "issuer_id": issuer_id,
                            "statement_scope": statement_scope,
                        },
                        connection=connection,
                    )
                    if not ml_raw.is_empty():
                        sort_ml = [
                            col
                            for col in ("period_type", "period_end_date", "filing_id")
                            if col in ml_raw.columns
                        ]
                        dedup_ml = [
                            col
                            for col in ("issuer_id", "period_type", "period_end_date")
                            if col in ml_raw.columns
                        ]
                        ml_scores = ml_raw.sort(sort_ml).unique(
                            subset=dedup_ml, keep="last"
                        )

            financial_periods = self._build_overlay_financial_periods(
                pinned,
                issuer_id=issuer_id,
                ticker=resolved_ticker,
                accounting_regime=accounting_regime,
                statement_scope=statement_scope,
                prices=prices,
                as_of=as_of,
                connection=connection,
            )

            valuation_series = (
                compute_pit_valuation_series(
                    prices,
                    financial_periods,
                    issuer_id=issuer_id,
                    statement_scope=statement_scope,
                )
                if financial_periods is not None and not financial_periods.is_empty()
                else None
            )

            payload = build_price_risk_overlay(
                prices,
                issuer_id=issuer_id,
                ml_scores=ml_scores,
                regulatory_events=reg_events,
                financial_periods=financial_periods,
                valuation_series=valuation_series,
                statement_scope=statement_scope,
                as_of=as_of,
            )
            price_series = payload["price_series"]
            latest_valuation = price_series[-1] if price_series else None
            return {
                **payload,
                "latest_valuation": latest_valuation,
            }
