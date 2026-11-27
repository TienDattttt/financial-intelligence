"""Text-first PyMuPDF batch PDF RAG pipeline with priority queue, SHA-256 dedup & Vision cap (V3-20)."""

from __future__ import annotations

import asyncio
import importlib
import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import polars as pl

from finintel.quota.guard import (
    QuotaStoreProtocol,
    RagUsageLedgerEntry,
    hash_actor_subject,
)
from finintel.quota.policies import (
    DEFAULT_DAILY_VISION_PAGE_CAP,
    QuotaBudgetConfig,
    current_quota_window,
)
from finintel.rag.chunker import ChunkedDocumentSegment, chunk_parsed_pages
from finintel.rag.parser import (
    DEFAULT_MIN_DIGITAL_ALNUM_CHARS,
    DEFAULT_OCR_BATCH_SIZE,
    DEFAULT_OCR_CONCURRENCY,
    DEFAULT_OCR_DPI,
    MAX_PDF_PAGES,
    ExtractionMethod,
    ParsedPage,
    _extract_digital_page_text,
    _load_cached_page,
    _ocr_pages_async,
    _render_page_png_bytes,
    _save_cached_page,
    build_filing_id,
    detect_section_title,
    detect_unit_scale,
    needs_vision_ocr,
)
from finintel.rag.pdf_catalog import CuratedPdfRecord, compute_sha256
from finintel.rag.store import IndexedDocumentSummary

logger = logging.getLogger("finintel.rag.batch_pipeline")

DEFAULT_TOP_MARKET_CAP_N = 300
BatchJobStatus = Literal["INDEXED", "SKIPPED_DEDUP", "DEFERRED_VISION_CAP", "FAILED"]
BatchRunStatus = Literal["COMPLETED", "DEFERRED_VISION_CAP", "COMPLETED_WITH_ERRORS"]


class BatchRagStoreProtocol(Protocol):
    """Minimal storage protocol required by ``run_text_first_batch_pipeline``."""

    def has_indexed_pdf(self, pdf_sha256: str) -> bool: ...

    def upsert_document_and_chunks(
        self,
        *,
        summary: IndexedDocumentSummary,
        segments: Sequence[ChunkedDocumentSegment],
        embeddings: Sequence[Sequence[float]],
    ) -> int: ...


class BatchEmbedderProtocol(Protocol):
    """Minimal embedder protocol required by ``run_text_first_batch_pipeline``."""

    def embed_texts(
        self,
        texts: Sequence[str],
        *,
        batch_size: int | None = None,
        show_progress_bar: bool = False,
    ) -> tuple[list[list[float]], Any]: ...


@dataclass(frozen=True, slots=True)
class IssuerPriorityInput:
    """Snapshot-derived priority attributes for one issuer."""

    issuer_id: str
    ticker: str
    risk_tier: str | None = None
    red_flag_count: int = 0
    market_cap_vnd: float | None = None


@dataclass(frozen=True, slots=True)
class PdfBatchCandidate:
    """Candidate footnote/BCTC PDF file queued for scaled ingestion."""

    record: CuratedPdfRecord
    pdf_path: Path
    risk_tier: str | None = None
    red_flag_count: int = 0
    market_cap_vnd: float | None = None


@dataclass(frozen=True, slots=True)
class PrioritizedPdfJob:
    """Priority-ranked PDF job inside the batch ingestion queue."""

    candidate: PdfBatchCandidate
    priority_tier: int
    priority_reasons: tuple[str, ...]
    rank_key: tuple[int, int, float, int, str]


@dataclass(frozen=True, slots=True)
class BatchPdfOutcome:
    """Per-PDF execution outcome including digital vs Vision page counts."""

    filing_id: str
    ticker: str
    pdf_sha256: str
    status: BatchJobStatus
    priority_tier: int
    priority_reasons: tuple[str, ...]
    total_pages: int
    digital_pages: int
    vision_pages: int
    deferred_vision_pages: int
    vision_calls: int
    chunks_indexed: int
    estimated_cost_usd: float = 0.0
    error_detail: str | None = None


