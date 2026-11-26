"""Unit and rollover-hygiene tests for ARCH-02 (L1/L1B cache memory safety)."""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

import finintel.serving.queries as queries_mod
import finintel.storage.repository as repo_mod
from finintel.analytics.tables import build_analytics_tables
from finintel.exceptions import ContractViolationError
from finintel.pipeline.state_store import StateStore
from finintel.serving.queries import (
    DashboardQueryService,
    _l1b_root_and_snapshot,
    clear_query_caches,
)
from finintel.storage.ducklake_store import DuckLakeStore
from finintel.storage.repository import ParquetRepository, clear_repository_caches
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_SCHEMA,
    AUDIT_QUARANTINE_TABLE,
    BENCHMARKS_TABLE,
    FILINGS_MANIFEST_SCHEMA,
    FILINGS_MANIFEST_TABLE,
    ML_SCORES_SCHEMA,
    ML_SCORES_TABLE,
    QUALITY_SUMMARY_SCHEMA,
    QUALITY_SUMMARY_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
    canonicalize_financial_facts,
)
from finintel.storage.snapshot_publisher import SnapshotPublisher


def _build_minimal_v2_tables(
    *,
    revenue_scale: float = 1.0,
) -> dict[str, pl.DataFrame]:
    """Build real, self-consistent V2 tables with benchmarks, ratios, ML, and manifest."""
    issuers = pl.DataFrame(
        {
            "issuer_id": ["FPT", "CMG"],
            "ticker": ["FPT", "CMG"],
            "exchange": ["HOSE", "HOSE"],
            "company_name_vn": ["Công ty Cổ phần FPT", "Tập đoàn Công nghệ CMC"],
            "accounting_regime": ["CORPORATE", "CORPORATE"],
            "icb_code_level2": ["9500", "9500"],
            "icb_name_level2": ["Công nghệ thông tin", "Công nghệ thông tin"],
            "icb_code_level3": ["9530", "9530"],
            "icb_name_level3": ["Phần mềm", "Phần mềm"],
            "industry_snapshot_date": [date(2025, 1, 1), date(2025, 1, 1)],
            "delisted_date": [None, None],
        }
    )

    quarter_windows = [
        (1, date(2024, 1, 1), date(2024, 3, 31), date(2024, 4, 20)),
        (2, date(2024, 4, 1), date(2024, 6, 30), date(2024, 7, 20)),
        (3, date(2024, 7, 1), date(2024, 9, 30), date(2024, 10, 20)),
        (4, date(2024, 10, 1), date(2024, 12, 31), date(2025, 1, 25)),
        (1, date(2025, 1, 1), date(2025, 3, 31), date(2025, 4, 20)),
        (2, date(2025, 4, 1), date(2025, 6, 30), date(2025, 7, 20)),
        (3, date(2025, 7, 1), date(2025, 9, 30), date(2025, 10, 20)),
        (4, date(2025, 10, 1), date(2025, 12, 31), date(2026, 1, 25)),
    ]

    fact_rows: list[dict[str, object]] = []
    manifest_rows: list[dict[str, object]] = []
    for issuer_id, scale in (("FPT", 1.0), ("CMG", 0.25)):
        for year in (2024, 2025):
            rev = 60_000.0 * scale * revenue_scale
            gp = rev * 0.40
            cogs = -(rev - gp)
            sell = -(rev * 0.10)
            admin = -(rev * 0.12)
            interest = -(700.0 * scale)
            op_profit = gp + sell + admin + interest
            ebt = op_profit + 500.0 * scale
            cur_tax = -(max(ebt, 0.0) * 0.18)
            def_tax = 50.0 * scale
            ni = ebt + cur_tax + def_tax
            ni_parent = ni * 0.85
            cfo = 10_500.0 * scale * revenue_scale
            capex = -4_000.0 * scale
            dep = 2_500.0 * scale

            total_assets = 80_000.0 * scale
            total_liab = 36_000.0 * scale
            total_eq = total_assets - total_liab
            cur_liab = total_liab * 0.65
            cur_assets = total_assets * 0.60

            metrics = {
                "BS_CURRENT_ASSETS": cur_assets,
                "BS_CASH_AND_EQUIVALENTS": 10_000.0 * scale,
                "BS_SHORT_TERM_INVESTMENTS": 5_000.0 * scale,
                "BS_SHORT_TERM_RECEIVABLES": 14_000.0 * scale,
                "BS_TRADE_RECEIVABLES_ST": 12_000.0 * scale,
                "BS_BAD_DEBT_PROVISION_ST": -500.0 * scale,
                "BS_INVENTORIES": 4_000.0 * scale,
                "BS_TOTAL_ASSETS": total_assets,
                "BS_TOTAL_LIABILITIES": total_liab,
                "BS_CURRENT_LIABILITIES": cur_liab,
                "BS_SHORT_TERM_BORROWINGS": 3_500.0 * scale,
                "BS_TRADE_PAYABLES_ST": 8_000.0 * scale,
                "BS_LONG_TERM_BORROWINGS": 4_000.0 * scale,
                "BS_CONVERTIBLE_BONDS": 0.0,
                "BS_TOTAL_EQUITY": total_eq,
                "BS_ORDINARY_SHARES_CAPITAL": 17_000.0 * scale,
                "BS_TREASURY_SHARES": 0.0,
                "BS_MINORITY_INTEREST": total_eq * 0.12,
                "IS_NET_REVENUE": rev,
                "IS_COST_OF_GOODS_SOLD": cogs,
                "IS_GROSS_PROFIT": gp,
                "IS_SELLING_EXPENSES": sell,
                "IS_ADMINISTRATIVE_EXPENSES": admin,
                "IS_INTEREST_EXPENSE": interest,
                "IS_OPERATING_PROFIT": op_profit,
                "IS_PROFIT_BEFORE_TAX": ebt,
                "IS_CURRENT_INCOME_TAX": cur_tax,
                "IS_DEFERRED_INCOME_TAX": def_tax,
                "IS_NET_INCOME": ni,
                "IS_NET_INCOME_PARENT": ni_parent,
                "IS_EPS_BASIC_REPORTED": 4500.0,
                "CF_DEPRECIATION_AMORTIZATION": dep,
                "CF_OPERATING_CASH_FLOW": cfo,
                "CF_CAPITAL_EXPENDITURE": capex,
            }

            ann_fid = f"FIL_{issuer_id}_{year}_ANNUAL"
            ann_pub = date(year + 1, 3, 20)
            manifest_rows.append(
                {
                    "filing_id": ann_fid,
                    "issuer_id": issuer_id,
                    "ticker_at_filing": issuer_id,
                    "source_provider": "hose_pdf_extract",
                    "connector_name": "file_adapter_v1",
                    "statement_type": "CONSOLIDATED_PACK",
                    "statement_scope": "CONSOLIDATED",
                    "period_type": "ANNUAL",
                    "period_end_date": date(year, 12, 31),
                    "published_at": ann_pub,
                    "published_at_basis": "EXACT_FILING",
                    "effective_available_at": ann_pub,
                    "audit_status": "AUDITED",
                    "audit_opinion": "UNMODIFIED",
                    "revision_no": 1,
                    "revision_reason": "ORIGINAL",
                    "canonical_content_sha256": f"{issuer_id.lower()}{year}".ljust(
                        64, "0"
                    )[:64],
                    "payload_file_sha256": f"{issuer_id.lower()}{year}".ljust(64, "0")[
                        :64
                    ],
                }
            )
            for metric_id, val in metrics.items():
                fact_rows.append(
                    {
                        "issuer_id": issuer_id,
                        "statement_scope": "CONSOLIDATED",
                        "period_type": "ANNUAL",
                        "fiscal_year_start_date": date(year, 1, 1),
                        "period_end_date": date(year, 12, 31),
                        "metric_id": metric_id,
                        "taxonomy_version": "VAS_TT200_2014",
                        "revision_no": 1,
                        "filing_id": ann_fid,
                        "fiscal_year": year,
                        "fiscal_quarter": None,
                        "period_start_date": date(year, 1, 1),
                        "published_at": ann_pub,
                        "published_at_basis": "EXACT_FILING",
                        "effective_available_at": ann_pub,
                        "revision_reason": "ORIGINAL",
                        "derivation_method": "REPORTED_DIRECTLY",
                        "audit_status": "AUDITED",
                        "audit_opinion": "UNMODIFIED",
                        "value": float(val),
                        "ttm_value": (
                            float(val) if not metric_id.startswith("BS_") else None
                        ),
                        "computation_note": None,
                    }
                )

            for q_num, q_start, q_end, q_pub in quarter_windows:
                if q_end.year != year:
                    continue
                q_fid = f"FIL_{issuer_id}_{year}_Q{q_num}"
                manifest_rows.append(
                    {
                        "filing_id": q_fid,
                        "issuer_id": issuer_id,
                        "ticker_at_filing": issuer_id,
                        "source_provider": "hose_pdf_extract",
                        "connector_name": "file_adapter_v1",
                        "statement_type": "CONSOLIDATED_PACK",
                        "statement_scope": "CONSOLIDATED",
                        "period_type": "QUARTER",
                        "period_end_date": q_end,
                        "published_at": q_pub,
                        "published_at_basis": "EXACT_FILING",
                        "effective_available_at": q_pub,
                        "audit_status": "UNAUDITED",
                        "audit_opinion": "NOT_APPLICABLE",
                        "revision_no": 1,
                        "revision_reason": "ORIGINAL",
                        "canonical_content_sha256": (
                            f"{issuer_id.lower()}{year}q{q_num}".ljust(64, "0")[:64]
                        ),
                        "payload_file_sha256": (
                            f"{issuer_id.lower()}{year}q{q_num}".ljust(64, "0")[:64]
                        ),
                    }
                )
                for metric_id, val in metrics.items():
                    is_stock = metric_id.startswith("BS_")
                    q_val = (
                        float(val)
                        if is_stock
                        else (
                            4500.0 / 4.0
                            if metric_id == "IS_EPS_BASIC_REPORTED"
                            else float(val) / 4.0
                        )
                    )
                    fact_rows.append(
                        {
                            "issuer_id": issuer_id,
                            "statement_scope": "CONSOLIDATED",
                            "period_type": "QUARTER",
                            "fiscal_year_start_date": date(year, 1, 1),
                            "period_end_date": q_end,
                            "metric_id": metric_id,
                            "taxonomy_version": "VAS_TT200_2014",
                            "revision_no": 1,
                            "filing_id": q_fid,
                            "fiscal_year": year,
                            "fiscal_quarter": q_num,
                            "period_start_date": q_start,
                            "published_at": q_pub,
                            "published_at_basis": "EXACT_FILING",
                            "effective_available_at": q_pub,
                            "revision_reason": "ORIGINAL",
                            "derivation_method": "REPORTED_DIRECTLY",
                            "audit_status": "UNAUDITED",
                            "audit_opinion": "NOT_APPLICABLE",
                            "value": q_val,
                            "ttm_value": None,
                            "computation_note": None,
                        }
                    )

    facts_df = canonicalize_financial_facts(pl.DataFrame(fact_rows))
    analytics = build_analytics_tables(facts_df, issuers, as_of=date(2026, 6, 1))
    ratios_df = analytics[RATIOS_TABLE]
    now_iso = datetime.now(UTC).isoformat()

    ml_rows = [
        {
            "issuer_id": str(row["issuer_id"]),
            "statement_scope": str(row["statement_scope"]),
            "period_type": str(row["period_type"]),
            "period_end_date": row["period_end_date"],
            "filing_id": str(row["filing_id"]),
            "model_role": "SCREENING_INDICATOR",
            "altman_z_double_prime": 4.0,
            "altman_em_score": 7.0,
            "altman_zone": "SAFE_ZONE",
            "piotroski_f_score": 8,
            "piotroski_valid_components_count": 9,
            "beneish_m8_score": -2.4,
            "beneish_flag": "VALID",
            "ml_distress_prob": 0.05 if str(row["period_type"]) == "QUARTER" else None,
            "ml_risk_rank": 10 if str(row["period_type"]) == "QUARTER" else None,
            "ml_model_version": "test_arch02_v1",
            "ml_feature_coverage": 1.0,
            "shap_top_drivers_json": (
                json.dumps(
                    {
                        "schema_version": 2,
                        "feature_mode": "RATIOS",
                        "base_value": 0.5,
                        "prediction_score": 0.05,
                        "top_risk_increasing": [],
                        "top_risk_decreasing": [],
                    }
                )
                if str(row["period_type"]) == "QUARTER"
                else None
            ),
            "as_of_date": date(2026, 2, 1),
        }
        for row in ratios_df.to_dicts()
    ]
    quality_rows = [
        {
            "issuer_id": str(row["issuer_id"]),
            "statement_scope": str(row["statement_scope"]),
            "period_type": str(row["period_type"]),
            "period_end_date": row["period_end_date"],
            "taxonomy_version": "VAS_TT200_2014",
            "status": "PASS",
            "critical_codes": [],
            "warning_codes": [],
            "info_codes": [],
            "filing_ids": [str(row["filing_id"])],
            "checked_at": now_iso,
        }
        for row in ratios_df.to_dicts()
    ]
    return {
        "issuers.parquet": issuers,
        "financial_facts.parquet": facts_df,
        "latest_facts.parquet": facts_df,
        RATIOS_TABLE: ratios_df,
        BENCHMARKS_TABLE: analytics[BENCHMARKS_TABLE],
        RED_FLAGS_TABLE: analytics[RED_FLAGS_TABLE],
        ML_SCORES_TABLE: pl.DataFrame(ml_rows, schema=ML_SCORES_SCHEMA),
        QUALITY_SUMMARY_TABLE: pl.DataFrame(
            quality_rows, schema=QUALITY_SUMMARY_SCHEMA
        ),
        AUDIT_QUARANTINE_TABLE: pl.DataFrame(schema=AUDIT_QUARANTINE_SCHEMA),
        FILINGS_MANIFEST_TABLE: pl.DataFrame(
            manifest_rows, schema=FILINGS_MANIFEST_SCHEMA
        ),
    }


