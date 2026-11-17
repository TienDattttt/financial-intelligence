"""Run Layer 4A against a normalized fact history and issuer reference table."""

from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date, datetime, timedelta, timezone

import polars as pl

from finintel.analytics.tables import RATIOS_TABLE, build_analytics_tables
from finintel.exceptions import ContractViolationError
from finintel.pipeline.normalize import _period_filter
from finintel.pipeline.runner import StepContext, StepOutcome
from finintel.pipeline.state_store import StateStore


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
        run_batch = (
            curated / "financial_facts" / context.run_id / "financial_facts.parquet"
        )
        default_facts = curated / "financial_facts.parquet"
        if run_batch.is_file() and not default_facts.is_file():
            default_facts = run_batch
        facts_path = self._input_path("facts_path", default_facts)
        issuers_path = self._input_path("issuers_path", curated / "issuers.parquet")
        as_of = self._as_of()
        try:
            facts, issuers = pl.read_parquet(facts_path), pl.read_parquet(issuers_path)
        except (OSError, pl.exceptions.PolarsError) as exc:
            raise ContractViolationError(
                "Analytics requires readable financial facts and issuers Parquet inputs."
            ) from exc

        tables = build_analytics_tables(facts, issuers, as_of=as_of)
        if context.tickers or context.request.period:
            # Calculate history and peers first. A replay filter limits only outputs.
            ratios = tables[RATIOS_TABLE]
            if context.tickers:
                ratios = ratios.filter(pl.col("ticker").is_in(context.tickers))
            if context.request.period:
                ratios = ratios.filter(_period_filter(context.request.period))
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
            staging.rename(parent / context.run_id)
        return StepOutcome()

    def _input_path(self, key: str, default: Path) -> Path:
        value = self.settings.get(key)
        if value is None:
            return default
        if not isinstance(value, str) or not value.strip():
            raise ContractViolationError(f"Analytics {key} must be a nonempty path.")
        return self.config_dir / value

    def _as_of(self) -> date:
        value = self.settings.get("as_of")
        if value is None:
            # Vietnam has a fixed UTC+7 offset. Keep the default consistent
            # between local Windows and CI without depending on a tzdata wheel.
            return datetime.now(timezone(timedelta(hours=7))).date()
        if type(value) is date:
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        raise ContractViolationError("Analytics as_of must be an ISO calendar date.")
