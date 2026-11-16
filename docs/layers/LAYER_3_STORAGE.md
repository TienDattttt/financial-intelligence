# ĐẶC TẢ XÂY DỰNG TẦNG 3: STORAGE, POINT-IN-TIME CONTRACTS & ATOMIC SNAPSHOTS

* **Mã tài liệu:** `LAYER-3`
* **Module đích:** `src/finintel/storage/`
* **Phụ thuộc đầu vào:** Dữ liệu chuẩn hóa từ Tầng 2, kết quả phân tích từ Tầng 4A và kiểm định chất lượng từ Tầng 6.

---

## 1. BÀI TOÁN & PHẠM VI TRÁCH NHIỆM

Tầng 3 quản lý lưu trữ và truy xuất cho toàn bộ dữ liệu đã chuẩn hóa (`curated`) và dữ liệu xuất bản cho người dùng cuối (`published`), giải quyết 4 yêu cầu cốt lõi:
1. **Triệt tiêu hoàn toàn xung đột khóa file DuckDB (Zero Concurrency Lock):** Đảm bảo tiến trình ETL chạy nền và ứng dụng Dashboard Streamlit mở liên tục không bao giờ khóa file của nhau.
2. **Khóa Nghiệp vụ Đa chiều (Multi-Dimensional Business Key):** Ngăn chặn việc ghi đè giữa Báo cáo Hợp nhất và Riêng lẻ, hoặc giữa bản gốc, bản kiểm toán và bản trình bày lại khi đổi Thông tư kế toán.
3. **Truy vấn Đúng Thời điểm (`Point-in-Time / As-Of Query`):** Phục vụ kiểm chứng lịch sử và huấn luyện mô hình mà không bị rò rỉ dữ liệu từ các bản sửa đổi trong tương lai.
4. **Xuất bản Nguyên tử & Dọn rác Snapshot (`Atomic Swap & Snapshot GC`):** Không bao giờ để Dashboard đọc phải file Parquet đang ghi dở dang, đồng thời không làm đầy ổ cứng.

---

## 2. NGUỒN HỌC ĐÃ KIỂM CHỨNG & CÁCH TÁI SỬ DỤNG

1. **Tài liệu chính thức DuckDB (`Querying Parquet` & `Concurrency`):**
   * *Cách áp dụng:* Không tạo 3 file `.duckdb` vật lý trên đĩa (`financial.duckdb`, `analytics.duckdb`, `pipeline_runs.duckdb`). Thay vào đó, lưu dữ liệu dưới dạng các file **Apache Parquet bất biến** và khởi tạo `duckdb.connect(":memory:")` trong `repository.py` để truy vấn trực tiếp các file Parquet với cơ chế đẩy bộ lọc xuống tầng đọc cột (filter pushdown & column projection).
2. **`microsoft/qlib` (Thiết kế Point-in-Time Database):**
   * *Cách áp dụng:* Mỗi sự kiện tài chính (`financial_fact`) lưu đồng thời kỳ kế toán (`period_end_date`) và mốc có hiệu lực thông tin (`effective_available_at`). Truy vấn tại thời điểm $T$ chỉ lọc các bản ghi có $\text{effective\_available\_at} \le T$ và lấy phiên bản mới nhất tính đến $T$.

---

## 3. KIẾN TRÚC MODULE & HỢP ĐỒNG SCHEMA PARQUET

