# ĐẶC TẢ XÂY DỰNG TẦNG 0: ORCHESTRATION & OPERATIONAL STATE

* **Mã tài liệu:** `LAYER-0`
* **Module đích:** `src/finintel/pipeline/`
* **Phụ thuộc đầu vào:** Cấu hình CLI, `config/settings.yaml`, trạng thái trong `data/metadata/state.sqlite`.

---

## 1. BÀI TOÁN & PHẠM VI TRÁCH NHIỆM

Tầng 0 chịu trách nhiệm điều phối toàn bộ vòng đời của một lần chạy pipeline (`Ingest -> Normalize -> Quality Gate 1 -> Compute Ratios & Warnings -> Quality Gate 2 -> Publish Snapshot`), đảm bảo:
1. **Không chạy chồng chéo (Single-Run Concurrency Control):** Ngăn chặn hai tiến trình ETL cùng ghi vào kho dữ liệu tại một thời điểm.
2. **Idempotency & Chạy lại theo phạm vi hẹp:** Cho phép chạy lại riêng một mã (`--ticker FPT`), một kỳ (`--period 2025-Q4`) hoặc một bước (`--stage normalize`) mà không nhân đôi dữ liệu hay làm hỏng các mã khác.
3. **Kiểm tra sức khỏe nguồn & Tuổi khóa (`Health & Key TTL Check`):** Kiểm tra hạn khóa `fhsc` (30 ngày) và hạn phiên bản thư viện trước khi bắt đầu batch lớn.
4. **Phân loại lỗi để Retry thông minh:** Tương thích với cơ chế Circuit Breaker nội bộ của nguồn dữ liệu, không retry mù quáng gây khóa IP.

*Ranh giới cấm:* Tầng 0 **không được** chứa bất kỳ câu lệnh biến đổi bảng `Polars`/`Pandas` hay công thức tính toán tài chính nào.

---

## 2. NGUỒN HỌC ĐÃ KIỂM CHỨNG & CÁCH TÁI SỬ DỤNG

1. **Tài liệu chính thức `tenacity` (Python Retry Library):**
   * *Cách áp dụng:* Sử dụng `retry_if_exception_type(TransientSourceError)` kết hợp `wait_random_exponential(multiplier=2, min=4, max=60)` và `stop_after_attempt(3)`.
   * *Tuyệt đối tránh:* Không gắn retry tự động ngắn hạn lên `RateLimitOrCircuitOpenError`, `SourceEntitlementError` hoặc `ContractViolationError`.
2. **Tài liệu chính thức SQLite Write-Ahead Logging (`PRAGMA journal_mode=WAL;`):**
   * *Cách áp dụng:* Bật chế độ `WAL` và `PRAGMA busy_timeout=5000;` ngay khi mở kết nối tới `data/metadata/state.sqlite` để ghi nhật ký trạng thái an toàn, nhẹ nhàng mà không cần cài đặt PostgreSQL hay tạo file DuckDB phụ (`pipeline_runs.duckdb`).

---

## 3. KIẾN TRÚC MODULE & HỢP ĐỒNG DỮ LIỆU (`state.sqlite`)

### 3.1. Cấu trúc File trong `src/finintel/pipeline/`
```text
src/finintel/pipeline/
├── __init__.py
├── cli.py           # Giao diện dòng lệnh (Typer/Argparse)
├── runner.py        # Bộ điều phối tuần tự các stage, quản lý FileLock & Circuit Breaker
├── retry_policy.py  # Định nghĩa decorator retry phân loại theo Exception
├── health.py        # Kiểm tra dung lượng đĩa, tuổi khóa API (30 ngày) và kết nối nguồn
└── state_store.py   # Lớp truy cập SQLite quản lý pipeline_runs, pipeline_steps, active_snapshot
```

### 3.2. Schema Cơ sở dữ liệu Trạng thái (`data/metadata/state.sqlite`)
Agent phải khởi tạo các bảng sau trong `state_store.py` bằng câu lệnh `CREATE TABLE IF NOT EXISTS`:

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,               -- UUIDv4 hoặc YYYYMMDD_HHMMSS_<short_hash>
    started_at TEXT NOT NULL,              -- ISO-8601 UTC timestamp
    finished_at TEXT,                      -- ISO-8601 UTC timestamp
    status TEXT NOT NULL,                  -- 'RUNNING' | 'SUCCEEDED' | 'PARTIAL_SUCCESS' | 'FAILED' | 'ABORTED_CIRCUIT_OPEN'
    trigger_mode TEXT NOT NULL,            -- 'CLI_MANUAL' | 'SCHEDULED_CRON' | 'BACKFILL'
    cli_args_json TEXT NOT NULL,
    git_commit_sha TEXT,
    published_snapshot_id TEXT             -- Khóa ngoại trỏ tới snapshot được xuất bản (nếu có)
);

