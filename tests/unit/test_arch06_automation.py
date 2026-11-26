"""Unit tests for ARCH-06: Dagster automation, TT96 seasonal scheduling, DBSP as_of decoupling, and ML cadence separation."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml  # type: ignore[import-untyped]

pytest.importorskip("dagster")

import dagster as dg  # noqa: E402

from finintel.exceptions import ContractViolationError  # noqa: E402
from finintel.orchestration.assets import (  # noqa: E402
    PublishMartsConfig,
    _resolve_ml_config,
    downstream_publish_job,
    downstream_publish_sensor,
    fhsc_ingestion_job,
    is_tt96_peak_window,
    published_financial_marts,
    quarterly_ml_retrain_job,
    quarterly_ml_retrain_schedule,
    raw_fhsc_filings,
    seasonal_ingestion_schedule,
)
from finintel.orchestration.checks import ALL_ASSET_CHECKS  # noqa: E402
from finintel.orchestration.definitions import build_default_resources, defs  # noqa: E402
from finintel.orchestration.partitions import (  # noqa: E402
    CORPORATE_TICKERS_PARTITION_NAME,
    load_universe_tickers_yaml,
    sync_corporate_ticker_partitions,
)
from finintel.orchestration.resources import (  # noqa: E402
    FinIntelPathsResource,
    StateStoreResource,
    StorageResource,
)
from finintel.pipeline.cli import load_settings
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import (
    ML_SCORES_KEY,
    ML_SCORES_SCHEMA,
    ML_SCORES_TABLE,
    canonicalize_financial_facts,
)
from finintel.transform.audits import run_blocking_publish_audits
from finintel.transform.incremental import (
    ML_ARTIFACT_FILENAME,
    TRANSFORM_STATE_FILENAME,
    MLTransformConfig,
    load_ml_artifact_bundle,
    run_incremental_analytics_transform,
    write_transform_state,
)
from tests.unit.test_option_b_dagster import (
    _make_fhsc_responder,
    _setup_workspace,
    fhsc_sdk,  # noqa: F401
)
from tests.unit.test_option_b_incremental_transform import (
    _assert_frames_close,
    _build_period_rows,
    _make_corporate_issuers,
    _make_universe_facts,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_universe_tickers_yaml_and_sync_corporate_ticker_partitions(
    tmp_path: Path,
) -> None:
    """config/universe_tickers.yaml holds valid CORPORATE tickers and syncs cleanly with fallback."""
    yaml_path = REPO_ROOT / "config" / "universe_tickers.yaml"
    assert yaml_path.is_file()
    tickers = load_universe_tickers_yaml(yaml_path)
    assert 600 <= len(tickers) <= 800
    assert "FPT" in tickers
    assert "HPG" in tickers
    assert "VNM" in tickers
    assert tickers == tuple(sorted(set(tickers)))

    bad_yaml = tmp_path / "bad_regime.yaml"
    bad_yaml.write_text(
        yaml.safe_dump({"accounting_regime": "BANK", "tickers": ["VCB"]}),
        encoding="utf-8",
    )
    with pytest.raises(ContractViolationError, match="CORPORATE"):
        load_universe_tickers_yaml(bad_yaml)

    sample_yaml = tmp_path / "universe.yaml"
    sample_yaml.write_text(
        yaml.safe_dump({"accounting_regime": "CORPORATE", "tickers": ["hpg", "FPT"]}),
        encoding="utf-8",
    )
    with dg.DagsterInstance.ephemeral() as instance:
        synced = sync_corporate_ticker_partitions(
            instance,
            tmp_path / "missing_issuers.parquet",
            universe_tickers_path=sample_yaml,
        )
        assert synced == ("FPT", "HPG")
        assert set(
            instance.get_dynamic_partitions(CORPORATE_TICKERS_PARTITION_NAME)
        ) == {"FPT", "HPG"}

        # Direct YAML path + pruning works identically.
        sample_yaml.write_text(
            yaml.safe_dump({"accounting_regime": "CORPORATE", "tickers": ["VNM"]}),
            encoding="utf-8",
        )
        synced2 = sync_corporate_ticker_partitions(
            instance, sample_yaml, prune_removed=True
        )
        assert synced2 == ("VNM",)
        assert list(
            instance.get_dynamic_partitions(CORPORATE_TICKERS_PARTITION_NAME)
        ) == ["VNM"]


def test_config_settings_yaml_orchestration_and_finintel_data_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """config/settings.yaml stays compatible with load_settings and FININTEL_DATA_ROOT aligns resources."""
    settings_path = REPO_ROOT / "config" / "settings.yaml"
    parsed = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    assert parsed["orchestration"]["universe_tickers_path"] == "universe_tickers.yaml"
    assert 3 <= int(parsed["orchestration"]["max_partitions_per_run"]) <= 5

    cli_settings = load_settings(settings_path)
    assert cli_settings.min_free_bytes == 104_857_600

    monkeypatch.setenv("FININTEL_DATA_ROOT", "/tmp/custom_finintel_data")
    resources = build_default_resources()
    paths_res = resources["paths"]
    state_res = resources["state"]
    assert isinstance(paths_res, FinIntelPathsResource)
    assert isinstance(state_res, StateStoreResource)
    assert Path(paths_res.data_root) == Path("/tmp/custom_finintel_data")
    assert Path(state_res.state_path) == Path(
        "/tmp/custom_finintel_data/metadata/state.sqlite"
    )


def test_as_of_decoupled_from_transform_config_hash_and_pit_visibility(
    tmp_path: Path,
) -> None:
    """Daily as_of advances do not force full_rebuild; newly visible facts dirty only affected issuers."""
    state_path = tmp_path / TRANSFORM_STATE_FILENAME
    issuers = _make_corporate_issuers(5)
    issuer_ids = issuers["issuer_id"].to_list()
    base_facts = _make_universe_facts(issuer_ids)

    # Inject a future Q1 2025 filing for CORP_01 becoming visible on 2025-06-15.
    q1_2025_rows = _build_period_rows("CORP_01", year=2025, quarter=1, scale=1.2)
    for row in q1_2025_rows:
        row["published_at"] = date(2025, 6, 15)
        row["effective_available_at"] = date(2025, 6, 15)
    facts = canonicalize_financial_facts(
        pl.concat([base_facts, pl.DataFrame(q1_2025_rows, schema=base_facts.schema)])
    )

    # Day 1: 2025-06-01 (2025 Q1 not visible yet).
    b1 = run_incremental_analytics_transform(
        facts, issuers, as_of=date(2025, 6, 1), run_id="day1", state_path=state_path
    )
    write_transform_state(state_path, b1.next_transform_state, snapshot_id="snap_d1")

    # Day 2: 2025-06-02 (still no new visible facts -> 0 dirty issuers, full_rebuild=False).
    b2 = run_incremental_analytics_transform(
        facts,
        issuers,
        as_of=date(2025, 6, 2),
        run_id="day2",
        previous_ratios=b1.financial_ratios,
        previous_benchmarks=b1.industry_benchmarks,
        previous_red_flags=b1.accounting_red_flags,
        previous_snapshot_id="snap_d1",
        state_path=state_path,
    )
    assert b2.diff.transform_config_changed is False
    assert b2.diff.full_rebuild is False
    assert b2.diff.dirty_issuers == ()
    assert b2.diff.dirty_slices == ()

    # Day 15: 2025-06-15 -> CORP_01's Q1 2025 becomes visible -> ONLY CORP_01 is dirty!
    b15 = run_incremental_analytics_transform(
        facts,
        issuers,
        as_of=date(2025, 6, 15),
        run_id="day15",
        previous_ratios=b1.financial_ratios,
        previous_benchmarks=b1.industry_benchmarks,
        previous_red_flags=b1.accounting_red_flags,
        previous_snapshot_id="snap_d1",
        state_path=state_path,
    )
    assert b15.diff.transform_config_changed is False
    assert b15.diff.full_rebuild is False
    assert b15.diff.dirty_issuers == ("CORP_01",)

    b15_full = run_incremental_analytics_transform(
        facts, issuers, as_of=date(2025, 6, 15), run_id="day15-full"
    )
    _assert_frames_close(
        b15.financial_ratios, b15_full.financial_ratios, sort_keys=ML_SCORES_KEY[:4]
    )


def test_ml_cadence_decoupled_retrain_vs_incremental_scoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrain persists artifact; subsequent incremental_score scores dirty_issuers without retraining or 0-row wipe."""
    as_of_train = date(2025, 3, 1)
    state_path = tmp_path / "data" / "metadata" / TRANSFORM_STATE_FILENAME
    model_dir = tmp_path / "data" / "metadata" / "ml_models"
    issuers = _make_corporate_issuers(12)
    issuer_ids = issuers["issuer_id"].to_list()

    distress_schedule = {
        "CORP_01": 2021,
        "CORP_02": 2021,
        "CORP_03": 2021,
        "CORP_04": 2022,
        "CORP_05": 2022,
        "CORP_06": 2024,
        "CORP_07": 2024,
    }
    rows: list[dict[str, object]] = []
    for idx, iid in enumerate(issuer_ids, start=1):
        d_year = distress_schedule.get(iid)
        for year in range(2018, 2025):
            for quarter in (1, 2, 3, 4):
                rows.extend(
                    _build_period_rows(
                        iid,
                        year=year,
                        quarter=quarter,
                        scale=1.0 + 0.03 * idx + 0.01 * quarter,
                        negative_equity=(d_year == year and quarter == 3),
                    )
                )
    facts_v1 = canonicalize_financial_facts(pl.DataFrame(rows))

    # 1. Quarterly retrain persists versioned ML artifact bundle.
    b_train = run_incremental_analytics_transform(
        facts_v1,
        issuers,
        as_of=as_of_train,
        run_id="ml-retrain-q1",
        state_path=state_path,
        ml_config=MLTransformConfig(
            enabled=True,
            mode="retrain",
            model_artifact_dir=model_dir,
            approved_target=True,
            require_exact_filing=True,
            validation_start=date(2021, 12, 1),
            test_start=date(2023, 3, 1),
        ),
        persist_state=True,
    )
    write_transform_state(state_path, b_train.next_transform_state, snapshot_id="s_ml1")
    assert b_train.ml_scores.height > 0
    assert (model_dir / ML_ARTIFACT_FILENAME).is_file()
    assert load_ml_artifact_bundle(model_dir) is not None

    # 2. Daily auto-sync (default PublishMartsConfig -> incremental_score) with 1 updated issuer (CORP_01).
    # Assert train_distress_model is NEVER invoked!
    def _forbid_retrain(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Incremental scoring must NOT call train_distress_model!")

    monkeypatch.setattr(
        "finintel.ml.supervised.train.train_distress_model", _forbid_retrain
    )

    q1_2025 = _build_period_rows("CORP_01", year=2025, quarter=1, scale=1.05)
    for r in q1_2025:
        r["published_at"] = date(2025, 4, 25)
        r["effective_available_at"] = date(2025, 4, 25)
    facts_v2 = canonicalize_financial_facts(
        pl.concat([facts_v1, pl.DataFrame(q1_2025, schema=facts_v1.schema)])
    )

    inc_ml_cfg = _resolve_ml_config(
        PublishMartsConfig(ml_enabled=False, ml_mode="incremental_score"),
        paths=FinIntelPathsResource(project_root=str(tmp_path)),
    )
    assert inc_ml_cfg.mode == "incremental_score"
    assert inc_ml_cfg.enabled is False

    b_inc = run_incremental_analytics_transform(
        facts_v2,
        issuers,
        as_of=date(2025, 5, 1),
        run_id="auto-sync-1",
        previous_ratios=b_train.financial_ratios,
        previous_benchmarks=b_train.industry_benchmarks,
        previous_red_flags=b_train.accounting_red_flags,
        previous_ml_scores=b_train.ml_scores,
        previous_snapshot_id="s_ml1",
        state_path=state_path,
        ml_config=inc_ml_cfg,
    )
    assert b_inc.diff.full_rebuild is False
    assert b_inc.diff.dirty_issuers == ("CORP_01",)
    assert b_inc.ml_scores.height == b_train.ml_scores.height + 1
    assert b_inc.ml_scores.schema == ML_SCORES_SCHEMA
    assert b_inc.ml_run_manifest is not None
    assert b_inc.ml_run_manifest["retrained"] is False
    assert b_inc.ml_run_manifest["scored_issuers"] == ["CORP_01"]
    run_blocking_publish_audits(b_inc, run_id="auto-sync-1")

    # Unchanged issuers retain exact prior ML rows.
    _assert_frames_close(
        b_inc.ml_scores.filter(pl.col("issuer_id") != "CORP_01"),
        b_train.ml_scores.filter(pl.col("issuer_id") != "CORP_01"),
        sort_keys=ML_SCORES_KEY,
    )
    # New Q1 2025 row for CORP_01 has non-null calibrated probability.
    new_row = b_inc.ml_scores.filter(
        (pl.col("issuer_id") == "CORP_01")
        & (pl.col("period_end_date") == date(2025, 3, 31))
    )
    assert new_row.height == 1
    assert new_row["calibrated_distress_prob"].item() is not None

    # 3. Checksum tamper on persisted artifact safely returns None and preserves previous_ml_scores.
    bundle_file = model_dir / ML_ARTIFACT_FILENAME
    bundle_file.write_bytes(b"corrupted-bytes")
    assert load_ml_artifact_bundle(model_dir) is None

    b_fallback = run_incremental_analytics_transform(
        facts_v2,
        issuers,
        as_of=date(2025, 5, 1),
        run_id="auto-sync-fallback",
        previous_ratios=b_train.financial_ratios,
        previous_benchmarks=b_train.industry_benchmarks,
        previous_red_flags=b_train.accounting_red_flags,
        previous_ml_scores=b_train.ml_scores,
        previous_snapshot_id="s_ml1",
        state_path=state_path,
        ml_config=inc_ml_cfg,
    )
    assert b_fallback.ml_scores.height > 0
    run_blocking_publish_audits(b_fallback, run_id="auto-sync-fallback")


def test_seasonal_ingestion_schedule_tt96_windows_cooldown_and_rotation(
    tmp_path: Path,
) -> None:
    """seasonal_ingestion_schedule enforces TT96 windows, SQLite cooldowns, and bounded rotating batches."""
    assert is_tt96_peak_window(date(2026, 1, 20)) == (True, "TT96_Q4_FILING_WINDOW")
    assert is_tt96_peak_window(date(2026, 3, 25)) == (
        True,
        "TT96_ANNUAL_AUDITED_WINDOW",
    )
    assert is_tt96_peak_window(date(2026, 4, 25)) == (True, "TT96_Q1_FILING_WINDOW")
    assert is_tt96_peak_window(date(2026, 8, 10)) == (True, "TT96_Q2_SEMIANNUAL_WINDOW")
    assert is_tt96_peak_window(date(2026, 10, 25)) == (True, "TT96_Q3_FILING_WINDOW")
    assert is_tt96_peak_window(date(2026, 6, 10)) == (False, "OFF_SEASON")

    paths_res, state_res, _ = _setup_workspace(tmp_path)
    state_store = state_res.initialize()

    dagster_home = tmp_path / "dagster_home"
    dagster_home.mkdir()
    repo_def = defs.get_repository_def()
    with dg.DagsterInstance.local_temp(str(dagster_home)) as instance:
        # Register 6 partitions so rotation with batch size 4 is observable.
        instance.add_dynamic_partitions(
            CORPORATE_TICKERS_PARTITION_NAME,
            ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"],
        )

        # 1. Off-season weekday (Wed 2026-06-10) -> SkipReason.
        ctx_off_weekday = dg.build_schedule_context(
            instance=instance,
            scheduled_execution_time=datetime(2026, 6, 10, 1, 0, tzinfo=timezone_vn()),
            resources={"paths": paths_res, "state": state_res},
            repository_def=repo_def,
        )
        res_off = seasonal_ingestion_schedule.evaluate_tick(ctx_off_weekday)
        assert res_off.run_requests is not None
        assert len(res_off.run_requests) == 0
        assert res_off.skip_message is not None
        assert "Outside TT96" in res_off.skip_message

        # 2. Off-season Sunday (Sun 2026-06-14) -> runs bounded batch of 4.
        ctx_off_sun = dg.build_schedule_context(
            instance=instance,
            scheduled_execution_time=datetime(2026, 6, 14, 1, 0, tzinfo=timezone_vn()),
            resources={"paths": paths_res, "state": state_res},
            repository_def=repo_def,
        )
        res_sun = seasonal_ingestion_schedule.evaluate_tick(ctx_off_sun)
        assert res_sun.run_requests is not None
        assert len(res_sun.run_requests) == 4
        assert [r.partition_key for r in res_sun.run_requests] == [
            "AAA",
            "BBB",
            "CCC",
            "DDD",
        ]

        # 3. Peak TT96 weekday (Tue 2026-01-20) -> runs bounded batch of 4 in sync mode.
        ctx_peak = dg.build_schedule_context(
            instance=instance,
            scheduled_execution_time=datetime(2026, 1, 20, 1, 0, tzinfo=timezone_vn()),
            resources={"paths": paths_res, "state": state_res},
            repository_def=repo_def,
        )
        res_peak = seasonal_ingestion_schedule.evaluate_tick(ctx_peak)
        assert res_peak.run_requests is not None
        assert len(res_peak.run_requests) == 4
        assert all(
            r.tags.get("finintel/tt96_window") == "TT96_Q4_FILING_WINDOW"
            for r in res_peak.run_requests
        )

        # 4. Active SQLite cooldown skips even during TT96 peak window.
        state_store.record_disclosure_failure(
            "fhsc",
            force_cooldown=True,
            cooldown_seconds=600.0,
            http_status=429,
            now=datetime(2026, 1, 20, 1, 0, tzinfo=timezone_vn()).astimezone(UTC),
        )
        res_cool = seasonal_ingestion_schedule.evaluate_tick(ctx_peak)
        assert res_cool.run_requests is not None
        assert len(res_cool.run_requests) == 0
        assert res_cool.skip_message is not None
        assert "cooldown active" in res_cool.skip_message.lower()

    # Quarterly ML retrain schedule produces valid RunRequest with ml_mode="retrain".
    q_ctx = dg.build_schedule_context(
        scheduled_execution_time=datetime(2026, 4, 1, 2, 0, tzinfo=timezone_vn()),
        resources={"paths": paths_res},
        repository_def=repo_def,
    )
    q_tick = quarterly_ml_retrain_schedule.evaluate_tick(q_ctx)
    assert q_tick.run_requests is not None
    assert len(q_tick.run_requests) == 1
    assert q_tick.run_requests[0].tags.get("finintel/ml_cadence") == "retrain"
    assert quarterly_ml_retrain_job.name == "quarterly_ml_retrain_job"
    assert fhsc_ingestion_job.name == "fhsc_ingestion_job"


def test_downstream_publish_sensor_triggers_and_defers_inflight(
    tmp_path: Path,
    fhsc_sdk: Any,  # noqa: F811
) -> None:
    """downstream_publish_sensor emits incremental publish RunRequest on new materializations and defers while busy."""
    fhsc_sdk(_make_fhsc_responder())
    paths_res, state_res, _ = _setup_workspace(tmp_path, backend="parquet")

    with dg.DagsterInstance.ephemeral() as instance:
        sync_corporate_ticker_partitions(instance, paths_res.resolved_issuers_path())

        # Before any ingestion -> SkipReason.
        ctx0 = dg.build_sensor_context(instance=instance)
        res0 = downstream_publish_sensor.evaluate_tick(ctx0)
        assert res0.run_requests is not None
        assert len(res0.run_requests) == 0

        # Materialize 1 partition -> records ASSET_MATERIALIZATION event.
        mat = dg.materialize(
            [raw_fhsc_filings],
            instance=instance,
            resources={"paths": paths_res, "state": state_res},
            run_config=dg.RunConfig(
                ops={"raw_fhsc_filings": {"config": {"mode": "backfill"}}}
            ),
            partition_key="FPT",
        )
        assert mat.success

        # If an ingestion run is in flight, sensor defers WITHOUT advancing cursor.
        inflight_run = instance.create_run_for_job(
            job_def=fhsc_ingestion_job.resolve(asset_graph=defs.resolve_asset_graph()),
            status=dg.DagsterRunStatus.STARTED,
        )
        assert inflight_run.run_id
        ctx_busy = dg.build_sensor_context(instance=instance, cursor=None)
        res_busy = downstream_publish_sensor.evaluate_tick(ctx_busy)
        assert res_busy.run_requests is not None
        assert len(res_busy.run_requests) == 0
        assert res_busy.cursor is None
        assert (
            res_busy.skip_message is not None and "in flight" in res_busy.skip_message
        )

        # Complete/cancel the simulated inflight run -> sensor emits RunRequest & advances cursor.
        instance.report_run_canceled(inflight_run)
        ctx_ready = dg.build_sensor_context(instance=instance, cursor=None)
        res_ready = downstream_publish_sensor.evaluate_tick(ctx_ready)
        assert res_ready.run_requests is not None
        assert len(res_ready.run_requests) == 1
        assert res_ready.cursor is not None
        req = res_ready.run_requests[0]
        assert (
            req.run_config["ops"]["published_financial_marts"]["config"]["ml_mode"]
            == "incremental_score"
        )
        assert downstream_publish_job.name == "downstream_publish_job"

        # Next tick with updated cursor sees no new events -> SkipReason.
        ctx_after = dg.build_sensor_context(instance=instance, cursor=res_ready.cursor)
        res_after = downstream_publish_sensor.evaluate_tick(ctx_after)
        assert res_after.run_requests is not None
        assert len(res_after.run_requests) == 0


def test_published_financial_marts_auto_sync_never_wipes_ml_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dagster published_financial_marts persists retrain artifacts and keeps/updates ml_scores on default auto-sync."""
    curated_dir = tmp_path / "data" / "curated"
    curated_dir.mkdir(parents=True, exist_ok=True)
    issuers = _make_corporate_issuers(12)
    issuers_path = curated_dir / "issuers.parquet"
    issuers.write_parquet(issuers_path)

    distress_schedule = {
        "CORP_01": 2021,
        "CORP_02": 2021,
        "CORP_03": 2021,
        "CORP_04": 2022,
        "CORP_05": 2022,
        "CORP_06": 2024,
        "CORP_07": 2024,
    }
    rows: list[dict[str, object]] = []
    for idx, iid in enumerate(issuers["issuer_id"].to_list(), start=1):
        d_year = distress_schedule.get(iid)
        for year in range(2018, 2025):
            for quarter in (1, 2, 3, 4):
                rows.extend(
                    _build_period_rows(
                        iid,
                        year=year,
                        quarter=quarter,
                        scale=1.0 + 0.03 * idx + 0.01 * quarter,
                        negative_equity=(d_year == year and quarter == 3),
                    )
                )
    facts_v1 = canonicalize_financial_facts(pl.DataFrame(rows))
    facts_path = curated_dir / "financial_facts.parquet"
    facts_v1.write_parquet(facts_path)

    paths_res = FinIntelPathsResource(project_root=str(tmp_path))
    state_res = StateStoreResource(
        state_path=str(tmp_path / "data" / "metadata" / "state.sqlite")
    )
    storage_res = StorageResource(backend="parquet")

    with dg.DagsterInstance.ephemeral() as instance:
        res_retrain = dg.materialize(
            [published_financial_marts, *ALL_ASSET_CHECKS],
            instance=instance,
            resources={"paths": paths_res, "state": state_res, "storage": storage_res},
            run_config=dg.RunConfig(
                ops={
                    "published_financial_marts": PublishMartsConfig(
                        as_of="2025-03-01",
                        facts_path=str(facts_path),
                        ml_enabled=True,
                        ml_mode="retrain",
                        ml_approved_target=True,
                        ml_validation_start="2021-12-01",
                        ml_test_start="2023-03-01",
                    )
                }
            ),
        )
        assert res_retrain.success

        repo_after_train = ParquetRepository(data_root=paths_res.resolved_data_root())
        trained_scores = repo_after_train.read_table(ML_SCORES_TABLE)
        assert trained_scores.height > 0

        # Add 1 new quarter for CORP_01 and run default PublishMartsConfig() (incremental_score).
        q1_2025 = _build_period_rows("CORP_01", year=2025, quarter=1, scale=1.06)
        for r in q1_2025:
            r["published_at"] = date(2025, 4, 25)
            r["effective_available_at"] = date(2025, 4, 25)
        canonicalize_financial_facts(
            pl.concat([facts_v1, pl.DataFrame(q1_2025, schema=facts_v1.schema)])
        ).write_parquet(facts_path)

        def _forbid_retrain(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("Default auto-sync publish must NOT retrain ML!")

        monkeypatch.setattr(
            "finintel.ml.supervised.train.train_distress_model", _forbid_retrain
        )

        res_auto = dg.materialize(
            [published_financial_marts, *ALL_ASSET_CHECKS],
            instance=instance,
            resources={"paths": paths_res, "state": state_res, "storage": storage_res},
            run_config=dg.RunConfig(
                ops={
                    "published_financial_marts": PublishMartsConfig(
                        as_of="2025-05-01",
                        facts_path=str(facts_path),
                    )
                }
            ),
        )
        assert res_auto.success
        assert all(ev.passed for ev in res_auto.get_asset_check_evaluations())

        repo_after_auto = ParquetRepository(data_root=paths_res.resolved_data_root())
        auto_scores = repo_after_auto.read_table(ML_SCORES_TABLE)
        assert auto_scores.height == trained_scores.height + 1


def test_incremental_transform_has_zero_platform_group_imports() -> None:
    """M-03 / H-01: incremental.py must never import platform-group packages (dagster/psycopg/redis/otel)."""
    forbidden = {
        "dagster",
        "psycopg",
        "redis",
        "opentelemetry",
        "pgvector",
        "granian",
        "fastapi",
    }
    target = REPO_ROOT / "src" / "finintel" / "transform" / "incremental.py"
    tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            assert node.module.split(".")[0] not in forbidden, node.module


def test_p1_1_quarterly_retrain_schedule_defaults(tmp_path: Path) -> None:
    """P1-1: quarterly_ml_retrain_schedule defaults to validation_start=None & target_mode=REGULATORY_DISTRESS."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")

    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "settings.yaml").write_text(
        "orchestration: {}\n", encoding="utf-8"
    )
    paths_res = FinIntelPathsResource(
        project_root=str(tmp_path), data_root=str(tmp_path / "data")
    )
    ctx = dg.build_schedule_context(
        scheduled_execution_time=datetime(2026, 4, 1, 2, 0, tzinfo=vn_tz),
        resources={"paths": paths_res},
    )
    eval_res = quarterly_ml_retrain_schedule.evaluate_tick(ctx)
    if isinstance(eval_res, dg.RunRequest):
        req = eval_res
    else:
        assert eval_res.run_requests is not None and len(eval_res.run_requests) == 1
        req = eval_res.run_requests[0]
    assert isinstance(req, dg.RunRequest)
    cfg = req.run_config["ops"]["published_financial_marts"]["config"]
    assert cfg.get("ml_validation_start") is None
    assert PublishMartsConfig(**cfg).ml_validation_start is None
    assert cfg["target_mode"] == "REGULATORY_DISTRESS"


