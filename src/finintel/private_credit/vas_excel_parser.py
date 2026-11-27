"""Secure VAS TT200/TT99 multi-column Excel (.xlsx) parser (V3-28).

Verified Sources:
    spec/v3/SPEC-v3-private-issuer-and-hitl.md (§2, §4, §5.1, §6, §7)
    config/taxonomy/corporate_tt200_2014.yaml & corporate_tt99_2025.yaml
    src/finintel/ingestion/file_adapter.py (ZIP-bomb & Excel guards)
"""

from __future__ import annotations

import hashlib
import importlib
import re
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Literal, cast

from finintel.private_credit._qg1 import (
    ParsedFactRow,
    Qg1CheckSummary,
    Qg1Discrepancy,
    StatementScopeStr,
    StatementType,
    TaxonomyVersionStr,
    evaluate_private_qg1,
    facts_to_canonical_polars,
)
from finintel.private_credit._xlsx_guards import (
    _SHEET_DEFS,
    MAX_SHEET_COLS,
    MAX_SHEET_ROWS,
    MAX_UPLOAD_BYTES,
    MAX_XLSX_UNCOMPRESSED_BYTES,
    PrivateCreditValidationError,
    build_vas_template_xlsx,
    parse_numeric_cell,
    parse_period_header,
    validate_xlsx_archive,
)
from finintel.serving.dictionary import load_statement_metric_catalog

__all__ = [
    "MAX_SHEET_COLS",
    "MAX_SHEET_ROWS",
    "MAX_UPLOAD_BYTES",
    "MAX_XLSX_UNCOMPRESSED_BYTES",
    "ParsedFactRow",
    "ParsedVasWorkbook",
    "PrivateCreditValidationError",
    "Qg1CheckSummary",
    "Qg1Discrepancy",
    "build_vas_template_xlsx",
    "parse_vas_excel_file",
    "parse_vas_excel_workbook",
]


@dataclass(frozen=True)
class ParsedVasWorkbook:
    company_name: str
    tax_code: str | None
    icb_code_level2: str | None
    statement_scope: StatementScopeStr
    taxonomy_version: TaxonomyVersionStr
    unit_multiplier: int
    source_filename: str
    source_sha256: str
    periods: tuple[date, ...]
    facts: tuple[ParsedFactRow, ...]
    qg1: Qg1CheckSummary


def _normalize_code(raw: object) -> str:
    if raw is None:
        return ""
    if isinstance(raw, int) and not isinstance(raw, bool):
        return f"{raw:02d}" if 0 < raw < 10 else str(raw)
    if isinstance(raw, float) and raw.is_integer():
        iv = int(raw)
        return f"{iv:02d}" if 0 < iv < 10 else str(iv)
    text = str(raw).strip()
    return f"0{text}" if text.isdigit() and len(text) == 1 else text


def _build_code_lookup(
    taxonomy_version: TaxonomyVersionStr,
) -> dict[tuple[StatementType, str], tuple[str, str]]:
    catalog = load_statement_metric_catalog()
    lookup: dict[tuple[StatementType, str], tuple[str, str]] = {}
    for metric_id, meta in catalog.items():
        if metric_id == "CF_SUPPLIER_PAYMENTS":
            continue
        code = (
            meta.tt99_code if taxonomy_version == "VAS_TT99_2025" else meta.tt200_code
        )
        if code:
            lookup[(meta.statement_type, code)] = (metric_id, meta.display_name_vn)
    lookup[("CASH_FLOW", "70")] = ("CF_ENDING_CASH", "Tiền và tương đương tiền cuối kỳ")
    return lookup


def _match_statement_sheet(sheet_name: str) -> StatementType | None:
    norm = re.sub(r"[^a-z0-9]+", "_", sheet_name.strip().lower()).strip("_")
    for stmt_type, canonical_short, aliases in _SHEET_DEFS:
        if norm == canonical_short.lower() or any(alias in norm for alias in aliases):
            return stmt_type
    return None


def _detect_taxonomy(bs_codes: set[str], explicit: str) -> TaxonomyVersionStr:
    if explicit in ("VAS_TT200_2014", "VAS_TT99_2025"):
        return cast(TaxonomyVersionStr, explicit)
    if "280" in bs_codes or "252" in bs_codes or "321" in bs_codes:
        return "VAS_TT99_2025"
    return "VAS_TT200_2014"


