import hashlib
import json
import sqlite3
import threading
import zipfile
from calendar import monthrange
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib import import_module
from io import BytesIO
from pathlib import Path

import httpx
import polars as pl
import pytest

from finintel.domain import schemas as domain_schemas
from finintel.exceptions import (
    ContractViolationError,
    RateLimitOrCircuitOpenError,
    SourceEntitlementError,
    TransientSourceError,
)
from finintel.ingestion.archive import ArchiveOutcome, RawArchive
from finintel.ingestion.base import (
    AuditStatus,
    FilingMetadata,
    PeriodType,
    RawFiling,
    RawFilingBundle,
    RawPayload,
    StatementScope,
    StatementType,
)
from finintel.ingestion.fhsc_oapi_adapter import FhscOapiAdapter
from finintel.ingestion.file_adapter import FileAdapter
from finintel.ingestion.hasher import canonical_content_sha256
from finintel.pipeline.cli import load_settings, main
from finintel.pipeline.ingest import IngestHandler
from finintel.pipeline.runner import (
    Runner,
    RunRequest,
    RunStatus,
    Stage,
    StepContext,
    StepOutcome,
    StepStatus,
)
from finintel.pipeline.state_store import StateStore

FIXTURES = Path(__file__).parents[1] / "fixtures" / "raw_samples"
PAGE_1 = "fhsc_fpt_bs_2026q3.json"
PAGE_2 = "fhsc_fpt_bs_2026q2.json"
EMPTY = "fhsc_empty_page.json"
SdkStub = Callable[[Callable[[httpx.Request], httpx.Response]], list[httpx.Request]]


def metadata(quarter: int | None = None, **overrides: object) -> FilingMetadata:
    if quarter is None:
        end = date(2025, 12, 31)
        start = date(2025, 1, 1)
    else:
        end_month = quarter * 3
        end = date(2025, end_month, monthrange(2025, end_month)[1])
        start = date(2025, end_month - 2, 1)
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
        (PeriodType.QUARTER, 2, AuditStatus.UNKNOWN, date(2026, 3, 1)),
        (PeriodType.QUARTER, 2, AuditStatus.UNAUDITED, date(2026, 1, 30)),
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
    on_time = replace(filing, published_at=available)
    assert on_time.is_filed_late is False
    late = replace(filing, published_at=date.fromordinal(available.toordinal() + 1))
    assert late.is_filed_late is True


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
    barrier = threading.Barrier(2)

    def store_synced(bundle: RawFilingBundle) -> ArchiveOutcome:
        barrier.wait(timeout=5)
        return archive.store(bundle)[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(store_synced, batch))
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


def test_xlsx_ignores_uncached_formula_outside_selected_columns(tmp_path: Path) -> None:
    path = tmp_path / "formula_in_notes.xlsx"
    book = import_module("openpyxl").Workbook()
    sheet = book.active
    sheet.append(["Filing title"])
    sheet.append(["Amount", "Notes", "Code"])
    sheet.append([100, "=SUM(A3:A3)", "001"])
    book.save(path)
    book.close()

    adapter = FileAdapter(
        path,
        metadata(),
        source_provider="manual_audit",
        value_column="Amount",
        metric_column="Code",
        header_row=2,
    )
    assert next(adapter.fetch()).filings[0].metrics == {"001": 100}


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


