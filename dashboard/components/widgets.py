"""Reusable display-only Streamlit UI widgets for Layer 5 dashboard pages.

Verified Sources:
    - Streamlit API Reference (`streamlit==1.45.1`, native status/metric/dataframe widgets,
      strictly no raw HTML rendering):
      https://docs.streamlit.io/develop/api-reference
    - Rouen, E., So, E. C., & Wang, C. C. Y. (2021). "Core Earnings: New Data and Evidence."
      Journal of Financial Economics, Vol. 142, Issue 3, pp. 1068-1091.
    - Schilit, H. M., Perler, J., & Engelhart, Y. (2018). Financial Shenanigans (4th Ed.) —
      Shenanigan #3 (RF_08) & Shenanigan #4 (RF_09).
    - Runtime API Verification (Python 3.11.9, streamlit==1.45.1, polars==1.35.2).

Architectural invariants:
    - Strictly display-only; zero financial or statistical calculations.
    - Strictly native Streamlit widgets only; raw HTML injection is prohibited.
    - All error banners route through ``sanitize_ui_error`` so disk paths and tracebacks
      never leak to the UI.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime

import polars as pl
import streamlit as st

from dashboard.client import sanitize_ui_error
from dashboard.components.charts import build_shap_waterfall_chart
from dashboard.queries import LineageComponent, RatioLineageCard

_ALTMAN_ZONE_LABELS_VN: dict[str, str] = {
    "SAFE_ZONE": "SAFE_ZONE — Vùng An toàn",
    "GREY_ZONE": "GREY_ZONE — Vùng Xám (Cần theo dõi)",
    "DISTRESS_ZONE": "DISTRESS_ZONE — Vùng Rủi ro Kiệt quệ",
}

_BENEISH_FLAG_LABELS_VN: dict[str, str] = {
    "VALID": "VALID (Dưới ngưỡng cảnh báo -1.78)",
    "HIGH_MANIPULATION_RISK_SCREEN": "HIGH_MANIPULATION_RISK_SCREEN (M > -1.78 — Cảnh báo tầm soát)",
    "MIXED_TAXONOMY": "MIXED_TAXONOMY (Giao thoa chế độ kế toán)",
    "MISSING_PRIOR_YEAR_BASE": "MISSING_PRIOR_YEAR_BASE (Thiếu kỳ gốc năm trước)",
    "MISSING_DEPRECIATION_DIRECT_CF": "MISSING_DEPRECIATION_DIRECT_CF (Thiếu khấu hao LCTT trực tiếp)",
    "MISSING_OR_INVALID_INPUT": "MISSING_OR_INVALID_INPUT (Thiếu hoặc không hợp lệ đầu vào)",
}


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _fmt_pct(value: object, digits: int = 2) -> str:
    numeric = _finite_float(value)
    if numeric is None:
        return "N/A"
    return f"{numeric:.{digits}%}"


def _fmt_num(value: object, digits: int = 2) -> str:
    numeric = _finite_float(value)
    if numeric is None:
        return "N/A"
    return f"{numeric:.{digits}f}"


def _fmt_vnd(value: object) -> str:
    numeric = _finite_float(value)
    if numeric is None:
        return "N/A"
    return f"{numeric:,.0f} VND"


def _fmt_date(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None:
        return "N/A"
    return str(value)


def render_sanitized_error(
    exc: BaseException, *, context: str = "truy vấn dữ liệu"
) -> None:
    """Render a sanitized user-friendly error banner without paths or tracebacks."""
    st.error(sanitize_ui_error(exc, context=context))


def render_quality_banner(
    quality_status: str | None,
    warning_codes: tuple[str, ...],
    taxonomy_version: str | None,
) -> None:
    """Render the top quality-status and accounting-regime banner."""
    regime_label = taxonomy_version or "Chưa xác định"
    if quality_status == "WARNING":
        codes_text = ", ".join(warning_codes) if warning_codes else "WARNING"
        st.warning(
            f"Trạng thái chất lượng: **WARNING** ({codes_text}) · "
            f"Chế độ kế toán kỳ mới nhất: **{regime_label}**"
        )
    elif quality_status == "PASS":
        st.success(
            f"Trạng thái chất lượng: **PASS** · "
            f"Chế độ kế toán kỳ mới nhất: **{regime_label}**"
        )
    else:
        st.info(
            f"Chưa có nhãn kiểm định chất lượng cho kỳ chọn · "
            f"Chế độ kế toán kỳ mới nhất: **{regime_label}**"
        )


def render_dupont_table(dupont_series: pl.DataFrame) -> None:
    """Display the pre-computed 5-factor DuPont decomposition table."""
    st.subheader("Phân rã DuPont 5 thành phần (ROE Tổng)")
    if dupont_series.is_empty():
        st.info("Chưa có dữ liệu phân rã DuPont cho bộ lọc hiện tại.")
        return

    rename_map = {
        "period_end_date": "Ngày kết thúc kỳ",
        "fiscal_year": "Năm TC",
        "fiscal_quarter": "Quý",
        "taxonomy_version": "Chế độ kế toán",
        "tax_burden": "Gánh nặng thuế (NI/EBT)",
        "interest_burden": "Gánh nặng lãi vay (EBT/EBIT)",
        "ebit_margin": "Biên EBIT (EBIT/DT)",
        "asset_turnover": "Vòng quay TS (DT/TS BQ)",
        "equity_multiplier_total": "Đòn bẩy VCSH (TS BQ/VCSH BQ)",
        "roe_total": "ROE Tổng",
        "dupont_valid_flag": "Hợp lệ DuPont",
    }
    applicable = {k: v for k, v in rename_map.items() if k in dupont_series.columns}
    display_df = dupont_series.rename(applicable)
    st.dataframe(display_df, use_container_width=True, hide_index=True)


def render_core_earnings_section(
    ratios_table: pl.DataFrame, red_flags: pl.DataFrame
) -> None:
    """Render Core Earnings Quality KPIs and RF_08/RF_09 forensic alerts."""
    st.subheader("Chất lượng Lợi nhuận Lõi (Core Earnings & Tầm soát RF_08 / RF_09)")

    core_cols = (
        "core_operating_profit_ttm",
        "core_operating_margin",
        "core_to_ebt_ratio",
        "non_core_distortion_to_assets",
    )
    available_core = [col for col in core_cols if col in ratios_table.columns]

    if ratios_table.is_empty() or not available_core:
        st.info(
            "Snapshot hiện tại chưa có cột chỉ tiêu Lợi nhuận Lõi V2 "
            "(`core_operating_margin`, `core_to_ebt_ratio`, `non_core_distortion_to_assets`)."
        )
    else:
        ordered = (
            ratios_table.sort("period_end_date")
            if "period_end_date" in ratios_table.columns
            else ratios_table
        )
        latest = ordered.row(-1, named=True)
        latest_date_str = _fmt_date(latest.get("period_end_date"))
        st.caption(f"Kỳ gần nhất: `{latest_date_str}`")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric(
            "Lợi nhuận kinh doanh lõi (Core Op. Profit TTM)",
            _fmt_vnd(latest.get("core_operating_profit_ttm")),
        )
        c2.metric(
            "Biên lợi nhuận lõi (Core Operating Margin)",
            _fmt_pct(latest.get("core_operating_margin")),
        )
        c3.metric(
            "Tỷ trọng LN lõi sau lãi vay / EBT",
            _fmt_pct(latest.get("core_to_ebt_ratio")),
        )
        c4.metric(
            "Độ lệch phi lõi / Tài sản BQ",
            _fmt_pct(latest.get("non_core_distortion_to_assets")),
        )

        history_cols = [
            col
            for col in (
                "period_end_date",
                "taxonomy_version",
                "core_operating_profit_ttm",
                "core_operating_margin",
                "core_to_ebt_ratio",
                "non_core_distortion_to_assets",
                "core_operating_profit_flag",
            )
            if col in ordered.columns
        ]
        if history_cols:
            with st.expander("Xem chuỗi lịch sử chỉ tiêu Lợi nhuận Lõi"):
                st.dataframe(
                    ordered.select(history_cols).reverse(),
                    use_container_width=True,
                    hide_index=True,
                )

    forensic_codes = (
        "RF_08_NON_CORE_EARNINGS_DEPENDENCE",
        "RF_09_FROZEN_CIP_CAPITALIZATION",
    )
    forensic_flags = (
        red_flags.filter(pl.col("flag_code").is_in(forensic_codes))
        if (not red_flags.is_empty() and "flag_code" in red_flags.columns)
        else pl.DataFrame()
    )
    if forensic_flags.is_empty():
        st.success(
            "Không ghi nhận cờ tầm soát lợi nhuận phi cốt lõi (`RF_08`) "
            "hoặc XDCB dở dang treo kéo dài (`RF_09`) trong giai đoạn đã chọn."
        )
    else:
        for flag in forensic_flags.to_dicts():
            code = str(flag.get("flag_code") or "")
            severity = str(flag.get("severity") or "HIGH")
            title = str(flag.get("flag_title_vn") or code)
            period = _fmt_date(flag.get("period_end_date"))
            explanation = str(flag.get("explanation_text_vn") or "")
            filing_id = str(flag.get("filing_id") or "")
            banner = f"**[{severity}] {title} (`{code}`) — Kỳ {period}**"
            if severity == "CRITICAL":
                st.error(banner)
            elif severity == "HIGH":
                st.warning(banner)
            else:
                st.info(banner)
            st.caption(f"{explanation} · Hồ sơ neo (`filing_id`): `{filing_id}`")


def render_ml_scores_section(ml_scores: pl.DataFrame, *, period_view: str) -> None:
    """Render Classical Scores, K-Means Archetype, Calibrated Distress Prob, and TreeSHAP Waterfall."""
    st.subheader(
        "Điểm Định lượng Kinh điển, Hình mẫu Tài chính & Giải thích TreeSHAP (Layer 4B)"
    )

    if ml_scores.is_empty():
        st.info(
            "Snapshot hiện tại chưa bật tầng ML (Layer 4B) hoặc doanh nghiệp này "
            "không thuộc phạm vi chấm điểm CORPORATE cho bộ lọc đã chọn."
        )
        return

    ordered = (
        ml_scores.sort("period_end_date", descending=True)
        if "period_end_date" in ml_scores.columns
        else ml_scores
    )
    records = ordered.to_dicts()
    if len(records) > 1:
        period_labels = [
            f"{rec.get('period_type', 'N/A')} · {_fmt_date(rec.get('period_end_date'))}"
            for rec in records
        ]
        selected_label = st.selectbox(
            "Chọn kỳ xem điểm định lượng & TreeSHAP",
            options=period_labels,
            index=0,
            key="p1_ml_period_select",
        )
        selected_idx = period_labels.index(str(selected_label))
        selected = records[selected_idx]
    else:
        selected = records[0]

    model_role = str(selected.get("model_role") or "SCREENING_INDICATOR")
    model_version = str(selected.get("model_version") or "Chưa gán phiên bản")
    period_type_str = str(selected.get("period_type") or "N/A")
    period_date_str = _fmt_date(selected.get("period_end_date"))

    st.caption(
        f"Kỳ đánh giá: `{period_type_str} {period_date_str}` · "
        f"Vai trò mô hình (`model_role`): `{model_role}` · "
        f"Phiên bản mô hình (`model_version`): `{model_version}`"
    )

    altman_zone = (
        str(selected["altman_zone"])
        if selected.get("altman_zone") is not None
        else None
    )
    altman_z = _finite_float(selected.get("altman_z_double_prime"))
    altman_em = _finite_float(selected.get("altman_em_score"))

    zone_text = (
        _ALTMAN_ZONE_LABELS_VN.get(altman_zone, altman_zone)
        if altman_zone is not None
        else "Chưa xác định phân vùng Altman"
    )
    if altman_zone == "SAFE_ZONE":
        st.success(f"Phân vùng Altman Z'': **{zone_text}**")
    elif altman_zone == "GREY_ZONE":
        st.warning(f"Phân vùng Altman Z'': **{zone_text}**")
    elif altman_zone == "DISTRESS_ZONE":
        st.error(f"Phân vùng Altman Z'': **{zone_text}**")
    else:
        st.info(f"Phân vùng Altman Z'': **{zone_text}**")

    piotroski_raw = selected.get("piotroski_f_score")
    piotroski_valid = selected.get("piotroski_valid_components_count")
    piotroski_display = (
        f"{int(piotroski_raw)} / 9" if piotroski_raw is not None else "N/A"
    )
    piotroski_delta = (
        f"{int(piotroski_valid)}/9 tiêu chí hợp lệ"
        if piotroski_valid is not None
        else None
    )

    beneish_score = _finite_float(selected.get("beneish_m8_score"))
    beneish_flag = (
        str(selected["beneish_flag"])
        if selected.get("beneish_flag") is not None
        else "N/A"
    )

    distress_prob = _finite_float(selected.get("calibrated_distress_prob"))
    distress_display = (
        f"{distress_prob:.1%}"
        if distress_prob is not None
        else ("Chỉ tính trên QUARTER" if period_view == "Annual" else "N/A")
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(
        "Altman Z'' (EM Score)",
        _fmt_num(altman_z, 2),
        delta=f"EM: {_fmt_num(altman_em, 2)}" if altman_em is not None else None,
        delta_color="off",
    )
    m2.metric(
        "Piotroski F-Score (/9)",
        piotroski_display,
        delta=piotroski_delta,
        delta_color="off",
    )
    m3.metric(
        "Beneish M-Score (8 biến)",
        _fmt_num(beneish_score, 3),
        delta=beneish_flag,
        delta_color="off",
    )
    m4.metric(
        "Xác suất suy thoái hiệu chuẩn (QUARTER)",
        distress_display,
    )

    if beneish_flag == "HIGH_MANIPULATION_RISK_SCREEN":
        st.warning(
            f"Cảnh báo tầm soát Beneish M-Score: **{_BENEISH_FLAG_LABELS_VN[beneish_flag]}**"
        )
    elif beneish_flag in _BENEISH_FLAG_LABELS_VN:
        st.caption(
            f"Trạng thái Beneish M-Score: `{_BENEISH_FLAG_LABELS_VN[beneish_flag]}`"
        )

    archetype = selected.get("cluster_archetype_name")
    cluster_id = selected.get("cluster_id")
    anomaly_flag = selected.get("anomaly_flag")
    anomaly_rank = _finite_float(selected.get("anomaly_percentile_rank"))

    k1, k2 = st.columns(2)
    with k1:
        if archetype:
            cluster_suffix = (
                f" (Cụm #{int(cluster_id)})" if cluster_id is not None else ""
            )
            st.info(f"**Hình mẫu K-Means Archetype:** {archetype}{cluster_suffix}")
        else:
            st.info(
                "**Hình mẫu K-Means Archetype:** Chưa khả dụng "
                "(chỉ chấm điểm trên kỳ `QUARTER`)."
            )
    with k2:
        if anomaly_flag == "STATISTICAL_MULTIVARIATE_OUTLIER":
            rank_txt = (
                f" (Bách phân vị {anomaly_rank:.1f}%)"
                if anomaly_rank is not None
                else ""
            )
            st.warning(
                f"**Cảnh báo ngoại lai đa biến (Isolation Forest):** `{anomaly_flag}`{rank_txt}"
            )
        elif anomaly_rank is not None:
            st.success(
                f"**Kiểm tra ngoại lai đa biến (Isolation Forest):** Bình thường "
                f"(Bách phân vị {anomaly_rank:.1f}%)."
            )
        else:
            st.info("**Kiểm tra ngoại lai đa biến:** Chưa khả dụng cho kỳ này.")

    raw_shap = selected.get("shap_top_drivers_json")
    if isinstance(raw_shap, str) and raw_shap.strip():
        st.plotly_chart(
            build_shap_waterfall_chart(raw_shap),
            use_container_width=True,
        )
    else:
        if period_view == "Annual":
            st.info(
                "Biểu đồ giải thích TreeSHAP và xác suất suy thoái hiệu chuẩn chỉ được "
                "tính trên kỳ `QUARTER` — hãy chuyển `Loại kỳ hiển thị` sang `TTM` hoặc `Quarterly`."
            )
        else:
            st.info("Chưa có dữ liệu giải thích TreeSHAP cho kỳ báo cáo đã chọn.")


def render_red_flags(red_flags: pl.DataFrame) -> None:
    """Display active Layer 4A Accounting Red Flags (RF_01..RF_09) with structured JSON evidence."""
    st.subheader("Cờ Cảnh báo Kế toán (Accounting Red Flags RF_01..RF_09)")
    if red_flags.is_empty():
        st.success(
            "Không có cờ cảnh báo kế toán nào kích hoạt trong giai đoạn đã chọn."
        )
        return

    for flag in red_flags.to_dicts():
        severity = str(flag.get("severity", "INFO"))
        title = str(flag.get("flag_title_vn", flag.get("flag_code", "")))
        code = str(flag.get("flag_code", ""))
        period = _fmt_date(flag.get("period_end_date"))
        explanation = str(flag.get("explanation_text_vn", ""))
        filing_id = str(flag.get("filing_id", ""))
        raw_evidence = str(flag.get("evidence_json", "{}"))

        header = f"[{severity}] {title} ({code}) — Kỳ {period}"
        if severity == "CRITICAL":
            st.error(header)
        elif severity == "HIGH":
            st.warning(header)
        else:
            st.info(header)

        st.caption(f"{explanation} · Hồ sơ neo (`filing_id`): `{filing_id}`")
        with st.expander(f"Xem bằng chứng số học ({code} · {period})"):
            try:
                st.json(json.loads(raw_evidence))
            except ValueError:
                st.code(raw_evidence)


def render_historical_screener_disclaimer() -> None:
    """Render the mandatory AGENT.md §5.7 non-PIT disclaimer banner on Page 4."""
    st.warning(
        "**Tuyên bố Miễn trừ Trung thực Nghiệp vụ (Latest Snapshot View — KHÔNG phải Point-in-Time Backtest):** "
        "Bộ lọc này truy vấn trực tiếp trên **bản chụp xuất bản mới nhất (Latest Snapshot)**. "
        "Số liệu ở các kỳ lịch sử có thể đã phản ánh **báo cáo kiểm toán/đính chính sau ngày kết thúc kỳ**, "
        "**phân loại ngành ICB hiện hành** và **điểm ML thuộc giai đoạn huấn luyện** ở các quý quá khứ. "
        "Tuyệt đối **KHÔNG** sử dụng kết quả này thay thế cho mô phỏng kiểm thử ngược chuẩn Point-in-Time (PIT Backtest)."
    )


def _components_to_table(components: tuple[LineageComponent, ...]) -> pl.DataFrame:
    if not components:
        return pl.DataFrame()
    return pl.DataFrame(
        [
            {
                "Chỉ tiêu chuẩn": item.metric_id,
                "Loại kỳ": item.period_type,
                "Năm": item.fiscal_year,
                "Quý": item.fiscal_quarter,
                "Ngày kết thúc kỳ": item.period_end_date.isoformat(),
                "Giá trị (VND / Đơn vị)": item.value,
                "filing_id": item.filing_id,
                "Chế độ kế toán": item.taxonomy_version,
                "Phương pháp suy diễn": item.derivation_method,
                "Nguồn": item.source_provider,
                "Connector": item.connector_name,
                "Ngày công bố": (
                    item.published_at.isoformat() if item.published_at else None
                ),
                "Cơ sở công bố": item.published_at_basis,
                "Ngày khả dụng PIT": (
                    item.effective_available_at.isoformat()
                    if item.effective_available_at
                    else None
                ),
                "canonical_content_sha256": item.canonical_content_sha256,
            }
            for item in components
        ]
    )


def render_lineage_card(card: RatioLineageCard) -> None:
    """Render the Page 3 Lineage Card tracing a financial ratio back to raw filings."""
    st.markdown(f"### Thẻ Truy xuất Nguồn gốc (Lineage Card): `{card.ratio_name}`")
    st.caption(
        f"Mã CK: **{card.ticker}** · Phạm vi: **{card.statement_scope}** · "
        f"Kỳ: **{card.period_type} {card.period_end_date.isoformat()}** · "
        f"Chế độ kế toán: **{card.taxonomy_version}**"
    )

    col1, col2, col3 = st.columns(3)
    col1.metric(
        card.display_name_vn,
        f"{card.ratio_value:.4f}" if card.ratio_value is not None else "NULL",
    )
    col2.metric("Cờ trạng thái (status_flag)", card.status_flag or "N/A")
    col3.metric("Chế độ mẫu số", card.denominator_mode)

    st.info(f"**Công thức toán học:** `{card.formula}`")

    if card.orphan_filing_ids:
        st.error(
            f"Phát hiện khóa ngoại mồ côi (orphan filing_id): "
            f"{', '.join(card.orphan_filing_ids)}"
        )
    else:
        st.success(
            f"Toàn vẹn truy xuất 100% — Không có khóa ngoại mồ côi "
            f"({len(card.source_filing_ids)} hồ sơ nguồn đã xác minh)."
        )

    st.markdown("#### 1. Chứng cứ Tử số (Numerator / TTM Components)")
    num_df = _components_to_table(card.numerator_components)
    if num_df.is_empty():
        st.warning("Không có bản ghi tử số tương ứng cho kỳ đã chọn.")
    else:
        st.dataframe(num_df, use_container_width=True, hide_index=True)

    st.markdown("#### 2. Chứng cứ Mẫu số (Denominator — Kỳ đầu & Kỳ cuối)")
    den_df = _components_to_table(card.denominator_components)
    if den_df.is_empty():
        st.warning("Không có bản ghi mẫu số tương ứng cho kỳ đã chọn.")
    else:
        st.dataframe(den_df, use_container_width=True, hide_index=True)
