"""Canonical stub-year harmonization and Vietcap IQ supplement merge helpers.

Centralizes multi-provider fact merging so both Dagster autonomous assets
(``published_financial_marts``) and offline evaluation scripts execute the exact
same schema, semi-join, and stub-period invariants.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import polars as pl

from finintel.ingestion.vietcap_supplement import (
    PeriodDateTuple,
    parse_vietcap_anchor_facts,
    parse_vietcap_annual_eps_facts,
    parse_vietcap_depreciation_facts,
)
from finintel.normalization.mapper import FACT_SCHEMA
from finintel.normalization.reconciliation import (
    PERIOD_KEY,
    RECONCILIATION_ANCHOR_METRICS,
)
from finintel.storage.schemas import canonicalize_financial_facts

logger = logging.getLogger(__name__)

VIETCAP_SUPPLEMENT_FILENAME = "vietcap_supplement_facts.parquet"


def harmonize_stub_fiscal_year_start_dates(
    facts: pl.DataFrame,
) -> tuple[pl.DataFrame, int]:
    """Align stub-year ``fiscal_year_start_date``, ``period_start_date``, and ``period_days``."""
    if facts.is_empty():
        return facts, 0
    slot_cols = [
        "issuer_id",
        "statement_scope",
        "period_type",
        "fiscal_year",
        "period_end_date",
    ]
    if set(slot_cols) - set(facts.columns):
        return facts, 0
    flow_starts = facts.group_by(slot_cols).agg(
        pl.col("fiscal_year_start_date").max().alias("_fysd")
    )
    fysd_expr = pl.coalesce("_fysd", "fiscal_year_start_date")
    pstart_expr = pl.max_horizontal("period_start_date", fysd_expr)
    harmonized = (
        facts.join(flow_starts, on=slot_cols, how="left")
        .with_columns(
            fysd_expr.alias("fiscal_year_start_date"),
            pstart_expr.alias("period_start_date"),
            ((pl.col("period_end_date") - pstart_expr).dt.total_days() + 1)
            .cast(pl.Int32)
            .alias("period_days"),
        )
        .drop("_fysd")
    )
    changed_rows = int(
        facts.select(
            (pl.col("fiscal_year_start_date") != harmonized["fiscal_year_start_date"])
            .cast(pl.Int64)
            .sum()
        ).item()
    )
    return harmonized, changed_rows


def merge_vietcap_supplement_facts(
    primary_facts: pl.DataFrame,
    *,
    curated_root: Path | None = None,
    supplement_path: Path | None = None,
) -> pl.DataFrame:
    """Semi-join optional ``vietcap_supplement_facts.parquet`` to primary periods and harmonize stubs."""
    canonical_primary = canonicalize_financial_facts(primary_facts)
    if canonical_primary.is_empty():
        return canonical_primary

    resolved_supp = supplement_path
    if resolved_supp is None and curated_root is not None:
        candidate = curated_root / VIETCAP_SUPPLEMENT_FILENAME
        if candidate.is_file():
            resolved_supp = candidate

    combined = canonical_primary
    if resolved_supp is not None and resolved_supp.is_file():
        supp_raw = canonicalize_financial_facts(pl.read_parquet(resolved_supp))
        if not supp_raw.is_empty():
            primary_periods = canonical_primary.select(list(PERIOD_KEY)).unique()
            supp_matched = supp_raw.join(
                primary_periods, on=list(PERIOD_KEY), how="semi"
            )
            if not supp_matched.is_empty():
                logger.info(
                    "Merged %d supplementary Vietcap facts from %s",
                    supp_matched.height,
                    resolved_supp,
                )
                combined = pl.concat([canonical_primary, supp_matched], how="vertical")

    harmonized, _ = harmonize_stub_fiscal_year_start_dates(combined)
    return canonicalize_financial_facts(harmonized)


def rebuild_vietcap_supplement_parquet(
    *,
    data_root: Path,
    primary_facts: pl.DataFrame,
    issuers: pl.DataFrame,
    output_path: Path | None = None,
) -> pl.DataFrame:
    """Rebuild ``vietcap_supplement_facts.parquet`` from CAS raw JSON caches with anchor metrics."""
    supp_dir = data_root / "raw_disclosures" / "vietcap_supplement"
    bs_dir = data_root / "raw_disclosures" / "vietcap_iq"
    note_dir = supp_dir / "NOTE"
    cf_dir = supp_dir / "CASH_FLOW"
    is_dir = supp_dir / "INCOME_STATEMENT"

    canonical_primary, _ = harmonize_stub_fiscal_year_start_dates(
        canonicalize_financial_facts(primary_facts)
    )
    cons_facts = canonical_primary.filter(pl.col("statement_scope") == "CONSOLIDATED")
    if cons_facts.is_empty():
        empty = pl.DataFrame(schema=FACT_SCHEMA)
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            empty.write_parquet(output_path)
        return empty

    slot_df = (
        cons_facts.select(
            "issuer_id",
            "period_type",
            "fiscal_year",
            "fiscal_quarter",
            "fiscal_year_start_date",
            "period_start_date",
            "period_end_date",
            "taxonomy_version",
        )
        .unique(
            subset=["issuer_id", "period_type", "fiscal_year", "fiscal_quarter"],
            keep="last",
        )
        .sort(["issuer_id", "period_type", "period_end_date"])
    )
    period_maps: dict[str, dict[tuple[str, int, int | None], PeriodDateTuple]] = {}
    for row in slot_df.to_dicts():
        iid = str(row["issuer_id"])
        fq_val = row["fiscal_quarter"]
        key = (
            str(row["period_type"]),
            int(row["fiscal_year"]),
            int(fq_val) if fq_val is not None else None,
        )
        fy_s = row["fiscal_year_start_date"]
        p_s = row["period_start_date"]
        p_e = row["period_end_date"]
        assert (
            isinstance(fy_s, date) and isinstance(p_s, date) and isinstance(p_e, date)
        )
        period_maps.setdefault(iid, {})[key] = (
            fy_s,
            p_s,
            p_e,
            str(row["taxonomy_version"]),
        )

    anchor_df = (
        cons_facts.filter(
            pl.col("metric_id").is_in(RECONCILIATION_ANCHOR_METRICS)
            & pl.col("value").is_not_null()
            & pl.col("value").is_finite()
        )
        .group_by(["issuer_id", "period_type", "fiscal_year", "fiscal_quarter"])
        .agg(pl.col("metric_id").unique().alias("metrics"))
    )
    anchor_maps: dict[str, dict[tuple[str, int, int | None], frozenset[str]]] = {}
    for row in anchor_df.to_dicts():
        iid = str(row["issuer_id"])
        fq_val = row["fiscal_quarter"]
        key = (
            str(row["period_type"]),
            int(row["fiscal_year"]),
            int(fq_val) if fq_val is not None else None,
        )
        anchor_maps.setdefault(iid, {})[key] = frozenset(str(m) for m in row["metrics"])

    frames: list[pl.DataFrame] = []
    issuer_rows = issuers.select("issuer_id", "ticker").unique().sort("issuer_id")
    for irow in issuer_rows.to_dicts():
        iid = str(irow["issuer_id"])
        tk = str(irow["ticker"]).strip().upper()
        pmap = period_maps.get(iid)
        if not pmap:
            continue

        note_p = note_dir / f"{tk}.json"
        cf_p = cf_dir / f"{tk}.json"
        is_p = is_dir / f"{tk}.json"
        bs_p = bs_dir / f"{tk}.json"

        note_b = note_p.read_bytes() if note_p.is_file() else None
        cf_b = cf_p.read_bytes() if cf_p.is_file() else None
        is_b = is_p.read_bytes() if is_p.is_file() else None
        bs_b = bs_p.read_bytes() if bs_p.is_file() else None

        issuer_supp: list[pl.DataFrame] = []
        if note_b is not None or cf_b is not None:
            dep = parse_vietcap_depreciation_facts(
                issuer_id=iid,
                ticker=tk,
                note_bytes=note_b,
                cash_flow_bytes=cf_b,
                existing_period_dates=pmap,
            )
            if not dep.is_empty():
                issuer_supp.append(dep)
        if is_b is not None:
            eps = parse_vietcap_annual_eps_facts(
                issuer_id=iid,
                ticker=tk,
                income_statement_bytes=is_b,
                existing_period_dates=pmap,
            )
            if not eps.is_empty():
                issuer_supp.append(eps)

        if not issuer_supp:
            continue
        frames.extend(issuer_supp)

        # Restrict anchor verification to slots where Vietcap supplement contributed a row
        # AND primary FHSC already holds non-null anchor metrics.
        supp_cat = pl.concat(issuer_supp, how="vertical")
        used_slots: set[tuple[str, int, int | None]] = {
            (
                str(r["period_type"]),
                int(r["fiscal_year"]),
                int(r["fiscal_quarter"]) if r["fiscal_quarter"] is not None else None,
            )
            for r in supp_cat.select("period_type", "fiscal_year", "fiscal_quarter")
            .unique()
            .to_dicts()
        }
        amap_full = anchor_maps.get(iid, {})
        scoped_anchors = {
            slot: amap_full[slot] for slot in used_slots if slot in amap_full
        }
        scoped_periods = {slot: pmap[slot] for slot in scoped_anchors if slot in pmap}
        if scoped_periods and (bs_b is not None or is_b is not None):
            anc = parse_vietcap_anchor_facts(
                issuer_id=iid,
                ticker=tk,
                balance_sheet_bytes=bs_b,
                income_statement_bytes=is_b,
                existing_period_dates=scoped_periods,
                existing_anchor_metrics=scoped_anchors,
            )
            if not anc.is_empty():
                frames.append(anc)

    result = (
        canonicalize_financial_facts(pl.concat(frames, how="vertical"))
        if frames
        else pl.DataFrame(schema=FACT_SCHEMA)
    )
    target = output_path or (data_root / "curated" / VIETCAP_SUPPLEMENT_FILENAME)
    target.parent.mkdir(parents=True, exist_ok=True)
    result.write_parquet(target)
    return result


__all__ = [
    "VIETCAP_SUPPLEMENT_FILENAME",
    "harmonize_stub_fiscal_year_start_dates",
    "merge_vietcap_supplement_facts",
    "rebuild_vietcap_supplement_parquet",
]
