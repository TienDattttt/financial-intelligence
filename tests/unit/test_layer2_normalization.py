"""Layer 2 contract tests with verified statutory codes and FPT source samples."""

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from finintel.exceptions import ContractViolationError
from finintel.ingestion.archive import RawArchive
from finintel.ingestion.base import (
    AuditStatus,
    FilingMetadata,
    PeriodType,
    RawFiling,
    RawFilingBundle,
    RawPayload,
    StatementScope,
    StatementType,
)
from finintel.ingestion.file_adapter import FileAdapter
from finintel.normalization.archive import normalize_archive
from finintel.normalization.mapper import TAXONOMIES, TaxonomyMapper, _alias_rows
from finintel.normalization.periods import compute_ttm, derive_quarters
from finintel.normalization.regime_resolver import resolve_regime
from finintel.normalization.restatement import map_restatement
from finintel.pipeline.cli import main
from finintel.pipeline.state_store import StateStore

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def mapper() -> TaxonomyMapper:
    return TaxonomyMapper(ROOT / "config")


def filing(
    year: int,
    quarter: int | None = None,
    *,
    ytd: bool = False,
    audit: AuditStatus = AuditStatus.UNAUDITED,
    statement: StatementType = StatementType.INCOME_STATEMENT,
) -> FilingMetadata:
    first = date(year, 1, 1)
    last_month = (quarter or 4) * 3
    return FilingMetadata(
        "VN-FPT",
        "FPT",
        statement,
        PeriodType.QUARTER if quarter is not None else PeriodType.ANNUAL,
        year,
        first,
        date(year, last_month - 2, 1) if quarter and not ytd else first,
        date(year, last_month, (31, 30, 30, 31)[(quarter or 4) - 1]),
        quarter,
        StatementScope.CONSOLIDATED,
        audit_status=audit,
    )


def test_fiscal_regime_boundary() -> None:
    assert resolve_regime(date(2025, 4, 1)) == "VAS_TT200_2014"
    assert resolve_regime(date(2026, 1, 1)) == "VAS_TT99_2025"
    assert resolve_regime(date(2025, 7, 1)) == "VAS_TT200_2014"
    assert resolve_regime(date(2026, 7, 1)) == "VAS_TT99_2025"


