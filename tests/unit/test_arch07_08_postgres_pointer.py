"""Tests for ARCH-07b (Unified DBSP Transform Flow) and ARCH-08 (Postgres Active Snapshot Pointer)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl

from finintel.pipeline.analytics import AnalyticsHandler
from finintel.pipeline.runner import RunRequest, Stage, StepContext
from finintel.pipeline.state_store import StateStore
from finintel.storage.schemas import BENCHMARKS_TABLE, RATIOS_TABLE, RED_FLAGS_TABLE
from finintel.transform.incremental import run_incremental_analytics_transform
from tests.unit.test_layer4_analytics import facts, issuer


def test_arch07b_cli_analytics_handler_matches_dbsp_incremental_engine(
    tmp_path: Path,
) -> None:
    """ARCH-07b: CLI AnalyticsHandler delegates to run_incremental_analytics_transform
    and produces identical ratios, benchmarks, and red_flags as Dagster's DBSP engine."""
    history = pl.concat(
        [
            facts(
                {
                    (2024, None): {
                        "BS_TOTAL_ASSETS": 1000.0,
                        "BS_TOTAL_LIABILITIES": 400.0,
                        "BS_TOTAL_EQUITY": 600.0,
                        "IS_NET_REVENUE": 500.0,
                        "IS_NET_INCOME": 80.0,
                    },
                    (2025, None): {
                        "BS_TOTAL_ASSETS": 1100.0,
                        "BS_TOTAL_LIABILITIES": 420.0,
                        "BS_TOTAL_EQUITY": 680.0,
                        "IS_NET_REVENUE": 550.0,
                        "IS_NET_INCOME": 95.0,
                    },
                },
                issuer_id="FPT",
            ),
            facts(
                {
                    (2024, None): {
                        "BS_TOTAL_ASSETS": 900.0,
                        "BS_TOTAL_LIABILITIES": 350.0,
                        "BS_TOTAL_EQUITY": 550.0,
                        "IS_NET_REVENUE": 450.0,
                        "IS_NET_INCOME": 65.0,
                    },
                    (2025, None): {
                        "BS_TOTAL_ASSETS": 950.0,
                        "BS_TOTAL_LIABILITIES": 360.0,
                        "BS_TOTAL_EQUITY": 590.0,
                        "IS_NET_REVENUE": 480.0,
                        "IS_NET_INCOME": 72.0,
                    },
                },
                issuer_id="CMG",
            ),
        ]
    )
    issuers_df = pl.concat([issuer("FPT"), issuer("CMG")])

    curated = tmp_path / "curated"
    curated.mkdir()
    issuers_df.write_parquet(curated / "issuers.parquet")
    history.write_parquet(curated / "financial_facts.parquet")

    state = StateStore(tmp_path / "metadata" / "state.sqlite")
    state.initialize()
    req = RunRequest(source="file")
    state.start_run("run-arch07b", "CLI_MANUAL", req.log_arguments(), None)

    AnalyticsHandler(state, tmp_path, {"as_of": "2026-06-01"})(
        StepContext("run-arch07b", Stage.ANALYTICS, req, (), None)
    )

    batch = curated / "analytics" / "run-arch07b"
    meta = json.loads((batch / "_analytics_meta.json").read_text(encoding="utf-8"))
    assert meta["transform_engine"] == "dbsp_incremental"
    assert meta["ml_enabled"] is False

    # Compare against direct DBSP engine output (same engine used by Dagster assets.py)
    bundle = run_incremental_analytics_transform(
        history,
        issuers_df,
        as_of=date(2026, 6, 1),
        run_id="run-arch07b-direct",
        persist_state=False,
    )
    assert pl.read_parquet(batch / RATIOS_TABLE).equals(bundle.financial_ratios)
    assert pl.read_parquet(batch / BENCHMARKS_TABLE).equals(bundle.industry_benchmarks)
    assert pl.read_parquet(batch / RED_FLAGS_TABLE).equals(bundle.accounting_red_flags)
