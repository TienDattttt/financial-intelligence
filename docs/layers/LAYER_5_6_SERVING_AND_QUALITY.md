# ĐẶC TẢ XÂY DỰNG TẦNG 5 & TẦNG 6: SERVING DASHBOARD & DATA QUALITY GATES

* **Mã tài liệu:** `LAYER-5-6`
* **Module đích:** `src/finintel/quality/`, `dashboard/` và `tests/`

---

## PHẦN A: ĐẶC TẢ TẦNG 6 — DATA QUALITY, RECONCILIATION & TESTING

*(Lưu ý: Tầng 6 được cài đặt và chạy kiểm định trước khi xuất bản dữ liệu lên Tầng 5).*

### 1. Bài toán & Cơ chế Cổng Kiểm định 3 Mức độ (3-Tier Severity Gate)
Không phải mọi sự cố dữ liệu trên thị trường chứng khoán Việt Nam đều giống nhau: nếu 1 doanh nghiệp trên sàn UPCoM nộp thiếu bảng LCTT mà dừng toàn bộ pipeline thì hệ thống sẽ tê liệt; nhưng nếu nguồn dữ liệu bị đổi đơn vị từ Đồng sang Tỷ đồng hoặc lệch bảng Cân đối kế toán mà vẫn xuất bản thì hệ thống sẽ mất uy tín.

Module `src/finintel/quality/gate.py` phải phân loại mọi kết quả kiểm tra thành **3 mức độ nghiêm trọng**:

| Mức độ | Điều kiện kích hoạt (Rules) | Hành vi xử lý của Pipeline |
|---|---|---|
| **`CRITICAL`** | 1. **Lệch Đẳng thức CĐKT (`BS_IDENTITY_MISMATCH`):** $\frac{\left|\text{Tổng Tài Sản} - (\text{Nợ Phải Trả} + \text{Vốn Chủ})\right|}{\max(\left|\text{Tổng Tài Sản}\right|, 1)} > 0.001$ ($0.1\%$).<br>2. **Lệch Tổng Khoản mục Cha-Con (`SUBTOTAL_MISMATCH`):** $\text{Tài sản ngắn hạn (100)} + \text{Tài sản dài hạn (200)} \neq \text{Tổng Tài sản (270)}$ vượt quá $0.1\%$.<br>3. **Bước nhảy Đơn vị Đột biến (`UNIT_SCALE_JUMP`):** Tổng tài sản kỳ hiện tại gấp $> 100$ lần hoặc nhỏ hơn $< 0.01$ lần kỳ liền trước của cùng `statement_scope`.<br>4. **Trùng khóa chính (`DUPLICATE_PRIMARY_KEY`)** hoặc thiếu các chỉ tiêu bắt buộc tối thiểu (`BS_TOTAL_ASSETS`, `BS_TOTAL_EQUITY`, `IS_NET_REVENUE`, `IS_NET_INCOME`). | - **Cách ly (`Quarantine`):** Chặn tờ khai (`filing_id`) bị lỗi, đẩy vào bảng `audit_quarantine.parquet`, không đưa vào Snapshot xuất bản.<br>- **Cầu dao toàn cục (`Batch Circuit Breaker`):** Nếu số lượng doanh nghiệp dính lỗi `CRITICAL` chiếm $> 10\%$ tổng số doanh nghiệp trong đợt chạy $\rightarrow$ Ném `CriticalDataQualityError` và **dừng ngay việc xuất bản Snapshot mới** (giữ nguyên Snapshot an toàn trước đó). |
| **`WARNING`** | 1. **Lệch đối chiếu Tiền mặt (`CASH_FLOW_RECONCILIATION_WARN`):** Tiền và tương đương tiền cuối kỳ trên Bảng LCTT (Mã `70`) lệch so với Tiền và tương đương tiền trên Bảng CĐKT (Mã `110`) $> 0.5\%$.<br>2. **Báo cáo khuyết bảng phụ (`PARTIAL_STATEMENT`):** Có CĐKT và KQKD nhưng thiếu Bảng LCTT, hoặc lập LCTT trực tiếp làm khuyết dòng Khấu hao.<br>3. **Suy diễn Quý 4 pha trộn kiểm toán:** `derivation_method == 'DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX'`. | - Cho phép dữ liệu đi tiếp vào Snapshot xuất bản.<br>- Gắn nhãn `quality_status = 'WARNING'` kèm danh sách mã cảnh báo vào bảng `quality_summary.parquet` để hiển thị băng rôn thông báo rõ ràng trên Dashboard. |
| **`INFO`** | Các tỷ số tài chính có giá trị âm sâu hoặc biến động mạnh nhưng **hợp lệ về mặt kế toán** (ví dụ: `ROE` âm, `CFO` âm, Vốn chủ sở hữu âm). | - **Tuyệt đối không coi là lỗi dữ liệu!**<br>- Chuyển sang `red_flags.py` ở Tầng 4A làm tín hiệu cảnh báo rủi ro tài chính. |

