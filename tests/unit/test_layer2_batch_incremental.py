"""TDD tests for Layer 2 vectorized batching, incremental builds, and TT99 continuity.

Verified Sources:
    Boncz, P. A., Zukowski, M., & Nes, N. (2005). "MonetDB/X100: Hyper-Pipelining
    Query Execution." CIDR 2005 — vectorized batch execution over per-row loops.
    Mokhov, A., Mitchell, N., & Peyton Jones, S. (2018). "Build Systems a la Carte."
    ICFP 2018 — content-addressed incremental builds and early cutoff.
    https://docs.pola.rs/user-guide/lazy/
    https://docs.pola.rs/user-guide/concepts/streaming/
    https://congbao.chinhphu.vn/van-ban/thong-tu-so-200-2014-tt-btc-6697/3529.htm
    https://congbao.chinhphu.vn/tai-ve-van-ban-so-99-2025-tt-btc-46529-59652?format=pdf
"""

from __future__ import annotations

import ctypes
import json
import shutil
import sys
import time
import tracemalloc
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from finintel.domain.schemas import (
    AuditOpinion,
    AuditStatus,
    FilingMetadata,
    PeriodType,
    RawFiling,
    RawFilingBundle,
    RawPayload,
    RevisionReason,
    StatementScope,
    StatementType,
)
from finintel.exceptions import ContractViolationError
from finintel.ingestion.archive import MANIFEST_SCHEMA, RawArchive
from finintel.ingestion.file_adapter import FileAdapter
from finintel.ingestion.hasher import canonical_content_sha256, payload_file_sha256
from finintel.normalization.archive import (
    _MAX_STREAM_BATCH_ISSUERS,
    _PQ,
    compute_taxonomy_sha256,
    normalize_archive,
    normalize_archive_incremental,
)
from finintel.normalization.mapper import (
    FACT_SCHEMA,
    UNMAPPED_SCHEMA,
    FilingMappingInput,
    TaxonomyMapper,
)
from finintel.normalization.periods import (
    BRIDGED_TTM_NOTE,
    compute_ttm,
)
from finintel.normalization.regime_resolver import TaxonomyVersion, resolve_regime
from finintel.normalization.restatement import map_restatement
from finintel.pipeline.normalize import NORMALIZATION_METADATA, NormalizeHandler
from finintel.pipeline.runner import RunRequest, Stage, StepContext
from finintel.pipeline.state_store import StateStore

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
CIP_FIXTURE_PATH = ROOT / "tests/fixtures/raw_samples/fhsc_cip_sample.json"


def _quarter_dates(year: int, quarter: int) -> tuple[date, date]:
    start_month = (quarter - 1) * 3 + 1
    end_month = quarter * 3
    end_day = (31, 30, 30, 31)[quarter - 1]
    return date(year, start_month, 1), date(year, end_month, end_day)


def _make_metadata(
    issuer_id: str,
    year: int,
    quarter: int | None = None,
    *,
    statement: StatementType = StatementType.INCOME_STATEMENT,
    scope: StatementScope = StatementScope.CONSOLIDATED,
    audit_status: AuditStatus = AuditStatus.UNAUDITED,
    published_at: date | None = None,
    ytd: bool = False,
) -> FilingMetadata:
    fiscal_start = date(year, 1, 1)
    if quarter is None:
        period_start = fiscal_start
        period_end = date(year, 12, 31)
        period_type = PeriodType.ANNUAL
    else:
        q_start, period_end = _quarter_dates(year, quarter)
        period_start = fiscal_start if ytd else q_start
        period_type = PeriodType.QUARTER
    ticker = issuer_id.replace("-", "").upper()[:16]
    return FilingMetadata(
        issuer_id=issuer_id,
        ticker_at_filing=ticker,
        statement_type=statement,
        period_type=period_type,
        fiscal_year=year,
        fiscal_year_start_date=fiscal_start,
        period_start_date=period_start,
        period_end_date=period_end,
        fiscal_quarter=quarter,
        statement_scope=scope,
        published_at=published_at,
        audit_status=audit_status,
        audit_opinion=(
            AuditOpinion.UNMODIFIED
            if audit_status == AuditStatus.AUDITED
            else AuditOpinion.NOT_APPLICABLE
        ),
    )


