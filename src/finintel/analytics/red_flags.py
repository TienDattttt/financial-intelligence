"""Screen accounting and solvency risks with reproducible numeric evidence.

Verified Sources:
    https://www.cuhk.edu.hk/acy2/workshop/June2009Wasley/1996TAR%29.pdf
    Sloan scales balance-sheet-derived accruals by average assets. Layer 4
    uses the direct (net income minus operating cash flows) proxy instead.
    Rouen, E., So, E. C., & Wang, C. C. Y. (2021). "Core Earnings: New Data and Evidence."
    Journal of Financial Economics, Vol. 142, Issue 3, pp. 1068-1091.
    Schilit, H. M., Perler, J., & Engelhart, Y. (2018). Financial Shenanigans: How to Detect
    Accounting Gimmicks and Fraud in Financial Reports (4th Ed., McGraw-Hill) —
    Shenanigan #3 (RF_08) & Shenanigan #4 (RF_09).
    Dechow, P. M., Ge, W., Larson, C. R., & Sloan, R. G. (2011). "Predicting Material Accounting
    Misstatements." Contemporary Accounting Research, Vol. 28, Issue 1, pp. 17-82.
    https://www.mheducation.com/highered/mhp/product/financial-shenanigans-fourth-edition.html
    The publisher describes earnings, cash flow and balance-sheet signals.
    The numeric cutoffs and the VAS receivables screen come from Layer 4.
    https://congbao.chinhphu.vn/tai-ve-van-ban-so-200-2014-tt-btc-6697-3551?format=pdf
    https://congbao.chinhphu.vn/tai-ve-van-ban-so-99-2025-tt-btc-46529-59655?format=pdf
    Lines 130/210 are net of doubtful-debt provisions. Lines 131/211
    show gross customer receivables before those provisions.
    BS_CIP_LONG_TERM maps TT200 code 242 to TT99 code 252; TT99 B02-DN code 21
    tracks IS_INVESTMENT_PROPERTY_DISPOSAL_GAIN_LOSS.
    RF_08 is a preliminary forensic screen: for holding companies with joint-venture/associate
    profits (B02-DN/HN code 24 when not reported separately) or cash-rich issuers earning stable
    bank deposit interest (IS_FINANCIAL_REVENUE), analysts must inspect financial statement notes.
    https://vanban.chinhphu.vn/default.aspx?docid=201902&pageid=27160
    https://cms.hnx.vn/vi-vn/huong-dan/chi-tiet-thu-tuc-21-30.html
    https://staticfile.hsx.vn/Uploads/LocalFiles/c79e12a5dbf749b6983b63e3242326a3/20210101_Thong%20tu%2096.2020%20TT-BTC.pdf
    Consume verified late-filing evidence when supplied by ingestion.
    Gate 0's FHSC payloads lack authoritative disclosure dates and audit opinions.
"""

import polars as pl

from finintel.analytics.intermediates import (
    ADMIN_EXPENSES,
    ASSETS,
    BORROWING_COSTS,
    CFO,
    CIP_LONG_TERM,
    COGS,
    CORPORATE,
    EARNINGS,
    EBT,
    EQUITY,
    FINANCIAL_REVENUE,
    GROSS_PROFIT,
    INTEREST,
    INVENTORY,
    IP_DISPOSAL,
    LT_RECEIVABLE_INPUTS,
    OTHER_EXPENSES,
    OTHER_INCOME,
    OTHER_PROFIT,
    REVENUE,
    RF03_INPUTS,
    SELLING_EXPENSES,
    SERIES,
    ST_RECEIVABLE_INPUTS,
    TANGIBLE_ASSETS,
    TRADE_RECEIVABLES,
    combine_filing_ids,
    cross_taxonomy,
    input_has,
    with_prior_year,
)
from finintel.storage.schemas import FLAG_KEY


def _balance_sheet_amount(metric: str) -> pl.Expr:
    """Read a balance-sheet input only from the selected total-assets filing."""
    return pl.when(pl.col(f"_filing_{metric}") == pl.col(f"_filing_{ASSETS}")).then(
        pl.col(metric)
    )


def _prior_balance_sheet_amount(metric: str) -> pl.Expr:
    """Read a prior-period balance-sheet input only from the prior total-assets filing."""
    return pl.when(
        pl.col(f"_filing_{metric}_prior") == pl.col(f"_filing_{ASSETS}_prior")
    ).then(pl.col(f"{metric}_prior"))


