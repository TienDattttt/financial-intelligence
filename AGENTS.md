# AGENTS.md — FinIntel Root Engineering & AI Coding Rules

> Luật làm việc thống nhất cho mọi AI coding agent và lập trình viên trên toàn repository `financial-intelligence`.  
> **Bắt buộc đọc trước khi sửa code:**
> - Lộ trình refactor đã khóa (`v1.1`): [`docs/refactor/00_MASTER_EXECUTION_ROADMAP.md`](./docs/refactor/00_MASTER_EXECUTION_ROADMAP.md) và file kế hoạch tương ứng (`01_*`, `02_*`, `03_*`).
> - Nếu chạm vào `frontend/`: **BẮT BUỘC** đọc thêm [`frontend/AGENTS.md`](./frontend/AGENTS.md), [`frontend/PRODUCT.md`](./frontend/PRODUCT.md) và [`frontend/DESIGN.md`](./frontend/DESIGN.md).

---

## 1. Phạm Vi & Kiến Trúc Hệ Thống

- **Phạm vi nghiệp vụ:** Doanh nghiệp **phi tài chính (`CORPORATE`)** niêm yết/đăng ký giao dịch tại Việt Nam (`HOSE`, `HNX`, `UPCOM`), chuẩn kế toán VAS `TT200/2014 → TT99/2025`. Không mở rộng sang Ngân hàng / Chứng khoán / Bảo hiểm.
- **Kiến trúc chuẩn:** Single-Node Modern Lakehouse (`Polars` + `DuckDB/DuckLake` + `Parquet` + `Dagster`) kết hợp Tier-1 Platform (`PostgreSQL 16 / pgvector` + `Valkey 8` + `FastAPI` + `Next.js 16`).
- **Ranh giới các tầng (KHÔNG vi phạm chiều phụ thuộc):**
  - `src/finintel/ingestion/` *(Layer 1)*: Thu thập, lưu Raw CAS bất biến (`SHA-256`), enrich ngày công bố thực (PIT TT96), SSRF allowlist, rate-limit `0.6 req/s`.
  - `src/finintel/normalization/` *(Layer 2)*: Ánh xạ chuẩn mực VAS (`TT200`/`TT99`), tách quý rời rạc & TTM, xử lý điều chỉnh hồi tố.
  - `src/finintel/storage/` *(Layer 3)*: DuckLake ACID snapshots (`MANAGED_MART_TABLES` tách khỏi bảng Extension Ports), `ParquetRepository` đọc-chỉ.
  - `src/finintel/analytics/` & `src/finintel/ml/` *(Layer 4A/4B)*: Toàn bộ tính toán tài chính, 10 cờ đỏ VAS (`RF_01..RF_10`), Merton DTD, Stress Test IMF WP/15/216 và mô hình ML. **Cấm tính toán công thức tài chính trong tầng Serving, UI hay CLI.**
  - `src/finintel/transform/` & `src/finintel/orchestration/`: Động cơ gia tăng DBSP và điều phối Dagster.
  - `src/finintel/serving/` *(Layer 5)*: FastAPI đọc-chỉ (`serving/queries/` chia gói domain `< 1.000` LOC) + DuckDB cursor per-thread + Valkey L2 cache + Observability.
  - `src/finintel/quality/` *(Layer 6)*: Cổng chất lượng QG1/QG2 và cách ly (`audit_quarantine`).
  - `frontend/`: Giao diện sản phẩm duy nhất (Next.js 16).

---

## 2. Ràng Buộc Bất Di Bất Dịch (Zero-Mock & Anti-"Pass Ảo")

