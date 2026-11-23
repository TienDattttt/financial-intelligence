"""Unit and integration tests for Layer 5 FastAPI V2 UI read-only extensions."""

from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from finintel.analytics.tables import build_analytics_tables
from finintel.pipeline.state_store import StateStore
from finintel.serving.api import create_app
from finintel.serving.dictionary import (
    OVERVIEW_CORE_RATIOS,
    RATIO_DICTIONARY_META,
    RED_FLAG_DICTIONARY,
    load_statement_metric_catalog,
)
from finintel.serving.queries import ALL_RATIOS, RATIO_LINEAGE_CATALOG
from finintel.storage.schemas import (
    BENCHMARKS_TABLE,
    FILINGS_MANIFEST_SCHEMA,
    FILINGS_MANIFEST_TABLE,
    ML_SCORES_SCHEMA,
    ML_SCORES_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)
from finintel.storage.snapshot_publisher import SnapshotPublisher


def _build_minimal_v2_tables() -> dict[str, pl.DataFrame]:
    issuers = pl.DataFrame(
        {
            "issuer_id": ["HPG", "FPT"],
            "ticker": ["HPG", "FPT"],
            "exchange": ["HOSE", "HOSE"],
            "company_name_vn": ["Tập đoàn Hòa Phát", "Công ty Cổ phần FPT"],
            "accounting_regime": ["CORPORATE", "CORPORATE"],
            "icb_code_level2": ["1700", "9500"],
            "icb_name_level2": ["Tài nguyên Cơ bản", "Công nghệ thông tin"],
            "icb_code_level3": ["1750", "9530"],
            "icb_name_level3": ["Kim loại", "Phần mềm"],
            "industry_snapshot_date": [date(2025, 1, 1), date(2025, 1, 1)],
            "delisted_date": [None, None],
        }
    )

    periods = [
        (1, date(2025, 1, 1), date(2025, 3, 31), date(2025, 4, 20), "VAS_TT200_2014"),
        (2, date(2025, 4, 1), date(2025, 6, 30), date(2025, 7, 20), "VAS_TT200_2014"),
        (3, date(2025, 7, 1), date(2025, 9, 30), date(2025, 10, 20), "VAS_TT200_2014"),
        (4, date(2025, 10, 1), date(2025, 12, 31), date(2026, 1, 25), "VAS_TT200_2014"),
        (1, date(2026, 1, 1), date(2026, 3, 31), date(2026, 4, 20), "VAS_TT99_2025"),
        (2, date(2026, 4, 1), date(2026, 6, 30), date(2026, 7, 20), "VAS_TT99_2025"),
    ]

    fact_rows: list[dict[str, object]] = []
    manifest_rows: list[dict[str, object]] = []
    for issuer, mult in (("HPG", 1.5), ("FPT", 1.0)):
        for fq, pstart, pend, pub, tax in periods:
            fy = pend.year
            fid_base = f"{issuer}_{fy}Q{fq}"
            revisions = [1, 2] if (issuer == "HPG" and fy == 2026 and fq == 2) else [1]
            for rev in revisions:
                fid = f"{fid_base}_r{rev}"
                manifest_rows.append(
                    {
                        "filing_id": fid,
                        "issuer_id": issuer,
                        "ticker_at_filing": issuer,
                        "source_provider": "TEST",
                        "connector_name": "fixture",
                        "statement_type": "INCOME_STATEMENT",
                        "statement_scope": "CONSOLIDATED",
                        "period_type": "QUARTER",
                        "period_end_date": pend,
                        "published_at": pub,
                        "published_at_basis": "EXACT_FILING",
                        "effective_available_at": pub,
                        "audit_status": "REVIEWED" if fq == 2 else "UNAUDITED",
                        "audit_opinion": "UNMODIFIED",
                        "revision_no": rev,
                        "revision_reason": "CORRECTION" if rev > 1 else "ORIGINAL",
                        "canonical_content_sha256": f"sha_{fid}",
                        "payload_file_sha256": f"raw_{fid}",
                    }
                )
                metrics_map: dict[str, tuple[str, float]] = {
                    "BS_TOTAL_ASSETS": ("BALANCE_SHEET", 120_000.0 * mult),
                    "BS_CURRENT_ASSETS": ("BALANCE_SHEET", 55_000.0 * mult),
                    "BS_CASH_AND_EQUIVALENTS": ("BALANCE_SHEET", 12_000.0 * mult),
                    "BS_SHORT_TERM_INVESTMENTS": ("BALANCE_SHEET", 4_000.0 * mult),
                    "BS_TRADE_RECEIVABLES_ST": ("BALANCE_SHEET", 15_000.0 * mult),
                    "BS_INVENTORIES": ("BALANCE_SHEET", 18_000.0 * mult),
                    "BS_CURRENT_LIABILITIES": ("BALANCE_SHEET", 38_000.0 * mult),
                    "BS_SHORT_TERM_BORROWINGS": ("BALANCE_SHEET", 16_000.0 * mult),
                    "BS_LONG_TERM_BORROWINGS": ("BALANCE_SHEET", 22_000.0 * mult),
                    "BS_TOTAL_LIABILITIES": ("BALANCE_SHEET", 68_000.0 * mult),
                    "BS_TOTAL_EQUITY": ("BALANCE_SHEET", 52_000.0 * mult),
                    "BS_TRADE_PAYABLES_ST": ("BALANCE_SHEET", 9_000.0 * mult),
                    "BS_ORDINARY_SHARES_CAPITAL": ("BALANCE_SHEET", 20_000.0 * mult),
                    "IS_NET_REVENUE": ("INCOME_STATEMENT", 32_000.0 * mult),
                    "IS_COST_OF_GOODS_SOLD": ("INCOME_STATEMENT", -26_000.0 * mult),
                    "IS_GROSS_PROFIT": ("INCOME_STATEMENT", 6_000.0 * mult),
                    "IS_SELLING_EXPENSES": ("INCOME_STATEMENT", -1_200.0 * mult),
                    "IS_ADMINISTRATIVE_EXPENSES": ("INCOME_STATEMENT", -1_100.0 * mult),
                    "IS_OPERATING_PROFIT": ("INCOME_STATEMENT", 3_700.0 * mult),
                    "IS_PROFIT_BEFORE_TAX": ("INCOME_STATEMENT", 3_100.0 * mult),
                    "IS_INTEREST_EXPENSE": ("INCOME_STATEMENT", -600.0 * mult),
                    "IS_CURRENT_INCOME_TAX": ("INCOME_STATEMENT", -500.0 * mult),
                    "IS_NET_INCOME": ("INCOME_STATEMENT", 2_600.0 * mult),
                    "IS_NET_INCOME_PARENT": ("INCOME_STATEMENT", 2_500.0 * mult),
                    "CF_OPERATING_CASH_FLOW": ("CASH_FLOW", 3_200.0 * mult),
                    "CF_DEPRECIATION_AMORTIZATION": ("CASH_FLOW", 1_100.0 * mult),
                    "CF_CAPITAL_EXPENDITURE": ("CASH_FLOW", -1_500.0 * mult),
                }
                for mid, (stype, val) in metrics_map.items():
                    fact_rows.append(
                        {
                            "issuer_id": issuer,
                            "ticker": issuer,
                            "ticker_at_filing": issuer,
                            "accounting_regime": "CORPORATE",
                            "statement_scope": "CONSOLIDATED",
                            "statement_type": stype,
                            "period_type": "QUARTER",
                            "fiscal_year": fy,
                            "fiscal_quarter": fq,
                            "fiscal_year_start_date": date(fy, 1, 1),
                            "period_start_date": pstart,
                            "period_end_date": pend,
                            "metric_id": mid,
                            "value": float(val),
                            "ttm_value": float(val * 4.0)
                            if stype != "BALANCE_SHEET"
                            else float(val),
                            "unit": "VND",
                            "value_type": "STOCK"
                            if stype == "BALANCE_SHEET"
                            else "FLOW",
                            "sign_convention": "SIGNED",
                            "comparable_across_taxonomy": True,
                            "taxonomy_version": tax,
                            "derivation_method": "REPORTED_DIRECTLY",
                            "filing_id": fid,
                            "revision_no": rev,
                            "revision_reason": "CORRECTION" if rev > 1 else "ORIGINAL",
                            "audit_status": "REVIEWED" if fq == 2 else "UNAUDITED",
                            "audit_opinion": "UNMODIFIED",
                            "source_provider": "TEST",
                            "published_at": pub,
                            "published_at_basis": "EXACT_FILING",
                            "effective_available_at": pub,
                            "is_filed_late": False,
                            "source_filing_ids": [fid],
                        }
                    )

    facts_df = pl.DataFrame(fact_rows, infer_schema_length=None)
    analytics = build_analytics_tables(facts_df, issuers, as_of=date(2026, 7, 25))
    ratios_df = analytics[RATIOS_TABLE]

    ml_rows: list[dict[str, object]] = []
    for rec in ratios_df.to_dicts():
        issuer = str(rec["issuer_id"])
        pend = rec["period_end_date"]
        is_latest = pend == date(2026, 6, 30)
        zone = "GREY_ZONE" if (issuer == "HPG" and is_latest) else "SAFE_ZONE"
        prob = 0.032 if (issuer == "HPG" and is_latest) else 0.015
        ml_rows.append(
            {
                "issuer_id": issuer,
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": pend,
                "filing_id": str(rec["filing_id"]),
                "model_role": "PRIMARY_CORPORATE",
                "altman_z_double_prime": 2.2 if zone == "GREY_ZONE" else 3.8,
                "altman_em_score": 5.1,
                "altman_zone": zone,
                "piotroski_f_score": 6 if issuer == "HPG" else 8,
                "piotroski_valid_components_count": 9,
                "beneish_m8_score": -1.65 if issuer == "HPG" else -2.55,
                "beneish_flag": (
                    "HIGH_MANIPULATION_RISK_SCREEN" if issuer == "HPG" else "VALID"
                ),
                "pca_pc1": 0.1,
                "pca_pc2": -0.2,
                "pca_pc3": 0.05,
                "cluster_id": 1,
                "cluster_archetype_name": "Sản xuất thâm dụng vốn",
                "anomaly_score_raw": 0.2,
                "anomaly_percentile_rank": 55.0,
                "statistical_outlier_flag": False,
                "anomaly_flag": "NORMAL",
                "distress_risk_score": -2.1,
                "calibrated_distress_prob": prob,
                "model_version": "v2.test",
                "shap_top_drivers_json": '{"base_value":-2.5,"prediction_score":-2.1,"other_features_delta":0.1,"top_risk_increasing":[{"feature":"debt_to_equity","display_name_vn":"Nợ vay / VCSH","value":0.73,"peer_median":0.45,"shap_delta":0.4}],"top_risk_decreasing":[{"feature":"roe_total","display_name_vn":"ROE Tổng","value":0.18,"peer_median":0.12,"shap_delta":-0.1}]}',
            }
        )
    ml_df = pl.DataFrame(ml_rows, schema=ML_SCORES_SCHEMA)
    manifest_df = pl.DataFrame(manifest_rows, schema=FILINGS_MANIFEST_SCHEMA)

    return {
        "issuers.parquet": issuers,
        "financial_facts.parquet": facts_df,
        RATIOS_TABLE: ratios_df,
        BENCHMARKS_TABLE: analytics[BENCHMARKS_TABLE],
        RED_FLAGS_TABLE: analytics[RED_FLAGS_TABLE],
        ML_SCORES_TABLE: ml_df,
        FILINGS_MANIFEST_TABLE: manifest_df,
    }


