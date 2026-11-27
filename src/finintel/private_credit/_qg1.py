"""Canonical Polars conversion and Layer 6 QG1 evaluation for private-issuer statements (V3-28)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal, cast

import polars as pl

from finintel.quality.rules import (
    REQUIRED_MIN_METRICS,
    build_period_wide,
    check_balance_sheet_identity,
    check_cash_flow_reconciliation,
    check_info_signals,
    check_missing_required_metrics,
    check_partial_statement,
    check_subtotal_mismatch,
)
from finintel.serving.dictionary import (
    StatementMetricMeta,
    load_statement_metric_catalog,
)
from finintel.storage.schemas import ISSUERS_SCHEMA

StatementType = Literal["BALANCE_SHEET", "INCOME_STATEMENT", "CASH_FLOW"]
TaxonomyVersionStr = Literal["VAS_TT200_2014", "VAS_TT99_2025"]
StatementScopeStr = Literal["CONSOLIDATED", "SEPARATE"]


@dataclass(frozen=True)
class ParsedFactRow:
    statement_type: StatementType
    vas_code: str
    metric_id: str
    line_label_vn: str
    period_end_date: date
    fiscal_year: int
    value: float | None


@dataclass(frozen=True)
class Qg1Discrepancy:
    period_end_date: date
    rule_code: str
    severity: str
    message_vi: str
    expected_value_vnd: float | None = None
    actual_value_vnd: float | None = None
    abs_residual_vnd: float | None = None
    missing_metrics: tuple[str, ...] = ()


@dataclass(frozen=True)
class Qg1CheckSummary:
    status: Literal["PASS", "WARNING", "FAIL"]
    discrepancies: tuple[Qg1Discrepancy, ...]
    warnings: tuple[str, ...]
    info_signals: tuple[str, ...]

    @property
    def can_score(self) -> bool:
        return self.status != "FAIL"


def facts_to_canonical_polars(
    facts: Sequence[ParsedFactRow],
    *,
    issuer_id: str,
    ticker: str,
    company_name: str,
    icb_code_level2: str | None,
    statement_scope: StatementScopeStr,
    taxonomy_version: TaxonomyVersionStr,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Convert parsed private-credit rows into canonical Layer 2/4/6 Polars frames."""
    catalog = load_statement_metric_catalog()
    rows: list[dict[str, object]] = []
    for f in facts:
        meta: StatementMetricMeta | None = catalog.get(f.metric_id)
        rows.append(
            {
                "issuer_id": issuer_id,
                "ticker_at_filing": ticker,
                "source_provider": "private_excel_upload",
                "statement_type": f.statement_type,
                "statement_scope": statement_scope,
                "period_type": "ANNUAL",
                "fiscal_year": f.fiscal_year,
                "fiscal_quarter": None,
                "fiscal_year_start_date": date(f.fiscal_year, 1, 1),
                "period_start_date": date(f.fiscal_year, 1, 1),
                "period_end_date": f.period_end_date,
                "metric_id": f.metric_id,
                "taxonomy_version": taxonomy_version,
                "revision_no": 1,
                "filing_id": f"PRIV-{issuer_id}-{f.fiscal_year}",
                "published_at": f.period_end_date,
                "published_at_basis": "EXACT_FILING",
                "effective_available_at": f.period_end_date,
                "revision_reason": "ORIGINAL",
                "derivation_method": "REPORTED_DIRECTLY",
                "audit_status": "AUDITED",
                "audit_opinion": "UNMODIFIED",
                "is_filed_late": False,
                "value": f.value,
                "ttm_value": f.value,
                "unit": meta.unit if meta is not None else "VND",
                "comparable_across_taxonomy": meta.comparable_across_taxonomy
                if meta is not None
                else True,
                "computation_note": None,
                "source_filing_ids": [f"PRIV-{issuer_id}-{f.fiscal_year}"],
            }
        )

    facts_df = pl.DataFrame(
        rows,
        schema_overrides={
            "fiscal_year": pl.Int32,
            "fiscal_quarter": pl.Int8,
            "revision_no": pl.Int64,
            "value": pl.Float64,
            "ttm_value": pl.Float64,
            "fiscal_year_start_date": pl.Date,
            "period_start_date": pl.Date,
            "period_end_date": pl.Date,
            "published_at": pl.Date,
            "effective_available_at": pl.Date,
            "is_filed_late": pl.Boolean,
            "comparable_across_taxonomy": pl.Boolean,
            "source_filing_ids": pl.List(pl.String),
        },
    )
    issuers_df = pl.DataFrame(
        [
            {
                "issuer_id": issuer_id,
                "ticker": ticker,
                "exchange": "PRIVATE",
                "company_name_vn": company_name,
                "accounting_regime": "CORPORATE",
                "icb_code_level2": icb_code_level2,
                "icb_name_level2": None,
                "icb_code_level3": None,
                "icb_name_level3": None,
                "industry_snapshot_date": date(2020, 1, 1),
                "delisted_date": None,
            }
        ],
        schema=ISSUERS_SCHEMA,
    )
    return facts_df, issuers_df