CREATE TABLE IF NOT EXISTS pipeline_steps (
    step_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    stage_name TEXT NOT NULL,              -- 'INGEST' | 'NORMALIZE' | 'QUALITY_GATE_1' | 'ANALYTICS' | 'QUALITY_GATE_2' | 'PUBLISH'
    issuer_id TEXT,                        -- NULL nếu là bước chạy trên toàn batch
    filing_id TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,                  -- 'RUNNING' | 'SUCCEEDED' | 'QUARANTINED' | 'SKIPPED_UNCHANGED' | 'FAILED'
    attempt_count INTEGER NOT NULL DEFAULT 1,
    error_type TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS api_key_registry (
    provider_name TEXT PRIMARY KEY,        -- Ví dụ: 'vnstock_fhsc'
    key_fingerprint TEXT NOT NULL,         -- 8 ký tự băm SHA-256 cuối của key (CẤM lưu plaintext API key vào DB!)
    provisioned_at TEXT NOT NULL,          -- Ngày cấp khóa (ISO-8601)
    expires_at TEXT NOT NULL,              -- Ngày hết hạn (provisioned_at + 30 ngày với fhsc)
    package_version TEXT NOT NULL          -- Ví dụ: 'vnstock==6.0.1'
);
```

---

## 4. QUY TẮC XỬ LÝ NGHIỆP VỤ ĐIỀU PHỐI

1. **Cơ chế Khóa Tiến trình (`Single-Run Lock`):**
   * Khi `runner.py` khởi chạy, phải chiếm khóa file tại `data/metadata/pipeline.lock` bằng thư viện `filelock` với `timeout=5`. Nếu không lấy được khóa, ghi log cảnh báo `Another pipeline run is currently active` và thoát với mã `exit(0)` (không làm hỏng tiến trình đang chạy).
2. **Kiểm tra Tuổi khóa trước khi chạy (`health.py`):**
   * Kiểm tra bảng `api_key_registry` hoặc biến môi trường `VNSTOCK_FHSC_KEY_CREATED_AT`.
   * Nếu thời gian còn lại trước khi khóa hết hạn 30 ngày nhỏ hơn `5 ngày`, phát cảnh báo `WARNING: API Key will expire in X days`. Nếu đã quá hạn ($\le 0$ ngày), ném `SourceEntitlementError` ngay lập tức trước khi chạy bước `INGEST` trực tuyến (trừ khi người dùng chạy chế độ `--source file`离线).
3. **Ứng xử khi gặp `RateLimitOrCircuitOpenError`:**
   * Nếu nguồn dữ liệu ném lỗi giới hạn tần suất hoặc mở Circuit Breaker giữa chừng khi đang tải danh sách nhiều mã cổ phiếu:
     * **Dừng ngay vòng lặp gọi API mới** (không thử gọi tiếp mã cổ phiếu kế tiếp vì sẽ bị chặn tiếp).
     * Lưu toàn bộ các báo cáo đã tải thành công trước thời điểm ngắt vào `data/raw/`.
     * Đánh dấu trạng thái của `pipeline_runs` là `'ABORTED_CIRCUIT_OPEN'` hoặc tiếp tục xử lý phần dữ liệu đã có nếu có cờ `--continue-on-partial-ingest`.

---

## 5. TIÊU CHÍ NGHIỆM THU TẦNG 0 (DEFINITION OF DONE)

Agent phải viết và chạy xanh toàn bộ file `tests/unit/test_layer0_orchestration.py` kiểm chứng các kịch bản sau:
1. **Test Concurrency Lock:** Giả lập 2 tiến trình gọi `runner.run()` cùng lúc; tiến trình thứ hai phải phát hiện khóa và từ chối chạy an toàn.
2. **Test Retry Classification:**
   * Hàm ném `TransientSourceError` 2 lần rồi thành công ở lần thứ 3 $\rightarrow$ `attempt_count == 3`, `status == 'SUCCEEDED'`.
   * Hàm ném `SourceEntitlementError` hoặc `RateLimitOrCircuitOpenError` $\rightarrow$ dừng ngay ở `attempt_count == 1`, không retry.
3. **Test Key Expiration Check:** Khi `expires_at` nhỏ hơn thời điểm hiện tại, `health_check()` phải chặn luồng gọi API trực tuyến và báo rõ thông điệp yêu cầu xoay vòng khóa (rotate key).