### 3.1. Cấu trúc File trong `src/finintel/storage/` và `data/`
```text
src/finintel/storage/
├── __init__.py
├── schemas.py             # Định nghĩa PyArrow/Polars Schema cứng cho các bảng Parquet
├── snapshot_publisher.py  # Ghi vào thư mục tạm _tmp_, kiểm tra toàn vẹn, rename nguyên tử & dọn rác GC
└── repository.py          # Giao diện truy vấn duy nhất (DuckDB in-memory trên Parquet)

data/
├── curated/
│   ├── issuers.parquet                  # Danh mục doanh nghiệp & lịch sử phân ngành ICB
│   ├── financial_facts.parquet          # Toàn bộ lịch sử các phiên bản chỉ tiêu chuẩn hóa (PiT)
│   └── unmapped_metrics.parquet         # Các chỉ tiêu chưa khớp chờ con người duyệt
└── published/
    └── snapshots/
        └── snap_20261116_140000_a1b2/   # Thư mục Snapshot bất biến đã qua Quality Gate
            ├── _snapshot_meta.json
            ├── issuers.parquet
            ├── latest_facts.parquet
            ├── financial_ratios.parquet
            ├── industry_benchmarks.parquet
            ├── accounting_red_flags.parquet
            └── quality_summary.parquet
```

### 3.2. Hợp đồng Schema Bảng `issuers.parquet` (Xử lý Thiên lệch Sống sót Ngành ICB)
Vì danh sách phân ngành ICB lấy từ API tại một thời điểm là ảnh chụp tĩnh (static snapshot), bảng `issuers.parquet` bắt buộc phải lưu ngày chụp phân ngành để cảnh báo thiên lệch sống sót (survivorship/reclassification bias):
* `issuer_id` (`pl.Utf8`, NOT NULL) — Khóa định danh cố định.
* `ticker` (`pl.Utf8`, NOT NULL) — Mã cổ phiếu.
* `exchange` (`pl.Utf8`, NOT NULL) — `'HOSE' | 'HNX' | 'UPCOM' | 'DELISTED'`.
* `company_name_vn` (`pl.Utf8`).
* `accounting_regime` (`pl.Utf8`, NOT NULL) — `'CORPORATE' | 'BANK' | 'SECURITIES' | 'INSURANCE'`.
* `icb_code_level2` (`pl.Utf8`), `icb_name_level2` (`pl.Utf8`).
* `icb_code_level3` (`pl.Utf8`), `icb_name_level3` (`pl.Utf8`).
* `industry_snapshot_date` (`pl.Date`, NOT NULL) — Ngày ghi nhận phân ngành ICB.
* `delisted_date` (`pl.Date`, Nullable) — Ngày hủy niêm yết (nếu có).

### 3.3. Hợp đồng Schema Bảng `curated/financial_facts.parquet`
**Khóa Duy nhất (Composite Business Key):**
`(issuer_id, statement_scope, period_type, fiscal_year_start_date, period_end_date, metric_id, taxonomy_version, revision_no, filing_id)`

**Danh sách cột bắt buộc:**
* Toàn bộ 9 cột trong Composite Business Key ở trên.
* `fiscal_year` (`pl.Int32`), `fiscal_quarter` (`pl.Int8`, Nullable).
* `period_start_date` (`pl.Date`, NOT NULL).
* `published_at` (`pl.Date`, Nullable), `published_at_basis` (`pl.Utf8`, NOT NULL), `effective_available_at` (`pl.Date`, NOT NULL).
* `revision_reason` (`pl.Utf8`, NOT NULL) — `'ORIGINAL' | 'AUDITED_UPDATE' | 'CORRECTION' | 'RESTATED_REGIME_CHANGE'`.
* `derivation_method` (`pl.Utf8`, NOT NULL) — `'REPORTED_DIRECTLY' | 'DERIVED_YTD_DIFF' | 'DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX'`.
* `audit_status` (`pl.Utf8`, NOT NULL), `audit_opinion` (`pl.Utf8`, NOT NULL).
* `value` (`pl.Float64`, Nullable) — Giá trị đã quy đổi về **VND**.
* `ttm_value` (`pl.Float64`, Nullable) — Giá trị trượt 4 quý (chỉ có ở chỉ tiêu `FLOW`).
* `computation_note` (`pl.Utf8`, Nullable) — Ví dụ `'MIXED_TAXONOMY'`, `'MISSING_INTERMEDIATE_QUARTER'`.

---

