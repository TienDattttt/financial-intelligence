"""TDD unit tests for V3-31..V3-32: Extension Port 4 bond signals, FX debt exposure, and IMF FX stress testing.

Verified Sources:
- spec/v3/SPEC-v3-fx-stress-and-bond-signals.md (§1, §4, §5, §6, §7)
- Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
  IMF Working Paper WP/15/216.
- Decree 153/2020/ND-CP & Decree 65/2022/ND-CP (HNX Cbonds corporate bond disclosure).
"""

from __future__ import annotations

import hashlib
import json
import socket
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import duckdb
import httpx
import polars as pl
import pytest

from finintel.exceptions import ContractViolationError
from finintel.ingestion.bond_signals_collector import (
    BOND_ALLOWED_HOSTS,
    HnxCbondsCollector,
    parse_hnx_cbonds_payload,
)
from finintel.pipeline.state_store import StateStore
from finintel.ports import (
    BOND_PORT_TABLES,
    CORPORATE_BOND_SIGNALS_KEY,
    CORPORATE_BOND_SIGNALS_SCHEMA,
    CORPORATE_BOND_SIGNALS_TABLE,
    FX_DEBT_EXPOSURE_KEY,
    FX_DEBT_EXPOSURE_SCHEMA,
    FX_DEBT_EXPOSURE_TABLE,
    CorporateBondSignalProvider,
    FxDebtExposureProvider,
    validate_corporate_bond_signals,
    validate_fx_debt_exposure,
)
from finintel.storage.ducklake_store import (
    EXTENSION_PORT_CATALOG_TABLES,
    MANAGED_MART_CATALOG_TABLES,
    DuckLakeStore,
    ensure_ducklake_attached,
)


def _sha256_hex(raw: bytes | str) -> str:
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    return hashlib.sha256(data).hexdigest()


def _sample_issuers() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issuer_id": ["VN_NVL", "VN_HPG"],
            "ticker": ["NVL", "HPG"],
            "exchange": ["HOSE", "HOSE"],
            "company_name_vn": ["Novaland", "Hoa Phat"],
            "accounting_regime": ["CORPORATE", "CORPORATE"],
            "icb_code_level2": ["8600", "1700"],
            "icb_name_level2": ["Bat dong san", "Tai nguyen co ban"],
            "icb_code_level3": ["8633", "1755"],
            "icb_name_level3": ["Phat trien BDS", "Thep"],
            "industry_snapshot_date": [date(2026, 1, 1), date(2026, 1, 1)],
            "delisted_date": pl.Series([None, None], dtype=pl.Date),
        }
    )


def _sample_facts() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issuer_id": ["VN_NVL", "VN_HPG"],
            "statement_scope": ["CONSOLIDATED", "CONSOLIDATED"],
            "period_type": ["QUARTER", "QUARTER"],
            "fiscal_year_start_date": [date(2025, 1, 1), date(2025, 1, 1)],
            "period_end_date": [date(2025, 12, 31), date(2025, 12, 31)],
            "metric_id": ["IS_NET_REVENUE", "IS_NET_REVENUE"],
            "taxonomy_version": ["VAS_TT200_2014", "VAS_TT200_2014"],
            "revision_no": [1, 1],
            "filing_id": ["filing-nvl-1", "filing-hpg-1"],
            "fiscal_year": [2025, 2025],
            "fiscal_quarter": pl.Series([4, 4], dtype=pl.Int8),
            "period_start_date": [date(2025, 10, 1), date(2025, 10, 1)],
            "published_at": [date(2026, 1, 25), date(2026, 1, 25)],
            "published_at_basis": ["EXACT_FILING", "EXACT_FILING"],
            "effective_available_at": [date(2026, 1, 25), date(2026, 1, 25)],
            "revision_reason": ["ORIGINAL", "ORIGINAL"],
            "derivation_method": ["REPORTED_DIRECTLY", "REPORTED_DIRECTLY"],
            "audit_status": ["UNAUDITED", "UNAUDITED"],
            "audit_opinion": ["UNKNOWN", "UNKNOWN"],
            "value": [1_000_000_000_000.0, 2_000_000_000_000.0],
            "ttm_value": pl.Series([None, None], dtype=pl.Float64),
            "computation_note": pl.Series([None, None], dtype=pl.String),
        }
    )