def timezone_vn() -> tzinfo:
    return timezone(timedelta(hours=7))


@pytest.fixture(autouse=True)
def _reset_dagster_defs_state_storage() -> Iterator[None]:
    from dagster._core.storage.defs_state.base import _current_storage

    _current_storage.set(None)
    try:
        yield
    finally:
        _current_storage.set(None)


def test_task02_finintel_data_root_unifies_cli_config_and_dagster_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-01 (TASK-02): FININTEL_DATA_ROOT overrides CLI load_settings, config.py, and Dagster storage."""
    from finintel.config import resolve_runtime_config

    custom_root = (tmp_path / "unified_data_root").resolve()
    monkeypatch.setenv("FININTEL_DATA_ROOT", str(custom_root))
    settings_path = REPO_ROOT / "config" / "settings.yaml"

    cli_cfg = load_settings(settings_path)
    assert cli_cfg.state_path == (custom_root / "metadata" / "state.sqlite").resolve()
    from collections.abc import Mapping

    raw_storage = dict(cli_cfg.storage)
    cat_obj = raw_storage.get("catalog")
    assert isinstance(cat_obj, Mapping)
    raw_cat = dict(cat_obj)
    assert (
        Path(str(raw_cat["sqlite_path"])).resolve()
        == (custom_root / "metadata" / "ducklake_catalog.sqlite").resolve()
    )
    assert (
        Path(str(raw_storage["data_path"])).resolve()
        == (custom_root / "lakehouse" / "data").resolve()
    )

    rt_cfg = resolve_runtime_config()
    assert rt_cfg.settings_path == settings_path.resolve()
    assert rt_cfg.data_root == custom_root
    assert rt_cfg.state_path == (custom_root / "metadata" / "state.sqlite").resolve()
    assert (
        rt_cfg.catalog_sqlite_path
        == (custom_root / "metadata" / "ducklake_catalog.sqlite").resolve()
    )
    assert rt_cfg.lakehouse_data_path == (custom_root / "lakehouse" / "data").resolve()

    # Sibling root under REPO_ROOT (without touching disk) must also honor FININTEL_DATA_ROOT.
    sibling_root = (REPO_ROOT / "tmp_nonexistent_sibling_probe").resolve()
    monkeypatch.setenv("FININTEL_DATA_ROOT", str(sibling_root))
    rt_sibling = resolve_runtime_config(config_path=settings_path)
    assert rt_sibling.data_root == sibling_root
    assert rt_sibling.state_path == sibling_root / "metadata" / "state.sqlite"
    assert (
        rt_sibling.catalog_sqlite_path
        == sibling_root / "metadata" / "ducklake_catalog.sqlite"
    )
    assert rt_sibling.lakehouse_data_path == sibling_root / "lakehouse" / "data"

    # Default Dagster resources honor FININTEL_DATA_ROOT dynamically even when constructed bare.
    monkeypatch.setenv("FININTEL_DATA_ROOT", str(custom_root))
    bare_paths = FinIntelPathsResource()
    bare_state = StateStoreResource()
    bare_storage = StorageResource()
    assert bare_paths.resolved_data_root() == custom_root
    assert bare_state.resolved_path() == custom_root / "metadata" / "state.sqlite"
    storage_cfg = bare_storage.resolve_storage_config(bare_paths)
    assert (
        storage_cfg.catalog.sqlite_path
        == custom_root / "metadata" / "ducklake_catalog.sqlite"
    )
    assert storage_cfg.data_path == custom_root / "lakehouse" / "data"


def test_task02_disclosure_fetcher_online_env_and_skipped_offline_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-01 (TASK-02): DisclosureFetcherResource honors FININTEL_DISCLOSURE_FETCH_ONLINE and marks SKIPPED_OFFLINE."""
    from finintel.orchestration.assets import raw_public_disclosures
    from finintel.orchestration.resources import DisclosureFetcherResource

    monkeypatch.delenv("FININTEL_DISCLOSURE_FETCH_ONLINE", raising=False)
    default_res = build_default_resources()["disclosure_fetcher"]
    assert isinstance(default_res, DisclosureFetcherResource)
    assert default_res.effective_fetch_online() is True

    monkeypatch.setenv("FININTEL_DISCLOSURE_FETCH_ONLINE", "false")
    offline_res = build_default_resources()["disclosure_fetcher"]
    assert isinstance(offline_res, DisclosureFetcherResource)
    assert offline_res.effective_fetch_online() is False

    paths_res, state_res, storage_res = _setup_workspace(tmp_path, backend="parquet")
    with dg.DagsterInstance.ephemeral() as instance:
        sync_corporate_ticker_partitions(instance, paths_res.resolved_issuers_path())
        res = dg.materialize(
            [raw_public_disclosures],
            partition_key="FPT",
            instance=instance,
            resources={
                "paths": paths_res,
                "state": state_res,
                "storage": storage_res,
                "disclosure_fetcher": DisclosureFetcherResource(),
            },
            selection=[raw_public_disclosures],
        )
        assert res.success
        mats = [
            m
            for m in res.asset_materializations_for_node("raw_public_disclosures")
            if "status" in m.metadata
        ]
        assert len(mats) == 1
        meta = {k: v.value for k, v in mats[0].metadata.items()}
        assert meta["status"] == "SKIPPED_OFFLINE"
        assert meta["enriched_filings"] == 0
        assert meta["attempted_fetches"] == 0


