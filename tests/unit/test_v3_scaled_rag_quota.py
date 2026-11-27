"""Unit and concurrency tests for V3-19..V3-21: Fair-Use Quota, Circuit Breaker, and Scaled PDF RAG."""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from finintel.auth.dependencies import AuthenticatedUser, UserRole
from finintel.domain.compliance import ConsentPurpose

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_user(
    role: UserRole = UserRole.USER,
    *,
    consents: frozenset[ConsentPurpose] = frozenset(
        {ConsentPurpose.ACCOUNT_CORE, ConsentPurpose.CROSS_BORDER_LLM_RAG}
    ),
) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(),
        email="analyst@finintel.vn",
        role=role,
        consents=consents,
        display_name="Minh Analyst",
    )


def test_quota_migration_ddl_and_zero_payment_tables() -> None:
    """V3-19: Migration defines fair-use quota & ledger tables with zero payment/billing artifacts."""
    ddl_path = REPO_ROOT / "config" / "migrations" / "app_v3_003_fair_use_quota.sql"
    ddl = ddl_path.read_text(encoding="utf-8")
    for table in ("usage_quota_daily", "global_llm_budget_daily", "rag_usage_ledger"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
    lowered = ddl.lower()
    for forbidden in ("stripe", "vnpay", "payos", "subscription_price", "checkout"):
        assert forbidden not in lowered


def test_quota_lazy_imports_without_platform_or_rag_deps() -> None:
    """Core quota and batch_pipeline modules import cleanly when psycopg/redis/fastapi/pymupdf are absent."""
    script = (
        "import importlib, sys\n"
        "for pkg in ('psycopg', 'redis', 'fastapi', 'pydantic', 'pymupdf', 'fitz', 'google', 'google.genai', 'pgvector', 'sentence_transformers'):\n"
        "    sys.modules[pkg] = None\n"
        "for mod_name in (\n"
        "    'finintel.quota',\n"
        "    'finintel.quota.policies',\n"
        "    'finintel.quota.guard',\n"
        "    'finintel.quota.router',\n"
        "    'finintel.quota.schemas',\n"
        "    'finintel.rag.batch_pipeline',\n"
        "):\n"
        "    importlib.import_module(mod_name)\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_role_policies_and_hanoi_midnight_reset() -> None:
    """V3-19: USER (15 RAG/10 xlsx), RESEARCHER (50 RAG/30 xlsx), INTERNAL_OPS configurable, reset at 00:00 +07:00."""
    from finintel.quota.policies import (
        VIETNAM_TZ,
        current_quota_window,
        get_role_quota_policy,
    )

    user_pol = get_role_quota_policy(UserRole.USER)
    assert user_pol.rag_queries_per_day == 15
    assert user_pol.xlsx_exports_per_day == 10
    assert user_pol.unlimited is False

    res_pol = get_role_quota_policy(UserRole.RESEARCHER)
    assert res_pol.rag_queries_per_day == 50
    assert res_pol.xlsx_exports_per_day == 30
    assert res_pol.unlimited is False

    ops_pol = get_role_quota_policy(UserRole.INTERNAL_OPS)
    assert ops_pol.rag_queries_per_day >= 500
    assert ops_pol.unlimited is True

    # 2026-11-27 17:30 UTC is 2026-11-28 00:30 Asia/Ho_Chi_Minh -> resets at 2026-11-29 00:00+07:00
    now_utc = datetime(2026, 11, 27, 17, 30, tzinfo=UTC)
    quota_date, reset_at = current_quota_window(now_utc)
    assert quota_date.isoformat() == "2026-11-28"
    assert reset_at.tzinfo == VIETNAM_TZ
    assert reset_at.isoformat() == "2026-11-29T00:00:00+07:00"


def test_atomic_concurrent_quota_enforcement_20_threads_limit_15() -> None:
    """V3-19: 20 concurrent requests with limit 15 -> exactly 15 succeed, 5 raise HTTP 429 with reset_at."""
    from finintel.quota.guard import (
        InMemoryQuotaStore,
        QuotaExceededError,
        enforce_and_consume_quota,
    )

    store = InMemoryQuotaStore()
    user = _make_user(UserRole.USER)
    now = datetime(2026, 11, 27, 8, 0, tzinfo=UTC)

    def _attempt(_idx: int) -> tuple[bool, int | None, str | None]:
        try:
            receipt = enforce_and_consume_quota(
                user=user,
                meter="rag_query",
                store=store,
                now=now,
            )
            return True, receipt.remaining, None
        except QuotaExceededError as exc:
            return False, exc.status_code, exc.reset_at.isoformat()

    with ThreadPoolExecutor(max_workers=20) as pool:
        outcomes = list(pool.map(_attempt, range(20)))

    successes = [o for o in outcomes if o[0] is True]
    failures = [o for o in outcomes if o[0] is False]
    assert len(successes) == 15
    assert len(failures) == 5
    for _, code, reset_iso in failures:
        assert code == 429
        assert reset_iso is not None and reset_iso.endswith("T00:00:00+07:00")

    status = store.get_user_status(user=user, now=now)
    rag_status = next(m for m in status.meters if m.meter == "rag_query")
    assert rag_status.used == 15
    assert rag_status.remaining == 0


def test_refund_on_cache_hit_and_5xx_never_deducts_quota() -> None:
    """V3-19: Refunding reserved quota (cache HIT or 5xx error) preserves exact remaining count."""
    from finintel.quota.guard import (
        InMemoryQuotaStore,
        enforce_and_consume_quota,
        refund_quota,
    )

    store = InMemoryQuotaStore()
    user = _make_user(UserRole.USER)
    now = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)

    receipt = enforce_and_consume_quota(
        user=user, meter="rag_query", store=store, now=now
    )
    assert receipt.used == 1
    assert receipt.remaining == 14

    refund_quota(receipt, store=store)
    status = store.get_user_status(user=user, now=now)
    rag_status = next(m for m in status.meters if m.meter == "rag_query")
    assert rag_status.used == 0
    assert rag_status.remaining == 15


def test_global_gemini_circuit_breaker_trips_at_85_percent() -> None:
    """V3-19: Global Gemini Circuit Breaker trips at 85% daily free-tier cap returning 503 without deducting user quota."""
    from finintel.quota.guard import (
        CircuitBreakerOpenError,
        InMemoryQuotaStore,
        enforce_and_consume_quota,
    )
    from finintel.quota.policies import QuotaBudgetConfig

    cfg = QuotaBudgetConfig(
        global_daily_gemini_cap=100,
        circuit_breaker_ratio=0.85,
        daily_vision_page_cap=150,
    )
    store = InMemoryQuotaStore(budget_config=cfg)
    user = _make_user(UserRole.USER)
    now = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)

    # 84 calls (< 85%) -> allowed
    store.record_global_gemini_call(units=84, now=now)
    receipt = enforce_and_consume_quota(
        user=user, meter="rag_query", store=store, now=now
    )
    assert receipt.used == 1

    # Reach 85/100 (85%) -> circuit breaker opens -> 503, user quota untouched!
    store.record_global_gemini_call(units=1, now=now)
    with pytest.raises(CircuitBreakerOpenError) as exc_info:
        enforce_and_consume_quota(user=user, meter="rag_query", store=store, now=now)
    assert exc_info.value.status_code == 503
    assert exc_info.value.reopen_at.isoformat().endswith("T00:00:00+07:00")
    assert int(exc_info.value.headers["Retry-After"]) > 0

    status = store.get_user_status(user=user, now=now)
    assert status.circuit_breaker_open is True
    rag_status = next(m for m in status.meters if m.meter == "rag_query")
    assert rag_status.used == 1  # Not deducted on 503!

    # xlsx_export is NOT a Gemini call, so it still works during Gemini circuit breaker!
    xlsx_receipt = enforce_and_consume_quota(
        user=user, meter="xlsx_export", store=store, now=now
    )
    assert xlsx_receipt.used == 1


