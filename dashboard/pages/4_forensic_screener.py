"""Page 4: Historical Cross-Sectional Screener — Latest Snapshot View.

Verified Sources:
    - Streamlit API Reference (`streamlit==1.45.1`, ``st.download_button``):
      https://docs.streamlit.io/develop/api-reference
    - OWASP CSV Injection Prevention Cheat Sheet:
      https://owasp.org/www-community/attacks/CSV_Injection
    - AGENT.md §5.7 (No Look-Ahead Bias — explicit non-PIT disclaimer for Latest Snapshot View).
    - Runtime API Verification (Python 3.11.9, streamlit==1.45.1, polars==1.35.2).

Architectural invariants:
    - Uses ONLY the shared ``DashboardClient.screen_market()`` facade (backed by
      ``DashboardQueryService.screen_market()`` or ``GET /api/v1/screener``).
    - Works seamlessly on snapshots without Layer 4B ML tables (``LEFT JOIN``).
    - Neutralizes all CSV formula triggers (``=``, ``+``, ``-``, ``@``, ``\\t``, ``\\r``)
      before download via ``dataframe_to_safe_csv_bytes``.
    - Native Streamlit widgets only, zero financial math, sanitized error display only.
"""

from __future__ import annotations

import math

import polars as pl
import streamlit as st

from dashboard.client import DashboardClient, dataframe_to_safe_csv_bytes
from dashboard.components.charts import build_screener_risk_scatter
from dashboard.components.widgets import (
    render_historical_screener_disclaimer,
    render_sanitized_error,
)
from dashboard.queries import (
    pagination_offset,
    pagination_window,
    percent_to_decimal,
)

_ALTMAN_ZONE_OPTIONS: tuple[str, ...] = (
    "Tất cả",
    "SAFE_ZONE",
    "GREY_ZONE",
    "DISTRESS_ZONE",
)
_BENEISH_FLAG_OPTIONS: tuple[str, ...] = (
    "Tất cả",
    "VALID",
    "HIGH_MANIPULATION_RISK_SCREEN",
    "MIXED_TAXONOMY",
    "MISSING_PRIOR_YEAR_BASE",
    "MISSING_DEPRECIATION_DIRECT_CF",
    "MISSING_OR_INVALID_INPUT",
)
_PAGE_SIZE_OPTIONS: tuple[int, ...] = (25, 50, 100, 200)


def _build_icb2_options(issuers: pl.DataFrame) -> list[tuple[str | None, str]]:
    options: list[tuple[str | None, str]] = [(None, "Tất cả ngành ICB Cấp 2")]
    if issuers.is_empty() or "icb_code_level2" not in issuers.columns:
        return options
    cols = ["icb_code_level2"]
    if "icb_name_level2" in issuers.columns:
        cols.append("icb_name_level2")
    unique_sectors = (
        issuers.select(cols)
        .filter(
            pl.col("icb_code_level2").is_not_null()
            & (pl.col("icb_code_level2").cast(pl.String).str.strip_chars() != "")
        )
        .unique(subset=["icb_code_level2"])
        .sort("icb_code_level2")
    )
    for row in unique_sectors.to_dicts():
        code = str(row["icb_code_level2"])
        name = str(row.get("icb_name_level2") or code)
        options.append((code, f"{code} — {name}"))
    return options


