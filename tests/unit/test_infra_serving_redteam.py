"""TDD regression tests for Red-Team findings P1-5, P2-4 (prewarm), and P2-8."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from prometheus_client import CollectorRegistry, Counter  # noqa: E402

from finintel.pipeline.state_store import StateStore  # noqa: E402
from finintel.serving.api import _sanitize_csv_cell, create_app  # noqa: E402
from finintel.serving.observability import (  # noqa: E402
    attach_observability,
    generate_metrics_payload,
    mark_current_process_dead,
)
from tests.unit.test_layer5_fastapi_serving import _build_synthetic_v2_tables  # noqa: E402


def test_p1_5_boot_guard_warns_loudly_when_active_snapshot_is_empty(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """P1-5: booting FastAPI with empty active_snapshot logs a clear WARNING without breaking health routes."""
    data_root = tmp_path / "empty_data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()

    app = create_app(data_root=data_root, valkey_url="")
    with caplog.at_level(logging.WARNING, logger="finintel.serving.dependencies"):
        with TestClient(app) as client:
            assert client.get("/health/live").status_code == 200
            assert client.get("/health/ready").status_code == 503

    messages = [
        rec.getMessage() for rec in caplog.records if rec.levelno >= logging.WARNING
    ]
    assert any(
        "active_snapshot" in msg and "FININTEL_DATA_ROOT" in msg for msg in messages
    ), f"Expected loud boot guard warning, got: {messages}"


def test_p1_5_boot_guard_silent_when_active_snapshot_exists(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """P1-5: no empty-snapshot boot warning is emitted when active_snapshot is populated."""
    from finintel.storage.snapshot_publisher import SnapshotPublisher

    data_root = tmp_path / "populated_data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    state.start_run("run-boot-ok", "MANUAL", {"source": "test"}, None)
    SnapshotPublisher(data_root / "published" / "snapshots", state_store=state).publish(
        "snap_boot_ok_001", _build_synthetic_v2_tables(), run_id="run-boot-ok"
    )
    state.finish_run("run-boot-ok", "SUCCEEDED", "snap_boot_ok_001")

    app = create_app(data_root=data_root, valkey_url="")
    with caplog.at_level(logging.WARNING, logger="finintel.serving.dependencies"):
        with TestClient(app) as client:
            assert client.get("/health/ready").status_code == 200

    assert not any(
        "active_snapshot is empty" in rec.getMessage() for rec in caplog.records
    )


def test_p2_4_rag_prewarm_uses_resolve_rag_pg_dsn_when_env_unset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2-4: _schedule_rag_prewarm / _prewarm_rag_worker use resolve_rag_pg_dsn() even when FININTEL_RAG_PG_DSN is unset."""
    data_root = tmp_path / "prewarm_data"
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()

    monkeypatch.delenv("FININTEL_RAG_PG_DSN", raising=False)
    monkeypatch.setenv("FININTEL_RAG_PREWARM_IN_TEST", "1")

    called_dsns: list[str] = []

    def fake_resolve(explicit: str | None = None) -> str:
        _ = explicit
        return "postgresql://finintel:finintel_dev@127.0.0.1:59998/finintel_rag"

    def fake_connect(dsn: str, **_kwargs: object) -> object:
        called_dsns.append(dsn)
        raise ConnectionError("Postgres unreachable in unit test")

    monkeypatch.setattr("finintel.rag.store.resolve_rag_pg_dsn", fake_resolve)
    monkeypatch.setattr(
        "finintel.serving.api.importlib.import_module",
        lambda name: SimpleNamespace(connect=fake_connect)
        if name == "psycopg"
        else __import__(name),
    )

    spawned: list[object] = []
    real_thread = __import__("threading").Thread

    class _SyncThread(real_thread):  # type: ignore[misc, valid-type]
        def start(self) -> None:
            spawned.append(self)
            self.run()

    monkeypatch.setattr("finintel.serving.api.threading.Thread", _SyncThread)

    app = create_app(data_root=data_root, valkey_url="")
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200

    assert len(spawned) == 1
    assert called_dsns == [
        "postgresql://finintel:finintel_dev@127.0.0.1:59998/finintel_rag?connect_timeout=1"
    ]
    assert getattr(app.state, "_rag_investigator", None) is None


