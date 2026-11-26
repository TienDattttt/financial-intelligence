"""Unit tests for Stage 3: cross-provider coalescing in periods.py and Vietcap IQ supplementary facts."""

from __future__ import annotations

import json
from datetime import date

import polars as pl
import pytest

from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.ratios import calculate_ratios
from finintel.analytics.tables import build_analytics_tables
from finintel.ingestion.vietcap_supplement import (
    parse_vietcap_annual_eps_facts,
    parse_vietcap_depreciation_facts,
)
from finintel.ml.classical_scores import add_classical_scores
from finintel.normalization.mapper import FACT_SCHEMA
from finintel.normalization.periods import (
    coalesce_cross_provider_facts,
    compute_ttm,
    derive_quarters,
)
from finintel.storage.schemas import RATIOS_TABLE


def _make_fact(
    *,
    issuer_id: str = "DHC",
    source_provider: str = "fhsc",
    filing_id: str = "fhsc_f1",
    statement_type: str = "CASH_FLOW",
    period_type: str = "QUARTER",
    fiscal_year: int = 2024,
    fiscal_quarter: int | None = 1,
    fy_start: date = date(2024, 1, 1),
    p_start: date = date(2024, 1, 1),
    p_end: date = date(2024, 3, 31),
    metric_id: str = "CF_DEPRECIATION_AMORTIZATION",
    value: float | None = 10_000_000_000.0,
    value_type: str = "FLOW",
    unit: str = "VND",
    sign_convention: str = "POSITIVE_MAGNITUDE",
    comparable: bool = True,
    audit_status: str = "UNAUDITED",
    published_at: date | None = date(2024, 4, 20),
    published_at_basis: str = "EXACT_FILING",
    effective_available_at: date = date(2024, 4, 20),
    revision_no: int = 1,
) -> dict[str, object]:
    return {
        "filing_id": filing_id,
        "issuer_id": issuer_id,
        "ticker_at_filing": issuer_id,
        "source_provider": source_provider,
        "statement_type": statement_type,
        "statement_scope": "CONSOLIDATED",
        "period_type": period_type,
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "fiscal_year_start_date": fy_start,
        "period_start_date": p_start,
        "period_end_date": p_end,
        "period_days": (p_end - p_start).days + 1,
        "taxonomy_version": "VAS_TT200_2014",
        "unit_multiplier": 1.0,
        "audit_status": audit_status,
        "audit_opinion": "UNMODIFIED"
        if audit_status == "AUDITED"
        else "NOT_APPLICABLE",
        "revision_no": revision_no,
        "revision_reason": "ORIGINAL",
        "published_at": published_at,
        "published_at_basis": published_at_basis,
        "effective_available_at": effective_available_at,
        "is_filed_late": False,
        "source_filing_ids": [filing_id],
        "metric_id": metric_id,
        "value_type": value_type,
        "unit": unit,
        "sign_convention": sign_convention,
        "comparable_across_taxonomy": comparable,
        "raw_metric_key": metric_id,
        "value": value,
        "derivation_method": "REPORTED_DIRECTLY",
    }


