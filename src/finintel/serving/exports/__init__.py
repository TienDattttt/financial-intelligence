"""Read-only institutional Excel (.xlsx) export package for Layer 5 serving.

Verified Sources:
- spec/v3/SPEC-v3-export-and-tearsheet.md
- XlsxWriter Documentation (``xlsxwriter==3.2.2``, in-memory ``BytesIO`` workbooks):
  https://xlsxwriter.readthedocs.io/working_with_memory_and_performance.html
"""

from __future__ import annotations

from finintel.serving.exports.excel_builder import (
    COMPANY_SHEET_NAMES,
    MAX_SCREENER_EXPORT_ROWS,
    SCREENER_SHEET_NAME,
    CompanyExportPayload,
    CompanyStatementExportSection,
    ScreenerExportPayload,
    build_company_workbook_bytes,
    build_screener_workbook_bytes,
    build_short_snapshot_id,
    sanitize_excel_text,
    sanitize_filename_token,
)

__all__ = [
    "COMPANY_SHEET_NAMES",
    "CompanyExportPayload",
    "CompanyStatementExportSection",
    "MAX_SCREENER_EXPORT_ROWS",
    "SCREENER_SHEET_NAME",
    "ScreenerExportPayload",
    "build_company_workbook_bytes",
    "build_screener_workbook_bytes",
    "build_short_snapshot_id",
    "sanitize_excel_text",
    "sanitize_filename_token",
]
