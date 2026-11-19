"""Read explicitly identified offline filings while retaining the supplied bytes."""

from __future__ import annotations

import csv
import json
import re
import zlib
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module
from io import BytesIO, StringIO
from itertools import zip_longest
from pathlib import Path
from typing import Literal, cast
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from finintel.domain.schemas import (
    FilingMetadata,
    FinancialStatementProvider,
    RawFiling,
    RawFilingBundle,
    RawPayload,
)
from finintel.exceptions import ContractViolationError

FORMATS = {"csv", "json", "xlsx", "parquet"}
MAX_FILING_BYTES = 50 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_XLSX_ROWS = 10_000
MAX_XLSX_COLS = 256
MAX_METRIC_KEY_LEN = 256


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
        normalized_source = source_provider.strip().lower()
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", normalized_source)
            or normalized_source == "fhsc"
        ):
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
        self.source_provider = normalized_source
        self.extension = cast(Literal["csv", "json", "xlsx", "parquet"], extension)
        self.metric_column = metric_column
        self.value_column = value_column
        self.sheet = sheet
        self.header_row = header_row

    def fetch(self) -> Iterator[RawFilingBundle]:
        try:
            if self.path.is_symlink():
                raise ContractViolationError(
                    "Offline filing path cannot be a symbolic link."
                )
            if self.path.stat().st_size > MAX_FILING_BYTES:
                raise ContractViolationError(
                    "Offline filing exceeds the maximum allowed size."
                )
            content = self.path.read_bytes()
        except OSError:
            raise ContractViolationError("Unable to read the offline filing.") from None
        fetched_at = datetime.now(UTC)
        metrics = self.read_metrics(content)
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

    def read_metrics(self, content: bytes) -> dict[str, object]:
        """Read the configured column pair from supplied original filing bytes."""
        if len(content) > MAX_FILING_BYTES:
            raise ContractViolationError(
                "Offline filing exceeds the maximum allowed size."
            )
        try:
            return self._read_metrics(content)
        except (
            OSError,
            ValueError,
            csv.Error,
            BadZipFile,
            ParseError,
            RecursionError,
            zlib.error,
        ):
            raise ContractViolationError("Unable to read the offline filing.") from None

    def _read_metrics(self, content: bytes) -> dict[str, object]:
        metrics: dict[str, object] = {}
        for raw_code, value in self._pairs(content):
            code_blank = raw_code is None or (
                isinstance(raw_code, str) and not raw_code.strip()
            )
            val_blank = value is None or (isinstance(value, str) and not value.strip())
            if code_blank and val_blank:
                continue
            if (
                not isinstance(raw_code, str)
                or not raw_code.strip()
                or len(raw_code) > MAX_METRIC_KEY_LEN
                or any(ord(ch) < 32 for ch in raw_code)
            ):
                raise ContractViolationError(
                    "Offline raw metric codes must be nonempty text."
                )
            if raw_code in metrics:
                raise ContractViolationError(
                    "Duplicate raw metric code in offline filing."
                )
            metrics[raw_code] = None if val_blank else value
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

    def _column_indexes(self, header: Sequence[object]) -> tuple[int, int]:
        columns = (self.metric_column, self.value_column)
        if any(header.count(name) != 1 for name in columns):
            raise ContractViolationError("Missing or duplicate raw metric columns.")
        return header.index(self.metric_column), header.index(self.value_column)

    def _table_pairs(
        self, rows: Iterable[Sequence[object]]
    ) -> Iterator[tuple[object, object]]:
        rows = iter(rows)
        code_index, value_index = self._column_indexes(next(rows, ()))
        for row in rows:
            if all(cell is None or cell == "" for cell in row):
                continue
            if len(row) <= max(code_index, value_index):
                raise ContractViolationError("A row is missing a raw metric column.")
            yield row[code_index], row[value_index]

    def _xlsx_pairs(self, content: bytes) -> Iterator[tuple[object, object]]:
        with ZipFile(BytesIO(content), "r") as archive_zip:
            total_uncompressed = 0
            for info in archive_zip.infolist():
                if info.file_size < 0 or info.file_size > MAX_XLSX_UNCOMPRESSED_BYTES:
                    raise ContractViolationError(
                        "XLSX archive exceeds the maximum uncompressed size."
                    )
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_XLSX_UNCOMPRESSED_BYTES:
                    raise ContractViolationError(
                        "XLSX archive exceeds the maximum uncompressed size."
                    )

        openpyxl = import_module("openpyxl")
        book = None
        formula_book = None
        try:
            try:
                book = openpyxl.load_workbook(
                    BytesIO(content), read_only=True, data_only=True, keep_links=False
                )
                formula_book = openpyxl.load_workbook(
                    BytesIO(content), read_only=True, data_only=False, keep_links=False
                )
            except (KeyError, openpyxl.utils.exceptions.InvalidFileException):
                raise ContractViolationError("Invalid XLSX filing.") from None

            try:
                sheet_name = self.sheet or book.sheetnames[0]
                sheet = book[sheet_name]
                formula_sheet = formula_book[sheet_name]
            except (KeyError, IndexError):
                raise ContractViolationError(
                    "The requested XLSX sheet is missing."
                ) from None

            # Reset worksheet dimensions so openpyxl does not rely on
            # declared dimensions that might understate the data extent.
            sheet.reset_dimensions()
            formula_sheet.reset_dimensions()

            data_stream = sheet.iter_rows(
                min_row=self.header_row,
                values_only=True,
            )
            formula_stream = formula_sheet.iter_rows(
                min_row=self.header_row,
                values_only=True,
            )
            header_tuple = next(data_stream, ())
            next(formula_stream, ())
            if len(header_tuple) > MAX_XLSX_COLS:
                raise ContractViolationError(
                    "XLSX header exceeds the maximum column limit."
                )
            code_index, value_index = self._column_indexes(header_tuple)
            column_indexes = (code_index, value_index)
            header_width = len(header_tuple)

            sentinel = object()
            for row_offset, (raw_d_row, raw_f_row) in enumerate(
                zip_longest(data_stream, formula_stream, fillvalue=sentinel),
                start=1,
            ):
                if raw_d_row is sentinel or raw_f_row is sentinel:
                    raise ContractViolationError(
                        "Row count mismatch between data and formula views of the XLSX."
                    )
                if row_offset > MAX_XLSX_ROWS:
                    raise ContractViolationError(
                        "XLSX filing exceeds the maximum row limit."
                    )
                d_seq = cast(Sequence[object], raw_d_row)
                f_seq = cast(Sequence[object], raw_f_row)
                d_row = tuple(
                    list(d_seq)[:header_width]
                    + [None] * max(0, header_width - len(d_seq))
                )
                f_row = tuple(
                    list(f_seq)[:header_width]
                    + [None] * max(0, header_width - len(f_seq))
                )
                for col_idx in column_indexes:
                    formula_val = f_row[col_idx]
                    data_val = d_row[col_idx]
                    if (
                        isinstance(formula_val, str)
                        and formula_val.startswith("=")
                        and data_val is None
                    ):
                        raise ContractViolationError(
                            f"Formula without cached value at row "
                            f"{self.header_row + row_offset}, column {col_idx + 1}. "
                            f"Open and save the workbook in Excel to populate cached "
                            f"formula results before ingestion."
                        )
                if all(cell is None or cell == "" for cell in d_row):
                    continue
                yield d_row[code_index], d_row[value_index]
        finally:
            if formula_book is not None:
                formula_book.close()
            if book is not None:
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
        if frame.height > MAX_XLSX_ROWS:
            raise ContractViolationError(
                "Parquet filing exceeds the maximum row limit."
            )
        columns = cast(dict[str, list[object]], frame.to_dict(as_series=False))
        yield from zip(
            columns[self.metric_column], columns[self.value_column], strict=True
        )
