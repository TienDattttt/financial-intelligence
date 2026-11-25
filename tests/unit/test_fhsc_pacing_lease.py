"""Unit and multi-process offline tests for TASK-03 (SQLite host pacing, SSRF guard & telemetry).

Verified Sources:
- spec/SPEC-v2-autonomous-institutional-finintel.md (§3.A, §3.F, §3.G, AC-02)
- CONSTRAINTS.md (<= 0.6 req/s, min_interval >= 1.75s across OS processes, lock hold < 5ms)
"""

from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from finintel.exceptions import ContractViolationError
from finintel.ingestion.disclosure_enricher import validate_disclosure_source_url
from finintel.ingestion.ssrf_guard import (
    MAX_HTML_JSON_BYTES,
    MAX_PDF_BYTES,
    fetch_guarded_https_bytes,
    validate_global_ip,
    validate_https_url_and_resolve_global_ips,
)
from finintel.pipeline.health import health_check
from finintel.pipeline.state_store import StateStore, acquire_host_pacing_slot
from finintel.serving.observability import (
    VALID_ENTRY_POINTS,
    bind_telemetry_context,
    generate_metrics_payload,
    get_ingestion_metrics_registry,
    get_telemetry_context,
    record_ingestion_request,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_sqlite_host_pacing_slot_lock_hold_under_5ms_and_sleep_outside_lock(
    tmp_path: Path,
) -> None:
    """SQLite BEGIN IMMEDIATE lock is held < 5ms and sleeping happens strictly outside lock."""
    db_path = tmp_path / "metadata" / "state.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # First slot at t=1000.0 requires zero sleep and schedules next at 1001.75.
    slept: list[float] = []
    w0 = acquire_host_pacing_slot(
        db_path,
        "open-api.fhsc.com.vn",
        min_interval_seconds=1.75,
        jitter_seconds=0.0,
        now_fn=lambda: 1000.0,
        sleeper=slept.append,
    )
    assert w0 == pytest.approx(0.0)
    assert slept == []

    # Second slot at t=1000.25 must sleep 1.50s, and while inside sleeper, another
    # writer must acquire BEGIN IMMEDIATE in < 5ms (proving lock was already released!).
    concurrent_lock_ms: list[float] = []

    def check_unlocked_during_sleep(duration: float) -> None:
        slept.append(duration)
        t_start = time.perf_counter()
        with sqlite3.connect(db_path, timeout=0.05, isolation_level=None) as other:
            other.execute("BEGIN IMMEDIATE;")
            other.execute("COMMIT;")
        concurrent_lock_ms.append((time.perf_counter() - t_start) * 1000.0)

    t_call = time.perf_counter()
    w1 = acquire_host_pacing_slot(
        db_path,
        "OPEN-API.FHSC.COM.VN",
        min_interval_seconds=1.75,
        jitter_seconds=0.0,
        now_fn=lambda: 1000.25,
        sleeper=check_unlocked_during_sleep,
    )
    total_ms = (time.perf_counter() - t_call) * 1000.0
    assert w1 == pytest.approx(1.50)
    assert len(concurrent_lock_ms) == 1
    assert concurrent_lock_ms[0] < 5.0, (
        f"SQLite lock held too long: {concurrent_lock_ms[0]:.2f}ms"
    )
    assert total_ms < 50.0


def test_two_os_processes_fhsc_adapter_maintain_polite_spacing_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-02: Two independent OS processes calling FhscOapiAdapter stay >= 1.70s apart offline."""
    db_path = tmp_path / "state.sqlite"
    store = StateStore(db_path)
    store.initialize()

    worker_code = """
import json, sys, time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(sys.argv[1]) / "src"))

import finintel.ingestion.fhsc_oapi_adapter as adapter_mod
from finintel.domain.schemas import PeriodType, StatementType
from finintel.ingestion.fhsc_oapi_adapter import FhscOapiAdapter
from finintel.pipeline.state_store import StateStore

timestamps = []
class _FakeSource:
    spec = SimpleNamespace(
        hosts=("open-api.fhsc.com.vn",),
        rate_per_second=0.6,
        ops={"financial-statement/statement": SimpleNamespace(max_pages=5)},
    )
    def plan(self, *args, **kwargs):
        return [SimpleNamespace(operation="financial-statement/statement", params={"symbol": "FPT", "type": "balance-sheet", "period": "quarter"})]
    def raw(self, *args, **kwargs):
        timestamps.append(time.time())
        return [SimpleNamespace(
            fetched_at=datetime.now(UTC).isoformat(),
            body={"error_code": "0", "data": []},
            api_version="v1",
            http_status=200,
            body_sha256="0" * 64,
            transport={"request_count": 1},
        )]

