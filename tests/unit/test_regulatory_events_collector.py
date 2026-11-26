"""Unit tests for the ML-03a Ground-Truth Regulatory Events Collector (Port 1)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from finintel.exceptions import ContractViolationError, RateLimitOrCircuitOpenError
from finintel.ingestion.regulatory_events_collector import (
    CuratedRegulatoryEventsCollector,
    PoliteHostHttpClient,
    RawHeadlineCandidate,
    build_mandatory_delistings_table,
    build_regulatory_events_table,
    classify_regulatory_headline,
    collect_ticker_raw_candidates,
    compute_payload_sha256,
    extract_audit_opinion_records,
    parse_cafef_events_candidates,
    parse_vietcap_news_candidates,
    read_cached_raw_receipt,
    validate_regulatory_url,
    write_raw_receipt,
)
from finintel.ports.regulatory import (
    RegulatoryEventProvider,
    validate_regulatory_events,
)


def test_ssrf_url_validation_enforces_https_and_allowlist() -> None:
    ok_url = "https://iq.vietcap.com.vn/api/iq-insight-service/v1/news?ticker=HNG"
    assert validate_regulatory_url(ok_url) == ok_url
    assert (
        validate_regulatory_url(
            "https://cafef.vn/du-lieu/Ajax/Events_RelatedNews_New.aspx?symbol=POM"
        )
        == "https://cafef.vn/du-lieu/Ajax/Events_RelatedNews_New.aspx?symbol=POM"
    )

    for bad_url in (
        "http://iq.vietcap.com.vn/api/news",
        "https://user:pass@iq.vietcap.com.vn/api/news",
        "https://iq.vietcap.com.vn:8443/api/news",
        "https://evil.example.com/news",
        "https://127.0.0.1/news",
        "",
    ):
        with pytest.raises(ContractViolationError):
            validate_regulatory_url(bad_url)


def test_polite_client_retries_429_and_enforces_pacing() -> None:
    calls = 0
    sleeps: list[float] = []
    now = [100.0]

    def _clock() -> float:
        return now[0]

    def _sleep(sec: float) -> None:
        sleeps.append(sec)
        now[0] += sec

    def _transport(
        url: str, timeout: float, max_bytes: int, headers: Mapping[str, str]
    ) -> tuple[int, Mapping[str, str], bytes]:
        nonlocal calls
        _ = (url, timeout, max_bytes, headers)
        calls += 1
        if calls == 1:
            return 429, {"Retry-After": "1"}, b""
        return 200, {}, b'{"ok":true}'

    client = PoliteHostHttpClient(
        min_interval_seconds=0.6,
        max_retries=2,
        backoff_base_seconds=0.1,
        backoff_max_seconds=2.0,
        transport=_transport,
        sleeper=_sleep,
        clock=_clock,
    )
    data = client.get_bytes("https://iq.vietcap.com.vn/api/iq-insight-service/v1/news")
    assert data == b'{"ok":true}'
    assert calls == 2
    assert any(s >= 0.6 for s in sleeps)

    # Exhausting 429 retries raises RateLimitOrCircuitOpenError
    def _always_429(
        url: str, timeout: float, max_bytes: int, headers: Mapping[str, str]
    ) -> tuple[int, Mapping[str, str], bytes]:
        _ = (url, timeout, max_bytes, headers)
        return 429, {"Retry-After": "0.1"}, b""

    failing_client = PoliteHostHttpClient(
        min_interval_seconds=0.0,
        max_retries=1,
        backoff_base_seconds=0.01,
        backoff_max_seconds=0.2,
        transport=_always_429,
        sleeper=lambda _: None,
        clock=lambda: 0.0,
    )
    with pytest.raises(RateLimitOrCircuitOpenError):
        failing_client.get_bytes(
            "https://iq.vietcap.com.vn/api/iq-insight-service/v1/news"
        )


def test_headline_classification_handles_real_cases_and_excludes_noise() -> None:
    # Real distress decisions
    ctrl = classify_regulatory_headline(
        "POM: Quyết định đưa cổ phiếu vào diện kiểm soát",
        event_date=date(2023, 4, 11),
    )
    assert ctrl is not None
    assert ctrl.kind == "EVENT"
    assert ctrl.event_type == "CONTROL"

    delist = classify_regulatory_headline(
        "PVX: Ngày 09/06/2020, ngày hủy niêm yết cổ phiếu của Tổng CTCP Xây lắp Dầu khí Việt Nam trên sàn HNX",
        event_date=date(2020, 5, 12),
    )
    assert delist is not None
    assert delist.kind == "EVENT"
    assert delist.event_type == "FORCED_DELISTING"
    assert delist.effective_from == date(2020, 6, 9)
    assert delist.exchange_hint == "HNX"

    restr = classify_regulatory_headline(
        "QBS: Hạn chế giao dịch từ 23/05/2024",
        event_date=date(2024, 5, 22),
    )
    assert restr is not None
    assert restr.event_type == "TRADING_RESTRICTION"
    assert restr.effective_from == date(2024, 5, 23)

    audit = classify_regulatory_headline(
        "PVX giảm lỗ sau soát xét, kiểm toán từ chối đưa ra kết luận",
        event_date=date(2021, 9, 4),
    )
    assert audit is not None
    assert audit.event_type == "ADVERSE_OR_DISCLAIMER_AUDIT"

    # Real resolution including Vietnam exchange typo "điện cảnh báo"
    res_hng = classify_regulatory_headline(
        "HNG: Quyết định chuyển cổ phiếu từ diện kiểm soát sang điện cảnh báo",
        event_date=date(2020, 9, 4),
    )
    assert res_hng is not None
    assert res_hng.kind == "RESOLUTION"
    assert res_hng.event_type == "CONTROL"

    # Control warning that also mentions possible delisting stays CONTROL, not FORCED_DELISTING
    ttf_ctrl = classify_regulatory_headline(
        "TTF: Giữ nguyên diện kiểm soát, khả năng hủy niêm yết bắt buộc đối với cổ phiếu TTF",
        event_date=date(2021, 9, 14),
    )
    assert ttf_ctrl is not None
    assert ttf_ctrl.kind == "EVENT"
    assert ttf_ctrl.event_type == "CONTROL"

    # False positives strictly excluded
    for noise in (
        "QBS: Quy chế hoạt động của Ban kiểm soát",
        "SMC: Thông báo về việc hủy niêm yết trái phiếu tại VSDC",
        "HBC: Giải trình biện pháp và lộ trình khắc phục tình trạng chứng khoán bị kiểm soát đặc biệt",
        "POM: Lưu ý công ty về khả năng hủy niêm yết",
        "HOSE lưu ý cổ phiếu SMC bị huỷ niêm yết bắt buộc",
        "HOSE nhắc nhở 2 Công ty của bầu Đức để tránh rơi vào hủy niêm yết bắt buộc",
        "SMC: Cổ phiếu có khả năng bị hủy niêm yết",
        "HNG: Công văn về việc HNG có khả năng hủy bỏ niêm yết bắt buộc",
        "Cổ phiếu PTL sẽ bị huỷ niêm yết",
        "PVL: Phúc đáp thông báo số 379/TB-SGDHN ngày 27/02/2023 về việc cổ phiếu PVL có khả năng bị hủy bỏ niêm yết",
        "PVL: Nhận được Quyết định đình chỉ xét xử phúc thẩm vụ án kinh doanh thương mại",
        "QBS: Công văn của đơn vị kiểm toán về không còn ý kiến ngoại trừ trên BCTC kiểm toán 2022",
        "POM: Quyết định đưa cổ phiếu vào diện cảnh báo",
    ):
        assert (
            classify_regulatory_headline(noise, event_date=date(2024, 4, 1)) is None
        ), f"Expected exclusion for: {noise}"


def test_parse_vietcap_and_cafef_fixtures_and_build_intervals(tmp_path: Path) -> None:
    vci_payload = json.dumps(
        {
            "successful": True,
            "data": {
                "totalPages": 1,
                "content": [
                    {
                        "newsTitle": "POM: Quyết định đưa cổ phiếu vào diện kiểm soát",
                        "publicDate": "2023-04-11T13:26:00",
                        "newsShortContent": "",
                    },
                    {
                        "newsTitle": "POM: Quyết định hủy niêm yết cổ phiếu",
                        "publicDate": "2024-04-11T10:26:00",
                        "newsShortContent": "Hủy niêm yết trên HOSE kể từ ngày 10/05/2024",
                    },
                    {
                        "newsTitle": "POM: Quyết định đưa vào diện hạn chế giao dịch và Thông báo về trạng thái chứng khoán",
                        "publicDate": "2024-05-22T12:23:00",
                        "newsShortContent": "",
                    },
                ],
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    vci_sha = write_raw_receipt(tmp_path, "vietcap_iq/POM_news_p0.json", vci_payload)
    vci_url = "https://iq.vietcap.com.vn/api/iq-insight-service/v1/news?ticker=POM&fromDate=2018-01-01&toDate=2026-11-22&page=0&size=1000"
    vci_cands, total_pages = parse_vietcap_news_candidates(
        "POM", vci_payload, issuer_id="POM", source_url=vci_url, payload_sha256=vci_sha
    )
    assert total_pages == 1
    assert len(vci_cands) == 3

    cafef_html = (
        '<div id="divEvents"><ul class="News_Title_Link">'
        '<li><span class="timeTitle">04/04/2025 15:00</span>'
        '<a class="docnhanhTitle" href="/du-lieu/DLG-1/dlg-dua-ra-khoi-dien-kiem-soat.chn" '
        'title="DLG: Quyết định về việc đưa cổ phiếu ra khỏi diện kiểm soát">DLG</a></li>'
        '<li><span class="timeTitle">04/04/2024 10:00</span>'
        '<a class="docnhanhTitle" href="/du-lieu/DLG-0/dlg-kiem-soat.chn" '
        'title="DLG: Quyết định đưa cổ phiếu vào diện kiểm soát">DLG</a></li>'
        "</ul></div>"
    ).encode("utf-8")
    cafef_sha = write_raw_receipt(
        tmp_path, "cafef_events/DLG_events_p1.html", cafef_html
    )
    cafef_url = "https://cafef.vn/du-lieu/Ajax/Events_RelatedNews_New.aspx?symbol=DLG&PageIndex=1"
    cafef_cands = parse_cafef_events_candidates(
        "DLG",
        cafef_html,
        issuer_id="DLG",
        source_url=cafef_url,
        payload_sha256=cafef_sha,
    )
    assert len(cafef_cands) == 2

    # Verify UPCOM demotion still captures exchange_delisted_from == "HOSE" for POM
    # and DLG resolution closes CONTROL interval
    reg_df = build_regulatory_events_table(
        [*vci_cands, *cafef_cands],
        exchange_by_issuer={"POM": "UPCOM", "DLG": "HOSE"},
    )
    validated = validate_regulatory_events(reg_df)
    assert validated.height == 4

    pom_ctrl = validated.filter(
        (pl.col("issuer_id") == "POM") & (pl.col("event_type") == "CONTROL")
    ).row(0, named=True)
    assert pom_ctrl["event_stage"] == "FIRST_ENTRY"
    assert pom_ctrl["resolution_date"] == date(2024, 5, 10)

    pom_delist = validated.filter(
        (pl.col("issuer_id") == "POM") & (pl.col("event_type") == "FORCED_DELISTING")
    ).row(0, named=True)
    assert pom_delist["event_stage"] == "ESCALATION"
    assert pom_delist["effective_from"] == date(2024, 5, 10)
    assert pom_delist["exchange_delisted_from"] == "HOSE"

    dlg_ctrl = validated.filter(
        (pl.col("issuer_id") == "DLG") & (pl.col("event_type") == "CONTROL")
    ).row(0, named=True)
    assert dlg_ctrl["event_stage"] == "FIRST_ENTRY"
    assert dlg_ctrl["resolution_date"] == date(2025, 4, 4)

    delist_summary = build_mandatory_delistings_table(
        validated, exchange_by_issuer={"POM": "UPCOM", "DLG": "HOSE"}
    )
    assert delist_summary.height == 1
    assert delist_summary.row(0, named=True)["exchange_delisted_from"] == "HOSE"
    assert delist_summary.row(0, named=True)["current_exchange"] == "UPCOM"


def test_raw_cache_audit_opinions_and_provider_protocol(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    payload = json.dumps(
        {
            "successful": True,
            "data": {
                "totalPages": 1,
                "content": [
                    {
                        "newsTitle": "PVX giảm lỗ sau soát xét, kiểm toán từ chối đưa ra kết luận",
                        "publicDate": "2021-09-04T10:00:00",
                    },
                    {
                        "newsTitle": "HNG bị kiểm toán nghi ngờ khả năng hoạt động liên tục",
                        "publicDate": "2024-08-30T10:00:00",
                    },
                ],
            },
        }
    ).encode("utf-8")
    rel = "vietcap_iq/PVX_news_p0.json"
    sha = write_raw_receipt(raw_dir, rel, payload)
    cached = read_cached_raw_receipt(raw_dir, rel)
    assert cached is not None
    assert cached[1] == sha == compute_payload_sha256(payload)

    # Empty CafeF cached page so collect_ticker_raw_candidates uses 100% cache without HTTP
    write_raw_receipt(raw_dir, "cafef_events/PVX_events_p1.html", b"<div></div>")
    client = PoliteHostHttpClient(
        transport=lambda *_args: (_ for _ in ()).throw(
            AssertionError("Network should not be called when cache hits")
        )
    )
    cands, outcome = collect_ticker_raw_candidates(
        ticker="PVX",
        issuer_id="PVX",
        current_exchange="UPCOM",
        raw_dir=raw_dir,
        client=client,
        use_cache=True,
    )
    assert outcome.errors == ()
    assert len(cands) == 2

    opinions = extract_audit_opinion_records(cands)
    assert {o.opinion_category for o in opinions} == {"DISCLAIMER", "GOING_CONCERN"}

    reg_df = build_regulatory_events_table(cands, exchange_by_issuer={"PVX": "UPCOM"})
    ledger_path = tmp_path / "regulatory_events.parquet"
    reg_df.write_parquet(ledger_path)

    provider = CuratedRegulatoryEventsCollector(ledger_path)
    assert isinstance(provider, RegulatoryEventProvider)
    assert provider.fetch_events(as_of=date(2020, 12, 31)).is_empty()
    fetched = provider.fetch_events(tickers=["PVX"], as_of=date(2022, 1, 1))
    assert fetched.height == 1
    assert fetched.row(0, named=True)["event_type"] == "ADVERSE_OR_DISCLAIMER_AUDIT"


def test_reentry_after_resolution_starts_new_first_entry() -> None:
    sha = "a" * 64
    cands = [
        RawHeadlineCandidate(
            issuer_id="ABC",
            ticker="ABC",
            event_date=date(2020, 4, 1),
            raw_headline="ABC: Quyết định đưa cổ phiếu vào diện kiểm soát",
            source_provider="vietcap_iq",
            source_url="https://iq.vietcap.com.vn/api/iq-insight-service/v1/news?ticker=ABC",
            payload_sha256=sha,
        ),
        RawHeadlineCandidate(
            issuer_id="ABC",
            ticker="ABC",
            event_date=date(2021, 5, 1),
            raw_headline="ABC: Quyết định đưa cổ phiếu ra khỏi diện kiểm soát",
            source_provider="vietcap_iq",
            source_url="https://iq.vietcap.com.vn/api/iq-insight-service/v1/news?ticker=ABC",
            payload_sha256=sha,
        ),
        RawHeadlineCandidate(
            issuer_id="ABC",
            ticker="ABC",
            event_date=date(2023, 4, 1),
            raw_headline="ABC: Quyết định đưa cổ phiếu vào diện kiểm soát",
            source_provider="vietcap_iq",
            source_url="https://iq.vietcap.com.vn/api/iq-insight-service/v1/news?ticker=ABC",
            payload_sha256=sha,
        ),
    ]
    df = build_regulatory_events_table(cands, exchange_by_issuer={"ABC": "HOSE"})
    assert df.height == 2
    assert df.get_column("event_stage").to_list() == ["FIRST_ENTRY", "FIRST_ENTRY"]


def test_board_transfer_upgrade_is_not_forced_delisting_and_hnx_format_detected() -> (
    None
):
    sha = "b" * 64
    cands = [
        # DGC HNX -> HOSE board transfer (must NOT emit FORCED_DELISTING)
        RawHeadlineCandidate(
            issuer_id="DGC",
            ticker="DGC",
            event_date=date(2020, 7, 7),
            raw_headline="DGC: Quyết định về việc chấp thuận niêm yết cổ phiếu lần đầu",
            source_provider="vietcap_iq",
            source_url="https://iq.vietcap.com.vn/api/iq-insight-service/v1/news?ticker=DGC",
            payload_sha256=sha,
        ),
        RawHeadlineCandidate(
            issuer_id="DGC",
            ticker="DGC",
            event_date=date(2020, 7, 13),
            raw_headline="DGC: Ngày 20/07/2020, ngày hủy niêm yết cổ phiếu của CTCP Tập đoàn Hóa chất Đức Giang",
            source_provider="vietcap_iq",
            source_url="https://iq.vietcap.com.vn/api/iq-insight-service/v1/news?ticker=DGC",
            payload_sha256=sha,
        ),
        # PVV genuine HNX -> UPCOM forced delisting
        RawHeadlineCandidate(
            issuer_id="PVV",
            ticker="PVV",
            event_date=date(2019, 5, 3),
            raw_headline="PVV: Ngày 24/05/2019, ngày hủy niêm yết cổ phiếu của Công ty cổ phần Vinaconex 39",
            source_provider="vietcap_iq",
            source_url="https://iq.vietcap.com.vn/api/iq-insight-service/v1/news?ticker=PVV",
            payload_sha256=sha,
        ),
    ]
    df = build_regulatory_events_table(
        cands, exchange_by_issuer={"DGC": "HOSE", "PVV": "UPCOM"}
    )
    assert df.filter(pl.col("issuer_id") == "DGC").is_empty()
    pvv = df.filter(pl.col("issuer_id") == "PVV").row(0, named=True)
    assert pvv["event_type"] == "FORCED_DELISTING"
    assert pvv["exchange_delisted_from"] == "HNX"
    assert pvv["effective_from"] == date(2019, 5, 24)


def test_task05_uncapped_post_2026_dates_and_incremental_receipt_replay(
    tmp_path: Path,
) -> None:
    """TASK-05: Port 1 removes 2026 date caps and replays cached receipts + fresh p0..p1 chronologically."""
    raw_dir = tmp_path / "raw_reg"
    # Cached historical receipt p2 from 2024 (entry into CONTROL)
    p2_payload = json.dumps(
        {
            "successful": True,
            "data": {
                "totalPages": 3,
                "content": [
                    {
                        "newsTitle": "HBC: Quyết định đưa cổ phiếu vào diện kiểm soát",
                        "publicDate": "2024-04-10T09:00:00",
                    }
                ],
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    write_raw_receipt(raw_dir, "vietcap_iq/HBC_news_p2.json", p2_payload)
    write_raw_receipt(raw_dir, "cafef_events/HBC_events_p1.html", b"<div></div>")

    def _transport(
        url: str,
        timeout_seconds: float,
        max_bytes: int,
        headers: Mapping[str, str],
    ) -> tuple[int, Mapping[str, str], bytes]:
        _ = (timeout_seconds, max_bytes, headers)
        if "vietcap.com.vn" in url and "page=0" in url:
            body = {
                "successful": True,
                "data": {
                    "totalPages": 3,
                    "content": [
                        {
                            "newsTitle": "HBC: Quyết định đưa cổ phiếu ra khỏi diện kiểm soát",
                            "publicDate": "2027-03-15T14:00:00",
                        }
                    ],
                },
            }
            return 200, {}, json.dumps(body, ensure_ascii=False).encode("utf-8")
        if "vietcap.com.vn" in url and "page=1" in url:
            body = {"successful": True, "data": {"totalPages": 3, "content": []}}
            return 200, {}, json.dumps(body).encode("utf-8")
        if "cafef.vn" in url:
            return 200, {}, b"<div></div>"
        raise AssertionError(f"Unexpected URL (p2 should hit cache!): {url}")

    client = PoliteHostHttpClient(
        min_interval_seconds=0.0,
        transport=_transport,
        sleeper=lambda _s: None,
    )
    cands, outcome = collect_ticker_raw_candidates(
        ticker="HBC",
        issuer_id="VN_HBC",
        current_exchange="UPCOM",
        raw_dir=raw_dir,
        client=client,
        to_date=date(2027, 4, 1),
        use_cache=True,
        refresh_head_pages=2,
    )
    assert outcome.errors == ()
    reg_df = build_regulatory_events_table(
        cands, exchange_by_issuer={"VN_HBC": "UPCOM"}
    )
    assert reg_df.height == 1
    row = reg_df.row(0, named=True)
    assert row["event_type"] == "CONTROL"
    assert row["effective_from"] == date(2024, 4, 10)
    assert row["resolution_date"] == date(2027, 3, 15)


def test_task05_cross_provider_reconciliation_emits_info_code_and_metric() -> None:
    """TASK-05: reconcile_cross_provider_facts flags >0.5% anchor discrepancy in info_codes without deleting facts."""
    from finintel.normalization.reconciliation import (
        CROSS_PROVIDER_DISCREPANCY_CODE,
        get_reconciliation_metrics_registry,
        reconcile_cross_provider_facts,
    )
    from finintel.quality.gate import evaluate_quality_gate
    from finintel.storage.schemas import canonicalize_financial_facts

    def _fact_row(
        metric_id: str,
        val: float,
        provider: str,
        filing_id: str,
        stmt: str = "BALANCE_SHEET",
        vtype: str = "STOCK",
    ) -> dict[str, object]:
        return {
            "issuer_id": "VN_FPT",
            "filing_id": filing_id,
            "statement_type": stmt,
            "statement_scope": "CONSOLIDATED",
            "period_type": "ANNUAL",
            "fiscal_year": 2025,
            "fiscal_quarter": None,
            "fiscal_year_start_date": date(2025, 1, 1),
            "period_start_date": date(2025, 1, 1),
            "period_end_date": date(2025, 12, 31),
            "period_days": 365,
            "published_at": date(2026, 3, 20),
            "published_at_basis": "EXACT_FILING",
            "effective_available_at": date(2026, 3, 20),
            "is_exact_filing_date": True,
            "metric_id": metric_id,
            "value": val,
            "value_type": vtype,
            "currency": "VND",
            "unit_scale": 1,
            "audit_status": "AUDITED",
            "audit_opinion": "UNMODIFIED",
            "taxonomy_version": "VAS_TT200_2014",
            "derivation_method": "REPORTED_DIRECTLY",
            "revision_no": 1,
            "revision_reason": "ORIGINAL",
            "source_filing_ids": [filing_id],
            "source_provider": provider,
        }

    facts = canonicalize_financial_facts(
        pl.DataFrame(
            [
                _fact_row("BS_TOTAL_ASSETS", 100_000_000_000.0, "fhsc", "f_fhsc_bs"),
                _fact_row(
                    "BS_TOTAL_LIABILITIES", 40_000_000_000.0, "fhsc", "f_fhsc_bs"
                ),
                _fact_row("BS_TOTAL_EQUITY", 60_000_000_000.0, "fhsc", "f_fhsc_bs"),
                _fact_row(
                    "IS_NET_REVENUE",
                    80_000_000_000.0,
                    "fhsc",
                    "f_fhsc_is",
                    "INCOME_STATEMENT",
                    "FLOW",
                ),
                _fact_row(
                    "IS_NET_INCOME",
                    12_000_000_000.0,
                    "fhsc",
                    "f_fhsc_is",
                    "INCOME_STATEMENT",
                    "FLOW",
                ),
                _fact_row(
                    "IS_NET_INCOME_PARENT",
                    100_000_000.0,
                    "fhsc",
                    "f_fhsc_is",
                    "INCOME_STATEMENT",
                    "FLOW",
                ),
                _fact_row(
                    "CF_OPERATING_CASH_FLOW",
                    15_000_000_000.0,
                    "fhsc",
                    "f_fhsc_cf",
                    "CASH_FLOW",
                    "FLOW",
                ),
                _fact_row(
                    "CF_DEPRECIATION_AMORTIZATION",
                    5_000_000_000.0,
                    "fhsc",
                    "f_fhsc_cf",
                    "CASH_FLOW",
                    "FLOW",
                ),
                # Vietcap IQ: BS_TOTAL_ASSETS matches within 0.1% (no warn), IS_NET_REVENUE differs by 1.0% (> 0.5% -> WARN!),
                # IS_NET_INCOME_PARENT differs by 2M on small 100M base (< 0.5% of 1e9 materiality floor -> no warn!)
                _fact_row(
                    "BS_TOTAL_ASSETS", 100_050_000_000.0, "vietcap_iq", "f_vci_bs"
                ),
                _fact_row(
                    "IS_NET_REVENUE",
                    80_800_000_000.0,
                    "vietcap_iq",
                    "f_vci_is",
                    "INCOME_STATEMENT",
                    "FLOW",
                ),
                _fact_row(
                    "IS_NET_INCOME_PARENT",
                    102_000_000.0,
                    "vietcap_iq",
                    "f_vci_is",
                    "INCOME_STATEMENT",
                    "FLOW",
                ),
            ]
        )
    )
    reg = get_reconciliation_metrics_registry()
    before = (
        reg.get_sample_value(
            "finintel_provider_discrepancy_total", {"metric_id": "IS_NET_REVENUE"}
        )
        or 0.0
    )

    rec_res = reconcile_cross_provider_facts(facts)
    assert rec_res.discrepancies.height == 1
    assert rec_res.discrepancies["metric_id"].to_list() == ["IS_NET_REVENUE"]

    after = (
        reg.get_sample_value(
            "finintel_provider_discrepancy_total", {"metric_id": "IS_NET_REVENUE"}
        )
        or 0.0
    )
    assert after == before + 1.0

    qg_res = evaluate_quality_gate(
        rec_res.coalesced_facts,
        extra_info_signals=rec_res.info_signals,
    )
    assert qg_res.audit_quarantine.is_empty()
    assert qg_res.clean_facts.height == 8
    summary_row = qg_res.quality_summary.row(0, named=True)
    assert summary_row["quality_status"] == "PASS"
    assert CROSS_PROVIDER_DISCREPANCY_CODE in summary_row["info_codes"]
    assert CROSS_PROVIDER_DISCREPANCY_CODE not in summary_row["warning_codes"]

    # Direct evaluate_quality_gate(facts) on uncoalesced multi-provider facts preserves ALL rows (no premature deletion)
    # while emitting CROSS_PROVIDER_DISCREPANCY_WARN into quality_summary.info_codes!
    qg_auto = evaluate_quality_gate(facts)
    assert qg_auto.clean_facts.height == 11
    assert set(qg_auto.clean_facts["source_provider"].to_list()) == {
        "fhsc",
        "vietcap_iq",
    }
    assert (
        CROSS_PROVIDER_DISCREPANCY_CODE
        in qg_auto.quality_summary.row(0, named=True)["info_codes"]
    )


def test_task05_quality_gate_catches_intra_provider_dup_and_audit_tier_reconcile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TASK-05 regression: intra-provider dup PK caught, audit-tier mismatch ignored, /metrics exposed, offline=0 HTTP."""
    from prometheus_client import CollectorRegistry

    from finintel.normalization.reconciliation import (
        CROSS_PROVIDER_DISCREPANCY_CODE,
        reconcile_cross_provider_facts,
    )
    from finintel.quality.gate import evaluate_quality_gate
    from finintel.quality.rules import QualityGateConfig
    from finintel.serving.observability import generate_metrics_payload
    from finintel.storage.schemas import canonicalize_financial_facts

    def _row(
        metric_id: str,
        value: float,
        provider: str,
        filing_id: str,
        statement_type: str = "BALANCE_SHEET",
        value_type: str = "STOCK",
        audit_status: str = "UNAUDITED",
        revision_no: int = 1,
        published_at: date = date(2026, 4, 20),
    ) -> dict[str, object]:
        return {
            "issuer_id": "VN_FPT",
            "filing_id": filing_id,
            "statement_type": statement_type,
            "statement_scope": "CONSOLIDATED",
            "period_type": "QUARTER",
            "fiscal_year": 2026,
            "fiscal_quarter": 1,
            "fiscal_year_start_date": date(2026, 1, 1),
            "period_start_date": date(2026, 1, 1),
            "period_end_date": date(2026, 3, 31),
            "period_days": 90,
            "published_at": published_at,
            "published_at_basis": "EXACT_FILING",
            "effective_available_at": published_at,
            "is_exact_filing_date": True,
            "metric_id": metric_id,
            "value": value,
            "value_type": value_type,
            "currency": "VND",
            "unit_scale": 1,
            "audit_status": audit_status,
            "audit_opinion": "NOT_APPLICABLE",
            "taxonomy_version": "VAS_TT200_2014",
            "derivation_method": "REPORTED_DIRECTLY",
            "revision_no": revision_no,
            "revision_reason": "ORIGINAL" if revision_no == 1 else "RESTATED",
            "source_filing_ids": [filing_id],
            "source_provider": provider,
        }

    # 1. AUDITED fhsc vs UNAUDITED vietcap_iq must NOT emit false discrepancy warning.
    audit_mix = canonicalize_financial_facts(
        pl.DataFrame(
            [
                _row(
                    "BS_TOTAL_ASSETS",
                    115_000_000_000.0,
                    "fhsc",
                    "f_aud",
                    audit_status="AUDITED",
                ),
                _row(
                    "BS_TOTAL_ASSETS",
                    100_000_000_000.0,
                    "vietcap_iq",
                    "f_un",
                    audit_status="UNAUDITED",
                ),
            ]
        )
    )
    mix_res = reconcile_cross_provider_facts(audit_mix, record_metrics=False)
    assert mix_res.discrepancies.is_empty()
    assert mix_res.info_signals.is_empty()

    # 2. Same audit_status discrepancy exposes finintel_provider_discrepancy_total via generate_metrics_payload.
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    same_tier = canonicalize_financial_facts(
        pl.DataFrame(
            [
                _row(
                    "BS_TOTAL_ASSETS",
                    105_000_000_000.0,
                    "fhsc",
                    "f_a1",
                    audit_status="AUDITED",
                ),
                _row(
                    "BS_TOTAL_ASSETS",
                    100_000_000_000.0,
                    "vietcap_iq",
                    "f_a2",
                    audit_status="AUDITED",
                ),
            ]
        )
    )
    same_res = reconcile_cross_provider_facts(same_tier, record_metrics=True)
    assert same_res.discrepancies.height == 1
    assert CROSS_PROVIDER_DISCREPANCY_CODE in same_res.info_signals["code"].to_list()
    prom_text = generate_metrics_payload(CollectorRegistry()).decode("utf-8")
    assert (
        'finintel_provider_discrepancy_total{metric_id="BS_TOTAL_ASSETS"}' in prom_text
    )

    # 3. Duplicate primary key inside fhsc is still caught even when vietcap_iq is also present!
    dup_facts = pl.concat(
        [
            canonicalize_financial_facts(
                pl.DataFrame(
                    [_row("BS_TOTAL_ASSETS", 100_000_000_000.0, "fhsc", "f_dup_1")]
                )
            ),
            canonicalize_financial_facts(
                pl.DataFrame(
                    [
                        _row("BS_TOTAL_ASSETS", 101_000_000_000.0, "fhsc", "f_dup_1"),
                        _row(
                            "BS_TOTAL_ASSETS",
                            100_000_000_000.0,
                            "vietcap_iq",
                            "f_vci_1",
                        ),
                    ]
                )
            ),
        ]
    )
    qg_dup = evaluate_quality_gate(
        dup_facts, config=QualityGateConfig(max_critical_issuer_ratio=1.0)
    )
    assert "DUPLICATE_PRIMARY_KEY" in qg_dup.audit_quarantine["rule_code"].to_list()

    # 4. Offline mode (fetch_online=False) makes ZERO HTTP requests on cache miss.
    def _forbid_http(
        url: str, timeout_seconds: float, max_bytes: int, headers: Mapping[str, str]
    ) -> tuple[int, Mapping[str, str], bytes]:
        raise AssertionError(f"Offline Port 1 must not hit network: {url}")

    offline_client = PoliteHostHttpClient(
        min_interval_seconds=0.0, transport=_forbid_http, sleeper=lambda _s: None
    )
    cands, out = collect_ticker_raw_candidates(
        ticker="HPG",
        issuer_id="VN_HPG",
        current_exchange="HOSE",
        raw_dir=tmp_path / "partial_raw",
        client=offline_client,
        use_cache=True,
        fetch_online=False,
    )
    assert cands == []
    assert out.errors == ()