def parse_vas_excel_workbook(
    content: bytes,
    *,
    filename: str,
    company_name: str,
    tax_code: str | None = None,
    icb_code_level2: str | None = None,
    statement_scope: StatementScopeStr = "SEPARATE",
    taxonomy_version: Literal["VAS_TT200_2014", "VAS_TT99_2025", "AUTO"] = "AUTO",
    unit_multiplier: int = 1,
) -> ParsedVasWorkbook:
    """Validate and parse a 3-sheet (BS/IS/CF) multi-column VAS Excel workbook."""
    if unit_multiplier not in (1, 1000, 1_000_000, 1_000_000_000):
        raise PrivateCreditValidationError(
            422, "Hệ số đơn vị tiền tệ phải thuộc {1, 1000, 1000000, 1000000000}."
        )
    clean_name = company_name.strip()
    if not clean_name:
        raise PrivateCreditValidationError(
            422, "Vui lòng nhập tên doanh nghiệp tư nhân."
        )

    validate_xlsx_archive(content, filename)
    source_sha256 = hashlib.sha256(content).hexdigest()
    openpyxl = importlib.import_module("openpyxl")

    raw_rows_by_stmt: dict[
        StatementType, list[tuple[str, str, date, int, float | None]]
    ] = {"BALANCE_SHEET": [], "INCOME_STATEMENT": [], "CASH_FLOW": []}
    bs_codes_seen: set[str] = set()
    book = None
    try:
        try:
            book = openpyxl.load_workbook(
                BytesIO(content), read_only=True, data_only=True, keep_links=False
            )
        except Exception:
            raise PrivateCreditValidationError(
                400, "Không thể đọc nội dung tệp Excel .xlsx."
            ) from None

        for sheet_name in book.sheetnames:
            stmt_type = _match_statement_sheet(sheet_name)
            if stmt_type is None:
                continue
            ws = book[sheet_name]
            ws.reset_dimensions()
            header: tuple[object, ...] | None = None
            code_col: int | None = None
            label_col: int | None = None
            period_cols: list[tuple[int, date, int]] = []

            for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
                if row_idx > MAX_SHEET_ROWS:
                    raise PrivateCreditValidationError(
                        413,
                        f"Sheet '{sheet_name}' vượt quá giới hạn tối đa {MAX_SHEET_ROWS} dòng.",
                    )
                if len(row) > MAX_SHEET_COLS:
                    raise PrivateCreditValidationError(
                        400,
                        f"Sheet '{sheet_name}' vượt quá giới hạn tối đa {MAX_SHEET_COLS} cột.",
                    )
                if header is None:
                    norm_cells = [
                        str(c).strip().lower() if c is not None else "" for c in row
                    ]
                    if any(
                        tok in ("mã số", "ma so", "code", "vas_code")
                        for tok in norm_cells
                    ):
                        header = tuple(row)
                        for idx, cell_text in enumerate(norm_cells):
                            if cell_text in ("mã số", "ma so", "code", "vas_code"):
                                code_col = idx
                            elif cell_text in ("chỉ tiêu", "chi tieu", "item", "label"):
                                label_col = idx
                            else:
                                parsed_p = parse_period_header(row[idx])
                                if parsed_p is not None:
                                    period_cols.append((idx, parsed_p[0], parsed_p[1]))
                    continue

                if code_col is None or not period_cols:
                    continue
                vas_code = _normalize_code(
                    row[code_col] if code_col < len(row) else None
                )
                if not vas_code:
                    continue
                if stmt_type == "BALANCE_SHEET":
                    bs_codes_seen.add(vas_code)
                label_txt = (
                    str(row[label_col]).strip()
                    if label_col is not None
                    and label_col < len(row)
                    and row[label_col] is not None
                    else ""
                )
                for col_idx, p_date, p_year in period_cols:
                    raw_val = row[col_idx] if col_idx < len(row) else None
                    num_val = parse_numeric_cell(raw_val)
                    scaled = (
                        num_val * float(unit_multiplier)
                        if num_val is not None
                        else None
                    )
                    raw_rows_by_stmt[stmt_type].append(
                        (vas_code, label_txt, p_date, p_year, scaled)
                    )
    finally:
        if book is not None:
            book.close()

    resolved_taxonomy = _detect_taxonomy(bs_codes_seen, taxonomy_version)
    lookup = _build_code_lookup(resolved_taxonomy)
    parsed_facts: list[ParsedFactRow] = []
    seen_keys: set[tuple[StatementType, str, date]] = set()
    periods_set: set[date] = set()

    for stmt_type, entries in raw_rows_by_stmt.items():
        for vas_code, label_txt, p_date, p_year, val in entries:
            mapped = lookup.get((stmt_type, vas_code))
            if mapped is None:
                continue
            metric_id, default_label = mapped
            key = (stmt_type, metric_id, p_date)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            periods_set.add(p_date)
            parsed_facts.append(
                ParsedFactRow(
                    statement_type=stmt_type,
                    vas_code=vas_code,
                    metric_id=metric_id,
                    line_label_vn=label_txt or default_label,
                    period_end_date=p_date,
                    fiscal_year=p_year,
                    value=val,
                )
            )

    if not parsed_facts:
        raise PrivateCreditValidationError(
            422,
            "Không nhận diện được bảng BS/IS/CF chuẩn VAS (Mã số và cột kỳ báo cáo).",
        )

    non_null_facts = [f for f in parsed_facts if f.value is not None]
    if not non_null_facts:
        qg1 = Qg1CheckSummary(
            status="FAIL",
            discrepancies=(
                Qg1Discrepancy(
                    period_end_date=min(periods_set) if periods_set else date.today(),
                    rule_code="EMPTY_STATEMENTS",
                    severity="CRITICAL",
                    message_vi="Tất cả ô số liệu BCTC đều trống; vui lòng điền tối thiểu Tài sản, VCSH, Doanh thu và LNST.",
                ),
            ),
            warnings=(),
            info_signals=(),
        )
    else:
        facts_df, issuers_df = facts_to_canonical_polars(
            non_null_facts,
            issuer_id=f"PRIV_{source_sha256[:12].upper()}",
            ticker="PRIVATE",
            company_name=clean_name,
            icb_code_level2=icb_code_level2.strip() if icb_code_level2 else None,
            statement_scope=statement_scope,
            taxonomy_version=resolved_taxonomy,
        )
        qg1 = evaluate_private_qg1(facts_df, issuers_df)

    return ParsedVasWorkbook(
        company_name=clean_name,
        tax_code=tax_code.strip() if tax_code and tax_code.strip() else None,
        icb_code_level2=icb_code_level2.strip()
        if icb_code_level2 and icb_code_level2.strip()
        else None,
        statement_scope=statement_scope,
        taxonomy_version=resolved_taxonomy,
        unit_multiplier=unit_multiplier,
        source_filename=Path(filename).name,
        source_sha256=source_sha256,
        periods=tuple(sorted(periods_set)),
        facts=tuple(parsed_facts),
        qg1=qg1,
    )


def parse_vas_excel_file(
    path: Path,
    *,
    company_name: str,
    tax_code: str | None = None,
    icb_code_level2: str | None = None,
    statement_scope: StatementScopeStr = "SEPARATE",
    taxonomy_version: Literal["VAS_TT200_2014", "VAS_TT99_2025", "AUTO"] = "AUTO",
    unit_multiplier: int = 1,
) -> ParsedVasWorkbook:
    """Safely read a local Excel path (rejecting symlinks and > 15 MB) and parse."""
    if path.is_symlink():
        raise PrivateCreditValidationError(400, "Từ chối đường dẫn tệp là symlink.")
    if not path.is_file():
        raise PrivateCreditValidationError(400, "Không tìm thấy tệp Excel đầu vào.")
    if path.stat().st_size > MAX_UPLOAD_BYTES:
        raise PrivateCreditValidationError(
            413, "Dung lượng tệp vượt quá giới hạn 15 MB."
        )
    return parse_vas_excel_workbook(
        path.read_bytes(),
        filename=path.name,
        company_name=company_name,
        tax_code=tax_code,
        icb_code_level2=icb_code_level2,
        statement_scope=statement_scope,
        taxonomy_version=taxonomy_version,
        unit_multiplier=unit_multiplier,
    )
