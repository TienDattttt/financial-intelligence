"""Formula-safe, in-memory multi-sheet Excel (.xlsx) workbook builder for FinIntel V3.

Verified Sources:
- spec/v3/SPEC-v3-export-and-tearsheet.md (§4, §5.2, §6)
- XlsxWriter Documentation (``xlsxwriter==3.2.2``, ``in_memory=True`` BytesIO streaming):
  https://xlsxwriter.readthedocs.io/working_with_memory_and_performance.html
- OWASP CSV / Spreadsheet Formula Injection Prevention Cheat Sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/CSV_Injection_Prevention_Cheat_Sheet.html

Guardrails:
- ``xlsxwriter`` is strictly lazy-imported inside builder functions so offline CI
  (``--group ml --dev`` without ``platform``) can import ``finintel.serving.exports``.
- Zero financial formulas calculated here; reads pre-computed Layer 4A/4B/5 payloads only.
- Missing accounting values (``None`` / non-finite) remain blank Excel cells, never ``0``.
"""

from __future__ import annotations

import importlib
import io
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import polars as pl

from finintel.domain.compliance import (
    DEFAULT_COMPLIANCE_NOTICE,
)
from finintel.serving.queries._cache import CompanyProfileData

COMPANY_SHEET_NAMES: tuple[str, str, str, str] = (
    "Tong_Quan_Rui_Ro",
    "BCTC_Chuan_Hoa",
    "Chi_So_Va_DuPont",
    "Co_Do_Va_Nguon_Du_Lieu",
)
SCREENER_SHEET_NAME = "Sang_Loc_Doanh_Nghiep"
MAX_SCREENER_EXPORT_ROWS = 2000
MAX_COMPANY_EXPORT_PERIODS = 20

ACCOUNTING_NUMBER_FORMAT = '#,##0.0;(#,##0.0);"-"'
RATIO_NUMBER_FORMAT = '#,##0.00;(#,##0.00);"-"'
PERCENT_NUMBER_FORMAT = "0.0%"
INTEGER_NUMBER_FORMAT = '#,##0;(#,##0);"-"'

_FORMULA_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")

_STATEMENT_TITLES_VI: dict[str, str] = {
    "BALANCE_SHEET": "BẢNG CÂN ĐỐI KẾ TOÁN",
    "INCOME_STATEMENT": "BÁO CÁO KẾT QUẢ KINH DOANH",
    "CASH_FLOW": "BÁO CÁO LƯU CHUYỂN TIỀN TỆ",
}

_SCREENER_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("ticker", "Mã CK", "text"),
    ("company_name_vn", "Tên doanh nghiệp", "text"),
    ("exchange", "Sàn", "text"),
    ("icb_name_level2", "Ngành ICB Cấp 2", "text"),
    ("icb_name_level3", "Ngành ICB Cấp 3", "text"),
    ("risk_tier", "Nhóm cảnh báo R1–R5", "text"),
    ("calibrated_distress_prob", "Xác suất kiệt quệ 1 năm", "percent"),
    ("risk_market_percentile", "Phân vị rủi ro (%)", "ratio"),
    ("relative_risk_lift", "Bội số rủi ro (x)", "ratio"),
    ("altman_z_double_prime", "Altman Z'' (EM)", "ratio"),
    ("altman_zone", "Vùng Altman", "text"),
    ("piotroski_f_score", "Piotroski F-Score", "int"),
    ("beneish_m8_score", "Beneish M-Score", "ratio"),
    ("beneish_flag", "Cờ Beneish", "text"),
    ("red_flag_count", "Số cờ đỏ", "int"),
    ("critical_red_flag_count", "Số cờ đỏ nghiêm trọng", "int"),
    ("roe_total", "ROE Tổng", "percent"),
    ("roe_parent", "ROE Công ty mẹ", "percent"),
    ("roa", "ROA", "percent"),
    ("ebit_margin", "Biên EBIT", "percent"),
    ("eps_ttm_estimated", "EPS TTM (VND/cp)", "acct"),
    ("liabilities_to_assets", "Nợ phải trả / Tài sản", "percent"),
    ("debt_to_equity", "Nợ vay / VCSH (lần)", "ratio"),
    ("interest_coverage", "Thanh toán lãi vay (lần)", "ratio"),
    ("cfo_ttm", "CFO TTM (VND)", "acct"),
    ("fcf_ttm", "FCF TTM (VND)", "acct"),
    ("ocf_to_total_liabilities", "CFO / Tổng nợ", "ratio"),
    ("revenue_growth_yoy", "Tăng trưởng DT YoY", "percent"),
    ("market_cap_vnd", "Vốn hóa PIT (VND)", "acct"),
    ("pe_ttm", "P/E (lần)", "ratio"),
    ("pe_flag", "Cờ P/E", "text"),
    ("pb_ratio", "P/B (lần)", "ratio"),
    ("pb_flag", "Cờ P/B", "text"),
    ("cluster_archetype_name", "Cụm tài chính", "text"),
    ("anomaly_flag", "Ngoại lai đa biến", "text"),
)


