"""Build canonical facts from exact, statement-specific taxonomy mappings.

Verified Sources:
    Boncz, P. A., Zukowski, M., & Nes, N. (2005). "MonetDB/X100: Hyper-Pipelining
    Query Execution." CIDR 2005 — vectorized batch execution replacing per-filing loops.
    https://docs.pola.rs/user-guide/lazy/
    https://docs.pola.rs/user-guide/concepts/streaming/
    https://congbao.chinhphu.vn/van-ban/thong-tu-so-200-2014-tt-btc-6697/3529.htm
    https://congbao.chinhphu.vn/tai-ve-van-ban-so-99-2025-tt-btc-46529-59652?format=pdf
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.ingestion.base import FilingMetadata, RevisionReason, StatementType
from finintel.normalization.regime_resolver import TaxonomyVersion, resolve_regime

TAXONOMIES = {
    TaxonomyVersion.TT200: ("corporate_tt200_2014.yaml", "tt200_code"),
    TaxonomyVersion.TT99: ("corporate_tt99_2025.yaml", "tt99_code"),
}
CASH_FLOW_METHODS = {"DIRECT", "INDIRECT"}
SIGN_CONVENTIONS = {
    "SIGNED",
    "SIGNED_EXPENSE",
    "POSITIVE_NORMAL",
    "POSITIVE_REVENUE",
    "POSITIVE_MAGNITUDE",
    "NEGATIVE_OUTFLOW",
}
METRIC_SCHEMA = pl.Schema(
    {
        "metric_id": pl.String(),
        "value_type": pl.String(),
        "unit": pl.String(),
        "sign_convention": pl.String(),
        "comparable_across_taxonomy": pl.Boolean(),
    }
)
CONTEXT_SCHEMA = pl.Schema(
    {
        "filing_id": pl.String(),
        "issuer_id": pl.String(),
        "ticker_at_filing": pl.String(),
        "source_provider": pl.String(),
        "statement_type": pl.String(),
        "statement_scope": pl.String(),
        "period_type": pl.String(),
        "fiscal_year": pl.Int32(),
        "fiscal_quarter": pl.Int32(),
        "fiscal_year_start_date": pl.Date(),
        "period_start_date": pl.Date(),
        "period_end_date": pl.Date(),
        "period_days": pl.Int32(),
        "taxonomy_version": pl.String(),
        "unit_multiplier": pl.Int64(),
        "audit_status": pl.String(),
        "audit_opinion": pl.String(),
        "revision_no": pl.Int64(),
        "revision_reason": pl.String(),
        "published_at": pl.Date(),
        "published_at_basis": pl.String(),
        "effective_available_at": pl.Date(),
        "is_filed_late": pl.Boolean(),
        "source_filing_ids": pl.List(pl.String),
    }
)
FACT_SCHEMA = pl.Schema(
    {
        **CONTEXT_SCHEMA,
        **METRIC_SCHEMA,
        "raw_metric_key": pl.String(),
        "value": pl.Float64(),
        "derivation_method": pl.String(),
    }
)
UNMAPPED_SCHEMA = pl.Schema(
    {
        **CONTEXT_SCHEMA,
        "raw_metric_key": pl.String(),
        "raw_value": pl.String(),
    }
)
ALIAS_SCHEMA = pl.Schema(
    {
        "taxonomy_version": pl.String(),
        "statement_type": pl.String(),
        "fhsc": pl.Boolean(),
        "cash_flow_method": pl.String(),
        "raw_metric_key": pl.String(),
        "_fallback": pl.Boolean(),
        **METRIC_SCHEMA,
    }
)
ALIAS_CONTEXT = ["taxonomy_version", "statement_type", "fhsc", "cash_flow_method"]
MISSING_VALUES = ["", "-", "—", "–", "N/A"]
ALLOWED_DEFINITION_KEYS = {
    "canonical_id",
    "statement_type",
    "value_type",
    "unit",
    "sign_convention",
    "tt200_code",
    "tt99_code",
    "filing_note",
    "comparable_across_taxonomy",
}
ALLOWED_MAPPING_KEYS = {
    "code",
    "labels",
    "fhsc",
    "fhsc_fallback",
    "cash_flow_method",
}
ALLOWED_TAXONOMY_KEYS = {"taxonomy_version", "metrics"}


class _YamlModule(Protocol):
    YAMLError: type[Exception]

    def safe_load(self, stream: str, /) -> object: ...


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ContractViolationError("Expected a taxonomy configuration mapping.")
    return dict(value)


def _yaml(path: Path) -> dict[str, object]:
    yaml = cast(_YamlModule, import_module("yaml"))
    try:
        return _mapping(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ContractViolationError(
            f"Unable to read taxonomy file {path.name}."
        ) from None


def _aliases(value: object) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(x, str) or not x for x in value
    ):
        raise ContractViolationError("Metric aliases must be exact nonempty strings.")
    return list(value)


def _validate_definition(metric_id: str, raw_definition: object) -> dict[str, object]:
    definition = _mapping(raw_definition)
    statement = definition.get("statement_type")
    sign = definition.get("sign_convention")
    unit = definition.get("unit")
    filing_note = definition.get("filing_note", False)
    tt200_code = definition.get("tt200_code")
    tt99_code = definition.get("tt99_code")
    value_type = (
        "STOCK"
        if statement == "BALANCE_SHEET"
        else "NON_ADDITIVE"
        if unit == "VND_PER_SHARE"
        else "FLOW"
    )
    if (
        not set(definition).issubset(ALLOWED_DEFINITION_KEYS)
        or definition.get("canonical_id") != metric_id
        or not isinstance(statement, str)
        or statement not in tuple(StatementType)
        or unit not in ("VND", "VND_PER_SHARE")
        or (unit == "VND_PER_SHARE" and statement != "INCOME_STATEMENT")
        or definition.get("value_type") != value_type
        or not isinstance(sign, str)
        or sign not in SIGN_CONVENTIONS
        or type(definition.get("comparable_across_taxonomy")) is not bool
        or type(filing_note) is not bool
        or (
            tt200_code is not None
            and (not isinstance(tt200_code, str) or not tt200_code)
        )
        or (tt99_code is not None and (not isinstance(tt99_code, str) or not tt99_code))
        or (
            (tt200_code is not None or tt99_code is not None)
            if filing_note
            else (tt200_code is None and tt99_code is None)
        )
    ):
        raise ContractViolationError(
            f"Invalid definition for canonical metric {metric_id}."
        )
    return definition


def _alias_rows(config_dir: Path) -> list[dict[str, object]]:
    definitions_doc = _yaml(config_dir / "metric_definitions.yaml")
    if set(definitions_doc) != {"metrics"}:
        raise ContractViolationError(
            "Unexpected metric_definitions.yaml top-level keys."
        )
    raw_metrics = _mapping(definitions_doc.get("metrics"))
    if not raw_metrics:
        raise ContractViolationError("No canonical metrics configured.")
    metrics = {
        metric_id: _validate_definition(metric_id, raw_def)
        for metric_id, raw_def in raw_metrics.items()
    }
    rows: list[dict[str, object]] = []
    for version, (filename, code_key) in TAXONOMIES.items():
        document = _yaml(config_dir / "taxonomy" / filename)
        if (
            not set(document).issubset(ALLOWED_TAXONOMY_KEYS)
            or document.get("taxonomy_version") != version
        ):
            raise ContractViolationError("Unexpected taxonomy version.")
        mappings = _mapping(document.get("metrics"))
        expected = {
            metric_id
            for metric_id, definition in metrics.items()
            if definition.get(code_key) is not None
            or definition.get("filing_note") is True
        }
        if mappings.keys() != expected:
            raise ContractViolationError(
                "Taxonomy does not match the canonical definitions."
            )
        for metric_id, entry in mappings.items():
            definition, mapping = metrics[metric_id], _mapping(entry)
            statement = cast(str, definition["statement_type"])
            code = mapping.get("code")
            filing_note = definition.get("filing_note") is True
            method = mapping.get("cash_flow_method")
            if (
                not set(mapping).issubset(ALLOWED_MAPPING_KEYS)
                or (
                    (
                        code is not None
                        or definition.get(code_key) is not None
                        or bool(mapping)
                    )
                    if filing_note
                    else not isinstance(code, str)
                    or not code
                    or code != definition.get(code_key)
                )
                or (
                    method is not None
                    and (
                        statement != "CASH_FLOW"
                        or not isinstance(method, str)
                        or method not in CASH_FLOW_METHODS
                    )
                )
            ):
                raise ContractViolationError(
                    f"Invalid definition for canonical metric {metric_id}."
                )
            labels = _aliases(mapping.get("labels", []))
            provider_keys = _aliases(mapping.get("fhsc", []))
            fallback = _aliases(mapping.get("fhsc_fallback", []))
            alias_groups = (
                (
                    False,
                    [code] if isinstance(code, str) else [],
                    [method] if method else ["", *sorted(CASH_FLOW_METHODS)],
                    False,
                ),
                (
                    False,
                    [metric_id, *labels],
                    ["", method] if method else ["", *sorted(CASH_FLOW_METHODS)],
                    False,
                ),
                (
                    True,
                    provider_keys,
                    ["", method] if method else ["", *sorted(CASH_FLOW_METHODS)],
                    False,
                ),
                (
                    True,
                    fallback,
                    ["", method] if method else ["", *sorted(CASH_FLOW_METHODS)],
                    True,
                ),
            )
            rows.extend(
                {
                    "taxonomy_version": version,
                    "statement_type": statement,
                    "fhsc": fhsc,
                    "cash_flow_method": cash_method,
                    "raw_metric_key": alias,
                    "_fallback": is_fallback,
                    "metric_id": metric_id,
                    **{
                        name: definition[name]
                        for name in METRIC_SCHEMA
                        if name != "metric_id"
                    },
                }
                for fhsc, aliases, methods, is_fallback in alias_groups
                for alias in aliases
                for cash_method in methods
            )
    return rows


def _raw_value(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    if type(value) in (int, float) or isinstance(value, Decimal):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        raise ContractViolationError("Unsupported raw metric value.") from None


def _numeric_value() -> pl.Expr:
    raw = pl.col("raw_value").str.strip_chars()
    number = raw.str.replace(r"^\(([0-9]+(?:\.[0-9]+)?)\)$", "-${1}")
    numeric = number.cast(pl.Float64, strict=False)
    return pl.when(raw.is_in(MISSING_VALUES)).then(None).otherwise(numeric)


@dataclass(frozen=True, slots=True)
class FilingMappingInput:
    """Normalized input bundle for vectorized batch mapping of one filing or restatement."""

    filing_id: str
    source_provider: str
    revision_no: int
    unit_multiplier: int | float
    cash_flow_method: str | None
    period: FilingMetadata
    filing: FilingMetadata
    taxonomy: TaxonomyVersion
    metrics: Mapping[str, object]


BATCH_CONTEXT_SCHEMA = pl.Schema(
    {
        "filing_idx": pl.UInt32(),
        "fhsc": pl.Boolean(),
        "cash_flow_method": pl.String(),
        **CONTEXT_SCHEMA,
    }
)
BATCH_RAW_SCHEMA = pl.Schema(
    {
        "filing_idx": pl.UInt32(),
        "raw_metric_key": pl.String(),
        "raw_value": pl.String(),
    }
)


class TaxonomyMapper:
    """Load verified aliases once and apply them with vectorized joins."""

    def __init__(self, config_dir: Path) -> None:
        frame = pl.DataFrame(_alias_rows(config_dir), schema=ALIAS_SCHEMA)
        if frame.select([*ALIAS_CONTEXT, "raw_metric_key"]).is_duplicated().any():
            raise ContractViolationError("Ambiguous exact taxonomy alias.")
        self._aliases_all = frame.rechunk()
        self._aliases = frame.partition_by(
            ALIAS_CONTEXT, as_dict=True, include_key=False
        )

    def map_batch(
        self,
        entries: Sequence[FilingMappingInput],
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        """Map a batch of filings/restatements with a single vectorized alias join."""
        if not entries:
            return (
                pl.DataFrame(schema=FACT_SCHEMA),
                pl.DataFrame(schema=UNMAPPED_SCHEMA),
            )

        ctx_cols: dict[str, list[object]] = {
            name: [] for name in BATCH_CONTEXT_SCHEMA.names()
        }
        raw_filing_idx: list[int] = []
        raw_keys: list[str] = []
        raw_values: list[str | None] = []

        for idx, entry in enumerate(entries):
            if (
                not entry.filing_id
                or not entry.source_provider
                or type(entry.revision_no) is not int
                or not 0 < entry.revision_no < 2**63
                or type(entry.unit_multiplier) is not int
                or not 0 < entry.unit_multiplier < 2**63
                or (
                    entry.cash_flow_method is not None
                    and (
                        entry.cash_flow_method not in CASH_FLOW_METHODS
                        or entry.period.statement_type != StatementType.CASH_FLOW
                    )
                )
            ):
                raise ContractViolationError(
                    "Invalid normalization provenance, unit or cash flow method."
                )
            context = _context(
                entry.period,
                entry.filing,
                entry.taxonomy,
                entry.filing_id,
                entry.source_provider,
                entry.revision_no,
                entry.unit_multiplier,
            )
            ctx_cols["filing_idx"].append(idx)
            ctx_cols["fhsc"].append(entry.source_provider == "fhsc")
            ctx_cols["cash_flow_method"].append(entry.cash_flow_method or "")
            for name in CONTEXT_SCHEMA:
                ctx_cols[name].append(context[name])

            for key, val in entry.metrics.items():
                if not isinstance(key, str) or not key.strip():
                    raise ContractViolationError(
                        "Raw metric keys must be nonempty text."
                    )
                raw_filing_idx.append(idx)
                raw_keys.append(key)
                raw_values.append(_raw_value(val))

        ctx_df = pl.DataFrame(ctx_cols, schema=BATCH_CONTEXT_SCHEMA)
        raw_df = pl.DataFrame(
            {
                "filing_idx": raw_filing_idx,
                "raw_metric_key": raw_keys,
                "raw_value": raw_values,
            },
            schema=BATCH_RAW_SCHEMA,
        )
        if raw_df.is_empty():
            return (
                pl.DataFrame(schema=FACT_SCHEMA),
                pl.DataFrame(schema=UNMAPPED_SCHEMA),
            )

        ctx_keys = ctx_df.select(["filing_idx", *ALIAS_CONTEXT, "unit_multiplier"])
        joined = raw_df.join(
            ctx_keys, on="filing_idx", how="left", maintain_order="left"
        ).join(
            self._aliases_all,
            on=[*ALIAS_CONTEXT, "raw_metric_key"],
            how="left",
            maintain_order="left",
        )

        ctx_for_unmapped = ctx_df.drop(["fhsc", "cash_flow_method"])
        unmapped = (
            joined.filter(pl.col("metric_id").is_null())
            .select(["filing_idx", "raw_metric_key", "raw_value"])
            .join(ctx_for_unmapped, on="filing_idx", how="left", maintain_order="left")
            .select(UNMAPPED_SCHEMA.names())
            .rechunk()
        )

        scale = (
            pl.when(pl.col("unit") == "VND_PER_SHARE")
            .then(1)
            .otherwise(pl.col("unit_multiplier"))
        )
        amounts = joined.filter(pl.col("metric_id").is_not_null()).with_columns(
            (_numeric_value() * scale).alias("value")
        )
        invalid = (
            pl.col("raw_value").is_not_null()
            & ~pl.col("raw_value").str.strip_chars().is_in(MISSING_VALUES)
            & ~pl.col("value").is_finite().fill_null(False)
        )
        if amounts.select(invalid.any()).item():
            raise ContractViolationError(
                "A mapped metric has an invalid or nonfinite amount."
            )
        fallback = pl.col("_fallback")
        priority = pl.when(pl.col("value").is_not_null()).then(2).otherwise(
            0
        ) - fallback.cast(pl.Int8)
        amounts = amounts.filter(
            ~fallback.any().over(["filing_idx", "metric_id"])
            | (priority == priority.max().over(["filing_idx", "metric_id"]))
        )
        if amounts.select(["filing_idx", "metric_id"]).is_duplicated().any():
            raise ContractViolationError(
                "Multiple source keys supplied the same canonical metric."
            )
        value = pl.col("value")
        signed = (
            pl.when(pl.col("sign_convention") == "POSITIVE_MAGNITUDE")
            .then(value.abs())
            .when(pl.col("sign_convention") == "NEGATIVE_OUTFLOW")
            .then(-value.abs())
            .otherwise(value)
        )
        ctx_for_facts = ctx_df.drop(
            [
                "taxonomy_version",
                "statement_type",
                "fhsc",
                "cash_flow_method",
                "unit_multiplier",
            ]
        )
        normalized = (
            amounts.with_columns(
                pl.when(signed == 0.0).then(0.0).otherwise(signed).alias("value"),
                pl.lit("REPORTED_DIRECTLY").alias("derivation_method"),
            )
            .join(ctx_for_facts, on="filing_idx", how="left", maintain_order="left")
            .select(FACT_SCHEMA.names())
            .rechunk()
        )
        return normalized, unmapped

    def map_metrics(
        self,
        metrics: Mapping[str, object],
        metadata: FilingMetadata,
        *,
        filing_id: str,
        source_provider: str,
        revision_no: int = 1,
        unit_multiplier: int = 1,
        cash_flow_method: str | None = None,
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        """Scale VND by the source multiplier and apply configured sign conventions.

        The taxonomy defines metric IDs and sign rules. Unknown keys are returned
        with provenance. Missing numbers remain null and no division is performed.
        """
        return self._map(
            metrics,
            metadata,
            metadata,
            resolve_regime(metadata.fiscal_year_start_date),
            filing_id,
            source_provider,
            revision_no,
            unit_multiplier,
            cash_flow_method,
        )

    def _map(
        self,
        metrics: Mapping[str, object],
        period: FilingMetadata,
        filing: FilingMetadata,
        taxonomy: TaxonomyVersion,
        filing_id: str,
        source_provider: str,
        revision_no: int,
        unit_multiplier: int,
        cash_flow_method: str | None,
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        if (
            not filing_id
            or not source_provider
            or type(revision_no) is not int
            or not 0 < revision_no < 2**63
            or type(unit_multiplier) is not int
            or not 0 < unit_multiplier < 2**63
            or (
                cash_flow_method is not None
                and (
                    cash_flow_method not in CASH_FLOW_METHODS
                    or period.statement_type != StatementType.CASH_FLOW
                )
            )
        ):
            raise ContractViolationError(
                "Invalid normalization provenance, unit or cash flow method."
            )
        if any(not isinstance(key, str) or not key.strip() for key in metrics):
            raise ContractViolationError("Raw metric keys must be nonempty text.")

        data = pl.DataFrame(
            {
                "raw_metric_key": list(metrics),
                "raw_value": [_raw_value(value) for value in metrics.values()],
            },
            schema={"raw_metric_key": pl.String, "raw_value": pl.String},
        )
        aliases = self._aliases.get(
            (
                taxonomy,
                period.statement_type,
                source_provider == "fhsc",
                cash_flow_method or "",
            )
        )
        if aliases is None:
            aliases = pl.DataFrame(
                schema={
                    "raw_metric_key": pl.String,
                    "_fallback": pl.Boolean,
                    **METRIC_SCHEMA,
                }
            )
        joined = data.join(
            aliases, on="raw_metric_key", how="left", maintain_order="left"
        )
        context = _context(
            period,
            filing,
            taxonomy,
            filing_id,
            source_provider,
            revision_no,
            unit_multiplier,
        )
        enriched = joined.with_columns(
            pl.lit(context[name], dtype=dtype).alias(name)
            for name, dtype in CONTEXT_SCHEMA.items()
        )
        unmapped = enriched.filter(pl.col("metric_id").is_null()).select(
            UNMAPPED_SCHEMA.names()
        )
        scale = (
            pl.when(pl.col("unit") == "VND_PER_SHARE")
            .then(1)
            .otherwise(pl.col("unit_multiplier"))
        )
        amounts = enriched.filter(pl.col("metric_id").is_not_null()).with_columns(
            (_numeric_value() * scale).alias("value")
        )
        invalid = (
            pl.col("raw_value").is_not_null()
            & ~pl.col("raw_value").str.strip_chars().is_in(MISSING_VALUES)
            & ~pl.col("value").is_finite().fill_null(False)
        )
        if amounts.select(invalid.any()).item():
            raise ContractViolationError(
                "A mapped metric has an invalid or nonfinite amount."
            )
        fallback = pl.col("_fallback")
        priority = pl.when(pl.col("value").is_not_null()).then(2).otherwise(
            0
        ) - fallback.cast(pl.Int8)
        amounts = amounts.filter(
            ~fallback.any().over("metric_id")
            | (priority == priority.max().over("metric_id"))
        )
        if amounts["metric_id"].is_duplicated().any():
            raise ContractViolationError(
                "Multiple source keys supplied the same canonical metric."
            )
        value = pl.col("value")
        signed = (
            pl.when(pl.col("sign_convention") == "POSITIVE_MAGNITUDE")
            .then(value.abs())
            .when(pl.col("sign_convention") == "NEGATIVE_OUTFLOW")
            .then(-value.abs())
            .otherwise(value)
        )
        normalized = amounts.with_columns(
            pl.when(signed == 0.0).then(0.0).otherwise(signed).alias("value"),
            pl.lit("REPORTED_DIRECTLY").alias("derivation_method"),
        ).select(FACT_SCHEMA.names())
        return normalized, unmapped


def _context(
    period: FilingMetadata,
    filing: FilingMetadata,
    taxonomy: TaxonomyVersion,
    filing_id: str,
    source_provider: str,
    revision_no: int,
    unit_multiplier: int,
) -> dict[str, str | date | int | bool | list[str] | None]:
    if (
        type(period.fiscal_year_start_date) is not date
        or type(period.period_start_date) is not date
        or type(period.period_end_date) is not date
        or not (
            period.fiscal_year_start_date
            <= period.period_start_date
            <= period.period_end_date
        )
        or (period.period_end_date - period.fiscal_year_start_date).days + 1 > 380
    ):
        raise ContractViolationError(
            "Filing period dates exceed the fiscal year boundary."
        )
    start = period.period_start_date
    if period.statement_type == StatementType.BALANCE_SHEET:
        start = period.period_end_date
    return {
        "filing_id": filing_id,
        "issuer_id": period.issuer_id,
        "ticker_at_filing": filing.ticker_at_filing,
        "source_provider": source_provider,
        "statement_type": period.statement_type,
        "statement_scope": period.statement_scope,
        "period_type": period.period_type,
        "fiscal_year": period.fiscal_year,
        "fiscal_quarter": period.fiscal_quarter,
        "fiscal_year_start_date": period.fiscal_year_start_date,
        "period_start_date": start,
        "period_end_date": period.period_end_date,
        "period_days": (period.period_end_date - start).days + 1,
        "taxonomy_version": taxonomy,
        "unit_multiplier": unit_multiplier,
        "audit_status": filing.audit_status,
        "audit_opinion": filing.audit_opinion,
        "revision_no": revision_no,
        "revision_reason": (
            RevisionReason.RESTATED_REGIME_CHANGE
            if period is not filing
            else filing.revision_reason or RevisionReason.ORIGINAL
        ),
        "published_at": filing.published_at,
        "published_at_basis": filing.published_at_basis,
        "effective_available_at": filing.effective_available_at,
        "is_filed_late": filing.is_filed_late,
        "source_filing_ids": [filing_id],
    }
