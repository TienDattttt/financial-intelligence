"""Cross-layer data contracts for financial statement metadata and raw payloads.

Verified Sources:
    - Circular 96/2020/TT-BTC (Articles 10 & 14) and Circular 68/2024/TT-BTC:
      https://staticfile.hsx.vn/Uploads/LocalFiles/c79e12a5dbf749b6983b63e3242326a3/20210101_Thong%20tu%2096.2020%20TT-BTC.pdf
      Outer parent/consolidated ceilings: unaudited quarter +30d, reviewed quarter +45d,
      semiannual / Q2 reviewed or unknown +60d, audited annual +90d. Standalone
      (SEPARATE) reports can carry shorter statutory ceilings (e.g., 20d/45d); Layer 1
      records the unpenalized Circular 96 statutory deadline upper-bound (+0d) when
      ``published_at`` is missing (see ``EMPIRICAL_STATUTORY_DELAY_P90_DAYS = 37``
      for empirical UPCOM distress late-filing sensitivity).
    - Vietnam Civil Code 2015 (Articles 147 & 148(5)): when the final day of a
      statutory deadline falls on a weekend or public holiday, the deadline ends at
      the close of the next working day.
    - Vietnam Labor Code 2019 (Article 112 & Article 111(3)) and official MOLISA /
      Ministry of Home Affairs / Government holiday notices for 2016-2026 (including
      TB 4875/TB-BLDTBXH, CV 245/VPCP-KGVX, TB 5034/TB-LDTBXH, TB 5015/TB-LDTBXH,
      TB 6150/TB-BLDTBXH, and the 2026 Tet Binh Ngo / National Day notice).
      For years outside 2016-2026 (< 2016 or > 2026), ``_roll_to_next_vn_business_day``
      deterministically rolls weekends (Sat/Sun) and fixed solar holidays
      (01/01, 30/04, 01/05, 02/09) only.
"""

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
    "IN_PROGRESS",
    "COMPLETE",
    "EARLY_STOPPED",
    "PAGE_LIMIT_REACHED",
    "THROTTLED",
    "INVALID_RESPONSE",
]
_Enum = TypeVar("_Enum", bound=StrEnum)
EMPIRICAL_STATUTORY_DELAY_P90_DAYS: int = 37

# Fixed solar Vietnam public holidays (Labor Code 2019, Art. 112).
_VN_FIXED_HOLIDAYS: frozenset[tuple[int, int]] = frozenset(
    {
        (1, 1),  # New Year's Day (Tet Duong lich)
        (4, 30),  # Reunification Day (30/04)
        (5, 1),  # International Workers' Day (01/05)
        (9, 2),  # National Day (02/09)
    }
)


def _date_span(start: date, end: date) -> tuple[date, ...]:
    days = (end - start).days + 1
    return tuple(start + timedelta(days=offset) for offset in range(days))


# Gazetted lunar and National Day adjacent Vietnam public holidays for 2016-2026
# per official MOLISA / Ministry of Home Affairs / Government notices.
# Administrative workday swaps (e.g., 2025-05-02 swapped with Sat 2025-04-26) are
# intentionally excluded because they are not statutory Art. 112 holidays.
_VN_GAZETTED_HOLIDAYS: frozenset[date] = frozenset(
    (
        # 2016 (Binh Than): Tet Mon 08/02..Fri 12/02; Hung Kings 16/04
        *_date_span(date(2016, 2, 8), date(2016, 2, 12)),
        date(2016, 4, 16),
        # 2017 (Dinh Dau): Tet Thu 26/01..Wed 01/02; Hung Kings 06/04
        *_date_span(date(2017, 1, 26), date(2017, 2, 1)),
        date(2017, 4, 6),
        # 2018 (Mau Tuat): Tet Wed 14/02..Tue 20/02; Hung Kings 25/04
        *_date_span(date(2018, 2, 14), date(2018, 2, 20)),
        date(2018, 4, 25),
        # 2019 (Ky Hoi): Tet Mon 04/02..Fri 08/02; Hung Kings 14/04
        *_date_span(date(2019, 2, 4), date(2019, 2, 8)),
        date(2019, 4, 14),
        # 2020 (Canh Ty): Tet Thu 23/01..Wed 29/01; Hung Kings 02/04
        *_date_span(date(2020, 1, 23), date(2020, 1, 29)),
        date(2020, 4, 2),
        # 2021 (Tan Suu, TB 4875/TB-BLDTBXH): Tet 10/02..16/02; Hung Kings 21/04;
        # National Day adjacent 03/09 (Labor Code 2019 effective 2021-01-01)
        *_date_span(date(2021, 2, 10), date(2021, 2, 16)),
        date(2021, 4, 21),
        date(2021, 9, 3),
        # 2022 (Nham Dan, CV 245/VPCP-KGVX): Tet 31/01..04/02; Hung Kings 10/04;
        # National Day adjacent 01/09
        *_date_span(date(2022, 1, 31), date(2022, 2, 4)),
        date(2022, 4, 10),
        date(2022, 9, 1),
        # 2023 (Quy Mao, TB 5034/TB-LDTBXH): Tet 20/01..26/01; Hung Kings 29/04;
        # National Day adjacent 01/09 + statutory compensatory Mon 04/09 (02/09 Sat)
        *_date_span(date(2023, 1, 20), date(2023, 1, 26)),
        date(2023, 4, 29),
        date(2023, 9, 1),
        date(2023, 9, 4),
        # 2024 (Giap Thin, TB 5015/TB-LDTBXH): Tet 08/02..14/02; Hung Kings 18/04;
        # National Day adjacent 03/09 (02/09 Mon)
        *_date_span(date(2024, 2, 8), date(2024, 2, 14)),
        date(2024, 4, 18),
        date(2024, 9, 3),
        # 2025 (At Ty, TB 6150/TB-BLDTBXH): Tet 27/01..31/01 (2024-Q4 2025-01-30 -> 2025-02-03);
        # Hung Kings 07/04; National Day adjacent 01/09
        *_date_span(date(2025, 1, 27), date(2025, 1, 31)),
        date(2025, 4, 7),
        date(2025, 9, 1),
        # 2026 (Binh Ngo, Ministry of Home Affairs notice): Tet 16/02..20/02;
        # Hung Kings 26/04; National Day adjacent 01/09
        *_date_span(date(2026, 2, 16), date(2026, 2, 20)),
        date(2026, 4, 26),
        date(2026, 9, 1),
    )
)


