import json
import multiprocessing
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from finintel.exceptions import (
    ContractViolationError,
    RateLimitOrCircuitOpenError,
    SourceEntitlementError,
    TransientSourceError,
)
from finintel.pipeline import health, runner
from finintel.pipeline.cli import load_settings, main
from finintel.pipeline.health import health_check
from finintel.pipeline.runner import (
    Runner,
    RunRequest,
    RunStatus,
    Stage,
    StepContext,
    StepOutcome,
)
from finintel.pipeline.state_store import StateStore


def hold_run(database: Path, acquired: Any, release: Any) -> None:
    def hold(context: StepContext) -> StepOutcome:
        acquired.set()
        if not release.wait(15):
            raise TimeoutError("Timed out waiting for the test to release the lock")
        return StepOutcome()

    result = Runner(StateStore(database), {Stage.INGEST: hold}).run(
        RunRequest(source="file", stage=Stage.INGEST)
    )
    if result.exit_code:
        raise RuntimeError("The first run failed")


def test_concurrent_process_exits_successfully_without_starting_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    database = tmp_path / "state.sqlite"
    spawn = multiprocessing.get_context("spawn")
    acquired, release = spawn.Event(), spawn.Event()
    proc = spawn.Process(target=hold_run, args=(database, acquired, release))
    proc.start()
    try:
        assert acquired.wait(12)
        monkeypatch.setattr(runner, "LOCK_TIMEOUT", 0.1)

        def unexpected(context: StepContext) -> StepOutcome:
            pytest.fail("The second process obtained the lock")

        result = Runner(StateStore(database), {Stage.INGEST: unexpected}).run(
            RunRequest(source="file", stage=Stage.INGEST)
        )
        assert result.run_id is None
        assert result.exit_code == 0
        assert "Another pipeline run is currently active" in caplog.text
        with sqlite3.connect(database) as connection:
            assert (
                connection.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0]
                == 1
            )
    finally:
        release.set()
        proc.join(15)
        if proc.is_alive():
            proc.terminate()
            proc.join(5)
    assert proc.exitcode == 0


@pytest.mark.parametrize(
    ("error", "count", "status"),
    [
        (TransientSourceError, 3, RunStatus.SUCCEEDED),
        (SourceEntitlementError, 1, RunStatus.FAILED),
        (RateLimitOrCircuitOpenError, 1, RunStatus.ABORTED_CIRCUIT_OPEN),
        (ContractViolationError, 1, RunStatus.FAILED),
    ],
)
def test_retry_classification(
    tmp_path: Path, error: type[Exception], count: int, status: RunStatus
) -> None:
    calls = 0
    waits: list[float] = []

    def step(context: StepContext) -> StepOutcome:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise error("untrusted source text")
        return StepOutcome()

    store = StateStore(tmp_path / "state.sqlite")
    outcome = Runner(store, {Stage.INGEST: step}, retry_sleep=waits.append).run(
        RunRequest(source="file", stage=Stage.INGEST)
    )
    assert outcome.run_id is not None
    assert outcome.status == status
    assert len(waits) == count - 1
    record = store.list_steps(outcome.run_id)[0]
    assert calls == record["attempt_count"] == count
    assert record["status"] == ("SUCCEEDED" if count == 3 else "FAILED")
    assert "untrusted source text" not in str(record["error_message"])


def test_expired_key_blocks_online_but_allows_file_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FHSC_API_KEY", raising=False)
    monkeypatch.delenv("VNSTOCK_FHSC_KEY_CREATED_AT", raising=False)
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    now = datetime(2026, 11, 16, 9, tzinfo=UTC)
    secret = "fake-key-used-by-test"
    store.register_fhsc_key(secret, now - timedelta(days=31))
    record = store.get_key("fhsc")
    assert record is not None and len(str(record["key_fingerprint"])) == 8
    assert secret.encode() not in store.path.read_bytes()

    def unreachable() -> None:
        pytest.fail("Expired or file-mode runs must not contact FHSC")

    with pytest.raises(SourceEntitlementError, match="(?i)rotate"):
        health_check(store, online=True, now=now, connectivity_check=unreachable)
    health_check(store, online=False, now=now, connectivity_check=unreachable)


@pytest.mark.parametrize(
    ("http_status", "expected_error"),
    [
        (200, None),
        (403, SourceEntitlementError),
        (429, RateLimitOrCircuitOpenError),
    ],
)
def test_fhsc_health_uses_the_locked_sdk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_status: int,
    expected_error: type[Exception] | None,
) -> None:
    monkeypatch.setenv("FHSC_API_KEY", "test-fhsc-key")
    monkeypatch.delenv("VNSTOCK_FHSC_KEY_CREATED_AT", raising=False)
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    source = import_module("vnstock_oapi").source("fhsc")
    transport_class = import_module("vnstock_oapi.core.transport").Transport
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(http_status, json={"data": []})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        transport = transport_class("fhsc", source.spec.hosts, client=client)
        monkeypatch.setattr(type(source), "transport", property(lambda _: transport))
        if expected_error is None:
            health_check(store, online=True)
        else:
            with pytest.raises(expected_error):
                health_check(store, online=True)

    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.host == "open-api.fhsc.com.vn"
    assert requests[0].url.path == "/trading/market/session"
    assert requests[0].url.params["exchange"] == "HOSE"


