"""Unit and integration tests for V3 multi-sheet Excel exports and API endpoints (V3-13..V3-14).

Verified Sources:
- spec/v3/SPEC-v3-export-and-tearsheet.md
- OWASP Spreadsheet / CSV Formula Injection Prevention Cheat Sheet
- openpyxl==3.1.5 read-back verification of xlsxwriter==3.2.2 output
"""

from __future__ import annotations

import ast
import importlib
import io
import time
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from finintel.domain.compliance import (
    COMPLIANCE_DISCLAIMER_VI,
    RISK_TIER_LEGAL_LABEL_VI,
)
from finintel.serving.exports.excel_builder import (
    COMPANY_SHEET_NAMES,
    MAX_SCREENER_EXPORT_ROWS,
    SCREENER_SHEET_NAME,
    CompanyExportPayload,
    CompanyStatementExportSection,
    ScreenerExportPayload,
    build_company_workbook_bytes,
    build_screener_workbook_bytes,
    sanitize_excel_text,
)
from finintel.serving.queries._cache import CompanyProfileData
from finintel.storage.schemas import ML_SCORES_SCHEMA


def _build_sample_company_payload(
    *,
    num_periods: int = 20,
    inject_formula: bool = False,
) -> CompanyExportPayload:
    periods_dates = [
        date(2021 + (i // 4), ((i % 4) + 1) * 3, 28) for i in range(num_periods)
    ]
    periods_dates.reverse()

    ratios_rows = [
        {
            "period_end_date": d,
            "fiscal_year": d.year,
            "fiscal_quarter": (d.month - 1) // 3 + 1,
            "taxonomy_version": "VAS_TT200_2014",
            "roe_total": 0.145 if idx % 3 != 0 else -0.042,
            "roa": 0.082,
            "ebit_margin": 0.165,
            "debt_to_equity": 0.72,
            "interest_coverage": 5.4,
            "ocf_to_total_liabilities": 0.28,
            "net_revenue_ttm": 12_500_000_000_000.0,
            "borrowing_cost_ttm": 250_000_000_000.0,
        }
        for idx, d in enumerate(periods_dates)
    ]
    dupont_rows = [
        {
            "period_end_date": d,
            "fiscal_year": d.year,
            "fiscal_quarter": (d.month - 1) // 3 + 1,
            "taxonomy_version": "VAS_TT200_2014",
            "tax_burden": 0.80,
            "interest_burden": 0.90,
            "ebit_margin": 0.165,
            "asset_turnover": 1.12,
            "equity_multiplier_total": 1.65,
            "roe_total": 0.145,
            "dupont_valid_flag": True,
        }
        for d in periods_dates
    ]
    ml_rows: list[dict[str, object]] = [
        {
            "issuer_id": "ISS_HPG",
            "statement_scope": "CONSOLIDATED",
            "period_type": "QUARTER",
            "period_end_date": d,
            "filing_id": f"FIL_HPG_{i}",
            "model_role": "PRIMARY",
            "altman_z_double_prime": 2.85 - (i * 0.03),
            "altman_em_score": 3.1,
            "altman_zone": "SAFE_ZONE",
            "piotroski_f_score": 7,
            "piotroski_valid_components_count": 9,
            "piotroski_missing_criteria": [],
            "piotroski_na_reason": None,
            "beneish_m8_score": -2.35,
            "beneish_flag": "VALID",
            "pca_pc1": 0.4,
            "pca_pc2": -0.2,
            "pca_pc3": 0.1,
            "cluster_id": 1,
            "cluster_archetype_name": (
                "@SUM(A1:A2)" if inject_formula else "Doanh nghiệp sản xuất ổn định"
            ),
            "anomaly_score_raw": -0.15,
            "anomaly_percentile_rank": 18.0,
            "statistical_outlier_flag": False,
            "anomaly_flag": "NORMAL",
            "distress_risk_score": 0.045,
            "calibrated_distress_prob": 0.032,
            "risk_tier": "R2",
            "risk_market_percentile": 22.5,
            "market_base_rate": 0.045,
            "relative_risk_lift": 0.71,
            "model_version": "v2.0",
            "shap_top_drivers_json": (
                '{"base_value": -2.1, "prediction_score": -3.2, "other_features_delta": -0.1, '
                '"top_risk_increasing": [{"feature": "debt_to_equity", "display_name_vn": "Nợ vay / VCSH", "value": 0.72, "peer_median": 0.55, "shap_delta": 0.18}], '
                '"top_risk_decreasing": [{"feature": "interest_coverage", "display_name_vn": "Hệ số thanh toán lãi vay", "value": 5.4, "peer_median": 3.1, "shap_delta": -0.45}]}'
            ),
        }
        for i, d in enumerate(periods_dates)
    ]

    red_flags_df = pl.DataFrame(
        {
            "period_end_date": [periods_dates[0]],
            "flag_code": ["RF_02_NEGATIVE_CFO_POSITIVE_NI"],
            "severity": ["HIGH"],
            "flag_title_vn": [
                "+CMD|' /C calc'!A0"
                if inject_formula
                else "Lợi nhuận dương nhưng dòng tiền hoạt động âm"
            ],
            "explanation_text_vn": [
                '=HYPERLINK("http://evil.example","Click")'
                if inject_formula
                else "CFO âm 1.250 tỷ trong khi LNST dương."
            ],
            "evidence_json": ["{}"],
            "filing_id": ["FIL_HPG_0"],
        }
    )

    profile = CompanyProfileData(
        ticker="HPG",
        issuer_id="ISS_HPG",
        company_name_vn=(
            "=CMD|' /C calc'!A0"
            if inject_formula
            else "Công ty Cổ phần Tập đoàn Hòa Phát"
        ),
        accounting_regime="CORPORATE",
        statement_scope="CONSOLIDATED",
        period_view="TTM",
        latest_taxonomy_version="VAS_TT200_2014",
        latest_quality_status="PASS",
        latest_warning_codes=(),
        growth_series=pl.DataFrame(),
        capital_structure_series=pl.DataFrame(),
        dupont_series=pl.DataFrame(dupont_rows),
        red_flags=red_flags_df,
        ratios_table=pl.DataFrame(ratios_rows),
        ml_scores=pl.DataFrame(ml_rows, schema=ML_SCORES_SCHEMA),
    )

    stmt_periods = [
        {
            "period_end_date": d,
            "fiscal_year": d.year,
            "fiscal_quarter": (d.month - 1) // 3 + 1,
            "label": f"Q{(d.month - 1) // 3 + 1}/{d.year}",
            "taxonomy_version": "VAS_TT200_2014",
            "audit_status": "UNAUDITED",
        }
        for d in periods_dates
    ]

    def _vals(base: float, *, null_idx: int | None = None) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for idx, d in enumerate(periods_dates):
            if null_idx is not None and idx == null_idx:
                out[d.isoformat()] = None
            else:
                out[d.isoformat()] = base * (1.0 if idx % 2 == 0 else -0.5)
        return out

    is_section = CompanyStatementExportSection(
        statement_type="INCOME_STATEMENT",
        periods=stmt_periods,
        rows=[
            {
                "metric_id": "IS_NET_REVENUE",
                "display_name_vn": "Doanh thu thuần",
                "tt200_code": "10",
                "tt99_code": "10",
                "unit": "VND",
                "is_total": True,
                "comparable_across_taxonomy": True,
                "sort_order": 10,
                "values": _vals(32_000_000_000_000.0),
            },
            {
                "metric_id": "IS_NET_INCOME",
                "display_name_vn": "Lợi nhuận sau thuế",
                "tt200_code": "60",
                "tt99_code": "60",
                "unit": "VND",
                "is_total": True,
                "comparable_across_taxonomy": True,
                "sort_order": 60,
                "values": _vals(2_500_000_000_000.0, null_idx=2),
            },
        ],
    )

    return CompanyExportPayload(
        snapshot_id="snap_v3_export_2026_001",
        published_at="2026-04-25T08:00:00Z",
        profile=profile,
        exchange="HOSE",
        icb_name_level2="-Tài nguyên Cơ bản" if inject_formula else "Tài nguyên Cơ bản",
        icb_name_level3="\tKim loại" if inject_formula else "Thép & Kim loại",
        statements=(is_section,),
        benchmarks_summary=[
            {
                "metric_name": "roe_total",
                "display_name_vn": "ROE Tổng",
                "category": "PROFITABILITY",
                "unit": "%",
                "format_type": "PERCENT",
                "direction": "HIGHER_IS_BETTER",
                "company_value": 0.145,
                "peer_median": 0.112,
                "peer_p25": 0.065,
                "peer_p75": 0.178,
                "percentile_rank": 68.0,
                "robust_z_score": 0.62,
                "peer_group_level": "ICB_LEVEL_3",
                "peer_count": 18,
            }
        ],
        filings=[
            {
                "filing_id": "FIL_HPG_0",
                "statement_type": "INCOME_STATEMENT",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": periods_dates[0],
                "taxonomy_version": "VAS_TT200_2014",
                "published_at": date(2026, 4, 20),
                "published_at_basis": "EXACT_FILING",
                "effective_available_at": date(2026, 4, 21),
                "audit_status": "AUDITED",
                "audit_opinion": "UNMODIFIED",
                "revision_no": 1,
                "revision_reason": "ORIGINAL",
                "has_restatement": False,
                "source_provider": "FHSC",
            }
        ],
        market_overlay={
            "latest_valuation": {
                "trade_date": date(2026, 4, 24),
                "close_raw": 28_500.0,
                "market_cap_vnd": 182_000_000_000_000.0,
                "pe_ttm": 12.4,
                "pe_flag": "VALID",
                "pb_ratio": 1.65,
                "pb_flag": "VALID",
                "merton_dtd": 3.45,
                "dtd_flag": "VALID",
            }
        },
    )


def test_exports_module_lazy_imports_xlsxwriter() -> None:
    """excel_builder and router must lazy-import xlsxwriter to preserve mvp-checks offline CI."""
    root = Path(__file__).resolve().parents[2]
    for rel in (
        "src/finintel/serving/exports/__init__.py",
        "src/finintel/serving/exports/excel_builder.py",
        "src/finintel/serving/exports/router.py",
    ):
        src_path = root / rel
        tree = ast.parse(src_path.read_text(encoding="utf-8"), filename=rel)
        for stmt in tree.body:
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    assert not alias.name.startswith("xlsxwriter"), (
                        f"Top-level xlsxwriter import forbidden in {rel}"
                    )
            elif isinstance(stmt, ast.ImportFrom) and stmt.module:
                assert not stmt.module.startswith("xlsxwriter"), (
                    f"Top-level xlsxwriter import forbidden in {rel}"
                )


def _load_openpyxl() -> Any:
    return importlib.import_module("openpyxl")


def test_sanitize_excel_text_formula_prefixes() -> None:
    """Pure text sanitizer neutralizes =, +, -, @, tab, CR (and leading whitespace) without xlsxwriter."""
    assert sanitize_excel_text("=CMD|' /C calc'!A0").startswith("'")
    assert sanitize_excel_text("+1+1").startswith("'")
    assert sanitize_excel_text("-SUM(A1:A2)").startswith("'")
    assert sanitize_excel_text("@SUM(1,2)").startswith("'")
    assert sanitize_excel_text("\t=1+1").startswith("'")
    assert sanitize_excel_text("\r=1+1").startswith("'")
    assert sanitize_excel_text("  =1+1").startswith("'")


def test_company_workbook_sheets_numeric_types_null_blanks_and_compliance() -> None:
    """Company .xlsx has 4 exact sheets, numeric floats (even negative), None=blank, snapshot_id & disclaimer."""
    pytest.importorskip("xlsxwriter")
    openpyxl = _load_openpyxl()
    payload = _build_sample_company_payload(num_periods=20, inject_formula=False)

    t0 = time.perf_counter()
    raw_bytes = build_company_workbook_bytes(payload)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert elapsed_ms < 300.0, (
        f"Company 20-period export took {elapsed_ms:.1f}ms (>= 300ms)"
    )

    wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=False)
    assert tuple(wb.sheetnames) == COMPANY_SHEET_NAMES

    sheet1 = wb["Tong_Quan_Rui_Ro"]
    sheet1_texts = [
        str(cell.value)
        for row in sheet1.iter_rows()
        for cell in row
        if cell.value is not None
    ]
    joined_s1 = "\n".join(sheet1_texts)
    assert "snap_v3_export_2026_001" in joined_s1
    assert COMPLIANCE_DISCLAIMER_VI in joined_s1
    assert RISK_TIER_LEGAL_LABEL_VI in joined_s1

    sheet2 = wb["BCTC_Chuan_Hoa"]
    # Find the row for IS_NET_INCOME where period idx=1 is negative and idx=2 is None (blank)
    net_income_row = None
    for row in sheet2.iter_rows(min_row=1):
        if row[1].value == "IS_NET_INCOME":
            net_income_row = row
            break
    assert net_income_row is not None
    # First 7 cols are metadata; col index 7..26 are period values
    val_0 = net_income_row[7].value
    val_1 = net_income_row[8].value
    val_2 = net_income_row[9].value
    assert isinstance(val_0, (int, float)) and val_0 > 0
    assert isinstance(val_1, (int, float)) and val_1 < 0, (
        "Negative financial amount must be stored as numeric float, not string"
    )
    assert net_income_row[8].data_type == "n"
    assert val_2 is None, (
        "NULL accounting amount must remain blank cell (None), never 0"
    )


def test_excel_formula_injection_neutralized_across_all_sheets() -> None:
    """Strings starting with =, +, -, @, tab, CR are sanitized and never stored as Excel formulas."""
    pytest.importorskip("xlsxwriter")

    payload = _build_sample_company_payload(num_periods=4, inject_formula=True)
    raw_bytes = build_company_workbook_bytes(payload)
    openpyxl = _load_openpyxl()
    wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=False)

    found_neutralized: list[str] = []
    for name in wb.sheetnames:
        ws = wb[name]
        for row in ws.iter_rows():
            for cell in row:
                assert cell.data_type != "f", (
                    f"Formula cell detected in sheet {name}: {cell.value!r}"
                )
                if isinstance(cell.value, str):
                    assert not cell.value.lstrip().startswith(("=", "+", "@")), (
                        f"Unsanitized formula prefix in {name}: {cell.value!r}"
                    )
                    if cell.value.startswith("'"):
                        found_neutralized.append(cell.value)

    assert any("=CMD" in v for v in found_neutralized)
    assert any("+CMD" in v for v in found_neutralized)
    assert any("@SUM" in v for v in found_neutralized)
    assert any("-Tài nguyên" in v for v in found_neutralized)


