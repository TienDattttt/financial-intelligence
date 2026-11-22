"""Unit tests for ML-03b fault-tolerant ingestion, FY auto-detection, and expansion helpers."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from importlib import import_module
from pathlib import Path

import httpx
import polars as pl
import pytest

from finintel.domain.schemas import FilingMetadata, PeriodType, StatementType
from finintel.exceptions import ContractViolationError, RateLimitOrCircuitOpenError
from finintel.ingestion import fhsc_oapi_adapter
from finintel.ingestion.fhsc_oapi_adapter import (
    FhscOapiAdapter,
    _validate_checkpoint_path,
)
from finintel.pipeline.ingest import FiscalCalendarProfile, IngestHandler
from finintel.pipeline.runner import (
    RunRequest,
    Stage,
    StepContext,
    StepStatus,
    TriggerMode,
)
from finintel.pipeline.state_store import StateStore

SdkStub = Callable[[Callable[[httpx.Request], httpx.Response]], list[httpx.Request]]


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch) -> Iterator[SdkStub]:
    """Patch vnstock_oapi FHSC HTTP transport with an in-memory request router."""
    monkeypatch.setenv("FHSC_API_KEY", "test-fhsc-key")
    monkeypatch.setattr(fhsc_oapi_adapter, "_default_sleep", lambda _: None)
    source = import_module("vnstock_oapi").source("fhsc")
    transport_class = import_module("vnstock_oapi.core.transport").Transport
    clients: list[httpx.Client] = []

    def install(
        respond: Callable[[httpx.Request], httpx.Response],
    ) -> list[httpx.Request]:
        requests: list[httpx.Request] = []

        def capture(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return respond(request)

        client = httpx.Client(transport=httpx.MockTransport(capture))
        clients.append(client)
        transport = transport_class("fhsc", source.spec.hosts, client=client)
        monkeypatch.setattr(type(source), "transport", property(lambda _: transport))
        return requests

    yield install
    for client in clients:
        client.close()


def _quarter_end(year: int, quarter: int) -> date:
    if quarter == 1:
        return date(year, 3, 31)
    if quarter == 2:
        return date(year, 6, 30)
    if quarter == 3:
        return date(year, 9, 30)
    return date(year, 12, 31)


def _quarter_meta(
    ticker: str,
    year: int,
    quarter: int,
    *,
    statement: StatementType = StatementType.BALANCE_SHEET,
) -> FilingMetadata:
    end = _quarter_end(year, quarter)
    start = date(year, (quarter - 1) * 3 + 1, 1)
    return FilingMetadata.from_mapping(
        {
            "issuer_id": f"VN_{ticker}",
            "ticker_at_filing": ticker,
            "statement_type": statement.value,
            "fiscal_year": year,
            "fiscal_quarter": quarter,
            "period_type": "QUARTER",
            "fiscal_year_start_date": date(year, 1, 1),
            "period_start_date": start,
            "period_end_date": end,
            "statement_scope": "CONSOLIDATED",
        }
    )


def _fhsc_ok(rows: Sequence[Mapping[str, object]]) -> httpx.Response:
    return httpx.Response(200, json={"error_code": "0", "data": list(rows)})


def test_fhsc_adapter_checkpoint_resumes_from_next_page_after_throttle(
    sdk: SdkStub, tmp_path: Path
) -> None:
    """Persist next_page on throttle and resume from that exact page on a new adapter."""
    ckpt_path = tmp_path / "metadata" / "fhsc_checkpoints" / "FPT__BS__QUARTER.json"
    attempt_page2 = 0

    def respond(req: httpx.Request) -> httpx.Response:
        nonlocal attempt_page2
        page = req.url.params["page"]
        if page == "1":
            return _fhsc_ok([{"year": 2026, "quarter": 3, "tongcongtaisan": 103.0}])
        if page == "2":
            attempt_page2 += 1
            if attempt_page2 == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return _fhsc_ok([{"year": 2026, "quarter": 2, "tongcongtaisan": 102.0}])
        if page == "3":
            return _fhsc_ok([{"year": 2026, "quarter": 1, "tongcongtaisan": 101.0}])
        return _fhsc_ok([])

    requests_run1 = sdk(respond)
    meta = tuple(_quarter_meta("FPT", 2026, q) for q in (1, 2, 3))

    adapter1 = FhscOapiAdapter(
        "FPT",
        StatementType.BALANCE_SHEET,
        PeriodType.QUARTER,
        metadata=meta,
        limit=1,
        checkpoint_path=ckpt_path,
        min_request_interval_seconds=0.0,
    )
    assert adapter1.next_page == 1

    statuses_run1: list[str | None] = []
    with pytest.raises(RateLimitOrCircuitOpenError):
        for bundle in adapter1.fetch():
            statuses_run1.append(bundle.pagination_status)

    assert statuses_run1 == ["IN_PROGRESS", "THROTTLED"]
    assert adapter1.next_page == 3
    assert ckpt_path.is_file()
    saved = json.loads(ckpt_path.read_text(encoding="utf-8"))
    assert saved["next_page"] == 3
    assert saved["seen"] == [[2026, 2], [2026, 3]]
    assert [req.url.params["page"] for req in requests_run1] == ["1", "2", "2"]

    requests_run2 = sdk(respond)
    adapter2 = FhscOapiAdapter(
        "FPT",
        StatementType.BALANCE_SHEET,
        PeriodType.QUARTER,
        metadata=meta,
        limit=1,
        checkpoint_path=ckpt_path,
        min_request_interval_seconds=0.0,
    )
    assert adapter2.next_page == 3

    bundles_run2 = list(adapter2.fetch())
    assert [b.pagination_status for b in bundles_run2] == ["IN_PROGRESS", "COMPLETE"]
    assert [f.metadata.fiscal_quarter for b in bundles_run2 for f in b.filings] == [1]
    assert [req.url.params["page"] for req in requests_run2] == ["3", "4"]
    assert adapter2.next_page == 1
    assert not ckpt_path.exists()


def test_fhsc_adapter_checkpoint_scope_mismatch_and_symlink_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject mismatched scope checkpoints safely and forbid symlinked checkpoint paths."""
    ckpt_dir = tmp_path / "metadata" / "fhsc_checkpoints"
    ckpt_dir.mkdir(parents=True)
    ckpt_path = ckpt_dir / "scope_test.json"
    ckpt_path.write_text(
        json.dumps(
            {
                "version": 1,
                "scope": {
                    "ticker": "FPT",
                    "statement_type": "BALANCE_SHEET",
                    "period_type": "QUARTER",
                    "year": None,
                    "quarter": None,
                    "limit": 5,
                },
                "next_page": 4,
                "seen": [[2026, 1]],
            }
        ),
        encoding="utf-8",
    )

    other_adapter = FhscOapiAdapter(
        "FPT",
        StatementType.INCOME_STATEMENT,
        PeriodType.QUARTER,
        limit=5,
        checkpoint_path=ckpt_path,
        min_request_interval_seconds=0.0,
    )
    assert other_adapter.next_page == 1

    target_file = tmp_path / "outside.json"
    target_file.write_text("{}", encoding="utf-8")
    symlink_path = ckpt_dir / "link.json"
    try:
        symlink_path.symlink_to(target_file)
    except OSError:
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda self: self.name == "simulated_link.json",
        )
        symlink_path = ckpt_dir / "simulated_link.json"

    with pytest.raises(ContractViolationError, match="symbolic links"):
        _validate_checkpoint_path(symlink_path)


