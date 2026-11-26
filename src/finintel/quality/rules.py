"""Vectorized 3-tier accounting data quality rules for Vietnamese financial statements.

Implements the Layer 6 severity classification (CRITICAL, WARNING, INFO) strictly
using vectorized Polars expressions without row loops or implicit zero-filling.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.normalization.periods import DEFAULT_PROVIDER_PRIORITY
from finintel.storage.schemas import FINANCIAL_FACTS_KEY


class QualitySeverity(StrEnum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


class QualityStatus(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"


class QualityRuleCode(StrEnum):
    # Tier 1: CRITICAL (Quarantine per filing/period + Batch Circuit Breaker > 10%)
    BS_IDENTITY_MISMATCH = "BS_IDENTITY_MISMATCH"
    SUBTOTAL_MISMATCH = "SUBTOTAL_MISMATCH"
    UNIT_SCALE_JUMP = "UNIT_SCALE_JUMP"
    DUPLICATE_PRIMARY_KEY = "DUPLICATE_PRIMARY_KEY"
    MISSING_REQUIRED_METRICS = "MISSING_REQUIRED_METRICS"

    # Tier 2: WARNING (Published with warning badge in quality_summary.parquet)
    CASH_FLOW_RECONCILIATION_WARN = "CASH_FLOW_RECONCILIATION_WARN"
    PARTIAL_STATEMENT = "PARTIAL_STATEMENT"
    DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX = "DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX"

    # Tier 3: INFO (Valid accounting states routed to Layer 4A red_flags, never errors)
    NEGATIVE_EQUITY = "NEGATIVE_EQUITY"
    NEGATIVE_CFO = "NEGATIVE_CFO"
    NEGATIVE_NET_INCOME = "NEGATIVE_NET_INCOME"
    NEGATIVE_ROE = "NEGATIVE_ROE"


PERIOD_KEY: tuple[str, ...] = (
    "issuer_id",
    "statement_scope",
    "period_type",
    "period_end_date",
    "taxonomy_version",
)
REQUIRED_MIN_METRICS: tuple[str, ...] = (
    "BS_TOTAL_ASSETS",
    "BS_TOTAL_EQUITY",
    "IS_NET_REVENUE",
    "IS_NET_INCOME",
)
QUALITY_METRICS: tuple[str, ...] = (
    "BS_TOTAL_ASSETS",
    "BS_TOTAL_LIABILITIES",
    "BS_TOTAL_EQUITY",
    "BS_CURRENT_ASSETS",
    "BS_NONCURRENT_ASSETS",
    "BS_CASH_AND_EQUIVALENTS",
    "IS_NET_REVENUE",
    "IS_NET_INCOME",
    "CF_OPERATING_CASH_FLOW",
    "CF_DEPRECIATION_AMORTIZATION",
    "CF_ENDING_CASH",
)
_METRIC_ALIASES: dict[str, str] = {
    "CF_CASH_AND_EQUIVALENTS_END": "CF_ENDING_CASH",
    "IS_NET_INCOME_TOTAL": "IS_NET_INCOME",
}
_REVISION_ORDER: tuple[str, ...] = (
    "effective_available_at",
    "revision_no",
    "filing_id",
)

QUARANTINE_EVENT_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "filing_id": pl.String,
        "statement_scope": pl.String,
        "period_type": pl.String,
        "period_end_date": pl.Date,
        "taxonomy_version": pl.String,
        "rule_code": pl.String,
        "severity": pl.String,
        "details_json": pl.String,
    }
)
PERIOD_SIGNAL_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "statement_scope": pl.String,
        "period_type": pl.String,
        "period_end_date": pl.Date,
        "taxonomy_version": pl.String,
        "code": pl.String,
    }
)


@dataclass(frozen=True)
class QualityGateConfig:
    """Configurable thresholds for the 3-tier Layer 6 quality gate."""

    bs_identity_rel_tol: float = 0.001
    subtotal_rel_tol: float = 0.001
    unit_scale_upper_factor: float = 100.0
    unit_scale_lower_factor: float = 0.01
    cash_flow_rel_tol: float = 0.005
    max_critical_issuer_ratio: float = 0.10
    required_metrics: tuple[str, ...] = REQUIRED_MIN_METRICS

    def __post_init__(self) -> None:
        if (
            self.bs_identity_rel_tol < 0.0
            or self.subtotal_rel_tol < 0.0
            or self.cash_flow_rel_tol < 0.0
        ):
            raise ContractViolationError(
                "Quality gate relative tolerances must be non-negative."
            )
        if self.unit_scale_upper_factor <= 1.0:
            raise ContractViolationError(
                "unit_scale_upper_factor must be strictly greater than 1.0."
            )
        if not 0.0 < self.unit_scale_lower_factor < 1.0:
            raise ContractViolationError(
                "unit_scale_lower_factor must be strictly between 0.0 and 1.0."
            )
        if not 0.0 <= self.max_critical_issuer_ratio <= 1.0:
            raise ContractViolationError(
                "max_critical_issuer_ratio must lie within [0.0, 1.0]."
            )
        if not self.required_metrics:
            raise ContractViolationError(
                "Quality gate requires at least one mandatory metric."
            )


def _empty_quarantine_events() -> pl.DataFrame:
    return pl.DataFrame(schema=QUARANTINE_EVENT_SCHEMA)


def _empty_period_signals() -> pl.DataFrame:
    return pl.DataFrame(schema=PERIOD_SIGNAL_SCHEMA)


def check_duplicate_primary_keys(facts: pl.DataFrame) -> pl.DataFrame:
    """Detect duplicate composite business keys (FINANCIAL_FACTS_KEY) before deduplication.

    Formula: count(*) OVER FINANCIAL_FACTS_KEY > 1.
    Input metrics: all rows of normalized financial_facts.
    Edge cases: empty frame returns empty quarantine events; preserves exact filing_id.
    """
    if facts.is_empty():
        return _empty_quarantine_events()
    missing = [col for col in FINANCIAL_FACTS_KEY if col not in facts.columns]
    if missing:
        raise ContractViolationError(
            f"Quality gate requires financial_facts key columns: {', '.join(missing)}."
        )
    dupes = facts.filter(pl.len().over(list(FINANCIAL_FACTS_KEY)) > 1)
    if dupes.is_empty():
        return _empty_quarantine_events()
    return (
        dupes.group_by([*PERIOD_KEY, "filing_id"])
        .agg(
            pl.len().alias("duplicate_row_count"),
            pl.col("metric_id").unique().sort().alias("duplicate_metrics"),
        )
        .select(
            "issuer_id",
            "filing_id",
            "statement_scope",
            "period_type",
            "period_end_date",
            "taxonomy_version",
            pl.lit(QualityRuleCode.DUPLICATE_PRIMARY_KEY.value).alias("rule_code"),
            pl.lit(QualitySeverity.CRITICAL.value).alias("severity"),
            pl.struct("duplicate_row_count", "duplicate_metrics")
            .struct.json_encode()
            .alias("details_json"),
        )
    )


def build_period_wide(
    facts: pl.DataFrame, issuers: pl.DataFrame | None = None
) -> pl.DataFrame:
    """Resolve the latest revision per period and pivot quality metrics into columns.

    Preserves NULL semantics strictly (never fills missing metrics with zero) and
    tracks exact filing provenance for balance-sheet, income-statement, and
    cash-flow items.
    """
    if facts.is_empty():
        return pl.DataFrame(
            schema={
                "issuer_id": pl.String,
                "statement_scope": pl.String,
                "period_type": pl.String,
                "period_end_date": pl.Date,
                "taxonomy_version": pl.String,
                "fiscal_year": pl.Int32,
                "fiscal_quarter": pl.Int32,
                "accounting_regime": pl.String,
                "filing_ids": pl.List(pl.String),
                "_primary_filing_id": pl.String,
                "_bs_filing_id": pl.String,
                "_cf_filing_id": pl.String,
                "_has_derived_q4_mix": pl.Boolean,
                **{metric: pl.Float64 for metric in QUALITY_METRICS},
            }
        )

    normalized = facts.with_columns(
        pl.col("metric_id").replace(_METRIC_ALIASES).alias("_q_metric_id"),
        pl.col("fiscal_year").cast(pl.Int32),
        pl.col("fiscal_quarter").cast(pl.Int32),
        pl.col("value").cast(pl.Float64),
    )
    if "source_provider" in normalized.columns:
        per_prov = normalized.sort(list(_REVISION_ORDER)).unique(
            subset=[*PERIOD_KEY, "_q_metric_id", "source_provider"], keep="last"
        )
        latest = (
            per_prov.with_columns(
                pl.col("value").is_null().alias("_is_null"),
                pl.col("source_provider")
                .replace_strict(
                    DEFAULT_PROVIDER_PRIORITY, default=99, return_dtype=pl.Int32
                )
                .alias("_prov_rank"),
            )
            .sort(
                ["_is_null", "_prov_rank", *_REVISION_ORDER],
                descending=[False, False, True, True, True],
            )
            .unique(subset=[*PERIOD_KEY, "_q_metric_id"], keep="first")
            .drop(["_is_null", "_prov_rank"])
            .sort(list(_REVISION_ORDER))
        )
    else:
        latest = (
            normalized.sort(list(_REVISION_ORDER))
            .unique(subset=[*PERIOD_KEY, "_q_metric_id"], keep="last")
            .sort(list(_REVISION_ORDER))
        )

    wide = latest.group_by(list(PERIOD_KEY)).agg(
        pl.col("fiscal_year").last().alias("fiscal_year"),
        pl.col("fiscal_quarter").last().alias("fiscal_quarter"),
        pl.col("filing_id").unique().sort().alias("filing_ids"),
        pl.col("filing_id").last().alias("_primary_filing_id"),
        pl.col("filing_id")
        .filter(pl.col("_q_metric_id") == "BS_TOTAL_ASSETS")
        .last()
        .alias("_bs_filing_id"),
        pl.col("filing_id")
        .filter(
            pl.col("_q_metric_id").is_in(
                [
                    "CF_OPERATING_CASH_FLOW",
                    "CF_ENDING_CASH",
                    "CF_DEPRECIATION_AMORTIZATION",
                ]
            )
        )
        .last()
        .alias("_cf_filing_id"),
        (
            pl.col("derivation_method")
            == QualityRuleCode.DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX.value
        )
        .any()
        .fill_null(False)
        .alias("_has_derived_q4_mix"),
        *(
            pl.col("value")
            .filter(pl.col("_q_metric_id") == metric)
            .last()
            .alias(metric)
            for metric in QUALITY_METRICS
        ),
    )

    if issuers is not None and not issuers.is_empty():
        if {"issuer_id", "accounting_regime"} - set(issuers.columns):
            raise ContractViolationError(
                "Issuers table passed to quality gate requires issuer_id and accounting_regime."
            )
        regime_map = issuers.select(
            pl.col("issuer_id").cast(pl.String),
            pl.col("accounting_regime").cast(pl.String),
        ).unique(subset=["issuer_id"], keep="last")
        wide = wide.join(regime_map, on="issuer_id", how="left").with_columns(
            pl.col("accounting_regime").fill_null("CORPORATE")
        )
    else:
        wide = wide.with_columns(pl.lit("CORPORATE").alias("accounting_regime"))

    return wide.sort(list(PERIOD_KEY))


def check_balance_sheet_identity(
    period_wide: pl.DataFrame, *, rel_tol: float
) -> pl.DataFrame:
    """Check BS_TOTAL_ASSETS == BS_TOTAL_LIABILITIES + BS_TOTAL_EQUITY within rel_tol.

    Formula: |Assets - (Liabilities + Equity)| / max(|Assets|, 1.0) > rel_tol (default 0.1%).
    Input metrics: BS_TOTAL_ASSETS, BS_TOTAL_LIABILITIES, BS_TOTAL_EQUITY.
    Edge cases:
      - Safe divisor max(|Assets|, 1.0) prevents division by zero when Assets == 0.
      - Missing (NULL) operands are NOT filled with 0; only evaluated when all 3 are non-null.
    """
    if period_wide.is_empty():
        return _empty_quarantine_events()
    assets = pl.col("BS_TOTAL_ASSETS")
    liabilities = pl.col("BS_TOTAL_LIABILITIES")
    equity = pl.col("BS_TOTAL_EQUITY")
    residual = (assets - (liabilities + equity)).abs()
    denom = pl.max_horizontal(assets.abs(), pl.lit(1.0))
    rel_diff = residual / denom

    violated = period_wide.filter(
        assets.is_not_null()
        & liabilities.is_not_null()
        & equity.is_not_null()
        & (rel_diff > rel_tol)
    )
    if violated.is_empty():
        return _empty_quarantine_events()
    return violated.select(
        "issuer_id",
        pl.coalesce("_bs_filing_id", "_primary_filing_id").alias("filing_id"),
        "statement_scope",
        "period_type",
        "period_end_date",
        "taxonomy_version",
        pl.lit(QualityRuleCode.BS_IDENTITY_MISMATCH.value).alias("rule_code"),
        pl.lit(QualitySeverity.CRITICAL.value).alias("severity"),
        pl.struct(
            assets.alias("total_assets"),
            liabilities.alias("total_liabilities"),
            equity.alias("total_equity"),
            residual.alias("abs_residual_vnd"),
            rel_diff.alias("rel_diff"),
            pl.lit(rel_tol).alias("rel_tol"),
        )
        .struct.json_encode()
        .alias("details_json"),
    )


def check_subtotal_mismatch(
    period_wide: pl.DataFrame, *, rel_tol: float
) -> pl.DataFrame:
    """Check BS_CURRENT_ASSETS (100) + BS_NONCURRENT_ASSETS (200) == BS_TOTAL_ASSETS (270/280).

    Formula: |(CurrentAssets + NoncurrentAssets) - TotalAssets| / max(|TotalAssets|, 1.0) > rel_tol.
    Input metrics: BS_CURRENT_ASSETS, BS_NONCURRENT_ASSETS, BS_TOTAL_ASSETS.
    Edge cases:
      - Applies to CORPORATE regime only (banks/insurers do not split current/non-current).
      - Evaluated only when all 3 operands are non-null; never fills NULL with 0.
    """
    if period_wide.is_empty():
        return _empty_quarantine_events()
    current = pl.col("BS_CURRENT_ASSETS")
    noncurrent = pl.col("BS_NONCURRENT_ASSETS")
    assets = pl.col("BS_TOTAL_ASSETS")
    residual = ((current + noncurrent) - assets).abs()
    denom = pl.max_horizontal(assets.abs(), pl.lit(1.0))
    rel_diff = residual / denom

    violated = period_wide.filter(
        (pl.col("accounting_regime") == "CORPORATE")
        & current.is_not_null()
        & noncurrent.is_not_null()
        & assets.is_not_null()
        & (rel_diff > rel_tol)
    )
    if violated.is_empty():
        return _empty_quarantine_events()
    return violated.select(
        "issuer_id",
        pl.coalesce("_bs_filing_id", "_primary_filing_id").alias("filing_id"),
        "statement_scope",
        "period_type",
        "period_end_date",
        "taxonomy_version",
        pl.lit(QualityRuleCode.SUBTOTAL_MISMATCH.value).alias("rule_code"),
        pl.lit(QualitySeverity.CRITICAL.value).alias("severity"),
        pl.struct(
            current.alias("current_assets"),
            noncurrent.alias("noncurrent_assets"),
            assets.alias("total_assets"),
            residual.alias("abs_residual_vnd"),
            rel_diff.alias("rel_diff"),
            pl.lit(rel_tol).alias("rel_tol"),
        )
        .struct.json_encode()
        .alias("details_json"),
    )


def check_unit_scale_jump(
    period_wide: pl.DataFrame, *, upper: float, lower: float
) -> pl.DataFrame:
    """Detect abrupt currency unit scaling jumps (> 100x or < 0.01x) across adjacent periods.

    Formula: Assets_t / Assets_{t-1} > upper OR Assets_t / Assets_{t-1} < lower,
    partitioned strictly by (issuer_id, statement_scope, period_type) ordered by period_end_date.
    Input metrics: BS_TOTAL_ASSETS.
    Edge cases:
      - Never compares CONSOLIDATED against SEPARATE (same statement_scope required).
      - Deduplicates same-period comparative TT99/TT200 rows before shifting so comparative
        reclassifications at the exact same period_end_date do not shift against each other.
      - Non-positive or NULL endpoints do not trigger unit-scale jump.
    """
    if period_wide.is_empty():
        return _empty_quarantine_events()
    series_keys = ["issuer_id", "statement_scope", "period_type"]
    # Collapse duplicate taxonomy rows at the same period_end_date (e.g. TT200 + restated TT99)
    # preferring TT99 (lexicographically higher) so shift(1) always compares distinct periods.
    series = (
        period_wide.filter(pl.col("BS_TOTAL_ASSETS").is_not_null())
        .sort([*series_keys, "period_end_date", "taxonomy_version"])
        .unique(subset=[*series_keys, "period_end_date"], keep="last")
        .sort([*series_keys, "period_end_date"])
        .with_columns(
            pl.col("BS_TOTAL_ASSETS").shift(1).over(series_keys).alias("_prev_assets"),
            pl.col("period_end_date")
            .shift(1)
            .over(series_keys)
            .alias("_prev_period_end_date"),
        )
    )
    curr = pl.col("BS_TOTAL_ASSETS")
    prev = pl.col("_prev_assets")
    ratio = curr / prev
    violated = series.filter(
        (curr > 0.0) & (prev > 0.0) & ((ratio > upper) | (ratio < lower))
    )
    if violated.is_empty():
        return _empty_quarantine_events()
    return violated.select(
        "issuer_id",
        pl.coalesce("_bs_filing_id", "_primary_filing_id").alias("filing_id"),
        "statement_scope",
        "period_type",
        "period_end_date",
        "taxonomy_version",
        pl.lit(QualityRuleCode.UNIT_SCALE_JUMP.value).alias("rule_code"),
        pl.lit(QualitySeverity.CRITICAL.value).alias("severity"),
        pl.struct(
            curr.alias("current_total_assets"),
            prev.alias("previous_total_assets"),
            pl.col("_prev_period_end_date")
            .cast(pl.String)
            .alias("previous_period_end_date"),
            ratio.alias("scale_ratio"),
            pl.lit(upper).alias("upper_threshold"),
            pl.lit(lower).alias("lower_threshold"),
        )
        .struct.json_encode()
        .alias("details_json"),
    )


def check_missing_required_metrics(
    period_wide: pl.DataFrame, *, required_metrics: tuple[str, ...]
) -> pl.DataFrame:
    """Check that all mandatory core metrics are present and non-null for every period.

    Default required metrics: BS_TOTAL_ASSETS, BS_TOTAL_EQUITY, IS_NET_REVENUE, IS_NET_INCOME.
    Edge cases: any NULL or absent mandatory metric flags MISSING_REQUIRED_METRICS (CRITICAL).
    """
    if period_wide.is_empty():
        return _empty_quarantine_events()
    missing_exprs = [
        pl.when(pl.col(metric).is_null())
        .then(pl.lit(metric))
        .otherwise(pl.lit(None, dtype=pl.String))
        for metric in required_metrics
        if metric in period_wide.columns
    ]
    absent_constants = [
        pl.lit(metric)
        for metric in required_metrics
        if metric not in period_wide.columns
    ]
    missing_list = pl.concat_list([*missing_exprs, *absent_constants]).list.drop_nulls()
    violated = period_wide.with_columns(missing_list.alias("_missing_metrics")).filter(
        pl.col("_missing_metrics").list.len() > 0
    )
    if violated.is_empty():
        return _empty_quarantine_events()
    return violated.select(
        "issuer_id",
        pl.coalesce("_bs_filing_id", "_primary_filing_id").alias("filing_id"),
        "statement_scope",
        "period_type",
        "period_end_date",
        "taxonomy_version",
        pl.lit(QualityRuleCode.MISSING_REQUIRED_METRICS.value).alias("rule_code"),
        pl.lit(QualitySeverity.CRITICAL.value).alias("severity"),
        pl.struct(pl.col("_missing_metrics").alias("missing_metrics"))
        .struct.json_encode()
        .alias("details_json"),
    )


def check_cash_flow_reconciliation(
    period_wide: pl.DataFrame, *, rel_tol: float
) -> pl.DataFrame:
    """Warn when CF ending cash (Line 70) diverges from BS cash (Line 110) by > rel_tol.

    Formula: |CF_ENDING_CASH - BS_CASH_AND_EQUIVALENTS| / max(|BS_CASH_AND_EQUIVALENTS|, 1.0) > rel_tol (0.5%).
    Input metrics: CF_ENDING_CASH, BS_CASH_AND_EQUIVALENTS.
    Edge cases: evaluated only when both operands are non-null; safe divisor max(|BS_Cash|, 1.0).
    """
    if period_wide.is_empty():
        return _empty_period_signals()
    cf_cash = pl.col("CF_ENDING_CASH")
    bs_cash = pl.col("BS_CASH_AND_EQUIVALENTS")
    rel_diff = (cf_cash - bs_cash).abs() / pl.max_horizontal(bs_cash.abs(), pl.lit(1.0))
    warned = period_wide.filter(
        cf_cash.is_not_null() & bs_cash.is_not_null() & (rel_diff > rel_tol)
    )
    if warned.is_empty():
        return _empty_period_signals()
    return warned.select(
        *PERIOD_KEY,
        pl.lit(QualityRuleCode.CASH_FLOW_RECONCILIATION_WARN.value).alias("code"),
    )


def check_partial_statement(period_wide: pl.DataFrame) -> pl.DataFrame:
    """Warn when BS & IS exist but Cash Flow is missing or Direct CF lacks Depreciation.

    Input metrics: BS_TOTAL_ASSETS, IS_NET_REVENUE, CF_OPERATING_CASH_FLOW, CF_DEPRECIATION_AMORTIZATION.
    Edge cases:
      - Triggers PARTIAL_STATEMENT (WARNING) when BS & IS are present and either CFO is NULL
        or (for CORPORATE issuers) CF_DEPRECIATION_AMORTIZATION is NULL.
      - Never blocks publication.
    """
    if period_wide.is_empty():
        return _empty_period_signals()
    has_bs_and_is = (
        pl.col("BS_TOTAL_ASSETS").is_not_null() & pl.col("IS_NET_REVENUE").is_not_null()
    )
    missing_cf = pl.col("CF_OPERATING_CASH_FLOW").is_null()
    missing_dep_corporate = (pl.col("accounting_regime") == "CORPORATE") & pl.col(
        "CF_DEPRECIATION_AMORTIZATION"
    ).is_null()
    warned = period_wide.filter(has_bs_and_is & (missing_cf | missing_dep_corporate))
    if warned.is_empty():
        return _empty_period_signals()
    return warned.select(
        *PERIOD_KEY,
        pl.lit(QualityRuleCode.PARTIAL_STATEMENT.value).alias("code"),
    )


def check_derived_q4_mix(
    period_wide: pl.DataFrame, ratios: pl.DataFrame | None = None
) -> pl.DataFrame:
    """Warn when a period contains Q4 figures derived from Audited Annual minus 9M Unaudited/Reviewed.

    Trigger: derivation_method == 'DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX' in facts,
    or any *_flag == 'DERIVED_Q4_INPUT' in ratios.
    """
    frames: list[pl.DataFrame] = []
    if not period_wide.is_empty():
        from_facts = period_wide.filter(pl.col("_has_derived_q4_mix")).select(
            *PERIOD_KEY,
            pl.lit(QualityRuleCode.DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX.value).alias(
                "code"
            ),
        )
        if not from_facts.is_empty():
            frames.append(from_facts)

    if ratios is not None and not ratios.is_empty():
        flag_cols = [
            col
            for col in ratios.columns
            if col.endswith("_flag") and col != "dupont_valid_flag"
        ]
        if flag_cols and all(col in ratios.columns for col in PERIOD_KEY):
            has_derived_ratio = pl.any_horizontal(
                *(pl.col(col) == "DERIVED_Q4_INPUT" for col in flag_cols)
            )
            from_ratios = ratios.filter(has_derived_ratio).select(
                *PERIOD_KEY,
                pl.lit(QualityRuleCode.DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX.value).alias(
                    "code"
                ),
            )
            if not from_ratios.is_empty():
                frames.append(from_ratios)

    if not frames:
        return _empty_period_signals()
    return pl.concat(frames).unique()


def check_info_signals(
    period_wide: pl.DataFrame, ratios: pl.DataFrame | None = None
) -> pl.DataFrame:
    """Record accounting-valid extreme/negative values strictly as Tier-3 INFO signals.

    Negative equity, negative CFO, negative net income, or negative ROE are NEVER
    treated as data quality errors and NEVER cause quarantine or WARNING status.
    """
    frames: list[pl.DataFrame] = []
    if not period_wide.is_empty():
        checks: tuple[tuple[pl.Expr, QualityRuleCode], ...] = (
            (pl.col("BS_TOTAL_EQUITY") <= 0.0, QualityRuleCode.NEGATIVE_EQUITY),
            (pl.col("CF_OPERATING_CASH_FLOW") < 0.0, QualityRuleCode.NEGATIVE_CFO),
            (pl.col("IS_NET_INCOME") < 0.0, QualityRuleCode.NEGATIVE_NET_INCOME),
        )
        for condition, code in checks:
            matched = period_wide.filter(condition.fill_null(False)).select(
                *PERIOD_KEY, pl.lit(code.value).alias("code")
            )
            if not matched.is_empty():
                frames.append(matched)

    if (
        ratios is not None
        and not ratios.is_empty()
        and "roe_total" in ratios.columns
        and all(col in ratios.columns for col in PERIOD_KEY)
    ):
        neg_roe = ratios.filter((pl.col("roe_total") < 0.0).fill_null(False)).select(
            *PERIOD_KEY, pl.lit(QualityRuleCode.NEGATIVE_ROE.value).alias("code")
        )
        if not neg_roe.is_empty():
            frames.append(neg_roe)

    if not frames:
        return _empty_period_signals()
    return pl.concat(frames).unique()