def test_task02_start_demo_685_script_reads_finintel_data_root() -> None:
    """TASK-02: scripts/start_demo_685.ps1 honors $env:FININTEL_DATA_ROOT with data/universe_600_800 fallback."""
    script_text = (REPO_ROOT / "scripts" / "start_demo_685.ps1").read_text(
        encoding="utf-8"
    )
    assert "$env:FININTEL_DATA_ROOT" in script_text
    lines_before_check = script_text.split("Test-Path $StateSqlite")[0]
    assert "$env:FININTEL_DATA_ROOT" in lines_before_check


def test_task04_retrain_relative_path_pipeline_write_pool_and_host_cooldown(
    tmp_path: Path,
) -> None:
    """TASK-04: Retrain uses relative regulatory_events_path, write ops use pipeline_write_pool, and cooldown honors hostname."""
    from finintel.orchestration.assets import (
        PIPELINE_WRITE_POOL,
        _resolve_confined_regulatory_events_path,
        normalized_financial_facts,
        run_lakehouse_maintenance,
    )

    assert PIPELINE_WRITE_POOL == "pipeline_write_pool"
    assert normalized_financial_facts.op.pool == PIPELINE_WRITE_POOL
    assert published_financial_marts.op.pool == PIPELINE_WRITE_POOL
    assert run_lakehouse_maintenance.pool == PIPELINE_WRITE_POOL

    paths_res, state_res, _ = _setup_workspace(tmp_path)
    reg_file = (
        paths_res.resolved_data_root()
        / "regulatory_ledger"
        / "regulatory_events.parquet"
    )
    reg_file.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"issuer_id": ["VN_FPT"]}).write_parquet(reg_file)

    # Default quarterly retrain emits relative path accepted by _resolve_confined_regulatory_events_path.
    ctx = dg.build_schedule_context(
        scheduled_execution_time=datetime(2026, 4, 1, 2, 0, tzinfo=timezone_vn()),
        resources={"paths": paths_res},
    )
    tick = quarterly_ml_retrain_schedule.evaluate_tick(ctx)
    if isinstance(tick, dg.RunRequest):
        req = tick
    else:
        assert tick.run_requests is not None
        req = tick.run_requests[0]
    cfg = req.run_config["ops"]["published_financial_marts"]["config"]
    assert (
        cfg["regulatory_events_path"] == "regulatory_ledger/regulatory_events.parquet"
    )
    resolved_reg = _resolve_confined_regulatory_events_path(
        cfg["regulatory_events_path"], paths_res
    )
    assert resolved_reg == reg_file.resolve()

    # Even if settings.yaml specifies an absolute path inside data_root, schedule normalizes to relative.
    (paths_res.resolved_config_dir() / "settings.yaml").write_text(
        yaml.safe_dump(
            {
                "orchestration": {
                    "ml_retrain": {"regulatory_events_path": str(reg_file.resolve())}
                }
            }
        ),
        encoding="utf-8",
    )
    tick_abs = quarterly_ml_retrain_schedule.evaluate_tick(ctx)
    if isinstance(tick_abs, dg.RunRequest):
        req_abs = tick_abs
    else:
        assert tick_abs.run_requests is not None
        req_abs = tick_abs.run_requests[0]
    cfg_abs = req_abs.run_config["ops"]["published_financial_marts"]["config"]
    assert (
        cfg_abs["regulatory_events_path"]
        == "regulatory_ledger/regulatory_events.parquet"
    )

    # Hostname cooldown key fhsc_cooldown:api.fiinquant.vn pauses seasonal_ingestion_schedule.
    state_store = state_res.initialize()
    state_store.record_disclosure_failure(
        "fhsc_cooldown:api.fiinquant.vn",
        force_cooldown=True,
        cooldown_seconds=600.0,
        http_status=429,
        now=datetime(2026, 1, 20, 1, 0, tzinfo=timezone_vn()).astimezone(UTC),
    )
    dg_home = tmp_path / "dg_home"
    dg_home.mkdir()
    repo_def = defs.get_repository_def()
    with dg.DagsterInstance.local_temp(str(dg_home)) as instance:
        instance.add_dynamic_partitions(CORPORATE_TICKERS_PARTITION_NAME, ["FPT"])
        s_ctx = dg.build_schedule_context(
            instance=instance,
            scheduled_execution_time=datetime(2026, 1, 20, 1, 0, tzinfo=timezone_vn()),
            resources={"paths": paths_res, "state": state_res},
            repository_def=repo_def,
        )
        s_res = seasonal_ingestion_schedule.evaluate_tick(s_ctx)
        assert s_res.run_requests is not None and len(s_res.run_requests) == 0
        assert (
            s_res.skip_message is not None
            and "fhsc_cooldown:api.fiinquant.vn" in s_res.skip_message
        )


