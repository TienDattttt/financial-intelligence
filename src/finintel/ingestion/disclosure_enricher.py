"""Enrich archived FHSC filings with verified public disclosure dates via RawArchive receipts.

Verified Sources:
    - Circular 96/2020/TT-BTC (Articles 10 & 14) and Circular 68/2024/TT-BTC:
      https://staticfile.hsx.vn/Uploads/LocalFiles/c79e12a5dbf749b6983b63e3242326a3/20210101_Thong%20tu%2096.2020%20TT-BTC.pdf
    - Vietnam Civil Code 2015 (Articles 147 & 148(5)) and Labor Code 2019 (Article 112),
      applied via ``FilingMetadata._statutory_deadline`` and ``FilingMetadata.is_filed_late``.
    - Vietcap IQ financial-statement ``publicDate`` JSON schema and CafeF BCTC AJAX HTML
      table conventions verified against ``tests/unit/test_layer4_disclosure_enrichment.py``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from finintel.domain.schemas import (
    AuditStatus,
    FilingMetadata,
    PeriodType,
    RawFiling,
    RawFilingBundle,
    RawPayload,
    StatementScope,
)
from finintel.exceptions import ArchiveIntegrityError, ContractViolationError
from finintel.ingestion.archive import RawArchive
from finintel.ingestion.hasher import (
    MANIFEST_SIGNATURE_FIELDS,
    canonical_content_sha256,
    compute_manifest_sig_sha256,
    payload_file_sha256,
)
from finintel.ingestion.ssrf_guard import (
    DISCLOSURE_SOURCE_ALLOWED_HOSTS,
    validate_https_allowlisted_url,
)

VN_TZ = timezone(timedelta(hours=7))
DISCLOSURE_CONNECTOR = "disclosure_enricher_v1"

_TICKER_RE = re.compile(r"[A-Z0-9]{1,16}")
_LOCATOR_RE = re.compile(r"/data/(0|[1-9][0-9]{0,5})")
_TR_RE = re.compile(r"<tr[^>]*>([\s\S]*?)</tr>", flags=re.IGNORECASE)
_TD_RE = re.compile(r"<td[^>]*>([\s\S]*?)</td>", flags=re.IGNORECASE)
_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", flags=re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_PERIOD_Q_RE = re.compile(r"^Q([1-4])/([12][0-9]{3})$", flags=re.IGNORECASE)
_PERIOD_A_RE = re.compile(r"^CN/([12][0-9]{3})$", flags=re.IGNORECASE)

_URL_YYYYMMDD_RE = re.compile(
    r"(?:^|[_/-])(20[12][0-9])(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])(?:[_/-]|2020)"
)
_URL_DDMMYYYY_DASH_TIME_RE = re.compile(
    r"(0[1-9]|[12][0-9]|3[01])(0[1-9]|1[0-2])(20[12][0-9])[-_]([01][0-9]|2[0-3])[0-5][0-9][0-5][0-9]"
)
_URL_DDMMYYYY_14_SUFFIX_RE = re.compile(
    r"[_-](0[1-9]|[12][0-9]|3[01])(0[1-9]|1[0-2])(20[12][0-9])"
    r"([01][0-9]|2[0-3])[0-5][0-9][0-5][0-9]\.(?:pdf|rar|zip)$",
    flags=re.IGNORECASE,
)
_URL_EPOCH10_SUFFIX_RE = re.compile(
    r"[_-](1[5-9][0-9]{8})\.(?:pdf|rar|zip)$",
    flags=re.IGNORECASE,
)
_CAFEF_DELIMITED_TICKER_RE = re.compile(r"_-_([A-Za-z0-9]{3,4})_-_")
_CAFEF_DATED_TICKER_RE = re.compile(r"^\d{8}[_-]+([A-Za-z][A-Za-z0-9]{2})[_-]")
_CAFEF_LEADING_TICKER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]{2})[_-]")
_CAFEF_DATA_SYMBOL_RE = re.compile(
    r"""data-(?:symbol|ticker)=["']([A-Za-z0-9]{1,16})["']""",
    flags=re.IGNORECASE,
)
_CAFEF_NON_TICKER_PREFIXES = frozenset(
    {
        "ALL",
        "BAN",
        "BAO",
        "BCT",
        "CON",
        "CTY",
        "DOC",
        "ENG",
        "FIN",
        "GIA",
        "HOP",
        "IMG",
        "NAM",
        "NEW",
        "OLD",
        "PDF",
        "QUY",
        "RAR",
        "REV",
        "TAI",
        "THE",
        "THU",
        "TMP",
        "TON",
        "USD",
        "VIE",
        "VND",
        "XLS",
        "ZIP",
    }
)
_CAFEF_UNSCOPED_EXCLUDE_TERMS: tuple[str, ...] = (
    "giải trình",
    "đính chính",
    "chênh lệch",
    "thuyết minh",
    "bổ sung",
    "nghị quyết",
    "đại hội",
    "riêng",
)
_SQLITE_IN_CHUNK_SIZE = 500


