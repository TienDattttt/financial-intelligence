"""Unit tests covering Phase 2 Stream A audit remediation deliverables (D1-D4, D6-D8, D10-D11, F7, L9)."""

from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from fastapi.testclient import TestClient

from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.ratios import RatioStatusFlag, calculate_ratios
from finintel.analytics.stress_test import StressScenario, simulate_financial_stress
from finintel.analytics.tables import build_analytics_tables
from finintel.pipeline.state_store import StateStore
from finintel.serving.api import create_app
from finintel.serving.freshness import format_composite_etag
from finintel.serving.queries import EXCHANGES, Exchange
from finintel.serving.queries._guards import (
    normalize_company_display_name,
    normalize_company_display_name_expr,
)
from finintel.storage.schemas import (
    BENCHMARKS_TABLE,
    FILINGS_MANIFEST_TABLE,
    ML_SCORES_SCHEMA,
    ML_SCORES_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)
from finintel.storage.snapshot_publisher import SnapshotPublisher
from tests.unit.test_layer4_analytics import facts as make_l4_facts
from tests.unit.test_layer4_analytics import issuer as make_l4_issuer
from tests.unit.test_layer5_fastapi_serving import _build_synthetic_v2_tables


def _publish_snapshot(
    tmp_path: Path, tables: dict[str, pl.DataFrame], snap_id: str = "snap_p2_001"
) -> tuple[Path, str]:
    data_root = tmp_path / "data_root"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    run_id = f"run_{snap_id}"
    state.start_run(run_id, "MANUAL", {"source": "test"}, None)
    publisher = SnapshotPublisher(
        data_root / "published" / "snapshots", state_store=state
    )
    publisher.publish(snap_id, tables, run_id=run_id)
    state.finish_run(run_id, "SUCCEEDED", snap_id)
    return data_root, snap_id


def test_d1_health_rag_available_and_etag_rotation(tmp_path: Path) -> None:
    tables = _build_synthetic_v2_tables()
    data_root, snap_id = _publish_snapshot(tmp_path, tables, "snap_d1_001")
    base_etag = format_composite_etag(snap_id, data_root)

    app_no_rag = create_app(data_root=data_root, valkey_url="")
    with TestClient(app_no_rag) as client:
        r_off = client.get("/api/v1/health")
        assert r_off.status_code == 200
        assert r_off.json()["rag_available"] is False
        assert r_off.headers["ETag"] == base_etag

    app_with_rag = create_app(
        data_root=data_root,
        valkey_url="",
        rag_store=object(),
        rag_investigator=object(),
    )
    with TestClient(app_with_rag) as client:
        r_on = client.get("/api/v1/health")
        assert r_on.status_code == 200
        assert r_on.json()["rag_available"] is True
        rag_etag = r_on.headers["ETag"]
        assert rag_etag != base_etag
        assert rag_etag.endswith(':rag1"')

        # Stale ETag from rag_available=False must NOT return 304 when True
        r_stale = client.get("/api/v1/health", headers={"If-None-Match": base_etag})
        assert r_stale.status_code == 200

        # Current :rag1 ETag returns 304
        r_304 = client.get("/api/v1/health", headers={"If-None-Match": rag_etag})
        assert r_304.status_code == 304


def test_f7_l9_screener_exchange_risk_tiers_and_new_sorts(tmp_path: Path) -> None:
    assert EXCHANGES == ("HOSE", "HNX", "UPCOM")
    ex_val: Exchange = "HNX"
    assert ex_val == "HNX"

    tables = _build_synthetic_v2_tables()
    tables["issuers.parquet"] = tables["issuers.parquet"].with_columns(
        pl.when(pl.col("ticker") == "CMG")
        .then(pl.lit("HNX"))
        .when(pl.col("ticker") == "HPG")
        .then(pl.lit("UPCOM"))
        .otherwise(pl.col("exchange"))
        .alias("exchange")
    )
    data_root, _ = _publish_snapshot(tmp_path, tables, "snap_f7_001")
    app = create_app(data_root=data_root, valkey_url="")

    with TestClient(app) as client:
        r_hnx = client.get("/api/v1/screener", params={"exchange": "HNX"})
        assert r_hnx.status_code == 200
        assert [i["ticker"] for i in r_hnx.json()["items"]] == ["CMG"]

        r_tiers = client.get("/api/v1/screener", params={"risk_tiers": "R5, R3"})
        assert r_tiers.status_code == 200
        assert {i["ticker"] for i in r_tiers.json()["items"]} == {"CMG", "HPG"}

        # Backward compatible single risk_tier + invalid risk_tiers -> 422
        r_single = client.get("/api/v1/screener", params={"risk_tier": "R1"})
        assert r_single.status_code == 200
        assert [i["ticker"] for i in r_single.json()["items"]] == ["FPT"]
        assert (
            client.get("/api/v1/screener", params={"risk_tiers": "R9"}).status_code
            == 422
        )

        # L9: sort by ebit_margin and interest_coverage on both JSON and CSV export
        for sort_col in ("ebit_margin", "interest_coverage"):
            r_sort = client.get(
                "/api/v1/screener",
                params={"sort_by": sort_col, "sort_dir": "desc"},
            )
            assert r_sort.status_code == 200
            vals = [
                i[sort_col] for i in r_sort.json()["items"] if i[sort_col] is not None
            ]
            assert vals == sorted(vals, reverse=True)
            api_tickers = [i["ticker"] for i in r_sort.json()["items"]]

            r_csv = client.get(
                "/api/v1/screener/export",
                params={"sort_by": sort_col, "sort_dir": "desc", "exchange": "HOSE"},
            )
            assert r_csv.status_code == 200
            rows = list(csv.DictReader(io.StringIO(r_csv.content.decode("utf-8-sig"))))
            assert [r["ticker"] for r in rows] == ["FPT"]
            assert len(api_tickers) == 3