def test_task04_downstream_publish_sensor_caps_retries_at_3_on_qg2_failure(
    tmp_path: Path,
) -> None:
    """TASK-04: downstream_publish_sensor retries failed publish at most 3 times per upstream_key."""
    paths_res, _, _ = _setup_workspace(tmp_path, backend="parquet")
    pub_job_def = downstream_publish_job.resolve(asset_graph=defs.resolve_asset_graph())

    with dg.DagsterInstance.ephemeral() as instance:
        sync_corporate_ticker_partitions(instance, paths_res.resolved_issuers_path())
        instance.report_runless_asset_event(
            dg.AssetMaterialization(asset_key=raw_fhsc_filings.key, partition="FPT")
        )

        cursor: str | None = None
        emitted_keys: list[str] = []
        for attempt in (1, 2, 3):
            ctx = dg.build_sensor_context(instance=instance, cursor=cursor)
            res = downstream_publish_sensor.evaluate_tick(ctx)
            assert res.run_requests is not None and len(res.run_requests) == 1, (
                f"Expected attempt {attempt} RunRequest"
            )
            req = res.run_requests[0]
            assert req.run_key is not None
            emitted_keys.append(req.run_key)
            cursor = res.cursor
            failed_run = instance.create_run_for_job(
                job_def=pub_job_def,
                status=dg.DagsterRunStatus.STARTED,
                tags=dict(req.tags),
            )
            instance.report_run_failed(failed_run)

        assert len(set(emitted_keys)) == 3
        # 4th tick for the same failed upstream_key MUST trip circuit breaker (0 RunRequests).
        ctx_halted = dg.build_sensor_context(instance=instance, cursor=cursor)
        res_halted = downstream_publish_sensor.evaluate_tick(ctx_halted)
        assert res_halted.run_requests is not None and len(res_halted.run_requests) == 0
        assert res_halted.skip_message is not None and "3" in res_halted.skip_message