def test_p2_8_generate_metrics_payload_does_not_drop_metrics_when_multiproc_dir_has_no_db_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2-8: when PROMETHEUS_MULTIPROC_DIR is set after import (empty dir), fallback to local registry."""
    mp_dir = tmp_path / "empty_multiproc"
    mp_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(mp_dir))

    registry = CollectorRegistry()
    counter = Counter(
        "finintel_p28_fallback_total", "Fallback counter", registry=registry
    )
    counter.inc(5)

    payload = generate_metrics_payload(registry).decode("utf-8")
    assert payload != ""
    assert "finintel_p28_fallback_total 5.0" in payload


def test_p2_8_lifespan_shutdown_calls_mark_process_dead_when_multiproc_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2-8: FastAPI lifespan shutdown invokes prometheus_client.multiprocess.mark_process_dead(os.getpid())."""
    mp_dir = tmp_path / "shutdown_multiproc"
    mp_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(mp_dir))

    data_root = tmp_path / "shutdown_data"
    StateStore(data_root / "metadata" / "state.sqlite").initialize()

    dead_pids: list[int] = []
    from prometheus_client import multiprocess

    monkeypatch.setattr(
        multiprocess,
        "mark_process_dead",
        lambda pid, *_args, **_kwargs: dead_pids.append(int(pid)),
    )

    app = create_app(data_root=data_root, valkey_url="")
    with TestClient(app):
        assert dead_pids == []
    assert dead_pids == [os.getpid()]


def test_p2_8_404_unmatched_paths_collapse_to_unmatched_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2-8: unmatched 404 routes collapse to handler='unmatched' to bound Prometheus cardinality."""
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    monkeypatch.setenv("FININTEL_METRICS_ENABLED", "1")

    app = FastAPI()
    attach_observability(app)

    @app.get("/api/v1/health")
    def ok_health() -> dict[str, str]:
        return {"status": "ok"}

    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/wp-login.php").status_code == 404
        assert client.get("/random/unbounded/path/123").status_code == 404
        assert client.get("/another/probe/xyz").status_code == 404

        metrics_text = client.get("/metrics").text
        assert "/wp-login.php" not in metrics_text
        assert "/random/unbounded/path/123" not in metrics_text
        assert "/another/probe/xyz" not in metrics_text
        assert (
            'http_requests_total{handler="unmatched",method="GET",status="404"} 3.0'
            in metrics_text
        )


def test_p2_8_sanitize_csv_cell_blocks_leading_whitespace_formulas_and_preserves_negatives() -> (
    None
):
    """P2-8: CSV sanitization strips leading whitespace before checking formula prefixes while keeping -123.45."""
    assert _sanitize_csv_cell(" =1+1") == "' =1+1"
    assert _sanitize_csv_cell("  +SUM(1,1)") == "'  +SUM(1,1)"
    assert _sanitize_csv_cell(" -cmd") == "' -cmd"
    assert _sanitize_csv_cell(" @SUM(A1)") == "' @SUM(A1)"
    assert _sanitize_csv_cell("\n=CMD()") == "'\n=CMD()"
    assert _sanitize_csv_cell(-123.45) == -123.45
    assert _sanitize_csv_cell(-7) == -7


def test_p2_8_mark_current_process_dead_closes_mmap_and_removes_live_gauge_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2-8: mark_current_process_dead closes live gauge mmap handles and removes gauge_livesum_<pid>.db."""
    from prometheus_client.mmap_dict import MmapedDict

    mp_dir = tmp_path / "real_mp"
    mp_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(mp_dir))

    db_path = mp_dir / f"gauge_livesum_{os.getpid()}.db"
    mmap_handle = MmapedDict(str(db_path))  # type: ignore[no-untyped-call]
    fake_gauge = SimpleNamespace(_value=SimpleNamespace(_file=mmap_handle))

    assert db_path.is_file()
    mark_current_process_dead((fake_gauge,))
    assert not db_path.exists()