def _sample_bond_signals() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issuer_id": ["VN_NVL"],
            "ticker": ["NVL"],
            "as_of_date": [date(2026, 3, 15)],
            "outstanding_bond_vnd": [12_000_000_000_000.0],
            "bond_maturity_12m_vnd": [4_500_000_000_000.0],
            "bond_payment_delay_flag": [True],
            "source_provider": ["hnx_cbonds"],
            "source_url": ["https://cbonds.hnx.vn/api/bond-signals?ticker=NVL"],
            "payload_sha256": [_sha256_hex("nvl-bonds-2026-03-15")],
        },
        schema=CORPORATE_BOND_SIGNALS_SCHEMA,
    )


def _sample_fx_debt() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issuer_id": ["VN_NVL", "VN_HPG"],
            "ticker": ["NVL", "HPG"],
            "as_of_date": [date(2026, 3, 15), date(2026, 3, 15)],
            "statement_scope": ["CONSOLIDATED", "CONSOLIDATED"],
            "period_type": ["QUARTER", "QUARTER"],
            "net_fx_debt_vnd": pl.Series([1_000_000_000_000.0, None], dtype=pl.Float64),
            "fx_currency": pl.Series(["USD", None], dtype=pl.String),
            "source_provider": ["financial_notes", "financial_notes"],
            "source_url": [
                "https://cbonds.hnx.vn/notes/nvl-fx-2026",
                "https://cbonds.hnx.vn/notes/hpg-fx-2026",
            ],
            "payload_sha256": [_sha256_hex("nvl-fx"), _sha256_hex("hpg-fx")],
        },
        schema=FX_DEBT_EXPOSURE_SCHEMA,
    )


def test_port4_bond_and_fx_schemas_validate_provenance_and_bounds() -> None:
    """Port 4 contracts enforce Zero-Mock source_url, 64-hex payload_sha256, non-negative bonds, and protocols."""
    assert CORPORATE_BOND_SIGNALS_KEY == ("issuer_id", "as_of_date")
    assert FX_DEBT_EXPOSURE_KEY == (
        "issuer_id",
        "as_of_date",
        "statement_scope",
        "period_type",
    )
    assert BOND_PORT_TABLES == frozenset(
        {CORPORATE_BOND_SIGNALS_TABLE, FX_DEBT_EXPOSURE_TABLE}
    )

    valid_bonds = validate_corporate_bond_signals(_sample_bond_signals())
    assert valid_bonds.height == 1

    valid_fx = validate_fx_debt_exposure(_sample_fx_debt())
    assert valid_fx.height == 2
    assert valid_fx["net_fx_debt_vnd"].to_list() == [1_000_000_000_000.0, None]

    # Reject missing/empty source_url
    with pytest.raises(ContractViolationError, match="source_url"):
        validate_corporate_bond_signals(
            _sample_bond_signals().with_columns(pl.lit("  ").alias("source_url"))
        )

    # Reject invalid payload_sha256
    with pytest.raises(ContractViolationError, match="payload_sha256"):
        validate_corporate_bond_signals(
            _sample_bond_signals().with_columns(
                pl.lit("short-hash").alias("payload_sha256")
            )
        )

    # Reject negative outstanding_bond_vnd
    with pytest.raises(ContractViolationError, match="outstanding_bond_vnd"):
        validate_corporate_bond_signals(
            _sample_bond_signals().with_columns(
                pl.lit(-1.0).alias("outstanding_bond_vnd")
            )
        )

    # Reject negative bond_maturity_12m_vnd
    with pytest.raises(ContractViolationError, match="bond_maturity_12m_vnd"):
        validate_corporate_bond_signals(
            _sample_bond_signals().with_columns(
                pl.lit(-100.0).alias("bond_maturity_12m_vnd")
            )
        )

    # Reject duplicate primary keys
    with pytest.raises(ContractViolationError, match="duplicate"):
        validate_corporate_bond_signals(
            pl.concat([_sample_bond_signals(), _sample_bond_signals()])
        )

    # Reject non-finite net_fx_debt_vnd or invalid sha256 on fx_debt_exposure
    with pytest.raises(ContractViolationError, match="net_fx_debt_vnd"):
        validate_fx_debt_exposure(
            _sample_fx_debt().with_columns(
                pl.lit(float("nan")).alias("net_fx_debt_vnd")
            )
        )
    with pytest.raises(ContractViolationError, match="payload_sha256"):
        validate_fx_debt_exposure(
            _sample_fx_debt().with_columns(pl.lit("bad").alias("payload_sha256"))
        )

    class _StubBondProvider:
        def fetch_bond_signals(
            self,
            *,
            tickers: Sequence[str] | None = None,
            as_of: date | None = None,
        ) -> pl.DataFrame:
            _ = (tickers, as_of)
            return _sample_bond_signals()

    class _StubFxProvider:
        def fetch_fx_debt_exposure(
            self,
            *,
            tickers: Sequence[str] | None = None,
            as_of: date | None = None,
        ) -> pl.DataFrame:
            _ = (tickers, as_of)
            return _sample_fx_debt()

    assert isinstance(_StubBondProvider(), CorporateBondSignalProvider)
    assert isinstance(_StubFxProvider(), FxDebtExposureProvider)


