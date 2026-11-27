"""Extension Port 4: Corporate Bond Signals & FX Debt Exposure contracts (V3-31 / B-03).

Verified Sources:
    - spec/v3/SPEC-v3-fx-stress-and-bond-signals.md (§5.1, §6)
    - Decree 153/2020/ND-CP & Decree 65/2022/ND-CP (HNX Cbonds corporate bond disclosure).
    - Circular 200/2014/TT-BTC & Circular 99/2025/TT-BTC (FX debt disclosures).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Protocol, runtime_checkable

import polars as pl

from finintel.exceptions import ContractViolationError

CORPORATE_BOND_SIGNALS_TABLE = "corporate_bond_signals.parquet"
CORPORATE_BOND_SIGNALS_KEY: tuple[str, ...] = ("issuer_id", "as_of_date")

FX_DEBT_EXPOSURE_TABLE = "fx_debt_exposure.parquet"
FX_DEBT_EXPOSURE_KEY: tuple[str, ...] = (
    "issuer_id",
    "as_of_date",
    "statement_scope",
    "period_type",
)

BOND_PORT_TABLES: frozenset[str] = frozenset(
    {CORPORATE_BOND_SIGNALS_TABLE, FX_DEBT_EXPOSURE_TABLE}
)

FX_STATEMENT_SCOPES: frozenset[str] = frozenset({"CONSOLIDATED", "SEPARATE"})
FX_PERIOD_TYPES: frozenset[str] = frozenset({"QUARTER", "ANNUAL"})

CORPORATE_BOND_SIGNALS_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "ticker": pl.String,
        "as_of_date": pl.Date,
        "outstanding_bond_vnd": pl.Float64,
        "bond_maturity_12m_vnd": pl.Float64,
        "bond_payment_delay_flag": pl.Boolean,
        "source_provider": pl.String,
        "source_url": pl.String,
        "payload_sha256": pl.String,
    }
)

FX_DEBT_EXPOSURE_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "ticker": pl.String,
        "as_of_date": pl.Date,
        "statement_scope": pl.String,
        "period_type": pl.String,
        "net_fx_debt_vnd": pl.Float64,
        "fx_currency": pl.String,
        "source_provider": pl.String,
        "source_url": pl.String,
        "payload_sha256": pl.String,
    }
)


def _require_non_empty_strings(
    frame: pl.DataFrame, columns: Sequence[str], *, label: str
) -> None:
    for col_name in columns:
        if (
            frame.schema[col_name] == pl.String
            and frame.select(pl.col(col_name).str.strip_chars().eq("").any()).item()
        ):
            raise ContractViolationError(
                f"{label} require non-empty {col_name} values."
            )


def _require_https_and_sha256(frame: pl.DataFrame, *, label: str) -> None:
    if (
        frame.schema["source_url"] == pl.String
        and frame.select(
            (~pl.col("source_url").str.strip_chars().str.starts_with("https://")).any()
        ).item()
    ):
        raise ContractViolationError(f"{label} require https:// source_url values.")
    if (
        frame.schema["payload_sha256"] == pl.String
        and frame.select(
            (~pl.col("payload_sha256").str.contains(r"^[0-9a-fA-F]{64}$")).any()
        ).item()
    ):
        raise ContractViolationError(
            f"{label} require 64-char hex payload_sha256 values."
        )


def _canonicalize_port_table(
    frame: pl.DataFrame, schema: Mapping[str, pl.DataType]
) -> pl.DataFrame:
    missing = [name for name in schema if name not in frame.columns]
    if missing:
        raise ContractViolationError(
            f"Port 4 table is missing required columns: {', '.join(missing)}."
        )
    expressions = [
        pl.col(name).cast(dtype, strict=True).alias(name)
        for name, dtype in schema.items()
    ]
    extras = [name for name in frame.columns if name not in schema]
    try:
        return frame.select([*expressions, *[pl.col(name) for name in extras]])
    except pl.exceptions.PolarsError as exc:
        raise ContractViolationError(
            "Port 4 table contains values that cannot be cast to its schema."
        ) from exc


def validate_corporate_bond_signals(frame: pl.DataFrame) -> pl.DataFrame:
    """Canonicalize and strictly validate a Port 4 ``corporate_bond_signals`` table."""
    canonical = _canonicalize_port_table(frame, CORPORATE_BOND_SIGNALS_SCHEMA)
    null_cols = [
        name
        for name in CORPORATE_BOND_SIGNALS_SCHEMA
        if canonical[name].is_null().any()
    ]
    if null_cols:
        raise ContractViolationError(
            "Corporate bond signals contain nulls in non-nullable columns: "
            + ", ".join(null_cols)
            + "."
        )
    if canonical.is_empty():
        return canonical

    _require_non_empty_strings(
        canonical,
        ("issuer_id", "ticker", "source_provider", "source_url", "payload_sha256"),
        label="Corporate bond signals",
    )
    _require_https_and_sha256(canonical, label="Corporate bond signals")

    for num_col in ("outstanding_bond_vnd", "bond_maturity_12m_vnd"):
        if canonical.select(
            (~pl.col(num_col).is_finite() | (pl.col(num_col) < 0.0)).any()
        ).item():
            raise ContractViolationError(
                f"Corporate bond signals require finite non-negative {num_col} values."
            )

    if canonical.select(list(CORPORATE_BOND_SIGNALS_KEY)).is_duplicated().any():
        raise ContractViolationError(
            "Corporate bond signals contain duplicate (issuer_id, as_of_date) keys."
        )
    return canonical


def validate_fx_debt_exposure(frame: pl.DataFrame) -> pl.DataFrame:
    """Canonicalize and strictly validate a Port 4 ``fx_debt_exposure`` table."""
    canonical = _canonicalize_port_table(frame, FX_DEBT_EXPOSURE_SCHEMA)
    non_null_cols = (
        "issuer_id",
        "ticker",
        "as_of_date",
        "statement_scope",
        "period_type",
        "source_provider",
        "source_url",
        "payload_sha256",
    )
    null_cols = [name for name in non_null_cols if canonical[name].is_null().any()]
    if null_cols:
        raise ContractViolationError(
            "FX debt exposure contains nulls in non-nullable columns: "
            + ", ".join(null_cols)
            + "."
        )
    if canonical.is_empty():
        return canonical

    _require_non_empty_strings(
        canonical,
        ("issuer_id", "ticker", "source_provider", "source_url", "payload_sha256"),
        label="FX debt exposure",
    )
    _require_https_and_sha256(canonical, label="FX debt exposure")

    bad_scopes = [
        val
        for val in canonical["statement_scope"].unique().to_list()
        if val not in FX_STATEMENT_SCOPES
    ]
    if bad_scopes:
        raise ContractViolationError(
            f"FX debt exposure contains invalid statement_scope={bad_scopes!r}."
        )
    bad_ptypes = [
        val
        for val in canonical["period_type"].unique().to_list()
        if val not in FX_PERIOD_TYPES
    ]
    if bad_ptypes:
        raise ContractViolationError(
            f"FX debt exposure contains invalid period_type={bad_ptypes!r}."
        )

    if canonical.select(
        (
            pl.col("net_fx_debt_vnd").is_not_null()
            & ~pl.col("net_fx_debt_vnd").is_finite()
        ).any()
    ).item():
        raise ContractViolationError(
            "FX debt exposure requires finite net_fx_debt_vnd values when non-null."
        )

    if canonical.select(list(FX_DEBT_EXPOSURE_KEY)).is_duplicated().any():
        raise ContractViolationError(
            "FX debt exposure contains duplicate primary keys."
        )
    return canonical


@runtime_checkable
class CorporateBondSignalProvider(Protocol):
    """Port 4 provider contract for point-in-time corporate bond maturity and delay signals."""

    def fetch_bond_signals(
        self,
        *,
        tickers: Sequence[str] | None = None,
        as_of: date | None = None,
    ) -> pl.DataFrame:
        """Return bond signal records conforming to ``CORPORATE_BOND_SIGNALS_SCHEMA``."""
        ...


@runtime_checkable
class FxDebtExposureProvider(Protocol):
    """Port 4 provider contract for disclosed net foreign-currency debt exposure."""

    def fetch_fx_debt_exposure(
        self,
        *,
        tickers: Sequence[str] | None = None,
        as_of: date | None = None,
    ) -> pl.DataFrame:
        """Return FX debt exposure records conforming to ``FX_DEBT_EXPOSURE_SCHEMA``."""
        ...


__all__ = [
    "BOND_PORT_TABLES",
    "CORPORATE_BOND_SIGNALS_KEY",
    "CORPORATE_BOND_SIGNALS_SCHEMA",
    "CORPORATE_BOND_SIGNALS_TABLE",
    "CorporateBondSignalProvider",
    "FX_DEBT_EXPOSURE_KEY",
    "FX_DEBT_EXPOSURE_SCHEMA",
    "FX_DEBT_EXPOSURE_TABLE",
    "FX_PERIOD_TYPES",
    "FX_STATEMENT_SCOPES",
    "FxDebtExposureProvider",
    "validate_corporate_bond_signals",
    "validate_fx_debt_exposure",
]
