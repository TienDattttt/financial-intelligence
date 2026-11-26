"""Unit tests for TASK-06: CAS-backed Curated PDF service & PDF parser resource caps."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from finintel.domain.schemas import RawFilingBundle, RawPayload
from finintel.exceptions import ContractViolationError
from finintel.ingestion.archive import RawArchive
from finintel.pipeline.state_store import StateStore
from finintel.rag.pdf_catalog import CuratedPdfRecord

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_task06_extract_pdf_candidates_from_raw_archive_cas_and_sync_manifest(
    tmp_path: Path,
) -> None:
    """TASK-06: curated_pdf_service extracts PDF links from RawArchive CAS receipts without <TICKER>.html."""
    from finintel.ingestion.curated_pdf_service import (
        extract_pdf_candidates_from_raw_archive,
        sync_curated_pdfs_from_archive,
    )

    state = StateStore(tmp_path / "metadata" / "state.sqlite")
    state.initialize()
    raw_root = tmp_path / "raw"
    archive = RawArchive(raw_root, state)

    cafef_html = (
        "<table>"
        "<tr>"
        "<td>Báo cáo tài chính hợp nhất năm 2025 đã được kiểm toán</td>"
        "<td>CN/2025</td>"
        "<td>25/03/2026</td>"
        '<td><a href="https://cafefnew.mediacdn.vn//Images/2026/03/25/FPT_2025_BCTC_HN_KT.pdf">Tải về</a></td>'
        "</tr>"
        "<tr>"
        "<td>Báo cáo tài chính riêng quý 1 năm 2026 tự lập</td>"
        "<td>Q1/2026</td>"
        "<td>20/04/2026</td>"
        '<td><a href="https://cafef1.mediacdn.vn/Images/2026/04/20/FPT_Q1_Me.pdf">Tải về</a></td>'
        "</tr>"
        "</table>"
    )
    src_url = "https://cafef.vn/du-lieu/Ajax/CongTy/BaoCaoTaiChinh.aspx?sym=FPT"
    envelope = json.dumps(
        {
            "raw_sha256": "0" * 64,
            "raw_utf8": cafef_html,
            "source_url": src_url,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    archive.store(
        RawFilingBundle(
            payload=RawPayload(
                content=envelope,
                extension="json",
                source_provider="fhsc",
                connector_name="public_disclosure_enricher_v1",
                nature="PROVIDER_RAW_JSON",
                fetched_at=datetime(2026, 3, 26, 8, 0, tzinfo=UTC),
                provider_attrs={
                    "disclosure_source": "cafef_bctc",
                    "source_url": src_url,
                },
            ),
            filings=(),
        )
    )
    # Confirm zero <TICKER>.html files exist on disk.
    assert list(raw_root.rglob("FPT.html")) == []

    by_ticker = extract_pdf_candidates_from_raw_archive(raw_root)
    assert set(by_ticker) == {"FPT"}
    assert len(by_ticker["FPT"]) == 1
    cand = by_ticker["FPT"][0]
    assert cand.fiscal_year == 2025
    assert (
        cand.source_url
        == "https://cafef1.mediacdn.vn/Images/2026/03/25/FPT_2025_BCTC_HN_KT.pdf"
    )

    sample_pdf = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
    mock_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _req: httpx.Response(200, content=sample_pdf)
        ),
        follow_redirects=False,
    )
    out_root = tmp_path / "curated_pdfs"
    manifest_path = out_root / "curated_pdfs_manifest.parquet"
    try:
        summary = sync_curated_pdfs_from_archive(
            raw_root=raw_root,
            output_root=out_root,
            manifest_path=manifest_path,
            fetch_online=True,
            http_client=mock_client,
        )
    finally:
        mock_client.close()

    assert summary.downloaded_count == 1
    assert summary.total_manifest_records == 1
    assert manifest_path.is_file()


def test_task06_pdf_parser_rejects_over_400_pages_and_clamps_4096px_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TASK-06: parser.py enforces <= 400 pages and <= 4096px rendered dimension before OCR."""
    from finintel.rag import parser as pdf_parser

    assert pdf_parser.MAX_PDF_PAGES == 400
    assert pdf_parser.MAX_RENDERED_IMAGE_DIMENSION_PX == 4096

    class _FakePix:
        def __init__(self, width: int, height: int) -> None:
            self.width = width
            self.height = height

        def tobytes(self, fmt: str) -> bytes:
            assert fmt == "png"
            return b"\x89PNG\r\n\x1a\n"

    class _FakeRect:
        width = 2500.0
        height = 3600.0

    captured_scales: list[tuple[float, float]] = []

    class _FakePage:
        rect = _FakeRect()

        def get_pixmap(self, *, matrix: Any, alpha: bool = False) -> _FakePix:
            _ = alpha
            sx, sy = matrix
            captured_scales.append((sx, sy))
            return _FakePix(
                int(round(self.rect.width * sx)), int(round(self.rect.height * sy))
            )

    class _FakeDoc:
        def __init__(self, page_count: int) -> None:
            self._page_count = page_count

        def __len__(self) -> int:
            return self._page_count

        def __enter__(self) -> _FakeDoc:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class _FakePyMuPDF:
        @staticmethod
        def Matrix(sx: float, sy: float) -> tuple[float, float]:
            return (sx, sy)

        @staticmethod
        def open(_path: str) -> _FakeDoc:
            return _FakeDoc(401)

    import types

    fake_mod = types.ModuleType("pymupdf")
    setattr(fake_mod, "Matrix", _FakePyMuPDF.Matrix)
    setattr(fake_mod, "open", _FakePyMuPDF.open)
    monkeypatch.setitem(sys.modules, "pymupdf", fake_mod)

    png = pdf_parser._render_page_png_bytes(_FakePage(), dpi=200)
    assert png.startswith(b"\x89PNG")
    assert len(captured_scales) == 1
    sx, sy = captured_scales[0]
    assert round(3600.0 * sy) <= 4096
    assert round(2500.0 * sx) <= 4096

    dummy_pdf = tmp_path / "huge_401p.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    rec = CuratedPdfRecord(
        issuer_id="FPT",
        fiscal_year=2025,
        fiscal_quarter=None,
        statement_scope="CONSOLIDATED",
        doc_type="AUDITED_ANNUAL_CONSOLIDATED_FS",
        source_url="https://cafef1.mediacdn.vn/Images/FPT.pdf",
        file_size_bytes=16,
        content_sha256="a" * 64,
        local_rel_path=f"sha256/aa/{'a' * 64}.pdf",
        is_valid_pdf=True,
        cohort="CORE",
        extracted_from_zip=False,
        zip_member_name=None,
        source_title="BCTC hợp nhất kiểm toán 2025",
    )
    with pytest.raises(ContractViolationError, match="400"):
        pdf_parser.parse_curated_pdf(
            dummy_pdf, record=rec, cache_dir=tmp_path / "cache"
        )