def test_d2_benchmarks_summary_empty_separate_returns_200(tmp_path: Path) -> None:
    tables = _build_synthetic_v2_tables()
    data_root, _ = _publish_snapshot(tmp_path, tables, "snap_d2_001")
    app = create_app(data_root=data_root, valkey_url="")

    with TestClient(app) as client:
        r_sep = client.get(
            "/api/v2/companies/FPT/benchmarks/summary",
            params={"statement_scope": "SEPARATE"},
        )
        assert r_sep.status_code == 200
        body = r_sep.json()
        assert body["ticker"] == "FPT"
        assert body["items"] == []
        assert body["period_end_date"] is None

        # Unknown ticker still returns 404
        r_404 = client.get(
            "/api/v2/companies/NOPE99/benchmarks/summary",
            params={"statement_scope": "SEPARATE"},
        )
        assert r_404.status_code == 404


def test_d3_annual_broad_coverage_filtering(tmp_path: Path) -> None:
    tables = _build_synthetic_v2_tables()
    base_issuers = tables["issuers.parquet"]
    base_ratios = tables[RATIOS_TABLE]
    base_ml = tables[ML_SCORES_TABLE]
    base_rf = tables[RED_FLAGS_TABLE]

    fpt_issuer = base_issuers.filter(pl.col("ticker") == "FPT")
    fpt_annual_2025 = base_ratios.filter(
        (pl.col("issuer_id") == "FPT")
        & (pl.col("period_type") == "ANNUAL")
        & (pl.col("period_end_date") == date(2025, 12, 31))
    )
    fpt_annual_2024 = base_ratios.filter(
        (pl.col("issuer_id") == "FPT")
        & (pl.col("period_type") == "ANNUAL")
        & (pl.col("period_end_date") == date(2024, 12, 31))
    )

    extra_issuers: list[pl.DataFrame] = []
    extra_ratios: list[pl.DataFrame] = []
    for idx in range(24):
        iid = f"C{idx:02d}"
        extra_issuers.append(
            fpt_issuer.with_columns(
                pl.lit(iid).alias("issuer_id"), pl.lit(iid).alias("ticker")
            )
        )
        extra_ratios.append(
            fpt_annual_2025.with_columns(
                pl.lit(iid).alias("issuer_id"), pl.lit(iid).alias("ticker")
            )
        )
        extra_ratios.append(
            fpt_annual_2024.with_columns(
                pl.lit(iid).alias("issuer_id"), pl.lit(iid).alias("ticker")
            )
        )
        if idx < 2:
            # Off-calendar 2-issuer slice in 2026-06-30 must be filtered out
            extra_ratios.append(
                fpt_annual_2025.with_columns(
                    pl.lit(iid).alias("issuer_id"),
                    pl.lit(iid).alias("ticker"),
                    pl.lit(date(2026, 6, 30)).alias("period_end_date"),
                    pl.lit(2026).cast(pl.Int32).alias("fiscal_year"),
                )
            )

    tables["issuers.parquet"] = pl.concat([base_issuers, *extra_issuers])
    tables[RATIOS_TABLE] = pl.concat([base_ratios, *extra_ratios])
    tables[ML_SCORES_TABLE] = base_ml
    tables[RED_FLAGS_TABLE] = base_rf
    data_root, _ = _publish_snapshot(tmp_path, tables, "snap_d3_001")
    app = create_app(data_root=data_root, valkey_url="")

    with TestClient(app) as client:
        r_meta = client.get("/api/v2/meta/periods")
        assert r_meta.status_code == 200
        ann_periods = r_meta.json()["available_periods"]["CONSOLIDATED"]["ANNUAL"]
        assert ann_periods == ["2025-12-31", "2024-12-31"]

        r_sec = client.get(
            "/api/v2/sectors/risk-summary", params={"period_type": "ANNUAL"}
        )
        assert r_sec.status_code == 200
        assert r_sec.json()["period_end_date"] == "2025-12-31"

        r_scr = client.get("/api/v1/screener", params={"period_type": "ANNUAL"})
        assert r_scr.status_code == 200
        assert r_scr.json()["period_end_date"] == "2025-12-31"

        r_st = client.post(
            "/api/v1/stress-test",
            json={"scenario_name": "Annual Check", "period_type": "ANNUAL"},
        )
        assert r_st.status_code == 200
        assert r_st.json()["period_end_date"] == "2025-12-31"


