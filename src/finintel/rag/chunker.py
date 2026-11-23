"""VAS-safe chunker matching Port 2 ``DOCUMENT_CHUNKS_CONTRACT`` (UI-08b / H-08)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl

from finintel.ports.documents import (
    DOCUMENT_CHUNKS_CONTRACT,
    DOCUMENT_STATEMENT_SCOPES,
    DocumentChunk,
    validate_document_chunks,
)

DEFAULT_MAX_CHUNK_CHARS = 1800
DEFAULT_OVERLAP_CHARS = 240
MIN_CHUNK_BODY_CHARS = 24

_SCOPE_VN_LABELS: dict[str, str] = {
    "CONSOLIDATED": "Hợp nhất",
    "SEPARATE": "Riêng",
}

_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?(?:\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?\s*$")


@dataclass(frozen=True, slots=True)
class ChunkInputPage:
    """Normalized page input accepted by the VAS-safe chunker."""

    page_no: int
    text: str
    section_title: str
    unit_scale: str


@dataclass(frozen=True, slots=True)
class ChunkedDocumentSegment:
    """Chunk ready for embedding and PostgreSQL ``pgvector`` storage."""

    filing_id: str
    issuer_id: str
    ticker: str
    fiscal_year: int
    fiscal_quarter: int | None
    statement_scope: str
    doc_type: str
    page_no: int
    chunk_index: int
    section_title: str
    unit_scale: str
    header_prefix: str
    body_text: str
    text_content: str
    content_sha256: str

    def to_document_chunk(self, *, score: float | None = None) -> DocumentChunk:
        """Convert to the Port 2 ``DocumentChunk`` contract dataclass."""
        return DocumentChunk(
            filing_id=self.filing_id,
            issuer_id=self.issuer_id,
            fiscal_year=self.fiscal_year,
            fiscal_quarter=self.fiscal_quarter,
            statement_scope=self.statement_scope,
            doc_type=self.doc_type,
            page_no=self.page_no,
            section_title=self.section_title,
            unit_scale=self.unit_scale,
            text_content=self.text_content,
            content_sha256=self.content_sha256,
            score=score,
        )


def format_period_label(fiscal_year: int, fiscal_quarter: int | None) -> str:
    """Format fiscal period for chunk header prefixes."""
    if fiscal_quarter is None:
        return f"FY{fiscal_year}"
    return f"Q{fiscal_quarter}/{fiscal_year}"


def build_chunk_header_prefix(
    *,
    ticker: str,
    fiscal_year: int,
    fiscal_quarter: int | None,
    statement_scope: str,
    section_title: str,
    unit_scale: str,
) -> str:
    """Build the mandatory VAS anti-mixing header prefix (H-08)."""
    clean_ticker = ticker.strip().upper()
    clean_scope = statement_scope.strip().upper()
    if clean_scope not in DOCUMENT_STATEMENT_SCOPES:
        raise ValueError(
            f"Unsupported statement_scope {statement_scope!r}; expected one of {sorted(DOCUMENT_STATEMENT_SCOPES)}."
        )
    scope_vn = _SCOPE_VN_LABELS[clean_scope]
    period = format_period_label(fiscal_year, fiscal_quarter)
    clean_section = " ".join(section_title.strip().split()) or "Thuyết minh BCTC"
    clean_unit = " ".join(unit_scale.strip().split()) or "VND"
    return (
        f"[Mã: {clean_ticker} | Kỳ: {period} | Phạm vi: {scope_vn} "
        f"| Mục: {clean_section} | Đơn vị: {clean_unit}]"
    )


def compute_chunk_sha256(
    *,
    filing_id: str,
    page_no: int,
    chunk_index: int,
    text_content: str,
) -> str:
    """Compute deterministic 64-char hex content hash for idempotent upsert."""
    payload = f"{filing_id}|{page_no}|{chunk_index}|{text_content}".encode()
    return hashlib.sha256(payload).hexdigest()


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.count("|") >= 2


def _split_long_paragraph(paragraph: str, max_body_chars: int) -> list[str]:
    """Split a single overly long paragraph on sentence or word boundaries."""
    if len(paragraph) <= max_body_chars:
        return [paragraph]

    pieces: list[str] = []
    remaining = paragraph.strip()
    while len(remaining) > max_body_chars:
        window = remaining[:max_body_chars]
        cut = max(window.rfind(". "), window.rfind("; "), window.rfind("\n"))
        if cut < max_body_chars // 3:
            cut = window.rfind(" ")
        if cut < max_body_chars // 4:
            cut = max_body_chars
        else:
            cut += 1
        piece = remaining[:cut].strip()
        if piece:
            pieces.append(piece)
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def _split_page_text_table_aware(
    text: str,
    *,
    max_body_chars: int,
    overlap_chars: int,
) -> list[str]:
    """Split a single page's text while keeping Markdown tables coherent."""
    cleaned = re.sub(r"\r\n?", "\n", text).strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_body_chars:
        return [cleaned]

    lines = cleaned.splitlines()
    blocks: list[tuple[bool, list[str]]] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if _is_table_line(line):
            table_lines: list[str] = []
            while idx < len(lines) and (
                _is_table_line(lines[idx]) or _TABLE_SEPARATOR_RE.match(lines[idx])
            ):
                table_lines.append(lines[idx])
                idx += 1
            blocks.append((True, table_lines))
        else:
            text_lines: list[str] = []
            while idx < len(lines) and not _is_table_line(lines[idx]):
                text_lines.append(lines[idx])
                idx += 1
            block_text = "\n".join(text_lines).strip()
            if block_text:
                blocks.append((False, [block_text]))

    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    def _flush() -> None:
        nonlocal current_parts, current_len
        if current_parts:
            merged = "\n\n".join(part for part in current_parts if part.strip()).strip()
            if merged:
                chunks.append(merged)
        current_parts = []
        current_len = 0

    for is_table, block_lines in blocks:
        if is_table:
            table_header: list[str] = []
            data_rows = block_lines
            if len(block_lines) >= 2 and _TABLE_SEPARATOR_RE.match(block_lines[1]):
                table_header = block_lines[:2]
                data_rows = block_lines[2:]
            header_text = "\n".join(table_header)
            full_table = "\n".join(block_lines)
            if current_len + len(full_table) + 2 <= max_body_chars:
                current_parts.append(full_table)
                current_len += len(full_table) + 2
                continue

            if current_parts:
                _flush()

            if len(full_table) <= max_body_chars:
                current_parts.append(full_table)
                current_len = len(full_table)
                continue

            # Split large table by rows while repeating Markdown column headers.
            row_buffer: list[str] = list(table_header)
            row_len = len(header_text)
            for row in data_rows:
                candidate_len = row_len + len(row) + 1
                if candidate_len > max_body_chars and len(row_buffer) > len(
                    table_header
                ):
                    chunks.append("\n".join(row_buffer))
                    row_buffer = list(table_header)
                    row_len = len(header_text)
                row_buffer.append(row)
                row_len += len(row) + 1
            if len(row_buffer) > len(table_header) or (not table_header and row_buffer):
                chunks.append("\n".join(row_buffer))
        else:
            paragraphs = [
                p.strip() for p in re.split(r"\n{2,}", block_lines[0]) if p.strip()
            ]
            for para in paragraphs:
                for piece in _split_long_paragraph(para, max_body_chars):
                    if current_len + len(piece) + 2 > max_body_chars and current_parts:
                        tail = (
                            current_parts[-1][-overlap_chars:].strip()
                            if overlap_chars > 0
                            else ""
                        )
                        _flush()
                        if tail and len(tail) + len(piece) + 2 <= max_body_chars:
                            current_parts.append(tail)
                            current_len = len(tail)
                    current_parts.append(piece)
                    current_len += len(piece) + 2

    _flush()
    return [chunk for chunk in chunks if len(chunk.strip()) >= MIN_CHUNK_BODY_CHARS]