def _store_fhsc_page(
    archive: RawArchive,
    records: Sequence[tuple[FilingMetadata, Mapping[str, object]]],
) -> tuple[str, ...]:
    page_rows: list[dict[str, object]] = []
    filings: list[RawFiling] = []
    for idx, (meta, metrics) in enumerate(records):
        row: dict[str, object] = {
            "year": meta.fiscal_year,
            "quarter": meta.fiscal_quarter,
            **metrics,
        }
        page_rows.append(row)
        filings.append(RawFiling(meta, dict(metrics), f"/data/{idx}"))
    payload_bytes = json.dumps(
        {"error_code": "0", "message": "success", "data": page_rows},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    stored = archive.store(
        RawFilingBundle(
            RawPayload(
                payload_bytes,
                "json",
                "fhsc",
                "vnstock-oapi==0.1.2",
                "PROVIDER_RAW_JSON",
                datetime.now(UTC),
            ),
            tuple(filings),
        )
    )
    return tuple(item.filing_id for item in stored)


def _sample_statement_metrics(statement: StatementType, base: int) -> dict[str, object]:
    if statement == StatementType.BALANCE_SHEET:
        return {
            "taisannganhan": base * 40,
            "tienvacackhoantuongduongtien": base * 10,
            "cackhoandaututaichinhnganhan": base * 5,
            "cackhoanphaithunganhan": base * 12,
            "phaithukhachhang": base * 10,
            "duphongphaithunganhankhodoi": -base,
            "hangtonkho_tong": base * 13,
            "taisandaihan": base * 60,
            "cackhoanphaithudaihan": base * 4,
            "taisancodinh": base * 35,
            "taisancodinhhuuhinh": base * 30,
            "taisancodinhvohinh": base * 5,
            "chiphixaydungcobandodang": base * 11,
            "daututaichinhdaihan": base * 8,
            "taisandaihankhac_tong": base * 2,
            "tongcongtaisan": base * 100,
            "nophaitra": base * 45,
            "nonganhan": base * 30,
            "phaitranguoibannganhan": base * 12,
            "vayvanothuetaichinhnganhan": base * 10,
            "nodaihan": base * 15,
            "vayvanothuetaichinhdaihan": base * 12,
            "vonchusohuu_tong": base * 55,
            "vonchusohuu": base * 55,
            "loinhuansauthuechuaphanphoi": base * 15,
            "tongcongnguonvon": base * 100,
            "unmapped_bs_note": base * 2,
        }
    if statement == StatementType.INCOME_STATEMENT:
        return {
            "doanhthubanhangvacungcapdichvu": base * 82,
            "cackhoangiamtrudoanhthu": base * 2,
            "doanhthuthuanvebanhangvacungcapdichvu": base * 80,
            "giavonhangban": base * 50,
            "loinhuangopvebanhangvacungcapdichvu": base * 30,
            "doanhthuhoatdongtaichinh": base * 3,
            "chiphitaichinh": base * 4,
            "trongdochiphilaivay": base * 3,
            "chiphibanhang": base * 7,
            "chiphiquanlydoanhnghiep": base * 6,
            "loinhuanthuantuhoatdongkinhdoanh": base * 16,
            "thunhapkhac": base * 2,
            "chiphikhac": base,
            "loinhuankhac": base,
            "tongloinhuanketoantruocthue": base * 17,
            "chiphithuetndnhienhanh": base * 3,
            "chiphithuetndnhoanlai": -base // 2,
            "loinhuansauthuethunhapdoanhnghiep": base * 14,
            "loinhuansauthuecuacongtyme": base * 13,
            "unmapped_is_note": base,
        }
    return {
        "khauhaotaisancodinh": base * 5,
        "luuchuyentienthuantuhoatdongkinhdoanh": base * 18,
        "tienchidemuasamxaydungtaisancodinh": -base * 7,
        "luuchuyentienthuantuhoatdongdautu": -base * 6,
        "tienvaynganhandaihannhanduoc": base * 9,
        "tienchitranogocvay": -base * 8,
        "luuchuyentienthuantusudungvaohoatdongtaichinh": -base * 2,
        "luuchuyentienthuantrongnam": base * 10,
        "unmapped_cf_note": base,
    }


def _sorted_frame(frame: pl.DataFrame) -> pl.DataFrame:
    sort_cols = [
        col
        for col in (
            "issuer_id",
            "filing_id",
            "period_end_date",
            "statement_type",
            "metric_id",
            "raw_metric_key",
            "derivation_method",
            "revision_no",
        )
        if col in frame.columns
    ]
    return frame.sort(sort_cols)


def _process_rss_bytes() -> int:
    if sys.platform == "win32":

        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_uint32),
                ("PageFaultCount", ctypes.c_uint32),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
        get_current_process = ctypes.windll.kernel32.GetCurrentProcess
        get_current_process.restype = ctypes.c_void_p
        get_mem_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_mem_info.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
        get_mem_info.restype = ctypes.c_int
        handle = get_current_process()
        ok = get_mem_info(handle, ctypes.byref(counters), counters.cb)
        if ok:
            return int(counters.WorkingSetSize)
    return 0


