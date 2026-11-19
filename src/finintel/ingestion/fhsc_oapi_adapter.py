"""Read the FHSC statement endpoint through the Gate 0 connector."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from datetime import datetime
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
STATEMENTS = {
    StatementType.BALANCE_SHEET: "balance-sheet",
    StatementType.INCOME_STATEMENT: "income-statement",
    StatementType.CASH_FLOW: "cash-flow",
}


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
        self.ticker = ticker
        self.statement_type = statement_type
        self.period_type = period_type
        self.year = year
        self.quarter = quarter
        self.limit = limit
        self._next_page = 1
        self._seen: set[tuple[int, int | None]] = set()
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
        for page in range(self._next_page, max_pages + 1):
            query: dict[str, object] = {
                key: plan.params[key] for key in ("symbol", "type", "period")
            }
            query.update(page=page, limit=self.limit)
            if self.year is not None:
                query["year"] = self.year
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
            elif page == max_pages:
                status = "PAGE_LIMIT_REACHED"
            yield RawFilingBundle(payload, filings, deferred, status)
            self._seen = page_seen
            self._next_page = page + 1
            if status == "THROTTLED":
                raise RateLimitOrCircuitOpenError("FHSC is throttling requests.")
            if empty:
                self._next_page = 1
                self._seen.clear()
                return
        self._next_page = 1
        self._seen.clear()

    def _records(
        self, body: object, seen: set[tuple[int, int | None]]
    ) -> tuple[tuple[RawFiling, ...], tuple[str, ...], bool]:
        if not isinstance(body, dict) or body.get("error_code") != "0":
            raise ContractViolationError("Invalid FHSC financial statement response.")
        rows = body.get("data")
        if not isinstance(rows, list):
            raise ContractViolationError("FHSC response is missing its data array.")

        filings: list[RawFiling] = []
        deferred: list[str] = []
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
            if self.quarter is not None and self.quarter != quarter:
                continue
            locator = f"/data/{index}"
            metadata = self.metadata.get(key)
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
