# ĐẶC TẢ XÂY DỰNG TẦNG 1: INGESTION & RAW ARCHIVE

* **Mã tài liệu:** `LAYER-1`
* **Module đích:** `src/finintel/ingestion/`
* **Điều kiện tiên quyết:** Đã chạy xong kịch bản `scripts/spike_vnstock6.py` (Chặng 0) để xác nhận cấu trúc thực tế của nguồn dữ liệu.

---

## 1. BÀI TOÁN & PHẠM VI TRÁCH NHIỆM

Tầng 1 chịu trách nhiệm thu thập Báo cáo Tài chính (Bảng Cân đối kế toán, Kết quả kinh doanh, Lưu chuyển tiền tệ) của các doanh nghiệp niêm yết và lưu trữ vào kho nguyên bản bất biến (`data/raw/`), đảm bảo:
1. **Đa nguồn qua Adapter Pattern:** Hỗ trợ cả nguồn thư viện `vnstock` Gen 6 và nguồn nhập file trực tiếp (`FileAdapter` cho Excel/CSV/Parquet/JSON) với tư cách là **hai đường dữ liệu hạng nhất ngang hàng nhau (First-Class Data Paths)**.
2. **Chống sinh bản sửa đổi giả (Anti-False-Revision Hashing):** Tách biệt mã băm toàn vẹn file vật lý và mã băm nội dung số liệu đã chuẩn hóa để không tạo ra phiên bản báo cáo mới khi nhà cung cấp chỉ đổi thứ tự cột hoặc dấu thời gian truy xuất.
3. **Mô hình hóa trung thực Ngày công bố (`published_at`) và Ý kiến kiểm toán (`audit_opinion`):** Phản ánh đúng thực tế nguồn dữ liệu có cung cấp ngày công bố thực hay phải ước tính theo trần thời hạn luật định.

---

## 2. NGUỒN HỌC ĐÃ KIỂM CHỨNG & CÁCH TÁI SỬ DỤNG

1. **Tài liệu chính thức `vnstock` Thế hệ 6 (`v6.0.1`):**
   * *Cách áp dụng:* Gọi các hàm `balance_sheet()`, `income_statement()`, `cash_flow()` theo lô (mỗi lần gọi lấy toàn bộ chuỗi lịch sử nhiều kỳ của một mã cổ phiếu thay vì gọi lặp từng quý). Đọc thông tin kiểm tra đẳng thức kế toán và danh sách mã chưa ánh xạ từ `df.attrs` (theo đúng tên khóa đã xác nhận trong `spike_report.json`).
   * *Lưu ý bản chất dữ liệu:* Vì gói `vnstock` thực hiện chuẩn hóa cây chỉ tiêu VAS ngay trên máy trạm (trừ khi dùng trực tiếp lớp `vnstock-oapi`), dữ liệu thu được từ `vnstock` phải được gắn nhãn `payload_nature = 'PROVIDER_NORMALIZED_TABLE'`, phân biệt với `'TRUE_ORIGINAL_DOCUMENT'` khi nạp từ file BCTC gốc qua `FileAdapter`.
2. **`dgunning/edgartools` (GitHub):**
   * *Cách áp dụng:* Học cách tách lớp đối tượng định danh tờ khai (`FilingMetadata`) khỏi nội dung số liệu (`RawPayload`) để có thể kiểm tra xem một báo cáo đã tồn tại trong kho chưa trước khi thực hiện bước chuẩn hóa nặng.
3. **Thông tư 96/2020/TT-BTC (Điều 10, 11) & Thông tư 68/2024/TT-BTC:**
   * *Cách áp dụng:* Quy định trần thời hạn công bố thông tin BCTC tại Việt Nam để tính mốc `effective_available_at` khi nguồn dữ liệu không trả về ngày công bố thực tế.

---

## 3. KIẾN TRÚC MODULE & HỢP ĐỒNG DỮ LIỆU

### 3.1. Cấu trúc File trong `src/finintel/ingestion/`
```text
src/finintel/ingestion/
├── __init__.py
├── base.py              # FinancialStatementProvider (ABC), RawFilingBundle, FilingMetadata
├── vnstock6_adapter.py  # Adapter kết nối vnstock Gen 6 (chỉ gọi sau khi pass health_check)
├── file_adapter.py      # Adapter hạng nhất đọc file Excel (.xlsx), CSV, JSON, Parquet chuẩn
├── hasher.py            # Tính payload_file_sha256 và canonical_content_sha256
└── archive.py           # Lưu file bất biến vào data/raw/ và ghi bảng filings_manifest
```

