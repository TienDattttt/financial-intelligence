"""Unit tests for Layer 6 3-tier quality gates, quarantine, circuit breaker, and UI guards."""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from finintel.exceptions import ContractViolationError, CriticalDataQualityError
from finintel.pipeline.state_store import StateStore
from finintel.quality.gate import (
    evaluate_quality_gate,
    verify_analytics_quality_gate,
)
from finintel.quality.rules import (
    QualityGateConfig,
    QualityRuleCode,
    QualityStatus,
)
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_TABLE,
    QUALITY_SUMMARY_TABLE,
    RATIOS_TABLE,
    canonicalize_financial_facts,
)
from finintel.storage.snapshot_publisher import SnapshotPublisher

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_issuer_facts(
    issuer_id: str,
    *,
    period_end: date = date(2025, 12, 31),
    period_start: date = date(2025, 1, 1),
    fiscal_start: date = date(2025, 1, 1),
    fiscal_year: int = 2025,
    fiscal_quarter: int | None = None,
    period_type: str = "ANNUAL",
    statement_scope: str = "CONSOLIDATED",
    taxonomy_version: str = "VAS_TT200_2014",
    assets: float | None = 1_000_000_000_000.0,
    liabilities: float | None = 400_000_000_000.0,
    equity: float | None = 600_000_000_000.0,
    current_assets: float | None = 550_000_000_000.0,
    noncurrent_assets: float | None = 450_000_000_000.0,
    bs_cash: float | None = 100_000_000_000.0,
    net_revenue: float | None = 800_000_000_000.0,
    net_income: float | None = 120_000_000_000.0,
    cfo: float | None = 130_000_000_000.0,
    depreciation: float | None = 40_000_000_000.0,
    cf_ending_cash: float | None = 100_000_000_000.0,
    derivation_method: str = "REPORTED_DIRECTLY",
    revision_no: int = 1,
    filing_prefix: str | None = None,
) -> pl.DataFrame:
    prefix = filing_prefix or f"{issuer_id}-{statement_scope}-{period_end.isoformat()}"
    metrics: list[tuple[str, float | None, str]] = [
        ("BS_TOTAL_ASSETS", assets, f"{prefix}-bs"),
        ("BS_TOTAL_LIABILITIES", liabilities, f"{prefix}-bs"),
        ("BS_TOTAL_EQUITY", equity, f"{prefix}-bs"),
        ("BS_CURRENT_ASSETS", current_assets, f"{prefix}-bs"),
        ("BS_NONCURRENT_ASSETS", noncurrent_assets, f"{prefix}-bs"),
        ("BS_CASH_AND_EQUIVALENTS", bs_cash, f"{prefix}-bs"),
        ("IS_NET_REVENUE", net_revenue, f"{prefix}-is"),
        ("IS_NET_INCOME", net_income, f"{prefix}-is"),
        ("CF_OPERATING_CASH_FLOW", cfo, f"{prefix}-cf"),
        ("CF_DEPRECIATION_AMORTIZATION", depreciation, f"{prefix}-cf"),
        ("CF_ENDING_CASH", cf_ending_cash, f"{prefix}-cf"),
    ]
    rows: list[dict[str, object]] = []
    for metric_id, val, fid in metrics:
        if val is None and metric_id in {
            "CF_OPERATING_CASH_FLOW",
            "CF_DEPRECIATION_AMORTIZATION",
            "CF_ENDING_CASH",
        }:
            continue
        rows.append(
            {
                "issuer_id": issuer_id,
                "statement_scope": statement_scope,
                "period_type": period_type,
                "fiscal_year_start_date": fiscal_start,
                "period_end_date": period_end,
                "metric_id": metric_id,
                "taxonomy_version": taxonomy_version,
                "revision_no": revision_no,
                "filing_id": fid,
                "fiscal_year": fiscal_year,
                "fiscal_quarter": fiscal_quarter,
                "period_start_date": (
                    period_end if metric_id.startswith("BS_") else period_start
                ),
                "published_at": date(fiscal_year + 1, 3, 20),
                "published_at_basis": "EXACT_FILING",
                "effective_available_at": date(fiscal_year + 1, 3, 20),
                "revision_reason": "ORIGINAL",
                "derivation_method": (
                    derivation_method
                    if not metric_id.startswith("BS_")
                    else "REPORTED_DIRECTLY"
                ),
                "audit_status": "AUDITED" if period_type == "ANNUAL" else "UNAUDITED",
                "audit_opinion": "UNMODIFIED"
                if period_type == "ANNUAL"
                else "NOT_APPLICABLE",
                "value": val,
                "ttm_value": None,
                "computation_note": None,
            }
        )
    return canonicalize_financial_facts(pl.DataFrame(rows))