def test_map_batch_bit_exact_equivalence_and_contract_errors(
    tmp_path: Path,
) -> None:
    mapper = TaxonomyMapper(CONFIG_DIR)
    bs_2025 = _make_metadata("VN-AAA", 2025, 4, statement=StatementType.BALANCE_SHEET)
    bs_2026 = _make_metadata("VN-AAA", 2026, 1, statement=StatementType.BALANCE_SHEET)
    is_2026 = _make_metadata(
        "VN-AAA", 2026, None, statement=StatementType.INCOME_STATEMENT
    )
    is_2025_comp = _make_metadata(
        "VN-AAA", 2025, None, statement=StatementType.INCOME_STATEMENT
    )
    cf_direct = _make_metadata("VN-BBB", 2026, None, statement=StatementType.CASH_FLOW)
    cf_none = _make_metadata("VN-BBB", 2025, 2, statement=StatementType.CASH_FLOW)

    entries = [
        FilingMappingInput(
            filing_id="f-bs-2025",
            source_provider="fhsc",
            revision_no=1,
            unit_multiplier=1,
            cash_flow_method=None,
            period=bs_2025,
            filing=bs_2025,
            taxonomy=resolve_regime(bs_2025.fiscal_year_start_date),
            metrics={
                "tongcongtaisan": 1000,
                "vonchusohuu_tong": None,
                "vonchusohuu": 400,
                "chiphixaydungcobandodang": 120,
                "unknown_field": "abc",
            },
        ),
        FilingMappingInput(
            filing_id="f-bs-2026",
            source_provider="issuer",
            revision_no=2,
            unit_multiplier=1000,
            cash_flow_method=None,
            period=bs_2026,
            filing=bs_2026,
            taxonomy=resolve_regime(bs_2026.fiscal_year_start_date),
            metrics={"280": "5.5", "252": "(0)", "242": 99},
        ),
        FilingMappingInput(
            filing_id="f-is-2026-comp",
            source_provider="issuer",
            revision_no=1,
            unit_multiplier=1000,
            cash_flow_method=None,
            period=is_2025_comp,
            filing=is_2026,
            taxonomy=TaxonomyVersion.TT99,
            metrics={"10": 500, "24": "(15)", "70": 3200},
        ),
        FilingMappingInput(
            filing_id="f-cf-direct",
            source_provider="issuer",
            revision_no=1,
            unit_multiplier=1,
            cash_flow_method="DIRECT",
            period=cf_direct,
            filing=cf_direct,
            taxonomy=resolve_regime(cf_direct.fiscal_year_start_date),
            metrics={"02": "25", "20": "-8", "21": 0},
        ),
        FilingMappingInput(
            filing_id="f-cf-none",
            source_provider="issuer",
            revision_no=1,
            unit_multiplier=1,
            cash_flow_method=None,
            period=cf_none,
            filing=cf_none,
            taxonomy=resolve_regime(cf_none.fiscal_year_start_date),
            metrics={"02": 12, "20": 50},
        ),
    ]

    expected_facts_list: list[pl.DataFrame] = []
    expected_unmapped_list: list[pl.DataFrame] = []
    for entry in entries:
        assert isinstance(entry.unit_multiplier, int)
        f_df, u_df = mapper._map(
            entry.metrics,
            entry.period,
            entry.filing,
            entry.taxonomy,
            entry.filing_id,
            entry.source_provider,
            entry.revision_no,
            entry.unit_multiplier,
            entry.cash_flow_method,
        )
        expected_facts_list.append(f_df)
        expected_unmapped_list.append(u_df)

    expected_facts = pl.concat(expected_facts_list).rechunk()
    expected_unmapped = pl.concat(expected_unmapped_list).rechunk()
    batch_facts, batch_unmapped = mapper.map_batch(entries)

    assert batch_facts.schema == FACT_SCHEMA
    assert batch_facts.n_chunks() <= 1
    assert batch_unmapped.n_chunks() <= 1
    assert batch_facts.equals(expected_facts, null_equal=True)
    assert batch_unmapped.equals(expected_unmapped, null_equal=True)

    # Empty batch and all-unmapped batch preserve exact schemas.
    empty_f, empty_u = mapper.map_batch([])
    assert empty_f.is_empty() and empty_u.is_empty()
    assert empty_f.schema == FACT_SCHEMA
    only_u_f, only_u_u = mapper.map_batch(
        [replace(entries[0], metrics={"unknown_only": 42})]
    )
    assert only_u_f.is_empty() and only_u_u.height == 1

    # Verify restated comparative row carries RESTATED_REGIME_CHANGE and TT99.
    comp_rows = batch_facts.filter(pl.col("filing_id") == "f-is-2026-comp")
    assert comp_rows["revision_reason"].unique().to_list() == [
        RevisionReason.RESTATED_REGIME_CHANGE
    ]
    assert comp_rows["taxonomy_version"].unique().to_list() == [TaxonomyVersion.TT99]

    # Verify ContractViolationError parity for invalid batch entries.
    invalid_cases: list[FilingMappingInput] = [
        replace(entries[0], unit_multiplier=0),
        replace(entries[0], unit_multiplier=-10),
        replace(entries[0], unit_multiplier=1.5),
        replace(entries[0], cash_flow_method="DIRECT"),
        replace(entries[3], cash_flow_method="INVALID"),
        replace(entries[0], metrics={"tongcongtaisan": float("nan")}),
        replace(entries[0], metrics={"tongcongtaisan": float("inf")}),
        replace(
            entries[1],
            metrics={"280": 100, "Tổng cộng tài sản": 200},
        ),
    ]
    for bad in invalid_cases:
        with pytest.raises(ContractViolationError):
            mapper.map_batch([entries[0], bad])

    # Verify batched normalize_archive matches legacy row-by-row mapping including comparative file.
    csv_path = tmp_path / "is_2026.csv"
    csv_path.write_text(
        "raw_metric_key,raw_value,prior\n10,150,120\n24,8,7\n", encoding="utf-8"
    )
    state = StateStore(tmp_path / "metadata/state.sqlite")
    raw_root = tmp_path / "raw"
    archive = RawArchive(raw_root, state)
    (stored_file,) = archive.store(
        next(FileAdapter(csv_path, is_2026, source_provider="issuer").fetch())
    )
    filing_settings: dict[str, object] = {
        stored_file.filing_id: {
            "unit_multiplier": 1000,
            "comparative": {
                "value_column": "prior",
                "fiscal_year": 2025,
                "fiscal_year_start_date": date(2025, 1, 1),
                "period_type": "ANNUAL",
                "period_start_date": date(2025, 1, 1),
                "period_end_date": date(2025, 12, 31),
            },
        }
    }
    norm_facts, norm_unmapped = normalize_archive(
        state,
        raw_root,
        mapper,
        source="file",
        filing_settings=filing_settings,
    )
    expected_cur, expected_cur_u = mapper.map_metrics(
        {"10": "150", "24": "8"},
        is_2026,
        filing_id=stored_file.filing_id,
        source_provider="issuer",
        unit_multiplier=1000,
    )
    expected_cmp, expected_cmp_u = map_restatement(
        mapper,
        {"10": "120", "24": "7"},
        is_2026,
        is_2025_comp,
        filing_id=stored_file.filing_id,
        source_provider="issuer",
        unit_multiplier=1000,
    )
    assert _sorted_frame(norm_facts).equals(
        _sorted_frame(pl.concat([expected_cur, expected_cmp])), null_equal=True
    )
    assert _sorted_frame(norm_unmapped).equals(
        _sorted_frame(pl.concat([expected_cur_u, expected_cmp_u])), null_equal=True
    )


