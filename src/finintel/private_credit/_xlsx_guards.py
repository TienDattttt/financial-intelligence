"""Excel ZIP-bomb/macro guards and VAS TT200/TT99 template generator (V3-28)."""

from __future__ import annotations

import email.parser
import email.policy
import importlib
import math
import re
from collections.abc import Sequence
from datetime import date, datetime
from io import BytesIO
from typing import TYPE_CHECKING
from zipfile import BadZipFile, ZipFile

from finintel.private_credit._qg1 import StatementType, TaxonomyVersionStr
from finintel.serving.dictionary import load_statement_metric_catalog

if TYPE_CHECKING:
    from fastapi import Request

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_SHEET_ROWS = 5_000
MAX_SHEET_COLS = 64
_MULTIPART_OVERHEAD_BYTES = 256 * 1024

_SHEET_DEFS: tuple[tuple[StatementType, str, tuple[str, ...]], ...] = (
    ("BALANCE_SHEET", "BS", ("bs", "cdkt", "can_doi_ke_toan", "b01", "balance_sheet")),
    (
        "INCOME_STATEMENT",
        "IS",
        ("is", "kqkd", "ket_qua_kinh_doanh", "b02", "income_statement"),
    ),
    ("CASH_FLOW", "CF", ("cf", "lctt", "luu_chuyen_tien_te", "b03", "cash_flow")),
)
_FORMULA_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")
_YEAR_HEADER_RE = re.compile(r"^(?:năm\s*|fy\s*)?(20\d{2})$", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"^(20\d{2})-(\d{2})-(\d{2})$")
_DMY_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(20\d{2})$")
_S_IFLNK = 0o120000


