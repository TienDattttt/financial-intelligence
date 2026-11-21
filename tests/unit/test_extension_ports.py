"""Unit tests for Extension Ports 1, 2, and 3 and DuckLake managed-table boundaries (ARCH-09)."""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Sequence
from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import duckdb
import polars as pl
import pytest

from finintel.exceptions import ContractViolationError
from finintel.orchestration.assets import PublishMartsConfig, _resolve_ml_config
from finintel.orchestration.resources import FinIntelPathsResource
from finintel.pipeline.state_store import StateStore
from finintel.ports import (
    DOCUMENT_CHUNKS_CONTRACT,
    MARKET_PRICES_SCHEMA,
    MARKET_PRICES_TABLE,
    PDF_CDN_ALLOWED_HOSTS,
    PIT_PRICE_JOIN_STRATEGY,
    REGULATORY_EVENTS_SCHEMA,
    REGULATORY_EVENTS_TABLE,
    DocumentChunk,
    DocumentRetriever,
    MarketPriceProvider,
    RegulatoryEventProvider,
    align_events_to_market_prices_pit,
    extract_pdf_members_from_zip,
    validate_document_chunks,
    validate_market_prices,
    validate_pdf_cdn_url,
    validate_regulatory_events,
)
from finintel.storage.ducklake_store import (
    EXTENSION_PORT_CATALOG_TABLES,
    MANAGED_MART_CATALOG_TABLES,
    MANAGED_MART_TABLES,
    DuckLakeStore,
)
from finintel.storage.schemas import EXTENSION_PORT_TABLES


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sample_issuers() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issuer_id": ["VN_FPT"],
            "ticker": ["FPT"],
            "exchange": ["HOSE"],
            "company_name_vn": ["FPT Corp"],
            "accounting_regime": ["CORPORATE"],
            "icb_code_level2": ["9500"],
            "icb_name_level2": ["Tech"],
            "icb_code_level3": ["9530"],
            "icb_name_level3": ["Software"],
            "industry_snapshot_date": [date(2026, 1, 1)],
            "delisted_date": pl.Series([None], dtype=pl.Date),
        }
    )


def _sample_facts() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issuer_id": ["VN_FPT"],
            "statement_scope": ["CONSOLIDATED"],
            "period_type": ["QUARTER"],
            "fiscal_year_start_date": [date(2025, 1, 1)],
            "period_end_date": [date(2025, 12, 31)],
            "metric_id": ["IS_NET_REVENUE"],
            "taxonomy_version": ["VAS_TT200_2014"],
            "revision_no": [1],
            "filing_id": ["filing-fpt-1"],
            "fiscal_year": [2025],
            "fiscal_quarter": pl.Series([4], dtype=pl.Int8),
            "period_start_date": [date(2025, 10, 1)],
            "published_at": [date(2026, 1, 25)],
            "published_at_basis": ["EXACT_FILING"],
            "effective_available_at": [date(2026, 1, 25)],
            "revision_reason": ["ORIGINAL"],
            "derivation_method": ["REPORTED_DIRECTLY"],
            "audit_status": ["UNAUDITED"],
            "audit_opinion": ["UNKNOWN"],
            "value": [1000.0],
            "ttm_value": pl.Series([None], dtype=pl.Float64),
            "computation_note": pl.Series([None], dtype=pl.String),
        }
    )


def _sample_regulatory_events() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issuer_id": ["VN_HNG", "VN_POM"],
            "event_date": [date(2024, 4, 1), date(2023, 5, 10)],
            "effective_from": [date(2024, 4, 5), date(2023, 5, 15)],
            "resolution_date": pl.Series([None, date(2024, 5, 10)], dtype=pl.Date),
            "event_type": ["CONTROL", "FORCED_DELISTING"],
            "event_stage": ["FIRST_ENTRY", "ESCALATION"],
            "exchange_delisted_from": pl.Series([None, "HOSE"], dtype=pl.String),
            "source_provider": ["vietcap_iq", "cafef_events"],
            "source_url": [
                "https://iq.vietcap.com.vn/news/1",
                "https://cafef.vn/news/2",
            ],
            "raw_headline": ["Dua vao dien kiem soat", "Huy niem yet bat buoc"],
            "payload_sha256": [_sha256("ev1"), _sha256("ev2")],
        }
    )


# ---------------------------------------------------------------------------
# B-03: MANAGED_MART_TABLES boundary & DuckLake DROP TABLE safety
# ---------------------------------------------------------------------------


