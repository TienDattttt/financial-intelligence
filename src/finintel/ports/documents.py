"""Extension Port 2: Grounded Document AI / PDF RAG contract & CDN/ZIP guards (ARCH-09 / H-02 / H-08)."""

from __future__ import annotations

import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZipFile

import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.storage.schemas import (
    DOCUMENT_CHUNKS_CONTRACT,
    DOCUMENT_STATEMENT_SCOPES,
    canonicalize_table,
)

PDF_CDN_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "images1.cafef.vn",
        "cafef1.mediacdn.vn",
        "staticfile.hsx.vn",
    }
)

MAX_PDF_ZIP_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_PDF_ZIP_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_PDF_ZIP_MEMBER_BYTES = 50 * 1024 * 1024
MAX_PDF_ZIP_ENTRIES = 64
MAX_PDF_ZIP_COMPRESSION_RATIO = 100.0
_ZIP_SYMLINK_MODE = 0o120000
_ZIP_FILE_TYPE_MASK = 0o170000


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    """Typed retrieval unit matching ``DOCUMENT_CHUNKS_CONTRACT``."""

    filing_id: str
    issuer_id: str
    fiscal_year: int
    fiscal_quarter: int | None
    statement_scope: str
    doc_type: str
    page_no: int
    section_title: str
    unit_scale: str
    text_content: str
    content_sha256: str
    score: float | None = None


@runtime_checkable
class DocumentRetriever(Protocol):
    """Port 2 retrieval protocol for scope-isolated, citation-backed RAG queries."""

    def retrieve(
        self,
        *,
        issuer_id: str,
        query: str,
        statement_scope: str | None = None,
        fiscal_year: int | None = None,
        fiscal_quarter: int | None = None,
        limit: int = 5,
    ) -> Sequence[DocumentChunk]:
        """Return grounded document chunks scoped to ``issuer_id`` and optional statement scope."""
        ...


def validate_document_chunks(frame: pl.DataFrame) -> pl.DataFrame:
    """Canonicalize and validate a Port 2 chunk DataFrame against ``DOCUMENT_CHUNKS_CONTRACT``."""
    return canonicalize_table(frame, DOCUMENT_CHUNKS_CONTRACT)


def validate_pdf_cdn_url(url: str) -> str:
    """Enforce HTTPS, port 443, no credentials, and the dedicated PDF CDN host allowlist (H-02)."""
    if not isinstance(url, str) or not url.strip() or any(ch.isspace() for ch in url):
        raise ContractViolationError("PDF CDN URL must be a valid HTTPS URL.")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise ContractViolationError("PDF CDN URL must use the HTTPS scheme.")
    if parsed.username is not None or parsed.password is not None:
        raise ContractViolationError("PDF CDN URL cannot include embedded credentials.")
    host = parsed.hostname.lower() if parsed.hostname is not None else ""
    if host not in PDF_CDN_ALLOWED_HOSTS:
        raise ContractViolationError(
            f"PDF CDN host {host!r} is not in the PDF CDN HTTPS allowlist."
        )
    if parsed.port not in (None, 443):
        raise ContractViolationError(
            "PDF CDN URL must use the standard HTTPS port 443."
        )
    return url


def _validate_zip_member_path(filename: str) -> str:
    if not filename or "\x00" in filename or "\\" in filename:
        raise ContractViolationError(
            f"ZIP archive contains an unsafe member path: {filename!r}."
        )
    if filename.startswith("/") or (len(filename) >= 2 and filename[1] == ":"):
        raise ContractViolationError(
            f"ZIP archive contains a non-relative member path: {filename!r}."
        )
    pure = PurePosixPath(filename)
    if pure.is_absolute() or ".." in pure.parts:
        raise ContractViolationError(
            f"ZIP archive contains a path-traversal member: {filename!r}."
        )
    return pure.as_posix()


