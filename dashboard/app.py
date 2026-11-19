"""Main Streamlit entry point for the finintel Layer 5 Serving Dashboard."""

from __future__ import annotations

import streamlit as st

from dashboard.queries import DashboardQueryService


def main() -> None:
    st.set_page_config(
        page_title="finintel — Phân tích BCTC Doanh nghiệp Việt Nam",
        page_icon="📊",
        layout="wide",
    )
    st.title("finintel — Hệ thống Trí tuệ Tài chính Doanh nghiệp Việt Nam")
    st.caption(
        "Giao diện phục vụ chỉ đọc (Read-Only) trực tiếp từ Snapshot Parquet bất biến "
        "thông qua DuckDB in-memory (`ParquetRepository`)."
    )

    try:
        service = DashboardQueryService.from_data_root()
        overview = service.get_snapshot_overview()
    except Exception as exc:
        st.error(f"Không thể nạp Snapshot đang hoạt động: {exc}")
        st.info(
            "Hãy chạy pipeline đến bước PUBLISH (`python -m finintel.pipeline.cli run`) "
            "hoặc đặt biến môi trường `FININTEL_DATA_ROOT` trỏ tới thư mục dữ liệu đã xuất bản."
        )
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Snapshot đang hoạt động", overview.snapshot_id or "N/A")
    c2.metric("Số doanh nghiệp (Issuers)", overview.total_issuers)
    c3.metric("Kỳ đạt chuẩn PASS / WARNING", f"{overview.pass_periods} / {overview.warning_periods}")
    c4.metric("Bản ghi cách ly (Quarantine)", overview.quarantined_rows)

    if overview.published_at:
        st.caption(f"Thời điểm xuất bản Snapshot (UTC): `{overview.published_at}`")

    st.markdown(
        """
### Hướng dẫn điều hướng 3 trang cốt lõi (Thanh bên trái)
1. **Trang 1 — Hồ sơ Sức khỏe Tài chính Doanh nghiệp:** Xem biểu đồ tăng trưởng Doanh thu – Lợi nhuận – CFO, cấu trúc Tài sản – Nợ vay – VCSH, bảng phân rã DuPont 5 thành phần và Cờ cảnh báo kế toán.
2. **Trang 2 — So sánh Tương quan Ngành:** Bảng xếp hạng cùng ngành ICB cấp 2/3, biểu đồ Box Plot đặt doanh nghiệp cạnh Trung vị ngành (`Peer Median`) và dải tứ phân vị ($P_{25} \\rightarrow P_{75}$).
3. **Trang 3 — Kiểm định Chất lượng & Truy xuất Chứng cứ:** Tra cứu bảng tổng hợp chất lượng, danh sách cách ly và **Thẻ Truy xuất Nguồn gốc (`Lineage Card`)** cho từng tỷ số về tận `filing_id`, `taxonomy_version`, ngày công bố và mã băm `canonical_content_sha256`.
"""
    )


if __name__ == "__main__":
    main()
