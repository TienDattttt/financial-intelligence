"""Command-line entry point for orchestration and operational health checks."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

from finintel.exceptions import FinIntelError
from finintel.pipeline.health import health_check
from finintel.pipeline.runner import (
    Handler,
    Runner,
    RunRequest,
    Stage,
    TriggerMode,
    _error_message,
)
from finintel.pipeline.state_store import StateStore

logger = logging.getLogger("finintel.pipeline")


@dataclass(frozen=True)
class Settings:
    state_path: Path
    min_free_bytes: int
    tickers: tuple[str, ...]


def load_settings(path: Path) -> Settings:
    yaml = import_module("yaml")
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ValueError("Unable to read the YAML settings file.") from None
    if not isinstance(content, dict):
        raise ValueError("Settings must be a YAML mapping.")
    state_path = content.get("state_path", "data/metadata/state.sqlite")
    min_space = content.get("min_free_bytes", 0)
    tickers = content.get("tickers", [])
    if not isinstance(state_path, str) or not state_path.strip():
        raise ValueError("state_path must be a nonempty path.")
    if type(min_space) is not int or min_space < 0:
        raise ValueError("min_free_bytes must be a non-negative integer.")
    if not isinstance(tickers, list) or any(
        not isinstance(ticker, str) for ticker in tickers
    ):
        raise ValueError("tickers must be a list of strings.")
    return Settings(Path(state_path), min_space, tuple(tickers))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        details = getattr(record, "details", {})
        return json.dumps(
            {
                "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname,
                "run_id": getattr(record, "run_id", None),
                "stage": getattr(record, "stage", None),
                "issuer_id": getattr(record, "issuer_id", None),
                "filing_id": getattr(record, "filing_id", None),
                "event": getattr(record, "event", "pipeline"),
                "details": {**details, "message": record.getMessage()},
            },
            ensure_ascii=False,
        )


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    sha = result.stdout.strip()
    if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
        return sha
    return None


def main(
    argv: list[str] | None = None,
    *,
    handlers: Mapping[Stage, Handler] | None = None,
    connectivity_check: Callable[[], None] | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="finintel")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/settings.yaml"),
        help="YAML settings path. Relative paths resolve from the current working directory.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run the pipeline or replay selected stages.")
    health = commands.add_parser(
        "health", help="Check the state directory and selected source."
    )
    for command in (run, health):
        command.add_argument("--source", choices=("fhsc", "file"), default="fhsc")
    run.add_argument(
        "--ticker", action="append", help="Limit the run to a ticker. Repeat as needed."
    )
    run.add_argument("--period", help="Limit the run to YYYY or YYYY-QN.")
    run.add_argument("--stage", choices=[stage.value.lower() for stage in Stage])
    run.add_argument("--continue-on-partial-ingest", action="store_true")
    run.add_argument(
        "--trigger-mode", choices=list(TriggerMode), default=TriggerMode.CLI_MANUAL
    )
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config)
        request = (
            RunRequest(
                source=args.source,
                tickers=tuple(args.ticker)
                if args.ticker is not None
                else settings.tickers,
                period=args.period,
                stage=Stage(args.stage.upper()) if args.stage else None,
                continue_on_partial_ingest=args.continue_on_partial_ingest,
                trigger_mode=TriggerMode(args.trigger_mode),
            )
            if args.command == "run"
            else None
        )
    except ValueError as error:
        parser.error(str(error))

    store = StateStore(settings.state_path)
    log_handler = logging.StreamHandler()
    log_handler.setFormatter(JsonFormatter())
    previous_level, previous_propagate = logger.level, logger.propagate
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(log_handler)
    try:
        if request is None:
            store.initialize()
            health_check(
                store,
                online=args.source == "fhsc",
                min_free_bytes=settings.min_free_bytes,
                connectivity_check=connectivity_check,
            )
            logger.info("Health check passed", extra={"event": "health_passed"})
            return 0
        return (
            Runner(
                store,
                handlers or {},
                min_free_bytes=settings.min_free_bytes,
                connectivity_check=connectivity_check,
                git_commit_sha=_git_commit(),
            )
            .run(request)
            .exit_code
        )
    except (FinIntelError, OSError, sqlite3.Error) as error:
        logger.error(
            _error_message(error),
            extra={
                "event": "operation_failed",
                "details": {"error_type": type(error).__name__},
            },
        )
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        logger.removeHandler(log_handler)
        log_handler.close()
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


if __name__ == "__main__":
    raise SystemExit(main())