def render_page() -> None:
    st.set_page_config(
        page_title="Tầm soát Mặt cắt Ngang Lịch sử (Latest Snapshot View)",
        layout="wide",
    )
    st.title(
        "4. Tầm soát Mặt cắt Ngang Lịch sử — Latest Snapshot View "
        "(Historical Cross-Sectional Screener)"
    )
    render_historical_screener_disclaimer()

    try:
        client = DashboardClient.from_env()
        issuers = client.list_issuers()
    except Exception as exc:
        render_sanitized_error(exc, context="nạp danh mục doanh nghiệp")
        return

    if issuers.is_empty():
        st.warning("Snapshot hiện không có danh sách doanh nghiệp.")
        return

    icb2_options = _build_icb2_options(issuers)

    with st.sidebar:
        st.header("Bộ lọc Tầm soát Toàn thị trường")
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

    try:
        available_periods = client.list_available_periods(
            statement_scope=str(statement_scope),
            period_type=str(period_type),
        )
    except Exception as exc:
        render_sanitized_error(exc, context="nạp danh sách kỳ báo cáo khả dụng")
        return

    selected_period_date = None
    with st.sidebar:
        if available_periods:
            selected_period_date = st.selectbox(
                "Kỳ mặt cắt ngang (period_end_date)",
                options=available_periods,
                index=0,
                format_func=lambda d: d.isoformat(),
            )
        selected_icb2_pair = st.selectbox(
            "Ngành ICB Cấp 2 (icb_code_level2)",
            options=icb2_options,
            index=0,
            format_func=lambda pair: pair[1],
        )
        icb_code_level2 = selected_icb2_pair[0]

        f_score_choice = st.selectbox(
            "Piotroski F-Score tối thiểu",
            options=["Không giới hạn", *list(range(0, 10))],
            index=0,
        )
        min_f_score = int(f_score_choice) if isinstance(f_score_choice, int) else None

        altman_choice = st.selectbox(
            "Phân vùng Altman (altman_zone)",
            options=list(_ALTMAN_ZONE_OPTIONS),
            index=0,
        )
        altman_zone = None if altman_choice == "Tất cả" else str(altman_choice)

        beneish_choice = st.selectbox(
            "Trạng thái Beneish (beneish_flag)",
            options=list(_BENEISH_FLAG_OPTIONS),
            index=0,
        )
        beneish_flag = None if beneish_choice == "Tất cả" else str(beneish_choice)

        red_flag_choice = st.selectbox(
            "Số cờ cảnh báo tối đa (max_red_flags)",
            options=["Không giới hạn", *list(range(0, 10))],
            index=0,
        )
        max_red_flags = (
            int(red_flag_choice) if isinstance(red_flag_choice, int) else None
        )

        use_distress_filter = st.checkbox(
            "Lọc theo Xác suất suy thoái tối đa (QUARTER)", value=False
        )
        max_distress_prob: float | None = None
        if use_distress_filter:
            max_distress_pct = st.slider(
                "Ngưỡng xác suất suy thoái tối đa (%)",
                min_value=5,
                max_value=100,
                value=50,
                step=5,
            )
            max_distress_prob = percent_to_decimal(max_distress_pct)

        st.divider()
        page_size = int(
            st.selectbox(
                "Số dòng / trang (tối đa 200)",
                options=list(_PAGE_SIZE_OPTIONS),
                index=1,
            )
        )
        page_num = int(st.number_input("Trang", min_value=1, value=1, step=1))

    offset = pagination_offset(page_num, page_size)

    try:
        screened_df, total_count, resolved_date = client.screen_market(
            period_type=str(period_type),
            period_end_date=selected_period_date,
            statement_scope=str(statement_scope),
            icb_code_level2=icb_code_level2,
            min_f_score=min_f_score,
            altman_zone=altman_zone,
            beneish_flag=beneish_flag,
            max_distress_prob=max_distress_prob,
            max_red_flags=max_red_flags,
            limit=page_size,
            offset=offset,
        )
    except Exception as exc:
        render_sanitized_error(exc, context="chạy tầm soát thị trường")
        return

    distress_on_page = (
        int(screened_df.filter(pl.col("altman_zone") == "DISTRESS_ZONE").height)
        if (not screened_df.is_empty() and "altman_zone" in screened_df.columns)
        else 0
    )
    total_pages = max(1, math.ceil(total_count / page_size)) if total_count > 0 else 1

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Tổng mã khớp bộ lọc", f"{total_count:,}")
    k2.metric("Số mã trên trang hiện tại", f"{screened_df.height:,}")
    k3.metric(
        "Kỳ mặt cắt ngang",
        resolved_date.isoformat() if resolved_date is not None else "N/A",
    )
    k4.metric("Mã thuộc DISTRESS_ZONE (trang này)", f"{distress_on_page:,}")

    st.plotly_chart(
        build_screener_risk_scatter(screened_df),
        use_container_width=True,
    )

    st.subheader("Bảng Kết quả Tầm soát Mặt cắt Ngang (Phân trang)")
    if total_count == 0 or screened_df.is_empty():
        st.info("Không có doanh nghiệp nào thỏa mãn bộ lọc hiện tại.")
        return

    _, start_row, end_row = pagination_window(offset, page_size, total_count)
    st.caption(
        f"Trang `{page_num}/{total_pages}` · "
        f"Hiển thị `{start_row:,}–{end_row:,}` trên tổng `{total_count:,}` doanh nghiệp."
    )

    st.dataframe(screened_df, use_container_width=True, hide_index=True)

    csv_bytes = dataframe_to_safe_csv_bytes(screened_df)
    date_slug = resolved_date.isoformat() if resolved_date is not None else "latest"
    st.download_button(
        label="Tải xuống CSV trang hiện tại (Đã chống CSV Formula Injection)",
        data=csv_bytes,
        file_name=f"finintel_historical_screener_{date_slug}_p{page_num}.csv",
        mime="text/csv",
    )
    st.caption(
        "Bảo mật xuất CSV: mọi ô văn bản bắt đầu bằng `=`, `+`, `-`, `@`, `Tab`, `CR` "
        "đều được tự động chèn tiền tố `'` theo chuẩn OWASP CSV Injection Prevention."
    )


render_page()
