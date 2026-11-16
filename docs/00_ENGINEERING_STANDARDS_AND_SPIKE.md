# CHUẨN KỸ THUẬT PHẦN MỀM DÙNG CHUNG & GIAO THỨC SPIKE CHẶNG 0

* **Mã tài liệu:** `DOC-00`
* **Phạm vi áp dụng:** Toàn bộ các tầng từ Tầng 0 đến Tầng 6 của dự án `finintel`.

---

## 1. CẤU TRÚC THƯ MỤC CHUẨN CỦA DỰ ÁN (REPOSITORY LAYOUT)

Agent bắt buộc phải tuân thủ đúng cây thư mục dưới đây. Không tự ý tạo thêm thư mục cấp cao (`microservices/`, `infra/`, `airflow/`, `mlflow/`) khi chưa được phê duyệt:

```text
financial-intelligence/
├── CLAUDE.md                                 # Hiến pháp kiểm soát AI Coding Agent
├── pyproject.toml                            # Quản lý dependencies ghim phiên bản & cấu hình ruff/mypy/pytest
├── Makefile                                  # Các lệnh chuẩn: make spike, make test, make lint, make run-mvp
├── config/
│   ├── settings.yaml                         # Tham số hệ thống, đường dẫn, ngưỡng Quality Gate, độ trễ luật định
│   ├── metric_definitions.yaml               # Định nghĩa chỉ tiêu chuẩn (STOCK/FLOW, đơn vị, comparable_across_taxonomy)
│   └── taxonomy/
│       ├── corporate_tt200_2014.yaml         # Bảng ánh xạ chính xác mã số & tên chỉ tiêu theo Thông tư 200
│       └── corporate_tt99_2025.yaml          # Bảng ánh xạ chính xác mã số & tên chỉ tiêu theo Thông tư 99 (từ 2026)
├── docs/
│   ├── 00_ENGINEERING_STANDARDS_AND_SPIKE.md # Tài liệu chuẩn kỹ thuật chung & Giao thức Spike Chặng 0
│   └── layers/                               # Đặc tả chi tiết từng tầng (Nạp từng file theo tiến độ)
│       ├── LAYER_0_ORCHESTRATION.md
│       ├── LAYER_1_INGESTION.md
│       ├── LAYER_2_NORMALIZATION.md
│       ├── LAYER_3_STORAGE.md
│       ├── LAYER_4_ANALYTICS_AND_ML.md
│       └── LAYER_5_6_SERVING_AND_QUALITY.md
├── scripts/
│   └── spike_vnstock6.py                     # Script khảo sát thực nghiệm nguồn dữ liệu ở Chặng 0
├── src/
│   └── finintel/
│       ├── __init__.py
│       ├── exceptions.py                     # Hệ thống phân loại Exception toàn cục
│       ├── domain/
│       │   └── schemas.py                    # Dataclasses/Enums định nghĩa Hợp đồng Dữ liệu giữa các tầng
│       ├── pipeline/                         # [Tầng 0] CLI, Runner, FileLock, Retry Policy, SQLite State Store
│       ├── ingestion/                        # [Tầng 1] Provider ABC, Vnstock6Adapter, FileAdapter, Raw Archive
│       ├── normalization/                    # [Tầng 2] Exact Mapper, TT200/TT99 Regime Resolver, Period & Q4 Deriver
│       ├── storage/                          # [Tầng 3] Snapshot Publisher, GC, Stateless DuckDB Query Repository
│       ├── analytics/                        # [Tầng 4A] Ratios Engine, DuPont 5-Step, Peer Benchmarking, Red Flags
│       └── quality/                          # [Tầng 6] Accounting Identity Rules, 3-Tier Gate, Quarantine Handler
├── dashboard/                                # [Tầng 5] Ứng dụng Streamlit 3 trang đọc Snapshot qua Repository
│   ├── app.py
│   ├── queries.py
│   ├── pages/
│   └── components/
├── tests/                                    # [Tầng 6] Bộ kiểm thử tự động & Golden Fixtures đối chứng
│   ├── fixtures/
│   │   ├── raw_samples/                      # Mẫu JSON/Excel thực tế từ BCTC gốc (10-20 doanh nghiệp)
│   │   └── golden_ratios/                    # Kết quả tỷ số đã tính kiểm chứng độc lập từ PDF BCTC gốc
│   ├── unit/
│   ├── integration/
│   └── reconciliation/
└── data/                                     # (Gitignored) Kho lưu trữ vật lý
    ├── raw/
    ├── curated/
    ├── published/
    │   └── snapshots/
    └── metadata/
        └── state.sqlite
```