@dataclass(frozen=True)
class CompanyStatementExportSection:
    """Pre-fetched normalized VAS statement slice for one statement type."""

    statement_type: str
    periods: list[dict[str, object]]
    rows: list[dict[str, object]]


@dataclass(frozen=True)
class CompanyExportPayload:
    """All pre-computed company datasets required to render the 4-sheet workbook."""

    snapshot_id: str | None
    published_at: str | None
    profile: CompanyProfileData
    exchange: str | None = None
    icb_name_level2: str | None = None
    icb_name_level3: str | None = None
    statements: tuple[CompanyStatementExportSection, ...] = ()
    benchmarks_summary: list[dict[str, object]] = field(default_factory=list)
    filings: list[dict[str, object]] = field(default_factory=list)
    market_overlay: dict[str, Any] | None = None


@dataclass(frozen=True)
class ScreenerExportPayload:
    """Pre-filtered screener DataFrame and lineage metadata for Excel export."""

    snapshot_id: str | None
    published_at: str | None
    statement_scope: str
    period_type: str
    period_end_date: date | None
    frame: pl.DataFrame
    truncated_from_total: int | None = None


def sanitize_excel_text(value: object) -> str:
    """Neutralize spreadsheet formula injection (``=``, ``+``, ``-``, ``@``, ``\\t``, ``\\r``)."""
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    if text.startswith(_FORMULA_PREFIXES) or text.lstrip().startswith(
        _FORMULA_PREFIXES
    ):
        return f"'{text}"
    return text


def sanitize_filename_token(raw: str, *, fallback: str = "export") -> str:
    """Return an ASCII-safe filename token free of path separators or control chars."""
    cleaned = _FILENAME_SAFE_RE.sub("", raw.strip())
    return cleaned[:48] if cleaned else fallback


def build_short_snapshot_id(snapshot_id: str | None) -> str:
    """Shorten a snapshot identifier safely for attachment filenames."""
    if not snapshot_id or not snapshot_id.strip():
        return "latest"
    token = sanitize_filename_token(snapshot_id, fallback="latest")
    return token[:16]


def _load_xlsxwriter() -> Any:
    return importlib.import_module("xlsxwriter")


def _finite_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = float(value)
        return num if math.isfinite(num) else None
    return None


