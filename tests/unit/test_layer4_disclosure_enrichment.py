"""Unit tests for hybrid point-in-time disclosure timestamp enrichment."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path


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
from finintel.ingestion.disclosure_enricher import (
    DisclosureRecord,
    enrich_fhsc_manifest_disclosures,
    merge_disclosure_records,
    parse_cafef_bctc_disclosures,
    parse_vietcap_iq_disclosures,
)
from finintel.normalization.archive import build_normalized_batch, normalize_archive
from finintel.normalization.mapper import TaxonomyMapper
from finintel.pipeline.state_store import StateStore


def _seed_fhsc_archive(tmp_path: Path) -> RawArchive:
    state = StateStore(tmp_path / "metadata" / "state.sqlite")
    archive = RawArchive(tmp_path / "raw", state)
    fetched_at = datetime(2026, 11, 18, 7, 0, 0, tzinfo=UTC)

    # Seed Q4 2024 (UNAUDITED) and Annual 2024 (AUDITED) for FPT and POM
    for ticker, issuer_id, bs_assets in (
        ("FPT", "VN_FPT", 71_999_995_678_620.0),
        ("POM", "VN_POM", 9_870_326_167_896.0),
    ):
        q_body = {
            "error_code": "0",
            "data": [{"year": 2024, "quarter": 4, "tongcongtaisan": bs_assets}],
        }
        q_payload = RawPayload(
            content=json.dumps(q_body, separators=(",", ":")).encode("utf-8"),
            extension="json",
            source_provider="fhsc",
            connector_name="vnstock-oapi==0.2.0",
            nature="PROVIDER_RAW_JSON",
            fetched_at=fetched_at,
        )
        q_meta = FilingMetadata(
            issuer_id=issuer_id,
            ticker_at_filing=ticker,
            statement_type=StatementType.BALANCE_SHEET,
            statement_scope=StatementScope.CONSOLIDATED,
            period_type=PeriodType.QUARTER,
            fiscal_year=2024,
            fiscal_quarter=4,
            fiscal_year_start_date=date(2024, 1, 1),
            period_start_date=date(2024, 10, 1),
            period_end_date=date(2024, 12, 31),
            published_at=None,
            audit_status=AuditStatus.UNAUDITED,
            audit_opinion=AuditOpinion.NOT_APPLICABLE,
        )
        archive.store(
            RawFilingBundle(
                payload=q_payload,
                filings=(
                    RawFiling(
                        metadata=q_meta,
                        metrics={"tongcongtaisan": bs_assets},
                        locator="/data/0",
                    ),
                ),
            )
        )

        a_body = {
            "error_code": "0",
            "data": [{"year": 2024, "quarter": None, "tongcongtaisan": bs_assets}],
        }
        a_payload = RawPayload(
            content=json.dumps(a_body, separators=(",", ":")).encode("utf-8"),
            extension="json",
            source_provider="fhsc",
            connector_name="vnstock-oapi==0.2.0",
            nature="PROVIDER_RAW_JSON",
            fetched_at=fetched_at,
        )
        a_meta = FilingMetadata(
            issuer_id=issuer_id,
            ticker_at_filing=ticker,
            statement_type=StatementType.BALANCE_SHEET,
            statement_scope=StatementScope.CONSOLIDATED,
            period_type=PeriodType.ANNUAL,
            fiscal_year=2024,
            fiscal_quarter=None,
            fiscal_year_start_date=date(2024, 1, 1),
            period_start_date=date(2024, 1, 1),
            period_end_date=date(2024, 12, 31),
            published_at=None,
            audit_status=AuditStatus.AUDITED,
            audit_opinion=AuditOpinion.UNKNOWN,
        )
        archive.store(
            RawFilingBundle(
                payload=a_payload,
                filings=(
                    RawFiling(
                        metadata=a_meta,
                        metrics={"tongcongtaisan": bs_assets},
                        locator="/data/0",
                    ),
                ),
            )
        )
    return archive


def test_parse_vietcap_iq_extracts_valid_dates_and_skips_corrupt_entries() -> None:
    payload = {
        "status": 200,
        "successful": True,
        "data": {
            "years": [
                {
                    "organCode": "FPT",
                    "ticker": "FPT",
                    "yearReport": 2024,
                    "lengthReport": 5,
                    "publicDate": "2025-03-15T00:00:00",
                },
                {
                    "organCode": "FPT",
                    "ticker": "FPT",
                    "yearReport": 2023,
                    "lengthReport": 5,
                    "publicDate": None,
                },
            ],
            "quarters": [
                {
                    "organCode": "FPT",
                    "ticker": "FPT",
                    "yearReport": 2024,
                    "lengthReport": 4,
                    "publicDate": "2025-03-15T00:00:00",
                },
                {
                    "organCode": "FPT",
                    "ticker": "FPT",
                    "yearReport": 2025,
                    "lengthReport": 1,
                    "publicDate": "2025-04-23T00:00:00",
                },
                {
                    "organCode": "OTHER",
                    "ticker": "OTHER",
                    "yearReport": 2025,
                    "lengthReport": 2,
                    "publicDate": "2025-08-01T00:00:00",
                },
                {
                    "organCode": "FPT",
                    "ticker": "FPT",
                    "yearReport": 2025,
                    "lengthReport": 3,
                    "publicDate": "2027-01-01T00:00:00",
                },
            ],
        },
    }
    records = parse_vietcap_iq_disclosures(
        "FPT",
        json.dumps(payload).encode("utf-8"),
        source_url="https://iq.vietcap.com.vn/test/FPT",
        max_date=date(2026, 11, 18),
    )
    by_key = {(r.period_type, r.fiscal_year, r.fiscal_quarter): r for r in records}
    assert set(by_key) == {
        (PeriodType.ANNUAL, 2024, None),
        (PeriodType.QUARTER, 2024, 4),
        (PeriodType.QUARTER, 2025, 1),
    }
    assert by_key[(PeriodType.ANNUAL, 2024, None)].published_at == date(2025, 3, 15)
    assert by_key[(PeriodType.QUARTER, 2025, 1)].published_at == date(2025, 4, 23)


def test_parse_cafef_bctc_extracts_explicit_dates_and_never_fabricates_short_names() -> (
    None
):
    html = """
    <table>
      <tr>
        <td>Báo cáo tài chính hợp nhất năm 2025 (đã kiểm toán)</td>
        <td>CN/2025</td>
        <td><a href="https://cafefnew.mediacdn.vn/BCTC/20260319_-_FPT_-_BCTC_hop_nhat_nam_2025_da_kiem_toan_1773994300.pdf">Tải</a></td>
      </tr>
      <tr>
        <td>Báo cáo tài chính hợp nhất quý 4 năm 2024</td>
        <td>Q4/2024</td>
        <td><a href="https://cafefnew.mediacdn.vn/BCTC/0006913580cng-ty-c-phn-fpt24012025-000000bo-co.pdf">Tải</a></td>
      </tr>
      <tr>
        <td>Báo cáo tài chính hợp nhất quý 1 năm 2026</td>
        <td>Q1/2026</td>
        <td><a href="https://cafefnew.mediacdn.vn/BCTC/_615314_Bao_cao_tai_chinh_quy_12026_1778673691.pdf">Tải</a></td>
      </tr>
      <tr>
        <td>Báo cáo tài chính công ty mẹ quý 1 năm 2026</td>
        <td>Q1/2026</td>
        <td><a href="https://cafefnew.mediacdn.vn/BCTC/pom_congtyme_09062026092704.pdf">Tải</a></td>
      </tr>
      <tr>
        <td>Báo cáo tài chính hợp nhất năm 2021 (đã kiểm toán)</td>
        <td>CN/2021</td>
        <td><a href="https://cafefnew.mediacdn.vn/2021/FPT_21CN_BCTC_HNKT.pdf">Tải</a></td>
      </tr>
      <tr>
        <td>Báo cáo tài chính hợp nhất quý 2 năm 2022 (đã soát xét)</td>
        <td>Q2/2022</td>
        <td><a href="https://cafefnew.mediacdn.vn/BCTC/FPT_22Q2_BCTC_HNSX_2022-102346.pdf">Tải</a></td>
      </tr>
    </table>
    """
    records = parse_cafef_bctc_disclosures(
        "FPT",
        html.encode("utf-8"),
        source_url="https://cafef.vn/du-lieu/Ajax/CongTy/BaoCaoTaiChinh.aspx?sym=FPT",
        max_date=date(2026, 11, 18),
    )
    by_key = {
        (r.statement_scope, r.period_type, r.fiscal_year, r.fiscal_quarter): r
        for r in records
    }
    # Short names (CN/2021) and partial timestamps (Q2/2022 '2022-102346') MUST NOT be guessed
    assert (StatementScope.CONSOLIDATED, PeriodType.ANNUAL, 2021, None) not in by_key
    assert (StatementScope.CONSOLIDATED, PeriodType.QUARTER, 2022, 2) not in by_key
    # Explicit dates are extracted accurately
    assert by_key[
        (StatementScope.CONSOLIDATED, PeriodType.ANNUAL, 2025, None)
    ].published_at == date(2026, 3, 19)
    assert by_key[
        (StatementScope.CONSOLIDATED, PeriodType.QUARTER, 2024, 4)
    ].published_at == date(2025, 1, 24)
    assert by_key[
        (StatementScope.CONSOLIDATED, PeriodType.QUARTER, 2026, 1)
    ].published_at == date(2026, 5, 13)
    assert by_key[
        (StatementScope.SEPARATE, PeriodType.QUARTER, 2026, 1)
    ].published_at == date(2026, 6, 9)


def test_merge_and_enrich_manifest_upgrades_to_exact_filing_and_preserves_normalization(
    tmp_path: Path,
) -> None:
    archive = _seed_fhsc_archive(tmp_path)
    vci_url_fpt = "https://iq.vietcap.com.vn/test/FPT"
    cafef_url_fpt = "https://cafef.vn/test/FPT"
    vci_url_pom = "https://iq.vietcap.com.vn/test/POM"

    vci_records = [
        DisclosureRecord(
            ticker="FPT",
            period_type=PeriodType.ANNUAL,
            fiscal_year=2024,
            fiscal_quarter=None,
            statement_scope=StatementScope.CONSOLIDATED,
            published_at=date(2025, 3, 15),
            source_name="vietcap_iq",
            source_url=vci_url_fpt,
            raw_locator="/data/years/0",
        ),
        # Vietcap IQ overwrites 2024-Q4 with the Annual audit date (2025-03-15)
        DisclosureRecord(
            ticker="FPT",
            period_type=PeriodType.QUARTER,
            fiscal_year=2024,
            fiscal_quarter=4,
            statement_scope=StatementScope.CONSOLIDATED,
            published_at=date(2025, 3, 15),
            source_name="vietcap_iq",
            source_url=vci_url_fpt,
            raw_locator="/data/quarters/0",
        ),
        # POM late annual filing (2025-04-29 > 2025-03-31)
        DisclosureRecord(
            ticker="POM",
            period_type=PeriodType.ANNUAL,
            fiscal_year=2024,
            fiscal_quarter=None,
            statement_scope=StatementScope.CONSOLIDATED,
            published_at=date(2025, 4, 29),
            source_name="vietcap_iq",
            source_url=vci_url_pom,
            raw_locator="/data/years/0",
        ),
        # Invalid pre-period-end date must be skipped safely without crashing or fabricating
        DisclosureRecord(
            ticker="POM",
            period_type=PeriodType.QUARTER,
            fiscal_year=2024,
            fiscal_quarter=4,
            statement_scope=StatementScope.CONSOLIDATED,
            published_at=date(2024, 12, 15),
            source_name="vietcap_iq",
            source_url=vci_url_pom,
            raw_locator="/data/quarters/0",
        ),
    ]
    cafef_records = [
        # CafeF provides the true Unaudited Q4/2024 disclosure date (2025-01-24)
        DisclosureRecord(
            ticker="FPT",
            period_type=PeriodType.QUARTER,
            fiscal_year=2024,
            fiscal_quarter=4,
            statement_scope=StatementScope.CONSOLIDATED,
            published_at=date(2025, 1, 24),
            source_name="cafef_bctc",
            source_url=cafef_url_fpt,
            raw_locator="/rows/1",
        ),
    ]

    merged = merge_disclosure_records(vci_records, cafef_records)
    assert merged[
        ("FPT", PeriodType.QUARTER, 2024, 4, StatementScope.CONSOLIDATED)
    ].published_at == date(2025, 1, 24)
    assert merged[
        ("FPT", PeriodType.ANNUAL, 2024, None, StatementScope.CONSOLIDATED)
    ].published_at == date(2025, 3, 15)

    evidence_payloads = {
        vci_url_fpt: b'{"source":"vci_fpt"}',
        cafef_url_fpt: b'{"source":"cafef_fpt"}',
        vci_url_pom: b'{"source":"vci_pom"}',
    }
    summary = enrich_fhsc_manifest_disclosures(
        archive,
        list(merged.values()),
        evidence_payloads=evidence_payloads,
        fetched_at=datetime(2026, 11, 18, 8, 30, 0, tzinfo=UTC),
    )
    assert summary.upgraded_filings == 3
    assert summary.skipped_pre_period_end == 1

    with archive.state.connection() as conn:
        rows = {
            (r["ticker_at_filing"], r["period_type"], r["fiscal_quarter"]): dict(r)
            for r in conn.execute("SELECT * FROM filings_manifest").fetchall()
        }

    fpt_q4 = rows[("FPT", "QUARTER", 4)]
    assert fpt_q4["published_at"] == "2025-01-24"
    assert fpt_q4["published_at_basis"] == "EXACT_FILING"
    assert fpt_q4["is_filed_late"] == 0

    fpt_ann = rows[("FPT", "ANNUAL", None)]
    assert fpt_ann["published_at"] == "2025-03-15"
    assert fpt_ann["published_at_basis"] == "EXACT_FILING"
    assert fpt_ann["is_filed_late"] == 0

    pom_ann = rows[("POM", "ANNUAL", None)]
    assert pom_ann["published_at"] == "2025-04-29"
    assert pom_ann["published_at_basis"] == "EXACT_FILING"
    assert pom_ann["is_filed_late"] == 1
    pom_attrs = json.loads(pom_ann["provider_attrs_json"])
    receipt_sha = pom_attrs["metadata_evidence"]["published_at"]["receipt_sha256"]
    receipt_path = archive.root / "provenance" / f"{receipt_sha}.json"
    assert receipt_path.is_file()
    assert hashlib.sha256(receipt_path.read_bytes()).hexdigest() == receipt_sha

    # Invalid POM Q4 remained untouched in STATUTORY_LAG
    pom_q4 = rows[("POM", "QUARTER", 4)]
    assert pom_q4["published_at"] is None
    assert pom_q4["published_at_basis"] == "STATUTORY_LAG"

    # Verify Layer 2 normalize_archive replays the enriched FHSC archive cleanly
    mapper = TaxonomyMapper(Path(__file__).resolve().parents[2] / "config")
    facts, unmapped = normalize_archive(
        archive.state, archive.root, mapper, source="fhsc"
    )
    normalized, _ = build_normalized_batch(facts, unmapped)
    assert normalized.height >= 4
    exact_rows = normalized.filter(normalized["published_at_basis"] == "EXACT_FILING")
    assert set(exact_rows["ticker_at_filing"].to_list()) == {"FPT", "POM"}
