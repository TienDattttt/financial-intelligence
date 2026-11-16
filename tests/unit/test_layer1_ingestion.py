import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from importlib import import_module
from pathlib import Path

import httpx
import polars as pl
import pytest

from finintel.exceptions import (
    ContractViolationError,
    RateLimitOrCircuitOpenError,
    SourceEntitlementError,
)
from finintel.ingestion.archive import RawArchive
from finintel.ingestion.base import (
    AuditStatus,
    FilingMetadata,
    PeriodType,
    StatementScope,
    StatementType,
)
from finintel.ingestion.fhsc_oapi_adapter import FhscOapiAdapter
from finintel.ingestion.file_adapter import FileAdapter
from finintel.pipeline.cli import main
from finintel.pipeline.state_store import StateStore

FIXTURES = Path(__file__).parents[1] / "fixtures" / "raw_samples"
PAGE_1 = "fhsc_fpt_bs_2026q3.json"
PAGE_2 = "fhsc_fpt_bs_2026q2.json"
EMPTY = "fhsc_empty_page.json"
SdkStub = Callable[[Callable[[httpx.Request], httpx.Response]], list[httpx.Request]]


def metadata(quarter: int | None = None, **overrides: object) -> FilingMetadata:
    end = date(2025, 12, 31) if quarter in (None, 4) else date(2025, 9, 30)
    start = date(2025, 1, 1) if quarter is None else date(end.year, end.month - 2, 1)
    return FilingMetadata.from_mapping(
        {
            "issuer_id": "FPT-ISSUER",
            "ticker_at_filing": "FPT",
            "statement_type": "BALANCE_SHEET",
            "fiscal_year": 2025,
            "fiscal_quarter": quarter,
            "period_type": "ANNUAL" if quarter is None else "QUARTER",
            "fiscal_year_start_date": date(2025, 1, 1),
            "period_start_date": start,
            "period_end_date": end,
            **overrides,
        }
    )


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def read_manifest(store: StateStore) -> list[sqlite3.Row]:
    with store.connection() as conn:
        return conn.execute(
            "SELECT * FROM filings_manifest ORDER BY fiscal_quarter, revision_no"
        ).fetchall()


@pytest.fixture
def archive(tmp_path: Path) -> RawArchive:
    return RawArchive(tmp_path / "raw", StateStore(tmp_path / "metadata/state.sqlite"))


@pytest.fixture
def sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[SdkStub]:
    monkeypatch.setenv("FHSC_API_KEY", "test-fhsc-key")
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


def quarterly(*, limit: int = 1) -> FhscOapiAdapter:
    return FhscOapiAdapter(
        "FPT",
        StatementType.BALANCE_SHEET,
        PeriodType.QUARTER,
        metadata=(
            metadata(
                3,
                fiscal_year=2026,
                fiscal_year_start_date=date(2026, 1, 1),
                period_start_date=date(2026, 7, 1),
                period_end_date=date(2026, 9, 30),
            ),
            metadata(
                2,
                fiscal_year=2026,
                fiscal_year_start_date=date(2026, 1, 1),
                period_start_date=date(2026, 4, 1),
                period_end_date=date(2026, 6, 30),
            ),
        ),
        year=2026,
        limit=limit,
    )