def test_batch_boundary_integrity_never_splits_issuer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mapper = TaxonomyMapper(CONFIG_DIR)
    state = StateStore(tmp_path / "metadata/state.sqlite")
    raw_root = tmp_path / "raw"
    archive = RawArchive(raw_root, state)

    for idx in range(120):
        issuer_id = f"ISS{idx:03d}"
        records = [
            (
                _make_metadata(
                    issuer_id, 2025, q, statement=StatementType.INCOME_STATEMENT
                ),
                {"doanhthuthuanvebanhangvacungcapdichvu": 1000 + idx * 10 + q},
            )
            for q in (1, 2)
        ]
        _store_fhsc_page(archive, records)

    seen_batches: list[set[str]] = []
    original_map_batch = mapper.map_batch

    def spy_map_batch(
        entries: Sequence[FilingMappingInput],
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        seen_batches.append({entry.period.issuer_id for entry in entries})
        return original_map_batch(entries)

    monkeypatch.setattr(mapper, "map_batch", spy_map_batch)
    facts, _ = normalize_archive(
        state, raw_root, mapper, source="fhsc", batch_issuers=50
    )

    assert [len(batch) for batch in seen_batches] == [50, 50, 20]
    all_seen: set[str] = set()
    for batch in seen_batches:
        assert all_seen.isdisjoint(batch), "An issuer_id was split across two batches!"
        all_seen.update(batch)
    assert len(all_seen) == 120
    assert facts.height == 240


def test_incremental_early_cutoff_mutation_deletion_and_invalidation(
    tmp_path: Path,
) -> None:
    # Copy config to tmp_path so Run 4 can safely mutate 1 byte of YAML.
    local_config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, local_config)

    state = StateStore(tmp_path / "metadata/state.sqlite")
    state.initialize()
    raw_root = tmp_path / "raw"
    archive = RawArchive(raw_root, state)
    curated_root = tmp_path / "curated/financial_facts"

    stored_by_issuer: dict[str, tuple[str, ...]] = {}
    for idx in range(10):
        issuer_id = f"INC{idx:02d}"
        records = [
            (
                _make_metadata(
                    issuer_id,
                    2025,
                    q,
                    statement=StatementType.INCOME_STATEMENT,
                    published_at=date(2025, q * 3 + 1, 25)
                    if q < 4
                    else date(2026, 1, 25),
                ),
                _sample_statement_metrics(
                    StatementType.INCOME_STATEMENT, base=(idx + 1) * 100 + q
                ),
            )
            for q in range(1, 5)
        ]
        stored_by_issuer[issuer_id] = _store_fhsc_page(archive, records)

    handler = NormalizeHandler(
        state,
        tmp_path,
        {"raw_path": "raw"},
        {"taxonomy_dir": "config"},
    )
    request = RunRequest(source="fhsc", tickers=(), period=None)

    def _run_handler(run_id: str) -> tuple[Path, dict[str, object]]:
        state.start_run(run_id, "CLI_MANUAL", request.log_arguments(), None)
        step_id = state.start_step(run_id, "NORMALIZE")
        outcome = handler(StepContext(run_id, Stage.NORMALIZE, request, (), None))
        state.finish_step(step_id, outcome.status)
        state.finish_run(run_id, "SUCCEEDED")
        batch_dir = curated_root / run_id
        meta = json.loads(
            (batch_dir / NORMALIZATION_METADATA).read_text(encoding="utf-8")
        )
        assert isinstance(meta, dict)
        return batch_dir, meta

    # Run 1: full build of 10 issuers.
    run1_dir, meta1 = _run_handler("run-001")
    assert meta1["source"] == "fhsc"
    assert meta1["full_history"] is True
    assert meta1["recomputed_issuers"] == 10
    assert meta1["taxonomy_sha256"] == compute_taxonomy_sha256(local_config, {})
    assert (run1_dir / "financial_facts.parquet").is_file()
    assert (run1_dir / "unmapped_metrics.parquet").is_file()
    facts_1 = pl.read_parquet(run1_dir / "financial_facts.parquet")
    unmapped_1 = pl.read_parquet(run1_dir / "unmapped_metrics.parquet")

    # Run 2: unchanged archive -> Early Cutoff (recomputed_issuers == 0) + files materialized in <run_2>/.
    run2_dir, meta2 = _run_handler("run-002")
    assert meta2["recomputed_issuers"] == 0
    assert meta2["taxonomy_sha256"] == meta1["taxonomy_sha256"]
    assert (run2_dir / "financial_facts.parquet").is_file()
    assert (run2_dir / "unmapped_metrics.parquet").is_file()
    assert _sorted_frame(pl.read_parquet(run2_dir / "financial_facts.parquet")).equals(
        _sorted_frame(facts_1), null_equal=True
    )
    assert _sorted_frame(pl.read_parquet(run2_dir / "unmapped_metrics.parquet")).equals(
        _sorted_frame(unmapped_1), null_equal=True
    )

    # Run 3: add/update 1 issuer (INC00) AND delete 1 filing of another issuer (INC01).
    _store_fhsc_page(
        archive,
        [
            (
                _make_metadata(
                    "INC00",
                    2026,
                    1,
                    statement=StatementType.INCOME_STATEMENT,
                    published_at=date(2026, 4, 20),
                ),
                _sample_statement_metrics(StatementType.INCOME_STATEMENT, base=999),
            )
        ],
    )
    deleted_filing_id = stored_by_issuer["INC01"][0]
    with state.connection() as conn:
        conn.execute(
            "DELETE FROM filings_manifest WHERE filing_id = ?", (deleted_filing_id,)
        )

    run3_dir, meta3 = _run_handler("run-003")
    assert meta3["recomputed_issuers"] == 2
    assert (run3_dir / "financial_facts.parquet").is_file()
    assert (run3_dir / "unmapped_metrics.parquet").is_file()

    # Compare Run 3 incremental outputs bit-for-bit against a clean full rebuild.
    clean_full_dir = tmp_path / "clean_full_run3"
    full_res = normalize_archive_incremental(
        state,
        raw_root,
        local_config,
        clean_full_dir,
        source="fhsc",
        previous_batch_dir=None,
    )
    assert full_res.recomputed_issuers == 10
    assert _sorted_frame(pl.read_parquet(run3_dir / "financial_facts.parquet")).equals(
        _sorted_frame(pl.read_parquet(full_res.facts_path)), null_equal=True
    )
    assert _sorted_frame(pl.read_parquet(run3_dir / "unmapped_metrics.parquet")).equals(
        _sorted_frame(pl.read_parquet(full_res.unmapped_path)), null_equal=True
    )
    assert deleted_filing_id not in set(
        pl.read_parquet(run3_dir / "financial_facts.parquet")["filing_id"].to_list()
    )

    # Metadata-only change (e.g., published_at / is_filed_late) on 1 issuer invalidates ONLY that issuer.
    with state.connection() as conn:
        conn.execute(
            "UPDATE filings_manifest SET is_filed_late = 1 WHERE filing_id = ?",
            (stored_by_issuer["INC02"][0],),
        )
    _, meta3b = _run_handler("run-003b")
    assert meta3b["recomputed_issuers"] == 1

    # Run 4a: modify 1 byte in YAML config -> taxonomy_sha256 changes -> automatic full rebuild of all 10 issuers.
    yaml_path = local_config / "metric_definitions.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8") + "\n# cache-bust\n", encoding="utf-8"
    )
    _, meta4 = _run_handler("run-004")
    assert meta4["taxonomy_sha256"] != meta3["taxonomy_sha256"]
    assert meta4["recomputed_issuers"] == 10

    # Run 4b: modify filing_settings -> taxonomy_sha256 changes -> automatic full rebuild of all 10 issuers.
    res_settings = normalize_archive_incremental(
        state,
        raw_root,
        local_config,
        curated_root / "run-005",
        source="fhsc",
        filing_settings={"unrelated-filing-id": {"unit_multiplier": 1000}},
        previous_batch_dir=curated_root / "run-004",
    )
    assert res_settings.taxonomy_sha256 != meta4["taxonomy_sha256"]
    assert res_settings.full_rebuild is True
    assert res_settings.recomputed_issuers == 10