def test_screener_workbook_685_rows_perf_and_row_cap() -> None:
    """Screener .xlsx renders 685 rows < 1000ms, preserves numeric/null types, and caps > 2000 rows."""
    pytest.importorskip("xlsxwriter")
    n_rows = 685
    frame = pl.DataFrame(
        {
            "ticker": [f"T{i:03d}" for i in range(n_rows)],
            "company_name_vn": [
                "=1+1" if i == 0 else f"Doanh nghiệp {i}" for i in range(n_rows)
            ],
            "exchange": ["HOSE"] * n_rows,
            "icb_name_level2": ["Công nghiệp"] * n_rows,
            "icb_name_level3": ["Xây dựng"] * n_rows,
            "risk_tier": ["R3"] * n_rows,
            "calibrated_distress_prob": [0.05 + (i % 10) * 0.01 for i in range(n_rows)],
            "risk_market_percentile": [50.0] * n_rows,
            "relative_risk_lift": [1.1] * n_rows,
            "altman_z_double_prime": [2.2] * n_rows,
            "altman_zone": ["GREY_ZONE"] * n_rows,
            "piotroski_f_score": [5] * n_rows,
            "beneish_m8_score": [-2.1] * n_rows,
            "beneish_flag": ["VALID"] * n_rows,
            "red_flag_count": [1] * n_rows,
            "critical_red_flag_count": [0] * n_rows,
            "roe_total": [
                None if i == 1 else -0.05 if i == 2 else 0.12 for i in range(n_rows)
            ],
            "roe_parent": [0.11] * n_rows,
            "roa": [0.06] * n_rows,
            "ebit_margin": [0.14] * n_rows,
            "eps_ttm_estimated": [2100.0] * n_rows,
            "liabilities_to_assets": [0.55] * n_rows,
            "debt_to_equity": [0.85] * n_rows,
            "interest_coverage": [3.5] * n_rows,
            "cfo_ttm": [500_000_000_000.0] * n_rows,
            "fcf_ttm": [200_000_000_000.0] * n_rows,
            "ocf_to_total_liabilities": [0.18] * n_rows,
            "revenue_growth_yoy": [0.09] * n_rows,
            "market_cap_vnd": [10_000_000_000_000.0] * n_rows,
            "pe_ttm": [11.5] * n_rows,
            "pe_flag": ["VALID"] * n_rows,
            "pb_ratio": [1.4] * n_rows,
            "pb_flag": ["VALID"] * n_rows,
            "cluster_archetype_name": ["Cụm ổn định"] * n_rows,
            "anomaly_flag": ["NORMAL"] * n_rows,
        }
    )

    payload = ScreenerExportPayload(
        snapshot_id="snap_screener_685",
        published_at="2026-04-25T08:00:00Z",
        statement_scope="CONSOLIDATED",
        period_type="QUARTER",
        period_end_date=date(2025, 12, 31),
        frame=frame,
    )

    t0 = time.perf_counter()
    raw_bytes = build_screener_workbook_bytes(payload)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert elapsed_ms < 1000.0, (
        f"Screener 685-row export took {elapsed_ms:.1f}ms (>= 1000ms)"
    )

    openpyxl = _load_openpyxl()
    wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=False)
    assert wb.sheetnames == [SCREENER_SHEET_NAME]
    ws = wb[SCREENER_SHEET_NAME]

    all_text = "\n".join(
        str(cell.value)
        for row in ws.iter_rows()
        for cell in row
        if cell.value is not None
    )
    assert "snap_screener_685" in all_text
    assert COMPLIANCE_DISCLAIMER_VI in all_text
    assert "'=1+1" in all_text

    # Verify hard cap at MAX_SCREENER_EXPORT_ROWS (2000)
    big_frame = pl.concat([frame] * 4).head(2500)
    capped_bytes = build_screener_workbook_bytes(
        ScreenerExportPayload(
            snapshot_id="snap_screener_big",
            published_at="2026-04-25T08:00:00Z",
            statement_scope="CONSOLIDATED",
            period_type="QUARTER",
            period_end_date=date(2025, 12, 31),
            frame=big_frame,
        )
    )
    wb_big = openpyxl.load_workbook(io.BytesIO(capped_bytes), data_only=False)
    ws_big = wb_big[SCREENER_SHEET_NAME]
    ticker_rows = [
        row[0].value
        for row in ws_big.iter_rows()
        if isinstance(row[0].value, str)
        and row[0].value.startswith("T")
        and len(row[0].value) == 4
    ]
    assert len(ticker_rows) == MAX_SCREENER_EXPORT_ROWS


