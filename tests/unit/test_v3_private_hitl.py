"""Unit and integration tests for V3-28..V3-30: Private Issuer VAS Scoring & HITL Triage."""

from __future__ import annotations

import importlib
import io
from datetime import date
from pathlib import Path
from typing import Any, Literal
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from finintel.private_credit.vas_excel_parser import (
    MAX_UPLOAD_BYTES,
    PrivateCreditValidationError,
    build_vas_template_xlsx,
    parse_vas_excel_file,
    parse_vas_excel_workbook,
)


def _populate_template_bytes(
    *,
    taxonomy: Literal["VAS_TT200_2014", "VAS_TT99_2025"],
    values_by_sheet_code: dict[str, dict[str, tuple[float | None, float | None]]],
) -> bytes:
    openpyxl: Any = importlib.import_module("openpyxl")
    raw = build_vas_template_xlsx(
        taxonomy_version=taxonomy,
        years=(2024, 2025),
    )
    wb = openpyxl.load_workbook(io.BytesIO(raw))
    for sheet_name, code_map in values_by_sheet_code.items():
        ws = wb[sheet_name]
        for row_idx in range(2, ws.max_row + 1):
            code = str(ws.cell(row=row_idx, column=1).value or "").strip()
            if code in code_map:
                v24, v25 = code_map[code]
                ws.cell(row=row_idx, column=4, value=v24)
                ws.cell(row=row_idx, column=5, value=v25)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _valid_tt99_fixture() -> bytes:
    return _populate_template_bytes(
        taxonomy="VAS_TT99_2025",
        values_by_sheet_code={
            "BS": {
                "100": (600_000_000_000.0, 700_000_000_000.0),
                "110": (120_000_000_000.0, 150_000_000_000.0),
                "120": (30_000_000_000.0, 40_000_000_000.0),
                "130": (250_000_000_000.0, 280_000_000_000.0),
                "131": (200_000_000_000.0, 230_000_000_000.0),
                "136": (-5_000_000_000.0, -6_000_000_000.0),
                "140": (200_000_000_000.0, 230_000_000_000.0),
                "200": (400_000_000_000.0, 500_000_000_000.0),
                "210": (20_000_000_000.0, 20_000_000_000.0),
                "211": (15_000_000_000.0, 15_000_000_000.0),
                "216": (0.0, 0.0),
                "221": (300_000_000_000.0, 350_000_000_000.0),
                "252": (30_000_000_000.0, 40_000_000_000.0),
                "260": (50_000_000_000.0, 90_000_000_000.0),
                "280": (1_000_000_000_000.0, 1_200_000_000_000.0),
                "300": (550_000_000_000.0, 650_000_000_000.0),
                "310": (350_000_000_000.0, 400_000_000_000.0),
                "311": (150_000_000_000.0, 170_000_000_000.0),
                "321": (180_000_000_000.0, 200_000_000_000.0),
                "330": (200_000_000_000.0, 250_000_000_000.0),
                "339": (180_000_000_000.0, 220_000_000_000.0),
                "400": (450_000_000_000.0, 550_000_000_000.0),
                "420": (120_000_000_000.0, 180_000_000_000.0),
                "429": (0.0, 0.0),
                "440": (1_000_000_000_000.0, 1_200_000_000_000.0),
            },
            "IS": {
                "10": (900_000_000_000.0, 1_100_000_000_000.0),
                "11": (650_000_000_000.0, 780_000_000_000.0),
                "20": (250_000_000_000.0, 320_000_000_000.0),
                "22": (8_000_000_000.0, 10_000_000_000.0),
                "23": (28_000_000_000.0, 32_000_000_000.0),
                "24": (25_000_000_000.0, 30_000_000_000.0),
                "25": (60_000_000_000.0, 70_000_000_000.0),
                "26": (45_000_000_000.0, 52_000_000_000.0),
                "30": (125_000_000_000.0, 176_000_000_000.0),
                "50": (126_000_000_000.0, 178_000_000_000.0),
                "51": (25_200_000_000.0, 35_600_000_000.0),
                "52": (0.0, 0.0),
                "60": (100_800_000_000.0, 142_400_000_000.0),
                "61": (100_800_000_000.0, 142_400_000_000.0),
            },
            "CF": {
                "02": (24_000_000_000.0, 28_000_000_000.0),
                "20": (110_000_000_000.0, 160_000_000_000.0),
                "21": (-50_000_000_000.0, -70_000_000_000.0),
                "31": (0.0, 0.0),
                "70": (120_000_000_000.0, 150_000_000_000.0),
            },
        },
    )