### 3.2. Hợp đồng Dữ liệu `filings_manifest` (Lưu trong `state.sqlite`)

```sql
CREATE TABLE IF NOT EXISTS filings_manifest (
    filing_id TEXT PRIMARY KEY,                  -- Định danh duy nhất của phiên bản tờ khai
    issuer_id TEXT NOT NULL,                     -- Mã định danh cố định của tổ chức phát hành
    ticker_at_filing TEXT NOT NULL,              -- Mã chứng khoán tại thời điểm báo cáo (VD: 'FPT')
    source_provider TEXT NOT NULL,               -- Nguồn dữ liệu gốc (VD: 'fhsc', 'hsx_filing', 'manual_audit')
    connector_name TEXT NOT NULL,                -- Công cụ nạp (VD: 'vnstock==6.0.1', 'file_adapter_v1')
    payload_nature TEXT NOT NULL,                -- 'TRUE_ORIGINAL_DOCUMENT' | 'PROVIDER_NORMALIZED_TABLE'
    statement_scope TEXT NOT NULL,               -- 'CONSOLIDATED' | 'SEPARATE'
    period_type TEXT NOT NULL,                   -- 'QUARTER' | 'SEMI_ANNUAL' | 'ANNUAL'
    fiscal_year INTEGER NOT NULL,
    fiscal_quarter INTEGER,                      -- 1, 2, 3, 4 (NULL nếu chỉ là báo cáo năm không tách quý)
    fiscal_year_start_date TEXT NOT NULL,        -- YYYY-MM-DD (VD: '2026-01-01' hoặc '2025-04-01' cho niên độ lệch)
    period_start_date TEXT NOT NULL,             -- YYYY-MM-DD
    period_end_date TEXT NOT NULL,               -- YYYY-MM-DD
    published_at TEXT,                           -- YYYY-MM-DD (NULL nếu nguồn không cung cấp ngày công bố thật)
    published_at_basis TEXT NOT NULL,            -- 'EXACT_FILING' | 'STATUTORY_LAG' | 'UNKNOWN'
    effective_available_at TEXT NOT NULL,        -- Ngày an toàn để tránh look-ahead bias (YYYY-MM-DD)
    audit_status TEXT NOT NULL,                  -- 'UNAUDITED' | 'REVIEWED' | 'AUDITED' | 'UNKNOWN'
    audit_opinion TEXT NOT NULL,                 -- 'UNMODIFIED' | 'EXCEPT_FOR' | 'ADVERSE' | 'DISCLAIMER' | 'NOT_APPLICABLE' | 'UNKNOWN'
    is_filed_late INTEGER,                       -- 0 (Đúng hạn) | 1 (Nộp trễ hạn luật định) | NULL (Chưa rõ ngày công bố)
    revision_no INTEGER NOT NULL DEFAULT 1,      -- Số thứ tự phiên bản (1, 2, 3...)
    revision_reason TEXT NOT NULL,               -- 'ORIGINAL' | 'AUDITED_UPDATE' | 'CORRECTION' | 'RESTATED_REGIME_CHANGE'
    fetched_at TEXT NOT NULL,                    -- ISO-8601 UTC timestamp khi tải về
    payload_file_sha256 TEXT NOT NULL,           -- Hash toàn vẹn của file vật lý lưu trên đĩa
    canonical_content_sha256 TEXT NOT NULL,      -- Hash trên tập (metric_code, period_end, scope, value) đã sắp xếp
    raw_archive_path TEXT NOT NULL,              -- Đường dẫn tương đối trong data/raw/
    provider_attrs_json TEXT                     -- Lưu nguyên trạng df.attrs hoặc metadata của nguồn
);
```

---

## 4. QUY TẮC NGHIỆP VỤ THU THẬP & LƯU TRỮ RAW