def _publish_minimal_v2_snapshot(
    data_root: Path,
    snapshot_id: str,
    *,
    revenue_scale: float = 1.0,
) -> Path:
    tables = _build_minimal_v2_tables(revenue_scale=revenue_scale)
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    publisher = SnapshotPublisher(
        data_root / "published" / "snapshots", state_store=state
    )
    run_id = f"run_{snapshot_id}"
    state.start_run(run_id, "MANUAL", {"test": True}, None)
    snap_dir = publisher.publish(snapshot_id, tables, run_id=run_id)
    state.finish_run(run_id, "SUCCEEDED", snapshot_id)
    return snap_dir


def _publish_minimal_v2_ducklake_snapshot(
    data_root: Path,
    snapshot_id: str,
    *,
    revenue_scale: float = 1.0,
) -> int:
    tables = _build_minimal_v2_tables(revenue_scale=revenue_scale)
    state = StateStore(data_root / "metadata" / "state.sqlite")
    state.initialize()
    store = DuckLakeStore(
        catalog_path=data_root / "metadata" / "ducklake_catalog.sqlite",
        data_path=data_root / "lakehouse" / "data",
        state_store=state,
    )
    run_id = f"run_{snapshot_id}"
    state.start_run(run_id, "MANUAL", {"test": True}, None)
    version = store.publish_snapshot(snapshot_id, tables, run_id=run_id)
    state.finish_run(run_id, "SUCCEEDED", snapshot_id)
    return version


