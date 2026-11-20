"""Partitioned Dagster software-defined assets and lakehouse maintenance job.

Verified Sources:
    - Dagster Official Documentation — Partitions and Backfills:
      https://docs.dagster.io/guides/build/partitions-and-backfills/
    - Dagster Official Documentation — Limiting Concurrency (op-level ``pool=``):
      https://docs.dagster.io/guides/limit-concurrency
    - Budiu, M., Chajed, T., McSherry, F., Ryzhyk, L., & Tannen, V. (2023).
      "DBSP: Automatic Incremental View Maintenance for Rich Query Languages."
      PVLDB Vol. 16, No. 7 (consumed via ``finintel.transform.incremental``).
    - Runtime API verification in project ``.venv`` (Python 3.11.9, ``dagster==1.13.30``):
      - ``@dg.asset(deps=..., partitions_def=..., backfill_policy=..., pool=..., dagster_type=dg.Nothing)``
        allows per-partition ``yield dg.AssetMaterialization(...)`` followed by ``yield dg.Output(None)``
        so completed partitions are persisted before mid-batch ``RateLimitOrCircuitOpenError``
        and downstream ``deps=[...]`` execute cleanly without IO-manager multi-partition conflicts.
      - ``dg.BackfillPolicy.multi_run(max_partitions_per_run=10)`` matches across partitioned
        upstream/downstream assets.
      - ``dg.ScheduleDefinition(name=..., cron_schedule="0 3 * * 0", job=..., execution_timezone="Asia/Ho_Chi_Minh")``.
"""

import hashlib
import json
import os
import re
import sqlite3
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import dagster as dg
import polars as pl
from dagster._core.storage.tags import BACKFILL_ID_TAG
from dagster._core.types.dagster_type import resolve_dagster_type

from finintel.domain.schemas import PeriodType, StatementScope
from finintel.exceptions import (
    ContractViolationError,
    RateLimitOrCircuitOpenError,
    TransientSourceError,
)
from finintel.ingestion.archive import RawArchive
from finintel.ingestion.disclosure_enricher import (
    DisclosureRecord,
    DisclosureSlot,
    enrich_fhsc_manifest_disclosures,
    merge_disclosure_records,
    parse_cafef_bctc_disclosures,
    parse_vietcap_iq_disclosures,
)
from finintel.normalization.archive import (
    NORMALIZATION_METADATA,
    normalize_archive_incremental,
)
from finintel.orchestration.partitions import (
    CORPORATE_TICKERS,
    FHSC_API_POOL,
    PUBLIC_DISCLOSURE_POOL,
)
from finintel.orchestration.resources import (
    DisclosureFetcherResource,
    FinIntelPathsResource,
    StateStoreResource,
    StorageResource,
)
from finintel.pipeline.ingest import IngestHandler
from finintel.pipeline.normalize import DEFAULT_TAXONOMY_DIR
from finintel.pipeline.runner import (
    RunRequest,
    RunStatus,
    Stage,
    StepContext,
    StepStatus,
    TriggerMode,
)
from finintel.pipeline.state_store import StateStore
from finintel.quality.rules import QualityGateConfig
from finintel.storage.manifest_export import (
    build_filings_manifest_snapshot,
    build_latest_facts_table,
)
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_TABLE,
    BENCHMARKS_TABLE,
    FILINGS_MANIFEST_TABLE,
    ML_SCORES_TABLE,
    QUALITY_SUMMARY_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)
from finintel.storage.snapshot_publisher import (
    TableInput,
    pipeline_write_lock,
)
from finintel.transform import (
    MLTransformConfig,
    load_transform_state,
    run_blocking_publish_audits,
    run_incremental_analytics_transform,
    write_transform_state,
)

VN_TZ = timezone(timedelta(hours=7))
FHSC_BACKFILL_POLICY = dg.BackfillPolicy.multi_run(max_partitions_per_run=10)
_NOTHING_DAGSTER_TYPE: dg.DagsterType = resolve_dagster_type(dg.Nothing)


class FhscIngestConfig(dg.Config):
    """Runtime options for ``raw_fhsc_filings`` (sync vs backfill early-stop control)."""

    mode: str = "sync"
    period: str | None = None
    as_of_year: int | None = None
    incremental_lookback_years: int | None = None
    infer_calendar_schedule: bool | None = None
    default_statement_scope: str | None = None


class NormalizeAssetConfig(dg.Config):
    """Runtime options for ``normalized_financial_facts``."""

    source: str = "fhsc"
    period: str | None = None
    batch_issuers: int = 50