@dataclass(frozen=True)
class DisclosureRecord:
    """A single verified public disclosure date for a company statement period."""

    ticker: str
    period_type: PeriodType
    fiscal_year: int
    fiscal_quarter: int | None
    statement_scope: StatementScope
    published_at: date
    source_name: str
    source_url: str
    raw_locator: str
    audit_status: AuditStatus | None = None
    revision_no: int | None = None

    def __post_init__(self) -> None:
        if not _TICKER_RE.fullmatch(self.ticker):
            raise ContractViolationError("Invalid ticker in disclosure record.")
        if type(self.fiscal_year) is not int or not 1990 <= self.fiscal_year <= 2100:
            raise ContractViolationError("Invalid fiscal year in disclosure record.")
        if self.period_type == PeriodType.QUARTER:
            if (
                type(self.fiscal_quarter) is not int
                or not 1 <= self.fiscal_quarter <= 4
            ):
                raise ContractViolationError(
                    "Quarterly disclosure requires quarter 1..4."
                )
        elif self.period_type == PeriodType.ANNUAL:
            if self.fiscal_quarter is not None:
                raise ContractViolationError(
                    "Annual disclosure cannot specify a quarter."
                )
        else:
            raise ContractViolationError(
                "Unsupported period type in disclosure record."
            )
        if self.statement_scope == StatementScope.UNKNOWN:
            raise ContractViolationError(
                "Disclosure record requires a known statement scope."
            )
        if type(self.published_at) is not date:
            raise ContractViolationError(
                "Disclosure published_at must be a calendar date."
            )
        if not self.source_name.strip() or not self.source_url.strip():
            raise ContractViolationError(
                "Disclosure record requires non-empty provenance."
            )
        if self.revision_no is not None and (
            type(self.revision_no) is not int or self.revision_no < 1
        ):
            raise ContractViolationError(
                "Disclosure revision_no must be a positive integer."
            )


@dataclass(frozen=True)
class EnrichmentSummary:
    """Audit counts produced when applying verified disclosure records to RawArchive."""

    matched_filings: int
    upgraded_filings: int
    unchanged_filings: int
    skipped_pre_period_end: int


def _safe_calendar_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_iso_prefix_date(raw: object) -> date | None:
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    prefix = raw[:10]
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", prefix):
        return None
    try:
        return date.fromisoformat(prefix)
    except ValueError:
        return None


def is_valid_vietcap_iq_payload(content: bytes) -> bool:
    """Return True when raw Vietcap IQ JSON represents a successful HTTP 200 payload."""
    try:
        decoded = json.loads(content)
    except (ValueError, UnicodeError):
        return False
    if not isinstance(decoded, dict) or decoded.get("successful") is False:
        return False
    status = decoded.get("status")
    return (
        status is None or (not isinstance(status, bool) and status == 200)
    ) and isinstance(decoded.get("data"), dict)


def parse_vietcap_iq_disclosures(
    ticker: str,
    content: bytes,
    *,
    source_url: str,
    max_date: date | None = None,
) -> tuple[DisclosureRecord, ...]:
    """Extract verified publicDate timestamps from a Vietcap IQ financial-statement JSON."""
    clean_ticker = ticker.strip().upper()
    if not _TICKER_RE.fullmatch(clean_ticker):
        raise ContractViolationError(
            "Invalid ticker for Vietcap IQ disclosure parsing."
        )
    try:
        decoded = json.loads(content)
    except (ValueError, UnicodeError) as exc:
        raise ContractViolationError("Malformed Vietcap IQ disclosure JSON.") from exc
    if not isinstance(decoded, dict):
        raise ContractViolationError("Vietcap IQ payload must be a JSON object.")
    data = decoded.get("data")
    if not isinstance(data, dict):
        raise ContractViolationError("Vietcap IQ payload is missing data mapping.")

    records: list[DisclosureRecord] = []
    for section_name, expected_type in (
        ("years", PeriodType.ANNUAL),
        ("quarters", PeriodType.QUARTER),
    ):
        entries = data.get(section_name, [])
        if not isinstance(entries, list):
            continue
        for idx, item in enumerate(entries):
            if not isinstance(item, dict):
                continue
            row_ticker = (
                str(item.get("ticker") or item.get("organCode") or "").strip().upper()
            )
            if row_ticker != clean_ticker:
                continue
            year = item.get("yearReport")
            length = item.get("lengthReport")
            pub_date = _parse_iso_prefix_date(item.get("publicDate"))
            if type(year) is not int or not 1990 <= year <= 2100 or pub_date is None:
                continue
            if max_date is not None and pub_date > max_date:
                continue
            if expected_type == PeriodType.ANNUAL:
                if length != 5:
                    continue
                quarter: int | None = None
                audit: AuditStatus | None = AuditStatus.AUDITED
            else:
                if type(length) is not int or not 1 <= length <= 4:
                    continue
                quarter = length
                audit = AuditStatus.REVIEWED if quarter == 2 else AuditStatus.UNAUDITED
            records.append(
                DisclosureRecord(
                    ticker=clean_ticker,
                    period_type=expected_type,
                    fiscal_year=year,
                    fiscal_quarter=quarter,
                    statement_scope=StatementScope.CONSOLIDATED,
                    published_at=pub_date,
                    source_name="vietcap_iq",
                    source_url=source_url,
                    raw_locator=f"/data/{section_name}/{idx}",
                    audit_status=audit,
                )
            )
    return tuple(records)


def _extract_cafef_filename_date(url: str) -> date | None:
    basename = url.split("?", 1)[0].rsplit("/", 1)[-1]
    if not basename:
        return None
    for pat, y_i, m_i, d_i in (
        (_URL_YYYYMMDD_RE, 1, 2, 3),
        (_URL_DDMMYYYY_DASH_TIME_RE, 3, 2, 1),
        (_URL_DDMMYYYY_14_SUFFIX_RE, 3, 2, 1),
    ):
        if (m := pat.search(basename)) is not None:
            if (
                p := _safe_calendar_date(int(m[y_i]), int(m[m_i]), int(m[d_i]))
            ) is not None:
                return p
    m_epoch = _URL_EPOCH10_SUFFIX_RE.search(basename)
    return datetime.fromtimestamp(int(m_epoch[1]), tz=VN_TZ).date() if m_epoch else None


def validate_disclosure_source_url(source_url: str) -> str:
    """Validate HTTPS scheme, port 443, and disclosure host allowlist via ssrf_guard."""
    validated, _ = validate_https_allowlisted_url(
        source_url, allowed_hosts=DISCLOSURE_SOURCE_ALLOWED_HOSTS
    )
    return validated


