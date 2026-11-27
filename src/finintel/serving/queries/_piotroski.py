"""Headless Piotroski F-Score missing-criteria diagnostics for Layer 5 serving (D4).

Verified Sources:
- Piotroski, J. D. (2000). "Value Investing: The Use of Historical Financial Statement
  Information to Separate Winners from Losers." Journal of Accounting Research, Vol. 38.
- Circular 200/2014/TT-BTC & Circular 99/2025/TT-BTC cross-taxonomy guard
  (``src/finintel/ml/classical_scores.py:98-168``).
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import polars as pl

PIOTROSKI_CRITERIA_CODES: tuple[str, ...] = (
    "F1_ROA",
    "F2_CFO",
    "F3_DELTA_ROA",
    "F4_ACCRUAL",
    "F5_DELTA_LEVERAGE",
    "F6_DELTA_LIQUIDITY",
    "F7_NO_DILUTION",
    "F8_DELTA_MARGIN",
    "F9_DELTA_TURNOVER",
)

PIOTROSKI_EXTRA_FACT_METRICS: tuple[str, ...] = (
    "BS_LONG_TERM_BORROWINGS",
    "BS_ORDINARY_SHARES_CAPITAL",
    "CF_EQUITY_ISSUANCE_PROCEEDS",
    "IS_DILUTIVE_NONCASH_EQUITY_ISSUANCE",
)

_STOCK_METRICS = frozenset(
    {"BS_TOTAL_ASSETS", "BS_LONG_TERM_BORROWINGS", "BS_ORDINARY_SHARES_CAPITAL"}
)


def _opt_finite(raw: object) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if math.isfinite(val) else None
    return None


def _opt_int(raw: object) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and math.isfinite(raw):
        return int(raw)
    return None


def _find_prior_year_date(
    target_date: date,
    ratio_by_date: dict[date, dict[str, Any]],
) -> date | None:
    """Match prior-year fiscal slot matching ``intermediates.with_prior_year``."""
    curr = ratio_by_date.get(target_date)
    curr_fy = _opt_int(curr.get("fiscal_year")) if curr is not None else None
    curr_fq = _opt_int(curr.get("fiscal_quarter")) if curr is not None else None
    if curr_fy is not None:
        for cand_date, cand_row in ratio_by_date.items():
            if cand_date >= target_date:
                continue
            cand_fy = _opt_int(cand_row.get("fiscal_year"))
            cand_fq = _opt_int(cand_row.get("fiscal_quarter"))
            if cand_fy == curr_fy - 1 and cand_fq == curr_fq:
                return cand_date
    for cand_date in sorted(ratio_by_date.keys(), reverse=True):
        delta = (target_date - cand_date).days
        if 350 <= delta <= 380:
            return cand_date
    return None


def _diagnose_single_period(
    pend: date,
    *,
    period_type: str,
    ratio_by_date: dict[date, dict[str, Any]],
    fact_lookup: dict[tuple[date, str], dict[str, Any]],
) -> tuple[list[str], str]:
    r_curr = ratio_by_date.get(pend)
    prior_end = _find_prior_year_date(pend, ratio_by_date)
    r_prev = ratio_by_date.get(prior_end) if prior_end is not None else None
    pprior_end = (
        _find_prior_year_date(prior_end, ratio_by_date)
        if prior_end is not None
        else None
    )

    def _fact_val(d: date | None, metric_id: str) -> float | None:
        if d is None:
            return None
        row = fact_lookup.get((d, metric_id))
        if row is None:
            return None
        if (
            metric_id not in _STOCK_METRICS
            and period_type == "QUARTER"
            and "ttm_value" in row
        ):
            return _opt_finite(row.get("ttm_value"))
        return _opt_finite(row.get("value"))

    def _fact_mixed(d: date | None, metric_id: str) -> bool:
        if d is None:
            return False
        row = fact_lookup.get((d, metric_id))
        if row is None:
            return False
        return str(row.get("computation_note") or "") == "MIXED_TAXONOMY"

    def _fact_comparable(d: date | None, metric_id: str) -> bool:
        if d is None:
            return True
        row = fact_lookup.get((d, metric_id))
        if row is None or row.get("comparable_across_taxonomy") is None:
            return True
        return bool(row.get("comparable_across_taxonomy"))

    curr_tax = str(r_curr.get("taxonomy_version") or "") if r_curr else ""
    prev_tax = str(r_prev.get("taxonomy_version") or "") if r_prev else ""

    def _cross_tax(*metric_ids: str) -> bool:
        if r_prev is None or not curr_tax or not prev_tax or curr_tax == prev_tax:
            return False
        return any(
            not _fact_comparable(pend, m) or not _fact_comparable(prior_end, m)
            for m in metric_ids
        )

    missing: list[str] = []
    has_taxonomy_block = False
    has_missing_prior_base = r_prev is None

    # F1_ROA
    roa = _opt_finite(r_curr.get("roa")) if r_curr else None
    roa_flag = str(r_curr.get("roa_flag") or "") if r_curr else ""
    if roa_flag == "MIXED_TAXONOMY":
        missing.append("F1_ROA")
        has_taxonomy_block = True
    elif roa is None:
        missing.append("F1_ROA")
        if roa_flag == "MISSING_PRIOR_YEAR_BASE":
            has_missing_prior_base = True

    # F2_CFO
    cfo = _fact_val(pend, "CF_OPERATING_CASH_FLOW")
    cfo_mixed = _fact_mixed(pend, "CF_OPERATING_CASH_FLOW")
    if cfo_mixed:
        missing.append("F2_CFO")
        has_taxonomy_block = True
    elif cfo is None:
        missing.append("F2_CFO")

    # F3_DELTA_ROA
    roa_prior = _opt_finite(r_prev.get("roa")) if r_prev else None
    roa_prior_flag = str(r_prev.get("roa_flag") or "") if r_prev else ""
    if (
        roa_flag == "MIXED_TAXONOMY"
        or roa_prior_flag == "MIXED_TAXONOMY"
        or _cross_tax("BS_TOTAL_ASSETS", "IS_NET_INCOME")
    ):
        missing.append("F3_DELTA_ROA")
        has_taxonomy_block = True
    elif r_prev is None or roa is None or roa_prior is None:
        missing.append("F3_DELTA_ROA")
        if (
            roa_flag == "MISSING_PRIOR_YEAR_BASE"
            or roa_prior_flag == "MISSING_PRIOR_YEAR_BASE"
        ):
            has_missing_prior_base = True

    # F4_ACCRUAL
    earnings = _fact_val(pend, "IS_NET_INCOME")
    if cfo_mixed or _fact_mixed(pend, "IS_NET_INCOME"):
        missing.append("F4_ACCRUAL")
        has_taxonomy_block = True
    elif cfo is None or earnings is None:
        missing.append("F4_ACCRUAL")

    # F5_DELTA_LEVERAGE
    assets = _fact_val(pend, "BS_TOTAL_ASSETS")
    assets_prior = _fact_val(prior_end, "BS_TOTAL_ASSETS")
    assets_pprior = _fact_val(pprior_end, "BS_TOTAL_ASSETS")
    lt_debt = _fact_val(pend, "BS_LONG_TERM_BORROWINGS")
    lt_debt_prior = _fact_val(prior_end, "BS_LONG_TERM_BORROWINGS")
    if (
        _cross_tax("BS_TOTAL_ASSETS", "BS_LONG_TERM_BORROWINGS")
        or _fact_mixed(pend, "BS_TOTAL_ASSETS")
        or _fact_mixed(pend, "BS_LONG_TERM_BORROWINGS")
        or _fact_mixed(prior_end, "BS_TOTAL_ASSETS")
        or _fact_mixed(prior_end, "BS_LONG_TERM_BORROWINGS")
    ):
        missing.append("F5_DELTA_LEVERAGE")
        has_taxonomy_block = True
    elif (
        r_prev is None
        or assets is None
        or assets <= 0.0
        or assets_prior is None
        or assets_prior <= 0.0
        or assets_pprior is None
        or assets_pprior <= 0.0
        or lt_debt is None
        or lt_debt_prior is None
    ):
        missing.append("F5_DELTA_LEVERAGE")

    # F6_DELTA_LIQUIDITY
    cr = _opt_finite(r_curr.get("current_ratio")) if r_curr else None
    cr_flag = str(r_curr.get("current_ratio_flag") or "") if r_curr else ""
    cr_prior = _opt_finite(r_prev.get("current_ratio")) if r_prev else None
    cr_prior_flag = str(r_prev.get("current_ratio_flag") or "") if r_prev else ""
    if (
        cr_flag == "MIXED_TAXONOMY"
        or cr_prior_flag == "MIXED_TAXONOMY"
        or _cross_tax("BS_CURRENT_ASSETS", "BS_CURRENT_LIABILITIES")
    ):
        missing.append("F6_DELTA_LIQUIDITY")
        has_taxonomy_block = True
    elif r_prev is None or cr is None or cr_prior is None:
        missing.append("F6_DELTA_LIQUIDITY")

    # F7_NO_DILUTION
    proceeds = _fact_val(pend, "CF_EQUITY_ISSUANCE_PROCEEDS")
    noncash = _fact_val(pend, "IS_DILUTIVE_NONCASH_EQUITY_ISSUANCE")
    shares = _fact_val(pend, "BS_ORDINARY_SHARES_CAPITAL")
    shares_prior = _fact_val(prior_end, "BS_ORDINARY_SHARES_CAPITAL")
    if _fact_mixed(pend, "CF_EQUITY_ISSUANCE_PROCEEDS") or _fact_mixed(
        pend, "IS_DILUTIVE_NONCASH_EQUITY_ISSUANCE"
    ):
        missing.append("F7_NO_DILUTION")
        has_taxonomy_block = True
    else:
        unchanged_shares = (
            shares is not None
            and shares > 0.0
            and shares_prior is not None
            and shares_prior > 0.0
            and shares <= shares_prior
            and not _cross_tax("BS_ORDINARY_SHARES_CAPITAL")
            and not _fact_mixed(pend, "BS_ORDINARY_SHARES_CAPITAL")
            and not _fact_mixed(prior_end, "BS_ORDINARY_SHARES_CAPITAL")
        )
        known_diluted = (proceeds is not None and proceeds > 0.0) or (
            noncash is not None and noncash > 0.0
        )
        known_no_dilution = (proceeds is not None and proceeds == 0.0) and (
            (noncash is not None and noncash == 0.0) or unchanged_shares
        )
        if not known_diluted and not known_no_dilution:
            missing.append("F7_NO_DILUTION")

    # F8_DELTA_MARGIN
    gm = _opt_finite(r_curr.get("gross_margin")) if r_curr else None
    gm_flag = str(r_curr.get("gross_margin_flag") or "") if r_curr else ""
    gm_prior = _opt_finite(r_prev.get("gross_margin")) if r_prev else None
    gm_prior_flag = str(r_prev.get("gross_margin_flag") or "") if r_prev else ""
    if (
        gm_flag == "MIXED_TAXONOMY"
        or gm_prior_flag == "MIXED_TAXONOMY"
        or _cross_tax("IS_GROSS_PROFIT", "IS_NET_REVENUE")
    ):
        missing.append("F8_DELTA_MARGIN")
        has_taxonomy_block = True
    elif r_prev is None or gm is None or gm_prior is None:
        missing.append("F8_DELTA_MARGIN")

    # F9_DELTA_TURNOVER
    at = _opt_finite(r_curr.get("asset_turnover")) if r_curr else None
    at_flag = str(r_curr.get("asset_turnover_flag") or "") if r_curr else ""
    at_prior = _opt_finite(r_prev.get("asset_turnover")) if r_prev else None
    at_prior_flag = str(r_prev.get("asset_turnover_flag") or "") if r_prev else ""
    if (
        at_flag == "MIXED_TAXONOMY"
        or at_prior_flag == "MIXED_TAXONOMY"
        or _fact_mixed(pend, "IS_NET_REVENUE")
    ):
        missing.append("F9_DELTA_TURNOVER")
        has_taxonomy_block = True
    elif r_prev is None or at is None or at_prior is None:
        missing.append("F9_DELTA_TURNOVER")
        if (
            at_flag == "MISSING_PRIOR_YEAR_BASE"
            or at_prior_flag == "MISSING_PRIOR_YEAR_BASE"
        ):
            has_missing_prior_base = True

    if has_taxonomy_block:
        reason = "TT200_TT99_TAXONOMY_GUARD"
    elif missing == ["F7_NO_DILUTION"]:
        reason = "MISSING_DILUTION_DATA"
    elif has_missing_prior_base and (
        ("F1_ROA" not in missing)
        or ("F2_CFO" not in missing)
        or roa_flag == "MISSING_PRIOR_YEAR_BASE"
        or at_flag == "MISSING_PRIOR_YEAR_BASE"
    ):
        reason = "MISSING_PRIOR_YEAR_BASE"
    else:
        reason = "INSUFFICIENT_COMPONENTS"

    return missing, reason


def enrich_ml_scores_piotroski_diagnostics(
    ml_scores: pl.DataFrame,
    ratios: pl.DataFrame,
    company_facts: pl.DataFrame,
    *,
    period_type: str,
) -> pl.DataFrame:
    """Attach ``piotroski_missing_criteria`` and ``piotroski_na_reason`` without mutating storage schemas."""
    if ml_scores.is_empty():
        return ml_scores.with_columns(
            pl.lit([], dtype=pl.List(pl.String)).alias("piotroski_missing_criteria"),
            pl.lit(None, dtype=pl.String).alias("piotroski_na_reason"),
        )

    ratio_by_date: dict[date, dict[str, Any]] = {}
    if not ratios.is_empty() and "period_end_date" in ratios.columns:
        for row in ratios.to_dicts():
            pend = row.get("period_end_date")
            if isinstance(pend, date):
                ratio_by_date[pend] = row

    fact_lookup: dict[tuple[date, str], dict[str, Any]] = {}
    if not company_facts.is_empty() and {"period_end_date", "metric_id"}.issubset(
        company_facts.columns
    ):
        for row in company_facts.to_dicts():
            pend = row.get("period_end_date")
            mid = row.get("metric_id")
            if isinstance(pend, date) and isinstance(mid, str):
                fact_lookup[(pend, mid)] = row

    missing_col: list[list[str]] = []
    reason_col: list[str | None] = []

    for row in ml_scores.to_dicts():
        f_score = row.get("piotroski_f_score")
        pend = row.get("period_end_date")
        if f_score is not None or not isinstance(pend, date):
            missing_col.append([])
            reason_col.append(None)
            continue
        missing, reason = _diagnose_single_period(
            pend,
            period_type=period_type,
            ratio_by_date=ratio_by_date,
            fact_lookup=fact_lookup,
        )
        missing_col.append(missing)
        reason_col.append(reason)

    return ml_scores.with_columns(
        pl.Series("piotroski_missing_criteria", missing_col, dtype=pl.List(pl.String)),
        pl.Series("piotroski_na_reason", reason_col, dtype=pl.String),
    )