def test_layer4_fhsc_fields_verified_in_both_taxonomies(mapper: TaxonomyMapper) -> None:
    fixture = ROOT / "tests/fixtures/raw_samples/fhsc_fpt_layer4_taxonomy_extract.json"
    raw = json.loads(fixture.read_text(encoding="utf-8"))["rows"]
    bs_ids = {
        "BS_TRADE_RECEIVABLES_ST",
        "BS_LT_RECEIVABLES",
        "BS_TRADE_RECEIVABLES_LT",
        "BS_BAD_DEBT_PROVISION_ST",
        "BS_BAD_DEBT_PROVISION_LT",
        "BS_TRADE_PAYABLES_ST",
        "BS_CONVERTIBLE_BONDS",
        "BS_TOTAL_EQUITY",
        "BS_TREASURY_SHARES",
    }
    for i, entry in enumerate(raw):
        income = entry["page"].startswith("income")
        meta = filing(
            entry["year"],
            entry["quarter"],
            statement=StatementType.INCOME_STATEMENT
            if income
            else StatementType.BALANCE_SHEET,
        )
        mapped, unmapped = mapper.map_metrics(
            entry["raw_metrics"],
            meta,
            filing_id=f"gate0-{i}",
            source_provider="fhsc",
        )
        assert unmapped.is_empty()
        values = dict(mapped.select("metric_id", "value").rows())
        if income:
            borrowing = (
                "IS_INTEREST_EXPENSE" if entry["year"] == 2025 else "IS_BORROWING_COSTS"
            )
            assert values[borrowing] == entry["raw_metrics"]["trongdochiphilaivay"]
            assert (
                values["IS_PROFIT_BEFORE_TAX"]
                == entry["raw_metrics"]["tongloinhuanketoantruocthue"]
            )
            assert (
                values["IS_CURRENT_INCOME_TAX"]
                == entry["raw_metrics"]["chiphithuetndnhienhanh"]
            )
            assert (
                values["IS_DEFERRED_INCOME_TAX"]
                == entry["raw_metrics"]["chiphithuetndnhoanlai"]
            )
            assert (
                values["IS_NET_INCOME"]
                == entry["raw_metrics"]["loinhuansauthuethunhapdoanhnghiep"]
            )
            assert values["IS_DEFERRED_INCOME_TAX"] < 0
            assert (
                values["IS_PROFIT_BEFORE_TAX"]
                - values["IS_CURRENT_INCOME_TAX"]
                - values["IS_DEFERRED_INCOME_TAX"]
                == values["IS_NET_INCOME"]
            )
        else:
            assert values.keys() == bs_ids
            assert (
                values["BS_TRADE_RECEIVABLES_ST"]
                == entry["raw_metrics"]["phaithukhachhang"]
            )
            assert (
                values["BS_LT_RECEIVABLES"]
                == entry["raw_metrics"]["cackhoanphaithudaihan"]
            )
            assert (
                values["BS_TRADE_RECEIVABLES_LT"]
                == entry["raw_metrics"]["phaithudaihancuakhachhang"]
            )
            assert (
                values["BS_BAD_DEBT_PROVISION_ST"]
                == entry["raw_metrics"]["duphongphaithunganhankhodoi"]
            )
            assert (
                values["BS_BAD_DEBT_PROVISION_LT"]
                == entry["raw_metrics"]["duphongphaithukhodoi"]
            )
            assert (
                values["BS_TRADE_PAYABLES_ST"]
                == entry["raw_metrics"]["phaitranguoibannganhan"]
            )
            assert (
                values["BS_CONVERTIBLE_BONDS"]
                == entry["raw_metrics"]["traiphieuchuyendoi"]
            )
            assert values["BS_TOTAL_EQUITY"] == entry["raw_metrics"]["vonchusohuu"]
            assert values["BS_TREASURY_SHARES"] == entry["raw_metrics"]["cophieuquy"]
            assert (
                mapped.filter(pl.col("metric_id") == "BS_TOTAL_EQUITY")[
                    "raw_metric_key"
                ].item()
                == "vonchusohuu_tong"
            )


@pytest.mark.parametrize(
    ("year", "short_code", "long_code"), [(2025, "137", "219"), (2026, "136", "216")]
)
def test_receivable_allowances_map_statutory_codes_and_exact_note_keys(
    mapper: TaxonomyMapper,
    year: int,
    short_code: str,
    long_code: str,
) -> None:
    metadata = filing(year, statement=StatementType.BALANCE_SHEET)
    keyed = {
        short_code: "(20)",
        long_code: "(30)",
        "BS_TRADE_RECEIVABLES_ALLOWANCE_ST": 10,
        "BS_TRADE_RECEIVABLES_ALLOWANCE_LT": 15,
    }
    mapped, unmapped = mapper.map_metrics(
        keyed, metadata, filing_id="filing-notes", source_provider="issuer"
    )
    assert unmapped.is_empty()
    assert dict(mapped.select("metric_id", "value").rows()) == {
        "BS_BAD_DEBT_PROVISION_ST": -20.0,
        "BS_BAD_DEBT_PROVISION_LT": -30.0,
        "BS_TRADE_RECEIVABLES_ALLOWANCE_ST": 10.0,
        "BS_TRADE_RECEIVABLES_ALLOWANCE_LT": 15.0,
    }