@pytest.fixture()
def v2_published_root(tmp_path: Path) -> tuple[Path, str]:
    data_root = tmp_path / "data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    run_id = "run_v2_ext_001"
    snapshot_id = "snap_v2_ext_001"
    state.start_run(run_id, "MANUAL", {"source": "test"}, None)
    publisher = SnapshotPublisher(
        data_root / "published" / "snapshots", state_store=state
    )
    publisher.publish(snapshot_id, _build_minimal_v2_tables(), run_id=run_id)
    state.finish_run(run_id, "SUCCEEDED", snapshot_id)
    return data_root, snapshot_id


def test_dictionary_and_yaml_catalog_completeness() -> None:
    assert set(RATIO_DICTIONARY_META.keys()) == set(ALL_RATIOS)
    assert set(RATIO_DICTIONARY_META.keys()) == set(RATIO_LINEAGE_CATALOG.keys())
    assert len(OVERVIEW_CORE_RATIOS) == 12
    assert len(RED_FLAG_DICTIONARY) == 9
    assert [rf.flag_code for rf in RED_FLAG_DICTIONARY] == [
        f"RF_0{i}_" + suffix
        for i, suffix in (
            (1, "ACCRUAL_DIVERGENCE"),
            (2, "RECEIVABLES_SURGE"),
            (3, "OTHER_RECEIVABLES_ANOMALY"),
            (4, "INVENTORY_BLOAT"),
            (5, "DEBT_SERVICE_DISTRESS"),
            (6, "NEGATIVE_EQUITY"),
            (7, "AUDIT_OR_FILING_WARNING"),
            (8, "NON_CORE_EARNINGS_DEPENDENCE"),
            (9, "FROZEN_CIP_CAPITALIZATION"),
        )
    ]

    catalog = load_statement_metric_catalog()
    assert len(catalog) > 50
    assert catalog["BS_TOTAL_ASSETS"].is_total is True
    assert catalog["BS_TOTAL_ASSETS"].tt99_code == "280"
    assert catalog["BS_TOTAL_ASSETS"].tt200_code == "270"
    assert catalog["IS_NET_REVENUE"].statement_type == "INCOME_STATEMENT"


