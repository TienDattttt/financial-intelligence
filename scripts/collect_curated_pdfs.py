"""Collect and verify 30-50 real audited consolidated annual BCTC PDFs (UI-08a).

Reads archived CafeF BCTC HTML tables from disk, filters strictly for audited
annual CONSOLIDATED financial statements, enforces Port 2 CDN allowlist &
ZIP-bomb guards (H-02), stores PDFs immutably by SHA-256, and writes the
manifest Parquet plus audit report.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from finintel.exceptions import ContractViolationError
from finintel.rag.pdf_catalog import (
    CuratedPdfRecord,
    PdfCandidate,
    download_curated_pdf,
    extract_audited_consolidated_annual_candidates,
    render_curated_pdfs_audit_markdown,
    select_candidates_for_years,
    verify_stored_manifest_files,
    write_manifest_parquet,
)

DEFAULT_CAFEF_HTML_DIR = Path(
    "d:/KiCuoiCung/DATN/financial-intelligence/data/go_live_nfr_run/raw_disclosures/cafef_bctc"
)
DEFAULT_OUTPUT_DIR = Path("d:/KiCuoiCung/DATN/financial-intelligence/data/curated_pdfs")
DEFAULT_AUDIT_REPORT = Path(
    "d:/KiCuoiCung/DATN/financial-intelligence/docs/refactor/curated_pdfs_audit.md"
)
DEFAULT_RATE_LIMIT_RPS = 0.6


@dataclass(frozen=True, slots=True)
class IssuerCollectionTarget:
    """Target issuer, cohort label, preferred fiscal years, and max PDFs to collect."""

    issuer_id: str
    cohort: str
    preferred_years: tuple[int, ...]
    max_items: int


CASE_STUDY_TARGETS: tuple[IssuerCollectionTarget, ...] = (
    IssuerCollectionTarget("HBC", "case_study", (2023, 2022, 2024), 2),
    IssuerCollectionTarget("POM", "case_study", (2022, 2021, 2025), 2),
    IssuerCollectionTarget("HNG", "case_study", (2023, 2022, 2024), 2),
    IssuerCollectionTarget("DLG", "case_study", (2023, 2022, 2024), 2),
    IssuerCollectionTarget("SMC", "case_study", (2024, 2023, 2025), 2),
    IssuerCollectionTarget("QBS", "case_study", (2021, 2020, 2019), 2),
    IssuerCollectionTarget("PVX", "case_study", (2023, 2020, 2022), 2),
    IssuerCollectionTarget("PVL", "case_study", (2022, 2021, 2020), 2),
    IssuerCollectionTarget("HVN", "case_study", (2023, 2022, 2024), 2),
    IssuerCollectionTarget("SII", "case_study", (2023, 2022, 2024), 2),
    IssuerCollectionTarget("SJF", "case_study", (2023, 2022, 2025), 2),
)

WATCHLIST_TARGETS: tuple[IssuerCollectionTarget, ...] = (
    IssuerCollectionTarget("FPT", "watchlist_bluechip", (2025, 2024), 1),
    IssuerCollectionTarget("HPG", "watchlist_bluechip", (2025, 2024), 1),
    IssuerCollectionTarget("VNM", "watchlist_bluechip", (2025, 2024), 1),
    IssuerCollectionTarget("MWG", "watchlist_bluechip", (2025, 2024), 1),
    IssuerCollectionTarget("DGC", "watchlist_bluechip", (2025, 2024), 1),
    IssuerCollectionTarget("GAS", "watchlist_bluechip", (2025, 2024), 1),
    IssuerCollectionTarget("VRE", "watchlist_bluechip", (2025, 2024), 1),
    IssuerCollectionTarget("VIC", "watchlist_bluechip", (2025, 2024), 1),
    IssuerCollectionTarget("GMD", "watchlist_bluechip", (2025, 2024), 1),
    IssuerCollectionTarget("VEA", "watchlist_bluechip", (2025, 2024, 2022), 1),
    IssuerCollectionTarget("REE", "watchlist_bluechip", (2025, 2024), 1),
    IssuerCollectionTarget("PNJ", "watchlist_bluechip", (2025, 2024), 1),
    IssuerCollectionTarget("MSN", "watchlist_bluechip", (2025, 2024), 1),
    IssuerCollectionTarget("SAB", "watchlist_bluechip", (2025, 2024), 1),
    IssuerCollectionTarget("VHM", "watchlist_bluechip", (2025, 2024), 1),
    IssuerCollectionTarget("PVD", "watchlist_bluechip", (2025, 2024), 1),
    IssuerCollectionTarget("NVL", "watchlist_high_risk", (2025, 2024), 1),
    IssuerCollectionTarget("MCG", "watchlist_high_risk", (2025, 2024), 1),
    IssuerCollectionTarget("ASP", "watchlist_high_risk", (2025, 2024), 1),
    IssuerCollectionTarget("CIG", "watchlist_high_risk", (2025, 2024), 1),
)

ALL_TARGETS: tuple[IssuerCollectionTarget, ...] = CASE_STUDY_TARGETS + WATCHLIST_TARGETS


def _plan_candidates(
    cafef_html_dir: Path,
    targets: tuple[IssuerCollectionTarget, ...],
) -> tuple[list[tuple[IssuerCollectionTarget, tuple[PdfCandidate, ...]]], list[str]]:
    plan: list[tuple[IssuerCollectionTarget, tuple[PdfCandidate, ...]]] = []
    missing_notes: list[str] = []

    for target in targets:
        html_path = cafef_html_dir / f"{target.issuer_id}.html"
        if not html_path.is_file():
            missing_notes.append(
                f"{target.issuer_id}: missing CafeF HTML file ({html_path})."
            )
            continue
        html_text = html_path.read_text(encoding="utf-8", errors="replace")
        all_candidates = extract_audited_consolidated_annual_candidates(
            target.issuer_id, html_text
        )
        ordered = select_candidates_for_years(
            all_candidates,
            target.preferred_years,
            max_items=max(target.max_items + 2, len(target.preferred_years)),
            fallback_to_latest=True,
        )
        if not ordered:
            missing_notes.append(
                f"{target.issuer_id}: no audited annual CONSOLIDATED PDF/ZIP candidate found."
            )
            continue
        plan.append((target, ordered))
    return plan, missing_notes


def collect_curated_pack(
    *,
    cafef_html_dir: Path,
    output_dir: Path,
    audit_report_path: Path,
    rate_limit_rps: float = DEFAULT_RATE_LIMIT_RPS,
    dry_run: bool = False,
) -> int:
    """Execute end-to-end collection, SHA-256 verification, manifest & audit writing."""
    plan, failures = _plan_candidates(cafef_html_dir, ALL_TARGETS)
    if dry_run:
        planned_count = sum(min(t.max_items, len(cands)) for t, cands in plan)
        print(
            f"[DRY-RUN] Planned {planned_count} PDFs across {len(plan)} issuers "
            f"({len(failures)} missing)."
        )
        for target, cands in plan:
            chosen = cands[: target.max_items]
            years = [c.fiscal_year for c in chosen]
            print(f"  {target.issuer_id:4s} ({target.cohort}): FY={years}")
        return 0

    min_interval = 1.0 / rate_limit_rps if rate_limit_rps > 0 else 0.0
    last_request_ts = 0.0
    records: list[CuratedPdfRecord] = []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "FinIntel-Curated-PDF-Collector/1.0"
        )
    }

    with httpx.Client(
        timeout=httpx.Timeout(60.0, connect=15.0), headers=headers
    ) as client:
        for target, candidates in plan:
            collected_for_issuer = 0
            for candidate in candidates:
                if collected_for_issuer >= target.max_items:
                    break
                elapsed = time.monotonic() - last_request_ts
                if last_request_ts > 0.0 and elapsed < min_interval:
                    time.sleep(min_interval - elapsed)
                last_request_ts = time.monotonic()

                try:
                    rec = download_curated_pdf(
                        candidate,
                        output_root=output_dir,
                        cohort=target.cohort,
                        client=client,
                    )
                except (ContractViolationError, httpx.HTTPError, OSError) as exc:
                    note = (
                        f"{candidate.issuer_id} FY{candidate.fiscal_year} "
                        f"({candidate.source_url}): {exc}"
                    )
                    failures.append(note)
                    print(f"[WARN] {note}", file=sys.stderr)
                    continue

                records.append(rec)
                collected_for_issuer += 1
                size_mb = rec.file_size_bytes / (1024 * 1024)
                print(
                    f"[OK] {rec.issuer_id:4s} FY{rec.fiscal_year} ({rec.cohort}) "
                    f"{size_mb:5.2f} MB sha256={rec.content_sha256[:12]} "
                    f"zip={rec.extracted_from_zip}"
                )

    manifest_path = output_dir / "curated_pdfs_manifest.parquet"
    manifest = write_manifest_parquet(records, manifest_path)
    verification = verify_stored_manifest_files(manifest, output_root=output_dir)

    audit_md = render_curated_pdfs_audit_markdown(
        manifest,
        case_study_tickers=[t.issuer_id for t in CASE_STUDY_TARGETS],
        output_root=output_dir,
        failures=failures,
    )
    audit_report_path.parent.mkdir(parents=True, exist_ok=True)
    audit_report_path.write_text(audit_md, encoding="utf-8")

    print(
        f"\n[DONE] Collected {manifest.height} valid PDFs across "
        f"{manifest['issuer_id'].n_unique()} issuers | "
        f"verified_ok={verification['verified_ok']} "
        f"invalid={verification['invalid_or_missing']} | "
        f"manifest={manifest_path} | audit={audit_report_path}"
    )
    if verification["invalid_or_missing"] > 0 or not (30 <= manifest.height <= 50):
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect & verify 30-50 curated audited consolidated BCTC PDFs (UI-08a)."
    )
    parser.add_argument(
        "--cafef-html-dir",
        type=Path,
        default=DEFAULT_CAFEF_HTML_DIR,
        help="Directory containing archived CafeF BCTC HTML files (<TICKER>.html).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Target directory for sha256/<2-char>/<sha256>.pdf and manifest parquet.",
    )
    parser.add_argument(
        "--audit-report",
        type=Path,
        default=DEFAULT_AUDIT_REPORT,
        help="Output Markdown path for curated_pdfs_audit.md.",
    )
    parser.add_argument(
        "--rate-limit-rps",
        type=float,
        default=DEFAULT_RATE_LIMIT_RPS,
        help="Maximum HTTP requests per second (default: 0.6 req/s).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned candidates without making network requests.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return collect_curated_pack(
        cafef_html_dir=args.cafef_html_dir,
        output_dir=args.output_dir,
        audit_report_path=args.audit_report,
        rate_limit_rps=args.rate_limit_rps,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