def test_coalesce_cross_provider_prefers_non_null_and_primary_provider() -> None:
    rows = [
        # Slot 1: fhsc is NULL, vietcap_iq has real value -> vietcap_iq wins
        _make_fact(
            source_provider="fhsc",
            filing_id="fhsc_q1",
            fiscal_quarter=1,
            p_start=date(2024, 1, 1),
            p_end=date(2024, 3, 31),
            value=None,
            effective_available_at=date(2024, 4, 25),
        ),
        _make_fact(
            source_provider="vietcap_iq",
            filing_id="vci_q1",
            fiscal_quarter=1,
            p_start=date(2024, 1, 1),
            p_end=date(2024, 3, 31),
            value=25_000_000_000.0,
            effective_available_at=date(2024, 4, 20),
        ),
        # Slot 2: both fhsc and vietcap_iq non-null -> fhsc (priority 0) wins
        _make_fact(
            source_provider="fhsc",
            filing_id="fhsc_q2",
            fiscal_quarter=2,
            p_start=date(2024, 4, 1),
            p_end=date(2024, 6, 30),
            value=26_000_000_000.0,
            effective_available_at=date(2024, 7, 20),
        ),
        _make_fact(
            source_provider="vietcap_iq",
            filing_id="vci_q2",
            fiscal_quarter=2,
            p_start=date(2024, 4, 1),
            p_end=date(2024, 6, 30),
            value=25_900_000_000.0,
            effective_available_at=date(2024, 7, 28),
        ),
    ]
    df = pl.DataFrame(rows, schema=FACT_SCHEMA)
    out = coalesce_cross_provider_facts(df).sort("fiscal_quarter")
    assert out.height == 2
    assert out["filing_id"].to_list() == ["vci_q1", "fhsc_q2"]
    assert out["value"].to_list() == pytest.approx([25_000_000_000.0, 26_000_000_000.0])


def test_cross_provider_ttm_sums_across_providers_and_preserves_filing_lineage() -> (
    None
):
    # Q1..Q3 from vietcap_iq, Q4 from fhsc -> TTM must sum all 4 quarters and keep all 4 filing IDs.
    q_dates = [
        (
            1,
            date(2024, 1, 1),
            date(2024, 3, 31),
            date(2024, 4, 20),
            "vietcap_iq",
            "vci_q1",
            10.0,
        ),
        (
            2,
            date(2024, 4, 1),
            date(2024, 6, 30),
            date(2024, 7, 20),
            "vietcap_iq",
            "vci_q2",
            20.0,
        ),
        (
            3,
            date(2024, 7, 1),
            date(2024, 9, 30),
            date(2024, 10, 20),
            "vietcap_iq",
            "vci_q3",
            30.0,
        ),
        (
            4,
            date(2024, 10, 1),
            date(2024, 12, 31),
            date(2025, 1, 25),
            "fhsc",
            "fhsc_q4",
            40.0,
        ),
    ]
    rows = [
        _make_fact(
            source_provider=prov,
            filing_id=fid,
            fiscal_quarter=q,
            p_start=ps,
            p_end=pe,
            published_at=pub,
            effective_available_at=pub,
            value=val,
        )
        for q, ps, pe, pub, prov, fid, val in q_dates
    ]
    ttm = compute_ttm(pl.DataFrame(rows, schema=FACT_SCHEMA)).sort("fiscal_quarter")
    q4 = ttm.row(-1, named=True)
    assert q4["ttm_value"] == pytest.approx(100.0)
    assert q4["ttm_exact_filing"] is True
    assert set(q4["ttm_source_filing_ids"]) == {"vci_q1", "vci_q2", "vci_q3", "fhsc_q4"}


