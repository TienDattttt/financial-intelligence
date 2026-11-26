"""Dagster Definitions entrypoint for FinIntel V2 local single-node orchestration.

Verified Sources:
    - Dagster Official Documentation — Code Locations, Sensors & Schedules:
      https://docs.dagster.io/guides/build/projects/
      https://docs.dagster.io/guides/automate/sensors/
    - Runtime API verification in project ``.venv`` (Python 3.11.9, ``dagster==1.13.30``):
      - ``dg.Definitions(assets=..., asset_checks=..., jobs=..., schedules=..., sensors=..., resources=...)``
      - ``dg.Definitions.validate_loadable(defs)`` is a staticmethod accepting ``defs``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

import dagster as dg
import polars as pl

from finintel.exceptions import RateLimitOrCircuitOpenError, TransientSourceError
from finintel.ingestion.curated_pdf_service import sync_curated_pdfs_from_archive
from finintel.ingestion.market_prices_collector import (
    PoliteMarketPriceHttpClient,
    sync_incremental_market_prices,
)
from finintel.ingestion.regulatory_events_collector import (
    PoliteHostHttpClient,
    build_regulatory_events_table,
    collect_ticker_raw_candidates,
)
from finintel.orchestration.assets import (
    _COOLDOWN_PROVIDER_KEYS,
    _INFLIGHT_RUN_STATUSES,
    MAX_PUBLISH_RETRY_ATTEMPTS,
    VN_TZ,
    FhscIngestConfig,
    PublishMartsConfig,
    _resolve_max_partitions_per_run,
    curated_pdf_rag_asset,
    curated_pdf_rag_job,
    curated_pdf_rag_schedule,
    downstream_publish_job,
    downstream_publish_sensor,
    ensure_pipeline_write_pool,
    fhsc_ingestion_job,
    is_tt96_peak_window,
    lakehouse_maintenance_job,
    lakehouse_maintenance_schedule,
    market_prices_eod_asset,
    market_prices_eod_job,
    market_prices_eod_schedule,
    normalized_financial_facts,
    published_financial_marts,
    quarterly_ml_retrain_job,
    quarterly_ml_retrain_schedule,
    raw_fhsc_filings,
    raw_public_disclosures,
    regulatory_events_ledger_asset,
    regulatory_events_ledger_job,
    regulatory_events_ledger_schedule,
    seasonal_ingestion_schedule,
    select_seasonal_ingestion_batch,
)
from finintel.orchestration.checks import ALL_ASSET_CHECKS
from finintel.orchestration.partitions import CORPORATE_TICKERS
from finintel.orchestration.resources import (
    DisclosureFetcherResource,
    FinIntelPathsResource,
    StateStoreResource,
    StorageResource,
)
from finintel.pipeline.state_store import StateStore
from finintel.ports.market_prices import MARKET_PRICES_SCHEMA, validate_market_prices
from finintel.ports.regulatory import (
    REGULATORY_EVENTS_SCHEMA,
    validate_regulatory_events,
)
from finintel.storage.snapshot_publisher import pipeline_write_lock

MAX_FEED_RUN_REQUESTS_PER_TICK = 5


def _active_cooldown_reason(
    state_store: StateStore, *, now_utc: datetime, label: str
) -> dg.SkipReason | None:
    if not state_store.path.is_file():
        return None
    for provider_key in _COOLDOWN_PROVIDER_KEYS:
        cooldown_until = state_store.active_disclosure_cooldown_until(
            provider_key, now=now_utc
        )
        if cooldown_until is not None:
            return dg.SkipReason(
                f"Skipping {label}: SQLite cooldown active for "
                f"{provider_key!r} until {cooldown_until.isoformat()}."
            )
    return None


def _eval_seasonal_ingestion_schedule(
    context: dg.ScheduleEvaluationContext,
    paths: FinIntelPathsResource,
    state: StateStoreResource,
) -> dg.SkipReason | list[dg.RunRequest]:
    ensure_pipeline_write_pool(context)
    scheduled = context.scheduled_execution_time
    vn_dt = (
        scheduled.replace(tzinfo=VN_TZ)
        if scheduled.utcoffset() is None
        else scheduled.astimezone(VN_TZ)
    )
    vn_date = vn_dt.date()
    now_utc = vn_dt.astimezone(UTC)

    state_store = state.get_store()
    cooldown_skip = _active_cooldown_reason(
        state_store, now_utc=now_utc, label="seasonal ingestion"
    )
    if cooldown_skip is not None:
        return cooldown_skip

    in_peak, window_name = is_tt96_peak_window(vn_date)
    if not in_peak and vn_dt.weekday() != 6:
        return dg.SkipReason(
            f"Outside TT96 disclosure peak window ({vn_date.isoformat()}); "
            "off-season sweeps run Sundays only."
        )

    max_per_run = _resolve_max_partitions_per_run(paths.load_settings_dict())
    batch_tickers = select_seasonal_ingestion_batch(
        context.instance,
        target_date=vn_date,
        max_partitions_per_run=max_per_run,
        state_store=state_store,
    )
    if not batch_tickers:
        return dg.SkipReason(
            "No corporate_tickers dynamic partitions registered; run "
            "sync_corporate_ticker_partitions() first."
        )
    if state_store.path.is_file():
        state_store.record_ticker_sweep_batch(
            batch_tickers, swept_at=now_utc.isoformat()
        )
    slot_stamp = vn_dt.strftime("%Y-%m-%dT%H:%M")
    return [
        dg.RunRequest(
            run_key=f"seasonal:{slot_stamp}:{window_name}:{ticker}",
            partition_key=ticker,
            run_config=dg.RunConfig(
                ops={"raw_fhsc_filings": FhscIngestConfig(mode="sync")}
            ),
            tags={
                "finintel/trigger": "seasonal_ingestion_schedule",
                "finintel/tt96_window": window_name,
            },
        )
        for ticker in batch_tickers
    ]


def _parse_publish_sensor_cursor(
    raw_cursor: str | None,
) -> tuple[int | None, str | None, int]:
    if raw_cursor is None or not raw_cursor.strip():
        return None, None, 0
    txt = raw_cursor.strip()
    if txt.startswith("{"):
        try:
            obj = json.loads(txt)
            if isinstance(obj, dict):
                raw_after = obj.get("after")
                after = int(raw_after) if isinstance(raw_after, int) else None
                raw_key = obj.get("upstream_key")
                key = str(raw_key) if isinstance(raw_key, str) and raw_key else None
                raw_att = obj.get("attempts")
                att = int(raw_att) if isinstance(raw_att, int) else 0
                return after, key, max(0, att)
        except (ValueError, TypeError):
            return None, None, 0
    try:
        return int(txt), txt, 1
    except ValueError:
        return None, None, 0


def _eval_downstream_publish_sensor(
    context: dg.SensorEvaluationContext,
) -> dg.SkipReason | dg.RunRequest:
    ensure_pipeline_write_pool(context)
    after_cursor, prev_upstream_key, prev_attempts = _parse_publish_sensor_cursor(
        context.cursor
    )
    records: list[dg.EventLogRecord] = []
    for asset_def in (raw_public_disclosures, raw_fhsc_filings):
        res = context.instance.fetch_materializations(
            dg.AssetRecordsFilter(
                asset_key=asset_def.key, after_storage_id=after_cursor
            ),
            limit=100,
        )
        records.extend(res.records)

    if context.instance.get_runs(
        filters=dg.RunsFilter(
            job_name="fhsc_ingestion_job", statuses=list(_INFLIGHT_RUN_STATUSES)
        ),
        limit=1,
    ):
        return dg.SkipReason(
            "Deferring downstream publish while fhsc_ingestion_job run is in flight."
        )
    if context.instance.get_runs(
        filters=dg.RunsFilter(
            job_name="downstream_publish_job", statuses=list(_INFLIGHT_RUN_STATUSES)
        ),
        limit=1,
    ):
        return dg.SkipReason(
            "Deferring downstream publish while downstream_publish_job is already running."
        )

    if records:
        max_storage_id = max(int(rec.storage_id) for rec in records)
        upstream_key = str(max_storage_id)
        tickers = sorted(
            {
                str(rec.partition_key)
                for rec in records
                if getattr(rec, "partition_key", None)
            }
        )
        req = dg.RunRequest(
            run_key=f"downstream-publish:{upstream_key}",
            run_config=dg.RunConfig(
                ops={
                    "published_financial_marts": PublishMartsConfig(
                        ml_mode="incremental_score"
                    )
                }
            ),
            tags={
                "finintel/trigger": "downstream_publish_sensor",
                "finintel/source_event_cursor": upstream_key,
                "finintel/materialized_tickers": ",".join(tickers[:10]),
            },
        )
        context.update_cursor(
            json.dumps(
                {"after": max_storage_id, "upstream_key": upstream_key, "attempts": 1},
                sort_keys=True,
            )
        )
        return req

    if prev_upstream_key is not None and prev_attempts >= 1:
        prior_runs = context.instance.get_runs(
            filters=dg.RunsFilter(
                job_name="downstream_publish_job",
                tags={"finintel/source_event_cursor": prev_upstream_key},
            ),
            limit=10,
        )
        failed_statuses = {dg.DagsterRunStatus.FAILURE, dg.DagsterRunStatus.CANCELED}
        if (
            prior_runs
            and len(prior_runs) >= prev_attempts
            and all(run.status in failed_statuses for run in prior_runs)
        ):
            if prev_attempts >= MAX_PUBLISH_RETRY_ATTEMPTS:
                return dg.SkipReason(
                    f"Circuit breaker open: downstream_publish_job failed "
                    f"{prev_attempts}/{MAX_PUBLISH_RETRY_ATTEMPTS} attempts for "
                    f"upstream_key={prev_upstream_key!r}."
                )
            next_attempt = prev_attempts + 1
            retry_req = dg.RunRequest(
                run_key=f"downstream-publish:{prev_upstream_key}:retry-{next_attempt}",
                run_config=dg.RunConfig(
                    ops={
                        "published_financial_marts": PublishMartsConfig(
                            ml_mode="incremental_score"
                        )
                    }
                ),
                tags={
                    "finintel/trigger": "downstream_publish_sensor",
                    "finintel/source_event_cursor": prev_upstream_key,
                    "finintel/retry_attempt": str(next_attempt),
                },
            )
            context.update_cursor(
                json.dumps(
                    {
                        "after": after_cursor,
                        "upstream_key": prev_upstream_key,
                        "attempts": next_attempt,
                    },
                    sort_keys=True,
                )
            )
            return retry_req

    return dg.SkipReason("No new raw ingestion materializations since last publish.")


def _normalize_retrain_reg_path(
    raw_value: object, paths: FinIntelPathsResource, target_mode: str
) -> str | None:
    if isinstance(raw_value, str) and raw_value.strip():
        text = raw_value.strip()
        cand = Path(text)
        if (
            cand.is_absolute()
            or PurePosixPath(text).is_absolute()
            or PureWindowsPath(text).is_absolute()
        ):
            resolved = cand.resolve()
            for base in (
                paths.resolved_data_root(),
                paths.resolved_project_root(),
                paths.resolved_config_dir(),
            ):
                if resolved.is_relative_to(base):
                    return resolved.relative_to(base).as_posix()
        return text.replace("\\", "/")
    if target_mode == "REGULATORY_DISTRESS":
        return "regulatory_ledger/regulatory_events.parquet"
    return None


def _eval_quarterly_ml_retrain_schedule(
    context: dg.ScheduleEvaluationContext,
    paths: FinIntelPathsResource,
) -> dg.RunRequest:
    ensure_pipeline_write_pool(context)
    scheduled = context.scheduled_execution_time
    vn_dt = (
        scheduled.replace(tzinfo=VN_TZ)
        if scheduled.utcoffset() is None
        else scheduled.astimezone(VN_TZ)
    )
    vn_date = vn_dt.date()

    settings = paths.load_settings_dict()
    orch_cfg = settings.get("orchestration")
    raw_retrain = orch_cfg.get("ml_retrain") if isinstance(orch_cfg, Mapping) else None
    retrain_cfg: Mapping[str, object] = (
        raw_retrain if isinstance(raw_retrain, Mapping) else {}
    )
    raw_val_start = retrain_cfg.get("validation_start")
    val_start: str | None = (
        str(raw_val_start)
        if isinstance(raw_val_start, str) and raw_val_start.strip()
        else None
    )
    test_start = str(
        retrain_cfg.get("test_start")
        or date(max(2020, vn_date.year - 1), 1, 1).isoformat()
    )
    target_mode = str(retrain_cfg.get("target_mode") or "REGULATORY_DISTRESS")
    reg_path = _normalize_retrain_reg_path(
        retrain_cfg.get("regulatory_events_path"), paths, target_mode
    )

    return dg.RunRequest(
        run_key=f"quarterly-ml-retrain:{vn_date.isoformat()}",
        run_config=dg.RunConfig(
            ops={
                "published_financial_marts": PublishMartsConfig(
                    as_of=vn_date.isoformat(),
                    ml_enabled=True,
                    ml_mode="retrain",
                    ml_approved_target=True,
                    ml_validation_start=val_start,
                    ml_test_start=test_start,
                    target_mode=target_mode,
                    regulatory_events_path=reg_path,
                )
            }
        ),
        tags={
            "finintel/trigger": "quarterly_ml_retrain_schedule",
            "finintel/ml_cadence": "retrain",
        },
    )


def _parse_bctc_feed_entries(raw_bytes: bytes) -> list[tuple[str, date]]:
    try:
        decoded = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return []
    items_obj: object = decoded
    if isinstance(decoded, Mapping):
        items_obj = decoded.get("data") or decoded.get("items") or []
    if not isinstance(items_obj, list):
        return []
    parsed: list[tuple[str, date]] = []
    seen: set[tuple[str, date]] = set()
    for entry in items_obj:
        if not isinstance(entry, Mapping):
            continue
        raw_ticker = (
            entry.get("ticker") or entry.get("symbol") or entry.get("organCode") or ""
        )
        raw_date = (
            entry.get("publicDate")
            or entry.get("published_at")
            or entry.get("pub_date")
            or ""
        )
        if not isinstance(raw_ticker, str) or not isinstance(raw_date, str):
            continue
        ticker = raw_ticker.strip().upper()
        date_prefix = raw_date.strip()[:10]
        if not ticker or len(date_prefix) != 10:
            continue
        try:
            pub_date = date.fromisoformat(date_prefix)
        except ValueError:
            continue
        pair = (ticker, pub_date)
        if pair not in seen:
            seen.add(pair)
            parsed.append(pair)
    return parsed


@dg.sensor(
    name="bctc_disclosure_feed_sensor",
    job=fhsc_ingestion_job,
    minimum_interval_seconds=900,
    default_status=dg.DefaultSensorStatus.RUNNING,
    description=(
        "15-minute event-driven BCTC disclosure feed sensor filtering registered "
        "CORPORATE_TICKERS and emitting <=5 deterministic feed:{ticker}:{pub_date} runs."
    ),
)
def bctc_disclosure_feed_sensor(
    context: dg.SensorEvaluationContext,
    paths: FinIntelPathsResource,
    state: StateStoreResource,
    disclosure_fetcher: DisclosureFetcherResource,
) -> dg.SkipReason | list[dg.RunRequest]:
    """Poll recent BCTC disclosure feed and emit <=5 unarchived corporate RunRequests."""
    ensure_pipeline_write_pool(context)
    now_utc = datetime.now(UTC)
    state_store = state.get_store()
    cooldown_skip = _active_cooldown_reason(
        state_store, now_utc=now_utc, label="BCTC disclosure feed sensor"
    )
    if cooldown_skip is not None:
        return cooldown_skip

    corporate_set = set(
        context.instance.get_dynamic_partitions(
            CORPORATE_TICKERS.name or "corporate_tickers"
        )
    )
    if not corporate_set:
        return dg.SkipReason("No corporate_tickers dynamic partitions registered.")

    config_dir = paths.resolved_config_dir()
    raw_feed = disclosure_fetcher.read_local_confined(
        "bctc_feed.json", base_dir=config_dir
    )
    if raw_feed is None:
        if not disclosure_fetcher.effective_fetch_online():
            return dg.SkipReason(
                "BCTC disclosure feed sensor skipped: offline mode without local bctc_feed.json."
            )
        settings = paths.load_settings_dict()
        orch_cfg = settings.get("orchestration")
        feed_url = (
            orch_cfg.get("bctc_feed_url") if isinstance(orch_cfg, Mapping) else None
        )
        if not isinstance(feed_url, str) or not feed_url.strip():
            return dg.SkipReason(
                "No bctc_feed_url configured; relying on 30-minute seasonal watermark sweep."
            )
        try:
            raw_feed = disclosure_fetcher.fetch_bytes(
                feed_url.strip(), state_store=state_store
            )
        except (RateLimitOrCircuitOpenError, TransientSourceError, OSError) as exc:
            return dg.SkipReason(f"BCTC feed fetch deferred ({type(exc).__name__}).")

    candidates: list[tuple[str, date]] = []
    for ticker, pub_date in _parse_bctc_feed_entries(raw_feed):
        if ticker not in corporate_set:
            continue
        if state_store.has_archived_filing_on_or_after(ticker, pub_date):
            continue
        candidates.append((ticker, pub_date))
        if len(candidates) >= MAX_FEED_RUN_REQUESTS_PER_TICK:
            break

    if not candidates:
        return dg.SkipReason("No unarchived corporate BCTC feed disclosures detected.")

    return [
        dg.RunRequest(
            run_key=f"feed:{ticker}:{pub_date.isoformat()}",
            partition_key=ticker,
            run_config=dg.RunConfig(
                ops={"raw_fhsc_filings": FhscIngestConfig(mode="sync")}
            ),
            tags={
                "finintel/trigger": "bctc_disclosure_feed_sensor",
                "finintel/pub_date": pub_date.isoformat(),
            },
        )
        for ticker, pub_date in candidates
    ]


def _load_corporate_issuer_maps(
    paths: FinIntelPathsResource,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    issuers_path = paths.resolved_issuers_path()
    if not issuers_path.is_file():
        return [], {}, {}
    df = pl.read_parquet(issuers_path)
    if "accounting_regime" in df.columns:
        df = df.filter(pl.col("accounting_regime") == "CORPORATE")
    tickers: list[str] = []
    issuer_map: dict[str, str] = {}
    exchange_map: dict[str, str] = {}
    for row in df.select("issuer_id", "ticker", "exchange").to_dicts():
        tk = str(row["ticker"]).strip().upper()
        iid = str(row["issuer_id"]).strip()
        ex = str(row["exchange"]).strip().upper()
        if tk and iid:
            tickers.append(tk)
            issuer_map[tk] = iid
            exchange_map[tk] = ex
    return sorted(set(tickers)), issuer_map, exchange_map


def _write_parquet_atomic(df: pl.DataFrame, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".parquet.tmp")
    df.write_parquet(tmp)
    tmp.replace(target)


def _exec_market_prices_eod_asset(
    context: dg.AssetExecutionContext,
    paths: FinIntelPathsResource,
    state: StateStoreResource,
    storage: StorageResource,
    disclosure_fetcher: DisclosureFetcherResource,
) -> dg.MaterializeResult[None]:
    _ = context
    data_root = paths.resolved_data_root()
    out_path = data_root / "market_prices" / "market_prices.parquet"
    existing = (
        validate_market_prices(pl.read_parquet(out_path))
        if out_path.is_file()
        else pl.DataFrame(schema=MARKET_PRICES_SCHEMA)
    )
    if not disclosure_fetcher.effective_fetch_online():
        return dg.MaterializeResult(
            metadata={"status": "SKIPPED_OFFLINE", "rows": existing.height}
        )
    tickers, issuer_map, exchange_map = _load_corporate_issuer_maps(paths)
    merged, refetched = sync_incremental_market_prices(
        existing,
        tickers=tickers,
        issuer_map=issuer_map,
        exchange_map=exchange_map,
        raw_dir=data_root / "market_prices" / "raw",
        client=PoliteMarketPriceHttpClient(),
    )
    with pipeline_write_lock(
        state.pipeline_lock_path(), timeout=storage.resolve_lock_timeout()
    ):
        _write_parquet_atomic(merged, out_path)
    return dg.MaterializeResult(
        metadata={
            "status": "SYNCED",
            "rows": merged.height,
            "split_refetched_count": len(refetched),
        }
    )


def _exec_regulatory_events_ledger_asset(
    context: dg.AssetExecutionContext,
    paths: FinIntelPathsResource,
    state: StateStoreResource,
    storage: StorageResource,
    disclosure_fetcher: DisclosureFetcherResource,
) -> dg.MaterializeResult[None]:
    _ = context
    data_root = paths.resolved_data_root()
    ledger_dir = data_root / "regulatory_ledger"
    out_path = ledger_dir / "regulatory_events.parquet"
    online = disclosure_fetcher.effective_fetch_online()
    if not online and not (ledger_dir / "raw").is_dir():
        existing_rows = (
            validate_regulatory_events(pl.read_parquet(out_path)).height
            if out_path.is_file()
            else 0
        )
        return dg.MaterializeResult(
            metadata={"status": "SKIPPED_OFFLINE", "rows": existing_rows}
        )
    tickers, issuer_map, exchange_map = _load_corporate_issuer_maps(paths)
    client = PoliteHostHttpClient()
    all_cands = []
    ex_by_issuer: dict[str, str] = {}
    for tk in tickers:
        iid = issuer_map[tk]
        ex = exchange_map.get(tk, "UNKNOWN")
        ex_by_issuer[iid] = ex
        cands, _ = collect_ticker_raw_candidates(
            ticker=tk,
            issuer_id=iid,
            current_exchange=ex,
            raw_dir=ledger_dir / "raw",
            client=client,
            use_cache=True,
            fetch_online=online,
            refresh_head_pages=2 if online else 0,
        )
        all_cands.extend(cands)
    reg_df = (
        build_regulatory_events_table(all_cands, exchange_by_issuer=ex_by_issuer)
        if all_cands
        else validate_regulatory_events(pl.DataFrame(schema=REGULATORY_EVENTS_SCHEMA))
    )
    with pipeline_write_lock(
        state.pipeline_lock_path(), timeout=storage.resolve_lock_timeout()
    ):
        _write_parquet_atomic(reg_df, out_path)
    return dg.MaterializeResult(metadata={"status": "SYNCED", "rows": reg_df.height})


def _exec_curated_pdf_rag_asset(
    context: dg.AssetExecutionContext,
    paths: FinIntelPathsResource,
    state: StateStoreResource,
    storage: StorageResource,
    disclosure_fetcher: DisclosureFetcherResource,
) -> dg.MaterializeResult[None]:
    _ = context
    data_root = paths.resolved_data_root()
    out_root = data_root / "curated_pdfs"
    summary = sync_curated_pdfs_from_archive(
        raw_root=paths.resolved_raw_root(),
        output_root=out_root,
        fetch_online=disclosure_fetcher.effective_fetch_online(),
        manifest_lock_path=state.pipeline_lock_path(),
        lock_timeout=storage.resolve_lock_timeout(),
        state_store_path=state.get_store().path,
    )
    return dg.MaterializeResult(
        metadata={
            "scanned_receipts": summary.scanned_receipts,
            "discovered_candidates": summary.discovered_candidates,
            "downloaded_count": summary.downloaded_count,
            "total_manifest_records": summary.total_manifest_records,
        }
    )


ALL_ASSETS = [
    raw_fhsc_filings,
    raw_public_disclosures,
    normalized_financial_facts,
    published_financial_marts,
]

EXTENSION_PORT_ASSETS = [
    market_prices_eod_asset,
    regulatory_events_ledger_asset,
    curated_pdf_rag_asset,
]


def build_default_resources() -> dict[str, object]:
    """Build default Dagster resources honoring ``FININTEL_DATA_ROOT`` if configured."""
    env_data_root = os.getenv("FININTEL_DATA_ROOT", "").strip()
    if env_data_root:
        data_root_path = Path(env_data_root)
        paths_res = FinIntelPathsResource(data_root=str(data_root_path))
        state_res = StateStoreResource(
            state_path=str(data_root_path / "metadata" / "state.sqlite")
        )
    else:
        paths_res = FinIntelPathsResource()
        state_res = StateStoreResource()
    return {
        "paths": paths_res,
        "state": state_res,
        "storage": StorageResource(),
        "disclosure_fetcher": DisclosureFetcherResource(),
    }


DEFAULT_RESOURCES: dict[str, object] = build_default_resources()

defs = dg.Definitions(
    assets=[*ALL_ASSETS, *EXTENSION_PORT_ASSETS],
    asset_checks=ALL_ASSET_CHECKS,
    jobs=[
        fhsc_ingestion_job,
        downstream_publish_job,
        quarterly_ml_retrain_job,
        lakehouse_maintenance_job,
        market_prices_eod_job,
        regulatory_events_ledger_job,
        curated_pdf_rag_job,
    ],
    schedules=[
        seasonal_ingestion_schedule,
        quarterly_ml_retrain_schedule,
        lakehouse_maintenance_schedule,
        market_prices_eod_schedule,
        regulatory_events_ledger_schedule,
        curated_pdf_rag_schedule,
    ],
    sensors=[downstream_publish_sensor, bctc_disclosure_feed_sensor],
    resources=DEFAULT_RESOURCES,
)
