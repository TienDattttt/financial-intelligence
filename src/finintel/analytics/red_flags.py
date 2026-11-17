"""Screen accounting and solvency risks with reproducible numeric evidence.

Verified Sources:
    https://www.cuhk.edu.hk/acy2/workshop/June2009Wasley/1996TAR%29.pdf
    Sloan scales balance-sheet-derived accruals by average assets. Layer 4
    uses the direct (net income minus operating cash flows) proxy instead.
    https://www.mheducation.com/highered/mhp/product/financial-shenanigans-fourth-edition.html
    The publisher describes earnings, cash flow and balance-sheet signals.
    The numeric cutoffs and the VAS receivables screen come from Layer 4.
    https://vanban.chinhphu.vn/default.aspx?docid=201902&pageid=27160
    https://cms.hnx.vn/vi-vn/huong-dan/chi-tiet-thu-tuc-21-30.html
    https://staticfile.hsx.vn/Uploads/LocalFiles/c79e12a5dbf749b6983b63e3242326a3/20210101_Thong%20tu%2096.2020%20TT-BTC.pdf
    Consume verified late-filing evidence when supplied by ingestion.
    Gate 0's FHSC payloads lack authoritative disclosure dates and audit opinions.
"""

import polars as pl

from finintel.analytics.intermediates import (
    ASSETS,
    CFO,
    COGS,
    CORPORATE,
    EARNINGS,
    EQUITY,
    GROSS_PROFIT,
    INVENTORY,
    LT_RECEIVABLES,
    LT_TRADE_RECEIVABLES,
    RECEIVABLES,
    REVENUE,
    SERIES,
    TRADE_RECEIVABLES,
    combine_filing_ids,
    cross_taxonomy,
    input_has,
    with_prior_year,
)
from finintel.storage.schemas import FLAG_KEY


def detect_red_flags(ratios: pl.DataFrame) -> pl.DataFrame:
    """Emit the seven screening rules, with no trigger on missing evidence.

    Compute receivable growth (current/prior − 1), other receivables
    ((BS_SHORT_TERM_RECEIVABLES − BS_TRADE_RECEIVABLES_ST)
     + (BS_LT_RECEIVABLES − BS_TRADE_RECEIVABLES_LT))
    / BS_TOTAL_ASSETS, and inventory / BS_TOTAL_ASSETS. Require positive
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
    other_receivables = (
        (c(RECEIVABLES) - trade) + (c(LT_RECEIVABLES) - c(LT_TRADE_RECEIVABLES))
    ) / assets
    prior_trade = c(f"{TRADE_RECEIVABLES}_prior")
    frame = with_prior_year(
        ratios.drop("_prior_end"), "dio", "dso", "gross_margin", "source_filing_ids"
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
            & ~input_has(
                "mixed",
                ASSETS,
                RECEIVABLES,
                TRADE_RECEIVABLES,
                LT_RECEIVABLES,
                LT_TRADE_RECEIVABLES,
            )
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
            "Phải thu ngoài khách hàng ở cả ngắn hạn và dài hạn vượt 25% tổng tài sản.",
            CORPORATE & (c("other_receivables_to_assets") > 0.25),
            [
                c(RECEIVABLES),
                trade,
                c(LT_RECEIVABLES),
                c(LT_TRADE_RECEIVABLES),
                assets,
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
    ]
    filings = c("source_filing_ids")
    evidence_filings = {
        "RF_01_ACCRUAL_DIVERGENCE": combine_filing_ids(
            filings, c("_previous_filing_ids")
        ),
        "RF_02_RECEIVABLES_SURGE": combine_filing_ids(
            filings, c("source_filing_ids_prior")
        ),
        "RF_03_OTHER_RECEIVABLES_ANOMALY": c("_period_filing_ids"),
        "RF_04_INVENTORY_BLOAT": combine_filing_ids(
            filings, c("source_filing_ids_prior")
        ),
        "RF_06_NEGATIVE_EQUITY": c("_period_filing_ids"),
        "RF_07_AUDIT_OR_FILING_WARNING": pl.concat_list("audit_filing_id"),
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
                (
                    c("audit_filing_id") if code.startswith("RF_07") else c("filing_id")
                ).alias("filing_id"),
            )
            for code, severity, title, explanation, condition, evidence in rules
        ]
    ).sort(FLAG_KEY)