def _create_formats(wb: Any) -> dict[str, Any]:
    return {
        "title": wb.add_format(
            {"bold": True, "font_size": 13, "font_color": "#0F172A"}
        ),
        "section": wb.add_format(
            {
                "bold": True,
                "font_size": 11,
                "font_color": "#163A5F",
                "bottom": 1,
                "bottom_color": "#CBD5E1",
            }
        ),
        "header": wb.add_format(
            {
                "bold": True,
                "font_size": 10,
                "font_color": "#0F172A",
                "bg_color": "#F1F5F9",
                "border": 1,
                "border_color": "#CBD5E1",
                "text_wrap": True,
            }
        ),
        "label": wb.add_format(
            {"bold": True, "font_size": 10, "font_color": "#334155"}
        ),
        "text": wb.add_format({"font_size": 10, "font_color": "#0F172A"}),
        "acct": wb.add_format(
            {
                "font_size": 10,
                "font_color": "#0F172A",
                "num_format": ACCOUNTING_NUMBER_FORMAT,
                "align": "right",
            }
        ),
        "ratio": wb.add_format(
            {
                "font_size": 10,
                "font_color": "#0F172A",
                "num_format": RATIO_NUMBER_FORMAT,
                "align": "right",
            }
        ),
        "percent": wb.add_format(
            {
                "font_size": 10,
                "font_color": "#0F172A",
                "num_format": PERCENT_NUMBER_FORMAT,
                "align": "right",
            }
        ),
        "int": wb.add_format(
            {
                "font_size": 10,
                "font_color": "#0F172A",
                "num_format": INTEGER_NUMBER_FORMAT,
                "align": "right",
            }
        ),
        "footer": wb.add_format(
            {"italic": True, "font_size": 9, "font_color": "#475569", "text_wrap": True}
        ),
    }


def _write_cell(
    ws: Any,
    row: int,
    col: int,
    value: object,
    formats: Mapping[str, Any],
    *,
    num_kind: str = "ratio",
) -> None:
    if value is None:
        ws.write_blank(row, col, None, formats["text"])
        return
    if isinstance(value, bool):
        ws.write_string(row, col, "Có" if value else "Không", formats["text"])
        return
    if isinstance(value, (int, float)):
        num = _finite_number(value)
        if num is None:
            ws.write_blank(row, col, None, formats["text"])
        else:
            ws.write_number(row, col, num, formats[num_kind])
        return
    if isinstance(value, (date, datetime)):
        ws.write_string(row, col, value.isoformat()[:10], formats["text"])
        return
    ws.write_string(row, col, sanitize_excel_text(value), formats["text"])


def _write_table_headers(
    ws: Any, row: int, headers: Sequence[str], formats: Mapping[str, Any]
) -> int:
    for col, title in enumerate(headers):
        ws.write_string(row, col, sanitize_excel_text(title), formats["header"])
    return row + 1


def _write_compliance_footer(
    ws: Any,
    row: int,
    *,
    snapshot_id: str | None,
    published_at: str | None,
    formats: Mapping[str, Any],
) -> int:
    notice = DEFAULT_COMPLIANCE_NOTICE
    ws.write_string(
        row,
        0,
        sanitize_excel_text(
            f"Nguồn snapshot: {snapshot_id or 'N/A'} · Cập nhật: {published_at or 'N/A'} · "
            f"Phiên bản chính sách: {notice.policy_version}"
        ),
        formats["footer"],
    )
    ws.write_string(
        row + 1,
        0,
        sanitize_excel_text(
            f"{notice.risk_tier_legal_label_vi}. {notice.disclaimer_vi}"
        ),
        formats["footer"],
    )
    return row + 2


def _parse_shap_drivers(raw_json: object) -> list[tuple[str, dict[str, object]]]:
    if not isinstance(raw_json, str) or not raw_json.strip():
        return []
    try:
        parsed = json.loads(raw_json)
    except ValueError:
        return []
    if not isinstance(parsed, dict):
        return []
    drivers: list[tuple[str, dict[str, object]]] = []
    for direction, key in (
        ("Tăng rủi ro", "top_risk_increasing"),
        ("Giảm rủi ro", "top_risk_decreasing"),
    ):
        items = parsed.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    drivers.append((direction, item))
    return drivers


