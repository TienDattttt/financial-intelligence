"""Comprehensive TDD unit, concurrency, security, and empirical tests for Layer 5 Serving.

Verified Sources:
- FastAPI Official Docs (TestClient lifespan, sync concurrency, CORS):
  https://fastapi.tiangolo.com/advanced/events/
  https://fastapi.tiangolo.com/async/
- DuckDB Python API & Multithreading (``duckdb==1.4.2``):
  https://duckdb.org/docs/stable/guides/python/multiple_threads
- RFC 9110 HTTP Conditional Requests (``ETag`` & ``If-None-Match`` -> ``304``):
  https://httpwg.org/specs/rfc9110.html#field.etag
- Chow, J. T. S. (2015). IMF Working Paper WP/15/216 stress-test equivalence.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from finintel.analytics.stress_test import (
    StressScenario,
    simulate_financial_stress,
    summarize_stress_by_sector,
)
from finintel.analytics.tables import build_analytics_tables
from finintel.exceptions import ContractViolationError
from finintel.pipeline.state_store import StateStore
from finintel.quality.gate import evaluate_quality_gate, verify_analytics_quality_gate
from finintel.serving.api import create_app
from finintel.serving.dependencies import DEFAULT_API_HOST, get_db_cursor
from finintel.serving.queries import (
    ALL_RATIOS,
    RATIO_LINEAGE_CATALOG,
    SCREENER_SCHEMA,
    DashboardQueryService,
    pagination_offset,
    pagination_total_pages,
    pagination_window,
    percent_to_decimal,
)
from finintel.storage.ducklake_store import DuckLakeStore
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_SCHEMA,
    AUDIT_QUARANTINE_TABLE,
    BENCHMARKS_TABLE,
    FILINGS_MANIFEST_SCHEMA,
    FILINGS_MANIFEST_TABLE,
    ML_SCORES_SCHEMA,
    ML_SCORES_TABLE,
    QUALITY_SUMMARY_SCHEMA,
    QUALITY_SUMMARY_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)
from finintel.storage.snapshot_publisher import SnapshotPublisher


def _build_synthetic_v2_tables() -> dict[str, pl.DataFrame]:
    """Build a 3-issuer, 2-year V2 dataset covering all 38/38 ratios, stress inputs, ML, and quality."""
    issuers = pl.DataFrame(
        {
            "issuer_id": ["FPT", "CMG", "HPG"],
            "ticker": ["FPT", "CMG", "HPG"],
            "exchange": ["HOSE", "HOSE", "HOSE"],
            "company_name_vn": [
                "Công ty Cổ phần FPT",
                "Tập đoàn Công nghệ CMC",
                "Tập đoàn Hòa Phát",
            ],
            "accounting_regime": ["CORPORATE", "CORPORATE", "CORPORATE"],
            "icb_code_level2": ["9500", "9500", "1700"],
            "icb_name_level2": [
                "Công nghệ thông tin",
                "Công nghệ thông tin",
                "Tài nguyên Cơ bản",
            ],
            "icb_code_level3": ["9530", "9530", "1750"],
            "icb_name_level3": ["Phần mềm", "Phần mềm", "Kim loại"],
            "industry_snapshot_date": [
                date(2024, 1, 1),
                date(2024, 1, 1),
                date(2024, 1, 1),
            ],
            "delisted_date": [None, None, None],
        }
    )

    quarter_windows = [
        (1, date(2024, 1, 1), date(2024, 3, 31), date(2024, 4, 20)),
        (2, date(2024, 4, 1), date(2024, 6, 30), date(2024, 7, 20)),
        (3, date(2024, 7, 1), date(2024, 9, 30), date(2024, 10, 20)),
        (4, date(2024, 10, 1), date(2024, 12, 31), date(2025, 1, 25)),
        (1, date(2025, 1, 1), date(2025, 3, 31), date(2025, 4, 20)),
        (2, date(2025, 4, 1), date(2025, 6, 30), date(2025, 7, 20)),
        (3, date(2025, 7, 1), date(2025, 9, 30), date(2025, 10, 20)),
        (4, date(2025, 10, 1), date(2025, 12, 31), date(2026, 1, 25)),
    ]

    fact_rows: list[dict[str, object]] = []
    manifest_rows: list[dict[str, object]] = []

    issuer_profiles = [
        # (issuer, scale, debt_mult, interest_mult, gm_ratio, sell_ratio, admin_ratio)
        ("FPT", 1.0, 1.0, 1.0, 0.40, 0.10, 0.12),
        ("CMG", 0.2, 1.2, 1.3, 0.32, 0.08, 0.11),
        # HPG deliberately tighter ICR so moderate stress pushes it to newly distressed
        ("HPG", 1.5, 4.5, 5.2, 0.18, 0.04, 0.05),
    ]

    for (
        issuer_id,
        scale,
        debt_mult,
        int_mult,
        gm_ratio,
        sell_r,
        admin_r,
    ) in issuer_profiles:
        for year in (2024, 2025):
            growth = 1.15 if year == 2025 else 1.0
            rev = 60_000.0 * scale * growth
            gp = rev * gm_ratio
            cogs = -(rev - gp)
            sell = -(rev * sell_r)
            admin = -(rev * admin_r)
            interest = -(700.0 * scale * int_mult)
            op_profit = gp + sell + admin + interest
            ebt = op_profit + 500.0 * scale
            cur_tax = -(max(ebt, 0.0) * 0.18)
            def_tax = 50.0 * scale
            ni = ebt + cur_tax + def_tax
            ni_parent = ni * 0.85
            cfo = 10_500.0 * scale * growth
            capex = -4_000.0 * scale
            dep = 2_500.0 * scale

            st_debt = 3_500.0 * scale * debt_mult
            lt_debt = 4_000.0 * scale * debt_mult
            total_assets = 80_000.0 * scale * (1.08 if year == 2025 else 1.0)
            total_liab = min(total_assets * 0.65, 25_000.0 * scale + st_debt + lt_debt)
            total_eq = total_assets - total_liab
            cur_liab = total_liab * 0.65
            cur_assets = total_assets * 0.60

            annual_metrics = {
                "BS_CURRENT_ASSETS": cur_assets,
                "BS_CASH_AND_EQUIVALENTS": 10_000.0 * scale,
                "BS_SHORT_TERM_INVESTMENTS": 5_000.0 * scale,
                "BS_SHORT_TERM_RECEIVABLES": 14_000.0 * scale,
                "BS_TRADE_RECEIVABLES_ST": 12_000.0 * scale,
                "BS_BAD_DEBT_PROVISION_ST": -500.0 * scale,
                "BS_INVENTORIES": 4_000.0 * scale,
                "BS_TOTAL_ASSETS": total_assets,
                "BS_TOTAL_LIABILITIES": total_liab,
                "BS_CURRENT_LIABILITIES": cur_liab,
                "BS_SHORT_TERM_BORROWINGS": st_debt,
                "BS_TRADE_PAYABLES_ST": 8_000.0 * scale,
                "BS_LONG_TERM_BORROWINGS": lt_debt,
                "BS_CONVERTIBLE_BONDS": 0.0,
                "BS_TOTAL_EQUITY": total_eq,
                "BS_ORDINARY_SHARES_CAPITAL": 17_000.0 * scale,
                "BS_TREASURY_SHARES": 0.0,
                "BS_MINORITY_INTEREST": total_eq * 0.12,
                "IS_NET_REVENUE": rev,
                "IS_COST_OF_GOODS_SOLD": cogs,
                "IS_GROSS_PROFIT": gp,
                "IS_SELLING_EXPENSES": sell,
                "IS_ADMINISTRATIVE_EXPENSES": admin,
                "IS_INTEREST_EXPENSE": interest,
                "IS_OPERATING_PROFIT": op_profit,
                "IS_PROFIT_BEFORE_TAX": ebt,
                "IS_CURRENT_INCOME_TAX": cur_tax,
                "IS_DEFERRED_INCOME_TAX": def_tax,
                "IS_NET_INCOME": ni,
                "IS_NET_INCOME_PARENT": ni_parent,
                "IS_EPS_BASIC_REPORTED": 4500.0,
                "CF_DEPRECIATION_AMORTIZATION": dep,
                "CF_OPERATING_CASH_FLOW": cfo,
                "CF_CAPITAL_EXPENDITURE": capex,
            }

            ann_fid = f"FIL_{issuer_id}_{year}_ANNUAL"
            ann_pub = date(year + 1, 3, 20)
            manifest_rows.append(
                {
                    "filing_id": ann_fid,
                    "issuer_id": issuer_id,
                    "ticker_at_filing": issuer_id,
                    "source_provider": "hose_pdf_extract",
                    "connector_name": "file_adapter_v1",
                    "statement_type": "CONSOLIDATED_PACK",
                    "statement_scope": "CONSOLIDATED",
                    "period_type": "ANNUAL",
                    "period_end_date": date(year, 12, 31),
                    "published_at": ann_pub,
                    "published_at_basis": "EXACT_FILING",
                    "effective_available_at": ann_pub,
                    "audit_status": "AUDITED",
                    "audit_opinion": "UNMODIFIED",
                    "revision_no": 1,
                    "revision_reason": "ORIGINAL",
                    "canonical_content_sha256": f"{issuer_id.lower()}{year}" * 8,
                    "payload_file_sha256": f"{issuer_id.lower()}{year}" * 8,
                }
            )
            for metric_id, val in annual_metrics.items():
                fact_rows.append(
                    {
                        "issuer_id": issuer_id,
                        "statement_scope": "CONSOLIDATED",
                        "period_type": "ANNUAL",
                        "fiscal_year_start_date": date(year, 1, 1),
                        "period_end_date": date(year, 12, 31),
                        "metric_id": metric_id,
                        "taxonomy_version": "VAS_TT200_2014",
                        "revision_no": 1,
                        "filing_id": ann_fid,
                        "fiscal_year": year,
                        "fiscal_quarter": None,
                        "period_start_date": date(year, 1, 1),
                        "published_at": ann_pub,
                        "published_at_basis": "EXACT_FILING",
                        "effective_available_at": ann_pub,
                        "revision_reason": "ORIGINAL",
                        "derivation_method": "REPORTED_DIRECTLY",
                        "audit_status": "AUDITED",
                        "audit_opinion": "UNMODIFIED",
                        "value": float(val),
                        "ttm_value": float(val)
                        if not metric_id.startswith("BS_")
                        else None,
                        "computation_note": None,
                    }
                )

            for q_num, q_start, q_end, q_pub in quarter_windows:
                if q_end.year != year:
                    continue
                q_fid = f"FIL_{issuer_id}_{year}_Q{q_num}"
                manifest_rows.append(
                    {
                        "filing_id": q_fid,
                        "issuer_id": issuer_id,
                        "ticker_at_filing": issuer_id,
                        "source_provider": "hose_pdf_extract",
                        "connector_name": "file_adapter_v1",
                        "statement_type": "CONSOLIDATED_PACK",
                        "statement_scope": "CONSOLIDATED",
                        "period_type": "QUARTER",
                        "period_end_date": q_end,
                        "published_at": q_pub,
                        "published_at_basis": "EXACT_FILING",
                        "effective_available_at": q_pub,
                        "audit_status": "UNAUDITED",
                        "audit_opinion": "NOT_APPLICABLE",
                        "revision_no": 1,
                        "revision_reason": "ORIGINAL",
                        "canonical_content_sha256": f"{issuer_id.lower()}{year}q{q_num}".ljust(
                            64, "0"
                        )[:64],
                        "payload_file_sha256": f"{issuer_id.lower()}{year}q{q_num}".ljust(
                            64, "0"
                        )[:64],
                    }
                )
                for metric_id, val in annual_metrics.items():
                    is_stock = metric_id.startswith("BS_")
                    q_val = (
                        float(val)
                        if is_stock
                        else (
                            float(val) / 4.0
                            if metric_id != "IS_EPS_BASIC_REPORTED"
                            else 1125.0
                        )
                    )
                    fact_rows.append(
                        {
                            "issuer_id": issuer_id,
                            "statement_scope": "CONSOLIDATED",
                            "period_type": "QUARTER",
                            "fiscal_year_start_date": date(year, 1, 1),
                            "period_end_date": q_end,
                            "metric_id": metric_id,
                            "taxonomy_version": "VAS_TT200_2014",
                            "revision_no": 1,
                            "filing_id": q_fid,
                            "fiscal_year": year,
                            "fiscal_quarter": q_num,
                            "period_start_date": q_start,
                            "published_at": q_pub,
                            "published_at_basis": "EXACT_FILING",
                            "effective_available_at": q_pub,
                            "revision_reason": "ORIGINAL",
                            "derivation_method": "REPORTED_DIRECTLY",
                            "audit_status": "UNAUDITED",
                            "audit_opinion": "NOT_APPLICABLE",
                            "value": q_val,
                            "ttm_value": float(val) if not is_stock else None,
                            "computation_note": None,
                        }
                    )

    facts_df = pl.DataFrame(fact_rows).with_columns(
        pl.col("fiscal_year").cast(pl.Int32),
        pl.col("fiscal_quarter").cast(pl.Int8),
        pl.col("revision_no").cast(pl.Int64),
        pl.col("computation_note").cast(pl.String),
    )
    analytics = build_analytics_tables(facts_df, issuers, as_of=date(2026, 6, 1))
    ratios_df = analytics[RATIOS_TABLE]

    # Build realistic Layer 4B ML scores including both V2 SHAP and legacy SHAP payloads.
    v2_shap_json = json.dumps(
        {
            "base_value": -1.2,
            "prediction_score": -1.8,
            "other_features_delta": -0.1,
            "top_risk_increasing": [
                {
                    "feature": "debt_to_equity",
                    "display_name_vn": "Nợ vay trên vốn chủ sở hữu (D/E)",
                    "value": 0.42,
                    "peer_median": 0.35,
                    "shap_delta": 0.15,
                }
            ],
            "top_risk_decreasing": [
                {
                    "feature": "roe_parent",
                    "display_name_vn": "Tỷ suất sinh lời trên VCSH công ty mẹ (ROE)",
                    "value": 0.24,
                    "peer_median": 0.16,
                    "shap_delta": -0.65,
                }
            ],
        }
    )
    legacy_shap_json = json.dumps(
        {
            "base_value": 0.5,
            "prediction_score": 0.85,
            "top_risk_increasing": [
                {
                    "feature": "interest_coverage",
                    "value": 1.2,
                    "peer_median": 6.5,
                    "shap_delta": 0.45,
                }
            ],
            "top_risk_decreasing": [
                {
                    "feature": "current_ratio",
                    "value": 1.5,
                    "peer_median": 1.3,
                    "shap_delta": -0.20,
                }
            ],
        }
    )
    ml_rows: list[dict[str, object]] = []
    for row in ratios_df.to_dicts():
        iid = str(row["issuer_id"])
        p_type = str(row["period_type"])
        is_q = p_type == "QUARTER"
        if iid == "FPT":
            zone, f_score, b_flag, prob, shap_str = (
                "SAFE_ZONE",
                8,
                "VALID",
                0.04 if is_q else None,
                v2_shap_json if is_q else None,
            )
        elif iid == "CMG":
            zone, f_score, b_flag, prob, shap_str = (
                "GREY_ZONE",
                6,
                "VALID",
                0.18 if is_q else None,
                v2_shap_json if is_q else None,
            )
        else:
            zone, f_score, b_flag, prob, shap_str = (
                "DISTRESS_ZONE",
                3,
                "HIGH_MANIPULATION_RISK_SCREEN",
                0.62 if is_q else None,
                legacy_shap_json if is_q else None,
            )
        ml_rows.append(
            {
                "issuer_id": iid,
                "statement_scope": str(row["statement_scope"]),
                "period_type": p_type,
                "period_end_date": row["period_end_date"],
                "filing_id": str(row["filing_id"]),
                "model_role": "SCREENING_INDICATOR",
                "altman_z_double_prime": 4.2
                if iid == "FPT"
                else (1.9 if iid == "CMG" else 0.7),
                "altman_em_score": 7.45
                if iid == "FPT"
                else (5.15 if iid == "CMG" else 3.95),
                "altman_zone": zone,
                "piotroski_f_score": f_score,
                "piotroski_valid_components_count": 9,
                "beneish_m8_score": -2.4 if iid != "HPG" else -1.1,
                "beneish_flag": b_flag,
                "pca_pc1": 0.3 if is_q else None,
                "pca_pc2": -0.2 if is_q else None,
                "pca_pc3": 0.1 if is_q else None,
                "cluster_id": 0 if is_q else None,
                "cluster_archetype_name": "High-Margin Tech Leader" if is_q else None,
                "anomaly_score_raw": 0.12 if is_q else None,
                "anomaly_percentile_rank": 35.0 if is_q else None,
                "statistical_outlier_flag": False if is_q else None,
                "anomaly_flag": None,
                "distress_risk_score": -1.8
                if (is_q and iid == "FPT")
                else (0.85 if is_q else None),
                "calibrated_distress_prob": prob,
                "risk_tier": (
                    "R1" if iid == "FPT" else ("R3" if iid == "CMG" else "R5")
                )
                if is_q
                else None,
                "risk_market_percentile": (
                    33.33 if iid == "FPT" else (66.67 if iid == "CMG" else 100.0)
                )
                if is_q
                else None,
                "market_base_rate": 0.025 if is_q else None,
                "relative_risk_lift": (prob / 0.025)
                if (is_q and prob is not None)
                else None,
                "model_version": "xgboost-layer4b-v2" if is_q else None,
                "shap_top_drivers_json": shap_str,
            }
        )
    ml_df = pl.DataFrame(ml_rows, schema=ML_SCORES_SCHEMA)

    # Quality summary with both PASS and WARNING rows + audit_quarantine rows for pagination tests.
    now_iso = datetime.now(UTC).isoformat()
    qs_rows = [
        {
            "issuer_id": str(row["issuer_id"]),
            "statement_scope": str(row["statement_scope"]),
            "period_type": str(row["period_type"]),
            "fiscal_year": int(row["fiscal_year"]),
            "fiscal_quarter": row["fiscal_quarter"],
            "period_end_date": row["period_end_date"],
            "taxonomy_version": str(row["taxonomy_version"]),
            "quality_status": "WARNING" if str(row["issuer_id"]) == "HPG" else "PASS",
            "warning_codes": ["DERIVED_Q4_INPUT"]
            if str(row["issuer_id"]) == "HPG"
            else [],
            "info_codes": [],
            "filing_ids": [str(row["filing_id"])],
            "checked_at": now_iso,
        }
        for row in ratios_df.to_dicts()
    ]
    quality_df = pl.DataFrame(qs_rows, schema=QUALITY_SUMMARY_SCHEMA)

    quarantine_df = pl.DataFrame(
        [
            {
                "issuer_id": "HPG",
                "filing_id": "FIL_HPG_2023_Q1_BAD",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": date(2023, 3, 31),
                "taxonomy_version": "VAS_TT200_2014",
                "rule_code": "BS_IDENTITY_MISMATCH",
                "severity": "CRITICAL",
                "details_json": '{"diff_pct": 0.25}',
                "quarantined_at": now_iso,
            },
            {
                "issuer_id": "CMG",
                "filing_id": "FIL_CMG_2023_Q2_BAD",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": date(2023, 6, 30),
                "taxonomy_version": "VAS_TT200_2014",
                "rule_code": "UNIT_SCALE_JUMP",
                "severity": "CRITICAL",
                "details_json": '{"ratio": 1000.0}',
                "quarantined_at": now_iso,
            },
            {
                "issuer_id": "FPT",
                "filing_id": "FIL_FPT_2023_Q3_BAD",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": date(2023, 9, 30),
                "taxonomy_version": "VAS_TT200_2014",
                "rule_code": "SUBTOTAL_MISMATCH",
                "severity": "CRITICAL",
                "details_json": '{"diff_pct": 0.12}',
                "quarantined_at": now_iso,
            },
        ],
        schema=AUDIT_QUARANTINE_SCHEMA,
    )
    manifest_df = pl.DataFrame(manifest_rows, schema=FILINGS_MANIFEST_SCHEMA)

    return {
        "issuers.parquet": issuers,
        "financial_facts.parquet": facts_df,
        "latest_facts.parquet": facts_df,
        RATIOS_TABLE: ratios_df,
        BENCHMARKS_TABLE: analytics[BENCHMARKS_TABLE],
        RED_FLAGS_TABLE: analytics[RED_FLAGS_TABLE],
        ML_SCORES_TABLE: ml_df,
        QUALITY_SUMMARY_TABLE: quality_df,
        AUDIT_QUARANTINE_TABLE: quarantine_df,
        FILINGS_MANIFEST_TABLE: manifest_df,
    }


@pytest.fixture(scope="module")
def synthetic_tables() -> dict[str, pl.DataFrame]:
    return _build_synthetic_v2_tables()


@pytest.fixture()
def published_parquet_root(
    tmp_path: Path, synthetic_tables: dict[str, pl.DataFrame]
) -> tuple[Path, str]:
    data_root = tmp_path / "data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    run_id = "run_test_b3_001"
    snapshot_id = "snap_test_b3_001"
    state.start_run(run_id, "MANUAL", {"source": "test"}, None)
    publisher = SnapshotPublisher(
        data_root / "published" / "snapshots", state_store=state
    )
    publisher.publish(snapshot_id, synthetic_tables, run_id=run_id)
    state.finish_run(run_id, "SUCCEEDED", snapshot_id)
    return data_root, snapshot_id


@pytest.fixture()
def published_no_ml_root(
    tmp_path: Path, synthetic_tables: dict[str, pl.DataFrame]
) -> tuple[Path, str]:
    data_root = tmp_path / "data_no_ml"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    run_id = "run_test_noml_001"
    snapshot_id = "snap_test_noml_001"
    state.start_run(run_id, "MANUAL", {"source": "test"}, None)
    no_ml_tables = {k: v for k, v in synthetic_tables.items() if k != ML_SCORES_TABLE}
    publisher = SnapshotPublisher(
        data_root / "published" / "snapshots", state_store=state
    )
    publisher.publish(snapshot_id, no_ml_tables, run_id=run_id)
    state.finish_run(run_id, "SUCCEEDED", snapshot_id)
    return data_root, snapshot_id


# ==============================================================================
# 1. All 10 HTTP Routes, 404/422 Validation, RFC 9110 ETag & 304, 38/38 Lineage
# ==============================================================================


def test_all_10_routes_etag_304_and_38_lineage_cards(
    published_parquet_root: tuple[Path, str],
) -> None:
    data_root, snapshot_id = published_parquet_root
    expected_etag = f'"{snapshot_id}"'
    app = create_app(data_root=data_root)

    with TestClient(app) as client:
        # Route 1: GET /api/v1/health
        r_health = client.get("/api/v1/health")
        assert r_health.status_code == 200
        assert r_health.headers["ETag"] == expected_etag
        assert (
            r_health.headers["Cache-Control"] == "private, max-age=0, must-revalidate"
        )
        assert r_health.json() == {
            "status": "ok",
            "active_snapshot_id": snapshot_id,
            "storage_backend": "parquet",
        }

        # Route 2: GET /api/v1/snapshot/overview
        r_overview = client.get("/api/v1/snapshot/overview")
        assert r_overview.status_code == 200
        ov = r_overview.json()
        assert ov["snapshot_id"] == snapshot_id
        assert ov["storage_backend"] == "parquet"
        assert ov["total_issuers"] == 3
        assert ov["total_ratio_periods"] > 0
        assert ov["pass_periods"] > 0
        assert ov["warning_periods"] > 0
        assert ov["quarantined_rows"] == 3

        # Route 3: GET /api/v1/issuers (paginated)
        r_issuers = client.get("/api/v1/issuers", params={"limit": 2, "offset": 0})
        assert r_issuers.status_code == 200
        iss_body = r_issuers.json()
        assert iss_body["total"] == 3
        assert iss_body["limit"] == 2
        assert iss_body["offset"] == 0
        assert [item["ticker"] for item in iss_body["items"]] == ["CMG", "FPT"]

        r_issuers_p2 = client.get("/api/v1/issuers", params={"limit": 2, "offset": 2})
        assert r_issuers_p2.status_code == 200
        assert [item["ticker"] for item in r_issuers_p2.json()["items"]] == ["HPG"]

        # Route 4: GET /api/v1/companies/{ticker}/profile
        r_prof = client.get(
            "/api/v1/companies/FPT/profile",
            params={"statement_scope": "CONSOLIDATED", "period_view": "TTM"},
        )
        assert r_prof.status_code == 200
        prof = r_prof.json()
        assert prof["ticker"] == "FPT"
        assert prof["latest_quality_status"] == "PASS"
        assert len(prof["growth_series"]) > 0
        assert len(prof["capital_structure_series"]) > 0
        assert len(prof["dupont_series"]) > 0
        assert len(prof["core_earnings_series"]) > 0
        assert prof["core_earnings_series"][-1]["core_operating_margin"] is not None
        assert len(prof["ml_scores"]) > 0
        first_ml = prof["ml_scores"][-1]
        assert first_ml["altman_zone"] == "SAFE_ZONE"
        assert first_ml["shap_top_drivers"] is not None
        assert first_ml["shap_top_drivers"]["other_features_delta"] == pytest.approx(
            -0.1
        )

        # Verify legacy SHAP fallback computes exact additive other_features_delta on HPG.
        r_hpg = client.get("/api/v1/companies/HPG/profile")
        assert r_hpg.status_code == 200
        hpg_shap = r_hpg.json()["ml_scores"][-1]["shap_top_drivers"]
        assert hpg_shap is not None
        assert hpg_shap["other_features_delta"] == pytest.approx(
            0.85 - 0.5 - 0.45 - (-0.20)
        )
        assert (
            hpg_shap["top_risk_increasing"][0]["display_name_vn"] == "interest_coverage"
        )

        # Route 5: GET /api/v1/companies/{ticker}/benchmarks
        r_bench = client.get(
            "/api/v1/companies/FPT/benchmarks",
            params={
                "metric_name": "roe_total",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": "2025-12-31",
                "include_history": "true",
            },
        )
        assert r_bench.status_code == 200
        bench = r_bench.json()
        assert bench["peer_count"] == 3
        assert bench["peer_group_level"] == "REGIME_FALLBACK"
        assert len(bench["peer_ranking"]) == 3
        assert len(bench["history"]) >= 4
        assert "peer_group_level" in bench["history"][0]
        assert "peer_count" in bench["history"][0]

        r_bench_no_hist = client.get(
            "/api/v1/companies/FPT/benchmarks",
            params={"metric_name": "roe_total", "include_history": "false"},
        )
        assert r_bench_no_hist.status_code == 200
        assert r_bench_no_hist.json()["history"] == []

        # Route 6: GET /api/v1/companies/{ticker}/lineage — ALL 38/38 ALL_RATIOS!
        assert len(ALL_RATIOS) == 38
        assert set(ALL_RATIOS) == set(RATIO_LINEAGE_CATALOG)
        for ratio_name in ALL_RATIOS:
            r_lin = client.get(
                "/api/v1/companies/FPT/lineage",
                params={
                    "ratio_name": ratio_name,
                    "statement_scope": "CONSOLIDATED",
                    "period_type": "QUARTER",
                    "period_end_date": "2025-12-31",
                },
            )
            assert r_lin.status_code == 200, f"Failed lineage route for {ratio_name}"
            lin = r_lin.json()
            assert lin["ratio_name"] == ratio_name
            assert lin["orphan_filing_ids"] == []
            assert lin["anchor_filing_id"] in lin["source_filing_ids"]
            assert lin["ratio_value"] is not None, f"Expected non-null {ratio_name}"
            assert len(lin["numerator_components"]) > 0
            assert len(lin["denominator_components"]) > 0

        # Route 7: GET /api/v1/screener
        r_scr = client.get(
            "/api/v1/screener",
            params={
                "period_type": "QUARTER",
                "period_end_date": "2025-12-31",
                "statement_scope": "CONSOLIDATED",
                "icb_code_level2": "9500",
                "min_f_score": 6,
                "altman_zone": "SAFE_ZONE",
                "beneish_flag": "VALID",
                "max_distress_prob": 0.25,
                "max_red_flags": 5,
                "limit": 50,
                "offset": 0,
            },
        )
        assert r_scr.status_code == 200
        scr = r_scr.json()
        assert scr["total"] == 1
        assert scr["items"][0]["ticker"] == "FPT"
        assert set(scr["items"][0].keys()) == set(SCREENER_SCHEMA.keys())

        # Route 8: POST /api/v1/stress-test
        shock_payload = {
            "scenario_name": "Rate & Demand Shock",
            "statement_scope": "CONSOLIDATED",
            "period_type": "QUARTER",
            "period_end_date": "2025-12-31",
            "revenue_shock_pct": -0.25,
            "gross_margin_shock_pp": -0.05,
            "interest_rate_shock_bps": 400.0,
            "dso_shock_days": 15.0,
            "dio_shock_days": 10.0,
            "top_n": 50,
        }
        r_stress = client.post("/api/v1/stress-test", json=shock_payload)
        assert r_stress.status_code == 200
        assert r_stress.headers["Cache-Control"] == "private, no-store"
        st_body = r_stress.json()
        assert st_body["total_issuers_evaluated"] == 3
        assert (
            st_body["stressed_debt_at_risk_pct"] >= st_body["baseline_debt_at_risk_pct"]
        )
        assert len(st_body["sector_summary"]) == 2

        # Verify 100% mathematical parity with core simulate_financial_stress & summarize_stress_by_sector.
        svc = DashboardQueryService(ParquetRepository(data_root=data_root))
        raw_inputs = svc.get_stress_test_snapshot_inputs(
            "CONSOLIDATED", "QUARTER", date(2025, 12, 31)
        )
        direct_stressed = simulate_financial_stress(
            raw_inputs,
            StressScenario(
                name="Rate & Demand Shock",
                interest_rate_shock_bps=400.0,
                revenue_shock_pct=-0.25,
                gross_margin_shock_pp=-0.05,
                dso_shock_days=15.0,
                dio_shock_days=10.0,
            ),
        )
        direct_sectors = summarize_stress_by_sector(direct_stressed).to_dicts()
        assert len(st_body["sector_summary"]) == len(direct_sectors)
        for api_sec, core_sec in zip(
            st_body["sector_summary"], direct_sectors, strict=True
        ):
            assert api_sec["icb_code_level2"] == core_sec["icb_code_level2"]
            assert api_sec["stressed_debt_at_risk_pct"] == pytest.approx(
                core_sec["stressed_debt_at_risk_pct"]
            )

        # Route 9: GET /api/v1/quality/summary
        r_qs = client.get("/api/v1/quality/summary", params={"limit": 5, "offset": 0})
        assert r_qs.status_code == 200
        qs_body = r_qs.json()
        assert qs_body["total"] == ov["pass_periods"] + ov["warning_periods"]
        assert len(qs_body["items"]) == 5

        # Route 10: GET /api/v1/quality/quarantine
        r_qq = client.get(
            "/api/v1/quality/quarantine", params={"limit": 2, "offset": 0}
        )
        assert r_qq.status_code == 200
        qq_body = r_qq.json()
        assert qq_body["total"] == 3
        assert len(qq_body["items"]) == 2

        # RFC 9110 Conditional GET -> 304 Not Modified (strong, weak W/, comma list, wildcard, mismatch)
        get_routes = [
            "/api/v1/health",
            "/api/v1/snapshot/overview",
            "/api/v1/issuers",
            "/api/v1/companies/FPT/profile",
            "/api/v1/companies/FPT/benchmarks?metric_name=roe_total",
            "/api/v1/companies/FPT/lineage?ratio_name=roe_total&period_end_date=2025-12-31",
            "/api/v1/screener",
            "/api/v1/quality/summary",
            "/api/v1/quality/quarantine",
        ]
        for url in get_routes:
            for inm in (
                expected_etag,
                f"W/{expected_etag}",
                f'"snap_old", W/{expected_etag}',
                "*",
            ):
                r_304 = client.get(url, headers={"If-None-Match": inm})
                assert r_304.status_code == 304, f"Expected 304 for {url} with {inm}"
                assert r_304.content == b""
                assert r_304.headers["ETag"] == expected_etag
                assert (
                    r_304.headers["Cache-Control"]
                    == "private, max-age=0, must-revalidate"
                )
            r_miss = client.get(url, headers={"If-None-Match": '"snap_mismatch"'})
            assert r_miss.status_code == 200


def test_http_404_and_422_strict_validation(
    published_parquet_root: tuple[Path, str],
) -> None:
    data_root, _ = published_parquet_root
    app = create_app(data_root=data_root)

    with TestClient(app) as client:
        # 404 unknown ticker
        assert client.get("/api/v1/companies/ZZZZ/profile").status_code == 404
        assert client.get("/api/v1/companies/ZZZZ/benchmarks").status_code == 404
        assert (
            client.get(
                "/api/v1/companies/ZZZZ/lineage",
                params={"ratio_name": "roe_total", "period_end_date": "2025-12-31"},
            ).status_code
            == 404
        )
        # 404 valid ticker, nonexistent period
        assert (
            client.get(
                "/api/v1/companies/FPT/lineage",
                params={"ratio_name": "roe_total", "period_end_date": "2010-03-31"},
            ).status_code
            == 404
        )

        # 422 bad ticker regex
        assert client.get("/api/v1/companies/fpt/profile").status_code == 422
        assert client.get("/api/v1/companies/BAD-TICKER/profile").status_code == 422
        assert (
            client.get("/api/v1/companies/TOOLONGTICKER99/profile").status_code == 422
        )

        # 422 bad enums & limits > 200 / offset < 0
        assert client.get("/api/v1/issuers", params={"limit": 201}).status_code == 422
        assert client.get("/api/v1/issuers", params={"offset": -1}).status_code == 422
        assert client.get("/api/v1/screener", params={"limit": 201}).status_code == 422
        assert (
            client.get(
                "/api/v1/screener", params={"altman_zone": "BAD_ZONE"}
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/api/v1/screener", params={"beneish_flag": "UNKNOWN"}
            ).status_code
            == 422
        )
        assert (
            client.get("/api/v1/screener", params={"min_f_score": 10}).status_code
            == 422
        )
        assert (
            client.get(
                "/api/v1/companies/FPT/profile", params={"period_view": "Monthly"}
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/api/v1/companies/FPT/lineage", params={"ratio_name": "fake_ratio"}
            ).status_code
            == 422
        )
        assert (
            client.get("/api/v1/quality/summary", params={"limit": 205}).status_code
            == 422
        )
        assert (
            client.get("/api/v1/quality/quarantine", params={"limit": 0}).status_code
            == 422
        )

        # 422 StressScenarioRequest bounds, extra fields, blank name
        invalid_payloads = [
            {"revenue_shock_pct": -0.81},
            {"revenue_shock_pct": 0.51},
            {"gross_margin_shock_pp": -0.55},
            {"gross_margin_shock_pp": 0.25},
            {"interest_rate_shock_bps": -501.0},
            {"interest_rate_shock_bps": 1501.0},
            {"dso_shock_days": -91.0},
            {"dio_shock_days": 181.0},
            {"top_n": 201},
            {"scenario_name": "   "},
            {"unexpected_field": 123},
        ]
        for bad_body in invalid_payloads:
            resp = client.post("/api/v1/stress-test", json=bad_body)
            assert resp.status_code == 422, f"Expected 422 for {bad_body}"


# ==============================================================================
# 2. Real Multithreaded Concurrency (20 Threads x 200 Requests) + Shutdown close()
# ==============================================================================


def test_20_threads_200_concurrent_requests_and_shutdown_close(
    published_parquet_root: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root, snapshot_id = published_parquet_root
    expected_etag = f'"{snapshot_id}"'

    # Verify sync def cursor dependency and sync def DB-reading routes.
    assert not inspect.iscoroutinefunction(get_db_cursor)

    close_calls: list[bool] = []
    real_connect = duckdb.connect

    class _TrackedConnection:
        def __init__(self, inner: duckdb.DuckDBPyConnection) -> None:
            self._inner = inner

        def cursor(self) -> duckdb.DuckDBPyConnection:
            return self._inner.cursor()

        def execute(self, *args: Any, **kwargs: Any) -> Any:
            return self._inner.execute(*args, **kwargs)

        def close(self) -> None:
            close_calls.append(True)
            self._inner.close()

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    def _spy_connect(database: str = ":memory:", **kwargs: Any) -> Any:
        conn = real_connect(database, **kwargs)
        if database == ":memory:":
            return _TrackedConnection(conn)
        return conn

    monkeypatch.setattr("finintel.serving.dependencies.duckdb.connect", _spy_connect)
    app = create_app(data_root=data_root)

    # Confirm all /api/v1 route handlers are synchronous defs (not async def).
    api_routes = [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/api/v1/")
    ]
    assert len(api_routes) == 10
    for route in api_routes:
        assert not inspect.iscoroutinefunction(route.endpoint), (
            f"Route {route.path} must be sync def for DuckDB cursor threadpool safety"
        )

    request_plan = [
        ("GET", "/api/v1/companies/FPT/profile?period_view=TTM", None),
        (
            "GET",
            "/api/v1/companies/FPT/benchmarks?metric_name=roe_total&include_history=true",
            None,
        ),
        (
            "GET",
            "/api/v1/companies/FPT/lineage?ratio_name=core_operating_margin&period_end_date=2025-12-31",
            None,
        ),
        ("GET", "/api/v1/screener?period_type=QUARTER&limit=50", None),
        (
            "POST",
            "/api/v1/stress-test",
            {
                "scenario_name": "Concurrent Shock",
                "revenue_shock_pct": -0.15,
                "interest_rate_shock_bps": 200.0,
                "top_n": 20,
            },
        ),
    ]

    with TestClient(app) as client:
        assert len(close_calls) == 0

        def _worker(idx: int) -> tuple[int, str | None, str | None]:
            method, url, body = request_plan[idx % len(request_plan)]
            if method == "POST":
                resp = client.post(url, json=body)
            else:
                resp = client.get(url)
            data = resp.json()
            return resp.status_code, resp.headers.get("ETag"), data.get("snapshot_id")

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(_worker, range(200)))

        assert len(results) == 200
        assert all(status == 200 for status, _, _ in results)
        assert all(etag == expected_etag for _, etag, _ in results)
        assert all(snap == snapshot_id for _, _, snap in results)

    # Lifespan shutdown MUST invoke duckdb_conn.close() cleanly.
    assert len(close_calls) == 1


def test_ducklake_read_only_lifespan_attach_and_serve(
    tmp_path: Path, synthetic_tables: dict[str, pl.DataFrame]
) -> None:
    data_root = tmp_path / "lake_data_root"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    run_id = "run_lake_b3_001"
    snapshot_id = "snap_lake_b3_001"
    state.start_run(run_id, "MANUAL", {"backend": "ducklake"}, None)

    store = DuckLakeStore(
        catalog_path=data_root / "metadata" / "ducklake_catalog.sqlite",
        data_path=data_root / "lakehouse" / "data",
        state_store=state,
    )
    try:
        store.publish_snapshot(snapshot_id, synthetic_tables, run_id=run_id)
    except duckdb.Error as exc:
        pytest.skip(f"DuckLake extension unavailable in this environment: {exc}")
    state.finish_run(run_id, "SUCCEEDED", snapshot_id)

    repo = ParquetRepository(data_root=data_root, backend="ducklake")
    app = create_app(repository=repo)
    with TestClient(app) as client:
        r_health = client.get("/api/v1/health")
        assert r_health.status_code == 200
        assert r_health.json() == {
            "status": "ok",
            "active_snapshot_id": snapshot_id,
            "storage_backend": "ducklake",
        }
        r_ov = client.get("/api/v1/snapshot/overview")
        assert r_ov.status_code == 200
        assert r_ov.json()["total_issuers"] == 3
        assert r_ov.json()["storage_backend"] == "ducklake"
        assert client.get("/api/v1/companies/FPT/profile").status_code == 200
        assert client.get("/api/v1/screener").status_code == 200


# ==============================================================================
# 3. Pushdown & Overview Efficiency Spy + Thin Shim Verification
# ==============================================================================


def test_pushdown_and_overview_efficiency_spy(
    published_parquet_root: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root, snapshot_id = published_parquet_root
    svc = DashboardQueryService(ParquetRepository(data_root=data_root))

    read_calls: list[tuple[str, Any, Any]] = []
    meta_calls: list[str] = []
    orig_read_table = ParquetRepository.read_table
    orig_get_meta = ParquetRepository.get_snapshot_metadata

    def _spy_read(
        self: ParquetRepository,
        table_name: str,
        *,
        columns: Any = None,
        filters: Any = None,
        connection: Any = None,
    ) -> pl.DataFrame:
        read_calls.append((table_name, columns, filters))
        return orig_read_table(
            self,
            table_name,
            columns=columns,
            filters=filters,
            connection=connection,
        )

    def _spy_meta(
        self: ParquetRepository, *, connection: Any = None
    ) -> dict[str, object]:
        meta_calls.append("called")
        return orig_get_meta(self, connection=connection)

    monkeypatch.setattr(ParquetRepository, "read_table", _spy_read)
    monkeypatch.setattr(ParquetRepository, "get_snapshot_metadata", _spy_meta)

    # 1. get_snapshot_overview MUST use get_snapshot_metadata() + DuckDB COUNT(*), ZERO read_table() calls!
    ov = svc.get_snapshot_overview()
    assert ov.snapshot_id == snapshot_id
    assert ov.total_issuers == 3
    assert len(meta_calls) >= 1
    assert len(read_calls) == 0

    # 2. get_company_profile MUST pass columns & filters (with issuer_id on all non-issuers tables)
    read_calls.clear()
    profile = svc.get_company_profile("FPT", period_view="TTM")
    assert profile.issuer_id == "FPT"
    assert len(read_calls) >= 4
    for tbl, cols, flt in read_calls:
        assert cols is not None and len(cols) > 0, f"Missing columns pushdown on {tbl}"
        assert flt is not None and len(flt) > 0, f"Missing filters pushdown on {tbl}"
        if tbl != "issuers.parquet":
            assert flt.get("issuer_id") == "FPT", f"Missing issuer_id filter on {tbl}"

    # 3. get_industry_comparison MUST pass columns & filters
    read_calls.clear()
    comp = svc.get_industry_comparison(
        "FPT", period_type="QUARTER", period_end_date=date(2025, 12, 31)
    )
    assert comp.peer_count == 3
    assert any(
        tbl == BENCHMARKS_TABLE and flt and flt.get("issuer_id") == "FPT"
        for tbl, _cols, flt in read_calls
    )
    for tbl, cols, flt in read_calls:
        assert cols is not None and len(cols) > 0
        assert flt is not None and len(flt) > 0

    # 4. list_ratio_periods MUST map ticker -> issuer_id and push columns & filters
    read_calls.clear()
    periods = svc.list_ratio_periods("FPT", statement_scope="CONSOLIDATED")
    assert not periods.is_empty()
    ratios_calls = [c for c in read_calls if c[0] == RATIOS_TABLE]
    assert len(ratios_calls) == 1
    assert ratios_calls[0][1] is not None
    assert ratios_calls[0][2]["issuer_id"] == "FPT"

    # 5. get_ratio_lineage MUST map ticker -> issuer_id and push columns & filters
    read_calls.clear()
    card = svc.get_ratio_lineage(
        "FPT",
        statement_scope="CONSOLIDATED",
        period_type="QUARTER",
        period_end_date=date(2025, 12, 31),
        ratio_name="roe_total",
    )
    assert card.orphan_filing_ids == ()
    for tbl, cols, flt in read_calls:
        assert cols is not None and len(cols) > 0
        assert flt is not None and len(flt) > 0
        if tbl != "issuers.parquet":
            assert flt.get("issuer_id") == "FPT", f"Missing issuer_id pushdown on {tbl}"


def test_serving_queries_helpers_and_zero_anti_patterns() -> None:
    # Canonical catalog and UI conversion/pagination helpers exposed via serving.queries.
    assert len(ALL_RATIOS) == 38
    assert len(RATIO_LINEAGE_CATALOG) == 38
    assert DashboardQueryService is not None
    assert StressScenario is not None
    assert simulate_financial_stress is not None
    assert summarize_stress_by_sector is not None

    assert percent_to_decimal(-25.0) == pytest.approx(-0.25)
    assert percent_to_decimal(0.0) == pytest.approx(0.0)
    with pytest.raises(ValueError):
        percent_to_decimal(float("nan"))
    assert pagination_offset(1, 50) == 0
    assert pagination_offset(3, 50) == 100
    assert pagination_total_pages(0, 50) == 1
    assert pagination_total_pages(101, 50) == 3
    assert pagination_window(0, 50, 120) == (1, 1, 50)
    assert pagination_window(100, 50, 120) == (3, 101, 120)
    assert pagination_window(0, 50, 0) == (1, 0, 0)

    # Verify zero iter_rows(), zero blind .fill_null(0), zero streamlit/dashboard imports in serving/.
    serving_dir = Path(__file__).resolve().parents[2] / "src" / "finintel" / "serving"
    for py_file in sorted(serving_dir.glob("*.py")):
        src = py_file.read_text(encoding="utf-8")
        assert "iter_rows(" not in src, f"Forbidden iter_rows() in {py_file.name}"
        assert ".fill_null(0" not in src, f"Forbidden .fill_null(0) in {py_file.name}"
        assert "Verified Sources:" in src, (
            f"Missing Verified Sources docstring in {py_file.name}"
        )
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(("streamlit", "dashboard")), (
                        f"Forbidden import {alias.name} in {py_file.name}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(("streamlit", "dashboard")), (
                    f"Forbidden import from {node.module} in {py_file.name}"
                )


# ==============================================================================
# 4. Security, CORS, Semaphore, Error Sanitization & No-ML LEFT JOIN Resilience
# ==============================================================================


def test_security_cors_defaults_semaphore_and_error_sanitization(
    published_parquet_root: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root, _ = published_parquet_root

    # Default uvicorn bind host must be loopback 127.0.0.1.
    assert DEFAULT_API_HOST == "127.0.0.1"

    # Deny-by-default CORS when FININTEL_CORS_ORIGINS is unset.
    monkeypatch.delenv("FININTEL_CORS_ORIGINS", raising=False)
    app_default = create_app(data_root=data_root)
    with TestClient(app_default) as client:
        resp = client.get(
            "/api/v1/health", headers={"Origin": "https://untrusted.example"}
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}

    # Explicit CORS allowlist via FININTEL_CORS_ORIGINS allows only configured origin.
    monkeypatch.setenv("FININTEL_CORS_ORIGINS", "http://localhost:8501")
    app_cors = create_app(data_root=data_root)
    with TestClient(app_cors) as client:
        allowed = client.get(
            "/api/v1/health", headers={"Origin": "http://localhost:8501"}
        )
        assert allowed.status_code == 200
        assert (
            allowed.headers.get("access-control-allow-origin")
            == "http://localhost:8501"
        )
        denied = client.get(
            "/api/v1/health", headers={"Origin": "https://evil.example"}
        )
        assert denied.status_code == 200
        assert "access-control-allow-origin" not in {k.lower() for k in denied.headers}
    monkeypatch.delenv("FININTEL_CORS_ORIGINS", raising=False)

    # Stress-test Semaphore(4) saturation returns 503 Service Unavailable with Retry-After.
    app_sem = create_app(
        data_root=data_root,
        stress_max_concurrency=4,
        stress_semaphore_timeout=0.05,
    )
    with TestClient(app_sem) as client:
        sem: threading.Semaphore = app_sem.state.stress_semaphore
        for _ in range(4):
            assert sem.acquire(blocking=False)
        try:
            r_busy = client.post("/api/v1/stress-test", json={"scenario_name": "Busy"})
            assert r_busy.status_code == 503
            assert r_busy.headers.get("Retry-After") == "1"
        finally:
            for _ in range(4):
                sem.release()

    # Global error sanitizer: 500, 422, 404 NEVER leak file paths or tracebacks.
    app_err = create_app(data_root=data_root)
    with TestClient(app_err, raise_server_exceptions=False) as client:

        def _boom(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError(
                r"Fatal error reading C:\Users\secret\data\financial_ratios.parquet"
            )

        monkeypatch.setattr(DashboardQueryService, "get_snapshot_overview", _boom)
        r_500 = client.get("/api/v1/snapshot/overview")
        assert r_500.status_code == 500
        assert r_500.json() == {"detail": "Internal serving error"}
        assert "secret" not in r_500.text
        assert ".parquet" not in r_500.text

        def _contract_err(*_args: Any, **_kwargs: Any) -> Any:
            raise ContractViolationError(
                "Invalid table at /srv/finintel/published/snapshots/snap_1/financial_ratios.parquet"
            )

        monkeypatch.setattr(
            DashboardQueryService, "get_snapshot_overview", _contract_err
        )
        r_422 = client.get("/api/v1/snapshot/overview")
        assert r_422.status_code == 422
        assert "/srv/" not in r_422.text
        assert ".parquet" not in r_422.text


def test_snapshot_without_ml_table_serves_profile_and_screener_200(
    published_no_ml_root: tuple[Path, str],
) -> None:
    data_root, _ = published_no_ml_root
    app = create_app(data_root=data_root)

    with TestClient(app) as client:
        r_prof = client.get("/api/v1/companies/FPT/profile")
        assert r_prof.status_code == 200
        assert r_prof.json()["ml_scores"] == []

        r_scr = client.get("/api/v1/screener", params={"period_type": "QUARTER"})
        assert r_scr.status_code == 200
        body = r_scr.json()
        assert body["total"] == 3
        assert all(item["altman_zone"] is None for item in body["items"])
        assert all(item["calibrated_distress_prob"] is None for item in body["items"])


def test_sector_risk_summary_high_red_flag_count_matches_screener_min_2(
    tmp_path: Path, synthetic_tables: dict[str, pl.DataFrame]
) -> None:
    data_root = tmp_path / "data_rf_threshold"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    run_id = "run_test_rf2_001"
    snapshot_id = "snap_test_rf2_001"
    state.start_run(run_id, "MANUAL", {"source": "test"}, None)

    red_flags_df = pl.DataFrame(
        [
            {
                "issuer_id": "HPG",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": date(2025, 12, 31),
                "flag_code": "RF_05_DEBT_SERVICE_DISTRESS",
                "severity": "CRITICAL",
                "flag_title_vn": "Áp lực trả nợ",
                "explanation_text_vn": "Test RF 1",
                "evidence_json": "{}",
                "filing_id": "FIL_HPG_2025_Q4",
            },
            {
                "issuer_id": "HPG",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": date(2025, 12, 31),
                "flag_code": "RF_08_NON_CORE_EARNINGS_DEPENDENCE",
                "severity": "HIGH",
                "flag_title_vn": "Phụ thuộc lợi nhuận phi cốt lõi",
                "explanation_text_vn": "Test RF 2",
                "evidence_json": "{}",
                "filing_id": "FIL_HPG_2025_Q4",
            },
            {
                "issuer_id": "CMG",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": date(2025, 12, 31),
                "flag_code": "RF_02_RECEIVABLES_SURGE",
                "severity": "MEDIUM",
                "flag_title_vn": "Phải thu tăng nhanh",
                "explanation_text_vn": "Test RF 3",
                "evidence_json": "{}",
                "filing_id": "FIL_CMG_2025_Q4",
            },
        ],
        schema=synthetic_tables[RED_FLAGS_TABLE].schema,
    )
    tables = {**synthetic_tables, RED_FLAGS_TABLE: red_flags_df}
    publisher = SnapshotPublisher(
        data_root / "published" / "snapshots", state_store=state
    )
    publisher.publish(snapshot_id, tables, run_id=run_id)
    state.finish_run(run_id, "SUCCEEDED", snapshot_id)

    app = create_app(data_root=data_root)
    with TestClient(app) as client:
        r_sum = client.get(
            "/api/v2/sectors/risk-summary",
            params={
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": "2025-12-31",
            },
        )
        assert r_sum.status_code == 200
        summary = r_sum.json()

        r_scr = client.get(
            "/api/v1/screener",
            params={
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": "2025-12-31",
                "min_red_flags": 2,
            },
        )
        assert r_scr.status_code == 200
        screener = r_scr.json()

        assert summary["high_red_flag_count"] == 1
        assert screener["total"] == 1
        assert summary["high_red_flag_count"] == screener["total"]


# ==============================================================================
# 5. Opt-In Real Empirical Snapshot Verification (FININTEL_EMPIRICAL_SNAPSHOT_DIR)
# ==============================================================================


@pytest.mark.skipif(
    not os.environ.get("FININTEL_EMPIRICAL_SNAPSHOT_DIR"),
    reason="Set FININTEL_EMPIRICAL_SNAPSHOT_DIR to run read-only empirical snapshot tests.",
)
class TestEmpiricalRealSnapshotServing:
    """Read-only empirical tests against a real 239-issuer snapshot (never mutates source)."""

    @staticmethod
    def _empirical_snapshot_dir() -> Path:
        raw = os.environ["FININTEL_EMPIRICAL_SNAPSHOT_DIR"]
        path = Path(raw).resolve()
        assert path.is_dir(), (
            "FININTEL_EMPIRICAL_SNAPSHOT_DIR must point to a snapshot directory."
        )
        return path

    def test_legacy_snapshot_direct_read_only_all_routes(self) -> None:
        snap_dir = self._empirical_snapshot_dir()
        repo = ParquetRepository(path=snap_dir, backend="parquet")
        app = create_app(repository=repo)

        with TestClient(app) as client:
            r_health = client.get("/api/v1/health")
            assert r_health.status_code == 200
            snap_id = r_health.json()["active_snapshot_id"]
            assert snap_id is not None
            etag = f'"{snap_id}"'
            assert r_health.headers["ETag"] == etag

            # 304 check on real snapshot
            r_304 = client.get("/api/v1/health", headers={"If-None-Match": f"W/{etag}"})
            assert r_304.status_code == 304

            r_ov = client.get("/api/v1/snapshot/overview")
            assert r_ov.status_code == 200
            assert r_ov.json()["total_issuers"] >= 200

            r_iss = client.get("/api/v1/issuers", params={"limit": 50, "offset": 0})
            assert r_iss.status_code == 200
            tickers = [row["ticker"] for row in r_iss.json()["items"]]
            sample_ticker = tickers[0]

            # Profile with real ML + legacy SHAP fallback
            r_prof = client.get(f"/api/v1/companies/{sample_ticker}/profile")
            assert r_prof.status_code == 200
            prof = r_prof.json()
            assert len(prof["ratios_table"]) > 0
            shap_items = [
                row["shap_top_drivers"]
                for row in prof["ml_scores"]
                if row["shap_top_drivers"] is not None
            ]
            assert len(shap_items) > 0
            for shap_item in shap_items[:5]:
                reconstructed = (
                    shap_item["base_value"]
                    + sum(d["shap_delta"] for d in shap_item["top_risk_increasing"])
                    + sum(d["shap_delta"] for d in shap_item["top_risk_decreasing"])
                    + shap_item["other_features_delta"]
                )
                assert reconstructed == pytest.approx(
                    shap_item["prediction_score"], abs=1e-6
                )

            # Benchmarks with history
            r_bench = client.get(
                f"/api/v1/companies/{sample_ticker}/benchmarks",
                params={"metric_name": "roe_total", "include_history": "true"},
            )
            assert r_bench.status_code == 200
            assert len(r_bench.json()["history"]) > 0

            # Lineage on real snapshot
            r_lin = client.get(
                f"/api/v1/companies/{sample_ticker}/lineage",
                params={"ratio_name": "roe_total"},
            )
            assert r_lin.status_code == 200
            assert r_lin.json()["orphan_filing_ids"] == []

            # Screener on latest quarter (unfiltered) AND on latest ML-scored quarter with real ML filters
            r_scr_latest = client.get(
                "/api/v1/screener",
                params={"period_type": "QUARTER", "limit": 50},
            )
            assert r_scr_latest.status_code == 200
            assert r_scr_latest.json()["total"] > 100

            ml_quarter_dates = (
                pl.scan_parquet(snap_dir / "ml_scores_and_explanations.parquet")
                .filter(
                    (pl.col("period_type") == "QUARTER")
                    & (pl.col("altman_zone") == "SAFE_ZONE")
                    & pl.col("calibrated_distress_prob").is_not_null()
                )
                .select(pl.col("period_end_date").max())
                .collect()
                .item()
            )
            assert isinstance(ml_quarter_dates, date)
            r_scr = client.get(
                "/api/v1/screener",
                params={
                    "period_type": "QUARTER",
                    "period_end_date": ml_quarter_dates.isoformat(),
                    "altman_zone": "SAFE_ZONE",
                    "beneish_flag": "VALID",
                    "max_distress_prob": 0.5,
                    "limit": 50,
                },
            )
            assert r_scr.status_code == 200
            assert r_scr.json()["total"] > 0

            # Quality routes gracefully return empty 200 OK when absent in legacy snapshot
            assert client.get("/api/v1/quality/summary").status_code == 200
            assert client.get("/api/v1/quality/quarantine").status_code == 200

            # Stress-test gracefully returns sanitized 422 on legacy snapshot missing stress intermediates
            r_st = client.post(
                "/api/v1/stress-test", json={"scenario_name": "LegacyCheck"}
            )
            assert r_st.status_code == 422
            assert str(snap_dir) not in r_st.text

    def test_v2_rebuilt_real_239_issuer_snapshot_all_10_routes_and_concurrency(
        self, tmp_path: Path
    ) -> None:
        snap_dir = self._empirical_snapshot_dir()
        raw_facts = pl.read_parquet(snap_dir / "financial_facts.parquet")
        issuers = pl.read_parquet(snap_dir / "issuers.parquet")
        ml_scores = pl.read_parquet(snap_dir / "ml_scores_and_explanations.parquet")

        qg1 = evaluate_quality_gate(raw_facts, issuers=issuers, run_id="emp_v2_run")
        clean_filing_ids = qg1.clean_facts.select("filing_id").unique()
        clean_ml_scores = ml_scores.join(clean_filing_ids, on="filing_id", how="semi")
        analytics = build_analytics_tables(
            qg1.clean_facts, issuers, as_of=date(2026, 11, 19)
        )
        updated_qs = verify_analytics_quality_gate(
            qg1.clean_facts,
            analytics[RATIOS_TABLE],
            benchmarks=analytics[BENCHMARKS_TABLE],
            red_flags=analytics[RED_FLAGS_TABLE],
            ml_scores=clean_ml_scores,
            quality_summary=qg1.quality_summary,
            run_id="emp_v2_run",
        )

        # Publish rebuilt V2 snapshot strictly into tmp_path (never mutating snap_dir!).
        data_root = tmp_path / "empirical_v2_data"
        state = StateStore(data_root / "metadata" / "state.sqlite")
        state.initialize()
        state.start_run("emp_v2_run", "MANUAL", {"empirical": True}, None)
        publisher = SnapshotPublisher(
            data_root / "published" / "snapshots", state_store=state
        )
        v2_tables = {
            "issuers.parquet": issuers,
            "financial_facts.parquet": qg1.clean_facts,
            RATIOS_TABLE: analytics[RATIOS_TABLE],
            BENCHMARKS_TABLE: analytics[BENCHMARKS_TABLE],
            RED_FLAGS_TABLE: analytics[RED_FLAGS_TABLE],
            ML_SCORES_TABLE: clean_ml_scores,
            QUALITY_SUMMARY_TABLE: updated_qs,
            AUDIT_QUARANTINE_TABLE: qg1.audit_quarantine,
        }
        snapshot_id = "snap_empirical_v2_001"
        publisher.publish(snapshot_id, v2_tables, run_id="emp_v2_run")
        state.finish_run("emp_v2_run", "SUCCEEDED", snapshot_id)

        app = create_app(data_root=data_root)
        with TestClient(app) as client:
            # 1-2. Health & Overview
            r_health = client.get("/api/v1/health")
            assert r_health.status_code == 200
            assert r_health.json()["active_snapshot_id"] == snapshot_id
            r_ov = client.get("/api/v1/snapshot/overview")
            assert r_ov.status_code == 200
            ov = r_ov.json()
            assert ov["total_issuers"] == issuers.height
            assert ov["pass_periods"] + ov["warning_periods"] == updated_qs.height
            assert ov["quarantined_rows"] == qg1.audit_quarantine.height

            # 3. Issuers
            r_iss = client.get("/api/v1/issuers", params={"limit": 200, "offset": 0})
            assert r_iss.status_code == 200
            assert r_iss.json()["total"] == issuers.height

            # Pick a real issuer with all 3 forensic ratios computed at a recent quarter
            forensic_candidates = analytics[RATIOS_TABLE].filter(
                (pl.col("period_type") == "QUARTER")
                & pl.col("core_operating_margin").is_not_null()
                & pl.col("non_core_distortion_to_assets").is_not_null()
                & pl.col("core_to_ebt_ratio").is_not_null()
            )
            assert not forensic_candidates.is_empty()
            sample_row = forensic_candidates.sort("period_end_date").row(-1, named=True)
            sample_ticker = str(sample_row["ticker"])
            sample_end = sample_row["period_end_date"].isoformat()

            # 4. Profile (verify real RF_08 presence on dataset + core earnings)
            rf08_tickers = (
                analytics[RED_FLAGS_TABLE]
                .filter(pl.col("flag_code") == "RF_08_NON_CORE_EARNINGS_DEPENDENCE")
                .join(
                    issuers.select("issuer_id", "ticker"), on="issuer_id", how="inner"
                )
                .get_column("ticker")
                .unique()
                .to_list()
            )
            assert len(rf08_tickers) > 0
            r_prof = client.get(f"/api/v1/companies/{rf08_tickers[0]}/profile")
            assert r_prof.status_code == 200
            assert any(
                rf["flag_code"] == "RF_08_NON_CORE_EARNINGS_DEPENDENCE"
                for rf in r_prof.json()["red_flags"]
            )

            # 5. Benchmarks
            r_bench = client.get(
                f"/api/v1/companies/{sample_ticker}/benchmarks",
                params={
                    "metric_name": "core_operating_margin",
                    "include_history": "true",
                },
            )
            assert r_bench.status_code == 200
            assert len(r_bench.json()["history"]) > 0

            # 6. ALL 38/38 ALL_RATIOS Lineage Cards on real Vietnamese corporate data!
            for ratio_name in ALL_RATIOS:
                r_lin = client.get(
                    f"/api/v1/companies/{sample_ticker}/lineage",
                    params={
                        "ratio_name": ratio_name,
                        "statement_scope": "CONSOLIDATED",
                        "period_type": "QUARTER",
                        "period_end_date": sample_end,
                    },
                )
                assert r_lin.status_code == 200
                assert r_lin.json()["orphan_filing_ids"] == []

            # 7. Screener
            r_scr = client.get(
                "/api/v1/screener",
                params={
                    "period_type": "QUARTER",
                    "period_end_date": sample_end,
                    "limit": 50,
                },
            )
            assert r_scr.status_code == 200
            assert r_scr.json()["total"] > 50

            # 8. Stress test on real 239-issuer universe!
            r_stress = client.post(
                "/api/v1/stress-test",
                json={
                    "scenario_name": "VN Severe Macro Shock",
                    "statement_scope": "CONSOLIDATED",
                    "period_type": "QUARTER",
                    "period_end_date": sample_end,
                    "revenue_shock_pct": -0.30,
                    "gross_margin_shock_pp": -0.05,
                    "interest_rate_shock_bps": 400.0,
                    "dso_shock_days": 30.0,
                    "dio_shock_days": 20.0,
                    "top_n": 50,
                },
            )
            assert r_stress.status_code == 200
            st_data = r_stress.json()
            assert st_data["total_issuers_evaluated"] > 100
            assert (
                st_data["stressed_debt_at_risk_pct"]
                >= st_data["baseline_debt_at_risk_pct"]
            )
            assert len(st_data["sector_summary"]) >= 5

            # 9-10. Quality summary & quarantine paginated
            r_qs = client.get(
                "/api/v1/quality/summary", params={"limit": 50, "offset": 0}
            )
            assert r_qs.status_code == 200
            assert r_qs.json()["total"] == updated_qs.height
            assert len(r_qs.json()["items"]) == 50

            r_qq = client.get(
                "/api/v1/quality/quarantine", params={"limit": 50, "offset": 0}
            )
            assert r_qq.status_code == 200
            assert r_qq.json()["total"] == qg1.audit_quarantine.height

            # 20 threads x 200 concurrent requests on real 239-issuer snapshot!
            plan = [
                ("GET", f"/api/v1/companies/{sample_ticker}/profile", None),
                (
                    "GET",
                    f"/api/v1/companies/{sample_ticker}/benchmarks?metric_name=roe_total&include_history=true",
                    None,
                ),
                (
                    "GET",
                    f"/api/v1/companies/{sample_ticker}/lineage?ratio_name=core_operating_margin&period_end_date={sample_end}",
                    None,
                ),
                (
                    "GET",
                    f"/api/v1/screener?period_end_date={sample_end}&limit=50",
                    None,
                ),
                (
                    "POST",
                    "/api/v1/stress-test",
                    {
                        "scenario_name": "Real Concurrent Stress",
                        "period_end_date": sample_end,
                        "revenue_shock_pct": -0.20,
                        "interest_rate_shock_bps": 300.0,
                        "top_n": 25,
                    },
                ),
            ]

            def _run(i: int) -> int:
                m, u, b = plan[i % len(plan)]
                res = client.post(u, json=b) if m == "POST" else client.get(u)
                return int(res.status_code)

            with ThreadPoolExecutor(max_workers=20) as pool:
                codes = list(pool.map(_run, range(200)))
            assert len(codes) == 200
            assert all(code == 200 for code in codes)


def test_ml07a_serving_exposes_credit_risk_triad_and_supports_legacy_snapshots(
    published_parquet_root: tuple[Path, str],
    synthetic_tables: dict[str, pl.DataFrame],
    tmp_path: Path,
) -> None:
    """Verify ML-07a additive fields across /profile, /screener, /market/changes + legacy fallback."""
    data_root, _ = published_parquet_root
    app = create_app(data_root=data_root)
    with TestClient(app) as client:
        # 1. /api/v1/companies/HPG/profile exposes risk_tier, risk_market_percentile, market_base_rate, relative_risk_lift
        prof = client.get("/api/v1/companies/HPG/profile")
        assert prof.status_code == 200
        ml_rows = prof.json()["ml_scores"]
        q_ml = [r for r in ml_rows if r["period_type"] == "QUARTER"]
        assert q_ml
        latest_q = q_ml[-1]
        assert latest_q["risk_tier"] == "R5"
        assert latest_q["risk_market_percentile"] == pytest.approx(100.0)
        assert latest_q["market_base_rate"] == pytest.approx(0.025)
        assert latest_q["relative_risk_lift"] == pytest.approx(
            latest_q["calibrated_distress_prob"] / 0.025
        )

        # 2. /api/v1/screener returns triad fields and supports optional risk_tier filter
        scr_all = client.get("/api/v1/screener", params={"period_type": "QUARTER"})
        assert scr_all.status_code == 200
        items_all = scr_all.json()["items"]
        assert any(it["risk_tier"] == "R5" for it in items_all)
        assert any(it["risk_tier"] == "R1" for it in items_all)

        scr_r5 = client.get(
            "/api/v1/screener", params={"period_type": "QUARTER", "risk_tier": "R5"}
        )
        assert scr_r5.status_code == 200
        items_r5 = scr_r5.json()["items"]
        assert len(items_r5) >= 1
        assert all(it["risk_tier"] == "R5" for it in items_r5)

        # 3. /api/v2/market/changes exposes risk_tier_prev/curr, risk_market_percentile_curr, relative_risk_lift_curr
        chg = client.get("/api/v2/market/changes", params={"period_type": "QUARTER"})
        assert chg.status_code == 200
        chg_items = chg.json()["items"]
        hpg_chg = [it for it in chg_items if it["ticker"] == "HPG"]
        assert hpg_chg
        assert hpg_chg[0]["risk_tier_curr"] == "R5"
        assert hpg_chg[0]["risk_market_percentile_curr"] == pytest.approx(100.0)
        assert hpg_chg[0]["relative_risk_lift_curr"] is not None

    # 4. Backward compatibility: legacy snapshot parquet WITHOUT the 4 ML-07a columns still serves 200 OK
    legacy_root = tmp_path / "legacy_data"
    state = StateStore(legacy_root / "metadata" / "state.sqlite")
    state.initialize()
    state.start_run("run_legacy", "MANUAL", {"source": "test"}, None)
    publisher = SnapshotPublisher(
        legacy_root / "published" / "snapshots", state_store=state
    )
    snap_dir = publisher.publish(
        "snap_legacy_pre_ml07a", synthetic_tables, run_id="run_legacy"
    )
    state.finish_run("run_legacy", "SUCCEEDED", "snap_legacy_pre_ml07a")
    legacy_ml = synthetic_tables[ML_SCORES_TABLE].drop(
        [
            "risk_tier",
            "risk_market_percentile",
            "market_base_rate",
            "relative_risk_lift",
        ]
    )
    legacy_ml.write_parquet(snap_dir / ML_SCORES_TABLE)

    legacy_app = create_app(data_root=legacy_root)
    with TestClient(legacy_app) as legacy_client:
        r_prof = legacy_client.get("/api/v1/companies/HPG/profile")
        assert r_prof.status_code == 200
        assert r_prof.json()["ml_scores"][-1]["risk_tier"] is None
        r_scr = legacy_client.get("/api/v1/screener", params={"period_type": "QUARTER"})
        assert r_scr.status_code == 200
        assert all(it["risk_tier"] is None for it in r_scr.json()["items"])
        r_chg = legacy_client.get(
            "/api/v2/market/changes", params={"period_type": "QUARTER"}
        )
        assert r_chg.status_code == 200
        assert all(it["risk_tier_curr"] is None for it in r_chg.json()["items"])