@dataclass(frozen=True, slots=True)
class BatchPipelineReport:
    """Aggregate report returned cleanly to Dagster or CLI callers without crashing."""

    status: BatchRunStatus
    queued_jobs: int
    indexed_documents: int
    skipped_dedup_documents: int
    deferred_documents: int
    failed_documents: int
    total_digital_pages: int
    total_vision_pages: int
    total_deferred_vision_pages: int
    total_vision_calls: int
    total_chunks_indexed: int
    vision_pages_cap: int
    vision_pages_remaining: int
    estimated_cost_usd: float
    outcomes: tuple[BatchPdfOutcome, ...]
    ledger_entries: tuple[RagUsageLedgerEntry, ...]


def _finite_positive_float(val: float | None) -> float:
    if val is None or not math.isfinite(val) or val <= 0.0:
        return 0.0
    return float(val)


def build_priority_pdf_queue(
    candidates: Sequence[PdfBatchCandidate],
    *,
    top_market_cap_n: int = DEFAULT_TOP_MARKET_CAP_N,
    include_non_priority: bool = False,
) -> list[PrioritizedPdfJob]:
    """Queue 100% R4/R5 + 100% red-flag issuers + Top 300 market cap deterministically."""
    if not candidates:
        return []
    max_mcap_by_issuer: dict[str, float] = {}
    for cand in candidates:
        issuer = cand.record.issuer_id.strip().upper()
        mcap = _finite_positive_float(cand.market_cap_vnd)
        if mcap > max_mcap_by_issuer.get(issuer, 0.0):
            max_mcap_by_issuer[issuer] = mcap

    top_mcap_issuers = {
        issuer
        for issuer, mcap in sorted(
            max_mcap_by_issuer.items(), key=lambda kv: (-kv[1], kv[0])
        )[: max(0, int(top_market_cap_n))]
        if mcap > 0.0
    }

    jobs: list[PrioritizedPdfJob] = []
    for cand in candidates:
        issuer = cand.record.issuer_id.strip().upper()
        tier_str = (cand.risk_tier or "").strip().upper()
        rf_count = max(0, int(cand.red_flag_count))
        mcap = _finite_positive_float(cand.market_cap_vnd)
        is_r5, is_r4 = tier_str == "R5", tier_str == "R4"

        reasons: list[str] = []
        if is_r5 or is_r4:
            reasons.append(f"RISK_TIER_{tier_str}")
        if rf_count > 0:
            reasons.append(f"RED_FLAGS_{rf_count}")
        if issuer in top_mcap_issuers:
            reasons.append(f"TOP_{top_market_cap_n}_MCAP")
        if not reasons and not include_non_priority:
            continue

        priority_tier = (
            1
            if (is_r5 or is_r4)
            else (2 if rf_count > 0 else (3 if issuer in top_mcap_issuers else 4))
        )
        tier_weight = 2 if is_r5 else (1 if is_r4 else 0)
        jobs.append(
            PrioritizedPdfJob(
                candidate=cand,
                priority_tier=priority_tier,
                priority_reasons=tuple(reasons) or ("OTHER",),
                rank_key=(
                    tier_weight,
                    1 if rf_count > 0 else 0,
                    mcap,
                    int(cand.record.fiscal_year),
                    issuer,
                ),
            )
        )

    jobs.sort(
        key=lambda j: (
            j.priority_tier,
            -j.rank_key[0],
            -j.rank_key[1],
            -j.candidate.red_flag_count,
            -j.rank_key[2],
            -j.rank_key[3],
            j.rank_key[4],
        )
    )
    return jobs