def test_task04_ticker_sweep_watermarks_30min_covers_universe_deterministically(
    tmp_path: Path,
) -> None:
    """TASK-04: seasonal_ingestion_schedule runs */30 * * * * using ticker_sweep_watermarks (< 2.86d / 685 tickers)."""
    assert seasonal_ingestion_schedule.cron_schedule == "*/30 * * * *"
    assert (
        seasonal_ingestion_schedule.default_status == dg.DefaultScheduleStatus.RUNNING
    )

    paths_res, state_res, _ = _setup_workspace(tmp_path)
    (paths_res.resolved_config_dir() / "settings.yaml").write_text(
        yaml.safe_dump({"orchestration": {"max_partitions_per_run": 5}}),
        encoding="utf-8",
    )
    state_store = state_res.initialize()

    tickers_12 = [f"T{i:02d}" for i in range(1, 13)]
    dg_sweep = tmp_path / "dg_sweep"
    dg_sweep.mkdir()
    repo_def = defs.get_repository_def()
    with dg.DagsterInstance.local_temp(str(dg_sweep)) as instance:
        instance.add_dynamic_partitions(CORPORATE_TICKERS_PARTITION_NAME, tickers_12)
        batches: list[list[str]] = []
        for slot_idx in range(3):
            t_exec = datetime(2026, 1, 20, 1, 0, tzinfo=timezone_vn()) + timedelta(
                minutes=30 * slot_idx
            )
            ctx = dg.build_schedule_context(
                instance=instance,
                scheduled_execution_time=t_exec,
                resources={"paths": paths_res, "state": state_res},
                repository_def=repo_def,
            )
            res = seasonal_ingestion_schedule.evaluate_tick(ctx)
            assert res.run_requests is not None and len(res.run_requests) == 5
            batches.append([str(r.partition_key) for r in res.run_requests])

        assert batches[0] == ["T01", "T02", "T03", "T04", "T05"]
        assert batches[1] == ["T06", "T07", "T08", "T09", "T10"]
        assert batches[2] == ["T11", "T12", "T01", "T02", "T03"]
        wm = state_store.list_ticker_sweep_watermarks()
        assert set(wm) == set(tickers_12)

    # Mathematical sweep guarantee: 685 tickers / (48 ticks/day * 5/tick) = 2.854 days.
    days_for_685 = 685 / (48 * 5)
    assert days_for_685 <= 2.86


