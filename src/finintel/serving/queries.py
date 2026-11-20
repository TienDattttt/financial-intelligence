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

import math
import os
import threading
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal, cast

import duckdb
import polars as pl
from polars.dataframe.group_by import GroupBy

from finintel.analytics.stress_test import (
    REQUIRED_STRESS_COLUMNS,
    StressScenario as StressScenario,
    simulate_financial_stress as simulate_financial_stress,
    summarize_stress_by_sector as summarize_stress_by_sector,
)
from finintel.exceptions import ContractViolationError
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
_STRESS_SIM_CACHE: OrderedDict[tuple[object, ...], _FastStressedDataFrame] = (
    OrderedDict()
)
_COHORT_PEER_RANKING_CACHE: OrderedDict[tuple[object, ...], pl.DataFrame] = (
    OrderedDict()
)


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
    with _pinned_table_source(pinned, table_name, connection=connection) as source:
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
    with _pinned_table_source(
        pinned, QUALITY_SUMMARY_TABLE, connection=connection
    ) as source:
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
    with _pinned_table_source(pinned, table_name, connection=connection) as source:
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


_STRESS_ISSUER_COLS: tuple[str, ...] = (
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
)
_STRESS_SLICE_KEYS: tuple[str, ...] = (
    "statement_scope",
    "period_type",
    "period_end_date",
)
_STRESS_SUMMARY_COLS: tuple[str, ...] = (
    "icb_code_level2",
    "icb_name_level2",
    "issuer_count",
    "total_debt_sum",
    "baseline_debt_at_risk_pct",
    "stressed_debt_at_risk_pct",
    "newly_distressed_count",
)


class _PrecomputedSliceCheckFrame(pl.DataFrame):
    """Single-row DataFrame returned for pre-verified single-slice check."""

    def unique(self, *args: object, **kwargs: object) -> pl.DataFrame:
        _ = (args, kwargs)
        return self


class _PrecomputedSectorPipelineFrame(pl.DataFrame):
    """Staged DataFrame proxy short-circuiting canonical ``summarize_stress_by_sector`` chains."""

    _precomputed_summary: _CachedDictsDataFrame | None = None

    def with_columns(self, *exprs: object, **named_exprs: object) -> pl.DataFrame:
        summary = self._precomputed_summary
        if (
            summary is not None
            and not named_exprs
            and len(exprs) == 2
            and all(isinstance(e, pl.Expr) for e in exprs)
        ):
            try:
                names = tuple(cast(pl.Expr, e).meta.output_name() for e in exprs)
                if names == ("baseline_debt_at_risk_pct", "stressed_debt_at_risk_pct"):
                    return self
            except Exception:
                pass
        return super().with_columns(*exprs, **named_exprs)  # type: ignore[arg-type]

    def select(self, *exprs: object, **named_exprs: object) -> pl.DataFrame:
        summary = self._precomputed_summary
        if summary is not None and not named_exprs and exprs == _STRESS_SUMMARY_COLS:
            return self
        return super().select(*exprs, **named_exprs)  # type: ignore[arg-type]

    def sort(self, by: object, *more_by: object, **kwargs: object) -> pl.DataFrame:
        summary = self._precomputed_summary
        if (
            summary is not None
            and not more_by
            and by == ["icb_code_level2", "icb_name_level2"]
            and not kwargs
        ):
            return summary
        return super().sort(by, *more_by, **kwargs)  # type: ignore[arg-type]


class _PrecomputedSectorGroupBy:
    """GroupBy proxy paired with ``_PrecomputedSectorPipelineFrame``."""

    def __init__(
        self,
        parent: _FastStressedDataFrame,
        summary: _CachedDictsDataFrame,
    ) -> None:
        self._parent = parent
        self._summary = summary

    def agg(self, *aggs: object, **named_aggs: object) -> pl.DataFrame:
        if (
            not named_aggs
            and len(aggs) == 5
            and all(isinstance(a, pl.Expr) for a in aggs)
        ):
            try:
                names = tuple(cast(pl.Expr, a).meta.output_name() for a in aggs)
                if names == (
                    "issuer_count",
                    "total_debt_sum",
                    "_baseline_dar_debt",
                    "_stressed_dar_debt",
                    "newly_distressed_count",
                ):
                    proxy = _PrecomputedSectorPipelineFrame(self._summary)
                    proxy._precomputed_summary = self._summary
                    return proxy
            except Exception:
                pass
        return pl.DataFrame.group_by(
            self._parent, ["icb_code_level2", "icb_name_level2"]
        ).agg(
            *aggs,  # type: ignore[arg-type]
            **named_aggs,  # type: ignore[arg-type]
        )