def test_quota_fastapi_endpoints_and_ops_rbac() -> None:
    """V3-19: GET /api/v3/quota/me and INTERNAL_OPS-only GET /api/v3/ops/quota/system-budget."""
    pytest.importorskip("fastapi")
    from collections.abc import Awaitable, Callable

    from fastapi import FastAPI, Request, Response
    from fastapi.testclient import TestClient

    from finintel.quota.guard import InMemoryQuotaStore, QuotaGuard
    from finintel.quota.router import create_quota_router

    store = InMemoryQuotaStore()
    guard = QuotaGuard(store)
    active_user: list[AuthenticatedUser | None] = [None]

    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if active_user[0] is not None:
            request.state._cached_auth_user = active_user[0]
            request.state._cached_auth_checked = True
        return await call_next(request)

    app.include_router(create_quota_router(get_quota_guard=lambda: guard))

    with TestClient(app) as client:
        # Anonymous guest sees unauthenticated quota view (200) but cannot hit ops endpoint (401)
        r_anon = client.get("/api/v3/quota/me")
        assert r_anon.status_code == 200
        assert r_anon.json()["authenticated"] is False

        r_ops_anon = client.get("/api/v3/ops/quota/system-budget")
        assert r_ops_anon.status_code == 401

        # Regular USER sees 15/10 limits, forbidden (403) on ops endpoint
        regular = _make_user(UserRole.USER)
        active_user[0] = regular
        guard.enforce_and_consume(user=regular, meter="rag_query", units=2)
        r_user = client.get("/api/v3/quota/me")
        assert r_user.status_code == 200
        body = r_user.json()
        assert body["authenticated"] is True
        assert body["role"] == "USER"
        assert body["cross_border_rag_consent"] is True
        assert body["timezone"] == "Asia/Ho_Chi_Minh"
        rag_m = next(m for m in body["meters"] if m["meter"] == "rag_query")
        assert rag_m["used"] == 2
        assert rag_m["limit"] == 15
        assert rag_m["remaining"] == 13

        assert client.get("/api/v3/ops/quota/system-budget").status_code == 403

        # INTERNAL_OPS accesses system budget (200)
        active_user[0] = _make_user(UserRole.INTERNAL_OPS)
        r_ops = client.get("/api/v3/ops/quota/system-budget")
        assert r_ops.status_code == 200
        ops_body = r_ops.json()
        assert ops_body["vision_pages_cap"] == 150
        assert ops_body["gemini_requests_cap"] == 1500
        assert ops_body["circuit_breaker_ratio"] == 0.85