def _validate_cafef_source_url_ticker(source_url: str, clean_ticker: str) -> None:
    if source_url.lower().startswith(("http://", "https://")):
        validate_disclosure_source_url(source_url)
    parsed = urlsplit(source_url)
    for key, values in parse_qs(parsed.query).items():
        if key.lower() not in {"sym", "symbol"}:
            continue
        for raw_val in values:
            val = raw_val.strip().upper()
            if val and val != clean_ticker:
                raise ContractViolationError(
                    "CafeF disclosure source_url ticker mismatches requested ticker."
                )


def _cafef_row_matches_ticker(tr_html: str, href: str, clean_ticker: str) -> bool:
    for attr_match in _CAFEF_DATA_SYMBOL_RE.finditer(tr_html):
        if attr_match[1].strip().upper() != clean_ticker:
            return False

    basename = href.split("?", 1)[0].rsplit("/", 1)[-1]
    if not basename:
        return True

    delimited = _CAFEF_DELIMITED_TICKER_RE.search(basename)
    if delimited is not None:
        return delimited[1].upper() == clean_ticker

    dated = _CAFEF_DATED_TICKER_RE.search(basename)
    if dated is not None:
        candidate = dated[1].upper()
        if candidate not in _CAFEF_NON_TICKER_PREFIXES:
            return candidate == clean_ticker

    leading = _CAFEF_LEADING_TICKER_RE.search(basename)
    if leading is not None:
        candidate = leading[1].upper()
        if candidate not in _CAFEF_NON_TICKER_PREFIXES:
            return candidate == clean_ticker

    return True


def _slot_quarter_index(period_type: PeriodType, year: int, quarter: int | None) -> int:
    return year * 4 + (quarter if quarter is not None else 4)


def _cafef_audit_and_priority(
    title_lower: str,
    period_type: PeriodType,
    quarter: int | None,
) -> tuple[AuditStatus, int]:
    is_reviewed = "soát xét" in title_lower
    is_audited = "kiểm toán" in title_lower
    if period_type == PeriodType.ANNUAL:
        audit = AuditStatus.AUDITED if is_audited else AuditStatus.UNAUDITED
        priority = 2 if is_audited else 1
    elif quarter == 2:
        audit = AuditStatus.REVIEWED if is_reviewed else AuditStatus.UNAUDITED
        priority = 2 if is_reviewed else 1
    else:
        audit = (
            AuditStatus.AUDITED
            if is_audited
            else (AuditStatus.REVIEWED if is_reviewed else AuditStatus.UNAUDITED)
        )
        priority = 2 if not (is_reviewed or is_audited) else 1
    return audit, priority


def parse_cafef_bctc_disclosures(
    ticker: str,
    content: bytes,
    *,
    source_url: str,
    max_date: date | None = None,
    allow_unscoped_standalone_as_consolidated: bool = False,
    standalone_consolidated_slots: Collection[tuple[PeriodType, int, int | None]]
    | None = None,
) -> tuple[DisclosureRecord, ...]:
    """Extract explicit filing dates embedded in CafeF BCTC document table rows.

    Short archival filenames without full calendar dates (e.g. ``FPT_21CN_BCTC_HNKT.pdf``
    or ``FPT_22Q2_BCTC_HNSX_2022-102346.pdf``) are strictly ignored rather than guessed.
    Mismatched ticker query parameters in ``source_url`` raise ``ContractViolationError``,
    and rows whose filenames or HTML attributes declare a different ticker are skipped.
    When ``allow_unscoped_standalone_as_consolidated`` (or ``standalone_consolidated_slots``)
    is enabled, unscoped standalone rows without any explicit ``hợp nhất`` / ``công ty mẹ``
    / ``riêng`` row within a ±4-quarter window (and verified against FHSC NCI slots when
    provided) map safely to ``StatementScope.CONSOLIDATED``.
    """
    clean_ticker = ticker.strip().upper()
    if not _TICKER_RE.fullmatch(clean_ticker):
        raise ContractViolationError("Invalid ticker for CafeF disclosure parsing.")
    _validate_cafef_source_url_ticker(source_url, clean_ticker)
    html = content.decode("utf-8", errors="replace")

    chosen: dict[
        tuple[StatementScope, PeriodType, int, int | None], tuple[int, DisclosureRecord]
    ] = {}
    explicit_scope_q_indices: set[int] = set()
    unscoped_candidates: list[tuple[int, PeriodType, int, int | None, date, str]] = []

    for row_idx, tr_match in enumerate(_TR_RE.finditer(html)):
        tr_html = tr_match[1]
        tds = [
            re.sub(r"\s+", " ", unescape(_TAG_RE.sub("", td))).strip()
            for td in _TD_RE.findall(tr_html)
        ]
        if len(tds) < 2:
            continue
        title_lower = tds[0].lower()
        period_text = tds[1].strip()
        href_match = _HREF_RE.search(tr_html)
        if href_match is None:
            continue
        href = href_match[1].strip()
        if not _cafef_row_matches_ticker(tr_html, href, clean_ticker):
            continue

        q_match = _PERIOD_Q_RE.fullmatch(period_text)
        a_match = _PERIOD_A_RE.fullmatch(period_text)
        if q_match is not None:
            period_type = PeriodType.QUARTER
            quarter: int | None = int(q_match[1])
            year = int(q_match[2])
        elif a_match is not None:
            period_type = PeriodType.ANNUAL
            quarter = None
            year = int(a_match[1])
        else:
            continue

        if "hợp nhất" in title_lower:
            scope: StatementScope | None = StatementScope.CONSOLIDATED
        elif (
            "công ty mẹ" in title_lower
            or "bctc riêng" in title_lower
            or "riêng lẻ" in title_lower
        ):
            scope = StatementScope.SEPARATE
        else:
            scope = None

        if scope is not None:
            explicit_scope_q_indices.add(
                _slot_quarter_index(period_type, year, quarter)
            )

        pub_date = _extract_cafef_filename_date(href)
        if pub_date is None or pub_date < date(year, 1, 1):
            continue
        if max_date is not None and pub_date > max_date:
            continue

        if scope is None:
            if (
                "báo cáo tài chính" in title_lower or "bctc" in title_lower
            ) and not any(
                term in title_lower for term in _CAFEF_UNSCOPED_EXCLUDE_TERMS
            ):
                unscoped_candidates.append(
                    (row_idx, period_type, year, quarter, pub_date, title_lower)
                )
            continue

        audit, priority = _cafef_audit_and_priority(title_lower, period_type, quarter)
        rec = DisclosureRecord(
            ticker=clean_ticker,
            period_type=period_type,
            fiscal_year=year,
            fiscal_quarter=quarter,
            statement_scope=scope,
            published_at=pub_date,
            source_name="cafef_bctc",
            source_url=source_url,
            raw_locator=f"/rows/{row_idx}",
            audit_status=audit,
        )
        slot = (scope, period_type, year, quarter)
        existing = chosen.get(slot)
        if existing is None or priority > existing[0]:
            chosen[slot] = (priority, rec)

    if (
        allow_unscoped_standalone_as_consolidated
        or standalone_consolidated_slots is not None
    ):
        allowed_slots = (
            set(standalone_consolidated_slots)
            if standalone_consolidated_slots is not None
            else None
        )
        for (
            row_idx,
            period_type,
            year,
            quarter,
            pub_date,
            title_lower,
        ) in unscoped_candidates:
            q_idx = _slot_quarter_index(period_type, year, quarter)
            if any(abs(other_q - q_idx) <= 4 for other_q in explicit_scope_q_indices):
                continue
            if (
                allowed_slots is not None
                and (period_type, year, quarter) not in allowed_slots
            ):
                continue
            audit, priority = _cafef_audit_and_priority(
                title_lower, period_type, quarter
            )
            rec = DisclosureRecord(
                ticker=clean_ticker,
                period_type=period_type,
                fiscal_year=year,
                fiscal_quarter=quarter,
                statement_scope=StatementScope.CONSOLIDATED,
                published_at=pub_date,
                source_name="cafef_bctc",
                source_url=source_url,
                raw_locator=f"/rows/{row_idx}",
                audit_status=audit,
            )
            slot = (StatementScope.CONSOLIDATED, period_type, year, quarter)
            existing = chosen.get(slot)
            if existing is None or priority > existing[0]:
                chosen[slot] = (priority, rec)

    return tuple(item[1] for item in chosen.values())