@pytest.mark.parametrize(("filename", "code_key"), tuple(TAXONOMIES.values()))
@pytest.mark.parametrize(
    "invalid_configuration",
    ["statutory_code", "taxonomy_code", "taxonomy_alias", "missing_entry"],
)
def test_alias_rows_rejects_invalid_filing_note_configurations(
    tmp_path: Path, filename: str, code_key: str, invalid_configuration: str
) -> None:
    metric_id = "BS_TRADE_RECEIVABLES_ALLOWANCE_ST"
    definition: dict[str, object] = {
        "canonical_id": metric_id,
        "statement_type": "BALANCE_SHEET",
        "value_type": "STOCK",
        "unit": "VND",
        "sign_convention": "POSITIVE_NORMAL",
        "filing_note": True,
        "comparable_across_taxonomy": True,
    }
    entry: dict[str, object] = {}
    if invalid_configuration == "statutory_code":
        definition[code_key] = "999"
    elif invalid_configuration == "taxonomy_code":
        entry["code"] = "999"
    elif invalid_configuration == "taxonomy_alias":
        entry["fhsc"] = ["unverified_note_field"]

    (tmp_path / "metric_definitions.yaml").write_text(
        json.dumps({"metrics": {metric_id: definition}}), encoding="utf-8"
    )
    taxonomy_dir = tmp_path / "taxonomy"
    taxonomy_dir.mkdir()
    for taxonomy_version, (taxonomy_name, _) in TAXONOMIES.items():
        metrics: dict[str, dict[str, object]] = {metric_id: {}}
        if taxonomy_name == filename:
            metrics = (
                {} if invalid_configuration == "missing_entry" else {metric_id: entry}
            )
        (taxonomy_dir / taxonomy_name).write_text(
            json.dumps({"taxonomy_version": taxonomy_version, "metrics": metrics}),
            encoding="utf-8",
        )

    message = (
        "Taxonomy does not match the canonical definitions"
        if invalid_configuration == "missing_entry"
        else f"Invalid definition for canonical metric {metric_id}"
    )
    with pytest.raises(ContractViolationError, match=message):
        _alias_rows(tmp_path)


def test_ttm_q4_lineage_and_nonadditive_eps(mapper: TaxonomyMapper) -> None:
    quarters = [(filing(2025, q), 10) for q in range(1, 5)]
    explicit = compute_ttm(facts(mapper, quarters))
    assert explicit["ttm_value"][-1] == 40
    assert explicit["ttm_contains_derived_q4"].to_list() == [False] * 4
    derived = compute_ttm(
        facts(mapper, [*quarters[:3], (filing(2025, audit=AuditStatus.AUDITED), 48)])
    )
    assert derived["ttm_value"][-1] == 48
    assert derived["ttm_contains_derived_q4"].to_list() == [False, False, False, True]

    unresolved = facts(mapper, quarters).with_columns(
        pl.when(pl.col("fiscal_quarter") == 4)
        .then(None)
        .otherwise(pl.col("derivation_method"))
        .alias("derivation_method")
    )
    result = compute_ttm(unresolved).sort("fiscal_quarter")
    assert result["ttm_value"][-1] is None
    assert result["ttm_contains_derived_q4"][-1] is False
    eps = facts(mapper, quarters, code="70")
    assert eps["value_type"].unique().to_list() == ["NON_ADDITIVE"]
    assert derive_quarters(eps).is_empty()
    assert compute_ttm(eps).is_empty()


def test_exact_codes_units_and_unmapped(mapper: TaxonomyMapper) -> None:
    old = replace(
        filing(2025, 4, statement=StatementType.BALANCE_SHEET),
        fiscal_year_start_date=date(2025, 4, 1),
        period_start_date=date(2026, 1, 1),
        period_end_date=date(2026, 3, 31),
    )
    metrics, unmapped = mapper.map_metrics(
        {"270": "3.5", "Phải thu nội bộ đặc thù XYZ": None},
        old,
        filing_id="old",
        source_provider="issuer",
        unit_multiplier=1000,
    )
    assert metrics.select("metric_id", "value", "unit_multiplier").row(0) == (
        "BS_TOTAL_ASSETS",
        3500.0,
        1000,
    )
    assert metrics.schema["value"] == pl.Float64
    assert unmapped.select("filing_id", "raw_metric_key").rows() == [
        ("old", "Phải thu nội bộ đặc thù XYZ"),
    ]
    new, _ = mapper.map_metrics(
        {"270": 90, "280": 100},
        filing(2026, 1, statement=StatementType.BALANCE_SHEET),
        filing_id="new",
        source_provider="issuer",
    )
    assert dict(new.select("metric_id", "value").rows()) == {
        "BS_OTHER_NONCURRENT_ASSETS": 90.0,
        "BS_TOTAL_ASSETS": 100.0,
    }