def test_cross_taxonomy_2026_debt_cost_bridge_and_ttm_exact_filing() -> None:
    mapper = TaxonomyMapper(CONFIG_DIR)
    quarters_spec = [
        (2025, 2, 10.0, date(2025, 7, 25)),
        (2025, 3, 20.0, date(2025, 10, 25)),
        (2025, 4, 30.0, date(2026, 1, 25)),
        (2026, 1, 40.0, date(2026, 4, 25)),
        (2026, 2, 50.0, date(2026, 7, 25)),
        (2026, 3, 60.0, date(2026, 10, 25)),
    ]

    # 1. FHSC source with EXACT_FILING on all 6 quarters -> valid bridged TTM + ttm_exact_filing=True.
    fhsc_frames = [
        mapper.map_metrics(
            {"trongdochiphilaivay": val},
            _make_metadata("VN-FPT", yr, q, published_at=pub),
            filing_id=f"fhsc-{yr}q{q}",
            source_provider="fhsc",
        )[0]
        for yr, q, val, pub in quarters_spec
    ]
    fhsc_facts = pl.concat(fhsc_frames)
    assert set(
        fhsc_facts.filter(pl.col("fiscal_year") == 2025)["metric_id"].to_list()
    ) == {"IS_INTEREST_EXPENSE"}
    assert set(
        fhsc_facts.filter(pl.col("fiscal_year") == 2026)["metric_id"].to_list()
    ) == {"IS_BORROWING_COSTS"}

    fhsc_ttm = compute_ttm(fhsc_facts)
    q2026 = fhsc_ttm.filter(pl.col("fiscal_year") == 2026).sort("fiscal_quarter")
    assert q2026.select(
        "fiscal_quarter",
        "metric_id",
        "ttm_value",
        "computation_note",
        "ttm_exact_filing",
    ).rows() == [
        (1, "IS_BORROWING_COSTS", 100.0, BRIDGED_TTM_NOTE, True),
        (2, "IS_BORROWING_COSTS", 140.0, BRIDGED_TTM_NOTE, True),
        (3, "IS_BORROWING_COSTS", 180.0, BRIDGED_TTM_NOTE, True),
    ]

    # If one 2025 quarter lacks exact filing date, ttm_exact_filing must be False for affected windows.
    fhsc_mixed_exact = fhsc_facts.with_columns(
        pl.when((pl.col("fiscal_year") == 2025) & (pl.col("fiscal_quarter") == 2))
        .then(pl.lit("STATUTORY_MAX_LAG"))
        .otherwise(pl.col("published_at_basis"))
        .alias("published_at_basis")
    )
    q2026_mixed = (
        compute_ttm(fhsc_mixed_exact)
        .filter(pl.col("fiscal_year") == 2026)
        .sort("fiscal_quarter")
    )
    assert q2026_mixed["ttm_exact_filing"].to_list() == [False, True, True]

    # 2. Non-FHSC source ("issuer") without TT99 restatements -> NULL + MIXED_TAXONOMY.
    issuer_frames = [
        mapper.map_metrics(
            {"23" if yr == 2025 else "24": val},
            _make_metadata("VN-FPT", yr, q, published_at=pub),
            filing_id=f"iss-{yr}q{q}",
            source_provider="issuer",
        )[0]
        for yr, q, val, pub in quarters_spec
    ]
    issuer_facts = pl.concat(issuer_frames)
    issuer_ttm = (
        compute_ttm(issuer_facts)
        .filter(pl.col("fiscal_year") == 2026)
        .sort("fiscal_quarter")
    )
    assert issuer_ttm.select(
        "fiscal_quarter", "ttm_value", "computation_note", "ttm_exact_filing"
    ).rows() == [
        (1, None, "MIXED_TAXONOMY", False),
        (2, None, "MIXED_TAXONOMY", False),
        (3, None, "MIXED_TAXONOMY", False),
    ]

    # 3. With TT99 RESTATED_REGIME_CHANGE comparative IS_BORROWING_COSTS for 2025-Q2..Q4:
    #    cleanly replaces TT200 IS_INTEREST_EXPENSE via BRIDGED_QUARTER_KEY anti-join,
    #    zero duplicate quarters, homogeneous TT99 TTM with computation_note=None.
    restated_frames = [
        map_restatement(
            mapper,
            {"24": restated_val},
            _make_metadata("VN-FPT", 2026, q, published_at=pub_2026),
            _make_metadata("VN-FPT", 2025, q, published_at=pub_2025),
            filing_id=f"iss-restated-2025q{q}",
            source_provider="issuer",
        )[0]
        for q, restated_val, pub_2025, pub_2026 in (
            (2, 11.0, date(2025, 7, 25), date(2026, 7, 25)),
            (3, 22.0, date(2025, 10, 25), date(2026, 10, 25)),
            (4, 33.0, date(2026, 1, 25), date(2027, 1, 25)),
        )
    ]
    restated_ttm = compute_ttm(pl.concat([issuer_facts, *restated_frames]))
    # No duplicate (fiscal_year, fiscal_quarter) rows!
    assert (
        restated_ttm.select(["fiscal_year", "fiscal_quarter"]).is_duplicated().any()
        is False
    )
    restated_2026 = restated_ttm.filter(pl.col("fiscal_year") == 2026).sort(
        "fiscal_quarter"
    )
    assert restated_2026.select(
        "fiscal_quarter", "ttm_value", "computation_note", "ttm_exact_filing"
    ).rows() == [
        (1, 106.0, None, True),  # 11 + 22 + 33 + 40
        (2, 145.0, None, True),  # 22 + 33 + 40 + 50
        (3, 183.0, None, True),  # 33 + 40 + 50 + 60
    ]

    # Same restatement on FHSC upgrades computation_note from BRIDGED to None without duplicating quarters.
    fhsc_restated_frames = [
        map_restatement(
            mapper,
            {"trongdochiphilaivay": restated_val},
            _make_metadata("VN-FPT", 2026, q, published_at=pub_2026),
            _make_metadata("VN-FPT", 2025, q, published_at=pub_2025),
            filing_id=f"fhsc-restated-2025q{q}",
            source_provider="fhsc",
        )[0]
        for q, restated_val, pub_2025, pub_2026 in (
            (2, 11.0, date(2025, 7, 25), date(2026, 7, 25)),
            (3, 22.0, date(2025, 10, 25), date(2026, 10, 25)),
            (4, 33.0, date(2026, 1, 25), date(2027, 1, 25)),
        )
    ]
    fhsc_restated_ttm = compute_ttm(pl.concat([fhsc_facts, *fhsc_restated_frames]))
    assert (
        fhsc_restated_ttm.select(["fiscal_year", "fiscal_quarter"])
        .is_duplicated()
        .any()
        is False
    )
    assert fhsc_restated_ttm.filter(pl.col("fiscal_year") == 2026).sort(
        "fiscal_quarter"
    ).select(
        "fiscal_quarter", "ttm_value", "computation_note", "ttm_exact_filing"
    ).rows() == [
        (1, 106.0, None, True),
        (2, 145.0, None, True),
        (3, 183.0, None, True),
    ]


