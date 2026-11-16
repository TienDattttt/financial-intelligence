"""Read explicitly identified offline filings while retaining the supplied bytes."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module
from io import BytesIO, StringIO
from pathlib import Path
from typing import Literal, cast
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from finintel.exceptions import ContractViolationError
from finintel.ingestion.base import (
    FilingMetadata,
    FinancialStatementProvider,
    RawFiling,
    RawFilingBundle,
    RawPayload,
)

FORMATS = {"csv", "json", "xlsx", "parquet"}


def _unique_object(items: list[tuple[str, object]]) -> dict[str, object]:
    values = dict(items)
    if len(values) != len(items):
        raise ContractViolationError("Duplicate field in offline JSON.")
    return values


class FileAdapter(FinancialStatementProvider):
    """Import one statement period with explicit columns for source codes and values."""

    def __init__(
        self,
        path: Path,
        metadata: FilingMetadata,
        *,
        source_provider: str,
        metric_column: str = "raw_metric_key",
        value_column: str = "raw_value",
        sheet: str | None = None,
        header_row: int = 1,
    ) -> None:
        extension = path.suffix.lower().removeprefix(".")
        if extension not in FORMATS:
            raise ContractViolationError(
                "Expected an XLSX, CSV, JSON, or Parquet filing."
            )
        if not source_provider.strip() or source_provider.strip().lower() == "fhsc":
            raise ContractViolationError(
                "Provide the offline document's original source."
            )
        if not metric_column or not value_column or metric_column == value_column:
            raise ContractViolationError(
                "Expected distinct raw code and value columns."
            )
        if type(header_row) is not int or header_row < 1:
            raise ContractViolationError("The header row must be a positive integer.")
        if extension != "xlsx" and (sheet is not None or header_row != 1):
            raise ContractViolationError("Sheet and header_row only apply to XLSX.")
        self.path = path
        self.metadata = metadata
        self.source_provider = source_provider.strip().lower()
        self.extension = cast(Literal["csv", "json", "xlsx", "parquet"], extension)
        self.metric_column = metric_column
        self.value_column = value_column
        self.sheet = sheet
        self.header_row = header_row

    def fetch(self) -> Iterator[RawFilingBundle]:
        try:
            content = self.path.read_bytes()
            fetched_at = datetime.now(UTC)
            metrics = self._read_metrics(content)
        except (OSError, ValueError, csv.Error, BadZipFile, ParseError):
            raise ContractViolationError("Unable to read the offline filing.") from None
        yield RawFilingBundle(
            RawPayload(
                content,
                self.extension,
                self.source_provider,
                "file_adapter_v1",
                "TRUE_ORIGINAL_DOCUMENT",
                fetched_at,
                {
                    "filename": self.path.name,
                    "metric_column": self.metric_column,
                    "value_column": self.value_column,
                    "sheet": self.sheet,
                    "header_row": self.header_row,
                },
            ),
            (RawFiling(self.metadata, metrics),),
        )

    def _read_metrics(self, content: bytes) -> dict[str, object]:
        metrics: dict[str, object] = {}
        for raw_code, value in self._pairs(content):
            if raw_code is None and value is None:
                continue
            if not isinstance(raw_code, str) or not raw_code.strip():
                raise ContractViolationError(
                    "Offline raw metric codes must be nonempty text."
                )
            if raw_code in metrics:
                raise ContractViolationError(
                    "Duplicate raw metric code in offline filing."
                )
            metrics[raw_code] = (
                None if isinstance(value, str) and not value.strip() else value
            )
        if not metrics:
            raise ContractViolationError("No raw metrics in the offline filing.")
        return metrics

    def _pairs(self, content: bytes) -> Iterator[tuple[object, object]]:
        if self.extension == "json":
            rows = json.loads(
                content, parse_float=Decimal, object_pairs_hook=_unique_object
            )
            if not isinstance(rows, list):
                raise ContractViolationError(
                    "Offline JSON must contain an array of metrics."
                )
            for row in rows:
                if (
                    not isinstance(row, dict)
                    or not {
                        self.metric_column,
                        self.value_column,
                    }
                    <= row.keys()
                ):
                    raise ContractViolationError(
                        "Offline JSON is missing the raw metric columns."
                    )
                yield row[self.metric_column], row[self.value_column]
        elif self.extension == "csv":
            with StringIO(content.decode("utf-8-sig"), newline="") as stream:
                yield from self._table_pairs(csv.reader(stream, strict=True))
        elif self.extension == "xlsx":
            yield from self._xlsx_pairs(content)
        else:
            yield from self._parquet_pairs(content)

    def _table_pairs(
        self, rows: Iterable[Sequence[object]]
    ) -> Iterator[tuple[object, object]]:
        rows = iter(rows)
        header = next(rows, ())
        columns = (self.metric_column, self.value_column)
        if any(header.count(name) != 1 for name in columns):
            raise ContractViolationError("Missing or duplicate raw metric columns.")
        code_index, value_index = (header.index(name) for name in columns)
        for row in rows:
            if all(cell is None or cell == "" for cell in row):
                continue
            if len(row) <= max(code_index, value_index):
                raise ContractViolationError("A row is missing a raw metric column.")
            yield row[code_index], row[value_index]

    def _xlsx_pairs(self, content: bytes) -> Iterator[tuple[object, object]]:
        openpyxl = import_module("openpyxl")
        try:
            book = openpyxl.load_workbook(
                BytesIO(content), read_only=True, data_only=True, keep_links=False
            )
        except (KeyError, openpyxl.utils.exceptions.InvalidFileException):
            raise ContractViolationError("Invalid XLSX filing.") from None
        try:
            sheet = book[self.sheet or book.sheetnames[0]]
            rows = cast(
                Iterator[Sequence[object]],
                sheet.iter_rows(
                    min_row=self.header_row,
                    values_only=True,
                ),
            )
            yield from self._table_pairs(rows)
        except (KeyError, IndexError):
            raise ContractViolationError(
                "The requested XLSX sheet is missing."
            ) from None
        finally:
            book.close()

    def _parquet_pairs(self, content: bytes) -> Iterator[tuple[object, object]]:
        polars = import_module("polars")
        try:
            frame = polars.read_parquet(
                BytesIO(content), columns=[self.metric_column, self.value_column]
            )
        except polars.exceptions.PolarsError:
            raise ContractViolationError(
                "Invalid Parquet filing or missing raw metric columns."
            ) from None
        columns = cast(dict[str, list[object]], frame.to_dict(as_series=False))
        yield from zip(
            columns[self.metric_column], columns[self.value_column], strict=True
        )
