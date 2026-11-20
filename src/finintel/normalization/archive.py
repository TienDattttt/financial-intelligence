"""Replay immutable Layer 1 filing revisions through the canonical mapper.

Verified Sources:
    Boncz, P. A., Zukowski, M., & Nes, N. (2005). "MonetDB/X100: Hyper-Pipelining
    Query Execution." CIDR 2005 — whole-issuer vectorized batching and rechunking.
    Mokhov, A., Mitchell, N., & Peyton Jones, S. (2018). "Build Systems a la Carte."
    ICFP 2018 — content-addressed incremental builds and early cutoff.
    https://docs.pola.rs/user-guide/lazy/
    https://docs.pola.rs/user-guide/concepts/streaming/
    https://congbao.chinhphu.vn/van-ban/thong-tu-so-200-2014-tt-btc-6697/3529.htm
    https://congbao.chinhphu.vn/tai-ve-van-ban-so-99-2025-tt-btc-46529-59652?format=pdf
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import shutil
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from importlib import import_module
from itertools import groupby
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol, cast

import polars as pl

from finintel.exceptions import ArchiveIntegrityError, ContractViolationError
from finintel.ingestion.base import (
    FilingMetadata,
    PeriodType,
    StatementScope,
    StatementType,
)
from finintel.ingestion.file_adapter import FileAdapter
from finintel.ingestion.hasher import (
    _rounded_value,
    _valid_metric_key,
    payload_file_sha256,
)
from finintel.normalization.mapper import (
    FACT_SCHEMA,
    TAXONOMIES,
    UNMAPPED_SCHEMA,
    FilingMappingInput,
    TaxonomyMapper,
)
from finintel.normalization.periods import derive_quarters
from finintel.normalization.regime_resolver import TaxonomyVersion, resolve_regime
from finintel.pipeline.state_store import StateStore

NORMALIZATION_METADATA = "_normalize_meta.json"
DEFAULT_BATCH_ISSUERS = 16
_MAX_STREAM_BATCH_ISSUERS = 8
_STREAM_WORKER_THREADS = 8
_PARQUET_ROW_GROUP_SIZE = 65_536
_MAX_FAST_INT_METRIC = 10**38
ALLOWED_FILING_SETTINGS = {"unit_multiplier", "cash_flow_method", "comparative"}
ALLOWED_COMPARATIVE_KEYS = {
    "value_column",
    "fiscal_year",
    "fiscal_quarter",
    "fiscal_year_start_date",
    "period_type",
    "period_start_date",
    "period_end_date",
}
SIGNATURE_FIELDS: tuple[str, ...] = (
    "canonical_content_sha256",
    "payload_file_sha256",
    "published_at",
    "published_at_basis",
    "effective_available_at",
    "is_filed_late",
    "statement_scope",
    "audit_status",
    "audit_opinion",
    "revision_reason",
    "fiscal_year_start_date",
    "period_start_date",
    "period_end_date",
    "revision_no",
    "statement_type",
    "period_type",
    "fiscal_year",
    "fiscal_quarter",
    "ticker_at_filing",
    "source_provider",
    "connector_name",
    "raw_archive_path",
    "raw_record_locator",
    "provider_attrs_json",
)
_FHSC_MANIFEST_COLUMNS: tuple[str, ...] = (
    "filing_id",
    "issuer_id",
    "ticker_at_filing",
    "source_provider",
    "connector_name",
    "payload_nature",
    "statement_type",
    "statement_scope",
    "period_type",
    "fiscal_year",
    "fiscal_quarter",
    "fiscal_year_start_date",
    "period_start_date",
    "period_end_date",
    "published_at",
    "published_at_basis",
    "effective_available_at",
    "audit_status",
    "audit_opinion",
    "is_filed_late",
    "revision_no",
    "revision_reason",
    "fetched_at",
    "payload_file_sha256",
    "canonical_content_sha256",
    "raw_archive_path",
    "raw_record_locator",
)


class _ArrowMemoryPool(Protocol):
    def release_unused(self) -> None: ...


class _ArrowSchema(Protocol):
    def equals(self, other: object, /) -> bool: ...
    def get_field_index(self, name: str, /) -> int: ...


class _ArrowTable(Protocol):
    @property
    def num_rows(self) -> int: ...
    @property
    def schema(self) -> _ArrowSchema: ...
    def column(self, name: str, /) -> object: ...
    def filter(self, mask: object, /) -> _ArrowTable: ...
    def cast(self, target_schema: _ArrowSchema, /) -> _ArrowTable: ...


class _ArrowModule(Protocol):
    def default_memory_pool(self) -> _ArrowMemoryPool: ...
    def string(self) -> object: ...
    def array(self, obj: Sequence[str], /, *, type: object) -> object: ...


class _ArrowComputeModule(Protocol):
    def is_in(self, values: object, /, *, value_set: object) -> object: ...
    def invert(self, values: object, /) -> object: ...


class _ParquetColumnStats(Protocol):
    @property
    def has_min_max(self) -> bool: ...
    @property
    def min(self) -> object: ...
    @property
    def max(self) -> object: ...


class _ParquetColumnMeta(Protocol):
    @property
    def statistics(self) -> _ParquetColumnStats | None: ...


class _ParquetRowGroupMeta(Protocol):
    @property
    def num_rows(self) -> int: ...
    def column(self, i: int, /) -> _ParquetColumnMeta: ...


class _ParquetFileMeta(Protocol):
    @property
    def num_rows(self) -> int: ...
    @property
    def num_row_groups(self) -> int: ...
    def row_group(self, i: int, /) -> _ParquetRowGroupMeta: ...


class _ParquetFile(Protocol):
    def __enter__(self) -> _ParquetFile: ...
    def __exit__(
        self, exc_type: object, exc_val: object, exc_tb: object, /
    ) -> None: ...
    @property
    def metadata(self) -> _ParquetFileMeta: ...
    @property
    def num_row_groups(self) -> int: ...
    @property
    def schema_arrow(self) -> _ArrowSchema: ...
    def read_row_group(self, i: int, /) -> _ArrowTable: ...
    def read_row_groups(self, row_groups: Sequence[int], /) -> _ArrowTable: ...
    def close(self) -> None: ...


class _ParquetWriter(Protocol):
    def __enter__(self) -> _ParquetWriter: ...
    def __exit__(
        self, exc_type: object, exc_val: object, exc_tb: object, /
    ) -> None: ...
    def write_table(
        self, table: _ArrowTable, /, *, row_group_size: int | None = None
    ) -> None: ...
    def close(self) -> None: ...


class _ParquetModule(Protocol):
    def read_metadata(self, where: Path | str, /) -> _ParquetFileMeta: ...
    def ParquetFile(
        self,
        where: Path | str,
        /,
        *,
        read_dictionary: Sequence[str] | None = None,
    ) -> _ParquetFile: ...
    def ParquetWriter(
        self,
        where: Path | str,
        schema: _ArrowSchema,
        /,
        *,
        compression: str = "lz4",
        use_dictionary: bool | Sequence[str] = False,
        write_statistics: bool | Sequence[str] = True,
        store_schema: bool = True,
    ) -> _ParquetWriter: ...


_PA = cast(_ArrowModule, import_module("pyarrow"))
_PC = cast(_ArrowComputeModule, import_module("pyarrow.compute"))
_PQ = cast(_ParquetModule, import_module("pyarrow.parquet"))
_EMPTY_FACT_ARROW = cast(_ArrowTable, pl.DataFrame(schema=FACT_SCHEMA).to_arrow())
_EMPTY_UNMAPPED_ARROW = cast(
    _ArrowTable, pl.DataFrame(schema=UNMAPPED_SCHEMA).to_arrow()
)
_FACT_ARROW_SCHEMA: _ArrowSchema = _EMPTY_FACT_ARROW.schema
_UNMAPPED_ARROW_SCHEMA: _ArrowSchema = _EMPTY_UNMAPPED_ARROW.schema
_FACT_DICT_COLS: tuple[str, ...] = tuple(
    name for name, dtype in FACT_SCHEMA.items() if dtype == pl.String
)
_UNMAPPED_DICT_COLS: tuple[str, ...] = tuple(
    name
    for name, dtype in UNMAPPED_SCHEMA.items()
    if dtype == pl.String and name != "raw_value"
)


@dataclass(frozen=True, slots=True)
class IncrementalNormalizeResult:
    """Materialized Layer 2 normalization batch and incremental build accounting."""

    facts_path: Path
    unmapped_path: Path
    meta_path: Path
    recomputed_issuers: int
    facts_rows: int
    unmapped_rows: int
    full_rebuild: bool
    taxonomy_sha256: str
    issuer_filings: dict[str, dict[str, str]]


def _period_filter(period: str) -> pl.Expr:
    year, _, quarter = period.partition("-Q")
    selected = pl.col("fiscal_year") == int(year)
    if not quarter:
        return selected & (pl.col("period_type") == "ANNUAL")
    return selected & (
        (pl.col("fiscal_quarter") == int(quarter)).fill_null(False)
        | ((int(quarter) == 2) & (pl.col("period_type") == "SEMI_ANNUAL"))
    )


def build_normalized_batch(
    facts: pl.DataFrame,
    unmapped: pl.DataFrame,
    *,
    period: str | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Combine normalized facts with derived discrete quarters for curated storage."""
    derived = derive_quarters(facts).filter(
        pl.col("derivation_method") != "REPORTED_DIRECTLY"
    )
    if period is not None:
        selected = _period_filter(period)
        restated = pl.col("revision_reason") == "RESTATED_REGIME_CHANGE"
        reported_ids = pl.concat(
            [
                facts.filter(selected & ~restated).select("filing_id"),
                unmapped.filter(selected & ~restated).select("filing_id"),
            ]
        ).unique()
        include = selected | (
            restated & pl.col("filing_id").is_in(reported_ids["filing_id"].implode())
        )
        facts = facts.filter(include)
        unmapped = unmapped.filter(include)
        derived = pl.concat(
            [
                derived.filter(selected),
                derived.join(reported_ids, on="filing_id", how="semi"),
            ]
        ).unique(maintain_order=True)
    q = pl.col("fiscal_quarter")
    days = pl.col("period_days")
    start = pl.col("period_start_date")
    end = pl.col("period_end_date")
    fiscal_start = pl.col("fiscal_year_start_date")
    discrete_quarter_flow = (
        q.is_between(1, 4)
        & days.is_between(80, 100)
        & (days == ((end - start).dt.total_days() + 1).cast(pl.Int32))
        & (start - fiscal_start).dt.total_days().is_between((q - 1) * 80, (q - 1) * 100)
    )
    retained_facts = facts.filter(
        ~(
            (pl.col("value_type") == "FLOW")
            & (pl.col("period_type") == "QUARTER")
            & ~discrete_quarter_flow
        )
    )
    normalized = pl.concat([retained_facts, derived])
    deduped_unmapped = unmapped.unique(
        subset=["filing_id", "period_end_date", "raw_metric_key"],
        maintain_order=True,
    )
    return normalized, deduped_unmapped