### 2.1. Khoa học dữ liệu, Kế toán & ML
1. **100% Dữ liệu thật — Cấm tuyệt đối Mock/Synthetic để ép pass:** Không dùng SMOTE/dữ liệu tổng hợp cho bảng BCTC, không bịa sự kiện pháp lý, không hardcode backdate metadata (như `industry_snapshot_date = 2016-01-01`).
2. **Bảo vệ kho dữ liệu gốc:** Tuyệt đối **KHÔNG** ghi, sửa, xóa trực tiếp vào `data/live_fhsc_universe/`. Mọi thử nghiệm/test chạy trên `tmp_path` hoặc bản sao độc lập.
3. **Kỷ luật kế toán VAS:** Không `.fill_null(0)` mù quáng lên chỉ tiêu tài chính; không chia cho mẫu số $\le 0$; không trộn `CONSOLIDATED` với `SEPARATE`; không tính TTM/YoY vượt ranh giới không tương thích `TT200 → TT99`.
4. **Chống rò rỉ thời gian (PIT & ML):**
   - Trong Mode B (`REGULATORY_DISTRESS`): bắt buộc **Symmetric Horizon Completeness** (`a_t + 365d <= cutoff` đối xứng cho cả `y=1` và `y=0`), theo dõi liên tục khoảng hiệu lực trạng thái kiểm soát `(effective_from, resolution_date)`, không dùng thông tin sau cutoff.
   - Trong Mode A (Ablation): `0%` chồng lấn cửa sổ TTM giữa feature `X_t` và nhãn tương lai.
   - Khi so sánh 3 mô hình (`Altman Z''`, `Logit/Shumway`, `XGBoost`): phải dùng cùng tập giao mẫu và hiệu chuẩn công bằng (Platt OOF không phạt L2 làm phẳng xác suất).
   - Khi tính định giá từ dữ liệu giá EOD (Port 3): tách bạch **`close_raw`** (tính `Vốn hóa / P/E / P/B` chuẩn PIT) và **`close_adj`** (vẽ đường giá & `% drawdown`), join bằng `join_asof(..., strategy="forward")`.

### 2.2. Kỹ thuật phần mềm, CI & Giao diện
1. **Tách bạch 2 loại PR/Commit:** Tuyệt đối **KHÔNG trộn** *Behavior-Preserving* (đổi cấu trúc, tối ưu, dọn dead code) với *Behavior-Changing* (đổi logic, schema, hạ tầng, tính năng) trong cùng một commit/PR.
2. **An toàn Dependency & CI:** Ghim phiên bản cứng (`==`) trong `pyproject.toml` + cập nhật `uv.lock`. Mọi thư viện thuộc group `platform` (`dagster`, `psycopg`, `redis`, `opentelemetry`) phải dùng **lazy-import** ở các module core để không làm gãy CI `mvp-checks` (`--group ml --dev`).
3. **An toàn DuckLake & Storage:** Không để `publish_snapshot()` chạy `DROP TABLE` lên bảng phụ trợ của Extension Ports (`regulatory_events`, `market_prices`) hay bảng bất biến.
4. **Giao diện chuẩn FinTech, không "lậm AI":** Không lộ token kỹ thuật (`REGIME_FALLBACK`, `BS_TOTAL_ASSETS`, `issuer_id`, `.parquet`, traceback) ra UI; tuân thủ danh sách CẤM trong `frontend/DESIGN.md` (cấm gradient tím-xanh, glassmorphism, ✨/"AI-Powered", emoji, marketing giả).

---

## 3. Bộ Lệnh Kiểm Chứng Chuẩn (Bắt Buộc PASS Trước Khi Xong Mỗi Task)

### Backend (Python 3.11 / `.venv`)
```powershell
.\.venv\Scripts\python.exe scripts/check_constraints.py --selftest
.\.venv\Scripts\python.exe scripts/check_constraints.py
.\.venv\Scripts\python.exe -m ruff format --check src scripts tests
.\.venv\Scripts\python.exe -m ruff check src scripts tests
.\.venv\Scripts\python.exe -m mypy --strict src/finintel scripts/spike_vnstock6.py scripts/spike_option_b.py tests/unit
.\.venv\Scripts\python.exe -m pytest -q
```

### Frontend (`frontend/` — Node 24+ qua `fnm`)
```powershell
fnm exec --using=default -- npm.cmd --prefix frontend run check
fnm exec --using=default -- npm.cmd --prefix frontend run test:unit
```
*(Nếu `npm` đã sẵn trong `PATH`, có thể chạy trực tiếp `npm --prefix frontend run check && npm --prefix frontend run test:unit`. Bắt buộc chạy thêm `npm run test:e2e` khi thay đổi API contract `/api/*` hoặc giao diện người dùng.)*

---

## 4. Quy Trình Thực Thi Từng Atomic Task

1. Đọc đúng mục Task trong `docs/refactor/` (`Allowed Scope`, `Non-Goals`, `Acceptance Criteria`).
2. Kiểm tra call-sites thực tế trong mã nguồn trước khi sửa — không đoán từ trí nhớ.
3. Nếu là task lớn hoặc đổi hành vi: trình bày ngắn gọn kế hoạch sửa file trước khi ghi code.
4. Thực hiện diff tối giản đúng phạm vi; **không** sửa lan sang file/module ngoài scope.
5. Chạy đủ bộ lệnh kiểm chứng ở Mục 3 và báo cáo kết quả đo thực tế (0 mock, 0 bỏ qua test fail).
