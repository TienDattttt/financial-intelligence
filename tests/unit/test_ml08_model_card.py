"""Unit tests for ML-08 (GATE-ML-6): versioned ML artifacts, model_card_meta in DuckLake, and metadata-dir independence."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest

from finintel.ml.supervised.train import SECTOR_ADJUSTED_FEATURES
from finintel.ml.tables import build_ml_artifacts
from finintel.pipeline.state_store import StateStore
from finintel.serving.queries import DashboardQueryService
from finintel.storage.ducklake_store import (
    MANAGED_MART_CATALOG_TABLES,
    MANAGED_MART_TABLES,
    OPTIONAL_MANAGED_MART_TABLES,
    DuckLakeStore,
)
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import (
    BENCHMARKS_TABLE,
    EXTENSION_PORT_TABLES,
    ML_SCORES_TABLE,
    MODEL_CARD_META_SCHEMA,
    MODEL_CARD_META_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
    canonicalize_financial_facts,
)
from finintel.storage.snapshot_publisher import KNOWN_TABLES
from finintel.transform.incremental import (
    ML_ARTIFACT_FILENAME,
    ML_MANIFEST_FILENAME,
    TRANSFORM_STATE_FILENAME,
    MLTransformConfig,
    load_ml_artifact_bundle,
    run_incremental_analytics_transform,
    write_ml_artifact_bundle,
    write_transform_state,
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _quarter_dates(year: int, quarter: int) -> tuple[date, date, date]:
    starts = {1: (1, 1), 2: (4, 1), 3: (7, 1), 4: (10, 1)}
    ends = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    p_start = date(year, *starts[quarter])
    p_end = date(year, *ends[quarter])
    pub = p_end + timedelta(days=25)
    return p_start, p_end, pub


def _build_panel_period_rows(
    issuer_id: str,
    *,
    year: int,
    quarter: int,
    distressed_profile: bool,
    taxonomy_version: str = "VAS_TT200_2014",
    scale: float = 1.0,
) -> list[dict[str, Any]]:
    p_start, p_end, pub = _quarter_dates(year, quarter)
    fy_start = date(year, 1, 1)
    filing_id = f"{issuer_id}-{year}Q{quarter}-R1"

    vnd = 1_000_000.0
    if distressed_profile:
        assets = 100_000.0 * vnd * scale
        debt_st = 42_000.0 * vnd * scale
        debt_lt = 36_000.0 * vnd * scale
        liab = 86_000.0 * vnd * scale
        equity = 14_000.0 * vnd * scale
        curr_assets = 28_000.0 * vnd * scale
        cash = 2_000.0 * vnd * scale
        receivables = 12_000.0 * vnd * scale
        inventories = 12_000.0 * vnd * scale
        curr_liab = 48_000.0 * vnd * scale
        payables = 11_000.0 * vnd * scale
        rev = 14_000.0 * vnd * scale
        cogs = -13_200.0 * vnd * scale
        gp = 800.0 * vnd * scale
        op_profit = -1_800.0 * vnd * scale
        interest = -1_600.0 * vnd * scale
        ebt = -3_400.0 * vnd * scale
        ni = -3_400.0 * vnd * scale
        cfo = -2_100.0 * vnd * scale
    else:
        assets = 150_000.0 * vnd * scale
        debt_st = 10_000.0 * vnd * scale
        debt_lt = 12_000.0 * vnd * scale
        liab = 42_000.0 * vnd * scale
        equity = 108_000.0 * vnd * scale
        curr_assets = 75_000.0 * vnd * scale
        cash = 22_000.0 * vnd * scale
        receivables = 18_000.0 * vnd * scale
        inventories = 20_000.0 * vnd * scale
        curr_liab = 26_000.0 * vnd * scale
        payables = 9_000.0 * vnd * scale
        rev = 42_000.0 * vnd * scale
        cogs = -29_000.0 * vnd * scale
        gp = 13_000.0 * vnd * scale
        op_profit = 6_500.0 * vnd * scale
        interest = -400.0 * vnd * scale
        ebt = 6_100.0 * vnd * scale
        ni = 4_900.0 * vnd * scale
        cfo = 5_400.0 * vnd * scale

    metrics: dict[str, tuple[str, float]] = {
        "BS_TOTAL_ASSETS": ("BALANCE_SHEET", assets),
        "BS_CURRENT_ASSETS": ("BALANCE_SHEET", curr_assets),
        "BS_CASH_AND_EQUIVALENTS": ("BALANCE_SHEET", cash),
        "BS_SHORT_TERM_INVESTMENTS": ("BALANCE_SHEET", 2_000.0 * vnd * scale),
        "BS_TRADE_RECEIVABLES_ST": ("BALANCE_SHEET", receivables),
        "BS_INVENTORIES": ("BALANCE_SHEET", inventories),
        "BS_CURRENT_LIABILITIES": ("BALANCE_SHEET", curr_liab),
        "BS_SHORT_TERM_BORROWINGS": ("BALANCE_SHEET", debt_st),
        "BS_LONG_TERM_BORROWINGS": ("BALANCE_SHEET", debt_lt),
        "BS_TOTAL_LIABILITIES": ("BALANCE_SHEET", liab),
        "BS_TOTAL_EQUITY": ("BALANCE_SHEET", equity),
        "BS_MINORITY_INTEREST": ("BALANCE_SHEET", 500.0 * vnd * scale),
        "BS_TRADE_PAYABLES_ST": ("BALANCE_SHEET", payables),
        "BS_RETAINED_EARNINGS": (
            "BALANCE_SHEET",
            18_000.0 * vnd * scale
            if not distressed_profile
            else -8_000.0 * vnd * scale,
        ),
        "BS_ORDINARY_SHARES_CAPITAL": ("BALANCE_SHEET", 20_000.0 * vnd * scale),
        "IS_NET_REVENUE": ("INCOME_STATEMENT", rev),
        "IS_COST_OF_GOODS_SOLD": ("INCOME_STATEMENT", cogs),
        "IS_GROSS_PROFIT": ("INCOME_STATEMENT", gp),
        "IS_SELLING_EXPENSES": ("INCOME_STATEMENT", -1_400.0 * vnd * scale),
        "IS_ADMINISTRATIVE_EXPENSES": ("INCOME_STATEMENT", -1_200.0 * vnd * scale),
        "IS_OPERATING_PROFIT": ("INCOME_STATEMENT", op_profit),
        "IS_PROFIT_BEFORE_TAX": ("INCOME_STATEMENT", ebt),
        "IS_INTEREST_EXPENSE": ("INCOME_STATEMENT", interest),
        "IS_CURRENT_INCOME_TAX": (
            "INCOME_STATEMENT",
            -1_200.0 * vnd * scale if not distressed_profile else 0.0,
        ),
        "IS_NET_INCOME": ("INCOME_STATEMENT", ni),
        "IS_NET_INCOME_PARENT": ("INCOME_STATEMENT", ni),
        "IS_DILUTIVE_NONCASH_EQUITY_ISSUANCE": ("INCOME_STATEMENT", 0.0),
        "CF_OPERATING_CASH_FLOW": ("CASH_FLOW", cfo),
        "CF_EQUITY_ISSUANCE_PROCEEDS": ("CASH_FLOW", 0.0),
        "CF_DEPRECIATION_AMORTIZATION": ("CASH_FLOW", 1_100.0 * vnd * scale),
        "CF_CAPITAL_EXPENDITURE": ("CASH_FLOW", -1_500.0 * vnd * scale),
    }
    rows: list[dict[str, Any]] = []
    for metric_id, (stype, val) in metrics.items():
        rows.append(
            {
                "issuer_id": issuer_id,
                "ticker": issuer_id,
                "ticker_at_filing": issuer_id,
                "accounting_regime": "CORPORATE",
                "statement_scope": "CONSOLIDATED",
                "statement_type": stype,
                "period_type": "QUARTER",
                "fiscal_year": year,
                "fiscal_quarter": quarter,
                "fiscal_year_start_date": fy_start,
                "period_start_date": p_start,
                "period_end_date": p_end,
                "metric_id": metric_id,
                "value": float(val),
                "ttm_value": float(val * 4.0)
                if stype != "BALANCE_SHEET"
                else float(val),
                "unit": "VND",
                "value_type": "STOCK" if stype == "BALANCE_SHEET" else "FLOW",
                "sign_convention": "SIGNED",
                "comparable_across_taxonomy": True,
                "taxonomy_version": taxonomy_version,
                "derivation_method": "REPORTED_DIRECTLY",
                "filing_id": filing_id,
                "revision_no": 1,
                "revision_reason": "ORIGINAL",
                "audit_status": "REVIEWED" if quarter == 2 else "UNAUDITED",
                "audit_opinion": "UNMODIFIED",
                "source_provider": "TEST",
                "published_at": pub,
                "published_at_basis": "EXACT_FILING",
                "effective_available_at": pub,
                "is_filed_late": False,
                "source_filing_ids": [filing_id],
            }
        )
    return rows


def build_ml08_real_shape_fixture() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Construct a multi-year 2017-2026 corporate panel with TT200/TT99 & regulatory events."""
    case_tickers = ["HBC", "POM", "HNG", "DLG", "SMC", "QBS", "PVX", "PVL"]
    distressed_schedule: dict[str, tuple[date, date]] = {
        "HBC": (date(2020, 4, 15), date(2021, 7, 20)),
        "POM": (date(2021, 4, 12), date(2022, 5, 10)),
        "HNG": (date(2022, 4, 18), date(2023, 8, 9)),
        "DLG": (date(2023, 4, 21), date(2024, 6, 15)),
        "SMC": (date(2024, 4, 10), date(2025, 5, 12)),
        "QBS": (date(2025, 4, 14), date(2025, 11, 18)),
        "PVX": (date(2020, 6, 10), date(2022, 9, 1)),
        "PVL": (date(2023, 5, 16), date(2024, 10, 20)),
        "DIST09": (date(2021, 5, 12), date(2023, 3, 10)),
        "DIST10": (date(2022, 6, 12), date(2024, 3, 10)),
        "DIST11": (date(2023, 6, 12), date(2025, 2, 10)),
        "DIST12": (date(2024, 5, 12), date(2025, 9, 10)),
    }
    healthy_tickers = [f"SAFE{idx:02d}" for idx in range(1, 13)]
    all_tickers = [
        *case_tickers,
        "DIST09",
        "DIST10",
        "DIST11",
        "DIST12",
        *healthy_tickers,
    ]

    issuer_rows: list[dict[str, Any]] = []
    for idx, tkr in enumerate(all_tickers):
        is_case = tkr in distressed_schedule
        delist_dt = distressed_schedule[tkr][1] if is_case else None
        issuer_rows.append(
            {
                "issuer_id": tkr,
                "ticker": tkr,
                "exchange": "HOSE" if idx % 2 == 0 else "HNX",
                "company_name_vn": f"Công ty {tkr}",
                "accounting_regime": "CORPORATE",
                "icb_code_level2": "1700" if idx % 3 == 0 else "2300",
                "icb_name_level2": "Tài nguyên" if idx % 3 == 0 else "Xây dựng",
                "icb_code_level3": "1750" if idx % 3 == 0 else "2350",
                "icb_name_level3": "Thép" if idx % 3 == 0 else "Hạ tầng",
                "industry_snapshot_date": date(2026, 1, 1),
                "delisted_date": delist_dt,
            }
        )
    issuers = pl.DataFrame(issuer_rows)

    fact_rows: list[dict[str, Any]] = []
    for idx, tkr in enumerate(all_tickers):
        event_pair = distressed_schedule.get(tkr)
        scale = 1.0 + 0.04 * idx
        for year in range(2017, 2027):
            quarters = (1, 2) if year == 2026 else (1, 2, 3, 4)
            tax_ver = "VAS_TT99_2025" if year == 2026 else "VAS_TT200_2014"
            for q in quarters:
                _, pend, _ = _quarter_dates(year, q)
                is_distressed = event_pair is not None and pend >= (
                    event_pair[0] - timedelta(days=540)
                )
                fact_rows.extend(
                    _build_panel_period_rows(
                        tkr,
                        year=year,
                        quarter=q,
                        distressed_profile=is_distressed,
                        taxonomy_version=tax_ver,
                        scale=scale,
                    )
                )

    reg_rows: list[dict[str, Any]] = []
    for tkr, (ctrl_dt, delist_dt) in distressed_schedule.items():
        reg_rows.append(
            {
                "issuer_id": tkr,
                "event_date": ctrl_dt,
                "effective_from": ctrl_dt,
                "resolution_date": None,
                "event_type": "CONTROL",
                "event_stage": "FIRST_ENTRY",
                "exchange_delisted_from": None,
                "source_provider": "vietcap_iq",
                "source_url": f"https://iq.vietcap.com.vn/events/{tkr}-ctrl",
                "raw_headline": f"{tkr} đưa vào diện kiểm soát",
                "payload_sha256": _sha256_text(f"{tkr}-ctrl-{ctrl_dt.isoformat()}"),
            }
        )
        reg_rows.append(
            {
                "issuer_id": tkr,
                "event_date": delist_dt,
                "effective_from": delist_dt,
                "resolution_date": None,
                "event_type": "FORCED_DELISTING",
                "event_stage": "ESCALATION",
                "exchange_delisted_from": "HOSE",
                "source_provider": "cafef_events",
                "source_url": f"https://cafef.vn/events/{tkr}-delist",
                "raw_headline": f"{tkr} hủy niêm yết bắt buộc",
                "payload_sha256": _sha256_text(f"{tkr}-delist-{delist_dt.isoformat()}"),
            }
        )
    regulatory_events = pl.DataFrame(
        reg_rows,
        schema={
            "issuer_id": pl.String,
            "event_date": pl.Date,
            "effective_from": pl.Date,
            "resolution_date": pl.Date,
            "event_type": pl.String,
            "event_stage": pl.String,
            "exchange_delisted_from": pl.String,
            "source_provider": pl.String,
            "source_url": pl.String,
            "raw_headline": pl.String,
            "payload_sha256": pl.String,
        },
    )
    facts = canonicalize_financial_facts(
        pl.DataFrame(fact_rows, infer_schema_length=None)
    )
    return facts, issuers, regulatory_events


