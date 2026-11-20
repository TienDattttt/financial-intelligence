"""Layer 0 thin handler publishing validated curated tables as an atomic Layer 3 snapshot.

Verified Sources:
- DuckLake Official Documentation: https://ducklake.select/docs/stable/
- Polars Parquet I/O (single-read in-memory reuse): https://docs.pola.rs/
- Internal Layer 3 contract: docs/v2_specs/04_STREAM_B1_LAYER3_DUCKLAKE_STORAGE_AND_CURATED_GC.md
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.pipeline.runner import StepContext, StepOutcome
from finintel.pipeline.state_store import StateStore
from finintel.quality.io import CLEAN_FACTS_TABLE
from finintel.storage.ducklake_store import DuckLakeStore, StorageConfig
from finintel.storage.manifest_export import (
    build_filings_manifest_snapshot,
    build_latest_facts_table,
)
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_TABLE,
    BENCHMARKS_TABLE,
    FILINGS_MANIFEST_TABLE,
    ML_SCORES_TABLE,
    QUALITY_SUMMARY_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)
from finintel.storage.snapshot_publisher import SnapshotPublisher, TableInput


def _snapshot_id_for_run(run_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", run_id)[:24] or "run"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"snap_{stamp}_{safe}"


class PublishHandler:
    """Thin Layer 0 wrapper delegating atomic snapshot creation to Layer 3."""

    def __init__(
        self,
        state: StateStore,
        config_dir: Path,
        analytics_settings: Mapping[str, object],
        *,
        storage_settings: Mapping[str, object] | None = None,
        keep_snapshots: int = 2,
    ) -> None:
        self.state = state
        self.config_dir = config_dir
        self.analytics_settings = analytics_settings
        self.storage_settings = dict(storage_settings) if storage_settings else {}
        self.keep_snapshots = keep_snapshots

    def __call__(self, context: StepContext) -> StepOutcome:
        data_root = self.state.path.parent.parent
        curated = data_root / "curated"
        quality_dir = curated / "quality" / context.run_id
        analytics_dir = curated / "analytics" / context.run_id
        norm_dir = curated / "financial_facts" / context.run_id

        storage_config = StorageConfig.from_mapping(
            self.storage_settings,
            config_dir=self.config_dir,
            data_root=data_root,
        )
        effective_keep = (
            storage_config.retention.keep_snapshots
            if self.storage_settings
            else self.keep_snapshots
        )

        facts_path = quality_dir / CLEAN_FACTS_TABLE
        if not facts_path.is_file():
            facts_path = norm_dir / "financial_facts.parquet"
        if not facts_path.is_file():
            raise ContractViolationError(
                "Publish requires clean financial_facts.parquet from QUALITY_GATE_1 or NORMALIZE."
            )

        issuers_setting = self.analytics_settings.get("issuers_path")
        issuers_path = (
            (self.config_dir / str(issuers_setting)).resolve()
            if isinstance(issuers_setting, str) and issuers_setting.strip()
            else curated / "issuers.parquet"
        )
        if not issuers_path.is_file():
            raise ContractViolationError(
                "Publish requires issuers.parquet in curated storage."
            )

        # Read clean_financial_facts.parquet exactly ONCE and share in memory (HI-09).
        try:
            facts_df = pl.read_parquet(facts_path)
        except (OSError, pl.exceptions.PolarsError) as exc:
            raise ContractViolationError(
                "Unable to read clean financial_facts.parquet for publication."
            ) from exc

        as_of = self._resolve_as_of(analytics_dir, context.run_id)
        tables: dict[str, TableInput] = {
            "issuers.parquet": issuers_path,
            "financial_facts.parquet": facts_df,
            "latest_facts.parquet": build_latest_facts_table(facts_df, as_of=as_of),
            FILINGS_MANIFEST_TABLE: build_filings_manifest_snapshot(
                self.state, facts_df
            ),
        }

        if storage_config.publish_unmapped:
            unmapped_path = norm_dir / "unmapped_metrics.parquet"
            if unmapped_path.is_file():
                tables["unmapped_metrics.parquet"] = unmapped_path

        for table_name in (
            RATIOS_TABLE,
            BENCHMARKS_TABLE,
            RED_FLAGS_TABLE,
            ML_SCORES_TABLE,
        ):
            candidate = analytics_dir / table_name
            if candidate.is_file():
                tables[table_name] = candidate

        for table_name in (QUALITY_SUMMARY_TABLE, AUDIT_QUARANTINE_TABLE):
            candidate = quality_dir / table_name
            if candidate.is_file():
                tables[table_name] = candidate

        snapshot_id = _snapshot_id_for_run(context.run_id)
        publish_metadata = {
            "run_id": context.run_id,
            "source": context.request.source,
            "as_of": as_of.isoformat(),
        }
        if storage_config.backend == "ducklake":
            ducklake_store = DuckLakeStore(
                catalog_path=storage_config.catalog.sqlite_path,
                data_path=storage_config.data_path,
                state_store=self.state,
                catalog_alias=storage_config.catalog_alias,
                catalog_driver=storage_config.catalog.driver,
                postgres_dsn_env=storage_config.catalog.postgres_dsn_env,
                keep_snapshots=effective_keep,
                min_snapshot_age_seconds=storage_config.retention.min_snapshot_age_seconds,
            )
            ducklake_store.publish_snapshot(
                snapshot_id,
                tables,
                run_id=context.run_id,
                metadata=publish_metadata,
            )
        else:
            publisher = SnapshotPublisher(
                data_root / "published" / "snapshots",
                state_store=self.state,
                keep_snapshots=effective_keep,
                min_snapshot_age_seconds=storage_config.retention.min_snapshot_age_seconds,
            )
            publisher.publish(
                snapshot_id,
                tables,
                run_id=context.run_id,
                metadata=publish_metadata,
            )
        return StepOutcome(snapshot_id=snapshot_id)

    def _resolve_as_of(self, analytics_dir: Path, run_id: str) -> date:
        meta_path = analytics_dir / "_analytics_meta.json"
        if meta_path.is_file():
            try:
                content = json.loads(meta_path.read_text(encoding="utf-8"))
                raw_as_of = content.get("as_of")
                if isinstance(raw_as_of, str):
                    return date.fromisoformat(raw_as_of)
            except (OSError, ValueError):
                pass
        run = self.state.get_run(run_id)
        started = run.get("started_at") if run is not None else None
        if isinstance(started, str):
            return (
                datetime.fromisoformat(started)
                .astimezone(timezone(timedelta(hours=7)))
                .date()
            )
        return datetime.now(timezone(timedelta(hours=7))).date()