def _fields(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ContractViolationError("Expected a mapping in normalization inputs.")
    return dict(value)


def _text(fields: Mapping[str, object], key: str) -> str:
    value = fields.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractViolationError(f"Normalization requires {key}.")
    return value


def _integer(fields: Mapping[str, object], key: str, default: int | None = None) -> int:
    value = fields.get(key, default)
    if type(value) is not int:
        raise ContractViolationError(f"Normalization requires integer {key}.")
    return value


def _decode(content: str | bytes) -> object:
    try:
        return json.loads(content)
    except (ValueError, UnicodeError):
        raise ContractViolationError("Malformed JSON in the raw archive.") from None


def _canonical_json_value(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(k): _canonical_json_value(v)
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, set):
        return sorted((_canonical_json_value(item) for item in value), key=str)
    return value


def compute_taxonomy_sha256(
    config_dir: Path, filing_settings: Mapping[str, object]
) -> str:
    """Hash all corporate taxonomy YAML files and canonical filing settings."""
    hasher = hashlib.sha256()
    yaml_files = [
        config_dir / "metric_definitions.yaml",
        *(config_dir / "taxonomy" / filename for filename, _ in TAXONOMIES.values()),
    ]
    for path in yaml_files:
        try:
            raw = path.read_bytes()
        except OSError:
            raise ContractViolationError(
                f"Unable to read taxonomy file {path.name}."
            ) from None
        label = path.relative_to(config_dir).as_posix().encode("utf-8")
        hasher.update(len(label).to_bytes(4, "big"))
        hasher.update(label)
        hasher.update(len(raw).to_bytes(8, "big"))
        hasher.update(raw)
    canonical_settings = json.dumps(
        _canonical_json_value(filing_settings),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    hasher.update(b"filing_settings:")
    hasher.update(len(canonical_settings).to_bytes(8, "big"))
    hasher.update(canonical_settings)
    return hasher.hexdigest()


@lru_cache(maxsize=4)
def _cached_mapper_by_digest(
    resolved_config_dir: str, yaml_digest: str
) -> TaxonomyMapper:
    _ = yaml_digest
    return TaxonomyMapper(Path(resolved_config_dir))


def _get_cached_mapper(config_dir: Path) -> TaxonomyMapper:
    resolved = config_dir.resolve()
    yaml_digest = compute_taxonomy_sha256(resolved, {})
    return _cached_mapper_by_digest(str(resolved), yaml_digest)


_DEFAULT_REPO_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
if (_DEFAULT_REPO_CONFIG_DIR / "metric_definitions.yaml").is_file():
    try:
        _get_cached_mapper(_DEFAULT_REPO_CONFIG_DIR)
    except Exception:
        pass


def _filing_signature(row: Mapping[str, object]) -> str:
    payload = [row.get(field) for field in SIGNATURE_FIELDS]
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class _PayloadCache:
    """Per-run payload and FHSC JSON cache keyed by (raw_root, relative_path, sha256)."""

    def __init__(self, raw_root: Path, *, maxsize: int = 32) -> None:
        self._root = raw_root.resolve()
        self._root_key = str(self._root)

        @lru_cache(maxsize=maxsize)
        def _read_bytes(
            root_key: str, relative: str, digest: str
        ) -> tuple[Path, bytes]:
            root = Path(root_key)
            path = (root / relative).resolve()
            if not path.is_relative_to(root):
                raise ContractViolationError("Raw filing path is outside its archive.")
            try:
                content = path.read_bytes()
            except OSError:
                raise ContractViolationError(
                    "Raw filing is missing or unreadable."
                ) from None
            if payload_file_sha256(content) != digest:
                raise ArchiveIntegrityError(
                    "Raw filing bytes differ from the manifest hash."
                )
            return path, content

        @lru_cache(maxsize=maxsize)
        def _read_fhsc_json(
            root_key: str, relative: str, digest: str
        ) -> dict[str, object]:
            _, content = _read_bytes(root_key, relative, digest)
            return _fields(_decode(content))

        self._read_bytes_cached = _read_bytes
        self._read_fhsc_json_cached = _read_fhsc_json

    def read_bytes(self, relative: str, digest: str) -> tuple[Path, bytes]:
        return self._read_bytes_cached(self._root_key, relative, digest)

    def read_fhsc_json(self, relative: str, digest: str) -> dict[str, object]:
        return self._read_fhsc_json_cached(self._root_key, relative, digest)

    def clear(self) -> None:
        """Evict cached raw bytes and decoded FHSC JSON payloads after a batch completes."""
        self._read_bytes_cached.cache_clear()
        self._read_fhsc_json_cached.cache_clear()

    def prefetch_fhsc_chunk(
        self,
        pairs: Sequence[tuple[str, str]],
        pool: ThreadPoolExecutor,
    ) -> None:
        """Warm up to maxsize unique FHSC payloads concurrently while preserving error order."""
        if len(pairs) <= 1:
            return

        def _warm(pair: tuple[str, str]) -> None:
            relative, digest = pair
            try:
                self._read_fhsc_json_cached(self._root_key, relative, digest)
            except (ArchiveIntegrityError, ContractViolationError):
                # Re-raised deterministically in manifest order by _extract_filing_inputs.
                return

        list(pool.map(_warm, pairs))


def _fhsc_record_from_body(
    body: Mapping[str, object],
    locator: str,
    metadata: FilingMetadata,
) -> dict[str, object]:
    match = re.fullmatch(r"/data/(0|[1-9][0-9]{0,5})", locator)
    records = body.get("data")
    if (
        match is None
        or body.get("error_code") != "0"
        or not isinstance(records, list)
        or int(match[1]) >= len(records)
    ):
        raise ContractViolationError("Invalid FHSC raw record locator or payload.")
    record = _fields(records[int(match[1])])
    year, quarter = record.pop("year", None), record.pop("quarter", None)
    if (
        type(year) is not int
        or year != metadata.fiscal_year
        or (quarter is not None and type(quarter) is not int)
        or quarter != metadata.fiscal_quarter
    ):
        raise ContractViolationError("FHSC raw period differs from its manifest entry.")
    return record


def _file_metrics(
    content: bytes,
    path: Path,
    metadata: FilingMetadata,
    source: str,
    attrs: Mapping[str, object],
    value_column: str,
) -> dict[str, object]:
    sheet = _text(attrs, "sheet") if attrs.get("sheet") is not None else None
    return FileAdapter(
        path,
        metadata,
        source_provider=source,
        metric_column=_text(attrs, "metric_column"),
        value_column=value_column,
        sheet=sheet,
        header_row=_integer(attrs, "header_row"),
    ).read_metrics(content)


def _validate_restatement_period(
    filing: FilingMetadata, comparative_period: FilingMetadata
) -> None:
    """Enforce the exact TT99 comparative-period contract from restatement.py."""
    is_opening_balance = comparative_period.period_end_date.toordinal() + 1 == (
        filing.fiscal_year_start_date.toordinal()
    )
    filing_days = (filing.period_end_date - filing.period_start_date).days + 1
    comparative_days = (
        comparative_period.period_end_date - comparative_period.period_start_date
    ).days + 1
    filing_ytd = filing.period_start_date == filing.fiscal_year_start_date
    comparative_ytd = (
        comparative_period.period_start_date
        == comparative_period.fiscal_year_start_date
    )
    comparative_q = comparative_period.fiscal_quarter or 4
    valid_flow_days = (
        350 <= comparative_days <= 380
        if comparative_period.period_type == PeriodType.ANNUAL
        else 160 <= comparative_days <= 200
        if comparative_period.period_type == PeriodType.SEMI_ANNUAL
        else (
            comparative_q * 80 <= comparative_days <= comparative_q * 100
            if comparative_ytd
            else 80 <= comparative_days <= 100
        )
    )
    if (
        resolve_regime(filing.fiscal_year_start_date) != TaxonomyVersion.TT99
        or resolve_regime(comparative_period.fiscal_year_start_date)
        != TaxonomyVersion.TT200
        or comparative_period.period_end_date >= filing.fiscal_year_start_date
        or comparative_period.fiscal_year != filing.fiscal_year - 1
        or comparative_period.issuer_id != filing.issuer_id
        or comparative_period.statement_scope != filing.statement_scope
        or comparative_period.statement_type != filing.statement_type
        or (
            filing.statement_type == StatementType.BALANCE_SHEET
            and (
                not is_opening_balance
                or comparative_period.period_type != PeriodType.ANNUAL
            )
        )
        or (
            filing.statement_type != StatementType.BALANCE_SHEET
            and (
                comparative_period.period_type != filing.period_type
                or comparative_period.fiscal_quarter != filing.fiscal_quarter
                or comparative_ytd != filing_ytd
                or abs(comparative_days - filing_days) > 10
                or not valid_flow_days
            )
        )
    ):
        raise ContractViolationError(
            "Comparative period does not match the TT99 filing."
        )


@lru_cache(maxsize=4096)
def _fast_key_and_json(key: object) -> tuple[str, str]:
    valid = _valid_metric_key(key)
    try:
        encoded_json = json.dumps(valid, ensure_ascii=False)
        encoded_json.encode("utf-8")
    except UnicodeEncodeError:
        raise ContractViolationError("Invalid UTF-8 in raw metric code.") from None
    return valid, encoded_json


def _fast_canonical_content_sha256(
    metrics: Mapping[str, object],
    scope: StatementScope,
    period_end_date: date,
) -> str:
    """Compute bit-exact canonical_content_sha256 with cached key validation and integer fast-path."""
    mid = f',"{scope}","{period_end_date.isoformat()}","'
    validated = [
        (_fast_key_and_json(key), value)
        for key, value in metrics.items()
        if value is not None
    ]
    validated.sort(key=lambda item: item[0][0])
    parts = [
        f'[{k_json}{mid}{value}.0000"]'
        if type(value) is int and -_MAX_FAST_INT_METRIC < value < _MAX_FAST_INT_METRIC
        else f'[{k_json}{mid}{_rounded_value(value)}"]'
        for (_, k_json), value in validated
    ]
    canonical = f"[{','.join(parts)}]".encode("utf-8")
    return payload_file_sha256(canonical)


def _extract_filing_inputs(
    row: Mapping[str, object],
    cache: _PayloadCache,
    settings: Mapping[str, object],
) -> list[FilingMappingInput]:
    metadata = FilingMetadata.from_mapping(row)
    filing_id, source = _text(row, "filing_id"), _text(row, "source_provider")
    if set(settings) - ALLOWED_FILING_SETTINGS:
        raise ContractViolationError("Unsupported normalization filing settings.")
    comparison = settings.get("comparative")
    unit_multiplier = _integer(
        settings, "unit_multiplier", 1 if source == "fhsc" else None
    )
    cash_flow_method = (
        _text(settings, "cash_flow_method") if "cash_flow_method" in settings else None
    )
    rel_path = _text(row, "raw_archive_path")
    file_digest = _text(row, "payload_file_sha256")
    attrs: dict[str, object] = {}
    path: Path | None = None
    content: bytes | None = None
    if source == "fhsc":
        if unit_multiplier != 1 or comparison is not None:
            raise ContractViolationError(
                "FHSC archive records use VND and a single verified period."
            )
        body = cache.read_fhsc_json(rel_path, file_digest)
        metrics = _fhsc_record_from_body(
            body, _text(row, "raw_record_locator"), metadata
        )
    elif row.get("connector_name") == "file_adapter_v1":
        path, content = cache.read_bytes(rel_path, file_digest)
        attrs = _fields(_decode(_text(row, "provider_attrs_json")))
        metrics = _file_metrics(
            content, path, metadata, source, attrs, _text(attrs, "value_column")
        )
    else:
        raise ContractViolationError("Unsupported raw filing connector.")
    if _fast_canonical_content_sha256(
        metrics, metadata.statement_scope, metadata.period_end_date
    ) != row.get("canonical_content_sha256"):
        raise ContractViolationError(
            "Raw metrics differ from the filing manifest hash."
        )
    revision_no = _integer(row, "revision_no")
    inputs = [
        FilingMappingInput(
            filing_id=filing_id,
            source_provider=source,
            revision_no=revision_no,
            unit_multiplier=unit_multiplier,
            cash_flow_method=cash_flow_method,
            period=metadata,
            filing=metadata,
            taxonomy=resolve_regime(metadata.fiscal_year_start_date),
            metrics=metrics,
        )
    ]
    if comparison is not None:
        assert path is not None and content is not None
        fields = _fields(comparison)
        if set(fields) - ALLOWED_COMPARATIVE_KEYS:
            raise ContractViolationError("Unsupported comparative period settings.")
        value_column = _text(fields, "value_column")
        if value_column == attrs.get("value_column"):
            raise ContractViolationError(
                "Comparative amounts require a separate source column."
            )
        comparative_period = FilingMetadata.from_mapping(
            {
                "issuer_id": metadata.issuer_id,
                "ticker_at_filing": metadata.ticker_at_filing,
                "statement_type": metadata.statement_type,
                "statement_scope": metadata.statement_scope,
                **{
                    key: fields.get(key)
                    for key in (
                        "fiscal_year",
                        "fiscal_quarter",
                        "fiscal_year_start_date",
                        "period_type",
                        "period_start_date",
                        "period_end_date",
                    )
                },
            }
        )
        _validate_restatement_period(metadata, comparative_period)
        comp_metrics = _file_metrics(
            content, path, metadata, source, attrs, value_column
        )
        inputs.append(
            FilingMappingInput(
                filing_id=filing_id,
                source_provider=source,
                revision_no=revision_no,
                unit_multiplier=unit_multiplier,
                cash_flow_method=cash_flow_method,
                period=comparative_period,
                filing=metadata,
                taxonomy=TaxonomyVersion.TT99,
                metrics=comp_metrics,
            )
        )
    return inputs


def _load_manifest_rows(
    state: StateStore, *, source: str, tickers: tuple[str, ...]
) -> list[dict[str, object]]:
    if source not in {"fhsc", "file"}:
        raise ContractViolationError("Unsupported normalization source.")
    conditions = [
        "source_provider = 'fhsc'" if source == "fhsc" else "source_provider != 'fhsc'"
    ]
    if tickers:
        placeholders = ", ".join("?" for _ in tickers)
        conditions.append(f"ticker_at_filing IN ({placeholders})")
    try:
        with state.connection() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'filings_manifest'"
                ).fetchone()
                is None
            ):
                raise ContractViolationError(
                    "No filing manifest is available for normalization."
                )
            rows = conn.execute(
                f"SELECT * FROM filings_manifest WHERE {' AND '.join(conditions)} "
                "ORDER BY issuer_id, raw_archive_path, statement_type, "
                "period_end_date, revision_no, filing_id",
                tickers,
            ).fetchall()
    except sqlite3.Error:
        raise ContractViolationError(
            "Unable to read the raw filing manifest."
        ) from None
    return [dict(entry) for entry in rows]