### 4.1. Quy tắc Hai Mã Băm (Dual SHA-256 Hashing) trong `hasher.py`
Để tránh việc mỗi lần gọi lại API lại sinh ra một `revision_no` mới chỉ vì trường `retrieved_at` trong metadata thay đổi:
1. **`payload_file_sha256`:** Băm trực tiếp trên chuỗi byte của file vật lý lưu xuống đĩa (`.parquet`, `.json.zst` hoặc `.xlsx`) để kiểm tra lỗi hỏng file (bit-rot).
2. **`canonical_content_sha256`:**
   * Trích xuất bộ tứ `(raw_metric_key, statement_scope, period_end_date, rounded_numeric_value)` của tất cả các dòng có giá trị khác `NULL`.
   * Chuẩn hóa số thực về chuỗi có độ chính xác cố định (ví dụ làm tròn tới $10^{-4}$), sắp xếp tăng dần theo thứ tự từ điển của `(raw_metric_key, statement_scope, period_end_date)`, nối thành chuỗi chuẩn UTF-8 rồi băm SHA-256.
   * **Quy tắc tạo Revision mới:** Khi thu thập lại cùng một `(issuer_id, statement_scope, period_type, period_end_date)`, nếu `canonical_content_sha256` **trùng khớp** với bản ghi mới nhất đã có trong `filings_manifest`, hệ thống đánh dấu bước đó là `'SKIPPED_UNCHANGED'` và không tăng `revision_no`. Chỉ khi `canonical_content_sha256` thay đổi thực sự về mặt con số kế toán mới tạo `filing_id` mới với `revision_no = max_revision + 1`.

### 4.2. Quy tắc Xác định `effective_available_at` (Chống Look-Ahead Bias)
Vì `vnstock` Gen 6 chỉ trả về nhãn kỳ kế toán (`2025-Q3`) mà không có ngày công bố thực tế:
* **Trường hợp 1 (`published_at` có giá trị thực từ văn bản CBTT / FileAdapter):**
  * `published_at_basis = 'EXACT_FILING'`
  * `effective_available_at = published_at`
  * Kiểm tra `is_filed_late`: So sánh `published_at` với hạn trần luật định theo Thông tư 96/2020/TT-BTC. Nếu vượt quá hạn trần $\rightarrow$ `is_filed_late = 1`, ngược lại `= 0`.
* **Trường hợp 2 (`published_at IS NULL` khi lấy từ API chỉ có kỳ kế toán):**
  * `published_at_basis = 'STATUTORY_LAG'`
  * `is_filed_late = NULL`
  * Cộng độ trễ trần bảo thủ của **BCTC Hợp nhất / Công ty mẹ** vào `period_end_date`:
    * Nếu `period_type == 'QUARTER'` (Q1, Q3, hoặc Q4 chưa kiểm toán): $\text{effective\_available\_at} = \text{period\_end\_date} + 30\text{ ngày}$.
    * Nếu `period_type == 'SEMI_ANNUAL'` (hoặc Q2 có soát xét bán niên): $\text{effective\_available\_at} = \text{period\_end\_date} + 60\text{ ngày}$.
    * Nếu `period_type == 'ANNUAL'` (Báo cáo năm kiểm toán): $\text{effective\_available\_at} = \text{period\_end\_date} + 90\text{ ngày}$.

---

## 5. TIÊU CHÍ NGHIỆM THU TẦNG 1 (DEFINITION OF DONE)

Agent phải viết và chạy xanh `tests/unit/test_layer1_ingestion.py`:
1. **Test Idempotent Re-ingestion (Chống Revision giả):** Nạp cùng một bảng dữ liệu hai lần nhưng thay đổi thứ tự cột và thay đổi dấu thời gian `retrieved_at` trong `df.attrs` $\rightarrow$ `canonical_content_sha256` phải giữ nguyên 100%, không tạo bản ghi `revision_no = 2`.
2. **Test True Revision Detection:** Thay đổi giá trị của một chỉ tiêu (ví dụ `BS_TOTAL_ASSETS` thay đổi sau kiểm toán) $\rightarrow$ `canonical_content_sha256` thay đổi, hệ thống tạo `filing_id` mới với `revision_no = 2` và giữ nguyên file của `revision_no = 1` trong `data/raw/`.
3. **Test Statutory Lag Calculation:** Với báo cáo Hợp nhất kỳ `period_end_date = '2025-12-31'` không có `published_at`, kiểm tra `effective_available_at` bằng `'2026-03-31'` ($+90$ ngày cho báo cáo năm).
4. **Test First-Class FileAdapter:** Nạp thành công một bộ BCTC chuẩn từ file `.xlsx` / `.json` trong `tests/fixtures/raw_samples/` mà không cần kết nối mạng hay gọi `vnstock`.