def test_task04_bctc_disclosure_feed_sensor_15min_filters_corporate_and_archived(
    tmp_path: Path,
) -> None:
    """TASK-04: bctc_disclosure_feed_sensor (900s) emits <= 5 deterministic feed:{ticker}:{pub_date} RunRequests."""
    import json
    from finintel.domain.schemas import (
        AuditOpinion,
        AuditStatus,
        FilingMetadata,
        PeriodType,
        RawFiling,
        RawFilingBundle,
        RawPayload,
        StatementScope,
        StatementType,
    )
    from finintel.ingestion.archive import RawArchive
    from finintel.orchestration.definitions import bctc_disclosure_feed_sensor
    from finintel.orchestration.resources import DisclosureFetcherResource

    assert bctc_disclosure_feed_sensor.minimum_interval_seconds == 900
    assert bctc_disclosure_feed_sensor.default_status == dg.DefaultSensorStatus.RUNNING
    assert any(s.name == "bctc_disclosure_feed_sensor" for s in (defs.sensors or []))

    paths_res, state_res, _ = _setup_workspace(tmp_path)
    state_store = state_res.initialize()
    archive = RawArchive(paths_res.resolved_raw_root(), state_store)

    # Seed an already-archived FPT filing published on 2026-04-20 so sensor skips it.
    fpt_meta = FilingMetadata(
        issuer_id="VN_FPT",
        ticker_at_filing="FPT",
        statement_type=StatementType.BALANCE_SHEET,
        statement_scope=StatementScope.CONSOLIDATED,
        period_type=PeriodType.QUARTER,
        fiscal_year=2026,
        fiscal_quarter=1,
        fiscal_year_start_date=date(2026, 1, 1),
        period_start_date=date(2026, 1, 1),
        period_end_date=date(2026, 3, 31),
        published_at=date(2026, 4, 20),
        audit_status=AuditStatus.UNAUDITED,
        audit_opinion=AuditOpinion.NOT_APPLICABLE,
    )
    archive.store(
        RawFilingBundle(
            payload=RawPayload(
                content=b'{"error_code":"0","data":[]}',
                extension="json",
                source_provider="fhsc",
                connector_name="vnstock-oapi==0.1.2",
                nature="PROVIDER_RAW_JSON",
                fetched_at=datetime(2026, 4, 20, 8, 0, tzinfo=UTC),
            ),
            filings=(RawFiling(fpt_meta, {"bs": 100.0}, "/data/0"),),
        )
    )

    feed_dir = tmp_path / "feed_cache"
    feed_dir.mkdir()
    feed_items = [
        {
            "ticker": "VCB",
            "publicDate": "2026-04-22",
        },  # BANK (not in CORPORATE_TICKERS) -> excluded
        {"ticker": "FPT", "publicDate": "2026-04-20"},  # Already archived -> excluded
        {"ticker": "HPG", "publicDate": "2026-04-21"},
        {"ticker": "VNM", "publicDate": "2026-04-21"},
        {"ticker": "CMG", "publicDate": "2026-04-21"},
        {"ticker": "MWG", "publicDate": "2026-04-22"},
        {"ticker": "PNJ", "publicDate": "2026-04-22"},
        {"ticker": "REE", "publicDate": "2026-04-23"},  # 6th valid -> capped at 5!
    ]
    (feed_dir / "bctc_feed.json").write_text(json.dumps(feed_items), encoding="utf-8")
    fetcher_res = DisclosureFetcherResource(
        fetch_online=False, raw_disclosures_dir=str(feed_dir)
    )

    dg_feed = tmp_path / "dg_feed"
    dg_feed.mkdir()
    repo_def = defs.get_repository_def()
    with dg.DagsterInstance.local_temp(str(dg_feed)) as instance:
        instance.add_dynamic_partitions(
            CORPORATE_TICKERS_PARTITION_NAME,
            ["FPT", "HPG", "VNM", "CMG", "MWG", "PNJ", "REE"],
        )
        with dg.build_sensor_context(
            instance=instance,
            instance_ref=instance.get_ref(),
            resources={
                "paths": paths_res,
                "state": state_res,
                "disclosure_fetcher": fetcher_res,
            },
            repository_def=repo_def,
        ) as s_ctx:
            s_res = bctc_disclosure_feed_sensor.evaluate_tick(s_ctx)
        assert s_res.run_requests is not None
        assert len(s_res.run_requests) == 5
        assert [r.run_key for r in s_res.run_requests] == [
            "feed:HPG:2026-04-21",
            "feed:VNM:2026-04-21",
            "feed:CMG:2026-04-21",
            "feed:MWG:2026-04-22",
            "feed:PNJ:2026-04-22",
        ]


