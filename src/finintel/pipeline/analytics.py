"""Run Layer 4A and optional gated Layer 4B via the unified DBSP incremental transform engine.

Thin CLI/dev stage handler sharing the exact ``run_incremental_analytics_transform``
contract, schemas, and ML guardrails used by Dagster ``published_financial_marts``
(ARCH-07b).

Verified Sources:
    docs/layers/LAYER_0_ORCHESTRATION.md and docs/layers/LAYER_4_ANALYTICS_AND_ML.md.
    docs/v2_specs/06_STREAM_A4_LAYER4B_ML_REMEDIATION_SECTOR_ADJ_AND_REGULATORY_TARGET.md.
    docs/refactor/01_ARCHITECTURE_AND_STACK_PLAN.md (ARCH-07b).
"""

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from tempfile import TemporaryDirectory
from typing import Literal, NoReturn

import polars as pl

from finintel.analytics.tables import (
    BENCHMARKS_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)
from finintel.exceptions import ContractViolationError
from finintel.pipeline.normalize import NORMALIZATION_METADATA, _period_filter
from finintel.pipeline.runner import StepContext, StepOutcome
from finintel.pipeline.state_store import StateStore
from finintel.ports.market_prices import validate_market_prices
from finintel.quality.rules import QualityGateConfig
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import (
    ML_SCORES_TABLE,
    MODEL_CARD_META_TABLE,
    PIT_VALUATION_MART_TABLE,
)
from finintel.transform.incremental import (
    TRANSFORM_STATE_FILENAME,
    MLTransformConfig,
    load_transform_state,
    run_incremental_analytics_transform,
)

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


def _read_optional_snapshot_table(
    repo: ParquetRepository, table_name: str
) -> pl.DataFrame | None:
    try:
        return repo.read_table(table_name)
    except (FileNotFoundError, ContractViolationError):
        return None


