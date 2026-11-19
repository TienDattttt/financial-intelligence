"""End-to-end integration tests covering all 6 pipeline stages and Layer 5 Lineage Traceability."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl
import yaml  # type: ignore[import-untyped]

from dashboard.components.charts import (
    build_capital_structure_chart,
    build_growth_chart,
    build_industry_comparison_chart,
)
from dashboard.queries import ALL_RATIOS, DashboardQueryService
from finintel.pipeline.cli import main
from finintel.pipeline.state_store import StateStore
from finintel.storage.repository import ParquetRepository
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_TABLE,
    BENCHMARKS_TABLE,
    FILINGS_MANIFEST_TABLE,
    QUALITY_SUMMARY_TABLE,
    RATIOS_TABLE,
    RED_FLAGS_TABLE,
)


def _write_json_filing(path: Path, metrics: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {"raw_metric_key": code, "raw_value": value} for code, value in metrics.items()
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _build_offline_slice_config(
    workspace: Path,
) -> tuple[Path, list[dict[str, object]]]:
    """Create a complete 2-year quarterly + annual offline dataset for 2 peers (FPT, CMG)."""
    input_dir = workspace / "inputs"
    curated_dir = workspace / "data" / "curated"
    curated_dir.mkdir(parents=True, exist_ok=True)

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
    issuers.write_parquet(curated_dir / "issuers.parquet")

    quarter_windows = [
        (1, "01-01", "03-31", "04-20"),
        (2, "04-01", "06-30", "07-20"),
        (3, "07-01", "09-30", "10-20"),
        (4, "10-01", "12-31", "01-25"),
    ]
    files_cfg: list[dict[str, object]] = []
    norm_filings_cfg: dict[str, dict[str, object]] = {}

    for ticker, scale in (("FPT", 1.0), ("CMG", 0.15)):
        for year in (2024, 2025):
            # Annual Balance Sheet + Income Statement + Cash Flow
            bs_metrics = {
                "100": 50_000_000_000_000.0 * scale * (1.1 if year == 2025 else 1.0),
                "110": 10_000_000_000_000.0 * scale,
                "120": 5_000_000_000_000.0 * scale,
                "130": 14_000_000_000_000.0 * scale,
                "131": 12_000_000_000_000.0 * scale,
                "137": -500_000_000_000.0 * scale,
                "140": 2_000_000_000_000.0 * scale,
                "200": 30_000_000_000_000.0 * scale * (1.1 if year == 2025 else 1.0),
                "210": 500_000_000_000.0 * scale,
                "211": 0.0,
                "219": -50_000_000_000.0 * scale,
                "270": 80_000_000_000_000.0 * scale * (1.1 if year == 2025 else 1.0),
                "300": 40_000_000_000_000.0 * scale * (1.1 if year == 2025 else 1.0),
                "310": 30_000_000_000_000.0 * scale * (1.1 if year == 2025 else 1.0),
                "311": 3_500_000_000_000.0 * scale,
                "320": 12_000_000_000_000.0 * scale,
                "330": 10_000_000_000_000.0 * scale * (1.1 if year == 2025 else 1.0),
                "338": 4_000_000_000_000.0 * scale,
                "339": 0.0,
                "400": 40_000_000_000_000.0 * scale * (1.1 if year == 2025 else 1.0),
                "411a": 17_000_000_000_000.0 * scale,
                "415": 0.0,
                "429": 6_000_000_000_000.0 * scale,
                "440": 80_000_000_000_000.0 * scale * (1.1 if year == 2025 else 1.0),
            }
            is_metrics = {
                "10": 60_000_000_000_000.0 * scale * (1.15 if year == 2025 else 1.0),
                "11": 36_000_000_000_000.0 * scale * (1.15 if year == 2025 else 1.0),
                "20": 24_000_000_000_000.0 * scale * (1.15 if year == 2025 else 1.0),
                "23": 700_000_000_000.0 * scale,
                "30": 11_000_000_000_000.0 * scale * (1.15 if year == 2025 else 1.0),
                "50": 11_500_000_000_000.0 * scale * (1.15 if year == 2025 else 1.0),
                "51": 1_800_000_000_000.0 * scale,
                "52": -100_000_000_000.0 * scale,
                "60": 9_800_000_000_000.0 * scale * (1.15 if year == 2025 else 1.0),
                "61": 8_200_000_000_000.0 * scale * (1.15 if year == 2025 else 1.0),
                "70": 5000.0,
            }
            cf_metrics = {
                "02": 2_500_000_000_000.0 * scale,
                "20": 10_500_000_000_000.0 * scale,
                "21": -4_000_000_000_000.0 * scale,
            }
            for stmt, data_dict, suf in (
                ("BALANCE_SHEET", bs_metrics, "bs"),
                ("INCOME_STATEMENT", is_metrics, "is"),
                ("CASH_FLOW", cf_metrics, "cf"),
            ):
                fp = input_dir / f"{ticker}_{year}_annual_{suf}.json"
                _write_json_filing(fp, data_dict)
                files_cfg.append(
                    {
                        "path": str(fp),
                        "source_provider": "hose_pdf_extract",
                        "metadata": {
                            "issuer_id": ticker,
                            "ticker_at_filing": ticker,
                            "statement_type": stmt,
                            "period_type": "ANNUAL",
                            "fiscal_year": year,
                            "fiscal_year_start_date": f"{year}-01-01",
                            "period_start_date": f"{year}-01-01",
                            "period_end_date": f"{year}-12-31",
                            "statement_scope": "CONSOLIDATED",
                            "published_at": f"{year + 1}-03-20",
                            "audit_status": "AUDITED",
                            "audit_opinion": "UNMODIFIED",
                        },
                    }
                )

            # Quarterly discrete Balance Sheet, Income Statement, and Cash Flow for 4 quarters
            for q_num, q_start, q_end, pub_md in quarter_windows:
                pub_year = year + 1 if q_num == 4 else year
                q_bs = {
                    k: (v if k not in {"415"} else 0.0) for k, v in bs_metrics.items()
                }
                q_is = {
                    k: (v / 4.0 if k != "70" else 1250.0) for k, v in is_metrics.items()
                }
                q_cf = {k: v / 4.0 for k, v in cf_metrics.items()}
                for stmt, data_dict, suf in (
                    ("BALANCE_SHEET", q_bs, "bs"),
                    ("INCOME_STATEMENT", q_is, "is"),
                    ("CASH_FLOW", q_cf, "cf"),
                ):
                    fp = input_dir / f"{ticker}_{year}_q{q_num}_{suf}.json"
                    _write_json_filing(fp, data_dict)
                    files_cfg.append(
                        {
                            "path": str(fp),
                            "source_provider": "hose_pdf_extract",
                            "metadata": {
                                "issuer_id": ticker,
                                "ticker_at_filing": ticker,
                                "statement_type": stmt,
                                "period_type": "QUARTER",
                                "fiscal_year": year,
                                "fiscal_quarter": q_num,
                                "fiscal_year_start_date": f"{year}-01-01",
                                "period_start_date": f"{year}-{q_start}",
                                "period_end_date": f"{year}-{q_end}",
                                "statement_scope": "CONSOLIDATED",
                                "published_at": f"{pub_year}-{pub_md}",
                                "audit_status": "UNAUDITED",
                                "audit_opinion": "NOT_APPLICABLE",
                            },
                        }
                    )

    # Pre-compute deterministic filing IDs for unit_multiplier & cash_flow_method settings.
    from finintel.ingestion.archive import deterministic_filing_id

    for entry in files_cfg:
        meta = entry["metadata"]
        assert isinstance(meta, dict)
        fid = deterministic_filing_id(
            str(entry["source_provider"]),
            str(meta["issuer_id"]),
            str(meta["statement_type"]),
            str(meta["statement_scope"]),
            str(meta["period_type"]),
            str(meta["period_end_date"]),
            1,
        )
        setting: dict[str, object] = {"unit_multiplier": 1}
        if meta["statement_type"] == "CASH_FLOW":
            setting["cash_flow_method"] = "INDIRECT"
        norm_filings_cfg[fid] = setting

    config_dir = workspace / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_payload = {
        "state_path": "../data/metadata/state.sqlite",
        "min_free_bytes": 0,
        "tickers": ["FPT", "CMG"],
        "ingestion": {"files": files_cfg},
        "normalization": {"filings": norm_filings_cfg},
        "analytics": {
            "issuers_path": "../data/curated/issuers.parquet",
            "as_of": "2026-06-01",
        },
        "quality": {"max_critical_issuer_ratio": 0.10},
    }
    config_path.write_text(
        yaml.safe_dump(config_payload, sort_keys=False), encoding="utf-8"
    )
    return config_path, files_cfg


def test_full_end_to_end_6_stage_pipeline_and_lineage_traceability(
    tmp_path: Path,
) -> None:
    """DoD #2: Full 6-stage CLI run -> atomic snapshot -> zero-orphan Lineage Card for EVERY ratio."""
    config_path, _ = _build_offline_slice_config(tmp_path)

    exit_code = main(["--config", str(config_path), "run", "--source", "file"])
    assert exit_code == 0

    data_root = tmp_path / "data"
    store = StateStore(data_root / "metadata" / "state.sqlite")
    active = store.active_snapshot()
    assert active is not None
    snapshot_id = str(active["snapshot_id"])
    assert snapshot_id.startswith("snap_")

    # Verify all published tables exist and are readable via ParquetRepository.
    repo = ParquetRepository(data_root=data_root)
    assert repo.snapshot_id == snapshot_id
    for table_name in (
        "issuers.parquet",
        "financial_facts.parquet",
        "latest_facts.parquet",
        RATIOS_TABLE,
        BENCHMARKS_TABLE,
        RED_FLAGS_TABLE,
        QUALITY_SUMMARY_TABLE,
        AUDIT_QUARANTINE_TABLE,
        FILINGS_MANIFEST_TABLE,
    ):
        df = repo.read_table(table_name)
        assert isinstance(df, pl.DataFrame)

    ratios_df = repo.read_table(RATIOS_TABLE)
    assert not ratios_df.is_empty()
    assert repo.read_table(AUDIT_QUARANTINE_TABLE).is_empty()

    # DoD #2 — Lineage Traceability: check EVERY ratio row & ratio in published snapshot.
    service = DashboardQueryService(repo)
    overview = service.get_snapshot_overview()
    assert overview.snapshot_id == snapshot_id
    assert overview.total_issuers == 2
    assert overview.quarantined_rows == 0
    assert overview.pass_periods > 0

    for row in ratios_df.to_dicts():
        ticker = str(row["ticker"])
        scope = str(row["statement_scope"])
        p_type = str(row["period_type"])
        p_end = row["period_end_date"]

        for ratio_name in ALL_RATIOS:
            card = service.get_ratio_lineage(
                ticker,
                statement_scope=scope,
                period_type=p_type,
                period_end_date=p_end,
                ratio_name=ratio_name,
            )
            # Zero orphan filing references across EVERY published ratio.
            assert card.orphan_filing_ids == (), (
                f"Orphan filing IDs found for {ticker} {p_type} {p_end} {ratio_name}: {card.orphan_filing_ids}"
            )
            assert card.anchor_filing_id in card.source_filing_ids
            assert card.formula

            if row[ratio_name] is not None:
                assert len(card.numerator_components) > 0, (
                    f"Missing numerator lineage for {ticker} {p_type} {p_end} {ratio_name}"
                )
                assert len(card.denominator_components) > 0, (
                    f"Missing denominator lineage for {ticker} {p_type} {p_end} {ratio_name}"
                )
                for comp in (*card.numerator_components, *card.denominator_components):
                    assert comp.filing_id in card.source_filing_ids
                    assert comp.source_provider == "hose_pdf_extract"
                    assert comp.connector_name == "file_adapter_v1"
                    assert comp.published_at_basis == "EXACT_FILING"
                    assert comp.canonical_content_sha256 is not None
                    assert len(comp.canonical_content_sha256) == 64

    # Verify Q4 2025 ROE TTM Lineage Card specifically traces 4 quarterly numerators & 2 equity endpoints.
    roe_q4 = service.get_ratio_lineage(
        "FPT",
        statement_scope="CONSOLIDATED",
        period_type="QUARTER",
        period_end_date=date(2025, 12, 31),
        ratio_name="roe_total",
    )
    assert len(roe_q4.numerator_components) == 4
    assert len(roe_q4.denominator_components) == 2
    assert roe_q4.status_flag == "VALID"
    assert roe_q4.orphan_filing_ids == ()

    # Verify Page 1 (Company Profile) & Page 2 (Industry Comparison) queries + Plotly figures.
    for view in ("TTM", "Quarterly", "Annual"):
        profile = service.get_company_profile("FPT", period_view=view)
        assert profile.latest_taxonomy_version == "VAS_TT200_2014"
        assert profile.latest_quality_status == "PASS"
        assert not profile.growth_series.is_empty()
        assert not profile.capital_structure_series.is_empty()
        assert not profile.dupont_series.is_empty()
        fig_growth = build_growth_chart(profile.growth_series, period_view=view)
        fig_cap = build_capital_structure_chart(profile.capital_structure_series)
        assert len(fig_growth.data) >= 1
        assert len(fig_cap.data) >= 1

    comparison = service.get_industry_comparison(
        "FPT",
        statement_scope="CONSOLIDATED",
        period_type="ANNUAL",
        period_end_date=date(2025, 12, 31),
        metric_name="roe_total",
    )
    assert comparison.peer_count == 2
    assert comparison.industry_snapshot_date == date(2025, 1, 1)
    assert comparison.peer_median is not None
    fig_ind = build_industry_comparison_chart(comparison)
    assert len(fig_ind.data) >= 2

    # Ensure ALL_RATIOS catalog is 100% consistent.
    assert len(ALL_RATIOS) == 35