def test_cash_flow_disambiguation_and_signs(mapper: TaxonomyMapper) -> None:
    cf = filing(2026, statement=StatementType.CASH_FLOW)
    direct, _ = mapper.map_metrics(
        {"02": "12", "20": "-8", "21": None},
        cf,
        filing_id="cf-d",
        source_provider="issuer",
        cash_flow_method="DIRECT",
    )
    assert dict(direct.select("metric_id", "value").rows()) == {
        "CF_SUPPLIER_PAYMENTS": -12.0,
        "CF_OPERATING_CASH_FLOW": -8.0,
        "CF_CAPITAL_EXPENDITURE": None,
    }
    indirect, unmapped = mapper.map_metrics(
        {"02": "(12)"},
        cf,
        filing_id="cf-i",
        source_provider="issuer",
        cash_flow_method="INDIRECT",
    )
    assert indirect.select("metric_id", "value").row(0) == (
        "CF_DEPRECIATION_AMORTIZATION",
        12.0,
    )
    assert unmapped.is_empty()
    unknown, unmapped = mapper.map_metrics(
        {"02": 12},
        cf,
        filing_id="cf-u",
        source_provider="issuer",
    )
    assert unknown.is_empty()
    assert unmapped["raw_metric_key"].to_list() == ["02"]


def test_finance_codes_follow_the_reporting_regime(mapper: TaxonomyMapper) -> None:
    amounts = {"21": -3, "22": 10, "23": 4, "52": -2}
    old, _ = mapper.map_metrics(
        amounts, filing(2025), filing_id="old", source_provider="issuer"
    )
    current, _ = mapper.map_metrics(
        amounts, filing(2026), filing_id="new", source_provider="issuer"
    )
    assert dict(old.select("metric_id", "value").rows()) == {
        "IS_FINANCIAL_REVENUE": -3.0,
        "IS_FINANCIAL_EXPENSES": 10.0,
        "IS_INTEREST_EXPENSE": 4.0,
        "IS_DEFERRED_INCOME_TAX": -2.0,
    }
    assert dict(current.select("metric_id", "value").rows()) == {
        "IS_INVESTMENT_PROPERTY_DISPOSAL_GAIN_LOSS": -3.0,
        "IS_FINANCIAL_REVENUE": 10.0,
        "IS_FINANCIAL_EXPENSES": 4.0,
        "IS_DEFERRED_INCOME_TAX": -2.0,
    }


@pytest.mark.parametrize("value", [True, "1.000,20", {"amount": 1}, float("inf")])
def test_invalid_mapped_scalar_fails_fast(
    mapper: TaxonomyMapper, value: object
) -> None:
    with pytest.raises(ContractViolationError):
        mapper.map_metrics(
            {"10": value}, filing(2025, 1), filing_id="bad", source_provider="issuer"
        )


def facts(
    mapper: TaxonomyMapper,
    entries: Sequence[tuple[FilingMetadata, int | None]],
    code: str = "10",
) -> pl.DataFrame:
    return pl.concat(
        [
            mapper.map_metrics(
                {code: value},
                meta,
                filing_id=f"filing-{i}",
                source_provider="issuer",
            )[0]
            for i, (meta, value) in enumerate(entries)
        ]
    )


