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
    now = datetime.now(UTC)
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
    result = Runner(
        store, {Stage.INGEST: lambda _: StepOutcome()}, connectivity_check=unreachable
    ).run(RunRequest(source="file", stage=Stage.INGEST))
    assert result.run_id is not None and result.status == RunStatus.SUCCEEDED
    assert store.list_steps(result.run_id)[0]["status"] == "SUCCEEDED"


@pytest.mark.parametrize(
    ("http_status", "expected_error"),
    [
        (200, None),
        (403, SourceEntitlementError),
        (429, RateLimitOrCircuitOpenError),
        (None, TransientSourceError),
        (500, TransientSourceError),
        (502, TransientSourceError),
        (503, TransientSourceError),
        (504, TransientSourceError),
        (302, ContractViolationError),
    ],
)
def test_fhsc_health_uses_the_locked_sdk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_status: int | None,
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
        if http_status is None:
            raise httpx.ReadTimeout("test timeout", request=request)
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

    def unexpected_check(source: object, *args: object, **kwargs: object) -> None:
        pytest.fail("The connector must not check the connection after its key expires")

    monkeypatch.setattr(
        source_class, "key_rotation", lambda _: {"due_on": "2026-11-16"}
    )
    monkeypatch.setattr(source_class, "check", unexpected_check)
    monkeypatch.setattr(source_class, "raw", unexpected_check)

    with pytest.raises(SourceEntitlementError, match="(?i)rotate"):
        health_check(store, online=True, now=datetime(2026, 11, 16, tzinfo=UTC))


def test_runner_retries_transient_fhsc_preflight_before_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VNSTOCK_FHSC_KEY_CREATED_AT", raising=False)
    calls: list[str] = []
    waits: list[float] = []

    def check_connection() -> None:
        calls.append("preflight")
        if len(calls) == 1:
            raise TransientSourceError()

    def ingest(context: StepContext) -> StepOutcome:
        calls.append("ingest")
        return StepOutcome()

    result = Runner(
        StateStore(tmp_path / "state.sqlite"),
        {Stage.INGEST: ingest},
        connectivity_check=check_connection,
        retry_sleep=waits.append,
    ).run(RunRequest(source="fhsc", stage=Stage.INGEST))
    assert result.status == RunStatus.SUCCEEDED
    assert calls == ["preflight", "preflight", "ingest"]
    assert len(waits) == 1


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


def test_successful_batch_ingest_forwards_discovered_tickers(tmp_path: Path) -> None:
    def ingest(context: StepContext) -> StepOutcome:
        assert context.tickers == ()
        assert context.record_archived is not None
        for ticker in ("fpt", "VNM", "FPT"):
            context.record_archived(ticker)
        return StepOutcome()

    remaining: list[tuple[str, ...]] = []

    def stage(context: StepContext) -> StepOutcome:
        remaining.append(context.tickers)
        return StepOutcome(
            snapshot_id="new" if context.stage == Stage.PUBLISH else None
        )

    handlers = dict.fromkeys(Stage, stage)
    handlers[Stage.INGEST] = ingest
    result = Runner(StateStore(tmp_path / "state.sqlite"), handlers).run(
        RunRequest(source="file")
    )
    assert result.status == RunStatus.SUCCEEDED
    assert remaining == [("FPT", "VNM")] * (len(Stage) - 1)


def test_runner_enforces_default_disk_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        shutil, "disk_usage", lambda _: SimpleNamespace(free=100 * 1024**2 - 1)
    )
    store = StateStore(tmp_path / "state.sqlite")

    def unexpected(context: StepContext) -> StepOutcome:
        pytest.fail("The handler must not run when disk space is below the default")

    run = Runner(store, {Stage.INGEST: unexpected}).run(
        RunRequest(source="file", stage=Stage.INGEST)
    )
    assert run.status == RunStatus.FAILED
    assert "Free disk space is below the configured minimum." in caplog.text
    with pytest.raises(health.InsufficientDiskSpaceError):
        health_check(store, online=False)