def _write_overview_sheet(
    ws: Any, payload: CompanyExportPayload, formats: Mapping[str, Any]
) -> None:
    prof = payload.profile
    notice = DEFAULT_COMPLIANCE_NOTICE
    ws.set_column(0, 0, 34)
    ws.set_column(1, 8, 18)

    row = 0
    ws.write_string(
        row,
        0,
        sanitize_excel_text(
            f"HỒ SƠ PHÂN TÍCH ĐỊNH LƯỢNG: {prof.ticker} — {prof.company_name_vn or prof.ticker}"
        ),
        formats["title"],
    )
    row += 1
    ws.write_string(
        row, 0, sanitize_excel_text(notice.risk_tier_legal_label_vi), formats["footer"]
    )
    row += 2

    meta_rows: list[tuple[str, object]] = [
        ("Mã chứng khoán", prof.ticker),
        ("Tên doanh nghiệp", prof.company_name_vn or prof.ticker),
        ("Sàn giao dịch", payload.exchange or "N/A"),
        (
            "Ngành ICB Cấp 2 / Cấp 3",
            f"{payload.icb_name_level2 or 'N/A'} / {payload.icb_name_level3 or 'N/A'}",
        ),
        ("Phạm vi báo cáo", f"{prof.statement_scope} · {prof.period_view}"),
        ("Chuẩn mực kế toán mới nhất", prof.latest_taxonomy_version or "N/A"),
        ("Trạng thái kiểm soát chất lượng", prof.latest_quality_status or "N/A"),
        ("Mã snapshot dữ liệu", payload.snapshot_id or "N/A"),
        ("Thời điểm công bố snapshot", payload.published_at or "N/A"),
    ]
    ws.write_string(row, 0, "THÔNG TIN DOANH NGHIỆP & TRUY VẾT", formats["section"])
    row += 1
    for label, val in meta_rows:
        ws.write_string(row, 0, label, formats["label"])
        _write_cell(ws, row, 1, val, formats)
        row += 1

    ml_rows = sorted(
        prof.ml_scores.to_dicts(),
        key=lambda item: str(item.get("period_end_date") or ""),
        reverse=True,
    )[:MAX_COMPANY_EXPORT_PERIODS]

    row += 1
    ws.write_string(
        row, 0, "ĐỊNH GIÁ POINT-IN-TIME & RỦI RO THỊ TRƯỜNG", formats["section"]
    )
    row += 1
    latest_val = (
        payload.market_overlay.get("latest_valuation")
        if isinstance(payload.market_overlay, dict)
        else None
    )
    if isinstance(latest_val, dict):
        val_rows: list[tuple[str, object, str]] = [
            ("Ngày giao dịch gần nhất", latest_val.get("trade_date"), "text"),
            ("Giá đóng cửa thực tế (VND)", latest_val.get("close_raw"), "acct"),
            ("Vốn hóa PIT (VND)", latest_val.get("market_cap_vnd"), "acct"),
            ("P/E TTM (lần)", latest_val.get("pe_ttm"), "ratio"),
            ("P/B (lần)", latest_val.get("pb_ratio"), "ratio"),
            ("Merton DTD (sigma)", latest_val.get("merton_dtd"), "ratio"),
        ]
        for label, val, kind in val_rows:
            ws.write_string(row, 0, label, formats["label"])
            _write_cell(ws, row, 1, val, formats, num_kind=kind)
            row += 1
    else:
        ws.write_string(row, 0, "Chưa có dữ liệu giá EOD cho kỳ này.", formats["text"])
        row += 1

    row += 1
    ws.write_string(
        row, 0, "LỊCH SỬ CẢNH BÁO RỦI RO ĐỊNH LƯỢNG (R1–R5)", formats["section"]
    )
    row += 1
    row = _write_table_headers(
        ws,
        row,
        (
            "Kỳ báo cáo",
            "Nhóm R1–R5",
            "Xác suất kiệt quệ 1Y",
            "Phân vị TT (%)",
            "Bội số rủi ro",
            "Altman Z''",
            "Vùng Altman",
            "F-Score",
            "Beneish M-Score",
            "Cụm tài chính",
            "Ngoại lai",
        ),
        formats,
    )
    for m in ml_rows:
        vals: list[tuple[object, str]] = [
            (m.get("period_end_date"), "text"),
            (m.get("risk_tier"), "text"),
            (m.get("calibrated_distress_prob"), "percent"),
            (m.get("risk_market_percentile"), "ratio"),
            (m.get("relative_risk_lift"), "ratio"),
            (m.get("altman_z_double_prime"), "ratio"),
            (m.get("altman_zone"), "text"),
            (m.get("piotroski_f_score"), "int"),
            (m.get("beneish_m8_score"), "ratio"),
            (m.get("cluster_archetype_name"), "text"),
            (m.get("anomaly_flag"), "text"),
        ]
        for col, (v, kind) in enumerate(vals):
            _write_cell(ws, row, col, v, formats, num_kind=kind)
        row += 1

    if ml_rows:
        drivers = _parse_shap_drivers(ml_rows[0].get("shap_top_drivers_json"))
        if drivers:
            row += 1
            ws.write_string(
                row, 0, "TOP YẾU TỐ TÁC ĐỘNG TREESHAP (KỲ MỚI NHẤT)", formats["section"]
            )
            row += 1
            row = _write_table_headers(
                ws,
                row,
                (
                    "Chiều tác động",
                    "Chỉ tiêu",
                    "Giá trị DN",
                    "Trung vị ngành",
                    "SHAP Delta",
                ),
                formats,
            )
            for direction, drv in drivers:
                _write_cell(ws, row, 0, direction, formats)
                _write_cell(
                    ws,
                    row,
                    1,
                    drv.get("display_name_vn") or drv.get("feature"),
                    formats,
                )
                _write_cell(ws, row, 2, drv.get("value"), formats, num_kind="ratio")
                _write_cell(
                    ws, row, 3, drv.get("peer_median"), formats, num_kind="ratio"
                )
                _write_cell(
                    ws, row, 4, drv.get("shap_delta"), formats, num_kind="ratio"
                )
                row += 1

    row += 1
    _write_compliance_footer(
        ws,
        row,
        snapshot_id=payload.snapshot_id,
        published_at=payload.published_at,
        formats=formats,
    )