DisclosureSlot = tuple[str, PeriodType, int, int | None, StatementScope]


def _default_slot_end_date(
    period_type: PeriodType, fiscal_year: int, fiscal_quarter: int | None
) -> date:
    if period_type == PeriodType.ANNUAL or fiscal_quarter == 4:
        return date(fiscal_year, 12, 31)
    if fiscal_quarter == 1:
        return date(fiscal_year, 3, 31)
    if fiscal_quarter == 2:
        return date(fiscal_year, 6, 30)
    if fiscal_quarter == 3:
        return date(fiscal_year, 9, 30)
    return date(fiscal_year, 12, 31)


def _is_audit_tier_compatible(
    vci_rec: DisclosureRecord, cafef_rec: DisclosureRecord
) -> bool:
    """Enforce Audit-Tier PIT Guard so lower-tier filing dates cannot replace higher-tier data."""
    vci_audit = vci_rec.audit_status
    cafef_audit = cafef_rec.audit_status
    if vci_rec.period_type == PeriodType.ANNUAL:
        if vci_audit == AuditStatus.AUDITED:
            return cafef_audit == AuditStatus.AUDITED
        if vci_audit == AuditStatus.REVIEWED:
            return cafef_audit in (AuditStatus.REVIEWED, AuditStatus.AUDITED)
        if cafef_audit == AuditStatus.UNAUDITED and vci_audit != AuditStatus.UNAUDITED:
            return False
        return True
    if vci_rec.period_type == PeriodType.QUARTER and vci_rec.fiscal_quarter == 2:
        if vci_audit == AuditStatus.AUDITED:
            return cafef_audit == AuditStatus.AUDITED
        if vci_audit == AuditStatus.REVIEWED:
            return cafef_audit in (AuditStatus.REVIEWED, AuditStatus.AUDITED)
        if cafef_audit == AuditStatus.UNAUDITED and vci_audit != AuditStatus.UNAUDITED:
            return False
        return True
    # Q1, Q3, Q4 (including Q4 where Vietcap IQ overwrites publicDate with Annual date)
    return True


def _merge_safe_audit_status(
    chosen: DisclosureRecord, other: DisclosureRecord
) -> DisclosureRecord:
    """Safely inherit a non-UNKNOWN audit_status without inflating earlier unaudited filings."""
    if chosen.audit_status not in (None, AuditStatus.UNKNOWN):
        return chosen
    if other.audit_status is None or other.audit_status == AuditStatus.UNKNOWN:
        return chosen
    if other.audit_status == AuditStatus.UNAUDITED:
        return replace(chosen, audit_status=AuditStatus.UNAUDITED)
    # Only inherit REVIEWED/AUDITED when the chosen record is not earlier than the
    # source asserting that audit tier (prevents promoting an earlier self-prepared
    # filing to AUDITED/REVIEWED).
    if chosen.published_at >= other.published_at:
        return replace(chosen, audit_status=other.audit_status)
    return chosen