def test_republishing_same_snapshot_refreshes_active_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    store.start_run("first", "CLI_MANUAL", {}, None)
    store.start_run("second", "CLI_MANUAL", {}, None)
    timestamps = iter(("2026-11-16T00:00:00+00:00", "2026-11-16T01:00:00+00:00"))
    monkeypatch.setattr("finintel.pipeline.state_store._now", lambda: next(timestamps))

    store.finish_run("first", RunStatus.SUCCEEDED, "snapshot")
    store.finish_run("second", RunStatus.SUCCEEDED, "snapshot")

    assert store.active_snapshot() == {
        "singleton": 1,
        "snapshot_id": "snapshot",
        "run_id": "second",
        "published_at": "2026-11-16T01:00:00+00:00",
    }


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


def test_lock_timeout_default_and_cli_exit_code_on_lock_contention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert runner.LOCK_TIMEOUT == 5
    database = tmp_path / "state.sqlite"
    config = tmp_path / "settings.yaml"
    config.write_text(
        f'state_path: "{database.as_posix()}"\nmin_free_bytes: 0\n',
        encoding="utf-8",
    )
    spawn = multiprocessing.get_context("spawn")
    acquired, release = spawn.Event(), spawn.Event()
    proc = spawn.Process(target=hold_run, args=(database, acquired, release))
    proc.start()
    try:
        assert acquired.wait(12)
        monkeypatch.setattr(runner, "LOCK_TIMEOUT", 0.1)
        exit_code = main(
            ["--config", str(config), "run", "--source", "file", "--stage", "ingest"],
            handlers={
                Stage.INGEST: lambda _: pytest.fail("Second CLI run obtained lock")
            },
        )
        assert exit_code == 0
        stderr = capsys.readouterr().err
        records = [json.loads(line) for line in stderr.splitlines()]
        assert any(r["event"] == "run_lock_busy" for r in records)
    finally:
        release.set()
        proc.join(15)
        if proc.is_alive():
            proc.terminate()
            proc.join(5)
    assert proc.exitcode == 0


def test_fhsc_health_translates_status_probe_oapi_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FHSC_API_KEY", "test-fhsc-key")
    monkeypatch.delenv("VNSTOCK_FHSC_KEY_CREATED_AT", raising=False)
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    errors = import_module("vnstock_oapi.errors")
    source_class = type(import_module("vnstock_oapi").source("fhsc"))

    def fail_status(_: object) -> dict[str, object]:
        raise errors.AuthError(
            "untrusted remote auth message", source="fhsc", http_status=401
        )

    monkeypatch.setattr(source_class, "status", fail_status)
    with pytest.raises(SourceEntitlementError) as caught:
        health_check(store, online=True, now=datetime(2026, 11, 16, tzinfo=UTC))
    assert "untrusted remote auth message" not in str(caught.value)


def test_key_expiring_in_less_than_24_hours_warns_at_least_one_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("FHSC_API_KEY", "test-fhsc-key")
    monkeypatch.setenv("VNSTOCK_FHSC_KEY_CREATED_AT", "2026-10-17T12:00:00+00:00")
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    health_check(
        store,
        online=True,
        now=datetime(2026, 11, 16, 6, 0, tzinfo=UTC),
        connectivity_check=lambda: None,
    )
    assert "API Key will expire in 1 days" in caplog.text
    registered = store.get_key("fhsc")
    assert registered is not None
    assert registered["package_version"] == "vnstock-oapi==0.1.2"