def test_end_to_end_cli_circuit_breaker_preserves_prior_active_snapshot(
    tmp_path: Path,
) -> None:
    """When a subsequent run trips the >10% CRITICAL gate, CLI fails and prior snapshot stays active."""
    config_path, _ = _build_offline_slice_config(tmp_path)
    assert main(["--config", str(config_path), "run", "--source", "file"]) == 0

    data_root = tmp_path / "data"
    repo = ParquetRepository(data_root=data_root)
    prior_snapshot_id = repo.snapshot_id
    assert prior_snapshot_id is not None

    # Corrupt CMG 2025 annual Balance Sheet so 1/2 issuers (50% > 10%) fails BS_IDENTITY_MISMATCH.
    corrupt_bs = tmp_path / "inputs" / "CMG_2025_annual_bs.json"
    raw = json.loads(corrupt_bs.read_text(encoding="utf-8"))
    for item in raw:
        if item["raw_metric_key"] == "270":
            item["raw_value"] = float(item["raw_value"]) * 1.5
    corrupt_bs.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    # Add the revision 2 normalization entry for CMG 2025 annual BS.
    from finintel.ingestion.archive import deterministic_filing_id

    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    rev2_fid = deterministic_filing_id(
        "hose_pdf_extract",
        "CMG",
        "BALANCE_SHEET",
        "CONSOLIDATED",
        "ANNUAL",
        "2025-12-31",
        2,
    )
    cfg["normalization"]["filings"][rev2_fid] = {"unit_multiplier": 1}
    config_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    # Pipeline must halt with exit_code=1 and preserve the prior active snapshot untouched.
    assert main(["--config", str(config_path), "run", "--source", "file"]) == 1
    assert ParquetRepository(data_root=data_root).snapshot_id == prior_snapshot_id
