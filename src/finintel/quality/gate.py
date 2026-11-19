"""Layer 6 3-tier severity quality gate, quarantine filter, and batch circuit breaker."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import polars as pl

from finintel.exceptions import ContractViolationError, CriticalDataQualityError
from finintel.quality.rules import (
    PERIOD_KEY,
    QualityGateConfig,
    QualityStatus,
    build_period_wide,
    check_balance_sheet_identity,
    check_cash_flow_reconciliation,
    check_derived_q4_mix,
    check_duplicate_primary_keys,
    check_info_signals,
    check_missing_required_metrics,
    check_partial_statement,
    check_subtotal_mismatch,
    check_unit_scale_jump,
)
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_KEY,
    AUDIT_QUARANTINE_SCHEMA,
    BENCHMARKS_TABLE,
    ML_SCORES_TABLE,
    QUALITY_SUMMARY_KEY,
    QUALITY_SUMMARY_SCHEMA,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
    validate_analytics_table,
)

logger = logging.getLogger("finintel.quality")


@dataclass(frozen=True)
class QualityGateResult:
    """Validated output of the 3-tier Layer 6 quality gate."""

    clean_facts: pl.DataFrame
    quality_summary: pl.DataFrame
    audit_quarantine: pl.DataFrame
    total_issuers: int
    critical_issuers: int
    critical_issuer_ratio: float
    quarantined_filing_ids: tuple[str, ...]
    clean_ratios: pl.DataFrame | None = None


def _utc_now_iso(checked_at: datetime | None) -> str:
    if checked_at is None:
        return datetime.now(UTC).isoformat()
    if checked_at.utcoffset() is None:
        raise ContractViolationError("checked_at must include a timezone.")
    return checked_at.astimezone(UTC).isoformat()


def _empty_quality_summary() -> pl.DataFrame:
    return pl.DataFrame(schema=QUALITY_SUMMARY_SCHEMA)


def _empty_audit_quarantine() -> pl.DataFrame:
    return pl.DataFrame(schema=AUDIT_QUARANTINE_SCHEMA)


def evaluate_quality_gate(
    facts: pl.DataFrame,
    *,
    issuers: pl.DataFrame | None = None,
    ratios: pl.DataFrame | None = None,
    config: QualityGateConfig | None = None,
    checked_at: datetime | None = None,
    on_quarantine: Callable[[pl.DataFrame], None] | None = None,
    run_id: str | None = None,
) -> QualityGateResult:
    """Execute the 3-tier severity quality gate on normalized financial facts.

    Tier 1 (CRITICAL):
      - Quarantines every violating period/filing into ``audit_quarantine.parquet``.
      - Excludes quarantined periods and filings from ``clean_facts`` (and ``clean_ratios``).
      - Trips the Batch Circuit Breaker (raises ``CriticalDataQualityError``) whenever
        ``critical_issuers / total_issuers > config.max_critical_issuer_ratio`` (default 10%).
    Tier 2 (WARNING):
      - Allows clean periods into the published snapshot with ``quality_status = 'WARNING'``
        and explicit ``warning_codes`` in ``quality_summary.parquet``.
    Tier 3 (INFO):
      - Valid negative financial values (negative equity, CFO, net income, ROE) remain
        ``quality_status = 'PASS'`` (unless a Tier-2 warning also fires) and are logged
        strictly under ``info_codes``.
    """
    cfg = config or QualityGateConfig()
    timestamp = _utc_now_iso(checked_at)

    if facts.is_empty():
        return QualityGateResult(
            clean_facts=facts,
            quality_summary=_empty_quality_summary(),
            audit_quarantine=_empty_audit_quarantine(),
            total_issuers=0,
            critical_issuers=0,
            critical_issuer_ratio=0.0,
            quarantined_filing_ids=(),
            clean_ratios=ratios,
        )

    dup_events = check_duplicate_primary_keys(facts)
    period_wide = build_period_wide(facts, issuers)

    critical_frames = [
        dup_events,
        check_balance_sheet_identity(period_wide, rel_tol=cfg.bs_identity_rel_tol),
        check_subtotal_mismatch(period_wide, rel_tol=cfg.subtotal_rel_tol),
        check_unit_scale_jump(
            period_wide,
            upper=cfg.unit_scale_upper_factor,
            lower=cfg.unit_scale_lower_factor,
        ),
        check_missing_required_metrics(
            period_wide, required_metrics=cfg.required_metrics
        ),
    ]
    non_empty_critical = [frame for frame in critical_frames if not frame.is_empty()]
    if non_empty_critical:
        audit_quarantine = (
            pl.concat(non_empty_critical)
            .unique(subset=list(AUDIT_QUARANTINE_KEY), keep="first")
            .with_columns(pl.lit(timestamp).alias("quarantined_at"))
            .select(AUDIT_QUARANTINE_SCHEMA.names())
            .sort(list(AUDIT_QUARANTINE_KEY))
        )
    else:
        audit_quarantine = _empty_audit_quarantine()

    total_issuers = int(facts["issuer_id"].n_unique())
    critical_issuers = int(audit_quarantine["issuer_id"].n_unique())
    critical_ratio = (
        float(critical_issuers / total_issuers) if total_issuers > 0 else 0.0
    )

    if on_quarantine is not None and not audit_quarantine.is_empty():
        on_quarantine(audit_quarantine)

    if total_issuers > 0 and critical_ratio > cfg.max_critical_issuer_ratio:
        logger.error(
            "Batch circuit breaker tripped: critical issuer ratio exceeded threshold",
            extra={
                "event": "quality_gate_circuit_breaker_tripped",
                "run_id": run_id,
                "stage": "QUALITY_GATE_1",
                "details": {
                    "critical_issuers": critical_issuers,
                    "total_issuers": total_issuers,
                    "critical_issuer_ratio": round(critical_ratio, 6),
                    "threshold": cfg.max_critical_issuer_ratio,
                },
            },
        )
        raise CriticalDataQualityError(
            f"Batch circuit breaker tripped: {critical_issuers}/{total_issuers} issuers "
            f"({critical_ratio:.1%}) failed CRITICAL quality checks "
            f"(threshold {cfg.max_critical_issuer_ratio:.1%})."
        )

    quarantined_filing_ids: tuple[str, ...] = ()
    if not audit_quarantine.is_empty():
        quarantined_periods = audit_quarantine.select(list(PERIOD_KEY)).unique()
        quarantined_filings_df = audit_quarantine.select("filing_id").unique()
        quarantined_filing_ids = tuple(
            sorted(quarantined_filings_df["filing_id"].to_list())
        )
        clean_facts = facts.join(
            quarantined_periods, on=list(PERIOD_KEY), how="anti"
        ).join(quarantined_filings_df, on="filing_id", how="anti")
        clean_wide = period_wide.join(
            quarantined_periods, on=list(PERIOD_KEY), how="anti"
        )
        clean_ratios = (
            ratios.join(quarantined_periods, on=list(PERIOD_KEY), how="anti").join(
                quarantined_filings_df, on="filing_id", how="anti"
            )
            if ratios is not None
            else None
        )
    else:
        clean_facts = facts
        clean_wide = period_wide
        clean_ratios = ratios

    quality_summary = _build_quality_summary(
        clean_wide,
        clean_ratios,
        config=cfg,
        timestamp=timestamp,
    )

    log_method = logger.warning if not audit_quarantine.is_empty() else logger.info
    log_method(
        "Quality gate evaluation completed",
        extra={
            "event": "quality_gate_completed",
            "run_id": run_id,
            "stage": "QUALITY_GATE_1",
            "details": {
                "total_issuers": total_issuers,
                "critical_issuers": critical_issuers,
                "quarantined_rows": audit_quarantine.height,
                "published_periods": quality_summary.height,
                "warning_periods": int(
                    quality_summary.filter(
                        pl.col("quality_status") == QualityStatus.WARNING.value
                    ).height
                ),
            },
        },
    )

    return QualityGateResult(
        clean_facts=clean_facts,
        quality_summary=quality_summary,
        audit_quarantine=audit_quarantine,
        total_issuers=total_issuers,
        critical_issuers=critical_issuers,
        critical_issuer_ratio=critical_ratio,
        quarantined_filing_ids=quarantined_filing_ids,
        clean_ratios=clean_ratios,
    )


def _build_quality_summary(
    clean_wide: pl.DataFrame,
    clean_ratios: pl.DataFrame | None,
    *,
    config: QualityGateConfig,
    timestamp: str,
) -> pl.DataFrame:
    if clean_wide.is_empty():
        return _empty_quality_summary()

    warning_frames = [
        check_cash_flow_reconciliation(clean_wide, rel_tol=config.cash_flow_rel_tol),
        check_partial_statement(clean_wide),
        check_derived_q4_mix(clean_wide, clean_ratios),
    ]
    non_empty_warnings = [frame for frame in warning_frames if not frame.is_empty()]
    if non_empty_warnings:
        warnings_agg = (
            pl.concat(non_empty_warnings)
            .unique()
            .group_by(list(PERIOD_KEY))
            .agg(pl.col("code").sort().alias("warning_codes"))
        )
    else:
        warnings_agg = pl.DataFrame(
            schema={
                "issuer_id": pl.String,
                "statement_scope": pl.String,
                "period_type": pl.String,
                "period_end_date": pl.Date,
                "taxonomy_version": pl.String,
                "warning_codes": pl.List(pl.String),
            }
        )

    info_signals = check_info_signals(clean_wide, clean_ratios)
    if not info_signals.is_empty():
        info_agg = info_signals.group_by(list(PERIOD_KEY)).agg(
            pl.col("code").sort().alias("info_codes")
        )
    else:
        info_agg = pl.DataFrame(
            schema={
                "issuer_id": pl.String,
                "statement_scope": pl.String,
                "period_type": pl.String,
                "period_end_date": pl.Date,
                "taxonomy_version": pl.String,
                "info_codes": pl.List(pl.String),
            }
        )

    empty_list = pl.lit([], dtype=pl.List(pl.String))
    summary = (
        clean_wide.select(
            *PERIOD_KEY,
            "fiscal_year",
            "fiscal_quarter",
            "filing_ids",
        )
        .join(warnings_agg, on=list(PERIOD_KEY), how="left")
        .join(info_agg, on=list(PERIOD_KEY), how="left")
        .with_columns(
            pl.col("warning_codes").fill_null(empty_list),
            pl.col("info_codes").fill_null(empty_list),
            pl.lit(timestamp).alias("checked_at"),
        )
        .with_columns(
            pl.when(pl.col("warning_codes").list.len() > 0)
            .then(pl.lit(QualityStatus.WARNING.value))
            .otherwise(pl.lit(QualityStatus.PASS.value))
            .alias("quality_status")
        )
        .select(QUALITY_SUMMARY_SCHEMA.names())
        .sort(list(QUALITY_SUMMARY_KEY))
    )
    return summary


def verify_analytics_quality_gate(
    clean_facts: pl.DataFrame,
    ratios: pl.DataFrame,
    *,
    benchmarks: pl.DataFrame | None = None,
    red_flags: pl.DataFrame | None = None,
    ml_scores: pl.DataFrame | None = None,
    quality_summary: pl.DataFrame | None = None,
    run_id: str | None = None,
) -> pl.DataFrame:
    """Validate Layer 4 analytics tables and enforce zero-orphan filing lineage (QUALITY_GATE_2).

    Checks:
      1. Strict Parquet schema and primary key uniqueness for all Layer 4 tables.
      2. Zero orphan foreign keys: every ``filing_id`` and every ID inside ``source_filing_ids``
         in ``financial_ratios`` (plus ``filing_id`` in ``red_flags`` and ``ml_scores``) must
         exist in ``clean_facts['filing_id']``.
      3. Propagates any ``DERIVED_Q4_INPUT`` warnings and Tier-3 ``NEGATIVE_ROE`` info notes
         into ``quality_summary`` without flagging negative financial ratios as errors.
    """
    validate_analytics_table(RATIOS_TABLE, ratios)
    if benchmarks is not None:
        validate_analytics_table(BENCHMARKS_TABLE, benchmarks)
    if red_flags is not None:
        validate_analytics_table(RED_FLAGS_TABLE, red_flags)
    if ml_scores is not None:
        validate_analytics_table(ML_SCORES_TABLE, ml_scores)

    known_filings = clean_facts.select(pl.col("filing_id").cast(pl.String)).unique()

    referenced_frames: list[pl.DataFrame] = []
    if not ratios.is_empty():
        referenced_frames.append(ratios.select(pl.col("filing_id").cast(pl.String)))
        if "source_filing_ids" in ratios.columns:
            referenced_frames.append(
                ratios.select(
                    pl.col("source_filing_ids").explode().cast(pl.String).alias("filing_id")
                ).drop_nulls()
            )
    if red_flags is not None and not red_flags.is_empty():
        referenced_frames.append(red_flags.select(pl.col("filing_id").cast(pl.String)))
    if ml_scores is not None and not ml_scores.is_empty():
        referenced_frames.append(ml_scores.select(pl.col("filing_id").cast(pl.String)))

    if referenced_frames:
        referenced = pl.concat(referenced_frames).unique()
        orphans = referenced.join(known_filings, on="filing_id", how="anti")
        if not orphans.is_empty():
            sample = sorted(orphans["filing_id"].to_list())[:5]
            logger.error(
                "Orphan filing reference detected in analytics tables",
                extra={
                    "event": "quality_gate_2_orphan_lineage",
                    "run_id": run_id,
                    "stage": "QUALITY_GATE_2",
                    "details": {
                        "orphan_count": orphans.height,
                        "sample_filing_ids": sample,
                    },
                },
            )
            raise CriticalDataQualityError(
                f"Analytics tables contain {orphans.height} orphan filing_id reference(s) "
                f"absent from clean financial_facts."
            )

    if quality_summary is None:
        return _empty_quality_summary()
    if quality_summary.is_empty() or ratios.is_empty():
        return quality_summary

    extra_warnings = check_derived_q4_mix(
        pl.DataFrame(schema={"_has_derived_q4_mix": pl.Boolean}), ratios
    )
    extra_info = check_info_signals(
        pl.DataFrame(
            schema={
                "BS_TOTAL_EQUITY": pl.Float64,
                "CF_OPERATING_CASH_FLOW": pl.Float64,
                "IS_NET_INCOME": pl.Float64,
            }
        ),
        ratios,
    )

    updated = quality_summary
    if not extra_warnings.is_empty():
        w_agg = extra_warnings.group_by(list(PERIOD_KEY)).agg(
            pl.col("code").alias("_extra_warnings")
        )
        empty_list = pl.lit([], dtype=pl.List(pl.String))
        updated = (
            updated.join(w_agg, on=list(PERIOD_KEY), how="left")
            .with_columns(
                pl.concat_list(
                    "warning_codes", pl.col("_extra_warnings").fill_null(empty_list)
                )
                .list.unique()
                .list.sort()
                .alias("warning_codes")
            )
            .drop("_extra_warnings")
            .with_columns(
                pl.when(pl.col("warning_codes").list.len() > 0)
                .then(pl.lit(QualityStatus.WARNING.value))
                .otherwise(pl.lit(QualityStatus.PASS.value))
                .alias("quality_status")
            )
        )

    if not extra_info.is_empty():
        i_agg = extra_info.group_by(list(PERIOD_KEY)).agg(
            pl.col("code").alias("_extra_info")
        )
        empty_list = pl.lit([], dtype=pl.List(pl.String))
        updated = (
            updated.join(i_agg, on=list(PERIOD_KEY), how="left")
            .with_columns(
                pl.concat_list(
                    "info_codes", pl.col("_extra_info").fill_null(empty_list)
                )
                .list.unique()
                .list.sort()
                .alias("info_codes")
            )
            .drop("_extra_info")
        )

    logger.info(
        "Analytics quality gate verification passed",
        extra={
            "event": "quality_gate_2_passed",
            "run_id": run_id,
            "stage": "QUALITY_GATE_2",
            "details": {
                "ratios_rows": ratios.height,
                "quality_summary_rows": updated.height,
            },
        },
    )
    return updated.select(QUALITY_SUMMARY_SCHEMA.names()).sort(list(QUALITY_SUMMARY_KEY))