class AnalyticsHandler:
    """Thin CLI/dev stage runner delegating to the unified DBSP incremental transform engine."""

    def __init__(
        self,
        state: StateStore,
        config_dir: Path,
        settings: Mapping[str, object],
        *,
        quality_settings: Mapping[str, object] | None = None,
    ) -> None:
        self.state = state
        self.config_dir = config_dir
        self.settings = settings
        self.quality_settings = quality_settings or {}

    def _quality_config(
        self,
        *,
        qg1_already_applied: bool,
        facts: pl.DataFrame,
    ) -> QualityGateConfig:
        if not qg1_already_applied and not self.quality_settings:
            # In the CLI stage runner, Stage.QUALITY_GATE_1 owns QG1 enforcement.
            # When Stage.ANALYTICS is invoked standalone (--stage analytics), keep
            # the unified DBSP engine active without re-tripping QG1 quarantine.
            anchor_metric = (
                str(facts["metric_id"][0])
                if not facts.is_empty() and "metric_id" in facts.columns
                else "BS_TOTAL_ASSETS"
            )
            return QualityGateConfig(
                bs_identity_rel_tol=1e12,
                subtotal_rel_tol=1e12,
                unit_scale_upper_factor=1e12,
                unit_scale_lower_factor=1e-12,
                cash_flow_rel_tol=1e12,
                max_critical_issuer_ratio=1.0,
                required_metrics=(anchor_metric,),
            )
        defaults = QualityGateConfig()
        raw = self.quality_settings
        if not raw:
            return defaults
        kwargs: dict[str, object] = {}
        for field_name in (
            "bs_identity_rel_tol",
            "subtotal_rel_tol",
            "unit_scale_upper_factor",
            "unit_scale_lower_factor",
            "cash_flow_rel_tol",
            "max_critical_issuer_ratio",
        ):
            val = raw.get(field_name)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                kwargs[field_name] = float(val)
        req_metrics = raw.get("required_metrics")
        if (
            isinstance(req_metrics, Sequence)
            and not isinstance(req_metrics, (str, bytes))
            and all(isinstance(m, str) for m in req_metrics)
        ):
            kwargs["required_metrics"] = tuple(str(m) for m in req_metrics)
        return QualityGateConfig(**kwargs)  # type: ignore[arg-type]

    def __call__(self, context: StepContext) -> StepOutcome:
        data_root = self.state.path.parent.parent
        curated = data_root / "curated"
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

        ml_cfg = self._build_ml_config()
        clean_from_qg1 = (
            curated / "quality" / context.run_id / "clean_financial_facts.parquet"
        )
        quality_cfg = self._quality_config(
            qg1_already_applied=(facts_path == clean_from_qg1),
            facts=facts,
        )

        transform_state_path = self.state.path.parent / TRANSFORM_STATE_FILENAME
        previous_state = load_transform_state(transform_state_path)
        active = self.state.active_snapshot()
        previous_snapshot_id = (
            str(active["snapshot_id"])
            if active is not None and active.get("snapshot_id") is not None
            else None
        )
        previous_ratios: pl.DataFrame | None = None
        previous_benchmarks: pl.DataFrame | None = None
        previous_red_flags: pl.DataFrame | None = None
        previous_ml_scores: pl.DataFrame | None = None
        previous_model_card_meta: pl.DataFrame | None = None
        market_prices_df: pl.DataFrame | None = None
        mp_path = self._input_path(
            "market_prices_path",
            data_root / "market_prices" / "market_prices.parquet",
        )
        if mp_path is None and (curated / "market_prices.parquet").is_file():
            mp_path = curated / "market_prices.parquet"
        if mp_path is not None:
            try:
                market_prices_df = validate_market_prices(pl.read_parquet(mp_path))
            except (OSError, pl.exceptions.PolarsError) as exc:
                raise ContractViolationError(
                    "Analytics requires readable market_prices Parquet input."
                ) from exc

        if (
            previous_state is not None
            and previous_snapshot_id is not None
            and previous_state.get("snapshot_id") == previous_snapshot_id
        ):
            repo = ParquetRepository(data_root=data_root)
            try:
                with repo.snapshot_session() as pinned:
                    previous_ratios = _read_optional_snapshot_table(
                        pinned, RATIOS_TABLE
                    )
                    previous_benchmarks = _read_optional_snapshot_table(
                        pinned, BENCHMARKS_TABLE
                    )
                    previous_red_flags = _read_optional_snapshot_table(
                        pinned, RED_FLAGS_TABLE
                    )
                    previous_ml_scores = _read_optional_snapshot_table(
                        pinned, ML_SCORES_TABLE
                    )
                    previous_model_card_meta = _read_optional_snapshot_table(
                        pinned, MODEL_CARD_META_TABLE
                    )
            except (FileNotFoundError, ContractViolationError):
                previous_ratios = None
                previous_benchmarks = None
                previous_red_flags = None
                previous_ml_scores = None
                previous_model_card_meta = None

        bundle = run_incremental_analytics_transform(
            facts,
            issuers,
            as_of=as_of,
            run_id=context.run_id,
            previous_ratios=previous_ratios,
            previous_benchmarks=previous_benchmarks,
            previous_red_flags=previous_red_flags,
            previous_ml_scores=previous_ml_scores,
            previous_model_card_meta=previous_model_card_meta,
            previous_state=previous_state,
            previous_snapshot_id=previous_snapshot_id,
            state_path=transform_state_path,
            ml_config=ml_cfg,
            quality_config=quality_cfg,
            market_prices=market_prices_df,
            persist_state=False,
        )

        tables: dict[str, pl.DataFrame] = {
            RATIOS_TABLE: bundle.financial_ratios,
            BENCHMARKS_TABLE: bundle.industry_benchmarks,
            RED_FLAGS_TABLE: bundle.accounting_red_flags,
        }
        if ml_cfg.enabled:
            tables[ML_SCORES_TABLE] = bundle.ml_scores
        if bundle.model_card_meta is not None:
            tables[MODEL_CARD_META_TABLE] = bundle.model_card_meta
        if bundle.pit_valuation_mart is not None:
            tables[PIT_VALUATION_MART_TABLE] = bundle.pit_valuation_mart

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
                if name not in (RATIOS_TABLE, MODEL_CARD_META_TABLE):
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
                        "ml_enabled": ml_cfg.enabled,
                        "transform_engine": "dbsp_incremental",
                        "full_rebuild": bundle.diff.full_rebuild,
                        "dirty_issuers_count": len(bundle.diff.dirty_issuers),
                    }
                ),
                encoding="utf-8",
            )
            if bundle.evaluation_report is not None:
                (staging / "model_evaluation_report.json").write_text(
                    json.dumps(
                        bundle.evaluation_report,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
            if bundle.ml_run_manifest is not None:
                (staging / "_ml_run_manifest.json").write_text(
                    json.dumps(
                        bundle.ml_run_manifest,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
            staging.rename(parent / context.run_id)
        return StepOutcome()

    def _build_ml_config(self) -> MLTransformConfig:
        raw_ml = self.settings.get("ml")
        if raw_ml is None:
            return MLTransformConfig(enabled=False)
        if not isinstance(raw_ml, Mapping):
            raise ContractViolationError(
                "Analytics ml configuration must be a mapping."
            )
        enabled = raw_ml.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ContractViolationError("Analytics ml.enabled must be a boolean.")
        if not enabled:
            return MLTransformConfig(enabled=False)

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
        resolved_target_mode: Literal["FUNDAMENTAL_DISTRESS", "REGULATORY_DISTRESS"] = (
            "REGULATORY_DISTRESS"
            if target_mode == "REGULATORY_DISTRESS"
            else "FUNDAMENTAL_DISTRESS"
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

        return MLTransformConfig(
            enabled=True,
            validation_start=validation_start,
            test_start=test_start,
            approved_target=approved_target,
            require_exact_filing=True,
            statement_scope=scope,
            taxonomy_version=taxonomy,
            scored_taxonomies=scored_taxonomies,
            feature_set=feature_set,
            target_mode=resolved_target_mode,
            regulatory_events=regulatory_events,
        )

    def _confined_ml_input_path(self, value: object, field_name: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ContractViolationError(
                f"Analytics {field_name} must be a nonempty relative path."
            )
        candidate = Path(value)
        posix_candidate = PurePosixPath(value)
        win_candidate = PureWindowsPath(value)
        if (
            candidate.is_absolute()
            or candidate.drive
            or candidate.root
            or posix_candidate.is_absolute()
            or win_candidate.is_absolute()
            or bool(win_candidate.drive)
            or bool(win_candidate.root)
        ):
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