def load_priority_inputs_from_snapshot(
    snapshot_dir: Path,
) -> dict[str, IssuerPriorityInput]:
    """Read latest risk tier, red-flag count, and PIT market cap from a snapshot (read-only)."""
    result: dict[str, IssuerPriorityInput] = {}
    ml_path = snapshot_dir / "ml_scores.parquet"
    if ml_path.is_file():
        ml_df = (
            pl.read_parquet(ml_path)
            .sort("period_end_date", descending=True)
            .unique(subset=["issuer_id"], keep="first")
        )
        for row in ml_df.select(["issuer_id", "risk_tier"]).to_dicts():
            iid = str(row["issuer_id"]).strip().upper()
            result[iid] = IssuerPriorityInput(
                issuer_id=iid,
                ticker=iid,
                risk_tier=str(row["risk_tier"]) if row.get("risk_tier") else None,
            )

    rf_path = snapshot_dir / "red_flags.parquet"
    if rf_path.is_file():
        rf_df = (
            pl.read_parquet(rf_path)
            .filter(pl.col("is_triggered").eq(True))
            .group_by("issuer_id")
            .len()
        )
        for row in rf_df.to_dicts():
            iid = str(row["issuer_id"]).strip().upper()
            prev = result.get(iid) or IssuerPriorityInput(issuer_id=iid, ticker=iid)
            result[iid] = IssuerPriorityInput(
                issuer_id=iid,
                ticker=prev.ticker,
                risk_tier=prev.risk_tier,
                red_flag_count=int(row["len"]),
                market_cap_vnd=prev.market_cap_vnd,
            )

    val_path = snapshot_dir / "pit_valuation_mart.parquet"
    if val_path.is_file():
        val_df = (
            pl.read_parquet(val_path)
            .sort("period_end_date", descending=True)
            .unique(subset=["issuer_id"], keep="first")
        )
        for row in val_df.select(["issuer_id", "market_cap_vnd"]).to_dicts():
            iid = str(row["issuer_id"]).strip().upper()
            prev = result.get(iid) or IssuerPriorityInput(issuer_id=iid, ticker=iid)
            raw_mcap = row.get("market_cap_vnd")
            mcap = float(raw_mcap) if isinstance(raw_mcap, (int, float)) else None
            result[iid] = IssuerPriorityInput(
                issuer_id=iid,
                ticker=prev.ticker,
                risk_tier=prev.risk_tier,
                red_flag_count=prev.red_flag_count,
                market_cap_vnd=mcap,
            )
    return result