def test_d3_market_changes_annual_broad_coverage_and_yoy_span(tmp_path: Path) -> None:
    tables = _build_synthetic_v2_tables()
    base_issuers = tables["issuers.parquet"]
    base_ratios = tables[RATIOS_TABLE]
    base_ml = tables[ML_SCORES_TABLE]
    base_rf = tables[RED_FLAGS_TABLE]

    fpt_issuer = base_issuers.filter(pl.col("ticker") == "FPT")
    fpt_annual_2025 = base_ratios.filter(
        (pl.col("issuer_id") == "FPT")
        & (pl.col("period_type") == "ANNUAL")
        & (pl.col("period_end_date") == date(2025, 12, 31))
    )
    fpt_annual_2024 = base_ratios.filter(
        (pl.col("issuer_id") == "FPT")
        & (pl.col("period_type") == "ANNUAL")
        & (pl.col("period_end_date") == date(2024, 12, 31))
    )
    fpt_ml_2025 = base_ml.filter(
        (pl.col("issuer_id") == "FPT")
        & (pl.col("period_type") == "ANNUAL")
        & (pl.col("period_end_date") == date(2025, 12, 31))
    )

    extra_issuers: list[pl.DataFrame] = []
    extra_ratios: list[pl.DataFrame] = []
    extra_ml: list[pl.DataFrame] = []
    # 22 broad-coverage calendar issuers on 2025-12-31, 2024-12-31, AND 2025-09-30
    # (putting >=20 issuers on 2025-09-30 verifies the >=330d YoY span check independently)
    for idx in range(22):
        iid = f"M{idx:02d}"
        extra_issuers.append(
            fpt_issuer.with_columns(
                pl.lit(iid).alias("issuer_id"), pl.lit(iid).alias("ticker")
            )
        )
        for yr_df in (fpt_annual_2025, fpt_annual_2024):
            extra_ratios.append(
                yr_df.with_columns(
                    pl.lit(iid).alias("issuer_id"), pl.lit(iid).alias("ticker")
                )
            )
        extra_ratios.append(
            fpt_annual_2025.with_columns(
                pl.lit(iid).alias("issuer_id"),
                pl.lit(iid).alias("ticker"),
                pl.lit(date(2025, 9, 30)).alias("period_end_date"),
            )
        )

    # Sparse off-calendar issuers (2026-06-30 / 2026-03-31) + HID fiscal year-end switch
    for off_id, d_curr, d_prev in (
        ("OFF1", date(2026, 6, 30), date(2025, 6, 30)),
        ("OFF2", date(2026, 3, 31), date(2025, 3, 31)),
    ):
        extra_issuers.append(
            fpt_issuer.with_columns(
                pl.lit(off_id).alias("issuer_id"), pl.lit(off_id).alias("ticker")
            )
        )
        extra_ratios.append(
            fpt_annual_2025.with_columns(
                pl.lit(off_id).alias("issuer_id"),
                pl.lit(off_id).alias("ticker"),
                pl.lit(d_curr).alias("period_end_date"),
                pl.lit(2026).cast(pl.Int32).alias("fiscal_year"),
            )
        )
        extra_ratios.append(
            fpt_annual_2025.with_columns(
                pl.lit(off_id).alias("issuer_id"),
                pl.lit(off_id).alias("ticker"),
                pl.lit(d_prev).alias("period_end_date"),
                pl.lit(2025).cast(pl.Int32).alias("fiscal_year"),
            )
        )

    # HID: switched from 2025-09-30 (92d < 330d) to 2025-12-31 -> prev must be None
    # HID2: has 2025-12-31, 2025-09-30, and 2024-12-31 -> skips 2025-09-30, pairs 2024-12-31
    for hid_id, dates_for_hid in (
        ("HID", (date(2025, 12, 31), date(2025, 9, 30))),
        ("HID2", (date(2025, 12, 31), date(2025, 9, 30), date(2024, 12, 31))),
    ):
        extra_issuers.append(
            fpt_issuer.with_columns(
                pl.lit(hid_id).alias("issuer_id"), pl.lit(hid_id).alias("ticker")
            )
        )
        for hd in dates_for_hid:
            extra_ratios.append(
                fpt_annual_2025.with_columns(
                    pl.lit(hid_id).alias("issuer_id"),
                    pl.lit(hid_id).alias("ticker"),
                    pl.lit(hd).alias("period_end_date"),
                )
            )
        extra_ml.append(fpt_ml_2025.with_columns(pl.lit(hid_id).alias("issuer_id")))

    tables["issuers.parquet"] = pl.concat([base_issuers, *extra_issuers])
    tables[RATIOS_TABLE] = pl.concat([base_ratios, *extra_ratios])
    tables[ML_SCORES_TABLE] = pl.concat([base_ml, *extra_ml])
    tables[RED_FLAGS_TABLE] = base_rf
    data_root, _ = _publish_snapshot(tmp_path, tables, "snap_d3_changes_001")
    app = create_app(data_root=data_root, valkey_url="")

    with TestClient(app) as client:
        # Sparse off-calendar tickers must not leak on ANNUAL market/changes
        r_off = client.get(
            "/api/v2/market/changes",
            params={"period_type": "ANNUAL", "tickers": "OFF1,OFF2"},
        )
        assert r_off.status_code == 200
        assert r_off.json()["items"] == []

        # HID 2025-12-31 must NOT pair with 2025-09-30 (< 330 days);
        # HID2 must skip 2025-09-30 and pair 2025-12-31 with 2024-12-31
        r_hid = client.get(
            "/api/v2/market/changes",
            params={"period_type": "ANNUAL", "tickers": "HID,HID2"},
        )
        assert r_hid.status_code == 200
        hid_by_t = {i["ticker"]: i for i in r_hid.json()["items"]}
        assert set(hid_by_t) == {"HID", "HID2"}
        assert hid_by_t["HID"]["curr_period_end_date"] == "2025-12-31"
        assert hid_by_t["HID"]["prev_period_end_date"] is None
        assert hid_by_t["HID2"]["curr_period_end_date"] == "2025-12-31"
        assert hid_by_t["HID2"]["prev_period_end_date"] == "2024-12-31"

        # Market-wide ANNUAL changes exclude 2026-06-30/03-31 and pair M00/FPT YoY
        r_mkt = client.get(
            "/api/v2/market/changes",
            params={"period_type": "ANNUAL", "limit": 50},
        )
        assert r_mkt.status_code == 200
        mkt_items = r_mkt.json()["items"]
        assert len(mkt_items) > 0
        for row in mkt_items:
            assert row["curr_period_end_date"] == "2025-12-31"
            assert row["prev_period_end_date"] in ("2024-12-31", None)
        fpt_row = next(i for i in mkt_items if i["ticker"] == "FPT")
        assert fpt_row["prev_period_end_date"] == "2024-12-31"