### 2. Quy chuẩn Bộ Dữ liệu Đối chứng (`Golden Test Fixtures`) và Dung sai Kiểm thử
Trong `tests/reconciliation/test_golden_ratios.py`, Agent phải đối chiếu kết quả chạy từ pipeline với bộ dữ liệu chuẩn trong `tests/fixtures/golden_ratios/` (do con người trích xuất trực tiếp từ file PDF BCTC gốc của các doanh nghiệp mẫu) theo đúng quy định dung sai sau:
* **Dung sai cho Giá trị Tiền tệ Chuẩn hóa (`Normalization & TTM Values`):** Sai số tương đối $\text{rel\_tol} = 10^{-3}$ ($0.1\%$ — bù đắp sai số làm tròn khi một số nguồn tóm tắt làm tròn tới hàng triệu đồng).
* **Dung sai cho Tỷ số Tài chính (`Financial Ratios` dạng số thập phân, ví dụ `ROA = 0.1245`):** Sai số tuyệt đối $\text{abs\_tol} = 10^{-4}$ ($0.01\text{ điểm phần trăm}$, tức $1\text{ bps}$).
* **Kiểm tra khớp 100% đối với Cờ Trạng thái (`status_flag`):** Các cờ `'NEGATIVE_OR_ZERO_EQUITY'`, `'NO_INTEREST_EXPENSE'`, `'MIXED_TAXONOMY'` phải khớp chính xác tuyệt đối về mặt chuỗi.

---

## PHẦN B: ĐẶC TẢ TẦNG 5 — SERVING / STREAMLIT DASHBOARD

### 1. Bài toán & Nguyên tắc Kiến trúc Giao diện
Tầng 5 cung cấp ứng dụng web nội bộ bằng **Streamlit + Plotly** (`dashboard/`) giúp người dùng tra cứu phân tích và kiểm chứng ngược từng con số về tận nguồn gốc.
* **Quy tắc Tách biệt Tuyệt đối:** Toàn bộ dữ liệu hiển thị phải được đọc từ thư mục `data/published/snapshots/<active_snapshot_id>/` thông qua `src/finintel/storage/repository.py` (sử dụng DuckDB in-memory).
* **Cấm Tính toán trên UI:** Trong thư mục `dashboard/` tuyệt đối không được chứa bất kỳ công thức cộng trừ nhân chia chỉ tiêu kế toán nào.

### 2. Thiết kế 3 Trang Cốt lõi của MVP (`dashboard/pages/`)

