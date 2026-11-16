"""Stable Polars schemas used by the Layer 3 storage boundary."""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from finintel.exceptions import ContractViolationError


ISSUERS_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "ticker": pl.String,
        "exchange": pl.String,
        "company_name_vn": pl.String,
        "accounting_regime": pl.String,
        "icb_code_level2": pl.String,
        "icb_name_level2": pl.String,
        "icb_code_level3": pl.String,
        "icb_name_level3": pl.String,
        "industry_snapshot_date": pl.Date,
        "delisted_date": pl.Date,
    }
)
ISSUER_SCHEMA = ISSUERS_SCHEMA


FINANCIAL_FACTS_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "statement_scope": pl.String,
        "period_type": pl.String,
        "fiscal_year_start_date": pl.Date,
        "period_end_date": pl.Date,
        "metric_id": pl.String,
        "taxonomy_version": pl.String,
        "revision_no": pl.Int64,
        "filing_id": pl.String,
        "fiscal_year": pl.Int32,
        "fiscal_quarter": pl.Int8,
        "period_start_date": pl.Date,
        "published_at": pl.Date,
        "published_at_basis": pl.String,
        "effective_available_at": pl.Date,
        "revision_reason": pl.String,
        "derivation_method": pl.String,
        "audit_status": pl.String,
        "audit_opinion": pl.String,
        "value": pl.Float64,
        "ttm_value": pl.Float64,
        "computation_note": pl.String,
    }
)


UNMAPPED_METRICS_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "filing_id": pl.String,
        "raw_metric_key": pl.String,
        "raw_value": pl.String,
        "statement_scope": pl.String,
        "period_type": pl.String,
        "period_end_date": pl.Date,
        "effective_available_at": pl.Date,
    }
)
UNMAPPED_SCHEMA = UNMAPPED_METRICS_SCHEMA
FACTS_SCHEMA = FINANCIAL_FACTS_SCHEMA

FINANCIAL_FACTS_KEY = (
    "issuer_id",
    "statement_scope",
    "period_type",
    "fiscal_year_start_date",
    "period_end_date",
    "metric_id",
    "taxonomy_version",
    "revision_no",
    "filing_id",
)
OPTIONAL_FINANCIAL_FACT_COLUMNS = frozenset({"ttm_value", "computation_note"})
NON_NULL_FINANCIAL_FACT_COLUMNS = frozenset(
    name
    for name in FINANCIAL_FACTS_SCHEMA
    if name
    not in {"fiscal_quarter", "published_at", "value", *OPTIONAL_FINANCIAL_FACT_COLUMNS}
)


def validate_required_columns(
    frame: pl.DataFrame, schema: Mapping[str, pl.DataType] = FINANCIAL_FACTS_SCHEMA
) -> None:
    """Raise a contract error when a storage table misses required columns."""

    optional = (
        OPTIONAL_FINANCIAL_FACT_COLUMNS
        if schema is FINANCIAL_FACTS_SCHEMA
        else frozenset()
    )
    missing = [
        name for name in schema if name not in frame.columns and name not in optional
    ]
    if missing:
        raise ContractViolationError(
            f"Storage table is missing required columns: {', '.join(missing)}."
        )
    if schema is FINANCIAL_FACTS_SCHEMA:
        if frame["effective_available_at"].is_null().any():
            raise ContractViolationError(
                "Financial facts require a non-null effective_available_at."
            )
        null_columns = [
            name
            for name in NON_NULL_FINANCIAL_FACT_COLUMNS
            if frame[name].is_null().any()
        ]
        if null_columns:
            raise ContractViolationError(
                "Financial facts contain nulls in non-nullable columns: "
                + ", ".join(sorted(null_columns))
                + "."
            )
        if frame.select(list(FINANCIAL_FACTS_KEY)).is_duplicated().any():
            raise ContractViolationError(
                "Financial facts contain duplicate composite business keys."
            )
    elif schema is ISSUERS_SCHEMA:
        null_columns = [
            name
            for name in (
                "issuer_id",
                "ticker",
                "exchange",
                "accounting_regime",
                "industry_snapshot_date",
            )
            if frame[name].is_null().any()
        ]
        if null_columns:
            raise ContractViolationError(
                "Issuers contain nulls in non-nullable columns: "
                + ", ".join(null_columns)
                + "."
            )


def canonicalize_financial_facts(frame: pl.DataFrame) -> pl.DataFrame:
    """Cast mandatory fact columns to the stable Parquet contract.

    Extra source-lineage columns are retained so Layer 2 provenance remains
    available to downstream quality and analytics stages.
    """

    validate_required_columns(frame)
    expressions = [
        (
            pl.col(name).cast(dtype, strict=True)
            if name in frame.columns
            else pl.lit(None, dtype=dtype)
        ).alias(name)
        for name, dtype in FINANCIAL_FACTS_SCHEMA.items()
    ]
    extras = [name for name in frame.columns if name not in FINANCIAL_FACTS_SCHEMA]
    try:
        canonical = frame.select([*expressions, *[pl.col(name) for name in extras]])
    except pl.exceptions.PolarsError as exc:
        raise ContractViolationError(
            "Financial facts contain values that cannot be cast to the storage schema."
        ) from exc
    validate_required_columns(canonical)
    return canonical


def canonicalize_table(
    frame: pl.DataFrame, schema: Mapping[str, pl.DataType]
) -> pl.DataFrame:
    """Cast a non-fact storage table strictly while retaining provenance columns."""

    validate_required_columns(frame, schema)
    expressions = [
        pl.col(name).cast(dtype, strict=True).alias(name)
        for name, dtype in schema.items()
    ]
    extras = [name for name in frame.columns if name not in schema]
    try:
        canonical = frame.select([*expressions, *[pl.col(name) for name in extras]])
    except pl.exceptions.PolarsError as exc:
        raise ContractViolationError(
            "Storage table contains values that cannot be cast to its schema."
        ) from exc
    validate_required_columns(canonical, schema)
    return canonical