def merge_disclosure_records(
    vietcap_records: Sequence[DisclosureRecord],
    cafef_records: Sequence[DisclosureRecord] = (),
    *,
    period_end_dates: Mapping[DisclosureSlot, date] | None = None,
) -> dict[DisclosureSlot, DisclosureRecord]:
    """Combine Vietcap IQ and CafeF BCTC disclosure records with PIT audit-tier guards.

    Vietcap IQ provides structured ``publicDate`` history but can lag the original
    filing PDF date (or overwrite ``Q4.publicDate`` with the Annual audited date).
    When both sources cover the same ``DisclosureSlot``:
    - A pre-period-end record never overwrites a valid record (while a lone
      pre-period-end record is preserved so ``enrich_fhsc_manifest_disclosures``
      rejects it and increments ``skipped_pre_period_end``).
    - ``Audit-Tier PIT Guard`` blocks ``UNAUDITED`` CafeF filings from replacing
      ``AUDITED`` annual or ``REVIEWED`` Q2 Vietcap IQ dates.
    - When audit tiers are compatible and valid, a strictly earlier CafeF date
      (``cafef.published_at < vci.published_at``) takes precedence.
    """
    merged: dict[DisclosureSlot, DisclosureRecord] = {}
    for rec in vietcap_records:
        slot: DisclosureSlot = (
            rec.ticker,
            rec.period_type,
            rec.fiscal_year,
            rec.fiscal_quarter,
            rec.statement_scope,
        )
        merged[slot] = rec

    for rec in cafef_records:
        slot = (
            rec.ticker,
            rec.period_type,
            rec.fiscal_year,
            rec.fiscal_quarter,
            rec.statement_scope,
        )
        current = merged.get(slot)
        if current is None:
            merged[slot] = rec
            continue

        slot_end = (
            period_end_dates[slot]
            if period_end_dates is not None and slot in period_end_dates
            else _default_slot_end_date(
                rec.period_type, rec.fiscal_year, rec.fiscal_quarter
            )
        )
        vci_valid = current.published_at >= slot_end
        cafef_valid = rec.published_at >= slot_end
        if not cafef_valid:
            continue
        if not vci_valid:
            merged[slot] = rec
            continue
        if not _is_audit_tier_compatible(current, rec):
            continue
        if rec.published_at < current.published_at:
            merged[slot] = _merge_safe_audit_status(rec, current)
        else:
            merged[slot] = _merge_safe_audit_status(current, rec)
    return merged


def _extract_fhsc_metrics(
    archive_root: Path,
    row: Mapping[str, object],
    metadata: FilingMetadata,
    *,
    resolved_root: Path | None = None,
    payload_cache: dict[tuple[str, str], list[object]] | None = None,
) -> dict[str, object]:
    rel_path = row.get("raw_archive_path")
    expected_sha = row.get("payload_file_sha256")
    locator = row.get("raw_record_locator")
    if (
        not isinstance(rel_path, str)
        or not isinstance(expected_sha, str)
        or not isinstance(locator, str)
    ):
        raise ContractViolationError("FHSC manifest row lacks raw payload pointers.")
    loc_match = _LOCATOR_RE.fullmatch(locator)
    cache_key = (rel_path, expected_sha)
    records = payload_cache.get(cache_key) if payload_cache is not None else None
    if records is None:
        root = resolved_root if resolved_root is not None else archive_root.resolve()
        full_path = (root / rel_path).resolve()
        if not full_path.is_relative_to(root):
            raise ContractViolationError(
                "FHSC raw payload path escapes raw archive root."
            )
        content = full_path.read_bytes()
        if payload_file_sha256(content) != expected_sha:
            raise ArchiveIntegrityError(
                "Archived FHSC payload bytes fail SHA-256 check."
            )
        body = json.loads(content)
        parsed_records = body.get("data") if isinstance(body, dict) else None
        if (
            not isinstance(body, dict)
            or body.get("error_code") != "0"
            or not isinstance(parsed_records, list)
        ):
            raise ContractViolationError(
                "Invalid FHSC raw payload structure or locator."
            )
        records = parsed_records
        if payload_cache is not None:
            payload_cache[cache_key] = records
    if loc_match is None or int(loc_match[1]) >= len(records):
        raise ContractViolationError("Invalid FHSC raw payload structure or locator.")
    raw_item = records[int(loc_match[1])]
    if not isinstance(raw_item, dict):
        raise ContractViolationError("FHSC raw record entry must be a JSON object.")
    metrics = dict(raw_item)
    year = metrics.pop("year", None)
    quarter = metrics.pop("quarter", None)
    if year != metadata.fiscal_year or quarter != metadata.fiscal_quarter:
        raise ContractViolationError("FHSC raw record period mismatches manifest row.")
    if canonical_content_sha256(
        metrics, metadata.statement_scope, metadata.period_end_date
    ) != row.get("canonical_content_sha256"):
        raise ArchiveIntegrityError(
            "FHSC canonical content hash mismatch during enrichment."
        )
    return metrics


def _json_evidence_bytes(source_url: str, raw_bytes: bytes) -> bytes:
    """Return raw JSON bytes directly, or wrap raw HTML bytes into a verifiable JSON envelope."""
    try:
        parsed = json.loads(raw_bytes)
        if isinstance(parsed, (dict, list)):
            return raw_bytes
    except (ValueError, UnicodeError):
        pass
    envelope = {
        "source_url": source_url,
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "raw_utf8": raw_bytes.decode("utf-8", errors="replace"),
    }
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _row_revision_no(row: Mapping[str, object]) -> int:
    raw = row.get("revision_no")
    return raw if type(raw) is int else 1