def test_industry_benchmarks_excluded_from_l1_table_cache(tmp_path: Path) -> None:
    clear_repository_caches()
    clear_query_caches()
    assert BENCHMARKS_TABLE not in repo_mod._IN_MEMORY_CACHEABLE_TABLES

    snap_dir = _publish_minimal_v2_snapshot(tmp_path, "snap_arch02_bench_001")
    repo = ParquetRepository(data_root=tmp_path)
    service = DashboardQueryService(repo)

    # Query benchmarks via both Repository and DashboardQueryService (DuckDB pushdown).
    bench_df = repo.read_table(
        BENCHMARKS_TABLE,
        columns=["issuer_id", "metric_or_ratio_name", "value", "peer_median"],
        filters={"issuer_id": "FPT", "metric_or_ratio_name": "roe_total"},
    )
    assert not bench_df.is_empty()

    comparison = service.get_industry_comparison(
        "FPT",
        statement_scope="CONSOLIDATED",
        period_type="ANNUAL",
        period_end_date=date(2025, 12, 31),
        metric_name="roe_total",
    )
    peer_hist = service.get_peer_band_history(
        "FPT",
        statement_scope="CONSOLIDATED",
        period_type="QUARTER",
        metric_name="roe_total",
    )
    assert comparison.peer_count == 2
    assert comparison.peer_median is not None
    assert not peer_hist.is_empty()

    # Exact parity against direct Parquet read.
    direct = pl.read_parquet(snap_dir / BENCHMARKS_TABLE).filter(
        (pl.col("issuer_id") == "FPT")
        & (pl.col("statement_scope") == "CONSOLIDATED")
        & (pl.col("period_type") == "ANNUAL")
        & (pl.col("period_end_date") == date(2025, 12, 31))
        & (pl.col("metric_or_ratio_name") == "roe_total")
    )
    assert direct.height == 1
    assert comparison.company_value == direct.row(0, named=True)["value"]
    assert comparison.peer_median == direct.row(0, named=True)["peer_median"]

    # Small serving tables enter L1 cache; industry_benchmarks.parquet MUST stay off-heap.
    cached_paths = [
        str(k[1])
        for k in repo_mod._SNAPSHOT_TABLE_DATA_CACHE
        if len(k) > 1 and isinstance(k[1], str)
    ]
    assert any("issuers.parquet" in p for p in cached_paths)
    assert not any(BENCHMARKS_TABLE in p for p in cached_paths)