def test_fhsc_pages_provenance_and_idempotence(
    archive: RawArchive,
    sdk: SdkStub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = {
        1: fixture(PAGE_1),
        2: fixture(PAGE_2),
        3: fixture(EMPTY),
    }
    current = dict(original)

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=current[int(request.url.params["page"])])

    requests = sdk(respond)
    clock = import_module("vnstock_oapi.core.envelope")
    monkeypatch.setattr(clock, "now_rfc3339", lambda: "2026-11-16T01:00:00Z")
    for page in quarterly().fetch():
        archive.store(page)
    rows = read_manifest(archive.state)
    assert len(rows) == 2
    for index, row in enumerate(reversed(rows), 1):
        payload = (archive.root / row["raw_archive_path"]).read_bytes()
        body = json.loads(payload)
        attrs = json.loads(row["provider_attrs_json"])
        assert row["statement_scope"] == "UNKNOWN"
        assert row["audit_status"] == row["audit_opinion"] == "UNKNOWN"
        assert row["published_at"] is None
        assert row["payload_nature"] == "PROVIDER_RAW_JSON"
        assert row["raw_record_locator"] == "/data/0"
        assert body == json.loads(original[index])
        assert body["data"][0]["phaithuvechovaynganhan"] is None
        assert attrs["request"]["page"] == index
        assert attrs["body_sha256"] == hashlib.sha256(original[index]).hexdigest()
        assert row["payload_file_sha256"] == hashlib.sha256(payload).hexdigest()
        assert row["fetched_at"] == "2026-11-16T01:00:00+00:00"
        assert "test-fhsc-key" not in row["provider_attrs_json"]

    assert [dict(req.url.params) for req in requests] == [
        {
            "symbol": "FPT",
            "type": "balance-sheet",
            "period": "quarterly",
            "limit": "1",
            "year": "2026",
            "page": str(page),
        }
        for page in (1, 2, 3)
    ]
    assert all(
        req.url.path == "/market/v2/financial-statement/statement" for req in requests
    )
    for number, raw in original.items():
        reordered = dict(reversed(list(json.loads(raw).items())))
        reordered["data"] = [
            dict(reversed(list(record.items()))) for record in reordered["data"]
        ]
        reordered["traceId"] = "new-trace"
        current[number] = json.dumps(reordered).encode()
    monkeypatch.setattr(clock, "now_rfc3339", lambda: "2026-11-16T02:00:00Z")
    outcomes = [
        outcome for page in quarterly().fetch() for outcome in archive.store(page)
    ]
    assert [outcome.status for outcome in outcomes] == ["SKIPPED_UNCHANGED"] * 2
    assert {outcome.filing_id for outcome in outcomes} == {
        row["filing_id"] for row in rows
    }
    assert read_manifest(archive.state) == rows
    receipts = [
        json.loads(path.read_bytes())
        for path in (archive.root / "provenance").glob("*.json")
    ]
    assert len(receipts) == 6
    assert sum(receipt["pagination_status"] == "COMPLETE" for receipt in receipts) == 2


def test_changed_value_creates_immutable_revision(
    archive: RawArchive, tmp_path: Path
) -> None:
    original = FIXTURES / "fpt_2025_balance_sheet_extract.json"
    filing = metadata(statement_scope="CONSOLIDATED")
    adapter = FileAdapter(original, filing, source_provider="manual_audit")
    (initial,) = archive.store(next(adapter.fetch()))
    payload = json.loads(original.read_bytes())
    payload[0]["raw_value"] += 100
    changed = tmp_path / "correction.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")
    adapter = FileAdapter(
        changed,
        metadata(statement_scope="CONSOLIDATED", audit_status="AUDITED"),
        source_provider="manual_audit",
    )
    (revision,) = archive.store(next(adapter.fetch()))
    before, after = read_manifest(archive.state)
    assert before["filing_id"] == initial.filing_id
    assert after["filing_id"] == revision.filing_id != initial.filing_id
    assert before["canonical_content_sha256"] != after["canonical_content_sha256"]
    assert after["revision_no"] == 2
    assert after["revision_reason"] == "AUDITED_UPDATE"
    assert (
        archive.root / before["raw_archive_path"]
    ).read_bytes() == original.read_bytes()


def test_fhsc_only_revises_changed_period_on_a_shared_page(
    archive: RawArchive,
    sdk: SdkStub,
) -> None:
    body = json.loads(fixture(PAGE_1))
    body["data"].extend(json.loads(fixture(PAGE_2))["data"])
    requests = sdk(
        lambda req: httpx.Response(200, json=body)
        if req.url.params["page"] == "1"
        else httpx.Response(200, content=fixture(EMPTY))
    )
    initial = [
        item for bundle in quarterly(limit=5).fetch() for item in archive.store(bundle)
    ]
    q2, q3 = read_manifest(archive.state)
    original = (archive.root / q3["raw_archive_path"]).read_bytes()
    assert q2["raw_archive_path"] == q3["raw_archive_path"]
    assert q2["raw_record_locator"] == "/data/1"
    assert q3["raw_record_locator"] == "/data/0"
    body["data"][0]["tongcongtaisan"] += 1
    updated = [
        item for bundle in quarterly(limit=5).fetch() for item in archive.store(bundle)
    ]
    assert len(requests) == 4  # Even a short page is followed by the empty page.
    assert updated[0].status == "ARCHIVED"
    assert updated[1].status == "SKIPPED_UNCHANGED"
    assert updated[1].filing_id == initial[1].filing_id
    revision = read_manifest(archive.state)[-1]
    assert revision["revision_no"] == 2
    assert revision["revision_reason"] == "CORRECTION"
    assert revision["filing_id"] != q3["filing_id"]
    assert (archive.root / q3["raw_archive_path"]).read_bytes() == original


