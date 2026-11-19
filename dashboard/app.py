"""Main Streamlit entry point for the finintel Layer 5 Serving Dashboard.

Verified Sources:
    - Streamlit Multipage Apps & Caching (`streamlit==1.45.1`):
      https://docs.streamlit.io/develop/concepts/architecture/caching
    - Runtime API Verification (Python 3.11.9, streamlit==1.45.1).

Architectural invariants:
    - Read-only serving entry point backed by ``DashboardClient`` (local DuckDB
      ``DashboardQueryService`` or FastAPI via validated ``FININTEL_API_URL``).
    - Native Streamlit widgets only (raw HTML rendering disabled) and sanitized errors.
"""

from __future__ import annotations

import streamlit as st

from dashboard.client import DashboardClient
from dashboard.components.widgets import render_sanitized_error


def main() -> None:
    st.set_page_config(
        page_title="finintel — Phân tích BCTC Doanh nghiệp Việt Nam",
        page_icon="📊",
        layout="wide",
    )
    st.title("finintel — Hệ thống Trí tuệ Tài chính Doanh nghiệp Việt Nam")
    st.caption(
        "Giao diện phục vụ chỉ đọc (Read-Only) từ Snapshot xuất bản bất biến "
        "(Parquet / DuckLake) qua lớp Serving hợp nhất."
    )

    try:
        client = DashboardClient.from_env()
        overview = client.get_snapshot_overview()
    except Exception as exc:
        render_sanitized_error(exc, context="nạp Snapshot đang hoạt động")
        st.info(
            "Hãy chạy pipeline đến bước PUBLISH (`python -m finintel.pipeline.cli run`) "
            "hoặc cấu hình biến môi trường `FININTEL_DATA_ROOT` / `FININTEL_API_URL` hợp lệ."
        )
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Snapshot đang hoạt động", overview.snapshot_id or "N/A")
    c2.metric("Số doanh nghiệp (Issuers)", overview.total_issuers)
    c3.metric(
        "Kỳ đạt chuẩn PASS / WARNING",
        f"{overview.pass_periods} / {overview.warning_periods}",
    )
    c4.metric("Bản ghi cách ly (Quarantine)", overview.quarantined_rows)

    mode_label = (
        "FastAPI Read-Only API (HTTP)"
        if client.mode == "api"
        else "Local DuckDB Read-Only Repository"
    )
    backend_label = getattr(overview, "storage_backend", "parquet")
    meta_parts = [
        f"Chế độ kết nối: `{mode_label}`",
        f"Backend lưu trữ: `{backend_label}`",
        f"Tổng số kỳ tỷ số tài chính: `{overview.total_ratio_periods:,}`",
    ]
    if overview.published_at:
        meta_parts.append(
            f"Thời điểm xuất bản Snapshot (UTC): `{overview.published_at}`"
        )
    st.caption(" · ".join(meta_parts))

    st.markdown(
        """
### Hướng dẫn điều hướng 5 trang phân tích (Thanh bên trái)
1. **Trang 1 — Hồ sơ Sức khỏe Tài chính Doanh nghiệp:** Biểu đồ tăng trưởng Doanh thu – Lợi nhuận – CFO, cấu trúc Tài sản – Nợ vay – VCSH, phân rã DuPont 5 thành phần, **Chất lượng Lợi nhuận Lõi (`Core Earnings` + `RF_08`/`RF_09`)**, **Điểm Định lượng Kinh điển (`Altman Z''`, `Piotroski F`, `Beneish M`), Hình mẫu K-Means, Xác suất Suy thoái hiệu chuẩn (`QUARTER`) & biểu đồ giải thích TreeSHAP Waterfall**, và Cờ cảnh báo kế toán (`RF_01..RF_09`).
2. **Trang 2 — So sánh Tương quan Ngành:** Bảng xếp hạng cùng ngành ICB cấp 2/3, biểu đồ Box Plot đặt doanh nghiệp cạnh Trung vị ngành (`Peer Median`) & dải tứ phân vị ($P_{25} \\rightarrow P_{75}$), kèm **Quỹ đạo Phân vị Ngành qua Thời gian (`[P25–P75]` + `Peer Median`)** có hiển thị rõ `peer_group_level`, `icb_code_used`, `peer_count` và cảnh báo `REGIME_FALLBACK`.
3. **Trang 3 — Kiểm định Chất lượng & Truy xuất Chứng cứ:** Tra cứu **38/38 Thẻ Truy xuất Nguồn gốc (`Lineage Card`)** bao gồm cả 3 tỷ số lợi nhuận lõi (`FORENSIC_RATIOS`) về tận `filing_id`, `taxonomy_version`, ngày công bố và `canonical_content_sha256`, cùng bảng phân trang `quality_summary` và `audit_quarantine`.
4. **Trang 4 — Tầm soát Mặt cắt Ngang Lịch sử (Latest Snapshot View):** Tầm soát toàn thị trường theo ngành ICB cấp 2, `Piotroski F`, `Altman Zone`, `Beneish Flag`, số cờ cảnh báo và xác suất suy thoái; trực quan hóa bằng biểu đồ phân tán rủi ro và xuất CSV an toàn (chống CSV formula injection).
5. **Trang 5 — Kiểm định Sức chịu đựng Tài chính (IMF WP/15/216 Stress Testing):** Mô phỏng trực tiếp cú sốc Doanh thu, Biên gộp, Lãi suất, DSO và DIO với 3 kịch bản dựng sẵn, KPI `Debt-at-Risk` toàn thị trường, biểu đồ so sánh theo ngành ICB cấp 2 và danh sách doanh nghiệp mới rơi xuống `Stressed ICR < 1.0`.
"""
    )


if __name__ == "__main__":
    main()