def test_l1b_all_10_caches_evict_stale_snapshot_on_rollover_and_preserve_other_roots(
    tmp_path: Path,
) -> None:
    clear_repository_caches()
    clear_query_caches()

    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    _publish_minimal_v2_snapshot(root_a, "snap_arch02_a1", revenue_scale=1.0)
    _publish_minimal_v2_snapshot(root_b, "snap_arch02_b1", revenue_scale=1.1)

    svc_a = DashboardQueryService(ParquetRepository(data_root=root_a))
    svc_b = DashboardQueryService(ParquetRepository(data_root=root_b))

    def _populate_all_10(svc: DashboardQueryService) -> None:
        svc.get_snapshot_overview()
        svc.get_company_profile("FPT", period_view="TTM")
        svc.get_industry_comparison(
            "FPT",
            statement_scope="CONSOLIDATED",
            period_type="ANNUAL",
            period_end_date=date(2025, 12, 31),
            metric_name="roe_total",
        )
        svc.get_peer_band_history(
            "FPT",
            statement_scope="CONSOLIDATED",
            period_type="QUARTER",
            metric_name="roe_total",
        )
        svc.list_ratio_periods("FPT", statement_scope="CONSOLIDATED")
        svc.get_ratio_lineage(
            "FPT",
            statement_scope="CONSOLIDATED",
            period_type="ANNUAL",
            period_end_date=date(2025, 12, 31),
            ratio_name="roe_total",
        )
        svc.screen_market(statement_scope="CONSOLIDATED", period_type="QUARTER")
        svc.get_stress_test_snapshot_inputs(
            statement_scope="CONSOLIDATED", period_type="QUARTER"
        )

    _populate_all_10(svc_a)
    _populate_all_10(svc_b)

    assert len(queries_mod._ALL_L1B_CACHES) == 10
    for idx, cache in enumerate(queries_mod._ALL_L1B_CACHES):
        keys_str = [repr(k) for k in cache]
        assert any("snap_arch02_a1" in ks for ks in keys_str), (
            f"L1B cache[{idx}] did not populate root_a snap_arch02_a1"
        )
        assert any("snap_arch02_b1" in ks for ks in keys_str), (
            f"L1B cache[{idx}] did not populate root_b snap_arch02_b1"
        )

    # Rollover root_a -> snap_arch02_a2 and run a single query on root_a.
    _publish_minimal_v2_snapshot(root_a, "snap_arch02_a2", revenue_scale=1.25)
    overview_a2 = svc_a.get_snapshot_overview()
    assert overview_a2.snapshot_id == "snap_arch02_a2"

    # ALL 10 L1B caches must have evicted snap_arch02_a1 while preserving root_b snap_arch02_b1.
    for idx, cache in enumerate(queries_mod._ALL_L1B_CACHES):
        keys_str = [repr(k) for k in cache]
        assert not any("snap_arch02_a1" in ks for ks in keys_str), (
            f"Stale snap_arch02_a1 leaked in L1B cache[{idx}] after rollover"
        )
        assert any("snap_arch02_b1" in ks for ks in keys_str), (
            f"Unrelated root_b entry evicted from L1B cache[{idx}]"
        )