def test_ducklake_publish_snapshot_preserves_port4_tables_b03(tmp_path: Path) -> None:
    """B-03: DuckLakeStore.publish_snapshot() MUST never DROP TABLE corporate_bond_signals or fx_debt_exposure."""
    assert "corporate_bond_signals" in EXTENSION_PORT_CATALOG_TABLES
    assert "fx_debt_exposure" in EXTENSION_PORT_CATALOG_TABLES
    assert MANAGED_MART_CATALOG_TABLES.isdisjoint(EXTENSION_PORT_CATALOG_TABLES)

    data_root = tmp_path / "data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    store = DuckLakeStore(
        catalog_path=data_root / "metadata" / "ducklake_catalog.sqlite",
        data_path=data_root / "lakehouse" / "data",
        state_store=state,
    )

    state.start_run("run-p4-1", "CLI_MANUAL", {}, None)
    store.publish_snapshot(
        "snap_p4_001",
        {
            "issuers.parquet": _sample_issuers(),
            "financial_facts.parquet": _sample_facts(),
        },
        run_id="run-p4-1",
    )
    state.finish_run("run-p4-1", "SUCCEEDED", "snap_p4_001")

    con = store._open_rw_connection()
    try:
        bonds_df = _sample_bond_signals()
        fx_df = _sample_fx_debt()
        con.register("_stage_bonds", bonds_df)
        con.register("_stage_fx", fx_df)
        con.execute(
            f'CREATE TABLE "{store.catalog_alias}"."corporate_bond_signals" '
            'AS SELECT * FROM "_stage_bonds";'
        )
        con.execute(
            f'CREATE TABLE "{store.catalog_alias}"."fx_debt_exposure" '
            'AS SELECT * FROM "_stage_fx";'
        )
    finally:
        con.close()

    # Rotate snapshot without Port 4 tables in the mart payload
    state.start_run("run-p4-2", "CLI_MANUAL", {}, None)
    store.publish_snapshot(
        "snap_p4_002",
        {
            "issuers.parquet": _sample_issuers(),
            "financial_facts.parquet": _sample_facts(),
        },
        run_id="run-p4-2",
    )
    state.finish_run("run-p4-2", "SUCCEEDED", "snap_p4_002")

    con_ro = duckdb.connect(":memory:")
    try:
        ensure_ducklake_attached(
            con_ro,
            catalog_path=store.catalog_path,
            data_path=store.data_path,
            catalog_alias=store.catalog_alias,
            read_only=True,
        )
        tables = {
            str(row[0])
            for row in con_ro.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_catalog = ? AND table_schema = 'main'",
                [store.catalog_alias],
            ).fetchall()
        }
        assert "corporate_bond_signals" in tables
        assert "fx_debt_exposure" in tables
        assert con_ro.execute(
            f'SELECT COUNT(*) FROM "{store.catalog_alias}"."corporate_bond_signals"'
        ).fetchone() == (1,)
        assert con_ro.execute(
            f'SELECT COUNT(*) FROM "{store.catalog_alias}"."fx_debt_exposure"'
        ).fetchone() == (2,)
    finally:
        con_ro.close()