def _write_statements_sheet(
    ws: Any, payload: CompanyExportPayload, formats: Mapping[str, Any]
) -> None:
    ws.set_column(0, 0, 26)
    ws.set_column(1, 1, 28)
    ws.set_column(2, 2, 42)
    ws.set_column(3, 6, 12)

    all_periods: dict[str, str] = {}
    for sec in payload.statements:
        for per in sec.periods:
            p_end = per.get("period_end_date")
            iso = (
                p_end.isoformat()[:10]
                if isinstance(p_end, (date, datetime))
                else str(p_end or "")
            )
            if iso and iso not in all_periods:
                all_periods[iso] = str(per.get("label") or iso)
    ordered_isos = sorted(all_periods.keys(), reverse=True)[:MAX_COMPANY_EXPORT_PERIODS]
    for col_idx in range(len(ordered_isos)):
        ws.set_column(7 + col_idx, 7 + col_idx, 18)

    row = 0
    ws.write_string(
        row,
        0,
        sanitize_excel_text(
            f"BÁO CÁO TÀI CHÍNH CHUẨN HÓA VAS — {payload.profile.ticker}"
        ),
        formats["title"],
    )
    row += 2

    period_headers = [f"{all_periods[iso]} ({iso})" for iso in ordered_isos]
    headers = [
        "Loại báo cáo",
        "Mã chỉ tiêu",
        "Tên chỉ tiêu (VAS)",
        "Mã TT200",
        "Mã TT99",
        "Đơn vị",
        "Tương thích",
        *period_headers,
    ]
    row = _write_table_headers(ws, row, headers, formats)

    for sec in payload.statements:
        sec_title = _STATEMENT_TITLES_VI.get(sec.statement_type, sec.statement_type)
        for item in sec.rows:
            _write_cell(ws, row, 0, sec_title, formats)
            _write_cell(ws, row, 1, item.get("metric_id"), formats)
            _write_cell(ws, row, 2, item.get("display_name_vn"), formats)
            _write_cell(ws, row, 3, item.get("tt200_code"), formats)
            _write_cell(ws, row, 4, item.get("tt99_code"), formats)
            _write_cell(ws, row, 5, item.get("unit"), formats)
            _write_cell(ws, row, 6, item.get("comparable_across_taxonomy"), formats)
            raw_vals = item.get("values")
            val_map = raw_vals if isinstance(raw_vals, Mapping) else {}
            for p_idx, iso in enumerate(ordered_isos):
                _write_cell(
                    ws, row, 7 + p_idx, val_map.get(iso), formats, num_kind="acct"
                )
            row += 1


