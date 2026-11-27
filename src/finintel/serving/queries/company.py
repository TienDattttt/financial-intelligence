"""Headless read-only DuckDB query submodule (ratio lineage catalog, benchmarks summary, and lineage card queries) for Layer 5 serving.

Verified Sources:
- DuckDB Python API & Multithreading (``duckdb==1.4.2``, non-owning ``conn.cursor()``):
  https://duckdb.org/docs/stable/guides/python/multiple_threads
- DuckLake Official Documentation (time travel ``AT (VERSION => ...)``):
  https://ducklake.select/docs/stable/
- Rouen, E., So, E. C., & Wang, C. C. Y. (2021). "Core Earnings: New Data and Evidence."
  Journal of Financial Economics, Vol. 142, Issue 3, pp. 1068-1091.
- Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
  IMF Working Paper WP/15/216.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import duckdb
import polars as pl

from finintel.serving.dictionary import OVERVIEW_CORE_RATIOS, RATIO_DICTIONARY_META
from finintel.serving.queries._cache import (
    _MAX_QUERY_CACHE_SIZE,
    _QUERY_CACHE_LOCK,
    _RATIO_LINEAGE_CACHE,
    PERIOD_TYPES,
    STATEMENT_SCOPES,
    RatioLineageCard,
    RatioLineageSpec,
    _available_table_columns,
    _can_use_query_result_cache,
    _evict_stale_l1b_caches_locked,
    _QueryServiceBase,
    _resolve_issuer_row,
    _select_existing_columns,
)
from finintel.serving.queries._guards import (
    apply_d8_ratio_guards,
    guard_ratio_by_denominator,
)
from finintel.serving.queries._helpers import (
    extract_lineage_components,
    opt_finite_float,
    resolve_benchmark_cohort_fallback,
)
from finintel.storage.schemas import (
    BENCHMARKS_TABLE,
    CORE_RATIOS,
    EXTRA_RATIOS,
    FILINGS_MANIFEST_TABLE,
    FORENSIC_RATIOS,
    RATIOS_TABLE,
    V3_RATIOS,
)

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
    "ocf_to_total_liabilities": RatioLineageSpec(
        "ocf_to_total_liabilities",
        "CFO / Tổng nợ phải trả",
        "CF_OPERATING_CASH_FLOW / BS_TOTAL_LIABILITIES",
        ("CF_OPERATING_CASH_FLOW",),
        ("BS_TOTAL_LIABILITIES",),
        "POINT_IN_TIME_STOCK",
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

ALL_RATIOS: tuple[str, ...] = (
    *CORE_RATIOS,
    *EXTRA_RATIOS,
    *FORENSIC_RATIOS,
    *V3_RATIOS,
)


class CompanyQueryMixin(_QueryServiceBase):
    """Ratio lineage catalog, benchmarks summary, and ratio lineage card queries."""

    def get_company_benchmarks_summary(
        self,
        ticker: str,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        metrics: Sequence[str] | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> tuple[str, date | None, list[dict[str, object]]]:
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
                columns=(
                    "issuer_id",
                    "ticker",
                    "accounting_regime",
                    "icb_code_level2",
                    "icb_code_level3",
                ),
                connection=connection,
            )
            issuer_id = str(issuer_row["issuer_id"])

            avail_ratio_cols = _available_table_columns(
                pinned, RATIOS_TABLE, connection=connection
            )
            if not avail_ratio_cols:
                return issuer_id, period_end_date, []
            denom_cols = [
                c
                for c, needed in (
                    ("net_revenue_ttm", "ebit_margin" in selected_metrics),
                    ("borrowing_cost_ttm", "interest_coverage" in selected_metrics),
                )
                if needed
            ]
            ratio_cols = _select_existing_columns(
                ("period_end_date", *selected_metrics, *denom_cols), avail_ratio_cols
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
                return issuer_id, period_end_date, []
            ratios = apply_d8_ratio_guards(ratios)

            resolved_date = period_end_date
            if resolved_date is None:
                raw_latest = ratios.get_column("period_end_date").max()
                if not isinstance(raw_latest, date):
                    return issuer_id, period_end_date, []
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
                missing_metrics = [
                    m for m in selected_metrics if m not in bench_by_metric
                ]
                if missing_metrics:
                    raw_l3 = issuer_row.get("icb_code_level3")
                    raw_l2 = issuer_row.get("icb_code_level2")
                    bench_by_metric.update(
                        resolve_benchmark_cohort_fallback(
                            pinned,
                            avail_bench_cols=avail_bench_cols,
                            accounting_regime=str(
                                issuer_row.get("accounting_regime") or "CORPORATE"
                            ),
                            icb_code_level3=str(raw_l3) if raw_l3 is not None else None,
                            icb_code_level2=str(raw_l2) if raw_l2 is not None else None,
                            statement_scope=statement_scope,
                            period_type=period_type,
                            period_end_date=resolved_date,
                            metric_names=missing_metrics,
                            connection=connection,
                        )
                    )

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
                company_val, _ = guard_ratio_by_denominator(
                    metric_name,
                    opt_finite_float(b_row.get("value")),
                    net_revenue_ttm=opt_finite_float(
                        latest_ratio_row.get("net_revenue_ttm")
                    ),
                    borrowing_cost_ttm=opt_finite_float(
                        latest_ratio_row.get("borrowing_cost_ttm")
                    ),
                )
                if company_val is None:
                    company_val = opt_finite_float(latest_ratio_row.get(metric_name))
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
                        "peer_median": opt_finite_float(b_row.get("peer_median")),
                        "peer_p25": opt_finite_float(b_row.get("peer_p25")),
                        "peer_p75": opt_finite_float(b_row.get("peer_p75")),
                        "percentile_rank": opt_finite_float(
                            b_row.get("percentile_rank")
                        ),
                        "robust_z_score": opt_finite_float(b_row.get("robust_z_score")),
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
            extra_denom = (
                ("net_revenue_ttm",)
                if ratio_name == "ebit_margin"
                else (
                    ("borrowing_cost_ttm",) if ratio_name == "interest_coverage" else ()
                )
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
                *extra_denom,
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

            raw_ratio_val = opt_finite_float(ratio_row.get(ratio_name))
            raw_status_flag = (
                str(ratio_row[f"{ratio_name}_flag"])
                if ratio_row.get(f"{ratio_name}_flag") is not None
                else None
            )
            guarded_ratio_val, guarded_status_flag = guard_ratio_by_denominator(
                ratio_name,
                raw_ratio_val,
                net_revenue_ttm=opt_finite_float(ratio_row.get("net_revenue_ttm")),
                borrowing_cost_ttm=opt_finite_float(
                    ratio_row.get("borrowing_cost_ttm")
                ),
                status_flag=raw_status_flag,
            )

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
                ratio_value=guarded_ratio_val,
                status_flag=guarded_status_flag,
                taxonomy_version=str(ratio_row["taxonomy_version"]),
                denominator_mode=spec.denominator_mode,
                anchor_filing_id=anchor_filing_id,
                source_filing_ids=source_ids,
                numerator_components=extract_lineage_components(
                    scoped_facts,
                    role="NUMERATOR",
                    metrics=spec.numerator_metrics,
                    period_type=period_type,
                    period_end_date=period_end_date,
                    denominator_mode=spec.denominator_mode,
                    manifest_lookup=manifest_lookup,
                ),
                denominator_components=extract_lineage_components(
                    scoped_facts,
                    role="DENOMINATOR",
                    metrics=spec.denominator_metrics,
                    period_type=period_type,
                    period_end_date=period_end_date,
                    denominator_mode=spec.denominator_mode,
                    manifest_lookup=manifest_lookup,
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
