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
│       ├── ingestion/                        # [Tầng 1] Provider ABC, FhscOapiAdapter, FileAdapter, Raw Archive
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
    """Nguồn dữ liệu trả về HTTP 429 hoặc circuit breaker của nguồn đang mở.
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

Kiến trúc Option 3 chọn `vnstock-oapi==0.1.2` làm connector chính của Tầng 1. Windows Application Control chặn phần mở rộng `_core.pyd` của gói `vnstock==6.0.1`. Spike và connector FHSC chỉ sử dụng `vnstock_oapi` để lấy phản hồi JSON. Tầng 1 lưu payload nguồn và thông tin truy xuất. Tầng 2 ánh xạ mã FHSC sang mã VAS chuẩn, xác định chế độ kế toán và xử lý quy ước kỳ.

### 3.1. Bằng chứng cần thu thập

1. **Quyền truy cập, schema và phân trang.** Đọc ba loại BCTC quý và năm của FPT. Ghi các chữ ký Python, tham số endpoint, số trang, miền thời gian và toàn bộ tên trường, kiểu dữ liệu, trường thiếu và giá trị `null`. Xác nhận tối thiểu 16 quý, 4 năm và trang kết thúc. Ghi HTTP 401/403 nếu nguồn từ chối. Gói tài khoản và hạn khóa chỉ được ghi khi nguồn cung cấp bằng chứng. [Tài liệu endpoint FHSC](https://developers.fhsc.com.vn/api-reference/phan-tich-co-ban/bao-cao-tai-chinh)
2. **Phạm vi báo cáo và ngày công bố.** Kiểm tra trường và bộ lọc phạm vi. Đối chiếu số liệu với BCTC công bố độc lập và ghi kỳ nào có bằng chứng `CONSOLIDATED` hoặc `SEPARATE`. Kỳ chưa có bằng chứng giữ `UNKNOWN`. `year` và `quarter` là nhãn kỳ. Ngày công bố và trạng thái kiểm toán cần bằng chứng riêng.
3. **Quy ước quý và đẳng thức kế toán.** Đối chiếu riêng `Q1 + Q2 + Q3 + Q4` và `Q4` với số năm cho doanh thu thuần, lợi nhuận sau thuế và lưu chuyển tiền thuần từ hoạt động kinh doanh. Kiểm tra `Tổng tài sản − Nợ phải trả − Vốn chủ sở hữu` trên toàn bộ kỳ FPT theo ngưỡng 1 VND. Lưu mã gốc, số liệu đối chiếu và từng sai lệch.
4. **Benchmark.** Gọi 25 mã phi tài chính × 3 loại báo cáo × 2 kỳ ở trang đầu, giãn tối thiểu 2 giây giữa hai lượt bắt đầu. Ghi thời gian toàn lượt, p50/p95 mỗi lời gọi, mã HTTP ở cả lần gửi đầu và lần gọi lại, header giới hạn tần suất và thời gian chờ của SDK. Dừng khi ghi nhận 401/403/429 hoặc circuit breaker. Hạn mức tài khoản chỉ được kết luận khi có bằng chứng từ FHSC. [Tài liệu giới hạn tần suất FHSC](https://developers.fhsc.com.vn/rate-limits)

### 3.2. Chạy và duyệt Gate 0

```sh
uv run --locked --group spike python scripts/spike_vnstock6.py --prompt-key
```

Script nhận khóa FHSC qua môi trường cục bộ hoặc lời nhắc ẩn. `--reuse-history` dùng lại các trang đã khảo sát để chạy lại benchmark. `--history-dir` chỉ định thư mục đã lưu khi dùng lại lịch sử. `--history-only` dừng sau phần khảo sát và đối chiếu kế toán.

* Đọc `data/metadata/spike_report.json` và kiểm tra `git status --porcelain CLAUDE.md AGENTS.md` sau khi chạy. Thư mục `data/` giữ báo cáo và trang khảo sát trên máy.
* `ready_for_signoff` nghĩa là lịch sử và benchmark đã được thu thập để người phụ trách duyệt, kể cả các ngoại lệ dữ liệu. Bắt đầu Tầng 1 sau khi Gate 0 được duyệt.
* `FileAdapter` là nguồn dự phòng khi truy cập FHSC bị chặn hoặc một kỳ cần đối chiếu với BCTC gốc.

### 3.3. Phê duyệt Gate 0

Ngày 16/11/2026, người phụ trách phê duyệt Option 3 với `vnstock-oapi==0.1.2` làm connector FHSC. Lần khảo sát xác nhận 55 quý và 22 năm cho mỗi loại BCTC FPT, cùng 150/150 phản hồi HTTP 200 trong benchmark. Các sai lệch bảng cân đối FPT ở Q1–Q3/2015 và phạm vi báo cáo chưa xác minh vẫn cần kiểm tra trước khi sử dụng số liệu chuẩn hóa.