def test_v2_routes_are_sync_and_v1_route_count_preserved(
    v2_published_root: tuple[Path, str],
) -> None:
    data_root, _ = v2_published_root
    app = create_app(data_root=data_root)
    v1_routes = [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/api/v1/")
    ]
    v2_routes = [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/api/v2/")
    ]
    assert len(v1_routes) == 11
    assert len(v2_routes) == 9
    for route in (*v1_routes, *v2_routes):
        assert not inspect.iscoroutinefunction(route.endpoint)
    openapi_paths = app.openapi()["paths"]
    assert "/api/v2/documents/{ticker}/list" in openapi_paths
    assert "/api/v2/documents/{ticker}/investigate" in openapi_paths


def test_all_7_v2_routes_etag_304_and_screener_extensions(
    v2_published_root: tuple[Path, str],
) -> None:
    data_root, snapshot_id = v2_published_root
    app = create_app(data_root=data_root)
    with TestClient(app) as client:
        r_periods = client.get("/api/v2/meta/periods")
        assert r_periods.status_code == 200
        p_json = r_periods.json()
        assert p_json["snapshot_id"] == snapshot_id
        assert "2026-06-30" in p_json["available_periods"]["CONSOLIDATED"]["QUARTER"]
        assert len(p_json["sectors_level2"]) == 2

        r_dict = client.get("/api/v2/meta/dictionary")
        assert r_dict.status_code == 200
        d_json = r_dict.json()
        assert len(d_json["ratios"]) == 38
        assert len(d_json["red_flags"]) == 9
        dte = next(r for r in d_json["ratios"] if r["ratio_name"] == "debt_to_equity")
        assert dte["direction"] == "LOWER_IS_BETTER"

        r_bsum = client.get("/api/v2/companies/HPG/benchmarks/summary")
        assert r_bsum.status_code == 200
        b_json = r_bsum.json()
        assert len(b_json["items"]) == 12
        assert b_json["items"][0]["sparkline"]

        r_stmt = client.get(
            "/api/v2/companies/HPG/statements",
            params={"statement_type": "INCOME_STATEMENT", "periods_limit": 6},
        )
        assert r_stmt.status_code == 200
        s_json = r_stmt.json()
        assert len(s_json["periods"]) == 6
        assert s_json["periods"][0]["taxonomy_version"] == "VAS_TT99_2025"
        assert any(
            r["metric_id"] == "IS_NET_REVENUE" and r["is_total"] for r in s_json["rows"]
        )

        r_fil = client.get("/api/v2/companies/HPG/filings")
        assert r_fil.status_code == 200
        f_json = r_fil.json()
        assert any(item["has_restatement"] is True for item in f_json["items"])

        r_sec = client.get("/api/v2/sectors/risk-summary")
        assert r_sec.status_code == 200
        sec_json = r_sec.json()
        assert sec_json["total_issuers"] == 2
        assert sec_json["grey_zone_count"] == 1
        assert len(sec_json["top_deteriorating"]) >= 1
        assert sec_json["top_deteriorating"][0]["ticker"] == "HPG"

        r_chg = client.get("/api/v2/market/changes", params={"tickers": "HPG,FPT"})
        assert r_chg.status_code == 200
        chg_json = r_chg.json()
        assert {item["ticker"] for item in chg_json["items"]} == {"HPG", "FPT"}

        # Screener extensions (backward compatible)
        r_scr = client.get(
            "/api/v1/screener",
            params={
                "q": "hòa phát",
                "min_distress_prob": 0.02,
                "sort_by": "ticker",
                "sort_dir": "asc",
            },
        )
        assert r_scr.status_code == 200
        assert [i["ticker"] for i in r_scr.json()["items"]] == ["HPG"]

        # ETag 304 checks across all v2 GET routes
        etag = r_periods.headers["ETag"]
        for url in (
            "/api/v2/meta/periods",
            "/api/v2/meta/dictionary",
            "/api/v2/companies/HPG/benchmarks/summary",
            "/api/v2/companies/HPG/statements",
            "/api/v2/companies/HPG/filings",
            "/api/v2/sectors/risk-summary",
            "/api/v2/market/changes",
        ):
            r_304 = client.get(url, headers={"If-None-Match": etag})
            assert r_304.status_code == 304

        # Error handling 404 / 422
        assert client.get("/api/v2/companies/UNKNOWN/statements").status_code == 404
        assert (
            client.get(
                "/api/v2/companies/HPG/statements", params={"statement_type": "BAD"}
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/api/v2/market/changes", params={"tickers": "BAD$!"}
            ).status_code
            == 422
        )


def test_v2_routes_fallback_without_manifest_or_ml_tables(tmp_path: Path) -> None:
    data_root = tmp_path / "data_fallback"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    run_id = "run_v2_fb_001"
    snapshot_id = "snap_v2_fb_001"
    state.start_run(run_id, "MANUAL", {"source": "test"}, None)
    raw = _build_minimal_v2_tables()
    reduced = {
        k: v
        for k, v in raw.items()
        if k not in (FILINGS_MANIFEST_TABLE, ML_SCORES_TABLE)
    }
    publisher = SnapshotPublisher(
        data_root / "published" / "snapshots", state_store=state
    )
    publisher.publish(snapshot_id, reduced, run_id=run_id)
    state.finish_run(run_id, "SUCCEEDED", snapshot_id)

    app = create_app(data_root=data_root)
    with TestClient(app) as client:
        r_fil = client.get("/api/v2/companies/HPG/filings")
        assert r_fil.status_code == 200
        assert len(r_fil.json()["items"]) > 0
        assert any(i["has_restatement"] is True for i in r_fil.json()["items"])

        r_sec = client.get("/api/v2/sectors/risk-summary")
        assert r_sec.status_code == 200
        assert r_sec.json()["total_issuers"] == 2