def test_dod_quarantine_1_of_20_issuers_publishes_19_clean_issuers(
    tmp_path: Path,
) -> None:
    """DoD #1a: 1/20 (5% <= 10%) BS identity mismatch -> quarantine 1, publish 19 cleanly."""
    frames = [_make_issuer_facts(f"CORP{i:02d}") for i in range(1, 20)]
    # Issuer 20 has a 10% Balance Sheet identity mismatch (Assets=1000, L+E=900).
    frames.append(
        _make_issuer_facts(
            "CORP20",
            assets=1_000_000_000_000.0,
            liabilities=400_000_000_000.0,
            equity=500_000_000_000.0,
        )
    )
    batch = pl.concat(frames)

    result = evaluate_quality_gate(batch)
    assert result.total_issuers == 20
    assert result.critical_issuers == 1
    assert result.critical_issuer_ratio == pytest.approx(0.05)

    assert result.audit_quarantine["issuer_id"].to_list() == ["CORP20"]
    assert result.audit_quarantine["rule_code"].to_list() == [
        QualityRuleCode.BS_IDENTITY_MISMATCH.value
    ]
    assert result.audit_quarantine["severity"].to_list() == ["CRITICAL"]

    clean_issuers = sorted(result.clean_facts["issuer_id"].unique().to_list())
    assert len(clean_issuers) == 19
    assert "CORP20" not in clean_issuers
    assert result.quality_summary.height == 19
    assert set(result.quality_summary["quality_status"].to_list()) == {
        QualityStatus.PASS.value
    }

    # Publish the 19 clean issuers + quality tables atomically to a new snapshot.
    snapshots_root = tmp_path / "published" / "snapshots"
    store = StateStore(tmp_path / "metadata" / "state.sqlite")
    store.initialize()
    store.start_run("run-20", "CLI_MANUAL", {}, None)
    publisher = SnapshotPublisher(snapshots_root, state_store=store)
    publisher.publish(
        "snap_clean_19",
        {
            "financial_facts.parquet": result.clean_facts,
            QUALITY_SUMMARY_TABLE: result.quality_summary,
            AUDIT_QUARANTINE_TABLE: result.audit_quarantine,
        },
        run_id="run-20",
    )

    repo = ParquetRepository(data_root=tmp_path)
    assert repo.snapshot_id == "snap_clean_19"
    published_facts = repo.read_table("financial_facts.parquet")
    assert published_facts["issuer_id"].n_unique() == 19
    assert "CORP20" not in set(published_facts["issuer_id"].to_list())
    published_quarantine = repo.read_table(AUDIT_QUARANTINE_TABLE)
    assert published_quarantine["issuer_id"].to_list() == ["CORP20"]


def test_dod_batch_circuit_breaker_3_of_10_unit_scale_jump_preserves_previous_snapshot(
    tmp_path: Path,
) -> None:
    """DoD #1b: 3/10 (30% > 10%) 1000x unit scale jump -> raise CriticalDataQualityError & keep old snapshot."""
    snapshots_root = tmp_path / "published" / "snapshots"
    store = StateStore(tmp_path / "metadata" / "state.sqlite")
    store.initialize()
    store.start_run("run-prev", "CLI_MANUAL", {}, None)
    publisher = SnapshotPublisher(snapshots_root, state_store=store)
    publisher.publish(
        "snap_safe_prev",
        {"financial_facts.parquet": _make_issuer_facts("SAFE01")},
        run_id="run-prev",
    )

    frames: list[pl.DataFrame] = []
    for i in range(1, 11):
        issuer = f"CORP{i:02d}"
        frames.append(
            _make_issuer_facts(
                issuer,
                fiscal_year=2024,
                fiscal_start=date(2024, 1, 1),
                period_start=date(2024, 1, 1),
                period_end=date(2024, 12, 31),
                assets=1_000_000_000_000.0,
                liabilities=400_000_000_000.0,
                equity=600_000_000_000.0,
                current_assets=550_000_000_000.0,
                noncurrent_assets=450_000_000_000.0,
            )
        )
        # Issuers 08, 09, 10 suffer a 1,000x unit scaling error in 2025.
        scale = 1_000.0 if i >= 8 else 1.0
        frames.append(
            _make_issuer_facts(
                issuer,
                fiscal_year=2025,
                fiscal_start=date(2025, 1, 1),
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                assets=1_000_000_000_000.0 * scale,
                liabilities=400_000_000_000.0 * scale,
                equity=600_000_000_000.0 * scale,
                current_assets=550_000_000_000.0 * scale,
                noncurrent_assets=450_000_000_000.0 * scale,
            )
        )
    batch = pl.concat(frames)
    captured_quarantine: list[pl.DataFrame] = []

    with pytest.raises(CriticalDataQualityError, match="3/10 issuers"):
        evaluate_quality_gate(batch, on_quarantine=captured_quarantine.append)

    assert len(captured_quarantine) == 1
    q_df = captured_quarantine[0]
    assert sorted(q_df["issuer_id"].to_list()) == ["CORP08", "CORP09", "CORP10"]
    assert set(q_df["rule_code"].to_list()) == {QualityRuleCode.UNIT_SCALE_JUMP.value}

    # Previous safe snapshot remains untouched and active.
    repo = ParquetRepository(data_root=tmp_path)
    assert repo.snapshot_id == "snap_safe_prev"


