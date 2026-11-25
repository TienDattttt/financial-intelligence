"""Unit tests for Port 3 EOD market prices collector (UI-09a / B-06)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from finintel.exceptions import ContractViolationError
from finintel.ingestion.market_prices_collector import (
    MARKET_PRICE_ALLOWED_HOSTS,
    CuratedMarketPricesCollector,
    LiveMarketPricesCollector,
    MarketPriceBar,
    PoliteMarketPriceHttpClient,
    attach_shares_outstanding_market,
    build_market_prices_table,
    collect_ticker_market_prices,
    compute_payload_sha256,
    parse_cafef_price_history,
    parse_retry_after,
    parse_vndirect_marketcap_ratios,
    parse_vndirect_stock_prices,
    read_cached_raw_receipt,
    validate_market_price_url,
    write_raw_receipt,
)
from finintel.ports.market_prices import (
    MARKET_PRICES_SCHEMA,
    MarketPriceProvider,
    align_events_to_market_prices_pit,
    validate_market_prices,
)


def test_validate_market_price_url_enforces_ssrf_allowlist() -> None:
    assert "api-finfo.vndirect.com.vn" in MARKET_PRICE_ALLOWED_HOSTS
    assert "cafef.vn" in MARKET_PRICE_ALLOWED_HOSTS

    valid = (
        "https://api-finfo.vndirect.com.vn/v4/stock_prices"
        "?sort=date:asc&q=code:FPT~date:gte:2018-01-01&size=3000&page=1"
    )
    assert validate_market_price_url(valid) == valid

    for bad_url in (
        "",
        "http://api-finfo.vndirect.com.vn/v4/stock_prices",
        "https://user:pass@api-finfo.vndirect.com.vn/v4/stock_prices",
        "https://api-finfo.vndirect.com.vn:8443/v4/stock_prices",
        "https://169.254.169.254/latest/meta-data",
        "https://evil.example.com/stock_prices",
        "https://api-finfo.vndirect.com.vn/v4/stock_prices?q=code:FPT ",
    ):
        with pytest.raises(ContractViolationError):
            validate_market_price_url(bad_url)


def test_parse_vndirect_stock_prices_and_marketcap_scales_and_filters() -> None:
    prices_payload = json.dumps(
        {
            "currentPage": 1,
            "totalPages": 1,
            "data": [
                {
                    "code": "FPT",
                    "date": "2018-01-02",
                    "close": 59.8,
                    "adClose": 14.026,
                    "nmVolume": 5022160.0,
                    "ptVolume": 100000.0,
                },
                {
                    "code": "FPT",
                    "date": "2018-01-03",
                    "close": 0.0,
                    "adClose": 14.1,
                    "nmVolume": 100.0,
                    "ptVolume": 0.0,
                },
                {
                    "code": "HPG",
                    "date": "2018-01-02",
                    "close": 47.7,
                    "adClose": 8.068,
                    "nmVolume": 10.0,
                    "ptVolume": 0.0,
                },
            ],
        }
    ).encode("utf-8")
    mcap_payload = json.dumps(
        {
            "currentPage": 1,
            "totalPages": 1,
            "data": [
                {
                    "code": "FPT",
                    "ratioCode": "MARKETCAP",
                    "reportDate": "2018-01-02",
                    "value": 31746547994200.0,
                },
                {
                    "code": "FPT",
                    "ratioCode": "BETA",
                    "reportDate": "2018-01-02",
                    "value": 1.1,
                },
            ],
        }
    ).encode("utf-8")

    bars, total_pages, dropped = parse_vndirect_stock_prices(
        "FPT", prices_payload, issuer_id="VN_FPT"
    )
    assert total_pages == 1
    assert dropped == 1
    assert len(bars) == 1
    assert bars[0].issuer_id == "VN_FPT"
    assert bars[0].trade_date == date(2018, 1, 2)
    assert bars[0].close_raw == 59_800.0
    assert bars[0].close_adj == 14_026.0
    assert bars[0].volume == 5_122_160.0
    assert bars[0].close_raw != bars[0].close_adj

    mcap_map, mcap_pages = parse_vndirect_marketcap_ratios("FPT", mcap_payload)
    assert mcap_pages == 1
    assert mcap_map == {date(2018, 1, 2): 31_746_547_994_200.0}

    enriched = attach_shares_outstanding_market(bars, mcap_map)
    assert enriched[0].shares_outstanding_market == pytest.approx(530_878_729.0)


def test_parse_cafef_price_history_and_deduplication() -> None:
    cafef_payload = json.dumps(
        {
            "Data": {
                "TotalCount": 2,
                "Data": [
                    {
                        "Symbol": "FPT",
                        "Ngay": "28/09/2018",
                        "GiaDieuChinh": 13.11,
                        "GiaDongCua": 46.3,
                        "KhoiLuongKhopLenh": 559790,
                        "KLThoaThuan": 0,
                    },
                    {
                        "Symbol": "FPT",
                        "Ngay": "29/09/2018",
                        "GiaDieuChinh": 0.0,
                        "GiaDongCua": 46.3,
                        "KhoiLuongKhopLenh": 0,
                        "KLThoaThuan": 0,
                    },
                ],
            },
            "Success": True,
        }
    ).encode("utf-8")

    bars, total_count, dropped = parse_cafef_price_history(
        "FPT", cafef_payload, issuer_id="FPT"
    )
    assert total_count == 2
    assert dropped == 1
    assert len(bars) == 1
    assert bars[0].trade_date == date(2018, 9, 28)
    assert bars[0].close_raw == 46_300.0
    assert bars[0].close_adj == 13_110.0
    assert bars[0].volume == 559_790.0
    assert bars[0].source_provider == "cafef_eod"

    # Deduplicate keeping primary/shares-populated entry
    dup_bar = MarketPriceBar(
        issuer_id="FPT",
        ticker="FPT",
        trade_date=date(2018, 9, 28),
        close_raw=46_300.0,
        close_adj=13_111.0,
        volume=559_790.0,
        shares_outstanding_market=613_000_000.0,
        source_provider="vndirect_finfo",
    )
    table = build_market_prices_table([bars[0], dup_bar])
    assert table.height == 1
    assert table.schema == MARKET_PRICES_SCHEMA
    assert table["source_provider"].to_list() == ["vndirect_finfo"]
    assert table["shares_outstanding_market"].to_list() == [613_000_000.0]


def test_raw_receipt_path_traversal_and_cache(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    content = b'{"data":[]}'
    sha = write_raw_receipt(raw_dir, "vndirect/stock_prices/FPT_p1.json", content)
    assert sha == compute_payload_sha256(content)
    cached = read_cached_raw_receipt(raw_dir, "vndirect/stock_prices/FPT_p1.json")
    assert cached == (content, sha)

    with pytest.raises(ContractViolationError, match="escapes"):
        write_raw_receipt(raw_dir, "../../escape.json", content)


def test_live_collector_fallback_retry_and_pit_alignment(tmp_path: Path) -> None:
    assert parse_retry_after("2.5") == 2.5
    calls: list[str] = []
    sleeps: list[float] = []
    now_clock = [0.0]

    def _clock() -> float:
        return now_clock[0]

    def _sleep(sec: float) -> None:
        sleeps.append(sec)
        now_clock[0] += sec

    # First VNDirect call for HBC returns HTTP 503 once then succeeds on retry;
    # POM returns empty from VNDirect so collector falls back to CafeF.
    attempt_counts: dict[str, int] = {}

    def _transport(
        url: str,
        timeout_seconds: float,
        max_bytes: int,
        headers: Mapping[str, str],
    ) -> tuple[int, Mapping[str, str], bytes]:
        _ = (timeout_seconds, max_bytes, headers)
        calls.append(url)
        attempt_counts[url] = attempt_counts.get(url, 0) + 1
        if "api-finfo.vndirect.com.vn/v4/stock_prices" in url and "code:HBC" in url:
            if attempt_counts[url] == 1:
                return 503, {"Retry-After": "0.1"}, b""
            payload = {
                "currentPage": 1,
                "totalPages": 1,
                "data": [
                    {
                        "code": "HBC",
                        "date": "2023-04-10",
                        "close": 8.5,
                        "adClose": 8.5,
                        "nmVolume": 1000.0,
                        "ptVolume": 0.0,
                    },
                    {
                        "code": "HBC",
                        "date": "2023-04-17",
                        "close": 7.9,
                        "adClose": 7.9,
                        "nmVolume": 2000.0,
                        "ptVolume": 0.0,
                    },
                ],
            }
            return 200, {}, json.dumps(payload).encode("utf-8")
        if "api-finfo.vndirect.com.vn/v4/ratios" in url and "code:HBC" in url:
            payload = {
                "currentPage": 1,
                "totalPages": 1,
                "data": [
                    {
                        "code": "HBC",
                        "ratioCode": "MARKETCAP",
                        "reportDate": "2023-04-10",
                        "value": 2_330_000_000_000.0,
                    }
                ],
            }
            return 200, {}, json.dumps(payload).encode("utf-8")
        if "api-finfo.vndirect.com.vn/v4/stock_prices" in url and "code:POM" in url:
            return (
                200,
                {},
                json.dumps({"currentPage": 1, "totalPages": 1, "data": []}).encode(
                    "utf-8"
                ),
            )
        if "cafef.vn/du-lieu/Ajax/PageNew/DataHistory/PriceHistory.ashx" in url:
            payload = {
                "Data": {
                    "TotalCount": 1,
                    "Data": [
                        {
                            "Symbol": "POM",
                            "Ngay": "15/05/2023",
                            "GiaDieuChinh": 5.2,
                            "GiaDongCua": 5.2,
                            "KhoiLuongKhopLenh": 50000,
                            "KLThoaThuan": 0,
                        }
                    ],
                },
                "Success": True,
            }
            return 200, {}, json.dumps(payload).encode("utf-8")
        return 404, {}, b""

    client = PoliteMarketPriceHttpClient(
        min_interval_seconds=0.05,
        backoff_base_seconds=0.01,
        transport=_transport,
        sleeper=_sleep,
        clock=_clock,
    )
    raw_dir = tmp_path / "raw"
    collector = LiveMarketPricesCollector(
        raw_dir=raw_dir,
        client=client,
        issuer_map={"HBC": "HBC", "POM": "POM"},
        exchange_map={"HBC": "HOSE", "POM": "UPCOM"},
        use_cache=True,
        include_marketcap=True,
    )
    assert isinstance(collector, MarketPriceProvider)

    df = collector.fetch_prices(
        tickers=["HBC", "POM"],
        start_date=date(2023, 1, 1),
        end_date=date(2023, 12, 31),
    )
    validated = validate_market_prices(df)
    assert validated.height == 3
    assert set(validated["source_provider"].to_list()) == {
        "vndirect_finfo",
        "cafef_eod",
    }
    assert sleeps  # Retry + rate-limit sleeps occurred

    # Verify PIT forward alignment on weekend regulatory event (Sat 2023-04-15 -> Mon 2023-04-17)
    events = pl.DataFrame(
        {
            "issuer_id": ["HBC"],
            "published_at": [date(2023, 4, 15)],
        }
    )
    aligned = align_events_to_market_prices_pit(events, validated)
    assert aligned["trade_date"].to_list() == [date(2023, 4, 17)]
    assert aligned["close_raw"].to_list() == [7_900.0]

    # Verify cached replay performs zero new network requests
    calls_before_cache = len(calls)
    bars_cached, outcome_cached = collect_ticker_market_prices(
        ticker="HBC",
        issuer_id="HBC",
        current_exchange="HOSE",
        raw_dir=raw_dir,
        client=client,
        start_date=date(2023, 1, 1),
        end_date=date(2023, 12, 31),
        use_cache=True,
    )
    assert len(calls) == calls_before_cache
    assert outcome_cached.valid_bars_kept == 2
    assert len(bars_cached) == 2

    # Verify CuratedMarketPricesCollector reads & filters parquet cleanly
    pq_path = tmp_path / "market_prices.parquet"
    validated.write_parquet(pq_path)
    curated = CuratedMarketPricesCollector(pq_path)
    assert isinstance(curated, MarketPriceProvider)
    hbc_only = curated.fetch_prices(
        tickers=["HBC"], start_date=date(2023, 4, 12), end_date=date(2023, 4, 30)
    )
    assert hbc_only.height == 1
    assert hbc_only["trade_date"].to_list() == [date(2023, 4, 17)]


def test_upcom_vwap_denominator_and_hose_close_raw_preservation() -> None:
    """UPCOM MARKETCAP divides by nmValue/nmVolume (fallback average*1000); HOSE uses close_raw."""
    payload = json.dumps(
        {
            "currentPage": 1,
            "totalPages": 1,
            "data": [
                {
                    "code": "ACV",
                    "date": "2018-01-02",
                    "floor": "UPCOM",
                    "close": 119.0,
                    "average": 116.309,
                    "adClose": 70.718,
                    "nmVolume": 169087.0,
                    "nmValue": 1.9666494e10,
                    "ptVolume": 0.0,
                },
                {
                    "code": "ACV",
                    "date": "2018-01-03",
                    "floor": "UPCOM",
                    "close": 120.0,
                    "average": 115.0,
                    "adClose": 71.0,
                    "nmVolume": 0.0,
                    "nmValue": 0.0,
                    "ptVolume": 1000.0,
                },
            ],
        }
    ).encode("utf-8")
    bars, total_pages, dropped = parse_vndirect_stock_prices("ACV", payload)
    assert total_pages == 1
    assert dropped == 0
    assert len(bars) == 2

    vwap_expected = 1.9666494e10 / 169087.0
    mcap_map = {
        date(2018, 1, 2): 3_582_265_610.0 * vwap_expected,
        date(2018, 1, 3): 3_582_265_610.0 * 115_000.0,
    }
    enriched = attach_shares_outstanding_market(bars, mcap_map)
    assert enriched[0].shares_outstanding_market == pytest.approx(3_582_265_610.0)
    assert enriched[1].shares_outstanding_market == pytest.approx(3_582_265_610.0)

    hose_payload = json.dumps(
        {
            "currentPage": 1,
            "totalPages": 1,
            "data": [
                {
                    "code": "FPT",
                    "date": "2018-01-02",
                    "floor": "HOSE",
                    "close": 59.8,
                    "average": 59.0,
                    "adClose": 14.026,
                    "nmVolume": 5022160.0,
                    "nmValue": 2.9630744e11,
                }
            ],
        }
    ).encode("utf-8")
    hose_bars, _, _ = parse_vndirect_stock_prices("FPT", hose_payload)
    hose_enriched = attach_shares_outstanding_market(
        hose_bars, {date(2018, 1, 2): 31_746_547_994_200.0}
    )
    assert hose_enriched[0].shares_outstanding_market == pytest.approx(
        31_746_547_994_200.0 / 59_800.0
    )
