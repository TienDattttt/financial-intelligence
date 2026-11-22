"""Curated audited consolidated PDF catalog, Port 2 CDN/ZIP guards, and SHA-256 CAS store (UI-08a)."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.ports.documents import (
    MAX_PDF_ZIP_ARCHIVE_BYTES,
    extract_pdf_members_from_zip,
    validate_pdf_cdn_url,
)

DOC_TYPE_AUDITED_ANNUAL_CONSOLIDATED = "AUDITED_ANNUAL_CONSOLIDATED_FS"
STATEMENT_SCOPE_CONSOLIDATED = "CONSOLIDATED"
_PDF_MAGIC_PREFIX = b"%PDF-"
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_TICKER_RE = re.compile(r"^[A-Z0-9]{1,16}$")

_CAFEF_LEGACY_CDN_HOST = "cafefnew.mediacdn.vn"
_CAFEF_CANONICAL_CDN_HOST = "cafef1.mediacdn.vn"

_TR_RE = re.compile(r"<tr[^>]*>([\s\S]*?)</tr>", flags=re.IGNORECASE)
_TD_RE = re.compile(r"<td[^>]*>([\s\S]*?)</td>", flags=re.IGNORECASE)
_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", flags=re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_PERIOD_ANNUAL_RE = re.compile(r"^CN/([12][0-9]{3})$", flags=re.IGNORECASE)

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

_EXCLUDED_TITLE_MARKERS: tuple[str, ...] = (
    "tiếng anh",
    "tieng anh",
    "english",
    "bản anh",
    "tóm tắt",
    "tom tat",
    "giải trình",
    "giai trinh",
    "soát xét",
    "soat xet",
    "tự lập",
    "tu lap",
)
_SEPARATE_ONLY_MARKERS: tuple[str, ...] = (
    "công ty mẹ",
    "cong ty me",
    "bctc riêng",
    "riêng lẻ",
    "rieng le",
)

ArchiveFormat = Literal["pdf", "zip"]

CURATED_PDFS_MANIFEST_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "fiscal_year": pl.Int32,
        "fiscal_quarter": pl.Int32,
        "statement_scope": pl.String,
        "doc_type": pl.String,
        "source_url": pl.String,
        "file_size_bytes": pl.Int64,
        "content_sha256": pl.String,
        "local_rel_path": pl.String,
        "is_valid_pdf": pl.Boolean,
        "cohort": pl.String,
        "extracted_from_zip": pl.Boolean,
        "zip_member_name": pl.String,
        "source_title": pl.String,
    }
)


@dataclass(frozen=True, slots=True)
class PdfCandidate:
    """Candidate audited annual consolidated BCTC download link parsed from CafeF HTML."""

    issuer_id: str
    fiscal_year: int
    fiscal_quarter: int | None
    statement_scope: str
    doc_type: str
    title: str
    raw_url: str
    source_url: str
    archive_format: ArchiveFormat
    row_index: int


@dataclass(frozen=True, slots=True)
class CuratedPdfRecord:
    """Verified, immutably archived PDF record matching ``CURATED_PDFS_MANIFEST_SCHEMA``."""

    issuer_id: str
    fiscal_year: int
    fiscal_quarter: int | None
    statement_scope: str
    doc_type: str
    source_url: str
    file_size_bytes: int
    content_sha256: str
    local_rel_path: str
    is_valid_pdf: bool
    cohort: str
    extracted_from_zip: bool
    zip_member_name: str | None
    source_title: str


def verify_pdf_bytes(payload: bytes) -> bool:
    """Return True when ``payload`` is non-empty and starts with the ``%PDF-`` magic header."""
    return len(payload) >= len(_PDF_MAGIC_PREFIX) and payload.startswith(
        _PDF_MAGIC_PREFIX
    )


def compute_sha256(payload: bytes) -> str:
    """Return the lowercase 64-char hex SHA-256 digest of ``payload``."""
    return hashlib.sha256(payload).hexdigest()


def normalize_cafef_pdf_url(raw_url: str) -> str:
    """Canonicalize a CafeF BCTC link and enforce the Port 2 PDF CDN allowlist (H-02).

    CafeF's archived HTML tables emit ``https://cafefnew.mediacdn.vn//Images/...`` links
    whose live CDN mirror is ``cafef1.mediacdn.vn`` (in ``PDF_CDN_ALLOWED_HOSTS``).
    Only that exact legacy CafeF host is remapped; every other host must already be
    present in ``PDF_CDN_ALLOWED_HOSTS`` or ``ContractViolationError`` is raised.
    """
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise ContractViolationError("PDF URL must be a non-empty string.")
    cleaned = raw_url.strip()
    if any(ch.isspace() for ch in cleaned):
        raise ContractViolationError("PDF URL cannot contain whitespace.")

    parsed = urlsplit(cleaned)
    if parsed.scheme.lower() != "https":
        raise ContractViolationError("PDF CDN URL must use the HTTPS scheme.")
    if parsed.username is not None or parsed.password is not None:
        raise ContractViolationError("PDF CDN URL cannot include embedded credentials.")
    if parsed.port not in (None, 443):
        raise ContractViolationError(
            "PDF CDN URL must use the standard HTTPS port 443."
        )

    host = (parsed.hostname or "").lower()
    canonical_host = (
        _CAFEF_CANONICAL_CDN_HOST if host == _CAFEF_LEGACY_CDN_HOST else host
    )
    normalized_path = re.sub(r"/{2,}", "/", parsed.path)
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path

    canonical_url = urlunsplit(
        ("https", canonical_host, normalized_path, parsed.query, "")
    )
    return validate_pdf_cdn_url(canonical_url)


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


def _detect_archive_format(url: str) -> ArchiveFormat | None:
    path = urlsplit(url).path.lower()
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith(".zip"):
        return "zip"
    return None


def _is_audited_consolidated_annual_title(title: str) -> bool:
    lower = title.lower()
    if "hợp nhất" not in lower:
        return False
    if "kiểm toán" not in lower:
        return False
    if "báo cáo tài chính" not in lower and "bctc" not in lower:
        return False
    if any(marker in lower for marker in _EXCLUDED_TITLE_MARKERS):
        return False
    if any(marker in lower for marker in _SEPARATE_ONLY_MARKERS):
        return False
    return True


def extract_audited_consolidated_annual_candidates(
    ticker: str,
    html_text: str,
) -> tuple[PdfCandidate, ...]:
    """Extract strictly filtered audited annual CONSOLIDATED BCTC links from CafeF HTML.

    Enforces:
    - ``period == CN/YYYY`` (annual only, ``fiscal_quarter=None``)
    - ``statement_scope == "CONSOLIDATED"`` (rejects separate/parent-only filings)
    - audited Vietnamese BCTC only (rejects reviewed/unaudited/English/explanation-only)
    - Port 2 CDN allowlist via ``normalize_cafef_pdf_url``
    - Supported formats: ``.pdf`` or ``.zip`` (prefers direct ``.pdf`` over ``.zip`` per year)
    """
    clean_ticker = ticker.strip().upper()
    if not _TICKER_RE.fullmatch(clean_ticker):
        raise ContractViolationError(f"Invalid issuer ticker: {ticker!r}.")

    by_year: dict[int, PdfCandidate] = {}
    for row_idx, tr_match in enumerate(_TR_RE.finditer(html_text)):
        tr_html = tr_match[1]
        tds = [
            re.sub(r"\s+", " ", unescape(_TAG_RE.sub("", td))).strip()
            for td in _TD_RE.findall(tr_html)
        ]
        if len(tds) < 2:
            continue
        title = tds[0]
        period_text = tds[1].strip()
        period_match = _PERIOD_ANNUAL_RE.fullmatch(period_text)
        if period_match is None:
            continue
        if not _is_audited_consolidated_annual_title(title):
            continue

        href_match = _HREF_RE.search(tr_html)
        if href_match is None:
            continue
        raw_href = href_match[1].strip()
        if not _cafef_row_matches_ticker(tr_html, raw_href, clean_ticker):
            continue

        try:
            canonical_url = normalize_cafef_pdf_url(raw_href)
        except ContractViolationError:
            continue

        archive_format = _detect_archive_format(canonical_url)
        if archive_format is None:
            continue

        fiscal_year = int(period_match[1])
        candidate = PdfCandidate(
            issuer_id=clean_ticker,
            fiscal_year=fiscal_year,
            fiscal_quarter=None,
            statement_scope=STATEMENT_SCOPE_CONSOLIDATED,
            doc_type=DOC_TYPE_AUDITED_ANNUAL_CONSOLIDATED,
            title=title,
            raw_url=raw_href,
            source_url=canonical_url,
            archive_format=archive_format,
            row_index=row_idx,
        )

        existing = by_year.get(fiscal_year)
        if existing is None or (
            existing.archive_format == "zip" and candidate.archive_format == "pdf"
        ):
            by_year[fiscal_year] = candidate

    return tuple(
        sorted(by_year.values(), key=lambda item: item.fiscal_year, reverse=True)
    )


def select_candidates_for_years(
    candidates: Sequence[PdfCandidate],
    preferred_years: Sequence[int],
    *,
    max_items: int,
    fallback_to_latest: bool = True,
) -> tuple[PdfCandidate, ...]:
    """Select up to ``max_items`` candidates matching ``preferred_years`` in order."""
    if max_items <= 0 or not candidates:
        return ()

    by_year = {item.fiscal_year: item for item in candidates}
    selected: list[PdfCandidate] = []
    seen_years: set[int] = set()

    for year in preferred_years:
        match = by_year.get(year)
        if match is not None and year not in seen_years:
            selected.append(match)
            seen_years.add(year)
            if len(selected) >= max_items:
                return tuple(selected)

    if fallback_to_latest and len(selected) < max_items:
        for item in sorted(candidates, key=lambda c: c.fiscal_year, reverse=True):
            if item.fiscal_year not in seen_years:
                selected.append(item)
                seen_years.add(item.fiscal_year)
                if len(selected) >= max_items:
                    break

    return tuple(selected)


def _score_zip_pdf_member(member_name: str, payload: bytes) -> tuple[int, int]:
    lower = member_name.lower()
    score = 0
    if "hop_nhat" in lower or "hopnhat" in lower or "hn" in lower:
        score += 3
    if "kiem_toan" in lower or "kiemtoan" in lower or "kt" in lower:
        score += 2
    if "bctc" in lower or "baocaotaichinh" in lower:
        score += 1
    if "giai_trinh" in lower or "giaitrinh" in lower or "eng" in lower:
        score -= 4
    return (score, len(payload))


def extract_verified_pdf_payload(
    raw_bytes: bytes,
    *,
    archive_format: ArchiveFormat,
) -> tuple[bytes, bool, str | None]:
    """Verify raw downloaded bytes as a valid PDF or safely extract one from a ZIP (H-02)."""
    if archive_format == "pdf":
        if not verify_pdf_bytes(raw_bytes):
            raise ContractViolationError(
                "Downloaded PDF payload does not start with valid %PDF- magic bytes."
            )
        return raw_bytes, False, None

    if archive_format == "zip":
        members = extract_pdf_members_from_zip(raw_bytes)
        best_name, best_payload = max(
            members.items(),
            key=lambda item: _score_zip_pdf_member(item[0], item[1]),
        )
        if not verify_pdf_bytes(best_payload):
            raise ContractViolationError(
                f"Extracted ZIP member {best_name!r} does not start with %PDF-."
            )
        return best_payload, True, best_name

    raise ContractViolationError(f"Unsupported archive format: {archive_format!r}.")


def store_immutable_pdf(
    pdf_bytes: bytes,
    *,
    output_root: Path,
) -> tuple[str, str, int]:
    """Persist verified PDF bytes immutably at ``sha256/<2-char>/<sha256>.pdf``."""
    if not verify_pdf_bytes(pdf_bytes):
        raise ContractViolationError(
            "Cannot archive payload without valid %PDF- magic bytes."
        )

    digest = compute_sha256(pdf_bytes)
    rel_path = f"sha256/{digest[:2]}/{digest}.pdf"
    target_path = output_root / "sha256" / digest[:2] / f"{digest}.pdf"
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if target_path.exists():
        existing_bytes = target_path.read_bytes()
        if compute_sha256(existing_bytes) != digest:
            raise ContractViolationError(
                f"Immutable CAS integrity mismatch for existing artifact {rel_path}."
            )
        return digest, rel_path, len(pdf_bytes)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{digest[:12]}.",
        suffix=".tmp",
        dir=target_path.parent,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(pdf_bytes)
        os.replace(tmp_path, target_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise

    return digest, rel_path, len(pdf_bytes)


def download_curated_pdf(
    candidate: PdfCandidate,
    *,
    output_root: Path,
    cohort: str,
    client: httpx.Client,
    max_download_bytes: int = MAX_PDF_ZIP_ARCHIVE_BYTES,
) -> CuratedPdfRecord:
    """Download, verify, and immutably archive a single curated PDF candidate."""
    validated_url = validate_pdf_cdn_url(candidate.source_url)
    if candidate.statement_scope != STATEMENT_SCOPE_CONSOLIDATED:
        raise ContractViolationError(
            "Curated PDF catalog only accepts CONSOLIDATED statements."
        )
    if candidate.fiscal_quarter is not None:
        raise ContractViolationError(
            "Curated PDF catalog only accepts annual filings (fiscal_quarter=None)."
        )

    chunks: list[bytes] = []
    total_bytes = 0
    with client.stream("GET", validated_url, follow_redirects=False) as response:
        if response.status_code != 200:
            raise ContractViolationError(
                f"PDF download failed with HTTP {response.status_code} for {validated_url}."
            )
        for chunk in response.iter_bytes():
            total_bytes += len(chunk)
            if total_bytes > max_download_bytes:
                raise ContractViolationError(
                    f"Downloaded payload exceeds max allowed size ({max_download_bytes} bytes)."
                )
            chunks.append(chunk)

    raw_bytes = b"".join(chunks)
    pdf_bytes, from_zip, zip_member = extract_verified_pdf_payload(
        raw_bytes,
        archive_format=candidate.archive_format,
    )
    digest, rel_path, file_size = store_immutable_pdf(
        pdf_bytes, output_root=output_root
    )

    return CuratedPdfRecord(
        issuer_id=candidate.issuer_id,
        fiscal_year=candidate.fiscal_year,
        fiscal_quarter=None,
        statement_scope=STATEMENT_SCOPE_CONSOLIDATED,
        doc_type=candidate.doc_type,
        source_url=validated_url,
        file_size_bytes=file_size,
        content_sha256=digest,
        local_rel_path=rel_path,
        is_valid_pdf=True,
        cohort=cohort,
        extracted_from_zip=from_zip,
        zip_member_name=zip_member,
        source_title=candidate.title,
    )


def build_manifest_dataframe(records: Iterable[CuratedPdfRecord]) -> pl.DataFrame:
    """Build and validate the Polars manifest DataFrame for curated PDFs."""
    record_list = list(records)
    if not record_list:
        return pl.DataFrame(schema=CURATED_PDFS_MANIFEST_SCHEMA)

    seen_slots: set[tuple[str, int, str]] = set()
    rows: list[dict[str, object]] = []
    for rec in record_list:
        if not _TICKER_RE.fullmatch(rec.issuer_id):
            raise ContractViolationError(f"Invalid issuer_id: {rec.issuer_id!r}.")
        if rec.statement_scope != STATEMENT_SCOPE_CONSOLIDATED:
            raise ContractViolationError(
                f"Manifest record for {rec.issuer_id} must be CONSOLIDATED."
            )
        if rec.fiscal_quarter is not None:
            raise ContractViolationError(
                f"Manifest record for {rec.issuer_id} must be annual (fiscal_quarter=None)."
            )
        if not rec.is_valid_pdf or rec.file_size_bytes <= 0:
            raise ContractViolationError(
                f"Manifest record for {rec.issuer_id} FY{rec.fiscal_year} is not a valid PDF."
            )
        if not _SHA256_HEX_RE.fullmatch(rec.content_sha256):
            raise ContractViolationError(
                f"Invalid SHA-256 digest in manifest: {rec.content_sha256!r}."
            )
        validate_pdf_cdn_url(rec.source_url)
        slot = (rec.issuer_id, rec.fiscal_year, rec.statement_scope)
        if slot in seen_slots:
            raise ContractViolationError(f"Duplicate manifest slot detected: {slot!r}.")
        seen_slots.add(slot)
        rows.append(
            {
                "issuer_id": rec.issuer_id,
                "fiscal_year": rec.fiscal_year,
                "fiscal_quarter": rec.fiscal_quarter,
                "statement_scope": rec.statement_scope,
                "doc_type": rec.doc_type,
                "source_url": rec.source_url,
                "file_size_bytes": rec.file_size_bytes,
                "content_sha256": rec.content_sha256,
                "local_rel_path": rec.local_rel_path,
                "is_valid_pdf": rec.is_valid_pdf,
                "cohort": rec.cohort,
                "extracted_from_zip": rec.extracted_from_zip,
                "zip_member_name": rec.zip_member_name,
                "source_title": rec.source_title,
            }
        )

    frame = pl.DataFrame(rows, schema=CURATED_PDFS_MANIFEST_SCHEMA)
    return frame.sort(
        ["cohort", "issuer_id", "fiscal_year"], descending=[False, False, True]
    )


def write_manifest_parquet(
    records: Iterable[CuratedPdfRecord],
    manifest_path: Path,
) -> pl.DataFrame:
    """Validate and write ``curated_pdfs_manifest.parquet`` atomically."""
    frame = build_manifest_dataframe(records)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".curated_pdfs_manifest.",
        suffix=".parquet.tmp",
        dir=manifest_path.parent,
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        frame.write_parquet(tmp_path)
        os.replace(tmp_path, manifest_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    return frame


def verify_stored_manifest_files(
    manifest: pl.DataFrame,
    *,
    output_root: Path,
) -> dict[str, int]:
    """Re-verify every archived PDF referenced by ``manifest`` on disk."""
    checked = 0
    invalid = 0
    for row in manifest.iter_rows(named=True):
        rel_path = str(row["local_rel_path"])
        expected_sha = str(row["content_sha256"])
        expected_size = int(row["file_size_bytes"])
        file_path = output_root / rel_path
        if not file_path.is_file():
            invalid += 1
            continue
        payload = file_path.read_bytes()
        if (
            not verify_pdf_bytes(payload)
            or len(payload) != expected_size
            or compute_sha256(payload) != expected_sha
        ):
            invalid += 1
        else:
            checked += 1
    return {"verified_ok": checked, "invalid_or_missing": invalid}


def render_curated_pdfs_audit_markdown(
    manifest: pl.DataFrame,
    *,
    case_study_tickers: Sequence[str],
    output_root: Path,
    failures: Sequence[str] = (),
) -> str:
    """Render ``docs/refactor/curated_pdfs_audit.md`` strictly from measured manifest stats."""
    total_pdfs = manifest.height
    unique_issuers = manifest["issuer_id"].n_unique() if total_pdfs > 0 else 0
    total_bytes = int(manifest["file_size_bytes"].sum()) if total_pdfs > 0 else 0
    total_mb = total_bytes / (1024 * 1024)
    zip_count = (
        int(manifest.filter(pl.col("extracted_from_zip")).height)
        if total_pdfs > 0
        else 0
    )
    zip_pct = (zip_count / total_pdfs * 100.0) if total_pdfs > 0 else 0.0

    case_study_set = {t.upper() for t in case_study_tickers}
    covered_case_studies = (
        sorted(set(manifest["issuer_id"].to_list()).intersection(case_study_set))
        if total_pdfs > 0
        else []
    )
    missing_case_studies = sorted(case_study_set.difference(covered_case_studies))

    verification = verify_stored_manifest_files(manifest, output_root=output_root)

    lines: list[str] = [
        "# Báo Cáo Kiểm Toán Kho PDF BCTC Hợp Nhất Kiểm Toán (`UI-08a`)",
        "",
        "> **Phạm vi:** Curated Evidence Pack (Port 2 — `UI-08a`) phục vụ Grounded PDF RAG (`UI-08b`).  ",
        "> **Nguồn sự thật:** 100% file PDF thật từ CDN CafeF (`cafef1.mediacdn.vn`, thuộc `PDF_CDN_ALLOWED_HOSTS`), lưu bất biến theo `SHA-256`.",
        "",
        "## 1. Tổng Quan Kiểm Định",
        "",
        "| Chỉ số kiểm toán | Giá trị đo thực tế |",
        "|---|---|",
        f"| Tổng số file PDF BCTC Hợp nhất kiểm toán hợp lệ | **{total_pdfs}** |",
        f"| Tổng số doanh nghiệp phủ được | **{unique_issuers}** |",
        f"| Độ phủ nhóm Case Studies lịch sử (`{len(case_study_set)}` mã) | **{len(covered_case_studies)}/{len(case_study_set)}** ({len(covered_case_studies) / max(len(case_study_set), 1) * 100:.1f}%) |",
        f"| Tổng dung lượng lưu trữ bất biến | **{total_mb:.2f} MB** (`{total_bytes:,}` bytes) |",
        f"| Số file giải nén từ `.zip` (có chống zip-bomb `H-02`) | **{zip_count}/{total_pdfs}** ({zip_pct:.1f}%) |",
        f"| Kiểm chứng lại magic bytes `%PDF-` + `SHA-256` trên đĩa | **{verification['verified_ok']}/{total_pdfs}** hợp lệ, **{verification['invalid_or_missing']}** lỗi/rỗng |",
        f"| Số link tải thất bại | **{len(failures)}** |",
        "",
    ]

    if missing_case_studies:
        lines.append(
            f"- **Case Studies chưa có PDF:** `{', '.join(missing_case_studies)}`"
        )
        lines.append("")
    else:
        lines.append(
            f"- **Case Studies phủ đủ 100% ({len(covered_case_studies)}/{len(case_study_set)}):** `{', '.join(covered_case_studies)}`"
        )
        lines.append("")

    lines.extend(
        [
            "## 2. Phân Bổ Theo Nhóm (`cohort`)",
            "",
            "| Nhóm | Số doanh nghiệp | Số PDF | Dung lượng (MB) |",
            "|---|---:|---:|---:|",
        ]
    )

    if total_pdfs > 0:
        cohort_summary = (
            manifest.group_by("cohort")
            .agg(
                pl.col("issuer_id").n_unique().alias("issuers"),
                pl.len().alias("pdfs"),
                pl.col("file_size_bytes").sum().alias("bytes"),
            )
            .sort("cohort")
        )
        for row in cohort_summary.iter_rows(named=True):
            mb = int(row["bytes"]) / (1024 * 1024)
            lines.append(
                f"| `{row['cohort']}` | {row['issuers']} | {row['pdfs']} | {mb:.2f} |"
            )

    lines.extend(
        [
            "",
            "## 3. Danh Mục Chi Tiết Các File PDF Đã Lưu Bất Biến",
            "",
            "| Mã CK | Năm TC | Nhóm | Phạm vi | Kích thước (MB) | Từ ZIP | SHA-256 (12 ký tự đầu) | Đường dẫn tương đối |",
            "|---|---:|---|---|---:|---|---|---|",
        ]
    )

    for row in manifest.iter_rows(named=True):
        size_mb = int(row["file_size_bytes"]) / (1024 * 1024)
        sha_short = str(row["content_sha256"])[:12]
        zip_flag = "Có" if bool(row["extracted_from_zip"]) else "Không"
        lines.append(
            f"| `{row['issuer_id']}` | {row['fiscal_year']} | `{row['cohort']}` | "
            f"`{row['statement_scope']}` | {size_mb:.2f} | {zip_flag} | "
            f"`{sha_short}` | `{row['local_rel_path']}` |"
        )

    if failures:
        lines.extend(["", "## 4. Ghi Nhận Link Không Tải Được", ""])
        for err in failures:
            lines.append(f"- {err}")

    lines.append("")
    return "\n".join(lines)
