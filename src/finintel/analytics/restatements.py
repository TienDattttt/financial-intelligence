"""Post-audit profit downgrade forensic screen (RF_10 — TASK-07).

Verified Sources:
    - Dechow, P. M., Ge, W., Larson, C. R., & Sloan, R. G. (2011). "Predicting Material
      Accounting Misstatements." Contemporary Accounting Research, 28(1), 17-82.
    - Schilit, H. M., Perler, J., & Engelhart, Y. (2018). Financial Shenanigans (4th Ed.).
    - Circular 96/2020/TT-BTC (Articles 10 & 14 — quarterly unaudited vs annual audited
      disclosure timing on HOSE, HNX, and UPCOM).
    - Circular 200/2014/TT-BTC & Circular 99/2025/TT-BTC (VAS B02-DN/HN Net Income).
    - ``spec/SPEC-v2-autonomous-institutional-finintel.md`` §3.C.
"""

from __future__ import annotations

import polars as pl

from finintel.normalization.periods import (
    REVISION_ORDER,
    coalesce_cross_provider_facts,
)

EARNINGS_METRIC = "IS_NET_INCOME"
DERIVED_Q4_MIX = "DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX"

RF10_MATERIALITY_FLOOR_VND = 1_000_000_000.0
RF10_WIPEOUT_ABS_FLOOR_VND = 5_000_000_000.0
RF10_WIPEOUT_REL_THRESHOLD = -0.15
RF10_CARRY_FORWARD_MAX_DAYS = 275

_PERIOD_KEY = (
    "issuer_id statement_scope period_type fiscal_year fiscal_quarter "
    "fiscal_year_start_date period_end_date"
).split()
_FY_MATCH_KEY = (
    "issuer_id statement_scope fiscal_year_start_date taxonomy_version".split()
)
_PROV_MAP = {"fhsc": 5, "issuer": 4, "vietcap_iq": 3, "cafef_pdf": 2}

RF10_COLUMNS: tuple[str, ...] = (
    "ni_unaudited_4q_sum",
    "ni_audited_annual",
    "audit_earnings_diff_vnd",
    "audit_adjustment_pct",
    "_rf10_triggered",
    "_rf10_source_filing_ids",
)


def _attach_empty_rf10_columns(periods: pl.DataFrame) -> pl.DataFrame:
    return periods.with_columns(
        pl.lit(None, dtype=pl.Float64).alias("ni_unaudited_4q_sum"),
        pl.lit(None, dtype=pl.Float64).alias("ni_audited_annual"),
        pl.lit(None, dtype=pl.Float64).alias("audit_earnings_diff_vnd"),
        pl.lit(None, dtype=pl.Float64).alias("audit_adjustment_pct"),
        pl.lit(False, dtype=pl.Boolean).alias("_rf10_triggered"),
        pl.lit([], dtype=pl.List(pl.String)).alias("_rf10_source_filing_ids"),
    )


def _ensure_enrichment_cols(df: pl.DataFrame) -> pl.DataFrame:
    exprs: list[pl.Expr] = []
    if "period_days" not in df.columns:
        exprs.append(
            (
                (
                    pl.col("period_end_date").cast(pl.Date)
                    - pl.col("period_start_date").cast(pl.Date)
                ).dt.total_days()
                + 1
            ).alias("period_days")
        )
    if "source_filing_ids" not in df.columns:
        exprs.append(
            pl.concat_list(pl.col("filing_id").cast(pl.String)).alias(
                "source_filing_ids"
            )
        )
    return df.with_columns(exprs) if exprs else df