def test_hnx_cbonds_collector_ssrf_guard_sha256_and_pit_aggregation() -> None:
    """BondSignalsCollector enforces ssrf_guard, computes real SHA-256, and aggregates tranches PIT-safely."""

    def _public_resolver(
        _host: str, _port: int, **_: object
    ) -> list[tuple[object, ...]]:
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("103.1.2.3", 443),
            )
        ]

    def _private_resolver(
        _host: str, _port: int, **_: object
    ) -> list[tuple[object, ...]]:
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("169.254.169.254", 443),
            )
        ]

    payload_dict = {
        "ticker": "NVL",
        "tranches": [
            {
                "bond_code": "NVLH2126001",
                "issue_date": "2021-09-01",
                "maturity_date": "2026-09-01",
                "outstanding_value_vnd": 1_500_000_000_000.0,
                "payment_delay_flag": True,
            },
            {
                "bond_code": "NVLH2228002",
                "issue_date": "2022-06-01",
                "maturity_date": "2028-06-01",
                "outstanding_value_vnd": 2_500_000_000_000.0,
                "payment_delay_flag": False,
            },
            {
                "bond_code": "NVL_FUTURE_NO_LEAK",
                "issue_date": "2026-04-01",
                "maturity_date": "2027-04-01",
                "outstanding_value_vnd": 9_000_000_000_000.0,
                "payment_delay_flag": False,
            },
        ],
    }
    raw_bytes = json.dumps(payload_dict).encode("utf-8")
    expected_sha = _sha256_hex(raw_bytes)

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host in BOND_ALLOWED_HOSTS
        return httpx.Response(200, content=raw_bytes)

    client = httpx.Client(
        transport=httpx.MockTransport(_handler), follow_redirects=False
    )
    collector = HnxCbondsCollector(
        client=client,
        resolver=_public_resolver,
        min_request_interval_seconds=0.0,
    )
    as_of = date(2026, 3, 15)
    df = collector.fetch_bond_signals(
        tickers=["NVL"],
        as_of=as_of,
        issuer_id_by_ticker={"NVL": "VN_NVL"},
    )
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["issuer_id"] == "VN_NVL"
    assert row["ticker"] == "NVL"
    assert row["as_of_date"] == as_of
    # Future tranche issued 2026-04-01 (> 2026-03-15) excluded -> 1.5T + 2.5T = 4.0T
    assert row["outstanding_bond_vnd"] == pytest.approx(4_000_000_000_000.0)
    # Only NVLH2126001 matures within 365 days of 2026-03-15 -> 1.5T
    assert row["bond_maturity_12m_vnd"] == pytest.approx(1_500_000_000_000.0)
    assert row["bond_payment_delay_flag"] is True
    assert row["payload_sha256"] == expected_sha
    assert row["source_url"].startswith("https://cbonds.hnx.vn/")

    # SSRF guard: private/metadata DNS IP blocked
    ssrf_collector = HnxCbondsCollector(
        client=client,
        resolver=_private_resolver,
        min_request_interval_seconds=0.0,
    )
    with pytest.raises(ContractViolationError, match="SSRF"):
        ssrf_collector.fetch_bond_signals(tickers=["NVL"], as_of=as_of)

    # SSRF guard: unallowlisted host or non-HTTPS blocked
    with pytest.raises(ContractViolationError):
        HnxCbondsCollector(
            url_template="http://cbonds.hnx.vn/api?ticker={ticker}",
            min_request_interval_seconds=0.0,
        )
    with pytest.raises(ContractViolationError):
        HnxCbondsCollector(
            url_template="https://evil.example.com/api?ticker={ticker}",
            min_request_interval_seconds=0.0,
        )

    # Direct parser enforces non-empty source_url
    with pytest.raises(ContractViolationError, match="source_url"):
        parse_hnx_cbonds_payload(
            raw_bytes,
            issuer_id="VN_NVL",
            ticker="NVL",
            as_of_date=as_of,
            source_provider="hnx_cbonds",
            source_url="",
        )