def test_parse_vietcap_depreciation_skips_zero_falls_back_to_cf_and_derives_q4() -> (
    None
):
    # Mimics BCF: NOTE has Q1, Q2, Q3 and Y2024 (AUDITED), with Q4 = 0.0 (undisclosed -> skipped).
    note_payload = {
        "successful": True,
        "data": {
            "years": [
                {
                    "yearReport": 2024,
                    "lengthReport": 5,
                    "publicDate": "2025-03-25T00:00:00",
                    "noc143": 100.0,
                }
            ],
            "quarters": [
                {
                    "yearReport": 2024,
                    "lengthReport": 1,
                    "publicDate": "2024-04-20T00:00:00",
                    "noc143": 20.0,
                },
                {
                    "yearReport": 2024,
                    "lengthReport": 2,
                    "publicDate": "2024-07-20T00:00:00",
                    "noc143": 0.0,
                },
                {
                    "yearReport": 2024,
                    "lengthReport": 3,
                    "publicDate": "2024-10-20T00:00:00",
                    "noc143": 30.0,
                },
                {
                    "yearReport": 2024,
                    "lengthReport": 4,
                    "publicDate": "2025-01-25T00:00:00",
                    "noc143": 0.0,
                },
            ],
        },
    }
    # CASH_FLOW fills Q2 via cfa2=25.0 while Q4 stays 0.0 -> Q4 must be derived as 100 - (20+25+30) = 25.0
    cf_payload = {
        "successful": True,
        "data": {
            "years": [],
            "quarters": [
                {
                    "yearReport": 2024,
                    "lengthReport": 2,
                    "publicDate": "2024-07-20T00:00:00",
                    "cfa2": 25.0,
                },
                {
                    "yearReport": 2024,
                    "lengthReport": 4,
                    "publicDate": "2025-01-25T00:00:00",
                    "cfa2": 0.0,
                },
            ],
        },
    }
    dep_facts = parse_vietcap_depreciation_facts(
        issuer_id="BCF",
        ticker="BCF",
        note_bytes=json.dumps(note_payload).encode("utf-8"),
        cash_flow_bytes=json.dumps(cf_payload).encode("utf-8"),
    )
    assert dep_facts.schema == FACT_SCHEMA
    assert dep_facts.height == 4  # Y2024 + Q1 + Q2(cf) + Q3; Q4=0.0 skipped
    derived = derive_quarters(dep_facts).sort("fiscal_quarter")
    assert derived["fiscal_quarter"].to_list() == [1, 2, 3, 4]
    assert derived["value"].to_list() == pytest.approx([20.0, 25.0, 30.0, 25.0])
    assert (
        derived.row(-1, named=True)["derivation_method"]
        == "DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX"
    )


