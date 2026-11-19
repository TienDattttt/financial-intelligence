"""Page 3: Data Quality Audit & Lineage Card (Kiểm định Chất lượng & Truy xuất Chứng cứ)."""

from __future__ import annotations

import streamlit as st

from dashboard.components.widgets import render_lineage_card
from dashboard.queries import ALL_RATIOS, DashboardQueryService


def render_page() -> None:
    st.set_page_config(
        page_title="Kiểm định Chất lượng & Truy xuất Chứng cứ", layout="wide"
    )
    st.title("3. Kiểm định Chất lượng & Truy xuất Chứng cứ (Audit & Lineage)")

    try:
        service = DashboardQueryService.from_data_root()
        issuers = service.list_issuers()
        quality_summary, audit_quarantine = service.get_quality_tables()
    except Exception as exc:
        st.error(f"Không thể truy vấn Snapshot đang hoạt động: {exc}")
        return

    tab_lineage, tab_quality, tab_quarantine = st.tabs(
        [
            "Thẻ Truy xuất Nguồn gốc (Lineage Card)",
            "Tổng hợp Chất lượng (quality_summary)",
            "Danh sách Cách ly (audit_quarantine)",
        ]
    )

    with tab_lineage:
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

            periods_df = service.list_ratio_periods(
                str(ticker), statement_scope=str(statement_scope)
            )
            if periods_df.is_empty():
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
                    )

                card = service.get_ratio_lineage(
                    str(ticker),
                    statement_scope=str(statement_scope),
                    period_type=str(selected_period["period_type"]),
                    period_end_date=selected_period["period_end_date"],
                    ratio_name=str(ratio_name),
                )
                render_lineage_card(card)

    with tab_quality:
        st.subheader("Bảng tổng hợp chất lượng dữ liệu đã xuất bản")
        if quality_summary.is_empty():
            st.info("Snapshot hiện chưa có bảng quality_summary.parquet.")
        else:
            st.dataframe(quality_summary, use_container_width=True, hide_index=True)

    with tab_quarantine:
        st.subheader("Bảng tờ khai bị cách ly (CRITICAL Quarantine)")
        if audit_quarantine.is_empty():
            st.success("Không có tờ khai nào bị cách ly trong đợt xuất bản này.")
        else:
            st.dataframe(audit_quarantine, use_container_width=True, hide_index=True)


render_page()