def test_older_receipt_does_not_roll_back_metadata(archive: RawArchive) -> None:
    """A replayed older capture cannot overwrite newer filing evidence."""
    original = FIXTURES / "fpt_2025_balance_sheet_extract.json"
    february = datetime(2026, 2, 1, tzinfo=UTC)
    march = datetime(2026, 3, 1, tzinfo=UTC)
    april = datetime(2026, 4, 1, tzinfo=UTC)
    may = datetime(2026, 5, 1, tzinfo=UTC)

    def captured(
        filing_metadata: FilingMetadata, fetched_at: datetime
    ) -> RawFilingBundle:
        (bundle,) = FileAdapter(
            original, filing_metadata, source_provider="manual_audit"
        ).fetch()
        return replace(bundle, payload=replace(bundle.payload, fetched_at=fetched_at))

    (initial,) = archive.store(captured(metadata(audit_status="AUDITED"), february))
    assert initial.status == "ARCHIVED"

    # A newer receipt confirming the unchanged value must advance its freshness
    # marker, otherwise the March replay below would still be accepted.
    (confirmed,) = archive.store(captured(metadata(audit_status="AUDITED"), april))
    assert confirmed.status == "SKIPPED_UNCHANGED"
    (row,) = read_manifest(archive.state)
    evidence = json.loads(row["provider_attrs_json"])["metadata_evidence"]
    assert evidence["audit_status"]["fetched_at"] == april.isoformat()

    (stale,) = archive.store(captured(metadata(audit_status="UNAUDITED"), march))
    assert stale.status == "SKIPPED_UNCHANGED"
    (row,) = read_manifest(archive.state)
    assert row["audit_status"] == "AUDITED"
    evidence = json.loads(row["provider_attrs_json"])["metadata_evidence"]
    assert evidence["audit_status"]["fetched_at"] == april.isoformat()

    (fresh,) = archive.store(captured(metadata(audit_status="UNAUDITED"), may))
    assert fresh.status == "SKIPPED_UNCHANGED"
    (row,) = read_manifest(archive.state)
    assert row["audit_status"] == "UNAUDITED"
    evidence = json.loads(row["provider_attrs_json"])["metadata_evidence"]
    assert evidence["audit_status"]["fetched_at"] == may.isoformat()


def test_unchanged_hash_merges_and_links_evidence_by_field(
    archive: RawArchive, tmp_path: Path
) -> None:
    original = FIXTURES / "fpt_2025_balance_sheet_extract.json"
    metrics = json.loads(original.read_bytes())
    audit_copy = tmp_path / "audited.json"
    audit_copy.write_text(json.dumps(metrics, separators=(",", ":")), encoding="utf-8")
    opinion_copy = tmp_path / "opinion.json"
    opinion_copy.write_text(json.dumps(metrics, indent=4), encoding="utf-8")
    publication = date(2026, 4, 1)

    filings = (
        (original, metadata(published_at=publication)),
        (audit_copy, metadata(audit_status="AUDITED", audit_opinion="UNMODIFIED")),
        (
            opinion_copy,
            metadata(audit_opinion="ADVERSE", published_at=publication),
        ),
        (original, metadata()),
    )
    snapshots = []
    outcomes = []
    for path, filing_metadata in filings:
        adapter = FileAdapter(path, filing_metadata, source_provider="manual_audit")
        (outcome,) = archive.store(next(adapter.fetch()))
        (row,) = read_manifest(archive.state)
        snapshots.append(dict(row))
        outcomes.append(outcome)

    initial, audited, revised_opinion, replay = snapshots
    assert [item.status for item in outcomes] == ["ARCHIVED"] + [
        "SKIPPED_UNCHANGED"
    ] * 3
    assert {item.filing_id for item in outcomes} == {initial["filing_id"]}
    assert {row["revision_no"] for row in snapshots} == {1}
    assert audited["audit_status"] == revised_opinion["audit_status"] == "AUDITED"
    assert audited["audit_opinion"] == "UNMODIFIED"
    assert revised_opinion["audit_opinion"] == "ADVERSE"
    for snapshot in snapshots:
        assert (
            snapshot["published_at"]
            == snapshot["effective_available_at"]
            == publication.isoformat()
        )
        assert snapshot["published_at_basis"] == "EXACT_FILING"
        assert snapshot["is_filed_late"] == 1
        for key in ("raw_archive_path", "payload_file_sha256", "fetched_at"):
            assert snapshot[key] == initial[key]
    assert replay == revised_opinion

    providers = json.loads(replay["provider_attrs_json"])
    assert providers["filename"] == original.name
    assert set(providers["metadata_evidence"]) == {
        "audit_status",
        "audit_opinion",
        "published_at",
    }
    for field, value, path in (
        ("audit_status", "AUDITED", audit_copy),
        ("audit_opinion", "ADVERSE", opinion_copy),
        ("published_at", publication.isoformat(), opinion_copy),
    ):
        reference = providers["metadata_evidence"][field]
        digest = reference["receipt_sha256"]
        receipt_bytes = (archive.root / "provenance" / f"{digest}.json").read_bytes()
        assert hashlib.sha256(receipt_bytes).hexdigest() == digest
        receipt = json.loads(receipt_bytes)
        assert reference["raw_record_locator"] is None
        assert receipt["filing_metadata"][0][field] == value
        assert (
            receipt["payload_file_sha256"]
            == hashlib.sha256(path.read_bytes()).hexdigest()
        )
        assert (
            archive.root / receipt["raw_archive_path"]
        ).read_bytes() == path.read_bytes()