def _write_ratios_and_dupont_sheet(
    ws: Any, payload: CompanyExportPayload, formats: Mapping[str, Any]
) -> None:
    prof = payload.profile
    ws.set_column(0, 0, 28)
    ws.set_column(1, 14, 16)

    row = 0
    ws.write_string(
        row,
        0,
        sanitize_excel_text(f"CHỈ SỐ TÀI CHÍNH, NGÀNH & DUPONT — {prof.ticker}"),
        formats["title"],
    )
    row += 2

    if payload.benchmarks_summary:
        ws.write_string(
            row, 0, "SO SÁNH TRUNG VỊ NGÀNH (KỲ MỚI NHẤT)", formats["section"]
        )
        row += 1
        row = _write_table_headers(
            ws,
            row,
            (
                "Chỉ số",
                "Nhóm",
                "Đơn vị",
                "Doanh nghiệp",
                "Trung vị ngành",
                "P25",
                "P75",
                "Phân vị (%)",
                "Z-Score",
                "Cấp ngành",
                "Số DN cùng ngành",
            ),
            formats,
        )
        for b in payload.benchmarks_summary:
            kind = "percent" if b.get("unit") == "%" else "ratio"
            _write_cell(
                ws, row, 0, b.get("display_name_vn") or b.get("metric_name"), formats
            )
            _write_cell(ws, row, 1, b.get("category"), formats)
            _write_cell(ws, row, 2, b.get("unit"), formats)
            _write_cell(ws, row, 3, b.get("company_value"), formats, num_kind=kind)
            _write_cell(ws, row, 4, b.get("peer_median"), formats, num_kind=kind)
            _write_cell(ws, row, 5, b.get("peer_p25"), formats, num_kind=kind)
            _write_cell(ws, row, 6, b.get("peer_p75"), formats, num_kind=kind)
            _write_cell(ws, row, 7, b.get("percentile_rank"), formats, num_kind="ratio")
            _write_cell(ws, row, 8, b.get("robust_z_score"), formats, num_kind="ratio")
            _write_cell(ws, row, 9, b.get("peer_group_level"), formats)
            _write_cell(ws, row, 10, b.get("peer_count"), formats, num_kind="int")
            row += 1
        row += 1

    dupont_rows = sorted(
        prof.dupont_series.to_dicts(),
        key=lambda item: str(item.get("period_end_date") or ""),
        reverse=True,
    )[:MAX_COMPANY_EXPORT_PERIODS]
    ws.write_string(row, 0, "PHÂN RÃ DUPONT 5 NHÂN TỐ", formats["section"])
    row += 1
    row = _write_table_headers(
        ws,
        row,
        (
            "Kỳ báo cáo",
            "Chuẩn mực",
            "Gánh nặng thuế",
            "Gánh nặng lãi vay",
            "Biên EBIT",
            "Vòng quay TS",
            "Đòn bẩy TS/VCSH",
            "ROE Tổng",
            "Hợp lệ",
        ),
        formats,
    )
    for d in dupont_rows:
        _write_cell(ws, row, 0, d.get("period_end_date"), formats)
        _write_cell(ws, row, 1, d.get("taxonomy_version"), formats)
        _write_cell(ws, row, 2, d.get("tax_burden"), formats, num_kind="ratio")
        _write_cell(ws, row, 3, d.get("interest_burden"), formats, num_kind="ratio")
        _write_cell(ws, row, 4, d.get("ebit_margin"), formats, num_kind="percent")
        _write_cell(ws, row, 5, d.get("asset_turnover"), formats, num_kind="ratio")
        _write_cell(
            ws, row, 6, d.get("equity_multiplier_total"), formats, num_kind="ratio"
        )
        _write_cell(ws, row, 7, d.get("roe_total"), formats, num_kind="percent")
        _write_cell(ws, row, 8, d.get("dupont_valid_flag"), formats)
        row += 1

    ratios_rows = sorted(
        prof.ratios_table.to_dicts(),
        key=lambda item: str(item.get("period_end_date") or ""),
        reverse=True,
    )[:MAX_COMPANY_EXPORT_PERIODS]
    if ratios_rows:
        row += 1
        ws.write_string(row, 0, "LỊCH SỬ TỶ SỐ TÀI CHÍNH", formats["section"])
        row += 1
        excluded = {
            "issuer_id",
            "ticker",
            "statement_scope",
            "period_type",
            "filing_id",
            "source_filing_ids",
        }
        col_names = [
            col
            for col in ratios_rows[0].keys()
            if col not in excluded and not col.endswith("_flag")
        ]
        row = _write_table_headers(ws, row, col_names, formats)
        for r in ratios_rows:
            for c_idx, col_name in enumerate(col_names):
                kind = (
                    "acct"
                    if col_name.endswith(("_ttm", "_vnd")) or col_name == "total_debt"
                    else "ratio"
                )
                _write_cell(ws, row, c_idx, r.get(col_name), formats, num_kind=kind)
            row += 1


