"""Layer 4B acceptance tests for screening models and leakage controls."""

from datetime import date

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
    assert split["train"]["label_available_at"].max() <= date(2022, 1, 1)
    assert split["validation"]["label_available_at"].max() <= date(2022, 10, 1)


def test_split_rejects_overlapping_boundaries() -> None:
    with pytest.raises(ContractViolationError):
        purged_walk_forward_split(
            pl.DataFrame(
                {
                    "period_end_date": [date(2020, 1, 1)],
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