@pytest.mark.parametrize(
    ("period_type", "quarter", "audit", "available"),
    [
        (PeriodType.ANNUAL, None, AuditStatus.UNKNOWN, date(2026, 3, 31)),
        (PeriodType.QUARTER, 4, AuditStatus.UNKNOWN, date(2026, 1, 30)),
        (PeriodType.QUARTER, 4, AuditStatus.REVIEWED, date(2026, 2, 14)),
        (PeriodType.SEMI_ANNUAL, None, AuditStatus.REVIEWED, date(2026, 3, 1)),
        (PeriodType.QUARTER, 2, AuditStatus.REVIEWED, date(2026, 3, 1)),
    ],
)
def test_statutory_lag(
    period_type: PeriodType,
    quarter: int | None,
    audit: AuditStatus,
    available: date,
) -> None:
    filing = metadata(
        fiscal_year_start_date=date(2025, 7, 1)
        if period_type != PeriodType.ANNUAL and quarter != 4
        else date(2025, 1, 1),
        period_start_date=date(2025, 7, 1)
        if period_type == PeriodType.SEMI_ANNUAL
        else (date(2025, 1, 1) if quarter is None else date(2025, 10, 1)),
        period_end_date=date(2025, 12, 31),
        fiscal_quarter=quarter,
        statement_scope=StatementScope.CONSOLIDATED,
        period_type=period_type,
        audit_status=audit,
    )
    assert filing.effective_available_at == available
    assert filing.published_at_basis == "STATUTORY_LAG"
    assert filing.is_filed_late is None


def test_file_cli_archives_original_bytes_and_exact_publication(tmp_path: Path) -> None:
    source_file = FIXTURES / "fpt_2025_balance_sheet_extract.json"
    config = tmp_path / "settings.yaml"
    config.write_text(
        f"""
state_path: metadata/state.sqlite
min_free_bytes: 0
ingestion:
  raw_path: raw
  files:
    - path: {json.dumps(str(source_file))}
      source_provider: manual_audit
      metadata:
        issuer_id: FPT-ISSUER
        ticker_at_filing: FPT
        statement_type: BALANCE_SHEET
        statement_scope: CONSOLIDATED
        period_type: ANNUAL
        fiscal_year: 2025
        fiscal_year_start_date: 2025-01-01
        period_start_date: 2025-01-01
        period_end_date: 2025-12-31
        published_at: 2026-04-01
""",
        encoding="utf-8",
    )
    args = [
        "--config",
        str(config),
        "run",
        "--source",
        "file",
        "--stage",
        "ingest",
        "--ticker",
        "FPT",
        "--period",
        "2025",
    ]
    assert main(args) == 0
    assert main(args) == 0
    store = StateStore(tmp_path / "metadata/state.sqlite")
    (row,) = read_manifest(store)
    assert row["published_at_basis"] == "EXACT_FILING"
    assert row["effective_available_at"] == "2026-04-01"
    assert row["is_filed_late"] == 1
    assert row["payload_nature"] == "TRUE_ORIGINAL_DOCUMENT"
    assert (
        tmp_path / "raw" / row["raw_archive_path"]
    ).read_bytes() == source_file.read_bytes()
    with store.connection() as conn:
        assert [
            row["status"]
            for row in conn.execute(
                "SELECT status FROM pipeline_steps ORDER BY step_id"
            )
        ] == ["SUCCEEDED", "SKIPPED_UNCHANGED"]