def test_l1b_ducklake_rollover_eviction_real_store(tmp_path: Path) -> None:
    clear_repository_caches()
    clear_query_caches()

    dl_root_a = tmp_path / "dl_root_a"
    dl_root_b = tmp_path / "dl_root_b"
    ver_a1 = _publish_minimal_v2_ducklake_snapshot(
        dl_root_a, "snap_dl_a1", revenue_scale=1.0
    )
    _publish_minimal_v2_ducklake_snapshot(dl_root_b, "snap_dl_b1", revenue_scale=1.1)

    repo_a = ParquetRepository(data_root=dl_root_a, backend="ducklake")
    repo_b = ParquetRepository(data_root=dl_root_b, backend="ducklake")
    svc_a = DashboardQueryService(repo_a)
    svc_b = DashboardQueryService(repo_b)

    snap_key_a1 = repo_a._snapshot_cache_key()
    assert snap_key_a1 is not None
    assert len(snap_key_a1) == 9
    assert snap_key_a1[0] == "ducklake"
    assert snap_key_a1[7] == "snap_dl_a1"
    assert snap_key_a1[8] == ver_a1
    parsed_a1 = _l1b_root_and_snapshot(snap_key_a1)
    assert parsed_a1 is not None
    # Verify changing catalog file identity (cat_id at index 2) changes snapshot_token.
    assert isinstance(snap_key_a1[2], int)
    recreated_key = (*snap_key_a1[:2], snap_key_a1[2] + 1, *snap_key_a1[3:])
    parsed_recreated = _l1b_root_and_snapshot(recreated_key)
    assert parsed_recreated is not None
    assert parsed_recreated[0] == parsed_a1[0]
    assert parsed_recreated[1] != parsed_a1[1]

    def _populate_all_10(svc: DashboardQueryService) -> None:
        svc.get_snapshot_overview()
        svc.get_company_profile("FPT", period_view="TTM")
        svc.get_industry_comparison(
            "FPT",
            statement_scope="CONSOLIDATED",
            period_type="ANNUAL",
            period_end_date=date(2025, 12, 31),
            metric_name="roe_total",
        )
        svc.get_peer_band_history(
            "FPT",
            statement_scope="CONSOLIDATED",
            period_type="QUARTER",
            metric_name="roe_total",
        )
        svc.list_ratio_periods("FPT", statement_scope="CONSOLIDATED")
        svc.get_ratio_lineage(
            "FPT",
            statement_scope="CONSOLIDATED",
            period_type="ANNUAL",
            period_end_date=date(2025, 12, 31),
            ratio_name="roe_total",
        )
        svc.screen_market(statement_scope="CONSOLIDATED", period_type="QUARTER")
        svc.get_stress_test_snapshot_inputs(
            statement_scope="CONSOLIDATED", period_type="QUARTER"
        )

    _populate_all_10(svc_a)
    _populate_all_10(svc_b)

    for idx, cache in enumerate(queries_mod._ALL_L1B_CACHES):
        keys_str = [repr(k) for k in cache]
        assert any("snap_dl_a1" in ks for ks in keys_str), (
            f"DuckLake L1B cache[{idx}] did not populate dl_root_a snap_dl_a1"
        )
        assert any("snap_dl_b1" in ks for ks in keys_str), (
            f"DuckLake L1B cache[{idx}] did not populate dl_root_b snap_dl_b1"
        )

    assert not any(
        BENCHMARKS_TABLE in repr(k) for k in repo_mod._SNAPSHOT_TABLE_DATA_CACHE
    )

    # Publish snap_dl_a2 on dl_root_a and query via DashboardQueryService.
    ver_a2 = _publish_minimal_v2_ducklake_snapshot(
        dl_root_a, "snap_dl_a2", revenue_scale=1.25
    )
    assert ver_a2 > ver_a1
    overview_a2 = svc_a.get_snapshot_overview()
    assert overview_a2.snapshot_id == "snap_dl_a2"
    assert len(queries_mod._SNAPSHOT_OVERVIEW_CACHE) == 2

    # ALL 10 L1B caches must have evicted snap_dl_a1 while keeping dl_root_b snap_dl_b1.
    for idx, cache in enumerate(queries_mod._ALL_L1B_CACHES):
        keys_str = [repr(k) for k in cache]
        assert not any("snap_dl_a1" in ks for ks in keys_str), (
            f"Stale DuckLake snap_dl_a1 leaked in L1B cache[{idx}] after rollover"
        )
        assert any("snap_dl_b1" in ks for ks in keys_str), (
            f"Unrelated DuckLake dl_root_b entry evicted from L1B cache[{idx}]"
        )

    # Re-querying profile on dl_root_a caches snap_dl_a2 without retaining snap_dl_a1.
    _ = svc_a.get_company_profile("FPT", period_view="TTM")
    assert len(queries_mod._COMPANY_PROFILE_CACHE) == 2
    assert not any("snap_dl_a1" in repr(k) for k in queries_mod._COMPANY_PROFILE_CACHE)
    assert any("snap_dl_a2" in repr(k) for k in queries_mod._COMPANY_PROFILE_CACHE)
    assert any("snap_dl_b1" in repr(k) for k in queries_mod._COMPANY_PROFILE_CACHE)


