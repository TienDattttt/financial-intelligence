"""Page 3: Data Quality Audit & 38-Ratio Lineage Card (Kiểm định Chất lượng & Truy xuất Chứng cứ).

Verified Sources:
    - Streamlit API Reference (`streamlit==1.45.1`):
      https://docs.streamlit.io/develop/api-reference
    - Runtime API Verification (Python 3.11.9, streamlit==1.45.1, polars==1.35.2).

Architectural invariants:
    - Supports all 38/38 ``ALL_RATIOS`` Lineage Cards (CORE + EXTRA + FORENSIC).
    - Paginates ``quality_summary`` and ``audit_quarantine`` strictly with ``limit <= 200``
      via ``DashboardClient`` so large snapshots never overwhelm browser memory.
    - Native Streamlit widgets only (no raw HTML rendering) and sanitized error display.
"""

from __future__ import annotations

import math

import streamlit as st

from dashboard.client import DashboardClient
from dashboard.components.widgets import render_lineage_card, render_sanitized_error
from dashboard.queries import (
    ALL_RATIOS,
    RATIO_LINEAGE_CATALOG,
    pagination_offset,
    pagination_window,
)
from finintel.storage.schemas import FORENSIC_RATIOS

_PAGE_SIZE_OPTIONS: tuple[int, ...] = (25, 50, 100, 200)


def _ratio_lineage_label(ratio_name: str) -> str:
    spec = RATIO_LINEAGE_CATALOG.get(ratio_name)
    prefix = "[FORENSIC] " if ratio_name in FORENSIC_RATIOS else ""
    if spec is None:
        return f"{prefix}{ratio_name}"
    return f"{prefix}{spec.display_name_vn} ({ratio_name})"


def _render_paginated_controls(
    *, prefix: str, default_limit: int = 50
) -> tuple[int, int]:
    c_size, c_page = st.columns([1, 2])
    with c_size:
        page_size = int(
            st.selectbox(
                "Số dòng / trang (tối đa 200)",
                options=list(_PAGE_SIZE_OPTIONS),
                index=list(_PAGE_SIZE_OPTIONS).index(default_limit),
                key=f"{prefix}_page_size",
            )
        )
    with c_page:
        page_num = int(
            st.number_input(
                "Trang",
                min_value=1,
                value=1,
                step=1,
                key=f"{prefix}_page_num",
            )
        )
    return page_size, pagination_offset(page_num, page_size)