def _load_fhsc_manifest_rows(
    archive: RawArchive, clean_tickers: tuple[str, ...] | None
) -> list[dict[str, object]]:
    try:
        with archive.state.connection() as conn:
            if clean_tickers is None:
                return [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM filings_manifest WHERE source_provider = 'fhsc' "
                        "ORDER BY ticker_at_filing, period_end_date, statement_type, revision_no"
                    ).fetchall()
                ]
            rows: list[dict[str, object]] = []
            for start in range(0, len(clean_tickers), _SQLITE_IN_CHUNK_SIZE):
                chunk = clean_tickers[start : start + _SQLITE_IN_CHUNK_SIZE]
                placeholders = ", ".join("?" for _ in chunk)
                rows.extend(
                    dict(row)
                    for row in conn.execute(
                        f"SELECT * FROM filings_manifest "
                        f"WHERE source_provider = 'fhsc' "
                        f"AND ticker_at_filing IN ({placeholders}) "
                        f"ORDER BY ticker_at_filing, period_end_date, statement_type, revision_no",
                        chunk,
                    ).fetchall()
                )
            rows.sort(
                key=lambda r: (
                    str(r.get("ticker_at_filing", "")),
                    str(r.get("period_end_date", "")),
                    str(r.get("statement_type", "")),
                    _row_revision_no(r),
                )
            )
            return rows
    except sqlite3.Error as exc:
        raise ContractViolationError(
            "Unable to read filings_manifest for enrichment."
        ) from exc


def load_fhsc_standalone_consolidated_slots(
    archive: RawArchive,
    target_tickers: Sequence[str],
) -> dict[str, set[tuple[PeriodType, int, int | None]]]:
    """Return ``(period_type, fiscal_year, fiscal_quarter)`` slots where FHSC BS has zero/null NCI."""
    if not target_tickers or not archive.state.path.exists():
        return {}
    clean_tickers = tuple(
        dict.fromkeys(
            t.strip().upper()
            for t in target_tickers
            if isinstance(t, str) and t.strip()
        )
    )
    if not clean_tickers:
        return {}
    rows: list[dict[str, object]] = []
    try:
        with archive.state.connection() as conn:
            has_table = (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'filings_manifest'"
                ).fetchone()
                is not None
            )
            if not has_table:
                return {}
            for start in range(0, len(clean_tickers), _SQLITE_IN_CHUNK_SIZE):
                chunk = clean_tickers[start : start + _SQLITE_IN_CHUNK_SIZE]
                placeholders = ", ".join("?" for _ in chunk)
                rows.extend(
                    dict(row)
                    for row in conn.execute(
                        f"""SELECT * FROM filings_manifest
                            WHERE source_provider = 'fhsc'
                              AND statement_type = 'BALANCE_SHEET'
                              AND statement_scope = 'CONSOLIDATED'
                              AND ticker_at_filing IN ({placeholders})""",
                        chunk,
                    ).fetchall()
                )
    except sqlite3.Error:
        return {}

    result: dict[str, set[tuple[PeriodType, int, int | None]]] = {
        ticker: set() for ticker in clean_tickers
    }
    resolved_root = archive.root.resolve()
    payload_cache: dict[tuple[str, str], list[object]] = {}
    for row in rows:
        try:
            meta_row = (
                {k: v for k, v in row.items() if k != "manifest_sig_sha256"}
                if "manifest_sig_sha256" in row
                else row
            )
            meta = FilingMetadata.from_mapping(meta_row)
            if meta.period_type not in (PeriodType.QUARTER, PeriodType.ANNUAL):
                continue
            metrics = _extract_fhsc_metrics(
                archive.root,
                row,
                meta,
                resolved_root=resolved_root,
                payload_cache=payload_cache,
            )
        except (
            ContractViolationError,
            ArchiveIntegrityError,
            OSError,
            ValueError,
            TypeError,
            KeyError,
        ):
            continue
        raw_nci = metrics.get("loiichcodongkhongkiemsoat")
        is_standalone = raw_nci is None or (
            isinstance(raw_nci, (int, float))
            and not isinstance(raw_nci, bool)
            and math.isfinite(float(raw_nci))
            and abs(float(raw_nci)) <= 1e-6
        )
        if is_standalone:
            result.setdefault(meta.ticker_at_filing, set()).add(
                (meta.period_type, meta.fiscal_year, meta.fiscal_quarter)
            )
    return result


def _evidence_receipt_sha256(payload: RawPayload, run_id: str | None) -> str:
    digest = payload_file_sha256(payload.content)
    relative_path = f"sha256/{digest[:2]}/{digest}.{payload.extension}"
    fetched_at = payload.fetched_at.astimezone(UTC).isoformat()
    provenance: dict[str, object] = {
        "source_provider": payload.source_provider,
        "connector_name": payload.connector_name,
        "payload_nature": payload.nature,
        "fetched_at": fetched_at,
        "payload_file_sha256": digest,
        "raw_archive_path": relative_path,
        "provider_attrs": dict(payload.provider_attrs),
        "pagination_status": None,
        "deferred_locators": (),
        "filing_metadata": [],
        "run_id": run_id,
    }
    receipt = json.dumps(
        provenance, ensure_ascii=False, sort_keys=True, allow_nan=False
    ).encode("utf-8")
    return payload_file_sha256(receipt)