def _stress_fixture_frame() -> pl.DataFrame:
    bn = 1_000_000_000.0
    return pl.DataFrame(
        {
            "issuer_id": ["FX_DISCLOSED", "FX_UNDISCLOSED", "FX_BANK"],
            "ticker": ["NVL", "FPT", "VCB"],
            "company_name_vn": ["Novaland", "FPT", "Vietcombank"],
            "icb_code_level2": ["8600", "9500", "8300"],
            "icb_name_level2": ["Bat dong san", "Cong nghe", "Ngan hang"],
            "accounting_regime": ["CORPORATE", "CORPORATE", "BANK"],
            "statement_scope": ["CONSOLIDATED", "CONSOLIDATED", "CONSOLIDATED"],
            "period_type": ["QUARTER", "QUARTER", "QUARTER"],
            "period_end_date": [date(2026, 3, 31)] * 3,
            "net_revenue_ttm": [5_000.0 * bn, 10_000.0 * bn, 20_000.0 * bn],
            "gross_profit_ttm": [1_500.0 * bn, 4_000.0 * bn, 10_000.0 * bn],
            "cogs_ttm": [3_500.0 * bn, 6_000.0 * bn, 10_000.0 * bn],
            "cfo_ttm": [400.0 * bn, 1_500.0 * bn, 3_000.0 * bn],
            "borrowing_cost_ttm": [100.0 * bn, 50.0 * bn, 200.0 * bn],
            "total_debt": [3_000.0 * bn, 1_000.0 * bn, 5_000.0 * bn],
            "ebit_ttm": [180.0 * bn, 800.0 * bn, 2_000.0 * bn],
            "gross_margin": [0.30, 0.40, 0.50],
            "effective_tax_rate": [0.20, 0.20, 0.20],
            "interest_coverage": [1.8, 16.0, None],
            "interest_coverage_flag": ["VALID", "VALID", "NOT_APPLICABLE_FOR_REGIME"],
            "net_fx_debt_vnd": pl.Series(
                [1_000.0 * bn, None, 500.0 * bn], dtype=pl.Float64
            ),
        }
    )