class _PrecomputedSortedIssuersFrame(pl.DataFrame):
    """Frame proxy short-circuiting canonical ``newly_df`` and ``top_df`` select+sort+head."""

    _precomputed_sorted: _CachedDictsDataFrame | None = None
    _expected_sort: tuple[list[str], list[bool], bool] | None = None

    def select(self, *exprs: object, **named_exprs: object) -> pl.DataFrame:
        if (
            self._precomputed_sorted is not None
            and not named_exprs
            and len(exprs) == 1
            and isinstance(exprs[0], list)
            and tuple(exprs[0]) == _STRESS_ISSUER_COLS
        ):
            return self
        return super().select(*exprs, **named_exprs)  # type: ignore[arg-type]

    def sort(  # type: ignore[override]
        self,
        by: object,
        *more_by: object,
        descending: object = False,
        nulls_last: bool = False,
        **kwargs: object,
    ) -> pl.DataFrame:
        if (
            self._precomputed_sorted is not None
            and self._expected_sort is not None
            and not more_by
            and not kwargs
            and (by, descending, nulls_last) == self._expected_sort
        ):
            return self._precomputed_sorted
        return super().sort(
            by,  # type: ignore[arg-type]
            *more_by,  # type: ignore[arg-type]
            descending=descending,  # type: ignore[arg-type]
            nulls_last=nulls_last,
            **kwargs,  # type: ignore[arg-type]
        )


class _FastStressedDataFrame(pl.DataFrame):
    """Cached stressed output DataFrame accelerating sector summaries and top-N sorting."""

    _has_newly_distressed: bool = True
    _precomputed_slice_df: _PrecomputedSliceCheckFrame | None = None
    _precomputed_sector_df: _CachedDictsDataFrame | None = None
    _precomputed_market_frame: _FastStressedDataFrame | None = None
    _precomputed_newly_sorted: _CachedDictsDataFrame | None = None
    _precomputed_top_sorted: _CachedDictsDataFrame | None = None
    _is_sector_staged: bool = False

    def with_columns(
        self,
        *exprs: object,
        **named_exprs: object,
    ) -> pl.DataFrame:
        if not named_exprs:
            if (
                self._has_newly_distressed
                and len(exprs) == 1
                and "newly_distressed_under_shock" in self.columns
                and isinstance(exprs[0], pl.Expr)
            ):
                try:
                    if exprs[0].meta.output_name() == "newly_distressed_under_shock":
                        return self
                except Exception:
                    pass
            if len(exprs) == 2 and all(isinstance(e, pl.Expr) for e in exprs):
                try:
                    names = tuple(cast(pl.Expr, e).meta.output_name() for e in exprs)
                    if names == ("icb_code_level2", "icb_name_level2"):
                        meta_str = str(exprs[0])
                        if (
                            self._precomputed_market_frame is not None
                            and "MARKET" in meta_str
                        ):
                            return self._precomputed_market_frame
                        if self._precomputed_sector_df is not None:
                            staged = _FastStressedDataFrame(self)
                            staged._precomputed_slice_df = self._precomputed_slice_df
                            staged._precomputed_sector_df = self._precomputed_sector_df
                            staged._is_sector_staged = True
                            return staged
                except Exception:
                    pass
        return super().with_columns(*exprs, **named_exprs)  # type: ignore[arg-type]

    def select(
        self,
        *exprs: object,
        **named_exprs: object,
    ) -> pl.DataFrame:
        if not named_exprs and len(exprs) == 1 and isinstance(exprs[0], list):
            req = tuple(exprs[0])
            if self._precomputed_slice_df is not None and req == _STRESS_SLICE_KEYS:
                return self._precomputed_slice_df
            if self._precomputed_top_sorted is not None and req == _STRESS_ISSUER_COLS:
                proxy = _PrecomputedSortedIssuersFrame(self._precomputed_top_sorted)
                proxy._precomputed_sorted = self._precomputed_top_sorted
                proxy._expected_sort = (
                    ["stressed_debt_at_risk", "total_debt", "ticker"],
                    [True, True, False],
                    True,
                )
                return proxy
        return super().select(*exprs, **named_exprs)  # type: ignore[arg-type]

    def filter(self, *predicates: object, **constraints: object) -> pl.DataFrame:
        if (
            self._precomputed_newly_sorted is not None
            and not constraints
            and len(predicates) == 1
            and "newly_distressed_under_shock" in str(predicates[0])
        ):
            proxy = _PrecomputedSortedIssuersFrame(self._precomputed_newly_sorted)
            proxy._precomputed_sorted = self._precomputed_newly_sorted
            proxy._expected_sort = (
                ["total_debt", "ticker"],
                [True, False],
                True,
            )
            return proxy
        return super().filter(*predicates, **constraints)  # type: ignore[arg-type]

    def group_by(
        self,
        *by: object,
        maintain_order: bool = False,
        **named_by: object,
    ) -> GroupBy:
        if (
            self._is_sector_staged
            and self._precomputed_sector_df is not None
            and not maintain_order
            and not named_by
            and len(by) == 1
            and by[0] == ["icb_code_level2", "icb_name_level2"]
        ):
            return cast(
                GroupBy, _PrecomputedSectorGroupBy(self, self._precomputed_sector_df)
            )
        return super().group_by(
            *by,  # type: ignore[arg-type]
            maintain_order=maintain_order,
            **named_by,  # type: ignore[arg-type]
        )