def test_ytd_diff_requires_audit_and_duration(mapper: TaxonomyMapper) -> None:
    q1, q2, q3 = (filing(2025, q, ytd=True) for q in range(1, 4))
    assert derive_quarters(facts(mapper, [(q1, 10), (q2, 26), (q3, 32)])).select(
        "fiscal_quarter", "value", "derivation_method"
    ).rows() == [
        (1, 10.0, "REPORTED_DIRECTLY"),
        (2, 16.0, "DERIVED_YTD_DIFF"),
        (3, 6.0, "DERIVED_YTD_DIFF"),
    ]
    assert derive_quarters(
        facts(
            mapper,
            [
                (q1, 10),
                (replace(q2, audit_status=AuditStatus.REVIEWED), 26),
                (replace(q2, period_end_date=date(2025, 6, 12)), 26),
            ],
        )
    )["fiscal_quarter"].to_list() == [1]


def test_ytd_retains_latest_revision_per_audit_status(mapper: TaxonomyMapper) -> None:
    unaudited = replace(filing(2025, 1), published_at=date(2025, 4, 15))
    reviewed = replace(
        unaudited,
        audit_status=AuditStatus.REVIEWED,
        published_at=date(2025, 5, 1),
    )
    q2 = filing(2025, 2, ytd=True)
    table = facts(mapper, [(unaudited, 10), (reviewed, 12), (q2, 26)])
    assert derive_quarters(table).filter(pl.col("fiscal_quarter") == 2).select(
        "value", "audit_status", "source_filing_ids"
    ).row(0) == (16.0, AuditStatus.UNAUDITED, ["filing-0", "filing-2"])


def test_preexisting_derived_quarter_cannot_override_recalculation(
    mapper: TaxonomyMapper,
) -> None:
    q1, q2 = [filing(2025, q, ytd=True) for q in (1, 2)]
    original = facts(mapper, [(q1, 10), (q2, 26)])
    stored = derive_quarters(original).filter(pl.col("fiscal_quarter") == 2)
    revised = pl.concat([facts(mapper, [(q1, 12), (q2, 30)]), stored])
    assert derive_quarters(revised).tail(1).select("value", "derivation_method").row(
        0
    ) == (18.0, "DERIVED_YTD_DIFF")
    direct_q2 = facts(mapper, [(filing(2025, 2), 20)])
    collision = pl.concat([revised, direct_q2])
    assert derive_quarters(collision).filter(pl.col("fiscal_quarter") == 2).select(
        "value", "derivation_method"
    ).row(0) == (20.0, "REPORTED_DIRECTLY")


def test_audited_q4_fallback_and_filing_availability(mapper: TaxonomyMapper) -> None:
    q1, q2, q3, q4 = (filing(2025, q) for q in range(1, 5))
    q2 = replace(q2, audit_status=AuditStatus.REVIEWED)
    annual = replace(
        filing(2025, audit=AuditStatus.AUDITED), published_at=date(2026, 3, 19)
    )
    table = facts(mapper, [(q1, 10), (q2, 20), (q3, 30), (annual, 105)])
    assert derive_quarters(table, as_of=date(2026, 3, 18))[
        "fiscal_quarter"
    ].to_list() == [1, 2, 3]
    assert derive_quarters(table).tail(1).select(
        "value", "derivation_method", "effective_available_at", "source_filing_ids"
    ).row(0) == (
        45.0,
        "DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX",
        date(2026, 3, 19),
        ["filing-0", "filing-1", "filing-2", "filing-3"],
    )
    with_q4 = pl.concat([table, facts(mapper, [(q4, 40)])])
    assert derive_quarters(with_q4).tail(1)["value"].item() == 40


def test_ttm_requires_four_non_null_contiguous_quarters(mapper: TaxonomyMapper) -> None:
    q1, q2, q3, q4 = (filing(2025, q) for q in range(1, 5))
    for entries in (
        [(q1, 1), (q2, 2), (q4, 4)],
        [(q1, 1), (q2, 2), (q3, None), (q4, 4)],
        [
            (q1, 1),
            (q2, 2),
            (replace(q3, period_start_date=date(2025, 6, 30)), 3),
            (q4, 4),
        ],
        [
            (q1, 1),
            (q2, 2),
            (replace(q3, period_start_date=date(2025, 7, 2)), 3),
            (q4, 4),
        ],
        [
            (q1, 1),
            (q2, 2),
            (replace(q3, statement_scope=StatementScope.SEPARATE), 3),
            (q4, 4),
        ],
    ):
        assert (
            compute_ttm(facts(mapper, entries))
            .filter(pl.col("fiscal_quarter") == 4)["ttm_value"]
            .item()
            is None
        )
    assert (
        compute_ttm(facts(mapper, [(q1, 1), (q2, 2), (q3, 3), (q4, 4)]))
        .tail(1)["ttm_value"]
        .item()
        == 10
    )


