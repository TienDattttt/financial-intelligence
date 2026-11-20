"""Page 5: Corporate Balance-Sheet Stress Testing (Kiểm định Sức chịu đựng IMF WP/15/216).

Verified Sources:
    - Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
      IMF Working Paper WP/15/216 (https://www.elibrary.imf.org).
    - Streamlit Caching & Session State (`streamlit==1.45.1`):
      https://docs.streamlit.io/develop/concepts/architecture/caching
    - Plotly Grouped Bar Charts (`plotly==6.0.1`):
      https://plotly.com/python/bar-charts/
    - Runtime API Verification (Python 3.11.9, streamlit==1.45.1, plotly==6.0.1, polars==1.35.2).

Architectural invariants:
    - Zero stress-test formulas in UI code: sliders convert user-friendly ``%`` / ``pp``
      to decimal fractions (``/ 100.0``) and delegate exclusively to ``DashboardClient.run_stress_test``
      (which invokes ``simulate_financial_stress`` / ``summarize_stress_by_sector`` or
      ``POST /api/v1/stress-test``).
    - Caches ONLY the baseline snapshot slice inputs (``max_entries=32``); NEVER caches
      continuous slider values.
    - Native Streamlit widgets only (no raw HTML rendering) and sanitized error display.
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from dashboard.client import DashboardClient, dataframe_to_safe_csv_bytes
from dashboard.components.charts import build_stress_sector_bar_chart
from dashboard.components.widgets import render_sanitized_error
from dashboard.queries import percent_to_decimal


@dataclass(frozen=True)
class _PresetScenario:
    key: str
    button_label: str
    scenario_name: str
    revenue_pct: float
    gross_margin_pp: float
    interest_rate_bps: int
    dso_days: int
    dio_days: int


_PRESET_SCENARIOS: tuple[_PresetScenario, ...] = (
    _PresetScenario(
        key="preset_rate_hike",
        button_label="Kịch bản 1: Thắt chặt Tiền tệ (+300 bps, DT -10%)",
        scenario_name="Thắt chặt Tiền tệ (IMF Rate +300bps)",
        revenue_pct=-10.0,
        gross_margin_pp=-1.5,
        interest_rate_bps=300,
        dso_days=15,
        dio_days=15,
    ),
    _PresetScenario(
        key="preset_recession",
        button_label="Kịch bản 2: Suy thoái Nhu cầu (DT -25%, Biên gộp -4 pp)",
        scenario_name="Suy thoái Nhu cầu Nặng (-25% Rev / -4pp GM)",
        revenue_pct=-25.0,
        gross_margin_pp=-4.0,
        interest_rate_bps=200,
        dso_days=30,
        dio_days=30,
    ),
    _PresetScenario(
        key="preset_severe_crisis",
        button_label="Kịch bản 3: Khủng hoảng Kép (DT -35%, LS +500 bps)",
        scenario_name="Khủng hoảng Kép Toàn diện (-35% Rev / +500bps)",
        revenue_pct=-35.0,
        gross_margin_pp=-5.0,
        interest_rate_bps=500,
        dso_days=45,
        dio_days=45,
    ),
)

_STATE_DEFAULTS: dict[str, object] = {
    "p5_scenario_name": "Kịch bản Tùy chỉnh",
    "p5_rev_pct": -15.0,
    "p5_gm_pp": -2.0,
    "p5_rate_bps": 250,
    "p5_dso_days": 15,
    "p5_dio_days": 15,
}


def _init_slider_state() -> None:
    for key, default in _STATE_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = default


def _apply_preset(preset: _PresetScenario) -> None:
    st.session_state["p5_scenario_name"] = preset.scenario_name
    st.session_state["p5_rev_pct"] = preset.revenue_pct
    st.session_state["p5_gm_pp"] = preset.gross_margin_pp
    st.session_state["p5_rate_bps"] = preset.interest_rate_bps
    st.session_state["p5_dso_days"] = preset.dso_days
    st.session_state["p5_dio_days"] = preset.dio_days


def _mark_custom_scenario() -> None:
    st.session_state["p5_scenario_name"] = "Kịch bản Tùy chỉnh"


def render_page() -> None:
    st.set_page_config(
        page_title="Kiểm định Sức chịu đựng Tài chính (Stress Testing)",
        layout="wide",
    )
    st.title("5. Kiểm định Sức chịu đựng Tài chính (IMF WP/15/216 Stress Testing)")
    st.caption(
        "Mô phỏng trực tiếp tác động đồng thời của cú sốc Doanh thu, Biên gộp, Lãi suất "
        "và Vốn lưu động (DSO/DIO) lên Hệ số trả lãi vay (ICR) và tỷ trọng Nợ vay Rủi ro "
        "(Debt-at-Risk: `Stressed ICR < 1.0`, `Total Debt > 0`). "
        "Dữ liệu đầu vào kỳ gốc được cache theo `snapshot_id`; mô phỏng chạy trực tiếp theo từng thanh trượt."
    )

    _init_slider_state()

    try:
        client = DashboardClient.from_env()
    except Exception as exc:
        render_sanitized_error(exc, context="khởi tạo kết nối dữ liệu")
        return

    with st.sidebar:
        st.header("Phạm vi & Kỳ Dữ liệu Gốc")
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
                "Kỳ báo cáo gốc (period_end_date)",
                options=available_periods,
                index=0,
                format_func=lambda d: d.isoformat(),
            )
        top_n = int(
            st.slider(
                "Số doanh nghiệp rủi ro cao nhất hiển thị (top_n)",
                min_value=10,
                max_value=200,
                value=50,
                step=10,
            )
        )

    st.subheader("Chọn Kịch bản Mẫu Dựng sẵn (Presets)")
    p_cols = st.columns(len(_PRESET_SCENARIOS))
    for col, preset in zip(p_cols, _PRESET_SCENARIOS, strict=True):
        with col:
            st.button(
                preset.button_label,
                key=preset.key,
                use_container_width=True,
                on_click=_apply_preset,
                args=(preset,),
            )

    st.subheader("Điều chỉnh 5 Tham số Cú sốc (Đơn vị thân thiện -> Thập phân chuẩn)")
    s1, s2, s3 = st.columns(3)
    with s1:
        rev_pct = float(
            st.slider(
                "Cú sốc Doanh thu thuần (%)",
                min_value=-80.0,
                max_value=50.0,
                step=1.0,
                key="p5_rev_pct",
                on_change=_mark_custom_scenario,
                help="Khoảng hợp lệ: -80% đến +50% (quy đổi / 100.0 sang [-0.80, +0.50]).",
            )
        )
    with s2:
        gm_pp = float(
            st.slider(
                "Cú sốc Biên lợi nhuận gộp (điểm % / pp)",
                min_value=-50.0,
                max_value=20.0,
                step=0.5,
                key="p5_gm_pp",
                on_change=_mark_custom_scenario,
                help="Khoảng hợp lệ: -50 pp đến +20 pp (quy đổi / 100.0 sang [-0.50, +0.20]).",
            )
        )
    with s3:
        rate_bps = int(
            st.slider(
                "Cú sốc Lãi suất vay (điểm cơ bản / bps)",
                min_value=-500,
                max_value=1500,
                step=25,
                key="p5_rate_bps",
                on_change=_mark_custom_scenario,
                help="Khoảng hợp lệ: -500 bps đến +1500 bps (100 bps = +1.0 điểm % lãi suất).",
            )
        )

    s4, s5 = st.columns(2)
    with s4:
        dso_days = int(
            st.slider(
                "Cú sốc Số ngày phải thu DSO (ngày)",
                min_value=-90,
                max_value=180,
                step=5,
                key="p5_dso_days",
                on_change=_mark_custom_scenario,
                help="Khoảng hợp lệ: -90 đến +180 ngày (tăng DSO làm hút dòng tiền HĐKD).",
            )
        )
    with s5:
        dio_days = int(
            st.slider(
                "Cú sốc Số ngày tồn kho DIO (ngày)",
                min_value=-90,
                max_value=180,
                step=5,
                key="p5_dio_days",
                on_change=_mark_custom_scenario,
                help="Khoảng hợp lệ: -90 đến +180 ngày (tăng DIO làm hút dòng tiền HĐKD).",
            )
        )

    revenue_shock_decimal = percent_to_decimal(rev_pct)
    gross_margin_shock_decimal = percent_to_decimal(gm_pp)
    scenario_name = str(
        st.session_state.get("p5_scenario_name") or "Kịch bản Tùy chỉnh"
    )

    st.caption(
        f"Tham số thập phân gửi tới `StressScenario`: "
        f"`revenue_shock_pct={revenue_shock_decimal:+.4f}`, "
        f"`gross_margin_shock_pp={gross_margin_shock_decimal:+.4f}`, "
        f"`interest_rate_shock_bps={float(rate_bps):+.0f}`, "
        f"`dso_shock_days={float(dso_days):+.0f}`, "
        f"`dio_shock_days={float(dio_days):+.0f}`"
    )

    try:
        result = client.run_stress_test(
            scenario_name=scenario_name,
            statement_scope=str(statement_scope),
            period_type=str(period_type),
            period_end_date=selected_period_date,
            revenue_shock_pct=revenue_shock_decimal,
            gross_margin_shock_pp=gross_margin_shock_decimal,
            interest_rate_shock_bps=float(rate_bps),
            dso_shock_days=float(dso_days),
            dio_shock_days=float(dio_days),
            top_n=top_n,
        )
    except Exception as exc:
        render_sanitized_error(exc, context="chạy mô phỏng cú sốc tài chính")
        return

    if result.total_issuers_evaluated == 0:
        st.info("Không có dữ liệu doanh nghiệp phi tài chính khả dụng cho kỳ đã chọn.")
        return

    if result.total_debt_sum == 0.0:
        st.info(
            "Kỳ báo cáo này chưa có đủ chỉ tiêu TTM đồng nhất chế độ kế toán "
            "(TT200/TT99) để đánh giá Debt-at-Risk. Hãy chọn kỳ báo cáo gần nhất "
            "có đủ dữ liệu TTM ở thanh bên trái."
        )

    st.divider()
    st.subheader("Tổng quan Rủi ro Nợ vay Toàn thị trường (IMF Debt-at-Risk)")
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("DN đánh giá (CORPORATE)", f"{result.total_issuers_evaluated:,}")
    k2.metric(
        "Tổng Nợ vay đánh giá (VND)",
        f"{result.total_debt_sum:,.0f}",
    )
    k3.metric(
        "% Debt-at-Risk Trước Sốc",
        f"{result.baseline_debt_at_risk_pct:.1%}",
    )
    k4.metric(
        "% Debt-at-Risk Sau Sốc",
        f"{result.stressed_debt_at_risk_pct:.1%}",
    )
    k5.metric(
        "DN mới rơi xuống ICR < 1.0",
        f"{result.newly_distressed_count:,}",
    )

    st.plotly_chart(
        build_stress_sector_bar_chart(result.sector_summary),
        use_container_width=True,
    )

    with st.expander("Xem bảng chi tiết Debt-at-Risk theo Ngành ICB Cấp 2"):
        if result.sector_summary.is_empty():
            st.info("Không có dữ liệu tổng hợp ngành.")
        else:
            st.dataframe(
                result.sector_summary, use_container_width=True, hide_index=True
            )

    st.divider()
    tab_new, tab_all = st.tabs(
        [
            f"Doanh nghiệp mới rơi vào Stressed ICR < 1.0 ({result.newly_distressed_issuers.height})",
            f"Top Doanh nghiệp Rủi ro Sau Sốc ({result.top_stressed_issuers.height})",
        ]
    )

    with tab_new:
        st.subheader(
            "Danh sách Doanh nghiệp mới rơi xuống vùng rủi ro trả lãi (Stressed ICR < 1.0)"
        )
        if result.newly_distressed_issuers.is_empty():
            st.success(
                "Không có doanh nghiệp nào mới chuyển từ an toàn sang vùng "
                "`Stressed ICR < 1.0` dưới kịch bản cú sốc này."
            )
        else:
            st.dataframe(
                result.newly_distressed_issuers,
                use_container_width=True,
                hide_index=True,
            )
            date_slug = (
                result.period_end_date.isoformat()
                if result.period_end_date is not None
                else "latest"
            )
            st.download_button(
                label="Tải xuống CSV danh sách DN mới rơi vào ICR < 1.0 (An toàn CSV)",
                data=dataframe_to_safe_csv_bytes(result.newly_distressed_issuers),
                file_name=f"finintel_stress_newly_distressed_{date_slug}.csv",
                mime="text/csv",
            )

    with tab_all:
        st.subheader("Top Doanh nghiệp có Nợ vay Rủi ro Sau Sốc (Stressed ICR < 1.0)")
        if result.top_stressed_issuers.is_empty():
            st.success("Không có doanh nghiệp nào thuộc nhóm Debt-at-Risk sau cú sốc.")
        else:
            st.dataframe(
                result.top_stressed_issuers,
                use_container_width=True,
                hide_index=True,
            )


render_page()