def _net_non_customer_receivables(inputs: tuple[str, str, str, str]) -> pl.Expr:
    """Bound or compute net non-customer receivables for one maturity.

    The balance-sheet provision is nonpositive. When the filing notes supply a
    verified customer allowance A_m in [0, min(C_m, -P_m)], compute exact
    N_m = T_m - (C_m - A_m). When no customer-allowance note is available,
    use the tight mathematical lower bound max(0.0, T_m - C_m) provided the
    balance sheet admits a valid allocation (T_m - C_m + min(C_m, -P_m) >= 0).
    """
    total, trade, provision, reported = map(_balance_sheet_amount, inputs)
    max_allowance = pl.min_horizontal(trade, -provision)
    exact_net = total - (trade - reported)
    exact_valid = (
        reported.is_not_null()
        & (trade >= 0)
        & (provision <= 0)
        & (reported >= 0)
        & (reported <= max_allowance)
        & (exact_net >= 0)
    )
    lower_bound_valid = (
        reported.is_null()
        & total.is_not_null()
        & (trade >= 0)
        & (provision <= 0)
        & ((total - trade + max_allowance) >= 0)
    )
    lower_bound_net = pl.max_horizontal(pl.lit(0.0), total - trade)
    return (
        pl.when(exact_valid)
        .then(exact_net)
        .when(lower_bound_valid)
        .then(lower_bound_net)
    )