def test_task06_curated_pdf_service_has_zero_platform_or_scripts_imports() -> None:
    """TASK-06 / M-03: curated_pdf_service.py loads cleanly in mvp-checks without pymupdf/dagster/pgvector."""
    target = REPO_ROOT / "src" / "finintel" / "ingestion" / "curated_pdf_service.py"
    tree = ast.parse(target.read_text(encoding="utf-8"))
    forbidden = {
        "dagster",
        "pymupdf",
        "fitz",
        "pgvector",
        "psycopg",
        "sentence_transformers",
        "easyocr",
        "fastapi",
        "scripts",
    }
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in forbidden

    sim = """
import sys
from importlib.abc import MetaPathFinder
BLOCKED = {"dagster", "pymupdf", "fitz", "pgvector", "psycopg", "sentence_transformers", "easyocr", "fastapi"}
class _Blocker(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] in BLOCKED:
            raise ModuleNotFoundError(f"Blocked: {fullname}")
        return None
sys.meta_path.insert(0, _Blocker())
import finintel.ingestion.curated_pdf_service
import finintel.rag.parser
print("MVP_RAG_OK")
"""
    proc = subprocess.run(
        [sys.executable, "-c", sim],
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "MVP_RAG_OK" in proc.stdout


def test_task06_pdf_sync_releases_pipeline_lock_during_http_download(
    tmp_path: Path,
) -> None:
    """TASK-06 regression: sync_curated_pdfs_from_archive must NOT hold pipeline.lock while HTTP download runs."""
    from filelock import FileLock

    from finintel.ingestion.curated_pdf_service import sync_curated_pdfs_from_archive

    raw_root = tmp_path / "data" / "raw"
    state_path = tmp_path / "data" / "metadata" / "state.sqlite"
    lock_path = tmp_path / "data" / "metadata" / "pipeline.lock"
    store = StateStore(state_path)
    store.initialize()
    archive = RawArchive(raw_root, store)

    cafef_url = "https://cafef1.mediacdn.vn/Images/2026/03/25/FPT_BCTC_HN_KT_2025.pdf"
    src_url = "https://cafef.vn/du-lieu/Ajax/CongTy/BaoCaoTaiChinh.aspx?sym=FPT"
    envelope = json.dumps(
        {
            "source_url": src_url,
            "raw_sha256": "f" * 64,
            "raw_utf8": (
                "<table><tr>"
                "<td>Báo cáo tài chính hợp nhất năm 2025 đã được kiểm toán</td>"
                "<td>CN/2025</td><td>25/03/2026</td>"
                f'<td><a href="{cafef_url}">Tải về</a></td>'
                "</tr></table>"
            ),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    archive.store(
        RawFilingBundle(
            payload=RawPayload(
                content=envelope,
                extension="json",
                source_provider="fhsc",
                connector_name="public_disclosure_enricher_v1",
                nature="PROVIDER_RAW_JSON",
                fetched_at=datetime(2026, 3, 26, 8, 0, tzinfo=UTC),
                provider_attrs={
                    "disclosure_source": "cafef_bctc",
                    "source_url": src_url,
                },
            ),
            filings=(),
        )
    )

    lock_was_free_during_http = False

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal lock_was_free_during_http
        # Probe that pipeline.lock is NOT held during network download!
        probe = FileLock(str(lock_path), timeout=0.05)
        with probe:
            lock_was_free_during_http = True
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n" + b"X" * 512,
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    try:
        summary = sync_curated_pdfs_from_archive(
            raw_root=raw_root,
            output_root=tmp_path / "curated_pdfs",
            fetch_online=True,
            http_client=client,
            parse_downloaded=False,
            manifest_lock_path=lock_path,
            state_store_path=state_path,
        )
    finally:
        client.close()

    assert summary.downloaded_count == 1
    assert lock_was_free_during_http is True