def test_ingest_handler_resumes_after_throttle_across_restarts(
    sdk: SdkStub, tmp_path: Path
) -> None:
    """Restarted IngestHandler + StateStore resumes throttled FHSC pagination via disk checkpoint."""
    curated = tmp_path / "curated"
    curated.mkdir()
    pl.DataFrame({"issuer_id": ["VN_FPT"], "ticker": ["FPT"]}).write_parquet(
        curated / "issuers.parquet"
    )
    disclosures_dir = tmp_path / "disclosures"
    disclosures_dir.mkdir()
    (disclosures_dir / "FPT.json").write_text(
        json.dumps(
            {
                "data": {
                    "years": [
                        {
                            "ticker": "FPT",
                            "yearReport": 2025,
                            "lengthReport": 5,
                            "publicDate": "2026-03-20T00:00:00",
                        }
                    ],
                    "quarters": [
                        {
                            "ticker": "FPT",
                            "yearReport": 2025,
                            "lengthReport": q,
                            "publicDate": (
                                _quarter_end(2025, q) + timedelta(days=20)
                            ).isoformat()
                            + "T00:00:00",
                        }
                        for q in (1, 2, 3, 4)
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    throttled_once = False

    def respond(req: httpx.Request) -> httpx.Response:
        nonlocal throttled_once
        stmt = req.url.params["type"]
        period = req.url.params["period"]
        page = req.url.params["page"]
        if stmt == "balance-sheet" and period == "quarterly":
            if page == "1":
                return _fhsc_ok([{"year": 2025, "quarter": 4, "tongcongtaisan": 400.0}])
            if page == "2":
                if not throttled_once:
                    throttled_once = True
                    return httpx.Response(429, headers={"Retry-After": "0"})
                return _fhsc_ok([{"year": 2025, "quarter": 3, "tongcongtaisan": 300.0}])
            if page == "3":
                return _fhsc_ok(
                    [
                        {"year": 2025, "quarter": 2, "tongcongtaisan": 200.0},
                        {"year": 2025, "quarter": 1, "tongcongtaisan": 100.0},
                    ]
                )
            return _fhsc_ok([])
        if stmt == "balance-sheet" and period == "annual" and page == "1":
            return _fhsc_ok([{"year": 2025, "quarter": None, "tongcongtaisan": 400.0}])
        return _fhsc_ok([])

    req_run1 = sdk(respond)
    settings: dict[str, object] = {
        "raw_path": "raw",
        "infer_calendar_schedule": True,
        "default_statement_scope": "CONSOLIDATED",
        "issuers_path": "curated/issuers.parquet",
        "disclosure_enrichment": {
            "enabled": True,
            "raw_disclosures_dir": "disclosures",
            "on_error": "warn",
        },
    }
    db_path = tmp_path / "metadata" / "state.sqlite"
    handler1 = IngestHandler(StateStore(db_path), tmp_path, settings)
    ctx = StepContext(
        run_id="run_restart_1",
        stage=Stage.INGEST,
        request=RunRequest(
            source="fhsc",
            tickers=("FPT",),
            stage=Stage.INGEST,
            trigger_mode=TriggerMode.BACKFILL,
        ),
        tickers=("FPT",),
        active_snapshot_id=None,
    )
    with pytest.raises(RateLimitOrCircuitOpenError):
        handler1(ctx)

    ckpt_file = (
        tmp_path / "metadata" / "fhsc_checkpoints" / "FPT__BALANCE_SHEET__QUARTER.json"
    )
    assert ckpt_file.is_file()
    assert json.loads(ckpt_file.read_text(encoding="utf-8"))["next_page"] == 3
    bs_q_pages_run1 = [
        r.url.params["page"]
        for r in req_run1
        if r.url.params["type"] == "balance-sheet"
        and r.url.params["period"] == "quarterly"
    ]
    assert bs_q_pages_run1 == ["1", "2", "2"]

    # Simulate fresh process restart with new StateStore and IngestHandler instances.
    req_run2 = sdk(respond)
    handler2 = IngestHandler(StateStore(db_path), tmp_path, settings)
    outcome = handler2(
        StepContext(
            run_id="run_restart_2",
            stage=Stage.INGEST,
            request=RunRequest(
                source="fhsc",
                tickers=("FPT",),
                stage=Stage.INGEST,
                trigger_mode=TriggerMode.BACKFILL,
            ),
            tickers=("FPT",),
            active_snapshot_id=None,
        )
    )
    assert outcome.status == StepStatus.SUCCEEDED
    assert not ckpt_file.exists()
    bs_q_pages_run2 = [
        r.url.params["page"]
        for r in req_run2
        if r.url.params["type"] == "balance-sheet"
        and r.url.params["period"] == "quarterly"
    ]
    assert bs_q_pages_run2 == ["3", "4"]

    with sqlite3.connect(db_path) as conn:
        quarters_saved = {
            row[0]
            for row in conn.execute(
                "SELECT fiscal_quarter FROM filings_manifest WHERE statement_type='BALANCE_SHEET'"
            ).fetchall()
        }
    assert quarters_saved == {1, 2, 3, 4, None}


def test_ingest_auto_detects_non_calendar_fiscal_years_and_transitions(
    sdk: SdkStub, tmp_path: Path
) -> None:
    """Auto-detect 03-31(+1), 06-30(+1), 09-30(0), transitional FY changes, ties, and archive seeding."""
    curated = tmp_path / "curated"
    curated.mkdir()
    tickers = ("CMG", "SBT", "HSG", "HID", "CONST", "ISMETA")
    pl.DataFrame(
        {
            "issuer_id": [f"VN_{t}" for t in tickers],
            "ticker": list(tickers),
            "fiscal_year_end_month": [None, None, None, None, None, 9],
            "annual_year_offset": [None, None, None, None, None, 0],
        },
        schema={
            "issuer_id": pl.String,
            "ticker": pl.String,
            "fiscal_year_end_month": pl.Int64,
            "annual_year_offset": pl.Int64,
        },
    ).write_parquet(curated / "issuers.parquet")

    disclosures_dir = tmp_path / "disclosures"
    disclosures_dir.mkdir()
    # CMG & SBT receive premature VCI annual dates (before FY-end) that MUST be rejected once true FY-end is set;
    # HSG receives genuine 2025-11-28 EXACT_FILING date (after 2025-09-30, rejected under wrong 2025-12-31 calendar).
    (disclosures_dir / "CMG.json").write_text(
        json.dumps(
            {
                "data": {
                    "years": [
                        {
                            "ticker": "CMG",
                            "yearReport": 2025,
                            "lengthReport": 5,
                            "publicDate": "2026-01-25T00:00:00",
                        }
                    ],
                    "quarters": [],
                }
            }
        ),
        encoding="utf-8",
    )
    (disclosures_dir / "SBT.json").write_text(
        json.dumps(
            {
                "data": {
                    "years": [
                        {
                            "ticker": "SBT",
                            "yearReport": 2025,
                            "lengthReport": 5,
                            "publicDate": "2026-03-13T00:00:00",
                        }
                    ],
                    "quarters": [],
                }
            }
        ),
        encoding="utf-8",
    )
    (disclosures_dir / "HSG.json").write_text(
        json.dumps(
            {
                "data": {
                    "years": [
                        {
                            "ticker": "HSG",
                            "yearReport": 2025,
                            "lengthReport": 5,
                            "publicDate": "2025-11-28T00:00:00",
                        }
                    ],
                    "quarters": [],
                }
            }
        ),
        encoding="utf-8",
    )

    def _q_rows_assets(
        years_range: range, assets_fn: Callable[[int, int], float]
    ) -> list[dict[str, object]]:
        return [
            {
                "year": y,
                "quarter": q,
                "tongcongtaisan": assets_fn(y, q),
                "vonchusohuu": assets_fn(y, q) * 0.4,
            }
            for y in years_range
            for q in (1, 2, 3, 4)
        ]

    bs_quarters: dict[str, list[dict[str, object]]] = {
        "CMG": _q_rows_assets(range(2024, 2027), lambda y, q: float(y * 100 + q * 10)),
        "SBT": _q_rows_assets(range(2024, 2027), lambda y, q: float(y * 1000 + q * 25)),
        "HSG": _q_rows_assets(range(2024, 2026), lambda y, q: float(y * 500 + q * 17)),
        "HID": _q_rows_assets(range(2023, 2026), lambda y, q: float(y * 300 + q * 11)),
        "CONST": [
            {
                "year": 2025,
                "quarter": q,
                "tongcongtaisan": 5_000.0,
                "vonchusohuu": 2_000.0,
            }
            for q in (1, 2, 3, 4)
        ],
        "ISMETA": [],
    }
    bs_annual: dict[str, list[dict[str, object]]] = {
        # CMG: 03-31 offset +1 -> FY2024 matches (2025, Q1), FY2025 matches (2026, Q1)
        "CMG": [
            {
                "year": 2025,
                "quarter": None,
                "tongcongtaisan": 2026 * 100 + 10.0,
                "vonchusohuu": (2026 * 100 + 10.0) * 0.4,
            },
            {
                "year": 2024,
                "quarter": None,
                "tongcongtaisan": 2025 * 100 + 10.0,
                "vonchusohuu": (2025 * 100 + 10.0) * 0.4,
            },
        ],
        # SBT: 06-30 offset +1 -> FY2024 matches (2025, Q2), FY2025 matches (2026, Q2)
        "SBT": [
            {
                "year": 2025,
                "quarter": None,
                "tongcongtaisan": 2026 * 1000 + 50.0,
                "vonchusohuu": (2026 * 1000 + 50.0) * 0.4,
            },
            {
                "year": 2024,
                "quarter": None,
                "tongcongtaisan": 2025 * 1000 + 50.0,
                "vonchusohuu": (2025 * 1000 + 50.0) * 0.4,
            },
        ],
        # HSG: 09-30 offset 0 -> FY2025 matches (2025, Q3)
        "HSG": [
            {
                "year": 2025,
                "quarter": None,
                "tongcongtaisan": 2025 * 500 + 51.0,
                "vonchusohuu": (2025 * 500 + 51.0) * 0.4,
            },
        ],
        # HID: transitional change from 03-31(+1) in 2023-2024 to 12-31 in 2025
        "HID": [
            {
                "year": 2025,
                "quarter": None,
                "tongcongtaisan": 2025 * 300 + 44.0,
                "vonchusohuu": (2025 * 300 + 44.0) * 0.4,
            },
            {
                "year": 2024,
                "quarter": None,
                "tongcongtaisan": 2025 * 300 + 11.0,
                "vonchusohuu": (2025 * 300 + 11.0) * 0.4,
            },
            {
                "year": 2023,
                "quarter": None,
                "tongcongtaisan": 2024 * 300 + 11.0,
                "vonchusohuu": (2024 * 300 + 11.0) * 0.4,
            },
        ],
        # Ambiguous constant quarters must fall back to default calendar 12-31
        "CONST": [
            {
                "year": 2025,
                "quarter": None,
                "tongcongtaisan": 5_000.0,
                "vonchusohuu": 2_000.0,
            }
        ],
        # No BS quarter rows -> uses issuers.parquet metadata (09-30)
        "ISMETA": [
            {
                "year": 2025,
                "quarter": None,
                "tongcongtaisan": 999.0,
                "vonchusohuu": 400.0,
            }
        ],
    }
    is_quarters: dict[str, list[dict[str, object]]] = {
        "CMG": [
            {
                "year": y,
                "quarter": q,
                "doanhthuthuanvebanhangvacungcapdichvu": 100.0 + q,
            }
            for y in (2025, 2026)
            for q in (1, 2, 3, 4)
        ],
        "CONST": [
            {"year": 2025, "quarter": q, "doanhthuthuanvebanhangvacungcapdichvu": 250.0}
            for q in (1, 2, 3, 4)
        ],
    }
    cf_annual: dict[str, list[dict[str, object]]] = {
        "SBT": [{"year": 2025, "quarter": None, "luuchuyentientuhdkd": 888.0}],
    }

    def respond(req: httpx.Request) -> httpx.Response:
        if req.url.params["page"] != "1":
            return _fhsc_ok([])
        sym = req.url.params["symbol"]
        stmt = req.url.params["type"]
        period = req.url.params["period"]
        if stmt == "balance-sheet" and period == "quarterly":
            return _fhsc_ok(bs_quarters.get(sym, []))
        if stmt == "balance-sheet" and period == "annual":
            return _fhsc_ok(bs_annual.get(sym, []))
        if stmt == "income-statement" and period == "quarterly":
            return _fhsc_ok(is_quarters.get(sym, []))
        if stmt == "income-statement" and period == "annual" and sym == "CMG":
            return _fhsc_ok(
                [
                    {
                        "year": 2025,
                        "quarter": None,
                        "doanhthuthuanvebanhangvacungcapdichvu": 102
                        + 103
                        + 104
                        + 101.0,
                    }
                ]
            )
        if stmt == "cash-flow" and period == "annual":
            return _fhsc_ok(cf_annual.get(sym, []))
        return _fhsc_ok([])

    sdk(respond)
    db_path = tmp_path / "metadata" / "state.sqlite"
    settings: dict[str, object] = {
        "raw_path": "raw",
        "infer_calendar_schedule": True,
        "default_statement_scope": "CONSOLIDATED",
        "issuers_path": "curated/issuers.parquet",
        "disclosure_enrichment": {
            "enabled": True,
            "raw_disclosures_dir": "disclosures",
            "on_error": "warn",
        },
    }
    handler = IngestHandler(
        StateStore(db_path),
        tmp_path,
        settings,
        enrichment_fetched_at=datetime(2099, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    outcome = handler(
        StepContext(
            run_id="run_auto_fy",
            stage=Stage.INGEST,
            request=RunRequest(
                source="fhsc",
                tickers=tickers,
                stage=Stage.INGEST,
                trigger_mode=TriggerMode.BACKFILL,
            ),
            tickers=tickers,
            active_snapshot_id=None,
        )
    )
    assert outcome.status == StepStatus.SUCCEEDED

    profiles: dict[str, FiscalCalendarProfile] = handler.detected_fiscal_calendars
    assert profiles["CMG"].fy_end_month == 3
    assert profiles["CMG"].annual_year_offset == 1
    assert profiles["SBT"].fy_end_month == 6
    assert profiles["SBT"].annual_year_offset == 1
    assert profiles["HSG"].fy_end_month == 9
    assert profiles["HSG"].annual_year_offset == 0
    assert profiles["HID"].fy_end_month == 3
    assert profiles["HID"].annual_year_offset == 1
    assert profiles["HID"].per_year_end_dates[2025] == date(2025, 12, 31)
    assert profiles["CONST"].source == "default_calendar"
    assert profiles["CONST"].fy_end_month == 12
    assert profiles["ISMETA"].source == "issuers_metadata"
    assert profiles["ISMETA"].fy_end_month == 9

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        annual_rows = {
            (r["ticker_at_filing"], r["statement_type"], r["fiscal_year"]): dict(r)
            for r in conn.execute(
                """SELECT ticker_at_filing, statement_type, fiscal_year,
                          fiscal_year_start_date, period_start_date, period_end_date,
                          published_at, published_at_basis
                   FROM filings_manifest
                   WHERE period_type = 'ANNUAL'"""
            ).fetchall()
        }

    cmg_2025 = annual_rows[("CMG", "BALANCE_SHEET", 2025)]
    assert cmg_2025["fiscal_year_start_date"] == "2025-04-01"
    assert cmg_2025["period_end_date"] == "2026-03-31"
    assert cmg_2025["published_at_basis"] == "STATUTORY_LAG"

    sbt_2025 = annual_rows[("SBT", "BALANCE_SHEET", 2025)]
    assert sbt_2025["fiscal_year_start_date"] == "2025-07-01"
    assert sbt_2025["period_end_date"] == "2026-06-30"
    assert sbt_2025["published_at_basis"] == "STATUTORY_LAG"
    # CASH_FLOW inherited BS detected FY without requiring CF-specific matching
    assert annual_rows[("SBT", "CASH_FLOW", 2025)]["period_end_date"] == "2026-06-30"

    hsg_2025 = annual_rows[("HSG", "BALANCE_SHEET", 2025)]
    assert hsg_2025["fiscal_year_start_date"] == "2024-10-01"
    assert hsg_2025["period_end_date"] == "2025-09-30"
    assert hsg_2025["published_at_basis"] == "EXACT_FILING"
    assert hsg_2025["published_at"] == "2025-11-28"

    hid_2024 = annual_rows[("HID", "BALANCE_SHEET", 2024)]
    assert hid_2024["period_start_date"] == "2024-04-01"
    assert hid_2024["period_end_date"] == "2025-03-31"
    hid_2025 = annual_rows[("HID", "BALANCE_SHEET", 2025)]
    assert hid_2025["fiscal_year_start_date"] == "2025-04-01"
    assert hid_2025["period_start_date"] == "2025-04-01"
    assert hid_2025["period_end_date"] == "2025-12-31"

    assert (
        annual_rows[("CONST", "BALANCE_SHEET", 2025)]["period_end_date"] == "2025-12-31"
    )
    assert (
        annual_rows[("ISMETA", "BALANCE_SHEET", 2025)]["period_end_date"]
        == "2025-09-30"
    )

    # Fresh IngestHandler running single-period 2025 CASH_FLOW for SBT reads archived BS via seed_from_archive
    def respond_single_period(req: httpx.Request) -> httpx.Response:
        if (
            req.url.params["symbol"] == "SBT"
            and req.url.params["type"] == "cash-flow"
            and req.url.params["period"] == "annual"
            and req.url.params["page"] == "1"
        ):
            return _fhsc_ok(
                [{"year": 2025, "quarter": None, "luuchuyentientuhdkd": 999.0}]
            )
        return _fhsc_ok([])

    sdk(respond_single_period)
    handler_fresh = IngestHandler(StateStore(db_path), tmp_path, settings)
    outcome_fresh = handler_fresh(
        StepContext(
            run_id="run_sbt_single_cf",
            stage=Stage.INGEST,
            request=RunRequest(source="fhsc", tickers=("SBT",), period="2025"),
            tickers=("SBT",),
            active_snapshot_id=None,
        )
    )
    assert outcome_fresh.status == StepStatus.SUCCEEDED
    assert handler_fresh.detected_fiscal_calendars["SBT"].fy_end_month == 6
    assert handler_fresh.detected_fiscal_calendars["SBT"].annual_year_offset == 1


def test_expand_script_guards_source_universe_and_repairs_baseline(
    tmp_path: Path,
) -> None:
    """Enforce read-only protection on source universes and surgically reset 8 non-calendar ANNUAL rows."""
    from scripts import expand_universe_ml03b as exp

    forbidden_live = tmp_path / "data" / "live_fhsc_universe"
    forbidden_golive = tmp_path / "data" / "go_live_nfr_run"
    safe_target = tmp_path / "data" / "universe_600_800"
    for p in (forbidden_live, forbidden_golive):
        p.mkdir(parents=True)

    with pytest.raises(ContractViolationError, match="protected source directory"):
        exp.assert_safe_destination_dir(
            forbidden_live, (forbidden_live, forbidden_golive)
        )
    with pytest.raises(ContractViolationError, match="protected source directory"):
        exp.assert_safe_destination_dir(
            forbidden_golive / "metadata", (forbidden_live, forbidden_golive)
        )
    assert (
        exp.assert_safe_destination_dir(safe_target, (forbidden_live, forbidden_golive))
        == safe_target.resolve()
    )

    from finintel.ingestion.archive import RawArchive

    state_db = tmp_path / "state.sqlite"
    store = StateStore(state_db)
    RawArchive(tmp_path / "raw", store)
    with sqlite3.connect(state_db) as conn:
        for idx, (t, pt, fq) in enumerate(
            [
                ("CMG", "ANNUAL", None),
                ("CMG", "QUARTER", 1),
                ("SBT", "ANNUAL", None),
                ("AAA", "ANNUAL", None),
            ]
        ):
            conn.execute(
                """INSERT INTO filings_manifest (
                       filing_id, issuer_id, ticker_at_filing, source_provider,
                       connector_name, payload_nature, statement_type, statement_scope,
                       period_type, fiscal_year, fiscal_quarter, fiscal_year_start_date,
                       period_start_date, period_end_date, published_at, published_at_basis,
                       effective_available_at, audit_status, audit_opinion, revision_no,
                       revision_reason, fetched_at, payload_file_sha256,
                       canonical_content_sha256, raw_archive_path
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"f{idx}",
                    t,
                    t,
                    "fhsc",
                    "vnstock",
                    "PROVIDER_RAW_JSON",
                    "BALANCE_SHEET",
                    "CONSOLIDATED",
                    pt,
                    2025,
                    fq,
                    "2025-01-01",
                    "2025-01-01",
                    "2025-12-31",
                    "2026-03-01",
                    "EXACT_FILING",
                    "2026-03-01",
                    "AUDITED",
                    "UNMODIFIED",
                    1,
                    "initial",
                    "2026-03-01T00:00:00Z",
                    "a" * 64,
                    "b" * 64,
                    "sha256/aa/test.json",
                ),
            )
        conn.commit()

    deleted = exp.repair_non_calendar_baseline_db(state_db)
    assert deleted == 2
    with sqlite3.connect(state_db) as conn:
        remaining = conn.execute(
            "SELECT ticker_at_filing, period_type FROM filings_manifest ORDER BY ticker_at_filing, period_type"
        ).fetchall()
    assert remaining == [("AAA", "ANNUAL"), ("CMG", "QUARTER")]


def test_expand_script_selection_prioritizes_distressed_upcom_hnx_and_non_calendar() -> (
    None
):
    """Select priority distressed UPCOM/HNX (including STATUTORY_LAG delisted) and exclude separate."""
    from scripts import expand_universe_ml03b as exp

    baseline = {"AAA", "FPT"}
    catalog = {
        "AAA": {"exchange": "HOSE"},
        "FPT": {"exchange": "HOSE"},
        "HSG": {"exchange": "HOSE"},
        "UP_DELIST": {"exchange": "UPCOM"},
        "HNX_WARN": {"exchange": "HNX"},
        "SEP_ONLY": {"exchange": "HNX"},
        "H_HNX": {"exchange": "HNX"},
        "H_UPCOM": {"exchange": "UPCOM"},
        "H_HOSE": {"exchange": "HOSE"},
    }
    screen = {
        "HSG": {
            "exchange": "HOSE",
            "cafef_cons_count": 29,
            "cafef_sep_count": 5,
            "vci_years": 10,
            "vci_quarters": 40,
            "has_distress_event": False,
            "distressed_event_types": [],
        },
        "UP_DELIST": {
            "exchange": "UPCOM",
            "cafef_cons_count": 0,
            "cafef_sep_count": 0,
            "vci_years": 0,
            "vci_quarters": 0,
            "has_distress_event": True,
            "distressed_event_types": ["FORCED_DELISTING"],
        },
        "HNX_WARN": {
            "exchange": "HNX",
            "cafef_cons_count": 5,
            "cafef_sep_count": 0,
            "vci_years": 7,
            "vci_quarters": 28,
            "has_distress_event": True,
            "distressed_event_types": ["WARNING"],
        },
        "SEP_ONLY": {
            "exchange": "HNX",
            "cafef_cons_count": 0,
            "cafef_sep_count": 4,
            "vci_years": 8,
            "vci_quarters": 32,
            "has_distress_event": True,
            "distressed_event_types": ["CONTROL"],
        },
        "H_HNX": {
            "exchange": "HNX",
            "cafef_cons_count": 10,
            "cafef_sep_count": 0,
            "vci_years": 8,
            "vci_quarters": 32,
            "has_distress_event": False,
            "distressed_event_types": [],
        },
        "H_UPCOM": {
            "exchange": "UPCOM",
            "cafef_cons_count": 6,
            "cafef_sep_count": 0,
            "vci_years": 6,
            "vci_quarters": 24,
            "has_distress_event": False,
            "distressed_event_types": [],
        },
        "H_HOSE": {
            "exchange": "HOSE",
            "cafef_cons_count": 12,
            "cafef_sep_count": 0,
            "vci_years": 10,
            "vci_quarters": 40,
            "has_distress_event": False,
            "distressed_event_types": [],
        },
    }
    selected = exp.select_expansion_tickers(
        baseline_tickers=baseline,
        catalog_tickers=catalog,
        screen_records=screen,
        target_total=7,
        extra_priority_tickers=frozenset({"HSG"}),
    )
    assert len(selected) == 5
    assert "SEP_ONLY" not in selected
    assert selected[:3] == ["HSG", "HNX_WARN", "UP_DELIST"]
    assert set(selected) == {"HSG", "HNX_WARN", "UP_DELIST", "H_HNX", "H_UPCOM"}


def test_expand_script_fhsc_bs_scope_guard_and_audit_rendering(
    tmp_path: Path,
) -> None:
    """Verify parent-only SEPARATE rejection, real snapshot_date, YAML round-trip, and audit report."""
    from finintel.orchestration.partitions import load_universe_tickers_yaml
    from scripts import expand_universe_ml03b as exp

    assert exp.assess_fhsc_bs_row_scope([], cafef_cons_count=5) == (False, "empty_fhsc")
    assert exp.assess_fhsc_bs_row_scope(
        [{"dautuvaocongtycon": 50_000.0, "loiichcodongkhongkiemsoat": 0.0}],
        cafef_cons_count=0,
    ) == (False, "parent_only_separate")
    assert exp.assess_fhsc_bs_row_scope(
        [{"dautuvaocongtycon": 34_000.0, "loiichcodongkhongkiemsoat": 1_500_000.0}],
        cafef_cons_count=0,
    ) == (True, "consolidated_nci_or_goodwill")
    assert exp.assess_fhsc_bs_row_scope(
        [{"dautuvaocongtycon": 0.0, "loiichcodongkhongkiemsoat": 0.0}],
        cafef_cons_count=0,
    ) == (True, "consolidated_or_single_entity")

    existing = pl.DataFrame(
        {
            "issuer_id": ["AAA"],
            "ticker": ["AAA"],
            "exchange": ["HOSE"],
            "company_name_vn": ["An Phat"],
            "accounting_regime": ["CORPORATE"],
            "icb_code_level2": ["basic-materials"],
            "icb_name_level2": ["Vat lieu"],
            "icb_code_level3": ["chemicals"],
            "icb_name_level3": ["Hoa chat"],
            "industry_snapshot_date": [date(2025, 1, 1)],
            "delisted_date": [None],
        },
        schema={
            "issuer_id": pl.String,
            "ticker": pl.String,
            "exchange": pl.String,
            "company_name_vn": pl.String,
            "accounting_regime": pl.String,
            "icb_code_level2": pl.String,
            "icb_name_level2": pl.String,
            "icb_code_level3": pl.String,
            "icb_name_level3": pl.String,
            "industry_snapshot_date": pl.Date,
            "delisted_date": pl.Date,
        },
    )
    snap = date(2026, 11, 22)
    issuers_df = exp.build_universe_issuers_frame(
        existing,
        {"VHG": {"exchange": "UPCOM", "company_name_vn": "VHG Corp"}},
        ["VHG", "AAA"],
        snapshot_date=snap,
        delisted_dates={"VHG": date(2025, 6, 15)},
    )
    vhg_row = issuers_df.filter(pl.col("ticker") == "VHG").to_dicts()[0]
    assert vhg_row["industry_snapshot_date"] == snap
    assert vhg_row["delisted_date"] == date(2025, 6, 15)

    yaml_path = exp.write_universe_tickers_yaml(
        tmp_path / "universe_tickers.yaml", ["VHG", "AAA", "AAA"]
    )
    assert load_universe_tickers_yaml(yaml_path) == ("AAA", "VHG")

    events_df = pl.DataFrame(
        {
            "issuer_id": ["VHG"],
            "event_date": [date(2025, 6, 15)],
            "effective_from": [date(2025, 6, 15)],
            "resolution_date": [None],
            "event_type": ["FORCED_DELISTING"],
            "event_stage": ["FIRST_ENTRY"],
            "exchange_delisted_from": ["HNX"],
            "source_provider": ["vietcap_iq"],
            "source_url": ["https://iq.vietcap.com.vn/news?ticker=VHG"],
            "raw_headline": ["Delisted"],
            "payload_sha256": ["a" * 64],
        },
        schema={
            "issuer_id": pl.String,
            "event_date": pl.Date,
            "effective_from": pl.Date,
            "resolution_date": pl.Date,
            "event_type": pl.String,
            "event_stage": pl.String,
            "exchange_delisted_from": pl.String,
            "source_provider": pl.String,
            "source_url": pl.String,
            "raw_headline": pl.String,
            "payload_sha256": pl.String,
        },
    )
    md = exp.render_ml03b_audit_markdown(
        generated_at="2026-11-22T12:00:00Z",
        issuers_df=issuers_df,
        events_df=events_df,
        delistings_df=pl.DataFrame({"issuer_id": ["VHG"]}),
        audit_df=pl.DataFrame(),
        pit_stats={
            "total_manifest_rows": 100,
            "overall_basis": {"EXACT_FILING": 60, "STATUTORY_LAG": 40},
            "upcom_basis": {"EXACT_FILING": 10, "STATUTORY_LAG": 30},
            "per_ticker_basis": {},
            "non_cal_summary": {},
            "hid_transitions": [],
        },
        integrity_summary={"integrity_passed": True},
    )
    assert "| Total Non-Financial Corporate Issuers | 375 | **2** |" in md
    assert "Mandatory-Delisted UPCOM" in md
