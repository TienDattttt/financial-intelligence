"""Unit and integration tests for V3-22..V3-24: Analyst Depth (QoQ, Peers, Z-Drivers) & Ops QA."""

from __future__ import annotations

from datetime import date
from pathlib import Path

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


def test_ops_migration_ddl_and_lazy_imports() -> None:
    """V3-23: Migration app_v3_004 defines 3 tables with immutability/evidence constraints & lazy imports work."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    ddl_path = repo_root / "config" / "migrations" / "app_v3_004_ops_qa.sql"
    ddl = ddl_path.read_text(encoding="utf-8")
    for table in (
        "ops_quarantine_tickets",
        "ops_revision_submissions",
        "issuer_lifecycle_events",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
    assert "APPROVE_REINGEST" in ddl
    assert "KEEP_QUARANTINED" in ddl
    assert "UNIQUE (ticket_id, revision_no)" in ddl

    script = (
        "import importlib, sys\n"
        "for pkg in ('psycopg', 'redis', 'fastapi', 'pydantic', 'dagster'):\n"
        "    sys.modules[pkg] = None\n"
        "for mod_name in (\n"
        "    'finintel.analytics.qoq_and_peers',\n"
        "    'finintel.ops',\n"
        "    'finintel.ops.qa_service',\n"
        "    'finintel.ops.lifecycle',\n"
        "):\n"
        "    importlib.import_module(mod_name)\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_ops_qa_rbac_immutable_revisions_and_analyst_endpoints(tmp_path: Path) -> None:
    """V3-23: USER/RESEARCHER get 403 on /api/v3/ops/*; INTERNAL_OPS appends revision_no=2 keeping rev 1 immutable."""
    pytest.importorskip("fastapi")
    pytest.importorskip("argon2")
    from fastapi.testclient import TestClient

    from finintel.auth._store import InMemoryAppStore
    from finintel.auth.dependencies import UserRole
    from finintel.auth.email import InMemoryEmailOutbox
    from finintel.ops.qa_service import InMemoryOpsQaStore
    from finintel.pipeline.state_store import StateStore
    from finintel.serving.api import create_app
    from finintel.storage.schemas import (
        ANALYTICS_SCHEMAS,
        AUDIT_QUARANTINE_TABLE,
        BENCHMARKS_TABLE,
        ML_SCORES_TABLE,
        PIT_VALUATION_MART_TABLE,
        RATIOS_TABLE,
    )
    from finintel.storage.snapshot_publisher import SnapshotPublisher

    def _conform(frame: pl.DataFrame, schema: pl.Schema) -> pl.DataFrame:
        exprs = [
            (
                pl.col(col).cast(dtype)
                if col in frame.columns
                else pl.lit(None, dtype=dtype).alias(col)
            )
            for col, dtype in schema.items()
        ]
        return frame.select(exprs)

    issuers_rows = []
    mart_rows = []
    ratios_rows = []
    ml_rows = []
    bench_rows = []
    for idx in range(6):
        ticker = f"P{idx:02d}"
        mcap = float((idx + 1) * 1_000_000_000_000)
        issuers_rows.append(
            {
                "issuer_id": ticker,
                "ticker": ticker,
                "exchange": "HOSE",
                "company_name_vn": f"CTCP {ticker}",
                "accounting_regime": "CORPORATE",
                "icb_code_level2": "1700",
                "icb_name_level2": "Tài nguyên Cơ bản",
                "icb_code_level3": "1750",
                "icb_name_level3": "Thép",
                "industry_snapshot_date": date(2025, 1, 1),
                "delisted_date": None,
            }
        )
        mart_rows.append(
            {
                "issuer_id": ticker,
                "ticker": ticker,
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": date(2025, 12, 31),
                "effective_trade_date": date(2026, 1, 30),
                "close_raw_vnd": 20_000.0,
                "pit_shares_outstanding": mcap / 20_000.0,
                "market_cap_vnd": mcap,
                "pe_ttm": 12.0,
                "pb_ratio": 1.5,
                "pe_flag": "OK",
                "pb_flag": "OK",
                "market_cap_flag": "OK",
            }
        )
        ratios_rows.append(
            {
                "issuer_id": ticker,
                "ticker": ticker,
                "accounting_regime": "CORPORATE",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "fiscal_year": 2025,
                "fiscal_quarter": 4,
                "period_end_date": date(2025, 12, 31),
                "filing_id": f"FIL-{ticker}",
                "taxonomy_version": "VAS_TT200_2014",
                "source_filing_ids": [f"FIL-{ticker}"],
            }
        )
        ml_rows.append(
            {
                "issuer_id": ticker,
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": date(2025, 12, 31),
                "filing_id": f"FIL-{ticker}",
                "model_role": "PRIMARY",
                "altman_z_double_prime": 2.1,
                "altman_em_score": 2.0,
                "altman_zone": "GREY_ZONE",
                "piotroski_f_score": 5,
                "piotroski_valid_components_count": 9,
                "beneish_m8_score": -2.1,
                "beneish_flag": "VALID",
                "pca_pc1": 0.1,
                "pca_pc2": 0.2,
                "pca_pc3": 0.0,
                "cluster_id": 1,
                "cluster_archetype_name": "Sản xuất ổn định",
                "anomaly_score_raw": 0.7 if ticker == "P02" else 0.1,
                "anomaly_percentile_rank": 97.5 if ticker == "P02" else 40.0,
                "statistical_outlier_flag": ticker == "P02",
                "anomaly_flag": "OUTLIER_TOP_5" if ticker == "P02" else "NORMAL",
                "distress_risk_score": 0.1,
                "calibrated_distress_prob": 0.04,
                "risk_tier": "R2",
                "risk_market_percentile": 35.0,
                "market_base_rate": 0.04,
                "relative_risk_lift": 1.0,
                "model_version": "v3.0",
                "shap_top_drivers_json": "{}",
            }
        )
    for metric, val, med, z in (
        ("debt_to_equity", 4.2, 0.9, 3.6),
        ("accruals_to_assets", 0.25, 0.02, 4.1),
        ("current_ratio", 0.55, 1.45, -2.5),
        ("roe_total", 0.09, 0.11, -0.4),
    ):
        bench_rows.append(
            {
                "issuer_id": "P02",
                "accounting_regime": "CORPORATE",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": date(2025, 12, 31),
                "metric_or_ratio_name": metric,
                "icb_code_used": "1750",
                "peer_group_level": "ICB_LEVEL_3",
                "peer_count": 6,
                "value": val,
                "peer_median": med,
                "peer_p25": med * 0.7,
                "peer_p75": med * 1.3,
                "peer_iqr": med * 0.6,
                "percentile_rank": 90.0,
                "robust_z_score": z,
            }
        )

    quarantine_df = pl.DataFrame(
        {
            "issuer_id": ["P00"],
            "filing_id": ["FIL-P00-BAD"],
            "statement_scope": ["CONSOLIDATED"],
            "period_type": ["QUARTER"],
            "period_end_date": [date(2025, 12, 31)],
            "taxonomy_version": ["VAS_TT200_2014"],
            "rule_code": ["BS_IDENTITY_MISMATCH"],
            "severity": ["CRITICAL"],
            "details_json": ['{"diff_pct": 0.08}'],
            "quarantined_at": ["2026-04-01T00:00:00Z"],
        }
    )

    data_root = tmp_path / "data_ops_test"
    snapshots_root = data_root / "published" / "snapshots"
    state_store = StateStore(data_root / "metadata" / "state.sqlite")
    state_store.initialize()
    state_store.start_run("run-ops-test", "CLI_MANUAL", {}, None)
    publisher = SnapshotPublisher(snapshots_root, state_store=state_store)
    publisher.publish(
        "snap_v3_ops",
        {
            "issuers.parquet": pl.DataFrame(
                issuers_rows, schema_overrides={"delisted_date": pl.Date}
            ),
            PIT_VALUATION_MART_TABLE: _conform(
                pl.DataFrame(mart_rows), ANALYTICS_SCHEMAS[PIT_VALUATION_MART_TABLE]
            ),
            RATIOS_TABLE: _conform(
                pl.DataFrame(ratios_rows), ANALYTICS_SCHEMAS[RATIOS_TABLE]
            ),
            ML_SCORES_TABLE: _conform(
                pl.DataFrame(ml_rows), ANALYTICS_SCHEMAS[ML_SCORES_TABLE]
            ),
            BENCHMARKS_TABLE: _conform(
                pl.DataFrame(bench_rows), ANALYTICS_SCHEMAS[BENCHMARKS_TABLE]
            ),
            AUDIT_QUARANTINE_TABLE: _conform(
                quarantine_df, ANALYTICS_SCHEMAS[AUDIT_QUARANTINE_TABLE]
            ),
        },
        run_id="run-ops-test",
    )

    app = create_app(data_root=data_root, valkey_url="")
    app_store = InMemoryAppStore()
    ops_store = InMemoryOpsQaStore()
    app.state._app_store = app_store
    app.state._ops_qa_store = ops_store
    app.state._email_sender = InMemoryEmailOutbox()

    csrf = {"X-Requested-With": "FinIntel-Web"}

    with (
        TestClient(app, base_url="https://testserver") as c_user,
        TestClient(app, base_url="https://testserver") as c_res,
        TestClient(app, base_url="https://testserver") as c_ops,
    ):
        # Analyst read endpoints work cleanly
        r_peers = c_user.get("/api/v3/companies/P02/suggested-peers?limit=4")
        assert r_peers.status_code == 200
        peers_body = r_peers.json()
        assert peers_body["peer_basis"] == "ICB_LEVEL_3"
        assert [p["ticker"] for p in peers_body["peers"]] == [
            "P03",
            "P01",
            "P04",
            "P05",
        ]

        r_drv = c_user.get("/api/v3/companies/P02/anomaly-drivers")
        assert r_drv.status_code == 200
        drv_body = r_drv.json()
        assert drv_body["is_multivariate_outlier"] is True
        assert [d["metric_name"] for d in drv_body["drivers"]] == [
            "accruals_to_assets",
            "debt_to_equity",
            "current_ratio",
        ]

        # Register 3 accounts and set roles USER, RESEARCHER, INTERNAL_OPS
        for client, email, role in (
            (c_user, "u@finintel.vn", UserRole.USER),
            (c_res, "r@finintel.vn", UserRole.RESEARCHER),
            (c_ops, "ops@finintel.vn", UserRole.INTERNAL_OPS),
        ):
            reg = client.post(
                "/api/v3/auth/register",
                json={
                    "email": email,
                    "password": "StrongPassword!2026",
                    "display_name": role.value,
                    "accepted_consent_version": "2026.01",
                    "accept_account_core": True,
                },
            )
            assert reg.status_code == 201
            rec = app_store.get_user_by_email(email)
            assert rec is not None
            app_store._users[rec.user_id] = type(rec)(**{**rec.__dict__, "role": role})
            assert (
                client.post(
                    "/api/v3/auth/login",
                    json={"email": email, "password": "StrongPassword!2026"},
                ).status_code
                == 200
            )

        # USER & RESEARCHER receive 403 Forbidden on /api/v3/ops/*
        assert c_user.get("/api/v3/ops/qa-queue").status_code == 403
        assert c_res.get("/api/v3/ops/qa-queue").status_code == 403
        assert c_user.get("/api/v3/ops/issuers/lifecycle").status_code == 403
        assert (
            c_res.post(
                "/api/v3/ops/issuers/sync-request",
                json={
                    "ticker": "P00",
                    "exchange": "HOSE",
                    "event_type": "METADATA_SYNC",
                    "effective_date": "2026-04-01",
                },
                headers=csrf,
            ).status_code
            == 403
        )

        # INTERNAL_OPS lists QA queue seeded from audit_quarantine.parquet
        q_res = c_ops.get("/api/v3/ops/qa-queue")
        assert q_res.status_code == 200
        tickets = q_res.json()["items"]
        assert len(tickets) == 1
        ticket_id = tickets[0]["ticket_id"]
        assert tickets[0]["latest_revision_no"] == 1
        assert len(tickets[0]["revisions"]) == 1
        assert tickets[0]["revisions"][0]["revision_no"] == 1

        # Reject revision without source_url or raw_file_sha256
        bad_rev = c_ops.post(
            f"/api/v3/ops/qa-queue/{ticket_id}/submit-revision",
            json={"reason": "Sửa tay không kèm nguồn"},
            headers=csrf,
        )
        assert bad_rev.status_code == 422

        # Submit valid revision_no = 2 with source_url & sha256; revision_no = 1 remains immutable!
        good_rev = c_ops.post(
            f"/api/v3/ops/qa-queue/{ticket_id}/submit-revision",
            json={
                "source_url": "https://congbothongtin.ssc.gov.vn/bctc/p00-q4-2025.pdf",
                "raw_file_sha256": "a" * 64,
                "reason": "BCTC kiểm toán đính chính bảng cân đối kế toán Q4/2025",
            },
            headers=csrf,
        )
        assert good_rev.status_code == 200
        rev_ticket = good_rev.json()["ticket"]
        assert rev_ticket["latest_revision_no"] == 2
        assert [r["revision_no"] for r in rev_ticket["revisions"]] == [1, 2]
        assert rev_ticket["revisions"][0]["reason"] == "ORIGINAL_QUARANTINED_FILING"
        assert rev_ticket["revisions"][1]["raw_file_sha256"] == "a" * 64

        # Approve reingest decision & record issuer lifecycle event
        dec = c_ops.post(
            f"/api/v3/ops/qa-queue/{ticket_id}/decision",
            json={
                "decision": "APPROVE_REINGEST",
                "note": "Đã đối chiếu PDF gốc UBCKNN",
            },
            headers=csrf,
        )
        assert dec.status_code == 200
        assert dec.json()["ticket"]["status"] == "RESOLVED"
        assert dec.json()["ticket"]["decision"] == "APPROVE_REINGEST"

        sync_res = c_ops.post(
            "/api/v3/ops/issuers/sync-request",
            json={
                "ticker": "P00",
                "exchange": "HOSE",
                "event_type": "FY_END_CHANGE",
                "effective_date": "2026-01-01",
                "fy_end_month": 3,
                "source_url": "https://hsx.vn/disclosure/p00-fy",
                "notes": "Thay đổi niên độ tài chính kết thúc tháng 3",
            },
            headers=csrf,
        )
        assert sync_res.status_code == 201

        lc_res = c_ops.get("/api/v3/ops/issuers/lifecycle")
        assert lc_res.status_code == 200
        p00_lc = next(i for i in lc_res.json()["items"] if i["ticker"] == "P00")
        assert p00_lc["fy_end_month"] == 3
        assert len(p00_lc["recent_events"]) == 1

        # Verify every Ops action was recorded in user_audit_logs
        logs, _ = app_store.list_audit_logs_ops(limit=50, offset=0)
        actions = {log.action for log in logs}
        assert {
            "OPS_QA_REVISION_SUBMITTED",
            "OPS_QA_DECISION",
            "OPS_ISSUER_SYNC_REQUESTED",
        } <= actions


def test_serving_query_service_exposes_backend_qoq_with_mixed_taxonomy_guard() -> None:
    """V3-23: build_statement_periods_and_rows attaches backend QoQ with MIXED_TAXONOMY guard."""
    from finintel.serving.queries._helpers import build_statement_periods_and_rows

    facts = pl.DataFrame(
        {
            "metric_id": ["IS_NET_REVENUE", "IS_NET_REVENUE", "IS_NET_REVENUE"],
            "period_end_date": [
                date(2025, 9, 30),
                date(2025, 12, 31),
                date(2026, 3, 31),
            ],
            "fiscal_year": [2025, 2025, 2026],
            "fiscal_quarter": [3, 4, 1],
            "taxonomy_version": [
                "VAS_TT200_2014",
                "VAS_TT200_2014",
                "VAS_TT99_2025",
            ],
            "value": [100.0, 125.0, 140.0],
            "unit": ["VND", "VND", "VND"],
            "comparable_across_taxonomy": [True, True, True],
        }
    )
    periods, rows = build_statement_periods_and_rows(
        facts, period_type="QUARTER", catalog={}
    )
    assert len(periods) == 3
    assert len(rows) == 1
    row = rows[0]
    qoq_values = row["qoq_values"]
    qoq_flags = row["qoq_flags"]
    assert isinstance(qoq_values, dict)
    assert isinstance(qoq_flags, dict)
    assert qoq_values["2025-12-31"] == pytest.approx(0.25)
    assert qoq_flags["2025-12-31"] == QoqStatusFlag.VALID.value
    assert qoq_values["2026-03-31"] is None
    assert qoq_flags["2026-03-31"] == QoqStatusFlag.MIXED_TAXONOMY.value
