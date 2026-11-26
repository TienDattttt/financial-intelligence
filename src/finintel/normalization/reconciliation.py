"""Cross-provider canonical fact reconciliation (FHSC vs Vietcap IQ — TASK-05).

Verified Sources:
    - ``spec/SPEC-v2-autonomous-institutional-finintel.md`` §3.B.1:
      Evaluates 4 anchor metrics (``BS_TOTAL_ASSETS``, ``BS_TOTAL_EQUITY``,
      ``IS_NET_REVENUE``, ``IS_NET_INCOME_PARENT``) before ``coalesce_cross_provider_facts``:
      ``delta = abs(v_fhsc - v_vietcap) / max(abs(v_fhsc), abs(v_vietcap), 1e9)``.
      When ``delta > 0.005``, emits ``CROSS_PROVIDER_DISCREPANCY_WARN`` into
      ``quality_summary.info_codes`` and increments ``finintel_provider_discrepancy_total``
      without deleting or quarantining valid QG1 facts.
    - Prometheus Python Client (``prometheus-client==0.26.0``):
      Bounded label cardinality (``metric_id`` restricted to 4 anchor constants).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import polars as pl
from prometheus_client import REGISTRY, CollectorRegistry, Counter

from finintel.normalization.periods import REVISION_ORDER, coalesce_cross_provider_facts

PERIOD_KEY: tuple[str, ...] = (
    "issuer_id",
    "statement_scope",
    "period_type",
    "period_end_date",
    "taxonomy_version",
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

CROSS_PROVIDER_DISCREPANCY_CODE = "CROSS_PROVIDER_DISCREPANCY_WARN"
DEFAULT_DISCREPANCY_REL_TOL = 0.005
DEFAULT_MATERIALITY_FLOOR_VND = 1_000_000_000.0

RECONCILIATION_ANCHOR_METRICS: tuple[str, ...] = (
    "BS_TOTAL_ASSETS",
    "BS_TOTAL_EQUITY",
    "IS_NET_REVENUE",
    "IS_NET_INCOME_PARENT",
)

DISCREPANCY_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "statement_scope": pl.String,
        "period_type": pl.String,
        "period_end_date": pl.Date,
        "taxonomy_version": pl.String,
        "metric_id": pl.String,
        "fhsc_value": pl.Float64,
        "vietcap_value": pl.Float64,
        "relative_delta": pl.Float64,
    }
)

_RECONCILIATION_METRICS_REGISTRY = CollectorRegistry()
_PROVIDER_DISCREPANCY_TOTAL = Counter(
    "finintel_provider_discrepancy_total",
    "Total cross-provider anchor metric discrepancies (>0.5% above 1e9 VND floor).",
    labelnames=("metric_id",),
    registry=_RECONCILIATION_METRICS_REGISTRY,
)
REGISTRY.register(_PROVIDER_DISCREPANCY_TOTAL)


def get_reconciliation_metrics_registry() -> CollectorRegistry:
    """Return the bounded-cardinality Prometheus registry for reconciliation metrics."""
    return _RECONCILIATION_METRICS_REGISTRY


@dataclass(frozen=True, slots=True)
class CrossProviderReconciliationResult:
    """Output of pre-coalesce ``FHSC <-> Vietcap IQ`` anchor reconciliation."""

    coalesced_facts: pl.DataFrame
    discrepancies: pl.DataFrame
    info_signals: pl.DataFrame


def _empty_discrepancies() -> pl.DataFrame:
    return pl.DataFrame(schema=DISCREPANCY_SCHEMA)


def _empty_signals() -> pl.DataFrame:
    return pl.DataFrame(schema=PERIOD_SIGNAL_SCHEMA)


def reconcile_cross_provider_facts(
    facts: pl.DataFrame,
    *,
    rel_tol: float = DEFAULT_DISCREPANCY_REL_TOL,
    materiality_floor_vnd: float = DEFAULT_MATERIALITY_FLOOR_VND,
    provider_priority: Mapping[str, int] | None = None,
    record_metrics: bool = True,
) -> CrossProviderReconciliationResult:
    """Compare ``fhsc`` vs ``vietcap_iq`` anchor metrics before coalescing across providers."""
    if (
        facts.is_empty()
        or "source_provider" not in facts.columns
        or facts["source_provider"].n_unique() <= 1
    ):
        return CrossProviderReconciliationResult(
            coalesced_facts=facts,
            discrepancies=_empty_discrepancies(),
            info_signals=_empty_signals(),
        )

    direct_predicate = (
        pl.col("derivation_method").is_null()
        | (pl.col("derivation_method") == "REPORTED_DIRECTLY")
        if "derivation_method" in facts.columns
        else pl.lit(True)
    )
    anchor_subset = facts.filter(
        pl.col("metric_id").is_in(RECONCILIATION_ANCHOR_METRICS)
        & pl.col("source_provider").is_in(("fhsc", "vietcap_iq"))
        & pl.col("value").is_not_null()
        & pl.col("value").is_finite()
        & direct_predicate
    )
    if anchor_subset.is_empty() or anchor_subset["source_provider"].n_unique() < 2:
        return CrossProviderReconciliationResult(
            coalesced_facts=coalesce_cross_provider_facts(
                facts, provider_priority=provider_priority
            ),
            discrepancies=_empty_discrepancies(),
            info_signals=_empty_signals(),
        )

    rev_cols = [col for col in REVISION_ORDER if col in anchor_subset.columns]
    audited_tier_expr = (
        (pl.col("audit_status") == "AUDITED").alias("_is_audited")
        if "audit_status" in anchor_subset.columns
        else pl.lit(False).alias("_is_audited")
    )
    tiered = anchor_subset.with_columns(audited_tier_expr)
    extra_keys = [
        col for col in ("period_start_date", "_is_audited") if col in tiered.columns
    ]
    join_keys = [*PERIOD_KEY, *extra_keys, "metric_id"]
    dedup_subset = [*join_keys, "source_provider"]
    latest = (
        tiered.sort(rev_cols).unique(subset=dedup_subset, keep="last")
        if rev_cols
        else tiered.unique(subset=dedup_subset, keep="last")
    )

    fhsc_df = latest.filter(pl.col("source_provider") == "fhsc").select(
        *join_keys, pl.col("value").cast(pl.Float64).alias("fhsc_value")
    )
    vietcap_df = latest.filter(pl.col("source_provider") == "vietcap_iq").select(
        *join_keys, pl.col("value").cast(pl.Float64).alias("vietcap_value")
    )

    if fhsc_df.is_empty() or vietcap_df.is_empty():
        return CrossProviderReconciliationResult(
            coalesced_facts=coalesce_cross_provider_facts(
                facts, provider_priority=provider_priority
            ),
            discrepancies=_empty_discrepancies(),
            info_signals=_empty_signals(),
        )

    denom = pl.max_horizontal(
        pl.col("fhsc_value").abs(),
        pl.col("vietcap_value").abs(),
        pl.lit(float(materiality_floor_vnd), dtype=pl.Float64),
    )
    discrepancies = (
        fhsc_df.join(vietcap_df, on=join_keys, how="inner")
        .with_columns(
            ((pl.col("fhsc_value") - pl.col("vietcap_value")).abs() / denom).alias(
                "relative_delta"
            )
        )
        .filter(pl.col("relative_delta") > float(rel_tol))
        .sort([*PERIOD_KEY, "metric_id", "relative_delta"])
        .unique(subset=[*PERIOD_KEY, "metric_id"], keep="last")
        .select(DISCREPANCY_SCHEMA.names())
        .sort([*PERIOD_KEY, "metric_id"])
    )

    if discrepancies.is_empty():
        info_signals = _empty_signals()
    else:
        if record_metrics:
            counts = discrepancies.group_by("metric_id").len().to_dicts()
            for item in counts:
                mid = str(item["metric_id"])
                if mid in RECONCILIATION_ANCHOR_METRICS:
                    _PROVIDER_DISCREPANCY_TOTAL.labels(metric_id=mid).inc(
                        float(int(str(item["len"])))
                    )
        info_signals = (
            discrepancies.select(list(PERIOD_KEY))
            .unique()
            .with_columns(
                pl.lit(CROSS_PROVIDER_DISCREPANCY_CODE, dtype=pl.String).alias("code")
            )
            .select(PERIOD_SIGNAL_SCHEMA.names())
            .sort(list(PERIOD_KEY))
        )

    coalesced = coalesce_cross_provider_facts(
        facts, provider_priority=provider_priority
    )
    return CrossProviderReconciliationResult(
        coalesced_facts=coalesced,
        discrepancies=discrepancies,
        info_signals=info_signals,
    )


__all__ = [
    "CROSS_PROVIDER_DISCREPANCY_CODE",
    "DEFAULT_DISCREPANCY_REL_TOL",
    "DEFAULT_MATERIALITY_FLOOR_VND",
    "DISCREPANCY_SCHEMA",
    "RECONCILIATION_ANCHOR_METRICS",
    "CrossProviderReconciliationResult",
    "get_reconciliation_metrics_registry",
    "reconcile_cross_provider_facts",
]
