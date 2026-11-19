"""Page 2: Industry Peer Comparison (So sánh Tương quan Ngành ICB)."""

from __future__ import annotations

import streamlit as st

from dashboard.components.charts import build_industry_comparison_chart
from dashboard.queries import ALL_RATIOS, DashboardQueryService


def render_page() -> None:
    st.set_page_config(page_title="So sánh Tương quan Ngành", layout="wide")
    st.title("2. So sánh Tương quan Ngành (ICB Peer Benchmarking)")

    try:
        service = DashboardQueryService.from_data_root()
        issuers = service.list_issuers()
    except Exception as exc:
        st.error(f"Không thể truy vấn Snapshot đang hoạt động: {exc}")
        return

    if issuers.is_empty():
        st.warning("Snapshot hiện không có danh sách doanh nghiệp.")
        return

    tickers = issuers["ticker"].to_list()
    with st.sidebar:
        st.header("Bộ lọc So sánh Ngành")
        ticker = st.selectbox("Mã cổ phiếu (Ticker)", options=tickers, index=0)
        statement_scope = st.selectbox(
            "Phạm vi báo cáo",
            options=["CONSOLIDATED", "SEPARATE"],
            index=0,
        )
        period_type = st.selectbox(
            "Loại kỳ",
            options=["QUARTER", "ANNUAL"],
            index=0,
        )
        default_ratio_idx = ALL_RATIOS.index("roe_total")
        metric_name = st.selectbox(
            "Chỉ số so sánh",
            options=list(ALL_RATIOS),
            index=default_ratio_idx,
        )

    periods_df = service.list_ratio_periods(
        str(ticker), statement_scope=str(statement_scope)
    )
    matching_periods = periods_df.filter(periods_df["period_type"] == str(period_type))[
        "period_end_date"
    ].to_list()

    selected_end_date = None
    if matching_periods:
        with st.sidebar:
            selected_end_date = st.selectbox(
                "Kỳ báo cáo",
                options=matching_periods,
                format_func=lambda d: d.isoformat(),
            )

    comparison = service.get_industry_comparison(
        str(ticker),
        statement_scope=str(statement_scope),
        period_type=str(period_type),
        period_end_date=selected_end_date,
        metric_name=str(metric_name),
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(
        f"{comparison.ticker} ({comparison.metric_name})",
        f"{comparison.company_value:.4f}"
        if comparison.company_value is not None
        else "NULL",
    )
    m2.metric(
        "Trung vị ngành (Peer Median)",
        f"{comparison.peer_median:.4f}"
        if comparison.peer_median is not None
        else "N/A",
    )
    m3.metric(
        "Xếp hạng bách phân vị",
        f"{comparison.percentile_rank:.1f}%"
        if comparison.percentile_rank is not None
        else "N/A",
    )
    m4.metric(
        "Robust Z-Score",
        f"{comparison.robust_z_score:.2f}"
        if comparison.robust_z_score is not None
        else "N/A",
    )

    st.plotly_chart(
        build_industry_comparison_chart(comparison),
        use_container_width=True,
    )

    st.subheader(
        f"Bảng xếp hạng nhóm tham chiếu ({comparison.peer_group_level or 'N/A'}"
        f"{f' · {comparison.icb_code_used}' if comparison.icb_code_used else ''})"
    )
    if comparison.peer_ranking.is_empty():
        st.info("Không có doanh nghiệp tham chiếu khả dụng cho bộ lọc hiện tại.")
    else:
        st.dataframe(comparison.peer_ranking, use_container_width=True, hide_index=True)

    snapshot_date_text = (
        comparison.industry_snapshot_date.isoformat()
        if comparison.industry_snapshot_date is not None
        else "Chưa ghi nhận"
    )
    st.divider()
    st.caption(
        f"Phân nhóm ngành ICB theo ảnh chụp danh mục ngày `{snapshot_date_text}`."
    )


render_page()