def _process_single_pdf(
    job: PrioritizedPdfJob,
    *,
    quota_store: QuotaStoreProtocol,
    seen_sha256: set[str],
    store: BatchRagStoreProtocol | None,
    embedder: BatchEmbedderProtocol | None,
    vision_ocr_fn: Callable[[list[tuple[int, bytes, str | None]]], dict[int, str]]
    | None,
    gemini_bundle: Any | None,
    cache_dir: Path | None,
    ocr_dpi: int,
    min_digital_alnum_chars: int,
    now: datetime | None,
) -> tuple[BatchPdfOutcome, RagUsageLedgerEntry]:
    rec = job.candidate.record
    pdf_path = job.candidate.pdf_path
    filing_id = build_filing_id(rec)
    ticker = rec.issuer_id.strip().upper()
    qdate, _ = current_quota_window(now)
    actor_hash = hash_actor_subject("dagster:batch_pdf_rag_pipeline")

    pdf_sha256 = rec.content_sha256 or (
        compute_sha256(pdf_path.read_bytes()) if pdf_path.is_file() else ""
    )
    if pdf_sha256 in seen_sha256 or (
        store is not None and store.has_indexed_pdf(pdf_sha256)
    ):
        seen_sha256.add(pdf_sha256)
        ledger = RagUsageLedgerEntry(
            usage_date=qdate,
            actor_subject_hash=actor_hash,
            operation="batch_skipped_dedup",
            ticker=ticker,
            filing_id=filing_id,
            pdf_sha256=pdf_sha256,
            status="SKIPPED_DEDUP",
        )
        quota_store.record_rag_usage(ledger)
        return (
            BatchPdfOutcome(
                filing_id=filing_id,
                ticker=ticker,
                pdf_sha256=pdf_sha256,
                status="SKIPPED_DEDUP",
                priority_tier=job.priority_tier,
                priority_reasons=job.priority_reasons,
                total_pages=0,
                digital_pages=0,
                vision_pages=0,
                deferred_vision_pages=0,
                vision_calls=0,
                chunks_indexed=0,
            ),
            ledger,
        )

    pymupdf: Any = importlib.import_module("pymupdf")  # Lazy import (M-03)
    pages_by_no: dict[int, ParsedPage] = {}
    pending_ocr: list[tuple[int, bytes, str | None]] = []

    with pymupdf.open(str(pdf_path)) as doc:
        total_pages = min(len(doc), MAX_PDF_PAGES)
        running_unit, running_section = "VND", "Báo cáo tài chính hợp nhất"
        for idx in range(total_pages):
            page_no = idx + 1
            cached = (
                _load_cached_page(cache_dir, pdf_sha256, page_no)
                if cache_dir is not None
                else None
            )
            if cached is not None:
                pages_by_no[page_no] = cached
                running_unit = cached.detected_unit_scale or running_unit
                running_section = cached.detected_section or running_section
                continue

            page = doc[idx]
            raw_text = str(page.get_text("text") or "")
            font_names = tuple(
                str(f[3]) for f in (page.get_fonts() or []) if len(f) > 3
            )
            image_count = len(page.get_images(full=False) or [])
            requires_ocr, ocr_reason = needs_vision_ocr(
                raw_text,
                font_names=font_names,
                image_count=image_count,
                min_alnum_chars=min_digital_alnum_chars,
            )
            if requires_ocr:
                pending_ocr.append(
                    (page_no, _render_page_png_bytes(page, dpi=ocr_dpi), ocr_reason)
                )
            else:
                extracted_text, has_tables = _extract_digital_page_text(page)
                method: ExtractionMethod = (
                    "digital" if extracted_text.strip() else "empty"
                )
                running_unit = detect_unit_scale(extracted_text, default=running_unit)
                running_section = detect_section_title(
                    extracted_text, previous_section=running_section
                )
                parsed_page = ParsedPage(
                    page_no=page_no,
                    text=extracted_text,
                    extraction_method=method,
                    char_count=len(extracted_text),
                    has_tables=has_tables,
                    detected_unit_scale=running_unit,
                    detected_section=running_section,
                    ocr_reason=None,
                )
                pages_by_no[page_no] = parsed_page
                if cache_dir is not None:
                    _save_cached_page(cache_dir, pdf_sha256, parsed_page)

    vision_calls = 0
    deferred_vision = 0
    if pending_ocr:
        budget = quota_store.get_system_budget(now=now)
        granted = (
            0
            if budget.circuit_breaker_open
            else quota_store.consume_vision_pages(requested=len(pending_ocr), now=now)[
                0
            ]
        )
        deferred_vision = len(pending_ocr) - granted
        allowed_tasks = pending_ocr[:granted]
        if allowed_tasks:
            if vision_ocr_fn is not None:
                ocr_texts = vision_ocr_fn(allowed_tasks)
                vision_calls = len(allowed_tasks)
            else:
                from finintel.rag._gemini import create_verified_gemini_bundle

                active_bundle = gemini_bundle or create_verified_gemini_bundle()
                ocr_texts = asyncio.run(
                    _ocr_pages_async(
                        active_bundle,
                        allowed_tasks,
                        concurrency=DEFAULT_OCR_CONCURRENCY,
                        batch_size=DEFAULT_OCR_BATCH_SIZE,
                        cache_dir=cache_dir,
                        pdf_sha256=pdf_sha256,
                    )
                )
                vision_calls = max(
                    1, math.ceil(len(allowed_tasks) / DEFAULT_OCR_BATCH_SIZE)
                )
                quota_store.record_global_gemini_call(units=vision_calls, now=now)

            for pno, _, reason in allowed_tasks:
                txt = ocr_texts.get(pno, "").strip()
                pages_by_no[pno] = ParsedPage(
                    page_no=pno,
                    text=txt,
                    extraction_method="vision_ocr" if txt else "empty",
                    char_count=len(txt),
                    has_tables=("|" in txt),
                    detected_unit_scale=detect_unit_scale(txt, default="VND"),
                    detected_section=detect_section_title(txt),
                    ocr_reason=reason,
                )

    ordered_pages = tuple(
        pages_by_no[pno] for pno in sorted(pages_by_no) if pages_by_no[pno].text.strip()
    )
    digital_pages = sum(1 for p in ordered_pages if p.extraction_method == "digital")
    vision_pages = sum(1 for p in ordered_pages if p.extraction_method == "vision_ocr")
    segments = chunk_parsed_pages(
        filing_id=filing_id,
        issuer_id=ticker,
        ticker=ticker,
        fiscal_year=rec.fiscal_year,
        fiscal_quarter=rec.fiscal_quarter,
        statement_scope=rec.statement_scope,
        doc_type=rec.doc_type,
        pages=[p.to_chunk_input() for p in ordered_pages],
    )
    chunks_indexed = len(segments)
    if segments and store is not None and embedder is not None:
        vectors, _ = embedder.embed_texts([s.text_content for s in segments])
        chunks_indexed = store.upsert_document_and_chunks(
            summary=IndexedDocumentSummary(
                filing_id=filing_id,
                issuer_id=ticker,
                ticker=ticker,
                fiscal_year=rec.fiscal_year,
                fiscal_quarter=rec.fiscal_quarter,
                statement_scope=rec.statement_scope,
                doc_type=rec.doc_type,
                cohort=rec.cohort,
                source_title=rec.source_title,
                source_url=rec.source_url,
                pdf_sha256=pdf_sha256,
                file_size_bytes=rec.file_size_bytes,
                total_pages=total_pages,
                digital_pages=digital_pages,
                ocr_pages=vision_pages,
                chunk_count=len(segments),
            ),
            segments=segments,
            embeddings=vectors,
        )

    if deferred_vision == 0:
        seen_sha256.add(pdf_sha256)
    job_status: BatchJobStatus = (
        "DEFERRED_VISION_CAP" if deferred_vision > 0 else "INDEXED"
    )
    ledger_op = (
        "batch_deferred_cap"
        if deferred_vision > 0
        else ("batch_vision_ocr" if vision_pages > 0 else "batch_digital")
    )
    ledger = RagUsageLedgerEntry(
        usage_date=qdate,
        actor_subject_hash=actor_hash,
        operation=ledger_op,
        ticker=ticker,
        filing_id=filing_id,
        pdf_sha256=pdf_sha256,
        digital_pages=digital_pages,
        vision_pages=vision_pages,
        llm_calls=vision_calls,
        status=job_status,
    )
    quota_store.record_rag_usage(ledger)
    return (
        BatchPdfOutcome(
            filing_id=filing_id,
            ticker=ticker,
            pdf_sha256=pdf_sha256,
            status=job_status,
            priority_tier=job.priority_tier,
            priority_reasons=job.priority_reasons,
            total_pages=total_pages,
            digital_pages=digital_pages,
            vision_pages=vision_pages,
            deferred_vision_pages=deferred_vision,
            vision_calls=vision_calls,
            chunks_indexed=chunks_indexed,
        ),
        ledger,
    )