def test_fhsc_health_reads_sdk_rotation_date_before_checking_the_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FHSC_API_KEY", "test-fhsc-key")
    monkeypatch.delenv("VNSTOCK_FHSC_KEY_CREATED_AT", raising=False)
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    source_class = type(import_module("vnstock_oapi").source("fhsc"))

    def unexpected_check(source: object) -> dict[str, object]:
        pytest.fail("The connector must not check the connection after its key expires")

    monkeypatch.setattr(
        source_class, "key_rotation", lambda _: {"due_on": "2026-11-16"}
    )
    monkeypatch.setattr(source_class, "check", unexpected_check)

    with pytest.raises(SourceEntitlementError, match="(?i)rotate"):
        health_check(store, online=True, now=datetime(2026, 11, 16, tzinfo=UTC))


def test_expiring_environment_key_warns_before_online_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    monkeypatch.setenv("VNSTOCK_FHSC_KEY_CREATED_AT", "2026-10-20")
    calls: list[bool] = []
    health_check(
        store,
        online=True,
        now=datetime(2026, 11, 16, tzinfo=UTC),
        connectivity_check=lambda: calls.append(True),
    )
    assert calls == [True]
    assert "API Key will expire in 3 days" in caplog.text


@pytest.mark.parametrize("continue_partial", [False, True])
def test_rate_limit_stops_more_ingestion_and_continues_only_archived_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, continue_partial: bool
) -> None:
    monkeypatch.delenv("VNSTOCK_FHSC_KEY_CREATED_AT", raising=False)
    archived: list[str] = []
    downstream: list[tuple[Stage, tuple[str, ...]]] = []
    ingest_attempts = []

    def ingest(context: StepContext) -> StepOutcome:
        (ticker,) = context.tickers
        ingest_attempts.append(ticker)
        if ticker == "VNM":
            raise RateLimitOrCircuitOpenError()
        archived.append(ticker)
        return StepOutcome()

    def next_stage(context: StepContext) -> StepOutcome:
        downstream.append((context.stage, context.tickers))
        return StepOutcome(
            snapshot_id="partial-snapshot" if context.stage == Stage.PUBLISH else None
        )

    handlers: dict[Stage, Callable[[StepContext], StepOutcome]] = dict.fromkeys(
        Stage, next_stage
    )
    handlers[Stage.INGEST] = ingest
    store = StateStore(tmp_path / "state.sqlite")
    result = Runner(store, handlers, connectivity_check=lambda: None).run(
        RunRequest(
            source="fhsc",
            tickers=("FPT", "VNM", "HPG"),
            period="2025-Q4",
            continue_on_partial_ingest=continue_partial,
        )
    )
    assert result.run_id is not None
    assert ingest_attempts == ["FPT", "VNM"]
    assert archived == ["FPT"]
    snapshot = store.active_snapshot()
    if continue_partial:
        assert result.status == RunStatus.PARTIAL_SUCCESS
        assert downstream == [(stage, ("FPT",)) for stage in list(Stage)[1:]]
        assert snapshot is not None and snapshot["snapshot_id"] == "partial-snapshot"
    else:
        assert result.status == RunStatus.ABORTED_CIRCUIT_OPEN
        assert downstream == []
        assert snapshot is None


def test_cli_routes_a_scoped_replay_and_writes_json_logs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "settings.yaml"
    config.write_text(
        f'state_path: "{(tmp_path / "state.sqlite").as_posix()}"\nmin_free_bytes: 0\n',
        encoding="utf-8",
    )
    observed: list[StepContext] = []

    def normalize(context: StepContext) -> StepOutcome:
        observed.append(context)
        return StepOutcome()

    assert (
        main(
            [
                "--config",
                str(config),
                "run",
                "--source",
                "file",
                "--ticker",
                "fpt",
                "--period",
                "2025-Q4",
                "--stage",
                "normalize",
            ],
            handlers={Stage.NORMALIZE: normalize},
        )
        == 0
    )
    assert len(observed) == 1
    assert observed[0].stage == Stage.NORMALIZE
    assert observed[0].tickers == ("FPT",)
    assert observed[0].request.period == "2025-Q4"
    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert records and records[-1]["run_id"] == observed[0].run_id
    assert {
        "timestamp",
        "level",
        "run_id",
        "stage",
        "issuer_id",
        "filing_id",
        "event",
        "details",
    } <= records[-1].keys()


def test_partial_ingest_with_discovered_tickers_only_forwards_archived_scope(
    tmp_path: Path,
) -> None:
    def ingest(context: StepContext) -> StepOutcome:
        assert context.tickers == ()
        assert context.record_archived is not None
        context.record_archived("fpt")
        context.record_archived("FPT")
        raise RateLimitOrCircuitOpenError()

    remaining: list[tuple[str, ...]] = []

    def stage(context: StepContext) -> StepOutcome:
        remaining.append(context.tickers)
        return StepOutcome(
            snapshot_id="merged-snapshot" if context.stage == Stage.PUBLISH else None
        )

    handlers = dict.fromkeys(Stage, stage)
    handlers[Stage.INGEST] = ingest
    result = Runner(StateStore(tmp_path / "state.sqlite"), handlers).run(
        RunRequest(source="file", continue_on_partial_ingest=True)
    )
    assert result.status == RunStatus.PARTIAL_SUCCESS
    assert remaining == [("FPT",)] * (len(Stage) - 1)