def test_arch02_benchmark_rollover_latency_and_bounded_cache_growth(
    tmp_path: Path,
) -> None:
    clear_repository_caches()
    clear_query_caches()
    svc = DashboardQueryService(ParquetRepository(data_root=tmp_path))

    metrics_to_query = ("roe_total", "roa", "net_profit_margin", "current_ratio")
    latencies_ms: list[float] = []
    for idx in range(1, 5):
        _publish_minimal_v2_snapshot(
            tmp_path, f"snap_arch02_roll_{idx}", revenue_scale=1.0 + 0.05 * idx
        )
        svc.get_snapshot_overview()
        for metric_name in metrics_to_query:
            t0 = time.perf_counter()
            comp = svc.get_industry_comparison(
                "FPT",
                statement_scope="CONSOLIDATED",
                period_type="ANNUAL",
                period_end_date=date(2025, 12, 31),
                metric_name=metric_name,
            )
            _ = svc.get_peer_band_history(
                "FPT",
                statement_scope="CONSOLIDATED",
                period_type="QUARTER",
                metric_name=metric_name,
            )
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            assert comp.peer_count == 2

    latencies_ms.sort()
    p95_ms = latencies_ms[int(0.95 * (len(latencies_ms) - 1))]
    assert p95_ms < 150.0, f"Benchmark query p95 too high: {p95_ms:.2f} ms"

    # Cache cardinality stays bounded at 1 snapshot after 4 rollovers.
    for cache in queries_mod._ALL_L1B_CACHES:
        assert len(cache) <= len(metrics_to_query)
    assert len(repo_mod._SNAPSHOT_TABLE_DATA_CACHE) <= len(
        repo_mod._IN_MEMORY_CACHEABLE_TABLES
    )


