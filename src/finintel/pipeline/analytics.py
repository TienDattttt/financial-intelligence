"""Run Layer 4A and optional gated Layer 4B against normalized fact history.

Verified Sources:
    docs/layers/LAYER_0_ORCHESTRATION.md and docs/layers/LAYER_4_ANALYTICS_AND_ML.md.
    docs/v2_specs/06_STREAM_A4_LAYER4B_ML_REMEDIATION_SECTOR_ADJ_AND_REGULATORY_TARGET.md.
    Forwards optional scored_taxonomies, feature_set, target_mode and path-confined
    regulatory_events_path into Layer 4B build_ml_artifacts().
"""

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal, NoReturn

import polars as pl

from finintel.analytics.tables import (
    BENCHMARKS_TABLE,
    RATIOS_TABLE,
    build_analytics_tables,
)
from finintel.exceptions import ContractViolationError
from finintel.pipeline.normalize import NORMALIZATION_METADATA, _period_filter
from finintel.pipeline.runner import StepContext, StepOutcome
from finintel.pipeline.state_store import StateStore
from finintel.storage.schemas import ML_SCORES_TABLE

logger = logging.getLogger("finintel.pipeline.analytics")


def _raise_input_error(context: StepContext, message: str) -> NoReturn:
    # These operator-facing messages are local constants. The runner still
    # sanitizes arbitrary exception details from input files and providers.
    logger.error(
        message,
        extra={
            "event": "analytics_input_error",
            "run_id": context.run_id,
            "stage": context.stage,
        },
    )
    raise ContractViolationError(message)


