"""Corporate accounting screens calculated from annual or annualized inputs.

Verified Sources:
    https://www.blackwellpublishing.com/content/BPL_images/Content_Store/Sample_Chapter/9780631225638/altman.pdf
    https://www.ivey.uwo.ca/media/3775523/value_investing_the_use_of_historical_financial_statement_information.pdf
    https://bpb-us-w2.wpmucdn.com/sites.udel.edu/dist/a/855/files/2020/07/Earnings-Manipulation-and-Expected-Returns.pdf
    https://github.com/JerBouma/FinanceToolkit/tree/main/financetoolkit/models
    Layer 4 uses book equity for Altman Z'', a filing-note check and average
    assets for Piotroski, and the 2013 cash-flow variant of Beneish accruals.
    TT99 Appendix IV renumbers retained earnings and long-term investments.
"""

import polars as pl

from finintel.analytics.intermediates import (
    ADMIN_EXPENSES,
    ASSETS,
    CFO,
    CONTINUING_EARNINGS,
    CORPORATE,
    CURRENT_ASSETS,
    CURRENT_LIABILITIES,
    DEPRECIATION,
    EARNINGS,
    EQUITY,
    EQUITY_PROCEEDS,
    GROSS_PROFIT,
    LIABILITIES,
    LT_DEBT,
    LT_INVESTMENTS,
    NONCASH_ISSUANCE,
    RETAINED_EARNINGS,
    REVENUE,
    SELLING_EXPENSES,
    TANGIBLE_ASSETS,
    TRADE_RECEIVABLES,
    average,
    cross_taxonomy,
    input_has,
    with_prior_year,
)

c = pl.col


def _divide(numerator: pl.Expr, denominator: pl.Expr) -> pl.Expr:
    result = numerator / denominator
    return pl.when((denominator > 0) & result.is_finite()).then(result)


def _altman(frame: pl.DataFrame) -> pl.DataFrame:
    """Weight working capital, retained earnings and EBIT/assets, plus book equity/debt."""
    assets, equity, liabilities = c(ASSETS), c(EQUITY), c(LIABILITIES)
    valid_x1 = _divide(c("working_capital"), assets)
    valid_x2 = _divide(c(RETAINED_EARNINGS), assets)
    valid_x3 = _divide(c("ebit_ttm"), assets)
    valid_x4 = (
        pl.when((liabilities == 0) & (equity > 0))
        .then(10.0)
        .otherwise(_divide(equity, liabilities))
    )
    z = 6.56 * valid_x1 + 3.26 * valid_x2 + 6.72 * valid_x3 + 1.05 * valid_x4
    score_valid = pl.all_horizontal(
        valid_x1.is_not_null(),
        valid_x2.is_not_null(),
        valid_x3.is_not_null(),
        valid_x4.is_not_null(),
    )
    return frame.with_columns(
        pl.when(score_valid & z.is_finite()).then(z).alias("altman_z_double_prime")
    ).with_columns(
        pl.when(c("altman_z_double_prime").is_not_null())
        .then(c("altman_z_double_prime") + 3.25)
        .alias("altman_em_score"),
        pl.when(c("altman_z_double_prime") > 2.60)
        .then(pl.lit("SAFE_ZONE"))
        .when(c("altman_z_double_prime") >= 1.10)
        .then(pl.lit("GREY_ZONE"))
        .when(c("altman_z_double_prime") < 1.10)
        .then(pl.lit("DISTRESS_ZONE"))
        .alias("altman_zone"),
    )


def _piotroski(frame: pl.DataFrame) -> pl.DataFrame:
    """Sum nine known signals using same-regime annual comparisons and filing evidence."""
    if "_prior_end" in frame.columns:
        frame = frame.drop("_prior_end")
    leverage = pl.when(
        (c(ASSETS) > 0) & (c(f"{ASSETS}_prior") > 0) & ~cross_taxonomy(ASSETS)
    ).then(_divide(c(LT_DEBT), average(ASSETS)))
    frame = with_prior_year(
        frame.with_columns(leverage.alias("_lt_leverage")),
        "roa",
        "_lt_leverage",
        "current_ratio",
        "gross_margin",
        "asset_turnover",
    )
    proceeds, noncash = c(EQUITY_PROCEEDS), c(NONCASH_ISSUANCE)
    signals = (
        pl.when(c("roa_flag") != "MIXED_TAXONOMY").then(c("roa") > 0),
        pl.when(~input_has("mixed", CFO)).then(c(CFO) > 0),
        pl.when(~cross_taxonomy(ASSETS, EARNINGS)).then(c("roa") > c("roa_prior")),
        pl.when(~input_has("mixed", CFO, EARNINGS)).then(c(CFO) > c(EARNINGS)),
        pl.when(~cross_taxonomy(ASSETS, LT_DEBT)).then(
            c("_lt_leverage") <= c("_lt_leverage_prior")
        ),
        pl.when(~cross_taxonomy(CURRENT_ASSETS, CURRENT_LIABILITIES)).then(
            c("current_ratio") > c("current_ratio_prior")
        ),
        pl.when((proceeds > 0) | (noncash > 0))
        .then(False)
        .when((proceeds == 0) & (noncash == 0))
        .then(True),
        pl.when(~cross_taxonomy(GROSS_PROFIT, REVENUE)).then(
            c("gross_margin") > c("gross_margin_prior")
        ),
        pl.when(~input_has("mixed", REVENUE)).then(
            c("asset_turnover") > c("asset_turnover_prior")
        ),
    )
    names = [f"_f_signal_{index}" for index in range(1, 10)]
    return (
        frame.with_columns(
            signal.alias(name) for name, signal in zip(names, signals, strict=True)
        )
        .with_columns(
            pl.sum_horizontal(*(c(name).is_not_null().cast(pl.Int8) for name in names))
            .cast(pl.Int8)
            .alias("piotroski_valid_components_count"),
            pl.sum_horizontal(
                *(c(name).cast(pl.Int8) for name in names), ignore_nulls=False
            )
            .cast(pl.Int8)
            .alias("piotroski_f_score"),
        )
        .drop(names)
    )