def _create_real_pdf(
    path: Path,
    *,
    digital_pages: list[str],
    scanned_pages: int = 0,
) -> tuple[Path, str, int]:
    """Create a genuine PDF on disk using PyMuPDF (digital text + optional image-only pages)."""
    import hashlib
    import struct
    import zlib

    pymupdf = pytest.importorskip("pymupdf")

    def _minimal_png(width: int = 64, height: int = 64) -> bytes:
        def _chunk(tag: bytes, data: bytes) -> bytes:
            return (
                struct.pack("!I", len(data))
                + tag
                + data
                + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        ihdr = struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0)
        raw_scanlines = b"".join(
            b"\x00" + (b"\xff\xff\xff" * width) for _ in range(height)
        )
        return (
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw_scanlines))
            + _chunk(b"IEND", b"")
        )

    doc = pymupdf.open()
    for text in digital_pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
    if scanned_pages > 0:
        png_bytes = _minimal_png()
        for _ in range(scanned_pages):
            page = doc.new_page()
            page.insert_image(pymupdf.Rect(50, 50, 300, 300), stream=png_bytes)

    pdf_bytes = doc.tobytes()
    doc.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes)
    return path, hashlib.sha256(pdf_bytes).hexdigest(), len(pdf_bytes)


def _make_pdf_record(
    ticker: str,
    *,
    fiscal_year: int = 2024,
    sha256: str = "",
    size_bytes: int = 1024,
) -> Any:
    from finintel.rag.pdf_catalog import CuratedPdfRecord

    return CuratedPdfRecord(
        issuer_id=ticker,
        fiscal_year=fiscal_year,
        fiscal_quarter=None,
        statement_scope="CONSOLIDATED",
        doc_type="AUDITED_ANNUAL_CONSOLIDATED_FS",
        source_url=f"https://cafef1.mediacdn.vn/{ticker.lower()}_{fiscal_year}.pdf",
        file_size_bytes=size_bytes,
        content_sha256=sha256 or ("0" * 64),
        local_rel_path=f"sha256/{sha256[:2] or '00'}/{sha256 or ('0' * 64)}.pdf",
        is_valid_pdf=True,
        cohort="v3_scaled",
        extracted_from_zip=False,
        zip_member_name=None,
        source_title=f"BCTC hop nhat {ticker} {fiscal_year}",
    )