def test_vas_template_and_parse_tt99_and_tt200(tmp_path: Path) -> None:
    """V3-28: Parse 3-sheet 2-year VAS TT99 and TT200 workbooks with QG1 PASS."""
    tt99_path = tmp_path / "private_tt99.xlsx"
    tt99_path.write_bytes(_valid_tt99_fixture())

    parsed_tt99 = parse_vas_excel_file(
        tt99_path,
        company_name="Công ty TNHH Sản xuất Minh Long",
        tax_code="0301234567",
        taxonomy_version="AUTO",
    )
    assert parsed_tt99.taxonomy_version == "VAS_TT99_2025"
    assert parsed_tt99.periods == (date(2024, 12, 31), date(2025, 12, 31))
    assert parsed_tt99.qg1.status == "PASS"
    assert parsed_tt99.qg1.can_score is True

    fact_map = {(f.metric_id, f.period_end_date): f.value for f in parsed_tt99.facts}
    assert fact_map[("BS_TOTAL_ASSETS", date(2025, 12, 31))] == 1_200_000_000_000.0
    assert fact_map[("BS_CIP_LONG_TERM", date(2025, 12, 31))] == 40_000_000_000.0
    assert fact_map[("IS_BORROWING_COSTS", date(2025, 12, 31))] == 30_000_000_000.0
    assert fact_map[("CF_OPERATING_CASH_FLOW", date(2025, 12, 31))] == 160_000_000_000.0

    tt200_bytes = _populate_template_bytes(
        taxonomy="VAS_TT200_2014",
        values_by_sheet_code={
            "BS": {
                "100": (300_000_000_000.0, 350_000_000_000.0),
                "110": (50_000_000_000.0, 60_000_000_000.0),
                "200": (200_000_000_000.0, 250_000_000_000.0),
                "270": (500_000_000_000.0, 600_000_000_000.0),
                "300": (300_000_000_000.0, 340_000_000_000.0),
                "310": (200_000_000_000.0, 220_000_000_000.0),
                "320": (90_000_000_000.0, 100_000_000_000.0),
                "330": (100_000_000_000.0, 120_000_000_000.0),
                "338": (80_000_000_000.0, 90_000_000_000.0),
                "400": (200_000_000_000.0, 260_000_000_000.0),
                "421": (40_000_000_000.0, 70_000_000_000.0),
                "440": (500_000_000_000.0, 600_000_000_000.0),
            },
            "IS": {
                "10": (400_000_000_000.0, 480_000_000_000.0),
                "11": (300_000_000_000.0, 350_000_000_000.0),
                "20": (100_000_000_000.0, 130_000_000_000.0),
                "23": (12_000_000_000.0, 14_000_000_000.0),
                "50": (40_000_000_000.0, 55_000_000_000.0),
                "60": (32_000_000_000.0, 44_000_000_000.0),
            },
            "CF": {
                "02": (15_000_000_000.0, 18_000_000_000.0),
                "20": (45_000_000_000.0, 58_000_000_000.0),
                "70": (50_000_000_000.0, 60_000_000_000.0),
            },
        },
    )
    parsed_tt200 = parse_vas_excel_workbook(
        tt200_bytes,
        filename="tt200_input.xlsx",
        company_name="Công ty CP Thương mại Đông Á",
        taxonomy_version="VAS_TT200_2014",
    )
    assert parsed_tt200.taxonomy_version == "VAS_TT200_2014"
    assert parsed_tt200.qg1.status == "PASS"


