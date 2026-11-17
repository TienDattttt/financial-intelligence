"""Layer 4 acceptance and regression tests."""

import json
from datetime import date, timedelta
from math import prod
from pathlib import Path

import polars as pl
import pytest

from finintel.ingestion.base import (
    AuditStatus,
    FilingMetadata,
    PeriodType,
    StatementScope,
    StatementType,
)
from finintel.normalization.mapper import FACT_SCHEMA, TaxonomyMapper

ROOT = Path(__file__).resolve().parents[2]


def issuer(issuer_id: str = "FPT", regime: str = "CORPORATE") -> pl.DataFrame:
    return pl.DataFrame(
        {"issuer_id": [issuer_id], "ticker": [issuer_id], "accounting_regime": [regime]}
    )


def facts(
    periods: dict[tuple[int, int | None], dict[str, float | None]],
    issuer_id: str = "FPT",
    scope: str = "CONSOLIDATED",
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for (year, quarter), values in periods.items():
        fiscal_start = date(year, 1, 1)
        start = date(year, 1 + 3 * ((quarter or 1) - 1), 1)
        ends = [
            date(year, 3, 31),
            date(year, 6, 30),
            date(year, 9, 30),
            date(year, 12, 31),
        ]
        end = ends[(quarter or 4) - 1]
        filing = f"{issuer_id}-{year}-{quarter}"
        rows.extend(
            {
                "issuer_id": issuer_id,
                "statement_scope": scope,
                "period_type": "ANNUAL" if quarter is None else "QUARTER",
                "fiscal_year": year,
                "fiscal_quarter": quarter,
                "fiscal_year_start_date": fiscal_start,
                "period_start_date": start,
                "period_end_date": end,
                "period_days": (end - start).days + 1,
                "effective_available_at": end
                + timedelta(days=90 if quarter is None else 45),
                "published_at_basis": "STATUTORY_LAG",
                "revision_no": 1,
                "filing_id": filing,
                "source_filing_ids": [filing],
                "source_provider": "file",
                "taxonomy_version": "VAS_TT200_2014"
                if year < 2026
                else "VAS_TT99_2025",
                "revision_reason": "ORIGINAL",
                "audit_status": "AUDITED" if quarter is None else "UNAUDITED",
                "audit_opinion": "UNMODIFIED",
                "derivation_method": "REPORTED_DIRECTLY",
                "metric_id": metric,
                "value": value,
                "value_type": "STOCK" if metric.startswith("BS_") else "FLOW",
                "comparable_across_taxonomy": True,
            }
            for metric, value in values.items()
        )
    return pl.DataFrame(rows, schema=FACT_SCHEMA)


def test_intermediates_annual_values_and_missing_optional_inputs() -> None:
    from finintel.analytics.intermediates import prepare_periods

    source = facts(
        {
            (2024, None): {"BS_TOTAL_ASSETS": 900, "BS_TOTAL_EQUITY": 300},
            (2025, None): {
                "BS_TOTAL_ASSETS": 1100,
                "BS_TOTAL_EQUITY": 400,
                "BS_MINORITY_INTEREST": 30,
                "BS_SHORT_TERM_BORROWINGS": 100,
                "BS_CONVERTIBLE_BONDS": 20,
                "BS_CASH_AND_EQUIVALENTS": 50,
                "BS_CURRENT_ASSETS": 300,
                "BS_CURRENT_LIABILITIES": 150,
                "BS_ORDINARY_SHARES_CAPITAL": 50000,
                "BS_TREASURY_SHARES": 0,
                "IS_PROFIT_BEFORE_TAX": 100,
                "IS_INTEREST_EXPENSE": -20,
                "IS_CURRENT_INCOME_TAX": 25,
                "IS_DEFERRED_INCOME_TAX": 0,
                "IS_NET_INCOME_PARENT": 60,
                "IS_EPS_BASIC_REPORTED": 10,
                "CF_OPERATING_CASH_FLOW": 80,
                "CF_CAPITAL_EXPENDITURE": -25,
            },
        }
    )
    row = prepare_periods(source, issuer(), as_of=date(2026, 6, 1)).row(-1, named=True)
    assert row["total_debt"] == 120
    assert row["net_debt"] == 70
    assert row["working_capital"] == 150
    assert row["invested_capital"] == 470
    assert row["parent_equity"] == 370
    assert row["ebit_ttm"] == 120
    assert row["ebitda_ttm"] is None
    assert row["ebitda_flag"] == "MISSING_DEPRECIATION_DIRECT_CF"
    assert row["effective_tax_rate"] == 0.25
    assert row["nopat_ttm"] == 90
    assert row["fcf_ttm"] == 55
    assert row["estimated_shares_outstanding"] == 5
    assert row["implied_weighted_shares"] == 6
    assert row["BS_TOTAL_ASSETS_prior"] == 900


def test_intermediates_quarters_respect_as_of_scope_gaps_and_taxonomy() -> None:
    from finintel.analytics.intermediates import prepare_periods

    cash = "CF_OPERATING_CASH_FLOW"
    source = facts(
        {
            (2025, 2): {cash: 2},
            (2025, 3): {cash: 3},
            (2025, 4): {cash: 4},
            (2026, 1): {cash: 5},
            (2026, 3): {cash: 7},
        }
    )
    revision = source.filter(
        pl.col("period_end_date") == date(2025, 9, 30)
    ).with_columns(
        pl.lit(date(2026, 7, 1)).alias("effective_available_at"),
        pl.lit(2, dtype=pl.Int64).alias("revision_no"),
        pl.lit(30.0).alias("value"),
    )
    combined = pl.concat(
        [source, revision, facts({(2026, 2): {cash: 6}}, scope="SEPARATE")]
    )
    result = prepare_periods(combined, issuer(), as_of=date(2026, 11, 15))
    assert (
        result.filter(pl.col("period_end_date") == date(2026, 3, 31))[cash].item() == 41
    )
    assert (
        result.filter(pl.col("period_end_date") == date(2026, 9, 30))[cash].item()
        is None
    )
    early = prepare_periods(combined, issuer(), as_of=date(2026, 6, 1))
    assert early[cash][-1] == 14
    mixed = prepare_periods(
        source.with_columns(pl.lit(False).alias("comparable_across_taxonomy")),
        issuer(),
        as_of=date(2026, 6, 1),
    )
    assert mixed[cash][-1] is None
    assert mixed[f"_mixed_{cash}"][-1] is True


def test_point_in_time_on_legacy_storage_respects_exact_and_statutory_dates() -> None:
    from finintel.analytics.intermediates import prepare_periods
    from finintel.storage.schemas import FINANCIAL_FACTS_SCHEMA

    source = facts(
        {
            (2024, None): {"BS_TOTAL_ASSETS": 100},
            (2025, None): {"BS_TOTAL_ASSETS": 120, "IS_NET_INCOME": 10},
        }
    )
    exact = (pl.col("fiscal_year") == 2025) & (pl.col("metric_id") == "BS_TOTAL_ASSETS")
    legacy = source.with_columns(
        pl.when(exact)
        .then(pl.lit("EXACT_FILING"))
        .otherwise(pl.col("published_at_basis"))
        .alias("published_at_basis"),
        pl.when(exact)
        .then(pl.lit(date(2026, 3, 15)))
        .otherwise(pl.col("published_at"))
        .alias("published_at"),
        pl.when(pl.col("fiscal_year") == 2025)
        .then(pl.lit(date(2026, 3, 31)))
        .otherwise(pl.col("effective_available_at"))
        .alias("effective_available_at"),
    ).select(name for name in FINANCIAL_FACTS_SCHEMA if name in source.columns)

    def latest(as_of: date) -> dict[str, object]:
        return prepare_periods(legacy, issuer(), as_of=as_of).row(-1, named=True)

    assert latest(date(2026, 3, 14))["fiscal_year"] == 2024
    assert latest(date(2026, 3, 15))["BS_TOTAL_ASSETS"] == 120
    assert latest(date(2026, 3, 15))["IS_NET_INCOME"] is None
    assert latest(date(2026, 3, 31))["IS_NET_INCOME"] == 10


def test_regime_routing_and_ratio_boundary_flags() -> None:
    from finintel.analytics.intermediates import prepare_periods
    from finintel.analytics.ratios import CORE_RATIOS, calculate_ratios

    history: dict[tuple[int, int | None], dict[str, float | None]] = {
        (2024, None): {
            "BS_TOTAL_EQUITY": -10,
            "BS_TOTAL_ASSETS": 200,
            "BS_MINORITY_INTEREST": 0,
        },
        (2025, None): {
            "BS_TOTAL_EQUITY": 100,
            "BS_MINORITY_INTEREST": 0,
            "BS_TOTAL_ASSETS": 300,
            "BS_TOTAL_LIABILITIES": 200,
            "BS_SHORT_TERM_BORROWINGS": 40,
            "BS_CURRENT_ASSETS": 90,
            "BS_CURRENT_LIABILITIES": 30,
            "IS_NET_INCOME": -50,
            "IS_NET_INCOME_PARENT": -50,
            "IS_PROFIT_BEFORE_TAX": 120,
            "IS_INTEREST_EXPENSE": 0,
            "BS_ORDINARY_SHARES_CAPITAL": 100000,
            "BS_TREASURY_SHARES": 0,
        },
    }
    cut_off = date(2026, 6, 1)
    corporate = calculate_ratios(
        prepare_periods(facts(history), issuer(), as_of=cut_off)
    )
    row = corporate.row(-1, named=True)
    assert len(CORE_RATIOS) == 28
    for ratio in ("roe_total", "roe_parent", "debt_to_equity"):
        assert row[ratio] is None
        assert row[f"{ratio}_flag"] == "NEGATIVE_OR_ZERO_EQUITY"
    assert row["interest_coverage"] is None
    assert row["interest_coverage_flag"] == "NO_INTEREST_EXPENSE"
    bank = calculate_ratios(
        prepare_periods(facts(history), issuer(regime="BANK"), as_of=cut_off)
    )
    row = bank.row(-1, named=True)
    assert row["roa"] == -0.2
    assert row["liabilities_to_assets"] == 200 / 300
    assert row["eps_ttm_estimated"] == -5
    for ratio in ("current_ratio", "quick_ratio", "inventory_turnover"):
        assert row[ratio] is None
        assert row[f"{ratio}_flag"] == "NOT_APPLICABLE_FOR_REGIME"

    losses = facts(history).with_columns(
        pl.when(pl.col("metric_id") == "BS_TOTAL_EQUITY")
        .then(-200.0)
        .otherwise(pl.col("value"))
        .alias("value")
    )
    row = calculate_ratios(prepare_periods(losses, issuer(), as_of=cut_off)).row(
        -1, named=True
    )
    assert row["roe_total"] is None
    assert row["roe_total_flag"] == "NEGATIVE_OR_ZERO_EQUITY"
    assert row["roe_parent"] is None
    assert row["roe_parent_flag"] == "NEGATIVE_OR_ZERO_EQUITY"


def test_parent_equity_requires_nci_for_consolidated_statements_at_both_endpoints() -> (
    None
):
    from finintel.analytics.tables import RATIOS_TABLE, build_analytics_tables

    history: dict[tuple[int, int | None], dict[str, float | None]] = {
        (2023, None): {"BS_TOTAL_EQUITY": 100, "BS_MINORITY_INTEREST": 0},
        (2024, None): {"BS_TOTAL_EQUITY": 100, "IS_NET_INCOME_PARENT": 10},
        (2025, None): {
            "BS_TOTAL_EQUITY": 100,
            "BS_MINORITY_INTEREST": 0,
            "IS_NET_INCOME_PARENT": 10,
        },
    }
    consolidated = build_analytics_tables(
        facts(history), issuer(), as_of=date(2026, 6, 1)
    )[RATIOS_TABLE]
    assert consolidated["parent_equity"].to_list() == [100, None, 100]
    assert consolidated["roe_parent"].to_list() == [None, None, None]
    assert consolidated["roe_parent_flag"].to_list() == ["MISSING_INPUT"] * 3
    separate = build_analytics_tables(
        facts(history, scope="SEPARATE"), issuer(), as_of=date(2026, 6, 1)
    )[RATIOS_TABLE]
    assert separate["parent_equity"].to_list() == [100, 100, 100]
    assert separate["roe_parent"].to_list() == [None, 0.1, 0.1]


def test_q4_provenance_survives_in_subsequent_ttm_and_missing_prior_is_flagged() -> (
    None
):
    from finintel.analytics.intermediates import prepare_periods
    from finintel.analytics.ratios import calculate_ratios

    revenue = "IS_NET_REVENUE"
    profit = "IS_GROSS_PROFIT"
    history = facts(
        {
            (2024, None): {revenue: 100, profit: 40},
            (2024, 1): {revenue: 20, profit: 8},
            (2024, 2): {revenue: 20, profit: 8},
            (2024, 3): {revenue: 25, profit: 10},
            (2025, 1): {revenue: 30, profit: 12, "BS_TOTAL_ASSETS": 500},
        }
    )
    result = calculate_ratios(
        prepare_periods(history, issuer(), as_of=date(2025, 5, 20))
    )
    row = result.row(-1, named=True)
    assert row[revenue] == 110
    assert row["gross_margin"] == 0.4
    assert row["gross_margin_flag"] == "DERIVED_Q4_INPUT"
    assert row["asset_turnover_flag"] == "MISSING_INPUT"

    missing = facts({(2025, None): {revenue: 100, "BS_TOTAL_ASSETS": 500}})
    row = calculate_ratios(
        prepare_periods(missing, issuer(), as_of=date(2026, 4, 1))
    ).row(0, named=True)
    assert row["asset_turnover_flag"] == "MISSING_PRIOR_YEAR_BASE"


def test_dupont_reconciles_both_equity_definitions_and_rejects_loss_denominators() -> (
    None
):
    from finintel.analytics.dupont import add_dupont
    from finintel.analytics.intermediates import prepare_periods
    from finintel.analytics.ratios import calculate_ratios

    history = facts(
        {
            (2024, None): {
                "BS_TOTAL_EQUITY": 300,
                "BS_TOTAL_ASSETS": 800,
                "BS_MINORITY_INTEREST": 20,
            },
            (2025, None): {
                "BS_TOTAL_ASSETS": 1000,
                "BS_TOTAL_EQUITY": 400,
                "BS_MINORITY_INTEREST": 40,
                "IS_NET_INCOME": 100,
                "IS_NET_INCOME_PARENT": 75,
                "IS_NET_REVENUE": 1200,
                "IS_PROFIT_BEFORE_TAX": 125,
                "IS_INTEREST_EXPENSE": 25,
            },
        }
    )
    row = add_dupont(
        calculate_ratios(
            prepare_periods(
                history,
                issuer(),
                as_of=date(2026, 6, 1),
            )
        )
    ).row(-1, named=True)
    core = prod(
        row[name]
        for name in ("tax_burden", "interest_burden", "ebit_margin", "asset_turnover")
    )
    assert abs(core * row["equity_multiplier_total"] - row["roe_total"]) <= 1e-9
    assert (
        abs(
            core * row["parent_profit_share"] * row["equity_multiplier_parent"]
            - row["roe_parent"]
        )
        <= 1e-9
    )
    assert row["dupont_valid_flag"] is True
    negative = history.with_columns(
        pl.when(pl.col("metric_id") == "IS_PROFIT_BEFORE_TAX")
        .then(-125.0)
        .otherwise(pl.col("value"))
        .alias("value")
    )
    row = add_dupont(
        calculate_ratios(
            prepare_periods(
                negative,
                issuer(),
                as_of=date(2026, 6, 1),
            )
        )
    ).row(-1, named=True)
    assert row["dupont_valid_flag"] is False
    assert row["tax_burden"] is None


def test_peer_benchmarks_use_valid_counts_fallback_and_clip_outliers() -> None:
    from finintel.analytics.comparisons import benchmark_ratios

    base = pl.DataFrame(
        {
            "issuer_id": [f"X{i}" for i in range(10)],
            "accounting_regime": ["CORPORATE"] * 10,
            "statement_scope": ["CONSOLIDATED"] * 10,
            "period_type": ["ANNUAL"] * 10,
            "fiscal_year": [2025] * 10,
            "fiscal_quarter": [None] * 10,
            "period_end_date": [date(2025, 12, 31)] * 10,
            "icb_code_level3": ["101010"] * 10,
            "icb_code_level2": ["1010"] * 10,
            "roe_total": [0.1 + i / 100 for i in range(9)] + [50.0],
        }
    )
    sample = pl.concat(
        [
            base,
            base.head(1).with_columns(
                pl.lit("BANK").alias("issuer_id"),
                pl.lit("BANK").alias("accounting_regime"),
                pl.lit(-50.0).alias("roe_total"),
            ),
            base.head(1).with_columns(
                pl.lit("NULL").alias("issuer_id"),
                pl.lit(None, dtype=pl.Float64).alias("roe_total"),
            ),
        ]
    )
    scores = benchmark_ratios(sample, ("roe_total",))
    outlier = scores.filter(pl.col("issuer_id") == "X9").row(0, named=True)
    assert outlier["peer_count"] == 10
    assert outlier["peer_group_level"] == "ICB_LEVEL_3"
    assert 0.14 < outlier["peer_median"] < 0.15
    assert 0.04 < outlier["peer_iqr"] < 0.05
    assert outlier["percentile_rank"] == 100
    assert outlier["robust_z_score"] == 5
    assert scores.filter(pl.col("issuer_id") == "BANK")["peer_count"].item() == 1
    assert "NULL" not in scores["issuer_id"]
    assert benchmark_ratios(base.head(7), ("roe_total",))[
        "peer_group_level"
    ].unique().to_list() == ["ICB_LEVEL_2"]
    assert benchmark_ratios(base.head(4), ("roe_total",))[
        "peer_group_level"
    ].unique().to_list() == ["REGIME_FALLBACK"]


def test_peer_groups_keep_fiscal_dates_and_statement_scopes_separate() -> None:
    from finintel.analytics.comparisons import benchmark_ratios

    peers = pl.DataFrame(
        {
            "issuer_id": ["A", "B", "A"],
            "accounting_regime": ["CORPORATE"] * 3,
            "statement_scope": ["CONSOLIDATED", "CONSOLIDATED", "SEPARATE"],
            "period_type": ["ANNUAL"] * 3,
            "period_end_date": [
                date(2025, 12, 31),
                date(2025, 9, 30),
                date(2025, 12, 31),
            ],
            "fiscal_year": [2025] * 3,
            "fiscal_quarter": [None] * 3,
            "icb_code_level3": ["101010"] * 3,
            "icb_code_level2": ["1010"] * 3,
            "roe_total": [0.1, 0.2, 0.3],
        }
    )
    result = benchmark_ratios(peers, ("roe_total",))
    assert result["peer_count"].to_list() == [1, 1, 1]
    assert result["peer_median"].to_list() == result["value"].to_list()


def test_forensic_flags_use_consecutive_periods_and_emit_evidence() -> None:
    from finintel.analytics.intermediates import prepare_periods
    from finintel.analytics.ratios import calculate_ratios
    from finintel.analytics.red_flags import detect_red_flags

    accounts: dict[str, float | None] = {
        "BS_TOTAL_ASSETS": 100,
        "BS_TOTAL_EQUITY": 50,
        "IS_NET_INCOME": 3,
        "CF_OPERATING_CASH_FLOW": -1,
        "BS_SHORT_TERM_RECEIVABLES": 40,
        "BS_LT_RECEIVABLES": 0,
        "BS_TRADE_RECEIVABLES_LT": 0,
        "BS_BAD_DEBT_PROVISION_ST": 0,
        "BS_BAD_DEBT_PROVISION_LT": 0,
        "BS_TRADE_RECEIVABLES_ST": 10,
    }
    history = facts({(year, q): accounts for year in (2024, 2025) for q in range(1, 5)})
    results = detect_red_flags(
        calculate_ratios(
            prepare_periods(
                history,
                issuer(),
                as_of=date(2026, 6, 1),
            )
        )
    )
    last = results.filter(pl.col("period_end_date") == date(2025, 12, 31))
    assert last["flag_code"].to_list() == [
        "RF_01_ACCRUAL_DIVERGENCE",
        "RF_03_OTHER_RECEIVABLES_ANOMALY",
    ]
    accrual, receivables = [json.loads(value) for value in last["evidence_json"]]
    assert accrual["net_income_ttm"] == 12
    assert accrual["previous_cfo_ttm"] == -4
    assert receivables["other_receivables_to_assets"] == 0.30
    start = results.filter(pl.col("period_end_date") == date(2024, 12, 31))
    assert start["flag_code"].to_list() == ["RF_03_OTHER_RECEIVABLES_ANOMALY"]


def test_analytics_cli_outputs_publishable_parquet_tables(tmp_path: Path) -> None:
    from finintel.pipeline.cli import main
    from finintel.storage import ParquetRepository, SnapshotPublisher

    curated = tmp_path / "curated"
    curated.mkdir()
    facts(
        {
            (2024, None): {"BS_TOTAL_EQUITY": 50, "BS_TOTAL_ASSETS": 100},
            (2025, None): {
                "BS_TOTAL_EQUITY": 50,
                "BS_TOTAL_ASSETS": 100,
                "IS_NET_INCOME": 10,
            },
        }
    ).write_parquet(curated / "financial_facts.parquet")
    issuer().write_parquet(curated / "issuers.parquet")
    config = tmp_path / "settings.yaml"
    config.write_text(
        "state_path: metadata/state.sqlite\nmin_free_bytes: 0\nanalytics:\n  as_of: '2026-06-01'\n",
        encoding="utf-8",
    )
    assert (
        main(
            [
                "--config",
                str(config),
                "run",
                "--source",
                "file",
                "--stage",
                "analytics",
                "--period",
                "2025",
            ]
        )
        == 0
    )
    (run_directory,) = (curated / "analytics").iterdir()
    tables = {path.name: path for path in run_directory.glob("*.parquet")}
    assert set(tables) == {
        "financial_ratios.parquet",
        "industry_benchmarks.parquet",
        "accounting_red_flags.parquet",
    }
    directory = SnapshotPublisher(tmp_path / "published" / "snapshots").publish(
        "snap_layer4", tables
    )
    repository = ParquetRepository(directory)
    ratios = repository.read_table("financial_ratios.parquet")
    assert ratios["roe_total"].to_list() == [0.2]
    assert ratios["roe_total_flag"].to_list() == ["VALID"]
    assert repository.read_table("industry_benchmarks.parquet")[
        "peer_count"
    ].to_list() == [1, 1, 1]
    assert repository.read_table("accounting_red_flags.parquet").is_empty()
    assert (
        json.loads(
            (run_directory / "_analytics_meta.json").read_text(encoding="utf-8")
        )["as_of"]
        == "2026-06-01"
    )


def test_normalize_to_analytics_carries_late_filing_and_ignores_newer_scoped_batch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from finintel.ingestion.archive import RawArchive
    from finintel.ingestion.file_adapter import FileAdapter
    from finintel.pipeline.cli import main
    from finintel.pipeline.state_store import StateStore

    store = StateStore(tmp_path / "metadata/state.sqlite")
    archive = RawArchive(tmp_path / "raw", store)
    path = tmp_path / "statement.csv"
    path.write_text("raw_metric_key,raw_value\n60,10\n", encoding="utf-8")
    start, end = date(2025, 1, 1), date(2025, 12, 31)
    (stored,) = archive.store(
        next(
            FileAdapter(
                path,
                FilingMetadata(
                    "FPT",
                    "FPT",
                    StatementType.INCOME_STATEMENT,
                    PeriodType.ANNUAL,
                    2025,
                    start,
                    start,
                    end,
                    statement_scope=StatementScope.CONSOLIDATED,
                    audit_status=AuditStatus.AUDITED,
                    published_at=date(2026, 5, 1),
                ),
                source_provider="issuer",
            ).fetch()
        )
    )
    curated = tmp_path / "curated"
    curated.mkdir()
    issuer().write_parquet(curated / "issuers.parquet")
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        f"state_path: metadata/state.sqlite\nmin_free_bytes: 0\n"
        f"normalization:\n  filings:\n    {stored.filing_id}:\n      unit_multiplier: 1\n"
        f"analytics:\n  as_of: '2026-06-01'\n",
        encoding="utf-8",
    )
    args = ["--config", str(settings), "run", "--source", "file", "--stage"]
    assert main([*args, "normalize", "--period", "2025"]) == 0
    assert main([*args, "analytics"]) == 1
    assert "complete normalized history" in capsys.readouterr().err
    assert main([*args, "normalize"]) == 0
    full_batches = set(curated.glob("financial_facts/*/financial_facts.parquet"))
    assert main([*args, "normalize", "--period", "2025"]) == 0
    assert main([*args, "analytics", "--ticker", "FPT", "--period", "2025"]) == 0
    assert main([*args, "analytics", "--ticker", "MISSING"]) == 1
    assert "No visible financial periods matched" in capsys.readouterr().err
    (batch,) = (curated / "analytics").iterdir()
    metadata = json.loads((batch / "_analytics_meta.json").read_text(encoding="utf-8"))
    assert Path(metadata["facts_path"]) in full_batches
    assert (
        Path(metadata["issuers_path"]).resolve()
        == (curated / "issuers.parquet").resolve()
    )
    alert = pl.read_parquet(batch / "accounting_red_flags.parquet").row(0, named=True)
    assert alert["filing_id"] == stored.filing_id
    assert json.loads(alert["evidence_json"])["is_filed_late"] is True


