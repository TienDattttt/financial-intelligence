"""Layer 4B acceptance tests.

Verified Sources:
    docs/layers/LAYER_4_ANALYTICS_AND_ML.md, sections 5.1, 5.2 and 7.2.
    docs/v2_specs/06_STREAM_A4_LAYER4B_ML_REMEDIATION_SECTOR_ADJ_AND_REGULATORY_TARGET.md.
"""

from datetime import date

import numpy as np
import polars as pl
import pytest

from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.ratios import calculate_ratios
from finintel.exceptions import ContractViolationError
from finintel.ml.classical_scores import add_classical_scores
from tests.unit.test_layer4_analytics import facts, issuer


def test_classical_scores_exactness_and_missing_note_evidence() -> None:
    baseline: dict[str, float | None] = {
        "BS_TOTAL_ASSETS": 100,
        "BS_TOTAL_EQUITY": 60,
        "BS_TOTAL_LIABILITIES": 40,
        "BS_CURRENT_ASSETS": 40,
        "BS_CURRENT_LIABILITIES": 20,
        "BS_RETAINED_EARNINGS": 20,
        "BS_LONG_TERM_BORROWINGS": 20,
        "BS_TRADE_RECEIVABLES_ST": 10,
        "BS_TANGIBLE_FIXED_ASSETS": 30,
        "IS_PROFIT_BEFORE_TAX": 10,
        "IS_INTEREST_EXPENSE": 0,
        "IS_NET_REVENUE": 50,
        "IS_GROSS_PROFIT": 15,
        "IS_NET_INCOME": 5,
        "IS_SELLING_EXPENSES": 2,
        "IS_ADMINISTRATIVE_EXPENSES": 3,
        "IS_CONTINUING_OPERATIONS_NET_INCOME": 5,
        "CF_OPERATING_CASH_FLOW": 8,
        "CF_DEPRECIATION_AMORTIZATION": 3,
    }
    current: dict[str, float | None] = {
        **baseline,
        "BS_CURRENT_LIABILITIES": 10,
        "IS_NET_REVENUE": 100,
        "IS_GROSS_PROFIT": 40,
        "IS_NET_INCOME": 10,
        "IS_CONTINUING_OPERATIONS_NET_INCOME": 10,
        "CF_OPERATING_CASH_FLOW": 15,
        "CF_EQUITY_ISSUANCE_PROCEEDS": 0,
        "IS_DILUTIVE_NONCASH_EQUITY_ISSUANCE": 0,
    }

    def score(updates: dict[str, float | None]) -> dict[str, object]:
        source = facts(
            {(2023, None): baseline, (2024, None): baseline, (2025, None): updates}
        )
        periods = prepare_periods(
            source, issuer(), as_of=date(2026, 6, 1), include_ml_inputs=True
        )
        return add_classical_scores(calculate_ratios(periods)).row(-1, named=True)

    row = score(current)
    assert row["altman_z_double_prime"] == pytest.approx(4.867)
    assert row["altman_em_score"] == pytest.approx(8.117)
    assert row["altman_zone"] == "SAFE_ZONE"
    assert row["piotroski_f_score"] == 9
    assert row["piotroski_valid_components_count"] == 9
    assert row["beneish_m8_score"] == pytest.approx(-2.32795)
    assert row["model_role"] == "SCREENING_INDICATOR"
    missing = score(
        {
            **current,
            "CF_DEPRECIATION_AMORTIZATION": None,
            "IS_DILUTIVE_NONCASH_EQUITY_ISSUANCE": None,
        }
    )
    assert missing["beneish_m8_score"] is None
    assert missing["beneish_flag"] == "MISSING_DEPRECIATION_DIRECT_CF"
    assert missing["piotroski_f_score"] is None
    assert missing["piotroski_valid_components_count"] == 8
    assert (
        score({**current, "IS_DILUTIVE_NONCASH_EQUITY_ISSUANCE": 2})[
            "piotroski_f_score"
        ]
        == 8
    )
    assert score({**current, "BS_TOTAL_LIABILITIES": 0})[
        "altman_z_double_prime"
    ] == pytest.approx(13.792)

    fallback = score(
        {
            **current,
            "IS_CONTINUING_OPERATIONS_NET_INCOME": None,
        }
    )
    assert fallback["beneish_m8_score"] == pytest.approx(-2.32795)

    with_charter_shares = facts(
        {
            (2023, None): {**baseline, "BS_ORDINARY_SHARES_CAPITAL": 100_000},
            (2024, None): {**baseline, "BS_ORDINARY_SHARES_CAPITAL": 100_000},
            (2025, None): {
                **current,
                "BS_ORDINARY_SHARES_CAPITAL": 100_000,
                "IS_DILUTIVE_NONCASH_EQUITY_ISSUANCE": None,
            },
        }
    )
    unchanged_row = add_classical_scores(
        calculate_ratios(
            prepare_periods(
                with_charter_shares,
                issuer(),
                as_of=date(2026, 6, 1),
                include_ml_inputs=True,
            )
        )
    ).row(-1, named=True)
    assert unchanged_row["piotroski_valid_components_count"] == 9
    assert unchanged_row["piotroski_f_score"] == 9

    increased_shares_no_cash = facts(
        {
            (2023, None): {**baseline, "BS_ORDINARY_SHARES_CAPITAL": 100_000},
            (2024, None): {**baseline, "BS_ORDINARY_SHARES_CAPITAL": 100_000},
            (2025, None): {
                **current,
                "BS_ORDINARY_SHARES_CAPITAL": 115_000,
                "IS_DILUTIVE_NONCASH_EQUITY_ISSUANCE": None,
            },
        }
    )
    ambiguous_row = add_classical_scores(
        calculate_ratios(
            prepare_periods(
                increased_shares_no_cash,
                issuer(),
                as_of=date(2026, 6, 1),
                include_ml_inputs=True,
            )
        )
    ).row(-1, named=True)
    assert ambiguous_row["piotroski_valid_components_count"] == 8
    assert ambiguous_row["piotroski_f_score"] is None

    cash_issuance_no_note = score(
        {
            **current,
            "CF_EQUITY_ISSUANCE_PROCEEDS": 50,
            "IS_DILUTIVE_NONCASH_EQUITY_ISSUANCE": None,
        }
    )
    assert cash_issuance_no_note["piotroski_valid_components_count"] == 9
    assert cash_issuance_no_note["piotroski_f_score"] == 8


