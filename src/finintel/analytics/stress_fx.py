"""Deterministic FX shock propagation and corporate bond liquidity evaluation (V3-32).

Verified Sources:
    - spec/v3/SPEC-v3-fx-stress-and-bond-signals.md (§1, §4, §5.2, §6, §7)
    - Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
      IMF Working Paper WP/15/216.
    - Circular 200/2014/TT-BTC & Circular 99/2025/TT-BTC (FX debt revaluation & cash coverage).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import polars as pl

from finintel.exceptions import ContractViolationError

FX_DEPRECIATION_BOUNDS: tuple[float, float] = (-0.30, 0.50)
FX_COVERAGE_REPORTED: Literal["REPORTED"] = "REPORTED"
FX_COVERAGE_NOT_DISCLOSED: Literal["NOT_DISCLOSED"] = "NOT_DISCLOSED"

BondPressureStatus = Literal[
    "NO_BONDS",
    "COVERED",
    "WATCH",
    "HIGH_PRESSURE",
    "PAYMENT_DELAY",
    "MISSING_CASH_DATA",
]


@dataclass(frozen=True, slots=True)
class BondLiquidityAssessment:
    """Deterministic 12-month corporate bond maturity vs cash & ST investments assessment."""

    has_outstanding_bonds: bool
    bond_maturity_to_cash_ratio: float | None
    liquidity_pressure_status: BondPressureStatus


def validate_fx_depreciation_pct(raw: object) -> float:
    """Validate optional ``fx_depreciation_pct`` within finite bounds ``[-0.30, 0.50]``."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ContractViolationError(
            "StressScenario parameter fx_depreciation_pct must be a finite number."
        )
    value = float(raw)
    low, high = FX_DEPRECIATION_BOUNDS
    if not math.isfinite(value) or not (low <= value <= high):
        raise ContractViolationError(
            f"StressScenario parameter fx_depreciation_pct={value!r} "
            f"outside allowed bounds [{low}, {high}]."
        )
    return value


def attach_fx_debt_exposure_pit(
    inputs_df: pl.DataFrame,
    fx_exposure_df: pl.DataFrame | None,
    *,
    as_of: date | None,
    statement_scope: str,
    period_type: str,
) -> pl.DataFrame:
    """Left-join point-in-time ``net_fx_debt_vnd`` without fabricating undisclosed values."""
    clean_base = (
        inputs_df.drop("net_fx_debt_vnd")
        if "net_fx_debt_vnd" in inputs_df.columns
        else inputs_df
    )
    if fx_exposure_df is None or fx_exposure_df.is_empty() or clean_base.is_empty():
        return clean_base.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("net_fx_debt_vnd")
        )

    scoped = fx_exposure_df.filter(
        (pl.col("statement_scope") == statement_scope)
        & (pl.col("period_type") == period_type)
    )
    if as_of is not None:
        scoped = scoped.filter(pl.col("as_of_date") <= as_of)
    if scoped.is_empty():
        return clean_base.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("net_fx_debt_vnd")
        )

    latest_fx = (
        scoped.sort(["issuer_id", "as_of_date"])
        .unique(subset=["issuer_id"], keep="last")
        .select(
            pl.col("issuer_id").cast(pl.String),
            pl.col("net_fx_debt_vnd").cast(pl.Float64, strict=False),
        )
    )
    return clean_base.join(latest_fx, on="issuer_id", how="left")


def load_fx_debt_exposure_from_roots(
    candidate_roots: Sequence[Path],
    *,
    issuer_ids: Sequence[str] | None = None,
) -> pl.DataFrame | None:
    """Load Port 4 ``fx_debt_exposure.parquet`` from standard candidate directories if present."""
    rel_candidates = (
        Path("bonds") / "fx_debt_exposure.parquet",
        Path("fx_debt_exposure.parquet"),
        Path("curated") / "fx_debt_exposure.parquet",
    )
    for root in candidate_roots:
        for rel in rel_candidates:
            parquet_path = root / rel
            if parquet_path.is_file():
                lf = pl.scan_parquet(parquet_path)
                if issuer_ids:
                    lf = lf.filter(pl.col("issuer_id").is_in(list(issuer_ids)))
                return lf.collect()
    return None