def extract_pdf_members_from_zip(
    archive_bytes: bytes,
    *,
    max_archive_bytes: int = MAX_PDF_ZIP_ARCHIVE_BYTES,
    max_uncompressed_bytes: int = MAX_PDF_ZIP_UNCOMPRESSED_BYTES,
    max_member_bytes: int = MAX_PDF_ZIP_MEMBER_BYTES,
    max_entries: int = MAX_PDF_ZIP_ENTRIES,
    max_compression_ratio: float = MAX_PDF_ZIP_COMPRESSION_RATIO,
) -> dict[str, bytes]:
    """Safely extract ``.pdf`` members from a downloaded ZIP archive with zip-bomb & traversal guards (H-02)."""
    if not archive_bytes:
        raise ContractViolationError("PDF ZIP archive is empty.")
    if len(archive_bytes) > max_archive_bytes:
        raise ContractViolationError(
            f"PDF ZIP archive exceeds maximum allowed size ({max_archive_bytes} bytes)."
        )

    extracted: dict[str, bytes] = {}
    total_uncompressed = 0
    try:
        with ZipFile(BytesIO(archive_bytes)) as archive:
            infos = archive.infolist()
            if len(infos) > max_entries:
                raise ContractViolationError(
                    f"PDF ZIP archive exceeds maximum entry count ({max_entries})."
                )
            for info in infos:
                unix_mode = (info.external_attr >> 16) & _ZIP_FILE_TYPE_MASK
                if unix_mode == _ZIP_SYMLINK_MODE:
                    raise ContractViolationError(
                        f"ZIP archive cannot contain symbolic links: {info.filename!r}."
                    )
                if info.is_dir():
                    _validate_zip_member_path(info.filename.rstrip("/") or ".")
                    continue

                safe_name = _validate_zip_member_path(info.filename)
                if not safe_name.lower().endswith(".pdf"):
                    raise ContractViolationError(
                        f"PDF ZIP archive contains disallowed non-PDF member: {safe_name!r}."
                    )
                if info.file_size > max_member_bytes:
                    raise ContractViolationError(
                        f"PDF ZIP member {safe_name!r} exceeds max member size ({max_member_bytes} bytes)."
                    )
                if info.compress_size == 0 and info.file_size > 0:
                    raise ContractViolationError(
                        f"PDF ZIP member {safe_name!r} has invalid zero compressed size."
                    )
                if (
                    info.compress_size > 0
                    and (info.file_size / info.compress_size) > max_compression_ratio
                ):
                    raise ContractViolationError(
                        f"PDF ZIP member {safe_name!r} exceeds maximum compression ratio."
                    )

                with archive.open(info, "r") as handle:
                    remaining_total = max_uncompressed_bytes - total_uncompressed
                    read_limit = min(max_member_bytes, remaining_total)
                    payload = handle.read(read_limit + 1)

                if len(payload) > max_member_bytes:
                    raise ContractViolationError(
                        f"PDF ZIP member {safe_name!r} actual uncompressed size exceeds limit."
                    )
                total_uncompressed += len(payload)
                if total_uncompressed > max_uncompressed_bytes:
                    raise ContractViolationError(
                        f"PDF ZIP archive uncompressed payload exceeds {max_uncompressed_bytes} bytes."
                    )
                if not payload.startswith(b"%PDF-"):
                    raise ContractViolationError(
                        f"PDF ZIP member {safe_name!r} does not start with a valid %PDF- header."
                    )
                if safe_name in extracted:
                    raise ContractViolationError(
                        f"PDF ZIP archive contains duplicate member path: {safe_name!r}."
                    )
                extracted[safe_name] = payload
    except ContractViolationError:
        raise
    except (BadZipFile, zlib.error, EOFError, RuntimeError, OSError):
        raise ContractViolationError("Invalid or corrupted PDF ZIP archive.") from None

    if not extracted:
        raise ContractViolationError("PDF ZIP archive contains no PDF files.")
    return extracted


__all__ = [
    "DOCUMENT_CHUNKS_CONTRACT",
    "DOCUMENT_STATEMENT_SCOPES",
    "DocumentChunk",
    "DocumentRetriever",
    "MAX_PDF_ZIP_ARCHIVE_BYTES",
    "MAX_PDF_ZIP_COMPRESSION_RATIO",
    "MAX_PDF_ZIP_ENTRIES",
    "MAX_PDF_ZIP_MEMBER_BYTES",
    "MAX_PDF_ZIP_UNCOMPRESSED_BYTES",
    "PDF_CDN_ALLOWED_HOSTS",
    "extract_pdf_members_from_zip",
    "validate_document_chunks",
    "validate_pdf_cdn_url",
]
