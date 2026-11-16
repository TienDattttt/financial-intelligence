"""Contracts for original payloads and filing metadata supplied by evidence."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Literal, TypeVar

from finintel.exceptions import ContractViolationError


class StatementType(StrEnum):
    BALANCE_SHEET = "BALANCE_SHEET"
    INCOME_STATEMENT = "INCOME_STATEMENT"
    CASH_FLOW = "CASH_FLOW"


class StatementScope(StrEnum):
    CONSOLIDATED = "CONSOLIDATED"
    SEPARATE = "SEPARATE"
    UNKNOWN = "UNKNOWN"


class PeriodType(StrEnum):
    QUARTER = "QUARTER"
    SEMI_ANNUAL = "SEMI_ANNUAL"
    ANNUAL = "ANNUAL"


class AuditStatus(StrEnum):
    UNAUDITED = "UNAUDITED"
    REVIEWED = "REVIEWED"
    AUDITED = "AUDITED"
    UNKNOWN = "UNKNOWN"


class AuditOpinion(StrEnum):
    UNMODIFIED = "UNMODIFIED"
    EXCEPT_FOR = "EXCEPT_FOR"
    ADVERSE = "ADVERSE"
    DISCLAIMER = "DISCLAIMER"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class RevisionReason(StrEnum):
    ORIGINAL = "ORIGINAL"
    AUDITED_UPDATE = "AUDITED_UPDATE"
    CORRECTION = "CORRECTION"
    RESTATED_REGIME_CHANGE = "RESTATED_REGIME_CHANGE"


PayloadNature = Literal["TRUE_ORIGINAL_DOCUMENT", "PROVIDER_RAW_JSON"]
PaginationStatus = Literal[
    "IN_PROGRESS", "COMPLETE", "PAGE_LIMIT_REACHED", "THROTTLED", "INVALID_RESPONSE"
]
_Enum = TypeVar("_Enum", bound=StrEnum)


def _enum(value: object, enum_type: type[_Enum]) -> _Enum:
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise ContractViolationError(f"Invalid {enum_type.__name__} in filing metadata.")


def _date(value: object, name: str) -> date:
    if type(value) is date:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ContractViolationError(f"Filing metadata requires a valid {name} date.")


@dataclass(frozen=True)
class FilingMetadata:
    """A single statement and period with a verified fiscal calendar."""

    issuer_id: str
    ticker_at_filing: str
    statement_type: StatementType
    period_type: PeriodType
    fiscal_year: int
    fiscal_year_start_date: date
    period_start_date: date
    period_end_date: date
    fiscal_quarter: int | None = None
    statement_scope: StatementScope = StatementScope.UNKNOWN
    published_at: date | None = None
    audit_status: AuditStatus = AuditStatus.UNKNOWN
    audit_opinion: AuditOpinion = AuditOpinion.UNKNOWN
    revision_reason: RevisionReason | None = None

    def __post_init__(self) -> None:
        if not self.issuer_id.strip() or not re.fullmatch(
            r"[A-Z0-9]{1,16}", self.ticker_at_filing
        ):
            raise ContractViolationError("A stable issuer ID and ticker are required.")
        if type(self.fiscal_year) is not int or not 1 <= self.fiscal_year <= 9999:
            raise ContractViolationError("Invalid fiscal year.")
        quarter = self.fiscal_quarter
        if quarter is not None and (type(quarter) is not int or not 1 <= quarter <= 4):
            raise ContractViolationError("Invalid fiscal quarter.")
        if self.period_type == PeriodType.QUARTER and quarter is None:
            raise ContractViolationError("A quarterly filing requires a quarter.")
        if self.period_type == PeriodType.ANNUAL and quarter is not None:
            raise ContractViolationError("An annual filing cannot have a quarter.")
        if self.period_type == PeriodType.SEMI_ANNUAL and quarter not in (None, 2):
            raise ContractViolationError("A semiannual filing may only use quarter 2.")
        if not (
            self.fiscal_year_start_date
            <= self.period_start_date
            <= self.period_end_date
        ):
            raise ContractViolationError("Filing dates are out of order.")
        if self.published_at is not None and self.published_at < self.period_end_date:
            raise ContractViolationError("Publication precedes the end of the period.")

    @classmethod
    def from_mapping(cls, fields: Mapping[str, object]) -> FilingMetadata:
        """Read operator-supplied dates and enum values from settings."""
        issuer_id = fields.get("issuer_id")
        ticker = fields.get("ticker_at_filing")
        year = fields.get("fiscal_year")
        quarter = fields.get("fiscal_quarter")
        publication = fields.get("published_at")
        revision = fields.get("revision_reason")
        if (
            not isinstance(issuer_id, str)
            or not isinstance(ticker, str)
            or type(year) is not int
            or (quarter is not None and type(quarter) is not int)
        ):
            raise ContractViolationError(
                "Invalid filing identity in ingestion settings."
            )
        return cls(
            issuer_id=issuer_id.strip(),
            ticker_at_filing=ticker.strip().upper(),
            statement_type=_enum(fields.get("statement_type"), StatementType),
            period_type=_enum(fields.get("period_type"), PeriodType),
            fiscal_year=year,
            fiscal_quarter=quarter,
            fiscal_year_start_date=_date(
                fields.get("fiscal_year_start_date"), "fiscal_year_start_date"
            ),
            period_start_date=_date(
                fields.get("period_start_date"), "period_start_date"
            ),
            period_end_date=_date(fields.get("period_end_date"), "period_end_date"),
            published_at=_date(publication, "published_at")
            if publication is not None
            else None,
            statement_scope=_enum(
                fields.get("statement_scope", "UNKNOWN"), StatementScope
            ),
            audit_status=_enum(fields.get("audit_status", "UNKNOWN"), AuditStatus),
            audit_opinion=_enum(fields.get("audit_opinion", "UNKNOWN"), AuditOpinion),
            revision_reason=_enum(revision, RevisionReason)
            if revision is not None
            else None,
        )

    @property
    def _statutory_deadline(self) -> date:
        # Layer 1's outer deadlines for parent companies under Circular 96,
        # Article 10 (annual) and Article 14 (quarterly and semiannual).
        days = 30
        if self.period_type == PeriodType.ANNUAL:
            days = 90
        elif self.period_type == PeriodType.SEMI_ANNUAL or (
            self.fiscal_quarter == 2 and self.audit_status == AuditStatus.REVIEWED
        ):
            days = 60
        elif self.audit_status == AuditStatus.REVIEWED:
            days = 45
        try:
            return self.period_end_date + timedelta(days=days)
        except OverflowError:
            raise ContractViolationError(
                "The statutory deadline is outside the date range."
            ) from None

    @property
    def effective_available_at(self) -> date:
        return self.published_at or self._statutory_deadline

    @property
    def published_at_basis(self) -> str:
        return "EXACT_FILING" if self.published_at is not None else "STATUTORY_LAG"

    @property
    def is_filed_late(self) -> bool | None:
        return (
            self.published_at > self._statutory_deadline if self.published_at else None
        )


@dataclass(frozen=True)
class RawPayload:
    content: bytes
    extension: Literal["json", "csv", "xlsx", "parquet"]
    source_provider: str
    connector_name: str
    nature: PayloadNature
    fetched_at: datetime
    provider_attrs: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RawFiling:
    metadata: FilingMetadata
    metrics: Mapping[str, object]
    locator: str | None = None


@dataclass(frozen=True)
class RawFilingBundle:
    """One physical payload and the periods identified inside it."""

    payload: RawPayload
    filings: tuple[RawFiling, ...] = ()
    deferred_locators: tuple[str, ...] = ()
    pagination_status: PaginationStatus | None = None


class FinancialStatementProvider(ABC):
    @abstractmethod
    def fetch(self) -> Iterator[RawFilingBundle]:
        """Yield each payload before making the next source request."""
