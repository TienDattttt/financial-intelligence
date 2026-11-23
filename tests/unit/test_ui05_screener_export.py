"""Unit and integration tests for UI-05a: GET /api/v1/screener/export."""

from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path

import polars as pl
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from finintel.pipeline.state_store import StateStore  # noqa: E402
from finintel.serving.api import _sanitize_csv_cell, create_app  # noqa: E402
from finintel.storage.schemas import (
    FINANCIAL_RATIOS_SCHEMA,
    ISSUERS_SCHEMA,
    ML_SCORES_SCHEMA,
    ML_SCORES_TABLE,
    OPTIONAL_ANALYTICS_SCHEMAS,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)
from finintel.storage.snapshot_publisher import SnapshotPublisher


def _build_screener_export_fixture(
    tmp_path: Path, *, issuer_count: int = 240
) -> tuple[Path, str]:
    data_root = tmp_path / "data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    run_id = "run_ui05_001"
    snapshot_id = "snap_ui05_001"
    state.start_run(run_id, "MANUAL", {"source": "test"}, "sha_ui05")

    period_end = date(2026, 9, 30)
    tiers = ("R1", "R2", "R3", "R4", "R5")
    zones = ("SAFE_ZONE", "GREY_ZONE", "DISTRESS_ZONE")
    beneish_flags = ("VALID", "HIGH_MANIPULATION_RISK_SCREEN")

    injection_names = {
        0: "=CMD|'/C calc'!A0",
        1: "+SUM(1,1)",
        2: "-cmd|'/C whoami'!A0",
        3: "@SUM(1+1)*cmd",
        4: '\t=HYPERLINK("http://evil")',
        5: "\r=1+1",
    }

    issuer_rows: list[dict[str, object]] = []
    ratio_rows: list[dict[str, object]] = []
    ml_rows: list[dict[str, object]] = []
    rf_rows: list[dict[str, object]] = []

    for idx in range(issuer_count):
        ticker = f"T{idx:03d}"
        is_tech = idx % 3 == 0
        icb2 = "9500" if is_tech else "2300"
        icb2_name = "Công nghệ thông tin" if is_tech else "Xây dựng"
        tier = tiers[idx % len(tiers)]
        zone = zones[idx % len(zones)]
        b_flag = beneish_flags[idx % len(beneish_flags)]
        # Ensure genuine negative numeric floats (including exact -15.4 on row 0)
        roe = -15.4 if idx == 0 else round(-0.25 + (idx % 60) * 0.015, 4)
        prob = round(0.01 + (idx % 50) * 0.01, 4)
        f_score = (idx % 9) + 1

        issuer_rows.append(
            {
                "issuer_id": ticker,
                "ticker": ticker,
                "exchange": "HOSE",
                "company_name_vn": injection_names.get(idx, f"Doanh nghiệp {ticker}"),
                "accounting_regime": "CORPORATE",
                "icb_code_level2": icb2,
                "icb_name_level2": icb2_name,
                "icb_code_level3": f"{icb2}1",
                "icb_name_level3": "Nhóm ngành con",
                "industry_snapshot_date": date(2025, 1, 1),
                "delisted_date": None,
            }
        )

        ratio_row: dict[str, object] = {
            col: None for col in FINANCIAL_RATIOS_SCHEMA.keys()
        }
        ratio_row.update(
            {
                "issuer_id": ticker,
                "ticker": ticker,
                "accounting_regime": "CORPORATE",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "fiscal_year": 2026,
                "fiscal_quarter": 3,
                "period_end_date": period_end,
                "filing_id": f"{ticker}_2026Q3",
                "taxonomy_version": "VAS_TT99_2025",
                "source_filing_ids": [f"{ticker}_2026Q3"],
                "roe_total": roe,
                "roa": round(roe / 2.0, 4),
                "ebit_margin": -0.12 if idx == 0 else 0.12,
                "debt_to_equity": round(0.2 + (idx % 10) * 0.25, 2),
                "interest_coverage": 4.5,
                "accruals_to_assets": -0.03,
                "core_operating_margin": 0.11,
                "non_core_distortion_to_assets": 0.01,
                "core_to_ebt_ratio": 0.85,
            }
        )
        ratio_rows.append(ratio_row)

        ml_rows.append(
            {
                "issuer_id": ticker,
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": period_end,
                "filing_id": f"{ticker}_2026Q3",
                "model_role": "PRIMARY_CORPORATE",
                "altman_z_double_prime": -1.2 if idx == 0 else 2.5,
                "altman_em_score": 4.2,
                "altman_zone": zone,
                "piotroski_f_score": f_score,
                "piotroski_valid_components_count": 9,
                "beneish_m8_score": -2.35,
                "beneish_flag": b_flag,
                "pca_pc1": 0.0,
                "pca_pc2": 0.0,
                "pca_pc3": 0.0,
                "cluster_id": 1,
                "cluster_archetype_name": (
                    "=1+1" if idx == 6 else "Sản xuất công nghiệp"
                ),
                "anomaly_score_raw": 0.1,
                "anomaly_percentile_rank": 40.0,
                "statistical_outlier_flag": False,
                "anomaly_flag": "NORMAL",
                "distress_risk_score": -2.0,
                "calibrated_distress_prob": prob,
                "risk_tier": tier,
                "risk_market_percentile": round(prob * 100.0, 2),
                "market_base_rate": 0.025,
                "relative_risk_lift": round(prob / 0.025, 2),
                "model_version": "v2.test",
                "shap_top_drivers_json": None,
            }
        )

        n_flags = idx % 4
        for f_idx in range(n_flags):
            rf_rows.append(
                {
                    "issuer_id": ticker,
                    "statement_scope": "CONSOLIDATED",
                    "period_type": "QUARTER",
                    "period_end_date": period_end,
                    "flag_code": f"RF_0{f_idx + 1}_TEST",
                    "severity": "CRITICAL" if f_idx == 0 else "WARNING",
                    "flag_title_vn": "Cờ kiểm tra",
                    "explanation_text_vn": "Mô tả",
                    "evidence_json": "{}",
                    "filing_id": f"{ticker}_2026Q3",
                }
            )

    tables: dict[str, pl.DataFrame] = {
        "issuers.parquet": pl.DataFrame(issuer_rows, schema=ISSUERS_SCHEMA),
        RATIOS_TABLE: pl.DataFrame(ratio_rows, schema=FINANCIAL_RATIOS_SCHEMA),
        ML_SCORES_TABLE: pl.DataFrame(ml_rows, schema=ML_SCORES_SCHEMA),
        RED_FLAGS_TABLE: pl.DataFrame(
            rf_rows, schema=OPTIONAL_ANALYTICS_SCHEMAS[RED_FLAGS_TABLE]
        ),
    }

    publisher = SnapshotPublisher(
        data_root / "published" / "snapshots", state_store=state
    )
    publisher.publish(
        snapshot_id,
        tables,
        run_id=run_id,
        metadata={"dagster_run_id": run_id, "git_commit_sha": "sha_ui05"},
    )
    state.finish_run(run_id, "SUCCEEDED", snapshot_id)
    return data_root, snapshot_id