def test_unresolvable_cumulative_q4_has_null_ttm(mapper: TaxonomyMapper) -> None:
    q1, q2, q4 = [filing(2025, q, ytd=True) for q in (1, 2, 4)]
    table = facts(mapper, [(q1, 10), (q2, 21), (q4, 46)])
    assert compute_ttm(table).filter(pl.col("fiscal_quarter") == 4).select(
        "value", "ttm_value", "computation_note"
    ).row(0) == (None, None, None)


def test_cross_taxonomy_ttm_uses_compatible_quarters(mapper: TaxonomyMapper) -> None:
    q3, q4, q1, q2 = [
        filing(y, q) for y, q in ((2025, 3), (2025, 4), (2026, 1), (2026, 2))
    ]
    entries = [(q3, 3), (q4, 4), (q1, 5), (q2, 6)]
    assert compute_ttm(facts(mapper, entries)).tail(1)["ttm_value"].item() == 18
    table = facts(mapper, entries, "30")
    assert compute_ttm(table).tail(1).select("ttm_value", "computation_note").row(
        0
    ) == (
        None,
        "MIXED_TAXONOMY",
    )
    comparative, _ = map_restatement(
        mapper,
        {"30": 100},
        filing(2026),
        filing(2025),
        filing_id="annual-2026",
        source_provider="issuer",
    )
    assert (
        compute_ttm(pl.concat([table, comparative])).tail(1)["ttm_value"].item() is None
    )
    restated = [
        map_restatement(
            mapper,
            {"30": value},
            filing(2026, meta.fiscal_quarter),
            meta,
            filing_id=f"restated-{meta.fiscal_quarter}",
            source_provider="issuer",
        )[0]
        for meta, value in ((q3, 30), (q4, 40))
    ]
    combined = pl.concat([table, *restated])
    assert (
        compute_ttm(combined, as_of=date(2026, 8, 1)).tail(1)["ttm_value"].item()
        is None
    )
    assert (
        compute_ttm(combined)
        .filter(pl.col("period_end_date") == q2.period_end_date)["ttm_value"]
        .item()
        == 81
    )


def test_restatement_retains_2026_source_date_and_2025_coverage(
    mapper: TaxonomyMapper,
) -> None:
    current = replace(filing(2026), published_at=date(2027, 3, 10))
    original = facts(mapper, [(filing(2025), 100)])
    restated, _ = map_restatement(
        mapper,
        {"10": 120},
        current,
        filing(2025),
        filing_id="2026-audit",
        source_provider="issuer",
        revision_no=2,
    )
    assert pl.concat([original, restated]).select(
        "fiscal_year",
        "taxonomy_version",
        "revision_reason",
        "effective_available_at",
        "filing_id",
        "revision_no",
    ).rows() == [
        (2025, "VAS_TT200_2014", "ORIGINAL", date(2026, 3, 31), "filing-0", 1),
        (
            2025,
            "VAS_TT99_2025",
            "RESTATED_REGIME_CHANGE",
            date(2027, 3, 10),
            "2026-audit",
            2,
        ),
    ]


def test_partial_restatement_preserves_complete_original_ttm(
    mapper: TaxonomyMapper,
) -> None:
    quarters = [filing(2025, q) for q in range(1, 5)]
    original = facts(mapper, [(meta, 10) for meta in quarters], "30")
    comparative, _ = map_restatement(
        mapper,
        {"30": 40},
        filing(2026, 4),
        quarters[-1],
        filing_id="restated-q4",
        source_provider="issuer",
    )
    assert compute_ttm(pl.concat([original, comparative])).tail(1).select(
        "ttm_value", "taxonomy_version"
    ).row(0) == (40.0, "VAS_TT200_2014")