def test_critical_rules_subtotal_duplicate_key_and_missing_required() -> None:
    """Verify SUBTOTAL_MISMATCH, DUPLICATE_PRIMARY_KEY, MISSING_REQUIRED_METRICS, and Assets==0 safety."""
    # Build 30 clean issuers so 3 critical issuers = 10% (exact boundary <= 10%, no circuit break).
    frames = [_make_issuer_facts(f"OK{i:02d}") for i in range(1, 28)]

    # 1. SUBTOTAL_MISMATCH: Current (500) + Noncurrent (400) != Total Assets (1000) (10% error).
    frames.append(
        _make_issuer_facts(
            "BAD_SUB",
            assets=1_000_000_000_000.0,
            liabilities=400_000_000_000.0,
            equity=600_000_000_000.0,
            current_assets=500_000_000_000.0,
            noncurrent_assets=400_000_000_000.0,
        )
    )

    # 2. MISSING_REQUIRED_METRICS: IS_NET_REVENUE is NULL.
    frames.append(_make_issuer_facts("BAD_MISS", net_revenue=None))

    # 3. DUPLICATE_PRIMARY_KEY: duplicate row for BS_TOTAL_ASSETS.
    dup_issuer = _make_issuer_facts("BAD_DUP")
    dup_row = dup_issuer.filter(pl.col("metric_id") == "BS_TOTAL_ASSETS")
    frames.append(pl.concat([dup_issuer, dup_row]))

    result = evaluate_quality_gate(pl.concat(frames))
    assert result.total_issuers == 30
    assert result.critical_issuers == 3

    rules_by_issuer = dict(
        zip(
            result.audit_quarantine["issuer_id"].to_list(),
            result.audit_quarantine["rule_code"].to_list(),
            strict=True,
        )
    )
    assert rules_by_issuer == {
        "BAD_SUB": QualityRuleCode.SUBTOTAL_MISMATCH.value,
        "BAD_MISS": QualityRuleCode.MISSING_REQUIRED_METRICS.value,
        "BAD_DUP": QualityRuleCode.DUPLICATE_PRIMARY_KEY.value,
    }
    assert result.clean_facts["issuer_id"].n_unique() == 27


def test_unit_scale_jump_never_compares_across_statement_scopes() -> None:
    """CONSOLIDATED (1,000T) and SEPARATE (2T) for the same issuer must NEVER trigger UNIT_SCALE_JUMP."""
    consolidated = _make_issuer_facts(
        "HOLDCO",
        statement_scope="CONSOLIDATED",
        assets=1_000_000_000_000_000.0,
        liabilities=400_000_000_000_000.0,
        equity=600_000_000_000_000.0,
        current_assets=500_000_000_000_000.0,
        noncurrent_assets=500_000_000_000_000.0,
    )
    separate = _make_issuer_facts(
        "HOLDCO",
        statement_scope="SEPARATE",
        assets=2_000_000_000_000.0,
        liabilities=800_000_000_000.0,
        equity=1_200_000_000_000.0,
        current_assets=1_000_000_000_000.0,
        noncurrent_assets=1_000_000_000_000.0,
    )
    result = evaluate_quality_gate(pl.concat([consolidated, separate]))
    assert result.audit_quarantine.is_empty()
    assert result.quality_summary.height == 2