def prepare_fx_revaluation_stage(
    frame: pl.DataFrame,
    *,
    fx_depreciation_pct: float,
) -> pl.DataFrame:
    """Attach ``net_fx_debt_vnd``, ``fx_coverage_status``, and ``fx_revaluation_loss_vnd``."""
    c = pl.col
    fx_shock = validate_fx_depreciation_pct(fx_depreciation_pct)

    raw_fx_expr = (
        c("net_fx_debt_vnd").cast(pl.Float64, strict=False)
        if "net_fx_debt_vnd" in frame.columns
        else pl.lit(None, dtype=pl.Float64)
    )
    disclosed_cond = raw_fx_expr.is_not_null() & raw_fx_expr.is_finite()
    clean_fx_expr = (
        pl.when(disclosed_cond)
        .then(raw_fx_expr)
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .alias("net_fx_debt_vnd")
    )

    staged = frame.with_columns(clean_fx_expr)
    return staged.with_columns(
        pl.when(c("net_fx_debt_vnd").is_not_null())
        .then(pl.lit(FX_COVERAGE_REPORTED))
        .otherwise(pl.lit(FX_COVERAGE_NOT_DISCLOSED))
        .alias("fx_coverage_status"),
        pl.when(c("_elig") & c("net_fx_debt_vnd").is_not_null())
        .then(c("net_fx_debt_vnd") * fx_shock)
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .cast(pl.Float64)
        .alias("fx_revaluation_loss_vnd"),
    )


def fx_borrowing_cost_delta_expr() -> pl.Expr:
    """Polars expression adding FX revaluation loss ONLY for issuers with disclosed FX debt."""
    return (
        pl.when(pl.col("fx_revaluation_loss_vnd").is_not_null())
        .then(pl.col("fx_revaluation_loss_vnd"))
        .otherwise(pl.lit(0.0))
    )


def evaluate_bond_liquidity_signal(
    *,
    outstanding_bond_vnd: float | None,
    bond_maturity_12m_vnd: float | None,
    bond_payment_delay_flag: bool,
    total_cash_and_st_investments_vnd: float | None,
) -> BondLiquidityAssessment:
    """Evaluate 12-month corporate bond maturity pressure against cash & ST investments."""
    if (
        outstanding_bond_vnd is None
        or not math.isfinite(outstanding_bond_vnd)
        or outstanding_bond_vnd <= 0.0
    ):
        return BondLiquidityAssessment(
            has_outstanding_bonds=False,
            bond_maturity_to_cash_ratio=None,
            liquidity_pressure_status="NO_BONDS",
        )

    mat_12m = (
        float(bond_maturity_12m_vnd)
        if bond_maturity_12m_vnd is not None and math.isfinite(bond_maturity_12m_vnd)
        else 0.0
    )
    ratio: float | None = None
    if (
        total_cash_and_st_investments_vnd is not None
        and math.isfinite(total_cash_and_st_investments_vnd)
        and total_cash_and_st_investments_vnd > 0.0
    ):
        ratio = mat_12m / float(total_cash_and_st_investments_vnd)

    status: BondPressureStatus
    if bond_payment_delay_flag:
        status = "PAYMENT_DELAY"
    elif ratio is None and mat_12m > 0.0:
        status = "MISSING_CASH_DATA"
    elif ratio is not None and ratio >= 1.0:
        status = "HIGH_PRESSURE"
    elif ratio is not None and ratio >= 0.5:
        status = "WATCH"
    else:
        status = "COVERED"

    return BondLiquidityAssessment(
        has_outstanding_bonds=True,
        bond_maturity_to_cash_ratio=ratio,
        liquidity_pressure_status=status,
    )


__all__ = [
    "BondLiquidityAssessment",
    "BondPressureStatus",
    "FX_COVERAGE_NOT_DISCLOSED",
    "FX_COVERAGE_REPORTED",
    "FX_DEPRECIATION_BOUNDS",
    "attach_fx_debt_exposure_pit",
    "evaluate_bond_liquidity_signal",
    "fx_borrowing_cost_delta_expr",
    "load_fx_debt_exposure_from_roots",
    "prepare_fx_revaluation_stage",
    "validate_fx_depreciation_pct",
]
