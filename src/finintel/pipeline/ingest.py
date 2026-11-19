"""Connect Layer 1 providers to the INGEST stage."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from finintel.domain.schemas import (
    FilingMetadata,
    FinancialStatementProvider,
    PeriodType,
    StatementType,
)
from finintel.exceptions import ContractViolationError
from finintel.ingestion.archive import ArchiveOutcome, RawArchive
from finintel.ingestion.fhsc_oapi_adapter import FhscOapiAdapter
from finintel.ingestion.file_adapter import FileAdapter
from finintel.pipeline.runner import StepContext, StepOutcome, StepStatus
from finintel.pipeline.state_store import StateStore

logger = logging.getLogger("finintel.pipeline.ingest")
_FIXTURES_ROOT = (Path(__file__).resolve().parents[3] / "tests" / "fixtures").resolve()


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ContractViolationError("Expected a mapping in ingestion settings.")
    return dict(value)


def _entries(value: object) -> Iterator[dict[str, object]]:
    if not isinstance(value, list):
        raise ContractViolationError("Expected a list in ingestion settings.")
    for item in value:
        yield _mapping(item)


def _text(fields: Mapping[str, object], key: str) -> str:
    value = fields.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractViolationError(f"Ingestion settings require {key}.")
    return value


def _matches_period(metadata: FilingMetadata, period: str | None) -> bool:
    if period is None:
        return True
    year, _, quarter = period.partition("-Q")
    if metadata.fiscal_year != int(year):
        return False
    if quarter:
        return metadata.fiscal_quarter == int(quarter) or (
            quarter == "2" and metadata.period_type == PeriodType.SEMI_ANNUAL
        )
    return metadata.period_type == PeriodType.ANNUAL


@dataclass
class _IngestProgress:
    providers: list[FinancialStatementProvider]
    completed_idx: int = 0
    results: list[ArchiveOutcome] = field(default_factory=list)
    archived_pages: int = 0
    deferred_periods: int = 0
    incomplete: bool = False


class IngestHandler:
    def __init__(
        self, state: StateStore, config_dir: Path, settings: Mapping[str, object]
    ) -> None:
        self.state = state
        self.config_dir = config_dir.resolve()
        self.settings = settings
        self._progress: dict[tuple[str, tuple[str, ...]], _IngestProgress] = {}
        if "raw_path" in self.settings:
            self._resolve_confined(_text(self.settings, "raw_path"))

    def _allowed_roots(self) -> tuple[Path, ...]:
        config_root = (
            self.config_dir.parent
            if self.config_dir.name == "config"
            else self.config_dir
        ).resolve()
        state_parent = self.state.path.parent
        state_root = (
            state_parent.parent if state_parent.name == "metadata" else state_parent
        ).resolve()
        return (config_root, state_root, _FIXTURES_ROOT)

    def _resolve_confined(self, raw_path_text: str) -> Path:
        candidate = self.config_dir / raw_path_text
        if candidate.is_symlink():
            raise ContractViolationError("Ingestion path cannot be a symbolic link.")
        resolved = candidate.resolve()
        if not any(
            resolved.is_relative_to(allowed) for allowed in self._allowed_roots()
        ):
            raise ContractViolationError(
                "Ingestion path is outside the allowed workspace directory."
            )
        return resolved

    def __call__(self, context: StepContext) -> StepOutcome:
        state_parent = self.state.path.parent
        root = (
            state_parent.parent if state_parent.name == "metadata" else state_parent
        ) / "raw"
        if "raw_path" in self.settings:
            root = self._resolve_confined(_text(self.settings, "raw_path"))
        archive = RawArchive(root, self.state)

        progress_key = (context.run_id, context.tickers)
        progress = self._progress.get(progress_key)
        if progress is None:
            providers = list(self._providers(context))
            if not providers:
                raise ContractViolationError(
                    "No ingestion input matched the requested scope."
                )
            progress = _IngestProgress(providers=providers)
            self._progress[progress_key] = progress

        while progress.completed_idx < len(progress.providers):
            provider = progress.providers[progress.completed_idx]
            for bundle in provider.fetch():
                outcomes = archive.store(bundle, run_id=context.run_id)
                progress.results.extend(outcomes)
                progress.archived_pages += 1
                progress.deferred_periods += len(bundle.deferred_locators)
                progress.incomplete |= bundle.pagination_status == "PAGE_LIMIT_REACHED"
                if context.record_archived is not None:
                    for filing in bundle.filings:
                        context.record_archived(filing.metadata.ticker_at_filing)
            progress.completed_idx += 1

        self._progress.pop(progress_key, None)
        results = progress.results
        new_filings = sum(item.status == "ARCHIVED" for item in results)
        issuers = {item.issuer_id for item in results}
        single_filing = results[0].filing_id if len(results) == 1 else None
        logger.info(
            "Ingestion archived source payloads",
            extra={
                "event": "ingestion_finished",
                "run_id": context.run_id,
                "stage": context.stage,
                "details": {
                    "payloads": progress.archived_pages,
                    "new_filings": new_filings,
                    "unchanged_filings": len(results) - new_filings,
                    "deferred_periods": progress.deferred_periods,
                    "page_limit_reached": progress.incomplete,
                },
            },
        )
        status = StepStatus.SUCCEEDED
        if progress.incomplete or progress.deferred_periods or not results:
            status = StepStatus.INCOMPLETE
        elif not new_filings:
            status = StepStatus.SKIPPED_UNCHANGED
        return StepOutcome(
            status,
            issuer_id=next(iter(issuers)) if len(issuers) == 1 else None,
            filing_id=single_filing,
        )

    def _providers(self, context: StepContext) -> Iterator[FinancialStatementProvider]:
        if context.request.source == "file":
            yield from self._files(context)
            return

        if not context.tickers:
            raise ContractViolationError(
                "Select at least one ticker for FHSC ingestion."
            )
        metadata = tuple(
            FilingMetadata.from_mapping(entry)
            for entry in _entries(self.settings.get("fhsc_filings", []))
        )
        period = context.request.period
        year = quarter = None
        periods: tuple[PeriodType, ...] = (PeriodType.QUARTER, PeriodType.ANNUAL)
        if period is not None:
            year = int(period[:4])
            quarter = int(period[-1]) if "-Q" in period else None
            periods = (PeriodType.ANNUAL if quarter is None else PeriodType.QUARTER,)
        for ticker in context.tickers:
            for statement in StatementType:
                for period_type in periods:
                    yield FhscOapiAdapter(
                        ticker,
                        statement,
                        period_type,
                        metadata=metadata,
                        year=year,
                        quarter=quarter,
                    )

    def _files(self, context: StepContext) -> Iterator[FileAdapter]:
        for entry in _entries(self.settings.get("files", [])):
            metadata = FilingMetadata.from_mapping(_mapping(entry.get("metadata")))
            if context.tickers and metadata.ticker_at_filing not in context.tickers:
                continue
            if not _matches_period(metadata, context.request.period):
                continue
            sheet = _text(entry, "sheet") if "sheet" in entry else None
            header_row = entry.get("header_row", 1)
            if type(header_row) is not int:
                raise ContractViolationError("The header row must be an integer.")
            file_path = self._resolve_confined(_text(entry, "path"))
            yield FileAdapter(
                file_path,
                metadata,
                source_provider=_text(entry, "source_provider"),
                metric_column=_text(entry, "metric_column")
                if "metric_column" in entry
                else "raw_metric_key",
                value_column=_text(entry, "value_column")
                if "value_column" in entry
                else "raw_value",
                sheet=sheet,
                header_row=header_row,
            )