def _iter_issuer_batches(
    rows: Sequence[dict[str, object]], batch_issuers: int
) -> Iterator[list[dict[str, object]]]:
    if type(batch_issuers) is not int or batch_issuers <= 0:
        raise ContractViolationError("batch_issuers must be a positive integer.")
    current_batch: list[dict[str, object]] = []
    issuer_count = 0
    for _, group in groupby(rows, key=lambda item: _text(item, "issuer_id")):
        current_batch.extend(group)
        issuer_count += 1
        if issuer_count >= batch_issuers:
            yield current_batch
            current_batch = []
            issuer_count = 0
    if current_batch:
        yield current_batch


def _extract_batch_inputs(
    issuer_batch: Sequence[dict[str, object]],
    cache: _PayloadCache,
    configured: Mapping[str, object],
    pool: ThreadPoolExecutor | None = None,
) -> list[FilingMappingInput]:
    """Extract mapping inputs for an issuer batch with bounded 32-file I/O prefetch."""
    inputs: list[FilingMappingInput] = []
    if pool is None or len(issuer_batch) <= 1:
        for row in issuer_batch:
            settings = _fields(configured.get(_text(row, "filing_id"), {}))
            inputs.extend(_extract_filing_inputs(row, cache, settings))
        return inputs

    # Process consecutive unique-file windows of size <= 32 to align with cache maxsize.
    chunk_rows: list[dict[str, object]] = []
    chunk_pairs: list[tuple[str, str]] = []
    chunk_pair_set: set[tuple[str, str]] = set()

    def _flush() -> None:
        if not chunk_rows:
            return
        cache.prefetch_fhsc_chunk(chunk_pairs, pool)
        for item in chunk_rows:
            settings = _fields(configured.get(_text(item, "filing_id"), {}))
            inputs.extend(_extract_filing_inputs(item, cache, settings))
        chunk_rows.clear()
        chunk_pairs.clear()
        chunk_pair_set.clear()

    for row in issuer_batch:
        src = row.get("source_provider")
        rel = row.get("raw_archive_path")
        dig = row.get("payload_file_sha256")
        if src == "fhsc" and isinstance(rel, str) and isinstance(dig, str):
            pair = (rel, dig)
            if pair not in chunk_pair_set:
                if len(chunk_pairs) >= 32:
                    _flush()
                chunk_pair_set.add(pair)
                chunk_pairs.append(pair)
        chunk_rows.append(row)
    _flush()
    return inputs


