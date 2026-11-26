"""Unit tests for Wave 1 multi-provider integrity fixes (Critical #1, #2, #3)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from finintel.ingestion.vietcap_supplement import (
    parse_vietcap_anchor_facts,
    parse_vietcap_annual_eps_facts,
    parse_vietcap_depreciation_facts,
)
from finintel.normalization.mapper import FACT_SCHEMA
from finintel.normalization.supplement_merge import (
    harmonize_stub_fiscal_year_start_dates,
    merge_vietcap_supplement_facts,
)
from finintel.pipeline.state_store import StateStore
from finintel.quality.gate import evaluate_quality_gate
from finintel.storage.manifest_export import (
    build_filings_manifest_snapshot,
    build_latest_facts_table,
)
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import FILINGS_MANIFEST_SCHEMA


def _fact_row(
    *,
    issuer_id: str = "AAA",
    source_provider: str = "fhsc",
    filing_id: str = "0001_fhsc",
    statement_type: str = "CASH_FLOW",
    period_type: str = "ANNUAL",
    fiscal_year: int = 2024,
    fiscal_quarter: int | None = None,
    fy_start: date = date(2024, 1, 1),
    p_start: date = date(2024, 1, 1),
    p_end: date = date(2024, 12, 31),
    metric_id: str = "CF_DEPRECIATION_AMORTIZATION",
    value: float | None = 100_000_000_000.0,
    value_type: str = "FLOW",
    unit: str = "VND",
    sign_convention: str = "POSITIVE_MAGNITUDE",
    comparable: bool = True,
    audit_status: str = "AUDITED",
    published_at: date | None = date(2025, 3, 20),
    published_at_basis: str = "EXACT_FILING",
    effective_available_at: date = date(2025, 3, 20),
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


def test_latest_facts_and_repo_prefer_fhsc_over_vietcap_and_preserve_null_restatement(
    tmp_path: Path,
) -> None:
    rows = [
        # 1. Both FHSC (EXACT_FILING Mar 20) and Vietcap (STATUTORY_LAG Mar 31, 'vci_' > '0001_')
        # have non-null depreciation -> FHSC MUST win in both latest_facts and get_facts_as_of!
        _fact_row(
            filing_id="0001_fhsc_dep",
            source_provider="fhsc",
            metric_id="CF_DEPRECIATION_AMORTIZATION",
            value=120_000_000_000.0,
            published_at=date(2025, 3, 20),
            published_at_basis="EXACT_FILING",
            effective_available_at=date(2025, 3, 20),
        ),
        _fact_row(
            filing_id="vci_note_AAA_2024Y_abcdef123456",
            source_provider="vietcap_iq",
            metric_id="CF_DEPRECIATION_AMORTIZATION",
            value=118_500_000_000.0,
            published_at=None,
            published_at_basis="STATUTORY_LAG",
            effective_available_at=date(2025, 3, 31),
        ),
        # 2. FHSC has NULL EPS, Vietcap has non-null EPS -> Vietcap wins!
        _fact_row(
            filing_id="0001_fhsc_eps",
            source_provider="fhsc",
            statement_type="INCOME_STATEMENT",
            metric_id="IS_EPS_BASIC_REPORTED",
            value=None,
            value_type="NON_ADDITIVE",
            unit="VND_PER_SHARE",
            sign_convention="SIGNED",
            comparable=False,
        ),
        _fact_row(
            filing_id="vci_is_AAA_2024Y_abcdef123456",
            source_provider="vietcap_iq",
            statement_type="INCOME_STATEMENT",
            metric_id="IS_EPS_BASIC_REPORTED",
            value=2_450.0,
            value_type="NON_ADDITIVE",
            unit="VND_PER_SHARE",
            sign_convention="SIGNED",
            comparable=False,
        ),
        # 3. Within-provider restatement to NULL (no Vietcap fallback) -> NULL wins!
        _fact_row(
            filing_id="0001_fhsc_rev1",
            source_provider="fhsc",
            statement_type="INCOME_STATEMENT",
            metric_id="IS_OTHER_INCOME",
            value=5_000_000_000.0,
            revision_no=1,
            published_at=date(2025, 3, 15),
            effective_available_at=date(2025, 3, 15),
        ),
        _fact_row(
            filing_id="0002_fhsc_rev2_null",
            source_provider="fhsc",
            statement_type="INCOME_STATEMENT",
            metric_id="IS_OTHER_INCOME",
            value=None,
            revision_no=2,
            published_at=date(2025, 3, 28),
            effective_available_at=date(2025, 3, 28),
        ),
    ]
    facts = pl.DataFrame(rows, schema=FACT_SCHEMA)
    latest = build_latest_facts_table(facts, as_of=date(2025, 4, 15)).sort("metric_id")
    by_metric = {r["metric_id"]: r for r in latest.to_dicts()}

    assert by_metric["CF_DEPRECIATION_AMORTIZATION"]["source_provider"] == "fhsc"
    assert by_metric["CF_DEPRECIATION_AMORTIZATION"]["value"] == pytest.approx(
        120_000_000_000.0
    )
    assert (
        by_metric["CF_DEPRECIATION_AMORTIZATION"]["published_at_basis"]
        == "EXACT_FILING"
    )
    assert by_metric["IS_EPS_BASIC_REPORTED"]["source_provider"] == "vietcap_iq"
    assert by_metric["IS_EPS_BASIC_REPORTED"]["value"] == pytest.approx(2_450.0)
    assert by_metric["IS_OTHER_INCOME"]["filing_id"] == "0002_fhsc_rev2_null"
    assert by_metric["IS_OTHER_INCOME"]["value"] is None

    pq_path = tmp_path / "financial_facts.parquet"
    facts.write_parquet(pq_path)
    repo_facts = ParquetRepository(pq_path).get_facts_as_of(date(2025, 4, 15))
    repo_by_metric = {r["metric_id"]: r for r in repo_facts.to_dicts()}
    assert repo_by_metric["CF_DEPRECIATION_AMORTIZATION"]["source_provider"] == "fhsc"
    assert repo_by_metric["IS_EPS_BASIC_REPORTED"]["source_provider"] == "vietcap_iq"
    assert repo_by_metric["IS_OTHER_INCOME"]["value"] is None


def test_filings_manifest_snapshot_exports_vietcap_iq_lineage_rows(
    tmp_path: Path,
) -> None:
    state = StateStore(tmp_path / "metadata" / "state.sqlite")
    state.initialize()
    facts = pl.DataFrame(
        [
            _fact_row(
                filing_id="vci_note_AAA_2024Y_abcdef123456",
                source_provider="vietcap_iq",
                metric_id="CF_DEPRECIATION_AMORTIZATION",
                value=88_000_000_000.0,
            )
        ],
        schema=FACT_SCHEMA,
    )
    manifest = build_filings_manifest_snapshot(state, facts)
    assert manifest.schema == FILINGS_MANIFEST_SCHEMA
    assert manifest.height == 1
    row = manifest.row(0, named=True)
    assert row["filing_id"] == "vci_note_AAA_2024Y_abcdef123456"
    assert row["source_provider"] == "vietcap_iq"
    assert len(row["canonical_content_sha256"]) == 64
    assert len(row["payload_file_sha256"]) == 64


def test_vietcap_supplement_skips_non_calendar_annual_and_emits_anchor_reconciliation() -> (
    None
):
    # SBT non-calendar ANNUAL (2025-07-01 .. 2026-06-30) vs calendar QUARTER 2025Q1 (2025-03-31)
    sbt_periods = {
        ("ANNUAL", 2025, None): (
            date(2025, 7, 1),
            date(2025, 7, 1),
            date(2026, 6, 30),
            "VAS_TT99_2025",
        ),
        ("QUARTER", 2025, 1): (
            date(2025, 1, 1),
            date(2025, 1, 1),
            date(2025, 3, 31),
            "VAS_TT99_2025",
        ),
    }
    is_payload = {
        "successful": True,
        "data": {
            "years": [
                {
                    "yearReport": 2025,
                    "lengthReport": 5,
                    "publicDate": "2026-03-13T00:00:00",
                    "isa3": 26_228_397_220_258.0,
                    "isa22": 739_288_736_135.0,
                    "isa23": 783.0,
                }
            ],
            "quarters": [
                {
                    "yearReport": 2025,
                    "lengthReport": 1,
                    "publicDate": "2025-04-29T00:00:00",
                    "isa3": 7_500_000_000_000.0,
                    "isa22": 180_000_000_000.0,
                }
            ],
        },
    }
    note_payload = {
        "successful": True,
        "data": {
            "years": [
                {
                    "yearReport": 2025,
                    "lengthReport": 5,
                    "publicDate": "2026-03-13T00:00:00",
                    "noc143": 529_048_277_534.0,
                }
            ],
            "quarters": [
                {
                    "yearReport": 2025,
                    "lengthReport": 1,
                    "publicDate": "2025-04-29T00:00:00",
                    "noc143": 129_864_424_157.0,
                }
            ],
        },
    }
    bs_payload = {
        "successful": True,
        "data": {
            "years": [
                {
                    "yearReport": 2025,
                    "lengthReport": 5,
                    "publicDate": "2026-03-13T00:00:00",
                    "bsa53": 35_898_659_501_110.0,
                    "bsa78": 12_210_486_500_367.0,
                }
            ],
            "quarters": [
                {
                    "yearReport": 2025,
                    "lengthReport": 1,
                    "publicDate": "2025-04-29T00:00:00",
                    "bsa53": 34_198_411_622_964.0,
                    "bsa78": 11_500_000_000_000.0,
                }
            ],
        },
    }
    eps = parse_vietcap_annual_eps_facts(
        issuer_id="SBT",
        ticker="SBT",
        income_statement_bytes=json.dumps(is_payload).encode("utf-8"),
        existing_period_dates=sbt_periods,
    )
    dep = parse_vietcap_depreciation_facts(
        issuer_id="SBT",
        ticker="SBT",
        note_bytes=json.dumps(note_payload).encode("utf-8"),
        existing_period_dates=sbt_periods,
    )
    anchors = parse_vietcap_anchor_facts(
        issuer_id="SBT",
        ticker="SBT",
        balance_sheet_bytes=json.dumps(bs_payload).encode("utf-8"),
        income_statement_bytes=json.dumps(is_payload).encode("utf-8"),
        existing_period_dates=sbt_periods,
    )
    # Non-calendar ANNUAL 2026-06-30 MUST be skipped across all 3 parsers!
    assert eps.is_empty()
    assert dep["period_type"].to_list() == ["QUARTER"]
    assert set(anchors["period_type"].to_list()) == {"QUARTER"}
    assert set(anchors["metric_id"].to_list()) == {
        "BS_TOTAL_ASSETS",
        "BS_TOTAL_EQUITY",
        "IS_NET_REVENUE",
        "IS_NET_INCOME_PARENT",
    }

    # Verify QG1 emits CROSS_PROVIDER_DISCREPANCY_WARN when anchor differs >0.5%
    issuers = pl.DataFrame(
        {
            "issuer_id": ["SBT"],
            "ticker": ["SBT"],
            "exchange": ["HOSE"],
            "company_name_vn": ["SBT"],
            "accounting_regime": ["CORPORATE"],
            "icb_code_level2": ["3500"],
            "icb_name_level2": ["Thực phẩm"],
            "icb_code_level3": ["3570"],
            "icb_name_level3": ["Sản xuất thực phẩm"],
            "industry_snapshot_date": [date(2020, 1, 1)],
            "delisted_date": [None],
        },
        schema_overrides={"delisted_date": pl.Date},
    )
    fhsc_q1 = pl.DataFrame(
        [
            _fact_row(
                issuer_id="SBT",
                filing_id="fhsc_sbt_25q1",
                statement_type="BALANCE_SHEET",
                period_type="QUARTER",
                fiscal_year=2025,
                fiscal_quarter=1,
                fy_start=date(2025, 1, 1),
                p_start=date(2025, 1, 1),
                p_end=date(2025, 3, 31),
                metric_id=mid,
                value=val,
                value_type="STOCK" if mid.startswith("BS_") else "FLOW",
                audit_status="UNAUDITED",
                published_at=date(2025, 4, 29),
                effective_available_at=date(2025, 4, 29),
            )
            for mid, val in (
                ("BS_TOTAL_ASSETS", 34_198_411_622_964.0),
                ("BS_TOTAL_LIABILITIES", 22_698_411_622_964.0),
                ("BS_TOTAL_EQUITY", 11_500_000_000_000.0),
                ("IS_NET_REVENUE", 7_800_000_000_000.0),  # >0.5% diff vs 7.5e12
                ("IS_NET_INCOME", 180_000_000_000.0),
                ("IS_NET_INCOME_PARENT", 180_000_000_000.0),
            )
        ],
        schema=FACT_SCHEMA,
    ).with_columns(pl.lit("VAS_TT99_2025").alias("taxonomy_version"))
    qg = evaluate_quality_gate(
        pl.concat([fhsc_q1, anchors]), issuers=issuers, run_id="test_qg_anchor"
    )
    info_codes = qg.quality_summary.row(0, named=True)["info_codes"]
    assert "CROSS_PROVIDER_DISCREPANCY_WARN" in info_codes


def test_merge_vietcap_supplement_facts_semi_joins_and_harmonizes_stub_years(
    tmp_path: Path,
) -> None:
    curated = tmp_path / "curated"
    curated.mkdir(parents=True)
    # JVC-like stub period where BS has Jan 1 fy_start and IS has Apr 1 fy_start
    primary = pl.DataFrame(
        [
            _fact_row(
                issuer_id="JVC",
                filing_id="jvc_bs",
                statement_type="BALANCE_SHEET",
                metric_id="BS_TOTAL_ASSETS",
                value=500_000_000_000.0,
                value_type="STOCK",
                fy_start=date(2021, 1, 1),
                p_start=date(2021, 1, 1),
                p_end=date(2021, 12, 31),
                fiscal_year=2021,
            ),
            _fact_row(
                issuer_id="JVC",
                filing_id="jvc_is",
                statement_type="INCOME_STATEMENT",
                metric_id="IS_NET_REVENUE",
                value=300_000_000_000.0,
                value_type="FLOW",
                fy_start=date(2021, 4, 1),
                p_start=date(2021, 4, 1),
                p_end=date(2021, 12, 31),
                fiscal_year=2021,
            ),
        ],
        schema=FACT_SCHEMA,
    )
    supp = pl.DataFrame(
        [
            _fact_row(
                issuer_id="JVC",
                source_provider="vietcap_iq",
                filing_id="vci_jvc_2021",
                metric_id="CF_DEPRECIATION_AMORTIZATION",
                value=15_000_000_000.0,
                fy_start=date(2021, 1, 1),
                p_start=date(2021, 1, 1),
                p_end=date(2021, 12, 31),
                fiscal_year=2021,
            ),
            # Orphan 2008 period absent from primary -> must be dropped by semi-join
            _fact_row(
                issuer_id="JVC",
                source_provider="vietcap_iq",
                filing_id="vci_jvc_2008",
                metric_id="CF_DEPRECIATION_AMORTIZATION",
                value=9_000_000_000.0,
                fy_start=date(2008, 1, 1),
                p_start=date(2008, 1, 1),
                p_end=date(2008, 12, 31),
                fiscal_year=2008,
            ),
        ],
        schema=FACT_SCHEMA,
    )
    supp.write_parquet(curated / "vietcap_supplement_facts.parquet")

    merged = merge_vietcap_supplement_facts(primary, curated_root=curated)
    assert merged.height == 3
    assert set(merged["fiscal_year_start_date"].to_list()) == {date(2021, 4, 1)}
    assert set(merged["period_start_date"].to_list()) == {date(2021, 4, 1)}
    _, changed = harmonize_stub_fiscal_year_start_dates(merged)
    assert changed == 0


def test_q2_reviewed_fhsc_vs_unaudited_vietcap_preserves_bs_identity_and_reconciles() -> (
    None
):
    issuers = pl.DataFrame(
        {
            "issuer_id": ["HPG"],
            "ticker": ["HPG"],
            "exchange": ["HOSE"],
            "company_name_vn": ["Hoa Phat"],
            "accounting_regime": ["CORPORATE"],
            "icb_code_level2": ["1700"],
            "icb_name_level2": ["Tài nguyên"],
            "icb_code_level3": ["1750"],
            "icb_name_level3": ["Thép"],
            "industry_snapshot_date": [date(2020, 1, 1)],
            "delisted_date": [None],
        },
        schema_overrides={"delisted_date": pl.Date},
    )
    fhsc_q2 = [
        _fact_row(
            issuer_id="HPG",
            filing_id="0001_fhsc_q2",
            statement_type="BALANCE_SHEET"
            if mid.startswith("BS_")
            else "INCOME_STATEMENT",
            period_type="QUARTER",
            fiscal_year=2025,
            fiscal_quarter=2,
            fy_start=date(2025, 1, 1),
            p_start=date(2025, 4, 1),
            p_end=date(2025, 6, 30),
            metric_id=mid,
            value=val,
            value_type="STOCK" if mid.startswith("BS_") else "FLOW",
            audit_status="REVIEWED",
            published_at=date(2025, 8, 25),
            effective_available_at=date(2025, 8, 25),
        )
        for mid, val in (
            ("BS_TOTAL_ASSETS", 200_000_000_000.0),
            ("BS_TOTAL_LIABILITIES", 80_000_000_000.0),
            ("BS_TOTAL_EQUITY", 120_000_000_000.0),
            ("IS_NET_REVENUE", 50_000_000_000.0),
            ("IS_NET_INCOME", 8_000_000_000.0),
            ("IS_NET_INCOME_PARENT", 8_000_000_000.0),
        )
    ]
    vci_q2 = [
        _fact_row(
            issuer_id="HPG",
            source_provider="vietcap_iq",
            filing_id="vci_bs_HPG_2025Q2_abcdef123456",
            statement_type="BALANCE_SHEET",
            period_type="QUARTER",
            fiscal_year=2025,
            fiscal_quarter=2,
            fy_start=date(2025, 1, 1),
            p_start=date(2025, 4, 1),
            p_end=date(2025, 6, 30),
            metric_id="BS_TOTAL_ASSETS",
            value=195_000_000_000.0,  # 2.5% discrepancy & UNAUDITED
            value_type="STOCK",
            audit_status="UNAUDITED",
            published_at=None,
            published_at_basis="STATUTORY_LAG",
            effective_available_at=date(2025, 8, 30),
        ),
        _fact_row(
            issuer_id="HPG",
            source_provider="vietcap_iq",
            filing_id="vci_note_HPG_2025Q2_abcdef123456",
            statement_type="CASH_FLOW",
            period_type="QUARTER",
            fiscal_year=2025,
            fiscal_quarter=2,
            fy_start=date(2025, 1, 1),
            p_start=date(2025, 4, 1),
            p_end=date(2025, 6, 30),
            metric_id="CF_DEPRECIATION_AMORTIZATION",
            value=3_500_000_000.0,
            value_type="FLOW",
            audit_status="UNAUDITED",
            published_at=date(2025, 8, 25),
            effective_available_at=date(2025, 8, 25),
        ),
    ]
    qg = evaluate_quality_gate(
        pl.DataFrame([*fhsc_q2, *vci_q2], schema=FACT_SCHEMA),
        issuers=issuers,
        run_id="test_q2_rev",
    )
    assert qg.audit_quarantine.is_empty()
    assert (
        "CROSS_PROVIDER_DISCREPANCY_WARN"
        in qg.quality_summary.row(0, named=True)["info_codes"]
    )
    # All historical rows preserved in clean_facts (TASK-05), while latest_facts picks fhsc BS & vietcap CF
    vci_clean = qg.clean_facts.filter(pl.col("source_provider") == "vietcap_iq")
    assert vci_clean.height == 2
    latest_q2 = build_latest_facts_table(qg.clean_facts, as_of=date(2025, 9, 15))
    by_m = {r["metric_id"]: r for r in latest_q2.to_dicts()}
    assert by_m["BS_TOTAL_ASSETS"]["source_provider"] == "fhsc"
    assert by_m["CF_DEPRECIATION_AMORTIZATION"]["source_provider"] == "vietcap_iq"