def test_unchanged_hash_recomputes_lateness_from_merged_metadata(
    archive: RawArchive,
) -> None:
    original = FIXTURES / "fpt_2025_balance_sheet_extract.json"
    publication = date(2026, 2, 5)
    for filing in (
        metadata(4, published_at=publication),
        metadata(4, audit_status="REVIEWED"),
    ):
        adapter = FileAdapter(original, filing, source_provider="manual_audit")
        archive.store(next(adapter.fetch()))
    (row,) = read_manifest(archive.state)
    assert (
        row["published_at"] == row["effective_available_at"] == publication.isoformat()
    )
    assert row["published_at_basis"] == "EXACT_FILING"
    assert row["is_filed_late"] == 0


def test_domain_schemas_contract_reexported_and_unknown_metadata_keys_rejected() -> (
    None
):
    assert domain_schemas.FilingMetadata is FilingMetadata
    assert domain_schemas.StatementType is StatementType
    assert domain_schemas.StatementScope is StatementScope
    assert domain_schemas.PeriodType is PeriodType
    assert domain_schemas.AuditStatus is AuditStatus
    assert domain_schemas.RawFilingBundle is RawFilingBundle
    assert domain_schemas.RawFiling is RawFiling
    assert domain_schemas.RawPayload is RawPayload

    with pytest.raises(
        ContractViolationError, match="Unsupported filing metadata fields"
    ):
        metadata(unknown_typo_field="bad")

    with pytest.raises(
        ContractViolationError, match="A stable issuer ID and ticker are required"
    ):
        metadata(issuer_id="../traversal")

    with pytest.raises(ContractViolationError, match="exceeds one fiscal year"):
        metadata(
            fiscal_year_start_date=date(2024, 1, 1),
            period_start_date=date(2024, 1, 1),
            period_end_date=date(2025, 12, 31),
        )


def test_revision_without_published_at_floors_effective_available_at_by_fetched_at(
    archive: RawArchive,
) -> None:
    payload_v1 = RawPayload(
        source_provider="fhsc",
        connector_name="fhsc_oapi_adapter",
        content=b'{"v": 1}',
        extension="json",
        fetched_at=datetime(2026, 4, 1, tzinfo=UTC),
        nature="PROVIDER_RAW_JSON",
    )
    payload_v2 = RawPayload(
        source_provider="fhsc",
        connector_name="fhsc_oapi_adapter",
        content=b'{"v": 2}',
        extension="json",
        fetched_at=datetime(2026, 11, 15, tzinfo=UTC),
        nature="PROVIDER_RAW_JSON",
    )
    filing_meta = metadata(statement_scope=StatementScope.CONSOLIDATED)
    archive.store(
        RawFilingBundle(
            payload=payload_v1,
            filings=(RawFiling(metadata=filing_meta, metrics={"10": 100}),),
        )
    )
    archive.store(
        RawFilingBundle(
            payload=payload_v2,
            filings=(RawFiling(metadata=filing_meta, metrics={"10": 250}),),
        )
    )
    rev1, rev2 = read_manifest(archive.state)
    assert rev1["revision_no"] == 1
    assert rev1["effective_available_at"] == "2026-03-31"
    assert rev2["revision_no"] == 2
    assert rev2["effective_available_at"] == "2026-11-15"