def test_page_without_calendar_and_page_limit(
    archive: RawArchive,
    sdk: SdkStub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = sdk(lambda _: httpx.Response(200, content=fixture(PAGE_1)))
    source = import_module("vnstock_oapi").source("fhsc")
    monkeypatch.setitem(
        source.spec.ops,
        "financial-statement/statement",
        replace(source.spec.ops["financial-statement/statement"], max_pages=1),
    )
    adapter = FhscOapiAdapter("FPT", StatementType.BALANCE_SHEET, PeriodType.QUARTER)
    (page,) = adapter.fetch()
    assert archive.store(page) == ()
    assert read_manifest(archive.state) == []
    assert len(requests) == 1
    (path,) = (archive.root / "provenance").glob("*.json")
    receipt = json.loads(path.read_bytes())
    assert receipt["pagination_status"] == "PAGE_LIMIT_REACHED"
    assert receipt["deferred_locators"] == ["/data/0"]


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, SourceEntitlementError),
        (403, SourceEntitlementError),
        (429, RateLimitOrCircuitOpenError),
    ],
)
def test_fhsc_stops_and_retains_previous_page(
    archive: RawArchive,
    sdk: SdkStub,
    status: int,
    error: type[Exception],
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.params["page"] == "1":
            return httpx.Response(200, content=fixture(PAGE_1))
        return httpx.Response(status, json={"message": "untrusted provider error"})

    requests = sdk(respond)
    with pytest.raises(error) as caught:
        for page in quarterly().fetch():
            archive.store(page)
    assert len(requests) == 2
    assert len(read_manifest(archive.state)) == 1
    assert "untrusted provider error" not in str(caught.value)


def test_fhsc_stops_pagination_after_sdk_recovers_from_429(
    archive: RawArchive,
    sdk: SdkStub,
) -> None:
    attempts = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert request.url.params["page"] == "1"
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, content=fixture(PAGE_1))

    sdk(respond)
    with pytest.raises(RateLimitOrCircuitOpenError):
        for page in quarterly().fetch():
            archive.store(page)
    assert attempts == 2
    (receipt,) = (archive.root / "provenance").glob("*.json")
    assert json.loads(receipt.read_bytes())["pagination_status"] == "THROTTLED"
    assert len(read_manifest(archive.state)) == 1


def test_file_duplicate_raw_code_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.csv"
    path.write_text("raw_metric_key,raw_value\n001,12\n001,13\n", encoding="utf-8")
    with pytest.raises(ContractViolationError):
        next(FileAdapter(path, metadata(), source_provider="manual_audit").fetch())


@pytest.mark.parametrize("extension", ["csv", "xlsx", "parquet"])
def test_file_formats_keep_codes_and_original_bytes(
    archive: RawArchive,
    tmp_path: Path,
    extension: str,
) -> None:
    path = tmp_path / f"filing.{extension}"
    if extension == "csv":
        path.write_text("raw_metric_key,raw_value\n001,1.25\n002,\n", encoding="utf-8")
    elif extension == "xlsx":
        book = import_module("openpyxl").Workbook()
        sheet = book.create_sheet("Original")
        sheet.append(["Report title"])
        sheet.append(["raw_metric_key", "raw_value"])
        sheet.append(["001", 1.25])
        sheet.append(["002", None])
        book.save(path)
        book.close()
    else:
        pl.DataFrame(
            {"raw_metric_key": ["001", "002"], "raw_value": [1.25, None]}
        ).write_parquet(path)
    bundle = next(
        FileAdapter(
            path,
            metadata(),
            source_provider="manual_audit",
            sheet="Original" if extension == "xlsx" else None,
            header_row=2 if extension == "xlsx" else 1,
        ).fetch()
    )
    assert set(bundle.filings[0].metrics) == {"001", "002"}
    assert bundle.filings[0].metrics["002"] is None
    archive.store(bundle)
    (row,) = read_manifest(archive.state)
    assert (archive.root / row["raw_archive_path"]).read_bytes() == path.read_bytes()


def test_concurrent_reingestion_allocates_only_one_revision(
    archive: RawArchive,
) -> None:
    adapter = FileAdapter(
        FIXTURES / "fpt_2025_balance_sheet_extract.json",
        metadata(),
        source_provider="manual_audit",
    )
    batch = [next(adapter.fetch()), next(adapter.fetch())]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = [results[0] for results in pool.map(archive.store, batch)]
    assert first.filing_id == second.filing_id
    assert {first.status, second.status} == {"ARCHIVED", "SKIPPED_UNCHANGED"}
    assert len(read_manifest(archive.state)) == 1


def test_xlsx_formula_without_cached_value_is_rejected(tmp_path: Path) -> None:
    """A formula cell without a cached result must be rejected with a clear error."""
    openpyxl = import_module("openpyxl")
    path = tmp_path / "formula_no_cache.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["raw_metric_key", "raw_value"])
    sheet.append(["001", 100])
    # Write a formula directly — the cached value is not populated because
    # openpyxl does not evaluate formulas.
    sheet["B3"] = "=SUM(B2:B2)"
    sheet["A3"] = "002"
    book.save(path)
    book.close()
    with pytest.raises(ContractViolationError, match="Formula without cached value"):
        next(FileAdapter(path, metadata(), source_provider="manual_audit").fetch())