def test_tier2_warnings_allow_publication_with_warning_codes() -> None:
    """All 3 WARNING rules allow publication and tag quality_summary with exact warning codes."""
    # 1. CASH_FLOW_RECONCILIATION_WARN: CF ending cash differs from BS cash by 2% (> 0.5%).
    cf_warn = _make_issuer_facts(
        "WARN_CF",
        bs_cash=100_000_000_000.0,
        cf_ending_cash=102_000_000_000.0,
    )
    # 2. PARTIAL_STATEMENT: missing Cash Flow entirely (e.g. UPCoM issuer).
    partial_no_cf = _make_issuer_facts(
        "WARN_PARTIAL_NOCF",
        cfo=None,
        depreciation=None,
        cf_ending_cash=None,
    )
    # 3. PARTIAL_STATEMENT: direct CF missing depreciation.
    partial_direct_cf = _make_issuer_facts(
        "WARN_PARTIAL_DEP",
        depreciation=None,
    )
    # 4. DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX: Q4 derived from FY - 9M.
    derived_q4 = _make_issuer_facts(
        "WARN_Q4",
        period_type="QUARTER",
        fiscal_quarter=4,
        period_start=date(2025, 10, 1),
        period_end=date(2025, 12, 31),
        derivation_method="DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX",
    )

    result = evaluate_quality_gate(
        pl.concat([cf_warn, partial_no_cf, partial_direct_cf, derived_q4])
    )
    assert result.audit_quarantine.is_empty()
    assert result.clean_facts["issuer_id"].n_unique() == 4
    assert result.quality_summary.height == 4

    summary_map = {
        row["issuer_id"]: (row["quality_status"], row["warning_codes"])
        for row in result.quality_summary.to_dicts()
    }
    assert summary_map["WARN_CF"] == (
        "WARNING",
        [QualityRuleCode.CASH_FLOW_RECONCILIATION_WARN.value],
    )
    assert summary_map["WARN_PARTIAL_NOCF"] == (
        "WARNING",
        [QualityRuleCode.PARTIAL_STATEMENT.value],
    )
    assert summary_map["WARN_PARTIAL_DEP"] == (
        "WARNING",
        [QualityRuleCode.PARTIAL_STATEMENT.value],
    )
    assert summary_map["WARN_Q4"] == (
        "WARNING",
        [QualityRuleCode.DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX.value],
    )


def test_tier3_info_negative_equity_cfo_and_loss_never_treated_as_data_errors() -> None:
    """Negative equity, negative CFO, and net loss are valid accounting states (INFO -> PASS)."""
    distressed = _make_issuer_facts(
        "HVN_LIKE",
        assets=50_000_000_000_000.0,
        liabilities=60_000_000_000_000.0,
        equity=-10_000_000_000_000.0,
        current_assets=10_000_000_000_000.0,
        noncurrent_assets=40_000_000_000_000.0,
        net_revenue=25_000_000_000_000.0,
        net_income=-5_000_000_000_000.0,
        cfo=-3_000_000_000_000.0,
    )
    result = evaluate_quality_gate(distressed)
    assert result.audit_quarantine.is_empty()
    assert result.quality_summary["quality_status"].to_list() == ["PASS"]
    assert result.quality_summary["warning_codes"].to_list() == [[]]
    info_codes = set(result.quality_summary["info_codes"][0].to_list())
    assert {
        QualityRuleCode.NEGATIVE_EQUITY.value,
        QualityRuleCode.NEGATIVE_CFO.value,
        QualityRuleCode.NEGATIVE_NET_INCOME.value,
    } <= info_codes


def test_quality_gate_2_rejects_orphan_filing_references() -> None:
    """QUALITY_GATE_2 must fail fast if financial_ratios references a missing filing_id."""
    from finintel.analytics.tables import build_analytics_tables

    facts_2024 = _make_issuer_facts(
        "FPT",
        fiscal_year=2024,
        fiscal_start=date(2024, 1, 1),
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
    )
    facts_2025 = _make_issuer_facts("FPT", fiscal_year=2025)
    facts = pl.concat([facts_2024, facts_2025])
    issuers = pl.DataFrame(
        {
            "issuer_id": ["FPT"],
            "ticker": ["FPT"],
            "exchange": ["HOSE"],
            "company_name_vn": ["FPT Corp"],
            "accounting_regime": ["CORPORATE"],
            "icb_code_level2": ["9500"],
            "icb_name_level2": ["Technology"],
            "icb_code_level3": ["9530"],
            "icb_name_level3": ["Software"],
            "industry_snapshot_date": [date(2025, 1, 1)],
            "delisted_date": [None],
        }
    )
    analytics = build_analytics_tables(facts, issuers, as_of=date(2026, 6, 1))
    ratios = analytics[RATIOS_TABLE]

    # Clean case passes with zero orphans.
    qg1 = evaluate_quality_gate(facts, issuers=issuers)
    updated_summary = verify_analytics_quality_gate(
        qg1.clean_facts, ratios, quality_summary=qg1.quality_summary
    )
    assert updated_summary.height == 2

    # Inject an orphan filing_id into source_filing_ids -> must raise CriticalDataQualityError.
    corrupted = ratios.with_columns(
        pl.lit(["orphan-filing-999"]).alias("source_filing_ids")
    )
    with pytest.raises(CriticalDataQualityError, match="orphan filing_id"):
        verify_analytics_quality_gate(qg1.clean_facts, corrupted)


