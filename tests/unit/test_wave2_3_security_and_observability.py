"""Unit tests for Wave 2 & Wave 3A security, observability, freshness, and Port 2 fixes."""

from __future__ import annotations

import gzip
import socket
from pathlib import Path
from typing import Any

import httpx
import pytest

from finintel.exceptions import ContractViolationError
from finintel.ingestion.curated_pdf_service import (
    extract_pdf_candidates_from_raw_archive,
)
from finintel.ingestion.ssrf_guard import fetch_guarded_https_bytes
from finintel.rag.pdf_catalog import PdfCandidate, download_curated_pdf
from finintel.serving import observability as obs
from finintel.serving.freshness import collect_data_freshness


def _public_resolver(
    host: str,
    port: object,
    family: int = 0,
    type: int = 0,
    proto: int = 0,
    flags: int = 0,
) -> list[tuple[Any, ...]]:
    _ = (host, port, family, type, proto, flags)
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def test_ssrf_guard_rejects_unsolicited_compressed_content_encoding() -> None:
    compressed_body = gzip.compress(b"x" * 2048)

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers.get("accept-encoding") == "identity"
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            content=compressed_body,
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    try:
        with pytest.raises(ContractViolationError, match="Content-Encoding"):
            fetch_guarded_https_bytes(
                "https://cafef.vn/du-lieu/bctc.chn",
                client=client,
                resolver=_public_resolver,
            )
    finally:
        client.close()


def test_pdf_catalog_download_sends_identity_and_rejects_compressed_encoding(
    tmp_path: Path,
) -> None:
    cand = PdfCandidate(
        issuer_id="FPT",
        fiscal_year=2025,
        fiscal_quarter=None,
        statement_scope="CONSOLIDATED",
        doc_type="AUDITED_ANNUAL_CONSOLIDATED",
        title="BCTC hợp nhất kiểm toán năm 2025",
        raw_url="https://cafef1.mediacdn.vn/Images/Uploaded/DuLieuDownload/2025/FPT_2025_KTHN.pdf",
        source_url="https://cafef1.mediacdn.vn/Images/Uploaded/DuLieuDownload/2025/FPT_2025_KTHN.pdf",
        archive_format="pdf",
        row_index=0,
    )

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers.get("accept-encoding") == "identity"
        return httpx.Response(
            200,
            headers={"Content-Encoding": "br"},
            content=b"%PDF-1.4 fake",
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    try:
        with pytest.raises(ContractViolationError, match="Content-Encoding"):
            download_curated_pdf(
                cand, output_root=tmp_path, cohort="TEST", client=client
            )
    finally:
        client.close()


def test_observability_dagster_asset_entry_point_and_lazy_otel_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentelemetry import trace

    with obs.bind_telemetry_context(
        entry_point="dagster_asset", run_id="run_123"
    ) as ctx:
        assert ctx["entry_point"] == "dagster_asset"

    calls: list[bool] = []
    monkeypatch.setenv("FININTEL_OTEL_ENABLED", "1")
    monkeypatch.setattr(obs, "_OTEL_TRACING_CONFIGURED", False)
    monkeypatch.setattr(
        trace, "get_tracer_provider", lambda: trace.ProxyTracerProvider()
    )

    def fake_configure(**_kwargs: object) -> bool:
        calls.append(True)
        monkeypatch.setattr(obs, "_OTEL_TRACING_CONFIGURED", True)
        return True

    monkeypatch.setattr(obs, "configure_otel_tracing", fake_configure)
    with obs.trace_span("finintel.transform.incremental", {"finintel.run_id": "r1"}):
        pass
    assert len(calls) == 1


def test_fastapi_response_includes_security_headers(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from finintel.serving.api import create_app

    app = create_app(data_root=tmp_path)
    with TestClient(app) as client:
        resp = client.get("/health/live")
        assert resp.status_code == 200
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
        assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


def test_freshness_isolates_external_stores_and_prefers_local_ports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import polars as pl

    from finintel.serving import freshness

    fake_default_data = tmp_path / "data"
    parent_pdfs = fake_default_data / "curated_pdfs"
    parent_pdfs.mkdir(parents=True)
    pl.DataFrame({"issuer_id": ["FPT", "VNM"]}).write_parquet(
        parent_pdfs / "curated_pdfs_manifest.parquet"
    )
    monkeypatch.setattr(freshness, "DEFAULT_DATA_ROOT", fake_default_data)

    sibling_store = tmp_path / "isolated_clone"
    sibling_store.mkdir(parents=True)
    info = collect_data_freshness(
        snapshot_id="snap_20261126T000000Z_test",
        published_at="2026-11-26T00:00:00Z",
        data_root=sibling_store,
    )
    assert info["curated_pdfs_count"] == 0

    child_universe = fake_default_data / "universe_600_800"
    local_pdfs = child_universe / "curated_pdfs"
    local_pdfs.mkdir(parents=True)
    pl.DataFrame({"issuer_id": ["HPG"]}).write_parquet(
        local_pdfs / "curated_pdfs_manifest.parquet"
    )
    child_info = collect_data_freshness(
        snapshot_id="snap_20261126T000000Z_test",
        published_at="2026-11-26T00:00:00Z",
        data_root=child_universe,
    )
    assert child_info["curated_pdfs_count"] == 1


def test_curated_pdf_service_fast_paths_cafef_bctc_html_without_scanning_fhsc_json(
    tmp_path: Path,
) -> None:
    raw_root = tmp_path / "raw"
    prov_dir = raw_root / "provenance"
    prov_dir.mkdir(parents=True)
    # Corrupt/unreadable JSON receipt that would fail or waste I/O if prov_dir were scanned
    (prov_dir / "fhsc_0001.json").write_text("{}", encoding="utf-8")

    cafef_dir = tmp_path / "raw_disclosures" / "cafef_bctc"
    cafef_dir.mkdir(parents=True)
    (cafef_dir / "HPG.html").write_text(
        """
        <table>
          <tr>
            <td>Báo cáo tài chính hợp nhất năm 2025 (đã kiểm toán)</td>
            <td>CN/2025</td>
            <td><a href="https://cafef1.mediacdn.vn/Images/Uploaded/DuLieuDownload/BCTC/HPG_2025_HNKT.pdf">Tải về</a></td>
          </tr>
        </table>
        """,
        encoding="utf-8",
    )

    found = extract_pdf_candidates_from_raw_archive(raw_root, tickers=["HPG"])
    assert "HPG" in found
    assert found["HPG"][0].fiscal_year == 2025