def test_replaying_older_revision_payload_does_not_create_false_new_revision(
    archive: RawArchive,
) -> None:
    filing_meta = metadata(statement_scope=StatementScope.CONSOLIDATED)
    jan = RawPayload(
        source_provider="fhsc",
        connector_name="fhsc_oapi_adapter",
        content=b'{"v": 1}',
        extension="json",
        fetched_at=datetime(2026, 4, 1, tzinfo=UTC),
        nature="PROVIDER_RAW_JSON",
    )
    jun = RawPayload(
        source_provider="fhsc",
        connector_name="fhsc_oapi_adapter",
        content=b'{"v": 2}',
        extension="json",
        fetched_at=datetime(2026, 6, 1, tzinfo=UTC),
        nature="PROVIDER_RAW_JSON",
    )
    stale_may_with_v1 = RawPayload(
        source_provider="fhsc",
        connector_name="fhsc_oapi_adapter",
        content=b'{"v": 1, "replayed": true}',
        extension="json",
        fetched_at=datetime(2026, 5, 1, tzinfo=UTC),
        nature="PROVIDER_RAW_JSON",
    )
    stale_may_with_unknown_v3 = RawPayload(
        source_provider="fhsc",
        connector_name="fhsc_oapi_adapter",
        content=b'{"v": 3}',
        extension="json",
        fetched_at=datetime(2026, 5, 15, tzinfo=UTC),
        nature="PROVIDER_RAW_JSON",
    )
    (r1,) = archive.store(
        RawFilingBundle(
            payload=jan,
            filings=(RawFiling(metadata=filing_meta, metrics={"10": 100}),),
        )
    )
    (r2,) = archive.store(
        RawFilingBundle(
            payload=jun,
            filings=(RawFiling(metadata=filing_meta, metrics={"10": 200}),),
        )
    )
    (replay_v1,) = archive.store(
        RawFilingBundle(
            payload=stale_may_with_v1,
            filings=(RawFiling(metadata=filing_meta, metrics={"10": 100}),),
        )
    )
    (replay_v3,) = archive.store(
        RawFilingBundle(
            payload=stale_may_with_unknown_v3,
            filings=(RawFiling(metadata=filing_meta, metrics={"10": 300}),),
        )
    )
    assert r1.status == "ARCHIVED"
    assert r2.status == "ARCHIVED"
    assert replay_v1.status == "SKIPPED_UNCHANGED"
    assert replay_v1.filing_id == r1.filing_id
    assert replay_v3.status == "SKIPPED_UNCHANGED"
    assert replay_v3.filing_id == r2.filing_id
    assert len(read_manifest(archive.state)) == 2


def test_scope_verification_upgrades_unknown_row_without_duplicate(
    archive: RawArchive,
) -> None:
    unknown_meta = metadata(statement_scope=StatementScope.UNKNOWN)
    verified_meta = metadata(
        statement_scope=StatementScope.CONSOLIDATED,
        audit_status=AuditStatus.AUDITED,
    )
    p1 = RawPayload(
        source_provider="fhsc",
        connector_name="fhsc_oapi_adapter",
        content=b'{"page": 1}',
        extension="json",
        fetched_at=datetime(2026, 4, 1, tzinfo=UTC),
        nature="PROVIDER_RAW_JSON",
    )
    p2 = RawPayload(
        source_provider="fhsc",
        connector_name="fhsc_oapi_adapter",
        content=b'{"page": 1, "verified": true}',
        extension="json",
        fetched_at=datetime(2026, 4, 2, tzinfo=UTC),
        nature="PROVIDER_RAW_JSON",
    )
    (first,) = archive.store(
        RawFilingBundle(
            payload=p1,
            filings=(RawFiling(metadata=unknown_meta, metrics={"10": 500}),),
        )
    )
    (second,) = archive.store(
        RawFilingBundle(
            payload=p2,
            filings=(RawFiling(metadata=verified_meta, metrics={"10": 500}),),
        )
    )
    assert first.status == "ARCHIVED"
    assert second.status == "SKIPPED_UNCHANGED"
    assert second.filing_id == first.filing_id
    rows = read_manifest(archive.state)
    assert len(rows) == 1
    assert rows[0]["statement_scope"] == "CONSOLIDATED"
    assert rows[0]["audit_status"] == "AUDITED"


