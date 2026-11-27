"""Unit and integration tests for V3-22..V3-24: Analyst Depth (QoQ, Peers, Z-Drivers) & Ops QA."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from finintel.analytics.qoq_and_peers import (
    QoqStatusFlag,
    compute_discrete_qoq_growth,
    compute_statement_row_qoq_map,
    extract_top_anomaly_drivers,
    suggest_similar_peers,
)


def test_discrete_qoq_blocks_tt200_to_tt99_and_non_positive_denominator() -> None:
    """V3-22: QoQ MUST return NULL + MIXED_TAXONOMY across Q4/2025->Q1/2026 and on base <= 0."""
    df = pl.DataFrame(
        {
            "issuer_id": ["FPT"] * 6,
            "statement_scope": ["CONSOLIDATED"] * 5 + ["SEPARATE"],
            "period_type": ["QUARTER"] * 6,
            "fiscal_year": [2025, 2025, 2025, 2026, 2026, 2025],
            "fiscal_quarter": [2, 3, 4, 1, 2, 3],
            "period_start_date": [
                date(2025, 4, 1),
                date(2025, 7, 1),
                date(2025, 10, 1),
                date(2026, 1, 1),
                date(2026, 4, 1),
                date(2025, 7, 1),
            ],
            "period_end_date": [
                date(2025, 6, 30),
                date(2025, 9, 30),
                date(2025, 12, 31),
                date(2026, 3, 31),
                date(2026, 6, 30),
                date(2025, 9, 30),
            ],
            "taxonomy_version": [
                "VAS_TT200_2014",
                "VAS_TT200_2014",
                "VAS_TT200_2014",
                "VAS_TT99_2025",
                "VAS_TT99_2025",
                "VAS_TT200_2014",
            ],
            "IS_NET_REVENUE": [100.0, 125.0, 0.0, 150.0, 180.0, 50.0],
            "IS_NET_INCOME_PARENT": [10.0, -5.0, 20.0, 25.0, 30.0, 4.0],
        }
    )

    out = compute_discrete_qoq_growth(df)
    cons = out.filter(pl.col("statement_scope") == "CONSOLIDATED").sort(
        "period_end_date"
    )
    rows = {r["period_end_date"]: r for r in cons.to_dicts()}

    # Q2/2025: first quarter -> missing prior base
    assert rows[date(2025, 6, 30)]["revenue_growth_qoq"] is None
    assert (
        rows[date(2025, 6, 30)]["revenue_growth_qoq_flag"]
        == QoqStatusFlag.MISSING_PRIOR_QUARTER_BASE.value
    )

    # Q3/2025 vs Q2/2025: same TT200, positive base -> +25%, negative NI base in Q3 doesn't affect Q3 QoQ (Q2 NI=+10)
    assert rows[date(2025, 9, 30)]["revenue_growth_qoq"] == pytest.approx(0.25)
    assert (
        rows[date(2025, 9, 30)]["revenue_growth_qoq_flag"] == QoqStatusFlag.VALID.value
    )
    assert rows[date(2025, 9, 30)]["net_income_parent_growth_qoq"] == pytest.approx(
        -1.5
    )

    # Q4/2025 vs Q3/2025: NI base Q3=-5 <= 0 -> NULL + NON_POSITIVE_BASE_PERIOD
    assert rows[date(2025, 12, 31)]["net_income_parent_growth_qoq"] is None
    assert (
        rows[date(2025, 12, 31)]["net_income_parent_growth_qoq_flag"]
        == QoqStatusFlag.NON_POSITIVE_BASE_PERIOD.value
    )

    # Q1/2026 vs Q4/2025: TT200 -> TT99 boundary -> NULL + MIXED_TAXONOMY (even though Q4 rev was 0)
    assert rows[date(2026, 3, 31)]["revenue_growth_qoq"] is None
    assert (
        rows[date(2026, 3, 31)]["revenue_growth_qoq_flag"]
        == QoqStatusFlag.MIXED_TAXONOMY.value
    )
    assert rows[date(2026, 3, 31)]["net_income_parent_growth_qoq"] is None
    assert (
        rows[date(2026, 3, 31)]["net_income_parent_growth_qoq_flag"]
        == QoqStatusFlag.MIXED_TAXONOMY.value
    )

    # Q2/2026 vs Q1/2026: both TT99 -> +20% revenue, +20% net income
    assert rows[date(2026, 6, 30)]["revenue_growth_qoq"] == pytest.approx(0.20)
    assert rows[date(2026, 6, 30)]["net_income_parent_growth_qoq"] == pytest.approx(
        0.20
    )

    # SEPARATE scope never mixes with CONSOLIDATED
    sep = out.filter(pl.col("statement_scope") == "SEPARATE").row(0, named=True)
    assert sep["revenue_growth_qoq"] is None
    assert (
        sep["revenue_growth_qoq_flag"] == QoqStatusFlag.MISSING_PRIOR_QUARTER_BASE.value
    )


def test_discrete_qoq_rejects_non_consecutive_gap_and_non_quarter_duration() -> None:
    """V3-22: Skipped quarters (> 100 days gap) or YTD/annual durations (>= 101 days) never produce QoQ."""
    df = pl.DataFrame(
        {
            "issuer_id": ["HPG"] * 3,
            "statement_scope": ["CONSOLIDATED"] * 3,
            "period_type": ["QUARTER", "QUARTER", "QUARTER"],
            "fiscal_year": [2025, 2025, 2025],
            "fiscal_quarter": [1, 3, 4],
            "period_start_date": [
                date(2025, 1, 1),
                date(2025, 7, 1),
                date(2025, 1, 1),  # 365 days YTD incorrectly labeled QUARTER
            ],
            "period_end_date": [
                date(2025, 3, 31),
                date(2025, 9, 30),
                date(2025, 12, 31),
            ],
            "taxonomy_version": ["VAS_TT200_2014"] * 3,
            "IS_NET_REVENUE": [100.0, 120.0, 400.0],
            "IS_NET_INCOME_PARENT": [10.0, 12.0, 40.0],
        }
    )
    out = compute_discrete_qoq_growth(df).sort("period_end_date")
    rows = out.to_dicts()
    # Q3/2025 skipped Q2 -> gap ~183 days -> MISSING_PRIOR_QUARTER_BASE
    assert rows[1]["revenue_growth_qoq"] is None
    assert (
        rows[1]["revenue_growth_qoq_flag"]
        == QoqStatusFlag.MISSING_PRIOR_QUARTER_BASE.value
    )
    # Q4/2025 has 365 period_days -> NOT_DISCRETE_QUARTER
    assert rows[2]["revenue_growth_qoq"] is None
    assert (
        rows[2]["revenue_growth_qoq_flag"] == QoqStatusFlag.NOT_DISCRETE_QUARTER.value
    )


def test_statement_row_qoq_map_respects_tt200_tt99_boundary() -> None:
    """V3-22: Statement row QoQ helper computes discrete QoQ and blocks TT200->TT99."""
    periods = [
        {
            "period_end_date": date(2026, 3, 31),
            "fiscal_year": 2026,
            "fiscal_quarter": 1,
            "taxonomy_version": "VAS_TT99_2025",
        },
        {
            "period_end_date": date(2025, 12, 31),
            "fiscal_year": 2025,
            "fiscal_quarter": 4,
            "taxonomy_version": "VAS_TT200_2014",
        },
        {
            "period_end_date": date(2025, 9, 30),
            "fiscal_year": 2025,
            "fiscal_quarter": 3,
            "taxonomy_version": "VAS_TT200_2014",
        },
    ]
    vals, flags = compute_statement_row_qoq_map(
        periods=periods,
        values={"2025-09-30": 100.0, "2025-12-31": 130.0, "2026-03-31": 140.0},
        period_type="QUARTER",
    )
    assert vals["2025-12-31"] == pytest.approx(0.30)
    assert flags["2025-12-31"] == QoqStatusFlag.VALID.value
    assert vals["2026-03-31"] is None
    assert flags["2026-03-31"] == QoqStatusFlag.MIXED_TAXONOMY.value


def test_peer_auto_suggest_icb_l3_closest_4_and_l2_fallback() -> None:
    """V3-22: L3 with 20 peers picks 4 closest by market_cap_vnd; L3 with 2 peers falls back to ICB_LEVEL_2."""
    issuers_rows: list[dict[str, object]] = []
    mart_rows: list[dict[str, object]] = []
    ratios_rows: list[dict[str, object]] = []
    ml_rows: list[dict[str, object]] = []

    # 20 issuers in L3 "1750" (L2 "1700")
    for idx in range(20):
        ticker = f"ST{idx:02d}"
        mcap = float((idx + 1) * 1_000_000_000_000)
        issuers_rows.append(
            {
                "issuer_id": ticker,
                "ticker": ticker,
                "exchange": "HOSE",
                "company_name_vn": f"Thép {ticker}",
                "accounting_regime": "CORPORATE",
                "icb_code_level2": "1700",
                "icb_name_level2": "Tài nguyên Cơ bản",
                "icb_code_level3": "1750",
                "icb_name_level3": "Thép",
                "industry_snapshot_date": date(2025, 1, 1),
                "delisted_date": None,
            }
        )
        mart_rows.append({"issuer_id": ticker, "market_cap_vnd": mcap})
        ratios_rows.append({"issuer_id": ticker, "BS_TOTAL_ASSETS": mcap * 1.5})
        ml_rows.append(
            {
                "issuer_id": ticker,
                "risk_tier": "R2",
                "calibrated_distress_prob": 0.03,
            }
        )

    # Small L3 "9537" with only 2 issuers, inside L2 "9500" which has 5 more issuers
    for idx, (ticker, l3, assets) in enumerate(
        [
            ("IT_A", "9537", 10_000.0),
            ("IT_B", "9537", 11_000.0),
            ("IT_C", "9533", 9_800.0),
            ("IT_D", "9533", 10_500.0),
            ("IT_E", "9533", 50_000.0),
            ("IT_F", "9533", 12_000.0),
        ]
    ):
        issuers_rows.append(
            {
                "issuer_id": ticker,
                "ticker": ticker,
                "exchange": "HOSE",
                "company_name_vn": f"Tech {ticker}",
                "accounting_regime": "CORPORATE",
                "icb_code_level2": "9500",
                "icb_name_level2": "Công nghệ thông tin",
                "icb_code_level3": l3,
                "icb_name_level3": f"Sub {l3}",
                "industry_snapshot_date": date(2025, 1, 1),
                "delisted_date": None,
            }
        )
        # Omit market_cap_vnd for IT_* to test BS_TOTAL_ASSETS fallback
        ratios_rows.append({"issuer_id": ticker, "BS_TOTAL_ASSETS": assets})

    issuers_df = pl.DataFrame(issuers_rows, schema_overrides={"delisted_date": pl.Date})
    mart_df = pl.DataFrame(mart_rows)
    ratios_df = pl.DataFrame(ratios_rows)
    ml_df = pl.DataFrame(ml_rows)

    # Target ST09 (mcap = 10T): closest log-mcap peers in L3 "1750" are ST10 (11T), ST08 (9T), ST11 (12T), ST07 (8T)
    basis_l3, peers_l3 = suggest_similar_peers(
        "ST09",
        issuers_df,
        valuation_mart=mart_df,
        ratios=ratios_df,
        ml_scores=ml_df,
        limit=4,
    )
    assert basis_l3 == "ICB_LEVEL_3"
    assert len(peers_l3) == 4
    assert [p.ticker for p in peers_l3] == ["ST10", "ST08", "ST11", "ST07"]
    assert all(p.peer_basis == "ICB_LEVEL_3" for p in peers_l3)
    assert all(p.size_metric == "market_cap_vnd" for p in peers_l3)
    assert peers_l3[0].size_ratio == pytest.approx(1.1)

    # Target IT_A (only 2 in L3 "9537" -> < 5 -> fallback to L2 "9500" using BS_TOTAL_ASSETS)
    basis_l2, peers_l2 = suggest_similar_peers(
        "IT_A",
        issuers_df,
        valuation_mart=mart_df,
        ratios=ratios_df,
        ml_scores=ml_df,
        limit=4,
    )
    assert basis_l2 == "ICB_LEVEL_2"
    assert len(peers_l2) == 4
    assert [p.ticker for p in peers_l2] == ["IT_C", "IT_D", "IT_B", "IT_F"]
    assert all(p.peer_basis == "ICB_LEVEL_2" for p in peers_l2)
    assert all(p.size_metric == "BS_TOTAL_ASSETS" for p in peers_l2)


def test_extract_top_anomaly_drivers_for_top_5_percent_outliers() -> None:
    """V3-22: Top 3 |sector_z| drivers returned only when anomaly_percentile_rank >= 95% (or outlier flag)."""
    bench_df = pl.DataFrame(
        {
            "metric_or_ratio_name": [
                "debt_to_equity",
                "gross_margin",
                "current_ratio",
                "accruals_to_assets",
                "roe_total",
            ],
            "value": [4.5, 0.05, 0.6, 0.22, -0.10],
            "peer_median": [0.9, 0.20, 1.5, 0.01, 0.12],
            "robust_z_score": [3.8, -1.9, -2.4, 4.2, -1.2],
            "peer_group_level": ["ICB_LEVEL_3"] * 5,
            "peer_count": [18] * 5,
        }
    )

    is_out_normal, drivers_normal = extract_top_anomaly_drivers(
        bench_df, anomaly_percentile_rank=82.0, statistical_outlier_flag=False
    )
    assert is_out_normal is False
    assert drivers_normal == []

    is_out_high, drivers_high = extract_top_anomaly_drivers(
        bench_df, anomaly_percentile_rank=96.5, statistical_outlier_flag=True
    )
    assert is_out_high is True
    assert len(drivers_high) == 3
    assert [d.metric_name for d in drivers_high] == [
        "accruals_to_assets",
        "debt_to_equity",
        "current_ratio",
    ]
    assert drivers_high[0].abs_sector_z == pytest.approx(4.2)
    assert drivers_high[0].direction_vs_sector == "ABOVE_MEDIAN"
    assert drivers_high[2].direction_vs_sector == "BELOW_MEDIAN"