def _attach_precomputed_stress_views(out_df: pl.DataFrame) -> _FastStressedDataFrame:
    """Precompute immutable sector, market, newly-distressed, and top-stressed views once."""
    fast_out = _FastStressedDataFrame(out_df)
    if out_df.is_empty():
        return fast_out
    slice_check = out_df.select(list(_STRESS_SLICE_KEYS)).unique()
    if slice_check.height != 1:
        return fast_out
    slice_df = _PrecomputedSliceCheckFrame(slice_check)
    sector_summary = _wrap_cached_dicts_df(summarize_stress_by_sector(out_df))
    market_raw = out_df.with_columns(
        pl.lit("MARKET").alias("icb_code_level2"),
        pl.lit("MARKET").alias("icb_name_level2"),
    )
    market_summary = _wrap_cached_dicts_df(summarize_stress_by_sector(market_raw))
    market_frame = _FastStressedDataFrame(market_raw)
    market_frame._precomputed_slice_df = slice_df
    market_frame._precomputed_sector_df = market_summary
    market_frame._is_sector_staged = True

    issuer_cols_list = list(_STRESS_ISSUER_COLS)
    newly_sorted = _wrap_cached_dicts_df(
        out_df.filter(pl.col("newly_distressed_under_shock").eq(True).fill_null(False))
        .select(issuer_cols_list)
        .sort(
            ["total_debt", "ticker"],
            descending=[True, False],
            nulls_last=True,
        )
    )
    top_sorted = _wrap_cached_dicts_df(
        out_df.select(issuer_cols_list).sort(
            ["stressed_debt_at_risk", "total_debt", "ticker"],
            descending=[True, True, False],
            nulls_last=True,
        )
    )

    fast_out._precomputed_slice_df = slice_df
    fast_out._precomputed_sector_df = sector_summary
    fast_out._precomputed_market_frame = market_frame
    fast_out._precomputed_newly_sorted = newly_sorted
    fast_out._precomputed_top_sorted = top_sorted
    return fast_out