def detect_red_flags(ratios: pl.DataFrame) -> pl.DataFrame:
    """Emit the ten screening rules (RF_01..RF_10), with no trigger on missing evidence.

    Compute receivable growth (current/prior − 1), adjusted net non-customer
    receivables / BS_TOTAL_ASSETS, and inventory / BS_TOTAL_ASSETS. Require positive
    denominators and valid matched periods. The remaining rules use financial
    ratios, verified audit/disclosure evidence and equity already computed.
    """
    c = pl.col
    end = c("period_end_date")
    years, quarters = c("fiscal_year"), c("fiscal_quarter")
    slot = years * 4 + quarters.fill_null(4)
    annual = c("period_type") == "ANNUAL"

    def previous(expression: pl.Expr) -> pl.Expr:
        return expression.shift(1).over(SERIES, order_by="period_end_date")

    days = (end - previous(end)).dt.total_days()
    adjacent = (
        annual & ((years - previous(years)) == 1) & days.is_between(350, 380)
    ) | (~annual & ((slot - previous(slot)) == 1) & days.is_between(80, 100))
    both_comparable = pl.all_horizontal(
        *(
            c(f"_comparable_{name}") & previous(c(f"_comparable_{name}"))
            for name in (EARNINGS, CFO)
        )
    )
    same_taxonomy = c("taxonomy_version") == previous(c("taxonomy_version"))
    cash_divergence = (
        (c(EARNINGS) > 0) & (c(CFO) < 0) & ~input_has("mixed", EARNINGS, CFO)
    )
    assets = c(ASSETS)
    trade = c(TRADE_RECEIVABLES)
    short_other = _net_non_customer_receivables(ST_RECEIVABLE_INPUTS)
    long_other = _net_non_customer_receivables(LT_RECEIVABLE_INPUTS)
    other_receivables = (short_other + long_other) / assets
    prior_trade = c(f"{TRADE_RECEIVABLES}_prior")
    rf10_defaults: list[pl.Expr] = []
    for rf10_col, rf10_default in (
        ("ni_unaudited_4q_sum", pl.lit(None, dtype=pl.Float64)),
        ("ni_audited_annual", pl.lit(None, dtype=pl.Float64)),
        ("audit_earnings_diff_vnd", pl.lit(None, dtype=pl.Float64)),
        ("audit_adjustment_pct", pl.lit(None, dtype=pl.Float64)),
        ("_rf10_triggered", pl.lit(False, dtype=pl.Boolean)),
        ("_rf10_source_filing_ids", pl.lit([], dtype=pl.List(pl.String))),
    ):
        if rf10_col not in ratios.columns:
            rf10_defaults.append(rf10_default.alias(rf10_col))
    base_ratios = ratios.with_columns(rf10_defaults) if rf10_defaults else ratios
    frame = with_prior_year(
        base_ratios.drop("_prior_end"),
        "dio",
        "dso",
        "gross_margin",
        "source_filing_ids",
    ).with_columns(
        (
            adjacent
            & cash_divergence
            & previous(cash_divergence)
            & (same_taxonomy | both_comparable)
            & (c("accruals_to_assets") > 0.10)
        ).alias("_accrual_divergence"),
        previous(c(EARNINGS)).alias("previous_net_income_ttm"),
        previous(c(CFO)).alias("previous_cfo_ttm"),
        previous(c("_period_filing_ids")).alias("_previous_filing_ids"),
        pl.when((prior_trade > 0) & ~cross_taxonomy(TRADE_RECEIVABLES, REVENUE))
        .then((trade - prior_trade) / prior_trade)
        .alias("trade_receivables_growth_yoy"),
        pl.when(
            (assets > 0)
            & other_receivables.is_finite()
            & ~input_has("mixed", *RF03_INPUTS)
        )
        .then(other_receivables)
        .alias("other_receivables_to_assets"),
        pl.when((assets > 0) & ~input_has("mixed", ASSETS, INVENTORY))
        .then(c(INVENTORY) / assets)
        .alias("inventory_to_assets"),
        (c("dso") - c("dso_prior")).alias("dso_change_days"),
        (c("dio") - c("dio_prior")).alias("dio_change_days"),
    )
    revenue_growth = c("revenue_growth_yoy")
    assets_prior = c(f"{ASSETS}_prior")
    cip_t = _balance_sheet_amount(CIP_LONG_TERM)
    cip_prior = _prior_balance_sheet_amount(CIP_LONG_TERM)
    tfa_t = _balance_sheet_amount(TANGIBLE_ASSETS)
    tfa_prior = _prior_balance_sheet_amount(TANGIBLE_ASSETS)
    rf08_precondition = (
        CORPORATE
        & (c(EBT) > 0)
        & c("core_operating_profit_ttm").is_not_null()
        & c("borrowing_cost_ttm").is_not_null()
        & ~input_has(
            "mixed",
            EBT,
            GROSS_PROFIT,
            SELLING_EXPENSES,
            ADMIN_EXPENSES,
            INTEREST,
            BORROWING_COSTS,
        )
    )
    rf08_branch1 = (c("core_operating_profit_ttm") - c("borrowing_cost_ttm")) <= 0
    rf08_branch2 = (
        c("core_to_ebt_ratio").is_not_null()
        & c("non_core_distortion_to_assets").is_not_null()
        & (c("core_to_ebt_ratio") < 0.50)
        & (c("non_core_distortion_to_assets") > 0.03)
    )
    rf09_condition = (
        CORPORATE
        & (assets > 0)
        & (assets_prior > 0)
        & (cip_t > 0)
        & (cip_prior > 0)
        & ~cross_taxonomy(ASSETS, CIP_LONG_TERM, TANGIBLE_ASSETS, REVENUE)
        & ~input_has(
            "mixed",
            ASSETS,
            CIP_LONG_TERM,
            TANGIBLE_ASSETS,
            REVENUE,
            f"{ASSETS}_prior",
            f"{CIP_LONG_TERM}_prior",
            f"{TANGIBLE_ASSETS}_prior",
            f"{REVENUE}_prior",
        )
        & ((cip_t / assets) > 0.20)
        & ((cip_prior / assets_prior) > 0.20)
        & (cip_t >= 0.95 * cip_prior)
        & revenue_growth.is_not_null()
        & (revenue_growth <= 0.05)
        & tfa_t.is_not_null()
        & tfa_prior.is_not_null()
        & (tfa_t <= 1.05 * tfa_prior)
    )
    rules = [
        (
            "RF_01_ACCRUAL_DIVERGENCE",
            "HIGH",
            "Lợi nhuận và dòng tiền trái chiều",
            "Lợi nhuận dương và dòng tiền kinh doanh âm hai kỳ liên tiếp, với tỷ lệ dồn tích vượt 10%.",
            CORPORATE & c("_accrual_divergence"),
            [
                c(EARNINGS).alias("net_income_ttm"),
                c(CFO).alias("cfo_ttm"),
                c("previous_net_income_ttm"),
                c("previous_cfo_ttm"),
                c("accruals_to_assets"),
            ],
        ),
        (
            "RF_02_RECEIVABLES_SURGE",
            "MEDIUM",
            "Phải thu khách hàng tăng nhanh",
            "Phải thu tăng vượt ngưỡng theo tăng trưởng doanh thu và số ngày thu tiền tăng hơn 15 ngày.",
            CORPORATE
            & (revenue_growth > 0)
            & (
                c("trade_receivables_growth_yoy")
                > pl.max_horizontal(1.5 * revenue_growth, 0.25)
            )
            & (c("dso_change_days") > 15),
            [
                revenue_growth,
                c("trade_receivables_growth_yoy"),
                c("dso"),
                c("dso_prior"),
                c("dso_change_days"),
            ],
        ),
        (
            "RF_03_OTHER_RECEIVABLES_ANOMALY",
            "HIGH",
            "Phải thu ngoài khách hàng ở mức cao",
            "Phải thu thuần ngoài khách hàng ở cả ngắn hạn và dài hạn vượt 25% tổng tài sản.",
            CORPORATE & (c("other_receivables_to_assets") > 0.25),
            [
                assets,
                *(
                    _balance_sheet_amount(metric).alias(metric)
                    for metric in RF03_INPUTS[1:]
                ),
                short_other.alias("net_non_customer_receivables_st"),
                long_other.alias("net_non_customer_receivables_lt"),
                c("other_receivables_to_assets"),
            ],
        ),
        (
            "RF_04_INVENTORY_BLOAT",
            "MEDIUM",
            "Tồn kho luân chuyển chậm",
            "Tồn kho vượt 15% tài sản, số ngày tồn kho tăng hơn 30 ngày và biên gộp suy giảm.",
            CORPORATE
            & (c("inventory_to_assets") > 0.15)
            & (c("dio_change_days") > 30)
            & (c("gross_margin") < c("gross_margin_prior"))
            & ~cross_taxonomy(INVENTORY, COGS, GROSS_PROFIT, REVENUE),
            [
                c("inventory_to_assets"),
                c("dio"),
                c("dio_prior"),
                c("dio_change_days"),
                c("gross_margin"),
                c("gross_margin_prior"),
            ],
        ),
        (
            "RF_05_DEBT_SERVICE_DISTRESS",
            "CRITICAL",
            "Áp lực trả nợ",
            "Lợi nhuận trước lãi và thuế thiếu để trả lãi hoặc dòng tiền âm đi cùng thanh khoản và đòn bẩy yếu.",
            CORPORATE
            & (
                ((c("interest_coverage") < 1) & (c("total_debt") > 0))
                | ((c("current_ratio") < 1) & (c(CFO) < 0) & (c("debt_to_equity") > 2))
            ),
            [
                c("interest_coverage"),
                c("total_debt"),
                c("current_ratio"),
                c(CFO),
                c("debt_to_equity"),
            ],
        ),
        (
            "RF_06_NEGATIVE_EQUITY",
            "CRITICAL",
            "Vốn chủ sở hữu không dương",
            "Vốn chủ sở hữu tổng hoặc vốn thuộc cổ đông công ty mẹ bằng 0 hoặc âm.",
            (c(EQUITY) <= 0) | (c("parent_equity") <= 0),
            [c(EQUITY), c("parent_equity")],
        ),
        (
            "RF_07_AUDIT_OR_FILING_WARNING",
            "CRITICAL",
            "Cảnh báo kiểm toán hoặc công bố",
            "Hồ sơ ghi nhận ý kiến kiểm toán ngoại trừ, trái ngược, từ chối hoặc nộp báo cáo trễ.",
            c("audit_filing_id").is_not_null(),
            [c("audit_opinion"), c("is_filed_late")],
        ),
        (
            "RF_08_NON_CORE_EARNINGS_DEPENDENCE",
            "HIGH",
            "Phụ thuộc lợi nhuận phi cốt lõi",
            "Lợi nhuận trước thuế dương nhưng lợi nhuận kinh doanh lõi sau lãi vay âm hoặc đóng góp dưới 50% EBT với độ lệch phi lõi vượt 3% tài sản bình quân. Lưu ý giới hạn tầm soát sơ bộ: với các tập đoàn holding có lãi công ty liên doanh liên kết (Mã 24 B02-DN/HN chưa tách riêng) hoặc doanh nghiệp thừa tiền mặt hưởng lãi tiền gửi ngân hàng ổn định (IS_FINANCIAL_REVENUE), nhà phân tích cần đọc thuyết minh kiểm chứng nguồn thu tài chính.",
            rf08_precondition & (rf08_branch1 | rf08_branch2),
            [
                c(EBT).alias("ebt_ttm"),
                c("core_operating_profit_ttm"),
                c("borrowing_cost_ttm").alias("debt_cost_ttm"),
                c("core_to_ebt_ratio"),
                c("non_core_distortion_to_assets"),
                pl.when(~input_has("mixed", FINANCIAL_REVENUE))
                .then(c(FINANCIAL_REVENUE))
                .alias("financial_revenue_ttm"),
                pl.when(~input_has("mixed", OTHER_PROFIT, OTHER_INCOME, OTHER_EXPENSES))
                .then(pl.coalesce(c(OTHER_PROFIT), c(OTHER_INCOME) - c(OTHER_EXPENSES)))
                .alias("other_profit_ttm"),
                pl.when(
                    (c("taxonomy_version") == "VAS_TT99_2025")
                    & ~input_has("mixed", IP_DISPOSAL)
                )
                .then(c(IP_DISPOSAL))
                .alias("ip_disposal_gain_loss_ttm"),
            ],
        ),
        (
            "RF_09_FROZEN_CIP_CAPITALIZATION",
            "MEDIUM",
            "Xây dựng cơ bản dở dang treo kéo dài",
            "Chi phí xây dựng cơ bản dở dang dài hạn vượt 20% tổng tài sản hai năm liên tiếp và không giảm quá 5%, trong khi tài sản cố định hữu hình và doanh thu tăng trưởng không quá 5%.",
            rf09_condition,
            [
                (cip_t / assets).alias("cip_to_assets_t"),
                (cip_prior / assets_prior).alias("cip_to_assets_prior"),
                ((cip_t - cip_prior) / cip_prior).alias("cip_growth_yoy"),
                pl.when(tfa_prior > 0)
                .then((tfa_t - tfa_prior) / tfa_prior)
                .otherwise(None)
                .cast(pl.Float64)
                .alias("tangible_fixed_assets_growth_yoy"),
                revenue_growth.alias("revenue_growth_yoy"),
            ],
        ),
        (
            "RF_10_POST_AUDIT_PROFIT_DOWNGRADE",
            "HIGH",
            "Lợi nhuận suy giảm trọng yếu hoặc đảo chiều lỗ sau kiểm toán",
            "Tổng lợi nhuận sau thuế 4 quý tự lập trước kiểm toán bị điều chỉnh giảm từ 15% trở lên (tối thiểu 5 tỷ đồng) hoặc đảo chiều từ lãi sang lỗ (tối thiểu 1 tỷ đồng) trên báo cáo tài chính năm đã kiểm toán.",
            CORPORATE & c("_rf10_triggered").fill_null(False),
            [
                c("ni_unaudited_4q_sum"),
                c("ni_audited_annual"),
                c("audit_earnings_diff_vnd"),
                c("audit_adjustment_pct"),
            ],
        ),
    ]
    filings = c("source_filing_ids")
    evidence_filings = {
        "RF_01_ACCRUAL_DIVERGENCE": combine_filing_ids(
            filings, c("_previous_filing_ids")
        ),
        "RF_02_RECEIVABLES_SURGE": combine_filing_ids(
            filings, c("source_filing_ids_prior")
        ),
        "RF_03_OTHER_RECEIVABLES_ANOMALY": pl.concat_list(f"_filing_{ASSETS}"),
        "RF_04_INVENTORY_BLOAT": combine_filing_ids(
            filings, c("source_filing_ids_prior")
        ),
        "RF_06_NEGATIVE_EQUITY": c("_period_filing_ids"),
        "RF_07_AUDIT_OR_FILING_WARNING": pl.concat_list("audit_filing_id"),
        "RF_09_FROZEN_CIP_CAPITALIZATION": combine_filing_ids(
            filings, c("source_filing_ids_prior")
        ),
        "RF_10_POST_AUDIT_PROFIT_DOWNGRADE": c("_rf10_source_filing_ids"),
    }
    evidence_filing = {
        "RF_03_OTHER_RECEIVABLES_ANOMALY": c(f"_filing_{ASSETS}"),
        "RF_07_AUDIT_OR_FILING_WARNING": c("audit_filing_id"),
    }
    return pl.concat(
        [
            frame.filter(condition).select(
                *FLAG_KEY[:-1],
                pl.lit(code).alias("flag_code"),
                pl.lit(severity).alias("severity"),
                pl.lit(title).alias("flag_title_vn"),
                pl.lit(explanation).alias("explanation_text_vn"),
                pl.struct(
                    *evidence,
                    evidence_filings.get(code, filings).alias("source_filing_ids"),
                )
                .struct.json_encode()
                .alias("evidence_json"),
                evidence_filing.get(code, c("filing_id")).alias("filing_id"),
            )
            for code, severity, title, explanation, condition, evidence in rules
        ]
    ).sort(FLAG_KEY)