def test_ducklake_publish_snapshot_never_drops_extension_port_tables(
    tmp_path: Path,
) -> None:
    assert len(MANAGED_MART_TABLES) == 10
    assert EXTENSION_PORT_TABLES == frozenset(
        {REGULATORY_EVENTS_TABLE, MARKET_PRICES_TABLE}
    )
    assert MANAGED_MART_TABLES.isdisjoint(EXTENSION_PORT_TABLES)
    assert MANAGED_MART_CATALOG_TABLES.isdisjoint(EXTENSION_PORT_CATALOG_TABLES)

    data_root = tmp_path / "data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    store = DuckLakeStore(
        catalog_path=data_root / "metadata" / "ducklake_catalog.sqlite",
        data_path=data_root / "lakehouse" / "data",
        state_store=state,
    )

    unmapped = pl.DataFrame(
        {
            "issuer_id": ["VN_FPT"],
            "filing_id": ["filing-fpt-1"],
            "raw_metric_key": ["X_RAW"],
            "raw_value": ["10"],
            "statement_scope": ["CONSOLIDATED"],
            "period_type": ["QUARTER"],
            "period_end_date": [date(2025, 12, 31)],
            "effective_available_at": [date(2026, 1, 25)],
        }
    )
    state.start_run("run-1", "CLI_MANUAL", {}, None)
    store.publish_snapshot(
        "snap_001",
        {
            "issuers.parquet": _sample_issuers(),
            "financial_facts.parquet": _sample_facts(),
            "unmapped_metrics.parquet": unmapped,
        },
        run_id="run-1",
    )
    state.finish_run("run-1", "SUCCEEDED", "snap_001")

    # Create independent Extension Port tables in the same DuckLake catalog.
    con = store._open_rw_connection()
    try:
        reg_df = _sample_regulatory_events()
        prices_df = pl.DataFrame(
            {
                "issuer_id": ["VN_FPT"],
                "ticker": ["FPT"],
                "trade_date": [date(2026, 1, 26)],
                "close_raw": [140_000.0],
                "close_adj": [112_000.0],
                "volume": [1_500_000.0],
                "shares_outstanding_market": [1_471_000_000.0],
                "source_provider": ["cafef_eod"],
            }
        )
        con.register("_stage_reg", reg_df)
        con.register("_stage_px", prices_df)
        con.execute(
            f'CREATE TABLE "{store.catalog_alias}"."regulatory_events" '
            'AS SELECT * FROM "_stage_reg";'
        )
        con.execute(
            f'CREATE TABLE "{store.catalog_alias}"."market_prices" '
            'AS SELECT * FROM "_stage_px";'
        )
    finally:
        con.close()

    # Publish snap_002 without unmapped_metrics and without Port tables.
    state.start_run("run-2", "CLI_MANUAL", {}, None)
    store.publish_snapshot(
        "snap_002",
        {
            "issuers.parquet": _sample_issuers(),
            "financial_facts.parquet": _sample_facts(),
        },
        run_id="run-2",
    )
    state.finish_run("run-2", "SUCCEEDED", "snap_002")

    con_ro = duckdb.connect(":memory:")
    try:
        from finintel.storage.ducklake_store import ensure_ducklake_attached

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
        assert "regulatory_events" in tables
        assert "market_prices" in tables
        assert "unmapped_metrics" not in tables

        reg_count = con_ro.execute(
            f'SELECT COUNT(*) FROM "{store.catalog_alias}"."regulatory_events"'
        ).fetchone()
        px_count = con_ro.execute(
            f'SELECT COUNT(*) FROM "{store.catalog_alias}"."market_prices"'
        ).fetchone()
        assert reg_count == (2,)
        assert px_count == (1,)
    finally:
        con_ro.close()


# ---------------------------------------------------------------------------
# Port 1: Regulatory Events (H-05) & PublishMartsConfig
# ---------------------------------------------------------------------------


