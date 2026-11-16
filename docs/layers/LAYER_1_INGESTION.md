# ĐẶC TẢ XÂY DỰNG TẦNG 1: INGESTION & RAW ARCHIVE

* **Mã tài liệu:** `LAYER-1`
* **Module đích:** `src/finintel/ingestion/`
* **Điều kiện tiên quyết:** Người phụ trách đã duyệt Gate 0 dựa trên `data/metadata/spike_report.json`.

---

## 1. BÀI TOÁN & PHẠM VI TRÁCH NHIỆM

Tầng 1 chịu trách nhiệm thu thập Báo cáo Tài chính (Bảng Cân đối kế toán, Kết quả kinh doanh, Lưu chuyển tiền tệ) của các doanh nghiệp niêm yết và lưu trữ vào kho nguyên bản bất biến (`data/raw/`), đảm bảo:
1. **Nguồn chính FHSC.** `FhscOapiAdapter` dùng `vnstock-oapi==0.1.2` để truy xuất JSON gốc của API, chạy được trên Windows với Windows Application Control. `FileAdapter` tiếp tục là đường nhập BCTC gốc từ Excel, CSV, Parquet hoặc JSON và là nguồn dự phòng.
2. **Phiên bản dữ liệu.** Tách mã băm file vật lý khỏi mã băm nội dung một kỳ theo mã chỉ tiêu gốc. Cách này giữ nguyên payload và tránh tăng phiên bản khi chỉ có thứ tự trường hoặc metadata truy xuất thay đổi.
3. **Phân công Tầng 1 và Tầng 2.** Tầng 1 lưu trang nguyên bản và thông tin truy xuất. Tầng 2 ánh xạ chính xác theo nguồn, loại báo cáo, mã chỉ tiêu gốc và chế độ kế toán. Tầng 2 xử lý giá trị thiếu, đơn vị, phạm vi và quy ước kỳ dựa trên bằng chứng trước khi xuất số liệu chuẩn.

---

## 2. NGUỒN HỌC ĐÃ KIỂM CHỨNG & CÁCH TÁI SỬ DỤNG

