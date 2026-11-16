import multiprocessing
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from finintel.exceptions import (
    ContractViolationError,
    RateLimitOrCircuitOpenError,
    SourceEntitlementError,
    TransientSourceError,
)
from finintel.pipeline import runner
from finintel.pipeline.cli import main
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
    import json

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