def attach_pre_vs_post_audit_restatement(
    periods: pl.DataFrame,
    visible_facts: pl.DataFrame,
) -> pl.DataFrame:
    """Attach PIT-safe pre-audit 4Q vs post-audit Annual net-income evidence onto ``periods``."""
    if periods.is_empty() or visible_facts.is_empty():
        return _attach_empty_rf10_columns(periods)

    ni_facts = _ensure_enrichment_cols(
        visible_facts.filter(
            (pl.col("metric_id") == EARNINGS_METRIC)
            & (pl.col("revision_reason") != "RESTATED_REGIME_CHANGE")
            & pl.col("value").is_not_null()
            & pl.col("value").is_finite()
        )
    )
    if ni_facts.is_empty():
        return _attach_empty_rf10_columns(periods)

    prov_prio = (
        pl.col("source_provider").replace_strict(_PROV_MAP, default=1)
        if "source_provider" in ni_facts.columns
        else pl.lit(1)
    )

    annual_raw = ni_facts.filter(
        (pl.col("period_type") == "ANNUAL")
        & (pl.col("audit_status") == "AUDITED")
        & pl.col("period_days").is_between(350, 380)
    )
    if annual_raw.is_empty():
        return _attach_empty_rf10_columns(periods)

    first_audit = annual_raw.group_by(_FY_MATCH_KEY).agg(
        pl.col("effective_available_at").min().alias("_first_ann_avail")
    )
    annual_audited = (
        annual_raw.with_columns(prov_prio.alias("_prov_prio"))
        .sort(["effective_available_at", "_prov_prio", "revision_no", "filing_id"])
        .unique(subset=[*_FY_MATCH_KEY, "effective_available_at"], keep="last")
        .select(
            *_FY_MATCH_KEY,
            pl.col("period_end_date").alias("_ann_end"),
            pl.col("effective_available_at").alias("_ann_avail"),
            pl.col("value").cast(pl.Float64).alias("ni_audited_annual"),
            pl.col("filing_id").cast(pl.String).alias("_ann_filing_id"),
            pl.col("source_filing_ids").alias("_ann_filings"),
        )
    )

    # Filter strictly BEFORE _first_ann_avail prior to cross-provider coalescing (P1-1).
    pre_audit_raw = (
        ni_facts.filter(
            (pl.col("period_type") == "QUARTER")
            & (pl.col("audit_status") == "UNAUDITED")
            & (pl.col("derivation_method") != DERIVED_Q4_MIX)
            & pl.col("fiscal_quarter").is_in([1, 2, 3, 4])
            & pl.col("period_days").is_between(80, 100)
        )
        .join(first_audit, on=_FY_MATCH_KEY, how="inner")
        .filter(pl.col("effective_available_at") < pl.col("_first_ann_avail"))
        .drop("_first_ann_avail")
    )
    if pre_audit_raw.is_empty():
        return _attach_empty_rf10_columns(periods)

    pre_audit_quarters = (
        coalesce_cross_provider_facts(pre_audit_raw)
        .sort(REVISION_ORDER)
        .unique(subset=[*_FY_MATCH_KEY, "fiscal_quarter"], keep="last")
        .with_columns(
            pl.concat_list(
                pl.concat_list(pl.col("filing_id").cast(pl.String)),
                pl.col("source_filing_ids").cast(pl.List(pl.String)),
            ).alias("_q_row_filings")
        )
    )

    four_q_summary = (
        pre_audit_quarters.group_by(_FY_MATCH_KEY)
        .agg(
            pl.col("fiscal_quarter").n_unique().alias("_q_cnt"),
            pl.col("period_days").sum().alias("_q_days_sum"),
            pl.col("period_end_date")
            .filter(pl.col("fiscal_quarter") == 4)
            .last()
            .alias("_q4_end"),
            pl.col("value").cast(pl.Float64).sum().alias("ni_unaudited_4q_sum"),
            pl.col("_q_row_filings")
            .explode()
            .drop_nulls()
            .cast(pl.String)
            .unique()
            .sort()
            .alias("_q_filings"),
        )
        .filter((pl.col("_q_cnt") == 4) & pl.col("_q_days_sum").is_between(350, 380))
    )
    if four_q_summary.is_empty():
        return _attach_empty_rf10_columns(periods)

    diff_expr = pl.col("ni_audited_annual") - pl.col("ni_unaudited_4q_sum")
    pct_expr = (
        pl.when(pl.col("ni_unaudited_4q_sum").abs() >= RF10_MATERIALITY_FLOOR_VND)
        .then(diff_expr / pl.col("ni_unaudited_4q_sum").abs())
        .otherwise(pl.lit(None, dtype=pl.Float64))
    )
    fy_evaluated = (
        annual_audited.join(four_q_summary, on=_FY_MATCH_KEY, how="inner")
        .filter(pl.col("_q4_end") == pl.col("_ann_end"))
        .with_columns(
            diff_expr.alias("audit_earnings_diff_vnd"),
            pct_expr.alias("audit_adjustment_pct"),
        )
        .with_columns(
            (
                (
                    (pl.col("ni_unaudited_4q_sum") > 0.0)
                    & (pl.col("ni_audited_annual") < 0.0)
                    & (pl.col("audit_earnings_diff_vnd") <= -RF10_MATERIALITY_FLOOR_VND)
                )
                | (
                    (pl.col("ni_unaudited_4q_sum").abs() >= RF10_MATERIALITY_FLOOR_VND)
                    & pl.col("audit_adjustment_pct").is_not_null()
                    & (pl.col("audit_adjustment_pct") <= RF10_WIPEOUT_REL_THRESHOLD)
                    & (pl.col("audit_earnings_diff_vnd") <= -RF10_WIPEOUT_ABS_FLOOR_VND)
                )
            ).alias("_rf10_triggered"),
            pl.concat_list(
                pl.concat_list("_ann_filing_id"),
                pl.col("_ann_filings").cast(pl.List(pl.String)),
                pl.col("_q_filings"),
            )
            .list.drop_nulls()
            .list.unique()
            .list.sort()
            .alias("_fy_filings"),
        )
    )
    if fy_evaluated.is_empty():
        return _attach_empty_rf10_columns(periods)

    eval_cols = [*RF10_COLUMNS[:5], "_fy_filings"]
    annual_rows = (
        periods.filter(pl.col("period_type") == "ANNUAL")
        .select(*_PERIOD_KEY, "taxonomy_version", "_period_available_at")
        .join(
            fy_evaluated.select(*_FY_MATCH_KEY, "_ann_end", "_ann_avail", *eval_cols),
            on=_FY_MATCH_KEY,
            how="inner",
        )
        .filter(
            (pl.col("period_end_date") == pl.col("_ann_end"))
            & pl.col("_period_available_at").is_not_null()
            & (pl.col("_period_available_at") >= pl.col("_ann_avail"))
        )
        .sort("_ann_avail")
        .unique(subset=_PERIOD_KEY, keep="last")
        .select(*_PERIOD_KEY, *eval_cols)
    )

    quarter_rows = (
        periods.filter(pl.col("period_type") == "QUARTER")
        .select(*_PERIOD_KEY, "_period_available_at")
        .join(
            fy_evaluated.select(
                "issuer_id", "statement_scope", "_ann_end", "_ann_avail", *eval_cols
            ),
            on=["issuer_id", "statement_scope"],
            how="inner",
        )
        .filter(
            (pl.col("period_end_date") - pl.col("_ann_end"))
            .dt.total_days()
            .is_between(1, RF10_CARRY_FORWARD_MAX_DAYS)
            & pl.col("_period_available_at").is_not_null()
            & (pl.col("_period_available_at") >= pl.col("_ann_avail"))
        )
        .sort(["_ann_end", "_ann_avail"])
        .unique(subset=_PERIOD_KEY, keep="last")
        .select(*_PERIOD_KEY, *eval_cols)
    )

    matched = pl.concat([annual_rows, quarter_rows], how="vertical")
    empty_str_list = pl.lit([], dtype=pl.List(pl.String))
    return (
        periods.join(
            matched, on=_PERIOD_KEY, how="left", nulls_equal=True, validate="1:1"
        )
        .with_columns(
            pl.col("_rf10_triggered").fill_null(False),
            pl.concat_list(
                pl.concat_list(pl.col("filing_id").cast(pl.String)),
                pl.col("_fy_filings").fill_null(empty_str_list),
            )
            .list.drop_nulls()
            .list.unique()
            .list.sort()
            .alias("_rf10_source_filing_ids"),
        )
        .drop("_fy_filings")
    )


__all__ = [
    "RF10_CARRY_FORWARD_MAX_DAYS",
    "RF10_COLUMNS",
    "RF10_MATERIALITY_FLOOR_VND",
    "RF10_WIPEOUT_ABS_FLOOR_VND",
    "RF10_WIPEOUT_REL_THRESHOLD",
    "attach_pre_vs_post_audit_restatement",
]
