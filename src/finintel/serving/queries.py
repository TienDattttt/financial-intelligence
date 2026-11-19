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
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal, cast

import duckdb
import polars as pl

from finintel.analytics.stress_test import REQUIRED_STRESS_COLUMNS
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
    with _pinned_table_source(pinned, table_name, connection=connection) as source:
        if source is None:
            return None
        con, source_sql, source_params = source
        try:
            cursor = con.execute(
                f"SELECT * FROM {source_sql} WHERE 1 = 0", source_params
            )
        except duckdb.CatalogException as exc:
            if "does not exist" in str(exc).lower():
                return None
            raise ContractViolationError(
                f"Unable to inspect schema for table {table_name!r}."
            ) from exc
        except (duckdb.Error, OSError) as exc:
            raise ContractViolationError(
                f"Unable to inspect schema for table {table_name!r}."
            ) from exc
        description = cursor.description
        if not description:
            return set()
        return {str(desc[0]) for desc in description}


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
    missing_exprs = [
        pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in schema.items()
        if name not in frame.columns
    ]
    if missing_exprs:
        frame = frame.with_columns(missing_exprs)
    return frame.select(
        *(
            pl.col(name).cast(dtype, strict=False).alias(name)
            for name, dtype in schema.items()
        )
    )


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
            return SnapshotOverview(
                snapshot_id=snap_id,
                published_at=published_at,
                total_issuers=total_issuers,
                total_ratio_periods=total_ratio_periods,
                pass_periods=pass_periods,
                warning_periods=warning_periods,
                quarantined_rows=quarantined_rows,
                storage_backend=storage_backend,
            )

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
            return CompanyProfileData(
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
                growth_series=growth_series,
                capital_structure_series=capital_structure_series,
                dupont_series=dupont_series,
                red_flags=red_flags,
                ratios_table=ratios,
                ml_scores=ml_scores,
            )

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

            peer_ranking = (
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

            return IndustryComparisonData(
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
        with self.repository.snapshot_session(connection=connection) as pinned:
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
            return _pad_schema_columns(frame, PEER_BAND_HISTORY_SCHEMA).sort(
                "period_end_date"
            )

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
            return ratios.sort(
                ["period_end_date", "period_type"], descending=[True, False]
            )

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

            return RatioLineageCard(
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
                if dates_df.is_empty():
                    return pl.DataFrame(schema=SCREENER_SCHEMA), 0
                raw_max = dates_df.get_column("period_end_date").max()
                if not isinstance(raw_max, date):
                    return pl.DataFrame(schema=SCREENER_SCHEMA), 0
                resolved_date = raw_max

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
            return projected.slice(offset, limit), total_count

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
            return ratios.join(issuers, on="issuer_id", how="left").sort("ticker")