def test_state_store_publish_snapshot_syncs_with_runner_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    timestamps = iter(
        (
            "2026-11-16T00:00:00+00:00",
            "2026-11-16T00:00:01+00:00",
            "2026-11-16T00:00:02+00:00",
            "2026-11-16T00:00:03+00:00",
            "2026-11-16T00:00:04+00:00",
            "2026-11-16T00:00:05+00:00",
        )
    )
    monkeypatch.setattr("finintel.pipeline.state_store._now", lambda: next(timestamps))

    def publish(context: StepContext) -> StepOutcome:
        store.publish_snapshot(context.run_id, "snap_001")
        return StepOutcome(snapshot_id="snap_001")

    result = Runner(store, {Stage.PUBLISH: publish}, min_free_bytes=0).run(
        RunRequest(source="file", stage=Stage.PUBLISH)
    )
    assert result.run_id is not None
    assert result.status == RunStatus.SUCCEEDED
    run_row = store.get_run(result.run_id)
    active = store.active_snapshot()
    assert run_row is not None and active is not None
    assert run_row["published_snapshot_id"] == "snap_001"
    assert active["snapshot_id"] == "snap_001"
    assert active["published_at"] == run_row["finished_at"]
    assert store.published_snapshot_ids() == ["snap_001"]


def test_ingest_handler_records_archived_tickers_for_partial_batch_continuation(
    tmp_path: Path,
) -> None:
    fpt_file = tmp_path / "fpt.csv"
    fpt_file.write_text("raw_metric_key,raw_value\n10,100\n", encoding="utf-8")
    vnm_file = tmp_path / "vnm.csv"
    vnm_file.write_text("raw_metric_key,raw_value\n10,200\n", encoding="utf-8")
    config = tmp_path / "settings.yaml"
    config.write_text(
        f"""
state_path: metadata/state.sqlite
min_free_bytes: 0
ingestion:
  raw_path: raw
  files:
    - path: {json.dumps(fpt_file.name)}
      source_provider: manual_audit
      metadata:
        issuer_id: FPT-ISSUER
        ticker_at_filing: FPT
        statement_type: INCOME_STATEMENT
        statement_scope: CONSOLIDATED
        period_type: ANNUAL
        fiscal_year: 2025
        fiscal_year_start_date: 2025-01-01
        period_start_date: 2025-01-01
        period_end_date: 2025-12-31
    - path: {json.dumps(vnm_file.name)}
      source_provider: manual_audit
      metadata:
        issuer_id: VNM-ISSUER
        ticker_at_filing: VNM
        statement_type: INCOME_STATEMENT
        statement_scope: CONSOLIDATED
        period_type: ANNUAL
        fiscal_year: 2025
        fiscal_year_start_date: 2025-01-01
        period_start_date: 2025-01-01
        period_end_date: 2025-12-31
""",
        encoding="utf-8",
    )
    settings = load_settings(config)
    store = StateStore(settings.state_path)
    from finintel.pipeline.ingest import IngestHandler

    ingest_handler = IngestHandler(store, config.parent, settings.ingestion)
    observed_downstream: list[tuple[str, ...]] = []

    def downstream(context: StepContext) -> StepOutcome:
        observed_downstream.append(context.tickers)
        return StepOutcome(
            snapshot_id="snap_done" if context.stage == Stage.PUBLISH else None
        )

    handlers: dict[Stage, Callable[[StepContext], StepOutcome]] = dict.fromkeys(
        Stage, downstream
    )
    handlers[Stage.INGEST] = ingest_handler
    result = Runner(store, handlers, min_free_bytes=0).run(RunRequest(source="file"))
    assert result.status == RunStatus.SUCCEEDED
    assert observed_downstream == [("FPT", "VNM")] * (len(Stage) - 1)


def test_keyboard_interrupt_marks_step_and_run_failed_and_returns_130(
    tmp_path: Path,
) -> None:
    config = tmp_path / "settings.yaml"
    database = tmp_path / "state.sqlite"
    config.write_text(
        f'state_path: "{database.as_posix()}"\nmin_free_bytes: 0\n',
        encoding="utf-8",
    )

    def interrupt(context: StepContext) -> StepOutcome:
        raise KeyboardInterrupt()

    code = main(
        ["--config", str(config), "run", "--source", "file", "--stage", "ingest"],
        handlers={Stage.INGEST: interrupt},
    )
    assert code == 130
    store = StateStore(database)
    with store.connection() as conn:
        run_row = conn.execute("SELECT run_id, status FROM pipeline_runs").fetchone()
        assert run_row is not None
        assert run_row["status"] == RunStatus.FAILED
        steps = store.list_steps(str(run_row["run_id"]))
        assert len(steps) == 1
        assert steps[0]["status"] == "FAILED"
        assert steps[0]["error_type"] == "KeyboardInterrupt"