def chunk_parsed_pages(
    *,
    filing_id: str,
    issuer_id: str,
    ticker: str,
    fiscal_year: int,
    fiscal_quarter: int | None,
    statement_scope: str,
    doc_type: str,
    pages: Sequence[ChunkInputPage],
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[ChunkedDocumentSegment]:
    """Chunk parsed PDF pages into Port 2 ``DOCUMENT_CHUNKS_CONTRACT`` segments."""
    clean_scope = statement_scope.strip().upper()
    if clean_scope not in DOCUMENT_STATEMENT_SCOPES:
        raise ValueError(
            f"Unsupported statement_scope {statement_scope!r}; expected one of {sorted(DOCUMENT_STATEMENT_SCOPES)}."
        )
    clean_issuer = issuer_id.strip().upper()
    clean_ticker = ticker.strip().upper()

    segments: list[ChunkedDocumentSegment] = []
    for page in pages:
        if page.page_no < 1:
            raise ValueError(f"page_no must be >= 1, got {page.page_no}.")
        header_prefix = build_chunk_header_prefix(
            ticker=clean_ticker,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            statement_scope=clean_scope,
            section_title=page.section_title,
            unit_scale=page.unit_scale,
        )
        max_body_chars = max(400, max_chunk_chars - len(header_prefix) - 2)
        body_slices = _split_page_text_table_aware(
            page.text,
            max_body_chars=max_body_chars,
            overlap_chars=min(overlap_chars, max_body_chars // 4),
        )
        for chunk_index, body in enumerate(body_slices):
            full_text = f"{header_prefix}\n{body}"
            content_sha = compute_chunk_sha256(
                filing_id=filing_id,
                page_no=page.page_no,
                chunk_index=chunk_index,
                text_content=full_text,
            )
            segments.append(
                ChunkedDocumentSegment(
                    filing_id=filing_id,
                    issuer_id=clean_issuer,
                    ticker=clean_ticker,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                    statement_scope=clean_scope,
                    doc_type=doc_type,
                    page_no=page.page_no,
                    chunk_index=chunk_index,
                    section_title=page.section_title.strip() or "Thuyết minh BCTC",
                    unit_scale=page.unit_scale.strip() or "VND",
                    header_prefix=header_prefix,
                    body_text=body,
                    text_content=full_text,
                    content_sha256=content_sha,
                )
            )
    return segments


def chunks_to_contract_frame(
    segments: Sequence[ChunkedDocumentSegment],
) -> pl.DataFrame:
    """Convert chunk segments to a validated ``DOCUMENT_CHUNKS_CONTRACT`` Polars DataFrame."""
    if not segments:
        return pl.DataFrame(schema=DOCUMENT_CHUNKS_CONTRACT)
    frame = pl.DataFrame(
        [
            {
                "filing_id": seg.filing_id,
                "issuer_id": seg.issuer_id,
                "fiscal_year": seg.fiscal_year,
                "fiscal_quarter": seg.fiscal_quarter,
                "statement_scope": seg.statement_scope,
                "doc_type": seg.doc_type,
                "page_no": seg.page_no,
                "section_title": seg.section_title,
                "unit_scale": seg.unit_scale,
                "text_content": seg.text_content,
                "content_sha256": seg.content_sha256,
            }
            for seg in segments
        ],
        schema=DOCUMENT_CHUNKS_CONTRACT,
    )
    return validate_document_chunks(frame)


__all__ = [
    "ChunkInputPage",
    "ChunkedDocumentSegment",
    "DEFAULT_MAX_CHUNK_CHARS",
    "DEFAULT_OVERLAP_CHARS",
    "build_chunk_header_prefix",
    "chunk_parsed_pages",
    "chunks_to_contract_frame",
    "compute_chunk_sha256",
    "format_period_label",
]