## 4. QUY TẮC TRUY VẤN POINT-IN-TIME & XUẤT BẢN SNAPSHOT NGUYÊN TỬ

### 4.1. Logic Truy vấn `get_facts_as_of` trong `repository.py`
Phương thức `repository.get_facts_as_of(as_of_date: date, require_exact_filing_date: bool = False)` phải thực thi câu lệnh DuckDB SQL với quy tắc sau:
1. Nếu `require_exact_filing_date == True` (dùng cho Tầng 4B ML): Chỉ lấy các bản ghi có `published_at_basis == 'EXACT_FILING'` và $\text{published\_at} \le \text{as\_of\_date}$.
2. Nếu `require_exact_filing_date == False` (dùng cho MVP Analytics & Dashboard): Lấy các bản ghi có $\text{effective\_available\_at} \le \text{as\_of\_date}$.
3. Với mỗi nhóm `(issuer_id, statement_scope, period_type, period_end_date, metric_id)`, dùng hàm cửa sổ `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY effective_available_at DESC, revision_no DESC)` để chọn đúng 1 bản ghi mới nhất tính đến ngày `as_of_date`.

### 4.2. Quy trình Xuất bản Snapshot Nguyên tử & Dọn rác (`snapshot_publisher.py`)
1. **Ghi vào thư mục tạm (`Staging Write`):** Khi xuất bản snapshot mới có mã `snap_<id>`, ghi toàn bộ các file `.parquet` và `_snapshot_meta.json` vào thư mục tạm `data/published/snapshots/_tmp_snap_<id>/`.
2. **Kiểm tra toàn vẹn sau khi ghi:** Đảm bảo tất cả các file Parquet tồn tại và có số dòng $> 0$.
3. **Đổi tên Nguyên tử (`Atomic Directory Rename`):** Dùng `os.replace("_tmp_snap_<id>", "snap_<id>")` trên cùng một phân vùng đĩa, sau đó cập nhật cột `published_snapshot_id` trong bảng `pipeline_runs` của `state.sqlite` trong một transaction.
4. **Thu gom rác (`Snapshot Garbage Collection`):** Giữ lại đúng **$N = 2$ snapshots thành công gần nhất** (bản `active` hiện tại và 1 bản liền trước để phục vụ rollback tức thì hoặc tránh lỗi nếu một phiên đọc của Streamlit đang truy cập bản cũ). Tự động xóa toàn bộ các thư mục `_tmp_*` bị bỏ dở và các snapshot cũ từ thứ 3 trở đi.

---

## 5. TIÊU CHÍ NGHIỆM THU TẦNG 3 (DEFINITION OF DONE)

Agent phải viết và chạy xanh `tests/unit/test_layer3_storage.py`:
1. **Test Point-in-Time Query:** Giả lập một kỳ `2025-Q4` có bản tự lập công bố ngày `2026-01-25` (`revision_no = 1`, `Net Income = 100 tỷ`) và bản kiểm toán đính chính công bố ngày `2026-03-28` (`revision_no = 2`, `Net Income = 80 tỷ`):
   * Gọi `get_facts_as_of(date(2026, 2, 15))` phải trả về đúng `100 tỷ`.
   * Gọi `get_facts_as_of(date(2026, 4, 1))` phải trả về đúng `80 tỷ`.
2. **Test Concurrent Read While Publishing:** Trong khi một luồng đang đọc dữ liệu từ `active_snapshot` qua `repository.py`, luồng thứ hai xuất bản `snapshot` mới và chạy GC $\rightarrow$ Không xảy ra bất kỳ lỗi khóa file nào trên cả Linux và Windows.
3. **Test Garbage Collection:** Tạo liên tiếp 4 snapshots (`snap_1`, `snap_2`, `snap_3`, `snap_4`) $\rightarrow$ Sau khi `snap_4` kích hoạt, trên đĩa chỉ còn đúng `snap_3` và `snap_4`.