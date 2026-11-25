"""Assemble Layer 4B ML scores, explanations, evaluation report and run manifest.

Verified Sources:
    docs/layers/LAYER_4_ANALYTICS_AND_ML.md, sections 2.1 (G1-G5), 5.3.4 and 6.4.
    Shumway, T. (2001). "Forecasting Bankruptcy More Accurately: A Simple Hazard Model."
        The Journal of Business, Vol. 74, No. 1, pp. 101-124.
    Platt, J. C. (1999) & Niculescu-Mizil, A., & Caruana, R. (2005) (ICML 2005, pp. 625-632).
    Lundberg, S. M. et al. (2020). Nature Machine Intelligence, Vol. 2, pp. 56-67.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal

import polars as pl

from finintel.analytics.comparisons import benchmark_ratios
from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.ratios import calculate_ratios
from finintel.exceptions import ContractViolationError
from finintel.ml.classical_scores import add_classical_scores
from finintel.ml.supervised.explain import explain_distress
from finintel.ml.supervised.target import resolve_target_mode
from finintel.ml.supervised.temporal_split import (
    attach_pit_sector_z_features,
    build_pit_ml_splits,
    resolve_peer_standardization_mode,
)
from finintel.ml.supervised.train import (
    BASE_FEATURES,
    MODEL_VERSION,
    SECTOR_ADJUSTED_FEATURES,
    TrainingResult,
    build_evaluation_report,
    compute_cross_sectional_risk_percentile,
    compute_taxonomy_drift_psi,
    evaluate_risk_tier_reliability,
    export_calibrator_spec,
    export_risk_tier_cutoffs_spec,
    run_expanding_walk_forward_evaluation,
    score_distress_model,
    train_distress_model,
)
from finintel.ml.unsupervised import FinancialArchetypes, InsufficientModelData
from finintel.storage.schemas import (
    ML_SCORES_KEY,
    ML_SCORES_SCHEMA,
    ML_SCORES_TABLE,
    MODEL_CARD_META_SCHEMA,
    MODEL_CARD_META_TABLE,
    validate_analytics_table,
)

_ML_PACKAGES = ("polars", "numpy", "scipy", "scikit-learn", "xgboost", "shap")
MODEL_CARD_SCHEMA_VERSION = 1
DEFAULT_WALK_FORWARD_YEARS: tuple[int, ...] = (2021, 2022, 2023, 2024, 2025)
DEFAULT_CASE_STUDY_TICKERS: tuple[str, ...] = (
    "HBC",
    "POM",
    "HNG",
    "DLG",
    "SMC",
    "QBS",
    "PVX",
    "PVL",
    "HVN",
    "NVL",
    "SII",
    "SJF",
)


@dataclass(frozen=True)
class MLArtifacts:
    """Validated Layer 4B Parquet table, OOS evaluation report and provenance manifest."""

    scores_table: pl.DataFrame
    evaluation_report: dict[str, Any]
    run_manifest: dict[str, Any]
    model_card: dict[str, Any] | None = None
    model_card_meta: pl.DataFrame | None = None


def _finite_or_none(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        fval = float(value)
        return fval if math.isfinite(fval) else None
    return None


def _iso_or_none(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _compute_training_artifact_sha256(training: TrainingResult) -> str:
    """Compute deterministic SHA-256 fingerprint over model weights, calibrator and cutoffs."""
    import os
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        training.classifier.save_model(tmp_path)
        with open(tmp_path, "rb") as fh:
            xgb_bytes = fh.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    meta_payload = json.dumps(
        {
            "model_version": MODEL_VERSION,
            "features": list(training.features),
            "calibrator": export_calibrator_spec(training),
            "risk_tier_cutoffs": export_risk_tier_cutoffs_spec(training),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(xgb_bytes)
    digest.update(b"\x00")
    digest.update(meta_payload)
    return digest.hexdigest()


def _parse_top_3_shap_drivers(raw_json: object) -> list[dict[str, Any]]:
    if not isinstance(raw_json, str) or not raw_json.strip():
        return []
    try:
        parsed = json.loads(raw_json)
    except ValueError:
        return []
    if not isinstance(parsed, Mapping):
        return []
    raw_inc = parsed.get("top_risk_increasing")
    if not isinstance(raw_inc, list):
        return []
    drivers: list[dict[str, Any]] = []
    for entry in raw_inc[:3]:
        if not isinstance(entry, Mapping):
            continue
        feature = entry.get("feature")
        shap_delta = _finite_or_none(entry.get("shap_delta"))
        if not isinstance(feature, str) or not feature or shap_delta is None:
            continue
        raw_vn = entry.get("display_name_vn")
        display_name_vn = (
            str(raw_vn) if isinstance(raw_vn, str) and raw_vn.strip() else feature
        )
        drivers.append(
            {
                "feature": feature,
                "display_name_vn": display_name_vn,
                "value": _finite_or_none(entry.get("value")),
                "peer_median": _finite_or_none(entry.get("peer_median")),
                "shap_delta": shap_delta,
            }
        )
    return drivers


def build_historical_case_studies(
    scores_table: pl.DataFrame,
    issuers: pl.DataFrame,
    *,
    regulatory_events: pl.DataFrame | None = None,
    availability_dates: pl.DataFrame | None = None,
    tickers: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Build historical early-warning case studies from real scored quarters & regulatory ledger."""
    if scores_table.is_empty() or issuers.is_empty():
        return []

    scored_q = scores_table.filter(
        (pl.col("period_type") == "QUARTER")
        & pl.col("calibrated_distress_prob").is_not_null()
        & pl.col("calibrated_distress_prob").is_finite()
    )
    if "model_role" in scored_q.columns:
        scored_q = scored_q.filter(
            pl.col("model_role").cast(pl.String).str.starts_with("OUT_OF_SAMPLE")
        )
    if scored_q.is_empty():
        return []

    if (
        availability_dates is not None
        and not availability_dates.is_empty()
        and "_period_available_at" in availability_dates.columns
    ):
        avail_cols = [c for c in ML_SCORES_KEY if c in availability_dates.columns]
        extra_avail = [
            c
            for c in ("_period_available_at", "fiscal_year", "fiscal_quarter")
            if c in availability_dates.columns
        ]
        avail_lookup = availability_dates.select(*avail_cols, *extra_avail).unique(
            subset=avail_cols
        )
        scored_q = scored_q.join(avail_lookup, on=avail_cols, how="left")

    scored_issuers = set(scored_q["issuer_id"].drop_nulls().cast(pl.String).to_list())
    issuer_meta: dict[str, dict[str, Any]] = {}
    ticker_to_issuer: dict[str, str] = {}
    for row in issuers.to_dicts():
        iid = str(row.get("issuer_id") or "")
        tkr = str(row.get("ticker") or "").strip().upper()
        if not iid:
            continue
        issuer_meta[iid] = row
        if tkr:
            ticker_to_issuer[tkr] = iid

    seed_tickers = tuple(
        t.strip().upper() for t in (tickers or DEFAULT_CASE_STUDY_TICKERS) if t.strip()
    )
    ordered_iids: list[str] = []
    seen_iids: set[str] = set()
    for tkr in seed_tickers:
        candidate_iid = ticker_to_issuer.get(tkr) or (
            tkr if tkr in scored_issuers else None
        )
        if (
            candidate_iid is not None
            and candidate_iid in scored_issuers
            and candidate_iid not in seen_iids
        ):
            ordered_iids.append(candidate_iid)
            seen_iids.add(candidate_iid)

    reg_by_issuer: dict[str, list[dict[str, Any]]] = {}
    if regulatory_events is not None and not regulatory_events.is_empty():
        for ev in regulatory_events.sort(["issuer_id", "event_date"]).to_dicts():
            iid = str(ev.get("issuer_id") or "")
            if iid:
                reg_by_issuer.setdefault(iid, []).append(ev)
        if len(ordered_iids) < 8:
            extra_iids = sorted(
                iid
                for iid in reg_by_issuer
                if iid in scored_issuers and iid not in seen_iids
            )
            for iid in extra_iids:
                ordered_iids.append(iid)
                seen_iids.add(iid)
                if len(ordered_iids) >= 12:
                    break

    if not ordered_iids:
        return []

    tier_rank = {"R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5}
    case_studies: list[dict[str, Any]] = []
    for iid in ordered_iids:
        imeta = issuer_meta.get(iid, {})
        ticker = str(imeta.get("ticker") or iid).strip().upper()
        events = reg_by_issuer.get(iid, [])

        warning_events = [
            ev
            for ev in events
            if str(ev.get("event_type") or "")
            in ("CONTROL", "TRADING_RESTRICTION", "ADVERSE_OR_DISCLAIMER_AUDIT")
        ]
        first_warning = (
            warning_events[0] if warning_events else (events[0] if events else None)
        )
        first_warning_date: date | None = (
            first_warning.get("event_date")
            if isinstance(first_warning, Mapping)
            and isinstance(first_warning.get("event_date"), date)
            else None
        )
        first_warning_type = (
            str(first_warning.get("event_type"))
            if isinstance(first_warning, Mapping)
            and first_warning.get("event_type") is not None
            else None
        )

        delist_events = [
            ev
            for ev in events
            if str(ev.get("event_type") or "") in ("FORCED_DELISTING", "SUSPENSION")
        ]
        first_delist = delist_events[0] if delist_events else None
        delist_date: date | None = (
            first_delist.get("event_date")
            if isinstance(first_delist, Mapping)
            and isinstance(first_delist.get("event_date"), date)
            else (
                imeta.get("delisted_date")
                if isinstance(imeta.get("delisted_date"), date)
                else None
            )
        )
        exchange_delisted = next(
            (
                str(ev.get("exchange_delisted_from"))
                for ev in delist_events
                if ev.get("exchange_delisted_from") is not None
            ),
            None,
        )

        sub = scored_q.filter(pl.col("issuer_id") == iid).sort("period_end_date")
        if sub.is_empty():
            continue
        rows = sub.to_dicts()
        r4_r5_rows = [r for r in rows if str(r.get("risk_tier") or "") in ("R4", "R5")]

        anchor_date = first_warning_date or delist_date
        chosen_r4_r5: dict[str, Any] | None = None
        if r4_r5_rows:
            if anchor_date is not None:
                prior_r4_r5: list[dict[str, Any]] = []
                for r in r4_r5_rows:
                    cand_dt = r.get("_period_available_at") or r.get("period_end_date")
                    if isinstance(cand_dt, date) and cand_dt <= anchor_date:
                        prior_r4_r5.append(r)
                chosen_r4_r5 = prior_r4_r5[0] if prior_r4_r5 else r4_r5_rows[0]
            else:
                chosen_r4_r5 = r4_r5_rows[0]

        peak_row = max(
            rows,
            key=lambda r: (
                tier_rank.get(str(r.get("risk_tier") or ""), 0),
                float(r.get("calibrated_distress_prob") or 0.0),
            ),
        )

        first_pend = (
            chosen_r4_r5.get("period_end_date")
            if chosen_r4_r5 is not None
            and isinstance(chosen_r4_r5.get("period_end_date"), date)
            else None
        )
        first_avail = (
            chosen_r4_r5.get("_period_available_at")
            if chosen_r4_r5 is not None
            and isinstance(chosen_r4_r5.get("_period_available_at"), date)
            else first_pend
        )
        first_fy = (
            int(chosen_r4_r5["fiscal_year"])
            if chosen_r4_r5 is not None and chosen_r4_r5.get("fiscal_year") is not None
            else (first_pend.year if first_pend is not None else None)
        )
        first_fq = (
            int(chosen_r4_r5["fiscal_quarter"])
            if chosen_r4_r5 is not None
            and chosen_r4_r5.get("fiscal_quarter") is not None
            else (((first_pend.month - 1) // 3) + 1 if first_pend is not None else None)
        )

        lead_days: int | None = None
        lead_quarters: int | None = None
        if anchor_date is not None and first_avail is not None:
            lead_days = int((anchor_date - first_avail).days)
            lead_quarters = int(round(lead_days / 91.25))

        driver_source = chosen_r4_r5 if chosen_r4_r5 is not None else peak_row
        top3 = _parse_top_3_shap_drivers(driver_source.get("shap_top_drivers_json"))

        case_studies.append(
            {
                "issuer_id": iid,
                "ticker": ticker,
                "company_name_vn": imeta.get("company_name_vn"),
                "exchange": imeta.get("exchange"),
                "first_warning_or_control_date": _iso_or_none(first_warning_date),
                "first_warning_event_type": first_warning_type,
                "forced_delisting_or_suspension_date": _iso_or_none(delist_date),
                "exchange_delisted_from": exchange_delisted,
                "first_r4_r5_period_end_date": _iso_or_none(first_pend),
                "first_r4_r5_fiscal_year": first_fy,
                "first_r4_r5_fiscal_quarter": first_fq,
                "first_r4_r5_available_at": _iso_or_none(first_avail),
                "first_r4_r5_risk_tier": (
                    str(chosen_r4_r5.get("risk_tier"))
                    if chosen_r4_r5 is not None and chosen_r4_r5.get("risk_tier")
                    else None
                ),
                "first_r4_r5_pd": (
                    _finite_or_none(chosen_r4_r5.get("calibrated_distress_prob"))
                    if chosen_r4_r5 is not None
                    else None
                ),
                "first_r4_r5_market_percentile": (
                    _finite_or_none(chosen_r4_r5.get("risk_market_percentile"))
                    if chosen_r4_r5 is not None
                    else None
                ),
                "first_r4_r5_lift": (
                    _finite_or_none(chosen_r4_r5.get("relative_risk_lift"))
                    if chosen_r4_r5 is not None
                    else None
                ),
                "early_warning_lead_quarters": lead_quarters,
                "early_warning_lead_days": lead_days,
                "peak_risk_tier": (
                    str(peak_row.get("risk_tier"))
                    if peak_row.get("risk_tier")
                    else None
                ),
                "peak_pd": _finite_or_none(peak_row.get("calibrated_distress_prob")),
                "peak_period_end_date": _iso_or_none(peak_row.get("period_end_date")),
                "top_3_shap_drivers": top3,
            }
        )
    return case_studies


def _normalize_metrics_dict(raw: Mapping[str, Any] | None) -> dict[str, float | None]:
    keys = (
        "roc_auc",
        "pr_auc",
        "brier_score",
        "brier_skill_score",
        "f2_score",
        "recall_at_top_5_percent",
        "recall_at_top_10_percent",
    )
    src = raw or {}
    return {k: _finite_or_none(src.get(k)) for k in keys}


def _format_walk_forward_for_card(wf_raw: Mapping[str, Any]) -> dict[str, Any]:
    model_names = ("Baseline_1_AltmanZ", "Baseline_2_Logit", "XGBoost")
    per_year_raw = wf_raw.get("per_year", {})
    per_year_list: list[dict[str, Any]] = []
    for yr_str in sorted(per_year_raw, key=lambda item: int(item)):
        ydata = per_year_raw[yr_str]
        test_block = ydata.get("test", {})
        unseen_block = ydata.get("true_unseen_issuers", {})
        per_year_list.append(
            {
                "year": int(yr_str),
                "train_rows": int(ydata.get("train_rows") or 0),
                "train_positive_rows": int(ydata.get("train_positive_rows") or 0),
                "train_unique_positive_issuers": int(
                    ydata.get("train_unique_positive_issuers") or 0
                ),
                "train_prevalence": _finite_or_none(ydata.get("train_prevalence")),
                "test_rows": int(test_block.get("common_sample_count") or 0),
                "test_positive_rows": int(test_block.get("positive_count") or 0),
                "test_unique_positive_issuers": int(
                    test_block.get("unique_positive_issuers") or 0
                ),
                "unseen_rows": int(unseen_block.get("common_sample_count") or 0),
                "unseen_positive_rows": int(unseen_block.get("positive_count") or 0),
                "unseen_unique_positive_issuers": int(
                    unseen_block.get("unique_positive_issuers") or 0
                ),
                "models": {
                    m: _normalize_metrics_dict(test_block.get("models", {}).get(m))
                    for m in model_names
                },
                "true_unseen_models": {
                    m: _normalize_metrics_dict(unseen_block.get("models", {}).get(m))
                    for m in model_names
                },
            }
        )

    def _build_summary(summary_key: str, boot_key: str) -> list[dict[str, Any]]:
        yr_sum = wf_raw.get(summary_key, {})
        boot_sum = wf_raw.get(boot_key, {})
        out: list[dict[str, Any]] = []
        for m in model_names:
            my = yr_sum.get(m, {})
            mb = boot_sum.get(m, {})
            ci_raw = mb.get("ci95", {})
            ci_clean: dict[str, list[float] | None] = {}
            for ck in (
                "roc_auc",
                "pr_auc",
                "brier_score",
                "brier_skill_score",
                "recall_at_top_5_percent",
                "recall_at_top_10_percent",
            ):
                pair = ci_raw.get(ck)
                if (
                    isinstance(pair, (list, tuple))
                    and len(pair) == 2
                    and _finite_or_none(pair[0]) is not None
                    and _finite_or_none(pair[1]) is not None
                ):
                    ci_clean[ck] = [float(pair[0]), float(pair[1])]
                else:
                    ci_clean[ck] = None
            out.append(
                {
                    "model": m,
                    "yearly_roc_auc_mean": _finite_or_none(
                        my.get("roc_auc", {}).get("mean")
                    ),
                    "yearly_roc_auc_std": _finite_or_none(
                        my.get("roc_auc", {}).get("std")
                    ),
                    "yearly_pr_auc_mean": _finite_or_none(
                        my.get("pr_auc", {}).get("mean")
                    ),
                    "yearly_pr_auc_std": _finite_or_none(
                        my.get("pr_auc", {}).get("std")
                    ),
                    "yearly_brier_mean": _finite_or_none(
                        my.get("brier_score", {}).get("mean")
                    ),
                    "yearly_brier_std": _finite_or_none(
                        my.get("brier_score", {}).get("std")
                    ),
                    "yearly_bss_mean": _finite_or_none(
                        my.get("brier_skill_score", {}).get("mean")
                    ),
                    "yearly_bss_std": _finite_or_none(
                        my.get("brier_skill_score", {}).get("std")
                    ),
                    "yearly_recall_top5_mean": _finite_or_none(
                        my.get("recall_at_top_5_percent", {}).get("mean")
                    ),
                    "yearly_recall_top10_mean": _finite_or_none(
                        my.get("recall_at_top_10_percent", {}).get("mean")
                    ),
                    "pooled_point": _normalize_metrics_dict(mb.get("point")),
                    "pooled_ci95": ci_clean,
                    "n_bootstrap_valid": int(mb.get("n_bootstrap_valid") or 0),
                    "n_clusters": int(mb.get("n_clusters") or 0),
                }
            )
        return out

    return {
        "test_years": [
            int(y)
            for y in wf_raw.get("evaluated_years") or wf_raw.get("test_years") or []
        ],
        "skipped_years": [int(y) for y in wf_raw.get("skipped_years") or []],
        "pooled_reference_train_prevalence": _finite_or_none(
            wf_raw.get("pooled_reference_train_prevalence")
        ),
        "per_year": per_year_list,
        "summary_all_oos": _build_summary(
            "yearly_summary_all_oos", "pooled_oos_cluster_bootstrap"
        ),
        "summary_true_unseen": _build_summary(
            "yearly_summary_true_unseen", "pooled_true_unseen_cluster_bootstrap"
        ),
        "pooled_oos_risk_tier_reliability": wf_raw.get(
            "pooled_oos_risk_tier_reliability"
        ),
    }


def _format_psi_for_card(psi_raw: Mapping[str, Any]) -> dict[str, Any]:
    raw_feat_map = psi_raw.get("feature_drift", {})
    feat_list = [
        {
            "feature": fname,
            "psi": _finite_or_none(fdata.get("psi")),
            "ref_missing_rate": _finite_or_none(fdata.get("ref_missing_rate")),
            "tgt_missing_rate": _finite_or_none(fdata.get("tgt_missing_rate")),
            "missing_rate_delta": _finite_or_none(fdata.get("missing_rate_delta")),
        }
        for fname, fdata in sorted(raw_feat_map.items())
        if isinstance(fdata, Mapping)
    ]
    raw_score_drift = psi_raw.get("score_drift")
    score_drift: dict[str, Any] | None = None
    if isinstance(raw_score_drift, Mapping) and raw_score_drift:
        score_drift = {
            "score_psi": _finite_or_none(raw_score_drift.get("score_psi")),
            "ref_mean_pd": _finite_or_none(raw_score_drift.get("ref_mean_pd")),
            "tgt_mean_pd": _finite_or_none(raw_score_drift.get("tgt_mean_pd")),
            "ref_r4_r5_share": _finite_or_none(raw_score_drift.get("ref_r4_r5_share")),
            "tgt_r4_r5_share": _finite_or_none(raw_score_drift.get("tgt_r4_r5_share")),
            "r4_r5_share_delta": _finite_or_none(
                raw_score_drift.get("r4_r5_share_delta")
            ),
        }
    return {
        "reference_taxonomy": "VAS_TT200_2014",
        "target_taxonomy": "VAS_TT99_2025",
        "reference_rows": int(psi_raw.get("reference_rows") or 0),
        "target_rows": int(psi_raw.get("target_rows") or 0),
        "mean_feature_psi": _finite_or_none(psi_raw.get("mean_feature_psi")),
        "max_feature_psi": _finite_or_none(psi_raw.get("max_feature_psi")),
        "features_psi_gt_0_25": list(psi_raw.get("features_psi_gt_0_25") or []),
        "mean_ref_missing_rate": _finite_or_none(psi_raw.get("mean_ref_missing_rate")),
        "mean_tgt_missing_rate": _finite_or_none(psi_raw.get("mean_tgt_missing_rate")),
        "score_drift": score_drift,
        "feature_drift": feat_list,
    }


def build_model_card_payload(
    *,
    training: TrainingResult,
    evaluation_report: Mapping[str, Any],
    facts: pl.DataFrame,
    issuers: pl.DataFrame,
    eligible_scored_quarters: pl.DataFrame,
    scores_table: pl.DataFrame,
    as_of: date,
    run_id: str,
    feature_set: Literal["sector_adjusted", "base"],
    selected_features: Sequence[str],
    statement_scope: str,
    taxonomy_version: str,
    target_mode: str,
    peer_standardization_mode: str,
    require_exact_filing: bool,
    regulatory_events: pl.DataFrame | None = None,
    min_unique_positive_issuers: int = 3,
    precomputed_as_of_scored: pl.DataFrame | None = None,
    artifact_sha256: str | None = None,
    walk_forward_n_bootstrap: int = 1000,
) -> dict[str, Any]:
    """Assemble the complete real-data ML-08 Model Card payload (GATE-ML-6)."""
    resolved_sha = artifact_sha256 or _compute_training_artifact_sha256(training)
    wf_raw = run_expanding_walk_forward_evaluation(
        facts,
        issuers,
        as_of=as_of,
        test_years=DEFAULT_WALK_FORWARD_YEARS,
        features=tuple(selected_features),
        require_exact_filing=require_exact_filing,
        statement_scope=statement_scope,
        taxonomy_version=taxonomy_version,
        target_mode=target_mode,
        regulatory_events=regulatory_events,
        min_unique_positive_issuers=min_unique_positive_issuers,
        n_bootstrap=walk_forward_n_bootstrap,
        precomputed_as_of_scored=precomputed_as_of_scored,
        strict_years=False,
    )
    walk_forward = _format_walk_forward_for_card(wf_raw)

    pooled_rel = walk_forward.get("pooled_oos_risk_tier_reliability")
    test_rel = evaluation_report.get("test", {}).get("risk_tier_reliability")
    if isinstance(pooled_rel, Mapping) and any(
        int(row.get("count") or 0) > 0 for row in pooled_rel.get("tiers", [])
    ):
        risk_tier_reliability = {
            "evaluation_source": "WALK_FORWARD_POOLED_OOS",
            **dict(pooled_rel),
        }
    elif isinstance(test_rel, Mapping) and any(
        int(row.get("count") or 0) > 0 for row in test_rel.get("tiers", [])
    ):
        risk_tier_reliability = {
            "evaluation_source": "OUT_OF_SAMPLE_TEST",
            **dict(test_rel),
        }
    else:
        oof_rel = evaluate_risk_tier_reliability(
            training.oof_targets,
            training.oof_calibrated_probs,
            training.risk_tier_cutoffs,
        )
        risk_tier_reliability = {
            "evaluation_source": "INNER_OOF_CALIBRATION",
            **oof_rel,
        }

    ref_tt200 = eligible_scored_quarters.filter(
        pl.col("taxonomy_version") == "VAS_TT200_2014"
    )
    ref_recent = ref_tt200.filter(pl.col("fiscal_year") >= 2024)
    if ref_recent.height >= 10:
        ref_tt200 = ref_recent
    tgt_tt99 = eligible_scored_quarters.filter(
        pl.col("taxonomy_version") == "VAS_TT99_2025"
    )
    psi_raw = compute_taxonomy_drift_psi(
        ref_tt200,
        tgt_tt99,
        features=tuple(selected_features),
        result=training,
    )
    taxonomy_drift_psi = _format_psi_for_card(psi_raw)

    case_studies = build_historical_case_studies(
        scores_table,
        issuers,
        regulatory_events=regulatory_events,
        availability_dates=eligible_scored_quarters,
    )

    limitations = [
        "Phạm vi áp dụng: chỉ chấm điểm doanh nghiệp phi tài chính niêm yết/đăng ký giao dịch (CORPORATE); loại trừ Ngân hàng, Chứng khoán và Bảo hiểm.",
        f"Chế độ chuẩn hóa đặc trưng ngành: {peer_standardization_mode}.",
        "Dữ liệu BCTC lịch sử từ FHSC hiện lưu ở revision_no = 1 (chưa có chuỗi tái lập kiểm toán trước năm 2026).",
    ]

    return {
        "schema_version": MODEL_CARD_SCHEMA_VERSION,
        "run_id": run_id,
        "model_version": MODEL_VERSION,
        "model_role": "SCREENING_INDICATOR",
        "target_mode": target_mode,
        "statement_scope": statement_scope,
        "feature_set": feature_set,
        "feature_count": len(selected_features),
        "selected_features": list(selected_features),
        "peer_standardization_mode": peer_standardization_mode,
        "trained_as_of": as_of.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(),
        "artifact_sha256": resolved_sha,
        "platt_slope": float(training.platt_slope),
        "platt_intercept": float(training.platt_intercept),
        "train_prevalence": float(training.train_prevalence),
        "risk_tier_cutoffs": {
            "R2_min_pd": float(training.risk_tier_cutoffs[0]),
            "R3_min_pd": float(training.risk_tier_cutoffs[1]),
            "R4_min_pd": float(training.risk_tier_cutoffs[2]),
            "R5_min_pd": float(training.risk_tier_cutoffs[3]),
        },
        "f2_thresholds": {k: float(v) for k, v in training.f2_thresholds.items()},
        "walk_forward": walk_forward,
        "risk_tier_reliability": risk_tier_reliability,
        "taxonomy_drift_psi": taxonomy_drift_psi,
        "case_studies": case_studies,
        "limitations": limitations,
    }


def build_model_card_meta_table(
    model_card: Mapping[str, Any],
    *,
    model_card_id: str = "active_model_card",
) -> pl.DataFrame:
    """Serialize Model Card payload into a 1-row DuckLake-validated DataFrame (H-04)."""
    trained_raw = model_card.get("trained_as_of")
    trained_date = (
        date.fromisoformat(str(trained_raw))
        if isinstance(trained_raw, str)
        else trained_raw
        if isinstance(trained_raw, date)
        else datetime.now(UTC).date()
    )
    frame = pl.DataFrame(
        [
            {
                "model_card_id": model_card_id,
                "schema_version": int(
                    model_card.get("schema_version") or MODEL_CARD_SCHEMA_VERSION
                ),
                "run_id": str(model_card["run_id"]),
                "model_version": str(model_card["model_version"]),
                "feature_set": str(model_card["feature_set"]),
                "feature_count": int(model_card["feature_count"]),
                "target_mode": str(model_card["target_mode"]),
                "statement_scope": str(model_card["statement_scope"]),
                "peer_standardization_mode": str(
                    model_card["peer_standardization_mode"]
                ),
                "trained_as_of": trained_date,
                "artifact_sha256": str(model_card["artifact_sha256"]),
                "model_card_json": json.dumps(
                    model_card, ensure_ascii=False, sort_keys=True, default=str
                ),
                "generated_at": str(model_card["generated_at"]),
            }
        ],
        schema=MODEL_CARD_META_SCHEMA,
    )
    validate_analytics_table(MODEL_CARD_META_TABLE, frame)
    return frame


def _installed_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for pkg in _ML_PACKAGES:
        try:
            versions[pkg] = version(pkg)
        except PackageNotFoundError:
            versions[pkg] = None
    return versions


def score_and_explain_with_pit_roles(
    *,
    training: TrainingResult,
    eligible: pl.DataFrame,
    facts: pl.DataFrame,
    issuers: pl.DataFrame,
    classical_as_of: pl.DataFrame,
    peer_medians: pl.DataFrame,
    as_of: date,
    test_start: date,
    selected_features: Sequence[str],
    statement_scope: str,
    taxonomy_version: str,
    target_mode: str,
    peer_standardization_mode: Literal["PIT_ICB", "PIT_REGIME_SYMMETRIC"],
    require_exact_filing: bool,
    regulatory_events: pl.DataFrame | None,
    min_unique_positive_issuers: int,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Score eligible quarters with honest PIT ``model_role`` (OUT_OF_SAMPLE_* vs IN_SAMPLE_TRAIN)."""
    if eligible.is_empty():
        return (
            score_distress_model(training, eligible, model_role="OUT_OF_SAMPLE_LIVE"),
            explain_distress(
                training,
                eligible,
                peer_medians=peer_medians,
                peer_standardization_mode=peer_standardization_mode,
            ),
        )

    pend = pl.col("period_end_date").cast(pl.Date)
    sup_parts: list[pl.DataFrame] = []
    shap_parts: list[pl.DataFrame] = []

    live_slice = eligible.filter(pend >= test_start)
    if not live_slice.is_empty():
        sup_parts.append(
            score_distress_model(training, live_slice, model_role="OUT_OF_SAMPLE_LIVE")
        )
        shap_parts.append(
            explain_distress(
                training,
                live_slice,
                peer_medians=peer_medians,
                peer_standardization_mode=peer_standardization_mode,
            )
        )

    in_sample_slices: list[pl.DataFrame] = []
    pre_wf_slice = eligible.filter(pend < date(2021, 1, 1))
    if not pre_wf_slice.is_empty():
        in_sample_slices.append(pre_wf_slice)

    wf_start_year = 2021
    wf_end_year = (
        test_start.year
        if test_start > date(test_start.year, 1, 1)
        else test_start.year - 1
    )
    for yr in range(wf_start_year, wf_end_year + 1):
        yr_start = date(yr, 1, 1)
        yr_end = min(date(yr + 1, 1, 1), test_start)
        if yr_start >= yr_end:
            continue
        yr_slice = eligible.filter((pend >= yr_start) & (pend < yr_end))
        if yr_slice.is_empty():
            continue
        wf_model: TrainingResult | None = None
        try:
            wf_split = build_pit_ml_splits(
                facts,
                issuers,
                validation_start=None,
                test_start=yr_start,
                test_end=yr_end,
                as_of=as_of,
                require_exact_filing=require_exact_filing,
                statement_scope=statement_scope,
                taxonomy_version=taxonomy_version,
                target_mode=target_mode,
                regulatory_events=regulatory_events,
                precomputed_as_of_scored=classical_as_of,
            )
            if not wf_split["train"].is_empty():
                wf_model = train_distress_model(
                    wf_split["train"],
                    None,
                    features=tuple(selected_features),
                    min_unique_positive_issuers=min_unique_positive_issuers,
                )
        except (InsufficientModelData, ContractViolationError):
            wf_model = None

        if wf_model is not None:
            sup_parts.append(
                score_distress_model(
                    wf_model, yr_slice, model_role="OUT_OF_SAMPLE_WALK_FORWARD"
                )
            )
            shap_parts.append(
                explain_distress(
                    wf_model,
                    yr_slice,
                    peer_medians=peer_medians,
                    peer_standardization_mode=peer_standardization_mode,
                )
            )
        else:
            in_sample_slices.append(yr_slice)

    if in_sample_slices:
        in_sample_df = pl.concat(in_sample_slices, how="vertical")
        sup_parts.append(
            score_distress_model(training, in_sample_df, model_role="IN_SAMPLE_TRAIN")
        )
        shap_parts.append(
            explain_distress(
                training,
                in_sample_df,
                peer_medians=peer_medians,
                peer_standardization_mode=peer_standardization_mode,
            )
        )

    supervised_out = compute_cross_sectional_risk_percentile(
        pl.concat(sup_parts, how="vertical")
    )
    shap_out = pl.concat(shap_parts, how="vertical")
    return supervised_out, shap_out


def build_ml_artifacts(
    facts: pl.DataFrame,
    issuers: pl.DataFrame,
    *,
    as_of: date,
    validation_start: date | None = None,
    test_start: date,
    approved_target: bool = False,
    require_exact_filing: bool = True,
    statement_scope: str = "CONSOLIDATED",
    taxonomy_version: str = "VAS_TT200_2014",
    scored_taxonomies: tuple[str, ...] = ("VAS_TT200_2014", "VAS_TT99_2025"),
    feature_set: Literal["sector_adjusted", "base"] = "sector_adjusted",
    target_mode: str | None = None,
    regulatory_events: pl.DataFrame | None = None,
    min_unique_positive_issuers: int = 3,
    peer_benchmarks: pl.DataFrame | None = None,
    facts_sha256: str | None = None,
    issuers_sha256: str | None = None,
    precomputed_ratios_as_of: pl.DataFrame | None = None,
    market_prices: pl.DataFrame | None = None,
    run_id: str | None = None,
    artifact_sha256: str | None = None,
    include_model_card: bool = True,
    walk_forward_n_bootstrap: int = 1000,
) -> MLArtifacts:
    """Train, calibrate, evaluate and score Layer 4B strictly from PIT real history.

    Guardrail G2 requires ``approved_target=True`` before supervised training runs.
    Guardrail G1 requires ``EXACT_FILING`` provenance across train/validation/test
    when ``require_exact_filing=True``. Classical scores (Altman Z'', Piotroski F,
    Beneish M) are computed for both QUARTER and ANNUAL corporate records, whereas
    Unsupervised and Supervised ML models score strictly on QUARTER records across
    ``scored_taxonomies``. When ``target_mode`` is omitted, ``REGULATORY_DISTRESS``
    activates automatically whenever ``regulatory_events`` is supplied. When
    ``validation_start`` is ``None`` (B-02 2-way mode), inner Purged Temporal OOF
    inside ``train`` handles early stopping and fair 3-model Platt calibration.
    """
    if not approved_target:
        raise ContractViolationError(
            "Layer 4B supervised ML requires explicit target approval (approved_target=True)."
        )
    if not scored_taxonomies or any(
        not isinstance(item, str) or not item.strip() for item in scored_taxonomies
    ):
        raise ContractViolationError(
            "Layer 4B scored_taxonomies must be a nonempty tuple of taxonomy strings."
        )
    if feature_set not in ("sector_adjusted", "base"):
        raise ContractViolationError(
            f"Unsupported feature_set {feature_set!r}; expected 'sector_adjusted' or 'base'."
        )
    resolved_target_mode = resolve_target_mode(target_mode, regulatory_events)
    selected_features = (
        SECTOR_ADJUSTED_FEATURES if feature_set == "sector_adjusted" else BASE_FEATURES
    )

    if precomputed_ratios_as_of is not None:
        ratios_as_of = precomputed_ratios_as_of
    else:
        periods_as_of = prepare_periods(
            facts,
            issuers,
            as_of=as_of,
            include_ml_inputs=True,
            market_prices=market_prices,
        )
        ratios_as_of = calculate_ratios(periods_as_of)
    classical_as_of = add_classical_scores(ratios_as_of)
    benchmarks = (
        peer_benchmarks
        if peer_benchmarks is not None
        else benchmark_ratios(ratios_as_of)
    )

    splits = build_pit_ml_splits(
        facts,
        issuers,
        validation_start=validation_start,
        test_start=test_start,
        as_of=as_of,
        require_exact_filing=require_exact_filing,
        statement_scope=statement_scope,
        taxonomy_version=taxonomy_version,
        target_mode=resolved_target_mode,
        regulatory_events=regulatory_events,
        precomputed_as_of_scored=classical_as_of,
    )
    validation_fold = splits.get("validation")
    peer_standardization_mode = resolve_peer_standardization_mode(
        splits["train"], validation_fold
    )
    archetypes = FinancialArchetypes().fit(splits["train"])
    training = train_distress_model(
        splits["train"],
        validation_fold,
        features=selected_features,
        min_unique_positive_issuers=min_unique_positive_issuers,
    )
    evaluation_report = build_evaluation_report(
        training,
        train=splits["train"],
        validation=validation_fold,
        test=splits["test"],
    )
    evaluation_report.update(
        {
            "scored_taxonomies": list(scored_taxonomies),
            "scored_period_types": ["QUARTER"],
            "feature_set": feature_set,
            "feature_count": len(selected_features),
            "selected_features": list(selected_features),
            "peer_standardization_mode": peer_standardization_mode,
            "target_mode": resolved_target_mode,
            "split_mode": "TWO_WAY_PURGED_OOF"
            if validation_fold is None
            else "THREE_WAY_HOLD_OUT",
            "platt_slope": training.platt_slope,
            "platt_intercept": training.platt_intercept,
            "risk_tier_cutoffs": {
                "R2_min_pd": training.risk_tier_cutoffs[0],
                "R3_min_pd": training.risk_tier_cutoffs[1],
                "R4_min_pd": training.risk_tier_cutoffs[2],
                "R5_min_pd": training.risk_tier_cutoffs[3],
            },
            "f2_thresholds": dict(training.f2_thresholds),
        }
    )

    eligible_quarters = classical_as_of.filter(
        (pl.col("statement_scope") == statement_scope)
        & (pl.col("period_type") == "QUARTER")
        & pl.col("taxonomy_version").is_in(list(scored_taxonomies))
    )
    eligible_with_z = attach_pit_sector_z_features(
        eligible_quarters, mode=peer_standardization_mode
    )
    eligible = (
        eligible_with_z.filter(pl.col("_exact_filing_basis").fill_null(False))
        if require_exact_filing
        else eligible_with_z
    )

    unsupervised_out = archetypes.transform(eligible)
    supervised_out, shap_out = score_and_explain_with_pit_roles(
        training=training,
        eligible=eligible,
        facts=facts,
        issuers=issuers,
        classical_as_of=classical_as_of,
        peer_medians=benchmarks,
        as_of=as_of,
        test_start=test_start,
        selected_features=selected_features,
        statement_scope=statement_scope,
        taxonomy_version=taxonomy_version,
        target_mode=resolved_target_mode,
        peer_standardization_mode=peer_standardization_mode,
        require_exact_filing=require_exact_filing,
        regulatory_events=regulatory_events,
        min_unique_positive_issuers=min_unique_positive_issuers,
    )

    join_keys = list(ML_SCORES_KEY)
    supervised_join = supervised_out.rename({"model_role": "_supervised_model_role"})
    assembled = (
        classical_as_of.join(unsupervised_out, on=join_keys, how="left", validate="1:1")
        .join(supervised_join, on=join_keys, how="left", validate="1:1")
        .join(shap_out, on=join_keys, how="left", validate="1:1")
        .with_columns(
            pl.coalesce("_supervised_model_role", "model_role").alias("model_role")
        )
        .select(
            *(
                pl.col(name).cast(dtype, strict=True).alias(name)
                for name, dtype in ML_SCORES_SCHEMA.items()
            )
        )
        .sort(join_keys)
    )
    validate_analytics_table(ML_SCORES_TABLE, assembled)

    run_manifest: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "as_of": as_of.isoformat(),
        "validation_start": validation_start.isoformat()
        if validation_start is not None
        else None,
        "test_start": test_start.isoformat(),
        "split_mode": "TWO_WAY_PURGED_OOF"
        if validation_fold is None
        else "THREE_WAY_HOLD_OUT",
        "oof_strategy": training.oof_strategy,
        "best_n_estimators": training.best_n_estimators,
        "train_prevalence": training.train_prevalence,
        "approved_target": approved_target,
        "require_exact_filing": require_exact_filing,
        "statement_scope": statement_scope,
        "taxonomy_version": taxonomy_version,
        "scored_taxonomies": list(scored_taxonomies),
        "scored_period_types": ["QUARTER"],
        "feature_set": feature_set,
        "feature_count": len(selected_features),
        "selected_features": list(selected_features),
        "peer_standardization_mode": peer_standardization_mode,
        "target_mode": resolved_target_mode,
        "platt_slope": training.platt_slope,
        "platt_intercept": training.platt_intercept,
        "risk_tier_cutoffs": {
            "R2_min_pd": training.risk_tier_cutoffs[0],
            "R3_min_pd": training.risk_tier_cutoffs[1],
            "R4_min_pd": training.risk_tier_cutoffs[2],
            "R5_min_pd": training.risk_tier_cutoffs[3],
        },
        "f2_thresholds": dict(training.f2_thresholds),
        "random_seed": 42,
        "facts_sha256": facts_sha256,
        "issuers_sha256": issuers_sha256,
        "split_counts": {
            fold_name: {
                "rows": int(fold_df.height),
                "unique_issuers": int(fold_df["issuer_id"].n_unique())
                if "issuer_id" in fold_df.columns
                else 0,
                "positive_labels": int(
                    (fold_df.get_column("distress_target").to_numpy() == 1).sum()
                )
                if not fold_df.is_empty()
                else 0,
            }
            for fold_name, fold_df in splits.items()
        },
        "package_versions": _installed_versions(),
    }
    resolved_run_id = (
        run_id.strip()
        if isinstance(run_id, str) and run_id.strip()
        else f"ml_{as_of.strftime('%Y%m%d')}_{(facts_sha256 or 'manual')[:12]}"
    )
    model_card: dict[str, Any] | None = None
    model_card_meta: pl.DataFrame | None = None
    if include_model_card:
        model_card = build_model_card_payload(
            training=training,
            evaluation_report=evaluation_report,
            facts=facts,
            issuers=issuers,
            eligible_scored_quarters=eligible,
            scores_table=assembled,
            as_of=as_of,
            run_id=resolved_run_id,
            feature_set=feature_set,
            selected_features=selected_features,
            statement_scope=statement_scope,
            taxonomy_version=taxonomy_version,
            target_mode=resolved_target_mode,
            peer_standardization_mode=peer_standardization_mode,
            require_exact_filing=require_exact_filing,
            regulatory_events=regulatory_events,
            min_unique_positive_issuers=min_unique_positive_issuers,
            precomputed_as_of_scored=classical_as_of,
            artifact_sha256=artifact_sha256,
            walk_forward_n_bootstrap=walk_forward_n_bootstrap,
        )
        model_card_meta = build_model_card_meta_table(model_card)
    return MLArtifacts(
        scores_table=assembled,
        evaluation_report=evaluation_report,
        run_manifest=run_manifest,
        model_card=model_card,
        model_card_meta=model_card_meta,
    )