def test_ingest_handler_resumes_from_failed_page_on_transient_retry(
    tmp_path: Path,
    sdk: SdkStub,
) -> None:
    fail_page2 = True

    def respond(req: httpx.Request) -> httpx.Response:
        if req.url.params["type"] != "balance-sheet":
            return httpx.Response(200, content=fixture(EMPTY))
        page = req.url.params["page"]
        if page == "1":
            return httpx.Response(200, content=fixture(PAGE_1))
        if page == "2":
            if fail_page2:
                return httpx.Response(
                    503, json={"message": "temporary upstream outage"}
                )
            return httpx.Response(200, content=fixture(PAGE_2))
        return httpx.Response(200, content=fixture(EMPTY))

    requests = sdk(respond)
    config = tmp_path / "settings.yaml"
    config.write_text(
        """
state_path: metadata/state.sqlite
min_free_bytes: 0
ingestion:
  raw_path: raw
  fhsc_filings:
    - issuer_id: FPT-ISSUER
      ticker_at_filing: FPT
      statement_type: BALANCE_SHEET
      statement_scope: CONSOLIDATED
      period_type: QUARTER
      fiscal_year: 2026
      fiscal_quarter: 3
      fiscal_year_start_date: 2026-01-01
      period_start_date: 2026-07-01
      period_end_date: 2026-09-30
    - issuer_id: FPT-ISSUER
      ticker_at_filing: FPT
      statement_type: BALANCE_SHEET
      statement_scope: CONSOLIDATED
      period_type: SEMI_ANNUAL
      fiscal_year: 2026
      fiscal_quarter: 2
      fiscal_year_start_date: 2026-01-01
      period_start_date: 2026-01-01
      period_end_date: 2026-06-30
""",
        encoding="utf-8",
    )
    settings = load_settings(config)
    store = StateStore(settings.state_path)
    handler = IngestHandler(store, config.parent, settings.ingestion)
    context = StepContext(
        run_id="run_resume",
        stage=Stage.INGEST,
        request=RunRequest(source="fhsc", tickers=("FPT",), period="2026-Q3"),
        tickers=("FPT",),
        active_snapshot_id=None,
    )
    with pytest.raises(TransientSourceError):
        handler(context)
    # Page 1 of balance-sheet was already fetched and archived; retry must resume at page 2 without re-requesting page 1
    requests.clear()
    fail_page2 = False
    outcome = handler(context)
    assert outcome.status == StepStatus.SUCCEEDED
    bs_pages = [
        req.url.params["page"]
        for req in requests
        if req.url.params["type"] == "balance-sheet"
    ]
    assert bs_pages == ["2", "3"]
    assert len(read_manifest(store)) == 1


def test_ingest_handler_reports_incomplete_when_periods_are_deferred_or_empty(
    tmp_path: Path,
    sdk: SdkStub,
) -> None:
    sdk(
        lambda req: httpx.Response(200, content=fixture(PAGE_1))
        if req.url.params["page"] == "1" and req.url.params["type"] == "balance-sheet"
        else httpx.Response(200, content=fixture(EMPTY))
    )
    config = tmp_path / "settings.yaml"
    config.write_text(
        """
state_path: metadata/state.sqlite
min_free_bytes: 0
ingestion:
  raw_path: raw
  fhsc_filings: []
""",
        encoding="utf-8",
    )
    settings = load_settings(config)
    store = StateStore(settings.state_path)
    handler = IngestHandler(store, config.parent, settings.ingestion)
    outcome = handler(
        StepContext(
            run_id="run_deferred",
            stage=Stage.INGEST,
            request=RunRequest(source="fhsc", tickers=("FPT",), period="2026-Q3"),
            tickers=("FPT",),
            active_snapshot_id=None,
        )
    )
    assert outcome.status == StepStatus.INCOMPLETE


def test_ingest_handler_records_archived_tickers_and_runner_gates_incomplete_scope(
    tmp_path: Path,
    sdk: SdkStub,
) -> None:
    sdk(
        lambda req: httpx.Response(200, content=fixture(PAGE_1))
        if req.url.params["page"] == "1" and req.url.params["type"] == "balance-sheet"
        else httpx.Response(200, content=fixture(EMPTY))
    )
    config = tmp_path / "settings.yaml"
    config.write_text(
        """
state_path: metadata/state.sqlite
min_free_bytes: 0
ingestion:
  raw_path: raw
  fhsc_filings: []
""",
        encoding="utf-8",
    )
    settings = load_settings(config)
    store = StateStore(settings.state_path)
    handler = IngestHandler(store, config.parent, settings.ingestion)
    downstream_called = False

    def downstream(_: StepContext) -> StepOutcome:
        nonlocal downstream_called
        downstream_called = True
        return StepOutcome(status=StepStatus.SUCCEEDED)

    # FHSC with deferred periods yields StepStatus.INCOMPLETE, which gates downstream stages in Runner
    handlers: dict[Stage, Callable[[StepContext], StepOutcome]] = dict.fromkeys(
        Stage, downstream
    )
    handlers[Stage.INGEST] = handler
    runner = Runner(store, handlers, min_free_bytes=0)
    res = runner.run(RunRequest(source="fhsc", tickers=("FPT",), period="2026-Q3"))
    assert res.status == RunStatus.FAILED
    assert downstream_called is False