def test_task04_fhsc_429_persists_cooldown_and_has_archived_filing_pit_safety(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TASK-04 regression: FHSC 429 records cooldown + failed sweep watermark, and STATUTORY_LAG never masks feed."""
    from finintel.domain.schemas import (
        AuditOpinion,
        AuditStatus,
        FilingMetadata,
        PeriodType,
        RawFiling,
        RawFilingBundle,
        RawPayload,
        StatementScope,
        StatementType,
    )
    from finintel.exceptions import RateLimitOrCircuitOpenError
    from finintel.ingestion.archive import RawArchive
    from finintel.orchestration.partitions import (
        DEFAULT_CONCURRENCY_POOL_LIMITS,
        FHSC_API_POOL,
        PIPELINE_WRITE_POOL,
        PUBLIC_DISCLOSURE_POOL,
    )

    assert DEFAULT_CONCURRENCY_POOL_LIMITS == {
        FHSC_API_POOL: 1,
        PUBLIC_DISCLOSURE_POOL: 3,
        PIPELINE_WRITE_POOL: 1,
    }

    paths_res, state_res, _ = _setup_workspace(tmp_path)
    state_store = state_res.initialize()
    archive = RawArchive(paths_res.resolved_raw_root(), state_store)

    # 1. STATUTORY_LAG filing (published_at=None -> effective_available_at=2026-04-30) must NOT suppress feed on 2026-04-19!
    stat_lag_meta = FilingMetadata(
        issuer_id="VN_HPG",
        ticker_at_filing="HPG",
        statement_type=StatementType.BALANCE_SHEET,
        statement_scope=StatementScope.CONSOLIDATED,
        period_type=PeriodType.QUARTER,
        fiscal_year=2026,
        fiscal_quarter=1,
        fiscal_year_start_date=date(2026, 1, 1),
        period_start_date=date(2026, 1, 1),
        period_end_date=date(2026, 3, 31),
        published_at=None,
        audit_status=AuditStatus.UNAUDITED,
        audit_opinion=AuditOpinion.NOT_APPLICABLE,
    )
    archive.store(
        RawFilingBundle(
            payload=RawPayload(
                content=b'{"error_code":"0","data":[{"stat_lag":1}]}',
                extension="json",
                source_provider="fhsc",
                connector_name="vnstock-oapi==0.1.2",
                nature="PROVIDER_RAW_JSON",
                fetched_at=datetime(2026, 4, 18, 8, 0, tzinfo=UTC),
            ),
            filings=(RawFiling(stat_lag_meta, {"bs": 1.0}, "/data/0"),),
        )
    )
    assert (
        state_store.has_archived_filing_on_or_after("HPG", date(2026, 4, 19)) is False
    )

    # 2. Restated historical filing published on 2026-04-20 must NOT suppress a distinct filing published on 2026-04-19!
    restate_meta = FilingMetadata(
        issuer_id="VN_HPG",
        ticker_at_filing="HPG",
        statement_type=StatementType.INCOME_STATEMENT,
        statement_scope=StatementScope.CONSOLIDATED,
        period_type=PeriodType.ANNUAL,
        fiscal_year=2025,
        fiscal_quarter=None,
        fiscal_year_start_date=date(2025, 1, 1),
        period_start_date=date(2025, 1, 1),
        period_end_date=date(2025, 12, 31),
        published_at=date(2026, 4, 20),
        audit_status=AuditStatus.AUDITED,
        audit_opinion=AuditOpinion.UNMODIFIED,
    )
    archive.store(
        RawFilingBundle(
            payload=RawPayload(
                content=b'{"error_code":"0","data":[{"restate":1}]}',
                extension="json",
                source_provider="fhsc",
                connector_name="vnstock-oapi==0.1.2",
                nature="PROVIDER_RAW_JSON",
                fetched_at=datetime(2026, 4, 20, 9, 0, tzinfo=UTC),
            ),
            filings=(RawFiling(restate_meta, {"is": 2.0}, "/data/0"),),
        )
    )
    assert (
        state_store.has_archived_filing_on_or_after("HPG", date(2026, 4, 19)) is False
    )
    assert state_store.has_archived_filing_on_or_after("HPG", date(2026, 4, 20)) is True

    # 3. FHSC 429 inside raw_fhsc_filings records fhsc_cooldown:api.fiinquant.vn and increments consecutive_failures.
    def _fake_call(self: Any, ctx: Any) -> Any:
        tk = ctx.tickers[0]
        if tk == "HPG":
            raise RateLimitOrCircuitOpenError("HTTP 429 Too Many Requests")
        from finintel.pipeline.runner import StepOutcome, StepStatus

        return StepOutcome(status=StepStatus.SUCCEEDED, issuer_id=f"VN_{tk}")

    monkeypatch.setattr(
        "finintel.orchestration.assets.IngestHandler.__call__", _fake_call
    )
    dg_429 = tmp_path / "dg_429"
    dg_429.mkdir()
    with dg.DagsterInstance.local_temp(str(dg_429)) as instance:
        instance.add_dynamic_partitions(
            CORPORATE_TICKERS_PARTITION_NAME, ["FPT", "HPG"]
        )
        res = dg.materialize(
            [raw_fhsc_filings],
            instance=instance,
            resources={"paths": paths_res, "state": state_res},
            partition_key="HPG",
            raise_on_error=False,
        )
        assert not res.success
    assert (
        state_store.active_disclosure_cooldown_until("fhsc_cooldown:api.fiinquant.vn")
        is not None
    )
    wm = state_store.list_ticker_sweep_watermarks()
    assert wm["HPG"]["consecutive_failures"] == 1