def render_page() -> None:
    st.set_page_config(
        page_title="Kiểm định Chất lượng & Truy xuất Chứng cứ", layout="wide"
    )
    st.title("3. Kiểm định Chất lượng & Truy xuất Chứng cứ (Audit & Lineage)")

    try:
        client = DashboardClient.from_env()
        issuers = client.list_issuers()
    except Exception as exc:
        render_sanitized_error(exc, context="nạp danh sách doanh nghiệp")
        return

    lineage_label = f"Thẻ Truy xuất Nguồn gốc ({len(ALL_RATIOS)} Tỷ số)"
    quality_label = "Tổng hợp Chất lượng (quality_summary)"
    quarantine_label = "Danh sách Cách ly (audit_quarantine)"
    active_section = st.radio(
        "Chế độ kiểm định",
        options=[lineage_label, quality_label, quarantine_label],
        horizontal=True,
        label_visibility="collapsed",
        key="p3_active_section",
    )

    if active_section == lineage_label:
        st.caption(
            f"Hỗ trợ truy xuất đầy đủ **{len(ALL_RATIOS)}/{len(ALL_RATIOS)}** tỷ số tài chính "
            f"(bao gồm {len(FORENSIC_RATIOS)} tỷ số lợi nhuận lõi `FORENSIC_RATIOS`: "
            f"{', '.join(f'`{r}`' for r in FORENSIC_RATIOS)})."
        )
        if issuers.is_empty():
            st.warning("Snapshot hiện không có danh sách doanh nghiệp.")
        else:
            tickers = issuers["ticker"].to_list()
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                ticker = st.selectbox("Mã cổ phiếu", options=tickers, index=0)
            with c2:
                statement_scope = st.selectbox(
                    "Phạm vi báo cáo",
                    options=["CONSOLIDATED", "SEPARATE"],
                    index=0,
                )

            try:
                periods_df = client.list_ratio_periods(
                    str(ticker), statement_scope=str(statement_scope)
                )
            except Exception as exc:
                render_sanitized_error(exc, context="nạp danh sách kỳ tỷ số")
                periods_df = None

            if periods_df is None or periods_df.is_empty():
                st.info("Không có kỳ tỷ số tài chính nào cho mã và phạm vi đã chọn.")
            else:
                period_options = periods_df.to_dicts()
                with c3:
                    selected_period = st.selectbox(
                        "Kỳ báo cáo",
                        options=period_options,
                        format_func=lambda row: (
                            f"{row['period_type']} · {row['period_end_date'].isoformat()} "
                            f"({row['taxonomy_version']})"
                        ),
                    )
                with c4:
                    ratio_name = st.selectbox(
                        "Tỷ số cần truy xuất",
                        options=list(ALL_RATIOS),
                        index=ALL_RATIOS.index("roe_total"),
                        format_func=_ratio_lineage_label,
                    )

                try:
                    card = client.get_ratio_lineage(
                        str(ticker),
                        statement_scope=str(statement_scope),
                        period_type=str(selected_period["period_type"]),
                        period_end_date=selected_period["period_end_date"],
                        ratio_name=str(ratio_name),
                    )
                except Exception as exc:
                    render_sanitized_error(
                        exc, context="truy xuất Thẻ Nguồn gốc tỷ số tài chính"
                    )
                else:
                    render_lineage_card(card)

    elif active_section == quality_label:
        st.subheader("Bảng tổng hợp chất lượng dữ liệu đã xuất bản")
        qs_limit, qs_offset = _render_paginated_controls(
            prefix="p3_quality", default_limit=50
        )
        try:
            quality_summary, total_summary = client.get_quality_summary_page(
                limit=qs_limit, offset=qs_offset
            )
        except Exception as exc:
            render_sanitized_error(exc, context="nạp bảng tổng hợp chất lượng")
        else:
            if total_summary == 0:
                st.info(
                    "Snapshot hiện chưa có bản ghi trong `quality_summary.parquet`."
                )
            else:
                total_pages = max(1, math.ceil(total_summary / qs_limit))
                current_page, start_row, end_row = pagination_window(
                    qs_offset, qs_limit, total_summary
                )
                st.caption(
                    f"Trang `{current_page}/{total_pages}` · "
                    f"Hiển thị dòng `{start_row:,}–{end_row:,}` trên tổng `{total_summary:,}` bản ghi."
                )
                if quality_summary.is_empty():
                    st.info("Trang yêu cầu vượt quá tổng số bản ghi hiện có.")
                else:
                    st.dataframe(
                        quality_summary, use_container_width=True, hide_index=True
                    )

    else:
        st.subheader("Bảng tờ khai bị cách ly (CRITICAL Quarantine)")
        aq_limit, aq_offset = _render_paginated_controls(
            prefix="p3_quarantine", default_limit=50
        )
        try:
            audit_quarantine, total_quarantine = client.get_audit_quarantine_page(
                limit=aq_limit, offset=aq_offset
            )
        except Exception as exc:
            render_sanitized_error(exc, context="nạp danh sách cách ly kiểm toán")
        else:
            if total_quarantine == 0:
                st.success("Không có tờ khai nào bị cách ly trong đợt xuất bản này.")
            else:
                total_pages = max(1, math.ceil(total_quarantine / aq_limit))
                current_page, start_row, end_row = pagination_window(
                    aq_offset, aq_limit, total_quarantine
                )
                st.caption(
                    f"Trang `{current_page}/{total_pages}` · "
                    f"Hiển thị dòng `{start_row:,}–{end_row:,}` trên tổng `{total_quarantine:,}` bản ghi cách ly."
                )
                if audit_quarantine.is_empty():
                    st.info("Trang yêu cầu vượt quá tổng số bản ghi cách ly hiện có.")
                else:
                    st.dataframe(
                        audit_quarantine, use_container_width=True, hide_index=True
                    )


render_page()