def test_fx_stress_test_zero_regression_exact_math_and_null_preservation() -> None:
    """V3-32: fx=0.0 matches V2 exactly; fx=0.10 computes 100B loss on 1000B FX debt; undisclosed stays NULL/NOT_DISCLOSED."""
    import math

    from finintel.analytics.stress_fx import evaluate_bond_liquidity_signal
    from finintel.analytics.stress_test import StressScenario, simulate_financial_stress

    bn = 1_000_000_000.0
    df = _stress_fixture_frame()
    df_no_fx_col = df.drop("net_fx_debt_vnd")

    # 1. Bounds validation on fx_depreciation_pct
    for bad_fx in (-0.31, 0.51, float("nan"), float("inf")):
        with pytest.raises(ContractViolationError):
            StressScenario(name="bad_fx", fx_depreciation_pct=bad_fx)

    # 2. 0% regression at fx_depreciation_pct = 0.0
    v2_baseline = simulate_financial_stress(
        df_no_fx_col,
        StressScenario(
            name="v2",
            interest_rate_shock_bps=100.0,
            revenue_shock_pct=-0.05,
        ),
    )
    v3_zero_fx = simulate_financial_stress(
        df,
        StressScenario(
            name="v2",
            interest_rate_shock_bps=100.0,
            revenue_shock_pct=-0.05,
            fx_depreciation_pct=0.0,
        ),
    )
    for col in (
        "stressed_net_revenue_ttm",
        "stressed_ebit_ttm",
        "stressed_borrowing_cost_ttm",
        "stressed_interest_coverage",
        "stressed_cfo_ttm",
        "baseline_debt_at_risk",
        "stressed_debt_at_risk",
        "newly_distressed_under_shock",
    ):
        assert v3_zero_fx[col].to_list() == v2_baseline[col].to_list()

    # 3. fx_depreciation_pct = 0.10 on net_fx_debt_vnd = 1,000 tỷ -> loss = 100 tỷ
    fx10 = simulate_financial_stress(
        df,
        StressScenario(name="fx10", fx_depreciation_pct=0.10),
    )
    rows = {r["issuer_id"]: r for r in fx10.to_dicts()}

    disclosed = rows["FX_DISCLOSED"]
    assert disclosed["fx_coverage_status"] == "REPORTED"
    assert disclosed["net_fx_debt_vnd"] == pytest.approx(1_000.0 * bn)
    assert disclosed["fx_revaluation_loss_vnd"] == pytest.approx(100.0 * bn)
    # Borrowing cost: 100B + 100B = 200B; EBIT: 180B -> ICR = 180 / 200 = 0.90x < 1.0 -> newly distressed!
    assert disclosed["stressed_borrowing_cost_ttm"] == pytest.approx(200.0 * bn)
    assert disclosed["stressed_interest_coverage"] == pytest.approx(0.90)
    assert disclosed["baseline_debt_at_risk"] is False
    assert disclosed["stressed_debt_at_risk"] is True
    assert disclosed["newly_distressed_under_shock"] is True

    # Undisclosed issuer: NULL preserved, NOT_DISCLOSED, zero NaN, zero FX impact
    undisclosed = rows["FX_UNDISCLOSED"]
    assert undisclosed["fx_coverage_status"] == "NOT_DISCLOSED"
    assert undisclosed["net_fx_debt_vnd"] is None
    assert undisclosed["fx_revaluation_loss_vnd"] is None
    assert undisclosed["stressed_borrowing_cost_ttm"] == pytest.approx(50.0 * bn)
    assert undisclosed["stressed_interest_coverage"] == pytest.approx(16.0)
    for val in undisclosed.values():
        if isinstance(val, float):
            assert math.isfinite(val)

    # Non-corporate BANK row: ineligible, fx_revaluation_loss_vnd stays None
    bank = rows["FX_BANK"]
    assert bank["fx_revaluation_loss_vnd"] is None
    assert bank["stressed_interest_coverage_flag"] == "NOT_APPLICABLE_FOR_REGIME"

    # 4. Bond liquidity signal calculation
    covered = evaluate_bond_liquidity_signal(
        outstanding_bond_vnd=500.0 * bn,
        bond_maturity_12m_vnd=100.0 * bn,
        bond_payment_delay_flag=False,
        total_cash_and_st_investments_vnd=400.0 * bn,
    )
    assert covered.bond_maturity_to_cash_ratio == pytest.approx(0.25)
    assert covered.liquidity_pressure_status == "COVERED"

    delayed = evaluate_bond_liquidity_signal(
        outstanding_bond_vnd=500.0 * bn,
        bond_maturity_12m_vnd=300.0 * bn,
        bond_payment_delay_flag=True,
        total_cash_and_st_investments_vnd=0.0,
    )
    assert delayed.bond_maturity_to_cash_ratio is None
    assert delayed.liquidity_pressure_status == "PAYMENT_DELAY"


