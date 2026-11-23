"""Read the FHSC statement endpoint through the Gate 0 connector.

Verified Sources:
    - ``vnstock-oapi==0.1.2`` runtime inspection in project ``.venv``:
      ``vnstock_oapi.source("fhsc").spec.rate_per_second == 0.6`` and
      ``Transport.request`` retries HTTP 429 at most once (``request_count > 1``).
    - ``docs/layers/LAYER_1_INGESTION.md`` and FHSC Open API reference:
      https://developers.fhsc.com.vn/api-reference/phan-tich-co-ban/bao-cao-tai-chinh
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

from finintel.domain.schemas import (
    FilingMetadata,
    FinancialStatementProvider,
    PaginationStatus,
    PeriodType,
    RawFiling,
    RawFilingBundle,
    RawPayload,
    StatementType,
)
from finintel.exceptions import ContractViolationError, RateLimitOrCircuitOpenError
from finintel.pipeline.health import (
    fhsc_retried_rate_limit,
    fhsc_source_errors,
    load_fhsc_source,
)
from finintel.pipeline.state_store import FHSC_CONNECTOR_VERSION

if TYPE_CHECKING:
    from vnstock_oapi.core.envelope import Envelope

OPERATION = "financial-statement/statement"
MAX_TRACE_ID_LEN = 128
DEFAULT_RATE_PER_SECOND = 0.6
STATEMENTS = {
    StatementType.BALANCE_SHEET: "balance-sheet",
    StatementType.INCOME_STATEMENT: "income-statement",
    StatementType.CASH_FLOW: "cash-flow",
}


def _default_sleep(seconds: float) -> None:
    time.sleep(seconds)


def _default_clock() -> float:
    return time.monotonic()


def _validate_checkpoint_path(path: Path) -> Path:
    """Resolve ``path`` and reject symbolic links on the checkpoint file or directory."""
    if not path.is_absolute():
        walk = Path()
        for part in path.parts:
            walk = walk / part
            if walk.is_symlink():
                raise ContractViolationError(
                    "FHSC checkpoint path cannot use symbolic links."
                )
    if (
        path.is_symlink()
        or path.parent.is_symlink()
        or (path.parent != path.parent.parent and path.parent.parent.is_symlink())
    ):
        raise ContractViolationError("FHSC checkpoint path cannot use symbolic links.")
    resolved = path.resolve()
    if resolved.is_symlink():
        raise ContractViolationError("FHSC checkpoint path cannot use symbolic links.")
    return resolved


def _payload(envelope: Envelope, query: dict[str, object]) -> RawPayload:
    try:
        fetched_at = datetime.fromisoformat(envelope.fetched_at)
        content = json.dumps(
            envelope.body, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError):
        raise ContractViolationError(
            "FHSC returned an invalid JSON envelope."
        ) from None
    raw_trace = (
        envelope.body.get("traceId") if isinstance(envelope.body, dict) else None
    )
    trace = (
        raw_trace
        if isinstance(raw_trace, str)
        and len(raw_trace) <= MAX_TRACE_ID_LEN
        and raw_trace.isprintable()
        else None
    )
    return RawPayload(
        content,
        "json",
        "fhsc",
        f"vnstock-oapi=={FHSC_CONNECTOR_VERSION}",
        "PROVIDER_RAW_JSON",
        fetched_at,
        {
            "operation": OPERATION,
            "api_version": envelope.api_version,
            "request": query,
            "http_status": envelope.http_status,
            "body_sha256": envelope.body_sha256,
            "traceId": trace,
            "request_count": (envelope.transport or {}).get("request_count"),
        },
    )


class FhscOapiAdapter(FinancialStatementProvider):
    """Archive each page, including the empty page that confirms completion."""

    def __init__(
        self,
        ticker: str,
        statement_type: StatementType,
        period_type: PeriodType,
        *,
        metadata: Sequence[FilingMetadata] = (),
        year: int | None = None,
        quarter: int | None = None,
        limit: int = 5,
        min_request_interval_seconds: float | None = None,
        early_stop_predicate: Callable[[tuple[RawFiling, ...]], bool] | None = None,
        metadata_resolver: Callable[[int, int | None], FilingMetadata | None]
        | None = None,
        record_metadata_resolver: Callable[
            [int, int | None, Mapping[str, object]], FilingMetadata | None
        ]
        | None = None,
        page_observer: Callable[[Sequence[Mapping[str, object]]], None] | None = None,
        checkpoint_path: Path | None = None,
        sleeper: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Z0-9]{1,16}", ticker):
            raise ContractViolationError("Invalid FHSC ticker.")
        if statement_type not in STATEMENTS or period_type not in (
            PeriodType.ANNUAL,
            PeriodType.QUARTER,
        ):
            raise ContractViolationError("Unsupported FHSC statement or period type.")
        if type(limit) is not int or not 1 <= limit <= 5:
            raise ContractViolationError("FHSC page size must be between 1 and 5.")
        if year is not None and (type(year) is not int or not 1 <= year <= 9999):
            raise ContractViolationError("Invalid FHSC year filter.")
        if quarter is not None and (
            type(quarter) is not int
            or not 1 <= quarter <= 4
            or period_type != PeriodType.QUARTER
            or year is None
        ):
            raise ContractViolationError(
                "An FHSC quarter filter requires a quarterly year."
            )
        if min_request_interval_seconds is not None and (
            isinstance(min_request_interval_seconds, bool)
            or not isinstance(min_request_interval_seconds, (int, float))
            or not math.isfinite(min_request_interval_seconds)
            or min_request_interval_seconds < 0
        ):
            raise ContractViolationError(
                "FHSC min_request_interval_seconds must be a finite non-negative number."
            )
        self.ticker = ticker
        self.statement_type = statement_type
        self.period_type = period_type
        self.year = year
        self.quarter = quarter
        self.limit = limit
        self.min_request_interval_seconds = (
            float(min_request_interval_seconds)
            if min_request_interval_seconds is not None
            else None
        )
        self.early_stop_predicate = early_stop_predicate
        self._metadata_resolver = metadata_resolver
        self._record_metadata_resolver = record_metadata_resolver
        self._page_observer = page_observer
        self._checkpoint_path = (
            _validate_checkpoint_path(checkpoint_path)
            if checkpoint_path is not None
            else None
        )
        self._sleeper = sleeper
        self._clock = clock
        self._last_request_at: float | None = None
        self._next_page = 1
        self._seen: set[tuple[int, int | None]] = set()
        if self._checkpoint_path is not None:
            self._load_checkpoint()
        self.metadata: dict[tuple[int, int | None], FilingMetadata] = {}
        for filing in metadata:
            if (
                filing.ticker_at_filing != ticker
                or filing.statement_type != statement_type
            ):
                continue
            if period_type == PeriodType.ANNUAL:
                if filing.period_type != PeriodType.ANNUAL:
                    continue
                key: tuple[int, int | None] = (filing.fiscal_year, None)
            else:
                if filing.period_type not in (
                    PeriodType.QUARTER,
                    PeriodType.SEMI_ANNUAL,
                ):
                    continue
                key = (
                    filing.fiscal_year,
                    2
                    if filing.period_type == PeriodType.SEMI_ANNUAL
                    else filing.fiscal_quarter,
                )
            if key in self.metadata:
                raise ContractViolationError("Ambiguous metadata for an FHSC period.")
            self.metadata[key] = filing

    def _resolve_interval(self, rate_per_second: object) -> float:
        if self.min_request_interval_seconds is not None:
            return self.min_request_interval_seconds
        if (
            isinstance(rate_per_second, (int, float))
            and not isinstance(rate_per_second, bool)
            and math.isfinite(rate_per_second)
            and rate_per_second > 0
        ):
            return 1.0 / float(rate_per_second)
        return 1.0 / DEFAULT_RATE_PER_SECOND

    @property
    def next_page(self) -> int:
        """Next page index to request when resuming pagination."""
        return self._next_page

    def clear_checkpoint(self) -> None:
        """Reset pagination state and delete any persisted checkpoint file."""
        self._next_page = 1
        self._seen.clear()
        self._clear_checkpoint()

    def _checkpoint_scope(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "statement_type": self.statement_type.value,
            "period_type": self.period_type.value,
            "year": self.year,
            "quarter": self.quarter,
            "limit": self.limit,
        }

    def _load_checkpoint(self) -> None:
        if self._checkpoint_path is None:
            return
        path = _validate_checkpoint_path(self._checkpoint_path)
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._clear_checkpoint()
            return
        if not isinstance(raw, dict) or raw.get("scope") != self._checkpoint_scope():
            self._next_page = 1
            self._seen.clear()
            return
        next_page = raw.get("next_page")
        seen_raw = raw.get("seen")
        if (
            type(next_page) is not int
            or next_page < 1
            or not isinstance(seen_raw, list)
        ):
            self._clear_checkpoint()
            return
        parsed_seen: set[tuple[int, int | None]] = set()
        for item in seen_raw:
            if not isinstance(item, list) or len(item) != 2:
                self._clear_checkpoint()
                return
            year, quarter = item
            if type(year) is not int or not 1 <= year <= 9999:
                self._clear_checkpoint()
                return
            if quarter is not None and (
                type(quarter) is not int or not 1 <= quarter <= 4
            ):
                self._clear_checkpoint()
                return
            parsed_seen.add((year, quarter))
        self._next_page = next_page
        self._seen = parsed_seen

    def _save_checkpoint(self) -> None:
        if self._checkpoint_path is None:
            return
        path = _validate_checkpoint_path(self._checkpoint_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _validate_checkpoint_path(path)
        payload = {
            "version": 1,
            "scope": self._checkpoint_scope(),
            "next_page": self._next_page,
            "seen": sorted(
                ([year, quarter] for year, quarter in self._seen),
                key=lambda item: (item[0], -1 if item[1] is None else item[1]),
            ),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        tmp_path: Path | None = None
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
                json.dump(payload, tmp, ensure_ascii=False, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, path)
            tmp_path = None
        finally:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def _clear_checkpoint(self) -> None:
        if self._checkpoint_path is None:
            return
        path = _validate_checkpoint_path(self._checkpoint_path)
        if path.exists():
            path.unlink()

    def _validate_resolved_metadata(
        self, resolved: FilingMetadata, year: int, quarter: int | None
    ) -> FilingMetadata:
        expected_quarter = (
            2
            if resolved.period_type == PeriodType.SEMI_ANNUAL
            else resolved.fiscal_quarter
        )
        if (
            resolved.ticker_at_filing != self.ticker
            or resolved.statement_type != self.statement_type
            or resolved.fiscal_year != year
            or expected_quarter != quarter
        ):
            raise ContractViolationError(
                "Resolved metadata does not match the FHSC period."
            )
        return resolved

    def fetch(self) -> Iterator[RawFilingBundle]:
        source = load_fhsc_source()
        period = "quarter" if self.period_type == PeriodType.QUARTER else "year"
        statement = STATEMENTS[self.statement_type]
        with fhsc_source_errors():
            plans = source.plan(
                "financials", self.ticker, statement=statement, period=period
            )
        if len(plans) != 1 or plans[0].operation != OPERATION:
            raise ContractViolationError("Unexpected FHSC financials operation.")
        plan = plans[0]
        max_pages = source.spec.ops[OPERATION].max_pages
        interval = self._resolve_interval(
            getattr(source.spec, "rate_per_second", DEFAULT_RATE_PER_SECOND)
        )
        sleeper = self._sleeper if self._sleeper is not None else _default_sleep
        clock = self._clock if self._clock is not None else _default_clock

        for page in range(self._next_page, max_pages + 1):
            query: dict[str, object] = {
                key: plan.params[key] for key in ("symbol", "type", "period")
            }
            query.update(page=page, limit=self.limit)
            if self.year is not None:
                query["year"] = self.year
            if self._last_request_at is not None and interval > 0:
                elapsed = clock() - self._last_request_at
                remaining = interval - elapsed
                if remaining > 0:
                    sleeper(remaining)
            self._last_request_at = clock()
            with fhsc_source_errors():
                pages = source.raw(
                    plan.operation, only_page=page, page_cap=None, **query
                )
            if len(pages) != 1:
                raise ContractViolationError("FHSC must return one envelope per page.")
            envelope = pages[0]
            payload = _payload(envelope, query)
            page_seen = set(self._seen)
            try:
                filings, deferred, empty = self._records(envelope.body, page_seen)
            except ContractViolationError:
                yield RawFilingBundle(payload, pagination_status="INVALID_RESPONSE")
                raise

            status: PaginationStatus = "IN_PROGRESS"
            if fhsc_retried_rate_limit(envelope):
                status = "THROTTLED"
            elif empty:
                status = "COMPLETE"
            elif (
                self.early_stop_predicate is not None
                and bool(filings)
                and not deferred
                and self.early_stop_predicate(filings)
            ):
                status = "EARLY_STOPPED"
            elif page == max_pages:
                status = "PAGE_LIMIT_REACHED"
            yield RawFilingBundle(payload, filings, deferred, status)
            self._seen = page_seen
            self._next_page = page + 1
            if status == "THROTTLED":
                self._save_checkpoint()
                raise RateLimitOrCircuitOpenError("FHSC is throttling requests.")
            if empty or status == "EARLY_STOPPED":
                self._next_page = 1
                self._seen.clear()
                self._clear_checkpoint()
                return
            self._save_checkpoint()
        self._next_page = 1
        self._seen.clear()
        self._clear_checkpoint()

    def _records(
        self, body: object, seen: set[tuple[int, int | None]]
    ) -> tuple[tuple[RawFiling, ...], tuple[str, ...], bool]:
        if not isinstance(body, dict) or body.get("error_code") != "0":
            raise ContractViolationError("Invalid FHSC financial statement response.")
        rows = body.get("data")
        if not isinstance(rows, list):
            raise ContractViolationError("FHSC response is missing its data array.")

        validated: list[tuple[int, int, int | None, dict[str, object]]] = []
        observed_rows: list[dict[str, object]] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ContractViolationError("Invalid FHSC period record.")
            year, quarter = row.get("year"), row.get("quarter")
            if (
                type(year) is not int
                or not 1 <= year <= 9999
                or (self.year is not None and self.year != year)
            ):
                raise ContractViolationError("Invalid FHSC response year.")
            if self.period_type == PeriodType.QUARTER:
                if type(quarter) is not int or not 1 <= quarter <= 4:
                    raise ContractViolationError("Invalid FHSC response quarter.")
            elif quarter is not None:
                raise ContractViolationError(
                    "Unexpected quarter in an annual FHSC response."
                )
            key = (year, quarter)
            if key in seen:
                raise ContractViolationError(
                    "Duplicate fiscal period in FHSC pagination."
                )
            seen.add(key)
            normalized_row = dict(row)
            observed_rows.append(normalized_row)
            if self.quarter is not None and self.quarter != quarter:
                continue
            validated.append((index, year, quarter, normalized_row))

        if self._page_observer is not None and observed_rows:
            self._page_observer(tuple(observed_rows))

        filings: list[RawFiling] = []
        deferred: list[str] = []
        for index, year, quarter, row in validated:
            locator = f"/data/{index}"
            metadata = self.metadata.get((year, quarter))
            if metadata is None and self._record_metadata_resolver is not None:
                resolved = self._record_metadata_resolver(year, quarter, row)
                if resolved is not None:
                    metadata = self._validate_resolved_metadata(resolved, year, quarter)
            if metadata is None and self._metadata_resolver is not None:
                resolved = self._metadata_resolver(year, quarter)
                if resolved is not None:
                    metadata = self._validate_resolved_metadata(resolved, year, quarter)
            if metadata is None:
                deferred.append(locator)
            else:
                metrics = {
                    code: value
                    for code, value in row.items()
                    if code not in {"year", "quarter"}
                }
                filings.append(RawFiling(metadata, metrics, locator))
        return tuple(filings), tuple(deferred), not rows
