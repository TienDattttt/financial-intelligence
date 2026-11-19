"""Enrich archived FHSC filings with verified public disclosure dates via RawArchive receipts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path

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
from finintel.ingestion.hasher import canonical_content_sha256, payload_file_sha256

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
    m_iso = _URL_YYYYMMDD_RE.search(basename)
    if m_iso is not None:
        parsed = _safe_calendar_date(int(m_iso[1]), int(m_iso[2]), int(m_iso[3]))
        if parsed is not None:
            return parsed
    m_ubck = _URL_DDMMYYYY_DASH_TIME_RE.search(basename)
    if m_ubck is not None:
        parsed = _safe_calendar_date(int(m_ubck[3]), int(m_ubck[2]), int(m_ubck[1]))
        if parsed is not None:
            return parsed
    m_suf14 = _URL_DDMMYYYY_14_SUFFIX_RE.search(basename)
    if m_suf14 is not None:
        parsed = _safe_calendar_date(int(m_suf14[3]), int(m_suf14[2]), int(m_suf14[1]))
        if parsed is not None:
            return parsed
    m_epoch = _URL_EPOCH10_SUFFIX_RE.search(basename)
    if m_epoch is not None:
        epoch_sec = int(m_epoch[1])
        return datetime.fromtimestamp(epoch_sec, tz=VN_TZ).date()
    return None


def parse_cafef_bctc_disclosures(
    ticker: str,
    content: bytes,
    *,
    source_url: str,
    max_date: date | None = None,
) -> tuple[DisclosureRecord, ...]:
    """Extract explicit filing dates embedded in CafeF BCTC document table rows.

    Short archival filenames without full calendar dates (e.g. ``FPT_21CN_BCTC_HNKT.pdf``
    or ``FPT_22Q2_BCTC_HNSX_2022-102346.pdf``) are strictly ignored rather than guessed.
    """
    clean_ticker = ticker.strip().upper()
    if not _TICKER_RE.fullmatch(clean_ticker):
        raise ContractViolationError("Invalid ticker for CafeF disclosure parsing.")
    html = content.decode("utf-8", errors="replace")

    chosen: dict[
        tuple[StatementScope, PeriodType, int, int | None], tuple[int, DisclosureRecord]
    ] = {}
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

        if "hợp nhất" in title_lower:
            scope = StatementScope.CONSOLIDATED
        elif (
            "công ty mẹ" in title_lower
            or "bctc riêng" in title_lower
            or "riêng lẻ" in title_lower
        ):
            scope = StatementScope.SEPARATE
        else:
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

        pub_date = _extract_cafef_filename_date(href)
        if pub_date is None or pub_date < date(year, 1, 1):
            continue
        if max_date is not None and pub_date > max_date:
            continue

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

    return tuple(item[1] for item in chosen.values())


DisclosureSlot = tuple[str, PeriodType, int, int | None, StatementScope]


def merge_disclosure_records(
    vietcap_records: Sequence[DisclosureRecord],
    cafef_records: Sequence[DisclosureRecord] = (),
) -> dict[DisclosureSlot, DisclosureRecord]:
    """Combine Vietcap IQ and CafeF BCTC disclosure records deterministically.

    Vietcap IQ provides structured ``publicDate`` history from 2018 onward. On recent
    fourth quarters where Vietcap IQ overwrites ``Q4.publicDate`` with the subsequent
    Annual Audited ``publicDate``, a verified CafeF Unaudited Q4 disclosure record with
    an earlier valid date takes precedence so Q4 retains its true quarterly filing date.
    """
    merged: dict[DisclosureSlot, DisclosureRecord] = {}
    annual_vci_dates: dict[tuple[str, int, StatementScope], date] = {}
    for rec in vietcap_records:
        slot: DisclosureSlot = (
            rec.ticker,
            rec.period_type,
            rec.fiscal_year,
            rec.fiscal_quarter,
            rec.statement_scope,
        )
        merged[slot] = rec
        if rec.period_type == PeriodType.ANNUAL:
            annual_vci_dates[(rec.ticker, rec.fiscal_year, rec.statement_scope)] = (
                rec.published_at
            )

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
        if rec.period_type == PeriodType.QUARTER and rec.fiscal_quarter == 4:
            annual_dt = annual_vci_dates.get(
                (rec.ticker, rec.fiscal_year, rec.statement_scope)
            )
            if (
                annual_dt is not None
                and current.published_at == annual_dt
                and rec.published_at < current.published_at
            ):
                merged[slot] = rec
    return merged


def _extract_fhsc_metrics(
    archive_root: Path, row: Mapping[str, object], metadata: FilingMetadata
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
    full_path = (archive_root / rel_path).resolve()
    if not full_path.is_relative_to(archive_root.resolve()):
        raise ContractViolationError("FHSC raw payload path escapes raw archive root.")
    content = full_path.read_bytes()
    if payload_file_sha256(content) != expected_sha:
        raise ArchiveIntegrityError("Archived FHSC payload bytes fail SHA-256 check.")

    loc_match = _LOCATOR_RE.fullmatch(locator)
    body = json.loads(content)
    records = body.get("data") if isinstance(body, dict) else None
    if (
        loc_match is None
        or not isinstance(body, dict)
        or body.get("error_code") != "0"
        or not isinstance(records, list)
        or int(loc_match[1]) >= len(records)
    ):
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


def enrich_fhsc_manifest_disclosures(
    archive: RawArchive,
    records: Sequence[DisclosureRecord],
    *,
    evidence_payloads: Mapping[str, bytes],
    fetched_at: datetime,
    run_id: str | None = None,
) -> EnrichmentSummary:
    """Upgrade archived FHSC filings to ``EXACT_FILING`` using verified public disclosures.

    Every update flows through ``RawArchive.store`` so the disclosure evidence payload
    and its ``provenance/<receipt_sha256>.json`` receipt are written immutably while
    preserving the original FHSC statement payload pointers for Layer 2 normalization.
    """
    if fetched_at.utcoffset() is None:
        raise ContractViolationError("Enrichment fetched_at must be timezone-aware.")

    lookup: dict[DisclosureSlot, DisclosureRecord] = {
        (
            r.ticker,
            r.period_type,
            r.fiscal_year,
            r.fiscal_quarter,
            r.statement_scope,
        ): r
        for r in records
    }
    if not lookup:
        return EnrichmentSummary(0, 0, 0, 0)

    try:
        with archive.state.connection() as conn:
            manifest_rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM filings_manifest WHERE source_provider = 'fhsc' "
                    "ORDER BY ticker_at_filing, period_end_date, statement_type, revision_no"
                ).fetchall()
            ]
    except sqlite3.Error as exc:
        raise ContractViolationError(
            "Unable to read filings_manifest for enrichment."
        ) from exc

    grouped_filings: dict[str, list[RawFiling]] = {}
    grouped_source_names: dict[str, str] = {}
    matched = 0
    skipped_pre_end = 0
    previously_matching = 0

    for row in manifest_rows:
        meta = FilingMetadata.from_mapping(row)
        slot: DisclosureSlot = (
            meta.ticker_at_filing,
            meta.period_type,
            meta.fiscal_year,
            meta.fiscal_quarter,
            meta.statement_scope,
        )
        rec = lookup.get(slot)
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
        if (
            meta.published_at == rec.published_at
            and row.get("published_at_basis") == "EXACT_FILING"
        ):
            previously_matching += 1

        metrics = _extract_fhsc_metrics(archive.root, row, meta)
        enriched_meta = replace(meta, published_at=rec.published_at)
        grouped_filings.setdefault(rec.source_url, []).append(
            RawFiling(
                metadata=enriched_meta,
                metrics=metrics,
                locator=rec.raw_locator,
            )
        )
        grouped_source_names[rec.source_url] = rec.source_name

    for source_url, filings in grouped_filings.items():
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
        archive.store(
            RawFilingBundle(payload=payload, filings=tuple(filings)),
            run_id=run_id,
        )

    upgraded = matched - previously_matching
    return EnrichmentSummary(
        matched_filings=matched,
        upgraded_filings=upgraded,
        unchanged_filings=previously_matching,
        skipped_pre_period_end=skipped_pre_end,
    )