def test_classical_scores_exclude_noncorporate_regimes() -> None:
    source = facts({(2025, None): {"BS_TOTAL_ASSETS": 100}})
    periods = prepare_periods(
        source, issuer(regime="BANK"), as_of=date(2026, 6, 1), include_ml_inputs=True
    )
    assert add_classical_scores(calculate_ratios(periods)).is_empty()


def test_preprocessor_uses_only_training_statistics() -> None:
    from finintel.ml.unsupervised import CrossSectionalPreprocessor

    train = pl.DataFrame(
        {
            "interest_coverage": [1.0, 2.0, 3.0, 4.0, None, None],
            "interest_coverage_flag": ["VALID"] * 4
            + ["NO_INTEREST_EXPENSE", "MISSING_INPUT"],
            "ebit_ttm": [1.0] * 6,
            "roa": [0.1, 0.2, 0.3, None, 0.4, None],
            "icb_code_level2": ["A", "A", "B", "A", "B", "C"],
            "accounting_regime": ["CORPORATE"] * 6,
        }
    )
    processor = CrossSectionalPreprocessor(("interest_coverage", "roa")).fit(train)
    original = processor.transform(train)
    raw = processor.impute(train)
    assert raw["interest_coverage"][4] == pytest.approx(3.85)
    assert raw["interest_coverage_missing"].to_list() == [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    assert raw["roa"][3] == pytest.approx((0.103 + 0.2) / 2)
    assert raw["roa_missing"].to_list() == [0, 0, 0, 1, 0, 1]
    processor.transform(train.with_columns(pl.lit(1e15).alias("roa")))
    assert processor.transform(train).equals(original)


def test_unsupervised_exports_names_and_scales_anomaly_by_training_rank() -> None:
    from finintel.ml.unsupervised import UNSUPERVISED_FEATURE_SET, FinancialArchetypes

    source = pl.DataFrame(
        {
            "accounting_regime": ["CORPORATE"] * 30,
            **{
                name: [float((i * (j + 7) + i * i * (j + 1)) % 97) for i in range(30)]
                for j, name in enumerate(UNSUPERVISED_FEATURE_SET)
            },
        }
    )
    model = FinancialArchetypes().fit(source)
    scored = model.transform(source)
    assert 3 <= len(model.component_names) <= 5
    assert 3 <= scored["cluster_id"].n_unique() <= 6
    assert scored["anomaly_percentile_rank"].max() == 100.0
    assert scored["statistical_outlier_flag"].sum() >= 1
    assert (
        scored.filter(pl.col("statistical_outlier_flag"))["anomaly_flag"]
        .unique()
        .item()
        == "STATISTICAL_MULTIVARIATE_OUTLIER"
    )
    keyed = source.with_columns(
        pl.Series("issuer_id", [f"ISS_{i:02d}" for i in range(30)]),
        pl.lit("CONSOLIDATED").alias("statement_scope"),
        pl.lit("QUARTER").alias("period_type"),
        pl.lit(date(2025, 12, 31)).alias("period_end_date"),
        pl.Series("filing_id", [f"FIL_{i:02d}" for i in range(30)]),
    )
    scored_keyed = model.transform(keyed)
    assert scored_keyed["issuer_id"].to_list() == [f"ISS_{i:02d}" for i in range(30)]
    assert scored_keyed["filing_id"].to_list() == [f"FIL_{i:02d}" for i in range(30)]


def test_preprocessor_clip_range_clips_unsupervised_and_preserves_default_none() -> (
    None
):
    from finintel.ml.unsupervised import CrossSectionalPreprocessor

    train = pl.DataFrame(
        {
            "roa": [0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12],
            "cash_conversion_cycle": [
                30.0,
                35.0,
                40.0,
                45.0,
                50.0,
                55.0,
                60.0,
                5000.0,
            ],
            "accounting_regime": ["CORPORATE"] * 8,
        }
    )
    probe = pl.DataFrame(
        {
            "roa": [0.08],
            "cash_conversion_cycle": [5000.0],
            "accounting_regime": ["CORPORATE"],
        }
    )
    unclipped = CrossSectionalPreprocessor(("roa", "cash_conversion_cycle")).fit(train)
    clipped = CrossSectionalPreprocessor(
        ("roa", "cash_conversion_cycle"), clip_range=(-5.0, 5.0)
    ).fit(train)

    out_unclipped = unclipped.transform(probe)
    out_clipped = clipped.transform(probe)
    assert out_unclipped["cash_conversion_cycle"].item() > 5.0
    assert out_clipped["cash_conversion_cycle"].item() == pytest.approx(5.0)

    with pytest.raises(ContractViolationError, match="clip_range"):
        CrossSectionalPreprocessor(("roa",), clip_range=(5.0, -5.0))


def test_financial_archetypes_unique_cluster_names_including_neutral_and_outlier_resistance() -> (
    None
):
    from finintel.ml.unsupervised import (
        UNSUPERVISED_FEATURE_SET,
        FinancialArchetypes,
        _build_unique_archetype_names,
    )

    width = len(UNSUPERVISED_FEATURE_SET)
    neutral_centers = np.zeros((4, width), dtype=np.float64)
    neutral_centers[1, 0] = 0.10
    neutral_centers[2, 0] = 0.10
    neutral_centers[3, 1] = -0.20
    neutral_names = _build_unique_archetype_names(neutral_centers)
    assert len(neutral_names) == 4
    assert len(set(neutral_names)) == 4

    duplicate_signal_centers = np.zeros((3, width), dtype=np.float64)
    duplicate_signal_centers[0, 0] = 1.5
    duplicate_signal_centers[0, 1] = 1.2
    duplicate_signal_centers[0, 2] = 0.8
    duplicate_signal_centers[1, 0] = 2.0
    duplicate_signal_centers[1, 1] = 1.4
    duplicate_signal_centers[1, 2] = -0.9
    duplicate_signal_centers[2, 0] = 2.0
    duplicate_signal_centers[2, 1] = 1.4
    duplicate_signal_centers[2, 2] = -0.9
    dup_names = _build_unique_archetype_names(duplicate_signal_centers)
    assert len(dup_names) == 3
    assert len(set(dup_names)) == 3

    rows: dict[str, list[float]] = {
        name: [float((i * (j + 5) + (i % 7) * (j + 3)) % 41) for i in range(36)]
        for j, name in enumerate(UNSUPERVISED_FEATURE_SET)
    }
    rows["cash_conversion_cycle"][-1] = 5000.0
    rows["interest_coverage"][-2] = 200.0
    source = pl.DataFrame(
        {
            "accounting_regime": ["CORPORATE"] * 36,
            **rows,
        }
    )
    model = FinancialArchetypes().fit(source)
    assert len(set(model._archetypes)) == len(model._archetypes)
    scaled_matrix = (
        model.preprocessor.transform(source).select(UNSUPERVISED_FEATURE_SET).to_numpy()
    )
    assert float(np.max(np.abs(scaled_matrix))) <= 5.0 + 1e-12
    scored = model.transform(source)
    assert (
        scored["cluster_archetype_name"].n_unique() == scored["cluster_id"].n_unique()
    )


def test_build_ml_artifacts_scores_tt200_and_tt99_quarters_while_keeping_annual_classical_only() -> (
    None
):
    from finintel.ml.tables import build_ml_artifacts

    def make_issuer_facts(idx: int, distressed_year: int | None) -> pl.DataFrame:
        periods_map: dict[tuple[int, int | None], dict[str, float | None]] = {}
        for year in range(2017, 2027):
            q_range = range(1, 4) if year == 2026 else range(1, 5)
            for q in q_range:
                hit = distressed_year == year and q == 3
                scale = 1.0 + 0.03 * idx + 0.01 * q
                interest_key = (
                    "IS_BORROWING_COSTS" if year >= 2026 else "IS_INTEREST_EXPENSE"
                )
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
                    interest_key: (4.0 + 0.5 * (idx % 4)) * scale,
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
            if year in (2024, 2025):
                periods_map[(year, None)] = dict(periods_map[(year, 4)])
        df = facts(periods_map, issuer_id=f"CORP_{idx:02d}")
        return df.with_columns(
            pl.when(pl.col("fiscal_year") >= 2026)
            .then(pl.lit("VAS_TT99_2025"))
            .otherwise(pl.lit("VAS_TT200_2014"))
            .alias("taxonomy_version"),
            pl.lit(True).alias("comparable_across_taxonomy"),
            pl.lit("EXACT_FILING").alias("published_at_basis"),
            pl.col("effective_available_at").alias("published_at"),
        )

    distress_schedule = {0: 2021, 1: 2021, 2: 2021, 3: 2022, 4: 2022, 5: 2024, 6: 2024}
    all_facts = pl.concat(
        [make_issuer_facts(i, distress_schedule.get(i)) for i in range(12)]
    )
    issuers_df = pl.DataFrame(
        {
            "issuer_id": [f"CORP_{i:02d}" for i in range(12)],
            "ticker": [f"C{i:02d}" for i in range(12)],
            "accounting_regime": ["CORPORATE"] * 12,
            "icb_code_level2": ["2000" if i < 6 else "3000" for i in range(12)],
            "icb_code_level3": ["201010" if i < 6 else "301010" for i in range(12)],
            "industry_snapshot_date": [date(2025, 6, 1)] * 12,
        }
    )

    artifacts = build_ml_artifacts(
        all_facts,
        issuers_df,
        as_of=date(2026, 11, 15),
        validation_start=date(2021, 12, 1),
        test_start=date(2023, 3, 1),
        approved_target=True,
        require_exact_filing=True,
    )
    scores = artifacts.scores_table
    tt99_q = scores.filter(
        (pl.col("period_type") == "QUARTER")
        & (pl.col("period_end_date") >= date(2026, 1, 1))
    )
    assert tt99_q.height == 12 * 3
    assert tt99_q["calibrated_distress_prob"].null_count() == 0
    assert tt99_q["pca_pc1"].null_count() == 0
    assert tt99_q["shap_top_drivers_json"].null_count() == 0

    annual_rows = scores.filter(pl.col("period_type") == "ANNUAL")
    assert annual_rows.height == 12 * 2
    assert annual_rows["altman_z_double_prime"].null_count() == 0
    assert annual_rows["pca_pc1"].null_count() == annual_rows.height
    assert annual_rows["calibrated_distress_prob"].null_count() == annual_rows.height
    assert annual_rows["shap_top_drivers_json"].null_count() == annual_rows.height

    assert artifacts.run_manifest["peer_standardization_mode"] == "PIT_REGIME_SYMMETRIC"
    assert artifacts.run_manifest["scored_taxonomies"] == [
        "VAS_TT200_2014",
        "VAS_TT99_2025",
    ]
    assert artifacts.run_manifest["scored_period_types"] == ["QUARTER"]
    assert 0.0 < artifacts.run_manifest["platt_slope"] <= 10.0
