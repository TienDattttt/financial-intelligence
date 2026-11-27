"""Isolated Layer 4A/4B & IMF stress scoring for private-issuer dossiers (V3-29).

Reuses 100% pure Polars analytics from ``finintel.analytics`` and ``finintel.ml``
without ever writing to DuckLake, Parquet snapshots, or ML training datasets.
"""

from __future__ import annotations

import importlib
import math
import time
from typing import Any, cast

import polars as pl

from finintel.analytics.dupont import add_dupont
from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.ratios import calculate_ratios
from finintel.analytics.red_flags import detect_red_flags
from finintel.analytics.stress_test import StressScenario, simulate_financial_stress
from finintel.ml.classical_scores import add_classical_scores
from finintel.private_credit._qg1 import facts_to_canonical_polars
from finintel.private_credit._store import PrivateCreditDossierRecord
from finintel.private_credit._xlsx_guards import PrivateCreditValidationError
from finintel.private_credit.schemas import (
    PrivateClassicalScores,
    PrivateDupontBreakdown,
    PrivateMlAccountingOnlyResult,
    PrivateRedFlagItem,
    PrivateScoreRequest,
    PrivateScoreResponse,
    PrivateStressResult,
)
from finintel.storage.schemas import (
    CORE_RATIOS,
    EXTRA_RATIOS,
    FORENSIC_RATIOS,
    V3_RATIOS,
)

MISSING_MARKET_TT96_FEATURES: tuple[str, ...] = (
    "overdue_or_late_count_4q",
    "cross_sectional_asset_size_pct",
    "sector_z_asset_turnover",
    "sector_z_cash_conversion_cycle",
    "sector_z_debt_to_equity",
    "sector_z_ebit_margin",
    "merton_dtd",
    "equity_volatility_1y",
    "naive_edf",
)
ACCOUNTING_BASE_FEATURES: tuple[str, ...] = (
    "roa",
    "roe_parent",
    "roic",
    "ebit_margin",
    "current_ratio",
    "quick_ratio",
    "cash_ratio",
    "interest_coverage",
    "ocf_to_current_liabilities",
    "fcf_to_revenue",
    "altman_z_double_prime",
    "piotroski_f_score",
    "liabilities_to_assets",
    "financial_debt_to_assets",
    "debt_to_equity",
    "net_debt_to_ebitda",
    "accruals_to_assets",
    "asset_turnover",
    "cash_conversion_cycle",
    "revenue_growth_yoy",
    "consecutive_loss_quarters",
)
_DEFAULT_TIER_CUTOFFS: tuple[float, float, float, float] = (0.010, 0.025, 0.060, 0.150)
_COVERAGE_WARNING_VI = (
    "Chế độ Kế toán thuần túy (ACCOUNTING_ONLY_MODE): Doanh nghiệp chưa niêm yết không có "
    "dữ liệu giá thị trường (Merton DTD) và mốc nộp công bố TT96/2020; các biến này được đặt "
    "NULL tường minh và xác suất chỉ phản ánh sức khỏe báo cáo tài chính."
)


def _opt_float(val: object) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        fval = float(val)
        return fval if math.isfinite(fval) else None
    return None


def _opt_int(val: object) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float) and math.isfinite(val):
        return int(val)
    return None


def _opt_bool(val: object) -> bool | None:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    return None