def _parse_csv_response(raw: bytes) -> tuple[list[str], list[dict[str, str]]]:
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    assert reader.fieldnames is not None
    rows = list(reader)
    return list(reader.fieldnames), rows


def test_sanitize_csv_cell_owasp_triggers_and_numeric_preservation() -> None:
    assert _sanitize_csv_cell("=SUM(1,1)") == "'=SUM(1,1)"
    assert _sanitize_csv_cell("+1+1") == "'+1+1"
    assert _sanitize_csv_cell("-cmd") == "'-cmd"
    assert _sanitize_csv_cell("@SUM(A1)") == "'@SUM(A1)"
    assert _sanitize_csv_cell("\t=1+1") == "'\t=1+1"
    assert _sanitize_csv_cell("\r=1+1") == "'\r=1+1"
    # Genuine numeric negative floats/ints MUST stay unprefixed
    assert _sanitize_csv_cell(-15.4) == -15.4
    assert _sanitize_csv_cell(-7) == -7
    assert _sanitize_csv_cell(float("nan")) == ""
    assert _sanitize_csv_cell(None) == ""
    assert _sanitize_csv_cell(date(2026, 9, 30)) == "2026-09-30"


def test_v1_screener_export_full_dataset_bom_headers_and_etag(
    tmp_path: Path,
) -> None:
    data_root, snapshot_id = _build_screener_export_fixture(tmp_path, issuer_count=240)
    app = create_app(data_root=data_root)

    with TestClient(app) as client:
        # Paginated endpoint remains capped at limit=50 / max 200
        r_page = client.get("/api/v1/screener", params={"limit": 50})
        assert r_page.status_code == 200
        assert r_page.json()["total"] == 240
        assert len(r_page.json()["items"]) == 50

        # Export endpoint returns 100% of filtered rows (> 100 and > 200)
        r_exp = client.get("/api/v1/screener/export")
        assert r_exp.status_code == 200
        assert r_exp.headers["content-type"].startswith("text/csv; charset=utf-8")
        assert (
            r_exp.headers["content-disposition"]
            == 'attachment; filename="finintel-screener.csv"'
        )
        assert r_exp.headers.get("X-Snapshot-ID") == snapshot_id
        etag = r_exp.headers.get("ETag")
        assert etag is not None

        fieldnames, rows = _parse_csv_response(r_exp.content)
        assert len(rows) == 240
        assert "ticker" in fieldnames
        assert "risk_tier" in fieldnames
        assert "calibrated_distress_prob" in fieldnames
        assert "roe_total" in fieldnames

        # ETag 304 support
        r_304 = client.get("/api/v1/screener/export", headers={"If-None-Match": etag})
        assert r_304.status_code == 304


