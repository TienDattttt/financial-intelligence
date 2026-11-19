"""Reconciliation tests verifying pipeline outputs against Golden PDF Fixtures.

Enforces exact Layer 6 tolerances from LAYER_5_6_SERVING_AND_QUALITY.md:
  - Normalized currency & TTM values: rel_tol = 1e-3 (0.1%)
  - Decimal financial ratios: abs_tol = 1e-4 (1 bps)
  - Status flags: 100% exact string match ('NEGATIVE_OR_ZERO_EQUITY',
    'NO_INTEREST_EXPENSE', 'MIXED_TAXONOMY').
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import polars as pl

from finintel.analytics.tables import RATIOS_TABLE, build_analytics_tables
from finintel.domain.schemas import (
    AuditOpinion,
    AuditStatus,
    FilingMetadata,
    PeriodType,
    StatementScope,
    StatementType,
)
from finintel.normalization.mapper import TaxonomyMapper
from finintel.normalization.periods import compute_ttm

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO_ROOT / "tests/fixtures/golden_ratios/fpt_2025_golden_ratios.json"
RAW_DIR = REPO_ROOT / "tests/fixtures/raw_samples"


def _fpt_issuer() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issuer_id": ["FPT"],
            "ticker": ["FPT"],
            "exchange": ["HOSE"],
            "company_name_vn": ["Công ty Cổ phần FPT"],
            "accounting_regime": ["CORPORATE"],
            "icb_code_level2": ["9500"],
            "icb_name_level2": ["Công nghệ thông tin"],
            "icb_code_level3": ["9530"],
            "icb_name_level3": ["Phần mềm & Dịch vụ CNTT"],
            "industry_snapshot_date": [date(2025, 1, 1)],
            "delisted_date": [None],
        }
    )


def _map_fpt_audited_annual(
    mapper: TaxonomyMapper,
    year: int,
    bs_raw: dict[str, object],
    is_raw: dict[str, object],
    cf_raw: dict[str, object],
) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    for stmt, raw_dict, suffix in (
        (StatementType.BALANCE_SHEET, bs_raw, "bs"),
        (StatementType.INCOME_STATEMENT, is_raw, "is"),
        (StatementType.CASH_FLOW, cf_raw, "cf"),
    ):
        meta = FilingMetadata(
            issuer_id="FPT",
            ticker_at_filing="FPT",
            statement_type=stmt,
            period_type=PeriodType.ANNUAL,
            fiscal_year=year,
            fiscal_year_start_date=date(year, 1, 1),
            period_start_date=date(year, 1, 1),
            period_end_date=date(year, 12, 31),
            statement_scope=StatementScope.CONSOLIDATED,
            published_at=date(year + 1, 3, 19),
            audit_status=AuditStatus.AUDITED,
            audit_opinion=AuditOpinion.UNMODIFIED,
        )
        mapped, unmapped = mapper.map_metrics(
            raw_dict,
            meta,
            filing_id=f"fpt-{year}-annual-{suffix}",
            source_provider="fhsc",
        )
        assert unmapped.is_empty(), f"Unexpected unmapped keys: {unmapped}"
        frames.append(mapped)
    return pl.concat(frames)


def test_reconcile_fpt_2025_audited_ratios_and_ttm_against_golden_pdf() -> None:
    """Reconcile normalized VND, TTM values (rel_tol=1e-3), and ratios (abs_tol=1e-4) vs FPT PDF."""
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    rel_tol = float(golden["tolerances"]["currency_and_ttm_rel_tol"])
    abs_tol = float(golden["tolerances"]["ratio_abs_tol"])
    expected = golden["fpt_2025_audited_consolidated"]

    annual_extract = json.loads(
        (RAW_DIR / "fhsc_fpt_annual_2017_2025_extract.json").read_text(encoding="utf-8")
    )["statements"]
    tax_extract = json.loads(
        (RAW_DIR / "fhsc_fpt_layer4_taxonomy_extract.json").read_text(encoding="utf-8")
    )["rows"]
    flow_extract = json.loads(
        (RAW_DIR / "fhsc_fpt_2025_annual_flow_extract.json").read_text(encoding="utf-8")
    )
    spike_ref = json.loads(
        (RAW_DIR / "fpt_2025_spike_reference.json").read_text(encoding="utf-8")
    )

    def _by_year(rows: list[dict[str, object]], yr: int) -> dict[str, object]:
        row = next(r for r in rows if r["year"] == yr)
        return {k: v for k, v in row.items() if k != "year"}

    bs_2024 = _by_year(annual_extract["balance-sheet"], 2024)
    is_2024 = _by_year(annual_extract["income-statement"], 2024)
    cf_2024 = _by_year(annual_extract["cash-flow"], 2024)

    bs_2025 = {
        **_by_year(annual_extract["balance-sheet"], 2025),
        "nophaitra": spike_ref["audited_annual_2025"]["values_vnd"][
            "total_liabilities"
        ]["value"],
        "cophieuquy": 0,
    }
    tax_2025_is = next(
        r["raw_metrics"]
        for r in tax_extract
        if r["page"] == "income-statement-year-001.json" and r["year"] == 2025
    )
    is_2025 = {**_by_year(annual_extract["income-statement"], 2025), **tax_2025_is}
    cf_2025 = {
        **_by_year(annual_extract["cash-flow"], 2025),
        "khauhaotaisancodinh": flow_extract["cash-flow-year-001.json"][
            "khauhaotaisancodinh"
        ],
    }

    mapper = TaxonomyMapper(REPO_ROOT / "config")
    facts_2024 = _map_fpt_audited_annual(mapper, 2024, bs_2024, is_2024, cf_2024)
    facts_2025 = _map_fpt_audited_annual(mapper, 2025, bs_2025, is_2025, cf_2025)
    facts = pl.concat([facts_2024, facts_2025])

    # 1. Reconcile normalized 2025 VND currency values (rel_tol = 1e-3).
    norm_2025 = dict(facts_2025.select("metric_id", "value").rows())
    for metric_id, golden_vnd in expected["normalized_currency_vnd"].items():
        assert math.isclose(norm_2025[metric_id], float(golden_vnd), rel_tol=rel_tol), (
            f"Currency mismatch on {metric_id}: {norm_2025[metric_id]} vs {golden_vnd}"
        )

    # 2. Reconcile 2025 Q4 TTM values from verified quarterly PDF lines (rel_tol = 1e-3).
    q_dates = [
        (1, date(2025, 1, 1), date(2025, 3, 31), date(2025, 4, 22)),
        (2, date(2025, 4, 1), date(2025, 6, 30), date(2025, 7, 22)),
        (3, date(2025, 7, 1), date(2025, 9, 30), date(2025, 10, 23)),
        (4, date(2025, 10, 1), date(2025, 12, 31), date(2026, 1, 26)),
    ]
    q_frames: list[pl.DataFrame] = []
    for q_num, p_start, p_end, pub in q_dates:
        label = f"2025-Q{q_num}"
        rev_val = spike_ref["lines"]["income_statement"]["expected_vnd"][
            "CONSOLIDATED"
        ]["quarter"][label]
        cfo_val = spike_ref["lines"]["cash_flow"]["expected_vnd"]["CONSOLIDATED"][
            "quarter"
        ][label]
        for stmt, raw_k, raw_v, suf in (
            (
                StatementType.INCOME_STATEMENT,
                "doanhthuthuanvebanhangvacungcapdichvu",
                rev_val,
                "is",
            ),
            (
                StatementType.CASH_FLOW,
                "luuchuyentienthuantuhoatdongkinhdoanh",
                cfo_val,
                "cf",
            ),
        ):
            meta = FilingMetadata(
                issuer_id="FPT",
                ticker_at_filing="FPT",
                statement_type=stmt,
                period_type=PeriodType.QUARTER,
                fiscal_year=2025,
                fiscal_quarter=q_num,
                fiscal_year_start_date=date(2025, 1, 1),
                period_start_date=p_start,
                period_end_date=p_end,
                statement_scope=StatementScope.CONSOLIDATED,
                published_at=pub,
                audit_status=AuditStatus.UNAUDITED,
                audit_opinion=AuditOpinion.NOT_APPLICABLE,
            )
            mapped_q, _ = mapper.map_metrics(
                {raw_k: raw_v},
                meta,
                filing_id=f"fpt-2025-q{q_num}-{suf}",
                source_provider="fhsc",
            )
            q_frames.append(mapped_q)

    ttm_df = compute_ttm(pl.concat(q_frames), as_of=date(2026, 2, 1)).filter(
        pl.col("period_end_date") == date(2025, 12, 31)
    )
    ttm_map = dict(ttm_df.select("metric_id", "ttm_value").rows())
    for metric_id, golden_ttm in expected["ttm_2025_q4_consolidated_vnd"].items():
        assert math.isclose(ttm_map[metric_id], float(golden_ttm), rel_tol=rel_tol), (
            f"TTM mismatch on {metric_id}: {ttm_map[metric_id]} vs {golden_ttm}"
        )

    # 3. Reconcile Layer 4A Financial Ratios (abs_tol = 1e-4 for decimal ratios).
    tables = build_analytics_tables(facts, _fpt_issuer(), as_of=date(2026, 4, 1))
    ratios_2025 = tables[RATIOS_TABLE].filter(
        pl.col("period_end_date") == date(2025, 12, 31)
    )
    assert ratios_2025.height == 1
    row = ratios_2025.row(0, named=True)

    for ratio_name, golden_val in expected["ratios"].items():
        observed = row[ratio_name]
        assert observed is not None, f"Ratio {ratio_name} unexpectedly NULL"
        if ratio_name == "eps_ttm_estimated":
            assert math.isclose(observed, float(golden_val), rel_tol=rel_tol)
        else:
            assert math.isclose(observed, float(golden_val), abs_tol=abs_tol), (
                f"Ratio mismatch on {ratio_name}: observed={observed}, golden={golden_val}"
            )

    for flag_col, expected_flag in expected["expected_flags"].items():
        assert row[flag_col] == expected_flag


def test_reconcile_exact_boundary_status_flags() -> None:
    """Enforce 100% exact string matches on NEGATIVE_OR_ZERO_EQUITY, NO_INTEREST_EXPENSE, MIXED_TAXONOMY."""
    golden_flags = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))[
        "boundary_flag_cases"
    ]
    mapper = TaxonomyMapper(REPO_ROOT / "config")

    # Case 1 & 2: Negative equity + Zero interest expense (debt-free firm with negative equity).
    def _case_facts(
        issuer: str,
        year: int,
        equity: float,
        interest: float,
        *,
        fiscal_start: date | None = None,
    ) -> pl.DataFrame:
        fs = fiscal_start or date(year, 1, 1)
        bs_meta = FilingMetadata(
            issuer_id=issuer,
            ticker_at_filing=issuer,
            statement_type=StatementType.BALANCE_SHEET,
            period_type=PeriodType.ANNUAL,
            fiscal_year=year,
            fiscal_year_start_date=fs,
            period_start_date=fs,
            period_end_date=date(year, 12, 31),
            statement_scope=StatementScope.CONSOLIDATED,
            published_at=date(year + 1, 3, 20),
            audit_status=AuditStatus.AUDITED,
            audit_opinion=AuditOpinion.UNMODIFIED,
        )
        is_meta = FilingMetadata(
            issuer_id=issuer,
            ticker_at_filing=issuer,
            statement_type=StatementType.INCOME_STATEMENT,
            period_type=PeriodType.ANNUAL,
            fiscal_year=year,
            fiscal_year_start_date=fs,
            period_start_date=fs,
            period_end_date=date(year, 12, 31),
            statement_scope=StatementScope.CONSOLIDATED,
            published_at=date(year + 1, 3, 20),
            audit_status=AuditStatus.AUDITED,
            audit_opinion=AuditOpinion.UNMODIFIED,
        )
        m_bs, _ = mapper.map_metrics(
            {
                "tongcongtaisan": 1_000_000_000.0,
                "nophaitra": 1_000_000_000.0 - equity,
                "vonchusohuu_tong": equity,
                "taisannganhan": 600_000_000.0,
                "taisandaihan": 400_000_000.0,
                "hangtonkho_tong": 200_000_000.0,
            },
            bs_meta,
            filing_id=f"{issuer}-{year}-bs",
            source_provider="fhsc",
        )
        m_is, _ = mapper.map_metrics(
            {
                "doanhthuthuanvebanhangvacungcapdichvu": 900_000_000.0,
                "giavonhangban": 500_000_000.0,
                "tongloinhuanketoantruocthue": 100_000_000.0,
                "trongdochiphilaivay": interest,
                "loinhuansauthuethunhapdoanhnghiep": 80_000_000.0,
            },
            is_meta,
            filing_id=f"{issuer}-{year}-is",
            source_provider="fhsc",
        )
        return pl.concat([m_bs, m_is])

    facts = pl.concat(
        [
            _case_facts("EDGE1", 2024, equity=-50_000_000.0, interest=0.0),
            _case_facts("EDGE1", 2025, equity=-30_000_000.0, interest=0.0),
            # Case 3: Cross TT200 (2025) -> TT99 (2026) on non-comparable ebit_growth_yoy
            # (IS_INTEREST_EXPENSE code 23 vs IS_BORROWING_COSTS code 24 stay comparable=false).
            _case_facts("EDGE2", 2025, equity=500_000_000.0, interest=10_000_000.0),
            _case_facts("EDGE2", 2026, equity=550_000_000.0, interest=10_000_000.0),
        ]
    )
    issuers = pl.DataFrame(
        {
            "issuer_id": ["EDGE1", "EDGE2"],
            "ticker": ["EDGE1", "EDGE2"],
            "exchange": ["HOSE", "HOSE"],
            "company_name_vn": ["Edge Corp 1", "Edge Corp 2"],
            "accounting_regime": ["CORPORATE", "CORPORATE"],
            "icb_code_level2": ["5000", "5000"],
            "icb_name_level2": ["Industrials", "Industrials"],
            "icb_code_level3": ["5500", "5500"],
            "icb_name_level3": ["Construction", "Construction"],
            "industry_snapshot_date": [date(2025, 1, 1), date(2025, 1, 1)],
            "delisted_date": [None, None],
        }
    )

    ratios = build_analytics_tables(facts, issuers, as_of=date(2027, 5, 1))[
        RATIOS_TABLE
    ]

    edge1_2025 = ratios.filter(
        (pl.col("issuer_id") == "EDGE1") & (pl.col("fiscal_year") == 2025)
    ).row(0, named=True)
    assert (
        edge1_2025["roe_total"]
        is golden_flags["negative_equity_case"]["expected_value"]
    )
    assert (
        edge1_2025["roe_total_flag"]
        == golden_flags["negative_equity_case"]["expected_flag"]
    )
    assert (
        edge1_2025["interest_coverage"]
        is golden_flags["no_interest_expense_case"]["expected_value"]
    )
    assert (
        edge1_2025["interest_coverage_flag"]
        == golden_flags["no_interest_expense_case"]["expected_flag"]
    )

    edge2_2026 = ratios.filter(
        (pl.col("issuer_id") == "EDGE2") & (pl.col("fiscal_year") == 2026)
    ).row(0, named=True)
    assert (
        edge2_2026["ebit_growth_yoy"]
        is golden_flags["mixed_taxonomy_case"]["expected_value"]
    )
    assert (
        edge2_2026["ebit_growth_yoy_flag"]
        == golden_flags["mixed_taxonomy_case"]["expected_flag"]
    )