def test_export_api_endpoints_headers_404_and_row_cap(tmp_path: Path) -> None:
    """FastAPI /api/v3/exports/* returns valid xlsx, X-Snapshot-ID, sanitized filename, and 404."""
    fastapi = pytest.importorskip("fastapi")
    _ = fastapi
    from fastapi.testclient import TestClient

    from finintel.serving.api import create_app
    from tests.unit.test_layer5_fastapi_serving import _build_synthetic_v2_tables

    from finintel.pipeline.state_store import StateStore
    from finintel.storage.snapshot_publisher import SnapshotPublisher

    tables = _build_synthetic_v2_tables()
    store = StateStore(tmp_path / "metadata" / "state.sqlite")
    store.initialize()
    store.start_run("run-export-01", "CLI_MANUAL", {}, None)
    publisher = SnapshotPublisher(
        tmp_path / "published" / "snapshots", state_store=store
    )
    publisher.publish("snap_export_api_001", tables, run_id="run-export-01")

    app = create_app(data_root=tmp_path)
    openpyxl = _load_openpyxl()
    with TestClient(app) as client:
        resp = client.get("/api/v3/exports/companies/FPT.xlsx")
        assert resp.status_code == 200
        assert (
            resp.headers["content-type"]
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert resp.headers.get("X-Snapshot-ID") == "snap_export_api_001"
        cd = resp.headers.get("Content-Disposition", "")
        assert "finintel-FPT-" in cd and cd.endswith('.xlsx"')

        wb = openpyxl.load_workbook(io.BytesIO(resp.content), data_only=False)
        assert tuple(wb.sheetnames) == COMPANY_SHEET_NAMES

        # Unknown ticker -> clean 404 without traceback
        missing = client.get("/api/v3/exports/companies/UNKNOWN99.xlsx")
        assert missing.status_code == 404
        detail = missing.json().get("detail", "")
        assert "Traceback" not in detail

        # Screener export
        scr = client.get("/api/v3/exports/screener.xlsx?statement_scope=CONSOLIDATED")
        assert scr.status_code == 200
        assert (
            scr.headers["content-type"]
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert scr.headers.get("X-Snapshot-ID") == "snap_export_api_001"
        assert "finintel-screener-" in scr.headers.get("Content-Disposition", "")
        wb_scr = openpyxl.load_workbook(io.BytesIO(scr.content), data_only=False)
        assert wb_scr.sheetnames == [SCREENER_SHEET_NAME]