class PublishMartsConfig(dg.Config):
    """Runtime options for ``published_financial_marts``."""

    as_of: str | None = None
    facts_path: str | None = None
    source: str = "fhsc"
    ml_enabled: bool = False
    ml_validation_start: str | None = None
    ml_test_start: str | None = None
    ml_approved_target: bool = False
    max_critical_issuer_ratio: float | None = None


_GIT_SHA_CACHE: dict[str, str | None] = {}


def _resolve_git_commit_sha(project_root: Path) -> str | None:
    """Resolve current git commit SHA from env or local repo (cached per root)."""
    env_sha = os.getenv("FININTEL_GIT_COMMIT_SHA")
    if env_sha and env_sha.strip():
        return env_sha.strip()[:40]
    key = str(project_root.resolve())
    if key in _GIT_SHA_CACHE:
        return _GIT_SHA_CACHE[key]
    resolved: str | None = None
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=key,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            resolved = proc.stdout.strip()[:40]
    except OSError:
        resolved = None
    _GIT_SHA_CACHE[key] = resolved
    return resolved


def _snapshot_id_for_run(run_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", run_id)[:18] or "run"
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:8]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"snap_{stamp}_{safe}_{digest}"


def _allocate_pipeline_run_id(state_store: StateStore, dagster_run_id: str) -> str:
    """Return a unique ``pipeline_runs.run_id`` even when Dagster retries a step."""
    if state_store.get_run(dagster_run_id) is None:
        return dagster_run_id
    attempt = 1
    while True:
        candidate = f"{dagster_run_id}_r{attempt}"
        if state_store.get_run(candidate) is None:
            return candidate
        attempt += 1


