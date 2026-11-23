"""Unit tests for ARCH-06: Dagster automation, TT96 seasonal scheduling, DBSP as_of decoupling, and ML cadence separation."""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any

import dagster as dg
import polars as pl
import pytest
import yaml  # type: ignore[import-untyped]

from finintel.exceptions import ContractViolationError
from finintel.orchestration.assets import (
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
from finintel.orchestration.checks import ALL_ASSET_CHECKS
from finintel.orchestration.definitions import build_default_resources, defs
from finintel.orchestration.partitions import (
    CORPORATE_TICKERS_PARTITION_NAME,
    load_universe_tickers_yaml,
    sync_corporate_ticker_partitions,
)
from finintel.orchestration.resources import (
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


def timezone_vn() -> tzinfo:
    return timezone(timedelta(hours=7))