class AnalyticsHandler:
    """Write analytics as an immutable batch for Layer 3 to publish."""

    def __init__(
        self, state: StateStore, config_dir: Path, settings: Mapping[str, object]
    ) -> None:
        self.state = state
        self.config_dir = config_dir
        self.settings = settings

    def __call__(self, context: StepContext) -> StepOutcome:
        curated = self.state.path.parent.parent / "curated"
        issuers_path = self._input_path("issuers_path", curated / "issuers.parquet")
        if issuers_path is None:
            _raise_input_error(
                context,
                "Analytics requires an issuer reference Parquet at curated/issuers.parquet "
                "or the configured analytics.issuers_path.",
            )
        facts_path = self._facts_path(curated, context)
        as_of = self._as_of(context.run_id)
        try:
            facts, issuers = pl.read_parquet(facts_path), pl.read_parquet(issuers_path)
        except (OSError, pl.exceptions.PolarsError) as exc:
            raise ContractViolationError(
                "Analytics requires readable financial facts and issuers Parquet inputs."
            ) from exc

        tables = build_analytics_tables(facts, issuers, as_of=as_of)
        ml_artifacts = self._maybe_run_ml(
            facts,
            issuers,
            facts_path=facts_path,
            issuers_path=issuers_path,
            as_of=as_of,
            benchmarks=tables[BENCHMARKS_TABLE],
        )
        if ml_artifacts is not None:
            tables[ML_SCORES_TABLE] = ml_artifacts.scores_table

        ratios = tables[RATIOS_TABLE]
        if context.tickers or context.request.period:
            # Calculate history and peers first. A replay filter limits only outputs.
            if context.tickers:
                ratios = ratios.filter(pl.col("ticker").is_in(context.tickers))
            if context.request.period:
                ratios = ratios.filter(_period_filter(context.request.period))
        if ratios.is_empty():
            _raise_input_error(
                context,
                "No visible financial periods matched the requested analytics scope.",
            )
        if context.tickers or context.request.period:
            tables[RATIOS_TABLE] = ratios
            keys = ["issuer_id", "statement_scope", "period_type", "period_end_date"]
            selected = ratios.select(keys)
            for name, table in tables.items():
                if name != RATIOS_TABLE:
                    tables[name] = table.join(selected, on=keys, how="semi")

        parent = curated / "analytics"
        parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".analytics-", dir=parent) as temporary:
            staging = Path(temporary) / "tables"
            staging.mkdir()
            for name, table in tables.items():
                table.write_parquet(staging / name)
            (staging / "_analytics_meta.json").write_text(
                json.dumps(
                    {
                        "as_of": as_of.isoformat(),
                        "facts_path": str(facts_path),
                        "issuers_path": str(issuers_path),
                        "source": context.request.source,
                        "ml_enabled": ml_artifacts is not None,
                    }
                ),
                encoding="utf-8",
            )
            if ml_artifacts is not None:
                (staging / "model_evaluation_report.json").write_text(
                    json.dumps(
                        ml_artifacts.evaluation_report,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
                (staging / "_ml_run_manifest.json").write_text(
                    json.dumps(
                        ml_artifacts.run_manifest,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
            staging.rename(parent / context.run_id)
        return StepOutcome()

    def _maybe_run_ml(
        self,
        facts: pl.DataFrame,
        issuers: pl.DataFrame,
        *,
        facts_path: Path,
        issuers_path: Path,
        as_of: date,
        benchmarks: pl.DataFrame,
    ) -> Any:
        raw_ml = self.settings.get("ml")
        if raw_ml is None:
            return None
        if not isinstance(raw_ml, Mapping):
            raise ContractViolationError(
                "Analytics ml configuration must be a mapping."
            )
        enabled = raw_ml.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ContractViolationError("Analytics ml.enabled must be a boolean.")
        if not enabled:
            return None

        require_exact = raw_ml.get("require_exact_filing", True)
        if require_exact is not True:
            raise ContractViolationError(
                "Guardrail G1 prohibits disabling require_exact_filing in pipeline ML runs."
            )
        approved_target = raw_ml.get("approved_target", False)
        if not isinstance(approved_target, bool):
            raise ContractViolationError(
                "Analytics ml.approved_target must be a boolean."
            )
        validation_start = self._parse_iso_date(
            raw_ml.get("validation_start"), "ml.validation_start"
        )
        test_start = self._parse_iso_date(raw_ml.get("test_start"), "ml.test_start")
        scope = raw_ml.get("statement_scope", "CONSOLIDATED")
        taxonomy = raw_ml.get("taxonomy_version", "VAS_TT200_2014")
        if not isinstance(scope, str) or not isinstance(taxonomy, str):
            raise ContractViolationError(
                "Analytics ml.statement_scope and ml.taxonomy_version must be strings."
            )
        raw_scored_taxonomies = raw_ml.get(
            "scored_taxonomies", ("VAS_TT200_2014", "VAS_TT99_2025")
        )
        if (
            isinstance(raw_scored_taxonomies, (str, bytes))
            or not isinstance(raw_scored_taxonomies, Sequence)
            or not raw_scored_taxonomies
            or any(
                not isinstance(item, str) or not item.strip()
                for item in raw_scored_taxonomies
            )
        ):
            raise ContractViolationError(
                "Analytics ml.scored_taxonomies must be a nonempty sequence of taxonomy strings."
            )
        scored_taxonomies = tuple(str(item) for item in raw_scored_taxonomies)

        raw_feature_set = raw_ml.get("feature_set", "sector_adjusted")
        if raw_feature_set not in ("sector_adjusted", "base"):
            raise ContractViolationError(
                "Analytics ml.feature_set must be 'sector_adjusted' or 'base'."
            )
        feature_set: Literal["sector_adjusted", "base"] = (
            "sector_adjusted" if raw_feature_set == "sector_adjusted" else "base"
        )

        target_mode = raw_ml.get("target_mode", "FUNDAMENTAL_DISTRESS")
        if not isinstance(target_mode, str) or target_mode not in (
            "FUNDAMENTAL_DISTRESS",
            "REGULATORY_DISTRESS",
        ):
            raise ContractViolationError(
                "Analytics ml.target_mode must be 'FUNDAMENTAL_DISTRESS' or 'REGULATORY_DISTRESS'."
            )

        raw_reg_path = raw_ml.get("regulatory_events_path")
        regulatory_events: pl.DataFrame | None = None
        if raw_reg_path is not None:
            reg_path = self._confined_ml_input_path(
                raw_reg_path, "ml.regulatory_events_path"
            )
            try:
                regulatory_events = pl.read_parquet(reg_path)
            except (OSError, pl.exceptions.PolarsError) as exc:
                raise ContractViolationError(
                    "Analytics ml.regulatory_events_path must point to a readable Parquet file."
                ) from exc

        from finintel.ml.tables import build_ml_artifacts

        return build_ml_artifacts(
            facts,
            issuers,
            as_of=as_of,
            validation_start=validation_start,
            test_start=test_start,
            approved_target=approved_target,
            require_exact_filing=True,
            statement_scope=scope,
            taxonomy_version=taxonomy,
            scored_taxonomies=scored_taxonomies,
            feature_set=feature_set,
            target_mode=target_mode,
            regulatory_events=regulatory_events,
            peer_benchmarks=benchmarks,
            facts_sha256=self._sha256_file(facts_path),
            issuers_sha256=self._sha256_file(issuers_path),
        )

    def _confined_ml_input_path(self, value: object, field_name: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ContractViolationError(
                f"Analytics {field_name} must be a nonempty relative path."
            )
        candidate = Path(value)
        if candidate.is_absolute():
            raise ContractViolationError(
                f"Analytics {field_name} must be a relative path confined to the workspace."
            )
        resolved = (self.config_dir / candidate).resolve()
        config_root = self.config_dir.resolve()
        data_root = self.state.path.resolve().parent.parent
        allowed_roots = [config_root, data_root]
        if config_root.name == "config":
            allowed_roots.append(config_root.parent)
        if not any(resolved.is_relative_to(root) for root in allowed_roots):
            raise ContractViolationError(
                f"Analytics {field_name} escapes the confined workspace directory."
            )
        if not resolved.is_file():
            raise ContractViolationError(
                f"Analytics {field_name} does not point to a readable input file."
            )
        return resolved

    @staticmethod
    def _parse_iso_date(value: object, field_name: str) -> date:
        if type(value) is date:
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        raise ContractViolationError(
            f"Analytics {field_name} must be an ISO calendar date."
        )

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _input_path(self, key: str, default: Path | None = None) -> Path | None:
        value = self.settings.get(key)
        if value is None:
            return default if default is not None and default.is_file() else None
        if not isinstance(value, str) or not value.strip():
            raise ContractViolationError(f"Analytics {key} must be a nonempty path.")
        path = (self.config_dir / value).resolve()
        if not path.is_file():
            raise ContractViolationError(
                f"Analytics {key} does not point to a readable input file."
            )
        return path

    def _facts_path(self, curated: Path, context: StepContext) -> Path:
        explicit = self._input_path("facts_path")
        if explicit is not None:
            return explicit
        clean_from_qg1 = (
            curated / "quality" / context.run_id / "clean_financial_facts.parquet"
        )
        if clean_from_qg1.is_file():
            return clean_from_qg1
        source = context.request.source
        batches = curated / "financial_facts"
        for run_id, legacy_full in self.state.completed_normalizations(source):
            batch = batches / run_id
            data = batch / "financial_facts.parquet"
            if not data.is_file():
                if run_id == context.run_id:
                    _raise_input_error(
                        context, "The current normalization batch is missing."
                    )
                continue
            metadata = batch / NORMALIZATION_METADATA
            if not metadata.is_file():
                if legacy_full:
                    return data
                continue
            try:
                content = json.loads(metadata.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise ContractViolationError(
                    "Invalid normalization batch metadata."
                ) from error
            if not isinstance(content, dict) or content.get("source") != source:
                raise ContractViolationError(
                    "Normalization batch source differs from the run."
                )
            if content.get("full_history") is True:
                return data
        legacy = curated / "financial_facts.parquet"
        if legacy.is_file():
            return legacy
        _raise_input_error(
            context,
            "Analytics requires complete normalized history. Run normalize without scope filters "
            "or configure analytics.facts_path to a complete history.",
        )

    def _as_of(self, run_id: str) -> date:
        value = self.settings.get("as_of")
        if value is None:
            # Vietnam has a fixed UTC+7 offset. Keep the default consistent
            # between local Windows and CI without depending on a tzdata wheel.
            run = self.state.get_run(run_id)
            started = run.get("started_at") if run is not None else None
            if not isinstance(started, str):
                raise ContractViolationError(
                    "Analytics requires a recorded run start time."
                )
            return (
                datetime.fromisoformat(started)
                .astimezone(timezone(timedelta(hours=7)))
                .date()
            )
        if type(value) is date:
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        raise ContractViolationError("Analytics as_of must be an ISO calendar date.")
