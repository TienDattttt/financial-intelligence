"""Page 2: Industry Peer Comparison & Peer Band History (So sánh Tương quan Ngành ICB).

Verified Sources:
    - Streamlit API Reference (`streamlit==1.45.1`):
      https://docs.streamlit.io/develop/api-reference
    - Plotly Continuous Error Bars (`plotly==6.0.1`):
      https://plotly.com/python/continuous-error-bars/
    - Runtime API Verification (Python 3.11.9, streamlit==1.45.1, plotly==6.0.1).

Architectural invariants:
    - Zero financial calculations; reads pre-computed Layer 4 peer benchmarks via ``DashboardClient``.
    - Explicitly surfaces ``peer_group_level``, ``icb_code_used``, ``peer_count``, and
      ``REGIME_FALLBACK`` warnings so ICB3/ICB2 cohorts are never silently blended with fallback.
    - Native Streamlit widgets only (no raw HTML rendering) and sanitized error display.
"""

from __future__ import annotations

import polars as pl
import streamlit as st

from dashboard.client import DashboardClient
from dashboard.components.charts import (
    build_industry_comparison_chart,
    build_peer_band_history_chart,
)
from dashboard.components.widgets import render_sanitized_error
from dashboard.queries import ALL_RATIOS, RATIO_LINEAGE_CATALOG


def _ratio_option_label(ratio_name: str) -> str:
    spec = RATIO_LINEAGE_CATALOG.get(ratio_name)
    if spec is None:
        return ratio_name
    return f"{spec.display_name_vn} ({ratio_name})"


def render_page() -> None:
    st.set_page_config(page_title="So sánh Tương quan Ngành", layout="wide")
    st.title("2. So sánh Tương quan Ngành (ICB Peer Benchmarking)")

    try:
        client = DashboardClient.from_env()
        issuers = client.list_issuers()
    except Exception as exc:
        render_sanitized_error(exc, context="nạp danh sách doanh nghiệp")
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
            format_func=_ratio_option_label,
        )

    try:
        periods_df = client.list_ratio_periods(
            str(ticker), statement_scope=str(statement_scope)
        )
    except Exception as exc:
        render_sanitized_error(exc, context="nạp danh sách kỳ báo cáo")
        return

    matching_periods = (
        periods_df.filter(pl.col("period_type") == str(period_type))[
            "period_end_date"
        ].to_list()
        if not periods_df.is_empty() and "period_type" in periods_df.columns
        else []
    )

    selected_end_date = None
    if matching_periods:
        with st.sidebar:
            selected_end_date = st.selectbox(
                "Kỳ báo cáo",
                options=matching_periods,
                format_func=lambda d: d.isoformat(),
            )

    try:
        comparison, history_df = client.get_industry_comparison_with_history(
            str(ticker),
            statement_scope=str(statement_scope),
            period_type=str(period_type),
            period_end_date=selected_end_date,
            metric_name=str(metric_name),
        )
    except Exception as exc:
        render_sanitized_error(exc, context="nạp dữ liệu so sánh ngành")
        return

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

    if comparison.peer_group_level == "REGIME_FALLBACK":
        st.warning(
            f"Kỳ chọn `{comparison.period_end_date.isoformat()}` rơi về nhóm tham chiếu "
            f"`REGIME_FALLBACK` (n={comparison.peer_count}) do phân ngành ICB cấp 3/2 "
            "không đủ số doanh nghiệp tối thiểu."
        )

    st.plotly_chart(
        build_industry_comparison_chart(comparison),
        use_container_width=True,
    )

    st.divider()
    st.subheader("Quỹ đạo Phân vị Ngành qua Thời gian ([P25–P75] & Peer Median)")
    metric_spec = RATIO_LINEAGE_CATALOG.get(str(metric_name))
    metric_label = (
        metric_spec.display_name_vn if metric_spec is not None else str(metric_name)
    )
    if (
        not history_df.is_empty()
        and "peer_group_level" in history_df.columns
        and history_df.filter(pl.col("peer_group_level") == "REGIME_FALLBACK").height
        > 0
    ):
        fb_count = history_df.filter(
            pl.col("peer_group_level") == "REGIME_FALLBACK"
        ).height
        st.warning(
            f"Chuỗi lịch sử có **{fb_count} kỳ** sử dụng nhóm tham chiếu `REGIME_FALLBACK` "
            "(đánh dấu tam giác vàng trên biểu đồ) thay vì `ICB_LEVEL_3`/`ICB_LEVEL_2`. "
            "Di chuột qua từng điểm để xem chính xác `peer_group_level`, `icb_code_used` và `peer_count`."
        )

    st.plotly_chart(
        build_peer_band_history_chart(history_df, metric_label),
        use_container_width=True,
    )

    st.divider()
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
