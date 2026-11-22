"""Unit tests for curated audited consolidated PDF catalog & Port 2 guards (UI-08a)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import polars as pl
import pytest

from finintel.exceptions import ContractViolationError
from finintel.rag.pdf_catalog import (
    CURATED_PDFS_MANIFEST_SCHEMA,
    DOC_TYPE_AUDITED_ANNUAL_CONSOLIDATED,
    CuratedPdfRecord,
    PdfCandidate,
    build_manifest_dataframe,
    compute_sha256,
    download_curated_pdf,
    extract_audited_consolidated_annual_candidates,
    extract_verified_pdf_payload,
    normalize_cafef_pdf_url,
    render_curated_pdfs_audit_markdown,
    select_candidates_for_years,
    store_immutable_pdf,
    verify_pdf_bytes,
    verify_stored_manifest_files,
    write_manifest_parquet,
)

_SAMPLE_PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


def _make_zip_bytes(
    entries: dict[str, bytes],
    *,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=compression) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buf.getvalue()


def test_normalize_cafef_pdf_url_remaps_legacy_host_and_enforces_allowlist() -> None:
    raw = (
        "https://cafefnew.mediacdn.vn//Images/Uploaded/DuLieuDownload/"
        "BCTC/HBC_23CN_BCTC_HNKT.pdf"
    )
    assert normalize_cafef_pdf_url(raw) == (
        "https://cafef1.mediacdn.vn/Images/Uploaded/DuLieuDownload/"
        "BCTC/HBC_23CN_BCTC_HNKT.pdf"
    )

    hsx = "https://staticfile.hsx.vn/Uploads/LocalFiles/report.pdf"
    assert normalize_cafef_pdf_url(hsx) == hsx

    with pytest.raises(ContractViolationError):
        normalize_cafef_pdf_url("http://cafef1.mediacdn.vn/report.pdf")
    with pytest.raises(ContractViolationError):
        normalize_cafef_pdf_url("https://evil.example.com/report.pdf")
    with pytest.raises(ContractViolationError):
        normalize_cafef_pdf_url("https://user:pass@cafef1.mediacdn.vn/report.pdf")
    with pytest.raises(ContractViolationError):
        normalize_cafef_pdf_url("https://cafef1.mediacdn.vn:8443/report.pdf")


def test_extract_audited_consolidated_annual_candidates_filters_strictly() -> None:
    html = """
    <table>
      <tr>
        <td>Báo cáo tài chính hợp nhất năm 2024 (đã kiểm toán)</td>
        <td>CN/2024</td>
        <td><a href="https://cafefnew.mediacdn.vn//Images/Uploaded/DuLieuDownload/BCTC/HBC_24CN_BCTC_HNKT.pdf">Tải về</a></td>
      </tr>
      <tr>
        <td>Báo cáo tài chính công ty mẹ năm 2024 (đã kiểm toán)</td>
        <td>CN/2024</td>
        <td><a href="https://cafefnew.mediacdn.vn//Images/Uploaded/DuLieuDownload/BCTC/HBC_24CN_BCTC_MEKT.pdf">Tải về</a></td>
      </tr>
      <tr>
        <td>Báo cáo tài chính hợp nhất quý 4 năm 2024 (tự lập)</td>
        <td>Q4/2024</td>
        <td><a href="https://cafefnew.mediacdn.vn//Images/Uploaded/DuLieuDownload/BCTC/HBC_24Q4_BCTC_HN.pdf">Tải về</a></td>
      </tr>
      <tr>
        <td>Báo cáo tài chính hợp nhất năm 2023 (đã kiểm toán) - Bản Tiếng Anh</td>
        <td>CN/2023</td>
        <td><a href="https://cafefnew.mediacdn.vn//Images/Uploaded/DuLieuDownload/BCTC/HBC_23CN_BCTC_ENG.pdf">Tải về</a></td>
      </tr>
      <tr>
        <td>Báo cáo tài chính hợp nhất năm 2023 (đã kiểm toán)</td>
        <td>CN/2023</td>
        <td><a href="https://cafefnew.mediacdn.vn//Images/Uploaded/DuLieuDownload/BCTC/HBC_23CN_BCTC_HNKT.zip">Tải về</a></td>
      </tr>
      <tr>
        <td>Báo cáo tài chính hợp nhất năm 2022 (đã kiểm toán)</td>
        <td>CN/2022</td>
        <td><a href="https://cafefnew.mediacdn.vn//Images/Uploaded/DuLieuDownload/BCTC/HBC_22CN_BCTC_HNKT.rar">Tải về</a></td>
      </tr>
      <tr>
        <td>Báo cáo tài chính hợp nhất năm 2021 (đã kiểm toán)</td>
        <td>CN/2021</td>
        <td><a href="https://cafefnew.mediacdn.vn//Images/Uploaded/DuLieuDownload/BCTC/20220330_-_FPT_-_BCTC_HNKT.pdf">Tải về</a></td>
      </tr>
    </table>
    """
    candidates = extract_audited_consolidated_annual_candidates("HBC", html)
    assert len(candidates) == 2
    assert [
        (c.fiscal_year, c.archive_format, c.statement_scope) for c in candidates
    ] == [
        (2024, "pdf", "CONSOLIDATED"),
        (2023, "zip", "CONSOLIDATED"),
    ]
    assert all(c.fiscal_quarter is None for c in candidates)
    assert all(
        c.source_url.startswith("https://cafef1.mediacdn.vn/") for c in candidates
    )


def test_select_candidates_for_years_respects_preference_and_fallback() -> None:
    cands = tuple(
        PdfCandidate(
            issuer_id="HBC",
            fiscal_year=yr,
            fiscal_quarter=None,
            statement_scope="CONSOLIDATED",
            doc_type=DOC_TYPE_AUDITED_ANNUAL_CONSOLIDATED,
            title=f"BCTC HN KT {yr}",
            raw_url=f"https://cafef1.mediacdn.vn/hbc_{yr}.pdf",
            source_url=f"https://cafef1.mediacdn.vn/hbc_{yr}.pdf",
            archive_format="pdf",
            row_index=idx,
        )
        for idx, yr in enumerate((2024, 2023, 2021))
    )
    selected = select_candidates_for_years(cands, (2022, 2023), max_items=2)
    assert [c.fiscal_year for c in selected] == [2023, 2024]


def test_extract_verified_pdf_payload_direct_zip_and_security_guards() -> None:
    assert verify_pdf_bytes(_SAMPLE_PDF_BYTES)
    assert not verify_pdf_bytes(b"<html>404</html>")
    assert not verify_pdf_bytes(b"")

    pdf_out, from_zip, member = extract_verified_pdf_payload(
        _SAMPLE_PDF_BYTES, archive_format="pdf"
    )
    assert pdf_out == _SAMPLE_PDF_BYTES
    assert from_zip is False
    assert member is None

    with pytest.raises(ContractViolationError):
        extract_verified_pdf_payload(b"<html>not a pdf</html>", archive_format="pdf")

    valid_zip = _make_zip_bytes(
        {
            "2023_giai_trinh.pdf": _SAMPLE_PDF_BYTES + b" ",
            "2023_BCTC_hop_nhat_kiem_toan.pdf": _SAMPLE_PDF_BYTES + b"\n%ok",
        }
    )
    zip_pdf, from_zip, member = extract_verified_pdf_payload(
        valid_zip, archive_format="zip"
    )
    assert from_zip is True
    assert member == "2023_BCTC_hop_nhat_kiem_toan.pdf"
    assert zip_pdf.startswith(b"%PDF-")

    # Path traversal inside ZIP must be rejected by Port 2 guard
    traversal_zip = _make_zip_bytes({"../escape.pdf": _SAMPLE_PDF_BYTES})
    with pytest.raises(ContractViolationError):
        extract_verified_pdf_payload(traversal_zip, archive_format="zip")

    # Non-PDF member inside ZIP must be rejected
    exe_zip = _make_zip_bytes({"payload.exe": b"MZ..."})
    with pytest.raises(ContractViolationError):
        extract_verified_pdf_payload(exe_zip, archive_format="zip")

    # Zip-bomb (compression ratio > 100x) must be rejected
    bomb_payload = b"%PDF-1.7\n" + (b"0" * (2 * 1024 * 1024))
    bomb_zip = _make_zip_bytes({"bomb.pdf": bomb_payload})
    with pytest.raises(ContractViolationError):
        extract_verified_pdf_payload(bomb_zip, archive_format="zip")


def test_store_immutable_pdf_cas_idempotency_and_tamper_detection(
    tmp_path: Path,
) -> None:
    digest, rel_path, size = store_immutable_pdf(
        _SAMPLE_PDF_BYTES, output_root=tmp_path
    )
    assert digest == compute_sha256(_SAMPLE_PDF_BYTES)
    assert rel_path == f"sha256/{digest[:2]}/{digest}.pdf"
    assert size == len(_SAMPLE_PDF_BYTES)
    stored_file = tmp_path / rel_path
    assert stored_file.read_bytes() == _SAMPLE_PDF_BYTES

    # Idempotent re-store succeeds
    digest2, rel_path2, size2 = store_immutable_pdf(
        _SAMPLE_PDF_BYTES, output_root=tmp_path
    )
    assert (digest2, rel_path2, size2) == (digest, rel_path, size)

    # Corrupted existing file must raise ContractViolationError
    stored_file.write_bytes(b"%PDF-corrupted")
    with pytest.raises(ContractViolationError):
        store_immutable_pdf(_SAMPLE_PDF_BYTES, output_root=tmp_path)


def test_download_curated_pdf_handles_pdf_zip_and_blocks_redirects(
    tmp_path: Path,
) -> None:
    zip_body = _make_zip_bytes({"HBC_2023_HNKT.pdf": _SAMPLE_PDF_BYTES})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".pdf"):
            return httpx.Response(200, content=_SAMPLE_PDF_BYTES)
        if request.url.path.endswith(".zip"):
            return httpx.Response(200, content=zip_body)
        return httpx.Response(
            302, headers={"Location": "https://evil.example.com/leak.pdf"}
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        pdf_cand = PdfCandidate(
            issuer_id="FPT",
            fiscal_year=2025,
            fiscal_quarter=None,
            statement_scope="CONSOLIDATED",
            doc_type=DOC_TYPE_AUDITED_ANNUAL_CONSOLIDATED,
            title="BCTC HN KT 2025",
            raw_url="https://cafef1.mediacdn.vn/fpt_2025.pdf",
            source_url="https://cafef1.mediacdn.vn/fpt_2025.pdf",
            archive_format="pdf",
            row_index=0,
        )
        rec_pdf = download_curated_pdf(
            pdf_cand, output_root=tmp_path, cohort="watchlist_bluechip", client=client
        )
        assert rec_pdf.is_valid_pdf is True
        assert rec_pdf.extracted_from_zip is False

        zip_cand = PdfCandidate(
            issuer_id="HBC",
            fiscal_year=2023,
            fiscal_quarter=None,
            statement_scope="CONSOLIDATED",
            doc_type=DOC_TYPE_AUDITED_ANNUAL_CONSOLIDATED,
            title="BCTC HN KT 2023",
            raw_url="https://cafef1.mediacdn.vn/hbc_2023.zip",
            source_url="https://cafef1.mediacdn.vn/hbc_2023.zip",
            archive_format="zip",
            row_index=1,
        )
        rec_zip = download_curated_pdf(
            zip_cand, output_root=tmp_path, cohort="case_study", client=client
        )
        assert rec_zip.is_valid_pdf is True
        assert rec_zip.extracted_from_zip is True
        assert rec_zip.zip_member_name == "HBC_2023_HNKT.pdf"

        redirect_cand = PdfCandidate(
            issuer_id="POM",
            fiscal_year=2022,
            fiscal_quarter=None,
            statement_scope="CONSOLIDATED",
            doc_type=DOC_TYPE_AUDITED_ANNUAL_CONSOLIDATED,
            title="BCTC HN KT 2022",
            raw_url="https://cafef1.mediacdn.vn/redirect",
            source_url="https://cafef1.mediacdn.vn/redirect",
            archive_format="pdf",
            row_index=2,
        )
        with pytest.raises(ContractViolationError):
            download_curated_pdf(
                redirect_cand, output_root=tmp_path, cohort="case_study", client=client
            )

    manifest_path = tmp_path / "curated_pdfs_manifest.parquet"
    frame = write_manifest_parquet([rec_pdf, rec_zip], manifest_path)
    assert frame.schema == CURATED_PDFS_MANIFEST_SCHEMA
    assert frame.height == 2
    reloaded = pl.read_parquet(manifest_path)
    assert reloaded.height == 2
    assert verify_stored_manifest_files(reloaded, output_root=tmp_path) == {
        "verified_ok": 2,
        "invalid_or_missing": 0,
    }

    audit_md = render_curated_pdfs_audit_markdown(
        reloaded, case_study_tickers=["HBC"], output_root=tmp_path
    )
    assert "**1/1**" in audit_md
    assert "HBC" in audit_md
    assert "FPT" in audit_md


def test_build_manifest_dataframe_rejects_duplicate_or_separate_scope() -> None:
    digest = compute_sha256(_SAMPLE_PDF_BYTES)
    bad = CuratedPdfRecord(
        issuer_id="HBC",
        fiscal_year=2023,
        fiscal_quarter=None,
        statement_scope="SEPARATE",
        doc_type=DOC_TYPE_AUDITED_ANNUAL_CONSOLIDATED,
        source_url="https://cafef1.mediacdn.vn/hbc.pdf",
        file_size_bytes=len(_SAMPLE_PDF_BYTES),
        content_sha256=digest,
        local_rel_path=f"sha256/{digest[:2]}/{digest}.pdf",
        is_valid_pdf=True,
        cohort="case_study",
        extracted_from_zip=False,
        zip_member_name=None,
        source_title="BCTC riêng",
    )
    with pytest.raises(ContractViolationError):
        build_manifest_dataframe([bad])