def test_d4_piotroski_missing_criteria_and_na_reasons(tmp_path: Path) -> None:
    # Build 4 issuers covering all 4 piotroski_na_reason classes + 1 valid issuer
    vnd = 1_000_000.0
    issuers = pl.DataFrame(
        {
            "issuer_id": ["DILUT", "NOPRIOR", "ZDENOM", "TAXGUARD", "VALID9"],
            "ticker": ["DILUT", "NOPRIOR", "ZDENOM", "TAXGUARD", "VALID9"],
            "exchange": ["HOSE"] * 5,
            "company_name_vn": ["Dilut", "NoPrior", "ZeroDenom", "TaxGuard", "Valid9"],
            "accounting_regime": ["CORPORATE"] * 5,
            "icb_code_level2": ["9500"] * 5,
            "icb_name_level2": ["Tech"] * 5,
            "icb_code_level3": ["9530"] * 5,
            "icb_name_level3": ["Soft"] * 5,
            "industry_snapshot_date": [date(2024, 1, 1)] * 5,
            "delisted_date": [None] * 5,
        }
    )

    def _yr_metrics(
        year: int,
        *,
        rev: float = 20_000.0 * vnd,
        shares: float = 10_000.0 * vnd,
        include_noncash: bool = True,
    ) -> dict[str, float | None]:
        d: dict[str, float | None] = {
            "BS_TOTAL_ASSETS": 50_000.0 * vnd,
            "BS_CURRENT_ASSETS": 25_000.0 * vnd,
            "BS_CURRENT_LIABILITIES": 12_000.0 * vnd,
            "BS_TOTAL_LIABILITIES": 20_000.0 * vnd,
            "BS_TOTAL_EQUITY": 30_000.0 * vnd,
            "BS_LONG_TERM_BORROWINGS": 5_000.0 * vnd,
            "BS_ORDINARY_SHARES_CAPITAL": shares,
            "IS_NET_REVENUE": rev,
            "IS_GROSS_PROFIT": 6_000.0 * vnd if rev > 0 else 0.0,
            "IS_PROFIT_BEFORE_TAX": 2_500.0 * vnd,
            "IS_INTEREST_EXPENSE": -200.0 * vnd,
            "IS_NET_INCOME": 2_000.0 * vnd,
            "CF_OPERATING_CASH_FLOW": 2_500.0 * vnd,
            "CF_EQUITY_ISSUANCE_PROCEEDS": 0.0,
        }
        if include_noncash:
            d["IS_DILUTIVE_NONCASH_EQUITY_ISSUANCE"] = 0.0
        return d

    f_dilut = make_l4_facts(
        {
            (2023, None): _yr_metrics(2023, shares=10_000 * vnd, include_noncash=False),
            (2024, None): _yr_metrics(2024, shares=10_000 * vnd, include_noncash=False),
            (2025, None): _yr_metrics(2025, shares=12_000 * vnd, include_noncash=False),
        },
        issuer_id="DILUT",
    )
    f_noprior = make_l4_facts(
        {(2025, None): _yr_metrics(2025)},
        issuer_id="NOPRIOR",
    )
    f_zdenom = make_l4_facts(
        {
            (2023, None): _yr_metrics(2023),
            (2024, None): _yr_metrics(2024),
            (2025, None): _yr_metrics(2025, rev=0.0),
        },
        issuer_id="ZDENOM",
    )
    f_taxguard = make_l4_facts(
        {
            (2023, None): _yr_metrics(2023),
            (2024, None): _yr_metrics(2024),
            (2025, None): _yr_metrics(2025),
        },
        issuer_id="TAXGUARD",
    ).with_columns(
        pl.when(pl.col("fiscal_year") == 2025)
        .then(pl.lit("VAS_TT99_2025"))
        .otherwise(pl.lit("VAS_TT200_2014"))
        .alias("taxonomy_version"),
        pl.lit(False).alias("comparable_across_taxonomy"),
    )
    f_valid = make_l4_facts(
        {
            (2023, None): _yr_metrics(2023),
            (2024, None): _yr_metrics(2024),
            (2025, None): _yr_metrics(2025),
        },
        issuer_id="VALID9",
    )

    all_facts = pl.concat(
        [f_dilut, f_noprior, f_zdenom, f_taxguard, f_valid]
    ).with_columns(pl.col("value").alias("ttm_value"))
    analytics = build_analytics_tables(all_facts, issuers, as_of=date(2026, 6, 1))
    ratios_df = analytics[RATIOS_TABLE]

    ml_rows: list[dict[str, Any]] = []
    for rec in ratios_df.filter(pl.col("fiscal_year") == 2025).to_dicts():
        iid = str(rec["issuer_id"])
        is_valid = iid == "VALID9"
        ml_rows.append(
            {
                "issuer_id": iid,
                "statement_scope": "CONSOLIDATED",
                "period_type": "ANNUAL",
                "period_end_date": rec["period_end_date"],
                "filing_id": str(rec["filing_id"]),
                "model_role": "SCREENING_INDICATOR",
                "altman_z_double_prime": 3.0,
                "altman_em_score": 6.25,
                "altman_zone": "SAFE_ZONE",
                "piotroski_f_score": 7 if is_valid else None,
                "piotroski_valid_components_count": 9 if is_valid else 8,
                "beneish_m8_score": None,
                "beneish_flag": None,
                "pca_pc1": None,
                "pca_pc2": None,
                "pca_pc3": None,
                "cluster_id": None,
                "cluster_archetype_name": None,
                "anomaly_score_raw": None,
                "anomaly_percentile_rank": None,
                "statistical_outlier_flag": None,
                "anomaly_flag": None,
                "distress_risk_score": None,
                "calibrated_distress_prob": None,
                "risk_tier": None,
                "risk_market_percentile": None,
                "market_base_rate": None,
                "relative_risk_lift": None,
                "model_version": None,
                "shap_top_drivers_json": None,
            }
        )
    ml_df = pl.DataFrame(ml_rows, schema=ML_SCORES_SCHEMA)
    tables = {
        "issuers.parquet": issuers,
        "financial_facts.parquet": all_facts,
        "latest_facts.parquet": all_facts,
        RATIOS_TABLE: ratios_df,
        BENCHMARKS_TABLE: analytics[BENCHMARKS_TABLE],
        RED_FLAGS_TABLE: analytics[RED_FLAGS_TABLE],
        ML_SCORES_TABLE: ml_df,
    }
    data_root, _ = _publish_snapshot(tmp_path, tables, "snap_d4_001")
    app = create_app(data_root=data_root, valkey_url="")

    with TestClient(app) as client:
        r_dilut = client.get(
            "/api/v1/companies/DILUT/profile?period_view=Annual"
        ).json()["ml_scores"][-1]
        assert r_dilut["piotroski_missing_criteria"] == ["F7_NO_DILUTION"]
        assert r_dilut["piotroski_na_reason"] == "MISSING_DILUTION_DATA"

        r_noprior = client.get(
            "/api/v1/companies/NOPRIOR/profile?period_view=Annual"
        ).json()["ml_scores"][-1]
        assert r_noprior["piotroski_na_reason"] == "MISSING_PRIOR_YEAR_BASE"
        assert "F3_DELTA_ROA" in r_noprior["piotroski_missing_criteria"]

        r_zdenom = client.get(
            "/api/v1/companies/ZDENOM/profile?period_view=Annual"
        ).json()["ml_scores"][-1]
        assert "F8_DELTA_MARGIN" in r_zdenom["piotroski_missing_criteria"]
        assert r_zdenom["piotroski_na_reason"] == "INSUFFICIENT_COMPONENTS"

        r_tax = client.get(
            "/api/v1/companies/TAXGUARD/profile?period_view=Annual"
        ).json()["ml_scores"][-1]
        assert r_tax["piotroski_na_reason"] == "TT200_TT99_TAXONOMY_GUARD"
        assert len(r_tax["piotroski_missing_criteria"]) > 0

        r_val = client.get(
            "/api/v1/companies/VALID9/profile?period_view=Annual"
        ).json()["ml_scores"][-1]
        assert r_val["piotroski_missing_criteria"] == []
        assert r_val["piotroski_na_reason"] is None