def _beneish(frame: pl.DataFrame) -> pl.DataFrame:
    """Clip seven relative indices and continuing earnings less CFO/assets before weighting."""

    def inputs(suffix: str) -> tuple[pl.Expr, ...]:
        (
            assets,
            current,
            tangible,
            investments,
            dep,
            revenue,
            trade,
            sell,
            admin,
            debt,
        ) = (
            c(f"{name}{suffix}")
            for name in (
                ASSETS,
                CURRENT_ASSETS,
                TANGIBLE_ASSETS,
                LT_INVESTMENTS,
                DEPRECIATION,
                REVENUE,
                TRADE_RECEIVABLES,
                SELLING_EXPENSES,
                ADMIN_EXPENSES,
                LIABILITIES,
            )
        )
        return (
            _divide(trade, revenue),
            1 - _divide(current + tangible + investments.fill_null(0), assets),
            _divide(dep.abs(), dep.abs() + tangible),
            _divide(sell.abs() + admin.abs(), revenue),
            _divide(debt, assets),
        )

    dsr, quality, dep, sga, leverage = inputs("")
    dsr_prior, quality_prior, dep_prior, sga_prior, leverage_prior = inputs("_prior")
    dsri, gmi, aqi, sgi, depi, sgai, lvgi = (
        _divide(n, d).clip(0.1, 10.0)
        for n, d in (
            (dsr, dsr_prior),
            (c("gross_margin_prior"), c("gross_margin")),
            (quality, quality_prior),
            (c(REVENUE), c(f"{REVENUE}_prior")),
            (dep_prior, dep),
            (sga, sga_prior),
            (leverage, leverage_prior),
        )
    )
    tata = _divide(c(CONTINUING_EARNINGS) - c(CFO), c(ASSETS)).clip(-1, 1)
    m = (
        -4.84
        + 0.920 * dsri
        + 0.528 * gmi
        + 0.404 * aqi
        + 0.892 * sgi
        + 0.115 * depi
        - 0.172 * sgai
        + 4.679 * tata
        - 0.327 * lvgi
    )
    inputs_used = (
        ASSETS,
        CURRENT_ASSETS,
        TANGIBLE_ASSETS,
        LT_INVESTMENTS,
        DEPRECIATION,
        REVENUE,
        TRADE_RECEIVABLES,
        GROSS_PROFIT,
        SELLING_EXPENSES,
        ADMIN_EXPENSES,
        LIABILITIES,
    )
    mixed = cross_taxonomy(*inputs_used) | input_has(
        "mixed",
        *(f"{name}{suffix}" for name in inputs_used for suffix in ("", "_prior")),
        CONTINUING_EARNINGS,
        CFO,
    )
    return frame.with_columns(
        pl.when(~mixed & m.is_finite()).then(m).alias("beneish_m8_score"),
        pl.when(mixed)
        .then(pl.lit("MIXED_TAXONOMY"))
        .when(c("_prior_end").is_null())
        .then(pl.lit("MISSING_PRIOR_YEAR_BASE"))
        .when(c(DEPRECIATION).is_null() | c(f"{DEPRECIATION}_prior").is_null())
        .then(pl.lit("MISSING_DEPRECIATION_DIRECT_CF"))
        .when(~m.is_finite().fill_null(False))
        .then(pl.lit("MISSING_OR_INVALID_INPUT"))
        .when(m > -1.78)
        .then(pl.lit("HIGH_MANIPULATION_RISK_SCREEN"))
        .otherwise(pl.lit("VALID"))
        .alias("beneish_flag"),
    )


def add_classical_scores(ratios: pl.DataFrame) -> pl.DataFrame:
    """Append classical screening values and statuses for corporate fiscal periods."""
    return _beneish(_piotroski(_altman(ratios.filter(CORPORATE)))).with_columns(
        pl.lit("SCREENING_INDICATOR").alias("model_role")
    )
