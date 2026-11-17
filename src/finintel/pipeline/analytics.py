"""Run Layer 4A against a normalized fact history and issuer reference table."""

import json
import logging
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NoReturn

import polars as pl

from finintel.analytics.tables import RATIOS_TABLE, build_analytics_tables
from finintel.exceptions import ContractViolationError
from finintel.pipeline.normalize import NORMALIZATION_METADATA, _period_filter
from finintel.pipeline.runner import StepContext, StepOutcome
from finintel.pipeline.state_store import StateStore

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
                    }
                ),
                encoding="utf-8",
            )
            staging.rename(parent / context.run_id)
        return StepOutcome()

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