*Lưu ý:* Thư mục `src/finintel/ml/` (Tầng 4B) **chưa được phép tạo trong giai đoạn MVP**. Chỉ khởi tạo khi toàn bộ Chặng 1 và Chặng 2 đã nghiệm thu xong.

---

## 2. CHUẨN KỸ THUẬT CODE PYTHON (CLEAN CODE & OPTIMIZATION STANDARDS)

### 2.1. Kiểu Dữ liệu, Typing và Docstring
* **Strict Type Hints:** Mọi hàm và phương thức phải có đầy đủ chú thích kiểu dữ liệu đầu vào và đầu ra (sử dụng cú pháp Python 3.11+ như `str | None`, `list[str]`, `Literal[...]`). Chạy `mypy --strict src/finintel` không được có bất kỳ lỗi nào.
* **Quy ước Số thực & Tiền tệ:**
  * Trong `Polars` và `Parquet`, toàn bộ giá trị tiền tệ trên BCTC sau khi chuẩn hóa về đơn vị gốc (**Đồng - VND**) được lưu ở kiểu `pl.Float64` (hoặc `pl.Decimal(38, 4)` nếu cần độ chính xác thập phân tuyệt đối) kèm cột `unit_multiplier` rõ ràng.
  * Mọi giá trị thiếu hoặc không xác định được biểu diễn thống nhất bằng `None` / `pl.Null` (tuyệt đối không trộn lẫn `np.nan`, chuỗi `"NaN"`, `"-"` hay `"N/A"` vào cột số).
* **Docstring Chuẩn Nghiệp vụ:** Mọi hàm tính toán kế toán hoặc tỷ số tài chính phải ghi rõ trong docstring: (1) Công thức toán học chính xác, (2) Mã chỉ tiêu đầu vào (`metric_id`), (3) Quy tắc xử lý khi mẫu số $\le 0$ hoặc thiếu kỳ.

### 2.2. Hệ thống Phân loại Ngoại lệ (`src/finintel/exceptions.py`)
Agent phải định nghĩa cây ngoại lệ rõ ràng ngay từ đầu để Tầng 0 xử lý đúng cơ chế retry và dừng an toàn:

```python
class FinIntelError(Exception):
    """Lớp cơ sở cho mọi ngoại lệ trong hệ thống."""

class TransientSourceError(FinIntelError):
    """Lỗi mạng tạm thời (Timeout, Connection reset, HTTP 502/503/504). Được phép retry ngắn."""

class RateLimitOrCircuitOpenError(FinIntelError):
    """Nguồn dữ liệu trả về HTTP 429 hoặc vnstock mở Circuit Breaker.
    TUYỆT ĐỐI KHÔNG retry dồn dập trong cùng một bước; phải dừng hoặc chờ cool-down dài."""

class SourceEntitlementError(FinIntelError):
    """Lỗi xác thực (401/403), khóa FHSC quá hạn 30 ngày, hoặc gói tài khoản không có quyền truy cập.
    Fail-fast ngay lập tức."""

class ContractViolationError(FinIntelError):
    """Dữ liệu đầu vào/đầu ra vi phạm schema bắt buộc hoặc sai cấu trúc hợp đồng. Fail-fast."""

class CriticalDataQualityError(FinIntelError):
    """Vi phạm đẳng thức kế toán nghiêm trọng hoặc tỷ lệ bản ghi lỗi vượt ngưỡng cho phép của Batch."""
```

### 2.3. Chuẩn Ghi Log (`Structured JSONL Logging`)
* Không dùng lệnh `print()` trong thư mục `src/finintel/`.
* Sử dụng module `logging` chuẩn với formatter xuất ra dòng JSON (`timestamp`, `level`, `run_id`, `stage`, `issuer_id`, `filing_id`, `event`, `details`) để dễ dàng truy vết khi chạy batch hàng trăm mã.