def test_port1_regulatory_events_schema_and_protocol() -> None:
    expected_cols = {
        "issuer_id",
        "event_date",
        "effective_from",
        "resolution_date",
        "event_type",
        "event_stage",
        "exchange_delisted_from",
        "source_provider",
        "source_url",
        "raw_headline",
        "payload_sha256",
    }
    assert set(REGULATORY_EVENTS_SCHEMA.names()) == expected_cols

    valid = validate_regulatory_events(_sample_regulatory_events())
    assert valid.height == 2

    class _StubProvider:
        def fetch_events(
            self, *, tickers: Sequence[str] | None = None, as_of: date | None = None
        ) -> pl.DataFrame:
            _ = (tickers, as_of)
            return _sample_regulatory_events()

    assert isinstance(_StubProvider(), RegulatoryEventProvider)

    # Inverted interval (resolution_date < effective_from) fails.
    inverted = _sample_regulatory_events().with_columns(
        pl.lit(date(2020, 1, 1)).alias("resolution_date")
    )
    with pytest.raises(
        ContractViolationError, match="resolution_date >= effective_from"
    ):
        validate_regulatory_events(inverted)

    # Invalid event_stage fails.
    bad_stage = _sample_regulatory_events().with_columns(
        pl.lit("INVALID_STAGE").alias("event_stage")
    )
    with pytest.raises(ContractViolationError, match="event_stage"):
        validate_regulatory_events(bad_stage)

    # Invalid payload_sha256 fails.
    bad_hash = _sample_regulatory_events().with_columns(
        pl.lit("not-a-sha256").alias("payload_sha256")
    )
    with pytest.raises(ContractViolationError, match="payload_sha256"):
        validate_regulatory_events(bad_hash)


def test_publish_marts_config_unlocks_target_mode_and_confined_regulatory_events(
    tmp_path: Path,
) -> None:
    paths = FinIntelPathsResource(project_root=str(tmp_path))
    curated = tmp_path / "data" / "curated"
    curated.mkdir(parents=True)
    reg_path = curated / "regulatory_events.parquet"
    _sample_regulatory_events().write_parquet(reg_path)

    default_cfg = PublishMartsConfig()
    assert default_cfg.target_mode == "FUNDAMENTAL_DISTRESS"
    assert default_cfg.regulatory_events_path is None
    assert _resolve_ml_config(default_cfg, paths=paths).enabled is False

    with pytest.raises(ContractViolationError, match="target_mode"):
        _resolve_ml_config(PublishMartsConfig(target_mode="INVALID_MODE"), paths=paths)

    with pytest.raises(
        ContractViolationError, match="regulatory_events_path is required"
    ):
        _resolve_ml_config(
            PublishMartsConfig(
                ml_enabled=True,
                ml_approved_target=True,
                ml_validation_start="2023-01-01",
                ml_test_start="2024-01-01",
                target_mode="REGULATORY_DISTRESS",
            ),
            paths=paths,
        )

    for bad_path in ("../outside.parquet", str(reg_path.resolve()), "/abs.parquet"):
        with pytest.raises(ContractViolationError, match="relative|escapes"):
            _resolve_ml_config(
                PublishMartsConfig(
                    ml_enabled=True,
                    ml_approved_target=True,
                    ml_validation_start="2023-01-01",
                    ml_test_start="2024-01-01",
                    target_mode="REGULATORY_DISTRESS",
                    regulatory_events_path=bad_path,
                ),
                paths=paths,
            )

    resolved = _resolve_ml_config(
        PublishMartsConfig(
            ml_enabled=True,
            ml_approved_target=True,
            ml_validation_start="2023-01-01",
            ml_test_start="2024-01-01",
            target_mode="REGULATORY_DISTRESS",
            regulatory_events_path="data/curated/regulatory_events.parquet",
        ),
        paths=paths,
    )
    assert resolved.target_mode == "REGULATORY_DISTRESS"
    assert resolved.regulatory_events is not None
    assert resolved.regulatory_events.height == 2


# ---------------------------------------------------------------------------
# Port 2: Document AI / RAG Contract, CDN Allowlist & ZIP Controls (H-02 / H-08)
# ---------------------------------------------------------------------------