def test_invalid_metric_or_revision_reason_does_not_leave_orphan_files(
    archive: RawArchive,
) -> None:
    bad_metric_bundle = RawFilingBundle(
        payload=RawPayload(
            source_provider="manual_audit",
            connector_name="file_adapter",
            content=b'{"001": "bad"}',
            extension="json",
            fetched_at=datetime(2026, 4, 1, tzinfo=UTC),
            nature="TRUE_ORIGINAL_DOCUMENT",
        ),
        filings=(
            RawFiling(
                metadata=metadata(),
                metrics={"001": "not-a-number"},
            ),
        ),
    )
    with pytest.raises(ContractViolationError):
        archive.store(bad_metric_bundle)
    assert list((archive.root / "manual_audit").rglob("*")) == []
    assert list((archive.root / "provenance").glob("*.json")) == []
    assert read_manifest(archive.state) == []


def test_canonical_hash_rounding_negative_zero_null_and_bounds() -> None:
    meta = metadata(statement_scope=StatementScope.CONSOLIDATED)
    # 1.00005 rounds HALF_EVEN at 4 decimal places to 1.0000, while 1.00015 rounds to 1.0002
    h_even_down = canonical_content_sha256(
        {"k": Decimal("1.00005")},
        meta.statement_scope,
        meta.period_end_date,
    )
    h_exact_one = canonical_content_sha256(
        {"k": Decimal("1")},
        meta.statement_scope,
        meta.period_end_date,
    )
    assert h_even_down == h_exact_one

    # Tiny negative value rounding to zero must normalize to "0.0000", never "-0.0000"
    h_tiny_neg = canonical_content_sha256(
        {"k": Decimal("-1e-15")},
        meta.statement_scope,
        meta.period_end_date,
    )
    h_zero = canonical_content_sha256(
        {"k": 0},
        meta.statement_scope,
        meta.period_end_date,
    )
    assert h_tiny_neg == h_zero

    # None is excluded from canonical entries, so None vs 0 must produce distinct hashes
    h_null = canonical_content_sha256(
        {"k": None},
        meta.statement_scope,
        meta.period_end_date,
    )
    assert h_null != h_zero

    # Huge numeric text rejected
    with pytest.raises(
        ContractViolationError, match="Invalid numeric raw metric value"
    ):
        canonical_content_sha256(
            {"k": "9" * 200},
            meta.statement_scope,
            meta.period_end_date,
        )

    # Control characters in metric key rejected
    with pytest.raises(ContractViolationError, match="Invalid raw metric code"):
        canonical_content_sha256(
            {"bad\x00key": 1},
            meta.statement_scope,
            meta.period_end_date,
        )


def test_file_adapter_csv_blank_row_with_notes_and_xlsx_sparse_row_guard(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "with_notes.csv"
    csv_path.write_text(
        "raw_metric_key,raw_value,notes\n001,10,ok\n,,trailing comment\n",
        encoding="utf-8",
    )
    bundle = next(
        FileAdapter(csv_path, metadata(), source_provider="manual_audit").fetch()
    )
    assert bundle.filings[0].metrics == {"001": "10"}

    openpyxl = import_module("openpyxl")
    staging = tmp_path / "staging_sparse.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["raw_metric_key", "raw_value"])
    sheet.append(["001", 10])
    sheet.append(["002", 20])
    book.save(staging)
    book.close()

    # Rewrite row 3 to row 20000 in sheet1.xml to simulate a sparse row bomb
    modified = BytesIO()
    with zipfile.ZipFile(BytesIO(staging.read_bytes()), "r") as zin:
        with zipfile.ZipFile(modified, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "xl/worksheets/sheet1.xml":
                    text = data.decode("utf-8")
                    text = (
                        text.replace('r="3"', 'r="20000"')
                        .replace('r="A3"', 'r="A20000"')
                        .replace('r="B3"', 'r="B20000"')
                    )
                    data = text.encode("utf-8")
                zout.writestr(item, data)
    sparse_xlsx = tmp_path / "sparse.xlsx"
    sparse_xlsx.write_bytes(modified.getvalue())
    with pytest.raises(ContractViolationError, match="maximum row limit"):
        next(
            FileAdapter(sparse_xlsx, metadata(), source_provider="manual_audit").fetch()
        )


def test_path_confinement_rejects_traversal_and_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text('{"001": 1}', encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = workspace / "settings.yaml"
    config.write_text(
        """
state_path: metadata/state.sqlite
min_free_bytes: 0
ingestion:
  raw_path: ../escaped_raw
""",
        encoding="utf-8",
    )
    settings = load_settings(config)
    store = StateStore(settings.state_path)
    with pytest.raises(ContractViolationError, match="outside the allowed workspace"):
        IngestHandler(store, config.parent, settings.ingestion)