def test_parquet_cursor_reuses_memory_conn_skips_cache_hits_and_skips_ducklake(
    tmp_path: Path,
) -> None:
    clear_repository_caches()
    clear_query_caches()

    pq_root = tmp_path / "pq_root"
    _publish_minimal_v2_snapshot(pq_root, "snap_cursor_pq_1", revenue_scale=1.0)
    svc_warm = DashboardQueryService(ParquetRepository(data_root=pq_root))
    comp = svc_warm.get_industry_comparison(
        "FPT",
        statement_scope="CONSOLIDATED",
        period_type="ANNUAL",
        period_end_date=date(2025, 12, 31),
        metric_name="roe_total",
    )
    assert comp.peer_count == 2
    first_conn = svc_warm._parquet_conn
    assert first_conn is not None

    hist = svc_warm.get_peer_band_history(
        "FPT",
        statement_scope="CONSOLIDATED",
        period_type="QUARTER",
        metric_name="roe_total",
    )
    assert not hist.is_empty()
    assert svc_warm._parquet_conn is first_conn

    # L1B cache hits must return before opening a lazy DuckDB connection.
    svc_hit = DashboardQueryService(ParquetRepository(data_root=pq_root))
    _ = svc_hit.get_industry_comparison(
        "FPT",
        statement_scope="CONSOLIDATED",
        period_type="ANNUAL",
        period_end_date=date(2025, 12, 31),
        metric_name="roe_total",
    )
    _ = svc_hit.get_peer_band_history(
        "FPT",
        statement_scope="CONSOLIDATED",
        period_type="QUARTER",
        metric_name="roe_total",
    )
    assert svc_hit._parquet_conn is None

    svc_warm.close()
    assert svc_warm._parquet_conn is None

    # DuckLake mode must never attach SQLite catalogs onto a persistent _parquet_conn.
    dl_root = tmp_path / "dl_root"
    _publish_minimal_v2_ducklake_snapshot(
        dl_root, "snap_cursor_dl_1", revenue_scale=1.0
    )
    svc_dl = DashboardQueryService(
        ParquetRepository(data_root=dl_root, backend="ducklake")
    )
    dl_comp = svc_dl.get_industry_comparison(
        "FPT",
        statement_scope="CONSOLIDATED",
        period_type="ANNUAL",
        period_end_date=date(2025, 12, 31),
        metric_name="roe_total",
    )
    assert dl_comp.peer_count == 2
    dl_hist = svc_dl.get_peer_band_history(
        "FPT",
        statement_scope="CONSOLIDATED",
        period_type="QUARTER",
        metric_name="roe_total",
    )
    assert not dl_hist.is_empty()
    assert svc_dl._parquet_conn is None


def test_inspect_parquet_columns_polars_schema_and_corrupt_error(
    tmp_path: Path,
) -> None:
    clear_repository_caches()
    snap_dir = _publish_minimal_v2_snapshot(
        tmp_path / "pq_schema_root", "snap_schema_001", revenue_scale=1.0
    )
    repo = ParquetRepository(data_root=snap_dir)
    cols_no_conn = repo.get_table_columns(BENCHMARKS_TABLE)
    assert cols_no_conn is not None
    assert "issuer_id" in cols_no_conn
    assert "metric_or_ratio_name" in cols_no_conn

    corrupt_path = tmp_path / "corrupt.parquet"
    corrupt_path.write_bytes(b"not-a-valid-parquet-file")
    with pytest.raises(ContractViolationError):
        repo._inspect_parquet_columns(
            corrupt_path,
            ("corrupt", 1, 24),
            "corrupt.parquet",
            connection=None,
        )