def _roll_to_next_vn_business_day(target: date) -> date:
    """Advance weekend or Vietnam statutory public-holiday dates to the next business day.

    Uses ``_VN_GAZETTED_HOLIDAYS`` for 2016-2026 and falls back to weekends plus
    ``_VN_FIXED_HOLIDAYS`` outside that range. May raise ``OverflowError`` near
    ``date.max``; callers must catch ``OverflowError``.
    """
    current = target
    while (
        current.weekday() >= 5
        or (current.month, current.day) in _VN_FIXED_HOLIDAYS
        or current in _VN_GAZETTED_HOLIDAYS
    ):
        current += timedelta(days=1)
    return current


_METADATA_KEYS = frozenset(
    {
        "issuer_id",
        "ticker_at_filing",
        "statement_type",
        "period_type",
        "fiscal_year",
        "fiscal_quarter",
        "fiscal_year_start_date",
        "period_start_date",
        "period_end_date",
        "statement_scope",
        "published_at",
        "audit_status",
        "audit_opinion",
        "revision_reason",
    }
)
_MANIFEST_EXTRA_KEYS = frozenset(
    {
        "filing_id",
        "source_provider",
        "connector_name",
        "payload_nature",
        "published_at_basis",
        "effective_available_at",
        "is_filed_late",
        "revision_no",
        "fetched_at",
        "payload_file_sha256",
        "canonical_content_sha256",
        "raw_archive_path",
        "raw_record_locator",
        "provider_attrs_json",
    }
)
_ALLOWED_MAPPING_KEYS = _METADATA_KEYS | _MANIFEST_EXTRA_KEYS


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
        if not re.fullmatch(
            r"[A-Za-z0-9._-]{1,64}", self.issuer_id
        ) or not re.fullmatch(r"[A-Z0-9]{1,16}", self.ticker_at_filing):
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
        if (self.period_end_date - self.fiscal_year_start_date).days > 370:
            raise ContractViolationError(
                "Period end date exceeds one fiscal year from fiscal_year_start_date."
            )
        if self.published_at is not None and self.published_at < self.period_end_date:
            raise ContractViolationError("Publication precedes the end of the period.")

    @classmethod
    def from_mapping(cls, fields: Mapping[str, object]) -> FilingMetadata:
        """Read operator-supplied dates and enum values from settings or manifest rows."""
        unknown = set(fields) - _ALLOWED_MAPPING_KEYS
        if unknown:
            raise ContractViolationError(
                f"Unsupported filing metadata fields: {', '.join(sorted(map(str, unknown)))}."
            )
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
        # Layer 1's outer deadlines for parent/consolidated filings under Circular 96,
        # Article 10 (annual: 90d) and Article 14 (semiannual/Q2 reviewed or unknown: 60d,
        # reviewed quarter: 45d, unaudited quarter: 30d).
        days = 30
        if self.period_type == PeriodType.ANNUAL:
            days = 90
        elif self.period_type == PeriodType.SEMI_ANNUAL or (
            self.fiscal_quarter == 2
            and self.audit_status in (AuditStatus.REVIEWED, AuditStatus.UNKNOWN)
        ):
            days = 60
        elif self.audit_status == AuditStatus.REVIEWED:
            days = 45
        try:
            raw_deadline = self.period_end_date + timedelta(days=days)
            return _roll_to_next_vn_business_day(raw_deadline)
        except OverflowError:
            raise ContractViolationError(
                "The statutory deadline is outside the date range."
            ) from None

    @property
    def effective_available_at(self) -> date:
        return self.published_at or self._statutory_deadline

    def effective_available_at_with_delay(self, statutory_delay_days: int = 0) -> date:
        """Return ``published_at`` or statutory deadline shifted by empirical delay days."""
        if statutory_delay_days < 0:
            raise ContractViolationError("statutory_delay_days must be non-negative.")
        if self.published_at is not None:
            return self.published_at
        return self._statutory_deadline + timedelta(days=statutory_delay_days)

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