adapter_mod.load_fhsc_source = lambda: _FakeSource()
st = StateStore(Path(sys.argv[2]))
ad = FhscOapiAdapter("FPT", StatementType.BALANCE_SHEET, PeriodType.QUARTER, state_store=st)
list(ad.fetch())
print(json.dumps(timestamps))
"""
    env = {**os.environ}
    p1 = subprocess.Popen(
        [sys.executable, "-c", worker_code, str(REPO_ROOT), str(db_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    p2 = subprocess.Popen(
        [sys.executable, "-c", worker_code, str(REPO_ROOT), str(db_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    out1, err1 = p1.communicate(timeout=15)
    out2, err2 = p2.communicate(timeout=15)
    assert p1.returncode == 0, err1
    assert p2.returncode == 0, err2

    import json

    ts_all = sorted(json.loads(out1.strip()) + json.loads(out2.strip()))
    assert len(ts_all) == 2
    gap = ts_all[1] - ts_all[0]
    assert gap >= 1.70, (
        f"Cross-process FHSC request gap {gap:.3f}s violated <= 0.6 req/s"
    )

    # Also verify health_check(online=True) reserves from the exact same provider_rate_limits row!
    monkeypatch.setenv("FHSC_API_KEY", "offline-test-key")
    monkeypatch.setenv("VNSTOCK_FHSC_KEY_CREATED_AT", "2026-11-10T00:00:00+00:00")
    fake_probe = SimpleNamespace(
        spec=SimpleNamespace(hosts=("open-api.fhsc.com.vn",)),
        raw=lambda *a, **kw: [SimpleNamespace(transport={"request_count": 1})],
    )
    monkeypatch.setattr("finintel.pipeline.health.load_fhsc_source", lambda: fake_probe)
    health_sleeps: list[float] = []
    health_check(
        store,
        online=True,
        now=datetime(2026, 11, 20, tzinfo=UTC),
        pacing_sleeper=health_sleeps.append,
    )
    assert len(health_sleeps) == 1
    assert health_sleeps[0] > 0.0


def test_ssrf_guard_blocks_non_global_ips_redirects_and_oversized_streams() -> None:
    """AC-02: ssrf_guard enforces HTTPS:443, allowlist, global DNS IPs, no redirects, identity & max_bytes."""
    assert MAX_HTML_JSON_BYTES == 5_242_880
    assert MAX_PDF_BYTES == 52_428_800

    for bad_ip in (
        "127.0.0.1",
        "10.0.0.8",
        "172.18.0.2",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
    ):
        with pytest.raises(ContractViolationError):
            validate_global_ip(bad_ip)

    assert str(validate_global_ip("104.18.22.45")) == "104.18.22.45"

    # DNS rebinding returning [public, loopback] must be rejected!
    def rebinding_resolver(
        host: str, port: int, *args: object, **kwargs: object
    ) -> list[tuple[object, ...]]:
        _ = (host, port, args, kwargs)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.22.45", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    with pytest.raises(ContractViolationError, match="non-global"):
        validate_https_url_and_resolve_global_ips(
            "https://iq.vietcap.com.vn/api/test",
            resolver=rebinding_resolver,
        )

    def clean_resolver(
        host: str, port: int, *args: object, **kwargs: object
    ) -> list[tuple[object, ...]]:
        _ = (host, port, args, kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.22.45", 443))]

    # Scheme, port, credential, and host allowlist checks
    for bad_url in (
        "http://iq.vietcap.com.vn/api/test",
        "https://user:pass@iq.vietcap.com.vn/api/test",
        "https://iq.vietcap.com.vn:8443/api/test",
        "https://evil.example.com/api/test",
    ):
        with pytest.raises(ContractViolationError):
            validate_https_url_and_resolve_global_ips(bad_url, resolver=clean_resolver)
        with pytest.raises(ContractViolationError):
            validate_disclosure_source_url(bad_url)

    seen_headers: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.update({k.lower(): v for k, v in req.headers.items()})
        if "redirect" in str(req.url):
            return httpx.Response(
                302, headers={"Location": "http://169.254.169.254/latest"}
            )
        if "bomb" in str(req.url):
            return httpx.Response(200, content=b"x" * 2048)
        return httpx.Response(200, content=b'{"ok":true}')

    with httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as client:
        payload = fetch_guarded_https_bytes(
            "https://iq.vietcap.com.vn/api/ok",
            client=client,
            resolver=clean_resolver,
            max_bytes=1024,
        )
        assert payload == b'{"ok":true}'
        assert seen_headers.get("accept-encoding") == "identity"

        with pytest.raises(ContractViolationError, match="redirect"):
            fetch_guarded_https_bytes(
                "https://iq.vietcap.com.vn/api/redirect",
                client=client,
                resolver=clean_resolver,
                max_bytes=1024,
            )

        with pytest.raises(ContractViolationError, match="exceeds"):
            fetch_guarded_https_bytes(
                "https://iq.vietcap.com.vn/api/bomb",
                client=client,
                resolver=clean_resolver,
                max_bytes=1024,
            )


def test_observability_entry_point_context_and_ingestion_metrics() -> None:
    """TASK-03: entry_point contextvars and bounded Prometheus ingestion metrics work without platform deps."""
    assert VALID_ENTRY_POINTS == frozenset(
        {"http_api", "dagster_sensor", "dagster_schedule", "cli"}
    )
    with pytest.raises(ValueError, match="entry_point"):
        with bind_telemetry_context(entry_point="invalid_source"):
            pass

    with bind_telemetry_context(
        entry_point="dagster_sensor", run_id="run_123", snapshot_id="snap_001"
    ):
        ctx = get_telemetry_context()
        assert ctx["entry_point"] == "dagster_sensor"
        assert ctx["run_id"] == "run_123"
        assert ctx["snapshot_id"] == "snap_001"

    assert get_telemetry_context()["entry_point"] is None

    record_ingestion_request("fhsc", "rate_limited", 0.35)
    record_ingestion_request("vietcap_iq", "success", 0.12)
    text = generate_metrics_payload(get_ingestion_metrics_registry()).decode("utf-8")
    assert (
        'finintel_ingestion_requests_total{outcome="rate_limited",provider="fhsc"}'
        in text
    )
    assert 'finintel_ingestion_duration_seconds_count{provider="vietcap_iq"}' in text
    assert "ticker=" not in text
    assert "filing_id=" not in text


def test_ingest_handler_and_disclosure_retry_honor_pacing_and_ssrf_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IngestHandler passes state_store to FhscOapiAdapter and disclosure retry enforces ssrf_guard."""
    from finintel.pipeline.ingest import (
        IngestHandler,
        fetch_disclosure_bytes_with_retry,
    )
    from finintel.pipeline.runner import RunRequest, Stage, StepContext, TriggerMode

    store = StateStore(tmp_path / "metadata" / "state.sqlite")
    store.initialize()
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    handler = IngestHandler(
        store,
        config_dir,
        {"default_statement_scope": "CONSOLIDATED"},
    )
    ctx = StepContext(
        run_id="run_test",
        stage=Stage.INGEST,
        request=RunRequest(
            source="fhsc",
            tickers=("FPT",),
            stage=Stage.INGEST,
            trigger_mode=TriggerMode.CLI_MANUAL,
        ),
        tickers=("FPT",),
        active_snapshot_id=None,
    )
    providers = list(handler._providers(ctx))
    assert len(providers) == 6
    assert all(getattr(p, "_state_store", None) is store for p in providers)

    req_headers: dict[str, str] = {}

    def mock_handler(req: httpx.Request) -> httpx.Response:
        req_headers.update({k.lower(): v for k, v in req.headers.items()})
        return httpx.Response(200, content=b'{"successful":true,"data":{}}')

    with httpx.Client(
        transport=httpx.MockTransport(mock_handler), follow_redirects=False
    ) as mock_client:
        out = fetch_disclosure_bytes_with_retry(
            "https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/FPT/financial-statement?section=BALANCE_SHEET",
            client=mock_client,
        )
        assert out == b'{"successful":true,"data":{}}'
        assert req_headers.get("accept-encoding") == "identity"

    monkeypatch.setattr(
        "finintel.ingestion.ssrf_guard.socket.getaddrinfo",
        lambda host, port, *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )
    with httpx.Client(follow_redirects=False) as real_client:
        with pytest.raises(ContractViolationError, match="non-global"):
            fetch_disclosure_bytes_with_retry(
                "https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/FPT/financial-statement?section=BALANCE_SHEET",
                client=real_client,
            )