def test_d6_filings_taxonomy_version_and_reviewed_opinion(tmp_path: Path) -> None:
    tables = _build_synthetic_v2_tables()
    manifest = tables[FILINGS_MANIFEST_TABLE]
    # Mark FPT 2025 Q2 as REVIEWED + NOT_APPLICABLE, 2025 Q4 as REVIEWED + UNKNOWN, 2024 Q2 as REVIEWED + UNMODIFIED
    manifest = manifest.with_columns(
        pl.when(pl.col("filing_id") == "FIL_FPT_2025_Q2")
        .then(pl.lit("REVIEWED"))
        .when(pl.col("filing_id") == "FIL_FPT_2025_Q4")
        .then(pl.lit("REVIEWED"))
        .when(pl.col("filing_id") == "FIL_FPT_2024_Q2")
        .then(pl.lit("REVIEWED"))
        .otherwise(pl.col("audit_status"))
        .alias("audit_status"),
        pl.when(pl.col("filing_id") == "FIL_FPT_2025_Q2")
        .then(pl.lit("NOT_APPLICABLE"))
        .when(pl.col("filing_id") == "FIL_FPT_2025_Q4")
        .then(pl.lit("UNKNOWN"))
        .when(pl.col("filing_id") == "FIL_FPT_2024_Q2")
        .then(pl.lit("UNMODIFIED"))
        .otherwise(pl.col("audit_opinion"))
        .alias("audit_opinion"),
    )
    # Add a 2026 manifest-only filing to verify date fallback VAS_TT99_2025
    extra_row = manifest.filter(pl.col("filing_id") == "FIL_FPT_2025_Q4").with_columns(
        pl.lit("FIL_FPT_2026_Q2").alias("filing_id"),
        pl.lit(date(2026, 6, 30)).alias("period_end_date"),
        pl.lit("REVIEWED").alias("audit_status"),
        pl.lit("NOT_APPLICABLE").alias("audit_opinion"),
    )
    tables[FILINGS_MANIFEST_TABLE] = pl.concat([manifest, extra_row])
    data_root, _ = _publish_snapshot(tmp_path, tables, "snap_d6_001")
    app = create_app(data_root=data_root, valkey_url="")

    with TestClient(app) as client:
        r = client.get("/api/v2/companies/FPT/filings")
        assert r.status_code == 200
        by_fid = {item["filing_id"]: item for item in r.json()["items"]}
        assert by_fid["FIL_FPT_2025_Q2"]["audit_opinion"] == "REVIEWED_NO_OPINION"
        assert by_fid["FIL_FPT_2025_Q2"]["taxonomy_version"] == "VAS_TT200_2014"
        assert by_fid["FIL_FPT_2025_Q4"]["audit_opinion"] == "REVIEWED_NO_OPINION"
        assert by_fid["FIL_FPT_2024_Q2"]["audit_opinion"] == "UNMODIFIED"
        assert by_fid["FIL_FPT_2026_Q2"]["taxonomy_version"] == "VAS_TT99_2025"
        assert by_fid["FIL_FPT_2026_Q2"]["audit_opinion"] == "REVIEWED_NO_OPINION"


