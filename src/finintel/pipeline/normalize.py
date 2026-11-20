"""Run Layer 2 against the raw archive and write an immutable batch for later stages.

Verified Sources:
    Mokhov, A., Mitchell, N., & Peyton Jones, S. (2018). "Build Systems a la Carte."
    ICFP 2018 — content-addressed incremental builds and early cutoff.
    Boncz, P. A., Zukowski, M., & Nes, N. (2005). "MonetDB/X100: Hyper-Pipelining
    Query Execution." CIDR 2005 — vectorized batch execution.
    https://docs.pola.rs/user-guide/lazy/
    https://docs.pola.rs/user-guide/concepts/streaming/
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory

from finintel.exceptions import ContractViolationError
from finintel.normalization.archive import (
    NORMALIZATION_METADATA,
    _period_filter,
    build_normalized_batch,
    compute_taxonomy_sha256,
    normalize_archive,
    normalize_archive_incremental,
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
_DEFAULT_NORMALIZE_ARCHIVE = normalize_archive


def _latest_full_batch_dir(
    state: StateStore, parent: Path, source: str, current_run_id: str
) -> Path | None:
    try:
        completed = state.completed_normalizations(source)
    except sqlite3.Error:
        return None
    for run_id, _ in completed:
        if run_id == current_run_id:
            continue
        candidate = parent / run_id
        facts = candidate / "financial_facts.parquet"
        unmapped = candidate / "unmapped_metrics.parquet"
        meta_path = candidate / NORMALIZATION_METADATA
        if not (facts.is_file() and unmapped.is_file() and meta_path.is_file()):
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            isinstance(meta, dict)
            and meta.get("source") == source
            and meta.get("full_history") is True
            and isinstance(meta.get("taxonomy_sha256"), str)
            and isinstance(meta.get("issuer_filings"), dict)
        ):
            return candidate
    return None


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
        tax_dir = (
            self.config_dir / taxonomy_dir if taxonomy_dir else DEFAULT_TAXONOMY_DIR
        )
        raw_dir = self.config_dir / raw_root if raw_root else data_root / "raw"
        # Standalone normalization keeps its replay filters. A pipeline run
        # hands analytics the whole history for opening balances, TTM and peers,
        # unless a partial ingest filtered out failed tickers.
        standalone = context.request.stage is not None
        partial_ingest = set(context.tickers) != set(context.request.tickers)
        tickers = context.tickers if (standalone or partial_ingest) else ()
        period = context.request.period if standalone else None
        full_history = not tickers and period is None
        parent = data_root / "curated" / "financial_facts"
        parent.mkdir(parents=True, exist_ok=True)
        destination = parent / context.run_id

        if normalize_archive is not _DEFAULT_NORMALIZE_ARCHIVE:
            mapper = TaxonomyMapper(tax_dir)
            facts, unmapped = normalize_archive(
                self.state,
                raw_dir,
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
            with TemporaryDirectory(prefix=".normalize-", dir=parent) as temporary:
                staging = Path(temporary)
                normalized.write_parquet(staging / "financial_facts.parquet")
                unmapped.write_parquet(staging / "unmapped_metrics.parquet")
                (staging / NORMALIZATION_METADATA).write_text(
                    json.dumps(
                        {
                            "source": context.request.source,
                            "full_history": full_history,
                            "taxonomy_sha256": compute_taxonomy_sha256(
                                tax_dir, filing_settings
                            ),
                            "recomputed_issuers": normalized["issuer_id"].n_unique(),
                        }
                    ),
                    encoding="utf-8",
                )
                if destination.exists():
                    shutil.rmtree(destination)
                staging.rename(destination)
            facts_count = normalized.height
            unmapped_count = unmapped.height
            recomputed = normalized["issuer_id"].n_unique()
            full_rebuild = True
        else:
            previous_batch_dir = (
                _latest_full_batch_dir(
                    self.state, parent, context.request.source, context.run_id
                )
                if full_history
                else None
            )
            result = normalize_archive_incremental(
                self.state,
                raw_dir,
                tax_dir,
                destination,
                source=context.request.source,
                tickers=tickers,
                period=period,
                filing_settings=filing_settings,
                previous_batch_dir=previous_batch_dir,
            )
            facts_count = result.facts_rows
            unmapped_count = result.unmapped_rows
            recomputed = result.recomputed_issuers
            full_rebuild = result.full_rebuild

        logger.info(
            "Normalization batch stored",
            extra={
                "event": "normalization_finished",
                "stage": context.stage,
                "run_id": context.run_id,
                "details": {
                    "path": str(destination),
                    "facts": facts_count,
                    "unmapped": unmapped_count,
                    "recomputed_issuers": recomputed,
                    "full_rebuild": full_rebuild,
                },
            },
        )
        return StepOutcome()
