"""Thin local/dev CLI entry point sharing the unified DBSP transform flow with Dagster.

Production orchestration runs via Dagster (``finintel.orchestration``). This CLI
serves as a thin local development and operational health-check runner whose
``ANALYTICS`` stage delegates directly to the same DBSP incremental transform
engine (``run_incremental_analytics_transform``) and storage schemas as Dagster
``published_financial_marts`` (ARCH-07b).

Verified Sources:
- Python argparse & PyYAML ``safe_load``: https://docs.python.org/3/library/argparse.html
- Internal Layer 3 storage settings contract:
  docs/v2_specs/04_STREAM_B1_LAYER3_DUCKLAKE_STORAGE_AND_CURATED_GC.md
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from finintel.exceptions import FinIntelError
from finintel.pipeline.analytics import AnalyticsHandler
from finintel.pipeline.health import DEFAULT_MIN_FREE_BYTES, health_check
from finintel.pipeline.ingest import IngestHandler
from finintel.pipeline.normalize import NormalizeHandler
from finintel.pipeline.publish import PublishHandler
from finintel.pipeline.quality import QualityGate1Handler, QualityGate2Handler
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


class _YamlModule(Protocol):
    YAMLError: type[Exception]

    def safe_load(self, stream: str, /) -> object: ...


@dataclass(frozen=True)
class Settings:
    state_path: Path
    min_free_bytes: int
    tickers: tuple[str, ...]
    ingestion: Mapping[str, object] = field(default_factory=dict)
    normalization: Mapping[str, object] = field(default_factory=dict)
    analytics: Mapping[str, object] = field(default_factory=dict)
    quality: Mapping[str, object] = field(default_factory=dict)
    storage: Mapping[str, object] = field(default_factory=dict)


def load_settings(path: Path) -> Settings:
    yaml = cast(_YamlModule, import_module("yaml"))
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ValueError("Unable to read the YAML settings file.") from None
    if not isinstance(content, dict):
        raise ValueError("Settings must be a YAML mapping.")
    state_path = content.get("state_path", "../data/metadata/state.sqlite")
    min_space = content.get("min_free_bytes", DEFAULT_MIN_FREE_BYTES)
    tickers = content.get("tickers", [])
    ingestion = content.get("ingestion", {})
    normalization = content.get("normalization", {})
    analytics = content.get("analytics", {})
    quality = content.get("quality", {})
    storage = content.get("storage", {})
    if not isinstance(state_path, str) or not state_path.strip():
        raise ValueError("state_path must be a nonempty path.")
    if type(min_space) is not int or min_space < 0:
        raise ValueError("min_free_bytes must be a non-negative integer.")
    if not isinstance(tickers, list) or any(
        not isinstance(ticker, str) for ticker in tickers
    ):
        raise ValueError("tickers must be a list of strings.")
    if not isinstance(ingestion, dict) or any(
        not isinstance(key, str) for key in ingestion
    ):
        raise ValueError("ingestion must be a YAML mapping.")
    if not isinstance(normalization, dict) or any(
        not isinstance(key, str) for key in normalization
    ):
        raise ValueError("normalization must be a YAML mapping.")
    if not isinstance(analytics, dict) or any(
        not isinstance(key, str) for key in analytics
    ):
        raise ValueError("analytics must be a YAML mapping.")
    if not isinstance(quality, dict) or any(
        not isinstance(key, str) for key in quality
    ):
        raise ValueError("quality must be a YAML mapping.")
    if not isinstance(storage, dict) or any(
        not isinstance(key, str) for key in storage
    ):
        raise ValueError("storage must be a YAML mapping.")
    env_data_root = os.getenv("FININTEL_DATA_ROOT", "").strip()
    if env_data_root:
        root_override = Path(env_data_root).resolve()
        resolved_state = (root_override / "metadata" / "state.sqlite").resolve()
        merged_storage = dict(storage)
        raw_cat = merged_storage.get("catalog")
        merged_cat = dict(raw_cat) if isinstance(raw_cat, Mapping) else {}
        merged_cat["sqlite_path"] = str(
            (root_override / "metadata" / "ducklake_catalog.sqlite").resolve()
        )
        merged_storage["catalog"] = merged_cat
        merged_storage["data_path"] = str(
            (root_override / "lakehouse" / "data").resolve()
        )
        storage = merged_storage
    else:
        resolved_state = (path.resolve().parent / state_path).resolve()

    return Settings(
        resolved_state,
        min_space,
        tuple(tickers),
        ingestion,
        normalization,
        analytics,
        quality,
        storage,
    )


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        raw_details = getattr(record, "details", {})
        details = (
            dict(raw_details)
            if isinstance(raw_details, Mapping)
            else {"value": raw_details}
        )
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
            default=str,
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
        help="YAML settings file. Relative state_path values resolve from this file's directory.",
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
    root_logger = logging.getLogger("finintel")
    log_handler = logging.StreamHandler()
    log_handler.setFormatter(JsonFormatter())
    previous_root_level, previous_root_propagate = (
        root_logger.level,
        root_logger.propagate,
    )
    previous_level, previous_propagate = logger.level, logger.propagate
    root_logger.setLevel(logging.INFO)
    root_logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.propagate = True
    root_logger.addHandler(log_handler)
    try:
        if request is None:
            if not store.path.exists():
                store.initialize()
            health_check(
                store,
                online=args.source == "fhsc",
                min_free_bytes=settings.min_free_bytes,
                connectivity_check=connectivity_check,
            )
            logger.info("Health check passed", extra={"event": "health_passed"})
            return 0
        config_dir = args.config.resolve().parent
        configured = (
            handlers
            if handlers is not None
            else {
                Stage.INGEST: IngestHandler(store, config_dir, settings.ingestion),
                Stage.NORMALIZE: NormalizeHandler(
                    store,
                    config_dir,
                    settings.ingestion,
                    settings.normalization,
                ),
                Stage.QUALITY_GATE_1: QualityGate1Handler(
                    store, config_dir, settings.analytics, settings.quality
                ),
                Stage.ANALYTICS: AnalyticsHandler(
                    store,
                    config_dir,
                    settings.analytics,
                    quality_settings=settings.quality,
                ),
                Stage.QUALITY_GATE_2: QualityGate2Handler(
                    store, config_dir, settings.analytics
                ),
                Stage.PUBLISH: PublishHandler(
                    store,
                    config_dir,
                    settings.analytics,
                    storage_settings=settings.storage,
                ),
            }
        )
        return (
            Runner(
                store,
                configured,
                min_free_bytes=settings.min_free_bytes,
                connectivity_check=connectivity_check,
                git_commit_sha=_git_commit(),
            )
            .run(request)
            .exit_code
        )
    except Exception as error:
        # The built-in health checks wrap provider failures in fixed messages.
        message = (
            str(error)
            if request is None
            and connectivity_check is None
            and isinstance(error, FinIntelError)
            else _error_message(error)
        )
        logger.error(
            message,
            extra={
                "event": "operation_failed",
                "details": {"error_type": type(error).__name__},
            },
        )
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        root_logger.removeHandler(log_handler)
        log_handler.close()
        root_logger.setLevel(previous_root_level)
        root_logger.propagate = previous_root_propagate
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


if __name__ == "__main__":
    raise SystemExit(main())
