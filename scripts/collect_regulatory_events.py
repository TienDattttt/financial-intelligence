"""Collect ground-truth regulatory & audit distress events for 375 corporate tickers (ML-03a)."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from finintel.ingestion.regulatory_events_collector import (  # noqa: E402
    DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
    AuditOpinionRecord,
    PoliteHostHttpClient,
    RawHeadlineCandidate,
    TickerCollectionOutcome,
    build_mandatory_delistings_table,
    build_regulatory_events_table,
    collect_ticker_raw_candidates,
    extract_audit_opinion_records,
)
from finintel.ports.regulatory import validate_regulatory_events  # noqa: E402

DEFAULT_MAIN_REPO = Path("d:/KiCuoiCung/DATN/financial-intelligence")
DEFAULT_ISSUERS_PARQUET = (
    DEFAULT_MAIN_REPO / "data" / "go_live_nfr_run" / "curated" / "issuers.parquet"
)
DEFAULT_OUTPUT_DIR = DEFAULT_MAIN_REPO / "data" / "regulatory_ledger"
DEFAULT_AUDIT_REPORT = DEFAULT_MAIN_REPO / "docs" / "refactor" / "dataset_audit_375.md"
NOTABLE_TICKERS: tuple[str, ...] = (
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


def _build_audit_opinions_frame(records: list[AuditOpinionRecord]) -> pl.DataFrame:
    schema = pl.Schema(
        {
            "issuer_id": pl.String,
            "ticker": pl.String,
            "event_date": pl.Date,
            "opinion_category": pl.String,
            "source_provider": pl.String,
            "source_url": pl.String,
            "raw_headline": pl.String,
            "payload_sha256": pl.String,
        }
    )
    if not records:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame([asdict(r) for r in records], schema=schema).sort(
        ["issuer_id", "event_date", "opinion_category"]
    )


def render_dataset_audit_report(
    *,
    issuers_df: pl.DataFrame,
    outcomes: list[TickerCollectionOutcome],
    reg_events: pl.DataFrame,
    audit_opinions: pl.DataFrame,
    mandatory_delistings: pl.DataFrame,
    generated_at: datetime,
) -> str:
    """Render the empirical audit report ``dataset_audit_375.md`` from verified tables."""
    total_tickers = len(outcomes)
    vci_ok = sum(1 for o in outcomes if o.vietcap_news_pages > 0)
    cafef_ok = sum(1 for o in outcomes if o.cafef_event_pages > 0)
    both_failed = [
        o for o in outcomes if o.vietcap_news_pages == 0 and o.cafef_event_pages == 0
    ]
    partial_errors = [o for o in outcomes if o.errors]

    lines: list[str] = [
        "# Dataset Audit Report — Ground-Truth Regulatory Events Ledger (`ML-03a`, 375 Corporate Tickers)",
        "",
        f"- **Generated (UTC):** `{generated_at.strftime('%Y-%m-%dT%H:%M:%SZ')}`",
        "- **Task:** `ML-03a` (`docs/refactor/02_ML_DATASET_AND_LABELING_PLAN.md` & `GATE-ML-1`)",
        f"- **Universe Size:** `{total_tickers}` corporate issuers (read-only from `data/go_live_nfr_run/curated/issuers.parquet`)",
        f"- **Vietcap IQ Coverage:** `{vci_ok}/{total_tickers}` (`{100.0 * vci_ok / max(total_tickers, 1):.2f}%`)",
        f"- **CafeF Events Coverage:** `{cafef_ok}/{total_tickers}` (`{100.0 * cafef_ok / max(total_tickers, 1):.2f}%`)",
        f"- **Zero-Source Failed Tickers:** `{len(both_failed)}`",
        f"- **Total Validated `regulatory_events` Rows:** `{reg_events.height}`",
        f"- **Distinct Positive Issuers (`regulatory_events`):** `{reg_events['issuer_id'].n_unique() if not reg_events.is_empty() else 0}`",
        f"- **Total `mandatory_delistings_375` Issuers:** `{mandatory_delistings.height}`",
        f"- **Total `audit_opinions_375` Rows:** `{audit_opinions.height}`",
        "- **Schema Validation (`validate_regulatory_events()`):** `PASS`",
        "",
        "---",
        "",
        "## 1. Breakdown by Year (`2018–2026`)",
        "",
        "| Year | Total Events | Distinct Positive Issuers | `FIRST_ENTRY` | `ESCALATION` | `CONTROL` | `TRADING_RESTRICTION` | `SUSPENSION` | `FORCED_DELISTING` | `ADVERSE_OR_DISCLAIMER_AUDIT` |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    ev_with_year = reg_events.with_columns(
        pl.col("event_date").dt.year().alias("_year")
    )
    for yr in range(2018, 2027):
        sub = ev_with_year.filter(pl.col("_year") == yr)
        n_ev = sub.height
        n_iss = sub["issuer_id"].n_unique() if n_ev > 0 else 0
        fe = sub.filter(pl.col("event_stage") == "FIRST_ENTRY").height
        esc = sub.filter(pl.col("event_stage") == "ESCALATION").height
        cnt_ctrl = sub.filter(pl.col("event_type") == "CONTROL").height
        cnt_tr = sub.filter(pl.col("event_type") == "TRADING_RESTRICTION").height
        cnt_susp = sub.filter(pl.col("event_type") == "SUSPENSION").height
        cnt_del = sub.filter(pl.col("event_type") == "FORCED_DELISTING").height
        cnt_aud = sub.filter(
            pl.col("event_type") == "ADVERSE_OR_DISCLAIMER_AUDIT"
        ).height
        lines.append(
            f"| {yr} | {n_ev} | {n_iss} | {fe} | {esc} | {cnt_ctrl} | {cnt_tr} | {cnt_susp} | {cnt_del} | {cnt_aud} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 2. Breakdown by Event Type & Stage",
            "",
            "| `event_type` | Total Rows | Distinct Issuers | `FIRST_ENTRY` | `ESCALATION` | Open (`resolution_date IS NULL`) |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for etype in (
        "CONTROL",
        "TRADING_RESTRICTION",
        "SUSPENSION",
        "FORCED_DELISTING",
        "ADVERSE_OR_DISCLAIMER_AUDIT",
    ):
        sub = reg_events.filter(pl.col("event_type") == etype)
        lines.append(
            f"| `{etype}` | {sub.height} | "
            f"{sub['issuer_id'].n_unique() if not sub.is_empty() else 0} | "
            f"{sub.filter(pl.col('event_stage') == 'FIRST_ENTRY').height} | "
            f"{sub.filter(pl.col('event_stage') == 'ESCALATION').height} | "
            f"{sub.filter(pl.col('resolution_date').is_null()).height} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 3. Mandatory Delistings & UPCOM Demotion Verification (`H-02`)",
            "",
            "| Ticker | Announcement Date | Mandatory Delisting Date | Delisted From Exchange | Current Exchange (`issuers.parquet`) | Headline |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in mandatory_delistings.to_dicts():
        headline_clean = str(row["raw_headline"]).replace("|", "/")
        lines.append(
            f"| `{row['ticker']}` | `{row['announcement_date']}` | `{row['mandatory_delisting_date']}` | "
            f"`{row['exchange_delisted_from']}` | `{row['current_exchange']}` | {headline_clean} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 4. Notable Distress Case Verification (`HBC, POM, HNG, DLG, SMC, QBS, PVX, PVL, HVN`)",
            "",
        ]
    )
    exchange_map = {
        str(r["issuer_id"]): str(r["exchange"])
        for r in issuers_df.select("issuer_id", "exchange").to_dicts()
    }
    for ticker in NOTABLE_TICKERS:
        sub = reg_events.filter(pl.col("issuer_id") == ticker).sort(
            ["event_date", "effective_from"]
        )
        cur_ex = exchange_map.get(ticker, "UNKNOWN")
        lines.append(
            f"### `{ticker}` (Current Exchange: `{cur_ex}`, Validated Events: `{sub.height}`)"
        )
        lines.append("")
        if sub.is_empty():
            lines.append(
                "- No regulatory distress events matched deterministic official rules."
            )
        else:
            lines.append(
                "| `event_date` | `effective_from` | `resolution_date` | `event_type` | `event_stage` | `exchange_delisted_from` | `source_provider` | `raw_headline` |"
            )
            lines.append("|---|---|---|---|---|---|---|---|")
            for r in sub.to_dicts():
                hl = str(r["raw_headline"]).replace("|", "/")
                lines.append(
                    f"| `{r['event_date']}` | `{r['effective_from']}` | `{r['resolution_date']}` | "
                    f"`{r['event_type']}` | `{r['event_stage']}` | `{r['exchange_delisted_from']}` | "
                    f"`{r['source_provider']}` | {hl} |"
                )
        lines.append("")

    lines.extend(
        [
            "---",
            "",
            "## 5. Supplementary Audit Opinions Summary (`audit_opinions_375.parquet`)",
            "",
            "| `opinion_category` | Rows | Distinct Issuers |",
            "|---|---:|---:|",
        ]
    )
    for cat in ("DISCLAIMER", "ADVERSE", "GOING_CONCERN", "EXCEPT_FOR"):
        sub = audit_opinions.filter(pl.col("opinion_category") == cat)
        lines.append(
            f"| `{cat}` | {sub.height} | "
            f"{sub['issuer_id'].n_unique() if not sub.is_empty() else 0} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 6. Provenance Spot-Check & Collection Errors",
            "",
            f"- **Tickers with partial/source errors (`errors` non-empty):** `{len(partial_errors)}`",
        ]
    )
    if partial_errors:
        lines.append("")
        lines.append("| Ticker | Vietcap Pages | CafeF Pages | Error Details |")
        lines.append("|---|---:|---:|---|")
        for err_o in partial_errors[:100]:
            joined = "; ".join(err_o.errors).replace("|", "/")
            lines.append(
                f"| `{err_o.ticker}` | {err_o.vietcap_news_pages} | {err_o.cafef_event_pages} | {joined} |"
            )

    if not reg_events.is_empty():
        sample_n = min(12, reg_events.height)
        sample_rows = reg_events.sample(n=sample_n, seed=42).to_dicts()
        lines.extend(
            [
                "",
                "### Random 12-Record Provenance Sample",
                "",
                "| `issuer_id` | `event_date` | `effective_from` | `event_type` | `event_stage` | `source_provider` | `source_url` | `payload_sha256` |",
                "|---|---|---|---|---|---|---|---|",
            ]
        )
        for r in sample_rows:
            lines.append(
                f"| `{r['issuer_id']}` | `{r['event_date']}` | `{r['effective_from']}` | "
                f"`{r['event_type']}` | `{r['event_stage']}` | `{r['source_provider']}` | "
                f"`{r['source_url']}` | `{r['payload_sha256']}` |"
            )
    lines.append("")
    return "\n".join(lines)


def run_collection(
    *,
    issuers_parquet: Path,
    output_dir: Path,
    audit_report_path: Path,
    min_interval_seconds: float = DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
    use_cache: bool = True,
    max_tickers: int | None = None,
    progress_file: Path | None = None,
) -> int:
    """Execute end-to-end collection, validation, and audit report generation."""
    if not issuers_parquet.is_file():
        raise FileNotFoundError(f"Issuers parquet not found: {issuers_parquet}")

    issuers_df = pl.read_parquet(
        issuers_parquet, columns=["issuer_id", "ticker", "exchange"]
    ).sort("ticker")
    if max_tickers is not None and max_tickers > 0:
        issuers_df = issuers_df.head(max_tickers)

    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    client = PoliteHostHttpClient(min_interval_seconds=min_interval_seconds)

    all_candidates: list[RawHeadlineCandidate] = []
    outcomes: list[TickerCollectionOutcome] = []
    exchange_by_issuer: dict[str, str] = {}

    rows = issuers_df.to_dicts()
    total = len(rows)
    manifest_path = raw_dir / "collection_manifest.jsonl"
    manifest_lines: list[str] = []

    for idx, row in enumerate(rows, start=1):
        issuer_id = str(row["issuer_id"])
        ticker = str(row["ticker"]).strip().upper()
        exchange = str(row["exchange"]).strip().upper()
        exchange_by_issuer[issuer_id] = exchange

        cands, outcome = collect_ticker_raw_candidates(
            ticker=ticker,
            issuer_id=issuer_id,
            current_exchange=exchange,
            raw_dir=raw_dir,
            client=client,
            use_cache=use_cache,
        )
        all_candidates.extend(cands)
        outcomes.append(outcome)
        manifest_lines.append(json.dumps(asdict(outcome), ensure_ascii=False))

        if idx % 25 == 0 or idx == total:
            now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            print(
                f"[{now_iso}] Processed {idx}/{total} tickers "
                f"(candidates={len(all_candidates)}, errors={sum(1 for o in outcomes if o.errors)})",
                flush=True,
            )
            if progress_file is not None:
                progress_file.write_text(
                    f"# Progress — events-data-dev (`ML-03a`)\n"
                    f"Last visited: {now_iso}\n\n"
                    f"## Current Phase\n"
                    f"- Phase 2: Collecting 375-ticker ground-truth regulatory events ({idx}/{total} tickers).\n\n"
                    f"## Metrics\n"
                    f"- Candidates parsed: {len(all_candidates)}\n"
                    f"- Tickers with fetch errors: {sum(1 for o in outcomes if o.errors)}\n",
                    encoding="utf-8",
                )

    manifest_tmp = manifest_path.with_suffix(".jsonl.tmp")
    manifest_tmp.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    manifest_tmp.replace(manifest_path)

    reg_events = build_regulatory_events_table(
        all_candidates,
        exchange_by_issuer=exchange_by_issuer,
        min_date=date(2018, 1, 1),
        max_date=date(2026, 12, 31),
    )
    reg_events = validate_regulatory_events(reg_events)

    audit_records = extract_audit_opinion_records(all_candidates)
    audit_opinions = _build_audit_opinions_frame(audit_records)
    mandatory_delistings = build_mandatory_delistings_table(
        reg_events, exchange_by_issuer=exchange_by_issuer
    )

    _write_parquet_atomic(reg_events, output_dir / "regulatory_events.parquet")
    _write_parquet_atomic(audit_opinions, output_dir / "audit_opinions_375.parquet")
    _write_parquet_atomic(
        mandatory_delistings, output_dir / "mandatory_delistings_375.parquet"
    )

    report_md = render_dataset_audit_report(
        issuers_df=issuers_df,
        outcomes=outcomes,
        reg_events=reg_events,
        audit_opinions=audit_opinions,
        mandatory_delistings=mandatory_delistings,
        generated_at=datetime.now(UTC),
    )
    audit_report_path.parent.mkdir(parents=True, exist_ok=True)
    report_tmp = audit_report_path.with_suffix(".md.tmp")
    report_tmp.write_text(report_md, encoding="utf-8")
    report_tmp.replace(audit_report_path)

    # Emit summary stats to stdout
    by_type: dict[str, int] = defaultdict(int)
    for r in reg_events.to_dicts():
        by_type[str(r["event_type"])] += 1
    print(
        f"Done: {reg_events.height} validated regulatory events across "
        f"{reg_events['issuer_id'].n_unique() if not reg_events.is_empty() else 0} issuers; "
        f"mandatory_delistings={mandatory_delistings.height}; "
        f"audit_opinions={audit_opinions.height}; breakdown={dict(by_type)}",
        flush=True,
    )
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--issuers-parquet",
        type=Path,
        default=DEFAULT_ISSUERS_PARQUET,
        help="Read-only path to curated issuers.parquet.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Target directory for regulatory_events.parquet and raw/ receipts.",
    )
    parser.add_argument(
        "--audit-report",
        type=Path,
        default=DEFAULT_AUDIT_REPORT,
        help="Target markdown path for dataset_audit_375.md.",
    )
    parser.add_argument(
        "--min-interval-seconds",
        type=float,
        default=DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
        help="Polite minimum delay in seconds between requests per host.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore cached raw receipts and re-fetch from remote endpoints.",
    )
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=None,
        help="Optional cap on ticker count (for smoke tests).",
    )
    parser.add_argument(
        "--progress-file",
        type=Path,
        default=None,
        help="Optional heartbeat markdown path updated every 25 tickers.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    return run_collection(
        issuers_parquet=args.issuers_parquet,
        output_dir=args.output_dir,
        audit_report_path=args.audit_report,
        min_interval_seconds=args.min_interval_seconds,
        use_cache=not args.no_cache,
        max_tickers=args.max_tickers,
        progress_file=args.progress_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())