1. **Trang 1 — Hồ sơ Sức khỏe Tài chính Doanh nghiệp (`1_company_profile.py`):**
   * Bộ lọc trên thanh bên: Chọn Mã cổ phiếu (`ticker`), Phạm vi báo cáo (`CONSOLIDATED` mặc định / `SEPARATE`), và Loại kỳ (`TTM` / `Quarterly` / `Annual`).
   * Hiển thị băng rôn trạng thái chất lượng (`quality_status`) và chế độ kế toán của kỳ mới nhất (`VAS_TT200_2014` hay `VAS_TT99_2025`).
   * **4 cụm biểu đồ Plotly:** (a) Tăng trưởng Doanh thu – Lợi nhuận – Dòng tiền CFO, (b) Cấu trúc Tài sản – Nợ vay – Vốn chủ sở hữu, (c) Bảng phân rã **DuPont 5 thành phần** qua các kỳ, và (d) Danh sách **Cờ Cảnh báo Kế toán (`Accounting Red Flags`)** đang kích hoạt.
2. **Trang 2 — So sánh Tương quan Ngành (`2_industry_comparison.py`):**
   * Hiển thị bảng xếp hạng các doanh nghiệp trong cùng ngành ICB cấp 2/cấp 3 tại kỳ được chọn.
   * Vẽ biểu đồ **Box Plot / Bullet Chart** đặt giá trị của doanh nghiệp cạnh đường Trung vị ngành (`Peer Median`) và dải tứ phân vị ($P_{25} \rightarrow P_{75}$).
   * Hiển thị ghi chú rõ ràng ở chân trang: *"Phân nhóm ngành ICB theo ảnh chụp danh mục ngày `<industry_snapshot_date>`"*.
3. **Trang 3 — Kiểm định Chất lượng & Truy xuất Chứng cứ (`3_audit_and_quality.py`):**
   * Đây là trang chứng minh tính minh bạch của hệ thống: Khi người dùng chọn một mã cổ phiếu, một kỳ và một tỷ số (ví dụ `ROE` hoặc `ROIC`), hệ thống hiển thị **Thẻ Truy xuất Nguồn gốc (`Lineage Card`)** gồm:
     * Công thức toán học đã dùng và trạng thái cờ (`status_flag`).
     * Giá trị tử số (`TTM`) được cộng từ 4 kỳ nào, mỗi kỳ thuộc `filing_id` nào, `taxonomy_version` nào, `derivation_method` là gì (`REPORTED_DIRECTLY` hay `DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX`).
     * Giá trị mẫu số được tính trung bình từ kỳ đầu và kỳ cuối nào.
     * Nguồn cung cấp (`source_provider`), công cụ nạp (`connector_name`), ngày công bố (`published_at` / `published_at_basis`) và mã băm `canonical_content_sha256`.

---

## 3. TIÊU CHÍ NGHIỆM THU TẦNG 5 & TẦNG 6 (DEFINITION OF DONE)

Agent phải viết và chạy xanh `tests/unit/test_layer6_quality.py` và `tests/integration/test_end_to_end_slice.py`:
1. **Test Quarantine vs Batch Halt:**
   * Đưa vào một lô 20 doanh nghiệp trong đó có 1 doanh nghiệp bị lệch bảng CĐKT ($\text{Assets} \neq \text{Liabilities} + \text{Equity}$) $\rightarrow$ Doanh nghiệp lỗi bị đẩy vào `audit_quarantine.parquet`, 19 doanh nghiệp còn lại vẫn xuất bản thành công ra Snapshot mới.
   * Đưa vào một lô 10 doanh nghiệp trong đó có 3 doanh nghiệp ($30\% > 10\%$) bị lỗi nhảy đơn vị gấp $1.000$ lần $\rightarrow$ Hệ thống ném `CriticalDataQualityError` và giữ nguyên Snapshot cũ.
2. **Test Lineage Traceability:** Với bất kỳ tỷ số nào trong `financial_ratios.parquet` của Snapshot xuất bản, truy vấn ngược qua `queries.py` phải trả về đầy đủ `filing_id` của cả tử số và mẫu số mà không có khóa ngoại mồ côi (orphan reference).