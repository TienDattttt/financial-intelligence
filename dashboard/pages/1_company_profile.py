"""Page 1: Company Financial Health Profile (Hồ sơ Sức khỏe Tài chính Doanh nghiệp)."""

from __future__ import annotations

from typing import cast

import streamlit as st

from dashboard.components.charts import (
    build_capital_structure_chart,
    build_growth_chart,
)
from dashboard.components.widgets import (
    render_dupont_table,
    render_quality_banner,
    render_red_flags,
)
from dashboard.queries import DashboardQueryService, PeriodView


def render_page() -> None:
    st.set_page_config(page_title="Hồ sơ Sức khỏe Tài chính", layout="wide")
    st.title("1. Hồ sơ Sức khỏe Tài chính Doanh nghiệp")

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
        st.header("Bộ lọc Hồ sơ")
        ticker = st.selectbox("Mã cổ phiếu (Ticker)", options=tickers, index=0)
        statement_scope = st.selectbox(
            "Phạm vi báo cáo",
            options=["CONSOLIDATED", "SEPARATE"],
            index=0,
        )
        period_view = cast(
            PeriodView,
            st.selectbox(
                "Loại kỳ hiển thị",
                options=["TTM", "Quarterly", "Annual"],
                index=0,
            ),
        )

    profile = service.get_company_profile(
        str(ticker),
        statement_scope=str(statement_scope),
        period_view=period_view,
    )

    st.subheader(
        f"{profile.ticker} — {profile.company_name_vn or profile.issuer_id} "
        f"({profile.accounting_regime})"
    )
    render_quality_banner(
        profile.latest_quality_status,
        profile.latest_warning_codes,
        profile.latest_taxonomy_version,
    )

    col_left, col_right = st.columns(2)
    with col_left:
        st.plotly_chart(
            build_growth_chart(profile.growth_series, period_view=profile.period_view),
            use_container_width=True,
        )
    with col_right:
        st.plotly_chart(
            build_capital_structure_chart(profile.capital_structure_series),
            use_container_width=True,
        )

    render_dupont_table(profile.dupont_series)
    render_red_flags(profile.red_flags)


render_page()