def test_snapshot_rejects_malformed_empty_analytics_table(tmp_path: Path) -> None:
    from finintel.exceptions import ContractViolationError
    from finintel.storage import SnapshotPublisher
    from finintel.storage.schemas import RED_FLAGS_TABLE

    publisher = SnapshotPublisher(tmp_path / "snapshots")
    with pytest.raises(ContractViolationError, match="invalid schema"):
        publisher.publish(
            "snap_invalid", {RED_FLAGS_TABLE: pl.DataFrame(schema={"wrong": pl.String})}
        )
    assert not (tmp_path / "snapshots/snap_invalid").exists()


def test_snapshot_requires_nonempty_ratios_with_valid_schema_and_unique_keys(
    tmp_path: Path,
) -> None:
    from finintel.analytics.tables import build_analytics_tables
    from finintel.exceptions import ContractViolationError
    from finintel.storage import SnapshotPublisher
    from finintel.storage.schemas import RATIOS_TABLE

    ratios = build_analytics_tables(
        facts({(2025, None): {"BS_TOTAL_ASSETS": 100}}),
        issuer(),
        as_of=date(2026, 6, 1),
    )[RATIOS_TABLE]
    publisher = SnapshotPublisher(tmp_path / "snapshots")
    for invalid in (
        ratios.drop("roa_flag"),
        ratios.with_columns(pl.col("fiscal_year").cast(pl.String)),
        pl.concat([ratios, ratios]),
    ):
        with pytest.raises(ContractViolationError):
            publisher.publish("snap_bad_ratios", {RATIOS_TABLE: invalid})
        assert not (publisher.root / "snap_bad_ratios").exists()
    with pytest.raises(ValueError, match="empty"):
        publisher.publish("snap_empty_ratios", {RATIOS_TABLE: ratios.clear()})