def test_batch_priority_queue_covers_r4_r5_red_flags_and_top_mcap(
    tmp_path: Path,
) -> None:
    """V3-20: Priority queue covers 100% R4/R5, 100% red-flag issuers, and Top N market cap."""
    from finintel.rag.batch_pipeline import PdfBatchCandidate, build_priority_pdf_queue

    dummy_pdf = tmp_path / "dummy.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4\n")

    candidates = [
        PdfBatchCandidate(
            record=_make_pdf_record("LOW", sha256="1" * 64),
            pdf_path=dummy_pdf,
            risk_tier="R1",
            red_flag_count=0,
            market_cap_vnd=100.0,
        ),
        PdfBatchCandidate(
            record=_make_pdf_record("MEGA", sha256="2" * 64),
            pdf_path=dummy_pdf,
            risk_tier="R1",
            red_flag_count=0,
            market_cap_vnd=50_000.0,
        ),
        PdfBatchCandidate(
            record=_make_pdf_record("FLAG", sha256="3" * 64),
            pdf_path=dummy_pdf,
            risk_tier="R2",
            red_flag_count=3,
            market_cap_vnd=200.0,
        ),
        PdfBatchCandidate(
            record=_make_pdf_record("RISK4", sha256="4" * 64),
            pdf_path=dummy_pdf,
            risk_tier="R4",
            red_flag_count=1,
            market_cap_vnd=500.0,
        ),
        PdfBatchCandidate(
            record=_make_pdf_record("RISK5", sha256="5" * 64),
            pdf_path=dummy_pdf,
            risk_tier="R5",
            red_flag_count=0,
            market_cap_vnd=50.0,
        ),
    ]

    jobs = build_priority_pdf_queue(candidates, top_market_cap_n=1)
    tickers = [j.candidate.record.issuer_id for j in jobs]
    assert tickers == ["RISK5", "RISK4", "FLAG", "MEGA"]
    assert "LOW" not in tickers
    assert jobs[0].priority_tier == 1
    assert jobs[2].priority_tier == 2
    assert jobs[3].priority_tier == 3


def test_batch_pipeline_text_first_zero_vision_calls_and_sha256_dedup(
    tmp_path: Path,
) -> None:
    """V3-20: Clean text pages make 0 Vision calls and duplicate content_sha256 PDFs are skipped 100%."""
    from finintel.quota.guard import InMemoryQuotaStore
    from finintel.rag.batch_pipeline import (
        PdfBatchCandidate,
        run_text_first_batch_pipeline,
    )

    clean_text = (
        "BAO CAO TAI CHINH HOP NHAT NAM 2024. Don vi tinh: Trieu dong. "
        "Tong tai san tai ngay 31/12/2024 dat 150.230 trieu dong, no phai tra ngan han "
        "la 42.100 trieu dong va doanh thu thuan dat 85.400 trieu dong theo chuan VAS."
    )
    pdf_a, sha_a, size_a = _create_real_pdf(
        tmp_path / "fpt_2024.pdf",
        digital_pages=[clean_text + " Trang 1.", clean_text + " Trang 2."],
    )
    pdf_dup = tmp_path / "fpt_2024_dup.pdf"
    pdf_dup.write_bytes(pdf_a.read_bytes())
    sha_dup, size_dup = sha_a, size_a

    vision_called = 0

    def _forbidden_vision(
        _tasks: list[tuple[int, bytes, str | None]],
    ) -> dict[int, str]:
        nonlocal vision_called
        vision_called += 1
        raise AssertionError(
            "Vision OCR must not be called on clean digital text pages"
        )

    class _FakeStore:
        def __init__(self) -> None:
            self.indexed: dict[str, int] = {}

        def has_indexed_pdf(self, pdf_sha256: str) -> bool:
            return pdf_sha256 in self.indexed

        def upsert_document_and_chunks(
            self, *, summary: Any, segments: Any, embeddings: Any
        ) -> int:
            _ = embeddings
            self.indexed[summary.pdf_sha256] = len(segments)
            return len(segments)

    class _FakeEmbedder:
        def embed_texts(
            self, texts: Any, **_kwargs: Any
        ) -> tuple[list[list[float]], None]:
            return [[0.1] * 1024 for _ in texts], None

    quota_store = InMemoryQuotaStore()
    rag_store = _FakeStore()
    candidates = [
        PdfBatchCandidate(
            record=_make_pdf_record("FPT", sha256=sha_a, size_bytes=size_a),
            pdf_path=pdf_a,
            risk_tier="R1",
            market_cap_vnd=100_000.0,
        ),
        PdfBatchCandidate(
            record=_make_pdf_record("FPTDUP", sha256=sha_dup, size_bytes=size_dup),
            pdf_path=pdf_dup,
            risk_tier="R1",
            market_cap_vnd=90_000.0,
        ),
    ]

    report = run_text_first_batch_pipeline(
        candidates,
        quota_store=quota_store,
        store=rag_store,
        embedder=_FakeEmbedder(),
        vision_ocr_fn=_forbidden_vision,
    )
    assert vision_called == 0
    assert report.status == "COMPLETED"
    assert report.indexed_documents == 1
    assert report.skipped_dedup_documents == 1
    assert report.total_digital_pages == 2
    assert report.total_vision_pages == 0
    assert report.total_vision_calls == 0
    assert report.estimated_cost_usd == 0.0
    assert len(quota_store.list_rag_usage()) == 2