def _write_red_flags_and_lineage_sheet(
    ws: Any, payload: CompanyExportPayload, formats: Mapping[str, Any]
) -> None:
    prof = payload.profile
    ws.set_column(0, 0, 16)
    ws.set_column(1, 2, 26)
    ws.set_column(3, 4, 48)
    ws.set_column(5, 10, 20)

    row = 0
    ws.write_string(
        row,
        0,
        sanitize_excel_text(f"CỜ ĐỎ KẾ TOÁN & NGUỒN DỮ LIỆU — {prof.ticker}"),
        formats["title"],
    )
    row += 2

    ws.write_string(
        row, 0, "DANH SÁCH CỜ ĐỎ KẾ TOÁN (RF_01..RF_10)", formats["section"]
    )
    row += 1
    row = _write_table_headers(
        ws,
        row,
        (
            "Kỳ báo cáo",
            "Mã cờ đỏ",
            "Mức độ",
            "Tiêu đề",
            "Diễn giải chi tiết",
            "Mã hồ sơ",
        ),
        formats,
    )
    rf_rows = prof.red_flags.to_dicts()
    if rf_rows:
        for rf in rf_rows:
            _write_cell(ws, row, 0, rf.get("period_end_date"), formats)
            _write_cell(ws, row, 1, rf.get("flag_code"), formats)
            _write_cell(ws, row, 2, rf.get("severity"), formats)
            _write_cell(ws, row, 3, rf.get("flag_title_vn"), formats)
            _write_cell(ws, row, 4, rf.get("explanation_text_vn"), formats)
            _write_cell(ws, row, 5, rf.get("filing_id"), formats)
            row += 1
    else:
        ws.write_string(
            row, 0, "Không ghi nhận cờ đỏ trong phạm vi truy vấn.", formats["text"]
        )
        row += 1

    row += 1
    ws.write_string(row, 0, "LỊCH SỬ HỒ SƠ CÔNG BỐ & KIỂM TOÁN", formats["section"])
    row += 1
    row = _write_table_headers(
        ws,
        row,
        (
            "Mã hồ sơ",
            "Loại BCTC",
            "Kỳ báo cáo",
            "Chuẩn mực",
            "Ngày công bố",
            "Cơ sở PIT",
            "Kiểm toán",
            "Ý kiến kiểm toán",
            "Lần sửa",
            "Nguồn",
        ),
        formats,
    )
    for fil in payload.filings:
        _write_cell(ws, row, 0, fil.get("filing_id"), formats)
        _write_cell(ws, row, 1, fil.get("statement_type"), formats)
        _write_cell(ws, row, 2, fil.get("period_end_date"), formats)
        _write_cell(ws, row, 3, fil.get("taxonomy_version"), formats)
        _write_cell(ws, row, 4, fil.get("published_at"), formats)
        _write_cell(ws, row, 5, fil.get("published_at_basis"), formats)
        _write_cell(ws, row, 6, fil.get("audit_status"), formats)
        _write_cell(ws, row, 7, fil.get("audit_opinion"), formats)
        _write_cell(ws, row, 8, fil.get("revision_no"), formats, num_kind="int")
        _write_cell(ws, row, 9, fil.get("source_provider"), formats)
        row += 1

    row += 1
    _write_compliance_footer(
        ws,
        row,
        snapshot_id=payload.snapshot_id,
        published_at=payload.published_at,
        formats=formats,
    )