def test_parse_vietcap_annual_eps_and_direct_cf_unlock_in_analytics() -> None:
    is_payload = {
        "successful": True,
        "data": {
            "years": [
                {
                    "yearReport": 2024,
                    "lengthReport": 5,
                    "publicDate": "2025-03-20T00:00:00",
                    "isa23": 3456.0,
                },
            ],
            "quarters": [
                {
                    "yearReport": 2024,
                    "lengthReport": 1,
                    "publicDate": "2024-04-20T00:00:00",
                    "isa23": 800.0,
                },
            ],
        },
    }
    eps_facts = parse_vietcap_annual_eps_facts(
        issuer_id="DHC",
        ticker="DHC",
        income_statement_bytes=json.dumps(is_payload).encode("utf-8"),
    )
    assert eps_facts.schema == FACT_SCHEMA
    assert eps_facts.height == 1
    assert eps_facts.row(0, named=True)["period_type"] == "ANNUAL"
    assert eps_facts.row(0, named=True)["value"] == pytest.approx(3456.0)

    # Minimal 2-year annual FHSC Direct-CF issuer (no depreciation, null EPS) + Vietcap supplement
    issuers = pl.DataFrame(
        {
            "issuer_id": ["DHC"],
            "ticker": ["DHC"],
            "exchange": ["HOSE"],
            "company_name_vn": ["Dong Hai Ben Tre"],
            "accounting_regime": ["CORPORATE"],
            "icb_code_level2": ["1700"],
            "icb_name_level2": ["Tài nguyên cơ bản"],
            "icb_code_level3": ["1737"],
            "icb_name_level3": ["Giấy"],
            "industry_snapshot_date": [date(2020, 1, 1)],
            "delisted_date": [None],
        },
        schema_overrides={"delisted_date": pl.Date},
    )
    fhsc_rows: list[dict[str, object]] = []
    for fy in (2023, 2024):
        fid = f"fhsc_dhc_{fy}Y"
        pub = date(fy + 1, 3, 25)
        for mid, stype, vtype, val in (
            ("BS_TOTAL_ASSETS", "BALANCE_SHEET", "STOCK", 3_000_000.0),
            ("BS_CURRENT_ASSETS", "BALANCE_SHEET", "STOCK", 1_200_000.0),
            ("BS_TRADE_RECEIVABLES_ST", "BALANCE_SHEET", "STOCK", 400_000.0),
            ("BS_TANGIBLE_FIXED_ASSETS", "BALANCE_SHEET", "STOCK", 1_000_000.0),
            ("BS_CURRENT_LIABILITIES", "BALANCE_SHEET", "STOCK", 800_000.0),
            ("BS_LONG_TERM_BORROWINGS", "BALANCE_SHEET", "STOCK", 300_000.0),
            ("BS_SHORT_TERM_BORROWINGS", "BALANCE_SHEET", "STOCK", 100_000.0),
            ("BS_CASH_AND_EQUIVALENTS", "BALANCE_SHEET", "STOCK", 200_000.0),
            ("BS_TOTAL_LIABILITIES", "BALANCE_SHEET", "STOCK", 1_200_000.0),
            ("BS_TOTAL_EQUITY", "BALANCE_SHEET", "STOCK", 1_800_000.0),
            ("BS_MINORITY_INTEREST", "BALANCE_SHEET", "STOCK", 0.0),
            ("IS_NET_REVENUE", "INCOME_STATEMENT", "FLOW", 3_500_000.0),
            ("IS_COST_OF_GOODS_SOLD", "INCOME_STATEMENT", "FLOW", -2_800_000.0),
            ("IS_GROSS_PROFIT", "INCOME_STATEMENT", "FLOW", 700_000.0),
            ("IS_SELLING_EXPENSES", "INCOME_STATEMENT", "FLOW", -100_000.0),
            ("IS_ADMINISTRATIVE_EXPENSES", "INCOME_STATEMENT", "FLOW", -100_000.0),
            ("IS_OPERATING_PROFIT", "INCOME_STATEMENT", "FLOW", 500_000.0),
            ("IS_PROFIT_BEFORE_TAX", "INCOME_STATEMENT", "FLOW", 480_000.0),
            ("IS_INTEREST_EXPENSE", "INCOME_STATEMENT", "FLOW", -20_000.0),
            ("IS_CURRENT_INCOME_TAX", "INCOME_STATEMENT", "FLOW", -80_000.0),
            ("IS_NET_INCOME", "INCOME_STATEMENT", "FLOW", 400_000.0),
            ("IS_NET_INCOME_PARENT", "INCOME_STATEMENT", "FLOW", 400_000.0),
            ("CF_OPERATING_CASH_FLOW", "CASH_FLOW", "FLOW", 450_000.0),
        ):
            fhsc_rows.append(
                _make_fact(
                    issuer_id="DHC",
                    source_provider="fhsc",
                    filing_id=fid,
                    statement_type=stype,
                    period_type="ANNUAL",
                    fiscal_year=fy,
                    fiscal_quarter=None,
                    fy_start=date(fy, 1, 1),
                    p_start=date(fy, 1, 1),
                    p_end=date(fy, 12, 31),
                    metric_id=mid,
                    value=val,
                    value_type=vtype,
                    audit_status="AUDITED",
                    published_at=pub,
                    effective_available_at=pub,
                )
            )
        # Null FHSC EPS row to confirm Vietcap non-null EPS overrides null FHSC EPS
        fhsc_rows.append(
            _make_fact(
                issuer_id="DHC",
                source_provider="fhsc",
                filing_id=fid,
                statement_type="INCOME_STATEMENT",
                period_type="ANNUAL",
                fiscal_year=fy,
                fiscal_quarter=None,
                fy_start=date(fy, 1, 1),
                p_start=date(fy, 1, 1),
                p_end=date(fy, 12, 31),
                metric_id="IS_EPS_BASIC_REPORTED",
                value=None,
                value_type="NON_ADDITIVE",
                unit="VND_PER_SHARE",
                sign_convention="SIGNED",
                comparable=False,
                audit_status="AUDITED",
                published_at=pub,
                effective_available_at=pub,
            )
        )

    note_2y = {
        "successful": True,
        "data": {
            "years": [
                {
                    "yearReport": 2023,
                    "lengthReport": 5,
                    "publicDate": "2024-03-25T00:00:00",
                    "noc143": 100_000.0,
                },
                {
                    "yearReport": 2024,
                    "lengthReport": 5,
                    "publicDate": "2025-03-25T00:00:00",
                    "noc143": 105_000.0,
                },
            ],
            "quarters": [],
        },
    }
    dep_2y = parse_vietcap_depreciation_facts(
        issuer_id="DHC",
        ticker="DHC",
        note_bytes=json.dumps(note_2y).encode("utf-8"),
    )
    combined = pl.concat(
        [pl.DataFrame(fhsc_rows, schema=FACT_SCHEMA), dep_2y, eps_facts]
    )
    tables = build_analytics_tables(combined, issuers, as_of=date(2025, 6, 1))
    ratios = tables[RATIOS_TABLE].sort("fiscal_year")
    row_2024 = ratios.row(-1, named=True)
    assert row_2024["filing_id"] == "fhsc_dhc_2024Y"  # Primary FHSC anchor preserved
    assert row_2024["eps_basic_reported"] == pytest.approx(3456.0)
    assert row_2024["ebitda_margin"] is not None and row_2024["ebitda_margin"] > 0.0
    assert row_2024["net_debt_to_ebitda"] is not None

    full_ratios = calculate_ratios(
        prepare_periods(
            combined, issuers, as_of=date(2025, 6, 1), include_ml_inputs=True
        )
    )
    scores = add_classical_scores(full_ratios).sort("period_end_date")
    sc_2024 = scores.row(-1, named=True)
    assert sc_2024["beneish_flag"] in ("VALID", "HIGH_MANIPULATION_RISK_SCREEN")
    assert sc_2024["beneish_m8_score"] is not None