def test_d7_industry_benchmarks_cohort_fallback(tmp_path: Path) -> None:
    tables = _build_synthetic_v2_tables()
    # Drop CMG's roe_total benchmark row on 2025-12-31; keep FPT's ICB_LEVEL_3/REGIME_FALLBACK rows
    bench = tables[BENCHMARKS_TABLE]
    bench = bench.filter(
        ~(
            (pl.col("issuer_id") == "CMG")
            & (pl.col("period_end_date") == date(2025, 12, 31))
            & (pl.col("metric_or_ratio_name") == "roe_total")
        )
    )
    tables[BENCHMARKS_TABLE] = bench
    data_root, _ = _publish_snapshot(tmp_path, tables, "snap_d7_001")
    app = create_app(data_root=data_root, valkey_url="")

    with TestClient(app) as client:
        r_sum = client.get(
            "/api/v2/companies/CMG/benchmarks/summary",
            params={"period_end_date": "2025-12-31"},
        )
        assert r_sum.status_code == 200
        roe_item = next(
            i for i in r_sum.json()["items"] if i["metric_name"] == "roe_total"
        )
        assert roe_item["peer_median"] is not None
        assert roe_item["peer_count"] > 0
        assert roe_item["peer_group_level"] == "REGIME_FALLBACK"
        assert roe_item["icb_code_used"] is None

        r_cmp = client.get(
            "/api/v1/companies/CMG/benchmarks",
            params={"metric_name": "roe_total", "period_end_date": "2025-12-31"},
        )
        assert r_cmp.status_code == 200
        cmp_body = r_cmp.json()
        assert cmp_body["peer_median"] is not None
        assert cmp_body["peer_group_level"] == "REGIME_FALLBACK"
        assert cmp_body["icb_code_used"] is None
        assert len(cmp_body["peer_ranking"]) > 0


