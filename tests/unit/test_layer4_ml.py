"""Layer 4B acceptance tests."""

from datetime import date

import polars as pl
import pytest

from finintel.analytics.intermediates import prepare_periods
from finintel.analytics.ratios import calculate_ratios
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