def evaluate_private_qg1(
    facts_df: pl.DataFrame, issuers_df: pl.DataFrame
) -> Qg1CheckSummary:
    """Run Layer 6 QG1 balance, subtotal, completeness, and CF checks in memory."""
    if facts_df.is_empty():
        return Qg1CheckSummary(
            status="FAIL",
            discrepancies=(
                Qg1Discrepancy(
                    period_end_date=date.today(),
                    rule_code="EMPTY_STATEMENTS",
                    severity="CRITICAL",
                    message_vi="Không tìm thấy chỉ tiêu tài chính nào trong file Excel tải lên.",
                ),
            ),
            warnings=(),
            info_signals=(),
        )

    period_wide = build_period_wide(facts_df, issuers_df)
    bs_err = check_balance_sheet_identity(period_wide, rel_tol=0.001)
    sub_err = check_subtotal_mismatch(period_wide, rel_tol=0.001)
    miss_err = check_missing_required_metrics(
        period_wide, required_metrics=REQUIRED_MIN_METRICS
    )
    discrepancies: list[Qg1Discrepancy] = []

    for frame in (bs_err, sub_err, miss_err):
        for row in frame.to_dicts():
            rule = str(row["rule_code"])
            details = json.loads(str(row.get("details_json") or "{}"))
            p_date = cast(date, row["period_end_date"])
            if rule == "BS_IDENTITY_MISMATCH":
                assets = float(details.get("total_assets") or 0.0)
                liab_eq = float(details.get("total_liabilities") or 0.0) + float(
                    details.get("total_equity") or 0.0
                )
                diff = float(details.get("abs_residual_vnd") or 0.0)
                discrepancies.append(
                    Qg1Discrepancy(
                        period_end_date=p_date,
                        rule_code=rule,
                        severity="CRITICAL",
                        message_vi=(
                            f"Kỳ {p_date.isoformat()}: Tổng Tài sản không khớp Tổng Nguồn vốn "
                            f"(Nợ phải trả + VCSH), chênh lệch {diff:,.0f} VND."
                        ),
                        expected_value_vnd=assets,
                        actual_value_vnd=liab_eq,
                        abs_residual_vnd=diff,
                    )
                )
            elif rule == "SUBTOTAL_MISMATCH":
                cur = float(details.get("current_assets") or 0.0)
                noncur = float(details.get("noncurrent_assets") or 0.0)
                tot = float(details.get("total_assets") or 0.0)
                diff = float(details.get("abs_residual_vnd") or 0.0)
                discrepancies.append(
                    Qg1Discrepancy(
                        period_end_date=p_date,
                        rule_code=rule,
                        severity="CRITICAL",
                        message_vi=(
                            f"Kỳ {p_date.isoformat()}: Tiểu tổng TS ngắn hạn (100) + TS dài hạn (200) "
                            f"lệch Tổng Tài sản {diff:,.0f} VND."
                        ),
                        expected_value_vnd=tot,
                        actual_value_vnd=cur + noncur,
                        abs_residual_vnd=diff,
                    )
                )
            else:
                missing_list = tuple(str(m) for m in details.get("missing_metrics", []))
                discrepancies.append(
                    Qg1Discrepancy(
                        period_end_date=p_date,
                        rule_code=rule,
                        severity="CRITICAL",
                        message_vi=(
                            f"Kỳ {p_date.isoformat()}: Thiếu chỉ tiêu bắt buộc ({', '.join(missing_list)})."
                        ),
                        missing_metrics=missing_list,
                    )
                )

    cf_warn = check_cash_flow_reconciliation(period_wide, rel_tol=0.005)
    part_warn = check_partial_statement(period_wide)
    info_df = check_info_signals(period_wide)
    warnings = tuple(
        f"{r['period_end_date']}: {r['code']}"
        for r in pl.concat([cf_warn, part_warn]).to_dicts()
    )
    info_signals = tuple(
        f"{r['period_end_date']}: {r['code']}" for r in info_df.to_dicts()
    )

    status: Literal["PASS", "WARNING", "FAIL"] = (
        "FAIL" if discrepancies else ("WARNING" if warnings else "PASS")
    )
    return Qg1CheckSummary(
        status=status,
        discrepancies=tuple(discrepancies),
        warnings=warnings,
        info_signals=info_signals,
    )
