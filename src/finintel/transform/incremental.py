"""Native Polars DBSP-inspired incremental analytics transform engine (Stream B2 Part 1).

Verified Sources:
    Budiu, M., Chajed, T., McSherry, F., Ryzhyk, L., & Tannen, V. (2023).
    "DBSP: Automatic Incremental View Maintenance for Rich Query Languages."
    PVLDB Vol. 16, No. 7 (VLDB 2023 Best Research Paper Award) — incremental
    change-set separation by dirty_issuers (entity tables) and dirty_slices
    (cross-sectional peer benchmark slices).
    docs/v2_specs/07_STREAM_B2_SQLMESH_AND_DAGSTER_PLATFORM.md (§3.1).
    Runtime API Verification (Python 3.11.9, polars==1.35.2):
    - finintel.quality.gate.evaluate_quality_gate(facts, *, issuers=None, ratios=None,
      config=None, checked_at=None, on_quarantine=None, run_id=None) -> QualityGateResult
    - finintel.analytics.intermediates.prepare_periods(facts, issuers, *, as_of,
      include_ml_inputs=False) -> pl.DataFrame
    - finintel.analytics.ratios.calculate_ratios(periods) -> pl.DataFrame
    - finintel.analytics.dupont.add_dupont(ratios) -> pl.DataFrame
    - finintel.analytics.red_flags.detect_red_flags(ratios) -> pl.DataFrame
    - finintel.analytics.comparisons.benchmark_ratios(ratios, metrics=...) -> pl.DataFrame
    - finintel.ml.tables.build_ml_artifacts(facts, issuers, *, as_of, validation_start,
      test_start, approved_target=False, require_exact_filing=True, ...) -> MLArtifacts
    - pl.DataFrame.write_ndjson(buf) with canonical sorted columns, schema header,
      and -0.0 normalization verified 100% deterministic across reversed orderings
      and shifted slice offsets (0 spurious diffs on 695k-row real snapshot).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal

import polars as pl

from finintel.analytics.comparisons import benchmark_ratios
from finintel.analytics.dupont import add_dupont
from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.ratios import calculate_ratios
from finintel.analytics.red_flags import detect_red_flags
from finintel.exceptions import ContractViolationError
from finintel.quality.gate import evaluate_quality_gate
from finintel.quality.rules import QualityGateConfig
from finintel.storage.schemas import (
    ANALYTICS_SCHEMAS,
    BENCHMARK_KEY,
    BENCHMARKS_TABLE,
    FINANCIAL_FACTS_KEY,
    FINANCIAL_RATIOS_SCHEMA,
    FLAG_KEY,
    ML_SCORES_SCHEMA,
    RATIO_INTERMEDIATES,
    RATIO_KEY,
    RED_FLAGS_TABLE,
)

logger = logging.getLogger("finintel.transform")

TRANSFORM_STATE_FILENAME = "_transform_state.json"
TRANSFORM_STATE_SCHEMA_VERSION = 1

_SLICE_KEY: tuple[str, str, str] = ("statement_scope", "period_type", "period_end_date")
_SLICE_SCHEMA = pl.Schema(
    {
        "statement_scope": pl.String,
        "period_type": pl.String,
        "period_end_date": pl.Date,
    }
)
_ISSUER_METADATA_COLUMNS: tuple[tuple[str, pl.DataType], ...] = (
    ("issuer_id", pl.String()),
    ("accounting_regime", pl.String()),
    ("icb_code_level2", pl.String()),
    ("icb_code_level3", pl.String()),
    ("industry_snapshot_date", pl.Date()),
    ("delisted_date", pl.Date()),
)


@dataclass(frozen=True, slots=True)
class MLTransformConfig:
    """Explicit guardrail configuration for optional Layer 4B ML execution."""

    enabled: bool = False
    validation_start: date | None = None
    test_start: date | None = None
    approved_target: bool = False
    require_exact_filing: bool = True
    statement_scope: str = "CONSOLIDATED"
    taxonomy_version: str = "VAS_TT200_2014"
    scored_taxonomies: tuple[str, ...] = ("VAS_TT200_2014", "VAS_TT99_2025")
    feature_set: Literal["sector_adjusted", "base"] = "sector_adjusted"
    target_mode: Literal["FUNDAMENTAL_DISTRESS", "REGULATORY_DISTRESS"] = (
        "FUNDAMENTAL_DISTRESS"
    )
    regulatory_events: pl.DataFrame | None = None
    min_unique_positive_issuers: int = 3


@dataclass(frozen=True, slots=True)
class TransformDiff:
    """Deterministic change-set summary driving DBSP incremental recomputation."""

    full_rebuild: bool
    benchmarks_full_rebuild: bool
    added_issuers: tuple[str, ...]
    modified_issuers: tuple[str, ...]
    deleted_issuers: tuple[str, ...]
    dirty_issuers: tuple[str, ...]
    dirty_slices: tuple[tuple[str, str, date], ...]
    issuers_metadata_changed: bool
    transform_config_changed: bool
    facts_sha256: str
    issuers_metadata_sha256: str
    transform_config_sha256: str


@dataclass(frozen=True, slots=True)
class TransformBundle:
    """Complete in-memory output of one incremental transform run ready for blocking audits."""

    as_of: date
    run_id: str
    issuers: pl.DataFrame
    clean_facts: pl.DataFrame
    financial_ratios: pl.DataFrame
    industry_benchmarks: pl.DataFrame
    accounting_red_flags: pl.DataFrame
    ml_scores: pl.DataFrame
    quality_summary: pl.DataFrame
    audit_quarantine: pl.DataFrame
    total_issuers: int
    critical_issuers: int
    critical_issuer_ratio: float
    quarantined_filing_ids: tuple[str, ...]
    diff: TransformDiff
    next_transform_state: dict[str, object]
    evaluation_report: dict[str, Any] | None = None
    ml_run_manifest: dict[str, Any] | None = None


def _validate_ml_config(ml_config: MLTransformConfig) -> None:
    if not isinstance(ml_config.enabled, bool):
        raise ContractViolationError("MLTransformConfig.enabled must be a boolean.")
    if not ml_config.enabled:
        return
    if ml_config.require_exact_filing is not True:
        raise ContractViolationError(
            "Guardrail G1 prohibits disabling require_exact_filing in transform ML runs."
        )
    if ml_config.approved_target is not True:
        raise ContractViolationError(
            "Guardrail G2 requires approved_target=True before supervised ML runs."
        )
    if type(ml_config.validation_start) is not date:
        raise ContractViolationError(
            "Enabled ML transform requires validation_start as a datetime.date."
        )
    if type(ml_config.test_start) is not date:
        raise ContractViolationError(
            "Enabled ML transform requires test_start as a datetime.date."
        )
    if not ml_config.scored_taxonomies or any(
        not isinstance(item, str) or not item.strip()
        for item in ml_config.scored_taxonomies
    ):
        raise ContractViolationError(
            "MLTransformConfig.scored_taxonomies must be a nonempty tuple of strings."
        )
    if ml_config.feature_set not in ("sector_adjusted", "base"):
        raise ContractViolationError(
            "MLTransformConfig.feature_set must be 'sector_adjusted' or 'base'."
        )
    if ml_config.target_mode not in ("FUNDAMENTAL_DISTRESS", "REGULATORY_DISTRESS"):
        raise ContractViolationError(
            "MLTransformConfig.target_mode must be 'FUNDAMENTAL_DISTRESS' or 'REGULATORY_DISTRESS'."
        )
    if (
        isinstance(ml_config.min_unique_positive_issuers, bool)
        or ml_config.min_unique_positive_issuers < 1
    ):
        raise ContractViolationError(
            "MLTransformConfig.min_unique_positive_issuers must be a positive integer."
        )


def _canonicalize_for_hash(frame: pl.DataFrame) -> pl.DataFrame:
    """Sort columns and normalize -0.0 floats so NDJSON serialization is 100% deterministic."""
    cols = sorted(frame.columns)
    exprs: list[pl.Expr] = []
    for col in cols:
        dtype = frame.schema[col]
        if dtype.is_float():
            exprs.append(
                pl.when(pl.col(col) == 0.0)
                .then(pl.lit(0.0, dtype=dtype))
                .otherwise(pl.col(col))
                .alias(col)
            )
        else:
            exprs.append(pl.col(col))
    return frame.select(exprs)


def _schema_header_bytes(frame: pl.DataFrame) -> bytes:
    return (
        "|".join(f"{name}:{frame.schema[name]}" for name in frame.columns) + "\n"
    ).encode("utf-8")


def _sha256_frame(frame: pl.DataFrame, *, schema_header: bytes | None = None) -> str:
    buf = io.BytesIO()
    frame.write_ndjson(buf)
    digest = hashlib.sha256(
        schema_header if schema_header is not None else _schema_header_bytes(frame)
    )
    digest.update(buf.getvalue())
    return digest.hexdigest()


def _hash_facts_per_issuer(clean_facts: pl.DataFrame) -> tuple[dict[str, str], str]:
    """Compute deterministic per-issuer SHA-256 digests over clean_facts (fully vectorized)."""
    if clean_facts.is_empty():
        empty_digest = hashlib.sha256(b"{}").hexdigest()
        return {}, empty_digest

    normalized = _canonicalize_for_hash(clean_facts)
    sort_cols = [col for col in FINANCIAL_FACTS_KEY if col in normalized.columns]
    if not sort_cols:
        sort_cols = [
            col
            for col in sorted(normalized.columns)
            if not isinstance(normalized.schema[col], (pl.List, pl.Struct))
        ]
    sorted_df = normalized.sort(sort_cols, nulls_last=True) if sort_cols else normalized
    schema_header = _schema_header_bytes(sorted_df)

    partitions = sorted_df.partition_by("issuer_id", as_dict=True, maintain_order=True)
    issuer_hashes: dict[str, str] = {}
    for key, sub_df in partitions.items():
        issuer_id = str(key[0])
        issuer_hashes[issuer_id] = _sha256_frame(sub_df, schema_header=schema_header)

    ordered_hashes = dict(sorted(issuer_hashes.items()))
    facts_digest = hashlib.sha256(
        json.dumps(ordered_hashes, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return ordered_hashes, facts_digest


def _hash_issuers_metadata(issuers: pl.DataFrame) -> str:
    """Hash ICB, regime, snapshot date, and delisting metadata affecting analytics/benchmarks."""
    exprs = [
        (
            pl.col(name).cast(dtype, strict=False).alias(name)
            if name in issuers.columns
            else pl.lit(None, dtype=dtype).alias(name)
        )
        for name, dtype in _ISSUER_METADATA_COLUMNS
    ]
    projected = issuers.select(exprs).sort("issuer_id", nulls_last=True)
    return _sha256_frame(projected)


def _hash_transform_config(
    *,
    as_of: date,
    ml_config: MLTransformConfig,
    quality_config: QualityGateConfig,
) -> str:
    reg_events_sha: str | None = None
    if ml_config.enabled and ml_config.regulatory_events is not None:
        reg_df = _canonicalize_for_hash(ml_config.regulatory_events)
        reg_sort_cols = [
            col
            for col in reg_df.columns
            if not isinstance(reg_df.schema[col], (pl.List, pl.Struct))
        ]
        if not reg_df.is_empty() and reg_sort_cols:
            reg_df = reg_df.sort(reg_sort_cols, nulls_last=True)
        reg_events_sha = _sha256_frame(reg_df)

    payload: dict[str, object] = {
        "schema_version": TRANSFORM_STATE_SCHEMA_VERSION,
        "as_of": as_of.isoformat(),
        "quality_config": {
            "bs_identity_rel_tol": quality_config.bs_identity_rel_tol,
            "subtotal_rel_tol": quality_config.subtotal_rel_tol,
            "unit_scale_upper_factor": quality_config.unit_scale_upper_factor,
            "unit_scale_lower_factor": quality_config.unit_scale_lower_factor,
            "cash_flow_rel_tol": quality_config.cash_flow_rel_tol,
            "max_critical_issuer_ratio": quality_config.max_critical_issuer_ratio,
            "required_metrics": list(quality_config.required_metrics),
        },
        "ml_config": {
            "enabled": ml_config.enabled,
            "validation_start": (
                ml_config.validation_start.isoformat()
                if ml_config.enabled and ml_config.validation_start is not None
                else None
            ),
            "test_start": (
                ml_config.test_start.isoformat()
                if ml_config.enabled and ml_config.test_start is not None
                else None
            ),
            "approved_target": ml_config.approved_target
            if ml_config.enabled
            else False,
            "require_exact_filing": ml_config.require_exact_filing,
            "statement_scope": ml_config.statement_scope if ml_config.enabled else None,
            "taxonomy_version": ml_config.taxonomy_version
            if ml_config.enabled
            else None,
            "scored_taxonomies": (
                list(ml_config.scored_taxonomies) if ml_config.enabled else None
            ),
            "feature_set": ml_config.feature_set if ml_config.enabled else None,
            "target_mode": ml_config.target_mode if ml_config.enabled else None,
            "min_unique_positive_issuers": (
                ml_config.min_unique_positive_issuers if ml_config.enabled else None
            ),
            "regulatory_events_sha256": reg_events_sha,
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _schema_matches(frame: pl.DataFrame, expected: pl.Schema) -> bool:
    if set(expected.names()) - set(frame.columns):
        return False
    return all(frame.schema.get(name) == dtype for name, dtype in expected.items())


def _is_valid_previous_state(state: Mapping[str, object] | None) -> bool:
    if state is None:
        return False
    if state.get("schema_version") != TRANSFORM_STATE_SCHEMA_VERSION:
        return False
    issuer_hashes = state.get("issuer_facts_sha256")
    if not isinstance(issuer_hashes, Mapping):
        return False
    if any(
        not isinstance(k, str) or not isinstance(v, str)
        for k, v in issuer_hashes.items()
    ):
        return False
    return isinstance(state.get("issuers_metadata_sha256"), str) and isinstance(
        state.get("transform_config_sha256"), str
    )


def load_transform_state(state_path: Path) -> dict[str, object] | None:
    """Load persisted ``_transform_state.json`` safely; return None if absent or incompatible."""
    if not state_path.is_file():
        return None
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning(
            "Unable to parse transform state file; falling back to full rebuild.",
            extra={"event": "transform_state_unreadable"},
        )
        return None
    if not isinstance(raw, dict) or not _is_valid_previous_state(raw):
        logger.warning(
            "Transform state schema is incompatible; falling back to full rebuild.",
            extra={"event": "transform_state_incompatible"},
        )
        return None
    return raw


def write_transform_state(
    state_path: Path,
    state: Mapping[str, object],
    *,
    snapshot_id: str | None = None,
) -> Path:
    """Atomically write ``_transform_state.json`` after snapshot publication succeeds."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = dict(state)
    payload["schema_version"] = TRANSFORM_STATE_SCHEMA_VERSION
    if snapshot_id is not None:
        payload["snapshot_id"] = snapshot_id
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=state_path.parent,
        prefix=".transform_state_",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(serialized)
        tmp_path = Path(tmp.name)
    try:
        os.replace(tmp_path, state_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    return state_path


def _attach_pit_icb(
    ratios_df: pl.DataFrame, issuers: pl.DataFrame, *, as_of: date
) -> pl.DataFrame:
    """Attach PIT-visible ICB codes matching ``prepare_periods`` semantics."""
    classification_visible = (
        pl.col("industry_snapshot_date").cast(pl.Date) <= as_of
        if "industry_snapshot_date" in issuers.columns
        else pl.lit(True)
    )
    icb_lookup = issuers.select(
        pl.col("issuer_id").cast(pl.String),
        *(
            pl.when(classification_visible)
            .then(pl.col(name).cast(pl.String))
            .alias(name)
            if name in issuers.columns
            else pl.lit(None, dtype=pl.String).alias(name)
            for name in ("icb_code_level2", "icb_code_level3")
        ),
    )
    return ratios_df.join(icb_lookup, on="issuer_id", how="left", validate="m:1")


def _compute_ratios_and_red_flags(
    facts_subset: pl.DataFrame,
    issuers: pl.DataFrame,
    *,
    as_of: date,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Compute Layer 4A ratios and red flags keeping intermediate columns in RAM for RF_01..RF_09."""
    rf_schema = ANALYTICS_SCHEMAS[RED_FLAGS_TABLE]
    if facts_subset.is_empty():
        return pl.DataFrame(schema=FINANCIAL_RATIOS_SCHEMA), pl.DataFrame(
            schema=rf_schema
        )

    periods = prepare_periods(facts_subset, issuers, as_of=as_of)
    if periods.is_empty():
        return pl.DataFrame(schema=FINANCIAL_RATIOS_SCHEMA), pl.DataFrame(
            schema=rf_schema
        )

    ratios_rich = add_dupont(calculate_ratios(periods))
    red_flags = (
        detect_red_flags(ratios_rich)
        .select(
            *(
                pl.col(name).cast(dtype, strict=True).alias(name)
                for name, dtype in rf_schema.items()
            )
        )
        .sort(list(FLAG_KEY))
    )

    ratios_pub = (
        ratios_rich.with_columns(
            pl.when(pl.col(name).is_finite()).then(pl.col(name)).alias(name)
            for name in RATIO_INTERMEDIATES
        )
        .select(
            *(
                pl.col(name).cast(dtype, strict=True).alias(name)
                for name, dtype in FINANCIAL_RATIOS_SCHEMA.items()
            )
        )
        .sort(list(RATIO_KEY))
    )
    return ratios_pub, red_flags


def _compute_benchmarks(
    ratios_df: pl.DataFrame, issuers: pl.DataFrame, *, as_of: date
) -> pl.DataFrame:
    bm_schema = ANALYTICS_SCHEMAS[BENCHMARKS_TABLE]
    if ratios_df.is_empty():
        return pl.DataFrame(schema=bm_schema)
    ratios_with_icb = _attach_pit_icb(ratios_df, issuers, as_of=as_of)
    return (
        benchmark_ratios(ratios_with_icb)
        .select(
            *(
                pl.col(name).cast(dtype, strict=True).alias(name)
                for name, dtype in bm_schema.items()
            )
        )
        .sort(list(BENCHMARK_KEY))
    )


def _find_identity_changed_issuers(
    previous_ratios: pl.DataFrame, issuers: pl.DataFrame
) -> list[str]:
    """Identify issuers whose ticker or accounting_regime changed relative to previous_ratios."""
    if previous_ratios.is_empty():
        return []
    prev_id = previous_ratios.select(
        pl.col("issuer_id").cast(pl.String),
        pl.col("ticker").cast(pl.String).alias("_prev_ticker"),
        pl.col("accounting_regime").cast(pl.String).alias("_prev_regime"),
    ).unique(subset=["issuer_id"])
    curr_id = issuers.select(
        pl.col("issuer_id").cast(pl.String),
        pl.col("ticker").cast(pl.String).alias("_curr_ticker"),
        pl.col("accounting_regime").cast(pl.String).alias("_curr_regime"),
    ).unique(subset=["issuer_id"])
    changed = prev_id.join(curr_id, on="issuer_id", how="left").filter(
        (pl.col("_prev_ticker") != pl.col("_curr_ticker")).fill_null(True)
        | (pl.col("_prev_regime") != pl.col("_curr_regime")).fill_null(True)
    )
    return [str(v) for v in changed["issuer_id"].to_list()]


def run_incremental_analytics_transform(
    facts: pl.DataFrame,
    issuers: pl.DataFrame,
    *,
    as_of: date,
    run_id: str,
    previous_ratios: pl.DataFrame | None = None,
    previous_benchmarks: pl.DataFrame | None = None,
    previous_red_flags: pl.DataFrame | None = None,
    previous_state: Mapping[str, object] | None = None,
    previous_snapshot_id: str | None = None,
    state_path: Path | None = None,
    ml_config: MLTransformConfig | None = None,
    quality_config: QualityGateConfig | None = None,
    persist_state: bool = False,
) -> TransformBundle:
    """Run QG1, DBSP incremental Layer 4A ratios/red_flags/benchmarks, and gated Layer 4B ML.

    Never writes ``_transform_state.json`` unless ``persist_state=True`` is explicitly set;
    orchestration callers should invoke ``write_transform_state(..., snapshot_id=...)``
    only AFTER ``run_blocking_publish_audits()`` and atomic snapshot publication succeed.
    """
    if type(as_of) is not date:
        raise ContractViolationError("as_of must be a datetime.date instance.")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ContractViolationError("run_id must be a nonempty string.")
    if persist_state and state_path is None:
        raise ContractViolationError("persist_state=True requires state_path.")

    effective_ml = ml_config or MLTransformConfig()
    _validate_ml_config(effective_ml)
    effective_qg = quality_config or QualityGateConfig()

    # Step 1: QUALITY_GATE_1 (raises CriticalDataQualityError if critical_ratio > threshold)
    qg1 = evaluate_quality_gate(
        facts,
        issuers=issuers,
        config=effective_qg,
        run_id=run_id,
    )

    # Step 2: Deterministic hashes & DBSP TransformDiff
    issuer_facts_sha256, facts_sha256 = _hash_facts_per_issuer(qg1.clean_facts)
    issuers_metadata_sha256 = _hash_issuers_metadata(issuers)
    transform_config_sha256 = _hash_transform_config(
        as_of=as_of,
        ml_config=effective_ml,
        quality_config=effective_qg,
    )

    loaded_prev_state: Mapping[str, object] | None
    if previous_state is not None:
        loaded_prev_state = (
            previous_state if _is_valid_previous_state(previous_state) else None
        )
    elif state_path is not None:
        loaded_prev_state = load_transform_state(state_path)
    else:
        loaded_prev_state = None

    snapshot_mismatch = (
        loaded_prev_state is not None
        and previous_snapshot_id is not None
        and loaded_prev_state.get("snapshot_id") != previous_snapshot_id
    )
    transform_config_changed = (
        loaded_prev_state is None
        or loaded_prev_state.get("transform_config_sha256") != transform_config_sha256
    )
    issuers_metadata_changed = (
        loaded_prev_state is None
        or loaded_prev_state.get("issuers_metadata_sha256") != issuers_metadata_sha256
    )

    ratios_usable = previous_ratios is not None and _schema_matches(
        previous_ratios, FINANCIAL_RATIOS_SCHEMA
    )
    red_flags_usable = previous_red_flags is not None and _schema_matches(
        previous_red_flags, ANALYTICS_SCHEMAS[RED_FLAGS_TABLE]
    )
    benchmarks_usable = previous_benchmarks is not None and _schema_matches(
        previous_benchmarks, ANALYTICS_SCHEMAS[BENCHMARKS_TABLE]
    )

    full_rebuild = (
        loaded_prev_state is None
        or snapshot_mismatch
        or transform_config_changed
        or not ratios_usable
        or not red_flags_usable
    )

    prev_issuer_hashes: dict[str, str] = {}
    if loaded_prev_state is not None:
        raw_hashes = loaded_prev_state.get("issuer_facts_sha256")
        if isinstance(raw_hashes, Mapping):
            prev_issuer_hashes = {str(k): str(v) for k, v in raw_hashes.items()}

    curr_ids = set(issuer_facts_sha256)
    prev_ids = set(prev_issuer_hashes)
    added_issuers = tuple(sorted(curr_ids - prev_ids))
    deleted_issuers = tuple(sorted(prev_ids - curr_ids))
    modified_set = {
        iid
        for iid in (curr_ids & prev_ids)
        if issuer_facts_sha256[iid] != prev_issuer_hashes[iid]
    }
    if not full_rebuild and previous_ratios is not None:
        for iid in _find_identity_changed_issuers(previous_ratios, issuers):
            if iid in curr_ids:
                modified_set.add(iid)
    modified_issuers = tuple(sorted(modified_set))

    if full_rebuild:
        dirty_issuers = tuple(sorted(curr_ids | prev_ids))
    else:
        dirty_issuers = tuple(
            sorted(set(added_issuers) | set(modified_issuers) | set(deleted_issuers))
        )

    # Step 3: Layer 4A Ratios & Red Flags (preserving intermediates for RF_01..RF_09,
    # merging via delete-by-issuer_id + concat to prevent ghost red flags and duplicate ANNUAL rows)
    rf_schema = ANALYTICS_SCHEMAS[RED_FLAGS_TABLE]
    if full_rebuild:
        financial_ratios, accounting_red_flags = _compute_ratios_and_red_flags(
            qg1.clean_facts, issuers, as_of=as_of
        )
        new_ratios_pub = financial_ratios
    else:
        assert previous_ratios is not None
        assert previous_red_flags is not None
        if not dirty_issuers:
            financial_ratios = previous_ratios.select(
                FINANCIAL_RATIOS_SCHEMA.names()
            ).sort(list(RATIO_KEY))
            accounting_red_flags = previous_red_flags.select(rf_schema.names()).sort(
                list(FLAG_KEY)
            )
            new_ratios_pub = pl.DataFrame(schema=FINANCIAL_RATIOS_SCHEMA)
        else:
            dirty_list = list(dirty_issuers)
            dirty_facts = qg1.clean_facts.filter(pl.col("issuer_id").is_in(dirty_list))
            new_ratios_pub, new_red_flags = _compute_ratios_and_red_flags(
                dirty_facts, issuers, as_of=as_of
            )
            kept_ratios = previous_ratios.filter(
                ~pl.col("issuer_id").is_in(dirty_list)
            ).select(FINANCIAL_RATIOS_SCHEMA.names())
            kept_red_flags = previous_red_flags.filter(
                ~pl.col("issuer_id").is_in(dirty_list)
            ).select(rf_schema.names())
            financial_ratios = pl.concat(
                [kept_ratios, new_ratios_pub], how="vertical"
            ).sort(list(RATIO_KEY))
            accounting_red_flags = pl.concat(
                [kept_red_flags, new_red_flags], how="vertical"
            ).sort(list(FLAG_KEY))

    # Step 4: DBSP Incremental Industry Benchmarks by dirty_slices = (statement_scope, period_type, period_end_date)
    bm_schema = ANALYTICS_SCHEMAS[BENCHMARKS_TABLE]
    if full_rebuild:
        dirty_slices_df = (
            financial_ratios.select(list(_SLICE_KEY)).unique().sort(list(_SLICE_KEY))
            if not financial_ratios.is_empty()
            else pl.DataFrame(schema=_SLICE_SCHEMA)
        )
    elif dirty_issuers:
        assert previous_ratios is not None
        dirty_list = list(dirty_issuers)
        prev_slices = previous_ratios.filter(
            pl.col("issuer_id").is_in(dirty_list)
        ).select(list(_SLICE_KEY))
        new_slices = new_ratios_pub.select(list(_SLICE_KEY))
        dirty_slices_df = (
            pl.concat([prev_slices, new_slices], how="vertical")
            .unique()
            .sort(list(_SLICE_KEY))
        )
    else:
        dirty_slices_df = pl.DataFrame(schema=_SLICE_SCHEMA)

    dirty_slices: tuple[tuple[str, str, date], ...] = tuple(
        zip(
            dirty_slices_df["statement_scope"].to_list(),
            dirty_slices_df["period_type"].to_list(),
            dirty_slices_df["period_end_date"].to_list(),
            strict=True,
        )
    )

    benchmarks_full_rebuild = (
        full_rebuild or issuers_metadata_changed or not benchmarks_usable
    )
    if benchmarks_full_rebuild:
        industry_benchmarks = _compute_benchmarks(
            financial_ratios, issuers, as_of=as_of
        )
    else:
        assert previous_benchmarks is not None
        if dirty_slices_df.is_empty():
            industry_benchmarks = previous_benchmarks.select(bm_schema.names()).sort(
                list(BENCHMARK_KEY)
            )
        else:
            affected_ratios = financial_ratios.join(
                dirty_slices_df, on=list(_SLICE_KEY), how="semi"
            )
            new_benchmarks = _compute_benchmarks(affected_ratios, issuers, as_of=as_of)
            kept_benchmarks = previous_benchmarks.join(
                dirty_slices_df, on=list(_SLICE_KEY), how="anti"
            ).select(bm_schema.names())
            industry_benchmarks = pl.concat(
                [kept_benchmarks, new_benchmarks], how="vertical"
            ).sort(list(BENCHMARK_KEY))

    diff = TransformDiff(
        full_rebuild=full_rebuild,
        benchmarks_full_rebuild=benchmarks_full_rebuild,
        added_issuers=added_issuers,
        modified_issuers=modified_issuers,
        deleted_issuers=deleted_issuers,
        dirty_issuers=dirty_issuers,
        dirty_slices=dirty_slices,
        issuers_metadata_changed=issuers_metadata_changed,
        transform_config_changed=transform_config_changed,
        facts_sha256=facts_sha256,
        issuers_metadata_sha256=issuers_metadata_sha256,
        transform_config_sha256=transform_config_sha256,
    )

    # Step 5: Gated Layer 4B ML
    evaluation_report: dict[str, Any] | None = None
    ml_run_manifest: dict[str, Any] | None = None
    if effective_ml.enabled:
        assert effective_ml.validation_start is not None
        assert effective_ml.test_start is not None
        from finintel.ml.tables import build_ml_artifacts

        ml_artifacts = build_ml_artifacts(
            qg1.clean_facts,
            issuers,
            as_of=as_of,
            validation_start=effective_ml.validation_start,
            test_start=effective_ml.test_start,
            approved_target=True,
            require_exact_filing=True,
            statement_scope=effective_ml.statement_scope,
            taxonomy_version=effective_ml.taxonomy_version,
            scored_taxonomies=effective_ml.scored_taxonomies,
            feature_set=effective_ml.feature_set,
            target_mode=effective_ml.target_mode,
            regulatory_events=effective_ml.regulatory_events,
            min_unique_positive_issuers=effective_ml.min_unique_positive_issuers,
            peer_benchmarks=industry_benchmarks,
            facts_sha256=diff.facts_sha256,
            issuers_sha256=diff.issuers_metadata_sha256,
        )
        ml_scores = ml_artifacts.scores_table
        evaluation_report = ml_artifacts.evaluation_report
        ml_run_manifest = ml_artifacts.run_manifest
    else:
        ml_scores = pl.DataFrame(schema=ML_SCORES_SCHEMA)

    next_transform_state: dict[str, object] = {
        "schema_version": TRANSFORM_STATE_SCHEMA_VERSION,
        "snapshot_id": None,
        "run_id": run_id,
        "as_of": as_of.isoformat(),
        "issuers_metadata_sha256": issuers_metadata_sha256,
        "transform_config_sha256": transform_config_sha256,
        "facts_sha256": facts_sha256,
        "issuer_facts_sha256": issuer_facts_sha256,
    }
    if persist_state and state_path is not None:
        write_transform_state(state_path, next_transform_state)

    logger.info(
        "Incremental analytics transform completed",
        extra={
            "event": "incremental_transform_completed",
            "run_id": run_id,
            "stage": "TRANSFORM",
            "details": {
                "full_rebuild": diff.full_rebuild,
                "benchmarks_full_rebuild": diff.benchmarks_full_rebuild,
                "dirty_issuers_count": len(diff.dirty_issuers),
                "dirty_slices_count": len(diff.dirty_slices),
                "ratios_rows": financial_ratios.height,
                "benchmarks_rows": industry_benchmarks.height,
                "red_flags_rows": accounting_red_flags.height,
                "ml_scores_rows": ml_scores.height,
            },
        },
    )

    return TransformBundle(
        as_of=as_of,
        run_id=run_id,
        issuers=issuers,
        clean_facts=qg1.clean_facts,
        financial_ratios=financial_ratios,
        industry_benchmarks=industry_benchmarks,
        accounting_red_flags=accounting_red_flags,
        ml_scores=ml_scores,
        quality_summary=qg1.quality_summary,
        audit_quarantine=qg1.audit_quarantine,
        total_issuers=qg1.total_issuers,
        critical_issuers=qg1.critical_issuers,
        critical_issuer_ratio=qg1.critical_issuer_ratio,
        quarantined_filing_ids=qg1.quarantined_filing_ids,
        diff=diff,
        next_transform_state=next_transform_state,
        evaluation_report=evaluation_report,
        ml_run_manifest=ml_run_manifest,
    )