def test_serving_fx_stress_and_v3_bond_signals_endpoint(tmp_path: Path) -> None:
    """V3-32 Serving: POST /api/v1/stress-test FX propagation and GET /api/v3/companies/{ticker}/bond-signals."""
    pytest.importorskip("fastapi")
    pytest.importorskip("pydantic")

    from fastapi.testclient import TestClient

    from finintel.analytics.tables import RATIOS_TABLE, build_analytics_tables
    from finintel.quality.gate import evaluate_quality_gate
    from finintel.serving.api import create_app
    from finintel.storage.snapshot_publisher import SnapshotPublisher

    issuers = _sample_issuers()
    vn_bn = 1_000_000_000.0
    period_rows: list[dict[str, object]] = []
    for issuer_id, rev, gp, ebit, bc, debt, cash in (
        (
            "VN_NVL",
            2_000.0 * vn_bn,
            600.0 * vn_bn,
            180.0 * vn_bn,
            100.0 * vn_bn,
            3_000.0 * vn_bn,
            2_000.0 * vn_bn,
        ),
        (
            "VN_HPG",
            5_000.0 * vn_bn,
            1_200.0 * vn_bn,
            800.0 * vn_bn,
            50.0 * vn_bn,
            1_000.0 * vn_bn,
            3_000.0 * vn_bn,
        ),
    ):
        for q_idx, (p_start, p_end) in enumerate(
            [
                (date(2025, 1, 1), date(2025, 3, 31)),
                (date(2025, 4, 1), date(2025, 6, 30)),
                (date(2025, 7, 1), date(2025, 9, 30)),
                (date(2025, 10, 1), date(2025, 12, 31)),
            ],
            start=1,
        ):
            metrics = {
                "BS_TOTAL_ASSETS": 10_000.0 * vn_bn,
                "BS_TOTAL_EQUITY": 4_000.0 * vn_bn,
                "BS_TOTAL_LIABILITIES": 6_000.0 * vn_bn,
                "BS_CURRENT_ASSETS": 5_000.0 * vn_bn,
                "BS_CURRENT_LIABILITIES": 2_500.0 * vn_bn,
                "BS_CASH_AND_EQUIVALENTS": cash,
                "BS_SHORT_TERM_BORROWINGS": debt / 2.0,
                "BS_LONG_TERM_BORROWINGS": debt / 2.0,
                "IS_NET_REVENUE": rev / 4.0,
                "IS_COST_OF_GOODS_SOLD": (rev - gp) / 4.0,
                "IS_GROSS_PROFIT": gp / 4.0,
                "IS_OPERATING_PROFIT": ebit / 4.0,
                "IS_PROFIT_BEFORE_TAX": (ebit - bc) / 4.0,
                "IS_INTEREST_EXPENSE": bc / 4.0,
                "IS_CURRENT_INCOME_TAX": max(0.0, (ebit - bc) * 0.2 / 4.0),
                "IS_DEFERRED_INCOME_TAX": 0.0,
                "IS_NET_INCOME": (ebit - bc) * 0.8 / 4.0,
                "IS_NET_INCOME_PARENT": (ebit - bc) * 0.8 / 4.0,
                "CF_OPERATING_CASH_FLOW": 100.0 * vn_bn,
                "CF_DEPRECIATION_AMORTIZATION": 20.0 * vn_bn,
            }
            for metric_id, val in metrics.items():
                period_rows.append(
                    {
                        "issuer_id": issuer_id,
                        "statement_scope": "CONSOLIDATED",
                        "period_type": "QUARTER",
                        "fiscal_year": 2025,
                        "fiscal_quarter": q_idx,
                        "fiscal_year_start_date": date(2025, 1, 1),
                        "period_start_date": p_start,
                        "period_end_date": p_end,
                        "published_at": date(2026, 1, 25),
                        "effective_available_at": date(2026, 1, 25),
                        "published_at_basis": "EXACT_FILING",
                        "revision_no": 1,
                        "filing_id": f"{issuer_id}-2025-Q{q_idx}",
                        "source_filing_ids": [f"{issuer_id}-2025-Q{q_idx}"],
                        "source_provider": "fhsc",
                        "taxonomy_version": "VAS_TT200_2014",
                        "revision_reason": "ORIGINAL",
                        "audit_status": "UNAUDITED",
                        "audit_opinion": "UNMODIFIED",
                        "derivation_method": "REPORTED_DIRECTLY",
                        "metric_id": metric_id,
                        "value": val,
                        "value_type": "STOCK"
                        if metric_id.startswith("BS_")
                        else "FLOW",
                        "comparable_across_taxonomy": True,
                        "is_filed_late": False,
                    }
                )

    facts = pl.DataFrame(period_rows)
    qg1 = evaluate_quality_gate(facts, issuers=issuers)
    tables = build_analytics_tables(qg1.clean_facts, issuers, as_of=date(2026, 4, 1))
    data_root = tmp_path / "data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    state.start_run("run-srv-1", "CLI_MANUAL", {}, None)
    pub = SnapshotPublisher(data_root / "published" / "snapshots", state_store=state)
    pub.publish(
        "snap_srv_001",
        {
            "issuers.parquet": issuers,
            "financial_facts.parquet": qg1.clean_facts,
            **tables,
        },
        run_id="run-srv-1",
    )
    state.finish_run("run-srv-1", "SUCCEEDED", "snap_srv_001")

    bonds_dir = data_root / "bonds"
    bonds_dir.mkdir(parents=True, exist_ok=True)
    # NVL has bond signals; HPG has no bond row -> NO_BONDS empty state.
    _sample_bond_signals().with_columns(
        pl.lit(date(2025, 12, 31)).alias("as_of_date")
    ).write_parquet(bonds_dir / CORPORATE_BOND_SIGNALS_TABLE)
    _sample_fx_debt().with_columns(
        pl.lit(date(2025, 12, 31)).alias("as_of_date")
    ).write_parquet(bonds_dir / FX_DEBT_EXPOSURE_TABLE)

    app = create_app(data_root=data_root)
    with TestClient(app) as client:
        # 1. Stress test with fx_depreciation_pct=0.10
        r_fx = client.post(
            "/api/v1/stress-test",
            json={
                "scenario_name": "FX +10%",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": "2025-12-31",
                "fx_depreciation_pct": 0.10,
            },
        )
        assert r_fx.status_code == 200
        body = r_fx.json()
        assert body["fx_depreciation_pct"] == pytest.approx(0.10)
        assert body["fx_disclosed_issuers_count"] == 1
        by_ticker = {it["ticker"]: it for it in body["top_stressed_issuers"]}
        assert by_ticker["NVL"]["fx_coverage_status"] == "REPORTED"
        assert by_ticker["NVL"]["net_fx_debt_vnd"] == pytest.approx(1_000.0 * vn_bn)
        assert by_ticker["NVL"]["fx_revaluation_loss_vnd"] == pytest.approx(
            100.0 * vn_bn
        )
        assert by_ticker["HPG"]["fx_coverage_status"] == "NOT_DISCLOSED"
        assert by_ticker["HPG"]["net_fx_debt_vnd"] is None
        assert by_ticker["HPG"]["fx_revaluation_loss_vnd"] is None

        # Out-of-bounds fx_depreciation_pct -> 422
        r_bad = client.post(
            "/api/v1/stress-test",
            json={"scenario_name": "Invalid FX", "fx_depreciation_pct": 0.75},
        )
        assert r_bad.status_code == 422

        # 2. GET /api/v3/companies/{ticker}/bond-signals (disclosed vs NO_BONDS vs 404 vs 304)
        r_nvl = client.get("/api/v3/companies/NVL/bond-signals")
        assert r_nvl.status_code == 200
        nvl_bond = r_nvl.json()
        assert nvl_bond["ticker"] == "NVL"
        assert nvl_bond["has_outstanding_bonds"] is True
        assert nvl_bond["outstanding_bond_vnd"] == pytest.approx(12_000.0 * vn_bn)
        assert nvl_bond["bond_maturity_12m_vnd"] == pytest.approx(4_500.0 * vn_bn)
        assert nvl_bond["bond_maturity_to_cash_ratio"] == pytest.approx(2.25)
        assert nvl_bond["bond_payment_delay_flag"] is True
        assert nvl_bond["liquidity_pressure_status"] == "PAYMENT_DELAY"
        assert nvl_bond["source_url"].startswith("https://cbonds.hnx.vn/")

        r_hpg = client.get("/api/v3/companies/HPG/bond-signals")
        assert r_hpg.status_code == 200
        hpg_bond = r_hpg.json()
        assert hpg_bond["has_outstanding_bonds"] is False
        assert hpg_bond["liquidity_pressure_status"] == "NO_BONDS"

        r_304 = client.get(
            "/api/v3/companies/NVL/bond-signals",
            headers={"If-None-Match": r_nvl.headers["ETag"]},
        )
        assert r_304.status_code == 304

        r_404 = client.get("/api/v3/companies/NOPE/bond-signals")
        assert r_404.status_code == 404
        _ = RATIOS_TABLE
