"""Collect real EOD market prices (2018-01-01 -> present) for 375 corporate tickers (UI-09a / B-06)."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from finintel.ingestion.market_prices_collector import (  # noqa: E402
    DEFAULT_CAFEF_PRICE_HISTORY_URL_TEMPLATE,
    DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
    DEFAULT_START_DATE,
    VN_TZ,
    MarketPriceBar,
    PoliteMarketPriceHttpClient,
    TickerPriceCollectionOutcome,
    build_market_prices_table,
    collect_ticker_market_prices,
    parse_cafef_price_history,
    read_cached_raw_receipt,
    write_raw_receipt,
)
from finintel.ports.market_prices import (  # noqa: E402
    align_events_to_market_prices_pit,
    validate_market_prices,
)

DEFAULT_MAIN_REPO = Path("d:/KiCuoiCung/DATN/financial-intelligence")
DEFAULT_ISSUERS_PARQUET = (
    DEFAULT_MAIN_REPO / "data" / "go_live_nfr_run" / "curated" / "issuers.parquet"
)
DEFAULT_REGULATORY_PARQUET = (
    DEFAULT_MAIN_REPO / "data" / "regulatory_ledger" / "regulatory_events.parquet"
)
DEFAULT_OUTPUT_DIR = DEFAULT_MAIN_REPO / "data" / "market_prices"
DEFAULT_AUDIT_REPORT = (
    DEFAULT_MAIN_REPO / "docs" / "refactor" / "market_prices_audit_375.md"
)

SPLIT_VERIFICATION_TICKERS: tuple[str, ...] = ("FPT", "HPG", "MWG")
CASE_STUDY_TICKERS: tuple[str, ...] = (
    "HBC",
    "POM",
    "HNG",
    "DLG",
    "SMC",
    "QBS",
    "PVX",
    "PVL",
    "HVN",
)


def _write_parquet_atomic(frame: pl.DataFrame, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    frame.write_parquet(tmp_path)
    tmp_path.replace(target)


def _fetch_cafef_spot_checks(
    *,
    tickers: tuple[str, ...],
    prices_df: pl.DataFrame,
    raw_dir: Path,
    client: PoliteMarketPriceHttpClient,
    start_date: date,
    end_date: date,
    use_cache: bool,
) -> list[dict[str, object]]:
    """Cross-verify split stocks on early 2018 sessions against CafeF PriceHistory."""
    results: list[dict[str, object]] = []
    spot_end = min(end_date, date(2018, 12, 31))
    start_mdy = start_date.strftime("%m/%d/%Y")
    end_mdy = spot_end.strftime("%m/%d/%Y")

    for ticker in tickers:
        url = DEFAULT_CAFEF_PRICE_HISTORY_URL_TEMPLATE.format(
            ticker=ticker,
            start_date_mdy=start_mdy,
            end_date_mdy=end_mdy,
            page=1,
            page_size=200,
        )
        rel_path = f"cafef/spot_check/{ticker}_2018_p1.json"
        cached = read_cached_raw_receipt(raw_dir, rel_path) if use_cache else None
        try:
            if cached is not None:
                content, _ = cached
            else:
                content = client.get_bytes(url)
                write_raw_receipt(raw_dir, rel_path, content)
            cafef_bars, _, _ = parse_cafef_price_history(
                ticker,
                content,
                issuer_id=ticker,
                start_date=start_date,
                end_date=spot_end,
            )
            cafef_map = {b.trade_date: b for b in cafef_bars}
            tk_df = prices_df.filter(pl.col("ticker") == ticker).sort("trade_date")
            matched_row: dict[str, object] | None = None
            for row in tk_df.to_dicts():
                td = row["trade_date"]
                if isinstance(td, date) and td in cafef_map:
                    cb = cafef_map[td]
                    matched_row = {
                        "ticker": ticker,
                        "trade_date": td.isoformat(),
                        "vndirect_close_raw": float(row["close_raw"]),
                        "cafef_close_raw": float(cb.close_raw),
                        "vndirect_close_adj": float(row["close_adj"]),
                        "cafef_close_adj": float(cb.close_adj),
                        "raw_diff_vnd": abs(
                            float(row["close_raw"]) - float(cb.close_raw)
                        ),
                        "adj_diff_vnd": abs(
                            float(row["close_adj"]) - float(cb.close_adj)
                        ),
                        "status": "MATCHED",
                    }
                    break
            if matched_row is None:
                matched_row = {
                    "ticker": ticker,
                    "trade_date": None,
                    "vndirect_close_raw": None,
                    "cafef_close_raw": None,
                    "vndirect_close_adj": None,
                    "cafef_close_adj": None,
                    "raw_diff_vnd": None,
                    "adj_diff_vnd": None,
                    "status": f"NO_OVERLAP (cafef_bars={len(cafef_bars)})",
                }
            results.append(matched_row)
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "ticker": ticker,
                    "trade_date": None,
                    "vndirect_close_raw": None,
                    "cafef_close_raw": None,
                    "vndirect_close_adj": None,
                    "cafef_close_adj": None,
                    "raw_diff_vnd": None,
                    "adj_diff_vnd": None,
                    "status": f"ERROR: {exc}",
                }
            )
    return results


def render_market_prices_audit_report(
    *,
    issuers_df: pl.DataFrame,
    outcomes: list[TickerPriceCollectionOutcome],
    prices_df: pl.DataFrame,
    reg_events_df: pl.DataFrame | None,
    cafef_spot_checks: list[dict[str, object]],
    start_date: date,
    end_date: date,
    generated_at: datetime,
) -> str:
    """Render empirical audit report ``market_prices_audit_375.md`` from verified data."""
    total_tickers = len(outcomes)
    covered_tickers = sum(1 for o in outcomes if o.valid_bars_kept > 0)
    missing_outcomes = [o for o in outcomes if o.valid_bars_kept == 0]
    error_outcomes = [o for o in outcomes if o.errors]
    dropped_outcomes = [o for o in outcomes if o.dropped_non_positive_bars > 0]
    total_dropped_bars = sum(o.dropped_non_positive_bars for o in outcomes)
    vndirect_count = sum(1 for o in outcomes if o.primary_provider == "vndirect_finfo")
    cafef_count = sum(1 for o in outcomes if o.primary_provider == "cafef_eod")

    total_rows = prices_df.height
    shares_non_null = (
        prices_df.filter(pl.col("shares_outstanding_market").is_not_null()).height
        if total_rows > 0
        else 0
    )
    raw_min = prices_df["trade_date"].min() if total_rows > 0 else None
    raw_max = prices_df["trade_date"].max() if total_rows > 0 else None
    min_dt = raw_min.isoformat() if isinstance(raw_min, date) else str(raw_min)
    max_dt = raw_max.isoformat() if isinstance(raw_max, date) else str(raw_max)

    lines: list[str] = [
        "# Market Prices Audit Report — Port 3 EOD Dataset (`UI-09a` / `B-06`, 375 Corporate Tickers)",
        "",
        f"- **Generated (UTC):** `{generated_at.strftime('%Y-%m-%dT%H:%M:%SZ')}`",
        "- **Task:** `UI-09a` / `B-06` (`docs/refactor/03_FRONTEND_UX_AND_DIFFERENTIATION_PLAN.md`)",
        f"- **Requested Date Window:** `{start_date.isoformat()}` → `{end_date.isoformat()}`",
        f"- **Observed Trade Date Range:** `{min_dt}` → `{max_dt}`",
        f"- **Universe Size (`issuers.parquet`):** `{total_tickers}` corporate issuers",
        f"- **Tickers with EOD Price Data:** `{covered_tickers}/{total_tickers}` (`{100.0 * covered_tickers / max(total_tickers, 1):.2f}%`)",
        f"- **Primary Provider (`vndirect_finfo`):** `{vndirect_count}` tickers | **Fallback (`cafef_eod`):** `{cafef_count}` tickers | **Zero Data:** `{len(missing_outcomes)}` tickers",
        f"- **Total Validated EOD Rows:** `{total_rows:,}`",
        f"- **PIT `shares_outstanding_market` Coverage:** `{shares_non_null:,}/{total_rows:,}` (`{100.0 * shares_non_null / max(total_rows, 1):.2f}%`)",
        f"- **Upstream Non-Positive/Null Price Bars Filtered:** `{total_dropped_bars:,}` (across `{len(dropped_outcomes)}` tickers)",
        "- **Schema Validation (`validate_market_prices()`):** `PASS`",
        "",
        "---",
        "",
        "## 1. Exchange Coverage Breakdown",
        "",
        "| Exchange | Universe Tickers | Covered Tickers | Missing Tickers | Total EOD Rows | Earliest Date | Latest Date |",
        "|---|---:|---:|---:|---:|---|---|",
    ]

    ex_joined = prices_df.join(
        issuers_df.select(["issuer_id", "exchange"]), on="issuer_id", how="left"
    )
    for ex in ("HOSE", "HNX", "UPCOM", "DELISTED"):
        u_cnt = issuers_df.filter(pl.col("exchange") == ex).height
        if u_cnt == 0:
            continue
        sub_out = [o for o in outcomes if o.current_exchange == ex]
        cov_cnt = sum(1 for o in sub_out if o.valid_bars_kept > 0)
        miss_cnt = sum(1 for o in sub_out if o.valid_bars_kept == 0)
        sub_px = ex_joined.filter(pl.col("exchange") == ex)
        ex_rows = sub_px.height
        raw_ex_min = sub_px["trade_date"].min() if ex_rows > 0 else None
        raw_ex_max = sub_px["trade_date"].max() if ex_rows > 0 else None
        ex_min = (
            raw_ex_min.isoformat() if isinstance(raw_ex_min, date) else str(raw_ex_min)
        )
        ex_max = (
            raw_ex_max.isoformat() if isinstance(raw_ex_max, date) else str(raw_ex_max)
        )
        lines.append(
            f"| `{ex}` | {u_cnt} | {cov_cnt} | {miss_cnt} | {ex_rows:,} | `{ex_min}` | `{ex_max}` |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 2. Missing, Short-History, or Filtered-Bar Tickers Audit",
            "",
            f"- **Zero-row tickers:** `{len(missing_outcomes)}`",
            f"- **Tickers with partial fetch errors (`errors` non-empty):** `{len(error_outcomes)}`",
        ]
    )
    if missing_outcomes:
        lines.extend(
            [
                "",
                "### Missing / Delisted Upstream Tickers",
                "",
                "| Ticker | `issuer_id` | Exchange | `dropped_non_positive_bars` | Errors / Reason |",
                "|---|---|---|---:|---|",
            ]
        )
        for o in missing_outcomes:
            err = (
                "; ".join(o.errors).replace("|", "/")
                if o.errors
                else "Upstream returned 0 valid EOD bars"
            )
            lines.append(
                f"| `{o.ticker}` | `{o.issuer_id}` | `{o.current_exchange}` | {o.dropped_non_positive_bars} | {err} |"
            )

    short_outcomes = sorted(
        [o for o in outcomes if 0 < o.valid_bars_kept < 1500],
        key=lambda x: x.valid_bars_kept,
    )
    lines.append(
        f"- **Tickers with `< 1,500` sessions (e.g. post-2018 IPO/listing or trading halts):** `{len(short_outcomes)}`"
    )
    if short_outcomes:
        lines.extend(
            [
                "",
                "| Ticker | Exchange | Valid Bars | Min Date | Max Date | Dropped Non-Positive | Provider |",
                "|---|---|---:|---|---|---:|---|",
            ]
        )
        for o in short_outcomes[:50]:
            lines.append(
                f"| `{o.ticker}` | `{o.current_exchange}` | {o.valid_bars_kept:,} | `{o.min_trade_date}` | `{o.max_trade_date}` | {o.dropped_non_positive_bars} | `{o.primary_provider}` |"
            )

    if dropped_outcomes:
        lines.extend(
            [
                "",
                "### Tickers with Upstream Non-Positive (`close <= 0` or `adClose <= 0`) Bars Filtered",
                "",
                "| Ticker | Exchange | Valid Bars Kept | Dropped Non-Positive Bars |",
                "|---|---|---:|---:|",
            ]
        )
        for o in sorted(
            dropped_outcomes, key=lambda x: x.dropped_non_positive_bars, reverse=True
        )[:30]:
            lines.append(
                f"| `{o.ticker}` | `{o.current_exchange}` | {o.valid_bars_kept:,} | {o.dropped_non_positive_bars:,} |"
            )

    # Section 3: B-06 Dual-Price Split Verification
    lines.extend(
        [
            "",
            "---",
            "",
            "## 3. Dual-Price (`close_raw` vs `close_adj`) Split Divergence Verification (`B-06`)",
            "",
            "Verifies that historical unadjusted `close_raw` diverges from retroactively adjusted `close_adj` on split/stock-dividend stocks (`FPT`, `HPG`, `MWG`), preventing the `2x-5x` historical Market Cap / P/E / P/B distortion.",
            "",
            "| Ticker | Earliest Date | Earliest `close_raw` (VND) | Earliest `close_adj` (VND) | Implied Adj Factor (`raw/adj`) | Latest Date | Latest `close_raw` (VND) | Latest `close_adj` (VND) | Latest `shares_outstanding_market` |",
            "|---|---|---:|---:|---:|---|---:|---:|---:|",
        ]
    )

    for tk in SPLIT_VERIFICATION_TICKERS:
        sub = prices_df.filter(pl.col("ticker") == tk).sort("trade_date")
        if sub.is_empty():
            lines.append(f"| `{tk}` | `MISSING` | - | - | - | - | - | - | - |")
            continue
        first = sub.row(0, named=True)
        last = sub.row(-1, named=True)
        ratio = float(first["close_raw"]) / float(first["close_adj"])
        sh = last["shares_outstanding_market"]
        sh_str = f"{sh:,.0f}" if sh is not None else "NULL"
        lines.append(
            f"| `{tk}` | `{first['trade_date']}` | {first['close_raw']:,.1f} | {first['close_adj']:,.1f} | "
            f"{ratio:.3f}x | `{last['trade_date']}` | {last['close_raw']:,.1f} | {last['close_adj']:,.1f} | {sh_str} |"
        )

    lines.extend(
        [
            "",
            "### Independent CafeF Spot Cross-Check (2018 Session)",
            "",
            "| Ticker | Trade Date | VNDirect `close_raw` | CafeF `close_raw` | VNDirect `close_adj` | CafeF `close_adj` | Raw Diff (VND) | Adj Diff (VND) | Status |",
            "|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for sc in cafef_spot_checks:
        if sc["status"] == "MATCHED":
            lines.append(
                f"| `{sc['ticker']}` | `{sc['trade_date']}` | {sc['vndirect_close_raw']:,.1f} | {sc['cafef_close_raw']:,.1f} | "
                f"{sc['vndirect_close_adj']:,.1f} | {sc['cafef_close_adj']:,.1f} | {sc['raw_diff_vnd']:,.1f} | {sc['adj_diff_vnd']:,.1f} | `MATCHED` |"
            )
        else:
            lines.append(
                f"| `{sc['ticker']}` | `-` | - | - | - | - | - | - | `{sc['status']}` |"
            )

    # Section 4: Case Study Pre/Post Regulatory Warning Verification
    lines.extend(
        [
            "",
            "---",
            "",
            "## 4. Case Studies Pre/Post `R4/R5` & Regulatory Warning Verification",
            "",
            "Aligns earliest regulatory distress event (`CONTROL` / `TRADING_RESTRICTION` / `SUSPENSION` / `FORCED_DELISTING` / `ADVERSE_OR_DISCLAIMER_AUDIT`) from `regulatory_events.parquet` onto `market_prices.parquet` via `align_events_to_market_prices_pit()` (`strategy='forward'`).",
            "",
            "| Ticker | Total Bars | Earliest Reg Event (`event_date`) | Event Type | PIT Aligned Session | Pre-Event Bars | Post-Event Bars | `close_raw` @ Warn (VND) | `close_adj` @ Warn (VND) | Min `close_adj` Post-Warn | Max Drawdown Post-Warn | Latest `close_adj` |",
            "|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )

    for tk in CASE_STUDY_TICKERS:
        sub_px = prices_df.filter(pl.col("ticker") == tk).sort("trade_date")
        if sub_px.is_empty():
            lines.append(
                f"| `{tk}` | 0 | `-` | `-` | `-` | 0 | 0 | - | - | - | - | - |"
            )
            continue
        issuer_id_val = str(sub_px["issuer_id"][0])
        if reg_events_df is not None and not reg_events_df.is_empty():
            tk_events = reg_events_df.filter(pl.col("issuer_id") == issuer_id_val).sort(
                ["event_date", "effective_from"]
            )
        else:
            tk_events = pl.DataFrame()

        if tk_events.is_empty():
            last_row = sub_px.row(-1, named=True)
            lines.append(
                f"| `{tk}` | {sub_px.height:,} | `NONE_IN_LEDGER` | `-` | `-` | {sub_px.height:,} | 0 | - | - | - | - | {last_row['close_adj']:,.1f} |"
            )
            continue

        first_ev = tk_events.head(1)
        aligned = align_events_to_market_prices_pit(
            first_ev.select(["issuer_id", "event_date", "event_type"]),
            sub_px,
            event_date_col="event_date",
        )
        al_row = aligned.row(0, named=True)
        ev_dt = al_row["event_date"]
        ev_type = al_row["event_type"]
        pit_dt = al_row["trade_date"]
        warn_raw = al_row["close_raw"]
        warn_adj = al_row["close_adj"]
        pre_bars = sub_px.filter(pl.col("trade_date") < ev_dt).height
        post_px = sub_px.filter(pl.col("trade_date") >= ev_dt)
        post_bars = post_px.height
        last_adj = float(sub_px.row(-1, named=True)["close_adj"])
        if pit_dt is not None and warn_adj is not None and post_bars > 0:
            min_post = float(post_px["close_adj"].min())  # type: ignore[arg-type]
            dd_pct = ((min_post - float(warn_adj)) / float(warn_adj)) * 100.0
            lines.append(
                f"| `{tk}` | {sub_px.height:,} | `{ev_dt}` | `{ev_type}` | `{pit_dt}` | "
                f"{pre_bars:,} | {post_bars:,} | {float(warn_raw):,.1f} | {float(warn_adj):,.1f} | "
                f"{min_post:,.1f} | {dd_pct:+.2f}% | {last_adj:,.1f} |"
            )
        else:
            lines.append(
                f"| `{tk}` | {sub_px.height:,} | `{ev_dt}` | `{ev_type}` | `NO_SESSION_ON_OR_AFTER` | "
                f"{pre_bars:,} | {post_bars:,} | - | - | - | - | {last_adj:,.1f} |"
            )

    lines.append("")
    return "\n".join(lines)


def run_collection(
    *,
    issuers_parquet: Path,
    regulatory_parquet: Path | None,
    output_dir: Path,
    audit_report_path: Path,
    start_date: date = DEFAULT_START_DATE,
    end_date: date | None = None,
    min_interval_seconds: float = DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
    use_cache: bool = True,
    include_marketcap: bool = True,
    max_tickers: int | None = None,
    max_pages: int = 10,
    progress_file: Path | None = None,
) -> int:
    """Execute end-to-end 375-ticker EOD collection, validation, and audit report."""
    if not issuers_parquet.is_file():
        raise FileNotFoundError(f"Issuers parquet not found: {issuers_parquet}")

    effective_end = end_date if end_date is not None else datetime.now(VN_TZ).date()
    issuers_df = pl.read_parquet(
        issuers_parquet, columns=["issuer_id", "ticker", "exchange"]
    ).sort("ticker")
    if max_tickers is not None and max_tickers > 0:
        issuers_df = issuers_df.head(max_tickers)

    reg_events_df: pl.DataFrame | None = None
    if regulatory_parquet is not None and regulatory_parquet.is_file():
        reg_events_df = pl.read_parquet(regulatory_parquet)

    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    client = PoliteMarketPriceHttpClient(min_interval_seconds=min_interval_seconds)

    all_bars: list[MarketPriceBar] = []
    outcomes: list[TickerPriceCollectionOutcome] = []
    manifest_lines: list[str] = []

    rows = issuers_df.to_dicts()
    total = len(rows)
    for idx, row in enumerate(rows, start=1):
        issuer_id = str(row["issuer_id"])
        ticker = str(row["ticker"]).strip().upper()
        exchange = str(row["exchange"]).strip().upper()

        bars, outcome = collect_ticker_market_prices(
            ticker=ticker,
            issuer_id=issuer_id,
            current_exchange=exchange,
            raw_dir=raw_dir,
            client=client,
            start_date=start_date,
            end_date=effective_end,
            use_cache=use_cache,
            include_marketcap=include_marketcap,
            max_pages=max_pages,
        )
        all_bars.extend(bars)
        outcomes.append(outcome)
        manifest_lines.append(json.dumps(asdict(outcome), ensure_ascii=False))

        if idx % 25 == 0 or idx == total:
            now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            err_cnt = sum(1 for o in outcomes if o.errors)
            zero_cnt = sum(1 for o in outcomes if o.valid_bars_kept == 0)
            print(
                f"[{now_iso}] Processed {idx}/{total} tickers "
                f"(bars={len(all_bars):,}, zero_data={zero_cnt}, errors={err_cnt})",
                flush=True,
            )
            if progress_file is not None:
                progress_file.write_text(
                    f"# Progress — `price-data-dev` (`UI-09a`)\n"
                    f"Last visited: {now_iso}\n\n"
                    f"## Current Phase\n"
                    f"- Phase 2: Collecting 375-ticker EOD market prices ({idx}/{total} tickers).\n\n"
                    f"## Metrics\n"
                    f"- EOD bars collected: {len(all_bars):,}\n"
                    f"- Zero-data tickers: {zero_cnt}\n"
                    f"- Tickers with fetch errors: {err_cnt}\n",
                    encoding="utf-8",
                )

    manifest_path = raw_dir / "collection_manifest.jsonl"
    manifest_tmp = manifest_path.with_suffix(".jsonl.tmp")
    manifest_tmp.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    manifest_tmp.replace(manifest_path)

    prices_df = validate_market_prices(build_market_prices_table(all_bars))
    _write_parquet_atomic(prices_df, output_dir / "market_prices.parquet")

    cafef_spot_checks = _fetch_cafef_spot_checks(
        tickers=SPLIT_VERIFICATION_TICKERS,
        prices_df=prices_df,
        raw_dir=raw_dir,
        client=client,
        start_date=start_date,
        end_date=effective_end,
        use_cache=use_cache,
    )

    generated_at = datetime.now(UTC)
    report_text = render_market_prices_audit_report(
        issuers_df=issuers_df,
        outcomes=outcomes,
        prices_df=prices_df,
        reg_events_df=reg_events_df,
        cafef_spot_checks=cafef_spot_checks,
        start_date=start_date,
        end_date=effective_end,
        generated_at=generated_at,
    )
    audit_report_path.parent.mkdir(parents=True, exist_ok=True)
    report_tmp = audit_report_path.with_suffix(".md.tmp")
    report_tmp.write_text(report_text, encoding="utf-8")
    report_tmp.replace(audit_report_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect real EOD market prices for FinIntel 375 corporate tickers (UI-09a)."
    )
    parser.add_argument(
        "--issuers-parquet",
        type=Path,
        default=DEFAULT_ISSUERS_PARQUET,
        help="Read-only issuers.parquet path.",
    )
    parser.add_argument(
        "--regulatory-parquet",
        type=Path,
        default=DEFAULT_REGULATORY_PARQUET,
        help="Read-only regulatory_events.parquet path for case-study verification.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory for raw/ and market_prices.parquet.",
    )
    parser.add_argument(
        "--audit-report",
        type=Path,
        default=DEFAULT_AUDIT_REPORT,
        help="Output Markdown path for market_prices_audit_375.md.",
    )
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        default=DEFAULT_START_DATE,
        help="Inclusive start trade date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=None,
        help="Inclusive end trade date (YYYY-MM-DD; defaults to VN today).",
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
        help="Minimum interval in seconds per host.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Force live refetch even when raw receipts exist.",
    )
    parser.add_argument(
        "--no-marketcap",
        action="store_true",
        help="Skip VNDirect MARKETCAP ratio calls.",
    )
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=None,
        help="Optional cap for smoke testing.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="Maximum pagination pages per VNDirect/CafeF endpoint.",
    )
    parser.add_argument(
        "--progress-file",
        type=Path,
        default=None,
        help="Optional progress.md path to update heartbeat during run.",
    )
    args = parser.parse_args(argv)
    return run_collection(
        issuers_parquet=args.issuers_parquet,
        regulatory_parquet=args.regulatory_parquet,
        output_dir=args.output_dir,
        audit_report_path=args.audit_report,
        start_date=args.start_date,
        end_date=args.end_date,
        min_interval_seconds=args.min_interval,
        use_cache=not args.no_cache,
        include_marketcap=not args.no_marketcap,
        max_tickers=args.max_tickers,
        max_pages=args.max_pages,
        progress_file=args.progress_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())