def test_vas_excel_rejects_zip_bomb_xlsm_symlink_and_fake_extension(
    tmp_path: Path,
) -> None:
    """V3-28: Reject .xlsm, fake extensions, macros, >15MB uploads, and >50MB ZIP-bombs."""
    valid_bytes = _valid_tt99_fixture()

    with pytest.raises(PrivateCreditValidationError) as exc_xlsm:
        parse_vas_excel_workbook(
            valid_bytes, filename="macro.xlsm", company_name="Test Co"
        )
    assert exc_xlsm.value.status_code == 400

    with pytest.raises(PrivateCreditValidationError) as exc_fake:
        parse_vas_excel_workbook(
            b"not a zip file", filename="fake.xlsx", company_name="Test Co"
        )
    assert exc_fake.value.status_code == 400

    with pytest.raises(PrivateCreditValidationError) as exc_big:
        parse_vas_excel_workbook(
            b"PK\x03\x04" + b"\x00" * (MAX_UPLOAD_BYTES + 10),
            filename="oversize.xlsx",
            company_name="Test Co",
        )
    assert exc_big.value.status_code == 413

    macro_buf = io.BytesIO()
    with ZipFile(macro_buf, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("xl/vbaProject.bin", b"VBA_MACRO_PAYLOAD")
    with pytest.raises(PrivateCreditValidationError) as exc_vba:
        parse_vas_excel_workbook(
            macro_buf.getvalue(), filename="hidden_macro.xlsx", company_name="Test Co"
        )
    assert exc_vba.value.status_code == 400

    # Compressed < 15 MB but uncompressed > 50 MB (ZIP-bomb)
    bomb_buf = io.BytesIO()
    with ZipFile(bomb_buf, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("xl/sharedStrings.xml", b"A" * (52 * 1024 * 1024))
    assert len(bomb_buf.getvalue()) < MAX_UPLOAD_BYTES
    with pytest.raises(PrivateCreditValidationError) as exc_bomb:
        parse_vas_excel_workbook(
            bomb_buf.getvalue(), filename="zipbomb.xlsx", company_name="Test Co"
        )
    assert exc_bomb.value.status_code == 413

    # ZIP containing symlink entry
    sym_buf = io.BytesIO()
    with ZipFile(sym_buf, "w") as zf:
        zi = ZipInfo("xl/workbook.xml")
        zi.external_attr = 0o120777 << 16
        zf.writestr(zi, b"/etc/passwd")
    with pytest.raises(PrivateCreditValidationError) as exc_sym:
        parse_vas_excel_workbook(
            sym_buf.getvalue(), filename="symlink_entry.xlsx", company_name="Test Co"
        )
    assert exc_sym.value.status_code == 400

    # Unused tmp_path assertion for lint cleanliness
    assert tmp_path.exists()


def test_vas_excel_qg1_imbalance_blocks_scoring() -> None:
    """V3-28: Balance sheet imbalance triggers QG1 FAIL with discrepancy detail and blocks scoring."""
    bad_bytes = _populate_template_bytes(
        taxonomy="VAS_TT99_2025",
        values_by_sheet_code={
            "BS": {
                "100": (600_000_000_000.0, 700_000_000_000.0),
                "200": (400_000_000_000.0, 500_000_000_000.0),
                "280": (1_000_000_000_000.0, 1_200_000_000_000.0),
                "300": (550_000_000_000.0, 650_000_000_000.0),
                "400": (450_000_000_000.0, 500_000_000_000.0),  # 2025 off by 50B!
                "440": (1_000_000_000_000.0, 1_150_000_000_000.0),
            },
            "IS": {
                "10": (900_000_000_000.0, 1_100_000_000_000.0),
                "60": (100_000_000_000.0, 120_000_000_000.0),
            },
            "CF": {
                "02": (20_000_000_000.0, 25_000_000_000.0),
                "20": (110_000_000_000.0, 140_000_000_000.0),
            },
        },
    )
    parsed = parse_vas_excel_workbook(
        bad_bytes,
        filename="imbalanced.xlsx",
        company_name="Công ty Lệch CĐKT",
    )
    assert parsed.qg1.status == "FAIL"
    assert parsed.qg1.can_score is False
    assert any(d.rule_code == "BS_IDENTITY_MISMATCH" for d in parsed.qg1.discrepancies)
    bs_disc = next(
        d for d in parsed.qg1.discrepancies if d.rule_code == "BS_IDENTITY_MISMATCH"
    )
    assert bs_disc.abs_residual_vnd == pytest.approx(50_000_000_000.0)