def test_parquet_cursor_close_waits_for_active_cursor(tmp_path: Path) -> None:
    clear_repository_caches()
    clear_query_caches()
    pq_root = tmp_path / "pq_race"
    _publish_minimal_v2_snapshot(pq_root, "snap_close_race_1", revenue_scale=1.0)
    svc = DashboardQueryService(ParquetRepository(data_root=pq_root))

    cur_ready = threading.Event()
    close_started = threading.Event()
    close_finished = threading.Event()
    errors: list[BaseException] = []
    rows_read: list[int] = []

    def _query_worker() -> None:
        try:
            with svc.repository.snapshot_session() as pinned:
                with svc._parquet_cursor(pinned, None) as cur:
                    assert cur is not None
                    cur_ready.set()
                    assert close_started.wait(timeout=5.0)
                    time.sleep(0.05)
                    assert not close_finished.is_set()
                    frame = pinned.read_table(
                        BENCHMARKS_TABLE,
                        columns=[
                            "issuer_id",
                            "metric_or_ratio_name",
                            "period_end_date",
                        ],
                        filters={"metric_or_ratio_name": "roe_total"},
                        connection=cur,
                    )
                    rows_read.append(frame.height)
        except BaseException as exc:  # pragma: no cover - assertion captures failure
            errors.append(exc)

    def _close_worker() -> None:
        try:
            assert cur_ready.wait(timeout=5.0)
            close_started.set()
            svc.close()
            close_finished.set()
        except BaseException as exc:  # pragma: no cover - assertion captures failure
            errors.append(exc)

    t_query = threading.Thread(target=_query_worker, name="parquet-cursor-holder")
    t_close = threading.Thread(target=_close_worker, name="parquet-cursor-closer")
    t_query.start()
    t_close.start()
    t_query.join(timeout=10.0)
    t_close.join(timeout=10.0)

    assert not t_query.is_alive()
    assert not t_close.is_alive()
    assert not errors, f"Unexpected close race errors: {errors!r}"
    assert close_finished.is_set()
    assert len(rows_read) == 1 and rows_read[0] > 0
    assert svc._parquet_conn is None
    assert svc._parquet_active_cursors == {}

    # Subsequent query after close() cleanly re-initializes a fresh connection.
    comp = svc.get_industry_comparison(
        "FPT",
        statement_scope="CONSOLIDATED",
        period_type="ANNUAL",
        period_end_date=date(2025, 12, 31),
        metric_name="roe_total",
    )
    assert comp.peer_count == 2
    svc.close()
    assert svc._parquet_conn is None


def test_parquet_cursor_concurrent_queries_with_interleaved_close(
    tmp_path: Path,
) -> None:
    clear_repository_caches()
    clear_query_caches()
    pq_root = tmp_path / "pq_stress"
    _publish_minimal_v2_snapshot(pq_root, "snap_close_stress_1", revenue_scale=1.0)
    svc = DashboardQueryService(ParquetRepository(data_root=pq_root))

    stop = threading.Event()
    errors: list[BaseException] = []

    def _reader(worker_idx: int) -> None:
        try:
            for step in range(12):
                with svc.repository.snapshot_session() as pinned:
                    with svc._parquet_cursor(pinned, None) as cur:
                        frame = pinned.read_table(
                            BENCHMARKS_TABLE,
                            columns=["issuer_id", "metric_or_ratio_name"],
                            filters={"metric_or_ratio_name": "roe_total"},
                            connection=cur,
                        )
                        assert frame.height > 0, f"worker={worker_idx} step={step}"
        except BaseException as exc:  # pragma: no cover - assertion captures failure
            errors.append(exc)

    def _closer() -> None:
        try:
            while not stop.is_set():
                svc.close()
                time.sleep(0.005)
        except BaseException as exc:  # pragma: no cover - assertion captures failure
            errors.append(exc)

    readers = [threading.Thread(target=_reader, args=(i,)) for i in range(8)]
    closer = threading.Thread(target=_closer)
    closer.start()
    for t in readers:
        t.start()
    for t in readers:
        t.join(timeout=20.0)
    stop.set()
    closer.join(timeout=10.0)

    assert not errors, f"Concurrent close stress errors: {errors!r}"
    svc.close()
    assert svc._parquet_conn is None
    assert svc._parquet_active_cursors == {}