def test_bs_cip_long_term_tt200_242_tt99_252_and_fixture() -> None:
    mapper = TaxonomyMapper(CONFIG_DIR)

    # 1. TT200 statutory code "242" -> BS_CIP_LONG_TERM.
    tt200_mapped, tt200_unmapped = mapper.map_metrics(
        {"242": 15_000_000_000},
        _make_metadata("VN-CIP", 2025, 4, statement=StatementType.BALANCE_SHEET),
        filing_id="cip-tt200",
        source_provider="issuer",
    )
    assert tt200_unmapped.is_empty()
    assert tt200_mapped.select("metric_id", "value", "comparable_across_taxonomy").row(
        0
    ) == ("BS_CIP_LONG_TERM", 15_000_000_000.0, True)

    # 2. TT99 statutory code "252" -> BS_CIP_LONG_TERM, while "242" (accum. depr. IP) -> unmapped_metrics!
    tt99_mapped, tt99_unmapped = mapper.map_metrics(
        {"252": 18_000_000_000, "242": -350_000_000},
        _make_metadata("VN-CIP", 2026, 1, statement=StatementType.BALANCE_SHEET),
        filing_id="cip-tt99",
        source_provider="issuer",
    )
    assert tt99_mapped.select(
        "metric_id", "value", "comparable_across_taxonomy"
    ).rows() == [("BS_CIP_LONG_TERM", 18_000_000_000.0, True)]
    assert tt99_unmapped.select("raw_metric_key", "raw_value").rows() == [
        ("242", "-350000000")
    ]

    # 3. Verify committed FHSC fixture fhsc_cip_sample.json across both 2025 (TT200) and 2026 (TT99).
    payload = json.loads(CIP_FIXTURE_PATH.read_text(encoding="utf-8"))
    for idx, entry in enumerate(payload["data"]):
        row = dict(entry)
        year = int(row.pop("year"))
        quarter = int(row.pop("quarter"))
        expected_cip = float(row["chiphixaydungcobandodang"])
        mapped, _ = mapper.map_metrics(
            row,
            _make_metadata(
                "VN-CIP", year, quarter, statement=StatementType.BALANCE_SHEET
            ),
            filing_id=f"cip-fixture-{idx}",
            source_provider="fhsc",
        )
        cip_rows = mapped.filter(pl.col("metric_id") == "BS_CIP_LONG_TERM")
        assert cip_rows.height == 1
        assert cip_rows["value"].item() == expected_cip
        assert cip_rows["raw_metric_key"].item() == "chiphixaydungcobandodang"
        assert cip_rows["taxonomy_version"].item() == (
            TaxonomyVersion.TT99 if year >= 2026 else TaxonomyVersion.TT200
        )


