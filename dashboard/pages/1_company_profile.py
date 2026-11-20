"""Page 1: Company Financial Health Profile (Hồ sơ Sức khỏe Tài chính Doanh nghiệp).

Verified Sources:
    - Streamlit Multipage Pages & Layout (`streamlit==1.45.1`):
      https://docs.streamlit.io/develop/api-reference
    - Plotly Waterfall Charts (`plotly==6.0.1`):
      https://plotly.com/python/waterfall-charts/
    - Rouen, E., So, E. C., & Wang, C. C. Y. (2021). "Core Earnings: New Data and Evidence."
      Journal of Financial Economics, Vol. 142, Issue 3, pp. 1068-1091.
    - Runtime API Verification (Python 3.11.9, streamlit==1.45.1, plotly==6.0.1).

Architectural invariants:
    - Zero financial calculations; reads pre-computed snapshot views via ``DashboardClient``.
    - Degrades gracefully on snapshots without Layer 4B ML or V2 Core Earnings columns.
    - Native Streamlit widgets only (no raw HTML rendering) and sanitized error display.
"""

from __future__ import annotations

from typing import cast

import polars as pl
import streamlit as st

from dashboard.client import DashboardClient
from dashboard.components.charts import (
    build_capital_structure_chart,
    build_growth_chart,
)
from dashboard.components.widgets import (
    render_core_earnings_section,
    render_dupont_table,
    render_ml_scores_section,
    render_quality_banner,
    render_red_flags,
    render_sanitized_error,
)
from dashboard.queries import PeriodView


def render_page() -> None:
    st.set_page_config(page_title="Hồ sơ Sức khỏe Tài chính", layout="wide")
    st.title("1. Hồ sơ Sức khỏe Tài chính Doanh nghiệp")

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

    try:
        profile = client.get_company_profile(
            str(ticker),
            statement_scope=str(statement_scope),
            period_view=period_view,
        )
    except Exception as exc:
        render_sanitized_error(exc, context="nạp hồ sơ tài chính doanh nghiệp")
        return

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

    st.divider()
    render_core_earnings_section(profile.ratios_table, profile.red_flags)

    st.divider()
    ml_scores = getattr(profile, "ml_scores", pl.DataFrame())
    render_ml_scores_section(ml_scores, period_view=profile.period_view)

    st.divider()
    render_dupont_table(profile.dupont_series)
    render_red_flags(profile.red_flags)


render_page()