def test_ml08_schema_and_b03_table_boundary_preserved() -> None:
    """model_card_meta belongs to KNOWN_TABLES & OPTIONAL_MANAGED_MART_TABLES without breaking B-03."""
    assert len(MANAGED_MART_TABLES) == 10
    assert MODEL_CARD_META_TABLE in OPTIONAL_MANAGED_MART_TABLES
    assert "model_card_meta" in MANAGED_MART_CATALOG_TABLES
    assert MODEL_CARD_META_TABLE in KNOWN_TABLES
    assert MANAGED_MART_TABLES.isdisjoint(EXTENSION_PORT_TABLES)


def test_ml08_versioned_artifact_persistence_incremental_link_and_ducklake_model_card(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GATE-ML-6: Persist XGBoost JSON + Preprocessor + Calibrator + R1-R5 cutoffs + SHA-256 + model_card_meta in DuckLake independent of data/metadata/."""
    facts, issuers, regulatory_events = build_ml08_real_shape_fixture()
    as_of = date(2026, 8, 15)
    run_id = "ml08_retrain_run_001"
    data_root = tmp_path / "data"
    model_dir = data_root / "metadata" / "ml_models"
    state_path = data_root / "metadata" / TRANSFORM_STATE_FILENAME

    # 1. Retrain with ML-05 23-feature set & REGULATORY_DISTRESS
    bundle = run_incremental_analytics_transform(
        facts,
        issuers,
        as_of=as_of,
        run_id=run_id,
        state_path=state_path,
        ml_config=MLTransformConfig(
            enabled=True,
            mode="retrain",
            model_artifact_dir=model_dir,
            approved_target=True,
            require_exact_filing=True,
            validation_start=date(2022, 1, 1),
            test_start=date(2024, 1, 1),
            target_mode="REGULATORY_DISTRESS",
            regulatory_events=regulatory_events,
            min_unique_positive_issuers=2,
        ),
        persist_state=True,
    )
    assert bundle.ml_artifact_payload is not None
    assert bundle.model_card_meta is not None
    assert bundle.model_card_meta.schema == MODEL_CARD_META_SCHEMA
    assert bundle.model_card_meta.height == 1

    write_ml_artifact_bundle(model_dir, bundle.ml_artifact_payload)
    write_transform_state(
        state_path, bundle.next_transform_state, snapshot_id="snap_ml08_1"
    )

    # 2. Verify versioned artifact layout tagged by run_id (XGBoost JSON + Preprocessor + Calibrator + R1-R5 + SHA-256)
    assert (model_dir / ML_ARTIFACT_FILENAME).is_file()
    assert (model_dir / ML_MANIFEST_FILENAME).is_file()
    run_artifact_dir = model_dir / "runs" / run_id
    for fname in (
        "xgboost_model.json",
        "preprocessor.pkl",
        "preprocessor_spec.json",
        "calibrator.json",
        "risk_tier_cutoffs.json",
        "bundle.pkl",
        "artifact_manifest.json",
    ):
        assert (run_artifact_dir / fname).is_file(), f"Missing {fname}"

    manifest_json = json.loads(
        (run_artifact_dir / "artifact_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest_json["run_id"] == run_id
    assert set(manifest_json["files"].keys()) == {
        "xgboost_model.json",
        "preprocessor.pkl",
        "preprocessor_spec.json",
        "calibrator.json",
        "risk_tier_cutoffs.json",
        "bundle.pkl",
    }
    for fname, meta in manifest_json["files"].items():
        actual_sha = hashlib.sha256((run_artifact_dir / fname).read_bytes()).hexdigest()
        assert actual_sha == meta["sha256"]

    # XGBoost native JSON is valid & loadable directly by XGBClassifier
    from xgboost import XGBClassifier

    reloaded_xgb = XGBClassifier()
    reloaded_xgb.load_model(str(run_artifact_dir / "xgboost_model.json"))
    calibrator_json = json.loads(
        (run_artifact_dir / "calibrator.json").read_text(encoding="utf-8")
    )
    cutoffs_json = json.loads(
        (run_artifact_dir / "risk_tier_cutoffs.json").read_text(encoding="utf-8")
    )
    assert (
        cutoffs_json["R2_min_pd"]
        < cutoffs_json["R3_min_pd"]
        < cutoffs_json["R4_min_pd"]
        < cutoffs_json["R5_min_pd"]
    )
    assert calibrator_json["platt_slope"] > 0.0

    # Loading by explicit run_id works; tampering with xgboost_model.json fails checksum verification
    loaded_by_run = load_ml_artifact_bundle(model_dir, run_id=run_id)
    assert loaded_by_run is not None
    assert loaded_by_run.trained_run_id == run_id

    orig_xgb_bytes = (run_artifact_dir / "xgboost_model.json").read_bytes()
    (run_artifact_dir / "xgboost_model.json").write_bytes(orig_xgb_bytes + b"\n")
    assert load_ml_artifact_bundle(model_dir, run_id=run_id) is None
    assert load_ml_artifact_bundle(model_dir) is None
    (run_artifact_dir / "xgboost_model.json").write_bytes(orig_xgb_bytes)
    assert load_ml_artifact_bundle(model_dir, run_id=run_id) is not None

    # 3. Verify Model Card real contents (23-feature, Walk-Forward 2021-2025, Bootstrap CI, True Unseen, R1-R5, TT200->TT99 PSI, Case Studies)
    card_row = bundle.model_card_meta.row(0, named=True)
    assert card_row["run_id"] == run_id
    assert card_row["feature_count"] == len(SECTOR_ADJUSTED_FEATURES) == 23
    card = json.loads(str(card_row["model_card_json"]))
    assert card["feature_count"] == 23
    wf = card["walk_forward"]
    assert wf["test_years"] == [2021, 2022, 2023, 2024, 2025]
    assert [row["year"] for row in wf["per_year"]] == [2021, 2022, 2023, 2024, 2025]
    assert {s["model"] for s in wf["summary_all_oos"]} == {
        "Baseline_1_AltmanZ",
        "Baseline_2_Logit",
        "XGBoost",
    }
    assert {s["model"] for s in wf["summary_true_unseen"]} == {
        "Baseline_1_AltmanZ",
        "Baseline_2_Logit",
        "XGBoost",
    }
    xgb_summary = next(s for s in wf["summary_all_oos"] if s["model"] == "XGBoost")
    assert xgb_summary["pooled_ci95"]["roc_auc"] is not None
    assert len(card["risk_tier_reliability"]["tiers"]) == 5
    psi = card["taxonomy_drift_psi"]
    assert psi["reference_taxonomy"] == "VAS_TT200_2014"
    assert psi["target_taxonomy"] == "VAS_TT99_2025"
    assert psi["reference_rows"] > 0
    assert psi["target_rows"] > 0
    assert psi["mean_feature_psi"] is not None
    assert len(card["case_studies"]) >= 6
    hbc_case = next(c for c in card["case_studies"] if c["ticker"] == "HBC")
    assert hbc_case["first_warning_or_control_date"] == "2020-04-15"
    assert hbc_case["forced_delisting_or_suspension_date"] == "2021-07-20"
    assert hbc_case["first_r4_r5_risk_tier"] in ("R4", "R5")
    assert len(hbc_case["top_3_shap_drivers"]) > 0

    # 4. Publish to DuckLake, then run incremental_score (ARCH-06) using run_id artifact without retraining
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    state.start_run(run_id, "MANUAL", {"mode": "retrain"}, None)
    store = DuckLakeStore(
        catalog_path=data_root / "metadata" / "ducklake_catalog.sqlite",
        data_path=data_root / "lakehouse" / "data",
        state_store=state,
    )
    tables_1 = {
        "issuers.parquet": bundle.issuers,
        "financial_facts.parquet": bundle.clean_facts,
        RATIOS_TABLE: bundle.financial_ratios,
        BENCHMARKS_TABLE: bundle.industry_benchmarks,
        RED_FLAGS_TABLE: bundle.accounting_red_flags,
        ML_SCORES_TABLE: bundle.ml_scores,
        MODEL_CARD_META_TABLE: bundle.model_card_meta,
    }
    try:
        store.publish_snapshot("snap_ml08_1", tables_1, run_id=run_id)
    except duckdb.Error as exc:
        pytest.skip(f"DuckLake extension unavailable in this environment: {exc}")
    state.finish_run(run_id, "SUCCEEDED", "snap_ml08_1")

    def _forbid_retrain(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("incremental_score must NOT retrain model!")

    monkeypatch.setattr(
        "finintel.ml.supervised.train.train_distress_model", _forbid_retrain
    )

    new_q3_rows = _build_panel_period_rows(
        "SAFE01",
        year=2026,
        quarter=3,
        distressed_profile=False,
        taxonomy_version="VAS_TT99_2025",
    )
    facts_v2 = canonicalize_financial_facts(
        pl.concat([facts, pl.DataFrame(new_q3_rows, schema=facts.schema)])
    )
    inc_bundle = run_incremental_analytics_transform(
        facts_v2,
        issuers,
        as_of=date(2026, 11, 1),
        run_id="ml08_inc_run_002",
        previous_ratios=bundle.financial_ratios,
        previous_benchmarks=bundle.industry_benchmarks,
        previous_red_flags=bundle.accounting_red_flags,
        previous_ml_scores=bundle.ml_scores,
        previous_model_card_meta=bundle.model_card_meta,
        previous_snapshot_id="snap_ml08_1",
        state_path=state_path,
        ml_config=MLTransformConfig(
            enabled=False,
            mode="incremental_score",
            model_artifact_dir=model_dir,
            model_run_id=run_id,
        ),
        persist_state=False,
    )
    assert inc_bundle.ml_run_manifest is not None
    assert inc_bundle.ml_run_manifest["trained_run_id"] == run_id
    assert inc_bundle.model_card_meta is not None

    state.start_run("ml08_inc_run_002", "MANUAL", {"mode": "incremental_score"}, None)
    store.publish_snapshot(
        "snap_ml08_2",
        {
            "issuers.parquet": inc_bundle.issuers,
            "financial_facts.parquet": inc_bundle.clean_facts,
            RATIOS_TABLE: inc_bundle.financial_ratios,
            BENCHMARKS_TABLE: inc_bundle.industry_benchmarks,
            RED_FLAGS_TABLE: inc_bundle.accounting_red_flags,
            ML_SCORES_TABLE: inc_bundle.ml_scores,
            MODEL_CARD_META_TABLE: inc_bundle.model_card_meta,
        },
        run_id="ml08_inc_run_002",
    )
    state.finish_run("ml08_inc_run_002", "SUCCEEDED", "snap_ml08_2")

    # 5. H-04 Metadata-dir independence: delete ml_models/ and ml_runs/ completely,
    #    then read Model Card exclusively from DuckLake snapshot!
    shutil.rmtree(model_dir)
    ml_runs_dir = data_root / "metadata" / "ml_runs"
    if ml_runs_dir.exists():
        shutil.rmtree(ml_runs_dir)

    repo = ParquetRepository(data_root=data_root, backend="ducklake")
    served_card = DashboardQueryService(repo).get_model_card()
    assert served_card["run_id"] == run_id
    assert served_card["artifact_sha256"] == card["artifact_sha256"]
    assert served_card["walk_forward"]["test_years"] == [2021, 2022, 2023, 2024, 2025]
    assert len(served_card["case_studies"]) >= 6


def test_build_ml_artifacts_direct_emits_model_card_and_meta() -> None:
    """build_ml_artifacts() returns populated model_card and validated model_card_meta."""
    facts, issuers, regulatory_events = build_ml08_real_shape_fixture()
    artifacts = build_ml_artifacts(
        facts,
        issuers,
        as_of=date(2026, 8, 15),
        validation_start=None,
        test_start=date(2024, 1, 1),
        approved_target=True,
        require_exact_filing=True,
        feature_set="sector_adjusted",
        target_mode="REGULATORY_DISTRESS",
        regulatory_events=regulatory_events,
        min_unique_positive_issuers=2,
        run_id="direct_ml08_run",
        walk_forward_n_bootstrap=60,
    )
    assert artifacts.model_card is not None
    assert artifacts.model_card_meta is not None
    assert artifacts.model_card["run_id"] == "direct_ml08_run"
    assert artifacts.model_card["feature_count"] == 23
    assert artifacts.model_card_meta.height == 1