def normalize_archive(
    state: StateStore,
    raw_root: Path,
    mapper: TaxonomyMapper,
    *,
    source: str,
    tickers: tuple[str, ...] = (),
    filing_settings: Mapping[str, object] | None = None,
    batch_issuers: int = DEFAULT_BATCH_ISSUERS,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Return all mapped filing revisions and unmapped metrics for the selected issuers."""
    rows = _load_manifest_rows(state, source=source, tickers=tickers)
    if not rows:
        return (
            pl.DataFrame(schema=FACT_SCHEMA),
            pl.DataFrame(schema=UNMAPPED_SCHEMA),
        )
    cache = _PayloadCache(raw_root, maxsize=32)
    configured = filing_settings or {}
    facts: list[pl.DataFrame] = []
    unmapped: list[pl.DataFrame] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for issuer_batch in _iter_issuer_batches(rows, batch_issuers):
            inputs = _extract_batch_inputs(issuer_batch, cache, configured, pool)
            batch_facts, batch_unmapped = mapper.map_batch(inputs)
            facts.append(batch_facts)
            unmapped.append(batch_unmapped)
    return pl.concat(facts).rechunk(), pl.concat(unmapped).rechunk()


def _link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _parquet_row_count(path: Path) -> int:
    return int(_PQ.read_metadata(path).num_rows)


def _load_manifest_signatures(
    state: StateStore, *, source: str, tickers: tuple[str, ...]
) -> dict[str, dict[str, str]]:
    """Compute per-issuer filing signature dicts directly via SQLite without materializing full row dicts."""
    if source not in {"fhsc", "file"}:
        raise ContractViolationError("Unsupported normalization source.")
    conditions = [
        "source_provider = 'fhsc'" if source == "fhsc" else "source_provider != 'fhsc'"
    ]
    if tickers:
        placeholders = ", ".join("?" for _ in tickers)
        conditions.append(f"ticker_at_filing IN ({placeholders})")
    sig_cols = ", ".join(SIGNATURE_FIELDS)
    cur_issuer_filings: dict[str, dict[str, str]] = {}
    sha256 = hashlib.sha256
    try:
        with state.connection() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'filings_manifest'"
                ).fetchone()
                is None
            ):
                raise ContractViolationError(
                    "No filing manifest is available for normalization."
                )
            old_factory = conn.row_factory
            conn.row_factory = None
            try:
                cursor = conn.execute(
                    f"SELECT issuer_id, filing_id, CAST(json_array({sig_cols}) AS BLOB) "
                    f"FROM filings_manifest WHERE {' AND '.join(conditions)}",
                    tickers,
                )
                last_iss_id: str | None = None
                cur_filings: dict[str, str] = {}
                for iss_id, fil_id, payload_blob in cursor:
                    if not isinstance(iss_id, str) or not iss_id.strip():
                        raise ContractViolationError(
                            "Normalization requires issuer_id."
                        )
                    if not isinstance(fil_id, str) or not fil_id.strip():
                        raise ContractViolationError(
                            "Normalization requires filing_id."
                        )
                    if iss_id != last_iss_id:
                        cur_filings = cur_issuer_filings.setdefault(iss_id, {})
                        last_iss_id = iss_id
                    raw_bytes = (
                        payload_blob
                        if isinstance(payload_blob, bytes)
                        else str(payload_blob).encode("utf-8")
                    )
                    cur_filings[fil_id] = sha256(raw_bytes).hexdigest()
            finally:
                conn.row_factory = old_factory
    except sqlite3.Error:
        raise ContractViolationError(
            "Unable to read the raw filing manifest."
        ) from None
    return {iss_id: cur_issuer_filings[iss_id] for iss_id in sorted(cur_issuer_filings)}


def _fetch_issuer_chunk_rows(
    conn: sqlite3.Connection,
    *,
    source: str,
    tickers: tuple[str, ...],
    issuer_chunk: Sequence[str],
) -> list[dict[str, object]]:
    """Fetch manifest rows for a bounded chunk of issuers, omitting unused 31MB provider_attrs_json for FHSC."""
    if not issuer_chunk:
        return []
    cols = (
        _FHSC_MANIFEST_COLUMNS
        if source == "fhsc"
        else (*_FHSC_MANIFEST_COLUMNS, "provider_attrs_json")
    )
    conditions = [
        "source_provider = 'fhsc'" if source == "fhsc" else "source_provider != 'fhsc'",
        f"issuer_id IN ({', '.join('?' for _ in issuer_chunk)})",
    ]
    params: list[str] = [*issuer_chunk]
    if tickers:
        conditions.append(f"ticker_at_filing IN ({', '.join('?' for _ in tickers)})")
        params.extend(tickers)
    cols_sql = ", ".join(cols)
    try:
        raw_rows = conn.execute(
            f"SELECT {cols_sql} FROM filings_manifest "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY issuer_id, raw_archive_path, statement_type, "
            "period_end_date, revision_no, filing_id",
            params,
        ).fetchall()
    except sqlite3.Error:
        raise ContractViolationError(
            "Unable to read the raw filing manifest."
        ) from None
    return [dict(zip(cols, entry, strict=True)) for entry in raw_rows]


def _stream_kept_row_groups_from_pf(
    pf: _ParquetFile,
    writer: _ParquetWriter,
    dirty_sorted: Sequence[str],
    dirty_arr: object,
    prefetch_pool: ThreadPoolExecutor | None = None,
) -> int:
    """Stream non-dirty rows from an open ParquetFile one row group at a time using min/max stats."""
    kept_rows = 0
    file_meta = pf.metadata
    schema_arrow = pf.schema_arrow
    iss_col_idx = schema_arrow.get_field_index("issuer_id")
    num_rgs = pf.num_row_groups
    active_rgs: list[tuple[int, bool]] = []
    for rg_idx in range(num_rgs):
        rg_meta = file_meta.row_group(rg_idx)
        if rg_meta.num_rows == 0:
            continue
        needs_filter = True
        if dirty_sorted and iss_col_idx >= 0:
            col_stats = rg_meta.column(iss_col_idx).statistics
            if col_stats is not None and col_stats.has_min_max:
                cmin_raw, cmax_raw = col_stats.min, col_stats.max
                cmin = (
                    cmin_raw.decode("utf-8")
                    if isinstance(cmin_raw, bytes)
                    else str(cmin_raw)
                )
                cmax = (
                    cmax_raw.decode("utf-8")
                    if isinstance(cmax_raw, bytes)
                    else str(cmax_raw)
                )
                needs_filter = any(cmin <= d <= cmax for d in dirty_sorted)
        active_rgs.append((rg_idx, needs_filter))

    if not active_rgs:
        return 0

    next_fut = (
        prefetch_pool.submit(pf.read_row_group, active_rgs[0][0])
        if prefetch_pool is not None
        else None
    )
    for pos, (rg_idx, needs_filter) in enumerate(active_rgs):
        if next_fut is not None:
            tbl = next_fut.result()
            next_fut = (
                prefetch_pool.submit(pf.read_row_group, active_rgs[pos + 1][0])
                if prefetch_pool is not None and pos + 1 < len(active_rgs)
                else None
            )
        else:
            tbl = pf.read_row_group(rg_idx)
        if needs_filter and dirty_sorted:
            mask = _PC.invert(_PC.is_in(tbl.column("issuer_id"), value_set=dirty_arr))
            tbl = tbl.filter(mask)
        if tbl.num_rows > 0:
            writer.write_table(
                tbl,
                row_group_size=min(max(tbl.num_rows, 1), _PARQUET_ROW_GROUP_SIZE),
            )
            kept_rows += tbl.num_rows
        del tbl
        if pos % 8 == 7:
            _PA.default_memory_pool().release_unused()
    return kept_rows


def _write_table_matching_schema(
    writer: _ParquetWriter,
    writer_schema: _ArrowSchema,
    table: _ArrowTable,
) -> None:
    if not table.schema.equals(writer_schema):
        table = table.cast(writer_schema)
    writer.write_table(
        table,
        row_group_size=min(max(table.num_rows, 1), _PARQUET_ROW_GROUP_SIZE),
    )


def _parse_previous_meta(
    previous_batch_dir: Path | None,
    *,
    source: str,
    taxonomy_sha256: str,
) -> dict[str, dict[str, str]] | None:
    if previous_batch_dir is None:
        return None
    facts_path = previous_batch_dir / "financial_facts.parquet"
    unmapped_path = previous_batch_dir / "unmapped_metrics.parquet"
    meta_path = previous_batch_dir / NORMALIZATION_METADATA
    if not (facts_path.is_file() and unmapped_path.is_file() and meta_path.is_file()):
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(meta, dict)
        or meta.get("source") != source
        or meta.get("full_history") is not True
        or meta.get("taxonomy_sha256") != taxonomy_sha256
    ):
        return None
    raw_issuers = meta.get("issuer_filings")
    if not isinstance(raw_issuers, dict):
        return None
    parsed: dict[str, dict[str, str]] = {}
    for issuer_id, filings in raw_issuers.items():
        if not isinstance(issuer_id, str) or not isinstance(filings, dict):
            return None
        parsed_filings: dict[str, str] = {}
        for filing_id, digest in filings.items():
            if not isinstance(filing_id, str) or not isinstance(digest, str):
                return None
            parsed_filings[filing_id] = digest
        parsed[issuer_id] = parsed_filings
    return parsed


def _write_meta(
    path: Path,
    *,
    source: str,
    full_history: bool,
    taxonomy_sha256: str,
    recomputed_issuers: int,
    issuer_filings: dict[str, dict[str, str]],
) -> None:
    path.write_text(
        json.dumps(
            {
                "source": source,
                "full_history": full_history,
                "taxonomy_sha256": taxonomy_sha256,
                "recomputed_issuers": recomputed_issuers,
                "issuer_filings": issuer_filings,
            },
            sort_keys=True,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _normalize_issuer_chunk(
    conn: sqlite3.Connection,
    *,
    source: str,
    tickers: tuple[str, ...],
    issuer_chunk: Sequence[str],
    cache: _PayloadCache,
    configured: Mapping[str, object],
    active_mapper: TaxonomyMapper,
    period: str | None,
    pool: ThreadPoolExecutor | None,
) -> tuple[_ArrowTable, _ArrowTable, int, int]:
    """Fetch, extract, map, and derive quarters for one bounded issuer chunk."""
    batch_rows = _fetch_issuer_chunk_rows(
        conn, source=source, tickers=tickers, issuer_chunk=issuer_chunk
    )
    inputs = _extract_batch_inputs(batch_rows, cache, configured, pool)
    del batch_rows
    cache.clear()
    mapped_f, mapped_u = active_mapper.map_batch(inputs)
    del inputs
    norm_f, norm_u = build_normalized_batch(mapped_f, mapped_u, period=period)
    del mapped_f, mapped_u
    if norm_f.height > 1:
        norm_f = norm_f.sort("issuer_id", maintain_order=True)
    f_height = norm_f.height
    u_height = norm_u.height
    f_arrow = (
        cast(_ArrowTable, norm_f.to_arrow()) if f_height > 0 else _EMPTY_FACT_ARROW
    )
    u_arrow = (
        cast(_ArrowTable, norm_u.to_arrow()) if u_height > 0 else _EMPTY_UNMAPPED_ARROW
    )
    del norm_f, norm_u
    return f_arrow, u_arrow, f_height, u_height


def normalize_archive_incremental(
    state: StateStore,
    raw_root: Path,
    config_dir: Path,
    output_dir: Path,
    *,
    source: str,
    tickers: tuple[str, ...] = (),
    period: str | None = None,
    filing_settings: Mapping[str, object] | None = None,
    previous_batch_dir: Path | None = None,
    batch_issuers: int = DEFAULT_BATCH_ISSUERS,
    mapper: TaxonomyMapper | None = None,
) -> IncrementalNormalizeResult:
    """Materialize a curated Layer 2 batch using issuer-level incremental early cutoff."""
    if type(batch_issuers) is not int or batch_issuers <= 0:
        raise ContractViolationError("batch_issuers must be a positive integer.")
    configured = filing_settings or {}
    full_history = not tickers and period is None
    chunk_size = min(batch_issuers, _MAX_STREAM_BATCH_ISSUERS)

    was_gc_enabled = gc.isenabled()
    gc.disable()
    try:
        taxonomy_sha256 = compute_taxonomy_sha256(config_dir, configured)
        if full_history and previous_batch_dir is not None:
            with ThreadPoolExecutor(max_workers=1) as meta_pool:
                prev_fut = meta_pool.submit(
                    _parse_previous_meta,
                    previous_batch_dir,
                    source=source,
                    taxonomy_sha256=taxonomy_sha256,
                )
                cur_issuer_filings = _load_manifest_signatures(
                    state, source=source, tickers=tickers
                )
                prev_issuers = prev_fut.result()
        else:
            cur_issuer_filings = _load_manifest_signatures(
                state, source=source, tickers=tickers
            )
            prev_issuers = None

        output_dir.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            prefix=".normalize-", dir=output_dir.parent
        ) as temporary:
            staging = Path(temporary)
            facts_dst = staging / "financial_facts.parquet"
            unmapped_dst = staging / "unmapped_metrics.parquet"
            meta_dst = staging / NORMALIZATION_METADATA
            meta_written = False

            if prev_issuers is not None:
                assert previous_batch_dir is not None
                dirty_issuers = {
                    issuer_id
                    for issuer_id in (prev_issuers.keys() | cur_issuer_filings.keys())
                    if prev_issuers.get(issuer_id) != cur_issuer_filings.get(issuer_id)
                }
                if not dirty_issuers:
                    if not cur_issuer_filings:
                        raise ContractViolationError(
                            "No archived filing matched the requested scope."
                        )
                    _link_or_copy(
                        previous_batch_dir / "financial_facts.parquet", facts_dst
                    )
                    _link_or_copy(
                        previous_batch_dir / "unmapped_metrics.parquet", unmapped_dst
                    )
                    facts_rows = _parquet_row_count(facts_dst)
                    unmapped_rows = _parquet_row_count(unmapped_dst)
                    if facts_rows == 0 and unmapped_rows == 0:
                        raise ContractViolationError(
                            "No archived filing matched the requested scope."
                        )
                    recomputed_issuers = 0
                    full_rebuild = False
                else:
                    active_mapper = mapper or _get_cached_mapper(config_dir)
                    cache = _PayloadCache(raw_root, maxsize=32)
                    dirty_sorted = sorted(dirty_issuers)
                    dirty_arr = _PA.array(dirty_sorted, type=_PA.string())
                    active_dirty = [
                        iss_id
                        for iss_id in cur_issuer_filings
                        if iss_id in dirty_issuers
                    ]
                    prev_facts_path = previous_batch_dir / "financial_facts.parquet"
                    prev_unmapped_path = previous_batch_dir / "unmapped_metrics.parquet"
                    recomputed_issuers = len(dirty_issuers)
                    full_rebuild = False

                    with (
                        state.connection() as conn,
                        _PQ.ParquetFile(
                            prev_facts_path, read_dictionary=_FACT_DICT_COLS
                        ) as pf_f,
                        _PQ.ParquetFile(
                            prev_unmapped_path, read_dictionary=_UNMAPPED_DICT_COLS
                        ) as pf_u,
                        ThreadPoolExecutor(max_workers=_STREAM_WORKER_THREADS) as pool,
                    ):
                        f_w_schema = pf_f.schema_arrow
                        u_w_schema = pf_u.schema_arrow
                        with (
                            _PQ.ParquetWriter(
                                facts_dst,
                                f_w_schema,
                                compression="lz4",
                                use_dictionary=_FACT_DICT_COLS,
                                write_statistics=["issuer_id"],
                                store_schema=False,
                            ) as f_writer,
                            _PQ.ParquetWriter(
                                unmapped_dst,
                                u_w_schema,
                                compression="lz4",
                                use_dictionary=_UNMAPPED_DICT_COLS,
                                write_statistics=["issuer_id"],
                                store_schema=False,
                            ) as u_writer,
                        ):
                            old_factory = conn.row_factory
                            conn.row_factory = None
                            try:
                                f_kept_fut = pool.submit(
                                    _stream_kept_row_groups_from_pf,
                                    pf_f,
                                    f_writer,
                                    dirty_sorted,
                                    dirty_arr,
                                    pool,
                                )
                                u_kept_fut = pool.submit(
                                    _stream_kept_row_groups_from_pf,
                                    pf_u,
                                    u_writer,
                                    dirty_sorted,
                                    dirty_arr,
                                    pool,
                                )
                                if active_dirty:
                                    first_chunk = active_dirty[:chunk_size]
                                    f_first, u_first, f_first_rows, u_first_rows = (
                                        _normalize_issuer_chunk(
                                            conn,
                                            source=source,
                                            tickers=tickers,
                                            issuer_chunk=first_chunk,
                                            cache=cache,
                                            configured=configured,
                                            active_mapper=active_mapper,
                                            period=None,
                                            pool=None,
                                        )
                                    )
                                else:
                                    f_first = _EMPTY_FACT_ARROW
                                    u_first = _EMPTY_UNMAPPED_ARROW
                                    f_first_rows = 0
                                    u_first_rows = 0

                                _write_meta(
                                    meta_dst,
                                    source=source,
                                    full_history=full_history,
                                    taxonomy_sha256=taxonomy_sha256,
                                    recomputed_issuers=recomputed_issuers,
                                    issuer_filings=cur_issuer_filings,
                                )
                                meta_written = True

                                facts_rows = f_kept_fut.result()
                                unmapped_rows = u_kept_fut.result()

                                if f_first_rows > 0:
                                    _write_table_matching_schema(
                                        f_writer, f_w_schema, f_first
                                    )
                                    facts_rows += f_first_rows
                                if u_first_rows > 0:
                                    _write_table_matching_schema(
                                        u_writer, u_w_schema, u_first
                                    )
                                    unmapped_rows += u_first_rows
                                del f_first, u_first

                                for b_idx, idx in enumerate(
                                    range(chunk_size, len(active_dirty), chunk_size)
                                ):
                                    iss_chunk = active_dirty[idx : idx + chunk_size]
                                    f_new, u_new, f_new_rows, u_new_rows = (
                                        _normalize_issuer_chunk(
                                            conn,
                                            source=source,
                                            tickers=tickers,
                                            issuer_chunk=iss_chunk,
                                            cache=cache,
                                            configured=configured,
                                            active_mapper=active_mapper,
                                            period=None,
                                            pool=pool,
                                        )
                                    )
                                    if f_new_rows > 0:
                                        _write_table_matching_schema(
                                            f_writer, f_w_schema, f_new
                                        )
                                        facts_rows += f_new_rows
                                    if u_new_rows > 0:
                                        _write_table_matching_schema(
                                            u_writer, u_w_schema, u_new
                                        )
                                        unmapped_rows += u_new_rows
                                    del f_new, u_new
                                    if b_idx % 5 == 4:
                                        gc.collect()
                                    else:
                                        gc.collect(1)
                                    _PA.default_memory_pool().release_unused()
                            finally:
                                conn.row_factory = old_factory

                            if facts_rows == 0:
                                _write_table_matching_schema(
                                    f_writer, f_w_schema, _EMPTY_FACT_ARROW
                                )
                            if unmapped_rows == 0:
                                _write_table_matching_schema(
                                    u_writer, u_w_schema, _EMPTY_UNMAPPED_ARROW
                                )

                    _PA.default_memory_pool().release_unused()
                    if facts_rows == 0 and unmapped_rows == 0:
                        raise ContractViolationError(
                            "No archived filing matched the requested scope."
                        )
            else:
                if not cur_issuer_filings:
                    raise ContractViolationError(
                        "No archived filing matched the requested scope."
                    )
                active_mapper = mapper or _get_cached_mapper(config_dir)
                cache = _PayloadCache(raw_root, maxsize=32)
                all_issuers = list(cur_issuer_filings.keys())
                facts_rows = 0
                unmapped_rows = 0

                with (
                    state.connection() as conn,
                    _PQ.ParquetWriter(
                        facts_dst,
                        _FACT_ARROW_SCHEMA,
                        compression="lz4",
                        use_dictionary=_FACT_DICT_COLS,
                        write_statistics=["issuer_id"],
                        store_schema=False,
                    ) as f_writer,
                    _PQ.ParquetWriter(
                        unmapped_dst,
                        _UNMAPPED_ARROW_SCHEMA,
                        compression="lz4",
                        use_dictionary=_UNMAPPED_DICT_COLS,
                        write_statistics=["issuer_id"],
                        store_schema=False,
                    ) as u_writer,
                    ThreadPoolExecutor(max_workers=_STREAM_WORKER_THREADS) as pool,
                ):
                    old_factory = conn.row_factory
                    conn.row_factory = None
                    try:
                        for b_idx, idx in enumerate(
                            range(0, len(all_issuers), chunk_size)
                        ):
                            iss_chunk = all_issuers[idx : idx + chunk_size]
                            f_new, u_new, f_new_rows, u_new_rows = (
                                _normalize_issuer_chunk(
                                    conn,
                                    source=source,
                                    tickers=tickers,
                                    issuer_chunk=iss_chunk,
                                    cache=cache,
                                    configured=configured,
                                    active_mapper=active_mapper,
                                    period=period,
                                    pool=pool,
                                )
                            )
                            if f_new_rows > 0:
                                f_writer.write_table(
                                    f_new,
                                    row_group_size=min(
                                        f_new_rows, _PARQUET_ROW_GROUP_SIZE
                                    ),
                                )
                                facts_rows += f_new_rows
                            if u_new_rows > 0:
                                u_writer.write_table(
                                    u_new,
                                    row_group_size=min(
                                        u_new_rows, _PARQUET_ROW_GROUP_SIZE
                                    ),
                                )
                                unmapped_rows += u_new_rows
                            del f_new, u_new
                            if b_idx % 5 == 4:
                                gc.collect()
                            else:
                                gc.collect(1)
                            _PA.default_memory_pool().release_unused()
                    finally:
                        conn.row_factory = old_factory

                    if facts_rows == 0 and unmapped_rows == 0:
                        raise ContractViolationError(
                            "No archived filing matched the requested scope."
                        )
                    if facts_rows == 0:
                        f_writer.write_table(_EMPTY_FACT_ARROW)
                    if unmapped_rows == 0:
                        u_writer.write_table(_EMPTY_UNMAPPED_ARROW)

                gc.collect()
                _PA.default_memory_pool().release_unused()
                recomputed_issuers = len(cur_issuer_filings)
                full_rebuild = True

            if not meta_written:
                _write_meta(
                    meta_dst,
                    source=source,
                    full_history=full_history,
                    taxonomy_sha256=taxonomy_sha256,
                    recomputed_issuers=recomputed_issuers,
                    issuer_filings=cur_issuer_filings,
                )
            if output_dir.exists():
                shutil.rmtree(output_dir)
            staging.rename(output_dir)

        return IncrementalNormalizeResult(
            facts_path=output_dir / "financial_facts.parquet",
            unmapped_path=output_dir / "unmapped_metrics.parquet",
            meta_path=output_dir / NORMALIZATION_METADATA,
            recomputed_issuers=recomputed_issuers,
            facts_rows=facts_rows,
            unmapped_rows=unmapped_rows,
            full_rebuild=full_rebuild,
            taxonomy_sha256=taxonomy_sha256,
            issuer_filings=cur_issuer_filings,
        )
    finally:
        if was_gc_enabled:
            gc.enable()
