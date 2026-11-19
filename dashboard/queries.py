"""Read-only DuckDB query layer for the Layer 5 Streamlit dashboard.

Architectural invariants:
  - Reads EXCLUSIVELY from published immutable snapshots via ``ParquetRepository``.
  - Contains ZERO financial or accounting calculations; all values, ratios,
    DuPont factors, benchmarks, red flags, and quality statuses are read as
    pre-computed columns from Layer 2/4/6 outputs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import polars as pl

from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_TABLE,
    BENCHMARKS_TABLE,
    CORE_RATIOS,
    EXTRA_RATIOS,
    FILINGS_MANIFEST_TABLE,
    QUALITY_SUMMARY_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
DenominatorMode = Literal[
    "AVERAGE_STOCK", "POINT_IN_TIME_STOCK", "FLOW", "PRIOR_YEAR_FLOW", "COMPOSITE"
]
PeriodView = Literal["TTM", "Quarterly", "Annual"]


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
}

ALL_RATIOS: tuple[str, ...] = (*CORE_RATIOS, *EXTRA_RATIOS)


@dataclass(frozen=True)
class SnapshotOverview:
    snapshot_id: str | None
    published_at: str | None
    total_issuers: int
    total_ratio_periods: int
    pass_periods: int
    warning_periods: int
    quarantined_rows: int


@dataclass(frozen=True)
class CompanyProfileData:
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


@dataclass(frozen=True)
class IndustryComparisonData:
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


def _read_optional_table(
    repo: ParquetRepository, table_name: str
) -> pl.DataFrame | None:
    try:
        return repo.read_table(table_name)
    except FileNotFoundError:
        return None


class DashboardQueryService:
    """Stateless query facade reading exclusively from active Layer 3 Parquet snapshots."""

    def __init__(self, repository: ParquetRepository) -> None:
        self.repository = repository

    @classmethod
    def from_data_root(cls, data_root: Path | None = None) -> DashboardQueryService:
        return cls(ParquetRepository(data_root=resolve_data_root(data_root)))

    def get_snapshot_overview(self) -> SnapshotOverview:
        with self.repository.snapshot_session() as pinned:
            snap_id = pinned.snapshot_id
            published_at: str | None = None
            meta_path = pinned.path.with_name("_snapshot_meta.json")
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if isinstance(meta, dict) and isinstance(
                        meta.get("published_at"), str
                    ):
                        published_at = meta["published_at"]
                except (OSError, ValueError):
                    pass

            issuers = _read_optional_table(pinned, "issuers.parquet")
            ratios = _read_optional_table(pinned, RATIOS_TABLE)
            quality = _read_optional_table(pinned, QUALITY_SUMMARY_TABLE)
            quarantine = _read_optional_table(pinned, AUDIT_QUARANTINE_TABLE)

            pass_count = (
                int(quality.filter(pl.col("quality_status") == "PASS").height)
                if quality is not None
                else 0
            )
            warn_count = (
                int(quality.filter(pl.col("quality_status") == "WARNING").height)
                if quality is not None
                else 0
            )
            return SnapshotOverview(
                snapshot_id=snap_id,
                published_at=published_at,
                total_issuers=issuers.height if issuers is not None else 0,
                total_ratio_periods=ratios.height if ratios is not None else 0,
                pass_periods=pass_count,
                warning_periods=warn_count,
                quarantined_rows=quarantine.height if quarantine is not None else 0,
            )

    def list_issuers(self) -> pl.DataFrame:
        with self.repository.snapshot_session() as pinned:
            issuers = pinned.read_table("issuers.parquet")
            return issuers.sort("ticker")

    def get_company_profile(
        self,
        ticker: str,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_view: PeriodView = "TTM",
    ) -> CompanyProfileData:
        clean_ticker = ticker.strip().upper()
        target_period_type = "ANNUAL" if period_view == "Annual" else "QUARTER"

        with self.repository.snapshot_session() as pinned:
            issuers = pinned.read_table("issuers.parquet")
            issuer_rows = issuers.filter(pl.col("ticker") == clean_ticker)
            if issuer_rows.is_empty():
                raise ValueError(f"Ticker not found in active snapshot: {clean_ticker}")
            issuer_row = issuer_rows.row(0, named=True)
            issuer_id = str(issuer_row["issuer_id"])

            ratios = (
                pinned.read_table(RATIOS_TABLE)
                .filter(
                    (pl.col("issuer_id") == issuer_id)
                    & (pl.col("statement_scope") == statement_scope)
                    & (pl.col("period_type") == target_period_type)
                )
                .sort("period_end_date")
            )

            facts_source = _read_optional_table(pinned, "latest_facts.parquet")
            if facts_source is None:
                facts_source = pinned.read_table("financial_facts.parquet")
            company_facts = facts_source.filter(
                (pl.col("issuer_id") == issuer_id)
                & (pl.col("statement_scope") == statement_scope)
                & (pl.col("period_type") == target_period_type)
            ).sort(["period_end_date", "taxonomy_version"])

            quality = _read_optional_table(pinned, QUALITY_SUMMARY_TABLE)
            latest_quality_status: str | None = None
            latest_warnings: tuple[str, ...] = ()
            if quality is not None and not quality.is_empty():
                q_filtered = quality.filter(
                    (pl.col("issuer_id") == issuer_id)
                    & (pl.col("statement_scope") == statement_scope)
                    & (pl.col("period_type") == target_period_type)
                ).sort("period_end_date")
                if not q_filtered.is_empty():
                    q_last = q_filtered.row(-1, named=True)
                    latest_quality_status = str(q_last["quality_status"])
                    raw_warnings = q_last.get("warning_codes") or []
                    latest_warnings = tuple(str(code) for code in raw_warnings)

            latest_taxonomy: str | None = (
                str(ratios["taxonomy_version"][-1]) if not ratios.is_empty() else None
            )

            # Growth series: read directly from pre-computed value / ttm_value columns (zero UI math).
            flow_value_col = (
                "ttm_value"
                if period_view == "TTM" and "ttm_value" in company_facts.columns
                else "value"
            )
            growth_metrics = (
                "IS_NET_REVENUE",
                "IS_NET_INCOME",
                "CF_OPERATING_CASH_FLOW",
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

            # Capital structure series: Assets, Equity from facts; Total Debt from pre-computed ratios.
            bs_metrics = ("BS_TOTAL_ASSETS", "BS_TOTAL_EQUITY", "BS_TOTAL_LIABILITIES")
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

            red_flags_raw = _read_optional_table(pinned, RED_FLAGS_TABLE)
            if red_flags_raw is not None and not red_flags_raw.is_empty():
                red_flags = red_flags_raw.filter(
                    (pl.col("issuer_id") == issuer_id)
                    & (pl.col("statement_scope") == statement_scope)
                    & (pl.col("period_type") == target_period_type)
                ).sort(
                    ["period_end_date", "severity", "flag_code"],
                    descending=[True, False, False],
                )
            else:
                red_flags = pl.DataFrame(
                    schema={
                        "period_end_date": pl.Date,
                        "flag_code": pl.String,
                        "severity": pl.String,
                        "flag_title_vn": pl.String,
                        "explanation_text_vn": pl.String,
                        "evidence_json": pl.String,
                        "filing_id": pl.String,
                    }
                )

            return CompanyProfileData(
                ticker=clean_ticker,
                issuer_id=issuer_id,
                company_name_vn=issuer_row.get("company_name_vn"),
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
            )

    def get_industry_comparison(
        self,
        ticker: str,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        metric_name: str = "roe_total",
    ) -> IndustryComparisonData:
        clean_ticker = ticker.strip().upper()
        with self.repository.snapshot_session() as pinned:
            issuers = pinned.read_table("issuers.parquet")
            issuer_rows = issuers.filter(pl.col("ticker") == clean_ticker)
            if issuer_rows.is_empty():
                raise ValueError(f"Ticker not found in active snapshot: {clean_ticker}")
            issuer_row = issuer_rows.row(0, named=True)
            issuer_id = str(issuer_row["issuer_id"])
            snapshot_date = issuer_row.get("industry_snapshot_date")

            benchmarks = pinned.read_table(BENCHMARKS_TABLE)
            target_rows = benchmarks.filter(
                (pl.col("issuer_id") == issuer_id)
                & (pl.col("statement_scope") == statement_scope)
                & (pl.col("period_type") == period_type)
                & (pl.col("metric_or_ratio_name") == metric_name)
            )
            if period_end_date is not None:
                target_rows = target_rows.filter(
                    pl.col("period_end_date") == period_end_date
                )
            else:
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
                    industry_snapshot_date=snapshot_date
                    if isinstance(snapshot_date, date)
                    else None,
                    peer_ranking=pl.DataFrame(),
                )

            selected = target_rows.row(0, named=True)
            resolved_end = selected["period_end_date"]
            peer_level = str(selected["peer_group_level"])
            icb_used = selected.get("icb_code_used")
            accounting_regime = str(selected["accounting_regime"])

            cohort = benchmarks.filter(
                (pl.col("accounting_regime") == accounting_regime)
                & (pl.col("statement_scope") == statement_scope)
                & (pl.col("period_type") == period_type)
                & (pl.col("period_end_date") == resolved_end)
                & (pl.col("metric_or_ratio_name") == metric_name)
                & (pl.col("peer_group_level") == peer_level)
            )
            if icb_used is not None:
                cohort = cohort.filter(pl.col("icb_code_used") == icb_used)

            peer_ranking = (
                cohort.join(
                    issuers.select(
                        "issuer_id",
                        "ticker",
                        "company_name_vn",
                        "icb_name_level2",
                        "icb_name_level3",
                    ),
                    on="issuer_id",
                    how="left",
                )
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
                industry_snapshot_date=snapshot_date
                if isinstance(snapshot_date, date)
                else None,
                peer_ranking=peer_ranking,
            )

    def get_quality_tables(self) -> tuple[pl.DataFrame, pl.DataFrame]:
        with self.repository.snapshot_session() as pinned:
            summary = _read_optional_table(pinned, QUALITY_SUMMARY_TABLE)
            quarantine = _read_optional_table(pinned, AUDIT_QUARANTINE_TABLE)
            return (
                summary if summary is not None else pl.DataFrame(),
                quarantine if quarantine is not None else pl.DataFrame(),
            )

    def list_ratio_periods(
        self, ticker: str, *, statement_scope: str | None = None
    ) -> pl.DataFrame:
        clean_ticker = ticker.strip().upper()
        with self.repository.snapshot_session() as pinned:
            ratios = pinned.read_table(RATIOS_TABLE).filter(
                pl.col("ticker") == clean_ticker
            )
            if statement_scope is not None:
                ratios = ratios.filter(pl.col("statement_scope") == statement_scope)
            return ratios.select(
                "statement_scope",
                "period_type",
                "fiscal_year",
                "fiscal_quarter",
                "period_end_date",
                "taxonomy_version",
                "filing_id",
            ).sort(["period_end_date", "period_type"], descending=[True, False])

    def get_ratio_lineage(
        self,
        ticker: str,
        *,
        statement_scope: str,
        period_type: str,
        period_end_date: date,
        ratio_name: str,
    ) -> RatioLineageCard:
        """Trace a published financial ratio back to all numerator & denominator filings.

        Returns full provenance for every component period (filing_id, taxonomy_version,
        derivation_method, source_provider, connector_name, published_at,
        published_at_basis, canonical_content_sha256) and flags any orphan references.
        """
        if ratio_name not in RATIO_LINEAGE_CATALOG:
            raise ValueError(
                f"Unknown financial ratio for lineage lookup: {ratio_name}"
            )
        spec = RATIO_LINEAGE_CATALOG[ratio_name]
        clean_ticker = ticker.strip().upper()

        with self.repository.snapshot_session() as pinned:
            ratios = pinned.read_table(RATIOS_TABLE).filter(
                (pl.col("ticker") == clean_ticker)
                & (pl.col("statement_scope") == statement_scope)
                & (pl.col("period_type") == period_type)
                & (pl.col("period_end_date") == period_end_date)
            )
            if ratios.is_empty():
                raise ValueError(
                    f"No ratio row found for {clean_ticker} {statement_scope} "
                    f"{period_type} {period_end_date.isoformat()}."
                )
            ratio_row = ratios.row(0, named=True)
            issuer_id = str(ratio_row["issuer_id"])
            anchor_filing_id = str(ratio_row["filing_id"])
            raw_source_ids = ratio_row.get("source_filing_ids") or []
            source_ids = tuple(
                sorted({anchor_filing_id, *(str(fid) for fid in raw_source_ids if fid)})
            )

            facts = _read_optional_table(pinned, "financial_facts.parquet")
            if facts is None:
                facts = pinned.read_table("latest_facts.parquet")

            manifest = _read_optional_table(pinned, FILINGS_MANIFEST_TABLE)

            known_ids = set(
                facts.filter(pl.col("filing_id").is_in(list(source_ids)))[
                    "filing_id"
                ].to_list()
            )
            orphans = tuple(sorted(set(source_ids) - known_ids))

            scoped_facts = facts.filter(
                (pl.col("issuer_id") == issuer_id)
                & (pl.col("statement_scope") == statement_scope)
                & pl.col("filing_id").is_in(list(source_ids))
            )

            manifest_lookup: dict[str, dict[str, object]] = {}
            if manifest is not None and not manifest.is_empty():
                matched_manifest = manifest.filter(
                    pl.col("filing_id").is_in(list(source_ids))
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
                    # Quarterly TTM: latest 4 discrete quarters ending at period_end_date
                    # for flow metrics, or exact period_end_date for stock numerators.
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

                # DENOMINATOR windowing by denominator_mode
                mode = spec.denominator_mode
                if mode == "POINT_IN_TIME_STOCK":
                    return frame.filter(pl.col("period_end_date") == period_end_date)
                if mode == "AVERAGE_STOCK":
                    # Closing stock at period_end_date + most recent opening stock prior to it.
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