---

## 3. GIAO THỨC SPIKE CHẶNG 0 — KHẢO SÁT THỰC NGHIỆM TRƯỚC KHI VIẾT CODE TẦNG 1

Trước khi viết bất kỳ dòng code nào của `Vnstock6Adapter`, Agent phải viết và thực thi kịch bản khảo sát `scripts/spike_vnstock6.py` để xác minh thực tế hành vi của `vnstock==6.0.1` trên môi trường máy chủ hiện tại.

### 3.1. Năm Câu hỏi Kỹ thuật Bắt buộc Phải Trả lời Bằng Thực nghiệm
Kịch bản `scripts/spike_vnstock6.py` phải kiểm tra và xuất ra báo cáo `data/metadata/spike_report.json` trả lời 5 câu hỏi sau:

1. **Quyền truy cập & Khóa `fhsc` (Entitlement & Key TTL):**
   * Môi trường hiện tại đã có khóa API của nguồn `fhsc` chưa? Khóa thuộc gói nào (Community hay trả phí)?
   * Khi gọi `balance_sheet()`, `income_statement()`, `cash_flow()`, thư viện có phát cảnh báo `KeyRotationWarning` (khóa sắp hết hạn 30 ngày) hoặc lỗi giới hạn gói không?
2. **Cấu trúc Bảng & Metadata thực tế (`df.columns` và `df.attrs`):**
   * Tên chính xác của các cột trả về và các khóa bên trong `df.attrs` là gì? (Xác nhận xem thư viện dùng tên `unmapped_codes` hay `unmapped_keys`, có trường `retrieved_at`, `taxonomy_version` hay kết quả kiểm tra đẳng thức kế toán đi kèm không?).
3. **Phân biệt Báo cáo Hợp nhất vs. Riêng lẻ (`statement_scope`):**
   * API `vnstock` Gen 6 có tham số hoặc trường dữ liệu nào phân biệt Báo cáo Hợp nhất (`CONSOLIDATED`) và Báo cáo Công ty mẹ (`SEPARATE`) không? Nếu mặc định chỉ trả về một loại thì đó là loại nào?
4. **Quy ước Số liệu Quý trên Bảng KQKD & LCTT (`Flow Convention`):**
   * Số liệu các quý (`Q1, Q2, Q3, Q4`) trả về từ `income_statement()` và `cash_flow()` là **số phát sinh rời của riêng từng quý** hay **số lũy kế từ đầu năm (YTD)**? (Kiểm tra bằng cách lấy 1 công ty cụ thể như `FPT` hoặc `VNM`, đối chiếu trực tiếp con số Q2, Q3 từ API với file PDF BCTC gốc của doanh nghiệp đó).
5. **Giới hạn Tần suất & Hành vi Circuit Breaker (Stress Test vừa phải):**
   * Chạy thử nghiệm lấy 3 bảng BCTC cho 50–70 mã cổ phiếu liên tiếp (tương đương 150–210 lời gọi hàm) có gắn bộ đếm thời gian để đo tốc độ phản hồi, giới hạn request/phút và cách thư viện ném ngoại lệ khi chạm ngưỡng rate-limit.

### 3.2. Tiêu chí Nghiệm thu Chặng 0 (Gate 0)
* Sau khi chạy `scripts/spike_vnstock6.py`, Agent phải kiểm tra `git status --porcelain CLAUDE.md AGENTS.md` để đảm bảo thư viện không tự ý sửa file cấu hình.
* Nếu nguồn `vnstock` Gen 6 không cấp quyền truy cập hoặc thiếu thông tin trọng yếu, Agent phải báo cáo ngay kết quả trong `spike_report.json` và chuyển sang sử dụng `FileAdapter` (nạp từ bộ mẫu Excel/JSON/Parquet chuẩn trong `tests/fixtures/raw_samples/`) làm nguồn chính để phát triển thông suốt các tầng 2, 3, 4A, 5, 6 mà không bị chặn tiến độ.