def enrich_fhsc_manifest_disclosures(
    archive: RawArchive,
    records: Sequence[DisclosureRecord],
    *,
    evidence_payloads: Mapping[str, bytes],
    fetched_at: datetime,
    run_id: str | None = None,
    tickers: Sequence[str] | None = None,
) -> EnrichmentSummary:
    """Upgrade archived FHSC filings to ``EXACT_FILING`` using verified public disclosures.

    Every update flows through ``RawArchive.store`` so the disclosure evidence payload
    and its ``provenance/<receipt_sha256>.json`` receipt are written immutably while
    preserving the original FHSC statement payload pointers for Layer 2 normalization.
    When ``tickers`` is provided, only manifest rows for those tickers are queried via
    SQL ``WHERE ticker_at_filing IN (...)``. Revisions with ``revision_no >= 2`` are
    protected from being overwritten by revision-1 disclosures unless the disclosure
    record explicitly specifies the matching ``revision_no``.
    """
    if fetched_at.utcoffset() is None:
        raise ContractViolationError("Enrichment fetched_at must be timezone-aware.")

    clean_tickers: tuple[str, ...] | None = None
    if tickers is not None:
        normalized_tickers: list[str] = []
        for raw_ticker in tickers:
            cleaned = raw_ticker.strip().upper()
            if not _TICKER_RE.fullmatch(cleaned):
                raise ContractViolationError(
                    "Invalid ticker filter in disclosure enrichment."
                )
            normalized_tickers.append(cleaned)
        clean_tickers = tuple(dict.fromkeys(normalized_tickers))
        if not clean_tickers:
            return EnrichmentSummary(0, 0, 0, 0)

    default_lookup: dict[DisclosureSlot, DisclosureRecord] = {}
    revision_lookup: dict[tuple[DisclosureSlot, int], DisclosureRecord] = {}
    for r in records:
        slot: DisclosureSlot = (
            r.ticker,
            r.period_type,
            r.fiscal_year,
            r.fiscal_quarter,
            r.statement_scope,
        )
        if r.revision_no is None:
            default_lookup[slot] = r
        else:
            revision_lookup[(slot, r.revision_no)] = r
            if r.revision_no == 1:
                default_lookup[slot] = r
    if not default_lookup and not revision_lookup:
        return EnrichmentSummary(0, 0, 0, 0)

    manifest_rows = _load_fhsc_manifest_rows(archive, clean_tickers)

    max_rev_by_key: dict[tuple[str, str, str, str, str], int] = {}
    for row in manifest_rows:
        rev_key = (
            str(row.get("issuer_id", "")),
            str(row.get("statement_type", "")),
            str(row.get("statement_scope", "")),
            str(row.get("period_type", "")),
            str(row.get("period_end_date", "")),
        )
        raw_rev = row.get("revision_no")
        row_rev = raw_rev if type(raw_rev) is int else 1
        if row_rev > max_rev_by_key.get(rev_key, 0):
            max_rev_by_key[rev_key] = row_rev

    grouped_filings: dict[str, list[RawFiling]] = {}
    grouped_hashes: dict[str, list[str]] = {}
    grouped_superseded: dict[
        str, list[tuple[dict[str, object], FilingMetadata, DisclosureRecord]]
    ] = {}
    grouped_source_names: dict[str, str] = {}
    calendar_sync_targets: list[tuple[str, str, str, bool | None]] = []
    matched = 0
    skipped_pre_end = 0
    previously_matching = 0
    resolved_root = archive.root.resolve()
    payload_cache: dict[tuple[str, str], list[object]] = {}

    for row in manifest_rows:
        meta_row = (
            {k: v for k, v in row.items() if k != "manifest_sig_sha256"}
            if "manifest_sig_sha256" in row
            else row
        )
        meta = FilingMetadata.from_mapping(meta_row)
        raw_rev = row.get("revision_no")
        row_rev = raw_rev if type(raw_rev) is int else 1
        slot = (
            meta.ticker_at_filing,
            meta.period_type,
            meta.fiscal_year,
            meta.fiscal_quarter,
            meta.statement_scope,
        )
        if row_rev >= 2:
            rec = revision_lookup.get((slot, row_rev))
        else:
            rec = revision_lookup.get((slot, 1)) or default_lookup.get(slot)
        if rec is None:
            continue
        if rec.source_url not in evidence_payloads:
            raise ContractViolationError(
                f"Missing raw evidence payload bytes for source URL: {rec.source_url}"
            )
        if rec.published_at < meta.period_end_date:
            skipped_pre_end += 1
            continue

        matched += 1
        next_audit_status = (
            rec.audit_status
            if (
                meta.audit_status == AuditStatus.UNKNOWN
                and rec.audit_status is not None
                and rec.audit_status != AuditStatus.UNKNOWN
            )
            else meta.audit_status
        )
        enriched_meta = replace(
            meta,
            published_at=rec.published_at,
            audit_status=next_audit_status,
        )
        raw_stored_late = row.get("is_filed_late")
        stored_late: bool | None = (
            None if raw_stored_late is None else bool(raw_stored_late)
        )
        expected_effective = enriched_meta.effective_available_at.isoformat()
        calendar_changed = (
            stored_late != enriched_meta.is_filed_late
            or row.get("effective_available_at") != expected_effective
        )
        if (
            meta.published_at == rec.published_at
            and row.get("published_at_basis") == "EXACT_FILING"
            and meta.audit_status == next_audit_status
            and not calendar_changed
        ):
            previously_matching += 1
            continue

        metrics = _extract_fhsc_metrics(
            archive.root,
            row,
            meta,
            resolved_root=resolved_root,
            payload_cache=payload_cache,
        )
        rev_key = (
            meta.issuer_id,
            str(meta.statement_type),
            str(meta.statement_scope),
            str(meta.period_type),
            meta.period_end_date.isoformat(),
        )
        grouped_source_names[rec.source_url] = rec.source_name
        if row_rev < max_rev_by_key[rev_key]:
            # Enriching a superseded revision (e.g. rev 1 when rev 2 exists) must not
            # flow through archive.store(filings=...) or archive.store would allocate a
            # spurious rev 3 with rev 1's older metrics.
            grouped_superseded.setdefault(rec.source_url, []).append(
                (row, enriched_meta, rec)
            )
        else:
            grouped_filings.setdefault(rec.source_url, []).append(
                RawFiling(
                    metadata=enriched_meta,
                    metrics=metrics,
                    locator=rec.raw_locator,
                )
            )
            grouped_hashes.setdefault(rec.source_url, []).append(
                str(row["canonical_content_sha256"])
            )
            if calendar_changed and isinstance(row.get("filing_id"), str):
                calendar_sync_targets.append(
                    (
                        str(row["filing_id"]),
                        rec.published_at.isoformat(),
                        expected_effective,
                        enriched_meta.is_filed_late,
                    )
                )

    all_urls = tuple(dict.fromkeys([*grouped_filings, *grouped_superseded]))
    fetched_utc_iso = fetched_at.astimezone(UTC).isoformat()
    for source_url in all_urls:
        raw_bytes = evidence_payloads[source_url]
        payload_bytes = _json_evidence_bytes(source_url, raw_bytes)
        payload = RawPayload(
            content=payload_bytes,
            extension="json",
            source_provider="fhsc",
            connector_name=DISCLOSURE_CONNECTOR,
            nature="PROVIDER_RAW_JSON",
            fetched_at=fetched_at,
            provider_attrs={
                "disclosure_source": grouped_source_names[source_url],
                "source_url": source_url,
                "raw_evidence_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            },
        )
        filings = grouped_filings.get(source_url, [])
        if filings:
            archive.store(
                RawFilingBundle(payload=payload, filings=tuple(filings)),
                run_id=run_id,
                precomputed_canonical_hashes=tuple(grouped_hashes[source_url]),
            )
        superseded_items = grouped_superseded.get(source_url, [])
        if superseded_items:
            archive.store(
                RawFilingBundle(payload=payload, filings=()),
                run_id=run_id,
            )
            receipt_sha256 = _evidence_receipt_sha256(payload, run_id)
            incoming_dt = fetched_at.astimezone(UTC)
            with archive.state.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                for row, enriched_meta, rec in superseded_items:
                    filing_id = str(row["filing_id"])
                    attrs = json.loads(str(row.get("provider_attrs_json") or "{}"))
                    evidence = attrs.get("metadata_evidence", {})
                    if not isinstance(evidence, dict):
                        evidence = {}
                    pub_ref = evidence.get("published_at")
                    prior_pub_str = (
                        pub_ref.get("fetched_at")
                        if isinstance(pub_ref, dict)
                        and isinstance(pub_ref.get("fetched_at"), str)
                        else str(row.get("fetched_at") or "")
                    )
                    if prior_pub_str and incoming_dt < datetime.fromisoformat(
                        prior_pub_str
                    ).astimezone(UTC):
                        continue
                    evidence["published_at"] = {
                        "receipt_sha256": receipt_sha256,
                        "raw_record_locator": rec.raw_locator,
                        "fetched_at": fetched_utc_iso,
                    }
                    if enriched_meta.audit_status != AuditStatus.UNKNOWN:
                        evidence["audit_status"] = {
                            "receipt_sha256": receipt_sha256,
                            "raw_record_locator": rec.raw_locator,
                            "fetched_at": fetched_utc_iso,
                        }
                    attrs["metadata_evidence"] = evidence
                    attrs_json = json.dumps(attrs, ensure_ascii=False, allow_nan=False)
                    pub_iso = (
                        enriched_meta.published_at.isoformat()
                        if enriched_meta.published_at is not None
                        else None
                    )
                    eff_iso = enriched_meta.effective_available_at.isoformat()
                    sig_row: dict[str, object] = {
                        **row,
                        "audit_status": str(enriched_meta.audit_status),
                        "published_at": pub_iso,
                        "published_at_basis": enriched_meta.published_at_basis,
                        "effective_available_at": eff_iso,
                        "is_filed_late": enriched_meta.is_filed_late,
                        "provider_attrs_json": attrs_json,
                    }
                    sig_sha = compute_manifest_sig_sha256(sig_row)
                    conn.execute(
                        """UPDATE filings_manifest
                           SET audit_status = ?,
                               published_at = ?,
                               published_at_basis = ?,
                               effective_available_at = ?,
                               is_filed_late = ?,
                               provider_attrs_json = ?,
                               manifest_sig_sha256 = ?
                           WHERE filing_id = ?""",
                        (
                            str(enriched_meta.audit_status),
                            pub_iso,
                            enriched_meta.published_at_basis,
                            eff_iso,
                            enriched_meta.is_filed_late,
                            attrs_json,
                            sig_sha,
                            filing_id,
                        ),
                    )

    if calendar_sync_targets:
        sig_cols = ", ".join(MANIFEST_SIGNATURE_FIELDS)
        with archive.state.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for filing_id, pub_iso, eff_iso, is_late in calendar_sync_targets:
                cur = conn.execute(
                    """UPDATE filings_manifest
                       SET effective_available_at = ?,
                           is_filed_late = ?,
                           manifest_sig_sha256 = NULL
                       WHERE filing_id = ?
                         AND published_at = ?
                         AND published_at_basis = 'EXACT_FILING'""",
                    (eff_iso, is_late, filing_id, pub_iso),
                )
                if cur.rowcount > 0:
                    refreshed_row = conn.execute(
                        f"SELECT {sig_cols} FROM filings_manifest WHERE filing_id = ?",
                        (filing_id,),
                    ).fetchone()
                    if refreshed_row is not None:
                        sig_sha = compute_manifest_sig_sha256(dict(refreshed_row))
                        conn.execute(
                            "UPDATE filings_manifest SET manifest_sig_sha256 = ? WHERE filing_id = ?",
                            (sig_sha, filing_id),
                        )

    upgraded = matched - previously_matching
    return EnrichmentSummary(
        matched_filings=matched,
        upgraded_filings=upgraded,
        unchanged_filings=previously_matching,
        skipped_pre_period_end=skipped_pre_end,
    )