def test_fhsc_exact_metric_fixture(mapper: TaxonomyMapper) -> None:
    payload = json.loads(
        (ROOT / "tests/fixtures/raw_samples/fhsc_fpt_bs_2026q2.json").read_bytes()
    )
    metrics = {
        key: value
        for key, value in payload["data"][0].items()
        if key not in {"year", "quarter"}
    }
    frame, _ = mapper.map_metrics(
        metrics,
        filing(2026, 2, statement=StatementType.BALANCE_SHEET),
        filing_id="fhsc-fpt",
        source_provider="fhsc",
    )
    assert (
        frame.filter(pl.col("metric_id") == "BS_TOTAL_ASSETS")["value"].item()
        == 73563105859500
    )


def test_fhsc_annual_flows_match_fpt_filing(mapper: TaxonomyMapper) -> None:
    samples = ROOT / "tests/fixtures/raw_samples"
    source = json.loads(
        (samples / "fhsc_fpt_2025_annual_flow_extract.json").read_bytes()
    )
    reference = json.loads((samples / "fpt_2025_spike_reference.json").read_bytes())
    expected = reference["audited_annual_2025"]["values_vnd"]
    for name, statement, metrics in (
        (
            "income-statement",
            StatementType.INCOME_STATEMENT,
            {"IS_NET_REVENUE": "net_revenue", "IS_NET_INCOME": "net_income"},
        ),
        (
            "cash-flow",
            StatementType.CASH_FLOW,
            {"CF_OPERATING_CASH_FLOW": "operating_cash_flow"},
        ),
    ):
        raw = source[f"{name}-year-001.json"]
        assert raw.pop("year") == 2025
        mapped, unmapped = mapper.map_metrics(
            raw,
            filing(2025, statement=statement, audit=AuditStatus.AUDITED),
            filing_id=name,
            source_provider="fhsc",
        )
        assert unmapped.is_empty()
        for metric, expected_key in metrics.items():
            assert (
                mapped.filter(pl.col("metric_id") == metric)["value"].item()
                == expected[expected_key]["value"]
            )


def test_normalize_cli_replays_verified_fhsc_page(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = (ROOT / "tests/fixtures/raw_samples/fhsc_fpt_bs_2026q2.json").read_bytes()
    row = json.loads(payload)["data"][0]
    archive_root = tmp_path / "raw"
    state = StateStore(tmp_path / "metadata/state.sqlite")
    archive = RawArchive(archive_root, state)
    (stored,) = archive.store(
        RawFilingBundle(
            RawPayload(
                payload,
                "json",
                "fhsc",
                "vnstock-oapi==0.1.2",
                "PROVIDER_RAW_JSON",
                datetime.now(UTC),
            ),
            (
                RawFiling(
                    filing(2026, 2, statement=StatementType.BALANCE_SHEET),
                    {
                        key: value
                        for key, value in row.items()
                        if key not in {"year", "quarter"}
                    },
                    "/data/0",
                ),
            ),
        )
    )
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        "state_path: metadata/state.sqlite\nmin_free_bytes: 0\n", encoding="utf-8"
    )
    args = [
        "--config",
        str(settings),
        "run",
        "--stage",
        "normalize",
        "--period",
        "2026-Q2",
    ]
    assert (
        main(args, connectivity_check=lambda: pytest.fail("Replay must work offline"))
        == 0
    )
    batches = tmp_path / "curated/financial_facts"
    (batch,) = batches.iterdir()
    assert pl.read_parquet(batch / "financial_facts.parquet").filter(
        pl.col("metric_id") == "BS_TOTAL_ASSETS"
    ).select("filing_id", "value").row(0) == (stored.filing_id, 73563105859500.0)
    assert pl.read_parquet(batch / "unmapped_metrics.parquet")[
        "filing_id"
    ].to_list() == [stored.filing_id]
    (raw_file,) = archive_root.glob("sha256/*/*.json")
    raw_file.write_bytes(payload + b" ")
    capsys.readouterr()
    assert main(args) == 1
    assert "Raw filing bytes differ from the manifest hash." in capsys.readouterr().err
    assert list(batches.iterdir()) == [batch]


