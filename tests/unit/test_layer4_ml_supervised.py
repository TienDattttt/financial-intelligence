"""Layer 4B acceptance tests for screening models and leakage controls.

Verified Sources:
    docs/layers/LAYER_4_ANALYTICS_AND_ML.md, sections 5.3 and 7.2.
    docs/v2_specs/06_STREAM_A4_LAYER4B_ML_REMEDIATION_SECTOR_ADJ_AND_REGULATORY_TARGET.md.
"""

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from finintel.exceptions import ContractViolationError
from finintel.ml.supervised.target import build_distress_targets
from finintel.ml.supervised.temporal_split import purged_walk_forward_split


def _periods(*, late_q1: bool = False, gap: bool = False) -> pl.DataFrame:
    rows = []
    quarter_ends = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    for year in range(2020, 2023):
        for quarter in range(1, 5):
            if gap and year == 2021 and quarter == 2:
                continue
            month, day = quarter_ends[quarter]
            rows.append(
                {
                    "issuer_id": "ABC",
                    "statement_scope": "CONSOLIDATED",
                    "period_type": "QUARTER",
                    "fiscal_year": year,
                    "fiscal_quarter": quarter,
                    "period_end_date": date(year, month, day),
                    "accounting_regime": "CORPORATE",
                    "BS_TOTAL_EQUITY": -1 if year == 2021 and quarter == 4 else 100,
                    "parent_equity": 100,
                    "interest_coverage": 2.0,
                    "CF_OPERATING_CASH_FLOW": 1.0,
                    "current_ratio": 2.0,
                    "roa": 0.05,
                    "_period_available_at": date(year, month, day)
                    if not (late_q1 and year == 2021 and quarter == 1)
                    else date(2022, 6, 1),
                }
            )
    return pl.DataFrame(rows)