def test_d8_near_zero_denominator_guards_layer4_and_layer5(tmp_path: Path) -> None:
    # 1. Layer 4 calculate_ratios tiny denominator guard
    l4_facts = make_l4_facts(
        {
            (2025, None): {
                "BS_TOTAL_ASSETS": 10_000_000_000.0,
                "BS_TOTAL_EQUITY": 5_000_000_000.0,
                "BS_SHORT_TERM_BORROWINGS": 1_000_000_000.0,
                "IS_NET_REVENUE": 500_000_000.0,  # < 1e9
                "IS_PROFIT_BEFORE_TAX": 100_000_000.0,
                "IS_INTEREST_EXPENSE": -50_000_000.0,  # < 1e8
                "IS_NET_INCOME": 80_000_000.0,
            }
        }
    )
    l4_ratios = calculate_ratios(
        prepare_periods(l4_facts, make_l4_issuer(), as_of=date(2026, 6, 1))
    ).row(-1, named=True)
    assert l4_ratios["ebit_margin"] is None
    assert l4_ratios["ebit_margin_flag"] == RatioStatusFlag.TINY_DENOMINATOR.value
    assert l4_ratios["interest_coverage"] is None
    assert l4_ratios["interest_coverage_flag"] == RatioStatusFlag.TINY_DENOMINATOR.value

    # 2. Layer 4 simulate_financial_stress tiny borrowing cost guard + debt-at-risk preserved
    stress_in = pl.DataFrame(
        {
            "issuer_id": ["QBS"],
            "ticker": ["QBS"],
            "accounting_regime": ["CORPORATE"],
            "statement_scope": ["CONSOLIDATED"],
            "period_type": ["QUARTER"],
            "period_end_date": [date(2026, 9, 30)],
            "net_revenue_ttm": [5_000_000_000.0],
            "gross_profit_ttm": [500_000_000.0],
            "cogs_ttm": [4_500_000_000.0],
            "cfo_ttm": [-100_000_000.0],
            "borrowing_cost_ttm": [0.0],
            "total_debt": [617_000_000.0],
            "ebit_ttm": [-5_000_000_000.0],
            "gross_margin": [0.10],
            "effective_tax_rate": [0.20],
            "interest_coverage": [None],
            "interest_coverage_flag": ["NO_INTEREST_EXPENSE"],
        }
    )
    stressed = simulate_financial_stress(
        stress_in, StressScenario(name="bps200", interest_rate_shock_bps=200.0)
    ).row(0, named=True)
    assert stressed["stressed_borrowing_cost_ttm"] == pytest.approx(12_340_000.0)
    assert stressed["stressed_interest_coverage"] is None
    assert stressed["stressed_interest_coverage_flag"] == "TINY_DENOMINATOR"
    assert stressed["baseline_debt_at_risk"] is False
    assert stressed["stressed_debt_at_risk"] is True
    assert stressed["newly_distressed_under_shock"] is True

    # 3. Layer 5 serving masks precomputed tiny-denominator outliers
    tables = _build_synthetic_v2_tables()
    tables[RATIOS_TABLE] = tables[RATIOS_TABLE].with_columns(
        pl.when(pl.col("issuer_id") == "CMG")
        .then(pl.lit(600.0))
        .otherwise(pl.col("net_revenue_ttm"))
        .alias("net_revenue_ttm"),
        pl.when(pl.col("issuer_id") == "CMG")
        .then(pl.lit(-8_052_893.4))
        .otherwise(pl.col("ebit_margin"))
        .alias("ebit_margin"),
        pl.when(pl.col("issuer_id") == "CMG")
        .then(pl.lit(1.0))
        .otherwise(pl.col("borrowing_cost_ttm"))
        .alias("borrowing_cost_ttm"),
        pl.when(pl.col("issuer_id") == "CMG")
        .then(pl.lit(12_660_000_000.0))
        .otherwise(pl.col("interest_coverage"))
        .alias("interest_coverage"),
    )
    tables[BENCHMARKS_TABLE] = tables[BENCHMARKS_TABLE].with_columns(
        pl.when(
            (pl.col("issuer_id") == "CMG")
            & (pl.col("metric_or_ratio_name") == "ebit_margin")
        )
        .then(pl.lit(-8_052_893.4))
        .when(
            (pl.col("issuer_id") == "CMG")
            & (pl.col("metric_or_ratio_name") == "interest_coverage")
        )
        .then(pl.lit(12_660_000_000.0))
        .otherwise(pl.col("value"))
        .alias("value")
    )
    data_root, _ = _publish_snapshot(tmp_path, tables, "snap_d8_001")
    app = create_app(data_root=data_root, valkey_url="")

    with TestClient(app) as client:
        scr = client.get(
            "/api/v1/screener", params={"period_end_date": "2025-12-31"}
        ).json()
        cmg_scr = next(i for i in scr["items"] if i["ticker"] == "CMG")
        assert cmg_scr["ebit_margin"] is None
        assert cmg_scr["interest_coverage"] is None

        prof = client.get("/api/v1/companies/CMG/profile").json()
        assert prof["dupont_series"][-1]["ebit_margin"] is None
        assert prof["ratios_table"][-1]["interest_coverage"] is None

        for metric in ("ebit_margin", "interest_coverage"):
            bench_cmg = client.get(
                "/api/v1/companies/CMG/benchmarks",
                params={
                    "metric_name": metric,
                    "period_type": "QUARTER",
                    "period_end_date": "2025-12-31",
                },
            ).json()
            assert bench_cmg["company_value"] is None
            assert len(bench_cmg["history"]) > 0
            assert all(pt["value"] is None for pt in bench_cmg["history"])
            ranking = bench_cmg["peer_ranking"]
            assert len(ranking) == 3
            assert ranking[-1]["ticker"] == "CMG"
            assert ranking[-1]["value"] is None
            assert all(r["value"] is not None for r in ranking[:-1])

            bench_fpt = client.get(
                "/api/v1/companies/FPT/benchmarks",
                params={
                    "metric_name": metric,
                    "period_type": "QUARTER",
                    "period_end_date": "2025-12-31",
                },
            ).json()
            fpt_rank = bench_fpt["peer_ranking"]
            assert fpt_rank[-1]["ticker"] == "CMG"
            assert fpt_rank[-1]["value"] is None
            assert fpt_rank[0]["ticker"] != "CMG"

        lin = client.get(
            "/api/v1/companies/CMG/lineage",
            params={"ratio_name": "ebit_margin", "period_end_date": "2025-12-31"},
        ).json()
        assert lin["ratio_value"] is None
        assert lin["status_flag"] == "TINY_DENOMINATOR"

        st = client.post(
            "/api/v1/stress-test",
            json={"scenario_name": "Zero", "period_end_date": "2025-12-31"},
        ).json()
        assert st["issuer_icrs"]["CMG"] is None