def test_port2_document_chunks_contract_and_protocol() -> None:
    expected_cols = {
        "filing_id",
        "issuer_id",
        "fiscal_year",
        "fiscal_quarter",
        "statement_scope",
        "doc_type",
        "page_no",
        "section_title",
        "unit_scale",
        "text_content",
        "content_sha256",
    }
    assert set(DOCUMENT_CHUNKS_CONTRACT.names()) == expected_cols

    chunks_df = pl.DataFrame(
        {
            "filing_id": ["f-1", "f-2"],
            "issuer_id": ["VN_HBC", "VN_HBC"],
            "fiscal_year": [2024, 2024],
            "fiscal_quarter": pl.Series([None, 2], dtype=pl.Int32),
            "statement_scope": ["CONSOLIDATED", "SEPARATE"],
            "doc_type": ["AUDITED_ANNUAL_PDF", "REVIEWED_SEMIANNUAL_PDF"],
            "page_no": [14, 7],
            "section_title": ["Thuyet minh 12 - Phai thu kho doi", "Bao cao kiem toan"],
            "unit_scale": ["VND", "MILLION_VND"],
            "text_content": [
                "Du phong phai thu ngan han kho doi...",
                "Y kien kiem toan...",
            ],
            "content_sha256": [_sha256("c1"), _sha256("c2")],
        }
    )
    validated = validate_document_chunks(chunks_df)
    assert validated.height == 2

    # H-08: UNKNOWN or mixed statement_scope must be rejected.
    with pytest.raises(ContractViolationError, match="statement_scope"):
        validate_document_chunks(
            chunks_df.with_columns(pl.lit("UNKNOWN").alias("statement_scope"))
        )

    # Invalid fiscal_quarter or page_no < 1 must be rejected.
    with pytest.raises(ContractViolationError, match="fiscal_quarter"):
        validate_document_chunks(
            chunks_df.with_columns(pl.lit(5).cast(pl.Int32).alias("fiscal_quarter"))
        )
    with pytest.raises(ContractViolationError, match="page_no"):
        validate_document_chunks(
            chunks_df.with_columns(pl.lit(0).cast(pl.Int32).alias("page_no"))
        )

    class _StubRetriever:
        def retrieve(
            self,
            *,
            issuer_id: str,
            query: str,
            statement_scope: str | None = None,
            fiscal_year: int | None = None,
            fiscal_quarter: int | None = None,
            limit: int = 5,
        ) -> Sequence[DocumentChunk]:
            _ = (query, fiscal_year, fiscal_quarter, limit)
            return [
                DocumentChunk(
                    filing_id="f-1",
                    issuer_id=issuer_id,
                    fiscal_year=2024,
                    fiscal_quarter=None,
                    statement_scope=statement_scope or "CONSOLIDATED",
                    doc_type="AUDITED_ANNUAL_PDF",
                    page_no=14,
                    section_title="Thuyet minh 12",
                    unit_scale="VND",
                    text_content="Noi dung trich dan",
                    content_sha256=_sha256("c1"),
                    score=0.91,
                )
            ]

    assert isinstance(_StubRetriever(), DocumentRetriever)