def run_text_first_batch_pipeline(
    candidates: Sequence[PdfBatchCandidate],
    *,
    quota_store: QuotaStoreProtocol | None = None,
    store: BatchRagStoreProtocol | None = None,
    embedder: BatchEmbedderProtocol | None = None,
    vision_ocr_fn: Callable[[list[tuple[int, bytes, str | None]]], dict[int, str]]
    | None = None,
    gemini_bundle: Any | None = None,
    cache_dir: Path | None = None,
    top_market_cap_n: int = DEFAULT_TOP_MARKET_CAP_N,
    daily_vision_page_cap: int = DEFAULT_DAILY_VISION_PAGE_CAP,
    include_non_priority: bool = False,
    ocr_dpi: int = DEFAULT_OCR_DPI,
    min_digital_alnum_chars: int = DEFAULT_MIN_DIGITAL_ALNUM_CHARS,
    now: datetime | None = None,
) -> BatchPipelineReport:
    """Execute priority-queued Text-first PDF ingestion with SHA-256 dedup & Vision cap."""
    if quota_store is None:
        from finintel.quota.guard import InMemoryQuotaStore

        active_quota: QuotaStoreProtocol = InMemoryQuotaStore(
            budget_config=QuotaBudgetConfig(daily_vision_page_cap=daily_vision_page_cap)
        )
    else:
        active_quota = quota_store

    jobs = build_priority_pdf_queue(
        candidates,
        top_market_cap_n=top_market_cap_n,
        include_non_priority=include_non_priority,
    )
    seen_sha256: set[str] = set()
    outcomes: list[BatchPdfOutcome] = []
    ledgers: list[RagUsageLedgerEntry] = []

    for job in jobs:
        try:
            outcome, job_ledger = _process_single_pdf(
                job,
                quota_store=active_quota,
                seen_sha256=seen_sha256,
                store=store,
                embedder=embedder,
                vision_ocr_fn=vision_ocr_fn,
                gemini_bundle=gemini_bundle,
                cache_dir=cache_dir,
                ocr_dpi=ocr_dpi,
                min_digital_alnum_chars=min_digital_alnum_chars,
                now=now,
            )
            outcomes.append(outcome)
            ledgers.append(job_ledger)
        except Exception as exc:
            rec = job.candidate.record
            logger.warning(
                "Batch PDF job failed gracefully for %s (%s): %s",
                rec.issuer_id,
                type(exc).__name__,
                exc,
            )
            outcomes.append(
                BatchPdfOutcome(
                    filing_id=build_filing_id(rec),
                    ticker=rec.issuer_id.strip().upper(),
                    pdf_sha256=rec.content_sha256,
                    status="FAILED",
                    priority_tier=job.priority_tier,
                    priority_reasons=job.priority_reasons,
                    total_pages=0,
                    digital_pages=0,
                    vision_pages=0,
                    deferred_vision_pages=0,
                    vision_calls=0,
                    chunks_indexed=0,
                    error_detail=type(exc).__name__,
                )
            )

    budget_after = active_quota.get_system_budget(now=now)
    deferred_docs = sum(1 for o in outcomes if o.status == "DEFERRED_VISION_CAP")
    failed_docs = sum(1 for o in outcomes if o.status == "FAILED")
    run_status: BatchRunStatus = (
        "DEFERRED_VISION_CAP"
        if deferred_docs > 0
        else ("COMPLETED_WITH_ERRORS" if failed_docs > 0 else "COMPLETED")
    )
    return BatchPipelineReport(
        status=run_status,
        queued_jobs=len(jobs),
        indexed_documents=sum(1 for o in outcomes if o.status == "INDEXED"),
        skipped_dedup_documents=sum(1 for o in outcomes if o.status == "SKIPPED_DEDUP"),
        deferred_documents=deferred_docs,
        failed_documents=failed_docs,
        total_digital_pages=sum(o.digital_pages for o in outcomes),
        total_vision_pages=sum(o.vision_pages for o in outcomes),
        total_deferred_vision_pages=sum(o.deferred_vision_pages for o in outcomes),
        total_vision_calls=sum(o.vision_calls for o in outcomes),
        total_chunks_indexed=sum(o.chunks_indexed for o in outcomes),
        vision_pages_cap=budget_after.vision_pages_cap,
        vision_pages_remaining=budget_after.vision_pages_remaining,
        estimated_cost_usd=0.0,
        outcomes=tuple(outcomes),
        ledger_entries=tuple(ledgers),
    )


__all__ = [
    "BatchEmbedderProtocol",
    "BatchJobStatus",
    "BatchPdfOutcome",
    "BatchPipelineReport",
    "BatchRagStoreProtocol",
    "BatchRunStatus",
    "DEFAULT_TOP_MARKET_CAP_N",
    "IssuerPriorityInput",
    "PdfBatchCandidate",
    "PrioritizedPdfJob",
    "build_priority_pdf_queue",
    "load_priority_inputs_from_snapshot",
    "run_text_first_batch_pipeline",
]
