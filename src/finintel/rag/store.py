"""PostgreSQL pgvector + unaccent FTS store for ``finintel_rag`` (UI-08b / Port 2)."""

from __future__ import annotations

import importlib
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from finintel.rag.chunker import ChunkedDocumentSegment
from finintel.rag.embedder import EMBEDDING_DIMENSION

DEFAULT_RAG_PG_DSN = "postgresql://finintel:finintel_dev@127.0.0.1:5432/finintel_rag"


def resolve_rag_pg_dsn(explicit: str | None = None) -> str:
    """Resolve ``finintel_rag`` PostgreSQL DSN from explicit value or environment."""
    if explicit is not None and explicit.strip():
        return explicit.strip()
    env_dsn = os.getenv("FININTEL_RAG_PG_DSN", "").strip()
    return env_dsn or DEFAULT_RAG_PG_DSN


@dataclass(frozen=True, slots=True)
class IndexedDocumentSummary:
    """Metadata summary for an indexed audited PDF stored in ``document_files``."""

    filing_id: str
    issuer_id: str
    ticker: str
    fiscal_year: int
    fiscal_quarter: int | None
    statement_scope: str
    doc_type: str
    cohort: str
    source_title: str
    source_url: str
    pdf_sha256: str
    file_size_bytes: int
    total_pages: int
    digital_pages: int
    ocr_pages: int
    chunk_count: int


@dataclass(frozen=True, slots=True)
class RagIndexStats:
    """Live storage & index statistics from PostgreSQL ``finintel_rag``."""

    total_documents: int
    total_issuers: int
    total_chunks: int
    total_pages: int
    total_digital_pages: int
    total_ocr_pages: int
    table_size_bytes: int
    index_size_bytes: int


_SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE EXTENSION IF NOT EXISTS vector;",
    "CREATE EXTENSION IF NOT EXISTS unaccent;",
    """
    CREATE TABLE IF NOT EXISTS document_files (
        filing_id TEXT PRIMARY KEY,
        issuer_id TEXT NOT NULL,
        ticker TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL,
        fiscal_quarter INTEGER NULL,
        statement_scope TEXT NOT NULL CHECK (statement_scope IN ('CONSOLIDATED', 'SEPARATE')),
        doc_type TEXT NOT NULL,
        cohort TEXT NOT NULL DEFAULT 'curated',
        source_title TEXT NOT NULL,
        source_url TEXT NOT NULL,
        pdf_sha256 CHAR(64) NOT NULL UNIQUE,
        file_size_bytes BIGINT NOT NULL,
        total_pages INTEGER NOT NULL,
        digital_pages INTEGER NOT NULL,
        ocr_pages INTEGER NOT NULL,
        chunk_count INTEGER NOT NULL DEFAULT 0,
        indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    f"""
    CREATE TABLE IF NOT EXISTS document_chunks (
        chunk_id BIGSERIAL PRIMARY KEY,
        content_sha256 CHAR(64) NOT NULL UNIQUE,
        filing_id TEXT NOT NULL REFERENCES document_files(filing_id) ON DELETE CASCADE,
        issuer_id TEXT NOT NULL,
        ticker TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL,
        fiscal_quarter INTEGER NULL,
        statement_scope TEXT NOT NULL CHECK (statement_scope IN ('CONSOLIDATED', 'SEPARATE')),
        doc_type TEXT NOT NULL,
        page_no INTEGER NOT NULL CHECK (page_no >= 1),
        chunk_index INTEGER NOT NULL DEFAULT 0,
        section_title TEXT NOT NULL,
        unit_scale TEXT NOT NULL,
        text_content TEXT NOT NULL,
        search_tsv TSVECTOR NOT NULL,
        embedding VECTOR({EMBEDDING_DIMENSION}) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_document_files_ticker
    ON document_files (ticker, fiscal_year DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_document_chunks_scope
    ON document_chunks (issuer_id, statement_scope, fiscal_year);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_document_chunks_ticker
    ON document_chunks (ticker, statement_scope, fiscal_year);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_document_chunks_tsv
    ON document_chunks USING GIN (search_tsv);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw
    ON document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
    """,
)


class PgVectorRagStore:
    """PostgreSQL ``pgvector`` + ``unaccent`` FTS storage adapter for Port 2 documents."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = resolve_rag_pg_dsn(dsn)

    def _connect(self) -> Any:
        psycopg: Any = importlib.import_module("psycopg")  # Lazy import (M-03)
        pgvector_psycopg: Any = importlib.import_module("pgvector.psycopg")

        conn = psycopg.connect(self.dsn)
        pgvector_psycopg.register_vector(conn)
        return conn

    def ensure_schema(self) -> None:
        """Idempotently create ``vector``/``unaccent`` extensions, tables, HNSW & GIN indexes."""
        psycopg: Any = importlib.import_module("psycopg")  # Lazy import (M-03)

        with psycopg.connect(self.dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                for stmt in _SCHEMA_STATEMENTS:
                    cur.execute(stmt)

    def upsert_document_and_chunks(
        self,
        *,
        summary: IndexedDocumentSummary,
        segments: Sequence[ChunkedDocumentSegment],
        embeddings: Sequence[Sequence[float]],
    ) -> int:
        """Idempotently upsert one PDF's metadata and all its chunks/embeddings in a transaction."""
        if len(segments) != len(embeddings):
            raise ValueError(
                f"Segment count ({len(segments)}) != embedding count ({len(embeddings)})."
            )

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO document_files (
                        filing_id, issuer_id, ticker, fiscal_year, fiscal_quarter,
                        statement_scope, doc_type, cohort, source_title, source_url,
                        pdf_sha256, file_size_bytes, total_pages, digital_pages,
                        ocr_pages, chunk_count, indexed_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, NOW()
                    )
                    ON CONFLICT (filing_id) DO UPDATE SET
                        cohort = EXCLUDED.cohort,
                        source_title = EXCLUDED.source_title,
                        source_url = EXCLUDED.source_url,
                        total_pages = EXCLUDED.total_pages,
                        digital_pages = EXCLUDED.digital_pages,
                        ocr_pages = EXCLUDED.ocr_pages,
                        chunk_count = EXCLUDED.chunk_count,
                        indexed_at = NOW()
                    """,
                    (
                        summary.filing_id,
                        summary.issuer_id,
                        summary.ticker,
                        summary.fiscal_year,
                        summary.fiscal_quarter,
                        summary.statement_scope,
                        summary.doc_type,
                        summary.cohort,
                        summary.source_title,
                        summary.source_url,
                        summary.pdf_sha256,
                        summary.file_size_bytes,
                        summary.total_pages,
                        summary.digital_pages,
                        summary.ocr_pages,
                        len(segments),
                    ),
                )

                if segments:
                    # Remove stale chunks for this filing that no longer match current content_sha256.
                    active_hashes = [seg.content_sha256 for seg in segments]
                    cur.execute(
                        """
                        DELETE FROM document_chunks
                        WHERE filing_id = %s
                          AND NOT (content_sha256 = ANY(%s))
                        """,
                        (summary.filing_id, active_hashes),
                    )

                    rows = [
                        (
                            seg.content_sha256,
                            seg.filing_id,
                            seg.issuer_id,
                            seg.ticker,
                            seg.fiscal_year,
                            seg.fiscal_quarter,
                            seg.statement_scope,
                            seg.doc_type,
                            seg.page_no,
                            seg.chunk_index,
                            seg.section_title,
                            seg.unit_scale,
                            seg.text_content,
                            seg.text_content,
                            list(vec),
                        )
                        for seg, vec in zip(segments, embeddings, strict=True)
                    ]
                    cur.executemany(
                        """
                        INSERT INTO document_chunks (
                            content_sha256, filing_id, issuer_id, ticker,
                            fiscal_year, fiscal_quarter, statement_scope, doc_type,
                            page_no, chunk_index, section_title, unit_scale,
                            text_content, search_tsv, embedding
                        )
                        VALUES (
                            %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, to_tsvector('simple', unaccent(%s)), %s
                        )
                        ON CONFLICT (content_sha256) DO UPDATE SET
                            section_title = EXCLUDED.section_title,
                            unit_scale = EXCLUDED.unit_scale,
                            text_content = EXCLUDED.text_content,
                            search_tsv = EXCLUDED.search_tsv,
                            embedding = EXCLUDED.embedding
                        """,
                        rows,
                    )
            conn.commit()
        return len(segments)

    def has_indexed_pdf(self, pdf_sha256: str) -> bool:
        """Return True if a PDF with ``pdf_sha256`` is already indexed with >= 1 chunk."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT chunk_count FROM document_files WHERE pdf_sha256 = %s",
                    (pdf_sha256,),
                )
                row = cur.fetchone()
                return bool(row is not None and int(row[0]) > 0)

    def list_documents(self, ticker: str) -> list[IndexedDocumentSummary]:
        """List all indexed audited PDFs for a given ticker/issuer_id."""
        clean = ticker.strip().upper()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        filing_id, issuer_id, ticker, fiscal_year, fiscal_quarter,
                        statement_scope, doc_type, cohort, source_title, source_url,
                        pdf_sha256, file_size_bytes, total_pages, digital_pages,
                        ocr_pages, chunk_count
                    FROM document_files
                    WHERE ticker = %s OR issuer_id = %s
                    ORDER BY fiscal_year DESC, filing_id ASC
                    """,
                    (clean, clean),
                )
                rows = cur.fetchall()
        return [
            IndexedDocumentSummary(
                filing_id=str(r[0]),
                issuer_id=str(r[1]),
                ticker=str(r[2]),
                fiscal_year=int(r[3]),
                fiscal_quarter=int(r[4]) if r[4] is not None else None,
                statement_scope=str(r[5]),
                doc_type=str(r[6]),
                cohort=str(r[7]),
                source_title=str(r[8]),
                source_url=str(r[9]),
                pdf_sha256=str(r[10]).strip(),
                file_size_bytes=int(r[11]),
                total_pages=int(r[12]),
                digital_pages=int(r[13]),
                ocr_pages=int(r[14]),
                chunk_count=int(r[15]),
            )
            for r in rows
        ]

    def get_index_stats(self) -> RagIndexStats:
        """Return aggregate document, chunk, page, and PostgreSQL index size statistics."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*)::INT,
                        COUNT(DISTINCT issuer_id)::INT,
                        COALESCE(SUM(total_pages), 0)::INT,
                        COALESCE(SUM(digital_pages), 0)::INT,
                        COALESCE(SUM(ocr_pages), 0)::INT
                    FROM document_files
                    """
                )
                f_row = cur.fetchone() or (0, 0, 0, 0, 0)

                cur.execute("SELECT COUNT(*)::INT FROM document_chunks")
                c_row = cur.fetchone() or (0,)

                cur.execute(
                    """
                    SELECT
                        pg_table_size('document_chunks')::BIGINT,
                        pg_indexes_size('document_chunks')::BIGINT
                    """
                )
                s_row = cur.fetchone() or (0, 0)

        return RagIndexStats(
            total_documents=int(f_row[0]),
            total_issuers=int(f_row[1]),
            total_chunks=int(c_row[0]),
            total_pages=int(f_row[2]),
            total_digital_pages=int(f_row[3]),
            total_ocr_pages=int(f_row[4]),
            table_size_bytes=int(s_row[0]),
            index_size_bytes=int(s_row[1]),
        )


__all__ = [
    "DEFAULT_RAG_PG_DSN",
    "IndexedDocumentSummary",
    "PgVectorRagStore",
    "RagIndexStats",
    "resolve_rag_pg_dsn",
]