class PrivateCreditValidationError(Exception):
    """Upload, parsing, or QG1 validation failure mapped cleanly to HTTP 400/413/422."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def safe_excel_text(value: str) -> str:
    return f"'{value}" if value.startswith(_FORMULA_PREFIXES) else value


def catalog_rows_for_taxonomy(
    taxonomy_version: TaxonomyVersionStr,
) -> dict[StatementType, list[tuple[str, str, str]]]:
    catalog = load_statement_metric_catalog()
    grouped: dict[StatementType, list[tuple[int, str, str, str]]] = {
        "BALANCE_SHEET": [],
        "INCOME_STATEMENT": [],
        "CASH_FLOW": [],
    }
    for metric_id, meta in catalog.items():
        if metric_id == "CF_SUPPLIER_PAYMENTS":
            continue
        code = (
            meta.tt99_code if taxonomy_version == "VAS_TT99_2025" else meta.tt200_code
        )
        if code:
            grouped[meta.statement_type].append(
                (meta.sort_order, code, metric_id, meta.display_name_vn)
            )
    if not any(m == "CF_ENDING_CASH" for _, _, m, _ in grouped["CASH_FLOW"]):
        grouped["CASH_FLOW"].append(
            (7000, "70", "CF_ENDING_CASH", "Tiền và tương đương tiền cuối kỳ")
        )
    out: dict[StatementType, list[tuple[str, str, str]]] = {}
    for stmt, entries in grouped.items():
        entries.sort(key=lambda item: (item[0], item[1], item[2]))
        out[stmt] = [(code, metric_id, label) for _, code, metric_id, label in entries]
    return out


def build_vas_template_xlsx(
    taxonomy_version: TaxonomyVersionStr = "VAS_TT99_2025",
    years: Sequence[int] | None = None,
) -> bytes:
    """Build a 3-sheet (BS/IS/CF) VAS Excel template locked with standard line codes."""
    openpyxl = importlib.import_module("openpyxl")
    period_years = tuple(years) if years else (2024, 2025)
    if any(not isinstance(y, int) or y < 2000 or y > 2100 for y in period_years):
        raise PrivateCreditValidationError(
            422, "Năm báo cáo trong mẫu Excel không hợp lệ."
        )

    wb = openpyxl.Workbook()
    default_ws = wb.active
    if default_ws is not None:
        wb.remove(default_ws)

    rows_by_stmt = catalog_rows_for_taxonomy(taxonomy_version)
    for stmt_type, sheet_name, _ in _SHEET_DEFS:
        ws = wb.create_sheet(title=sheet_name)
        ws.append(
            ["Mã số", "Chỉ tiêu", "Mã chuẩn hóa", *(f"Năm {yr}" for yr in period_years)]
        )
        for vas_code, metric_id, label_vn in rows_by_stmt[stmt_type]:
            ws.append(
                [
                    safe_excel_text(vas_code),
                    safe_excel_text(label_vn),
                    safe_excel_text(metric_id),
                    *(None for _ in period_years),
                ]
            )
        ws.column_dimensions["A"].width = 12
        ws.column_dimensions["B"].width = 52
        ws.column_dimensions["C"].width = 34
        for idx in range(len(period_years)):
            col_letter = openpyxl.utils.get_column_letter(4 + idx)
            ws.column_dimensions[col_letter].width = 20

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def validate_xlsx_archive(content: bytes, filename: str) -> None:
    if not content:
        raise PrivateCreditValidationError(400, "Tệp tải lên rỗng.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise PrivateCreditValidationError(
            413, "Dung lượng tệp vượt quá giới hạn tối đa 15 MB."
        )

    lower_name = filename.strip().lower()
    if lower_name.endswith(".xlsm") or not lower_name.endswith(".xlsx"):
        raise PrivateCreditValidationError(
            400,
            "Chỉ chấp nhận tệp Excel chuẩn .xlsx (từ chối .xlsm chứa macro hoặc định dạng khác).",
        )
    if not content.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        raise PrivateCreditValidationError(
            400, "Định dạng tệp không phải cấu trúc Excel .xlsx hợp lệ."
        )

    try:
        with ZipFile(BytesIO(content), "r") as zf:
            total_declared = 0
            total_actual = 0
            for info in zf.infolist():
                if (info.external_attr >> 16) & 0o170000 == _S_IFLNK:
                    raise PrivateCreditValidationError(
                        400, "Từ chối tệp nén chứa liên kết tượng trưng (symlink)."
                    )
                entry_lower = info.filename.lower()
                if "vbaproject" in entry_lower or entry_lower.endswith(".bin"):
                    raise PrivateCreditValidationError(
                        400,
                        "Từ chối tệp Excel chứa macro VBA (.xlsm / vbaProject.bin).",
                    )
                if info.file_size < 0 or info.file_size > MAX_XLSX_UNCOMPRESSED_BYTES:
                    raise PrivateCreditValidationError(
                        413,
                        "Tệp giải nén vượt quá giới hạn an toàn 50 MB (chống ZIP-bomb).",
                    )
                total_declared += info.file_size
                if total_declared > MAX_XLSX_UNCOMPRESSED_BYTES:
                    raise PrivateCreditValidationError(
                        413,
                        "Tệp giải nén vượt quá giới hạn an toàn 50 MB (chống ZIP-bomb).",
                    )
                with zf.open(info, "r") as entry_fh:
                    while True:
                        chunk = entry_fh.read(65536)
                        if not chunk:
                            break
                        total_actual += len(chunk)
                        if total_actual > MAX_XLSX_UNCOMPRESSED_BYTES:
                            raise PrivateCreditValidationError(
                                413,
                                "Tệp giải nén vượt quá giới hạn an toàn 50 MB (chống ZIP-bomb).",
                            )
            if "[Content_Types].xml" in zf.namelist():
                ct_bytes = zf.read("[Content_Types].xml")
                if b"macroEnabled" in ct_bytes or b"vbaProject" in ct_bytes:
                    raise PrivateCreditValidationError(
                        400, "Từ chối tệp Excel có khai báo macro."
                    )
    except BadZipFile:
        raise PrivateCreditValidationError(
            400, "Tệp .xlsx bị hỏng hoặc không thể giải nén."
        ) from None


def parse_period_header(raw: object) -> tuple[date, int] | None:
    if isinstance(raw, datetime):
        return raw.date(), raw.date().year
    if isinstance(raw, date):
        return raw, raw.year
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        yr = int(raw)
        if 2000 <= yr <= 2100 and abs(float(raw) - yr) < 1e-9:
            return date(yr, 12, 31), yr
        return None
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if m_yr := _YEAR_HEADER_RE.match(text):
        yr = int(m_yr.group(1))
        return date(yr, 12, 31), yr
    if m_iso := _ISO_DATE_RE.match(text):
        return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))), int(
            m_iso.group(1)
        )
    if m_dmy := _DMY_DATE_RE.match(text):
        return date(int(m_dmy.group(3)), int(m_dmy.group(2)), int(m_dmy.group(1))), int(
            m_dmy.group(3)
        )
    return None


def parse_numeric_cell(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise PrivateCreditValidationError(
            422, "Ô số liệu BCTC không chấp nhận giá trị logic."
        )
    if isinstance(raw, (int, float)):
        val = float(raw)
        if not math.isfinite(val):
            raise PrivateCreditValidationError(422, "Ô số liệu chứa NaN hoặc vô cực.")
        return val
    if isinstance(raw, str):
        s = raw.strip()
        if s in {"", "-", "—", "–", "N/A", "n/a"}:
            return None
        neg = s.startswith("(") and s.endswith(")")
        if neg:
            s = s[1:-1].strip()
        s = s.replace(" ", "")
        if "," in s and "." in s:
            s = (
                s.replace(".", "").replace(",", ".")
                if s.rfind(",") > s.rfind(".")
                else s.replace(",", "")
            )
        elif "," in s:
            parts = s.split(",")
            s = (
                s.replace(",", ".")
                if len(parts) == 2 and len(parts[1]) <= 2
                else s.replace(",", "")
            )
        try:
            val = float(s)
        except ValueError:
            raise PrivateCreditValidationError(
                422, f"Giá trị '{raw}' không thể chuyển đổi thành số tài chính hợp lệ."
            ) from None
        if not math.isfinite(val):
            raise PrivateCreditValidationError(422, "Ô số liệu chứa giá trị vô cực.")
        return -abs(val) if neg else val
    raise PrivateCreditValidationError(422, "Định dạng ô số liệu không được hỗ trợ.")


async def parse_multipart_upload(
    request: Request,
) -> tuple[bytes, str, dict[str, str]]:
    """Parse ``multipart/form-data`` using Python stdlib without requiring ``python-multipart``."""
    from fastapi import HTTPException

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type.lower():
        raise HTTPException(
            status_code=400,
            detail="Yêu cầu tải lên phải dùng định dạng multipart/form-data.",
        )

    max_body = MAX_UPLOAD_BYTES + _MULTIPART_OVERHEAD_BYTES
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_body:
            raise HTTPException(
                status_code=413,
                detail="Dung lượng tệp tải lên vượt quá giới hạn tối đa 15 MB.",
            )
        chunks.append(chunk)

    raw_body = b"".join(chunks)
    header_block = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode(
        "latin-1"
    )
    msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(
        header_block + raw_body
    )
    if not msg.is_multipart():
        raise HTTPException(
            status_code=400, detail="Không thể phân tích gói tin multipart/form-data."
        )

    file_bytes = b""
    filename = ""
    fields: dict[str, str] = {}
    for part in msg.iter_parts():
        field_name = part.get_param("name", header="content-disposition")
        if not isinstance(field_name, str) or not field_name:
            continue
        part_filename = part.get_filename()
        payload = part.get_payload(decode=True)
        raw_part = payload if isinstance(payload, bytes) else b""
        if part_filename is not None:
            filename = part_filename
            file_bytes = raw_part
        else:
            fields[field_name] = raw_part.decode("utf-8", errors="replace").strip()

    if not filename:
        raise HTTPException(
            status_code=422, detail="Vui lòng đính kèm tệp Excel (.xlsx) cần thẩm định."
        )
    return file_bytes, filename, fields