def test_target_looks_ahead_four_exact_quarters_and_tracks_onset() -> None:
    targets = build_distress_targets(_periods())
    base_2020_q4 = targets.filter(
        (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert base_2020_q4["distress_target"] == 1
    assert base_2020_q4["label_available_at"] == date(2021, 12, 31)
    distressed = targets.filter(
        (pl.col("fiscal_year") == 2021) & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert distressed["already_distressed_at_t"] is True


def test_target_uses_latest_delayed_filing_as_label_availability() -> None:
    targets = build_distress_targets(_periods(late_q1=True))
    base_2020_q4 = targets.filter(
        (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert base_2020_q4["label_available_at"] == date(2022, 6, 1)


def test_target_emits_zero_for_a_complete_healthy_horizon() -> None:
    targets = build_distress_targets(_periods())
    healthy = targets.filter(
        (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 1)
    ).row(0, named=True)
    assert healthy["distress_target"] == 0
    assert healthy["already_distressed_at_t"] is False


def test_target_invalidates_missing_quarter_and_honors_corporate_only() -> None:
    source = _periods(gap=True).with_columns(
        pl.when(pl.col("fiscal_year") == 2022)
        .then(pl.lit("BANK"))
        .otherwise(pl.col("accounting_regime"))
        .alias("accounting_regime")
    )
    targets = build_distress_targets(source)
    row = targets.filter(
        (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert row["distress_target"] is None
    assert targets.filter(pl.col("accounting_regime") == "BANK").height == 0


def test_purged_walk_forward_drops_late_labels_at_each_boundary() -> None:
    source = pl.DataFrame(
        {
            "period_end_date": [
                date(2020, 12, 31),
                date(2022, 3, 31),
                date(2022, 9, 30),
                date(2022, 10, 31),
            ],
            "_period_available_at": [
                date(2020, 12, 31),
                date(2022, 3, 31),
                date(2022, 9, 30),
                date(2022, 10, 31),
            ],
            "label_available_at": [
                date(2021, 12, 31),
                date(2022, 6, 1),
                date(2022, 11, 1),
                date(2023, 3, 31),
            ],
            "distress_target": [0, 1, 0, 1],
        }
    )
    split = purged_walk_forward_split(
        source, validation_start=date(2022, 1, 1), test_start=date(2022, 10, 1)
    )
    assert split["train"].height == 1
    assert split["validation"].height == 1
    assert split["test"].height == 1
    train_label_max = split["train"]["label_available_at"].max()
    validation_label_max = split["validation"]["label_available_at"].max()
    assert isinstance(train_label_max, date)
    assert isinstance(validation_label_max, date)
    assert train_label_max <= date(2022, 1, 1)
    assert validation_label_max <= date(2022, 10, 1)


def test_split_rejects_overlapping_boundaries() -> None:
    with pytest.raises(ContractViolationError):
        purged_walk_forward_split(
            pl.DataFrame(
                {
                    "period_end_date": [date(2020, 1, 1)],
                    "_period_available_at": [date(2020, 1, 1)],
                    "label_available_at": [date(2021, 1, 1)],
                    "distress_target": [1],
                }
            ),
            validation_start=date(2022, 1, 1),
            test_start=date(2022, 1, 1),
        )


def test_monotone_constraints_cover_exact_model_feature_sets() -> None:
    from finintel.ml.supervised.train import BASE_FEATURES, _constraints

    constraints = dict(zip(BASE_FEATURES, _constraints(BASE_FEATURES), strict=True))
    assert constraints["roa"] == -1
    assert constraints["interest_coverage"] == -1
    assert constraints["debt_to_equity"] == 1
    assert constraints["revenue_growth_yoy"] == 0
    with pytest.raises(ContractViolationError):
        _constraints(("unmapped_feature",))


def test_fitted_model_respects_interest_and_roa_monotonicity() -> None:
    import numpy as np

    from finintel.ml.supervised.train import (
        BASE_FEATURES,
        score_distress_model,
        train_distress_model,
    )

    def synthetic_fold(seed: int, n_rows: int, year_base: int) -> pl.DataFrame:
        rng = np.random.default_rng(seed)
        rows: dict[str, list[float]] = {name: [] for name in BASE_FEATURES}
        roa_vals = rng.uniform(-0.18, 0.22, size=n_rows)
        ic_vals = rng.uniform(-1.0, 12.0, size=n_rows)
        dte_vals = rng.uniform(0.05, 4.5, size=n_rows)
        altman_vals = (
            2.2
            + 2.5 * roa_vals
            + 0.12 * ic_vals
            - 0.4 * dte_vals
            + rng.normal(0.0, 1.4, size=n_rows)
        )
        logits = (
            -6.0 * roa_vals
            - 0.35 * ic_vals
            + 0.7 * dte_vals
            + rng.normal(0.0, 0.45, size=n_rows)
            - 0.4
        )
        targets = (logits > 0.0).astype(int).tolist()
        for idx, name in enumerate(BASE_FEATURES):
            if name == "roa":
                rows[name] = roa_vals.tolist()
            elif name == "interest_coverage":
                rows[name] = ic_vals.tolist()
            elif name == "debt_to_equity":
                rows[name] = dte_vals.tolist()
            elif name == "altman_z_double_prime":
                rows[name] = altman_vals.tolist()
            else:
                rows[name] = rng.normal(0.2 + 0.02 * idx, 0.25, size=n_rows).tolist()
        return pl.DataFrame(
            {
                **rows,
                "issuer_id": [f"SYN_{i:03d}" for i in range(n_rows)],
                "accounting_regime": ["CORPORATE"] * n_rows,
                "statement_scope": ["CONSOLIDATED"] * n_rows,
                "period_type": ["QUARTER"] * n_rows,
                "fiscal_year": [year_base + (i % 2) for i in range(n_rows)],
                "fiscal_quarter": [(i % 4) + 1 for i in range(n_rows)],
                "period_end_date": [date(year_base, 12, 31)] * n_rows,
                "filing_id": [f"FIL_{seed}_{i:03d}" for i in range(n_rows)],
                "taxonomy_version": ["VAS_TT200_2014"] * n_rows,
                "_period_available_at": [date(year_base, 12, 31)] * n_rows,
                "distress_target": targets,
            }
        )

    train_df = synthetic_fold(seed=42, n_rows=260, year_base=2019)
    valid_df = synthetic_fold(seed=99, n_rows=120, year_base=2021)
    result = train_distress_model(train_df, valid_df)

    trees_df = result.classifier.get_booster().trees_to_dataframe()
    split_features = set(trees_df.loc[trees_df["Feature"] != "Leaf", "Feature"])
    assert split_features, (
        "Expected non-trivial tree splits rather than Leaf-only trees"
    )

    probe = synthetic_fold(seed=123, n_rows=1, year_base=2023)
    ic_grid = [-0.8, -0.2, 0.5, 1.2, 2.5, 5.0, 8.0, 11.5]
    ic_probes = pl.concat(
        [probe.with_columns(pl.lit(val).alias("interest_coverage")) for val in ic_grid]
    )
    ic_scores = score_distress_model(result, ic_probes)[
        "distress_risk_score"
    ].to_numpy()
    assert np.all(np.diff(ic_scores) <= 1e-12)
    assert ic_scores[-1] < ic_scores[0]

    roa_grid = [-0.16, -0.10, -0.04, 0.0, 0.05, 0.10, 0.16, 0.21]
    roa_probes = pl.concat(
        [probe.with_columns(pl.lit(val).alias("roa")) for val in roa_grid]
    )
    roa_scores = score_distress_model(result, roa_probes)[
        "distress_risk_score"
    ].to_numpy()
    assert np.all(np.diff(roa_scores) <= 1e-12)
    assert roa_scores[-1] < roa_scores[0]


def test_target_preserves_debt_free_labels_positive_events_and_exact_quarter_onset() -> (
    None
):
    debt_free = _periods().with_columns(
        pl.lit(None, dtype=pl.Float64).alias("interest_coverage"),
        pl.lit("NO_INTEREST_EXPENSE").alias("interest_coverage_flag"),
    )
    targets = build_distress_targets(debt_free)
    q1_2020 = targets.filter(
        (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 1)
    ).row(0, named=True)
    assert q1_2020["distress_target"] == 0
    assert q1_2020["already_distressed_at_t"] is False

    partial_secondary = _periods().with_columns(
        pl.when((pl.col("fiscal_year") == 2021) & (pl.col("fiscal_quarter") == 2))
        .then(pl.lit(None, dtype=pl.Float64))
        .otherwise(pl.col("roa"))
        .alias("roa")
    )
    q4_2020 = (
        build_distress_targets(partial_secondary)
        .filter((pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 4))
        .row(0, named=True)
    )
    assert q4_2020["distress_target"] == 1

    gapped = pl.DataFrame(
        {
            "issuer_id": ["GAP", "GAP"],
            "statement_scope": ["CONSOLIDATED", "CONSOLIDATED"],
            "period_type": ["QUARTER", "QUARTER"],
            "fiscal_year": [2020, 2023],
            "fiscal_quarter": [1, 1],
            "period_end_date": [date(2020, 3, 31), date(2023, 3, 31)],
            "accounting_regime": ["CORPORATE", "CORPORATE"],
            "BS_TOTAL_EQUITY": [100.0, 100.0],
            "parent_equity": [100.0, 100.0],
            "interest_coverage": [0.5, 0.5],
            "CF_OPERATING_CASH_FLOW": [-5.0, -5.0],
            "current_ratio": [0.8, 0.8],
            "roa": [0.02, 0.02],
            "_period_available_at": [date(2020, 5, 1), date(2023, 5, 1)],
        }
    )
    gapped_out = build_distress_targets(gapped)
    assert gapped_out["already_distressed_at_t"].to_list() == [False, False]


def test_treeshap_additivity_original_feature_names_and_per_period_peer_medians() -> (
    None
):
    import json

    import numpy as np

    from finintel.ml.supervised.explain import (
        compute_shap_contributions,
        explain_distress,
        explain_global_importance,
    )
    from finintel.ml.supervised.train import (
        BASE_FEATURES,
        score_distress_model,
        train_distress_model,
    )

    compact_features = (
        "roa",
        "interest_coverage",
        "debt_to_equity",
        "altman_z_double_prime",
    )

    def make_split(n_rows: int, seed_offset: int) -> pl.DataFrame:
        local_rng = np.random.default_rng(7 + seed_offset)
        roa = local_rng.uniform(-0.15, 0.20, size=n_rows)
        ic = local_rng.uniform(0.0, 8.0, size=n_rows)
        dte = local_rng.uniform(0.1, 3.5, size=n_rows)
        alt = 2.0 + 6.0 * roa + 0.3 * ic - 0.5 * dte
        y = ((-5.0 * roa - 0.3 * ic + 0.8 * dte - 0.2) > 0).astype(int)
        return pl.DataFrame(
            {
                "issuer_id": [f"C_{i:03d}" for i in range(n_rows)],
                "accounting_regime": ["CORPORATE"] * n_rows,
                "statement_scope": ["CONSOLIDATED"] * n_rows,
                "period_type": ["QUARTER"] * n_rows,
                "period_end_date": [
                    date(2022, 3, 31) if i % 2 == 0 else date(2022, 6, 30)
                    for i in range(n_rows)
                ],
                "filing_id": [f"F_{seed_offset}_{i:03d}" for i in range(n_rows)],
                "taxonomy_version": ["VAS_TT200_2014"] * n_rows,
                "_period_available_at": [date(2022, 8, 1)] * n_rows,
                "roa": roa.tolist(),
                "interest_coverage": [
                    None if i % 9 == 0 else float(ic[i]) for i in range(n_rows)
                ],
                "debt_to_equity": dte.tolist(),
                "altman_z_double_prime": alt.tolist(),
                "distress_target": y.tolist(),
            }
        )

    train_df = make_split(200, 0)
    valid_df = make_split(100, 1)
    eval_df = make_split(6, 2)
    result = train_distress_model(train_df, valid_df, features=compact_features)

    benchmarks = pl.DataFrame(
        {
            "issuer_id": ["C_000", "C_001"],
            "accounting_regime": ["CORPORATE", "CORPORATE"],
            "statement_scope": ["CONSOLIDATED", "CONSOLIDATED"],
            "period_type": ["QUARTER", "QUARTER"],
            "period_end_date": [date(2022, 3, 31), date(2022, 6, 30)],
            "metric_or_ratio_name": ["debt_to_equity", "debt_to_equity"],
            "peer_median": [0.75, 1.85],
        }
    )
    explained = explain_distress(result, eval_df, peer_medians=benchmarks)
    scored = score_distress_model(result, eval_df)
    base_val, orig_features, orig_shap, raw_margins = compute_shap_contributions(
        result, eval_df
    )
    assert orig_features == compact_features
    assert np.allclose(
        base_val + orig_shap.sum(axis=1),
        scored["distress_risk_score"].to_numpy(),
        rtol=1e-6,
        atol=1e-6,
    )
    assert np.allclose(raw_margins, scored["distress_risk_score"].to_numpy(), atol=1e-6)

    for idx, payload_str in enumerate(explained["shap_top_drivers_json"].to_list()):
        payload = json.loads(payload_str)
        drivers = payload["top_risk_increasing"] + payload["top_risk_decreasing"]
        for item in drivers:
            assert item["feature"] in BASE_FEATURES
            assert not item["feature"].endswith("_missing")
        reconstructed = payload["base_value"] + sum(d["shap_delta"] for d in drivers)
        assert reconstructed == pytest.approx(
            scored["distress_risk_score"][idx], abs=1e-6
        )
        if idx in (0, 1):
            dte_items = [d for d in drivers if d["feature"] == "debt_to_equity"]
            if dte_items:
                expected_median = 0.75 if idx == 0 else 1.85
                assert dte_items[0]["peer_median"] == pytest.approx(expected_median)

    global_imp = explain_global_importance(result, eval_df)
    assert set(global_imp) == set(compact_features)
    assert all(not name.endswith("_missing") for name in global_imp)


def test_pit_splits_prevent_restatement_leakage_and_enforce_exact_filing() -> None:
    from finintel.ml.supervised.temporal_split import (
        build_pit_ml_splits,
        purged_walk_forward_split,
    )
    from tests.unit.test_layer4_analytics import facts, issuer

    sample = pl.DataFrame(
        {
            "period_end_date": [date(2020, 12, 31), date(2020, 12, 31)],
            "_period_available_at": [date(2021, 2, 1), date(2021, 2, 1)],
            "label_available_at": [date(2021, 12, 31), date(2021, 12, 31)],
            "distress_target": [0, 1],
            "_exact_filing_basis": [True, False],
            "_label_exact_filing_basis": [True, True],
        }
    )
    strict = purged_walk_forward_split(
        sample,
        validation_start=date(2022, 1, 1),
        test_start=date(2023, 1, 1),
        require_exact_filing=True,
    )
    assert strict["train"].height == 1
    assert strict["train"]["distress_target"].item() == 0

    quarter_template: dict[str, float | None] = {
        "BS_TOTAL_ASSETS": 1000.0,
        "BS_TOTAL_EQUITY": 500.0,
        "BS_MINORITY_INTEREST": 0.0,
        "BS_TOTAL_LIABILITIES": 500.0,
        "BS_CURRENT_ASSETS": 400.0,
        "BS_CURRENT_LIABILITIES": 200.0,
        "BS_CASH_AND_EQUIVALENTS": 100.0,
        "BS_SHORT_TERM_BORROWINGS": 100.0,
        "BS_RETAINED_EARNINGS": 200.0,
        "IS_NET_REVENUE": 300.0,
        "IS_GROSS_PROFIT": 120.0,
        "IS_PROFIT_BEFORE_TAX": 50.0,
        "IS_INTEREST_EXPENSE": 5.0,
        "IS_NET_INCOME": 40.0,
        "IS_NET_INCOME_PARENT": 40.0,
        "CF_OPERATING_CASH_FLOW": 45.0,
    }
    raw_facts = facts(
        {(y, q): quarter_template for y in range(2019, 2024) for q in range(1, 5)}
    ).with_columns(
        pl.lit("EXACT_FILING").alias("published_at_basis"),
        pl.col("effective_available_at").alias("published_at"),
    )
    restatement_2024 = raw_facts.filter(
        (pl.col("fiscal_year") == 2020)
        & (pl.col("fiscal_quarter") == 4)
        & (pl.col("metric_id") == "BS_CURRENT_ASSETS")
    ).with_columns(
        pl.lit(9999.0).alias("value"),
        pl.lit(2, dtype=pl.Int64).alias("revision_no"),
        pl.lit("FPT-2020-4-RESTATED-2024").alias("filing_id"),
        pl.lit(["FPT-2020-4-RESTATED-2024"]).alias("source_filing_ids"),
        pl.lit(date(2024, 5, 1)).alias("published_at"),
        pl.lit(date(2024, 5, 1)).alias("effective_available_at"),
    )
    splits = build_pit_ml_splits(
        pl.concat([raw_facts, restatement_2024]),
        issuer(),
        validation_start=date(2022, 3, 1),
        test_start=date(2023, 3, 1),
        as_of=date(2025, 1, 1),
        require_exact_filing=True,
    )
    q4_2020_train = splits["train"].filter(
        (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 4)
    )
    assert q4_2020_train.height == 1
    assert q4_2020_train["current_ratio"].item() == pytest.approx(2.0)


def test_ml_pipeline_guardrails_and_publishable_ml_scores_snapshot(
    tmp_path: Path,
) -> None:
    import json

    from finintel.ml.unsupervised import InsufficientModelData
    from finintel.pipeline.analytics import AnalyticsHandler
    from finintel.pipeline.runner import RunRequest, Stage, StepContext
    from finintel.pipeline.state_store import StateStore
    from finintel.storage import ParquetRepository, SnapshotPublisher
    from finintel.storage.schemas import ML_SCORES_TABLE
    from tests.unit.test_layer4_analytics import facts

    def issuer_periods(idx: int, distressed_year: int | None) -> pl.DataFrame:
        periods_map: dict[tuple[int, int | None], dict[str, float | None]] = {}
        for year in range(2017, 2025):
            for q in range(1, 5):
                hit = distressed_year == year and q == 3
                scale = 1.0 + 0.03 * idx + 0.01 * q
                periods_map[(year, q)] = {
                    "BS_TOTAL_ASSETS": 1000.0 * scale,
                    "BS_TOTAL_EQUITY": -25.0
                    if hit
                    else (450.0 - 8.0 * (idx % 7)) * scale,
                    "BS_MINORITY_INTEREST": 10.0 * scale,
                    "BS_TOTAL_LIABILITIES": (550.0 + 8.0 * (idx % 7)) * scale,
                    "BS_CURRENT_ASSETS": (360.0 + 5.0 * (idx % 5)) * scale,
                    "BS_CURRENT_LIABILITIES": (200.0 + 4.0 * (idx % 4)) * scale,
                    "BS_CASH_AND_EQUIVALENTS": (80.0 + 3.0 * (idx % 6)) * scale,
                    "BS_SHORT_TERM_BORROWINGS": (120.0 + 6.0 * (idx % 5)) * scale,
                    "BS_LONG_TERM_BORROWINGS": (90.0 + 4.0 * (idx % 3)) * scale,
                    "BS_RETAINED_EARNINGS": (150.0 - 5.0 * (idx % 6)) * scale,
                    "BS_ORDINARY_SHARES_CAPITAL": 200_000.0,
                    "BS_TREASURY_SHARES": 0.0,
                    "BS_INVENTORIES": (70.0 + 2.0 * (idx % 5)) * scale,
                    "BS_TRADE_RECEIVABLES_ST": (95.0 + 3.0 * (idx % 4)) * scale,
                    "BS_TRADE_PAYABLES_ST": (65.0 + 2.0 * (idx % 3)) * scale,
                    "BS_TANGIBLE_FIXED_ASSETS": 300.0 * scale,
                    "IS_NET_REVENUE": (260.0 + 7.0 * (idx % 6)) * scale,
                    "IS_COST_OF_GOODS_SOLD": (160.0 + 4.0 * (idx % 5)) * scale,
                    "IS_GROSS_PROFIT": (100.0 + 3.0 * (idx % 6)) * scale,
                    "IS_OPERATING_PROFIT": (42.0 - 2.0 * (idx % 5)) * scale,
                    "IS_PROFIT_BEFORE_TAX": (38.0 - 2.0 * (idx % 5)) * scale,
                    "IS_INTEREST_EXPENSE": (4.0 + 0.5 * (idx % 4)) * scale,
                    "IS_CURRENT_INCOME_TAX": 7.0 * scale,
                    "IS_DEFERRED_INCOME_TAX": 0.5 * scale,
                    "IS_NET_INCOME": (30.5 - 2.0 * (idx % 5)) * scale,
                    "IS_NET_INCOME_PARENT": (28.0 - 2.0 * (idx % 5)) * scale,
                    "IS_SELLING_EXPENSES": 12.0 * scale,
                    "IS_ADMINISTRATIVE_EXPENSES": 15.0 * scale,
                    "CF_OPERATING_CASH_FLOW": (34.0 - 2.5 * (idx % 5)) * scale,
                    "CF_CAPITAL_EXPENDITURE": -12.0 * scale,
                    "CF_DEPRECIATION_AMORTIZATION": 8.0 * scale,
                    "CF_EQUITY_ISSUANCE_PROCEEDS": 0.0,
                }
        return facts(periods_map, issuer_id=f"CORP_{idx:02d}")

    distress_schedule = {0: 2021, 1: 2021, 2: 2021, 3: 2022, 4: 2022, 5: 2024, 6: 2024}
    statutory_facts = pl.concat(
        [issuer_periods(i, distress_schedule.get(i)) for i in range(12)]
    )
    issuers_df = pl.DataFrame(
        {
            "issuer_id": [f"CORP_{i:02d}" for i in range(12)],
            "ticker": [f"C{i:02d}" for i in range(12)],
            "accounting_regime": ["CORPORATE"] * 12,
            "icb_code_level2": ["2000" if i < 6 else "3000" for i in range(12)],
            "icb_code_level3": ["201010" if i < 6 else "301010" for i in range(12)],
            "industry_snapshot_date": [date(2017, 1, 1)] * 12,
        }
    )

    curated = tmp_path / "curated"
    curated.mkdir()
    issuers_df.write_parquet(curated / "issuers.parquet")
    statutory_facts.write_parquet(curated / "financial_facts.parquet")

    state = StateStore(tmp_path / "metadata/state.sqlite")
    state.initialize()
    req = RunRequest(source="file")

    state.start_run("run_unapproved", "CLI_MANUAL", req.log_arguments(), None)
    with pytest.raises(ContractViolationError, match="approved_target"):
        AnalyticsHandler(
            state,
            tmp_path,
            {
                "as_of": "2025-03-01",
                "ml": {
                    "enabled": True,
                    "approved_target": False,
                    "validation_start": "2021-12-01",
                    "test_start": "2023-03-01",
                },
            },
        )(StepContext("run_unapproved", Stage.ANALYTICS, req, (), None))

    state.start_run("run_statutory", "CLI_MANUAL", req.log_arguments(), None)
    with pytest.raises((ContractViolationError, InsufficientModelData)):
        AnalyticsHandler(
            state,
            tmp_path,
            {
                "as_of": "2025-03-01",
                "ml": {
                    "enabled": True,
                    "approved_target": True,
                    "validation_start": "2021-12-01",
                    "test_start": "2023-03-01",
                },
            },
        )(StepContext("run_statutory", Stage.ANALYTICS, req, (), None))

    exact_facts = statutory_facts.with_columns(
        pl.lit("EXACT_FILING").alias("published_at_basis"),
        pl.col("effective_available_at").alias("published_at"),
    )
    exact_facts.write_parquet(curated / "financial_facts.parquet")
    state.start_run("run_exact", "CLI_MANUAL", req.log_arguments(), None)
    AnalyticsHandler(
        state,
        tmp_path,
        {
            "as_of": "2025-03-01",
            "ml": {
                "enabled": True,
                "approved_target": True,
                "validation_start": "2021-12-01",
                "test_start": "2023-03-01",
            },
        },
    )(StepContext("run_exact", Stage.ANALYTICS, req, (), None))

    batch = curated / "analytics/run_exact"
    parquet_tables = {p.name: p for p in batch.glob("*.parquet")}
    assert ML_SCORES_TABLE in parquet_tables
    assert (batch / "model_evaluation_report.json").is_file()
    assert (batch / "_ml_run_manifest.json").is_file()

    report = json.loads(
        (batch / "model_evaluation_report.json").read_text(encoding="utf-8")
    )
    assert set(report["test"]["models"]) == {
        "Baseline_1_AltmanZ",
        "Baseline_2_Logit",
        "XGBoost",
    }
    assert "by_fiscal_year" in report["test"]
    assert (
        report["validation"]["evaluation_role"]
        == "IN_SAMPLE_CALIBRATION_FOLD_DO_NOT_USE_AS_OOS_CLAIM"
    )
    manifest = json.loads((batch / "_ml_run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["require_exact_filing"] is True
    assert manifest["facts_sha256"]

    snap_dir = SnapshotPublisher(tmp_path / "published/snapshots").publish(
        "snap_ml_test", parquet_tables
    )
    ml_table = ParquetRepository(snap_dir).read_table(ML_SCORES_TABLE)
    assert ml_table.height > 0
    assert ml_table["model_role"].unique().to_list() == ["SCREENING_INDICATOR"]
    assert ml_table["shap_top_drivers_json"].drop_nulls().len() > 0


def test_calibration_rejects_single_positive_validation_sample() -> None:
    from finintel.ml.supervised.train import BASE_FEATURES, train_distress_model
    from finintel.ml.unsupervised import InsufficientModelData

    def make_rows(targets: list[int]) -> pl.DataFrame:
        n = len(targets)
        return pl.DataFrame(
            {
                **{name: [0.1 + 0.02 * i for i in range(n)] for name in BASE_FEATURES},
                "accounting_regime": ["CORPORATE"] * n,
                "statement_scope": ["CONSOLIDATED"] * n,
                "taxonomy_version": ["VAS_TT200_2014"] * n,
                "altman_z_double_prime": [2.0] * n,
                "_period_available_at": [date(2021, 1, 1)] * n,
                "distress_target": targets,
            }
        )

    train_df = make_rows([0, 1] * 10)
    valid_one_pos = make_rows([0] * 9 + [1])
    with pytest.raises(InsufficientModelData, match="at least two observations"):
        train_distress_model(train_df, valid_one_pos)


def test_target_gap03_indirect_cf_trap_4q_persistence_not_7q_and_event3_all_quarters() -> (
    None
):
    quarter_ends = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}

    # 1. POM simulation: CFO > 0 (indirect CF working capital liquidate), EBIT < 0 (IC < 0),
    # CR = 0.35 < 0.80, total_debt > 0, ROA = -0.13 < -0.10.
    pom_rows = []
    for year in range(2020, 2023):
        for q in range(1, 5):
            m, d = quarter_ends[q]
            in_distress = (year == 2021 and q >= 1) or (year == 2022 and q == 1)
            pom_rows.append(
                {
                    "issuer_id": "POM",
                    "statement_scope": "CONSOLIDATED",
                    "period_type": "QUARTER",
                    "fiscal_year": year,
                    "fiscal_quarter": q,
                    "period_end_date": date(year, m, d),
                    "accounting_regime": "CORPORATE",
                    "BS_TOTAL_EQUITY": 500.0,
                    "parent_equity": 500.0,
                    "total_debt": 800.0,
                    "interest_coverage": -0.4 if in_distress else 2.5,
                    "CF_OPERATING_CASH_FLOW": 20.0,  # CFO > 0 trap!
                    "current_ratio": 0.35 if in_distress else 1.8,
                    "roa": -0.13 if in_distress else 0.06,
                    "_period_available_at": date(year, m, d),
                }
            )
    pom_targets = build_distress_targets(pl.DataFrame(pom_rows))
    q4_2020 = pom_targets.filter(
        (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert q4_2020["distress_target"] == 1
    assert q4_2020["already_distressed_at_t"] is False
    for q_idx in range(1, 5):
        row_2021 = pom_targets.filter(
            (pl.col("fiscal_year") == 2021) & (pl.col("fiscal_quarter") == q_idx)
        ).row(0, named=True)
        assert row_2021["already_distressed_at_t"] is True

    # 2. Prove 4Q persistence (NO 7-quarter lookback bug):
    # 2 quarters of liquidity_bad at t-6 (2020-Q1) and t-5 (2020-Q2), then healthy for 5 quarters
    # t-4..t (2020-Q3, 2020-Q4, 2021-Q1, 2021-Q2, 2021-Q3).
    rec_rows = []
    for year in range(2020, 2023):
        for q in range(1, 5):
            m, d = quarter_ends[q]
            liq_hit = year == 2020 and q in (1, 2)
            rec_rows.append(
                {
                    "issuer_id": "REC",
                    "statement_scope": "CONSOLIDATED",
                    "period_type": "QUARTER",
                    "fiscal_year": year,
                    "fiscal_quarter": q,
                    "period_end_date": date(year, m, d),
                    "accounting_regime": "CORPORATE",
                    "BS_TOTAL_EQUITY": 200.0,
                    "parent_equity": 200.0,
                    "interest_coverage": 0.4 if liq_hit else 3.0,
                    "CF_OPERATING_CASH_FLOW": -10.0 if liq_hit else 15.0,
                    "current_ratio": 0.7 if liq_hit else 1.8,
                    "roa": 0.02,
                    "_period_available_at": date(year, m, d),
                }
            )
    rec_targets = build_distress_targets(pl.DataFrame(rec_rows))
    # At 2020-Q4 (t-3..t includes 2020-Q1 and 2020-Q2), trailing_liquidity_4q == 2 -> True
    assert (
        rec_targets.filter(
            (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 4)
        )["already_distressed_at_t"].item()
        is True
    )
    # At 2021-Q3 (t-6 and t-5 were bad, t-4..t are all healthy), already_distressed_at_t MUST be False!
    assert (
        rec_targets.filter(
            (pl.col("fiscal_year") == 2021) & (pl.col("fiscal_quarter") == 3)
        )["already_distressed_at_t"].item()
        is False
    )

    # 3. GATE-ML-2 (ML-04): Mode A evaluates TTM-based Event 3 strictly at t+4 (0% TTM overlap).
    # Severe loss ONLY at 2021-Q2 must NOT trigger 2020-Q4 (t+2 has 50% TTM overlap),
    # while triggering cleanly at 2020-Q2 (where t+4 == 2021-Q2 has 0% TTM overlap).
    ev3_df = _periods().with_columns(
        pl.lit(100.0).alias("BS_TOTAL_EQUITY"),
        pl.when((pl.col("fiscal_year") == 2021) & (pl.col("fiscal_quarter") == 2))
        .then(pl.lit(-0.15))
        .otherwise(pl.lit(0.05))
        .alias("roa"),
        pl.when((pl.col("fiscal_year") == 2021) & (pl.col("fiscal_quarter") == 2))
        .then(pl.lit(-5.0))
        .otherwise(pl.lit(10.0))
        .alias("CF_OPERATING_CASH_FLOW"),
    )
    ev3_targets = build_distress_targets(ev3_df)
    assert (
        ev3_targets.filter(
            (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 4)
        )["distress_target"].item()
        == 0
    )
    assert (
        ev3_targets.filter(
            (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 2)
        )["distress_target"].item()
        == 1
    )


def test_target_gap04_label_exact_filing_basis_uses_period_exact_filing() -> None:
    from finintel.analytics.intermediates import prepare_periods
    from finintel.analytics.ratios import calculate_ratios
    from tests.unit.test_layer4_analytics import facts, issuer

    template: dict[str, float | None] = {
        "BS_TOTAL_ASSETS": 1000.0,
        "BS_TOTAL_EQUITY": 500.0,
        "BS_MINORITY_INTEREST": 0.0,
        "BS_TOTAL_LIABILITIES": 500.0,
        "BS_CURRENT_ASSETS": 400.0,
        "BS_CURRENT_LIABILITIES": 200.0,
        "BS_CASH_AND_EQUIVALENTS": 100.0,
        "BS_SHORT_TERM_BORROWINGS": 100.0,
        "BS_RETAINED_EARNINGS": 200.0,
        "IS_NET_REVENUE": 300.0,
        "IS_GROSS_PROFIT": 120.0,
        "IS_PROFIT_BEFORE_TAX": 50.0,
        "IS_INTEREST_EXPENSE": 5.0,
        "IS_NET_INCOME": 40.0,
        "IS_NET_INCOME_PARENT": 40.0,
        "CF_OPERATING_CASH_FLOW": 45.0,
    }
    raw_facts = facts(
        {(y, q): template for y in range(2017, 2021) for q in range(1, 5)}
    ).with_columns(
        pl.when(pl.col("fiscal_year") == 2017)
        .then(pl.lit("STATUTORY_LAG"))
        .otherwise(pl.lit("EXACT_FILING"))
        .alias("published_at_basis"),
        pl.when(pl.col("fiscal_year") == 2017)
        .then(pl.lit(None, dtype=pl.Date))
        .otherwise(pl.col("effective_available_at"))
        .alias("published_at"),
    )
    periods = prepare_periods(
        raw_facts, issuer(), as_of=date(2021, 6, 1), include_ml_inputs=True
    )
    ratios = calculate_ratios(periods)
    targets = build_distress_targets(ratios, as_of=date(2021, 6, 1))
    window_rows = targets.filter(
        ((pl.col("fiscal_year") == 2018) & (pl.col("fiscal_quarter") == 4))
        | ((pl.col("fiscal_year") == 2019) & pl.col("fiscal_quarter").is_in([1, 2, 3]))
    )
    assert window_rows.height == 4
    assert window_rows["_label_exact_filing_basis"].to_list() == [True] * 4


def test_pit_symmetric_peer_z_prevents_future_icb_leakage_and_upgrades_when_historical_icb_present() -> (
    None
):
    from finintel.ml.supervised.temporal_split import (
        build_pit_ml_splits,
        resolve_peer_standardization_mode,
    )
    from finintel.ml.supervised.train import (
        BASE_FEATURES,
        SECTOR_ADJUSTED_FEATURES,
        _constraints,
    )
    from tests.unit.test_layer4_analytics import facts

    assert len(BASE_FEATURES) == 20
    assert len(SECTOR_ADJUSTED_FEATURES) == 20
    adj_constraints = dict(
        zip(
            SECTOR_ADJUSTED_FEATURES,
            _constraints(SECTOR_ADJUSTED_FEATURES),
            strict=True,
        )
    )
    assert adj_constraints["sector_z_cash_conversion_cycle"] == 1
    assert adj_constraints["sector_z_debt_to_equity"] == 1
    assert adj_constraints["sector_z_asset_turnover"] == -1
    assert adj_constraints["sector_z_ebit_margin"] == -1

    def make_corp_facts(idx: int) -> pl.DataFrame:
        template: dict[str, float | None] = {
            "BS_TOTAL_ASSETS": 1000.0 * (1.0 + 0.05 * idx),
            "BS_TOTAL_EQUITY": 450.0 * (1.0 + 0.04 * idx),
            "BS_MINORITY_INTEREST": 10.0,
            "BS_TOTAL_LIABILITIES": 550.0 * (1.0 + 0.06 * idx),
            "BS_CURRENT_ASSETS": 360.0,
            "BS_CURRENT_LIABILITIES": 200.0,
            "BS_CASH_AND_EQUIVALENTS": 80.0,
            "BS_SHORT_TERM_BORROWINGS": 120.0 + 10.0 * idx,
            "BS_LONG_TERM_BORROWINGS": 90.0,
            "BS_RETAINED_EARNINGS": 150.0,
            "BS_INVENTORIES": 70.0 + 3.0 * idx,
            "BS_TRADE_RECEIVABLES_ST": 95.0 + 2.0 * idx,
            "BS_TRADE_PAYABLES_ST": 65.0 + 4.0 * idx,
            "BS_TANGIBLE_FIXED_ASSETS": 300.0,
            "IS_NET_REVENUE": 260.0 + 12.0 * idx,
            "IS_COST_OF_GOODS_SOLD": 160.0 + 6.0 * idx,
            "IS_GROSS_PROFIT": 100.0 + 6.0 * idx,
            "IS_OPERATING_PROFIT": 42.0 + 2.0 * idx,
            "IS_PROFIT_BEFORE_TAX": 38.0 + 2.0 * idx,
            "IS_INTEREST_EXPENSE": 4.0,
            "IS_CURRENT_INCOME_TAX": 7.0,
            "IS_DEFERRED_INCOME_TAX": 0.5,
            "IS_NET_INCOME": 30.5 + 1.5 * idx,
            "IS_NET_INCOME_PARENT": 28.0 + 1.5 * idx,
            "IS_SELLING_EXPENSES": 12.0,
            "IS_ADMINISTRATIVE_EXPENSES": 15.0,
            "CF_OPERATING_CASH_FLOW": 34.0 + 1.0 * idx,
            "CF_CAPITAL_EXPENDITURE": -12.0,
            "CF_DEPRECIATION_AMORTIZATION": 8.0,
        }
        return facts(
            {(y, q): template for y in range(2018, 2025) for q in range(1, 5)},
            issuer_id=f"P_{idx:02d}",
        ).with_columns(
            pl.lit("EXACT_FILING").alias("published_at_basis"),
            pl.col("effective_available_at").alias("published_at"),
        )

    all_facts = pl.concat([make_corp_facts(i) for i in range(10)])
    issuers_2025_a = pl.DataFrame(
        {
            "issuer_id": [f"P_{i:02d}" for i in range(10)],
            "ticker": [f"P{i:02d}" for i in range(10)],
            "accounting_regime": ["CORPORATE"] * 10,
            "icb_code_level2": ["2000" if i < 5 else "3000" for i in range(10)],
            "icb_code_level3": ["201010" if i < 5 else "301010" for i in range(10)],
            "industry_snapshot_date": [date(2025, 1, 1)] * 10,
        }
    )
    issuers_2025_b = issuers_2025_a.with_columns(
        pl.lit("9000").alias("icb_code_level2"),
        pl.lit("901010").alias("icb_code_level3"),
    )
    splits_a = build_pit_ml_splits(
        all_facts,
        issuers_2025_a,
        validation_start=date(2021, 12, 1),
        test_start=date(2023, 3, 1),
        as_of=date(2025, 3, 1),
    )
    splits_b = build_pit_ml_splits(
        all_facts,
        issuers_2025_b,
        validation_start=date(2021, 12, 1),
        test_start=date(2023, 3, 1),
        as_of=date(2025, 3, 1),
    )
    assert (
        resolve_peer_standardization_mode(splits_a["train"], splits_a["validation"])
        == "PIT_REGIME_SYMMETRIC"
    )
    for fold_name in ("train", "validation", "test"):
        assert splits_a[fold_name].height > 0
        for z_col in (
            "sector_z_cash_conversion_cycle",
            "sector_z_asset_turnover",
            "sector_z_debt_to_equity",
            "sector_z_ebit_margin",
        ):
            assert np.allclose(
                splits_a[fold_name][z_col].to_numpy(),
                splits_b[fold_name][z_col].to_numpy(),
                atol=1e-12,
                equal_nan=True,
            )

    issuers_2017 = issuers_2025_a.with_columns(
        pl.lit(date(2017, 1, 1)).alias("industry_snapshot_date")
    )
    splits_hist = build_pit_ml_splits(
        all_facts,
        issuers_2017,
        validation_start=date(2021, 12, 1),
        test_start=date(2023, 3, 1),
        as_of=date(2025, 3, 1),
    )
    assert (
        resolve_peer_standardization_mode(
            splits_hist["train"], splits_hist["validation"]
        )
        == "PIT_ICB"
    )


def test_margin_platt_calibrator_oof_raw_margin_single_group_fallback_and_min_positive_issuers() -> (
    None
):
    from finintel.ml.supervised.train import (
        BASE_FEATURES,
        MarginPlattCalibrator,
        train_distress_model,
    )
    from finintel.ml.unsupervised import InsufficientModelData

    # 1. Direct test on MarginPlattCalibrator: degenerate inverted margins (w <= 0)
    # must trigger monotone MLE intercept-only fallback (slope == 1.0) matching validation prevalence
    raw_margins_deg = np.array([-2.0, -1.5, -1.0, -0.5, 0.5, 1.0, 1.5, 2.0])
    y_deg = np.array([1, 1, 1, 0, 0, 0, 0, 0], dtype=np.int8)
    cal_deg = MarginPlattCalibrator(classifier=None).fit_margins(raw_margins_deg, y_deg)
    assert cal_deg.slope == pytest.approx(1.0)
    probs_deg = cal_deg.predict_margin_proba(raw_margins_deg)[:, 1]
    assert np.all(np.diff(probs_deg) > 0.0)
    assert float(np.mean(probs_deg)) == pytest.approx(float(np.mean(y_deg)), abs=1e-4)

    # 1b. Shifted raw_margin where LogisticRegression slope exceeds PLATT_MAX_SLOPE (5.0):
    # _fit_platt_params clamps slope=5.0 (H-07 [0.15, 5.0]) and solves centered 1D MLE intercept.
    rng_shift = np.random.default_rng(0)
    shifted_margins = np.concatenate(
        [
            rng_shift.uniform(-4.5, -4.05, size=2500),
            rng_shift.uniform(-3.95, -3.5, size=2500),
        ]
    )
    y_shift = np.array([0] * 2500 + [1] * 2500, dtype=np.int8)
    cal_shift = MarginPlattCalibrator(classifier=None).fit_margins(
        shifted_margins, y_shift
    )
    assert cal_shift.slope == pytest.approx(5.0)
    assert cal_shift.intercept > 17.0
    probs_shift = cal_shift.predict_margin_proba(shifted_margins)[:, 1]
    assert float(np.mean(probs_shift)) == pytest.approx(0.5, abs=5e-3)

    # 2. Single positive group in validation (n_pos_groups == 1) falls back cleanly to StratifiedKFold
    def make_fold(
        n_rows: int,
        seed: int,
        *,
        pos_issuers: list[str],
    ) -> pl.DataFrame:
        rng = np.random.default_rng(seed)
        targets = [1 if i < len(pos_issuers) else 0 for i in range(n_rows)]
        issuers = [
            pos_issuers[i] if i < len(pos_issuers) else f"NEG_{i:03d}"
            for i in range(n_rows)
        ]
        feature_cols = {
            name: [
                (-0.15 if targets[i] == 1 else 0.12) + float(rng.normal(0.0, 0.03))
                if name == "roa"
                else (3.5 if targets[i] == 1 else 0.6) + float(rng.normal(0.0, 0.1))
                if name == "debt_to_equity"
                else float(rng.normal(0.5, 0.2))
                for i in range(n_rows)
            ]
            for name in BASE_FEATURES
        }
        return pl.DataFrame(
            {
                **feature_cols,
                "issuer_id": issuers,
                "accounting_regime": ["CORPORATE"] * n_rows,
                "statement_scope": ["CONSOLIDATED"] * n_rows,
                "taxonomy_version": ["VAS_TT200_2014"] * n_rows,
                "_period_available_at": [date(2022, 1, 1)] * n_rows,
                "distress_target": targets,
            }
        )

    train_3pos = make_fold(40, 10, pos_issuers=["POS_A", "POS_B", "POS_C", "POS_A"])
    valid_1group = make_fold(24, 20, pos_issuers=["VAL_ONLY_ONE", "VAL_ONLY_ONE"])
    trained = train_distress_model(train_3pos, valid_1group)
    assert 0.15 <= trained.platt_slope <= 5.0
    assert len(trained.calibrated.oof_probabilities) == 24

    # 3. Fail-fast when unique positive issuers in train < min_unique_positive_issuers (3)
    train_2pos = make_fold(40, 30, pos_issuers=["POS_A", "POS_B", "POS_A", "POS_B"])
    with pytest.raises(InsufficientModelData, match="unique positive issuers"):
        train_distress_model(train_2pos, valid_1group, min_unique_positive_issuers=3)


def test_mode_b_regulatory_distress_offline_contract_delisting_and_right_censoring(
    tmp_path: Path,
) -> None:
    from finintel.pipeline.analytics import AnalyticsHandler
    from finintel.pipeline.runner import RunRequest, Stage, StepContext
    from finintel.pipeline.state_store import StateStore

    base = _periods().with_columns(pl.lit(100.0).alias("BS_TOTAL_EQUITY"))

    # (a) Missing or invalid regulatory_events raises ContractViolationError
    with pytest.raises(ContractViolationError, match="regulatory_events"):
        build_distress_targets(base, mode="REGULATORY_DISTRESS", regulatory_events=None)

    bad_events = pl.DataFrame(
        {
            "issuer_id": ["ABC"],
            "event_date": [date(2021, 5, 1)],
            "event_type": ["UNKNOWN_EVENT"],
        }
    )
    with pytest.raises(ContractViolationError, match="event_type"):
        build_distress_targets(
            base, mode="REGULATORY_DISTRESS", regulatory_events=bad_events
        )

    # (b) FORCED_DELISTING at a_t + 180d produces distress_target == 1 even when post-delisting
    # quarters t+3 and t+4 are absent, (c) multiple events in 1 year do not duplicate rows,
    # and (B-01) label_available_at is symmetrically locked to a_t + 365d for y=1 as well as y=0.
    delisted_periods = base.filter(
        (pl.col("fiscal_year") == 2020)
        | ((pl.col("fiscal_year") == 2021) & pl.col("fiscal_quarter").is_in([1, 2]))
    )
    multi_events = pl.DataFrame(
        {
            "issuer_id": ["ABC", "ABC"],
            "event_date": [date(2021, 4, 15), date(2021, 6, 20)],
            "event_type": ["CONTROL", "FORCED_DELISTING"],
        }
    )
    out_delist = build_distress_targets(
        delisted_periods,
        mode="REGULATORY_DISTRESS",
        as_of=date(2022, 3, 1),
        regulatory_events=multi_events,
    )
    assert out_delist.height == delisted_periods.height
    q4_2020 = out_delist.filter(
        (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert q4_2020["distress_target"] == 1
    assert q4_2020["label_available_at"] == date(2021, 12, 31)

    # (d) Symmetric right-censoring lock (B-01) on BOTH y=1 and y=0:
    # For 2020-Q4 (a_t = 2020-12-31), horizon_end = a_t + 365d = 2021-12-31.
    # If as_of = 2021-10-01, BOTH negative AND positive rows (even with event on 2021-04-15)
    # stay censored (distress_target is None) because a_t + 365d > as_of.
    early_filings = base.with_columns(
        pl.when(pl.col("fiscal_year") == 2021)
        .then(pl.lit(date(2021, 9, 1)))
        .otherwise(pl.col("_period_available_at"))
        .alias("_period_available_at"),
        pl.when((pl.col("fiscal_year") == 2021) & (pl.col("fiscal_quarter") == 4))
        .then(pl.lit(date(2021, 9, 1)))
        .otherwise(pl.col("period_end_date"))
        .alias("period_end_date"),
    )
    empty_events = pl.DataFrame(
        schema={"issuer_id": pl.String, "event_date": pl.Date, "event_type": pl.String}
    )
    censored_neg = build_distress_targets(
        early_filings,
        mode="REGULATORY_DISTRESS",
        as_of=date(2021, 10, 1),
        regulatory_events=empty_events,
    )
    q4_censored = censored_neg.filter(
        (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert q4_censored["distress_target"] is None
    assert q4_censored["label_available_at"] is None

    censored_pos = build_distress_targets(
        delisted_periods,
        mode="REGULATORY_DISTRESS",
        as_of=date(2021, 10, 1),
        regulatory_events=multi_events,
    )
    q4_censored_pos = censored_pos.filter(
        (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert q4_censored_pos["distress_target"] is None
    assert q4_censored_pos["label_available_at"] is None

    uncensored = build_distress_targets(
        early_filings,
        mode="REGULATORY_DISTRESS",
        as_of=date(2022, 1, 15),
        regulatory_events=empty_events,
    )
    q4_uncensored = uncensored.filter(
        (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert q4_uncensored["distress_target"] == 0
    assert q4_uncensored["label_available_at"] == date(2021, 12, 31)

    # (d2) Pure exogenous Mode B decouples label provenance from future BCTC STATUTORY_LAG
    # and enforces symmetric label_available_at = a_t + 365d regardless of Mode A future filings.
    stat_lag_periods = _periods().with_columns(
        pl.when((pl.col("fiscal_year") == 2021) & (pl.col("fiscal_quarter") == 4))
        .then(pl.lit(date(2021, 10, 1)))
        .otherwise(pl.col("_period_available_at"))
        .alias("_period_available_at"),
        pl.when((pl.col("fiscal_year") == 2021) & (pl.col("fiscal_quarter") == 4))
        .then(pl.lit(False))
        .otherwise(pl.lit(True))
        .alias("_period_exact_filing"),
        pl.lit(True).alias("_exact_filing_basis"),
    )
    late_reg_events = pl.DataFrame(
        {
            "issuer_id": ["ABC"],
            "event_date": [date(2021, 11, 15)],
            "event_type": ["CONTROL"],
        }
    )
    late_reg_out = build_distress_targets(
        stat_lag_periods,
        mode="REGULATORY_DISTRESS",
        as_of=date(2022, 1, 1),
        regulatory_events=late_reg_events,
    )
    q4_late_reg = late_reg_out.filter(
        (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert q4_late_reg["distress_target"] == 1
    assert q4_late_reg["label_available_at"] == date(2021, 12, 31)
    assert q4_late_reg["_label_exact_filing_basis"] is True
    # Because label_available_at = 2021-12-31 > validation_start = 2021-11-01, B-01 purges it
    # from train symmetrically (no 100% prevalence cliff before cutoff).
    split_strict_reg = purged_walk_forward_split(
        late_reg_out,
        validation_start=date(2021, 11, 1),
        test_start=date(2022, 1, 1),
        require_exact_filing=True,
    )
    assert (
        split_strict_reg["train"]
        .filter((pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 4))
        .height
        == 0
    )

    early_reg_events = pl.DataFrame(
        {
            "issuer_id": ["ABC"],
            "event_date": [date(2021, 8, 1)],
            "event_type": ["CONTROL"],
        }
    )
    early_reg_out = build_distress_targets(
        stat_lag_periods,
        mode="REGULATORY_DISTRESS",
        as_of=date(2022, 1, 1),
        regulatory_events=early_reg_events,
    )
    q4_early_reg = early_reg_out.filter(
        (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert q4_early_reg["distress_target"] == 1
    assert q4_early_reg["label_available_at"] == date(2021, 12, 31)
    assert q4_early_reg["_label_exact_filing_basis"] is True

    # (e) Path-confinement and end-to-end AnalyticsHandler forwarding for ml.* options
    import json

    from finintel.storage.schemas import ML_SCORES_TABLE
    from tests.unit.test_layer4_analytics import facts

    state = StateStore(tmp_path / "metadata/state.sqlite")
    state.initialize()
    handler = AnalyticsHandler(
        state,
        tmp_path,
        {
            "ml": {
                "enabled": True,
                "approved_target": True,
                "validation_start": "2021-12-01",
                "test_start": "2023-03-01",
                "target_mode": "REGULATORY_DISTRESS",
                "regulatory_events_path": "../outside.parquet",
            }
        },
    )
    for bad_path in (
        "../outside.parquet",
        str((tmp_path / "abs.parquet").resolve()),
        "/outside.parquet",
        r"\outside.parquet",
        "C:outside.parquet",
    ):
        with pytest.raises(ContractViolationError, match="escapes|relative"):
            handler._confined_ml_input_path(bad_path, "ml.regulatory_events_path")

    def make_e2e_issuer_facts(idx: int) -> pl.DataFrame:
        periods_map: dict[tuple[int, int | None], dict[str, float | None]] = {}
        for year in range(2017, 2025):
            for q in range(1, 5):
                scale = 1.0 + 0.03 * idx + 0.01 * q
                periods_map[(year, q)] = {
                    "BS_TOTAL_ASSETS": 1000.0 * scale,
                    "BS_TOTAL_EQUITY": (450.0 - 8.0 * (idx % 7)) * scale,
                    "BS_MINORITY_INTEREST": 10.0 * scale,
                    "BS_TOTAL_LIABILITIES": (550.0 + 8.0 * (idx % 7)) * scale,
                    "BS_CURRENT_ASSETS": (360.0 + 5.0 * (idx % 5)) * scale,
                    "BS_CURRENT_LIABILITIES": (200.0 + 4.0 * (idx % 4)) * scale,
                    "BS_CASH_AND_EQUIVALENTS": (80.0 + 3.0 * (idx % 6)) * scale,
                    "BS_SHORT_TERM_BORROWINGS": (120.0 + 6.0 * (idx % 5)) * scale,
                    "BS_LONG_TERM_BORROWINGS": (90.0 + 4.0 * (idx % 3)) * scale,
                    "BS_RETAINED_EARNINGS": (150.0 - 5.0 * (idx % 6)) * scale,
                    "BS_ORDINARY_SHARES_CAPITAL": 200_000.0,
                    "BS_TREASURY_SHARES": 0.0,
                    "BS_INVENTORIES": (70.0 + 2.0 * (idx % 5)) * scale,
                    "BS_TRADE_RECEIVABLES_ST": (95.0 + 3.0 * (idx % 4)) * scale,
                    "BS_TRADE_PAYABLES_ST": (65.0 + 2.0 * (idx % 3)) * scale,
                    "BS_TANGIBLE_FIXED_ASSETS": 300.0 * scale,
                    "IS_NET_REVENUE": (260.0 + 7.0 * (idx % 6)) * scale,
                    "IS_COST_OF_GOODS_SOLD": (160.0 + 4.0 * (idx % 5)) * scale,
                    "IS_GROSS_PROFIT": (100.0 + 3.0 * (idx % 6)) * scale,
                    "IS_OPERATING_PROFIT": (42.0 - 2.0 * (idx % 5)) * scale,
                    "IS_PROFIT_BEFORE_TAX": (38.0 - 2.0 * (idx % 5)) * scale,
                    "IS_INTEREST_EXPENSE": (4.0 + 0.5 * (idx % 4)) * scale,
                    "IS_CURRENT_INCOME_TAX": 7.0 * scale,
                    "IS_DEFERRED_INCOME_TAX": 0.5 * scale,
                    "IS_NET_INCOME": (30.5 - 2.0 * (idx % 5)) * scale,
                    "IS_NET_INCOME_PARENT": (28.0 - 2.0 * (idx % 5)) * scale,
                    "IS_SELLING_EXPENSES": 12.0 * scale,
                    "IS_ADMINISTRATIVE_EXPENSES": 15.0 * scale,
                    "CF_OPERATING_CASH_FLOW": (34.0 - 2.5 * (idx % 5)) * scale,
                    "CF_CAPITAL_EXPENDITURE": -12.0 * scale,
                    "CF_DEPRECIATION_AMORTIZATION": 8.0 * scale,
                    "CF_EQUITY_ISSUANCE_PROCEEDS": 0.0,
                }
        return facts(periods_map, issuer_id=f"REG_{idx:02d}").with_columns(
            pl.lit("EXACT_FILING").alias("published_at_basis"),
            pl.col("effective_available_at").alias("published_at"),
        )

    curated = tmp_path / "curated"
    curated.mkdir()
    pl.concat([make_e2e_issuer_facts(i) for i in range(12)]).write_parquet(
        curated / "financial_facts.parquet"
    )
    pl.DataFrame(
        {
            "issuer_id": [f"REG_{i:02d}" for i in range(12)],
            "ticker": [f"R{i:02d}" for i in range(12)],
            "accounting_regime": ["CORPORATE"] * 12,
            "icb_code_level2": ["2000" if i < 6 else "3000" for i in range(12)],
            "icb_code_level3": ["201010" if i < 6 else "301010" for i in range(12)],
            "industry_snapshot_date": [date(2017, 1, 1)] * 12,
        }
    ).write_parquet(curated / "issuers.parquet")
    pl.DataFrame(
        {
            "issuer_id": [
                "REG_00",
                "REG_01",
                "REG_02",
                "REG_03",
                "REG_04",
                "REG_05",
            ],
            "event_date": [
                date(2021, 5, 1),
                date(2021, 6, 1),
                date(2021, 7, 1),
                date(2022, 6, 1),
                date(2022, 7, 1),
                date(2024, 6, 1),
            ],
            "event_type": [
                "CONTROL",
                "TRADING_RESTRICTION",
                "SUSPENSION",
                "CONTROL",
                "FORCED_DELISTING",
                "ADVERSE_OR_DISCLAIMER_AUDIT",
            ],
        }
    ).write_parquet(curated / "regulatory_events.parquet")

    req = RunRequest(source="file")
    state.start_run("run_mode_b_e2e", "CLI_MANUAL", req.log_arguments(), None)
    AnalyticsHandler(
        state,
        tmp_path,
        {
            "as_of": "2025-03-01",
            "ml": {
                "enabled": True,
                "approved_target": True,
                "validation_start": "2021-12-01",
                "test_start": "2023-03-01",
                "scored_taxonomies": ["VAS_TT200_2014", "VAS_TT99_2025"],
                "feature_set": "sector_adjusted",
                "target_mode": "REGULATORY_DISTRESS",
                "regulatory_events_path": "curated/regulatory_events.parquet",
            },
        },
    )(StepContext("run_mode_b_e2e", Stage.ANALYTICS, req, (), None))

    batch = curated / "analytics/run_mode_b_e2e"
    assert (batch / ML_SCORES_TABLE).is_file()
    manifest = json.loads((batch / "_ml_run_manifest.json").read_text(encoding="utf-8"))
    report = json.loads(
        (batch / "model_evaluation_report.json").read_text(encoding="utf-8")
    )
    assert manifest["target_mode"] == "REGULATORY_DISTRESS"
    assert manifest["feature_set"] == "sector_adjusted"
    assert manifest["scored_taxonomies"] == ["VAS_TT200_2014", "VAS_TT99_2025"]
    assert report["target_mode"] == "REGULATORY_DISTRESS"
    assert manifest["split_counts"]["train"]["positive_labels"] > 0
    assert manifest["split_counts"]["validation"]["positive_labels"] > 0


def test_treeshap_waterfall_other_features_delta_and_vietnamese_display_names() -> None:
    import json

    from numpy.typing import NDArray

    from finintel.ml.supervised.explain import explain_distress
    from finintel.ml.supervised.train import (
        SECTOR_ADJUSTED_FEATURES,
        score_distress_model,
        train_distress_model,
    )

    def make_20feat_fold(n_rows: int, seed: int) -> pl.DataFrame:
        rng = np.random.default_rng(seed)
        cols: dict[str, list[float]] = {}
        latent: NDArray[np.float64] = np.zeros(n_rows, dtype=np.float64)
        for idx, feat in enumerate(SECTOR_ADJUSTED_FEATURES):
            vals = rng.normal(0.0, 1.0, size=n_rows)
            cols[feat] = vals.tolist()
            sign = 1.0 if idx % 2 == 0 else -1.0
            latent = latent + sign * (0.4 + 0.05 * idx) * vals
        targets = (latent > np.median(latent)).astype(int).tolist()
        return pl.DataFrame(
            {
                **cols,
                "issuer_id": [f"ISS_{i:03d}" for i in range(n_rows)],
                "accounting_regime": ["CORPORATE"] * n_rows,
                "statement_scope": ["CONSOLIDATED"] * n_rows,
                "period_type": ["QUARTER"] * n_rows,
                "period_end_date": [date(2023, 12, 31)] * n_rows,
                "filing_id": [f"FIL_{seed}_{i:03d}" for i in range(n_rows)],
                "taxonomy_version": ["VAS_TT200_2014"] * n_rows,
                "_period_available_at": [date(2023, 12, 31)] * n_rows,
                "distress_target": targets,
            }
        )

    train_df = make_20feat_fold(240, 101)
    valid_df = make_20feat_fold(120, 202)
    eval_df = make_20feat_fold(15, 303)
    trained = train_distress_model(
        train_df, valid_df, features=SECTOR_ADJUSTED_FEATURES
    )
    explained = explain_distress(trained, eval_df)
    scored = score_distress_model(trained, eval_df)

    for idx, payload_str in enumerate(explained["shap_top_drivers_json"].to_list()):
        payload = json.loads(payload_str)
        assert "other_features_delta" in payload
        inc = payload["top_risk_increasing"]
        dec = payload["top_risk_decreasing"]
        total = (
            payload["base_value"]
            + sum(d["shap_delta"] for d in inc)
            + sum(d["shap_delta"] for d in dec)
            + payload["other_features_delta"]
        )
        assert total == pytest.approx(scored["distress_risk_score"][idx], abs=1e-6)
        assert total == pytest.approx(payload["prediction_score"], abs=1e-6)
        for driver in inc + dec:
            assert driver["feature"] in SECTOR_ADJUSTED_FEATURES
            assert (
                isinstance(driver["display_name_vn"], str) and driver["display_name_vn"]
            )
            if driver["feature"].startswith("sector_z_"):
                assert driver["peer_median"] == pytest.approx(0.0)
                assert (
                    "chuẩn hóa toàn thị trường (Z-Score)" in driver["display_name_vn"]
                )
                assert "chuẩn hóa ngành" not in driver["display_name_vn"]

    explained_icb = explain_distress(
        trained, eval_df, peer_standardization_mode="PIT_ICB"
    )
    sector_z_icb_names = [
        drv["display_name_vn"]
        for payload_str in explained_icb["shap_top_drivers_json"].to_list()
        for drv in (
            json.loads(payload_str)["top_risk_increasing"]
            + json.loads(payload_str)["top_risk_decreasing"]
        )
        if drv["feature"].startswith("sector_z_")
    ]
    assert sector_z_icb_names
    assert all("chuẩn hóa ngành (Z-Score)" in name for name in sector_z_icb_names)


def test_synthesize_issuers_from_candidates_uses_real_collection_date_not_2016(
    tmp_path: Path,
) -> None:
    import importlib.util
    import json
    import os
    import sys
    from datetime import UTC, datetime

    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "migrate_universe_to_v2.py"
    )
    module_name = "migrate_universe_to_v2_test"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise

    candidates_path = tmp_path / "universe_candidates.json"
    candidates_path.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "issuer_id": "FPT",
                        "ticker": "FPT",
                        "exchange": "HOSE",
                        "company_name_vn": "CTCP FPT",
                        "accounting_regime": "CORPORATE",
                        "icb_code_level2": "technology",
                        "icb_code_level3": "software",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    mtime_ts = datetime(2026, 11, 19, 13, 57, 35, tzinfo=UTC).timestamp()
    os.utime(candidates_path, (mtime_ts, mtime_ts))

    df_from_mtime = mod._synthesize_issuers_from_candidates(candidates_path, ["FPT"])
    assert df_from_mtime["industry_snapshot_date"].to_list() == [date(2026, 11, 19)]
    assert df_from_mtime["industry_snapshot_date"].item() != date(2016, 1, 1)

    candidates_with_meta = tmp_path / "universe_candidates_meta.json"
    candidates_with_meta.write_text(
        json.dumps(
            {
                "collected_at": "2026-10-15T08:30:00Z",
                "candidates": [
                    {
                        "issuer_id": "HPG",
                        "ticker": "HPG",
                        "exchange": "HOSE",
                        "company_name_vn": "CTCP Tập đoàn Hòa Phát",
                        "accounting_regime": "CORPORATE",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    df_from_meta = mod._synthesize_issuers_from_candidates(
        candidates_with_meta, ["HPG"]
    )
    assert df_from_meta["industry_snapshot_date"].to_list() == [date(2026, 10, 15)]


def test_ml04_gate_ml2_symmetric_censoring_continuous_state_first_onset_and_zero_ttm_overlap() -> (
    None
):
    """Prove GATE-ML-2 (B-01 + H-05 + Mode A 0% TTM overlap + Mode B auto-activation)."""
    quarter_ends = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    rows = []
    for issuer in ("HBC_SIM", "HNG_SIM", "HEALTHY_GAP"):
        for year in range(2020, 2026):
            for quarter in range(1, 5):
                # HEALTHY_GAP is missing future quarters in 2024 and has null roa in 2023-Q2
                if issuer == "HEALTHY_GAP" and year == 2024:
                    continue
                month, day = quarter_ends[quarter]
                p_end = date(year, month, day)
                avail = date(
                    year + (1 if month == 12 else 0),
                    1 if month == 12 else month + 1,
                    25,
                )
                rows.append(
                    {
                        "issuer_id": issuer,
                        "statement_scope": "CONSOLIDATED",
                        "period_type": "QUARTER",
                        "fiscal_year": year,
                        "fiscal_quarter": quarter,
                        "period_end_date": p_end,
                        "accounting_regime": "CORPORATE",
                        "BS_TOTAL_EQUITY": 500.0,
                        "parent_equity": 500.0,
                        "interest_coverage": 0.2 if issuer == "HBC_SIM" else 4.0,
                        "CF_OPERATING_CASH_FLOW": -10.0
                        if issuer == "HBC_SIM"
                        else 50.0,
                        "current_ratio": 0.7 if issuer == "HBC_SIM" else 2.0,
                        "roa": None
                        if (issuer == "HEALTHY_GAP" and year == 2023 and quarter == 2)
                        else (-0.15 if issuer == "HBC_SIM" else 0.08),
                        "_period_available_at": avail,
                    }
                )
    panel = pl.DataFrame(rows)

    # HBC_SIM: CONTROL starts 2022-04-10 (FIRST_ENTRY), persists > 2 years until FORCED_DELISTING
    # on 2024-07-26 (ESCALATION, resolution_date=None).
    # HNG_SIM: CONTROL 2021-04-10..2022-04-10 (resolved), then relapse FIRST_ENTRY on 2024-04-10.
    events = pl.DataFrame(
        {
            "issuer_id": ["HBC_SIM", "HBC_SIM", "HNG_SIM", "HNG_SIM"],
            "event_date": [
                date(2022, 4, 10),
                date(2024, 7, 26),
                date(2021, 4, 10),
                date(2024, 4, 10),
            ],
            "effective_from": [
                date(2022, 4, 10),
                date(2024, 7, 26),
                date(2021, 4, 10),
                date(2024, 4, 10),
            ],
            "resolution_date": [
                date(2024, 7, 26),
                None,
                date(2022, 4, 10),
                None,
            ],
            "event_type": [
                "CONTROL",
                "FORCED_DELISTING",
                "CONTROL",
                "CONTROL",
            ],
            "event_stage": [
                "FIRST_ENTRY",
                "ESCALATION",
                "FIRST_ENTRY",
                "FIRST_ENTRY",
            ],
        }
    )

    # 1. Auto-activation of Mode B as Primary Target when regulatory_events is supplied (mode omitted)
    out = build_distress_targets(
        panel, as_of=date(2025, 12, 31), regulatory_events=events
    )

    # 2. H-05 Continuous distress state: HBC_SIM stays already_distressed_at_t == True in Year 2 & Year 3
    # (2023-Q2..2025-Q1), even > 365d after initial 2022-04-10 CONTROL, and NEVER triggers false onset for ESCALATION.
    hbc_2021_q4 = out.filter(
        (pl.col("issuer_id") == "HBC_SIM")
        & (pl.col("fiscal_year") == 2021)
        & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    # Even though HBC_SIM has weak financial ratios at 2021-Q4, pure Mode B keeps it eligible for onset!
    assert hbc_2021_q4["already_distressed_at_t"] is False
    assert hbc_2021_q4["distress_target"] == 1
    assert hbc_2021_q4["label_available_at"] == date(2023, 1, 25)

    hbc_year2 = out.filter(
        (pl.col("issuer_id") == "HBC_SIM")
        & (pl.col("fiscal_year") == 2023)
        & (pl.col("fiscal_quarter") == 3)
    ).row(0, named=True)
    # 2023-Q3 available 2023-10-25 is > 365d after 2022-04-10, yet inside [2022-04-10, 2024-07-26)
    assert hbc_year2["already_distressed_at_t"] is True
    # FORCED_DELISTING on 2024-07-26 is ESCALATION, NOT FIRST_ENTRY -> distress_target == 0
    assert hbc_year2["distress_target"] == 0

    hbc_after_delist = out.filter(
        (pl.col("issuer_id") == "HBC_SIM")
        & (pl.col("fiscal_year") == 2024)
        & (pl.col("fiscal_quarter") == 3)
    ).row(0, named=True)
    # resolution_date is None on FORCED_DELISTING -> stays continuously distressed forever
    assert hbc_after_delist["already_distressed_at_t"] is True

    # 3. H-05 Recovery & Relapse: HNG_SIM exits distress on resolution_date=2022-04-10,
    # becomes eligible again in 2022-Q2..2023-Q4, and predicts the 2024-04-10 relapse FIRST_ENTRY.
    hng_active = out.filter(
        (pl.col("issuer_id") == "HNG_SIM")
        & (pl.col("fiscal_year") == 2021)
        & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert hng_active["already_distressed_at_t"] is True

    hng_recovered = out.filter(
        (pl.col("issuer_id") == "HNG_SIM")
        & (pl.col("fiscal_year") == 2022)
        & (pl.col("fiscal_quarter") == 2)
    ).row(0, named=True)
    assert hng_recovered["already_distressed_at_t"] is False
    assert hng_recovered["distress_target"] == 0

    hng_relapse_lead = out.filter(
        (pl.col("issuer_id") == "HNG_SIM")
        & (pl.col("fiscal_year") == 2023)
        & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert hng_relapse_lead["already_distressed_at_t"] is False
    assert hng_relapse_lead["distress_target"] == 1

    # 4. H-05 Decoupled pure exogenous negative_verified: HEALTHY_GAP is missing all of 2024
    # and has null roa in 2023-Q2, yet still gets verified y=0 when a_t + 365d <= as_of.
    healthy_2023_q3 = out.filter(
        (pl.col("issuer_id") == "HEALTHY_GAP")
        & (pl.col("fiscal_year") == 2023)
        & (pl.col("fiscal_quarter") == 3)
    ).row(0, named=True)
    assert healthy_2023_q3["distress_target"] == 0
    assert healthy_2023_q3["label_available_at"] == date(2024, 10, 24)

    # 5. B-01 Symmetric Horizon Completeness at historical cutoff as_of = 2022-07-01:
    # At 2021-Q4 (a_t = 2022-01-25), a_t + 365d = 2023-01-25 > 2022-07-01.
    # Even though HBC_SIM already entered CONTROL on 2022-04-10 (<= 2022-07-01),
    # BOTH HBC_SIM (y=1 candidate) and HEALTHY_GAP (y=0 candidate) MUST be censored to None!
    pit_2022 = build_distress_targets(
        panel, as_of=date(2022, 7, 1), regulatory_events=events
    )
    for iss in ("HBC_SIM", "HEALTHY_GAP"):
        r = pit_2022.filter(
            (pl.col("issuer_id") == iss)
            & (pl.col("fiscal_year") == 2021)
            & (pl.col("fiscal_quarter") == 4)
        ).row(0, named=True)
        assert r["distress_target"] is None
        assert r["label_available_at"] is None

    # 6. PIT masking of future resolution_date: at as_of = 2022-03-01 (before HNG resolution 2022-04-10),
    # HNG_SIM has no known resolution_date yet.
    pit_before_res = build_distress_targets(
        panel, as_of=date(2022, 3, 1), regulatory_events=events
    )
    hng_2021_q4_pit = pit_before_res.filter(
        (pl.col("issuer_id") == "HNG_SIM")
        & (pl.col("fiscal_year") == 2021)
        & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert hng_2021_q4_pit["already_distressed_at_t"] is True

    # 7. Mode A 0% TTM overlap for liquidity distress:
    # Liquidity bad ONLY at t+1..t+3 (2021-Q1..Q3), healthy at t+4 (2021-Q4) -> 2020-Q4 MUST be y=0!
    liq_overlap = _periods().with_columns(
        pl.lit(100.0).alias("BS_TOTAL_EQUITY"),
        pl.when(
            (pl.col("fiscal_year") == 2021) & pl.col("fiscal_quarter").is_in([1, 2, 3])
        )
        .then(pl.lit(0.4))
        .otherwise(pl.lit(3.0))
        .alias("interest_coverage"),
        pl.when(
            (pl.col("fiscal_year") == 2021) & pl.col("fiscal_quarter").is_in([1, 2, 3])
        )
        .then(pl.lit(-10.0))
        .otherwise(pl.lit(15.0))
        .alias("CF_OPERATING_CASH_FLOW"),
        pl.when(
            (pl.col("fiscal_year") == 2021) & pl.col("fiscal_quarter").is_in([1, 2, 3])
        )
        .then(pl.lit(0.6))
        .otherwise(pl.lit(2.0))
        .alias("current_ratio"),
    )
    mode_a_out = build_distress_targets(liq_overlap, mode="FUNDAMENTAL_DISTRESS")
    assert (
        mode_a_out.filter(
            (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 4)
        )["distress_target"].item()
        == 0
    )
    # At 2020-Q3, t+4 is 2021-Q3 (0% TTM overlap with 2020-Q3) -> MUST be y=1!
    assert (
        mode_a_out.filter(
            (pl.col("fiscal_year") == 2020) & (pl.col("fiscal_quarter") == 3)
        )["distress_target"].item()
        == 1
    )

    # 8. Contract validation rejects inverted resolution_date < effective_from and invalid event_stage
    bad_stage = events.with_columns(pl.lit("INVALID_STAGE").alias("event_stage"))
    with pytest.raises(ContractViolationError, match="event_stage"):
        build_distress_targets(panel, regulatory_events=bad_stage)
    bad_interval = events.with_columns(
        pl.lit(date(2019, 1, 1)).alias("resolution_date")
    )
    with pytest.raises(ContractViolationError, match="resolution_date"):
        build_distress_targets(panel, regulatory_events=bad_interval)


def test_ml06_two_way_purged_oof_fair_three_model_calibration_bss_unseen_bootstrap_and_psi() -> (
    None
):
    """Prove ML-06 (B-02, H-07, M-05, GATE-ML-3..5) on 2-way Train_OOF -> Test."""
    from datetime import timedelta

    from finintel.ml.supervised.temporal_split import purged_two_way_split
    from finintel.ml.supervised.train import (
        BASE_FEATURES,
        LOGIT_CLIP_RANGE,
        PLATT_MAX_SLOPE,
        PLATT_MIN_SLOPE,
        MarginPlattCalibrator,
        _compute_xgb_regularization,
        build_evaluation_report,
        cluster_bootstrap_metrics,
        compute_taxonomy_drift_psi,
        evaluate_distress_models,
        filter_true_unseen_issuers,
        train_distress_model,
    )

    # 1. Anti-overfitting XGBoost invariant: single positive leaf is mathematically forbidden
    for pos, neg in ((4, 36), (14, 1455), (50, 2500), (160, 7000)):
        spw, mcw = _compute_xgb_regularization(pos, neg)
        assert 0.25 * spw < mcw

    # 2. Unpenalized MarginPlattCalibrator (H-07): preserves risk spread on ~2% prevalence
    #    and clamps weak positive slope to PLATT_MIN_SLOPE = 0.15
    rng = np.random.default_rng(77)
    n_imbal = 2000
    margins_imbal = rng.normal(0.0, 1.0, size=n_imbal)
    true_p = 1.0 / (1.0 + np.exp(-(1.8 * margins_imbal - 4.5)))
    y_imbal = (rng.uniform(0.0, 1.0, size=n_imbal) < true_p).astype(np.int8)
    cal_unpen = MarginPlattCalibrator(None).fit_margins(margins_imbal, y_imbal)
    assert PLATT_MIN_SLOPE <= cal_unpen.slope <= PLATT_MAX_SLOPE
    p_pred = cal_unpen.predict_margin_proba(margins_imbal)[:, 1]
    assert float(np.max(p_pred) - np.min(p_pred)) > 0.15
    assert float(np.mean(p_pred)) == pytest.approx(float(np.mean(y_imbal)), abs=0.01)

    weak_margins = np.linspace(-100.0, 100.0, 400)
    y_weak = np.array([0] * 190 + [1] * 10 + [0] * 185 + [1] * 15, dtype=np.int8)
    cal_weak = MarginPlattCalibrator(None).fit_margins(weak_margins, y_weak)
    assert cal_weak.slope == pytest.approx(PLATT_MIN_SLOPE)

    # 3. 2-way Purged Split + inner PURGED_TEMPORAL_OOF + Fair 3-model Platt calibration + BSS
    quarter_ends = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    rows = []
    rng_panel = np.random.default_rng(123)
    for year in range(2018, 2024):
        for q in range(1, 5):
            m, d = quarter_ends[q]
            p_end = date(year, m, d)
            avail = p_end + timedelta(days=30)
            label_avail = avail + timedelta(days=365)
            for iss_idx in range(18):
                issuer = f"CO_{iss_idx:02d}"
                risk_signal = -1.6 + 0.22 * iss_idx + float(rng_panel.normal(0.0, 0.35))
                roa_v = -0.10 * risk_signal + 0.04
                ic_v = -2.0 * risk_signal + 3.5
                dte_v = 1.2 + 0.9 * max(0.0, risk_signal)
                alt_v = 2.2 - 1.1 * risk_signal + float(rng_panel.normal(0.0, 0.2))
                y_v = int(1 if risk_signal > 1.7 else 0)
                row: dict[str, object] = {
                    "issuer_id": issuer,
                    "accounting_regime": "CORPORATE",
                    "statement_scope": "CONSOLIDATED",
                    "period_type": "QUARTER",
                    "fiscal_year": year,
                    "fiscal_quarter": q,
                    "period_end_date": p_end,
                    "filing_id": f"{issuer}_{year}_Q{q}",
                    "taxonomy_version": "VAS_TT200_2014",
                    "_period_available_at": avail,
                    "label_available_at": label_avail,
                    "_exact_filing_basis": True,
                    "_label_exact_filing_basis": True,
                    "already_distressed_at_t": False,
                    "distress_target": y_v,
                }
                for feat in BASE_FEATURES:
                    if feat == "roa":
                        row[feat] = roa_v
                    elif feat == "interest_coverage":
                        row[feat] = ic_v
                    elif feat == "debt_to_equity":
                        row[feat] = dte_v
                    elif feat == "altman_z_double_prime":
                        row[feat] = alt_v
                    else:
                        row[feat] = float(rng_panel.normal(0.5, 0.3))
                rows.append(row)
    panel = pl.DataFrame(rows)

    two_way = purged_two_way_split(
        panel,
        test_start=date(2023, 1, 1),
        test_end=date(2024, 1, 1),
        require_exact_filing=True,
    )
    assert two_way["train"]["label_available_at"].max() <= date(2023, 1, 1)  # type: ignore[operator]
    assert two_way["test"]["period_end_date"].min() >= date(2023, 1, 1)  # type: ignore[operator]

    trained = train_distress_model(
        two_way["train"],
        validation=None,
        features=BASE_FEATURES,
        min_unique_positive_issuers=2,
    )
    assert trained.oof_strategy == "PURGED_TEMPORAL_OOF"
    assert trained.logit_preprocessor is not None
    assert trained.logit_preprocessor.clip_range == LOGIT_CLIP_RANGE
    assert trained.calibrated_altman is not None
    assert trained.calibrated_logit is not None
    assert PLATT_MIN_SLOPE <= trained.platt_slope <= PLATT_MAX_SLOPE
    assert PLATT_MIN_SLOPE <= trained.altman_platt_slope <= PLATT_MAX_SLOPE
    assert PLATT_MIN_SLOPE <= trained.logit_platt_slope <= PLATT_MAX_SLOPE
    assert 15 <= trained.best_n_estimators <= 300
    assert 0.25 * trained.scale_pos_weight < trained.min_child_weight

    # Verify BSS strictly uses train_prevalence, NOT y_test.mean()
    test_eval = evaluate_distress_models(trained, two_way["test"])
    assert test_eval["reference_train_prevalence"] == pytest.approx(
        trained.train_prevalence
    )
    assert test_eval["unique_positive_issuers"] > 0
    for model_name in ("Baseline_1_AltmanZ", "Baseline_2_Logit", "XGBoost"):
        m_dict = test_eval["models"][model_name]
        assert m_dict["brier_skill_score"] is not None
        assert m_dict["recall_at_top_5_percent"] is not None
        assert m_dict["recall_at_top_10_percent"] is not None

    # Exact numerical BSS verification against train_prevalence when test prevalence differs
    from finintel.ml.supervised.train import _metrics

    y_custom = np.array([0, 0, 0, 0, 1, 1, 1, 1], dtype=np.int8)  # test mean = 0.50
    s_custom = np.array([0.05, 0.10, 0.15, 0.20, 0.60, 0.70, 0.80, 0.90])
    train_prev_ref = 0.05
    m_custom = _metrics(y_custom, s_custom, reference_prevalence=train_prev_ref)
    expected_brier = float(np.mean((y_custom - s_custom) ** 2))
    expected_clim = float(np.mean((y_custom - train_prev_ref) ** 2))
    assert m_custom["brier_skill_score"] == pytest.approx(
        1.0 - expected_brier / expected_clim
    )

    report = build_evaluation_report(
        trained, train=two_way["train"], validation=None, test=two_way["test"]
    )
    assert report["validation"]["evaluation_role"] == "INNER_PURGED_OOF_CALIBRATION"
    assert "in_sample_models" in report["train_summary"]

    # 4. True Unseen Issuers removes BOTH y=0 and y=1 of prior-event issuers
    prior_reg = pl.DataFrame(
        {
            "issuer_id": ["CO_17", "CO_00"],
            "event_date": [date(2022, 6, 1), date(2023, 6, 1)],
            "event_type": ["CONTROL", "CONTROL"],
        }
    )
    unseen = filter_true_unseen_issuers(
        two_way["test"], test_start=date(2023, 1, 1), regulatory_events=prior_reg
    )
    assert "CO_17" not in set(unseen["issuer_id"].to_list())
    assert "CO_00" in set(unseen["issuer_id"].to_list())

    # 5. Cluster Bootstrap 95% CI (clustered by issuer_id)
    boot = cluster_bootstrap_metrics(
        two_way["test"]["distress_target"].to_numpy().astype(np.int8),
        np.where(two_way["test"]["distress_target"].to_numpy() == 1, 0.8, 0.1),
        two_way["test"]["issuer_id"].to_numpy(),
        reference_prevalence=trained.train_prevalence,
        n_bootstrap=100,
        seed=42,
    )
    assert boot["n_bootstrap_valid"] > 0
    assert boot["ci95"]["roc_auc"] is not None
    lo, hi = boot["ci95"]["roc_auc"]
    assert 0.0 <= lo <= hi <= 1.0

    # 6. Taxonomy Drift PSI (M-05): ~0 on identical slice, > 0.25 on shifted slice
    same_psi = compute_taxonomy_drift_psi(
        two_way["test"], two_way["test"], features=BASE_FEATURES, result=trained
    )
    assert same_psi["max_feature_psi"] == pytest.approx(0.0, abs=1e-6)
    shifted_test = two_way["test"].with_columns((pl.col("roa") + 0.50).alias("roa"))
    shift_psi = compute_taxonomy_drift_psi(
        two_way["test"], shifted_test, features=BASE_FEATURES, result=trained
    )
    assert "roa" in shift_psi["features_psi_gt_0_25"]
    assert shift_psi["score_drift"]["score_psi"] is not None

    # 7. Regression guard: when earliest temporal inner block has an all-null feature
    #    (e.g. 2016 YoY features), train_distress_model falls back cleanly to issuer-grouped OOF.
    early_null_train = two_way["train"].with_columns(
        pl.when(pl.col("fiscal_year") <= 2020)
        .then(pl.lit(None, dtype=pl.Float64))
        .otherwise(pl.col("revenue_growth_yoy"))
        .alias("revenue_growth_yoy")
    )
    trained_fallback = train_distress_model(
        early_null_train,
        validation=None,
        features=BASE_FEATURES,
        min_unique_positive_issuers=2,
    )
    assert trained_fallback.oof_strategy == "STRATIFIED_GROUP_OOF"
    assert trained_fallback.oof_sample_count > 0