def test_offline_synthetic_37_issuer_benchmark(tmp_path: Path) -> None:
    """Benchmark 37 issuers x 150 filings (5,550 filings) completely offline."""
    state = StateStore(tmp_path / "metadata/state.sqlite")
    state.initialize()
    raw_root = tmp_path / "raw"
    curated_root = tmp_path / "curated/financial_facts"

    # Build 37 issuers x 150 filings = 5,550 genuine FHSC filings (40Q+10Y per statement).
    manifest_rows: list[tuple[object, ...]] = []
    years = list(range(2017, 2027))
    now_iso = datetime.now(UTC).isoformat()

    for iss_idx in range(37):
        issuer_id = f"BENCH{iss_idx:02d}"
        for stmt in (
            StatementType.BALANCE_SHEET,
            StatementType.INCOME_STATEMENT,
            StatementType.CASH_FLOW,
        ):
            # 5 quarterly pages of 8 quarters each (40) + 1 annual page of 10 years (10) = 50 per statement.
            pages: list[tuple[str, list[tuple[int, int | None]]]] = [
                (
                    f"qpage{p}",
                    [
                        (years[y_i], q)
                        for y_i in range(p * 2, p * 2 + 2)
                        for q in range(1, 5)
                    ],
                )
                for p in range(5)
            ]
            pages.append(("annual", [(yr, None) for yr in years]))
            # Total per statement: 5*8 = 40 quarters + 10 annuals = 50 filings -> 150 per issuer.
            for page_tag, periods in pages:
                page_data: list[dict[str, object]] = []
                period_metrics: list[tuple[FilingMetadata, dict[str, object]]] = []
                for p_idx, (yr, q) in enumerate(periods):
                    audit = AuditStatus.AUDITED if q is None else AuditStatus.UNAUDITED
                    pub_date = (
                        date(yr + 1, 3, 25)
                        if q is None
                        else date(yr + (1 if q == 4 else 0), (q * 3) % 12 + 1, 20)
                    )
                    meta = _make_metadata(
                        issuer_id,
                        yr,
                        q,
                        statement=stmt,
                        audit_status=audit,
                        published_at=pub_date,
                    )
                    metrics = _sample_statement_metrics(
                        stmt, base=10_000 + iss_idx * 100 + yr + (q or 0) + p_idx
                    )
                    page_data.append({"year": yr, "quarter": q, **metrics})
                    period_metrics.append((meta, metrics))

                payload_bytes = json.dumps(
                    {"error_code": "0", "message": "success", "data": page_data},
                    separators=(",", ":"),
                ).encode("utf-8")
                file_digest = payload_file_sha256(payload_bytes)
                rel_path = f"sha256/{file_digest[:2]}/{file_digest}.json"
                full_path = raw_root / rel_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                if not full_path.exists():
                    full_path.write_bytes(payload_bytes)

                for loc_idx, (meta, metrics) in enumerate(period_metrics):
                    content_digest = canonical_content_sha256(
                        metrics, meta.statement_scope, meta.period_end_date
                    )
                    filing_id = (
                        f"{issuer_id}-{stmt.value}-{page_tag}-{meta.fiscal_year}-"
                        f"{meta.fiscal_quarter or 0}"
                    )
                    manifest_rows.append(
                        (
                            filing_id,
                            meta.issuer_id,
                            meta.ticker_at_filing,
                            "fhsc",
                            "vnstock-oapi==0.1.2",
                            "PROVIDER_RAW_JSON",
                            meta.statement_type.value,
                            meta.statement_scope.value,
                            meta.period_type.value,
                            meta.fiscal_year,
                            meta.fiscal_quarter,
                            meta.fiscal_year_start_date.isoformat(),
                            meta.period_start_date.isoformat(),
                            meta.period_end_date.isoformat(),
                            meta.published_at.isoformat()
                            if meta.published_at
                            else None,
                            meta.published_at_basis,
                            meta.effective_available_at.isoformat(),
                            meta.audit_status.value,
                            meta.audit_opinion.value,
                            int(meta.is_filed_late)
                            if meta.is_filed_late is not None
                            else None,
                            1,
                            RevisionReason.ORIGINAL.value,
                            now_iso,
                            file_digest,
                            content_digest,
                            rel_path,
                            f"/data/{loc_idx}",
                            None,
                        )
                    )

    assert len(manifest_rows) == 5_550
    with state.connection() as conn:
        conn.executescript(MANIFEST_SCHEMA)
        conn.executemany(
            """
            INSERT INTO filings_manifest (
                filing_id, issuer_id, ticker_at_filing, source_provider, connector_name,
                payload_nature, statement_type, statement_scope, period_type, fiscal_year,
                fiscal_quarter, fiscal_year_start_date, period_start_date, period_end_date,
                published_at, published_at_basis, effective_available_at, audit_status,
                audit_opinion, is_filed_late, revision_no, revision_reason, fetched_at,
                payload_file_sha256, canonical_content_sha256, raw_archive_path,
                raw_record_locator, provider_attrs_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            manifest_rows,
        )

    mapper = TaxonomyMapper(CONFIG_DIR)
    rss_before = _process_rss_bytes()

    # 1. Full Normalize benchmark (37 issuers, 5,550 filings) -> must be < 3.5s.
    t0 = time.perf_counter()
    full_res = normalize_archive_incremental(
        state,
        raw_root,
        CONFIG_DIR,
        curated_root / "bench-full",
        source="fhsc",
        mapper=mapper,
    )
    full_elapsed = time.perf_counter() - t0
    rss_after_full = _process_rss_bytes()

    assert full_res.recomputed_issuers == 37
    assert full_res.full_rebuild is True
    assert full_res.facts_rows > 0
    assert full_elapsed < 3.5, (
        f"Full 37-issuer normalize took {full_elapsed:.3f}s (>= 3.5s)"
    )

    # 2. Incremental Early Cutoff (0 dirty issuers) -> must be < 1.5s.
    t1 = time.perf_counter()
    cutoff_res = normalize_archive_incremental(
        state,
        raw_root,
        CONFIG_DIR,
        curated_root / "bench-cutoff",
        source="fhsc",
        previous_batch_dir=curated_root / "bench-full",
        mapper=mapper,
    )
    cutoff_elapsed = time.perf_counter() - t1
    assert cutoff_res.recomputed_issuers == 0
    assert cutoff_elapsed < 1.5, f"Early cutoff took {cutoff_elapsed:.3f}s (>= 1.5s)"

    # 3. Incremental 1-dirty-issuer benchmark -> must be < 1.5s.
    with state.connection() as conn:
        conn.execute(
            "UPDATE filings_manifest SET is_filed_late = 1 "
            "WHERE filing_id = (SELECT filing_id FROM filings_manifest WHERE issuer_id = 'BENCH00' LIMIT 1)"
        )
    tracemalloc.start()
    try:
        t2 = time.perf_counter()
        inc_res = normalize_archive_incremental(
            state,
            raw_root,
            CONFIG_DIR,
            curated_root / "bench-inc1",
            source="fhsc",
            previous_batch_dir=curated_root / "bench-cutoff",
            mapper=mapper,
        )
        inc_elapsed = time.perf_counter() - t2
        _, inc_peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    rss_after_inc = _process_rss_bytes()
    rss_delta_mb = max(0, max(rss_after_full, rss_after_inc) - rss_before) / (
        1024 * 1024
    )
    inc_peak_mb = inc_peak_bytes / (1024 * 1024)

    assert inc_res.recomputed_issuers == 1
    assert inc_res.full_rebuild is False
    assert inc_elapsed < 1.5, f"1-issuer incremental took {inc_elapsed:.3f}s (>= 1.5s)"
    assert rss_delta_mb < 500.0, f"RSS delta {rss_delta_mb:.1f} MB >= 500 MB"
    assert inc_peak_mb < 500.0, f"Traced heap peak {inc_peak_mb:.1f} MB >= 500 MB"
    # Verify incremental output matches total rows from full build.
    assert inc_res.facts_rows == full_res.facts_rows
    assert inc_res.unmapped_rows == full_res.unmapped_rows


def test_streaming_batch_normalization_parity_and_bounded_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEFECT-B6-02 regression: streaming chunked ParquetWriter preserves exact parity and bounds RAM."""
    state = StateStore(tmp_path / "metadata/state.sqlite")
    state.initialize()
    raw_root = tmp_path / "raw"
    archive = RawArchive(raw_root, state)

    num_issuers = 20
    for idx in range(num_issuers):
        issuer_id = f"STR{idx:03d}"
        records: list[tuple[FilingMetadata, Mapping[str, object]]] = []
        for q in range(1, 5):
            metrics = _sample_statement_metrics(
                StatementType.INCOME_STATEMENT, base=(idx + 1) * 100 + q
            )
            metrics[f"unmapped_custom_line_{q}"] = (idx + 1) * 10 + q
            records.append(
                (
                    _make_metadata(
                        issuer_id,
                        2025,
                        q,
                        statement=StatementType.INCOME_STATEMENT,
                        published_at=date(2025, q * 3 + 1, 20)
                        if q < 4
                        else date(2026, 1, 20),
                    ),
                    metrics,
                )
            )
        _store_fhsc_page(archive, records)

    mapper = TaxonomyMapper(CONFIG_DIR)
    ref_facts, ref_unmapped = normalize_archive(
        state, raw_root, mapper, source="fhsc", batch_issuers=50
    )

    seen_chunks: list[set[str]] = []
    original_map_batch = mapper.map_batch

    def spy_map_batch(
        entries: Sequence[FilingMappingInput],
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        seen_chunks.append({entry.period.issuer_id for entry in entries})
        return original_map_batch(entries)

    monkeypatch.setattr(mapper, "map_batch", spy_map_batch)

    rss_before = _process_rss_bytes()
    tracemalloc.start()
    try:
        stream_50 = normalize_archive_incremental(
            state,
            raw_root,
            CONFIG_DIR,
            tmp_path / "stream_50",
            source="fhsc",
            batch_issuers=50,
            mapper=mapper,
        )
        _, full_peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    rss_after = _process_rss_bytes()

    # Streaming chunk size must be capped at _MAX_STREAM_BATCH_ISSUERS and never split an issuer.
    assert len(seen_chunks) == 3
    assert all(len(chunk) <= _MAX_STREAM_BATCH_ISSUERS for chunk in seen_chunks)
    seen_union: set[str] = set()
    for chunk in seen_chunks:
        assert seen_union.isdisjoint(chunk)
        seen_union.update(chunk)
    assert len(seen_union) == num_issuers

    # Peak traced Python heap and OS RSS delta must stay strictly bounded.
    assert full_peak_bytes / (1024 * 1024) < 64.0
    assert max(0, rss_after - rss_before) / (1024 * 1024) < 500.0

    # Parquet files must contain per-chunk row groups with issuer_id min/max stats and exact schemas.
    f_meta = _PQ.read_metadata(stream_50.facts_path)
    u_meta = _PQ.read_metadata(stream_50.unmapped_path)
    assert f_meta.num_row_groups == 3
    assert u_meta.num_row_groups == 3
    assert pl.read_parquet_schema(stream_50.facts_path) == FACT_SCHEMA
    assert pl.read_parquet_schema(stream_50.unmapped_path) == UNMAPPED_SCHEMA

    # Compare batch_issuers=50 and batch_issuers=3 outputs bit-for-bit against normalize_archive.
    stream_3 = normalize_archive_incremental(
        state,
        raw_root,
        CONFIG_DIR,
        tmp_path / "stream_3",
        source="fhsc",
        batch_issuers=3,
        mapper=mapper,
    )
    for res in (stream_50, stream_3):
        facts_df = pl.read_parquet(res.facts_path)
        unmapped_df = pl.read_parquet(res.unmapped_path)
        assert facts_df.schema == FACT_SCHEMA
        assert unmapped_df.schema == UNMAPPED_SCHEMA
        assert _sorted_frame(facts_df).equals(_sorted_frame(ref_facts), null_equal=True)
        assert _sorted_frame(unmapped_df).equals(
            _sorted_frame(ref_unmapped), null_equal=True
        )

    # Multi-chunk incremental update (10 dirty issuers > _MAX_STREAM_BATCH_ISSUERS) matches clean rebuild.
    dirty_ids = [f"STR{idx:03d}" for idx in range(10)]
    with state.connection() as conn:
        conn.execute(
            f"UPDATE filings_manifest SET is_filed_late = 1 "
            f"WHERE issuer_id IN ({', '.join('?' for _ in dirty_ids)})",
            dirty_ids,
        )
    inc_multi = normalize_archive_incremental(
        state,
        raw_root,
        CONFIG_DIR,
        tmp_path / "stream_inc_multi",
        source="fhsc",
        previous_batch_dir=tmp_path / "stream_50",
        batch_issuers=50,
        mapper=mapper,
    )
    clean_after = normalize_archive_incremental(
        state,
        raw_root,
        CONFIG_DIR,
        tmp_path / "stream_clean_after",
        source="fhsc",
        previous_batch_dir=None,
        batch_issuers=50,
        mapper=mapper,
    )
    assert inc_multi.recomputed_issuers == 10
    assert inc_multi.full_rebuild is False
    assert pl.read_parquet_schema(inc_multi.facts_path) == FACT_SCHEMA
    assert pl.read_parquet_schema(inc_multi.unmapped_path) == UNMAPPED_SCHEMA
    assert _sorted_frame(pl.read_parquet(inc_multi.facts_path)).equals(
        _sorted_frame(pl.read_parquet(clean_after.facts_path)), null_equal=True
    )
    assert _sorted_frame(pl.read_parquet(inc_multi.unmapped_path)).equals(
        _sorted_frame(pl.read_parquet(clean_after.unmapped_path)), null_equal=True
    )

    # Edge case: 0-row unmapped_metrics.parquet transitioning to non-empty via incremental update.
    empty_u_state = StateStore(tmp_path / "empty_u/state.sqlite")
    empty_u_state.initialize()
    empty_u_raw = tmp_path / "empty_u/raw"
    empty_u_archive = RawArchive(empty_u_raw, empty_u_state)
    _store_fhsc_page(
        empty_u_archive,
        [
            (
                _make_metadata(
                    "ONLY01", 2025, 1, statement=StatementType.INCOME_STATEMENT
                ),
                {"doanhthuthuanvebanhangvacungcapdichvu": 1000},
            )
        ],
    )
    res_zero = normalize_archive_incremental(
        empty_u_state,
        empty_u_raw,
        CONFIG_DIR,
        tmp_path / "empty_u/b1",
        source="fhsc",
        mapper=mapper,
    )
    assert res_zero.unmapped_rows == 0
    assert pl.read_parquet_schema(res_zero.unmapped_path) == UNMAPPED_SCHEMA

    _store_fhsc_page(
        empty_u_archive,
        [
            (
                _make_metadata(
                    "ONLY01", 2025, 2, statement=StatementType.INCOME_STATEMENT
                ),
                {
                    "doanhthuthuanvebanhangvacungcapdichvu": 1200,
                    "brand_new_unmapped_metric": 42,
                },
            )
        ],
    )
    res_nonzero = normalize_archive_incremental(
        empty_u_state,
        empty_u_raw,
        CONFIG_DIR,
        tmp_path / "empty_u/b2",
        source="fhsc",
        previous_batch_dir=tmp_path / "empty_u/b1",
        mapper=mapper,
    )
    assert res_nonzero.recomputed_issuers == 1
    assert res_nonzero.unmapped_rows == 1
    assert pl.read_parquet_schema(res_nonzero.unmapped_path) == UNMAPPED_SCHEMA
    assert pl.read_parquet(res_nonzero.unmapped_path)["raw_metric_key"].to_list() == [
        "brand_new_unmapped_metric"
    ]
