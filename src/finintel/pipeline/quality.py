"""Layer 0 thin handlers connecting QUALITY_GATE_1 and QUALITY_GATE_2 to Layer 6."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from finintel.exceptions import ContractViolationError
from finintel.pipeline.normalize import NORMALIZATION_METADATA
from finintel.pipeline.runner import StepContext, StepOutcome
from finintel.pipeline.state_store import StateStore
from finintel.quality.io import (
    CLEAN_FACTS_TABLE,
    config_from_mapping,
    run_quality_gate_1_batch,
    run_quality_gate_2_batch,
)


def _resolve_optional_path(
    config_dir: Path, settings: Mapping[str, object], key: str, default: Path
) -> Path | None:
    value = settings.get(key)
    if value is None:
        return default if default.is_file() else None
    if not isinstance(value, str) or not value.strip():
        raise ContractViolationError(f"Setting {key} must be a nonempty path.")
    path = (config_dir / value).resolve()
    if not path.is_file():
        raise ContractViolationError(f"Setting {key} does not point to a readable file.")
    return path


def _resolve_facts_path(
    state: StateStore,
    config_dir: Path,
    analytics_settings: Mapping[str, object],
    curated: Path,
    context: StepContext,
) -> Path:
    explicit = _resolve_optional_path(
        config_dir, analytics_settings, "facts_path", curated / "__none__"
    )
    if explicit is not None:
        return explicit

    current_batch = curated / "financial_facts" / context.run_id / "financial_facts.parquet"
    if current_batch.is_file():
        return current_batch

    batches = curated / "financial_facts"
    for run_id, legacy_full in state.completed_normalizations(context.request.source):
        batch = batches / run_id
        data = batch / "financial_facts.parquet"
        if not data.is_file():
            continue
        meta_path = batch / NORMALIZATION_METADATA
        if not meta_path.is_file():
            if legacy_full:
                return data
            continue
        try:
            content = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ContractViolationError("Invalid normalization batch metadata.") from exc
        if isinstance(content, dict) and content.get("full_history") is True:
            return data

    legacy = curated / "financial_facts.parquet"
    if legacy.is_file():
        return legacy
    raise ContractViolationError(
        "Quality gate 1 requires normalized financial_facts.parquet."
    )


class QualityGate1Handler:
    """Thin Layer 0 wrapper invoking Layer 6 QUALITY_GATE_1 on normalized facts."""

    def __init__(
        self,
        state: StateStore,
        config_dir: Path,
        analytics_settings: Mapping[str, object],
        quality_settings: Mapping[str, object] = {},
    ) -> None:
        self.state = state
        self.config_dir = config_dir
        self.analytics_settings = analytics_settings
        self.quality_settings = quality_settings

    def __call__(self, context: StepContext) -> StepOutcome:
        curated = self.state.path.parent.parent / "curated"
        facts_path = _resolve_facts_path(
            self.state, self.config_dir, self.analytics_settings, curated, context
        )
        issuers_path = _resolve_optional_path(
            self.config_dir,
            self.analytics_settings,
            "issuers_path",
            curated / "issuers.parquet",
        )
        config = config_from_mapping(self.quality_settings)
        run_quality_gate_1_batch(
            facts_path=facts_path,
            issuers_path=issuers_path,
            output_dir=curated / "quality" / context.run_id,
            run_id=context.run_id,
            config=config,
        )
        return StepOutcome()


class QualityGate2Handler:
    """Thin Layer 0 wrapper invoking Layer 6 QUALITY_GATE_2 on Layer 4 analytics."""

    def __init__(
        self,
        state: StateStore,
        config_dir: Path,
        analytics_settings: Mapping[str, object],
    ) -> None:
        self.state = state
        self.config_dir = config_dir
        self.analytics_settings = analytics_settings

    def __call__(self, context: StepContext) -> StepOutcome:
        curated = self.state.path.parent.parent / "curated"
        quality_dir = curated / "quality" / context.run_id
        clean_facts = quality_dir / CLEAN_FACTS_TABLE
        if not clean_facts.is_file():
            clean_facts = _resolve_facts_path(
                self.state, self.config_dir, self.analytics_settings, curated, context
            )
        analytics_dir = curated / "analytics" / context.run_id
        if not analytics_dir.is_dir():
            raise ContractViolationError(
                "Quality gate 2 requires curated analytics output for the active run."
            )
        run_quality_gate_2_batch(
            clean_facts_path=clean_facts,
            analytics_dir=analytics_dir,
            quality_dir=quality_dir,
            run_id=context.run_id,
        )
        return StepOutcome()