def test_d10_company_display_name_normalization(tmp_path: Path) -> None:
    assert normalize_company_display_name("Cổ phiếu  CTCP A", ticker="AAA") == "CTCP A"
    assert normalize_company_display_name("APP - CTCP B", ticker="APP") == "CTCP B"
    assert normalize_company_display_name("FCS- CTCP C", ticker="FCS") == "CTCP C"
    assert (
        normalize_company_display_name("CTCP - Tổng công ty D", ticker="DTK")
        == "CTCP - Tổng công ty D"
    )
    assert (
        normalize_company_display_name("Becamex - CTCP", ticker="BCM")
        == "Becamex - CTCP"
    )
    assert (
        normalize_company_display_name('\t=HYPERLINK("http://evil")', ticker="T004")
        == '\t=HYPERLINK("http://evil")'
    )
    assert normalize_company_display_name("\r=1+1", ticker="T005") == "\r=1+1"

    df_expr = pl.DataFrame(
        {
            "ticker": ["APP", "FCS", "DTK", "BCM", "T004"],
            "company_name_vn": [
                "Cổ phiếu  APP - CTCP B",
                "FCS- CTCP C",
                "CTCP - Tổng công ty D",
                "Becamex - CTCP",
                '\t=HYPERLINK("http://evil")',
            ],
        }
    ).with_columns(normalize_company_display_name_expr())
    assert df_expr["company_name_vn"].to_list() == [
        "CTCP B",
        "CTCP C",
        "CTCP - Tổng công ty D",
        "Becamex - CTCP",
        '\t=HYPERLINK("http://evil")',
    ]

    tables = _build_synthetic_v2_tables()
    tables["issuers.parquet"] = tables["issuers.parquet"].with_columns(
        pl.when(pl.col("ticker") == "FPT")
        .then(pl.lit("Cổ phiếu  FPT - Công ty Cổ phần FPT"))
        .when(pl.col("ticker") == "CMG")
        .then(pl.lit("CMG- Tập đoàn Công nghệ CMC"))
        .otherwise(pl.lit("Cổ phiếu Tập đoàn Hòa Phát"))
        .alias("company_name_vn")
    )
    data_root, _ = _publish_snapshot(tmp_path, tables, "snap_d10_001")
    app = create_app(data_root=data_root, valkey_url="")

    with TestClient(app) as client:
        iss_items = {
            i["ticker"]: i["company_name_vn"]
            for i in client.get("/api/v1/issuers").json()["items"]
        }
        assert iss_items == {
            "FPT": "Công ty Cổ phần FPT",
            "CMG": "Tập đoàn Công nghệ CMC",
            "HPG": "Tập đoàn Hòa Phát",
        }
        assert (
            client.get("/api/v1/companies/FPT/profile").json()["company_name_vn"]
            == "Công ty Cổ phần FPT"
        )
        scr_items = {
            i["ticker"]: i["company_name_vn"]
            for i in client.get("/api/v1/screener").json()["items"]
        }
        assert scr_items["FPT"] == "Công ty Cổ phần FPT"
        assert scr_items["CMG"] == "Tập đoàn Công nghệ CMC"


def test_d11_stress_test_issuer_icrs_and_scenario_a_diff(tmp_path: Path) -> None:
    tables = _build_synthetic_v2_tables()
    data_root, _ = _publish_snapshot(tmp_path, tables, "snap_d11_001")
    app = create_app(data_root=data_root, valkey_url="")

    with TestClient(app) as client:
        r_a = client.post(
            "/api/v1/stress-test",
            json={
                "scenario_name": "Scenario A",
                "period_end_date": "2025-12-31",
                "interest_rate_shock_bps": 100.0,
            },
        )
        assert r_a.status_code == 200
        body_a = r_a.json()
        assert body_a["total_newly_distressed"] == body_a["newly_distressed_count"]
        assert set(body_a["issuer_icrs"].keys()) == {"CMG", "FPT", "HPG"}

        r_b = client.post(
            "/api/v1/stress-test",
            json={
                "scenario_name": "Scenario B",
                "period_end_date": "2025-12-31",
                "interest_rate_shock_bps": 400.0,
                "revenue_shock_pct": -0.25,
                "reference_icrs": {
                    k.lower(): v for k, v in body_a["issuer_icrs"].items()
                },
            },
        )
        assert r_b.status_code == 200
        body_b = r_b.json()
        assert body_b["total_newly_distressed"] == body_b["newly_distressed_count"]
        for item in body_b["top_stressed_issuers"]:
            assert item["scenario_a_icr"] == pytest.approx(
                body_a["issuer_icrs"][item["ticker"]]
            )

        r_empty = client.post(
            "/api/v1/stress-test",
            json={"scenario_name": "Empty", "tickers": ["NOPE99"]},
        )
        assert r_empty.status_code == 200
        assert r_empty.json()["total_newly_distressed"] == 0
        assert r_empty.json()["issuer_icrs"] == {}

        r_bool = client.post(
            "/api/v1/stress-test",
            json={
                "scenario_name": "Bool Reject",
                "reference_icrs": {"FPT": True},
            },
        )
        assert r_bool.status_code == 422