def _load_manifest_period_end_dates(
    state: StateStore, target_tickers: Sequence[str]
) -> dict[DisclosureSlot, date]:
    if not target_tickers or not state.path.exists():
        return {}
    result: dict[DisclosureSlot, date] = {}
    try:
        with state.connection() as conn:
            table_exists = (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'filings_manifest'"
                ).fetchone()
                is not None
            )
            if not table_exists:
                return {}
            for start in range(0, len(target_tickers), 500):
                chunk = tuple(target_tickers[start : start + 500])
                placeholders = ", ".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""SELECT ticker_at_filing, period_type, fiscal_year,
                               fiscal_quarter, statement_scope, period_end_date
                        FROM filings_manifest
                        WHERE source_provider = 'fhsc'
                          AND ticker_at_filing IN ({placeholders})""",
                    chunk,
                ).fetchall()
                for row in rows:
                    try:
                        scope = StatementScope(str(row["statement_scope"]))
                        ptype = PeriodType(str(row["period_type"]))
                        if (
                            scope == StatementScope.UNKNOWN
                            or ptype == PeriodType.SEMI_ANNUAL
                        ):
                            continue
                        slot: DisclosureSlot = (
                            str(row["ticker_at_filing"]),
                            ptype,
                            int(row["fiscal_year"]),
                            int(row["fiscal_quarter"])
                            if row["fiscal_quarter"] is not None
                            else None,
                            scope,
                        )
                        result[slot] = date.fromisoformat(str(row["period_end_date"]))
                    except (ValueError, TypeError):
                        continue
    except sqlite3.OperationalError:
        return {}
    return result


def _is_valid_full_normalize_batch(candidate: Path, *, source: str) -> bool:
    facts = candidate / "financial_facts.parquet"
    unmapped = candidate / "unmapped_metrics.parquet"
    meta_path = candidate / NORMALIZATION_METADATA
    if not (facts.is_file() and unmapped.is_file() and meta_path.is_file()):
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(meta, dict)
        and meta.get("source") == source
        and meta.get("full_history") is True
        and isinstance(meta.get("taxonomy_sha256"), str)
        and isinstance(meta.get("issuer_filings"), dict)
    )


def _find_latest_full_normalize_batch(
    state: StateStore,
    parent: Path,
    *,
    source: str,
    exclude_run_id: str | None = None,
) -> Path | None:
    """Find newest full-history normalization batch for incremental early cutoff."""
    if not parent.is_dir():
        return None
    try:
        for run_id, _ in state.completed_normalizations(source):
            if exclude_run_id is not None and run_id == exclude_run_id:
                continue
            candidate = parent / run_id
            if _is_valid_full_normalize_batch(candidate, source=source):
                return candidate
    except sqlite3.Error:
        pass

    candidates: list[tuple[int, str, Path]] = []
    for entry in parent.iterdir():
        if (
            not entry.is_dir()
            or entry.is_symlink()
            or entry.name.startswith(".")
            or (exclude_run_id is not None and entry.name == exclude_run_id)
        ):
            continue
        if _is_valid_full_normalize_batch(entry, source=source):
            try:
                mtime_ns = entry.stat().st_mtime_ns
            except OSError:
                continue
            candidates.append((mtime_ns, entry.name, entry))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def _resolve_facts_path_for_publish(
    *,
    paths: FinIntelPathsResource,
    state_store: StateStore,
    config: PublishMartsConfig,
    dagster_run_id: str,
) -> Path:
    if config.facts_path is not None:
        explicit = Path(config.facts_path)
        resolved = (
            explicit
            if explicit.is_absolute()
            else paths.resolved_project_root() / explicit
        ).resolve()
        if not resolved.is_file():
            raise ContractViolationError(
                f"Configured facts_path does not exist: {resolved}"
            )
        return resolved

    curated = paths.resolved_curated_root()
    batches_parent = curated / "financial_facts"
    same_run = batches_parent / dagster_run_id / "financial_facts.parquet"
    if same_run.is_file():
        return same_run.resolve()

    latest_batch = _find_latest_full_normalize_batch(
        state_store, batches_parent, source=config.source
    )
    if latest_batch is not None:
        return (latest_batch / "financial_facts.parquet").resolve()

    compat_fallback = curated / "financial_facts.parquet"
    if compat_fallback.is_file():
        return compat_fallback.resolve()

    raise ContractViolationError(
        "No normalized financial_facts.parquet batch is available for publication."
    )


def _resolve_as_of_date(
    config: PublishMartsConfig, settings: Mapping[str, object]
) -> date:
    if config.as_of is not None:
        try:
            return date.fromisoformat(config.as_of)
        except ValueError as exc:
            raise ContractViolationError(
                "PublishMartsConfig.as_of must be YYYY-MM-DD."
            ) from exc
    analytics_cfg = settings.get("analytics")
    if isinstance(analytics_cfg, Mapping):
        raw_as_of = analytics_cfg.get("as_of")
        if isinstance(raw_as_of, str) and raw_as_of.strip():
            try:
                return date.fromisoformat(raw_as_of.strip())
            except ValueError as exc:
                raise ContractViolationError(
                    "settings.yaml analytics.as_of must be YYYY-MM-DD."
                ) from exc
    return datetime.now(VN_TZ).date()


def _resolve_quality_config(
    config: PublishMartsConfig, settings: Mapping[str, object]
) -> QualityGateConfig:
    if config.max_critical_issuer_ratio is not None:
        return QualityGateConfig(
            max_critical_issuer_ratio=config.max_critical_issuer_ratio
        )
    quality_cfg = settings.get("quality")
    if isinstance(quality_cfg, Mapping):
        raw_ratio = quality_cfg.get("max_critical_issuer_ratio")
        if isinstance(raw_ratio, (int, float)) and not isinstance(raw_ratio, bool):
            return QualityGateConfig(max_critical_issuer_ratio=float(raw_ratio))
    return QualityGateConfig()


def _resolve_ml_config(config: PublishMartsConfig) -> MLTransformConfig:
    if not config.ml_enabled:
        return MLTransformConfig(enabled=False)
    val_start: date | None = None
    test_start: date | None = None
    if config.ml_validation_start is not None:
        try:
            val_start = date.fromisoformat(config.ml_validation_start)
        except ValueError as exc:
            raise ContractViolationError(
                "PublishMartsConfig.ml_validation_start must be YYYY-MM-DD."
            ) from exc
    if config.ml_test_start is not None:
        try:
            test_start = date.fromisoformat(config.ml_test_start)
        except ValueError as exc:
            raise ContractViolationError(
                "PublishMartsConfig.ml_test_start must be YYYY-MM-DD."
            ) from exc
    return MLTransformConfig(
        enabled=True,
        validation_start=val_start,
        test_start=test_start,
        approved_target=config.ml_approved_target,
        require_exact_filing=True,
    )


def _read_optional_snapshot_table(
    repo_session: object, table_name: str
) -> pl.DataFrame | None:
    """Read a table from a pinned ParquetRepository session, returning None if absent."""
    from finintel.storage.repository import ParquetRepository

    assert isinstance(repo_session, ParquetRepository)
    try:
        return repo_session.read_table(table_name)
    except (FileNotFoundError, ContractViolationError):
        return None


@dg.asset(
    partitions_def=CORPORATE_TICKERS,
    backfill_policy=FHSC_BACKFILL_POLICY,
    pool=FHSC_API_POOL,
    dagster_type=_NOTHING_DAGSTER_TYPE,
    group_name="ingestion",
    description=(
        "Ingest immutable raw FHSC financial statements per corporate ticker partition. "
        "Op-level concurrency is bounded by fhsc_api_pool (limit=1); 0.6 req/s request-rate "
        "pacing is enforced inside FhscOapiAdapter."
    ),
)
def raw_fhsc_filings(
    context: dg.AssetExecutionContext,
    config: FhscIngestConfig,
    paths: FinIntelPathsResource,
    state: StateStoreResource,
) -> Iterator[dg.AssetMaterialization | dg.Output[None]]:
    """Ingest FHSC statements for one or more corporate ticker partitions.

    Emits ``dg.AssetMaterialization`` immediately after each completed ticker so
    a mid-batch ``RateLimitOrCircuitOpenError`` fails fast without losing prior
    completed partitions. Early-stop pagination is disabled on backfills and
    enabled on incremental syncs.
    """
    if config.mode not in ("sync", "backfill"):
        raise ContractViolationError(
            f"FhscIngestConfig.mode must be 'sync' or 'backfill', got {config.mode!r}."
        )
    if (
        config.default_statement_scope is not None
        and config.default_statement_scope
        not in (
            "CONSOLIDATED",
            "SEPARATE",
        )
    ):
        raise ContractViolationError(
            "FhscIngestConfig.default_statement_scope must be 'CONSOLIDATED' or 'SEPARATE'."
        )
    state_store = state.initialize()
    settings = paths.load_settings_dict()
    raw_ingest = settings.get("ingestion")
    if raw_ingest is not None and not isinstance(raw_ingest, Mapping):
        raise ContractViolationError("settings.yaml 'ingestion' must be a mapping.")
    fhsc_settings: dict[str, object] = (
        dict(raw_ingest) if raw_ingest is not None else {}
    )
    # Disclosure enrichment executes in its own downstream asset (public_disclosure_pool).
    fhsc_settings["disclosure_enrichment"] = {"enabled": False}
    if paths.raw_root is not None and "raw_path" not in fhsc_settings:
        fhsc_settings["raw_path"] = str(paths.resolved_raw_root())
    if config.incremental_lookback_years is not None:
        fhsc_settings["incremental_lookback_years"] = config.incremental_lookback_years
    if config.infer_calendar_schedule is not None:
        fhsc_settings["infer_calendar_schedule"] = config.infer_calendar_schedule
    if config.default_statement_scope is not None:
        fhsc_settings["default_statement_scope"] = config.default_statement_scope
    if (
        fhsc_settings.get("infer_calendar_schedule") is True
        and "issuers_path" not in fhsc_settings
    ):
        fhsc_settings["issuers_path"] = str(paths.resolved_issuers_path())

    is_backfill = config.mode == "backfill" or BACKFILL_ID_TAG in context.run.tags
    trigger_mode = TriggerMode.BACKFILL if is_backfill else TriggerMode.SCHEDULED_CRON
    active = state_store.active_snapshot()
    active_snapshot_id = str(active["snapshot_id"]) if active is not None else None

    batch_tickers = tuple(context.partition_keys)
    completed: list[str] = []

    for ticker in batch_tickers:
        request = RunRequest(
            source="fhsc",
            tickers=(ticker,),
            period=config.period,
            stage=Stage.INGEST,
            trigger_mode=trigger_mode,
        )
        step_ctx = StepContext(
            run_id=context.run.run_id,
            stage=Stage.INGEST,
            request=request,
            tickers=(ticker,),
            active_snapshot_id=active_snapshot_id,
        )
        handler = IngestHandler(
            state_store,
            paths.resolved_config_dir(),
            fhsc_settings,
            as_of_year=config.as_of_year,
        )
        try:
            outcome = handler(step_ctx)
        except RateLimitOrCircuitOpenError:
            context.log.warning(
                "FHSC rate limit or circuit breaker tripped on ticker %s after "
                "completing %d/%d partition(s) %s; aborting batch fail-fast.",
                ticker,
                len(completed),
                len(batch_tickers),
                completed,
            )
            raise

        completed.append(ticker)
        yield dg.AssetMaterialization(
            asset_key=context.asset_key,
            partition=ticker,
            metadata={
                "ticker": ticker,
                "status": str(outcome.status),
                "mode": "backfill" if is_backfill else "sync",
                "early_stop_enabled": not is_backfill,
                "issuer_id": outcome.issuer_id or "",
            },
        )

    yield dg.Output(value=None)


@dg.asset(
    deps=[raw_fhsc_filings],
    partitions_def=CORPORATE_TICKERS,
    backfill_policy=FHSC_BACKFILL_POLICY,
    pool=PUBLIC_DISCLOSURE_POOL,
    dagster_type=_NOTHING_DAGSTER_TYPE,
    group_name="ingestion",
    description=(
        "Fetch Vietcap IQ & CafeF BCTC public disclosure dates after raw_fhsc_filings "
        "completes, merge with PIT audit-tier guards, and archive SHA-256 receipts."
    ),
)
def raw_public_disclosures(
    context: dg.AssetExecutionContext,
    paths: FinIntelPathsResource,
    state: StateStoreResource,
    disclosure_fetcher: DisclosureFetcherResource,
) -> Iterator[dg.AssetMaterialization | dg.Output[None]]:
    """Enrich archived FHSC filings with verified Vietcap IQ and CafeF disclosure dates."""
    disclosure_fetcher.validate_config()
    state_store = state.initialize()
    archive = RawArchive(paths.resolved_raw_root(), state_store)
    max_date = disclosure_fetcher.parsed_max_date()
    batch_tickers = tuple(context.partition_keys)

    vietcap_records: list[DisclosureRecord] = []
    cafef_records: list[DisclosureRecord] = []
    evidence_payloads: dict[str, bytes] = {}
    config_dir = paths.resolved_config_dir()
    attempted_fetches = 0
    failed_fetches = 0
    parse_errors = 0

    with disclosure_fetcher.session():
        for ticker in batch_tickers:
            vci_url = disclosure_fetcher.validate_url(
                disclosure_fetcher.vietcap_url_template.format(ticker=ticker)
            )
            cafef_url = disclosure_fetcher.validate_url(
                disclosure_fetcher.cafef_url_template.format(ticker=ticker)
            )

            vci_bytes: bytes | None = None
            attempted_fetches += 1
            try:
                vci_bytes = disclosure_fetcher.fetch_payload(
                    local_filename=f"{ticker}.json",
                    source_url=vci_url,
                    base_dir=config_dir,
                    state_store=state_store,
                )
            except ContractViolationError:
                raise
            except Exception as exc:
                failed_fetches += 1
                if disclosure_fetcher.on_error == "fail":
                    raise
                context.log.warning(
                    "Vietcap IQ disclosure fetch skipped for %s (%s)",
                    ticker,
                    type(exc).__name__,
                )
            if vci_bytes is not None:
                try:
                    parsed_vci = parse_vietcap_iq_disclosures(
                        ticker,
                        vci_bytes,
                        source_url=vci_url,
                        max_date=max_date,
                    )
                    evidence_payloads[vci_url] = vci_bytes
                    vietcap_records.extend(parsed_vci)
                except Exception as exc:
                    parse_errors += 1
                    if disclosure_fetcher.on_error == "fail":
                        raise
                    context.log.warning(
                        "Vietcap IQ disclosure parse skipped for %s (%s)",
                        ticker,
                        type(exc).__name__,
                    )

            cafef_bytes: bytes | None = None
            attempted_fetches += 1
            try:
                cafef_bytes = disclosure_fetcher.fetch_payload(
                    local_filename=f"{ticker}.html",
                    source_url=cafef_url,
                    base_dir=config_dir,
                    state_store=state_store,
                )
            except ContractViolationError:
                raise
            except Exception as exc:
                failed_fetches += 1
                if disclosure_fetcher.on_error == "fail":
                    raise
                context.log.warning(
                    "CafeF BCTC disclosure fetch skipped for %s (%s)",
                    ticker,
                    type(exc).__name__,
                )
            if cafef_bytes is not None:
                try:
                    parsed_cafef = parse_cafef_bctc_disclosures(
                        ticker,
                        cafef_bytes,
                        source_url=cafef_url,
                        max_date=max_date,
                    )
                    evidence_payloads[cafef_url] = cafef_bytes
                    cafef_records.extend(parsed_cafef)
                except Exception as exc:
                    parse_errors += 1
                    if disclosure_fetcher.on_error == "fail":
                        raise
                    context.log.warning(
                        "CafeF BCTC disclosure parse skipped for %s (%s)",
                        ticker,
                        type(exc).__name__,
                    )

    if (
        disclosure_fetcher.fetch_online
        and attempted_fetches > 0
        and (failed_fetches + parse_errors) >= attempted_fetches
        and not vietcap_records
        and not cafef_records
    ):
        raise TransientSourceError(
            "All online public disclosure fetches/parses failed "
            f"({failed_fetches} fetch failures, {parse_errors} parse failures across "
            f"{attempted_fetches} attempts); refusing silent STATUTORY_LAG degradation."
        )

    period_end_dates = _load_manifest_period_end_dates(state_store, batch_tickers)
    merged = merge_disclosure_records(
        vietcap_records,
        cafef_records,
        period_end_dates=period_end_dates,
    )
    summary = enrich_fhsc_manifest_disclosures(
        archive,
        list(merged.values()),
        evidence_payloads=evidence_payloads,
        fetched_at=datetime.now(UTC),
        run_id=context.run.run_id,
        tickers=batch_tickers,
    )

    for ticker in batch_tickers:
        yield dg.AssetMaterialization(
            asset_key=context.asset_key,
            partition=ticker,
            metadata={
                "ticker": ticker,
                "matched_filings": summary.matched_filings,
                "upgraded_filings": summary.upgraded_filings,
                "unchanged_filings": summary.unchanged_filings,
                "skipped_pre_period_end": summary.skipped_pre_period_end,
                "attempted_fetches": attempted_fetches,
                "failed_fetches": failed_fetches,
                "parse_errors": parse_errors,
            },
        )

    yield dg.Output(value=None)


@dg.asset(
    deps=[raw_fhsc_filings, raw_public_disclosures],
    group_name="normalization",
    description=(
        "Incrementally normalize archived raw filings into an immutable "
        "curated/financial_facts/<run_id>/ batch via normalize_archive_incremental()."
    ),
)
def normalized_financial_facts(
    context: dg.AssetExecutionContext,
    config: NormalizeAssetConfig,
    paths: FinIntelPathsResource,
    state: StateStoreResource,
) -> dg.MaterializeResult[None]:
    """Normalize archived filings incrementally into ``curated/financial_facts/<run_id>/``."""
    if config.source not in ("fhsc", "file"):
        raise ContractViolationError(
            f"NormalizeAssetConfig.source must be 'fhsc' or 'file', got {config.source!r}."
        )
    state_store = state.initialize()
    settings = paths.load_settings_dict()
    raw_norm = settings.get("normalization")
    if raw_norm is not None and not isinstance(raw_norm, Mapping):
        raise ContractViolationError("settings.yaml 'normalization' must be a mapping.")
    norm_settings: Mapping[str, object] = raw_norm if raw_norm is not None else {}

    taxonomy_setting = norm_settings.get("taxonomy_dir")
    if taxonomy_setting is not None:
        if not isinstance(taxonomy_setting, str) or not taxonomy_setting.strip():
            raise ContractViolationError(
                "normalization.taxonomy_dir must be a non-empty path."
            )
        tax_dir = (paths.resolved_config_dir() / taxonomy_setting.strip()).resolve()
    elif (paths.resolved_config_dir() / "metric_definitions.yaml").is_file():
        tax_dir = paths.resolved_config_dir()
    else:
        tax_dir = DEFAULT_TAXONOMY_DIR

    filing_settings = norm_settings.get("filings", {})
    if not isinstance(filing_settings, dict) or any(
        not isinstance(key, str) for key in filing_settings
    ):
        raise ContractViolationError(
            "normalization.filings must be a mapping keyed by filing ID."
        )

    parent = paths.resolved_curated_root() / "financial_facts"
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / context.run.run_id
    previous_batch_dir = (
        _find_latest_full_normalize_batch(
            state_store,
            parent,
            source=config.source,
            exclude_run_id=context.run.run_id,
        )
        if config.period is None
        else None
    )

    git_sha = _resolve_git_commit_sha(paths.resolved_project_root())
    norm_run_id = _allocate_pipeline_run_id(state_store, context.run.run_id)
    state_store.start_run(
        norm_run_id,
        TriggerMode.SCHEDULED_CRON.value,
        {
            "source": config.source,
            "period": config.period,
            "orchestrator": "dagster",
            "dagster_run_id": context.run.run_id,
            "batch_dir": str(destination),
        },
        git_sha,
    )
    step_id = state_store.start_step(norm_run_id, Stage.NORMALIZE.value)
    try:
        result = normalize_archive_incremental(
            state_store,
            paths.resolved_raw_root(),
            tax_dir,
            destination,
            source=config.source,
            period=config.period,
            filing_settings=filing_settings,
            previous_batch_dir=previous_batch_dir,
            batch_issuers=config.batch_issuers,
        )
        state_store.finish_step(step_id, StepStatus.SUCCEEDED.value)
        state_store.finish_run(norm_run_id, RunStatus.SUCCEEDED.value)
    except BaseException:
        state_store.finish_step(step_id, "FAILED")
        state_store.finish_run(norm_run_id, RunStatus.FAILED.value)
        raise
    return dg.MaterializeResult(
        metadata={
            "batch_dir": str(destination),
            "facts_path": str(result.facts_path),
            "unmapped_path": str(result.unmapped_path),
            "facts_rows": result.facts_rows,
            "unmapped_rows": result.unmapped_rows,
            "recomputed_issuers": result.recomputed_issuers,
            "full_rebuild": result.full_rebuild,
            "taxonomy_sha256": result.taxonomy_sha256,
        }
    )


@dg.asset(
    deps=[normalized_financial_facts],
    group_name="marts",
    description=(
        "Execute single-writer incremental analytics transform, blocking pre-publish "
        "quality/DuPont audits, and single atomic multi-table snapshot publication under "
        "pipeline.lock + state.sqlite RUNNING run."
    ),
)
def published_financial_marts(
    context: dg.AssetExecutionContext,
    config: PublishMartsConfig,
    paths: FinIntelPathsResource,
    state: StateStoreResource,
    storage: StorageResource,
) -> dg.MaterializeResult[None]:
    """Transform, audit, and atomically publish all financial mart tables under ``pipeline.lock``."""
    if config.source not in ("fhsc", "file"):
        raise ContractViolationError(
            f"PublishMartsConfig.source must be 'fhsc' or 'file', got {config.source!r}."
        )
    settings = paths.load_settings_dict()
    as_of = _resolve_as_of_date(config, settings)
    quality_cfg = _resolve_quality_config(config, settings)
    ml_cfg = _resolve_ml_config(config)
    storage_cfg = storage.resolve_storage_config(paths)
    lock_timeout = storage.resolve_lock_timeout()
    lock_path = state.pipeline_lock_path()

    git_sha = _resolve_git_commit_sha(paths.resolved_project_root())
    with pipeline_write_lock(lock_path, timeout=lock_timeout):
        state_store = state.initialize()
        recovered = state_store.recover_interrupted_runs()
        if int(recovered.get("runs_recovered", 0)) > 0:
            context.log.warning(
                "Recovered %d interrupted RUNNING pipeline run(s) before publish.",
                int(recovered.get("runs_recovered", 0)),
            )
        pipeline_run_id = _allocate_pipeline_run_id(state_store, context.run.run_id)
        state_store.start_run(
            pipeline_run_id,
            TriggerMode.SCHEDULED_CRON.value,
            {
                "source": config.source,
                "orchestrator": "dagster",
                "dagster_run_id": context.run.run_id,
                "as_of": as_of.isoformat(),
                "ml_enabled": ml_cfg.enabled,
            },
            git_sha,
        )
        try:
            facts_path = _resolve_facts_path_for_publish(
                paths=paths,
                state_store=state_store,
                config=config,
                dagster_run_id=context.run.run_id,
            )
            issuers_path = paths.resolved_issuers_path()
            if not issuers_path.is_file():
                raise ContractViolationError(
                    f"Issuers reference file does not exist: {issuers_path}"
                )

            facts_df = pl.read_parquet(facts_path)
            issuers_df = pl.read_parquet(issuers_path)

            transform_state_path = paths.resolved_transform_state_path()
            previous_state = load_transform_state(transform_state_path)
            active = state_store.active_snapshot()
            previous_snapshot_id = (
                str(active["snapshot_id"]) if active is not None else None
            )

            previous_ratios: pl.DataFrame | None = None
            previous_benchmarks: pl.DataFrame | None = None
            previous_red_flags: pl.DataFrame | None = None
            if (
                previous_state is not None
                and previous_snapshot_id is not None
                and previous_state.get("snapshot_id") == previous_snapshot_id
            ):
                repo = storage.build_repository(paths)
                try:
                    with repo.snapshot_session() as pinned:
                        previous_ratios = _read_optional_snapshot_table(
                            pinned, RATIOS_TABLE
                        )
                        previous_benchmarks = _read_optional_snapshot_table(
                            pinned, BENCHMARKS_TABLE
                        )
                        previous_red_flags = _read_optional_snapshot_table(
                            pinned, RED_FLAGS_TABLE
                        )
                except (FileNotFoundError, ContractViolationError):
                    previous_ratios = None
                    previous_benchmarks = None
                    previous_red_flags = None

            bundle = run_incremental_analytics_transform(
                facts_df,
                issuers_df,
                as_of=as_of,
                run_id=pipeline_run_id,
                previous_ratios=previous_ratios,
                previous_benchmarks=previous_benchmarks,
                previous_red_flags=previous_red_flags,
                previous_state=previous_state,
                previous_snapshot_id=previous_snapshot_id,
                state_path=transform_state_path,
                ml_config=ml_cfg,
                quality_config=quality_cfg,
                persist_state=False,
            )

            updated_quality_summary = run_blocking_publish_audits(
                bundle,
                run_id=pipeline_run_id,
            )

            # Build serving helpers from clean_facts in RAM (single read, zero extra disk I/O).
            latest_facts = build_latest_facts_table(
                bundle.clean_facts, as_of=bundle.as_of
            )
            filings_manifest = build_filings_manifest_snapshot(
                state_store, bundle.clean_facts
            )

            tables: dict[str, TableInput] = {
                "issuers.parquet": bundle.issuers,
                "financial_facts.parquet": bundle.clean_facts,
                "latest_facts.parquet": latest_facts,
                FILINGS_MANIFEST_TABLE: filings_manifest,
                RATIOS_TABLE: bundle.financial_ratios,
                BENCHMARKS_TABLE: bundle.industry_benchmarks,
                RED_FLAGS_TABLE: bundle.accounting_red_flags,
                ML_SCORES_TABLE: bundle.ml_scores,
                QUALITY_SUMMARY_TABLE: updated_quality_summary,
                AUDIT_QUARANTINE_TABLE: bundle.audit_quarantine,
            }
            if storage_cfg.publish_unmapped:
                unmapped_candidate = facts_path.with_name("unmapped_metrics.parquet")
                if unmapped_candidate.is_file():
                    tables["unmapped_metrics.parquet"] = unmapped_candidate

            snapshot_id = _snapshot_id_for_run(pipeline_run_id)
            pub_info = storage.publish_snapshot(
                snapshot_id=snapshot_id,
                tables=tables,
                run_id=pipeline_run_id,
                state_store=state_store,
                paths=paths,
                metadata={
                    "run_id": pipeline_run_id,
                    "dagster_run_id": context.run.run_id,
                    "git_commit_sha": git_sha,
                    "source": config.source,
                    "as_of": as_of.isoformat(),
                },
            )
            state_store.finish_run(pipeline_run_id, "SUCCEEDED", snapshot_id)
            write_transform_state(
                transform_state_path,
                bundle.next_transform_state,
                snapshot_id=snapshot_id,
            )

            if (
                bundle.evaluation_report is not None
                or bundle.ml_run_manifest is not None
            ):
                ml_meta_dir = (
                    paths.resolved_metadata_dir() / "ml_runs" / pipeline_run_id
                )
                ml_meta_dir.mkdir(parents=True, exist_ok=True)
                if bundle.evaluation_report is not None:
                    (ml_meta_dir / "evaluation_report.json").write_text(
                        json.dumps(
                            bundle.evaluation_report,
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        ),
                        encoding="utf-8",
                    )
                if bundle.ml_run_manifest is not None:
                    (ml_meta_dir / "ml_run_manifest.json").write_text(
                        json.dumps(
                            bundle.ml_run_manifest,
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        ),
                        encoding="utf-8",
                    )
        except BaseException:
            state_store.finish_run(pipeline_run_id, "FAILED")
            raise

    return dg.MaterializeResult(
        metadata={
            "snapshot_id": snapshot_id,
            "run_id": pipeline_run_id,
            "backend": str(pub_info.get("backend", storage_cfg.backend)),
            "as_of": as_of.isoformat(),
            "full_rebuild": bundle.diff.full_rebuild,
            "benchmarks_full_rebuild": bundle.diff.benchmarks_full_rebuild,
            "dirty_issuers_count": len(bundle.diff.dirty_issuers),
            "published_tables_count": len(tables),
            "ratios_rows": bundle.financial_ratios.height,
            "benchmarks_rows": bundle.industry_benchmarks.height,
            "red_flags_rows": bundle.accounting_red_flags.height,
            "ml_scores_rows": bundle.ml_scores.height,
            "quality_summary_rows": updated_quality_summary.height,
            "quarantined_rows": bundle.audit_quarantine.height,
            "critical_issuer_ratio": round(bundle.critical_issuer_ratio, 6),
        }
    )


@dg.op(
    name="run_lakehouse_maintenance",
    description=(
        "Acquire pipeline.lock before compacting/expiring DuckLake snapshots "
        "(or Parquet GC) and collecting aged curated run directories."
    ),
)
def run_lakehouse_maintenance(
    context: dg.OpExecutionContext,
    paths: FinIntelPathsResource,
    state: StateStoreResource,
    storage: StorageResource,
) -> dict[str, object]:
    """Run synchronized storage compaction/expiration and curated garbage collection."""
    state_store = state.initialize()
    report = storage.maintain(state_store=state_store, paths=paths)
    removed = report.get("removed_curated_dirs")
    removed_count = len(removed) if isinstance(removed, list) else 0
    context.log.info(
        "Lakehouse maintenance completed (backend=%s, removed_curated=%d)",
        report.get("backend"),
        removed_count,
    )
    return report


@dg.job(
    name="lakehouse_maintenance_job",
    description="Weekly DuckLake maintenance + curated GC synchronized via pipeline.lock.",
)
def lakehouse_maintenance_job() -> None:
    run_lakehouse_maintenance()


lakehouse_maintenance_schedule = dg.ScheduleDefinition(
    name="lakehouse_maintenance_schedule",
    job=lakehouse_maintenance_job,
    cron_schedule="0 3 * * 0",
    execution_timezone="Asia/Ho_Chi_Minh",
    description="Weekly Sunday 03:00 Asia/Ho_Chi_Minh lakehouse maintenance and curated GC.",
)