def test_v1_screener_export_filter_sort_parity_and_formula_injection(
    tmp_path: Path,
) -> None:
    data_root, _ = _build_screener_export_fixture(tmp_path, issuer_count=160)
    app = create_app(data_root=data_root)

    with TestClient(app) as client:
        # 1. Verify OWASP sanitization & negative numeric preservation on full export
        r_all = client.get(
            "/api/v1/screener/export",
            params={"sort_by": "ticker", "sort_dir": "asc"},
        )
        assert r_all.status_code == 200
        _, rows_all = _parse_csv_response(r_all.content)
        assert len(rows_all) == 160
        by_ticker = {r["ticker"]: r for r in rows_all}

        assert by_ticker["T000"]["company_name_vn"] == "'=CMD|'/C calc'!A0"
        assert by_ticker["T001"]["company_name_vn"] == "'+SUM(1,1)"
        assert by_ticker["T002"]["company_name_vn"] == "'-cmd|'/C whoami'!A0"
        assert by_ticker["T003"]["company_name_vn"] == "'@SUM(1+1)*cmd"
        assert by_ticker["T004"]["company_name_vn"] == '\'\t=HYPERLINK("http://evil")'
        assert by_ticker["T005"]["company_name_vn"] == "'\r=1+1"
        assert by_ticker["T006"]["cluster_archetype_name"] == "'=1+1"

        # Genuine numeric negatives remain unprefixed!
        assert by_ticker["T000"]["roe_total"] == "-15.4"
        assert by_ticker["T000"]["altman_z_double_prime"] == "-1.2"
        assert by_ticker["T000"]["ebit_margin"] == "-0.12"

        # 2. Verify exact filter & sort parity with /api/v1/screener
        filter_cases: list[dict[str, str | int]] = [
            {"risk_tier": "R4", "sort_by": "roe_total", "sort_dir": "asc"},
            {
                "altman_zone": "DISTRESS_ZONE",
                "beneish_flag": "HIGH_MANIPULATION_RISK_SCREEN",
                "min_f_score": 3,
                "max_red_flags": 2,
                "icb_code_level2": "9500",
                "sort_by": "calibrated_distress_prob",
                "sort_dir": "desc",
            },
            {"q": "T01", "sort_by": "ticker", "sort_dir": "desc"},
        ]
        for params in filter_cases:
            r_json = client.get("/api/v1/screener", params={**params, "limit": 200})
            r_csv = client.get("/api/v1/screener/export", params=params)
            assert r_json.status_code == 200
            assert r_csv.status_code == 200
            json_tickers = [item["ticker"] for item in r_json.json()["items"]]
            _, csv_rows = _parse_csv_response(r_csv.content)
            csv_tickers = [row["ticker"] for row in csv_rows]
            assert csv_tickers == json_tickers
            assert len(csv_tickers) == r_json.json()["total"]