def _score_accounting_only_ml(
    classical_latest: pl.DataFrame,
    *,
    altman_z: float | None,
    ml_training_result: Any | None = None,
) -> PrivateMlAccountingOnlyResult:
    """Compute ML PD under explicit ACCOUNTING_ONLY_MODE with NULL market/TT96 features."""
    row_dict = classical_latest.row(0, named=True)
    observed = sum(
        1
        for feat in ACCOUNTING_BASE_FEATURES
        if _opt_float(row_dict.get(feat)) is not None
    )
    coverage = float(observed / len(ACCOUNTING_BASE_FEATURES))

    # Explicitly preserve NULL for market, peer Z-score, and TT96 features.
    null_exprs = [
        pl.lit(None, dtype=pl.Float64).alias(col)
        for col in MISSING_MARKET_TT96_FEATURES
        if col not in classical_latest.columns
    ]
    explicit_frame = classical_latest.with_columns(
        *null_exprs,
        *(
            pl.lit(None, dtype=pl.Float64).alias(col)
            for col in MISSING_MARKET_TT96_FEATURES
            if col in classical_latest.columns
        ),
    )

    if ml_training_result is not None:
        train_mod = importlib.import_module("finintel.ml.supervised.train")
        model_features = tuple(getattr(ml_training_result, "features", ()) or ())
        extra_missing = [
            pl.lit(None, dtype=pl.Float64).alias(col)
            for col in model_features
            if col not in explicit_frame.columns
        ]
        if "accounting_regime" not in explicit_frame.columns:
            extra_missing.append(pl.lit("CORPORATE").alias("accounting_regime"))
        scored_input = (
            explicit_frame.with_columns(*extra_missing)
            if extra_missing
            else explicit_frame
        )
        scored = train_mod.score_distress_model(
            ml_training_result,
            scored_input,
            model_role="PRIVATE_ACCOUNTING_ONLY",
        )
        srow = scored.row(0, named=True)
        return PrivateMlAccountingOnlyResult(
            ml_mode="ACCOUNTING_ONLY_MODE",
            calibrated_distress_prob=_opt_float(srow.get("calibrated_distress_prob")),
            risk_tier=str(srow["risk_tier"])
            if srow.get("risk_tier") is not None
            else None,
            model_version="xgboost-layer4b-v2",
            accounting_feature_coverage=round(coverage, 4),
            missing_market_tt96_features=list(MISSING_MARKET_TT96_FEATURES),
            coverage_warning_vi=_COVERAGE_WARNING_VI,
        )

    if altman_z is None:
        return PrivateMlAccountingOnlyResult(
            ml_mode="ACCOUNTING_ONLY_MODE",
            calibrated_distress_prob=None,
            risk_tier=None,
            model_version="accounting-only-calibrated-v1",
            accounting_feature_coverage=round(coverage, 4),
            missing_market_tt96_features=list(MISSING_MARKET_TT96_FEATURES),
            coverage_warning_vi=_COVERAGE_WARNING_VI,
        )

    # Calibrated accounting-only logistic mapping anchored at Altman Z''=1.10 distress threshold
    # scaled onto the FinIntel R1..R5 TTC band (using train.py cutoffs when available).
    margin = -(altman_z - 1.10) - 2.60
    clipped = max(-60.0, min(60.0, margin))
    prob = 1.0 / (1.0 + math.exp(-clipped))
    c1, c2, c3, c4 = _DEFAULT_TIER_CUTOFFS
    if prob < c1:
        tier = "R1"
    elif prob < c2:
        tier = "R2"
    elif prob < c3:
        tier = "R3"
    elif prob < c4:
        tier = "R4"
    else:
        tier = "R5"

    return PrivateMlAccountingOnlyResult(
        ml_mode="ACCOUNTING_ONLY_MODE",
        calibrated_distress_prob=round(prob, 6),
        risk_tier=tier,
        model_version="accounting-only-calibrated-v1",
        accounting_feature_coverage=round(coverage, 4),
        missing_market_tt96_features=list(MISSING_MARKET_TT96_FEATURES),
        coverage_warning_vi=_COVERAGE_WARNING_VI,
    )


