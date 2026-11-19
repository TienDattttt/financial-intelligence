"""Reusable Streamlit UI widgets for Layer 5 dashboard pages.

All widgets are strictly display-only and perform zero financial calculations.
"""

from __future__ import annotations

import json

import polars as pl
import streamlit as st

from dashboard.queries import LineageComponent, RatioLineageCard


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

    display_df = dupont_series.rename(
        {
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
    )
    st.dataframe(display_df, use_container_width=True, hide_index=True)


def render_red_flags(red_flags: pl.DataFrame) -> None:
    """Display active Layer 4A Accounting Red Flags with structured JSON evidence."""
    st.subheader("Cờ Cảnh báo Kế toán (Accounting Red Flags)")
    if red_flags.is_empty():
        st.success("Không có cờ cảnh báo kế toán nào kích hoạt trong giai đoạn đã chọn.")
        return

    for flag in red_flags.to_dicts():
        severity = str(flag.get("severity", "INFO"))
        title = str(flag.get("flag_title_vn", flag.get("flag_code", "")))
        code = str(flag.get("flag_code", ""))
        period = str(flag.get("period_end_date", ""))
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