1. **FHSC Open API và `vnstock-oapi`.** Dùng `source("fhsc")` rồi lập truy vấn bằng `Source.plan("financials", ticker, statement=..., period=...)`. `Source.raw()` trả về các `Envelope` theo trang, với payload FHSC trong `Envelope.body`. Dùng `PROVIDER_RAW_JSON` cho dữ liệu này và `TRUE_ORIGINAL_DOCUMENT` cho tài liệu BCTC gốc. [Tài liệu endpoint FHSC](https://developers.fhsc.com.vn/api-reference/phan-tich-co-ban/bao-cao-tai-chinh) · [Tài liệu Python của vnstock-oapi](https://www.vnstocks.com/docs/vnstock-oapi/python)
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
├── fhsc_oapi_adapter.py # Adapter đọc JSON FHSC thông qua vnstock-oapi
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
    connector_name TEXT NOT NULL,                -- Ví dụ 'vnstock-oapi==0.1.2', 'file_adapter_v1'
    payload_nature TEXT NOT NULL,                -- 'TRUE_ORIGINAL_DOCUMENT' | 'PROVIDER_RAW_JSON'
    statement_type TEXT NOT NULL,                -- 'BALANCE_SHEET' | 'INCOME_STATEMENT' | 'CASH_FLOW'
    statement_scope TEXT NOT NULL,               -- 'CONSOLIDATED' | 'SEPARATE' | 'UNKNOWN'
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
    canonical_content_sha256 TEXT NOT NULL,      -- Hash nội dung một kỳ theo mã chỉ tiêu gốc đã sắp xếp
    raw_archive_path TEXT NOT NULL,              -- Đường dẫn tương đối trong data/raw/
    raw_record_locator TEXT,                     -- JSON Pointer tới kỳ trong trang FHSC, ví dụ '/data/2'
    provider_attrs_json TEXT                     -- Tham số truy vấn, traceId, HTTP status và metadata truy xuất
);
```

---

## 4. QUY TẮC NGHIỆP VỤ THU THẬP & LƯU TRỮ RAW

### Truy vấn, lưu trang và bàn giao

* Endpoint `financial-statement/statement` nhận `symbol`, `type`, `period` (`quarterly` hoặc `annual`), `page`, `limit` từ 1 đến 5 và bộ lọc `year` tùy chọn. Duyệt từ trang mới nhất cho tới trang rỗng đã được kiểm chứng. Ghi trạng thái chưa hoàn tất nếu chạm giới hạn trang của client. Dừng nếu nguồn trả 401/403/429 hoặc circuit breaker đang mở.
* Lưu mỗi payload nguyên bản theo trang với mã chỉ tiêu, giá trị, `null` và thứ tự mảng do FHSC trả về. Giữ thông tin nguồn, tham số truy vấn đã lọc, mã HTTP, giờ tải và mã băm phản hồi trong metadata. Một trang có thể phục vụ nhiều bản ghi manifest qua `raw_record_locator`. Gói `vnstock-oapi` là thư viện kết nối duy nhất trong đường dẫn FHSC.
* FHSC trả `year` và, với báo cáo quý, `quarter`. Lấy ticker, loại báo cáo và loại kỳ từ yêu cầu. Lấy `fetched_at` từ envelope. Ngày đầu kỳ và cuối kỳ cần lịch tài chính đã xác nhận. Nếu chưa đủ thông tin để lập bản ghi manifest, giữ trang trong kho raw để bổ sung metadata sau.
* Mặc định giữ `statement_scope = 'UNKNOWN'` khi thiếu bằng chứng. Sáu chỉ tiêu năm 2025 của FPT khớp BCTC hợp nhất kiểm toán. Bộ lọc `statement_scope=separate` cho cùng dữ liệu trong phép thử FPT. Phạm vi được xác nhận theo kỳ từ bằng chứng đối chiếu trước khi Tầng 2 ghép chuỗi.
* Tầng 2 dùng mã FHSC chính xác để ánh xạ VAS. Ba chỉ tiêu dòng đã kiểm tra ở FPT khớp tổng bốn quý với năm trong giai đoạn 2022–2025. Giữ riêng số năm và số Q4, ghi rõ các chênh lệch lịch sử, và chuyển các kỳ vi phạm đẳng thức sang kiểm tra chất lượng. Các mã chưa khớp đi vào `unmapped_metrics`.

### 4.1. Quy tắc Hai Mã Băm (Dual SHA-256 Hashing) trong `hasher.py`
`traceId` và thời điểm truy xuất có thể thay đổi sau mỗi lần gọi API.

1. **`payload_file_sha256`** băm byte của file vật lý đã lưu. Khi JSON được ghi lại từ `Envelope.body`, lưu riêng `Envelope.body_sha256` vì đây là mã băm byte phản hồi của FHSC.
2. **`canonical_content_sha256`** băm nội dung số liệu một kỳ theo mã gốc.
   * Trích xuất bộ tứ `(raw_metric_key, statement_scope, period_end_date, rounded_numeric_value)` của tất cả các dòng có giá trị khác `NULL`.
   * Chuẩn hóa số thực về chuỗi có độ chính xác cố định (ví dụ làm tròn tới $10^{-4}$), sắp xếp tăng dần theo thứ tự từ điển của `(raw_metric_key, statement_scope, period_end_date)`, nối thành chuỗi chuẩn UTF-8 rồi băm SHA-256.
   * Khi thu thập lại cùng `(source_provider, issuer_id, statement_type, statement_scope, period_type, period_end_date)`, đối chiếu hash với bản ghi mới nhất. Hash trùng thì ghi `SKIPPED_UNCHANGED`. Hash đổi thì tạo phiên bản mới và giữ các phiên bản đã lưu. Phạm vi chưa xác minh giữ định danh riêng.

### 4.2. Quy tắc Xác định `effective_available_at` (Chống Look-Ahead Bias)
Mẫu FHSC chỉ cung cấp nhãn kỳ kế toán cho mỗi dòng. Khởi tạo `published_at = NULL`, `audit_status = 'UNKNOWN'` và `audit_opinion = 'UNKNOWN'` cho các kỳ chỉ có dữ liệu API. Áp dụng các quy tắc sau khi xác định được ngày cuối kỳ.
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
1. **Test Idempotent Re-ingestion.** Nạp cùng một trang JSON hai lần, đổi thứ tự trường, `traceId` và thời điểm truy xuất. `canonical_content_sha256` của từng kỳ giữ nguyên và lần nạp lại được đánh dấu `SKIPPED_UNCHANGED`.
2. **Test True Revision Detection:** Thay đổi giá trị của một chỉ tiêu (ví dụ `BS_TOTAL_ASSETS` thay đổi sau kiểm toán) $\rightarrow$ `canonical_content_sha256` thay đổi, hệ thống tạo `filing_id` mới với `revision_no = 2` và giữ nguyên file của `revision_no = 1` trong `data/raw/`.
3. **Test Statutory Lag Calculation:** Với báo cáo Hợp nhất kỳ `period_end_date = '2025-12-31'` không có `published_at`, kiểm tra `effective_available_at` bằng `'2026-03-31'` ($+90$ ngày cho báo cáo năm).
4. **Test FileAdapter.** Dùng BCTC mẫu `.xlsx` hoặc `.json` trong `tests/fixtures/raw_samples/` để kiểm thử nạp ngoại tuyến.
5. **Test phân trang và provenance FHSC.** Dùng mẫu có nhiều trang, trang kết thúc rỗng và giá trị `null`. Kiểm tra trang lưu giữ payload gốc, mỗi bản ghi manifest trỏ đúng kỳ và phạm vi thiếu bằng chứng được ghi `UNKNOWN`.
