"""Index all 99 curated audited consolidated PDFs into PostgreSQL ``finintel_rag`` (UI-08b)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from finintel.rag._gemini import create_verified_gemini_bundle
from finintel.rag.chunker import chunk_parsed_pages, chunks_to_contract_frame
from finintel.rag.embedder import BgeM3Embedder
from finintel.rag.parser import (
    DEFAULT_OCR_CONCURRENCY,
    DEFAULT_OCR_DPI,
    DEFAULT_PARSED_CACHE_DIR,
    LocalGpuOcrEngine,
    OcrBackend,
    ParsedDocument,
    parse_curated_pdf,
)
from finintel.rag.pdf_catalog import CuratedPdfRecord, load_existing_manifest_records
from finintel.rag.store import (
    DEFAULT_RAG_PG_DSN,
    IndexedDocumentSummary,
    PgVectorRagStore,
)

DEFAULT_CURATED_ROOT = Path(
    "d:/KiCuoiCung/DATN/financial-intelligence/data/curated_pdfs"
)
DEFAULT_STATS_OUTPUT = Path(
    "d:/KiCuoiCung/DATN/financial-intelligence/data/curated_pdfs/parsed_cache/index_run_stats.json"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("finintel.scripts.index_curated_pdfs")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse, chunk, GPU-embed (BGE-M3), and index curated PDFs into finintel_rag."
    )
    parser.add_argument(
        "--curated-root",
        type=Path,
        default=DEFAULT_CURATED_ROOT,
        help="Directory containing curated_pdfs_manifest.parquet and sha256/ archive.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_PARSED_CACHE_DIR,
        help="Resumable per-page parse/OCR JSON cache directory.",
    )
    parser.add_argument(
        "--dsn",
        type=str,
        default=DEFAULT_RAG_PG_DSN,
        help="PostgreSQL finintel_rag DSN.",
    )
    parser.add_argument(
        "--ocr-backend",
        type=str,
        choices=("auto", "gemini", "easyocr"),
        default="auto",
        help="OCR backend for scanned/legacy-font pages (auto falls back to EasyOCR GPU on 429).",
    )
    parser.add_argument(
        "--ocr-concurrency",
        type=int,
        default=DEFAULT_OCR_CONCURRENCY,
        help="Max concurrent Gemini Vision OCR requests per PDF.",
    )
    parser.add_argument(
        "--ocr-dpi",
        type=int,
        default=DEFAULT_OCR_DPI,
        help="PNG render DPI for scanned/legacy-font pages.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=16,
        help="Embedding batch size for BAAI/bge-m3.",
    )
    parser.add_argument(
        "--skip-indexed",
        action="store_true",
        help="Skip PDFs already indexed in document_files with chunk_count > 0.",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="Disable Vision/GPU OCR (extract digital pages only).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional limit on number of PDFs to index (0 = all).",
    )
    parser.add_argument(
        "--tickers",
        type=str,
        default="",
        help="Optional comma-separated ticker subset (e.g. HBC,POM,FPT).",
    )
    parser.add_argument(
        "--stats-out",
        type=Path,
        default=DEFAULT_STATS_OUTPUT,
        help="Path to write JSON summary of indexing run.",
    )
    return parser.parse_args()


def _filter_records(
    records: list[CuratedPdfRecord],
    *,
    tickers_csv: str,
    limit: int,
) -> list[CuratedPdfRecord]:
    selected = records
    if tickers_csv.strip():
        wanted = {t.strip().upper() for t in tickers_csv.split(",") if t.strip()}
        selected = [r for r in selected if r.issuer_id.upper() in wanted]
    if limit > 0:
        selected = selected[:limit]
    return selected


def main() -> int:
    args = _parse_args()
    manifest_path = args.curated_root / "curated_pdfs_manifest.parquet"
    if not manifest_path.is_file():
        logger.error("Manifest not found at %s", manifest_path)
        return 1

    records = load_existing_manifest_records(
        manifest_path, output_root=args.curated_root
    )
    records = _filter_records(records, tickers_csv=args.tickers, limit=args.limit)
    logger.info("Loaded %d curated PDF records from %s", len(records), manifest_path)

    store = PgVectorRagStore(args.dsn)
    store.ensure_schema()

    ocr_backend: OcrBackend = args.ocr_backend
    gemini_bundle = None
    local_ocr_engine: LocalGpuOcrEngine | None = None

    if not args.no_ocr:
        if ocr_backend in ("auto", "gemini"):
            try:
                gemini_bundle = create_verified_gemini_bundle()
                logger.info(
                    "Gemini Vision OCR verified: mode=%s model=%s",
                    gemini_bundle.mode,
                    gemini_bundle.model,
                )
            except Exception as exc:
                if ocr_backend == "gemini":
                    raise
                logger.warning(
                    "Gemini init failed (%s); switching to LocalGpuOcrEngine (EasyOCR GPU).",
                    type(exc).__name__,
                )
                ocr_backend = "easyocr"
        if ocr_backend in ("auto", "easyocr"):
            local_ocr_engine = LocalGpuOcrEngine(gpu=True)

    run_started = time.perf_counter()
    total_parse_seconds = 0.0
    parsed_pairs: list[tuple[CuratedPdfRecord, ParsedDocument]] = []
    skipped_count = 0
    failed_files: list[dict[str, str]] = []

    # ------------------------------------------------------------------
    # Stage 1: Hybrid Parse + OCR -> Resumable Disk Cache (VRAM-isolated)
    # ------------------------------------------------------------------
    for idx, rec in enumerate(records, start=1):
        pdf_path = args.curated_root / rec.local_rel_path
        if not pdf_path.is_file():
            logger.error("[%d/%d] Missing PDF file: %s", idx, len(records), pdf_path)
            failed_files.append(
                {"ticker": rec.issuer_id, "error": f"Missing {pdf_path}"}
            )
            continue

        if args.skip_indexed and store.has_indexed_pdf(rec.content_sha256):
            skipped_count += 1
            logger.info(
                "[%d/%d] SKIP already indexed: %s FY%d",
                idx,
                len(records),
                rec.issuer_id,
                rec.fiscal_year,
            )
            continue

        try:
            t_parse = time.perf_counter()
            parsed = parse_curated_pdf(
                pdf_path,
                record=rec,
                cache_dir=args.cache_dir,
                gemini_bundle=gemini_bundle,
                local_ocr_engine=local_ocr_engine,
                ocr_backend=ocr_backend,
                enable_vision_ocr=not args.no_ocr,
                ocr_dpi=args.ocr_dpi,
                ocr_concurrency=args.ocr_concurrency,
            )
            parse_dt = time.perf_counter() - t_parse
            total_parse_seconds += parse_dt
            parsed_pairs.append((rec, parsed))
            logger.info(
                "[Stage 1 %d/%d] %s FY%d | pages=%d (digital=%d, ocr=%d) | parse=%.1fs",
                idx,
                len(records),
                rec.issuer_id,
                rec.fiscal_year,
                parsed.total_pages,
                parsed.digital_pages,
                parsed.ocr_pages,
                parse_dt,
            )
        except Exception as exc:
            logger.exception(
                "[Stage 1 %d/%d] FAILED %s FY%d: %s",
                idx,
                len(records),
                rec.issuer_id,
                rec.fiscal_year,
                exc,
            )
            failed_files.append(
                {
                    "ticker": rec.issuer_id,
                    "fiscal_year": str(rec.fiscal_year),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    if local_ocr_engine is not None:
        local_ocr_engine.release()

    # ------------------------------------------------------------------
    # Stage 2: VAS-Safe Chunking + BGE-M3 FP16 GPU Embed + pgvector Upsert
    # ------------------------------------------------------------------
    embedder = BgeM3Embedder(batch_size=args.batch_size)
    logger.info(
        "Stage 2 Embedder ready: model=%s device=%s gpu=%s dtype=%s batch_size=%d",
        embedder.model_name,
        embedder.device,
        embedder.gpu_name,
        embedder.dtype,
        embedder.batch_size,
    )

    total_embed_seconds = 0.0
    total_chunks_embedded = 0
    indexed_count = 0

    for idx, (rec, parsed) in enumerate(parsed_pairs, start=1):
        try:
            segments = chunk_parsed_pages(
                filing_id=parsed.filing_id,
                issuer_id=parsed.issuer_id,
                ticker=parsed.ticker,
                fiscal_year=parsed.fiscal_year,
                fiscal_quarter=parsed.fiscal_quarter,
                statement_scope=parsed.statement_scope,
                doc_type=parsed.doc_type,
                pages=[p.to_chunk_input() for p in parsed.pages if p.text.strip()],
            )
            _ = chunks_to_contract_frame(segments)

            texts = [seg.text_content for seg in segments]
            vectors, emb_stats = embedder.embed_texts(texts)
            total_embed_seconds += emb_stats.elapsed_seconds
            total_chunks_embedded += emb_stats.total_texts

            summary = IndexedDocumentSummary(
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
                summary=summary,
                segments=segments,
                embeddings=vectors,
            )
            indexed_count += 1
            logger.info(
                "[Stage 2 %d/%d] %s FY%d | chunks=%d | embed=%.2fs (%.1f ch/s)",
                idx,
                len(parsed_pairs),
                rec.issuer_id,
                rec.fiscal_year,
                len(segments),
                emb_stats.elapsed_seconds,
                emb_stats.chunks_per_second,
            )
        except Exception as exc:
            logger.exception(
                "[Stage 2 %d/%d] FAILED %s FY%d: %s",
                idx,
                len(parsed_pairs),
                rec.issuer_id,
                rec.fiscal_year,
                exc,
            )
            failed_files.append(
                {
                    "ticker": rec.issuer_id,
                    "fiscal_year": str(rec.fiscal_year),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    db_stats = store.get_index_stats()
    total_wall_seconds = round(time.perf_counter() - run_started, 2)
    avg_chunks_per_sec = (
        round(total_chunks_embedded / total_embed_seconds, 2)
        if total_embed_seconds > 0
        else 0.0
    )

    ocr_label = (
        f"hybrid:{gemini_bundle.model}+easyocr_gpu"
        if gemini_bundle and ocr_backend == "auto"
        else (
            gemini_bundle.model
            if gemini_bundle and ocr_backend == "gemini"
            else "easyocr_gpu_vi_en+gemini_cached"
        )
    )

    run_summary = {
        "indexed_this_run": indexed_count,
        "skipped_this_run": skipped_count,
        "failed_count": len(failed_files),
        "failed_files": failed_files,
        "embedder": {
            "model": embedder.model_name,
            "device": embedder.device,
            "gpu_name": embedder.gpu_name,
            "dtype": embedder.dtype,
            "batch_size": embedder.batch_size,
            "chunks_embedded_this_run": total_chunks_embedded,
            "embed_seconds_this_run": round(total_embed_seconds, 2),
            "chunks_per_second": avg_chunks_per_sec,
        },
        "ocr_model": ocr_label,
        "parse_seconds_this_run": round(total_parse_seconds, 2),
        "wall_seconds_this_run": total_wall_seconds,
        "db_stats": {
            "total_documents": db_stats.total_documents,
            "total_issuers": db_stats.total_issuers,
            "total_chunks": db_stats.total_chunks,
            "total_pages": db_stats.total_pages,
            "total_digital_pages": db_stats.total_digital_pages,
            "total_ocr_pages": db_stats.total_ocr_pages,
            "table_size_mb": round(db_stats.table_size_bytes / (1024 * 1024), 2),
            "index_size_mb": round(db_stats.index_size_bytes / (1024 * 1024), 2),
        },
    }

    args.stats_out.parent.mkdir(parents=True, exist_ok=True)
    args.stats_out.write_text(
        json.dumps(run_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "Indexing complete! Summary:\n%s",
        json.dumps(run_summary, ensure_ascii=False, indent=2),
    )
    return 0 if not failed_files else 2


if __name__ == "__main__":
    sys.exit(main())