def test_batch_pipeline_graceful_stop_at_vision_cap_finishes_clean_text(
    tmp_path: Path,
) -> None:
    """V3-20: Hitting daily_vision_page_cap stops Vision gracefully in DEFERRED_VISION_CAP while indexing clean text."""
    from finintel.quota.guard import InMemoryQuotaStore
    from finintel.quota.policies import QuotaBudgetConfig
    from finintel.rag.batch_pipeline import (
        PdfBatchCandidate,
        run_text_first_batch_pipeline,
    )

    clean_text = (
        "THUYET MINH BAO CAO TAI CHINH HOP NHAT NAM 2024. Don vi tinh: VND. "
        "Khoan phai thu khach hang ngan han va du phong no kho doi duoc trinh bay day du tai muc V.3."
    )
    pdf1, sha1, size1 = _create_real_pdf(
        tmp_path / "hbc_2024.pdf",
        digital_pages=[clean_text + " Trang 1."],
        scanned_pages=3,
    )
    pdf2, sha2, size2 = _create_real_pdf(
        tmp_path / "nov_2024.pdf",
        digital_pages=[clean_text + " Trang A.", clean_text + " Trang B."],
        scanned_pages=2,
    )

    vision_pages_seen: list[int] = []

    def _stub_vision(tasks: list[tuple[int, bytes, str | None]]) -> dict[int, str]:
        out: dict[int, str] = {}
        for pno, _png, _reason in tasks:
            vision_pages_seen.append(pno)
            out[pno] = (
                f"Noi dung OCR trang {pno}: du phong phai thu kho doi 120 ty dong."
            )
        return out

    class _FakeStore:
        def __init__(self) -> None:
            self.docs: dict[str, int] = {}

        def has_indexed_pdf(self, pdf_sha256: str) -> bool:
            _ = pdf_sha256
            return False

        def upsert_document_and_chunks(
            self, *, summary: Any, segments: Any, embeddings: Any
        ) -> int:
            _ = embeddings
            self.docs[summary.ticker] = len(segments)
            return len(segments)

    class _FakeEmbedder:
        def embed_texts(
            self, texts: Any, **_kwargs: Any
        ) -> tuple[list[list[float]], None]:
            return [[0.1] * 1024 for _ in texts], None

    quota_store = InMemoryQuotaStore(
        budget_config=QuotaBudgetConfig(daily_vision_page_cap=2)
    )
    rag_store = _FakeStore()
    candidates = [
        PdfBatchCandidate(
            record=_make_pdf_record("HBC", sha256=sha1, size_bytes=size1),
            pdf_path=pdf1,
            risk_tier="R5",
            red_flag_count=4,
        ),
        PdfBatchCandidate(
            record=_make_pdf_record("NVL", sha256=sha2, size_bytes=size2),
            pdf_path=pdf2,
            risk_tier="R4",
            red_flag_count=2,
        ),
    ]

    report = run_text_first_batch_pipeline(
        candidates,
        quota_store=quota_store,
        store=rag_store,
        embedder=_FakeEmbedder(),
        vision_ocr_fn=_stub_vision,
        daily_vision_page_cap=2,
    )

    assert report.status == "DEFERRED_VISION_CAP"
    assert report.deferred_documents == 2
    assert report.total_vision_pages == 2
    assert len(vision_pages_seen) == 2
    assert report.total_deferred_vision_pages == 3
    assert report.vision_pages_remaining == 0
    # All 3 clean digital text pages across both PDFs MUST still finish indexing!
    assert report.total_digital_pages == 3
    assert rag_store.docs["HBC"] >= 3
    assert rag_store.docs["NVL"] >= 2
    assert any(
        entry.operation == "batch_deferred_cap"
        for entry in quota_store.list_rag_usage()
    )