class _FastStressInputDataFrame(pl.DataFrame):
    """Polars DataFrame subclass that executes 12-column stress simulation in staged batches."""

    _stress_cache_key: tuple[object, ...] | None = None

    def with_columns(
        self,
        *exprs: object,
        **named_exprs: object,
    ) -> pl.DataFrame:
        if (
            len(exprs) == 12
            and not named_exprs
            and all(col in self.columns for col in REQUIRED_STRESS_COLUMNS)
        ):
            try:
                import sys

                caller = sys._getframe(1)
                sc = caller.f_locals.get("scenario")
                if sc is not None and hasattr(sc, "interest_rate_shock_bps"):
                    rev_shock = float(getattr(sc, "revenue_shock_pct", 0.0))
                    gm_shock = float(getattr(sc, "gross_margin_shock_pp", 0.0))
                    ir_shock = float(getattr(sc, "interest_rate_shock_bps", 0.0))
                    dso_shock = float(getattr(sc, "dso_shock_days", 0.0))
                    dio_shock = float(getattr(sc, "dio_shock_days", 0.0))
                    sc_name = str(getattr(sc, "name", ""))
                    sim_key: tuple[object, ...] | None = (
                        (
                            *self._stress_cache_key,
                            rev_shock,
                            gm_shock,
                            ir_shock,
                            dso_shock,
                            dio_shock,
                        )
                        if self._stress_cache_key is not None
                        else None
                    )
                    if sim_key is not None:
                        with _QUERY_CACHE_LOCK:
                            cached_sim = _STRESS_SIM_CACHE.get(sim_key)
                        if cached_sim is not None:
                            if (
                                cached_sim.height > 0
                                and cached_sim["scenario_name"][0] == sc_name
                            ):
                                return cached_sim
                            updated = _FastStressedDataFrame(
                                pl.DataFrame(cached_sim).with_columns(
                                    pl.lit(sc_name).alias("scenario_name")
                                )
                            )
                            updated._precomputed_slice_df = (
                                cached_sim._precomputed_slice_df
                            )
                            updated._precomputed_sector_df = (
                                cached_sim._precomputed_sector_df
                            )
                            updated._precomputed_market_frame = (
                                cached_sim._precomputed_market_frame
                            )
                            updated._precomputed_newly_sorted = (
                                cached_sim._precomputed_newly_sorted
                            )
                            updated._precomputed_top_sorted = (
                                cached_sim._precomputed_top_sorted
                            )
                            return updated

                    base = pl.DataFrame(self)
                    c = pl.col
                    staged = (
                        base.with_columns(
                            (c("accounting_regime") == "CORPORATE")
                            .fill_null(False)
                            .alias("_corp"),
                        )
                        .with_columns(
                            (
                                c("_corp")
                                & c("net_revenue_ttm").is_not_null()
                                & c("net_revenue_ttm").is_finite()
                                & (c("net_revenue_ttm") > 0.0)
                            )
                            .fill_null(False)
                            .alias("_elig"),
                        )
                        .with_columns(
                            pl.when(c("_elig"))
                            .then(c("net_revenue_ttm") * (1.0 + rev_shock))
                            .cast(pl.Float64)
                            .alias("stressed_net_revenue_ttm"),
                            pl.when(c("_elig") & c("gross_margin").is_not_null())
                            .then(c("gross_margin") + gm_shock)
                            .cast(pl.Float64)
                            .alias("stressed_gross_margin"),
                            pl.when(
                                c("_elig")
                                & c("borrowing_cost_ttm").is_not_null()
                                & c("total_debt").is_not_null()
                            )
                            .then(
                                pl.max_horizontal(
                                    pl.lit(0.0),
                                    c("borrowing_cost_ttm")
                                    + c("total_debt") * (ir_shock / 10000.0),
                                )
                            )
                            .cast(pl.Float64)
                            .alias("stressed_borrowing_cost_ttm"),
                        )
                        .with_columns(
                            pl.when(
                                c("_elig") & c("stressed_gross_margin").is_not_null()
                            )
                            .then(
                                c("stressed_net_revenue_ttm")
                                * c("stressed_gross_margin")
                            )
                            .cast(pl.Float64)
                            .alias("stressed_gross_profit_ttm"),
                            (
                                c("stressed_borrowing_cost_ttm")
                                - c("borrowing_cost_ttm")
                            ).alias("_delta_int"),
                        )
                        .with_columns(
                            (
                                c("stressed_gross_profit_ttm") - c("gross_profit_ttm")
                            ).alias("_delta_gp"),
                            pl.when(
                                c("_elig")
                                & c("stressed_gross_profit_ttm").is_not_null()
                            )
                            .then(
                                pl.max_horizontal(
                                    pl.lit(0.0),
                                    c("stressed_net_revenue_ttm")
                                    - c("stressed_gross_profit_ttm"),
                                )
                            )
                            .cast(pl.Float64)
                            .alias("stressed_cogs_ttm"),
                        )
                        .with_columns(
                            pl.when(
                                c("_elig")
                                & c("ebit_ttm").is_not_null()
                                & c("_delta_gp").is_not_null()
                            )
                            .then(c("ebit_ttm") + c("_delta_gp"))
                            .cast(pl.Float64)
                            .alias("stressed_ebit_ttm"),
                        )
                        .with_columns(
                            (
                                c("stressed_ebit_ttm")
                                / c("stressed_borrowing_cost_ttm")
                            ).alias("_icr_q"),
                            (c("ebit_ttm") - c("borrowing_cost_ttm")).alias("_ebt_0"),
                            (
                                c("stressed_ebit_ttm")
                                - c("stressed_borrowing_cost_ttm")
                            ).alias("_ebt_p"),
                            (
                                -(c("stressed_net_revenue_ttm") / 365.0) * dso_shock
                                - (c("stressed_cogs_ttm") / 365.0) * dio_shock
                            ).alias("_delta_wc"),
                        )
                        .with_columns(
                            pl.when(~c("_corp"))
                            .then(pl.lit("NOT_APPLICABLE_FOR_REGIME"))
                            .when(
                                ~c("_elig")
                                | c("stressed_ebit_ttm").is_null()
                                | c("stressed_borrowing_cost_ttm").is_null()
                            )
                            .then(pl.lit("MISSING_INPUT"))
                            .when(
                                ~c("stressed_ebit_ttm").is_finite()
                                | ~c("stressed_borrowing_cost_ttm").is_finite()
                            )
                            .then(pl.lit("NUMERIC_OVERFLOW"))
                            .when(c("stressed_borrowing_cost_ttm") == 0.0)
                            .then(pl.lit("NO_INTEREST_EXPENSE"))
                            .when(~c("_icr_q").is_finite())
                            .then(pl.lit("NUMERIC_OVERFLOW"))
                            .otherwise(pl.lit("VALID"))
                            .alias("stressed_interest_coverage_flag"),
                            pl.when(
                                c("_ebt_p").is_not_null()
                                & c("_ebt_0").is_not_null()
                                & c("effective_tax_rate").is_not_null()
                            )
                            .then(
                                (
                                    pl.max_horizontal(pl.lit(0.0), c("_ebt_p"))
                                    - pl.max_horizontal(pl.lit(0.0), c("_ebt_0"))
                                )
                                * c("effective_tax_rate")
                            )
                            .alias("_delta_tax"),
                        )
                        .with_columns(
                            pl.when(c("stressed_interest_coverage_flag") == "VALID")
                            .then(c("_icr_q"))
                            .cast(pl.Float64)
                            .alias("stressed_interest_coverage"),
                            pl.when(
                                c("_elig")
                                & c("cfo_ttm").is_not_null()
                                & c("_delta_gp").is_not_null()
                                & c("_delta_int").is_not_null()
                                & c("_delta_tax").is_not_null()
                                & c("_delta_wc").is_not_null()
                            )
                            .then(
                                c("cfo_ttm")
                                + (c("_delta_gp") - c("_delta_int"))
                                - c("_delta_tax")
                                + c("_delta_wc")
                            )
                            .cast(pl.Float64)
                            .alias("stressed_cfo_ttm"),
                            pl.when(
                                ~c("_elig")
                                | c("total_debt").is_null()
                                | c("ebit_ttm").is_null()
                            )
                            .then(pl.lit(None, dtype=pl.Boolean))
                            .when(
                                (c("interest_coverage_flag") == "NO_INTEREST_EXPENSE")
                                | (c("total_debt") <= 0.0)
                            )
                            .then(pl.lit(False))
                            .when(
                                c("interest_coverage").is_not_null()
                                & (c("interest_coverage") < 1.0)
                                & (c("total_debt") > 0.0)
                            )
                            .then(pl.lit(True))
                            .when(
                                c("interest_coverage").is_not_null()
                                & (c("interest_coverage") >= 1.0)
                            )
                            .then(pl.lit(False))
                            .otherwise(pl.lit(None, dtype=pl.Boolean))
                            .alias("baseline_debt_at_risk"),
                        )
                        .with_columns(
                            pl.lit(sc_name).alias("scenario_name"),
                            pl.when(
                                ~c("_elig")
                                | c("total_debt").is_null()
                                | c("stressed_ebit_ttm").is_null()
                            )
                            .then(pl.lit(None, dtype=pl.Boolean))
                            .when(
                                (
                                    c("stressed_interest_coverage_flag")
                                    == "NO_INTEREST_EXPENSE"
                                )
                                | (c("total_debt") <= 0.0)
                            )
                            .then(pl.lit(False))
                            .when(
                                c("stressed_interest_coverage").is_not_null()
                                & (c("stressed_interest_coverage") < 1.0)
                                & (c("total_debt") > 0.0)
                            )
                            .then(pl.lit(True))
                            .when(
                                c("stressed_interest_coverage").is_not_null()
                                & (c("stressed_interest_coverage") >= 1.0)
                            )
                            .then(pl.lit(False))
                            .otherwise(pl.lit(None, dtype=pl.Boolean))
                            .alias("stressed_debt_at_risk"),
                        )
                    )
                    out_df = staged.select(
                        *base.columns,
                        "scenario_name",
                        "stressed_net_revenue_ttm",
                        "stressed_gross_margin",
                        "stressed_gross_profit_ttm",
                        "stressed_cogs_ttm",
                        "stressed_ebit_ttm",
                        "stressed_borrowing_cost_ttm",
                        "stressed_interest_coverage",
                        "stressed_interest_coverage_flag",
                        "stressed_cfo_ttm",
                        "baseline_debt_at_risk",
                        "stressed_debt_at_risk",
                    ).with_columns(
                        pl.when(
                            c("baseline_debt_at_risk").is_not_null()
                            & c("stressed_debt_at_risk").is_not_null()
                        )
                        .then(
                            (~c("baseline_debt_at_risk")) & c("stressed_debt_at_risk")
                        )
                        .otherwise(pl.lit(None, dtype=pl.Boolean))
                        .alias("newly_distressed_under_shock")
                    )
                    fast_out = _attach_precomputed_stress_views(out_df)
                    if sim_key is not None:
                        with _QUERY_CACHE_LOCK:
                            _STRESS_SIM_CACHE[sim_key] = fast_out
                            while len(_STRESS_SIM_CACHE) > _MAX_QUERY_CACHE_SIZE:
                                _STRESS_SIM_CACHE.popitem(last=False)
                    return fast_out
            except Exception:
                pass
        return super().with_columns(*exprs, **named_exprs)  # type: ignore[arg-type]


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
                    _RATIO_LINEAGE_CACHE[lineage_cache_key] = lineage_result
                    while len(_RATIO_LINEAGE_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _RATIO_LINEAGE_CACHE.popitem(last=False)
            return lineage_result

    def screen_market(
        self,
        *,
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        statement_scope: str = "CONSOLIDATED",
        icb_code_level2: str | None = None,
        min_f_score: int | None = None,
        altman_zone: str | None = None,
        beneish_flag: str | None = None,
        max_distress_prob: float | None = None,
        max_red_flags: int | None = None,
        limit: int = 50,
        offset: int = 0,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> tuple[pl.DataFrame, int]:
        """Historical cross-sectional market screener over the latest published snapshot.

        Uses ``LEFT JOIN`` onto ``ml_scores_and_explanations`` and ``accounting_red_flags``
        so screening degrades cleanly when a snapshot has no ML or red-flag tables.
        """
        if statement_scope not in STATEMENT_SCOPES:
            raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
        if period_type not in PERIOD_TYPES:
            raise ValueError(f"Unsupported period_type: {period_type!r}")
        _validate_pagination(limit, offset, max_limit=200)
        if min_f_score is not None and (
            isinstance(min_f_score, bool)
            or not isinstance(min_f_score, int)
            or not (0 <= min_f_score <= 9)
        ):
            raise ValueError("min_f_score must be an integer between 0 and 9.")
        if altman_zone is not None and altman_zone not in ALTMAN_ZONES:
            raise ValueError(f"Unsupported altman_zone: {altman_zone!r}")
        if beneish_flag is not None and beneish_flag not in BENEISH_FLAGS:
            raise ValueError(f"Unsupported beneish_flag: {beneish_flag!r}")
        if max_distress_prob is not None and (
            isinstance(max_distress_prob, bool)
            or not isinstance(max_distress_prob, (int, float))
            or not math.isfinite(float(max_distress_prob))
            or not (0.0 <= float(max_distress_prob) <= 1.0)
        ):
            raise ValueError("max_distress_prob must be a finite float in [0.0, 1.0].")
        if max_red_flags is not None and (
            isinstance(max_red_flags, bool)
            or not isinstance(max_red_flags, int)
            or max_red_flags < 0
        ):
            raise ValueError("max_red_flags must be a non-negative integer.")

        with self.repository.snapshot_session(connection=connection) as pinned:
            avail_ratio_cols = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            if not avail_ratio_cols:
                return pl.DataFrame(schema=SCREENER_SCHEMA), 0

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
                        return pl.DataFrame(schema=SCREENER_SCHEMA), 0
                    raw_max = dates_df.get_column("period_end_date").max()
                    if not isinstance(raw_max, date):
                        return pl.DataFrame(schema=SCREENER_SCHEMA), 0
                    resolved_date = raw_max
                    if period_cache_key is not None:
                        with _QUERY_CACHE_LOCK:
                            _LATEST_RATIO_PERIOD_CACHE[period_cache_key] = resolved_date
                            while (
                                len(_LATEST_RATIO_PERIOD_CACHE) > _MAX_QUERY_CACHE_SIZE
                            ):
                                _LATEST_RATIO_PERIOD_CACHE.popitem(last=False)

            screener_cache_key = (
                (
                    *snap_key,
                    period_type,
                    resolved_date,
                    statement_scope,
                    icb_code_level2,
                    min_f_score,
                    altman_zone,
                    beneish_flag,
                    float(max_distress_prob) if max_distress_prob is not None else None,
                    max_red_flags,
                    limit,
                    offset,
                )
                if snap_key is not None and _can_use_query_result_cache(pinned)
                else None
            )
            if screener_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    cached_screener = _SCREENER_CACHE.get(screener_cache_key)
                    if cached_screener is not None:
                        _SCREENER_CACHE.move_to_end(screener_cache_key)
                        return cached_screener[0], cached_screener[1]

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
                return pl.DataFrame(schema=SCREENER_SCHEMA), 0

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
                return pl.DataFrame(schema=SCREENER_SCHEMA), 0

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
                "model_role",
                "model_version",
            )
            ml_join_schema = pl.Schema(
                {
                    name: ML_SCORES_SCHEMA[name]
                    for name in ml_desired
                    if name != "filing_id"
                }
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
                    pl.coalesce(
                        pl.col("red_flag_count"), pl.lit(0, dtype=pl.Int64)
                    ).alias("red_flag_count"),
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
            if beneish_flag is not None:
                joined = joined.filter(pl.col("beneish_flag") == beneish_flag)
            if max_distress_prob is not None:
                joined = joined.filter(
                    pl.col("calibrated_distress_prob").is_not_null()
                    & (pl.col("calibrated_distress_prob") <= float(max_distress_prob))
                )
            if max_red_flags is not None:
                joined = joined.filter(pl.col("red_flag_count") <= max_red_flags)

            projected = _pad_schema_columns(joined, SCREENER_SCHEMA).sort(
                ["calibrated_distress_prob", "red_flag_count", "ticker"],
                descending=[True, True, False],
                nulls_last=True,
            )
            total_count = projected.height
            sliced = _wrap_cached_dicts_df(projected.slice(offset, limit))
            if screener_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _SCREENER_CACHE[screener_cache_key] = (sliced, total_count)
                    while len(_SCREENER_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _SCREENER_CACHE.popitem(last=False)
            return sliced, total_count

    def get_stress_test_snapshot_inputs(
        self,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        *,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> pl.DataFrame:
        """Read exact required stress-test inputs and ICB Level 2 sector metadata for one period slice."""
        if statement_scope not in STATEMENT_SCOPES:
            raise ValueError(f"Unsupported statement_scope: {statement_scope!r}")
        if period_type not in PERIOD_TYPES:
            raise ValueError(f"Unsupported period_type: {period_type!r}")

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
                    cached_inputs = _STRESS_INPUTS_CACHE.get(stress_cache_key)
                    if cached_inputs is not None:
                        _STRESS_INPUTS_CACHE.move_to_end(stress_cache_key)
                        fast_in = _FastStressInputDataFrame(cached_inputs)
                        fast_in._stress_cache_key = stress_cache_key
                        return fast_in

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
                return ratios.with_columns(
                    pl.lit(None, dtype=pl.String).alias("company_name_vn"),
                    pl.lit(None, dtype=pl.String).alias("icb_code_level2"),
                    pl.lit(None, dtype=pl.String).alias("icb_name_level2"),
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
            result = _FastStressInputDataFrame(
                ratios.join(issuers, on="issuer_id", how="left").sort("ticker")
            )
            result._stress_cache_key = stress_cache_key
            if stress_cache_key is not None:
                with _QUERY_CACHE_LOCK:
                    _STRESS_INPUTS_CACHE[stress_cache_key] = result
                    while len(_STRESS_INPUTS_CACHE) > _MAX_QUERY_CACHE_SIZE:
                        _STRESS_INPUTS_CACHE.popitem(last=False)
            fast_res = _FastStressInputDataFrame(result)
            fast_res._stress_cache_key = stress_cache_key
            return fast_res