def test_pipeline_analytics_uses_current_full_history_over_curated_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from finintel.normalization.mapper import UNMAPPED_SCHEMA
    from finintel.pipeline.analytics import AnalyticsHandler
    from finintel.pipeline.normalize import NormalizeHandler
    from finintel.pipeline.runner import RunRequest, Stage, StepContext
    from finintel.pipeline.state_store import StateStore

    history = pl.concat(
        [
            facts(
                {
                    (2024, None): {"BS_TOTAL_ASSETS": 100},
                    (2025, None): {"BS_TOTAL_ASSETS": 100, "IS_NET_INCOME": 20},
                }
            ),
            facts(
                {
                    (2024, None): {"BS_TOTAL_ASSETS": 100},
                    (2025, None): {"BS_TOTAL_ASSETS": 100, "IS_NET_INCOME": 10},
                },
                issuer_id="PEER",
            ),
        ]
    )

    def read_archive(
        *args: object, tickers: tuple[str, ...], **kwargs: object
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        assert tickers == ()
        return history, pl.DataFrame(schema=UNMAPPED_SCHEMA)

    monkeypatch.setattr("finintel.pipeline.normalize.normalize_archive", read_archive)
    curated = tmp_path / "curated"
    curated.mkdir()
    pl.concat([issuer(), issuer("PEER")]).write_parquet(curated / "issuers.parquet")
    history.filter(pl.col("fiscal_year") == 2024).write_parquet(
        curated / "financial_facts.parquet"
    )
    state = StateStore(tmp_path / "metadata/state.sqlite")
    state.initialize()
    run = RunRequest(source="file", tickers=("FPT",), period="2025")
    state.start_run("current", "CLI_MANUAL", run.log_arguments(), None)
    step = state.start_step("current", "NORMALIZE")
    outcome = NormalizeHandler(state, tmp_path, {}, {})(
        StepContext("current", Stage.NORMALIZE, run, run.tickers, None)
    )
    state.finish_step(step, outcome.status)
    AnalyticsHandler(state, tmp_path, {"as_of": "2026-06-01"})(
        StepContext("current", Stage.ANALYTICS, run, run.tickers, None)
    )
    batch = curated / "analytics/current"
    ratios = pl.read_parquet(batch / "financial_ratios.parquet")
    peers = pl.read_parquet(batch / "industry_benchmarks.parquet")
    assert ratios.select("ticker", "fiscal_year", "roa").rows() == [("FPT", 2025, 0.2)]
    assert peers.select("issuer_id", "peer_count").rows() == [("FPT", 2)]
    meta = json.loads((batch / "_analytics_meta.json").read_text(encoding="utf-8"))
    assert Path(meta["facts_path"]).parent.name == "current"


def test_fpt_golden_reconciliation_2022_2025_and_dupont_2018_2025() -> None:
    from finintel.analytics.dupont import add_dupont
    from finintel.analytics.intermediates import prepare_periods
    from finintel.analytics.ratios import calculate_ratios

    path = ROOT / "tests/fixtures/raw_samples/fhsc_fpt_annual_2017_2025_extract.json"
    extract = json.loads(path.read_text(encoding="utf-8"))
    mapper = TaxonomyMapper(ROOT / "config")
    tables: list[pl.DataFrame] = []
    for file_key, statement in (
        ("balance-sheet", StatementType.BALANCE_SHEET),
        ("income-statement", StatementType.INCOME_STATEMENT),
        ("cash-flow", StatementType.CASH_FLOW),
    ):
        for source_row in extract["statements"][file_key]:
            year = int(source_row["year"])
            start, end = date(year, 1, 1), date(year, 12, 31)
            metadata = FilingMetadata(
                "FPT",
                "FPT",
                statement,
                PeriodType.ANNUAL,
                year,
                start,
                start,
                end,
                statement_scope=StatementScope.CONSOLIDATED,
                audit_status=AuditStatus.AUDITED,
            )
            mapped, unmapped = mapper.map_metrics(
                {name: value for name, value in source_row.items() if name != "year"},
                metadata,
                filing_id=f"FPT-{year}-{file_key}",
                source_provider="fhsc",
            )
            assert unmapped.is_empty()
            tables.append(mapped)

    periods = prepare_periods(pl.concat(tables), issuer(), as_of=date(2026, 6, 1))
    ratios = add_dupont(calculate_ratios(periods))
    # Independent VND values checked against B01-DN/HN, B02-DN/HN and B03-DN/HN.
    expected = [
        (
            51_650_403_735_130,
            44_009_527_680_911,
            6_491_343_454_469,
            5_053_831_756_700,
            -3_215_243_200_871,
        ),
        (
            60_282_827_532_899,
            52_617_900_827_385,
            7_788_049_739_616,
            9_517_095_698_405,
            -3_978_252_082_224,
        ),
        (
            71_999_995_678_620,
            62_848_794_351_367,
            9_427_422_530_444,
            11_703_777_188_868,
            -3_275_312_325_702,
        ),
        (
            88_141_991_634_625,
            70_112_825_100_710,
            11_232_339_450_734,
            10_136_043_915_911,
            -5_097_919_349_856,
        ),
    ]
    audited = ratios.filter(pl.col("fiscal_year").is_between(2022, 2025))
    assert (
        audited.select(
            "BS_TOTAL_ASSETS",
            "IS_NET_REVENUE",
            "IS_NET_INCOME",
            "CF_OPERATING_CASH_FLOW",
            "CF_CAPITAL_EXPENDITURE",
        ).rows()
        == expected
    )
    assert audited["fcf_ttm"].to_list() == [
        cfo - abs(capex) for *_, cfo, capex in expected
    ]
    assert audited["eps_basic_reported"][-1] == 5216
    valid = ratios.filter(pl.col("dupont_valid_flag"))
    assert valid["fiscal_year"].to_list() == list(range(2018, 2026))
    common = (
        pl.col("tax_burden")
        * pl.col("interest_burden")
        * pl.col("ebit_margin")
        * pl.col("asset_turnover")
    )
    errors = valid.select(
        (pl.col("roe_total") - common * pl.col("equity_multiplier_total")).abs().max(),
        (
            pl.col("roe_parent")
            - common
            * pl.col("parent_profit_share")
            * pl.col("equity_multiplier_parent")
        )
        .abs()
        .max(),
    ).row(0)
    assert all(error <= 1e-9 for error in errors)


def test_verified_source_headers_and_eps_scaling() -> None:
    for module in (ROOT / "src/finintel/analytics").glob("*.py"):
        assert "Verified Sources:" in module.read_text(encoding="utf-8")
    mapper = TaxonomyMapper(ROOT / "config")
    metadata = FilingMetadata(
        "FPT",
        "FPT",
        StatementType.INCOME_STATEMENT,
        PeriodType.ANNUAL,
        2025,
        date(2025, 1, 1),
        date(2025, 1, 1),
        date(2025, 12, 31),
    )
    mapped, _ = mapper.map_metrics(
        {"70": 5216, "61": 9376},
        metadata,
        filing_id="issuer",
        source_provider="file",
        unit_multiplier=1_000_000,
    )
    assert mapped.select("metric_id", "value", "unit_multiplier").sort(
        "metric_id"
    ).rows() == [
        ("IS_EPS_BASIC_REPORTED", 5216.0, 1_000_000),
        ("IS_NET_INCOME_PARENT", 9_376_000_000.0, 1_000_000),
    ]


def test_fhsc_total_equity_uses_exact_verified_fallback() -> None:
    mapper = TaxonomyMapper(ROOT / "config")
    metadata = FilingMetadata(
        "FPT",
        "FPT",
        StatementType.BALANCE_SHEET,
        PeriodType.ANNUAL,
        2025,
        date(2025, 1, 1),
        date(2025, 1, 1),
        date(2025, 12, 31),
    )
    examples: list[tuple[dict[str, object], tuple[float, str]]] = [
        ({"vonchusohuu": 75}, (75.0, "vonchusohuu")),
        ({"vonchusohuu_tong": None, "vonchusohuu": 75}, (75.0, "vonchusohuu")),
        ({"vonchusohuu_tong": 80, "vonchusohuu": 75}, (80.0, "vonchusohuu_tong")),
    ]
    for raw, expected in examples:
        mapped, unmapped = mapper.map_metrics(
            raw,
            metadata,
            filing_id="fhsc-report",
            source_provider="fhsc",
        )
        assert mapped.select("value", "raw_metric_key").row(0) == expected
        assert unmapped.is_empty()


def test_remaining_forensic_rules_and_audit_filing_provenance() -> None:
    from finintel.analytics.intermediates import prepare_periods
    from finintel.analytics.ratios import calculate_ratios
    from finintel.analytics.red_flags import detect_red_flags

    base: dict[str, float | None] = {
        "BS_TRADE_RECEIVABLES_ST": 10,
        "BS_INVENTORIES": 10,
        "BS_TOTAL_ASSETS": 1000,
        "IS_NET_REVENUE": 100,
        "IS_COST_OF_GOODS_SOLD": 50,
        "IS_GROSS_PROFIT": 50,
    }
    current = {
        **base,
        "BS_TRADE_RECEIVABLES_ST": 240,
        "BS_INVENTORIES": 300,
        "IS_NET_REVENUE": 110,
        "IS_COST_OF_GOODS_SOLD": 60,
        "IS_GROSS_PROFIT": 40,
        "BS_SHORT_TERM_BORROWINGS": 100,
        "BS_TOTAL_EQUITY": -5,
        "IS_PROFIT_BEFORE_TAX": -20,
        "IS_INTEREST_EXPENSE": 10,
    }
    history = facts({(2023, None): base, (2024, None): base, (2025, None): current})
    history = history.with_columns(
        (pl.col("fiscal_year") == 2025).alias("is_filed_late"),
    )
    alerts = detect_red_flags(
        calculate_ratios(
            prepare_periods(
                history,
                issuer(),
                as_of=date(2026, 6, 1),
            )
        )
    )
    assert alerts["flag_code"].to_list() == [
        "RF_02_RECEIVABLES_SURGE",
        "RF_04_INVENTORY_BLOAT",
        "RF_05_DEBT_SERVICE_DISTRESS",
        "RF_06_NEGATIVE_EQUITY",
        "RF_07_AUDIT_OR_FILING_WARNING",
    ]
    audit = alerts.row(-1, named=True)
    assert audit["filing_id"] == "FPT-2025-None"
    assert json.loads(audit["evidence_json"])["is_filed_late"] is True


def test_missing_inputs_and_signed_tax_do_not_fabricate_financial_results() -> None:
    from finintel.analytics.intermediates import prepare_periods
    from finintel.analytics.ratios import calculate_ratios

    common: dict[str, float | None] = {
        "IS_PROFIT_BEFORE_TAX": 100,
        "IS_NET_REVENUE": 150,
        "CF_OPERATING_CASH_FLOW": 40,
        "BS_CONVERTIBLE_BONDS": 30,
    }
    source = facts(
        {
            (2023, None): common,
            (2024, None): {
                **common,
                "IS_INTEREST_EXPENSE": 0,
                "IS_CURRENT_INCOME_TAX": 25,
            },
            (2025, None): {
                **common,
                "IS_INTEREST_EXPENSE": 20,
                "IS_CURRENT_INCOME_TAX": 25,
                "IS_DEFERRED_INCOME_TAX": -5,
                "CF_CAPITAL_EXPENDITURE": 8,
            },
            (2026, None): {**common, "IS_BORROWING_COSTS": 10},
        }
    )
    source = source.with_columns(
        (
            ~pl.col("metric_id").is_in(["IS_INTEREST_EXPENSE", "IS_BORROWING_COSTS"])
        ).alias("comparable_across_taxonomy")
    )
    rows = calculate_ratios(
        prepare_periods(source, issuer(), as_of=date(2027, 6, 1))
    ).rows(named=True)
    assert rows[0]["total_debt"] == 30
    assert rows[0]["ebit_ttm"] is None
    assert rows[0]["fcf_to_revenue_flag"] == "MISSING_INPUT"
    assert rows[1]["ebit_ttm"] == 100
    assert rows[1]["effective_tax_rate"] is None
    assert rows[1]["nopat_flag"] == "MISSING_INPUT"
    assert rows[2]["effective_tax_rate"] == 0.2
    assert rows[2]["nopat_ttm"] == 96
    assert rows[2]["fcf_ttm"] == 32
    assert rows[3]["ebit_ttm"] == 110
    assert rows[3]["interest_coverage"] == 11
    assert rows[3]["ebit_growth_yoy_flag"] == "MIXED_TAXONOMY"


@pytest.mark.parametrize("opinion", ["EXCEPT_FOR", "ADVERSE", "DISCLAIMER"])
def test_forensic_audit_opinion_evidence_survives_a_different_period_anchor(
    opinion: str,
) -> None:
    from finintel.analytics.tables import RED_FLAGS_TABLE, build_analytics_tables

    source = facts({(2025, None): {"IS_NET_INCOME": 100, "BS_TOTAL_ASSETS": 200}})
    income = pl.col("metric_id") == "IS_NET_INCOME"
    source = source.with_columns(
        pl.when(income)
        .then(pl.lit(opinion))
        .otherwise(pl.col("audit_opinion"))
        .alias("audit_opinion"),
        pl.when(income)
        .then(pl.lit("audit-filing"))
        .otherwise(pl.lit("z-balance-sheet"))
        .alias("filing_id"),
    ).with_columns(pl.concat_list("filing_id").alias("source_filing_ids"))
    alerts = build_analytics_tables(source, issuer(), as_of=date(2026, 6, 1))[
        RED_FLAGS_TABLE
    ]
    row = alerts.row(0, named=True)
    assert row["flag_code"] == "RF_07_AUDIT_OR_FILING_WARNING"
    assert row["filing_id"] == "audit-filing"
    evidence = json.loads(row["evidence_json"])
    assert evidence["audit_opinion"] == opinion
    assert evidence["source_filing_ids"] == ["audit-filing"]


def test_eps_uses_weighted_annual_shares_and_rejects_unknown_treasury_balance() -> None:
    from finintel.analytics.tables import RATIOS_TABLE, build_analytics_tables

    base: dict[str, float | None] = {
        "IS_NET_INCOME_PARENT": 100,
        "BS_ORDINARY_SHARES_CAPITAL": 100_000,
    }
    source = facts(
        {
            (2023, None): base,
            (2024, None): {**base, "BS_TREASURY_SHARES": 0},
            (2025, None): {
                **base,
                "BS_TREASURY_SHARES": -100,
                "IS_EPS_BASIC_REPORTED": 20,
            },
        }
    )
    out = build_analytics_tables(source, issuer(), as_of=date(2026, 6, 1))[RATIOS_TABLE]
    assert out["eps_ttm_estimated"].to_list() == [None, 10, 20]
    assert out["eps_estimate_basis"].to_list() == [
        None,
        "NO_TREASURY_AT_PERIOD_END",
        "IMPLIED_ANNUAL_WEIGHTED",
    ]


def test_quarterly_cutoff_and_ttm_filing_lineage_in_published_ratios() -> None:
    from finintel.analytics.tables import RATIOS_TABLE, build_analytics_tables

    source = facts(
        {
            (2025, 1): {"IS_NET_REVENUE": 10, "IS_GROSS_PROFIT": 2},
            (2025, 2): {"IS_NET_REVENUE": 20, "IS_GROSS_PROFIT": 6},
            (2025, 3): {"IS_NET_REVENUE": 30, "IS_GROSS_PROFIT": 12},
            (2025, 4): {"IS_NET_REVENUE": 40, "IS_GROSS_PROFIT": 25},
        }
    )
    before = build_analytics_tables(source, issuer(), as_of=date(2026, 2, 13))[
        RATIOS_TABLE
    ]
    assert before["fiscal_quarter"].max() == 3
    assert before["gross_margin"].null_count() == before.height
    after = build_analytics_tables(source, issuer(), as_of=date(2026, 2, 14))[
        RATIOS_TABLE
    ]
    assert after["gross_margin"][-1] == (2 + 6 + 12 + 25) / (10 + 20 + 30 + 40)
    assert set(after["source_filing_ids"][-1]) == {f"FPT-2025-{q}" for q in range(1, 5)}


def test_derived_q4_uses_matching_annual_closing_stocks() -> None:
    from finintel.analytics.tables import RATIOS_TABLE, build_analytics_tables

    source = facts(
        {
            (2025, 1): {"IS_NET_REVENUE": 20},
            (2025, 2): {"IS_NET_REVENUE": 25},
            (2025, 3): {"IS_NET_REVENUE": 30},
            (2025, None): {
                "IS_NET_REVENUE": 110,
                "BS_CURRENT_ASSETS": 300,
                "BS_CURRENT_LIABILITIES": 150,
            },
        }
    )
    out = build_analytics_tables(source, issuer(), as_of=date(2026, 6, 1))[RATIOS_TABLE]
    quarter = out.filter(pl.col("fiscal_quarter") == 4).row(0, named=True)
    assert quarter["current_ratio"] == 2
    assert quarter["source_filing_ids"] == [
        "FPT-2025-1",
        "FPT-2025-2",
        "FPT-2025-3",
        "FPT-2025-None",
    ]


def test_growth_with_missing_prior_period_reports_specific_status() -> None:
    from finintel.analytics.tables import RATIOS_TABLE, build_analytics_tables

    source = facts(
        {
            (2025, None): {
                "IS_NET_REVENUE": 100,
                "IS_PROFIT_BEFORE_TAX": 25,
                "IS_INTEREST_EXPENSE": 5,
                "IS_NET_INCOME_PARENT": 20,
            }
        }
    )
    result = build_analytics_tables(source, issuer(), as_of=date(2026, 6, 1))[
        RATIOS_TABLE
    ]
    for name in ("revenue", "ebit", "net_income_parent"):
        assert result[f"{name}_growth_yoy_flag"].item() == "MISSING_PRIOR_YEAR_BASE"


def test_rf03_requires_reported_long_term_receivables() -> None:
    from finintel.analytics.tables import RED_FLAGS_TABLE, build_analytics_tables

    base: dict[str, float | None] = {
        "BS_TOTAL_ASSETS": 100,
        "BS_SHORT_TERM_RECEIVABLES": 40,
        "BS_TRADE_RECEIVABLES_ST": 10,
        "BS_BAD_DEBT_PROVISION_ST": 0,
        "BS_BAD_DEBT_PROVISION_LT": 0,
    }
    source = facts(
        {
            (2024, None): base,
            (2025, None): {
                **base,
                "BS_LT_RECEIVABLES": 0,
                "BS_TRADE_RECEIVABLES_LT": 0,
            },
        }
    )
    flags = build_analytics_tables(source, issuer(), as_of=date(2026, 6, 1))[
        RED_FLAGS_TABLE
    ]
    assert flags.select("period_end_date", "flag_code").rows() == [
        (date(2025, 12, 31), "RF_03_OTHER_RECEIVABLES_ANOMALY"),
    ]


def test_rf03_excludes_long_term_customer_receivables() -> None:
    from finintel.analytics.tables import RED_FLAGS_TABLE, build_analytics_tables

    base: dict[str, float | None] = {
        "BS_TOTAL_ASSETS": 100,
        "BS_SHORT_TERM_RECEIVABLES": 10,
        "BS_TRADE_RECEIVABLES_ST": 10,
        "BS_LT_RECEIVABLES": 40,
        "BS_BAD_DEBT_PROVISION_ST": 0,
        "BS_BAD_DEBT_PROVISION_LT": 0,
    }
    source = facts(
        {
            (2023, None): base,
            (2024, None): {**base, "BS_TRADE_RECEIVABLES_LT": 40},
            (2025, None): {**base, "BS_TRADE_RECEIVABLES_LT": 10},
        }
    )
    alert = build_analytics_tables(source, issuer(), as_of=date(2026, 6, 1))[
        RED_FLAGS_TABLE
    ].row(0, named=True)
    assert alert["period_end_date"] == date(2025, 12, 31)
    evidence = json.loads(alert["evidence_json"])
    assert evidence["BS_TRADE_RECEIVABLES_LT"] == 10
    assert evidence["other_receivables_to_assets"] == 0.30


@pytest.mark.parametrize("maturity", ["ST", "LT"])
@pytest.mark.parametrize(
    ("allocation", "expected"), [(20, 0.35), (0, None), (None, None), (21, None)]
)
def test_rf03_uses_only_verified_customer_allowance(
    maturity: str,
    allocation: int | None,
    expected: float | None,
) -> None:
    from finintel.analytics.tables import RED_FLAGS_TABLE, build_analytics_tables

    raw: dict[str, float | None] = {
        "BS_TOTAL_ASSETS": 100,
        "BS_SHORT_TERM_RECEIVABLES": 0,
        "BS_TRADE_RECEIVABLES_ST": 0,
        "BS_LT_RECEIVABLES": 0,
        "BS_TRADE_RECEIVABLES_LT": 0,
        "BS_BAD_DEBT_PROVISION_ST": 0,
        "BS_BAD_DEBT_PROVISION_LT": 0,
    }
    total = "BS_SHORT_TERM_RECEIVABLES" if maturity == "ST" else "BS_LT_RECEIVABLES"
    raw.update(
        {
            total: 80 if allocation is None else 65,
            f"BS_TRADE_RECEIVABLES_{maturity}": 50,
            f"BS_BAD_DEBT_PROVISION_{maturity}": -20,
            f"BS_TRADE_RECEIVABLES_ALLOWANCE_{maturity}": allocation,
        }
    )
    alerts = build_analytics_tables(
        facts({(2025, None): raw}), issuer(), as_of=date(2026, 6, 1)
    )[RED_FLAGS_TABLE]
    if expected is None:
        assert alerts.is_empty()
    else:
        evidence = json.loads(alerts["evidence_json"].item())
        assert evidence[f"BS_TRADE_RECEIVABLES_ALLOWANCE_{maturity}"] == allocation
        assert evidence["other_receivables_to_assets"] == expected


def test_rf03_does_not_reuse_an_allowance_from_an_older_filing_revision() -> None:
    from finintel.analytics.tables import RED_FLAGS_TABLE, build_analytics_tables

    original = facts(
        {
            (2025, None): {
                "BS_TOTAL_ASSETS": 100,
                "BS_SHORT_TERM_RECEIVABLES": 65,
                "BS_TRADE_RECEIVABLES_ST": 50,
                "BS_BAD_DEBT_PROVISION_ST": -20,
                "BS_TRADE_RECEIVABLES_ALLOWANCE_ST": 20,
                "BS_LT_RECEIVABLES": 0,
                "BS_TRADE_RECEIVABLES_LT": 0,
                "BS_BAD_DEBT_PROVISION_LT": 0,
            }
        }
    )
    revised = original.filter(
        pl.col("metric_id") != "BS_TRADE_RECEIVABLES_ALLOWANCE_ST"
    ).with_columns(
        pl.lit(2, dtype=pl.Int64).alias("revision_no"),
        pl.lit("revised-filing").alias("filing_id"),
        pl.lit(["revised-filing"]).alias("source_filing_ids"),
        pl.lit(date(2026, 4, 15)).alias("effective_available_at"),
    )
    history = pl.concat([original, revised])
    assert (
        build_analytics_tables(history, issuer(), as_of=date(2026, 4, 14))[
            RED_FLAGS_TABLE
        ].height
        == 1
    )
    assert build_analytics_tables(history, issuer(), as_of=date(2026, 4, 15))[
        RED_FLAGS_TABLE
    ].is_empty()


def test_overflow_flags_match_null_publication_and_dependent_ratios() -> None:
    from finintel.analytics.tables import RATIOS_TABLE, build_analytics_tables

    source = facts(
        {
            (2025, None): {
                "IS_PROFIT_BEFORE_TAX": 1e308,
                "IS_INTEREST_EXPENSE": 0,
                "CF_DEPRECIATION_AMORTIZATION": 1e308,
                "IS_NET_REVENUE": 1e308,
                "BS_SHORT_TERM_BORROWINGS": 10,
                "BS_CASH_AND_EQUIVALENTS": 4,
            }
        }
    )
    row = build_analytics_tables(source, issuer(), as_of=date(2026, 6, 1))[
        RATIOS_TABLE
    ].row(0, named=True)
    assert row["ebitda_ttm"] is None
    assert row["ebitda_flag"] == "NUMERIC_OVERFLOW"
    assert row["ebitda_margin"] is None
    assert row["ebitda_margin_flag"] == "NUMERIC_OVERFLOW"
    assert row["net_debt_to_ebitda"] is None
    assert row["net_debt_to_ebitda_flag"] == "NUMERIC_OVERFLOW"


@pytest.mark.parametrize(("earnings", "eps"), [(-100, -20), (100, 20)])
def test_annual_eps_implies_positive_weighted_shares_with_either_sign(
    earnings: int, eps: int
) -> None:
    from finintel.analytics.tables import RATIOS_TABLE, build_analytics_tables

    source = facts(
        {
            (2025, None): {
                "IS_NET_INCOME_PARENT": earnings,
                "IS_EPS_BASIC_REPORTED": eps,
                "BS_ORDINARY_SHARES_CAPITAL": 100_000,
                "BS_TREASURY_SHARES": 0,
            }
        }
    )
    row = build_analytics_tables(source, issuer(), as_of=date(2026, 6, 1))[
        RATIOS_TABLE
    ].row(0, named=True)
    assert row["implied_weighted_shares"] == 5
    assert row["eps_ttm_estimated"] == eps
    assert row["eps_estimate_basis"] == "IMPLIED_ANNUAL_WEIGHTED"