def test_cli_formats_sibling_layer_logs_and_non_mapping_details_as_jsonl(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import logging

    config = tmp_path / "settings.yaml"
    config.write_text(
        f'state_path: "{(tmp_path / "state.sqlite").as_posix()}"\nmin_free_bytes: 0\n',
        encoding="utf-8",
    )
    sibling_logger = logging.getLogger("finintel.storage")

    def step(context: StepContext) -> StepOutcome:
        sibling_logger.info(
            "Sibling layer event",
            extra={
                "run_id": context.run_id,
                "stage": context.stage,
                "event": "storage_event",
                "details": {"path": tmp_path / "snap_1"},
            },
        )
        sibling_logger.warning(
            "Non-mapping details",
            extra={"event": "non_mapping", "details": ["raw-item"]},
        )
        return StepOutcome()

    assert (
        main(
            ["--config", str(config), "run", "--source", "file", "--stage", "ingest"],
            handlers={Stage.INGEST: step},
        )
        == 0
    )
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    events = {item["event"]: item for item in lines}
    assert "storage_event" in events
    assert events["storage_event"]["details"]["path"] == str(tmp_path / "snap_1")
    assert events["non_mapping"]["details"]["value"] == ["raw-item"]


def test_layer0_pipeline_modules_do_not_import_polars_or_pandas() -> None:
    import ast

    pipeline_dir = Path(__file__).resolve().parents[2] / "src" / "finintel" / "pipeline"
    layer0_files = (
        "__init__.py",
        "cli.py",
        "runner.py",
        "retry_policy.py",
        "health.py",
        "state_store.py",
    )
    forbidden = {"polars", "pandas"}
    violations: list[str] = []
    for name in layer0_files:
        path = pipeline_dir / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in forbidden:
                        violations.append(f"{path.name}:{node.lineno}:{alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                if node.module.split(".")[0] in forbidden:
                    violations.append(f"{path.name}:{node.lineno}:{node.module}")
    assert violations == []


def test_sqlite_check_constraints_reject_invalid_run_and_step_statuses(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    with pytest.raises(sqlite3.IntegrityError):
        store.start_run("valid", "CLI_MANUAL", {}, None)
        with store.connection() as conn:
            conn.execute(
                "UPDATE pipeline_runs SET status = 'INVALID_STATUS' WHERE run_id = 'valid'"
            )
    step_id = store.start_step("valid", Stage.INGEST)
    with pytest.raises(sqlite3.IntegrityError):
        store.finish_step(step_id, "INVALID_STEP_STATUS")


def test_health_cli_skips_ddl_when_database_exists_and_get_key_handles_uninitialized_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path / "uninitialized.sqlite")
    assert store.get_key("fhsc") is None
    with sqlite3.connect(store.path) as conn:
        conn.execute("CREATE TABLE dummy (id INTEGER PRIMARY KEY)")
    assert store.get_key("fhsc") is None

    existing = StateStore(tmp_path / "existing.sqlite")
    existing.initialize()
    config = tmp_path / "settings.yaml"
    config.write_text(
        f'state_path: "{existing.path.as_posix()}"\nmin_free_bytes: 0\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        StateStore,
        "initialize",
        lambda _: pytest.fail("health must not re-run DDL on an existing database"),
    )
    assert main(["--config", str(config), "health", "--source", "file"]) == 0


def test_unconfigured_stage_logs_error_and_returns_failed_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "settings.yaml"
    config.write_text(
        f'state_path: "{(tmp_path / "state.sqlite").as_posix()}"\nmin_free_bytes: 0\n',
        encoding="utf-8",
    )
    assert (
        main(
            ["--config", str(config), "run", "--source", "file", "--stage", "publish"],
            handlers={},
        )
        == 1
    )
    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert any(r["event"] == "stages_unconfigured" for r in records)