def test_sqlite_uses_wal_and_recovers_an_interrupted_run(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    store.start_run("interrupted", "CLI_MANUAL", {}, None)
    store.start_step("interrupted", "INGEST")
    assert (
        Runner(store, {Stage.INGEST: lambda _: StepOutcome()})
        .run(RunRequest(source="file", stage=Stage.INGEST))
        .status
        == RunStatus.SUCCEEDED
    )
    run = store.get_run("interrupted")
    assert run is not None and run["status"] == RunStatus.FAILED
    assert store.list_steps("interrupted")[0]["status"] == "FAILED"
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_missed_publish_contract_leaves_existing_active_snapshot(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    request = RunRequest(source="file", stage=Stage.PUBLISH)
    assert (
        Runner(store, {Stage.PUBLISH: lambda _: StepOutcome(snapshot_id="existing")})
        .run(request)
        .status
        == RunStatus.SUCCEEDED
    )
    assert (
        Runner(store, {Stage.PUBLISH: lambda _: StepOutcome()}).run(request).status
        == RunStatus.FAILED
    )
    active = store.active_snapshot()
    assert active is not None and active["snapshot_id"] == "existing"


def test_relative_state_path_follows_config_and_defaults_to_disk_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config" / "settings.yaml"
    config.parent.mkdir()
    config.write_text("state_path: ../data/metadata/state.sqlite\n", encoding="utf-8")
    expected = tmp_path / "data" / "metadata" / "state.sqlite"
    assert load_settings(config).state_path == expected

    monkeypatch.chdir(tmp_path)
    settings = load_settings(Path("config/settings.yaml"))
    assert settings.state_path == expected
    assert settings.min_free_bytes == 100 * 1024**2


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [("[", "Unable to read"), ("[]", "YAML mapping")],
)
def test_settings_reject_malformed_or_nonmapping_yaml(
    tmp_path: Path, yaml_text: str, message: str
) -> None:
    config = tmp_path / "settings.yaml"
    config.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_settings(config)


@pytest.mark.parametrize("status", [RunStatus.FAILED, RunStatus.ABORTED_CIRCUIT_OPEN])
def test_unsuccessful_runs_cannot_promote_a_snapshot(
    tmp_path: Path, status: RunStatus
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    store.start_run("previous", "CLI_MANUAL", {}, None)
    store.finish_run("previous", RunStatus.SUCCEEDED, "existing")
    store.start_run("unsuccessful", "CLI_MANUAL", {}, None)

    store.finish_run("unsuccessful", status, "invalid")

    record = store.get_run("unsuccessful")
    assert record is not None
    assert record["status"] == status
    assert record["published_snapshot_id"] is None
    active = store.active_snapshot()
    assert active is not None and active["snapshot_id"] == "existing"


def test_health_rejects_naive_time_before_contacting_provider(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    store.register_fhsc_key("test-key", datetime(2026, 11, 16, tzinfo=UTC))

    def unexpected_check() -> None:
        pytest.fail("Invalid timestamps must fail before the connectivity check")

    with pytest.raises(ContractViolationError, match="timezone"):
        health_check(
            store,
            online=True,
            now=datetime(2026, 11, 17),
            connectivity_check=unexpected_check,
        )


@pytest.mark.parametrize(
    ("source", "cause", "error_type", "message"),
    [
        ("file", "disk", "InsufficientDiskSpaceError", "Free disk space"),
        (
            "fhsc",
            "version",
            "SourceEntitlementError",
            "approved version of vnstock-oapi",
        ),
        ("fhsc", "import", "ImportError", "The pipeline operation failed."),
    ],
)
def test_health_cli_logs_failures_as_json_without_provider_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source: str,
    cause: str,
    error_type: str,
    message: str,
) -> None:
    config = tmp_path / "settings.yaml"
    config.write_text(
        f'state_path: "{(tmp_path / "state.sqlite").as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("VNSTOCK_FHSC_KEY_CREATED_AT", raising=False)

    def fail_import(name: str) -> object:
        raise ImportError("private connector detail")

    if cause == "disk":
        monkeypatch.setattr(shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    elif cause == "version":
        monkeypatch.setattr(health, "version", lambda _: "0.0.0")
    else:
        monkeypatch.setattr(health, "version", lambda _: "0.1.2")
        monkeypatch.setattr(health, "import_module", fail_import)

    assert main(["--config", str(config), "health", "--source", source]) == 1
    stderr = capsys.readouterr().err
    record = json.loads(stderr.splitlines()[-1])
    assert record["details"]["error_type"] == error_type
    assert message in record["details"]["message"]
    assert "private connector detail" not in stderr
