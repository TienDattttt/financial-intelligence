"""Run Layer 2 against the raw archive and write an immutable batch for later stages."""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory

from finintel.exceptions import ContractViolationError
from finintel.normalization.archive import (
    _period_filter,
    build_normalized_batch,
    normalize_archive,
)
from finintel.normalization.mapper import TaxonomyMapper
from finintel.pipeline.runner import StepContext, StepOutcome
from finintel.pipeline.state_store import StateStore

__all__ = [
    "DEFAULT_TAXONOMY_DIR",
    "NORMALIZATION_METADATA",
    "NormalizeHandler",
    "_period_filter",
]

logger = logging.getLogger("finintel.pipeline.normalize")
DEFAULT_TAXONOMY_DIR = Path(__file__).resolve().parents[3] / "config"
NORMALIZATION_METADATA = "_normalize_meta.json"


class NormalizeHandler:
    def __init__(
        self,
        state: StateStore,
        config_dir: Path,
        ingestion: Mapping[str, object],
        settings: Mapping[str, object],
    ) -> None:
        self.state = state
        self.config_dir = config_dir
        self.ingestion = ingestion
        self.settings = settings

    def __call__(self, context: StepContext) -> StepOutcome:
        data_root = self.state.path.parent.parent
        raw_root = self.ingestion.get("raw_path")
        if raw_root is not None and (
            not isinstance(raw_root, str) or not raw_root.strip()
        ):
            raise ContractViolationError("Expected an archive path for normalization.")
        taxonomy_dir = self.settings.get("taxonomy_dir")
        if taxonomy_dir is not None and (
            not isinstance(taxonomy_dir, str) or not taxonomy_dir.strip()
        ):
            raise ContractViolationError("Expected a taxonomy configuration directory.")
        filing_settings = self.settings.get("filings", {})
        if not isinstance(filing_settings, dict) or any(
            not isinstance(key, str) for key in filing_settings
        ):
            raise ContractViolationError(
                "Expected normalization settings keyed by filing ID."
            )
        mapper = TaxonomyMapper(
            self.config_dir / taxonomy_dir if taxonomy_dir else DEFAULT_TAXONOMY_DIR
        )
        # Standalone normalization keeps its replay filters. A pipeline run
        # hands analytics the whole history for opening balances, TTM and peers,
        # unless a partial ingest filtered out failed tickers.
        standalone = context.request.stage is not None
        partial_ingest = set(context.tickers) != set(context.request.tickers)
        tickers = context.tickers if (standalone or partial_ingest) else ()
        period = context.request.period if standalone else None
        facts, unmapped = normalize_archive(
            self.state,
            self.config_dir / raw_root if raw_root else data_root / "raw",
            mapper,
            source=context.request.source,
            tickers=tickers,
            filing_settings=filing_settings,
        )
        normalized, unmapped = build_normalized_batch(
            facts,
            unmapped,
            period=period,
        )
        if normalized.is_empty() and unmapped.is_empty():
            raise ContractViolationError(
                "No archived filing matched the requested scope."
            )
        parent = data_root / "curated" / "financial_facts"
        parent.mkdir(parents=True, exist_ok=True)
        destination = parent / context.run_id
        with TemporaryDirectory(prefix=".normalize-", dir=parent.parent) as temporary:
            staging = Path(temporary)
            normalized.write_parquet(staging / "financial_facts.parquet")
            unmapped.write_parquet(staging / "unmapped_metrics.parquet")
            (staging / NORMALIZATION_METADATA).write_text(
                json.dumps(
                    {
                        "source": context.request.source,
                        "full_history": not tickers and period is None,
                    }
                ),
                encoding="utf-8",
            )
            if destination.exists():
                shutil.rmtree(destination)
            staging.rename(destination)
        logger.info(
            "Normalization batch stored",
            extra={
                "event": "normalization_finished",
                "stage": context.stage,
                "run_id": context.run_id,
                "details": {
                    "path": str(destination),
                    "facts": normalized.height,
                    "unmapped": unmapped.height,
                },
            },
        )
        return StepOutcome()