@pytest.mark.parametrize(
    ("manifest_edits", "filing_settings", "message"),
    [
        (
            {"raw_archive_path": "../income.csv"},
            {"unit_multiplier": 1},
            "Raw filing path is outside its archive",
        ),
        (
            {"canonical_content_sha256": "0" * 64},
            {"unit_multiplier": 1},
            "Raw metrics differ from the filing manifest hash",
        ),
        (
            {"connector_name": "unsupported"},
            {"unit_multiplier": 1},
            "Unsupported raw filing connector",
        ),
        ({}, {}, "Normalization requires integer unit_multiplier"),
    ],
)
def test_file_archive_enforces_manifest_guards_and_required_unit(
    mapper: TaxonomyMapper,
    tmp_path: Path,
    manifest_edits: dict[str, str],
    filing_settings: dict[str, int],
    message: str,
) -> None:
    source = tmp_path / "income.csv"
    source.write_text("raw_metric_key,raw_value\n10,135\n", encoding="utf-8")
    state = StateStore(tmp_path / "metadata/state.sqlite")
    root = tmp_path / "raw"
    (stored,) = RawArchive(root, state).store(
        next(FileAdapter(source, filing(2025), source_provider="issuer").fetch())
    )
    with state.connection() as conn:
        for key, value in manifest_edits.items():
            conn.execute(
                f"UPDATE filings_manifest SET {key} = ? WHERE filing_id = ?",
                (value, stored.filing_id),
            )
    with pytest.raises(ContractViolationError, match=message):
        normalize_archive(
            state,
            root,
            mapper,
            source="file",
            filing_settings={stored.filing_id: filing_settings},
        )


def test_normalize_cli_offline_comparative_column(tmp_path: Path) -> None:
    source = tmp_path / "income.csv"
    source.write_text("raw_metric_key,raw_value,prior\n10,135,120\n", encoding="utf-8")
    current = replace(filing(2026), published_at=date(2027, 3, 10))
    archive = RawArchive(
        tmp_path / "raw", StateStore(tmp_path / "metadata/state.sqlite")
    )
    (stored,) = archive.store(
        next(FileAdapter(source, current, source_provider="issuer").fetch())
    )
    source.unlink()
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        f"""state_path: metadata/state.sqlite
min_free_bytes: 0
normalization:
  filings:
    {stored.filing_id}:
      unit_multiplier: 1000
      comparative:
        value_column: prior
        fiscal_year: 2025
        fiscal_year_start_date: 2025-01-01
        period_type: ANNUAL
        period_start_date: 2025-01-01
        period_end_date: 2025-12-31
""",
        encoding="utf-8",
    )
    assert (
        main(
            [
                "--config",
                str(settings),
                "run",
                "--stage",
                "normalize",
                "--source",
                "file",
                "--period",
                "2026",
            ]
        )
        == 0
    )
    (path,) = (tmp_path / "curated/financial_facts").glob("*/financial_facts.parquet")
    assert pl.read_parquet(path).sort("fiscal_year").select(
        "fiscal_year",
        "value",
        "effective_available_at",
        "filing_id",
    ).rows() == [
        (2025, 120000.0, date(2027, 3, 10), stored.filing_id),
        (2026, 135000.0, date(2027, 3, 10), stored.filing_id),
    ]
    assert (
        main(
            [
                "--config",
                str(settings),
                "run",
                "--stage",
                "normalize",
                "--source",
                "file",
                "--period",
                "2025",
            ]
        )
        == 0
    )
    (older,) = set(path.parent.parent.glob("*/financial_facts.parquet")) - {path}
    assert pl.read_parquet(older)["fiscal_year"].to_list() == [2025]
