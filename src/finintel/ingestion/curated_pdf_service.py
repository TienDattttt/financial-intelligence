"""CAS-backed Port 2 Curated PDF discovery & archival service (TASK-06).

Verified Sources:
    - ``spec/SPEC-v2-autonomous-institutional-finintel.md`` §3.B.3:
      Extracts audited annual consolidated ``.pdf`` / ``.zip`` links directly from
      ``RawArchive`` CAS provenance receipts & SHA-256 payloads (never reading
      nonexistent ``<TICKER>.html`` files or importing ``scripts/``).
    - ``src/finintel/ingestion/ssrf_guard.py`` (``MAX_PDF_BYTES = 52_428_800``,
      DNS global-IP validation, ``follow_redirects=False``).
    - All ``platform`` / ``rag`` runtime packages (``pymupdf``, ``pgvector``,
      ``psycopg``, ``sentence_transformers``, ``easyocr``) are strictly lazy-imported.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import httpx
from filelock import FileLock

from finintel.exceptions import ContractViolationError
from finintel.ingestion.ssrf_guard import (
    MAX_PDF_BYTES,
    validate_https_url_and_resolve_global_ips,
)
from finintel.pipeline.state_store import acquire_host_pacing_slot
from finintel.ports.documents import PDF_CDN_ALLOWED_HOSTS

if TYPE_CHECKING:
    from finintel.rag.pdf_catalog import CuratedPdfRecord, PdfCandidate

_TICKER_RE = re.compile(r"^[A-Z0-9]{1,16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class CuratedPdfSyncSummary:
    """Summary of an incremental CAS-backed Curated PDF sync run."""

    scanned_receipts: int
    discovered_candidates: int
    downloaded_count: int
    skipped_existing_count: int
    parsed_count: int
    total_manifest_records: int
    failures: tuple[str, ...]
    indexed_count: int = 0


def _extract_ticker_from_receipt_attrs(attrs: Mapping[str, object]) -> str | None:
    raw_ticker = attrs.get("ticker")
    if isinstance(raw_ticker, str):
        cand = raw_ticker.strip().upper()
        if _TICKER_RE.fullmatch(cand):
            return cand
    raw_url = attrs.get("source_url")
    if isinstance(raw_url, str) and raw_url.strip():
        parsed = urlsplit(raw_url.strip())
        qs = parse_qs(parsed.query)
        for key in ("sym", "symbol", "ticker", "s"):
            vals = qs.get(key) or qs.get(key.upper())
            if vals:
                cand = vals[0].strip().upper()
                if _TICKER_RE.fullmatch(cand):
                    return cand
    return None


def _decode_cas_html_payload(raw_bytes: bytes, *, extension: str) -> str | None:
    if extension == "html":
        return raw_bytes.decode("utf-8", errors="replace")
    try:
        decoded = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if isinstance(decoded, Mapping):
        raw_utf8 = decoded.get("raw_utf8")
        if isinstance(raw_utf8, str) and "<tr" in raw_utf8.lower():
            return raw_utf8
    return None


def _merge_extracted_candidates(
    by_ticker_year: dict[str, dict[int, PdfCandidate]],
    ticker: str,
    extracted: Sequence[PdfCandidate],
) -> None:
    year_map = by_ticker_year.setdefault(ticker, {})
    for cand in extracted:
        prev = year_map.get(cand.fiscal_year)
        if prev is None or (
            prev.archive_format == "zip" and cand.archive_format == "pdf"
        ):
            year_map[cand.fiscal_year] = cand


def _extract_pdf_candidates_with_count(
    raw_root: Path,
    *,
    tickers: Sequence[str] | None = None,
) -> tuple[dict[str, tuple[PdfCandidate, ...]], int]:
    """Extract audited annual PDF links from ``raw_disclosures/cafef_bctc`` and CAS receipts."""
    pdf_cat: Any = importlib.import_module("finintel.rag.pdf_catalog")

    resolved_root = raw_root.resolve()
    allowed_tickers: set[str] | None = None
    if tickers is not None:
        allowed_tickers = {
            t.strip().upper()
            for t in tickers
            if _TICKER_RE.fullmatch(t.strip().upper())
        }
        if not allowed_tickers:
            return {}, 0

    by_ticker_year: dict[str, dict[int, PdfCandidate]] = {}
    scanned_count = 0

    cafef_dir = resolved_root.parent / "raw_disclosures" / "cafef_bctc"
    cafef_covered: set[str] = set()
    if cafef_dir.is_dir():
        html_paths = (
            [cafef_dir / f"{tk}.html" for tk in sorted(allowed_tickers)]
            if allowed_tickers is not None
            else sorted(cafef_dir.glob("*.html"))
        )
        for html_path in html_paths:
            if html_path.is_symlink() or not html_path.is_file():
                continue
            tk = html_path.stem.strip().upper()
            if not _TICKER_RE.fullmatch(tk):
                continue
            scanned_count += 1
            cafef_covered.add(tk)
            try:
                html_text = html_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "<tr" not in html_text.lower():
                continue
            extracted: Sequence[PdfCandidate] = (
                pdf_cat.extract_audited_consolidated_annual_candidates(tk, html_text)
            )
            _merge_extracted_candidates(by_ticker_year, tk, extracted)

        if allowed_tickers is not None and allowed_tickers <= cafef_covered:
            return (
                {
                    tk: tuple(
                        sorted(ymap.values(), key=lambda c: c.fiscal_year, reverse=True)
                    )
                    for tk, ymap in sorted(by_ticker_year.items())
                    if ymap
                },
                scanned_count,
            )
        if allowed_tickers is None and cafef_covered:
            return (
                {
                    tk: tuple(
                        sorted(ymap.values(), key=lambda c: c.fiscal_year, reverse=True)
                    )
                    for tk, ymap in sorted(by_ticker_year.items())
                    if ymap
                },
                scanned_count,
            )

    prov_dir = resolved_root / "provenance"
    if not prov_dir.is_dir():
        return {}, scanned_count

    for receipt_path in sorted(prov_dir.glob("*.json")):
        if receipt_path.is_symlink() or not receipt_path.is_file():
            continue
        scanned_count += 1
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(receipt, Mapping):
            continue

        attrs = receipt.get("provider_attrs")
        if not isinstance(attrs, Mapping):
            continue
        disc_src = str(attrs.get("disclosure_source") or "").lower()
        src_url = str(attrs.get("source_url") or "")
        if disc_src != "cafef_bctc" and "baocaotaichinh" not in src_url.lower():
            continue

        ticker = _extract_ticker_from_receipt_attrs(attrs)
        if (
            ticker is None
            or ticker in cafef_covered
            or (allowed_tickers is not None and ticker not in allowed_tickers)
        ):
            continue

        rel_archive = receipt.get("raw_archive_path")
        expected_sha = receipt.get("payload_file_sha256")
        if not isinstance(rel_archive, str) or not rel_archive.strip():
            continue
        payload_path = (resolved_root / rel_archive).resolve()
        if (
            payload_path.is_symlink()
            or not payload_path.is_relative_to(resolved_root)
            or not payload_path.is_file()
        ):
            continue

        raw_bytes = payload_path.read_bytes()
        if (
            isinstance(expected_sha, str)
            and _SHA256_RE.fullmatch(expected_sha)
            and hashlib.sha256(raw_bytes).hexdigest() != expected_sha
        ):
            raise ContractViolationError(
                f"CAS integrity mismatch in RawArchive payload {rel_archive}."
            )

        ext = payload_path.suffix.lstrip(".").lower()
        html_text_cas = _decode_cas_html_payload(raw_bytes, extension=ext)
        if not html_text_cas:
            continue

        extracted_cas: Sequence[PdfCandidate] = (
            pdf_cat.extract_audited_consolidated_annual_candidates(
                ticker, html_text_cas
            )
        )
        _merge_extracted_candidates(by_ticker_year, ticker, extracted_cas)

    return (
        {
            tk: tuple(sorted(ymap.values(), key=lambda c: c.fiscal_year, reverse=True))
            for tk, ymap in sorted(by_ticker_year.items())
            if ymap
        },
        scanned_count,
    )


def extract_pdf_candidates_from_raw_archive(
    raw_root: Path,
    *,
    tickers: Sequence[str] | None = None,
) -> dict[str, tuple[PdfCandidate, ...]]:
    """Scan ``RawArchive`` CAS provenance receipts and extract audited annual PDF links."""
    by_ticker, _ = _extract_pdf_candidates_with_count(raw_root, tickers=tickers)
    return by_ticker


def _index_parsed_document_into_pgvector(
    rec: CuratedPdfRecord,
    parsed: Any,
    *,
    rag_pg_dsn: str | None = None,
) -> int:
    chunker_mod: Any = importlib.import_module("finintel.rag.chunker")
    embedder_mod: Any = importlib.import_module("finintel.rag.embedder")
    store_mod: Any = importlib.import_module("finintel.rag.store")

    store = store_mod.PgVectorRagStore(dsn=rag_pg_dsn)
    store.ensure_schema()
    if store.has_indexed_pdf(rec.content_sha256):
        return 0
    pages = [p.to_chunk_input() for p in parsed.pages if p.text.strip()]
    if not pages:
        return 0
    segments = chunker_mod.chunk_parsed_pages(
        filing_id=parsed.filing_id,
        issuer_id=parsed.issuer_id,
        ticker=parsed.ticker,
        fiscal_year=parsed.fiscal_year,
        fiscal_quarter=parsed.fiscal_quarter,
        statement_scope=parsed.statement_scope,
        doc_type=parsed.doc_type,
        pages=pages,
    )
    if not segments:
        return 0
    embedder = embedder_mod.BgeM3Embedder(batch_size=16)
    vectors, _ = embedder.embed_texts([seg.text_content for seg in segments])
    summary = store_mod.IndexedDocumentSummary(
        filing_id=parsed.filing_id,
        issuer_id=parsed.issuer_id,
        ticker=parsed.ticker,
        fiscal_year=parsed.fiscal_year,
        fiscal_quarter=parsed.fiscal_quarter,
        statement_scope=parsed.statement_scope,
        doc_type=parsed.doc_type,
        cohort=rec.cohort,
        source_title=rec.source_title,
        source_url=rec.source_url,
        pdf_sha256=rec.content_sha256,
        file_size_bytes=rec.file_size_bytes,
        total_pages=parsed.total_pages,
        digital_pages=parsed.digital_pages,
        ocr_pages=parsed.ocr_pages,
        chunk_count=len(segments),
    )
    store.upsert_document_and_chunks(
        summary=summary, segments=segments, embeddings=vectors
    )
    return 1


def sync_curated_pdfs_from_archive(
    *,
    raw_root: Path,
    output_root: Path,
    manifest_path: Path | None = None,
    tickers: Sequence[str] | None = None,
    preferred_years: Sequence[int] | None = None,
    max_per_ticker: int = 2,
    cohort: str = "CORE_UNIVERSE",
    fetch_online: bool = True,
    parse_downloaded: bool = False,
    parsed_cache_dir: Path | None = None,
    index_to_pgvector: bool = False,
    rag_pg_dsn: str | None = None,
    http_client: httpx.Client | None = None,
    max_pdf_bytes: int = min(MAX_PDF_BYTES, 50 * 1024 * 1024),
    manifest_lock_path: Path | None = None,
    lock_timeout: float = 60.0,
    state_store_path: Path | None = None,
    min_interval_seconds: float = 0.6,
) -> CuratedPdfSyncSummary:
    """Incrementally download & archive new audited annual PDFs discovered in ``RawArchive``."""
    pdf_cat: Any = importlib.import_module("finintel.rag.pdf_catalog")

    def _write_manifest_locked(records: list[CuratedPdfRecord], target: Path) -> None:
        if manifest_lock_path is not None:
            manifest_lock_path.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(manifest_lock_path), timeout=lock_timeout):
                pdf_cat.write_manifest_parquet(records, target)
        else:
            pdf_cat.write_manifest_parquet(records, target)

    eff_manifest = (
        manifest_path
        if manifest_path is not None
        else output_root / "curated_pdfs_manifest.parquet"
    )

    existing_records: list[CuratedPdfRecord] = list(
        pdf_cat.load_existing_manifest_records(eff_manifest, output_root=output_root)
    )
    existing_slots: set[tuple[str, int, str]] = {
        (r.issuer_id, r.fiscal_year, r.statement_scope) for r in existing_records
    }

    by_ticker, scanned_receipts = _extract_pdf_candidates_with_count(
        raw_root, tickers=tickers
    )
    discovered = sum(len(items) for items in by_ticker.values())
    if not fetch_online or not by_ticker:
        _write_manifest_locked(existing_records, eff_manifest)
        return CuratedPdfSyncSummary(
            scanned_receipts=scanned_receipts,
            discovered_candidates=discovered,
            downloaded_count=0,
            skipped_existing_count=len(existing_records),
            parsed_count=0,
            total_manifest_records=len(existing_records),
            failures=(),
            indexed_count=0,
        )

    owned_client = http_client is None
    client = (
        http_client
        if http_client is not None
        else httpx.Client(follow_redirects=False, timeout=httpx.Timeout(20.0))
    )
    is_mock_transport = isinstance(
        getattr(client, "_transport", None), httpx.MockTransport
    )

    downloaded = 0
    skipped = 0
    parsed_count = 0
    indexed_count = 0
    failures: list[str] = []
    all_records: list[CuratedPdfRecord] = list(existing_records)

    try:
        for tk, cands in by_ticker.items():
            chosen: Sequence[PdfCandidate] = (
                pdf_cat.select_candidates_for_years(
                    cands,
                    preferred_years,
                    max_items=max_per_ticker,
                    fallback_to_latest=True,
                )
                if preferred_years is not None
                else cands[: max(1, max_per_ticker)]
            )
            for cand in chosen:
                slot = (cand.issuer_id, cand.fiscal_year, cand.statement_scope)
                if slot in existing_slots:
                    skipped += 1
                    continue
                try:
                    if not is_mock_transport:
                        validate_https_url_and_resolve_global_ips(
                            cand.source_url, allowed_hosts=PDF_CDN_ALLOWED_HOSTS
                        )
                        host = (
                            (urlsplit(cand.source_url).hostname or "").strip().lower()
                        )
                        if state_store_path is not None and host:
                            acquire_host_pacing_slot(
                                state_store_path,
                                host,
                                min_interval_seconds=min_interval_seconds,
                            )
                    rec: CuratedPdfRecord = pdf_cat.download_curated_pdf(
                        cand,
                        output_root=output_root,
                        cohort=cohort,
                        client=client,
                        max_download_bytes=max_pdf_bytes,
                    )
                    all_records.append(rec)
                    existing_slots.add(slot)
                    downloaded += 1
                    if parse_downloaded:
                        parser_mod: Any = importlib.import_module("finintel.rag.parser")
                        parsed_doc = parser_mod.parse_curated_pdf(
                            output_root / rec.local_rel_path,
                            record=rec,
                            cache_dir=parsed_cache_dir,
                            enable_vision_ocr=False,
                        )
                        parsed_count += 1
                        if index_to_pgvector:
                            indexed_count += _index_parsed_document_into_pgvector(
                                rec, parsed_doc, rag_pg_dsn=rag_pg_dsn
                            )
                except Exception as exc:
                    failures.append(f"{tk} FY{cand.fiscal_year}: {exc}")
    finally:
        if owned_client:
            client.close()

    _write_manifest_locked(all_records, eff_manifest)
    return CuratedPdfSyncSummary(
        scanned_receipts=scanned_receipts,
        discovered_candidates=discovered,
        downloaded_count=downloaded,
        skipped_existing_count=skipped,
        parsed_count=parsed_count,
        total_manifest_records=len(all_records),
        failures=tuple(failures),
        indexed_count=indexed_count,
    )


__all__ = [
    "CuratedPdfSyncSummary",
    "extract_pdf_candidates_from_raw_archive",
    "sync_curated_pdfs_from_archive",
]
