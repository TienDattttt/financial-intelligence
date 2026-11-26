"""TDD unit and empirical integration tests for Stream B2 Part 2 (Dagster orchestration).

Verified Sources:
    - Dagster Official Documentation — Testing Assets, Partitions, Asset Checks:
      https://docs.dagster.io/guides/test/
      https://docs.dagster.io/guides/test/asset-checks
    - Runtime API verification in project ``.venv`` (Python 3.11.9, ``dagster==1.13.30``):
      - ``dg.Definitions.validate_loadable(defs)`` (staticmethod)
      - ``dg.materialize(..., tags={ASSET_PARTITION_RANGE_START_TAG: ..., ASSET_PARTITION_RANGE_END_TAG: ...})``
      - ``dg.DagsterInstance.from_config(...)`` + ``EventLogStorage.get_pool_limits()``
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from datetime import date
from importlib import import_module
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest
import yaml  # type: ignore[import-untyped]
from filelock import FileLock, Timeout

pytest.importorskip("dagster")

import dagster as dg  # noqa: E402
from dagster._core.errors import DagsterAssetCheckFailedError  # noqa: E402
from dagster._core.storage.tags import (  # noqa: E402
    ASSET_PARTITION_RANGE_END_TAG,
    ASSET_PARTITION_RANGE_START_TAG,
)

from finintel.exceptions import (  # noqa: E402
    ContractViolationError,
    CriticalDataQualityError,
    RateLimitOrCircuitOpenError,
)
from finintel.ingestion import fhsc_oapi_adapter  # noqa: E402
from finintel.orchestration import (  # noqa: E402
    ALL_ASSET_CHECKS,
    ALL_ASSETS,
    CORPORATE_TICKERS,
    CORPORATE_TICKERS_PARTITION_NAME,
    FHSC_API_POOL,
    PUBLIC_DISCLOSURE_POOL,
    DisclosureFetcherResource,
    FhscIngestConfig,
    FinIntelPathsResource,
    PublishMartsConfig,
    StateStoreResource,
    StorageResource,
    check_quality_gate_1_critical_ratio,
    check_quality_gate_2_lineage_and_dupont,
    configure_concurrency_pools,
    defs,
    lakehouse_maintenance_job,
    lakehouse_maintenance_schedule,
    normalized_financial_facts,
    published_financial_marts,
    raw_fhsc_filings,
    raw_public_disclosures,
    sync_corporate_ticker_partitions,
)
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_TABLE,
    BENCHMARKS_TABLE,
    FILINGS_MANIFEST_TABLE,
    ML_SCORES_TABLE,
    QUALITY_SUMMARY_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SdkStub = Callable[[Callable[[httpx.Request], httpx.Response]], list[httpx.Request]]


@pytest.fixture
def fhsc_sdk(monkeypatch: pytest.MonkeyPatch) -> Iterator[SdkStub]:
    """Install a deterministic httpx.MockTransport onto vnstock_oapi's FHSC source."""
    monkeypatch.setenv("FHSC_API_KEY", "test-dagster-fhsc-key")
    monkeypatch.setattr(fhsc_oapi_adapter, "_default_sleep", lambda _: None)
    source = import_module("vnstock_oapi").source("fhsc")
    transport_class = import_module("vnstock_oapi.core.transport").Transport
    clients: list[httpx.Client] = []

    def install(
        respond: Callable[[httpx.Request], httpx.Response],
    ) -> list[httpx.Request]:
        requests: list[httpx.Request] = []

        def capture(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return respond(request)

        client = httpx.Client(transport=httpx.MockTransport(capture))
        clients.append(client)
        transport = transport_class("fhsc", source.spec.hosts, client=client)
        monkeypatch.setattr(type(source), "transport", property(lambda _: transport))
        return requests

    yield install
    for client in clients:
        client.close()


def _write_three_corporate_issuers(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    issuers = pl.DataFrame(
        {
            "issuer_id": ["VN_FPT", "VN_HPG", "VN_VNM"],
            "ticker": ["FPT", "HPG", "VNM"],
            "exchange": ["HOSE", "HOSE", "HOSE"],
            "company_name_vn": [
                "Công ty Cổ phần FPT",
                "Tập đoàn Hòa Phát",
                "Công ty Cổ phần Sữa Việt Nam",
            ],
            "accounting_regime": ["CORPORATE", "CORPORATE", "CORPORATE"],
            "icb_code_level2": ["9500", "1700", "3500"],
            "icb_name_level2": ["Công nghệ", "Tài nguyên", "Thực phẩm"],
            "icb_code_level3": ["9530", "1750", "3570"],
            "icb_name_level3": ["Phần mềm", "Thép", "Sữa"],
            "industry_snapshot_date": [
                date(2024, 1, 1),
                date(2024, 1, 1),
                date(2024, 1, 1),
            ],
            "delisted_date": [None, None, None],
        }
    )
    issuers.write_parquet(path)


def _fhsc_statement_rows(
    ticker: str, statement_slug: str, period_slug: str
) -> list[dict[str, object]]:
    scales = {"FPT": 1.0, "HPG": 2.0, "VNM": 0.8}
    scale = scales.get(ticker, 1.0)
    rows: list[dict[str, object]] = []

    def _bs(year: int, quarter: int | None, growth: float) -> dict[str, object]:
        ca = 50_000_000_000_000.0 * scale * growth
        nca = 30_000_000_000_000.0 * scale * growth
        ta = ca + nca
        cl = 30_000_000_000_000.0 * scale * growth
        ncl = 10_000_000_000_000.0 * scale * growth
        tl = cl + ncl
        eq = ta - tl
        return {
            "year": year,
            "quarter": quarter,
            "taisannganhan": ca,
            "tienvacackhoantuongduongtien": 10_000_000_000_000.0 * scale,
            "cackhoandaututaichinhnganhan": 5_000_000_000_000.0 * scale,
            "cackhoanphaithunganhan": 14_000_000_000_000.0 * scale,
            "phaithukhachhang": 12_000_000_000_000.0 * scale,
            "duphongphaithunganhankhodoi": -500_000_000_000.0 * scale,
            "hangtonkho_tong": 2_000_000_000_000.0 * scale,
            "taisandaihan": nca,
            "cackhoanphaithudaihan": 500_000_000_000.0 * scale,
            "phaithudaihancuakhachhang": 0.0,
            "duphongphaithukhodoi": -50_000_000_000.0 * scale,
            "tongcongtaisan": ta,
            "nophaitra": tl,
            "nonganhan": cl,
            "phaitranguoibannganhan": 3_500_000_000_000.0 * scale,
            "vayvanothuetaichinhnganhan": 12_000_000_000_000.0 * scale,
            "nodaihan": ncl,
            "vayvanothuetaichinhdaihan": 4_000_000_000_000.0 * scale,
            "traiphieuchuyendoi": 0.0,
            "vonchusohuu_tong": eq,
            "cophieuphothongcoquyenbieuquyet": 17_000_000_000_000.0 * scale,
            "loiichcodongkhongkiemsoat": 6_000_000_000_000.0 * scale,
            "tongcongnguonvon": ta,
        }

    def _is(
        year: int, quarter: int | None, growth: float, divisor: float
    ) -> dict[str, object]:
        rev = 60_000_000_000_000.0 * scale * growth / divisor
        cogs = 36_000_000_000_000.0 * scale * growth / divisor
        gp = rev - cogs
        sell = 5_000_000_000_000.0 * scale * growth / divisor
        admin = 4_000_000_000_000.0 * scale * growth / divisor
        interest = 700_000_000_000.0 * scale / divisor
        op = gp - sell - admin - interest
        pbt = op + 500_000_000_000.0 * scale / divisor
        tax_cur = 1_800_000_000_000.0 * scale / divisor
        tax_def = -100_000_000_000.0 * scale / divisor
        ni = pbt - tax_cur - tax_def
        return {
            "year": year,
            "quarter": quarter,
            "doanhthuthuanvebanhangvacungcapdichvu": rev,
            "giavonhangban": cogs,
            "loinhuangopvebanhangvacungcapdichvu": gp,
            "chiphibanhang": sell,
            "chiphiquanlydoanhnghiep": admin,
            "trongdochiphilaivay": interest,
            "loinhuanthuantuhoatdongkinhdoanh": op,
            "tongloinhuanketoantruocthue": pbt,
            "chiphithuetndnhienhanh": tax_cur,
            "chiphithuetndnhoanlai": tax_def,
            "loinhuansauthuethunhapdoanhnghiep": ni,
            "loinhuansauthuecuacongtyme": ni * 0.85,
            "laicobantrencophieu": 5000.0 / divisor,
        }

    def _cf(year: int, quarter: int | None, divisor: float) -> dict[str, object]:
        return {
            "year": year,
            "quarter": quarter,
            "khauhaotaisancodinh": 2_500_000_000_000.0 * scale / divisor,
            "luuchuyentienthuantuhoatdongkinhdoanh": 10_500_000_000_000.0
            * scale
            / divisor,
            "tienchidemuasamxaydungtaisancodinh": -4_000_000_000_000.0
            * scale
            / divisor,
        }

    if period_slug in ("annual", "year"):
        for year, growth in ((2025, 1.12), (2024, 1.0)):
            if statement_slug == "balance-sheet":
                rows.append(_bs(year, None, growth))
            elif statement_slug == "income-statement":
                rows.append(_is(year, None, growth, 1.0))
            else:
                rows.append(_cf(year, None, 1.0))
    else:
        for year, growth in ((2025, 1.12), (2024, 1.0)):
            for quarter in (4, 3, 2, 1):
                if statement_slug == "balance-sheet":
                    rows.append(_bs(year, quarter, growth))
                elif statement_slug == "income-statement":
                    rows.append(_is(year, quarter, growth, 4.0))
                else:
                    rows.append(_cf(year, quarter, 4.0))
    return rows


def _make_fhsc_responder(
    *,
    rate_limit_ticker: str | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    def respond(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        symbol = params["symbol"]
        stmt_type = params["type"]
        period = params["period"]
        page = int(params.get("page", "1"))
        limit = int(params.get("limit", "5"))

        if rate_limit_ticker is not None and symbol == rate_limit_ticker:
            return httpx.Response(
                429,
                json={"error_code": "429", "message": "Rate limit exceeded"},
                headers={"Retry-After": "1"},
            )

        all_rows = _fhsc_statement_rows(symbol, stmt_type, period)
        start = (page - 1) * limit
        chunk = all_rows[start : start + limit]
        return httpx.Response(
            200,
            json={
                "error_code": "0",
                "traceId": f"trace-{symbol}-{page}",
                "data": chunk,
            },
        )

    return respond


def _build_mock_disclosure_responses(tickers: tuple[str, ...]) -> dict[str, bytes]:
    responses: dict[str, bytes] = {}
    for ticker in tickers:
        vci_url = (
            f"https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/"
            f"{ticker}/financial-statement?section=BALANCE_SHEET"
        )
        vci_payload = {
            "status": 200,
            "successful": True,
            "data": {
                "years": [
                    {
                        "ticker": ticker,
                        "yearReport": 2024,
                        "lengthReport": 5,
                        "publicDate": "2025-03-20T00:00:00",
                    },
                    {
                        "ticker": ticker,
                        "yearReport": 2025,
                        "lengthReport": 5,
                        "publicDate": "2026-03-20T00:00:00",
                    },
                ],
                "quarters": [
                    {
                        "ticker": ticker,
                        "yearReport": year,
                        "lengthReport": q,
                        "publicDate": (
                            f"{year + 1}-01-25T00:00:00"
                            if q == 4
                            else f"{year}-{q * 3 + 1:02d}-20T00:00:00"
                        ),
                    }
                    for year in (2024, 2025)
                    for q in (1, 2, 3, 4)
                ],
            },
        }
        responses[vci_url] = json.dumps(vci_payload).encode("utf-8")

        cafef_url = (
            f"https://cafef.vn/du-lieu/Ajax/CongTy/BaoCaoTaiChinh.aspx?sym={ticker}"
        )
        cafef_html = (
            "<table>"
            f"<tr><td>BCTC hợp nhất kiểm toán 2025</td><td>CN/2025</td>"
            f'<td><a href="https://cafef.vn/files/{ticker}_20260318_BCTC_HNKT.pdf">PDF</a></td></tr>'
            f"<tr><td>BCTC hợp nhất Quý 4/2025</td><td>Q4/2025</td>"
            f'<td><a href="https://cafef.vn/files/{ticker}_20260122_BCTC_Q4.pdf">PDF</a></td></tr>'
            "</table>"
        )
        responses[cafef_url] = cafef_html.encode("utf-8")
    return responses


class _MockDisclosureFetcherResource(DisclosureFetcherResource):
    """Test subclass injecting httpx.MockTransport while preserving all SSRF guards."""

    mock_payloads_json: str = "{}"

    def _create_http_client(self) -> httpx.Client:
        raw_map: dict[str, str] = json.loads(self.mock_payloads_json)

        def handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            if url_str not in raw_map:
                return httpx.Response(404, text="Not Found")
            return httpx.Response(200, content=raw_map[url_str].encode("utf-8"))

        return httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            timeout=httpx.Timeout(float(self.timeout_seconds)),
        )


def _setup_workspace(
    tmp_path: Path, *, backend: str = "parquet"
) -> tuple[FinIntelPathsResource, StateStoreResource, StorageResource]:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    issuers_path = tmp_path / "data" / "curated" / "issuers.parquet"
    _write_three_corporate_issuers(issuers_path)

    settings_payload: dict[str, Any] = {
        "state_path": "../data/metadata/state.sqlite",
        "min_free_bytes": 0,
        "tickers": ["FPT", "HPG", "VNM"],
        "storage": {
            "backend": backend,
            "catalog": {
                "driver": "sqlite",
                "sqlite_path": "../data/metadata/ducklake_catalog.sqlite",
            },
            "data_path": "../data/lakehouse/data",
            "catalog_alias": "finintel_lake",
            "retention": {
                "keep_snapshots": 2,
                "min_snapshot_age_seconds": 0,
                "curated_keep_runs": 2,
            },
            "publish_unmapped": False,
        },
        "ingestion": {
            "infer_calendar_schedule": True,
            "default_statement_scope": "CONSOLIDATED",
            "issuers_path": "../data/curated/issuers.parquet",
            "incremental_lookback_years": 2,
        },
        "analytics": {
            "issuers_path": "../data/curated/issuers.parquet",
            "as_of": "2026-06-01",
        },
        "quality": {
            "max_critical_issuer_ratio": 0.10,
        },
    }
    (config_dir / "settings.yaml").write_text(
        yaml.safe_dump(settings_payload, sort_keys=False), encoding="utf-8"
    )

    paths_res = FinIntelPathsResource(
        project_root=str(tmp_path),
        config_dir="config",
        data_root="data",
    )
    state_res = StateStoreResource(
        state_path=str(tmp_path / "data" / "metadata" / "state.sqlite")
    )
    storage_res = StorageResource(backend=backend)
    return paths_res, state_res, storage_res


@pytest.fixture(autouse=True)
def _reset_dagster_defs_state_storage() -> Iterator[None]:
    from dagster._core.storage.defs_state.base import _current_storage

    _current_storage.set(None)
    try:
        yield
    finally:
        _current_storage.set(None)


def test_definitions_validate_loadable_pools_deps_and_schedule() -> None:
    """Definitions load cleanly; pools, deps=[raw_fhsc_filings], blocking checks, and schedule match Spec 07."""
    dg.Definitions.validate_loadable(defs)

    assert raw_fhsc_filings.op.pool == FHSC_API_POOL
    assert raw_fhsc_filings.partitions_def == CORPORATE_TICKERS
    assert raw_fhsc_filings.backfill_policy == dg.BackfillPolicy.multi_run(
        max_partitions_per_run=10
    )

    assert raw_public_disclosures.op.pool == PUBLIC_DISCLOSURE_POOL
    assert raw_public_disclosures.partitions_def == CORPORATE_TICKERS
    assert (
        raw_fhsc_filings.key
        in raw_public_disclosures.asset_deps[raw_public_disclosures.key]
    )

    norm_deps = normalized_financial_facts.asset_deps[normalized_financial_facts.key]
    assert raw_fhsc_filings.key in norm_deps
    assert raw_public_disclosures.key in norm_deps

    pub_deps = published_financial_marts.asset_deps[published_financial_marts.key]
    assert normalized_financial_facts.key in pub_deps

    for check_def in ALL_ASSET_CHECKS:
        specs = list(check_def.check_specs)
        assert len(specs) == 1
        assert specs[0].asset_key == published_financial_marts.key
        assert specs[0].blocking is True

    assert lakehouse_maintenance_schedule.cron_schedule == "0 3 * * 0"
    assert lakehouse_maintenance_schedule.execution_timezone == "Asia/Ho_Chi_Minh"
    assert lakehouse_maintenance_schedule.job == lakehouse_maintenance_job

    assert {job.name for job in (defs.jobs or [])} >= {
        "fhsc_ingestion_job",
        "downstream_publish_job",
        "quarterly_ml_retrain_job",
        "lakehouse_maintenance_job",
    }
    assert {sched.name for sched in (defs.schedules or [])} >= {
        "seasonal_ingestion_schedule",
        "quarterly_ml_retrain_schedule",
        "lakehouse_maintenance_schedule",
    }
    assert {sensor.name for sensor in (defs.sensors or [])} >= {
        "downstream_publish_sensor",
    }


def test_dagster_yaml_storage_location_and_concurrency_pools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config/dagster.yaml keeps SQLite under data/metadata/dagster and configures op-level pools."""
    yaml_src = REPO_ROOT / "config" / "dagster.yaml"
    assert yaml_src.is_file()

    parsed = yaml.safe_load(yaml_src.read_text(encoding="utf-8"))
    assert parsed["storage"]["sqlite"]["base_dir"] == "data/metadata/dagster"
    assert parsed["concurrency"]["pools"]["granularity"] == "op"
    assert parsed["concurrency"]["pools"]["default_limit"] == 4
    assert "run_coordinator" not in parsed
    assert "tag_concurrency_limits" not in json.dumps(parsed)

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    shutil.copy2(yaml_src, cfg_dir / "dagster.yaml")
    monkeypatch.chdir(tmp_path)

    from finintel.orchestration.partitions import PIPELINE_WRITE_POOL

    with dg.DagsterInstance.from_config(str(cfg_dir)) as instance:
        applied = configure_concurrency_pools(instance)
        assert applied == {
            FHSC_API_POOL: 1,
            PUBLIC_DISCLOSURE_POOL: 3,
            PIPELINE_WRITE_POOL: 1,
        }

        limits = {
            item.name: item.limit
            for item in instance.event_log_storage.get_pool_limits()
        }
        assert limits[FHSC_API_POOL] == 1
        assert limits[PUBLIC_DISCLOSURE_POOL] == 3
        assert limits[PIPELINE_WRITE_POOL] == 1

        assert (tmp_path / "data" / "metadata" / "dagster" / "history").is_dir()
        assert list(cfg_dir.glob("*.db")) == []
        assert list(tmp_path.glob("*.db")) == []


def test_sync_corporate_ticker_partitions_filters_corporate_and_is_idempotent(
    tmp_path: Path,
) -> None:
    """sync_corporate_ticker_partitions loads CORPORATE tickers idempotently and supports pruning."""
    issuers_path = tmp_path / "issuers.parquet"
    pl.DataFrame(
        {
            "issuer_id": ["VN_FPT", "VN_HPG", "VN_VCB"],
            "ticker": ["fpt", "HPG", "VCB"],
            "accounting_regime": ["CORPORATE", "CORPORATE", "BANK"],
        }
    ).write_parquet(issuers_path)

    with dg.DagsterInstance.ephemeral() as instance:
        synced_1 = sync_corporate_ticker_partitions(instance, issuers_path)
        assert synced_1 == ("FPT", "HPG")
        assert list(
            instance.get_dynamic_partitions(CORPORATE_TICKERS_PARTITION_NAME)
        ) == ["FPT", "HPG"]

        # Repeat call is idempotent.
        synced_2 = sync_corporate_ticker_partitions(instance, issuers_path)
        assert synced_2 == ("FPT", "HPG")

        # Update issuers table and prune removed ticker.
        pl.DataFrame(
            {
                "issuer_id": ["VN_FPT", "VN_VNM"],
                "ticker": ["FPT", "VNM"],
                "accounting_regime": ["CORPORATE", "CORPORATE"],
            }
        ).write_parquet(issuers_path)
        synced_3 = sync_corporate_ticker_partitions(
            instance, issuers_path, prune_removed=True
        )
        assert synced_3 == ("FPT", "VNM")
        assert set(
            instance.get_dynamic_partitions(CORPORATE_TICKERS_PARTITION_NAME)
        ) == {"FPT", "VNM"}


def test_disclosure_fetcher_security_guards(tmp_path: Path) -> None:
    """DisclosureFetcherResource enforces HTTPS allowlist, follow_redirects=False, timeout<=15s, and confinement."""
    fetcher = DisclosureFetcherResource(fetch_online=True, timeout_seconds=10.0)

    for bad_url in (
        "http://cafef.vn/du-lieu/test",
        "https://evil.example.com/payload.json",
        "https://user:pass@cafef.vn/test",
        "https://iq.vietcap.com.vn:8443/api/test",
        "https://cafef.vn/path with space",
    ):
        with pytest.raises(ContractViolationError):
            fetcher.validate_url(bad_url)

    with pytest.raises(ContractViolationError):
        DisclosureFetcherResource(timeout_seconds=16.0).validate_config()
    with pytest.raises(ContractViolationError):
        DisclosureFetcherResource(timeout_seconds=0.0).validate_config()

    class _RedirectFetcher(DisclosureFetcherResource):
        def _create_http_client(self) -> httpx.Client:
            return httpx.Client(
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(
                        302, headers={"Location": "https://evil.example.com/"}
                    )
                ),
                follow_redirects=False,
                timeout=httpx.Timeout(5.0),
            )

    with pytest.raises(RuntimeError, match="HTTP 302"):
        _RedirectFetcher(fetch_online=True).fetch_bytes(
            "https://cafef.vn/du-lieu/Ajax/CongTy/BaoCaoTaiChinh.aspx?sym=FPT"
        )

    class _OversizedFetcher(DisclosureFetcherResource):
        def _create_http_client(self) -> httpx.Client:
            return httpx.Client(
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(200, content=b"x" * 2048)
                ),
                follow_redirects=False,
                timeout=httpx.Timeout(5.0),
            )

    with pytest.raises(ContractViolationError, match="size limit"):
        _OversizedFetcher(fetch_online=True, max_bytes=1024).fetch_bytes(
            "https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/FPT/financial-statement?section=BALANCE_SHEET"
        )

    local_dir = tmp_path / "disclosures"
    local_dir.mkdir()
    local_fetcher = DisclosureFetcherResource(raw_disclosures_dir=str(local_dir))
    with pytest.raises(ContractViolationError):
        local_fetcher.read_local_confined("../secret.json")


def test_end_to_end_materialize_3_partitions_mock_fhsc_and_disclosures(
    tmp_path: Path, fhsc_sdk: SdkStub
) -> None:
    """Full Dagster dg.materialize on 3 dynamic partitions publishes all tables + passes blocking checks."""
    requests = fhsc_sdk(_make_fhsc_responder())
    paths_res, state_res, storage_res = _setup_workspace(tmp_path, backend="parquet")
    mock_disclosures = {
        url: body.decode("utf-8")
        for url, body in _build_mock_disclosure_responses(("FPT", "HPG", "VNM")).items()
    }
    disclosure_res = _MockDisclosureFetcherResource(
        fetch_online=True,
        on_error="fail",
        max_date="2026-06-01",
        mock_payloads_json=json.dumps(mock_disclosures),
    )

    with dg.DagsterInstance.ephemeral() as instance:
        synced = sync_corporate_ticker_partitions(
            instance, paths_res.resolved_issuers_path()
        )
        assert synced == ("FPT", "HPG", "VNM")

        result = dg.materialize(
            [*ALL_ASSETS, *ALL_ASSET_CHECKS],
            instance=instance,
            resources={
                "paths": paths_res,
                "state": state_res,
                "storage": storage_res,
                "disclosure_fetcher": disclosure_res,
            },
            run_config=dg.RunConfig(
                ops={
                    "raw_fhsc_filings": FhscIngestConfig(
                        mode="backfill", as_of_year=2026
                    ),
                    "published_financial_marts": PublishMartsConfig(as_of="2026-06-01"),
                }
            ),
            tags={
                ASSET_PARTITION_RANGE_START_TAG: "FPT",
                ASSET_PARTITION_RANGE_END_TAG: "VNM",
            },
        )

        assert result.success
        assert len(requests) > 0

        # Both blocking asset checks evaluated and passed.
        check_evals = {ev.check_name: ev for ev in result.get_asset_check_evaluations()}
        assert set(check_evals) == {
            "quality_gate_1_critical_ratio",
            "quality_gate_2_lineage_and_dupont",
        }
        assert all(ev.passed for ev in check_evals.values())

        # Verify disclosures upgraded filings to EXACT_FILING and wrote SHA-256 receipts.
        store = state_res.get_store()
        with store.connection() as conn:
            exact_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM filings_manifest WHERE published_at_basis = 'EXACT_FILING'"
                ).fetchone()[0]
            )
            assert exact_count > 0
        assert any((paths_res.resolved_raw_root() / "provenance").glob("*.json"))

        # Verify state.sqlite recorded SUCCEEDED run and active_snapshot.
        active = store.active_snapshot()
        assert active is not None
        snapshot_id = str(active["snapshot_id"])
        assert snapshot_id.startswith("snap_")
        run_row = store.get_run(str(active["run_id"]))
        assert run_row is not None
        assert run_row["status"] == "SUCCEEDED"

        # Verify _transform_state.json updated atomically with published snapshot_id.
        state_json = json.loads(
            paths_res.resolved_transform_state_path().read_text(encoding="utf-8")
        )
        assert state_json["snapshot_id"] == snapshot_id

        # Verify all 10 published tables are readable cleanly via ParquetRepository.
        repo = ParquetRepository(data_root=paths_res.resolved_data_root())
        assert repo.snapshot_id == snapshot_id
        for table_name in (
            "issuers.parquet",
            "financial_facts.parquet",
            "latest_facts.parquet",
            FILINGS_MANIFEST_TABLE,
            RATIOS_TABLE,
            BENCHMARKS_TABLE,
            RED_FLAGS_TABLE,
            ML_SCORES_TABLE,
            QUALITY_SUMMARY_TABLE,
            AUDIT_QUARANTINE_TABLE,
        ):
            df = repo.read_table(table_name)
            assert isinstance(df, pl.DataFrame)

        ratios = repo.read_table(RATIOS_TABLE)
        assert ratios["issuer_id"].n_unique() == 3
        assert repo.read_table(AUDIT_QUARANTINE_TABLE).is_empty()

        # Verify sync mode enables early-stop (fewer FHSC calls than backfill on unchanged older pages).
        from dagster._core.storage.defs_state.base import _current_storage

        _current_storage.set(None)
        requests.clear()
        sync_res = dg.materialize(
            [raw_fhsc_filings],
            instance=instance,
            resources={"paths": paths_res, "state": state_res},
            run_config=dg.RunConfig(
                ops={
                    "raw_fhsc_filings": FhscIngestConfig(
                        mode="sync", as_of_year=2026, incremental_lookback_years=1
                    )
                }
            ),
            partition_key="FPT",
        )
        assert sync_res.success
        sync_calls = len(requests)

        requests.clear()
        backfill_res = dg.materialize(
            [raw_fhsc_filings],
            instance=instance,
            resources={"paths": paths_res, "state": state_res},
            run_config=dg.RunConfig(
                ops={
                    "raw_fhsc_filings": FhscIngestConfig(
                        mode="backfill", as_of_year=2026, incremental_lookback_years=1
                    )
                }
            ),
            partition_key="FPT",
        )
        assert backfill_res.success
        assert len(requests) > sync_calls

        # Verify a second published_financial_marts materialization runs incrementally (full_rebuild=False).
        inc_res = dg.materialize(
            [published_financial_marts, *ALL_ASSET_CHECKS],
            instance=instance,
            resources={
                "paths": paths_res,
                "state": state_res,
                "storage": storage_res,
            },
            run_config=dg.RunConfig(
                ops={
                    "published_financial_marts": PublishMartsConfig(as_of="2026-06-01")
                }
            ),
        )
        assert inc_res.success
        pub_mats = [
            ev.step_materialization_data.materialization
            for ev in inc_res.get_asset_materialization_events()
            if ev.step_materialization_data.materialization.asset_key
            == published_financial_marts.key
        ]
        assert len(pub_mats) == 1
        assert pub_mats[0].metadata["full_rebuild"].value is False
        assert pub_mats[0].metadata["dirty_issuers_count"].value == 0


def test_raw_fhsc_filings_fail_fast_preserves_prior_partition_materializations(
    tmp_path: Path, fhsc_sdk: SdkStub
) -> None:
    """RateLimitOrCircuitOpenError on ticker #2 fails fast while preserving ticker #1 materialization & data."""
    requests = fhsc_sdk(_make_fhsc_responder(rate_limit_ticker="HPG"))
    paths_res, state_res, _ = _setup_workspace(tmp_path, backend="parquet")

    with dg.DagsterInstance.ephemeral() as instance:
        sync_corporate_ticker_partitions(instance, paths_res.resolved_issuers_path())

        result = dg.materialize(
            [raw_fhsc_filings],
            instance=instance,
            resources={"paths": paths_res, "state": state_res},
            run_config=dg.RunConfig(
                ops={
                    "raw_fhsc_filings": FhscIngestConfig(
                        mode="backfill", as_of_year=2026
                    )
                }
            ),
            tags={
                ASSET_PARTITION_RANGE_START_TAG: "FPT",
                ASSET_PARTITION_RANGE_END_TAG: "VNM",
            },
            raise_on_error=False,
        )

        assert not result.success

        # Confirm failure cause is RateLimitOrCircuitOpenError.
        failure_events = [
            ev for ev in result.all_events if ev.event_type_value == "STEP_FAILURE"
        ]
        assert len(failure_events) == 1
        err_data = failure_events[0].step_failure_data
        assert err_data is not None
        assert err_data.error is not None
        cause = err_data.error.cause
        assert cause is not None
        assert cause.cls_name == RateLimitOrCircuitOpenError.__name__

        # Ticker 1 (FPT) was materialized BEFORE ticker 2 (HPG) failed; VNM was never attempted.
        assert instance.get_materialized_partitions(raw_fhsc_filings.key) == {"FPT"}
        requested_symbols = {req.url.params["symbol"] for req in requests}
        assert "FPT" in requested_symbols
        assert "HPG" in requested_symbols
        assert "VNM" not in requested_symbols

        # FPT archived rows are durably committed in filings_manifest.
        with state_res.get_store().connection() as conn:
            fpt_rows = int(
                conn.execute(
                    "SELECT COUNT(*) FROM filings_manifest WHERE ticker_at_filing = 'FPT'"
                ).fetchone()[0]
            )
            vnm_rows = int(
                conn.execute(
                    "SELECT COUNT(*) FROM filings_manifest WHERE ticker_at_filing = 'VNM'"
                ).fetchone()[0]
            )
        assert fpt_rows > 0
        assert vnm_rows == 0


def test_blocking_checks_and_pre_publish_audit_protect_active_snapshot(
    tmp_path: Path, fhsc_sdk: SdkStub
) -> None:
    """Corrupt facts (>10% critical) fail publish preserving prior snapshot; corrupt snapshot fails blocking check."""
    fhsc_sdk(_make_fhsc_responder())
    paths_res, state_res, storage_res = _setup_workspace(tmp_path, backend="parquet")
    mock_disclosures = {
        url: body.decode("utf-8")
        for url, body in _build_mock_disclosure_responses(("FPT", "HPG", "VNM")).items()
    }
    disclosure_res = _MockDisclosureFetcherResource(
        fetch_online=True,
        on_error="fail",
        max_date="2026-06-01",
        mock_payloads_json=json.dumps(mock_disclosures),
    )

    with dg.DagsterInstance.ephemeral() as instance:
        sync_corporate_ticker_partitions(instance, paths_res.resolved_issuers_path())
        res_ok = dg.materialize(
            [*ALL_ASSETS, *ALL_ASSET_CHECKS],
            instance=instance,
            resources={
                "paths": paths_res,
                "state": state_res,
                "storage": storage_res,
                "disclosure_fetcher": disclosure_res,
            },
            run_config=dg.RunConfig(
                ops={
                    "raw_fhsc_filings": FhscIngestConfig(mode="backfill"),
                    "published_financial_marts": PublishMartsConfig(as_of="2026-06-01"),
                }
            ),
            tags={
                ASSET_PARTITION_RANGE_START_TAG: "FPT",
                ASSET_PARTITION_RANGE_END_TAG: "VNM",
            },
        )
        assert res_ok.success

        store = state_res.get_store()
        prior_active = store.active_snapshot()
        assert prior_active is not None
        prior_snapshot_id = str(prior_active["snapshot_id"])

        # Corrupt 1/3 issuers (33% > 10%) BS_TOTAL_ASSETS in a staged facts file -> QG1 must abort publish.
        repo = ParquetRepository(data_root=paths_res.resolved_data_root())
        clean_facts = repo.read_table("financial_facts.parquet")
        corrupt_facts = clean_facts.with_columns(
            pl.when(
                (pl.col("issuer_id") == "VN_FPT")
                & (pl.col("metric_id") == "BS_TOTAL_ASSETS")
            )
            .then(pl.col("value") * 2.5)
            .otherwise(pl.col("value"))
            .alias("value")
        )
        corrupt_path = tmp_path / "data" / "curated" / "corrupt_facts.parquet"
        corrupt_facts.write_parquet(corrupt_path)

        with pytest.raises(CriticalDataQualityError):
            dg.materialize(
                [published_financial_marts],
                instance=instance,
                resources={
                    "paths": paths_res,
                    "state": state_res,
                    "storage": storage_res,
                },
                run_config=dg.RunConfig(
                    ops={
                        "published_financial_marts": PublishMartsConfig(
                            as_of="2026-06-01",
                            facts_path=str(corrupt_path),
                        )
                    }
                ),
            )

        # Prior active snapshot must remain untouched and failed run recorded as FAILED.
        after_active = store.active_snapshot()
        assert after_active is not None
        assert str(after_active["snapshot_id"]) == prior_snapshot_id
        with store.connection() as conn:
            failed_runs = conn.execute(
                "SELECT COUNT(*) FROM pipeline_runs WHERE status = 'FAILED'"
            ).fetchone()[0]
        assert int(failed_runs) == 1

        # Tamper directly with published financial_ratios.parquet (orphan filing_id) -> blocking QG2 check fails!
        snap_dir = (
            paths_res.resolved_data_root()
            / "published"
            / "snapshots"
            / prior_snapshot_id
        )
        ratios_path = snap_dir / RATIOS_TABLE
        original_ratios = pl.read_parquet(ratios_path)
        tampered = original_ratios.with_columns(
            pl.lit("orphan-filing-id-999").alias("filing_id")
        )
        tampered.write_parquet(ratios_path)

        with pytest.raises(DagsterAssetCheckFailedError):
            dg.materialize(
                [
                    published_financial_marts,
                    check_quality_gate_1_critical_ratio,
                    check_quality_gate_2_lineage_and_dupont,
                ],
                instance=instance,
                resources={
                    "paths": paths_res,
                    "state": state_res,
                    "storage": storage_res,
                },
                selection=dg.AssetSelection.checks_for_assets(
                    published_financial_marts
                ),
            )

        # Restore valid filing_ids, then tamper DuPont tax_burden by > 1e-9 -> blocking QG2 check fails!
        dupont_tampered = original_ratios.with_columns(
            pl.when(pl.col("dupont_valid_flag") == True)  # noqa: E712
            .then(pl.col("tax_burden") + 1e-6)
            .otherwise(pl.col("tax_burden"))
            .alias("tax_burden")
        )
        dupont_tampered.write_parquet(ratios_path)
        with pytest.raises(DagsterAssetCheckFailedError):
            dg.materialize(
                [
                    published_financial_marts,
                    check_quality_gate_2_lineage_and_dupont,
                ],
                instance=instance,
                resources={
                    "paths": paths_res,
                    "state": state_res,
                    "storage": storage_res,
                },
                selection=dg.AssetSelection.checks_for_assets(
                    published_financial_marts
                ),
            )

        # Restore valid ratios, then inject >10% critical issuer in audit_quarantine -> blocking QG1 check fails!
        original_ratios.write_parquet(ratios_path)
        quarantine_path = snap_dir / AUDIT_QUARANTINE_TABLE
        pl.DataFrame(
            {
                "issuer_id": ["VN_FPT"],
                "filing_id": ["bad-filing-1"],
                "statement_scope": ["CONSOLIDATED"],
                "period_type": ["ANNUAL"],
                "period_end_date": [date(2025, 12, 31)],
                "taxonomy_version": ["VAS_TT200_2014"],
                "rule_code": ["BS_IDENTITY_MISMATCH"],
                "severity": ["CRITICAL"],
                "details_json": ["{}"],
                "quarantined_at": ["2026-06-01T00:00:00+00:00"],
            }
        ).write_parquet(quarantine_path)
        with pytest.raises(DagsterAssetCheckFailedError):
            dg.materialize(
                [
                    published_financial_marts,
                    check_quality_gate_1_critical_ratio,
                ],
                instance=instance,
                resources={
                    "paths": paths_res,
                    "state": state_res,
                    "storage": storage_res,
                },
                selection=dg.AssetSelection.checks_for_assets(
                    published_financial_marts
                ),
            )


def test_lakehouse_maintenance_job_respects_pipeline_lock_and_preserves_active_files(
    tmp_path: Path, fhsc_sdk: SdkStub
) -> None:
    """lakehouse_maintenance_job respects pipeline.lock and cleans only aged unreferenced curated dirs."""
    fhsc_sdk(_make_fhsc_responder())
    paths_res, state_res, storage_res = _setup_workspace(tmp_path, backend="parquet")
    mock_disclosures = {
        url: body.decode("utf-8")
        for url, body in _build_mock_disclosure_responses(("FPT", "HPG", "VNM")).items()
    }
    disclosure_res = _MockDisclosureFetcherResource(
        fetch_online=True,
        on_error="fail",
        max_date="2026-06-01",
        mock_payloads_json=json.dumps(mock_disclosures),
    )

    with dg.DagsterInstance.ephemeral() as instance:
        sync_corporate_ticker_partitions(instance, paths_res.resolved_issuers_path())
        mat_res = dg.materialize(
            [*ALL_ASSETS],
            instance=instance,
            resources={
                "paths": paths_res,
                "state": state_res,
                "storage": storage_res,
                "disclosure_fetcher": disclosure_res,
            },
            run_config=dg.RunConfig(
                ops={
                    "raw_fhsc_filings": FhscIngestConfig(mode="backfill"),
                    "published_financial_marts": PublishMartsConfig(as_of="2026-06-01"),
                }
            ),
            tags={
                ASSET_PARTITION_RANGE_START_TAG: "FPT",
                ASSET_PARTITION_RANGE_END_TAG: "VNM",
            },
        )
        assert mat_res.success

    # Seed 3 aged unreferenced curated batches so curated_keep_runs=2 prunes the oldest safely.
    ff_root = paths_res.resolved_curated_root() / "financial_facts"
    old_dirs: list[Path] = []
    for idx in range(3):
        old_dir = ff_root / f"legacy_unreferenced_run_{idx}"
        old_dir.mkdir(parents=True, exist_ok=True)
        (old_dir / "financial_facts.parquet").write_bytes(b"placeholder")
        (old_dir / "unmapped_metrics.parquet").write_bytes(b"placeholder")
        (old_dir / "_normalize_meta.json").write_text(
            json.dumps(
                {
                    "source": "fhsc",
                    "full_history": False,
                    "taxonomy_sha256": "abc",
                    "issuer_filings": {},
                }
            ),
            encoding="utf-8",
        )
        old_ts = time.time() - 7200 - idx * 60
        os.utime(old_dir, (old_ts, old_ts))
        old_dirs.append(old_dir)

    # 1. Hold pipeline.lock from another thread -> maintenance job MUST fail with Timeout and touch nothing.
    lock_path = state_res.pipeline_lock_path()
    acquired = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        lock = FileLock(lock_path, is_singleton=False)
        with lock:
            acquired.set()
            release.wait(timeout=10.0)

    holder = threading.Thread(target=hold_lock, daemon=True)
    holder.start()
    assert acquired.wait(timeout=5.0)
    try:
        fast_timeout_storage = StorageResource(
            backend="parquet",
            min_snapshot_age_seconds=60,
            curated_keep_runs=2,
            lock_timeout_seconds=0.15,
        )
        with pytest.raises(Timeout):
            lakehouse_maintenance_job.execute_in_process(
                resources={
                    "paths": paths_res,
                    "state": state_res,
                    "storage": fast_timeout_storage,
                }
            )
        # All directories untouched while lock was held.
        for d in old_dirs:
            assert d.is_dir()
    finally:
        release.set()
        holder.join(timeout=5.0)

    # 2. After lock release -> maintenance succeeds, prunes aged unreferenced dirs, preserves active files.
    maint_storage = StorageResource(
        backend="parquet",
        min_snapshot_age_seconds=60,
        curated_keep_runs=2,
        lock_timeout_seconds=5.0,
    )
    maint_res = lakehouse_maintenance_job.execute_in_process(
        resources={
            "paths": paths_res,
            "state": state_res,
            "storage": maint_storage,
        }
    )
    assert maint_res.success
    for d in old_dirs:
        assert not d.exists()

    # Active snapshot, issuers.parquet, and active run curated batch remain intact.
    assert paths_res.resolved_issuers_path().is_file()
    repo = ParquetRepository(data_root=paths_res.resolved_data_root())
    assert not repo.read_table(RATIOS_TABLE).is_empty()


def test_ducklake_backend_publish_and_maintenance_job(
    tmp_path: Path, fhsc_sdk: SdkStub
) -> None:
    """StorageResource(backend='ducklake') publishes atomically to DuckLake and runs maintain()."""
    fhsc_sdk(_make_fhsc_responder())
    paths_res, state_res, storage_res = _setup_workspace(tmp_path, backend="ducklake")
    mock_disclosures = {
        url: body.decode("utf-8")
        for url, body in _build_mock_disclosure_responses(("FPT", "HPG", "VNM")).items()
    }
    disclosure_res = _MockDisclosureFetcherResource(
        fetch_online=True,
        on_error="fail",
        max_date="2026-06-01",
        mock_payloads_json=json.dumps(mock_disclosures),
    )

    with dg.DagsterInstance.ephemeral() as instance:
        sync_corporate_ticker_partitions(instance, paths_res.resolved_issuers_path())
        res = dg.materialize(
            [*ALL_ASSETS, *ALL_ASSET_CHECKS],
            instance=instance,
            resources={
                "paths": paths_res,
                "state": state_res,
                "storage": storage_res,
                "disclosure_fetcher": disclosure_res,
            },
            run_config=dg.RunConfig(
                ops={
                    "raw_fhsc_filings": FhscIngestConfig(mode="backfill"),
                    "published_financial_marts": PublishMartsConfig(as_of="2026-06-01"),
                }
            ),
            tags={
                ASSET_PARTITION_RANGE_START_TAG: "FPT",
                ASSET_PARTITION_RANGE_END_TAG: "VNM",
            },
        )
        assert res.success
        assert all(ev.passed for ev in res.get_asset_check_evaluations())

        repo = storage_res.build_repository(paths_res)
        meta = repo.get_snapshot_metadata()
        assert meta["storage_backend"] == "ducklake"
        assert repo.read_table(RATIOS_TABLE)["issuer_id"].n_unique() == 3

        maint = lakehouse_maintenance_job.execute_in_process(
            resources={
                "paths": paths_res,
                "state": state_res,
                "storage": storage_res,
            }
        )
        assert maint.success


def test_empirical_snapshot_partitions_publish_and_blocking_checks(
    tmp_path: Path,
) -> None:
    """Opt-in real empirical test (FININTEL_EMPIRICAL_SNAPSHOT_DIR) on 15 real corporate issuers in tmp_path."""
    raw_dir = os.environ.get("FININTEL_EMPIRICAL_SNAPSHOT_DIR")
    if not raw_dir:
        pytest.skip(
            "Set FININTEL_EMPIRICAL_SNAPSHOT_DIR to run real empirical Dagster verification."
        )

    empirical_dir = Path(raw_dir).resolve()
    issuers_src = empirical_dir / "issuers.parquet"
    facts_src = empirical_dir / "financial_facts.parquet"
    assert issuers_src.is_file() and facts_src.is_file()

    # Select 15 real corporate issuers (strictly read-only from empirical snapshot into tmp_path).
    all_issuers = pl.read_parquet(issuers_src).filter(
        pl.col("accounting_regime") == "CORPORATE"
    )
    subset_issuers = all_issuers.sort("ticker").head(15)
    subset_ids = subset_issuers["issuer_id"].to_list()
    expected_tickers = tuple(sorted(subset_issuers["ticker"].to_list()))
    subset_facts = pl.read_parquet(facts_src).filter(
        pl.col("issuer_id").is_in(subset_ids)
    )
    assert subset_issuers.height == 15
    assert not subset_facts.is_empty()

    curated_dir = tmp_path / "data" / "curated"
    curated_dir.mkdir(parents=True, exist_ok=True)
    issuers_dst = curated_dir / "issuers.parquet"
    facts_dst = curated_dir / "financial_facts.parquet"
    subset_issuers.write_parquet(issuers_dst)
    subset_facts.write_parquet(facts_dst)

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.yaml").write_text(
        yaml.safe_dump(
            {
                "state_path": "../data/metadata/state.sqlite",
                "storage": {"backend": "ducklake"},
                "analytics": {"as_of": "2026-11-19"},
                "quality": {"max_critical_issuer_ratio": 0.10},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    paths_res = FinIntelPathsResource(
        project_root=str(tmp_path),
        config_dir="config",
        data_root="data",
    )
    state_res = StateStoreResource(
        state_path=str(tmp_path / "data" / "metadata" / "state.sqlite")
    )
    storage_res = StorageResource(
        backend="ducklake",
        min_snapshot_age_seconds=0,
    )

    with dg.DagsterInstance.ephemeral() as instance:
        synced = sync_corporate_ticker_partitions(instance, issuers_dst)
        assert synced == expected_tickers
        assert len(synced) == 15

        result = dg.materialize(
            [published_financial_marts, *ALL_ASSET_CHECKS],
            instance=instance,
            resources={
                "paths": paths_res,
                "state": state_res,
                "storage": storage_res,
            },
            run_config=dg.RunConfig(
                ops={
                    "published_financial_marts": PublishMartsConfig(
                        as_of="2026-11-19",
                        facts_path=str(facts_dst),
                    )
                }
            ),
        )
        assert result.success

        evals = {ev.check_name: ev for ev in result.get_asset_check_evaluations()}
        assert evals["quality_gate_1_critical_ratio"].passed is True
        assert evals["quality_gate_2_lineage_and_dupont"].passed is True

        repo = storage_res.build_repository(paths_res)
        assert repo.snapshot_id is not None
        ratios = repo.read_table(RATIOS_TABLE)
        assert ratios.width == 120
        assert not ratios.is_empty()
        assert not repo.read_table(BENCHMARKS_TABLE).is_empty()
        assert not repo.read_table(QUALITY_SUMMARY_TABLE).is_empty()

        # Verify DuckLake maintenance job runs cleanly after empirical publish.
        maint_res = lakehouse_maintenance_job.execute_in_process(
            resources={
                "paths": paths_res,
                "state": state_res,
                "storage": storage_res,
            }
        )
        assert maint_res.success

