"""Unit tests for hybrid point-in-time disclosure timestamp enrichment."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path


import pytest

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
from finintel.exceptions import ContractViolationError
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
        <td><a href="https://cafefnew.mediacdn.vn/BCTC/fpt_congtyme_09062026092704.pdf">Tải</a></td>
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


def test_parse_cafef_bctc_rejects_mismatched_ticker_in_url_and_filename() -> None:
    html = """
    <table>
      <tr>
        <td>Báo cáo tài chính hợp nhất năm 2025 (đã kiểm toán)</td>
        <td>CN/2025</td>
        <td><a href="https://cafefnew.mediacdn.vn/BCTC/20260319_-_VIC_-_BCTC_hop_nhat_nam_2025_da_kiem_toan_1773994300.pdf">Tải</a></td>
      </tr>
      <tr>
        <td>Báo cáo tài chính công ty mẹ quý 1 năm 2026</td>
        <td>Q1/2026</td>
        <td><a href="https://cafefnew.mediacdn.vn/BCTC/pom_congtyme_09062026092704.pdf">Tải</a></td>
      </tr>
      <tr>
        <td>Báo cáo tài chính hợp nhất quý 1 năm 2026</td>
        <td>Q1/2026</td>
        <td><a href="https://cafefnew.mediacdn.vn/BCTC/_615314_Bao_cao_tai_chinh_quy_12026_1778673691.pdf">Tải</a></td>
      </tr>
    </table>
    """
    with pytest.raises(ContractViolationError, match="mismatches requested ticker"):
        parse_cafef_bctc_disclosures(
            "FPT",
            html.encode("utf-8"),
            source_url="https://cafef.vn/du-lieu/Ajax/CongTy/BaoCaoTaiChinh.aspx?sym=POM",
        )

    fpt_records = parse_cafef_bctc_disclosures(
        "FPT",
        html.encode("utf-8"),
        source_url="https://cafef.vn/du-lieu/Ajax/CongTy/BaoCaoTaiChinh.aspx?sym=FPT",
    )
    fpt_keys = {
        (r.statement_scope, r.period_type, r.fiscal_year, r.fiscal_quarter)
        for r in fpt_records
    }
    assert fpt_keys == {(StatementScope.CONSOLIDATED, PeriodType.QUARTER, 2026, 1)}

    pom_records = parse_cafef_bctc_disclosures(
        "POM",
        html.encode("utf-8"),
        source_url="https://cafef.vn/du-lieu/Ajax/CongTy/BaoCaoTaiChinh.aspx?sym=POM",
    )
    pom_by_key = {
        (r.statement_scope, r.period_type, r.fiscal_year, r.fiscal_quarter): r
        for r in pom_records
    }
    assert pom_by_key[
        (StatementScope.SEPARATE, PeriodType.QUARTER, 2026, 1)
    ].published_at == date(2026, 6, 9)


def test_merge_disclosure_records_audit_tier_pit_guard_and_none_safety() -> None:
    vci_url = "https://iq.vietcap.com.vn/test/FPT"
    cafef_url = "https://cafef.vn/test/FPT"

    def make_rec(
        *,
        period_type: PeriodType,
        year: int,
        quarter: int | None,
        published_at: date,
        source_name: str,
        audit_status: AuditStatus | None,
        ticker: str = "FPT",
    ) -> DisclosureRecord:
        return DisclosureRecord(
            ticker=ticker,
            period_type=period_type,
            fiscal_year=year,
            fiscal_quarter=quarter,
            statement_scope=StatementScope.CONSOLIDATED,
            published_at=published_at,
            source_name=source_name,
            source_url=vci_url if source_name == "vietcap_iq" else cafef_url,
            raw_locator="/data/0",
            audit_status=audit_status,
        )

    vci_records = [
        # 1. Annual AUDITED vs earlier CafeF UNAUDITED -> CafeF MUST BE BLOCKED
        make_rec(
            period_type=PeriodType.ANNUAL,
            year=2024,
            quarter=None,
            published_at=date(2025, 3, 20),
            source_name="vietcap_iq",
            audit_status=AuditStatus.AUDITED,
        ),
        # 2. Annual AUDITED vs earlier CafeF AUDITED -> CafeF WINS (same audit tier)
        make_rec(
            period_type=PeriodType.ANNUAL,
            year=2023,
            quarter=None,
            published_at=date(2024, 3, 25),
            source_name="vietcap_iq",
            audit_status=AuditStatus.AUDITED,
        ),
        # 3. Q2 REVIEWED vs earlier CafeF UNAUDITED -> CafeF MUST BE BLOCKED
        make_rec(
            period_type=PeriodType.QUARTER,
            year=2025,
            quarter=2,
            published_at=date(2025, 8, 15),
            source_name="vietcap_iq",
            audit_status=AuditStatus.REVIEWED,
        ),
        # 4. Q2 REVIEWED vs earlier CafeF REVIEWED -> CafeF WINS
        make_rec(
            period_type=PeriodType.QUARTER,
            year=2024,
            quarter=2,
            published_at=date(2024, 8, 20),
            source_name="vietcap_iq",
            audit_status=AuditStatus.REVIEWED,
        ),
        # 5. Q1 delayed VCI vs earlier valid CafeF -> CafeF WINS
        make_rec(
            period_type=PeriodType.QUARTER,
            year=2025,
            quarter=1,
            published_at=date(2025, 5, 10),
            source_name="vietcap_iq",
            audit_status=AuditStatus.UNAUDITED,
        ),
        # 6. Q3 valid VCI vs invalid pre-period-end CafeF -> VCI KEPT
        make_rec(
            period_type=PeriodType.QUARTER,
            year=2025,
            quarter=3,
            published_at=date(2025, 10, 25),
            source_name="vietcap_iq",
            audit_status=AuditStatus.UNAUDITED,
        ),
        # 7. Q4 VCI AUDITED (overwritten by annual) vs earlier CafeF with audit_status=None
        #    -> CafeF wins and MUST NOT falsely inherit AUDITED!
        make_rec(
            period_type=PeriodType.QUARTER,
            year=2024,
            quarter=4,
            published_at=date(2025, 3, 20),
            source_name="vietcap_iq",
            audit_status=AuditStatus.AUDITED,
        ),
        # 8. Non-calendar fiscal year HSG ANNUAL ending 2025-09-30:
        #    VCI date 2025-09-15 is pre-period-end; valid CafeF 2025-12-10 replaces it.
        make_rec(
            ticker="HSG",
            period_type=PeriodType.ANNUAL,
            year=2025,
            quarter=None,
            published_at=date(2025, 9, 15),
            source_name="vietcap_iq",
            audit_status=AuditStatus.AUDITED,
        ),
        # 9. Equal-date tie-break: VCI kept (strict <) and inherits AUDITED from CafeF
        make_rec(
            period_type=PeriodType.ANNUAL,
            year=2022,
            quarter=None,
            published_at=date(2023, 3, 20),
            source_name="vietcap_iq",
            audit_status=None,
        ),
    ]
    cafef_records = [
        make_rec(
            period_type=PeriodType.ANNUAL,
            year=2024,
            quarter=None,
            published_at=date(2025, 1, 24),
            source_name="cafef_bctc",
            audit_status=AuditStatus.UNAUDITED,
        ),
        make_rec(
            period_type=PeriodType.ANNUAL,
            year=2023,
            quarter=None,
            published_at=date(2024, 3, 18),
            source_name="cafef_bctc",
            audit_status=AuditStatus.AUDITED,
        ),
        make_rec(
            period_type=PeriodType.QUARTER,
            year=2025,
            quarter=2,
            published_at=date(2025, 7, 22),
            source_name="cafef_bctc",
            audit_status=AuditStatus.UNAUDITED,
        ),
        make_rec(
            period_type=PeriodType.QUARTER,
            year=2024,
            quarter=2,
            published_at=date(2024, 8, 12),
            source_name="cafef_bctc",
            audit_status=AuditStatus.REVIEWED,
        ),
        make_rec(
            period_type=PeriodType.QUARTER,
            year=2025,
            quarter=1,
            published_at=date(2025, 4, 22),
            source_name="cafef_bctc",
            audit_status=AuditStatus.UNAUDITED,
        ),
        make_rec(
            period_type=PeriodType.QUARTER,
            year=2025,
            quarter=3,
            published_at=date(2025, 9, 10),
            source_name="cafef_bctc",
            audit_status=AuditStatus.UNAUDITED,
        ),
        make_rec(
            period_type=PeriodType.QUARTER,
            year=2024,
            quarter=4,
            published_at=date(2025, 1, 24),
            source_name="cafef_bctc",
            audit_status=None,
        ),
        make_rec(
            ticker="HSG",
            period_type=PeriodType.ANNUAL,
            year=2025,
            quarter=None,
            published_at=date(2025, 12, 10),
            source_name="cafef_bctc",
            audit_status=AuditStatus.AUDITED,
        ),
        make_rec(
            period_type=PeriodType.ANNUAL,
            year=2022,
            quarter=None,
            published_at=date(2023, 3, 20),
            source_name="cafef_bctc",
            audit_status=AuditStatus.AUDITED,
        ),
    ]

    merged = merge_disclosure_records(
        vci_records,
        cafef_records,
        period_end_dates={
            ("HSG", PeriodType.ANNUAL, 2025, None, StatementScope.CONSOLIDATED): date(
                2025, 9, 30
            )
        },
    )

    ann_2024 = merged[
        ("FPT", PeriodType.ANNUAL, 2024, None, StatementScope.CONSOLIDATED)
    ]
    assert ann_2024.source_name == "vietcap_iq"
    assert ann_2024.published_at == date(2025, 3, 20)
    assert ann_2024.audit_status == AuditStatus.AUDITED

    ann_2023 = merged[
        ("FPT", PeriodType.ANNUAL, 2023, None, StatementScope.CONSOLIDATED)
    ]
    assert ann_2023.source_name == "cafef_bctc"
    assert ann_2023.published_at == date(2024, 3, 18)

    q2_2025 = merged[("FPT", PeriodType.QUARTER, 2025, 2, StatementScope.CONSOLIDATED)]
    assert q2_2025.source_name == "vietcap_iq"
    assert q2_2025.published_at == date(2025, 8, 15)

    q2_2024 = merged[("FPT", PeriodType.QUARTER, 2024, 2, StatementScope.CONSOLIDATED)]
    assert q2_2024.source_name == "cafef_bctc"
    assert q2_2024.published_at == date(2024, 8, 12)

    q1_2025 = merged[("FPT", PeriodType.QUARTER, 2025, 1, StatementScope.CONSOLIDATED)]
    assert q1_2025.source_name == "cafef_bctc"
    assert q1_2025.published_at == date(2025, 4, 22)

    q3_2025 = merged[("FPT", PeriodType.QUARTER, 2025, 3, StatementScope.CONSOLIDATED)]
    assert q3_2025.source_name == "vietcap_iq"
    assert q3_2025.published_at == date(2025, 10, 25)

    q4_2024 = merged[("FPT", PeriodType.QUARTER, 2024, 4, StatementScope.CONSOLIDATED)]
    assert q4_2024.source_name == "cafef_bctc"
    assert q4_2024.published_at == date(2025, 1, 24)
    assert q4_2024.audit_status is None

    hsg_ann = merged[
        ("HSG", PeriodType.ANNUAL, 2025, None, StatementScope.CONSOLIDATED)
    ]
    assert hsg_ann.source_name == "cafef_bctc"
    assert hsg_ann.published_at == date(2025, 12, 10)

    ann_2022 = merged[
        ("FPT", PeriodType.ANNUAL, 2022, None, StatementScope.CONSOLIDATED)
    ]
    assert ann_2022.source_name == "vietcap_iq"
    assert ann_2022.audit_status == AuditStatus.AUDITED


def test_enrich_manifest_guards_none_audit_status_rev2_tickers_and_calendar_recalc(
    tmp_path: Path,
) -> None:
    state = StateStore(tmp_path / "metadata" / "state.sqlite")
    archive = RawArchive(tmp_path / "raw", state)
    t1 = datetime(2026, 11, 18, 7, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 11, 18, 9, 0, 0, tzinfo=UTC)

    def seed_row(
        *,
        ticker: str,
        issuer_id: str,
        period_type: PeriodType,
        quarter: int | None,
        assets: float,
        fetched_at: datetime,
        audit_status: AuditStatus = AuditStatus.UNKNOWN,
    ) -> None:
        body = {
            "error_code": "0",
            "data": [{"year": 2024, "quarter": quarter, "tongcongtaisan": assets}],
        }
        payload = RawPayload(
            content=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            extension="json",
            source_provider="fhsc",
            connector_name="vnstock-oapi==0.1.2",
            nature="PROVIDER_RAW_JSON",
            fetched_at=fetched_at,
        )
        meta = FilingMetadata(
            issuer_id=issuer_id,
            ticker_at_filing=ticker,
            statement_type=StatementType.BALANCE_SHEET,
            statement_scope=StatementScope.CONSOLIDATED,
            period_type=period_type,
            fiscal_year=2024,
            fiscal_quarter=quarter,
            fiscal_year_start_date=date(2024, 1, 1),
            period_start_date=date(2024, 10, 1) if quarter == 4 else date(2024, 1, 1),
            period_end_date=date(2024, 12, 31),
            published_at=None,
            audit_status=audit_status,
            audit_opinion=AuditOpinion.UNKNOWN,
        )
        archive.store(
            RawFilingBundle(
                payload=payload,
                filings=(
                    RawFiling(
                        metadata=meta,
                        metrics={"tongcongtaisan": assets},
                        locator="/data/0",
                    ),
                ),
            )
        )

    # Seed FPT Q4 (rev 1 + rev 2 restatement) and FPT ANNUAL (rev 1 UNKNOWN) + POM ANNUAL (rev 1)
    seed_row(
        ticker="FPT",
        issuer_id="VN_FPT",
        period_type=PeriodType.QUARTER,
        quarter=4,
        assets=70_000.0,
        fetched_at=t1,
    )
    seed_row(
        ticker="FPT",
        issuer_id="VN_FPT",
        period_type=PeriodType.QUARTER,
        quarter=4,
        assets=72_000.0,
        fetched_at=t2,
    )
    seed_row(
        ticker="FPT",
        issuer_id="VN_FPT",
        period_type=PeriodType.ANNUAL,
        quarter=None,
        assets=72_000.0,
        fetched_at=t1,
    )
    seed_row(
        ticker="POM",
        issuer_id="VN_POM",
        period_type=PeriodType.ANNUAL,
        quarter=None,
        assets=9_000.0,
        fetched_at=t1,
    )

    vci_fpt = "https://iq.vietcap.com.vn/test/FPT"
    vci_pom = "https://iq.vietcap.com.vn/test/POM"
    enrich_time = datetime(2026, 11, 19, 8, 0, 0, tzinfo=UTC)

    records = [
        # FPT Q4 rev-1 disclosure with audit_status=None:
        # must enrich rev 1 cleanly, must NOT overwrite rev 2, must NOT create rev 3!
        DisclosureRecord(
            ticker="FPT",
            period_type=PeriodType.QUARTER,
            fiscal_year=2024,
            fiscal_quarter=4,
            statement_scope=StatementScope.CONSOLIDATED,
            published_at=date(2025, 1, 31),
            source_name="vietcap_iq",
            source_url=vci_fpt,
            raw_locator="/data/quarters/0",
            audit_status=None,
        ),
        # FPT ANNUAL with explicit AUDITED upgrades UNKNOWN -> AUDITED
        DisclosureRecord(
            ticker="FPT",
            period_type=PeriodType.ANNUAL,
            fiscal_year=2024,
            fiscal_quarter=None,
            statement_scope=StatementScope.CONSOLIDATED,
            published_at=date(2025, 3, 18),
            source_name="vietcap_iq",
            source_url=vci_fpt,
            raw_locator="/data/years/0",
            audit_status=AuditStatus.AUDITED,
        ),
        # POM record present in input, but excluded by tickers=["FPT"] filter
        DisclosureRecord(
            ticker="POM",
            period_type=PeriodType.ANNUAL,
            fiscal_year=2024,
            fiscal_quarter=None,
            statement_scope=StatementScope.CONSOLIDATED,
            published_at=date(2025, 4, 29),
            source_name="vietcap_iq",
            source_url=vci_pom,
            raw_locator="/data/years/0",
            audit_status=AuditStatus.AUDITED,
        ),
    ]
    payloads = {vci_fpt: b'{"src":"fpt"}', vci_pom: b'{"src":"pom"}'}

    summary = enrich_fhsc_manifest_disclosures(
        archive,
        records,
        evidence_payloads=payloads,
        fetched_at=enrich_time,
        tickers=["FPT"],
    )
    assert summary.matched_filings == 2
    assert summary.upgraded_filings == 2

    with archive.state.connection() as conn:
        rows = {
            (r["ticker_at_filing"], r["period_type"], r["revision_no"]): dict(r)
            for r in conn.execute("SELECT * FROM filings_manifest").fetchall()
        }

    # No spurious revision 3 was created for FPT Q4!
    assert ("FPT", "QUARTER", 3) not in rows
    fpt_q4_r1 = rows[("FPT", "QUARTER", 1)]
    fpt_q4_r2 = rows[("FPT", "QUARTER", 2)]
    assert fpt_q4_r1["published_at"] == "2025-01-31"
    assert fpt_q4_r1["audit_status"] == "UNKNOWN"
    # 2024-Q4 deadline rolled past Tet At Ty to 2025-02-03 -> 2025-01-31 is on time (0)
    assert fpt_q4_r1["is_filed_late"] == 0
    # Revision 2 was protected from revision 1's disclosure date!
    assert fpt_q4_r2["published_at"] is None
    assert fpt_q4_r2["published_at_basis"] == "STATUTORY_LAG"

    # FPT ANNUAL upgraded UNKNOWN -> AUDITED
    fpt_ann = rows[("FPT", "ANNUAL", 1)]
    assert fpt_ann["published_at"] == "2025-03-18"
    assert fpt_ann["audit_status"] == "AUDITED"

    # POM was NOT touched because tickers=["FPT"] filtered it out
    pom_ann = rows[("POM", "ANNUAL", 1)]
    assert pom_ann["published_at"] is None
    assert pom_ann["published_at_basis"] == "STATUTORY_LAG"

    # Explicit revision_no=2 disclosure DOES enrich rev 2
    rev2_rec = DisclosureRecord(
        ticker="FPT",
        period_type=PeriodType.QUARTER,
        fiscal_year=2024,
        fiscal_quarter=4,
        statement_scope=StatementScope.CONSOLIDATED,
        published_at=date(2025, 2, 10),
        source_name="vietcap_iq",
        source_url=vci_fpt,
        raw_locator="/data/quarters/0-r2",
        audit_status=AuditStatus.AUDITED,
        revision_no=2,
    )
    rev2_summary = enrich_fhsc_manifest_disclosures(
        archive,
        [rev2_rec],
        evidence_payloads=payloads,
        fetched_at=datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC),
        tickers=["FPT"],
    )
    assert rev2_summary.upgraded_filings == 1
    with archive.state.connection() as conn:
        fpt_q4_r2_after = dict(
            conn.execute(
                "SELECT * FROM filings_manifest WHERE filing_id = ?",
                (fpt_q4_r2["filing_id"],),
            ).fetchone()
        )
    assert fpt_q4_r2_after["published_at"] == "2025-02-10"
    assert fpt_q4_r2_after["is_filed_late"] == 1

    # Simulate pre-holiday-calendar stale is_filed_late=1 on FPT ANNUAL and verify
    # deterministic recomputation even when fetched_at is identical to prior run.
    with archive.state.connection() as conn:
        conn.execute(
            "UPDATE filings_manifest SET is_filed_late = 1 WHERE filing_id = ?",
            (fpt_ann["filing_id"],),
        )
    recalc_summary = enrich_fhsc_manifest_disclosures(
        archive,
        [records[1]],
        evidence_payloads=payloads,
        fetched_at=enrich_time,
        tickers=["FPT"],
    )
    assert recalc_summary.matched_filings == 1
    assert recalc_summary.upgraded_filings == 1
    with archive.state.connection() as conn:
        fpt_ann_after = dict(
            conn.execute(
                "SELECT * FROM filings_manifest WHERE filing_id = ?",
                (fpt_ann["filing_id"],),
            ).fetchone()
        )
    assert fpt_ann_after["is_filed_late"] == 0