def test_vietcap_supplement_skips_orphan_periods_when_existing_period_dates_given() -> (
    None
):
    """When existing_period_dates is supplied, slots absent from primary filings must be skipped."""
    existing = {
        ("ANNUAL", 2024, None): (
            date(2024, 1, 1),
            date(2024, 1, 1),
            date(2024, 12, 31),
            "VAS_TT200_2014",
        ),
        ("QUARTER", 2024, 1): (
            date(2024, 1, 1),
            date(2024, 1, 1),
            date(2024, 3, 31),
            "VAS_TT200_2014",
        ),
    }
    note_payload = {
        "successful": True,
        "data": {
            "years": [
                {"yearReport": 2008, "lengthReport": 5, "noc143": 50.0},
                {"yearReport": 2024, "lengthReport": 5, "noc143": 100.0},
            ],
            "quarters": [
                {"yearReport": 2008, "lengthReport": 1, "noc143": 12.0},
                {"yearReport": 2024, "lengthReport": 1, "noc143": 25.0},
            ],
        },
    }
    is_payload = {
        "successful": True,
        "data": {
            "years": [
                {"yearReport": 2008, "lengthReport": 5, "isa23": 1200.0},
                {"yearReport": 2024, "lengthReport": 5, "isa23": 3500.0},
            ],
            "quarters": [],
        },
    }
    dep = parse_vietcap_depreciation_facts(
        issuer_id="DHC",
        ticker="DHC",
        note_bytes=json.dumps(note_payload).encode("utf-8"),
        existing_period_dates=existing,
    )
    eps = parse_vietcap_annual_eps_facts(
        issuer_id="DHC",
        ticker="DHC",
        income_statement_bytes=json.dumps(is_payload).encode("utf-8"),
        existing_period_dates=existing,
    )
    assert sorted(dep["fiscal_year"].to_list()) == [2024, 2024]
    assert eps["fiscal_year"].to_list() == [2024]