def build_company_workbook_bytes(payload: CompanyExportPayload) -> bytes:
    """Build a 4-sheet formatted ``.xlsx`` workbook in RAM (``BytesIO``) for one company."""
    xlsxwriter = _load_xlsxwriter()
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    try:
        formats = _create_formats(wb)
        ws_overview = wb.add_worksheet(COMPANY_SHEET_NAMES[0])
        ws_statements = wb.add_worksheet(COMPANY_SHEET_NAMES[1])
        ws_ratios = wb.add_worksheet(COMPANY_SHEET_NAMES[2])
        ws_lineage = wb.add_worksheet(COMPANY_SHEET_NAMES[3])

        _write_overview_sheet(ws_overview, payload, formats)
        _write_statements_sheet(ws_statements, payload, formats)
        _write_ratios_and_dupont_sheet(ws_ratios, payload, formats)
        _write_red_flags_and_lineage_sheet(ws_lineage, payload, formats)
    finally:
        wb.close()
    return buf.getvalue()


def build_screener_workbook_bytes(payload: ScreenerExportPayload) -> bytes:
    """Build a single-sheet formula-safe Screener ``.xlsx`` workbook capped at 2,000 rows."""
    xlsxwriter = _load_xlsxwriter()
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    try:
        formats = _create_formats(wb)
        ws = wb.add_worksheet(SCREENER_SHEET_NAME)
        ws.set_column(0, 0, 12)
        ws.set_column(1, 1, 32)
        ws.set_column(2, 4, 20)
        ws.set_column(5, len(_SCREENER_COLUMNS) - 1, 16)

        capped = payload.frame.head(MAX_SCREENER_EXPORT_ROWS)
        row = 0
        ws.write_string(
            row,
            0,
            sanitize_excel_text(
                f"KẾT QUẢ SÀNG LỌC DOANH NGHIỆP ({payload.statement_scope} · {payload.period_type})"
            ),
            formats["title"],
        )
        row += 1
        period_str = (
            payload.period_end_date.isoformat()
            if payload.period_end_date
            else "Mới nhất"
        )
        cap_note = (
            f" (giới hạn tối đa {MAX_SCREENER_EXPORT_ROWS} dòng từ {payload.truncated_from_total})"
            if payload.truncated_from_total
            and payload.truncated_from_total > MAX_SCREENER_EXPORT_ROWS
            else ""
        )
        ws.write_string(
            row,
            0,
            sanitize_excel_text(
                f"Kỳ dữ liệu: {period_str} · Số dòng xuất: {capped.height}{cap_note} · Snapshot: {payload.snapshot_id or 'N/A'}"
            ),
            formats["footer"],
        )
        row += 2

        row = _write_table_headers(
            ws, row, [title for _, title, _ in _SCREENER_COLUMNS], formats
        )
        for rec in capped.to_dicts():
            for c_idx, (col_name, _, kind) in enumerate(_SCREENER_COLUMNS):
                _write_cell(
                    ws,
                    row,
                    c_idx,
                    rec.get(col_name),
                    formats,
                    num_kind=kind if kind != "text" else "ratio",
                )
            row += 1

        row += 1
        _write_compliance_footer(
            ws,
            row,
            snapshot_id=payload.snapshot_id,
            published_at=payload.published_at,
            formats=formats,
        )
    finally:
        wb.close()
    return buf.getvalue()