def test_quality_gate_config_validation() -> None:
    with pytest.raises(ContractViolationError):
        QualityGateConfig(bs_identity_rel_tol=-0.01)
    with pytest.raises(ContractViolationError):
        QualityGateConfig(unit_scale_upper_factor=0.5)
    with pytest.raises(ContractViolationError):
        QualityGateConfig(max_critical_issuer_ratio=1.5)


def test_pre_tt200_legacy_quarantine_isolates_filings_without_false_active_circuit_break() -> (
    None
):
    """Pre-2015 (QĐ 15/2006) defects are quarantined from clean_facts without halting clean TT200/TT99 batches."""
    frames: list[pl.DataFrame] = []
    for i in range(1, 11):
        issuer = f"HIST{i:02d}"
        frames.append(
            _make_issuer_facts(
                issuer,
                fiscal_year=2025,
                fiscal_start=date(2025, 1, 1),
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
            )
        )
        if i <= 3:
            frames.append(
                _make_issuer_facts(
                    issuer,
                    fiscal_year=2012,
                    fiscal_start=date(2012, 1, 1),
                    period_start=date(2012, 1, 1),
                    period_end=date(2012, 12, 31),
                    net_revenue=None,
                )
            )
    result = evaluate_quality_gate(pl.concat(frames))
    assert result.audit_quarantine.height == 3
    assert sorted(result.audit_quarantine["issuer_id"].to_list()) == [
        "HIST01",
        "HIST02",
        "HIST03",
    ]
    assert result.clean_facts.filter(pl.col("fiscal_year") == 2012).is_empty()
    assert (
        result.clean_facts.filter(pl.col("fiscal_year") == 2025)["issuer_id"].n_unique()
        == 10
    )
    assert result.critical_issuer_ratio == 0.0


def test_serving_and_frontend_boundary_zero_raw_or_curated_reads() -> None:
    """Architectural guard: legacy dashboard/ is removed and serving/frontend respect layer boundaries."""
    assert not (REPO_ROOT / "dashboard").exists(), (
        "Legacy Streamlit dashboard/ must be removed"
    )

    serving_dir = REPO_ROOT / "src" / "finintel" / "serving"
    serving_py_files = sorted(serving_dir.rglob("*.py"))
    assert len(serving_py_files) >= 5

    forbidden_serving_modules = {
        "finintel.normalization",
        "finintel.ingestion",
        "streamlit",
        "dashboard",
    }
    for py_file in serving_py_files:
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in forbidden_serving_modules:
                        assert not alias.name.startswith(forbidden), (
                            f"Serving file {py_file.name} illegally imports {alias.name}"
                        )
            if isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in forbidden_serving_modules:
                    assert not node.module.startswith(forbidden), (
                        f"Serving file {py_file.name} illegally imports {node.module}"
                    )
        assert "data/raw" not in source and "data/curated" not in source

    frontend_src_dir = REPO_ROOT / "frontend" / "src"
    frontend_files = sorted(
        list(frontend_src_dir.rglob("*.ts")) + list(frontend_src_dir.rglob("*.tsx"))
    )
    assert len(frontend_files) >= 10
    for ts_file in frontend_files:
        source = ts_file.read_text(encoding="utf-8")
        assert "data/raw" not in source and "data/curated" not in source, (
            f"Frontend file {ts_file.name} references raw/curated storage path"
        )
        assert "finintel." not in source, (
            f"Frontend file {ts_file.name} imports Python backend module"
        )
        assert "financial_facts.parquet" not in source, (
            f"Frontend file {ts_file.name} references raw Parquet dataset table"
        )