def test_xlsx_formula_with_cached_value_is_accepted(tmp_path: Path) -> None:
    """A formula cell that has a usable cached value should be ingested normally."""
    import zipfile
    from io import BytesIO

    openpyxl = import_module("openpyxl")
    staging = tmp_path / "staging_formula.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["raw_metric_key", "raw_value"])
    sheet.append(["001", 100])
    sheet["A3"] = "002"
    sheet["B3"] = "=SUM(B2:B2)"
    book.save(staging)
    book.close()

    # Inject a cached value <v>100</v> into the formula cell B3 in XML
    # to simulate a workbook saved by Excel with evaluated formula cache.
    original_bytes = staging.read_bytes()
    modified = BytesIO()
    with zipfile.ZipFile(BytesIO(original_bytes), "r") as zin:
        with zipfile.ZipFile(modified, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "xl/worksheets/sheet1.xml":
                    text = data.decode("utf-8")
                    text = text.replace("<v />", "<v>100</v>")
                    data = text.encode("utf-8")
                zout.writestr(item, data)

    path = tmp_path / "formula_with_cache.xlsx"
    path.write_bytes(modified.getvalue())

    bundle = next(FileAdapter(path, metadata(), source_provider="manual_audit").fetch())
    assert set(bundle.filings[0].metrics) == {"001", "002"}
    assert bundle.filings[0].metrics["002"] == 100


def test_xlsx_understated_dimensions_reads_all_rows(tmp_path: Path) -> None:
    """When a worksheet's declared dimensions understate its data, all rows
    must still be read (via reset_dimensions) or the workbook rejected."""
    import zipfile

    openpyxl = import_module("openpyxl")
    staging = tmp_path / "staging.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["raw_metric_key", "raw_value"])
    sheet.append(["001", 10])
    sheet.append(["002", 20])
    sheet.append(["003", 30])
    book.save(staging)
    book.close()

    # Manually understate the declared sheet dimension in the XLSX ZIP so that
    # iter_rows without reset_dimensions would only see the header + 1 data row.
    from io import BytesIO

    original_bytes = staging.read_bytes()
    modified = BytesIO()
    with zipfile.ZipFile(BytesIO(original_bytes), "r") as zin:
        with zipfile.ZipFile(modified, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "xl/worksheets/sheet1.xml":
                    text = data.decode("utf-8")
                    text = text.replace("A1:B4", "A1:B2")
                    data = text.encode("utf-8")
                zout.writestr(item, data)

    path = tmp_path / "understated.xlsx"
    path.write_bytes(modified.getvalue())

    bundle = next(FileAdapter(path, metadata(), source_provider="manual_audit").fetch())
    # All three data rows must be present despite understated dimensions.
    assert set(bundle.filings[0].metrics) == {"001", "002", "003"}
    assert bundle.filings[0].metrics["001"] == 10
    assert bundle.filings[0].metrics["002"] == 20
    assert bundle.filings[0].metrics["003"] == 30


def test_unchanged_hash_updates_audit_metadata(archive: RawArchive) -> None:
    """When canonical content hash is unchanged but audit_status or published_at
    changes, the existing record's metadata should be updated in-place."""
    original = FIXTURES / "fpt_2025_balance_sheet_extract.json"
    # First ingest with UNKNOWN audit status
    filing_unknown = metadata(
        statement_scope="CONSOLIDATED",
        audit_status="UNKNOWN",
    )
    adapter = FileAdapter(original, filing_unknown, source_provider="manual_audit")
    (initial,) = archive.store(next(adapter.fetch()))
    assert initial.status == "ARCHIVED"

    # Re-ingest with same content but AUDITED status
    filing_audited = metadata(
        statement_scope="CONSOLIDATED",
        audit_status="AUDITED",
    )
    adapter = FileAdapter(original, filing_audited, source_provider="manual_audit")
    (second,) = archive.store(next(adapter.fetch()))
    assert second.status == "SKIPPED_UNCHANGED"
    assert second.filing_id == initial.filing_id

    # Verify that the manifest record was updated with the new audit status
    rows = read_manifest(archive.state)
    assert len(rows) == 1
    assert rows[0]["audit_status"] == "AUDITED"
    assert rows[0]["filing_id"] == initial.filing_id
