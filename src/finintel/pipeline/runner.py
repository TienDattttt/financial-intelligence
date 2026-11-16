"""Orchestrate stage handlers and record their operational outcomes."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from time import sleep
from typing import Literal
from uuid import uuid4

from filelock import FileLock, Timeout

from finintel.exceptions import (
    ContractViolationError,
    CriticalDataQualityError,
    RateLimitOrCircuitOpenError,
    SourceEntitlementError,
    TransientSourceError,
)
from finintel.pipeline.health import InsufficientDiskSpaceError, health_check
from finintel.pipeline.retry_policy import run_with_retry
from finintel.pipeline.state_store import StateStore

logger = logging.getLogger("finintel.pipeline")
LOCK_TIMEOUT = 5
Source = Literal["fhsc", "file"]


class Stage(StrEnum):
    INGEST = "INGEST"
    NORMALIZE = "NORMALIZE"
    QUALITY_GATE_1 = "QUALITY_GATE_1"
    ANALYTICS = "ANALYTICS"
    QUALITY_GATE_2 = "QUALITY_GATE_2"
    PUBLISH = "PUBLISH"


class TriggerMode(StrEnum):
    CLI_MANUAL = "CLI_MANUAL"
    SCHEDULED_CRON = "SCHEDULED_CRON"
    BACKFILL = "BACKFILL"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"
    ABORTED_CIRCUIT_OPEN = "ABORTED_CIRCUIT_OPEN"


class StepStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    QUARANTINED = "QUARANTINED"
    SKIPPED_UNCHANGED = "SKIPPED_UNCHANGED"


def _normalize_ticker(value: str) -> str:
    ticker = value.strip().upper()
    if re.fullmatch(r"[A-Z0-9]{1,16}", ticker) is None:
        raise ValueError("Tickers must contain 1 to 16 letters or digits.")
    return ticker


@dataclass(frozen=True)
class RunRequest:
    source: Source = "fhsc"
    tickers: tuple[str, ...] = ()
    period: str | None = None
    stage: Stage | None = None
    continue_on_partial_ingest: bool = False
    trigger_mode: TriggerMode = TriggerMode.CLI_MANUAL

    def __post_init__(self) -> None:
        if self.source not in {"fhsc", "file"}:
            raise ValueError("Source must be fhsc or file.")
        tickers = tuple(dict.fromkeys(map(_normalize_ticker, self.tickers)))
        object.__setattr__(self, "tickers", tickers)
        if self.period is not None:
            period = self.period.strip().upper()
            if re.fullmatch(
                r"[0-9]{4}(?:-Q[1-4])?", period
            ) is None or period.startswith("0000"):
                raise ValueError("Period must be YYYY or YYYY-Q1 through YYYY-Q4.")
            object.__setattr__(self, "period", period)

    def log_arguments(self) -> dict[str, object]:
        return {
            "source": self.source,
            "tickers": self.tickers,
            "period": self.period,
            "stage": self.stage,
            "continue_on_partial_ingest": self.continue_on_partial_ingest,
        }


@dataclass(frozen=True)
class StepContext:
    """Ingest handlers persist raw output before returning a successful outcome.

    Publish handlers keep untouched scopes in the completed snapshot and return
    its identifier only after validation and durable publication.
    """

    run_id: str
    stage: Stage
    request: RunRequest
    tickers: tuple[str, ...]
    active_snapshot_id: str | None
    record_archived: Callable[[str], None] | None = None


@dataclass(frozen=True)
class StepOutcome:
    status: StepStatus = StepStatus.SUCCEEDED
    issuer_id: str | None = None
    filing_id: str | None = None
    snapshot_id: str | None = None


@dataclass(frozen=True)
class RunResult:
    run_id: str | None
    status: RunStatus | None

    @property
    def exit_code(self) -> int:
        return int(self.status in {RunStatus.FAILED, RunStatus.ABORTED_CIRCUIT_OPEN})


Handler = Callable[[StepContext], StepOutcome]


def _error_message(error: BaseException) -> str:
    # Provider exception messages may contain credentials or arbitrary remote text.
    messages: tuple[tuple[type[BaseException], str], ...] = (
        (TransientSourceError, "Transient source error after retry."),
        (
            RateLimitOrCircuitOpenError,
            "Source rate limit or circuit breaker stopped the run.",
        ),
        (
            SourceEntitlementError,
            "Source entitlement check failed. Check or rotate the API key.",
        ),
        (
            ContractViolationError,
            "A pipeline input or stage outcome violated its contract.",
        ),
        (CriticalDataQualityError, "A critical data quality check failed."),
        (
            InsufficientDiskSpaceError,
            "Free disk space is below the configured minimum.",
        ),
        (KeyboardInterrupt, "The operator interrupted the pipeline."),
    )
    return next(
        (message for category, message in messages if isinstance(error, category)),
        "The pipeline operation failed.",
    )


class Runner:
    def __init__(
        self,
        store: StateStore,
        handlers: Mapping[Stage, Handler],
        *,
        min_free_bytes: int = 0,
        connectivity_check: Callable[[], None] | None = None,
        retry_sleep: Callable[[float], None] = sleep,
        git_commit_sha: str | None = None,
    ) -> None:
        self.store = store
        self.handlers = dict(handlers)
        self.min_free_bytes = min_free_bytes
        self.connectivity_check = connectivity_check
        self.retry_sleep = retry_sleep
        self.git_commit_sha = git_commit_sha

    def run(self, request: RunRequest) -> RunResult:
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(
            self.store.path.with_name("pipeline.lock"), timeout=LOCK_TIMEOUT
        )
        try:
            lock.acquire()
        except Timeout:
            logger.warning(
                "Another pipeline run is currently active",
                extra={"event": "run_lock_busy"},
            )
            return RunResult(None, None)
        try:
            return self._run_locked(request)
        finally:
            lock.release()

    def _run_locked(self, request: RunRequest) -> RunResult:
        self.store.initialize()
        self.store.recover_interrupted_runs()
        run_id = str(uuid4())
        self.store.start_run(
            run_id, request.trigger_mode, request.log_arguments(), self.git_commit_sha
        )
        logger.info(
            "Pipeline run started",
            extra={
                "event": "run_started",
                "run_id": run_id,
                "details": request.log_arguments(),
            },
        )
        stages = (request.stage,) if request.stage is not None else tuple(Stage)
        snapshot_id = None
        try:
            missing = [stage for stage in stages if stage not in self.handlers]
            if missing:
                logger.error(
                    "No handlers registered for the requested stages",
                    extra={
                        "event": "stages_unconfigured",
                        "run_id": run_id,
                        "details": {"stages": missing},
                    },
                )
                status = RunStatus.FAILED
            else:
                health_check(
                    self.store,
                    online=request.source == "fhsc" and Stage.INGEST in stages,
                    min_free_bytes=self.min_free_bytes,
                    connectivity_check=self.connectivity_check,
                    run_id=run_id,
                )
                active = self.store.active_snapshot()
                previous_snapshot = (
                    str(active["snapshot_id"]) if active is not None else None
                )
                status, snapshot_id = self._run_stages(
                    run_id, request, stages, previous_snapshot
                )
        except KeyboardInterrupt:
            self.store.finish_run(run_id, RunStatus.FAILED)
            self._log_failure(run_id, KeyboardInterrupt())
            raise
        except Exception as error:
            self._log_failure(run_id, error)
            status = (
                RunStatus.ABORTED_CIRCUIT_OPEN
                if isinstance(error, RateLimitOrCircuitOpenError)
                else RunStatus.FAILED
            )
        self.store.finish_run(run_id, status, snapshot_id)
        logger.info(
            "Pipeline run finished",
            extra={
                "event": "run_finished",
                "run_id": run_id,
                "details": {"status": status, "snapshot_id": snapshot_id},
            },
        )
        return RunResult(run_id, status)

    def _run_stages(
        self,
        run_id: str,
        request: RunRequest,
        stages: tuple[Stage, ...],
        previous_snapshot: str | None,
    ) -> tuple[RunStatus, str | None]:
        tickers = request.tickers
        status = RunStatus.SUCCEEDED
        snapshot_id = None
        archived: dict[str, None] = {}

        def record_archived(ticker: str) -> None:
            archived[_normalize_ticker(ticker)] = None

        for stage in stages:
            if stage == Stage.INGEST and tickers:
                tickers, status = self._ingest_tickers(
                    StepContext(run_id, stage, request, tickers, previous_snapshot)
                )
                if not tickers:
                    break
                continue
            context = StepContext(
                run_id,
                stage,
                request,
                tickers,
                previous_snapshot,
                record_archived=record_archived if stage == Stage.INGEST else None,
            )
            try:
                outcome = self._run_step(context)
            except RateLimitOrCircuitOpenError:
                if (
                    stage == Stage.INGEST
                    and request.continue_on_partial_ingest
                    and archived
                ):
                    tickers = tuple(archived)
                    status = RunStatus.PARTIAL_SUCCESS
                    continue
                raise
            if outcome.status == StepStatus.QUARANTINED:
                return RunStatus.PARTIAL_SUCCESS, None
            if stage == Stage.PUBLISH:
                snapshot_id = outcome.snapshot_id
        return status, snapshot_id

    def _ingest_tickers(self, batch: StepContext) -> tuple[tuple[str, ...], RunStatus]:
        completed: list[str] = []
        status = RunStatus.SUCCEEDED
        for ticker in batch.tickers:
            step = StepContext(
                batch.run_id,
                batch.stage,
                batch.request,
                (ticker,),
                batch.active_snapshot_id,
            )
            try:
                outcome = self._run_step(step)
            except RateLimitOrCircuitOpenError:
                # Any earlier successful results have already been archived by their handlers.
                if batch.request.continue_on_partial_ingest and completed:
                    return tuple(completed), RunStatus.PARTIAL_SUCCESS
                raise
            if outcome.status == StepStatus.QUARANTINED:
                status = RunStatus.PARTIAL_SUCCESS
            else:
                completed.append(ticker)
        return tuple(completed), status

    def _run_step(self, context: StepContext) -> StepOutcome:
        step_id = self.store.start_step(context.run_id, context.stage)
        log_fields = {"run_id": context.run_id, "stage": context.stage}

        def before_attempt(attempt: int) -> None:
            self.store.record_attempt(step_id, attempt)
            logger.info(
                "Pipeline step attempt started",
                extra={
                    **log_fields,
                    "event": "step_attempt",
                    "details": {"attempt": attempt, "tickers": context.tickers},
                },
            )

        try:
            handler = self.handlers[context.stage]
            outcome = run_with_retry(
                lambda: handler(context), before_attempt, wait=self.retry_sleep
            )
            if not isinstance(outcome, StepOutcome) or not isinstance(
                outcome.status, StepStatus
            ):
                raise ContractViolationError(
                    "Stage handler returned an invalid outcome."
                )
            requires_snapshot = (
                context.stage == Stage.PUBLISH
                and outcome.status == StepStatus.SUCCEEDED
            )
            if requires_snapshot and not outcome.snapshot_id:
                raise ContractViolationError(
                    "Publish must return the completed snapshot ID."
                )
            if not requires_snapshot and outcome.snapshot_id is not None:
                raise ContractViolationError(
                    "Only successful publish may return a snapshot ID."
                )
        except (Exception, KeyboardInterrupt) as error:
            self.store.finish_step(
                step_id,
                "FAILED",
                error_type=type(error).__name__,
                error_message=_error_message(error),
            )
            self._log_failure(context.run_id, error, context.stage, context.tickers)
            raise
        self.store.finish_step(
            step_id,
            outcome.status,
            issuer_id=outcome.issuer_id,
            filing_id=outcome.filing_id,
        )
        logger.info(
            "Pipeline step finished",
            extra={
                **log_fields,
                "event": "step_finished",
                "issuer_id": outcome.issuer_id,
                "filing_id": outcome.filing_id,
                "details": {"status": outcome.status, "tickers": context.tickers},
            },
        )
        return outcome

    @staticmethod
    def _log_failure(
        run_id: str,
        error: BaseException,
        stage: Stage | None = None,
        tickers: tuple[str, ...] = (),
    ) -> None:
        logger.error(
            _error_message(error),
            extra={
                "event": "run_failed" if stage is None else "step_failed",
                "run_id": run_id,
                "stage": stage,
                "details": {"error_type": type(error).__name__, "tickers": tickers},
            },
        )
