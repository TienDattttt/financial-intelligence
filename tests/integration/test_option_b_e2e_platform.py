"""Offline zero-network End-to-End Platform integration suites for Gate B6 (Wave 5).

Verified Sources:
    - Gate B6 Specification (Bước 4 — Offline E2E Suites 1-4 + Migration Integrity Suite 5):
      docs/v2_specs/09_GATE_B6_UNIVERSE_MIGRATION_E2E_AND_BENCHMARK_VERIFICATION.md
    - Gate B0 Storage & Extension Evidence:
      docs/evidence/option_b_spike_report.json
    - Circular 200/2014/TT-BTC & Circular 99/2025/TT-BTC Taxonomy Mappings
      (including BS_CIP_LONG_TERM 242 -> 252 and TT99 code 242 non-aliasing):
      config/taxonomy/corporate_tt200_2014.yaml
      config/taxonomy/corporate_tt99_2025.yaml
      config/metric_definitions.yaml
    - Real Offline FHSC CIP Payload Sample (HPG 2025-Q3..2026-Q3):
      tests/fixtures/raw_samples/fhsc_cip_sample.json
    - Circular 96/2020/TT-BTC & Vietnam Holiday Calendar (disclosure enrichment):
      src/finintel/ingestion/disclosure_enricher.py
    - RFC 9110 HTTP Conditional Requests (ETag / If-None-Match -> 304 Not Modified):
      https://httpwg.org/specs/rfc9110.html#field.etag
      https://httpwg.org/specs/rfc9110.html#field.if-none-match
    - Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
      IMF Working Paper WP/15/216 (via finintel.analytics.stress_test & POST /api/v1/stress-test).
    - Decree 155/2020/ND-CP & VNX Decision 22/QD-HDTV (Mode B REGULATORY_DISTRESS contract):
      src/finintel/ml/supervised/target.py
      src/finintel/ml/tables.py
    - SQLite Online Backup API (WAL-safe sqlite3.Connection.backup()):
      https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup
    - Runtime API Verification (Python 3.11.9, fastapi==0.165.0, dagster==1.13.30,
      duckdb==1.4.2, polars==1.35.2, filelock==4.2.10, pydantic==2.14.4).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import sys
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

import duckdb
import polars as pl
import pytest

# Guard optional platform dependencies so GitHub Actions `mvp-checks` (no `--group platform`)
# skips cleanly during collection while `platform-checks` executes 100% of suites with 0 skips.
pytest.importorskip("fastapi")
pytest.importorskip("dagster")

from fastapi.testclient import TestClient  # noqa: E402

from finintel.domain.schemas import (  # noqa: E402
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
from finintel.exceptions import (  # noqa: E402
    ArchiveIntegrityError,
    ContractViolationError,
    CriticalDataQualityError,
)
from finintel.ingestion.archive import RawArchive  # noqa: E402
from finintel.ingestion.disclosure_enricher import (  # noqa: E402
    DisclosureRecord,
    DisclosureSlot,
    enrich_fhsc_manifest_disclosures,
    merge_disclosure_records,
    parse_cafef_bctc_disclosures,
    parse_vietcap_iq_disclosures,
)
from finintel.ingestion.hasher import (  # noqa: E402
    canonical_content_sha256,
    payload_file_sha256,
)
from finintel.ml.supervised.target import build_distress_targets  # noqa: E402
from finintel.ml.tables import build_ml_artifacts  # noqa: E402
from finintel.normalization.archive import normalize_archive_incremental  # noqa: E402
from finintel.normalization.mapper import (  # noqa: E402
    FilingMappingInput,
    TaxonomyMapper,
)
from finintel.normalization.regime_resolver import TaxonomyVersion  # noqa: E402
from finintel.pipeline.state_store import StateStore  # noqa: E402
from finintel.quality.rules import QualityGateConfig  # noqa: E402
from finintel.serving.api import create_app  # noqa: E402
from finintel.serving.queries import ALL_RATIOS  # noqa: E402
from finintel.storage.curated_gc import collect_curated_garbage  # noqa: E402
from finintel.storage.ducklake_store import DuckLakeStore  # noqa: E402
from finintel.storage.manifest_export import (  # noqa: E402
    build_filings_manifest_snapshot,
    build_latest_facts_table,
)
from finintel.storage.repository import ParquetRepository  # noqa: E402
from finintel.storage.schemas import (  # noqa: E402
    AUDIT_QUARANTINE_TABLE,
    BENCHMARKS_TABLE,
    FILINGS_MANIFEST_TABLE,
    ML_SCORES_TABLE,
    QUALITY_SUMMARY_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)
from finintel.storage.snapshot_publisher import (  # noqa: E402
    SnapshotPublisher,
    TableInput,
    pipeline_write_lock,
    snapshot_write_lock,
)
from finintel.transform import (  # noqa: E402
    TransformBundle,
    run_blocking_publish_audits,
    run_incremental_analytics_transform,
    write_transform_state,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
FHSC_CIP_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "raw_samples" / "fhsc_cip_sample.json"
)
MIGRATION_SCRIPT_PATH = REPO_ROOT / "scripts" / "migrate_universe_to_v2.py"

StorageBackendName = Literal["ducklake", "parquet"]

_QUARTER_DATES: dict[int, tuple[tuple[int, int], tuple[int, int], tuple[int, int]]] = {
    # quarter -> ((start_m, start_d), (end_m, end_d), (pub_m, pub_d))
    1: ((1, 1), (3, 31), (4, 20)),
    2: ((4, 1), (6, 30), (7, 20)),
    3: ((7, 1), (9, 30), (10, 20)),
    4: ((10, 1), (12, 31), (1, 25)),
}


@dataclass(frozen=True)
class _IssuerProfile:
    ticker: str
    company_name_vn: str
    icb2_code: str
    icb2_name: str
    icb3_code: str
    icb3_name: str
    scale: float
    debt_mult: float = 1.0
    interest_mult: float = 1.0
    gross_margin_ratio: float = 0.40
    sell_ratio: float = 0.08
    admin_ratio: float = 0.09
    non_core_boost: float = 0.0
    frozen_cip: bool = False
    use_real_cip_fixture: bool = False


_OFFLINE_PROFILES: tuple[_IssuerProfile, ...] = (
    _IssuerProfile(
        ticker="FPT",
        company_name_vn="Công ty Cổ phần FPT",
        icb2_code="9500",
        icb2_name="Công nghệ thông tin",
        icb3_code="9530",
        icb3_name="Phần mềm",
        scale=1.0,
    ),
    _IssuerProfile(
        ticker="CMG",
        company_name_vn="Tập đoàn Công nghệ CMC",
        icb2_code="9500",
        icb2_name="Công nghệ thông tin",
        icb3_code="9530",
        icb3_name="Phần mềm",
        scale=0.20,
        debt_mult=1.1,
        interest_mult=1.2,
        gross_margin_ratio=0.34,
    ),
    _IssuerProfile(
        ticker="HPG",
        company_name_vn="Tập đoàn Hòa Phát",
        icb2_code="1700",
        icb2_name="Tài nguyên Cơ bản",
        icb3_code="1750",
        icb3_name="Kim loại",
        scale=3.0,
        debt_mult=1.4,
        interest_mult=1.4,
        gross_margin_ratio=0.22,
        sell_ratio=0.03,
        admin_ratio=0.03,
        use_real_cip_fixture=True,
    ),
    _IssuerProfile(
        # Tight baseline ICR (~1.6x) so IMF +300bps / -20% rev / -3pp GM shock pushes ICR' < 1.0
        ticker="HSG",
        company_name_vn="Tập đoàn Hoa Sen",
        icb2_code="1700",
        icb2_name="Tài nguyên Cơ bản",
        icb3_code="1750",
        icb3_name="Kim loại",
        scale=0.50,
        debt_mult=2.2,
        interest_mult=8.0,
        gross_margin_ratio=0.18,
        sell_ratio=0.04,
        admin_ratio=0.04,
    ),
    _IssuerProfile(
        # High non-core financial/other income relative to core operating profit -> triggers RF_08
        ticker="REE",
        company_name_vn="Cơ Điện Lạnh REE",
        icb2_code="5000",
        icb2_name="Công nghiệp",
        icb3_code="5010",
        icb3_name="Xây dựng và Vật liệu",
        scale=0.45,
        gross_margin_ratio=0.15,
        sell_ratio=0.08,
        admin_ratio=0.08,
        non_core_boost=12_000_000_000_000.0,
    ),
    _IssuerProfile(
        # CIP > 25% of total assets across 2 consecutive years with flat revenue & TFA -> triggers RF_09
        ticker="DGC",
        company_name_vn="Hóa chất Đức Giang",
        icb2_code="1700",
        icb2_name="Tài nguyên Cơ bản",
        icb3_code="1750",
        icb3_name="Kim loại",
        scale=0.40,
        frozen_cip=True,
    ),
)


def _ducklake_extensions_available() -> bool:
    """Probe locally cached DuckLake and SQLite extensions strictly offline (never INSTALL)."""
    con = duckdb.connect(":memory:")
    try:
        con.execute("LOAD ducklake;")
        con.execute("LOAD sqlite;")
        return True
    except duckdb.Error:
        return False
    finally:
        con.close()


def _select_backend() -> StorageBackendName:
    return "ducklake" if _ducklake_extensions_available() else "parquet"


def _build_issuers_frame(
    *,
    name_suffix: str = "",
    industry_snapshot_date: date = date(2024, 1, 1),
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "issuer_id": [p.ticker for p in _OFFLINE_PROFILES],
            "ticker": [p.ticker for p in _OFFLINE_PROFILES],
            "exchange": ["HOSE"] * len(_OFFLINE_PROFILES),
            "company_name_vn": [
                f"{p.company_name_vn}{name_suffix}" for p in _OFFLINE_PROFILES
            ],
            "accounting_regime": ["CORPORATE"] * len(_OFFLINE_PROFILES),
            "icb_code_level2": [p.icb2_code for p in _OFFLINE_PROFILES],
            "icb_name_level2": [p.icb2_name for p in _OFFLINE_PROFILES],
            "icb_code_level3": [p.icb3_code for p in _OFFLINE_PROFILES],
            "icb_name_level3": [p.icb3_name for p in _OFFLINE_PROFILES],
            "industry_snapshot_date": [industry_snapshot_date] * len(_OFFLINE_PROFILES),
            "delisted_date": pl.Series([None] * len(_OFFLINE_PROFILES), dtype=pl.Date),
        }
    )


def _synthetic_bs_record(
    profile: _IssuerProfile,
    *,
    year: int,
    quarter: int | None,
    growth: float,
) -> dict[str, object]:
    s = profile.scale
    ca = 50_000_000_000_000.0 * s * growth
    nca = 30_000_000_000_000.0 * s * growth
    ta = ca + nca
    st_debt = 3_500_000_000_000.0 * s * profile.debt_mult
    lt_debt = 4_000_000_000_000.0 * s * profile.debt_mult
    cl = min(ta * 0.42, 16_000_000_000_000.0 * s * growth + st_debt)
    ncl = min(ta * 0.23, 6_000_000_000_000.0 * s * growth + lt_debt)
    tl = cl + ncl
    eq = ta - tl
    cip = (
        ta * 0.26
        if profile.frozen_cip
        else 4_500_000_000_000.0 * s * (1.0 if year == 2024 else 1.15)
    )
    tfa = (
        10_000_000_000_000.0 * s
        if profile.frozen_cip
        else 14_000_000_000_000.0 * s * growth
    )
    return {
        "year": year,
        "quarter": quarter,
        "taisannganhan": ca,
        "tienvacackhoantuongduongtien": 10_000_000_000_000.0 * s,
        "cackhoandaututaichinhnganhan": 5_000_000_000_000.0 * s,
        "cackhoanphaithunganhan": 14_000_000_000_000.0 * s,
        "phaithukhachhang": 12_000_000_000_000.0 * s,
        "duphongphaithunganhankhodoi": -500_000_000_000.0 * s,
        "hangtonkho_tong": 4_000_000_000_000.0 * s,
        "taisandaihan": nca,
        "cackhoanphaithudaihan": 500_000_000_000.0 * s,
        "phaithudaihancuakhachhang": 0.0,
        "duphongphaithukhodoi": -50_000_000_000.0 * s,
        "taisancodinh": tfa + 1_000_000_000_000.0 * s,
        "taisancodinhhuuhinh": tfa,
        "taisancodinhvohinh": 1_000_000_000_000.0 * s,
        "chiphixaydungcobandodang": cip,
        "daututaichinhdaihan": 1_500_000_000_000.0 * s,
        "tongcongtaisan": ta,
        "nophaitra": tl,
        "nonganhan": cl,
        "phaitranguoibannganhan": 3_500_000_000_000.0 * s,
        "vayvanothuetaichinhnganhan": st_debt,
        "nodaihan": ncl,
        "vayvanothuetaichinhdaihan": lt_debt,
        "traiphieuchuyendoi": 0.0,
        "vonchusohuu_tong": eq,
        "vonchusohuu": eq,
        "cophieuphothongcoquyenbieuquyet": 17_000_000_000_000.0 * s,
        "cophieuquy": 0.0,
        "loinhuansauthuechuaphanphoi": eq * 0.30,
        "loiichcodongkhongkiemsoat": eq * 0.12,
        "tongcongnguonvon": ta,
    }


def _synthetic_is_record(
    profile: _IssuerProfile,
    *,
    year: int,
    quarter: int | None,
    growth: float,
    divisor: float,
    revenue_boost: float = 1.0,
) -> dict[str, object]:
    s = profile.scale
    rev = 60_000_000_000_000.0 * s * growth * revenue_boost / divisor
    gp = rev * profile.gross_margin_ratio
    cogs = rev - gp
    sell = rev * profile.sell_ratio
    admin = rev * profile.admin_ratio
    interest = 700_000_000_000.0 * s * profile.interest_mult / divisor
    fin_rev = (1_200_000_000_000.0 * s + profile.non_core_boost * s) / divisor
    other_profit = 200_000_000_000.0 * s / divisor
    op = gp - sell - admin + fin_rev - interest
    ebt = op + other_profit
    cur_tax = max(ebt, 0.0) * 0.18
    def_tax = -50_000_000_000.0 * s / divisor
    ni = ebt - cur_tax - def_tax
    ni_parent = ni * 0.86
    return {
        "year": year,
        "quarter": quarter,
        "doanhthuthuanvebanhangvacungcapdichvu": rev,
        "giavonhangban": cogs,
        "loinhuangopvebanhangvacungcapdichvu": gp,
        "doanhthuhoatdongtaichinh": fin_rev,
        "chiphitaichinh": interest * 1.1,
        "trongdochiphilaivay": interest,
        "chiphibanhang": sell,
        "chiphiquanlydoanhnghiep": admin,
        "loinhuanthuantuhoatdongkinhdoanh": op,
        "thunhapkhac": other_profit * 1.2,
        "chiphikhac": other_profit * 0.2,
        "loinhuankhac": other_profit,
        "tongloinhuanketoantruocthue": ebt,
        "chiphithuetndnhienhanh": cur_tax,
        "chiphithuetndnhoanlai": def_tax,
        "loinhuansauthuethunhapdoanhnghiep": ni,
        "loinhuansauthuecuacongtyme": ni_parent,
        "laicobantrencophieu": 4800.0 / divisor,
    }


def _synthetic_cf_record(
    profile: _IssuerProfile,
    *,
    year: int,
    quarter: int | None,
    growth: float,
    divisor: float,
) -> dict[str, object]:
    s = profile.scale
    return {
        "year": year,
        "quarter": quarter,
        "khauhaotaisancodinh": 2_500_000_000_000.0 * s / divisor,
        "luuchuyentienthuantuhoatdongkinhdoanh": (
            10_500_000_000_000.0 * s * growth / divisor
        ),
        "tienchidemuasamxaydungtaisancodinh": -3_500_000_000_000.0 * s / divisor,
        "tienthutuphathanhcophieunhangopvoncuachusohuu": 0.0,
    }


def _store_fhsc_page(
    archive: RawArchive,
    *,
    ticker: str,
    statement_type: StatementType,
    records: list[dict[str, object]],
    fetched_at: datetime,
    raw_bytes_override: bytes | None = None,
) -> None:
    """Archive one FHSC multi-record JSON response immutably through ``RawArchive.store``."""
    raw_bytes = (
        raw_bytes_override
        if raw_bytes_override is not None
        else json.dumps(
            {"error_code": "0", "message": "success", "data": records},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    payload = RawPayload(
        content=raw_bytes,
        extension="json",
        source_provider="fhsc",
        connector_name="vnstock-oapi==0.1.2",
        nature="PROVIDER_RAW_JSON",
        fetched_at=fetched_at,
    )
    filings: list[RawFiling] = []
    for idx, rec in enumerate(records):
        year = rec["year"]
        quarter = rec["quarter"]
        assert isinstance(year, int)
        assert quarter is None or isinstance(quarter, int)
        if quarter is None:
            p_type = PeriodType.ANNUAL
            p_start = date(year, 1, 1)
            p_end = date(year, 12, 31)
            audit_status = AuditStatus.AUDITED
            audit_opinion = AuditOpinion.UNMODIFIED
        else:
            p_type = PeriodType.QUARTER
            (sm, sd), (em, ed), _ = _QUARTER_DATES[quarter]
            p_start = date(year, sm, sd)
            p_end = date(year, em, ed)
            audit_status = (
                AuditStatus.REVIEWED if quarter == 2 else AuditStatus.UNAUDITED
            )
            audit_opinion = AuditOpinion.NOT_APPLICABLE

        metrics = {k: v for k, v in rec.items() if k not in {"year", "quarter"}}
        meta = FilingMetadata(
            issuer_id=ticker,
            ticker_at_filing=ticker,
            statement_type=statement_type,
            statement_scope=StatementScope.CONSOLIDATED,
            period_type=p_type,
            fiscal_year=year,
            fiscal_quarter=quarter,
            fiscal_year_start_date=date(year, 1, 1),
            period_start_date=p_start,
            period_end_date=p_end,
            published_at=None,  # Upgraded via Layer 1 disclosure enrichment
            audit_status=audit_status,
            audit_opinion=audit_opinion,
        )
        filings.append(
            RawFiling(metadata=meta, metrics=metrics, locator=f"/data/{idx}")
        )

    archive.store(
        RawFilingBundle(
            payload=payload,
            filings=tuple(filings),
            pagination_status="COMPLETE",
        )
    )


def _build_offline_disclosure_payloads(
    ticker: str,
) -> tuple[bytes, str, bytes, str]:
    """Generate offline Vietcap IQ JSON and CafeF BCTC HTML covering 2024..2026-Q3."""
    vci_url = f"https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/{ticker}/financial-statement?section=BALANCE_SHEET"
    cafef_url = f"https://s.cafef.vn/Ajax/CongTy/BaoCaoTaiChinh.aspx?sym={ticker}"

    vci_years: list[dict[str, object]] = [
        {
            "organCode": ticker,
            "ticker": ticker,
            "yearReport": year,
            "lengthReport": 5,
            "publicDate": f"{year + 1}-03-20T00:00:00",
        }
        for year in (2024, 2025)
    ]
    vci_quarters: list[dict[str, object]] = []
    for year in (2024, 2025):
        for q in (1, 2, 3, 4):
            _, _, (pm, pd) = _QUARTER_DATES[q]
            # Simulate Vietcap overwriting Q4 publicDate with the March Annual date
            pub_date = f"{year + 1}-03-20" if q == 4 else f"{year}-{pm:02d}-{pd:02d}"
            vci_quarters.append(
                {
                    "organCode": ticker,
                    "ticker": ticker,
                    "yearReport": year,
                    "lengthReport": q,
                    "publicDate": f"{pub_date}T00:00:00",
                }
            )
    for q in (1, 2, 3):
        _, _, (pm, pd) = _QUARTER_DATES[q]
        vci_quarters.append(
            {
                "organCode": ticker,
                "ticker": ticker,
                "yearReport": 2026,
                "lengthReport": q,
                "publicDate": f"2026-{pm:02d}-{pd:02d}T00:00:00",
            }
        )

    vci_bytes = json.dumps(
        {
            "status": 200,
            "successful": True,
            "data": {"years": vci_years, "quarters": vci_quarters},
        },
        separators=(",", ":"),
    ).encode("utf-8")

    # CafeF HTML provides earlier Q4 unaudited PDF dates (including 2024-Q4 Tet rollover
    # on 2025-01-30) and an UNAUDITED 2025 Annual draft that Audit-Tier PIT Guard must NOT
    # allow to overwrite the AUDITED Vietcap Annual date.
    cafef_html = f"""
    <table>
      <tr>
        <td>BCTC hợp nhất quý 4 năm 2024</td>
        <td>Q4/2024</td>
        <td><a href="https://cafef1.mediacdn.vn/data/20250130_{ticker}_BCTC_HN_Q4_2024.pdf">PDF</a></td>
      </tr>
      <tr>
        <td>BCTC hợp nhất quý 4 năm 2025</td>
        <td>Q4/2025</td>
        <td><a href="https://cafef1.mediacdn.vn/data/20260125_{ticker}_BCTC_HN_Q4_2025.pdf">PDF</a></td>
      </tr>
      <tr>
        <td>BCTC hợp nhất năm 2025 (tự lập)</td>
        <td>CN/2025</td>
        <td><a href="https://cafef1.mediacdn.vn/data/20260126_{ticker}_BCTC_HN_2025_TULAP.pdf">PDF</a></td>
      </tr>
    </table>
    """.encode()
    return vci_bytes, vci_url, cafef_html, cafef_url


def _seed_offline_tt200_tt99_universe(
    data_root: Path,
    *,
    revenue_boost: float = 1.0,
    issuers_name_suffix: str = "",
) -> tuple[StateStore, RawArchive, pl.DataFrame]:
    """Seed 6 corporate issuers (TT200 2024-2025 + TT99 2026-Q1..Q3 + real HPG CIP fixture)."""
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    archive = RawArchive(data_root / "raw", state)

    curated_dir = data_root / "curated"
    curated_dir.mkdir(parents=True, exist_ok=True)
    issuers_df = _build_issuers_frame(name_suffix=issuers_name_suffix)
    issuers_df.write_parquet(curated_dir / "issuers.parquet")

    real_cip_bytes = FHSC_CIP_FIXTURE.read_bytes()
    real_cip_payload = json.loads(real_cip_bytes.decode("utf-8"))
    real_cip_records: list[dict[str, object]] = list(real_cip_payload["data"])
    real_cip_by_key: dict[tuple[int, int], dict[str, object]] = {}
    for rec in real_cip_records:
        raw_y = rec["year"]
        raw_q = rec["quarter"]
        assert isinstance(raw_y, int) and isinstance(raw_q, int)
        real_cip_by_key[(raw_y, raw_q)] = rec

    fetched_at = datetime(2026, 11, 1, 8, 0, 0, tzinfo=UTC)

    for profile in _OFFLINE_PROFILES:
        bs_annual: list[dict[str, object]] = []
        bs_quarterly: list[dict[str, object]] = []
        is_annual: list[dict[str, object]] = []
        is_quarterly: list[dict[str, object]] = []
        cf_annual: list[dict[str, object]] = []
        cf_quarterly: list[dict[str, object]] = []

        for year in (2024, 2025):
            growth = 1.0 if year == 2024 else (1.02 if profile.frozen_cip else 1.12)
            if profile.use_real_cip_fixture and year == 2025:
                # Match real HPG 2025-Q4 balance sheet scale (~258T VND) for annual 2025
                annual_hpg_bs = dict(real_cip_by_key[(2025, 4)])
                annual_hpg_bs["quarter"] = None
                bs_annual.append(annual_hpg_bs)
            else:
                bs_annual.append(
                    _synthetic_bs_record(
                        profile, year=year, quarter=None, growth=growth
                    )
                )
            is_annual.append(
                _synthetic_is_record(
                    profile,
                    year=year,
                    quarter=None,
                    growth=growth,
                    divisor=1.0,
                    revenue_boost=revenue_boost,
                )
            )
            cf_annual.append(
                _synthetic_cf_record(
                    profile, year=year, quarter=None, growth=growth, divisor=1.0
                )
            )

            for q in (1, 2, 3, 4):
                if profile.use_real_cip_fixture and (year, q) in real_cip_by_key:
                    continue  # Archived verbatim from fhsc_cip_sample.json below
                bs_quarterly.append(
                    _synthetic_bs_record(profile, year=year, quarter=q, growth=growth)
                )
                is_quarterly.append(
                    _synthetic_is_record(
                        profile,
                        year=year,
                        quarter=q,
                        growth=growth,
                        divisor=4.0,
                        revenue_boost=revenue_boost,
                    )
                )
                cf_quarterly.append(
                    _synthetic_cf_record(
                        profile, year=year, quarter=q, growth=growth, divisor=4.0
                    )
                )

        # 2026-Q1..Q3 under VAS_TT99_2025 (fiscal_year_start_date = 2026-01-01)
        growth_2026 = 1.03 if profile.frozen_cip else 1.18
        for q in (1, 2, 3):
            if not (profile.use_real_cip_fixture and (2026, q) in real_cip_by_key):
                bs_quarterly.append(
                    _synthetic_bs_record(
                        profile, year=2026, quarter=q, growth=growth_2026
                    )
                )
            is_quarterly.append(
                _synthetic_is_record(
                    profile,
                    year=2026,
                    quarter=q,
                    growth=growth_2026,
                    divisor=4.0,
                    revenue_boost=revenue_boost,
                )
            )
            cf_quarterly.append(
                _synthetic_cf_record(
                    profile, year=2026, quarter=q, growth=growth_2026, divisor=4.0
                )
            )

        # Also add HPG's 2025-Q3..Q4 IS & CF (since the skip above only bypassed BS)
        if profile.use_real_cip_fixture:
            for q in (3, 4):
                is_quarterly.append(
                    _synthetic_is_record(
                        profile,
                        year=2025,
                        quarter=q,
                        growth=1.12,
                        divisor=4.0,
                        revenue_boost=revenue_boost,
                    )
                )
                cf_quarterly.append(
                    _synthetic_cf_record(
                        profile, year=2025, quarter=q, growth=1.12, divisor=4.0
                    )
                )
            # Store verbatim real HPG fixture bytes covering 2025-Q3..2026-Q3
            _store_fhsc_page(
                archive,
                ticker=profile.ticker,
                statement_type=StatementType.BALANCE_SHEET,
                records=real_cip_records,
                fetched_at=fetched_at,
                raw_bytes_override=real_cip_bytes,
            )

        _store_fhsc_page(
            archive,
            ticker=profile.ticker,
            statement_type=StatementType.BALANCE_SHEET,
            records=bs_annual,
            fetched_at=fetched_at,
        )
        _store_fhsc_page(
            archive,
            ticker=profile.ticker,
            statement_type=StatementType.BALANCE_SHEET,
            records=bs_quarterly,
            fetched_at=fetched_at,
        )
        _store_fhsc_page(
            archive,
            ticker=profile.ticker,
            statement_type=StatementType.INCOME_STATEMENT,
            records=is_annual,
            fetched_at=fetched_at,
        )
        _store_fhsc_page(
            archive,
            ticker=profile.ticker,
            statement_type=StatementType.INCOME_STATEMENT,
            records=is_quarterly,
            fetched_at=fetched_at,
        )
        _store_fhsc_page(
            archive,
            ticker=profile.ticker,
            statement_type=StatementType.CASH_FLOW,
            records=cf_annual,
            fetched_at=fetched_at,
        )
        _store_fhsc_page(
            archive,
            ticker=profile.ticker,
            statement_type=StatementType.CASH_FLOW,
            records=cf_quarterly,
            fetched_at=fetched_at,
        )

    # Layer 1 Disclosure Enrichment (offline Vietcap IQ JSON + CafeF HTML)
    all_vci: list[DisclosureRecord] = []
    all_cafef: list[DisclosureRecord] = []
    evidence_payloads: dict[str, bytes] = {}
    period_end_dates: dict[DisclosureSlot, date] = {}
    for profile in _OFFLINE_PROFILES:
        vci_bytes, vci_url, cafef_bytes, cafef_url = _build_offline_disclosure_payloads(
            profile.ticker
        )
        evidence_payloads[vci_url] = vci_bytes
        evidence_payloads[cafef_url] = cafef_bytes
        all_vci.extend(
            parse_vietcap_iq_disclosures(
                profile.ticker,
                vci_bytes,
                source_url=vci_url,
                max_date=date(2026, 11, 15),
            )
        )
        all_cafef.extend(
            parse_cafef_bctc_disclosures(
                profile.ticker,
                cafef_bytes,
                source_url=cafef_url,
                max_date=date(2026, 11, 15),
            )
        )
        for year in (2024, 2025):
            period_end_dates[
                (
                    profile.ticker,
                    PeriodType.ANNUAL,
                    year,
                    None,
                    StatementScope.CONSOLIDATED,
                )
            ] = date(year, 12, 31)
            for q in (1, 2, 3, 4):
                _, (em, ed), _ = _QUARTER_DATES[q]
                period_end_dates[
                    (
                        profile.ticker,
                        PeriodType.QUARTER,
                        year,
                        q,
                        StatementScope.CONSOLIDATED,
                    )
                ] = date(year, em, ed)
        for q in (1, 2, 3):
            _, (em, ed), _ = _QUARTER_DATES[q]
            period_end_dates[
                (
                    profile.ticker,
                    PeriodType.QUARTER,
                    2026,
                    q,
                    StatementScope.CONSOLIDATED,
                )
            ] = date(2026, em, ed)

    merged = merge_disclosure_records(
        all_vci, all_cafef, period_end_dates=period_end_dates
    )
    # Audit-Tier PIT Guard check: UNAUDITED CafeF 2025 annual draft (2026-01-26) MUST NOT
    # replace Vietcap AUDITED 2025 annual date (2026-03-20), whereas Q4 CafeF earlier date wins.
    fpt_annual_2025 = merged[
        ("FPT", PeriodType.ANNUAL, 2025, None, StatementScope.CONSOLIDATED)
    ]
    assert fpt_annual_2025.published_at == date(2026, 3, 20)
    fpt_q4_2024 = merged[
        ("FPT", PeriodType.QUARTER, 2024, 4, StatementScope.CONSOLIDATED)
    ]
    assert fpt_q4_2024.published_at == date(2025, 1, 30)

    summary = enrich_fhsc_manifest_disclosures(
        archive,
        list(merged.values()),
        evidence_payloads=evidence_payloads,
        fetched_at=datetime(2026, 11, 2, 8, 0, 0, tzinfo=UTC),
        run_id="run_disclosure_seed",
    )
    assert summary.upgraded_filings > 0
    assert summary.skipped_pre_period_end == 0

    return state, archive, issuers_df


def _publish_bundle_snapshot(
    *,
    data_root: Path,
    state: StateStore,
    bundle: TransformBundle,
    updated_quality_summary: pl.DataFrame,
    snapshot_id: str,
    run_id: str,
    backend: StorageBackendName,
    keep_snapshots: int = 2,
    min_snapshot_age_seconds: int = 0,
) -> str:
    """Publish all 10 mart tables atomically via DuckLakeStore or SnapshotPublisher."""
    latest_facts = build_latest_facts_table(bundle.clean_facts, as_of=bundle.as_of)
    filings_manifest = build_filings_manifest_snapshot(state, bundle.clean_facts)
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

    state.start_run(
        run_id,
        "E2E_TEST",
        {"source": "fhsc", "backend": backend, "as_of": bundle.as_of.isoformat()},
        None,
    )
    try:
        if backend == "ducklake":
            store = DuckLakeStore(
                catalog_path=data_root / "metadata" / "ducklake_catalog.sqlite",
                data_path=data_root / "lakehouse" / "data",
                state_store=state,
                keep_snapshots=keep_snapshots,
                min_snapshot_age_seconds=min_snapshot_age_seconds,
            )
            store.publish_snapshot(snapshot_id, tables, run_id=run_id)
        else:
            publisher = SnapshotPublisher(
                data_root / "published" / "snapshots",
                state_store=state,
                keep_snapshots=keep_snapshots,
                min_snapshot_age_seconds=min_snapshot_age_seconds,
            )
            publisher.publish(snapshot_id, tables, run_id=run_id)
        state.finish_run(run_id, "SUCCEEDED", snapshot_id)
        write_transform_state(
            data_root / "metadata" / "_transform_state.json",
            bundle.next_transform_state,
            snapshot_id=snapshot_id,
        )
    except BaseException:
        state.finish_run(run_id, "FAILED")
        raise
    return snapshot_id


def _rollback_snapshot_for_backend(
    data_root: Path,
    state: StateStore,
    target_snapshot_id: str,
    *,
    backend: StorageBackendName,
) -> None:
    """Atomically switch ``active_snapshot`` on DuckLakeStore or Parquet+RWLock backend."""
    if backend == "ducklake":
        store = DuckLakeStore(
            catalog_path=data_root / "metadata" / "ducklake_catalog.sqlite",
            data_path=data_root / "lakehouse" / "data",
            state_store=state,
        )
        store.rollback_snapshot(target_snapshot_id, state)
        return

    snapshots_root = data_root / "published" / "snapshots"
    lock_path = state.path.with_name("pipeline.lock")
    with pipeline_write_lock(lock_path), snapshot_write_lock(snapshots_root):
        target_dir = snapshots_root / target_snapshot_id
        if not target_dir.is_dir():
            raise FileNotFoundError(target_dir)
        now = datetime.now(UTC).isoformat()
        with state.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT run_id FROM pipeline_runs WHERE published_snapshot_id = ? "
                "ORDER BY snapshot_publish_order DESC LIMIT 1",
                (target_snapshot_id,),
            ).fetchone()
            assert row is not None, f"Unknown Parquet snapshot {target_snapshot_id}"
            conn.execute(
                """
                INSERT INTO active_snapshot (singleton, snapshot_id, run_id, published_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    snapshot_id = excluded.snapshot_id,
                    run_id = excluded.run_id,
                    published_at = excluded.published_at
                """,
                (target_snapshot_id, str(row[0]), now),
            )


def _load_migration_module() -> ModuleType:
    """Dynamically load ``scripts/migrate_universe_to_v2.py`` without mutating sys.path."""
    assert MIGRATION_SCRIPT_PATH.is_file(), (
        f"Expected migration script at {MIGRATION_SCRIPT_PATH}"
    )
    module_name = "finintel_migrate_universe_to_v2_e2e"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, MIGRATION_SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


# ==============================================================================
# Suite 1 — Happy Path Full Stack (TT200 + TT99 + BS_CIP_LONG_TERM 242 -> 252
#           + All 10 HTTP Routes + 38/38 Lineage + Curated GC + Rollback)
# ==============================================================================


def test_suite1_happy_path_full_stack_all_10_routes_gc_and_rollback(
    tmp_path: Path,
) -> None:
    """Offline full-stack E2E from raw TT200/TT99 ingest through all 10 FastAPI routes, GC, and rollback."""
    backend = _select_backend()
    data_root = tmp_path / "data"
    as_of = date(2026, 11, 15)

    # 1. Explicit VAS circular mapping proof: BS_CIP_LONG_TERM maps TT200 code 242 -> TT99 code 252,
    #    and TT99 code 242 (Investment Property accumulated depreciation) is NOT aliased to CIP.
    mapper = TaxonomyMapper(CONFIG_DIR)
    tt200_meta = FilingMetadata(
        issuer_id="HPG",
        ticker_at_filing="HPG",
        statement_type=StatementType.BALANCE_SHEET,
        statement_scope=StatementScope.CONSOLIDATED,
        period_type=PeriodType.ANNUAL,
        fiscal_year=2025,
        fiscal_quarter=None,
        fiscal_year_start_date=date(2025, 1, 1),
        period_start_date=date(2025, 1, 1),
        period_end_date=date(2025, 12, 31),
        published_at=date(2026, 3, 20),
    )
    tt99_meta = FilingMetadata(
        issuer_id="HPG",
        ticker_at_filing="HPG",
        statement_type=StatementType.BALANCE_SHEET,
        statement_scope=StatementScope.CONSOLIDATED,
        period_type=PeriodType.QUARTER,
        fiscal_year=2026,
        fiscal_quarter=3,
        fiscal_year_start_date=date(2026, 1, 1),
        period_start_date=date(2026, 7, 1),
        period_end_date=date(2026, 9, 30),
        published_at=date(2026, 10, 20),
    )
    cip_facts, cip_unmapped = mapper.map_batch(
        [
            FilingMappingInput(
                filing_id="fid_tt200_cip",
                source_provider="hose_pdf_extract",
                revision_no=1,
                unit_multiplier=1,
                cash_flow_method=None,
                period=tt200_meta,
                filing=tt200_meta,
                taxonomy=TaxonomyVersion.TT200,
                metrics={"242": 10_721_076_531_961.0},
            ),
            FilingMappingInput(
                filing_id="fid_tt99_cip",
                source_provider="hose_pdf_extract",
                revision_no=1,
                unit_multiplier=1,
                cash_flow_method=None,
                period=tt99_meta,
                filing=tt99_meta,
                taxonomy=TaxonomyVersion.TT99,
                metrics={"252": 16_113_318_372_418.0, "242": -4_752_726_625.0},
            ),
        ]
    )
    cip_rows = cip_facts.filter(pl.col("metric_id") == "BS_CIP_LONG_TERM").sort(
        "taxonomy_version"
    )
    assert cip_rows["taxonomy_version"].to_list() == [
        "VAS_TT200_2014",
        "VAS_TT99_2025",
    ]
    assert cip_rows["value"].to_list() == [
        pytest.approx(10_721_076_531_961.0),
        pytest.approx(16_113_318_372_418.0),
    ]
    assert cip_unmapped.filter(pl.col("raw_metric_key") == "242").height == 1

    # 2. Seed offline 6-issuer TT200 + TT99 FHSC universe + real HPG CIP fixture + disclosures.
    state, _archive, issuers_df = _seed_offline_tt200_tt99_universe(data_root)

    # 3. Layer 2 Full Normalize + 0-dirty Incremental Normalize early cutoff.
    ff_root = data_root / "curated" / "financial_facts"
    norm_1_dir = ff_root / "run_e2e_1"
    norm_1 = normalize_archive_incremental(
        state,
        data_root / "raw",
        CONFIG_DIR,
        norm_1_dir,
        source="fhsc",
    )
    assert norm_1.full_rebuild is True
    assert norm_1.recomputed_issuers == len(_OFFLINE_PROFILES)
    assert norm_1.facts_rows > 0

    norm_2_dir = ff_root / "run_e2e_2"
    norm_2 = normalize_archive_incremental(
        state,
        data_root / "raw",
        CONFIG_DIR,
        norm_2_dir,
        source="fhsc",
        previous_batch_dir=norm_1_dir,
    )
    assert norm_2.full_rebuild is False
    assert norm_2.recomputed_issuers == 0
    assert norm_2.facts_rows == norm_1.facts_rows

    facts_df = pl.read_parquet(norm_2.facts_path)
    taxonomies_present = set(facts_df["taxonomy_version"].unique().to_list())
    assert {"VAS_TT200_2014", "VAS_TT99_2025"}.issubset(taxonomies_present)
    # Verify real HPG BS_CIP_LONG_TERM values survived full ingest -> enrich -> normalize across regimes
    hpg_cip = facts_df.filter(
        (pl.col("issuer_id") == "HPG")
        & (pl.col("metric_id") == "BS_CIP_LONG_TERM")
        & (pl.col("period_type") == "QUARTER")
        & (pl.col("period_end_date").is_in([date(2025, 12, 31), date(2026, 9, 30)]))
    ).sort("period_end_date")
    assert hpg_cip["taxonomy_version"].to_list() == [
        "VAS_TT200_2014",
        "VAS_TT99_2025",
    ]
    assert hpg_cip["value"].to_list() == [
        pytest.approx(10_721_076_531_961.0),
        pytest.approx(16_113_318_372_418.0),
    ]

    # 4. Native Polars Incremental Transform + Blocking QG1/QG2 Audits.
    bundle_1 = run_incremental_analytics_transform(
        facts_df,
        issuers_df,
        as_of=as_of,
        run_id="run_e2e_1",
        quality_config=QualityGateConfig(max_critical_issuer_ratio=0.10),
    )
    assert bundle_1.audit_quarantine.is_empty()
    assert bundle_1.critical_issuers == 0
    assert bundle_1.diff.full_rebuild is True

    # Confirm 2026 TT99 quarterly ratios and forensic red flags (RF_08 & RF_09) are computed
    tt99_q3 = bundle_1.financial_ratios.filter(
        (pl.col("taxonomy_version") == "VAS_TT99_2025")
        & (pl.col("period_type") == "QUARTER")
        & (pl.col("period_end_date") == date(2026, 9, 30))
    )
    assert tt99_q3.height == len(_OFFLINE_PROFILES)
    for col in ("ebit_ttm", "ebitda_ttm", "roic", "interest_coverage", "roe_total"):
        assert tt99_q3[col].null_count() == 0, f"Unexpected NULL in 2026-Q3 {col}"
    flag_codes = set(bundle_1.accounting_red_flags["flag_code"].unique().to_list())
    assert "RF_08_NON_CORE_EARNINGS_DEPENDENCE" in flag_codes
    assert "RF_09_FROZEN_CIP_CAPITALIZATION" in flag_codes

    updated_summary_1 = run_blocking_publish_audits(bundle_1, run_id="run_e2e_1")

    # 5. Atomic Snapshot Publish (snap_e2e_v1).
    from finintel.serving.freshness import format_composite_etag

    snap_1 = _publish_bundle_snapshot(
        data_root=data_root,
        state=state,
        bundle=bundle_1,
        updated_quality_summary=updated_summary_1,
        snapshot_id="snap_e2e_v1",
        run_id="run_e2e_1",
        backend=backend,
    )
    etag_1 = format_composite_etag(snap_1, data_root)

    # 6. FastAPI TestClient: test ALL 10 HTTP routes + RFC 9110 ETag/304 + 38/38 lineage.
    app = create_app(data_root=data_root)
    with TestClient(app) as client:
        # Helper verifying 200 + ETag + strong/weak If-None-Match -> 304 Not Modified
        def _assert_get_with_304(
            url: str, params: Mapping[str, Any] | None = None
        ) -> dict[str, Any]:
            r = client.get(url, params=params)
            assert r.status_code == 200, f"GET {url} failed ({r.status_code}): {r.text}"
            assert r.headers["ETag"] == etag_1
            assert r.headers["Cache-Control"] == "private, max-age=0, must-revalidate"
            for inm in (etag_1, f"W/{etag_1}"):
                r304 = client.get(url, params=params, headers={"If-None-Match": inm})
                assert r304.status_code == 304, f"Expected 304 for {url} with {inm}"
                assert r304.content == b""
                assert r304.headers["ETag"] == etag_1
            payload: dict[str, Any] = r.json()
            return payload

        # Route 1: GET /api/v1/health
        health = _assert_get_with_304("/api/v1/health")
        assert health == {
            "status": "ok",
            "active_snapshot_id": snap_1,
            "storage_backend": backend,
            "rag_available": False,
        }

        # Route 2: GET /api/v1/snapshot/overview
        overview = _assert_get_with_304("/api/v1/snapshot/overview")
        assert overview["snapshot_id"] == snap_1
        assert overview["storage_backend"] == backend
        assert overview["total_issuers"] == len(_OFFLINE_PROFILES)
        assert overview["quarantined_rows"] == 0
        assert overview["pass_periods"] > 0

        # Route 3: GET /api/v1/issuers
        issuers_page = _assert_get_with_304(
            "/api/v1/issuers", {"limit": 50, "offset": 0}
        )
        assert issuers_page["snapshot_id"] == snap_1
        assert issuers_page["total"] == len(_OFFLINE_PROFILES)
        assert {item["ticker"] for item in issuers_page["items"]} == {
            p.ticker for p in _OFFLINE_PROFILES
        }

        # Route 4: GET /api/v1/companies/{ticker}/profile (TT99 latest taxonomy verified on HPG)
        hpg_profile = _assert_get_with_304(
            "/api/v1/companies/HPG/profile",
            {"statement_scope": "CONSOLIDATED", "period_view": "TTM"},
        )
        assert hpg_profile["snapshot_id"] == snap_1
        assert hpg_profile["ticker"] == "HPG"
        assert hpg_profile["latest_taxonomy_version"] == "VAS_TT99_2025"
        assert hpg_profile["latest_quality_status"] == "PASS"
        assert len(hpg_profile["growth_series"]) > 0
        assert len(hpg_profile["capital_structure_series"]) > 0
        assert len(hpg_profile["dupont_series"]) > 0
        assert len(hpg_profile["core_earnings_series"]) > 0
        assert len(hpg_profile["ratios_table"]) > 0

        # Route 5: GET /api/v1/companies/{ticker}/benchmarks
        benchmarks = _assert_get_with_304(
            "/api/v1/companies/HPG/benchmarks",
            {
                "metric_name": "roe_total",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": "2025-12-31",
                "include_history": "true",
            },
        )
        assert benchmarks["snapshot_id"] == snap_1
        assert benchmarks["peer_count"] >= 2
        assert benchmarks["company_value"] is not None
        assert benchmarks["peer_median"] is not None
        assert len(benchmarks["peer_ranking"]) >= 2
        assert len(benchmarks["history"]) > 0

        # Route 6: GET /api/v1/companies/{ticker}/lineage across ALL 38/38 ALL_RATIOS
        assert len(ALL_RATIOS) == 38
        for idx, ratio_name in enumerate(ALL_RATIOS):
            params = {
                "ratio_name": ratio_name,
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": "2025-12-31",
            }
            if idx == 0:
                card = _assert_get_with_304("/api/v1/companies/FPT/lineage", params)
            else:
                r_lin = client.get("/api/v1/companies/FPT/lineage", params=params)
                assert r_lin.status_code == 200, (
                    f"Lineage failed for {ratio_name}: {r_lin.text}"
                )
                assert r_lin.headers["ETag"] == etag_1
                card = r_lin.json()
            assert card["orphan_filing_ids"] == [], (
                f"Orphan filing IDs for {ratio_name}: {card['orphan_filing_ids']}"
            )
            assert card["ratio_value"] is not None, f"NULL ratio_value for {ratio_name}"
            assert card["anchor_filing_id"] in card["source_filing_ids"]
            assert len(card["numerator_components"]) > 0
            assert len(card["denominator_components"]) > 0
            assert card["formula"]

        # Verify TT99 (2026-Q3) lineage also resolves with 0 orphans
        tt99_lin = client.get(
            "/api/v1/companies/HPG/lineage",
            params={
                "ratio_name": "roe_total",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": "2026-09-30",
            },
        )
        assert tt99_lin.status_code == 200
        tt99_card = tt99_lin.json()
        assert tt99_card["taxonomy_version"] == "VAS_TT99_2025"
        assert tt99_card["orphan_filing_ids"] == []
        assert tt99_card["ratio_value"] is not None

        # Route 7: GET /api/v1/screener
        screener = _assert_get_with_304(
            "/api/v1/screener",
            {
                "period_type": "QUARTER",
                "period_end_date": "2025-12-31",
                "statement_scope": "CONSOLIDATED",
                "limit": 50,
                "offset": 0,
            },
        )
        assert screener["snapshot_id"] == snap_1
        assert screener["total"] == len(_OFFLINE_PROFILES)
        assert len(screener["items"]) == len(_OFFLINE_PROFILES)

        # Route 8: POST /api/v1/stress-test (IMF combined shock)
        r_stress = client.post(
            "/api/v1/stress-test",
            json={
                "scenario_name": "IMF Combined Shock E2E",
                "statement_scope": "CONSOLIDATED",
                "period_type": "QUARTER",
                "period_end_date": "2025-12-31",
                "interest_rate_shock_bps": 300.0,
                "revenue_shock_pct": -0.20,
                "gross_margin_shock_pp": -0.03,
                "dso_shock_days": 30.0,
                "dio_shock_days": 30.0,
                "top_n": 20,
            },
        )
        assert r_stress.status_code == 200, r_stress.text
        assert r_stress.headers["Cache-Control"] == "private, no-store"
        assert r_stress.headers["ETag"] == etag_1
        stress = r_stress.json()
        assert stress["snapshot_id"] == snap_1
        assert stress["total_issuers_evaluated"] == len(_OFFLINE_PROFILES)
        assert stress["total_debt_sum"] > 0.0
        assert (
            stress["stressed_debt_at_risk_pct"] >= stress["baseline_debt_at_risk_pct"]
        )
        assert stress["newly_distressed_count"] >= 1
        assert len(stress["sector_summary"]) >= 2

        # Route 9: GET /api/v1/quality/summary
        q_sum = _assert_get_with_304(
            "/api/v1/quality/summary", {"limit": 50, "offset": 0}
        )
        assert q_sum["snapshot_id"] == snap_1
        assert q_sum["total"] > 0
        assert len(q_sum["items"]) > 0

        # Route 10: GET /api/v1/quality/quarantine
        q_quar = _assert_get_with_304(
            "/api/v1/quality/quarantine", {"limit": 50, "offset": 0}
        )
        assert q_quar["snapshot_id"] == snap_1
        assert q_quar["total"] == 0
        assert q_quar["items"] == []

    # 7. Publish snap_2 & snap_3 (3 total runs) -> Curated GC (keep_runs=2) + Rollback.
    norm_3_dir = ff_root / "run_e2e_3"
    normalize_archive_incremental(
        state,
        data_root / "raw",
        CONFIG_DIR,
        norm_3_dir,
        source="fhsc",
        previous_batch_dir=norm_2_dir,
    )
    assert len([p for p in ff_root.iterdir() if p.is_dir()]) == 3

    issuers_v2 = _build_issuers_frame(name_suffix=" v2")
    bundle_2 = run_incremental_analytics_transform(
        facts_df,
        issuers_v2,
        as_of=as_of,
        run_id="run_e2e_2",
        previous_ratios=bundle_1.financial_ratios,
        previous_benchmarks=bundle_1.industry_benchmarks,
        previous_red_flags=bundle_1.accounting_red_flags,
        previous_state={**bundle_1.next_transform_state, "snapshot_id": snap_1},
        previous_snapshot_id=snap_1,
    )
    summary_2 = run_blocking_publish_audits(bundle_2, run_id="run_e2e_2")
    snap_2 = _publish_bundle_snapshot(
        data_root=data_root,
        state=state,
        bundle=bundle_2,
        updated_quality_summary=summary_2,
        snapshot_id="snap_e2e_v2",
        run_id="run_e2e_2",
        backend=backend,
    )

    issuers_v3 = _build_issuers_frame(name_suffix=" v3")
    bundle_3 = run_incremental_analytics_transform(
        facts_df,
        issuers_v3,
        as_of=as_of,
        run_id="run_e2e_3",
        previous_ratios=bundle_2.financial_ratios,
        previous_benchmarks=bundle_2.industry_benchmarks,
        previous_red_flags=bundle_2.accounting_red_flags,
        previous_state={**bundle_2.next_transform_state, "snapshot_id": snap_2},
        previous_snapshot_id=snap_2,
    )
    summary_3 = run_blocking_publish_audits(bundle_3, run_id="run_e2e_3")
    snap_3 = _publish_bundle_snapshot(
        data_root=data_root,
        state=state,
        bundle=bundle_3,
        updated_quality_summary=summary_3,
        snapshot_id="snap_e2e_v3",
        run_id="run_e2e_3",
        backend=backend,
    )

    if backend == "ducklake":
        dl_store = DuckLakeStore(
            catalog_path=data_root / "metadata" / "ducklake_catalog.sqlite",
            data_path=data_root / "lakehouse" / "data",
            state_store=state,
            keep_snapshots=2,
            min_snapshot_age_seconds=0,
        )
        maint = dl_store.maintain(keep_snapshots=2, min_snapshot_age_seconds=0)
        assert isinstance(maint["remaining_versions"], list)
        assert len(maint["remaining_versions"]) >= 2

    removed_curated = collect_curated_garbage(
        data_root,
        state,
        keep_runs=2,
        min_age_seconds=0,
        keep_snapshots=2,
    )
    assert any(p.name == "run_e2e_1" for p in removed_curated)
    remaining_ff_dirs = sorted(p.name for p in ff_root.iterdir() if p.is_dir())
    assert remaining_ff_dirs == ["run_e2e_2", "run_e2e_3"]
    assert (data_root / "curated" / "issuers.parquet").is_file()

    # 8. Rollback back (snap_3 -> snap_2) and forward (snap_2 -> snap_3) verifying atomic switch.
    repo = ParquetRepository(data_root=data_root)
    assert repo.snapshot_id == snap_3

    _rollback_snapshot_for_backend(data_root, state, snap_2, backend=backend)
    assert ParquetRepository(data_root=data_root).snapshot_id == snap_2
    assert ParquetRepository(data_root=data_root).read_table(
        "issuers.parquet", filters={"issuer_id": "FPT"}
    )["company_name_vn"].to_list() == ["Công ty Cổ phần FPT v2"]

    with TestClient(create_app(data_root=data_root)) as rollback_client:
        r_rb = rollback_client.get("/api/v1/health")
        assert r_rb.status_code == 200
        assert r_rb.json()["active_snapshot_id"] == snap_2
        assert r_rb.headers["ETag"] == format_composite_etag(snap_2, data_root)

        _rollback_snapshot_for_backend(data_root, state, snap_3, backend=backend)
        assert ParquetRepository(data_root=data_root).snapshot_id == snap_3

        r_fw = rollback_client.get("/api/v1/health")
        assert r_fw.status_code == 200
        assert r_fw.json()["active_snapshot_id"] == snap_3
        assert r_fw.headers["ETag"] == format_composite_etag(snap_3, data_root)
        r_prof_fw = rollback_client.get("/api/v1/companies/FPT/profile")
        assert r_prof_fw.status_code == 200
        assert r_prof_fw.json()["company_name_vn"] == "Công ty Cổ phần FPT v3"


# ==============================================================================
# Suite 2 — Failing QUALITY_GATE_1 Circuit-Breaker E2E
# ==============================================================================


def test_suite2_failing_qg1_circuit_breaker_preserves_live_fastapi_snapshot(
    tmp_path: Path,
) -> None:
    """Injecting >10% CRITICAL issuers aborts BEFORE publish while prior FastAPI snapshot stays intact."""
    backend = _select_backend()
    data_root = tmp_path / "data"
    as_of = date(2026, 11, 15)

    state, _archive, issuers_df = _seed_offline_tt200_tt99_universe(data_root)
    norm = normalize_archive_incremental(
        state,
        data_root / "raw",
        CONFIG_DIR,
        data_root / "curated" / "financial_facts" / "run_cb_healthy",
        source="fhsc",
    )
    healthy_facts = pl.read_parquet(norm.facts_path)

    healthy_bundle = run_incremental_analytics_transform(
        healthy_facts,
        issuers_df,
        as_of=as_of,
        run_id="run_cb_healthy",
        quality_config=QualityGateConfig(max_critical_issuer_ratio=0.10),
    )
    healthy_summary = run_blocking_publish_audits(
        healthy_bundle, run_id="run_cb_healthy"
    )
    from finintel.serving.freshness import format_composite_etag

    snap_healthy = _publish_bundle_snapshot(
        data_root=data_root,
        state=state,
        bundle=healthy_bundle,
        updated_quality_summary=healthy_summary,
        snapshot_id="snap_cb_healthy_v1",
        run_id="run_cb_healthy",
        backend=backend,
    )
    etag_healthy = format_composite_etag(snap_healthy, data_root)

    with TestClient(create_app(data_root=data_root)) as client:
        r_before = client.get("/api/v1/snapshot/overview")
        assert r_before.status_code == 200
        assert r_before.headers["ETag"] == etag_healthy
        assert r_before.json()["snapshot_id"] == snap_healthy
        assert r_before.json()["quarantined_rows"] == 0

        # Corrupt 2 of 6 issuers (33.3% > 10% threshold) with severe BS_IDENTITY_MISMATCH
        corrupted_facts = healthy_facts.with_columns(
            pl.when(
                pl.col("issuer_id").is_in(["CMG", "REE"])
                & (pl.col("metric_id") == "BS_TOTAL_ASSETS")
                & (pl.col("period_type") == "ANNUAL")
                & (pl.col("fiscal_year") == 2025)
            )
            .then(pl.col("value") * 1.65)
            .otherwise(pl.col("value"))
            .alias("value")
        )

        # Must trip QUALITY_GATE_1 circuit breaker BEFORE any publish step is reached
        with pytest.raises(CriticalDataQualityError):
            run_incremental_analytics_transform(
                corrupted_facts,
                issuers_df,
                as_of=as_of,
                run_id="run_cb_corrupted",
                quality_config=QualityGateConfig(max_critical_issuer_ratio=0.10),
            )

        # Prior healthy snapshot MUST remain active and serve 100% intact with unchanged ETag
        assert ParquetRepository(data_root=data_root).snapshot_id == snap_healthy
        active_row = state.active_snapshot()
        assert active_row is not None
        assert active_row["snapshot_id"] == snap_healthy

        r_health = client.get("/api/v1/health")
        assert r_health.status_code == 200
        assert r_health.headers["ETag"] == etag_healthy
        assert r_health.json()["active_snapshot_id"] == snap_healthy

        r_304 = client.get(
            "/api/v1/snapshot/overview", headers={"If-None-Match": etag_healthy}
        )
        assert r_304.status_code == 304
        assert r_304.headers["ETag"] == etag_healthy

        r_prof = client.get("/api/v1/companies/CMG/profile")
        assert r_prof.status_code == 200
        assert r_prof.headers["ETag"] == etag_healthy
        assert r_prof.json()["snapshot_id"] == snap_healthy


# ==============================================================================
# Suite 3 — Mode B REGULATORY_DISTRESS Offline Library Contract E2E
# ==============================================================================


def _build_mode_b_issuer_facts(
    issuer_id: str,
    idx: int,
    *,
    last_year_quarter: tuple[int, int] = (2024, 4),
) -> pl.DataFrame:
    """Build deterministic quarterly EXACT_FILING financial facts for Mode B ML E2E."""
    rows: list[dict[str, object]] = []
    for year in range(2017, 2025):
        for q in (1, 2, 3, 4):
            if (year, q) > last_year_quarter:
                continue
            (sm, sd), (em, ed), (pm, pd) = _QUARTER_DATES[q]
            p_start = date(year, sm, sd)
            p_end = date(year, em, ed)
            pub_date = date(year + 1 if q == 4 else year, pm, pd)
            scale = (1.0 + 0.04 * idx + 0.015 * q) * 10_000_000.0
            fid = f"fid_{issuer_id}_{year}_q{q}"
            metrics: dict[str, float] = {
                "BS_TOTAL_ASSETS": 1000.0 * scale,
                "BS_TOTAL_EQUITY": (460.0 - 10.0 * (idx % 6)) * scale,
                "BS_MINORITY_INTEREST": 12.0 * scale,
                "BS_TOTAL_LIABILITIES": (540.0 + 10.0 * (idx % 6)) * scale,
                "BS_CURRENT_ASSETS": (370.0 + 6.0 * (idx % 5)) * scale,
                "BS_CURRENT_LIABILITIES": (195.0 + 5.0 * (idx % 4)) * scale,
                "BS_CASH_AND_EQUIVALENTS": (85.0 + 4.0 * (idx % 5)) * scale,
                "BS_SHORT_TERM_BORROWINGS": (115.0 + 7.0 * (idx % 5)) * scale,
                "BS_LONG_TERM_BORROWINGS": (90.0 + 5.0 * (idx % 3)) * scale,
                "BS_RETAINED_EARNINGS": (160.0 - 6.0 * (idx % 6)) * scale,
                "BS_ORDINARY_SHARES_CAPITAL": 200_000.0 * 10_000_000.0,
                "BS_TREASURY_SHARES": 0.0,
                "BS_INVENTORIES": (68.0 + 3.0 * (idx % 5)) * scale,
                "BS_TRADE_RECEIVABLES_ST": (92.0 + 4.0 * (idx % 4)) * scale,
                "BS_TRADE_PAYABLES_ST": (62.0 + 3.0 * (idx % 3)) * scale,
                "BS_TANGIBLE_FIXED_ASSETS": 310.0 * scale,
                "IS_NET_REVENUE": (265.0 + 8.0 * (idx % 6)) * scale,
                "IS_COST_OF_GOODS_SOLD": (162.0 + 5.0 * (idx % 5)) * scale,
                "IS_GROSS_PROFIT": (103.0 + 3.0 * (idx % 6)) * scale,
                "IS_OPERATING_PROFIT": (44.0 - 2.0 * (idx % 5)) * scale,
                "IS_PROFIT_BEFORE_TAX": (40.0 - 2.0 * (idx % 5)) * scale,
                "IS_INTEREST_EXPENSE": (4.0 + 0.6 * (idx % 4)) * scale,
                "IS_CURRENT_INCOME_TAX": 7.2 * scale,
                "IS_DEFERRED_INCOME_TAX": 0.4 * scale,
                "IS_NET_INCOME": (32.4 - 2.0 * (idx % 5)) * scale,
                "IS_NET_INCOME_PARENT": (29.5 - 2.0 * (idx % 5)) * scale,
                "IS_SELLING_EXPENSES": 12.0 * scale,
                "IS_ADMINISTRATIVE_EXPENSES": 15.0 * scale,
                "CF_OPERATING_CASH_FLOW": (35.0 - 2.5 * (idx % 5)) * scale,
                "CF_CAPITAL_EXPENDITURE": -12.0 * scale,
                "CF_DEPRECIATION_AMORTIZATION": 8.0 * scale,
                "CF_EQUITY_ISSUANCE_PROCEEDS": 0.0,
            }
            for metric_id, val in metrics.items():
                rows.append(
                    {
                        "issuer_id": issuer_id,
                        "statement_scope": "CONSOLIDATED",
                        "period_type": "QUARTER",
                        "fiscal_year_start_date": date(year, 1, 1),
                        "period_end_date": p_end,
                        "metric_id": metric_id,
                        "taxonomy_version": "VAS_TT200_2014",
                        "revision_no": 1,
                        "filing_id": fid,
                        "fiscal_year": year,
                        "fiscal_quarter": q,
                        "period_start_date": p_start,
                        "published_at": pub_date,
                        "published_at_basis": "EXACT_FILING",
                        "effective_available_at": pub_date,
                        "revision_reason": "ORIGINAL",
                        "derivation_method": "REPORTED_DIRECTLY",
                        "audit_status": "REVIEWED" if q == 2 else "UNAUDITED",
                        "audit_opinion": "NOT_APPLICABLE",
                        "value": val,
                        "ttm_value": None,
                        "computation_note": None,
                        "source_provider": "fhsc",
                        "source_filing_ids": [fid],
                        "comparable_across_taxonomy": True,
                        "is_filed_late": False,
                    }
                )
    return pl.DataFrame(rows).with_columns(
        pl.col("fiscal_quarter").cast(pl.Int8),
        pl.col("revision_no").cast(pl.Int32),
        pl.col("fiscal_year").cast(pl.Int32),
        pl.col("ttm_value").cast(pl.Float64),
        pl.col("computation_note").cast(pl.String),
    )


def test_suite3_mode_b_regulatory_distress_offline_contract_e2e() -> None:
    """Verify Mode B offline library contract guards right-censoring and preserves FORCED_DELISTING y=1."""
    # Part A: Direct target-builder contract verification (right-censoring, forced delisting, future-event guard)
    period_rows: list[dict[str, object]] = []
    for issuer_id, max_yq in (
        ("DELIST_CO", (2021, 2)),  # Stops filing after 2021-Q2 due to forced delisting!
        ("HEALTHY_CO", (2022, 4)),
        ("FUTURE_EVT_CO", (2022, 4)),
    ):
        for year in (2020, 2021, 2022):
            for q in (1, 2, 3, 4):
                if (year, q) > max_yq:
                    continue
                _, (em, ed), (pm, pd) = _QUARTER_DATES[q]
                avail = date(year + 1 if q == 4 else year, pm, pd)
                period_rows.append(
                    {
                        "issuer_id": issuer_id,
                        "statement_scope": "CONSOLIDATED",
                        "period_type": "QUARTER",
                        "fiscal_year": year,
                        "fiscal_quarter": q,
                        "period_end_date": date(year, em, ed),
                        "accounting_regime": "CORPORATE",
                        "BS_TOTAL_EQUITY": 500.0,
                        "parent_equity": 480.0,
                        "interest_coverage": 5.0,
                        "CF_OPERATING_CASH_FLOW": 80.0,
                        "current_ratio": 2.2,
                        "roa": 0.08,
                        "_period_available_at": avail,
                        "_period_exact_filing": True,
                    }
                )
    periods_df = pl.DataFrame(period_rows)

    with pytest.raises(ContractViolationError, match="regulatory_events"):
        build_distress_targets(
            periods_df, mode="REGULATORY_DISTRESS", regulatory_events=None
        )

    as_of_cutoff = date(2022, 11, 1)
    events_df = pl.DataFrame(
        {
            "issuer_id": ["DELIST_CO", "FUTURE_EVT_CO"],
            # DELIST_CO delisted on 2021-08-15 within (2021-01-25, 2022-01-25]
            # FUTURE_EVT_CO event occurs AFTER as_of_cutoff -> MUST NOT leak!
            "event_date": [date(2021, 8, 15), date(2022, 12, 15)],
            "event_type": ["FORCED_DELISTING", "CONTROL"],
        }
    )
    targets = build_distress_targets(
        periods_df,
        mode="REGULATORY_DISTRESS",
        as_of=as_of_cutoff,
        regulatory_events=events_df,
    )

    # 1. Forced delisting preserves y = 1 even though post-delisting quarters t+3 and t+4 are absent,
    #    and B-01 locks label_available_at symmetrically to a_t + 365d (2022-01-25).
    delist_q4_2020 = targets.filter(
        (pl.col("issuer_id") == "DELIST_CO")
        & (pl.col("fiscal_year") == 2020)
        & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert delist_q4_2020["distress_target"] == 1
    assert delist_q4_2020["label_available_at"] == date(2022, 1, 25)
    assert delist_q4_2020["_label_exact_filing_basis"] is True

    # 2. Right-censoring lock on negative labels: HEALTHY_CO 2021-Q4 has a_t = 2022-01-25,
    #    so a_t + 365d = 2023-01-25 > as_of_cutoff (2022-11-01) -> MUST stay unlabeled (None)!
    healthy_q4_2021 = targets.filter(
        (pl.col("issuer_id") == "HEALTHY_CO")
        & (pl.col("fiscal_year") == 2021)
        & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert healthy_q4_2021["distress_target"] is None
    assert healthy_q4_2021["label_available_at"] is None

    # Earlier completed horizon (HEALTHY_CO 2020-Q4, a_t + 365d = 2022-01-25 <= 2022-11-01) -> y = 0
    healthy_q4_2020 = targets.filter(
        (pl.col("issuer_id") == "HEALTHY_CO")
        & (pl.col("fiscal_year") == 2020)
        & (pl.col("fiscal_quarter") == 4)
    ).row(0, named=True)
    assert healthy_q4_2020["distress_target"] == 0

    # 3. Future regulatory event after as_of_cutoff MUST NOT leak into 2021-Q3 (a_t=2021-10-20)
    future_q3_2021 = targets.filter(
        (pl.col("issuer_id") == "FUTURE_EVT_CO")
        & (pl.col("fiscal_year") == 2021)
        & (pl.col("fiscal_quarter") == 3)
    ).row(0, named=True)
    assert future_q3_2021["distress_target"] == 0

    # Part B: Full end-to-end build_ml_artifacts() under Mode B REGULATORY_DISTRESS
    # Include a forcibly delisted issuer (REG_04) whose financials stop after 2022-Q2!
    issuer_ids = [f"REG_{i:02d}" for i in range(12)]
    all_facts = pl.concat(
        [
            _build_mode_b_issuer_facts(
                iid,
                i,
                last_year_quarter=(2022, 2) if iid == "REG_04" else (2024, 4),
            )
            for i, iid in enumerate(issuer_ids)
        ]
    )
    issuers_df = pl.DataFrame(
        {
            "issuer_id": issuer_ids,
            "ticker": [f"R{i:02d}" for i in range(12)],
            "exchange": ["HOSE"] * 12,
            "company_name_vn": [f"Mode B Issuer {i:02d}" for i in range(12)],
            "accounting_regime": ["CORPORATE"] * 12,
            "icb_code_level2": ["2000" if i < 6 else "3000" for i in range(12)],
            "icb_name_level2": [
                "Industrial" if i < 6 else "Consumer" for i in range(12)
            ],
            "icb_code_level3": ["2010" if i < 6 else "3010" for i in range(12)],
            "icb_name_level3": ["Machinery" if i < 6 else "Food" for i in range(12)],
            "industry_snapshot_date": [date(2017, 1, 1)] * 12,
            "delisted_date": pl.Series(
                [date(2022, 8, 15) if iid == "REG_04" else None for iid in issuer_ids],
                dtype=pl.Date,
            ),
        }
    )
    reg_events = pl.DataFrame(
        {
            "issuer_id": [
                "REG_00",
                "REG_01",
                "REG_02",
                "REG_03",
                "REG_04",
                "REG_05",
            ],
            "event_date": [
                date(2021, 5, 10),
                date(2021, 6, 15),
                date(2021, 7, 20),
                date(2022, 6, 10),
                date(
                    2022, 8, 15
                ),  # FORCED_DELISTING with missing post-delisting quarters
                date(2024, 6, 15),
            ],
            "event_type": [
                "CONTROL",
                "TRADING_RESTRICTION",
                "SUSPENSION",
                "CONTROL",
                "FORCED_DELISTING",
                "ADVERSE_OR_DISCLAIMER_AUDIT",
            ],
        }
    )

    artifacts = build_ml_artifacts(
        all_facts,
        issuers_df,
        as_of=date(2025, 3, 1),
        validation_start=date(2021, 12, 1),
        test_start=date(2023, 3, 1),
        approved_target=True,
        require_exact_filing=True,
        target_mode="REGULATORY_DISTRESS",
        regulatory_events=reg_events,
    )
    assert artifacts.run_manifest["target_mode"] == "REGULATORY_DISTRESS"
    assert artifacts.evaluation_report["target_mode"] == "REGULATORY_DISTRESS"
    assert 0.0 < float(artifacts.evaluation_report["platt_slope"]) <= 10.0
    split_counts = artifacts.run_manifest["split_counts"]
    assert split_counts["train"]["positive_labels"] >= 3
    assert split_counts["validation"]["positive_labels"] >= 2
    assert split_counts["test"]["positive_labels"] >= 1
    assert not artifacts.scores_table.is_empty()
    probs = artifacts.scores_table["calibrated_distress_prob"].drop_nulls()
    assert probs.len() > 0
    assert bool(((probs >= 0.0) & (probs <= 1.0)).all())


# ==============================================================================
# Suite 4 — 8-Thread Concurrent Read + Live Publish Race Test
# ==============================================================================


def test_suite4_concurrent_8_readers_and_live_publish_race(tmp_path: Path) -> None:
    """8 reader threads querying FastAPI during a live snapshot publish see 0 errors, 0 skew, clean ETag switch."""
    backend = _select_backend()
    data_root = tmp_path / "data"
    as_of = date(2026, 11, 15)

    state, _archive, issuers_v1 = _seed_offline_tt200_tt99_universe(
        data_root, issuers_name_suffix=" RaceV1"
    )
    norm_v1 = normalize_archive_incremental(
        state,
        data_root / "raw",
        CONFIG_DIR,
        data_root / "curated" / "financial_facts" / "run_race_v1",
        source="fhsc",
    )
    facts_v1 = pl.read_parquet(norm_v1.facts_path)
    bundle_v1 = run_incremental_analytics_transform(
        facts_v1, issuers_v1, as_of=as_of, run_id="run_race_v1"
    )
    summary_v1 = run_blocking_publish_audits(bundle_v1, run_id="run_race_v1")
    snap_v1 = _publish_bundle_snapshot(
        data_root=data_root,
        state=state,
        bundle=bundle_v1,
        updated_quality_summary=summary_v1,
        snapshot_id="snap_race_v1",
        run_id="run_race_v1",
        backend=backend,
    )
    from finintel.serving.freshness import format_composite_etag

    etag_v1 = format_composite_etag(snap_v1, data_root)

    # Prepare v2 bundle upfront (updated issuer names AND scaled revenue facts to detect cross-table skew)
    issuers_v2 = _build_issuers_frame(name_suffix=" RaceV2")
    facts_v2 = facts_v1.with_columns(
        pl.when(pl.col("metric_id") == "IS_NET_REVENUE")
        .then(pl.col("value") * 1.05)
        .otherwise(pl.col("value"))
        .alias("value")
    )
    bundle_v2 = run_incremental_analytics_transform(
        facts_v2, issuers_v2, as_of=as_of, run_id="run_race_v2"
    )
    summary_v2 = run_blocking_publish_audits(bundle_v2, run_id="run_race_v2")
    snap_v2 = "snap_race_v2"
    etag_v2 = format_composite_etag(snap_v2, data_root)

    routes = (
        "/api/v1/snapshot/overview",
        "/api/v1/companies/FPT/profile?period_view=TTM",
        "/api/v1/screener?period_type=QUARTER&period_end_date=2025-12-31&limit=50",
    )

    with TestClient(create_app(data_root=data_root)) as client:
        r_init = client.get("/api/v1/health")
        assert r_init.status_code == 200
        assert r_init.headers["ETag"] == etag_v1

        readers_ready = threading.Barrier(9)  # 8 reader threads + 1 writer thread
        publish_done = threading.Event()
        errors: list[str] = []
        observed_snapshots: list[str] = []
        lock = threading.Lock()

        def _reader(worker_id: int) -> int:
            completed = 0
            try:
                # Warm one v1 read before signaling barrier
                warm = client.get("/api/v1/health")
                if warm.status_code != 200:
                    with lock:
                        errors.append(
                            f"worker {worker_id} warm status={warm.status_code}"
                        )
                readers_ready.wait(timeout=15.0)

                # Keep reading continuously across the live publish commit until publish_done
                # is set AND at least 8 post-publish reads finish (bounded by a 30s deadline).
                post_publish_reads = 0
                step = 0
                deadline = time.monotonic() + 30.0
                while time.monotonic() < deadline and (
                    not publish_done.is_set() or post_publish_reads < 8
                ):
                    url = routes[(worker_id + step) % len(routes)]
                    step += 1
                    resp = client.get(url)
                    if resp.status_code != 200:
                        with lock:
                            errors.append(
                                f"worker {worker_id} {url} status={resp.status_code} body={resp.text[:200]}"
                            )
                        continue
                    body = resp.json()
                    body_snap = body.get("snapshot_id")
                    hdr_etag = resp.headers.get("ETag")
                    expected_etag = (
                        format_composite_etag(str(body_snap), data_root)
                        if body_snap
                        else None
                    )
                    if body_snap not in {snap_v1, snap_v2}:
                        with lock:
                            errors.append(
                                f"worker {worker_id} unexpected snapshot_id={body_snap}"
                            )
                    if hdr_etag != expected_etag:
                        with lock:
                            errors.append(
                                f"worker {worker_id} ETag skew hdr={hdr_etag} vs body={body_snap}"
                            )
                    if "/profile" in url:
                        cname = str(body.get("company_name_vn", ""))
                        if body_snap == snap_v1 and not cname.endswith("RaceV1"):
                            with lock:
                                errors.append(
                                    f"worker {worker_id} cross-table skew v1 got {cname!r}"
                                )
                        if body_snap == snap_v2 and not cname.endswith("RaceV2"):
                            with lock:
                                errors.append(
                                    f"worker {worker_id} cross-table skew v2 got {cname!r}"
                                )
                    with lock:
                        if isinstance(body_snap, str):
                            observed_snapshots.append(body_snap)
                    completed += 1
                    if publish_done.is_set():
                        post_publish_reads += 1
                if not publish_done.is_set() or post_publish_reads < 8:
                    with lock:
                        errors.append(
                            f"worker {worker_id} timed out waiting for post-publish reads "
                            f"(publish_done={publish_done.is_set()}, post_reads={post_publish_reads})"
                        )
            except Exception as exc:  # pragma: no cover - captured for assertion
                with lock:
                    errors.append(
                        f"worker {worker_id} exception {type(exc).__name__}: {exc}"
                    )
            return completed

        def _writer() -> None:
            try:
                readers_ready.wait(timeout=15.0)
                # Tiny stagger so all 8 readers are actively mid-flight when publish commits
                time.sleep(0.02)
                _publish_bundle_snapshot(
                    data_root=data_root,
                    state=state,
                    bundle=bundle_v2,
                    updated_quality_summary=summary_v2,
                    snapshot_id=snap_v2,
                    run_id="run_race_v2",
                    backend=backend,
                )
            except Exception as exc:  # pragma: no cover - captured for assertion
                with lock:
                    errors.append(f"writer exception {type(exc).__name__}: {exc}")
            finally:
                publish_done.set()

        writer_thread = threading.Thread(target=_writer, daemon=True)
        writer_thread.start()
        with ThreadPoolExecutor(max_workers=8) as pool:
            counts = list(pool.map(_reader, range(8)))
        writer_thread.join(timeout=30.0)
        assert not writer_thread.is_alive(), "Concurrent writer thread timed out"

        assert errors == [], f"Concurrent read/publish race errors: {errors}"
        assert sum(counts) >= 64
        assert snap_v2 in observed_snapshots

        # Post-publish verification: ETag switched cleanly to snap_v2 and old ETag returns 200
        r_after = client.get(
            "/api/v1/companies/FPT/profile", headers={"If-None-Match": etag_v1}
        )
        assert r_after.status_code == 200
        assert r_after.headers["ETag"] == etag_v2
        assert r_after.json()["snapshot_id"] == snap_v2
        assert r_after.json()["company_name_vn"].endswith("RaceV2")

        r_304_v2 = client.get("/api/v1/health", headers={"If-None-Match": etag_v2})
        assert r_304_v2.status_code == 304
        assert r_304_v2.headers["ETag"] == etag_v2


# ==============================================================================
# Suite 5 — Migration Script WAL-Safe Backup & Dual SHA-256 Tamper Detection
# ==============================================================================


def test_suite5_migration_wal_safe_backup_and_dual_sha256_integrity(
    tmp_path: Path,
) -> None:
    """Verify sqlite3.Connection.backup() WAL safety, pre-cutover backup, and dual SHA-256 tamper fail-fast."""
    mod = _load_migration_module()

    # 1. WAL-safe backup: committed un-checkpointed WAL rows MUST appear in backup without mutating source
    wal_src = tmp_path / "wal_src" / "state.sqlite"
    wal_src.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(wal_src)
    try:
        src_conn.execute("PRAGMA journal_mode = WAL;")
        src_conn.execute("PRAGMA wal_autocheckpoint = 0;")
        src_conn.execute(
            "CREATE TABLE wal_probe (id INTEGER PRIMARY KEY, label TEXT NOT NULL)"
        )
        src_conn.execute("INSERT INTO wal_probe VALUES (1, 'before_wal')")
        src_conn.commit()
        src_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

        # Insert rows strictly into the -wal file without checkpointing
        src_conn.execute("INSERT INTO wal_probe VALUES (2, 'uncheckpointed_wal_row')")
        src_conn.execute("INSERT INTO wal_probe VALUES (3, 'second_wal_row')")
        src_conn.commit()

        wal_sidecar = Path(f"{wal_src}-wal")
        assert wal_sidecar.is_file() and wal_sidecar.stat().st_size > 0
        src_bytes_before = wal_src.read_bytes()

        wal_dst = tmp_path / "wal_dst" / "state.sqlite"
        backed_up = mod.backup_sqlite_wal_safe(wal_src, wal_dst, read_only_source=True)
        assert backed_up == wal_dst
        assert wal_dst.is_file()
        assert wal_src.read_bytes() == src_bytes_before
    finally:
        src_conn.close()

    verify_conn = sqlite3.connect(wal_dst)
    try:
        rows = verify_conn.execute(
            "SELECT id, label FROM wal_probe ORDER BY id"
        ).fetchall()
    finally:
        verify_conn.close()
    assert rows == [
        (1, "before_wal"),
        (2, "uncheckpointed_wal_row"),
        (3, "second_wal_row"),
    ]

    # 2. Pre-cutover backup: rejects backup_dir inside target_dir, succeeds outside target_dir
    existing_target = tmp_path / "target_data"
    target_state = StateStore(existing_target / "metadata" / "state.sqlite")
    target_state.initialize()
    target_state.start_run("run_prior_001", "MANUAL", {"source": "fhsc"}, None)
    snap_dir = existing_target / "published" / "snapshots" / "snap_prior_001"
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "_snapshot_meta.json").write_text(
        json.dumps({"snapshot_id": "snap_prior_001"}), encoding="utf-8"
    )
    target_state.publish_snapshot("run_prior_001", "snap_prior_001")

    with pytest.raises((ValueError, ContractViolationError)):
        mod.pre_cutover_backup(existing_target, existing_target / "inside_backup")

    outside_backup = tmp_path / "pre_cutover_backup_out"
    backup_info = mod.pre_cutover_backup(existing_target, outside_backup)
    assert backup_info["active_snapshot_id"] == "snap_prior_001"
    assert (outside_backup / "metadata" / "state.sqlite").is_file()

    # 3. Dual SHA-256 verification: PASS on clean archive, FAIL-FAST on tampered payload & canonical hash
    clean_source = tmp_path / "clean_universe"
    src_state = StateStore(clean_source / "metadata" / "state.sqlite")
    src_state.initialize()
    src_archive = RawArchive(clean_source / "raw", src_state)
    fetched_at = datetime(2026, 11, 10, 8, 0, 0, tzinfo=UTC)

    for ticker, ta_val in (
        ("FPT", 80_000_000_000_000.0),
        ("HPG", 250_000_000_000_000.0),
    ):
        _store_fhsc_page(
            src_archive,
            ticker=ticker,
            statement_type=StatementType.BALANCE_SHEET,
            records=[
                {
                    "year": 2025,
                    "quarter": 4,
                    "tongcongtaisan": ta_val,
                    "tongcongnguonvon": ta_val,
                    "chiphixaydungcobandodang": 5_000_000_000_000.0,
                }
            ],
            fetched_at=fetched_at,
        )

    (clean_source / "ingestion_progress.json").write_text(
        json.dumps(
            {
                "done": ["FPT", "HPG"],
                "skipped": {"HSG": "non_calendar_fy_annual_2026"},
                "failed": {"PTC": "TransientSourceError"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (clean_source / "universe_candidates.json").write_text(
        json.dumps({"total_verified_eligible": 5}, indent=2),
        encoding="utf-8",
    )

    clean_report = mod.verify_source_integrity(clean_source, raise_on_error=True)
    assert clean_report.passed is True
    assert clean_report.done_count == 2
    assert clean_report.skipped_count == 1
    assert clean_report.failed_count == 1
    assert clean_report.pending_count == 1
    assert clean_report.manifest_rows_checked == 2
    assert clean_report.missing_files == ()
    assert clean_report.payload_sha_mismatches == ()
    assert clean_report.canonical_sha_mismatches == ()
    assert clean_report.provenance_mismatches == ()

    # Tamper case A: Corrupt 1 byte inside raw/sha256/*/*.json -> payload_file_sha256 FAIL-FAST
    tampered_payload_dir = tmp_path / "tampered_payload_universe"
    shutil.copytree(clean_source, tampered_payload_dir)
    raw_files = sorted((tampered_payload_dir / "raw" / "sha256").rglob("*.json"))
    assert len(raw_files) == 2
    raw_files[0].write_bytes(raw_files[0].read_bytes() + b" ")

    with pytest.raises(ArchiveIntegrityError):
        mod.verify_source_integrity(tampered_payload_dir, raise_on_error=True)
    payload_report = mod.verify_source_integrity(
        tampered_payload_dir, raise_on_error=False
    )
    assert payload_report.passed is False
    assert len(payload_report.payload_sha_mismatches) >= 1

    # Tamper case B: Replace raw payload + update payload_file_sha256 + provenance receipt so
    # payload_file_sha256 PASSES 100%, while leaving canonical_content_sha256 at the original hash!
    tampered_canonical_dir = tmp_path / "tampered_canonical_universe"
    shutil.copytree(clean_source, tampered_canonical_dir)
    tc_state_path = tampered_canonical_dir / "metadata" / "state.sqlite"
    with closing(sqlite3.connect(tc_state_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT filing_id, raw_archive_path, payload_file_sha256, canonical_content_sha256 "
            "FROM filings_manifest WHERE issuer_id = 'FPT' LIMIT 1"
        ).fetchone()
        assert row is not None
        old_rel = str(row["raw_archive_path"])
        old_sha = str(row["payload_file_sha256"])
        old_file = tampered_canonical_dir / "raw" / old_rel
        parsed = json.loads(old_file.read_text(encoding="utf-8"))
        # Mutate the metric value so canonical_content_sha256 diverges
        parsed["data"][0]["tongcongtaisan"] = 99_999_000_000_000.0
        new_bytes = json.dumps(parsed, separators=(",", ":")).encode("utf-8")
        new_sha = payload_file_sha256(new_bytes)
        new_rel = f"sha256/{new_sha[:2]}/{new_sha}.json"
        new_file = tampered_canonical_dir / "raw" / new_rel
        new_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.write_bytes(new_bytes)
        old_file.unlink()

        # Re-hash the matching provenance receipt so ONLY canonical_content_sha256 mismatches
        prov_dir = tampered_canonical_dir / "raw" / "provenance"
        for prov_file in sorted(prov_dir.glob("*.json")):
            prov_obj = json.loads(prov_file.read_text(encoding="utf-8"))
            if prov_obj.get("payload_file_sha256") == old_sha:
                prov_obj["payload_file_sha256"] = new_sha
                prov_obj["raw_archive_path"] = new_rel
                new_prov_bytes = json.dumps(
                    prov_obj, ensure_ascii=False, sort_keys=True, allow_nan=False
                ).encode("utf-8")
                new_prov_sha = payload_file_sha256(new_prov_bytes)
                (prov_dir / f"{new_prov_sha}.json").write_bytes(new_prov_bytes)
                prov_file.unlink()

        # Confirm the recomputed canonical hash genuinely differs from the stored manifest hash
        mutated_metrics = {
            k: v for k, v in parsed["data"][0].items() if k not in {"year", "quarter"}
        }
        assert canonical_content_sha256(
            mutated_metrics, StatementScope.CONSOLIDATED, date(2025, 12, 31)
        ) != str(row["canonical_content_sha256"])

        conn.execute(
            "UPDATE filings_manifest SET payload_file_sha256 = ?, raw_archive_path = ? "
            "WHERE filing_id = ?",
            (new_sha, new_rel, str(row["filing_id"])),
        )
        conn.commit()

    with pytest.raises(ArchiveIntegrityError):
        mod.verify_source_integrity(tampered_canonical_dir, raise_on_error=True)
    canonical_report = mod.verify_source_integrity(
        tampered_canonical_dir, raise_on_error=False
    )
    assert canonical_report.passed is False
    assert canonical_report.payload_sha_mismatches == ()
    assert canonical_report.provenance_mismatches == ()
    assert len(canonical_report.canonical_sha_mismatches) == 1