def test_port2_pdf_cdn_allowlist_and_zip_controls() -> None:
    assert PDF_CDN_ALLOWED_HOSTS == frozenset(
        {"images1.cafef.vn", "cafef1.mediacdn.vn", "staticfile.hsx.vn"}
    )
    for host in PDF_CDN_ALLOWED_HOSTS:
        url = f"https://{host}/reports/sample.pdf"
        assert validate_pdf_cdn_url(url) == url

    for bad_url in (
        "http://images1.cafef.vn/reports/sample.pdf",
        "https://user:pass@staticfile.hsx.vn/reports/sample.pdf",
        "https://staticfile.hsx.vn:8443/reports/sample.pdf",
        "https://169.254.169.254/latest/meta-data",
        "https://cafef.vn/reports/sample.pdf",
    ):
        with pytest.raises(ContractViolationError):
            validate_pdf_cdn_url(bad_url)

    # Valid ZIP containing a PDF.
    good_buf = BytesIO()
    with ZipFile(good_buf, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("2024/FPT_BCTC.pdf", b"%PDF-1.7\nvalid-body")
    extracted = extract_pdf_members_from_zip(good_buf.getvalue())
    assert list(extracted) == ["2024/FPT_BCTC.pdf"]
    assert extracted["2024/FPT_BCTC.pdf"].startswith(b"%PDF-")

    # Traversal ZIP rejected.
    traversal_buf = BytesIO()
    with ZipFile(traversal_buf, "w") as zf:
        zf.writestr("../escape.pdf", b"%PDF-1.7\nbody")
    with pytest.raises(ContractViolationError, match="path-traversal"):
        extract_pdf_members_from_zip(traversal_buf.getvalue())

    # Non-PDF member rejected.
    non_pdf_buf = BytesIO()
    with ZipFile(non_pdf_buf, "w") as zf:
        zf.writestr("malware.exe", b"MZ...")
    with pytest.raises(ContractViolationError, match="non-PDF"):
        extract_pdf_members_from_zip(non_pdf_buf.getvalue())

    # Symlink member rejected.
    symlink_buf = BytesIO()
    with ZipFile(symlink_buf, "w") as zf:
        info = ZipInfo("link.pdf")
        info.external_attr = (0o120777) << 16
        zf.writestr(info, b"%PDF-1.7\n")
    with pytest.raises(ContractViolationError, match="symbolic links"):
        extract_pdf_members_from_zip(symlink_buf.getvalue())

    # Zip bomb / uncompressed limit rejected.
    bomb_buf = BytesIO()
    with ZipFile(bomb_buf, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("huge.pdf", b"%PDF-1.4\n" + b"A" * 10_000)
    with pytest.raises(ContractViolationError, match="compression ratio|size|limit"):
        extract_pdf_members_from_zip(
            bomb_buf.getvalue(), max_member_bytes=1_000, max_compression_ratio=2.0
        )


# ---------------------------------------------------------------------------
# Port 3: Market Prices EOD (B-06) & PIT Forward Join
# ---------------------------------------------------------------------------


def test_port3_market_prices_schema_protocol_and_pit_forward_join() -> None:
    assert PIT_PRICE_JOIN_STRATEGY == "forward"
    assert set(MARKET_PRICES_SCHEMA.names()) == {
        "issuer_id",
        "ticker",
        "trade_date",
        "close_raw",
        "close_adj",
        "volume",
        "shares_outstanding_market",
        "source_provider",
    }

    prices = pl.DataFrame(
        {
            "issuer_id": ["VN_FPT", "VN_FPT"],
            "ticker": ["FPT", "FPT"],
            "trade_date": [date(2025, 4, 4), date(2025, 4, 7)],  # Fri & Mon
            "close_raw": [120_000.0, 121_500.0],
            "close_adj": [96_000.0, 97_200.0],
            "volume": [1_200_000.0, 0.0],
            "shares_outstanding_market": pl.Series(
                [1_471_000_000.0, None], dtype=pl.Float64
            ),
            "source_provider": ["cafef_eod", "cafef_eod"],
        }
    )
    assert validate_market_prices(prices).height == 2

    # Saturday disclosure MUST forward-join to Monday (2025-04-07), never Friday.
    events = pl.DataFrame(
        {
            "issuer_id": ["VN_FPT", "VN_FPT"],
            "filing_id": ["f-fri", "f-sat"],
            "published_at": [date(2025, 4, 4), date(2025, 4, 5)],
        }
    )
    aligned = align_events_to_market_prices_pit(events, prices).sort("filing_id")
    rows = aligned.to_dicts()
    fri_row = next(r for r in rows if r["filing_id"] == "f-fri")
    sat_row = next(r for r in rows if r["filing_id"] == "f-sat")
    assert fri_row["trade_date"] == date(2025, 4, 4)
    assert fri_row["close_raw"] == 120_000.0
    assert sat_row["trade_date"] == date(2025, 4, 7)
    assert sat_row["close_raw"] == 121_500.0
    assert sat_row["close_adj"] == 97_200.0

    # Non-positive close_raw rejected.
    with pytest.raises(ContractViolationError, match="close_raw"):
        validate_market_prices(prices.with_columns(pl.lit(0.0).alias("close_raw")))

    class _StubPriceProvider:
        def fetch_prices(
            self,
            *,
            tickers: Sequence[str] | None = None,
            start_date: date | None = None,
            end_date: date | None = None,
        ) -> pl.DataFrame:
            _ = (tickers, start_date, end_date)
            return prices

    assert isinstance(_StubPriceProvider(), MarketPriceProvider)


# ---------------------------------------------------------------------------
# M-03: Zero top-level platform-group imports in core/ports/storage modules
# ---------------------------------------------------------------------------


def test_ports_and_storage_have_no_platform_group_imports() -> None:
    forbidden = {"dagster", "psycopg", "redis", "opentelemetry", "pgvector", "granian"}
    repo_root = Path(__file__).resolve().parents[2]
    target_files = [
        *sorted((repo_root / "src" / "finintel" / "ports").glob("*.py")),
        repo_root / "src" / "finintel" / "storage" / "schemas.py",
        repo_root / "src" / "finintel" / "storage" / "ducklake_store.py",
    ]
    for file_path in target_files:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in forbidden, file_path
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                assert node.module.split(".")[0] not in forbidden, file_path
