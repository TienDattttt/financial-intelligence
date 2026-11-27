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