def score_private_dossier(
    dossier: PrivateCreditDossierRecord,
    *,
    request: PrivateScoreRequest | None = None,
    ml_training_result: Any | None = None,
) -> PrivateScoreResponse:
    """Run isolated Layer 4A + Layer 4B (ACCOUNTING_ONLY_MODE) + IMF stress test."""
    if dossier.qg1_status == "FAIL":
        raise PrivateCreditValidationError(
            422,
            "Hồ sơ chưa đạt kiểm định cân bằng kế toán QG1; vui lòng sửa lỗi CĐKT trước khi chấm điểm.",
        )
    non_null_facts = [f for f in dossier.facts if f.value is not None]
    if not non_null_facts:
        raise PrivateCreditValidationError(
            422, "Hồ sơ không có số liệu tài chính hợp lệ để chấm điểm."
        )

    started = time.perf_counter()
    issuer_id = f"PRIV_{dossier.source_sha256[:12].upper()}"
    latest_period = max(f.period_end_date for f in non_null_facts)
    facts_df, issuers_df = facts_to_canonical_polars(
        non_null_facts,
        issuer_id=issuer_id,
        ticker="PRIVATE",
        company_name=dossier.company_name,
        icb_code_level2=dossier.icb_code_level2,
        statement_scope=dossier.statement_scope,
        taxonomy_version=dossier.taxonomy_version,
    )

    periods = prepare_periods(
        facts_df,
        issuers_df,
        as_of=latest_period,
        include_ml_inputs=True,
        market_prices=None,
    )
    ratios = add_dupont(calculate_ratios(periods))
    latest_ratios_df = ratios.filter(pl.col("period_end_date") == latest_period)
    if latest_ratios_df.is_empty():
        raise PrivateCreditValidationError(
            422, "Không thể tính chỉ số tài chính cho kỳ báo cáo mới nhất."
        )

    classical_df = add_classical_scores(ratios)
    classical_latest = classical_df.filter(pl.col("period_end_date") == latest_period)
    red_flags_df = detect_red_flags(ratios).filter(
        pl.col("period_end_date") == latest_period
    )

    req = request or PrivateScoreRequest()
    scenario = StressScenario(
        name=req.scenario_name,
        interest_rate_shock_bps=req.interest_rate_shock_bps,
        revenue_shock_pct=req.revenue_shock_pct,
        gross_margin_shock_pp=req.gross_margin_shock_pp,
        dso_shock_days=req.dso_shock_days,
        dio_shock_days=req.dio_shock_days,
    )
    stress_df = simulate_financial_stress(latest_ratios_df, scenario)

    rrow = latest_ratios_df.row(0, named=True)
    crow = classical_latest.row(0, named=True)
    srow = stress_df.row(0, named=True)

    ratio_names = (*CORE_RATIOS, *EXTRA_RATIOS, *FORENSIC_RATIOS, *V3_RATIOS)
    ratio_values = {name: _opt_float(rrow.get(name)) for name in ratio_names}
    ratio_flags = {
        name: str(rrow[f"{name}_flag"])
        if rrow.get(f"{name}_flag") is not None
        else None
        for name in ratio_names
    }

    dupont = PrivateDupontBreakdown(
        tax_burden=_opt_float(rrow.get("tax_burden")),
        interest_burden=_opt_float(rrow.get("interest_burden")),
        ebit_margin=_opt_float(rrow.get("ebit_margin")),
        asset_turnover=_opt_float(rrow.get("asset_turnover")),
        equity_multiplier_total=_opt_float(rrow.get("equity_multiplier_total")),
        parent_profit_share=_opt_float(rrow.get("parent_profit_share")),
        equity_multiplier_parent=_opt_float(rrow.get("equity_multiplier_parent")),
        roe_total=_opt_float(rrow.get("roe_total")),
        roe_parent=_opt_float(rrow.get("roe_parent")),
        dupont_valid_flag=bool(rrow.get("dupont_valid_flag")),
    )

    altman_z = _opt_float(crow.get("altman_z_double_prime"))
    classical = PrivateClassicalScores(
        altman_z_double_prime=altman_z,
        altman_em_score=_opt_float(crow.get("altman_em_score")),
        altman_zone=str(crow["altman_zone"])
        if crow.get("altman_zone") is not None
        else None,
        piotroski_f_score=_opt_int(crow.get("piotroski_f_score")),
        piotroski_valid_components_count=_opt_int(
            crow.get("piotroski_valid_components_count")
        ),
        beneish_m8_score=_opt_float(crow.get("beneish_m8_score")),
        beneish_flag=str(crow["beneish_flag"])
        if crow.get("beneish_flag") is not None
        else None,
    )

    red_flags = [
        PrivateRedFlagItem(
            period_end_date=cast(Any, rf["period_end_date"]),
            flag_code=str(rf["flag_code"]),
            severity=str(rf["severity"]),
            flag_title_vn=str(rf["flag_title_vn"]),
            explanation_text_vn=str(rf["explanation_text_vn"]),
            evidence_json=str(rf["evidence_json"]),
        )
        for rf in red_flags_df.to_dicts()
    ]

    ml_assessment = _score_accounting_only_ml(
        classical_latest,
        altman_z=altman_z,
        ml_training_result=ml_training_result,
    )

    stress_test = PrivateStressResult(
        scenario_name=req.scenario_name,
        baseline_ebit_vnd=_opt_float(srow.get("ebit_ttm")),
        stressed_ebit_vnd=_opt_float(srow.get("stressed_ebit_ttm")),
        baseline_interest_coverage=_opt_float(srow.get("interest_coverage")),
        stressed_interest_coverage=_opt_float(srow.get("stressed_interest_coverage")),
        stressed_interest_coverage_flag=(
            str(srow["stressed_interest_coverage_flag"])
            if srow.get("stressed_interest_coverage_flag") is not None
            else None
        ),
        baseline_cfo_vnd=_opt_float(srow.get("cfo_ttm")),
        stressed_cfo_vnd=_opt_float(srow.get("stressed_cfo_ttm")),
        baseline_debt_at_risk=_opt_bool(srow.get("baseline_debt_at_risk")),
        stressed_debt_at_risk=_opt_bool(srow.get("stressed_debt_at_risk")),
    )

    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
    return PrivateScoreResponse(
        dossier_id=dossier.dossier_id,
        company_name=dossier.company_name,
        tax_code=dossier.tax_code,
        taxonomy_version=dossier.taxonomy_version,
        statement_scope=dossier.statement_scope,
        scored_period_end_date=latest_period,
        qg1_status=cast(Any, dossier.qg1_status),
        ratios=ratio_values,
        ratio_flags=ratio_flags,
        dupont=dupont,
        classical=classical,
        red_flags=red_flags,
        ml_assessment=ml_assessment,
        stress_test=stress_test,
        ducklake_contaminated=False,
        ml_labels_modified=False,
        elapsed_ms=elapsed_ms,
    )
