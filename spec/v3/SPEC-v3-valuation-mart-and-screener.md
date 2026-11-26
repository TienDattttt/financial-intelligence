# Spec: `v3-valuation-mart-and-screener` — PIT Valuation Mart & Full Screener — V3.0

> **Module ID:** `v3-valuation-mart-and-screener`  
> **Wave / Build Order:** Phase 1 — **Wave 1A** (chạy song song với `v3-compliance-and-mrm`, `v3-infra-ha-storage`)  
> **Depends On:** `—`  
> **Consumers:** `v3-export-and-tearsheet`, `v3-analyst-depth-and-ops-qa`, `v3-fx-stress-and-bond-signals`  
> **MoSCoW / Audit Refs:** `#18` (`ocf_to_total_liabilities`), `#27` (cột `EPS`, `Debt Ratio`, `Cash Flow` trên bảng xếp hạng), `#38` (lọc đa tiêu chí tỷ số tài chính + định giá + sàn + cụm + ngoại lai).

---

## 1. Objective

Lấp khoảng trống cạnh tranh lớn nhất của màn hình Screener so với FiinPro-X, WiChart, Simplize và Koyfin:
1. **Materialize bảng `pit_valuation_mart` trong DuckLake:** Hiện tại `market_cap_vnd`, `pe_ttm`, `pb_ratio` chỉ tính on-demand cho 1 mã (`src/finintel/analytics/market_valuation.py:331-351`). Cần xây dựng bảng mart vật lý chuẩn PIT (dùng `close_raw` + `join_asof(..., strategy="forward")`, tuyệt đối không dùng `close_adj`) và đăng ký vào `MANAGED_MART_TABLES` + Dagster asset.
2. **Bổ sung tỷ số dòng tiền còn thiếu (`#18`):** Thêm `ocf_to_total_liabilities` (`cfo_ttm / BS_TOTAL_LIABILITIES`, trả `NULL` + flag khi mẫu số $\le 0$) trong Layer 4A `ratios.py` và từ điển `config/metric_definitions.yaml`.
3. **Nâng cấp Screener API & UI (`#27`, `#38`):**
   - Mở rộng `ScreenerItem` và `/api/v1/screener` (cũng như `/api/v1/screener/export`) hỗ trợ trả về và lọc theo dải: `ROE`, `ROA`, `EPS`, `Debt Ratio (liabilities_to_assets)`, `D/E`, `CFO (cfo_ttm)`, `FCF`, `OCF/Tổng nợ`, `Tăng trưởng DT YoY`, `P/E (TTM)`, `P/B`, `Vốn hóa (tỷ VND)`, cùng bộ lọc rời rạc `exchange` (`HOSE/HNX/UPCOM`), `cluster_id`, và `anomaly_flag`.
   - Khắc phục sẵn lỗi type `ScreenerSortField` trên `frontend/src/app/screener/page.tsx` khi bổ sung trường sort mới và đồng bộ `gen:api-types`.

---

## 2. Tech Stack

- **Backend (`Python 3.11`):** `polars==1.35.2`, `duckdb==1.4.2`, `fastapi==0.165.0`, `pydantic==2.14.4`, `dagster==1.13.30` (lazy-import trong core). Không thêm thư viện mới.
- **Frontend (`Node 24+`):** `next==16.4.6`, `react==19.2.4`, `@tanstack/react-query==^5.90.0`, `@tanstack/react-table`, `@base-ui/react==^1.3.0`, `zod==^4.1.0`.

---

## 3. Commands

```powershell
# Backend
.\.venv\Scripts\python.exe scripts/check_constraints.py
.\.venv\Scripts\python.exe -m ruff format --check src scripts tests
.\.venv\Scripts\python.exe -m ruff check src scripts tests
.\.venv\Scripts\python.exe -m mypy --strict src/finintel/analytics/valuation_mart.py src/finintel/serving/queries/screener.py tests/unit/test_v3_valuation_mart_screener.py
.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_valuation_mart_screener.py tests/unit/test_layer4_analytics.py tests/unit/test_layer5_fastapi_serving.py tests/unit/test_serving_latency_budget.py -q

# Regenerate FE API types & verify FE
fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types
fnm exec --using=default -- npm.cmd --prefix frontend run check
fnm exec --using=default -- npm.cmd --prefix frontend run test:unit
```

---

## 4. Project Structure & File Ownership

| Path | Action | Responsibility |
|---|---|---|
| `src/finintel/analytics/valuation_mart.py` | Create (`<= 350` LOC) | Vectorized Polars builder tính `pit_valuation_mart` toàn thị trường từ `ratios` + `market_prices` (`close_raw`, `join_asof forward`) |
| `src/finintel/analytics/ratios.py` | Modify (minimal) | Thêm `ocf_to_total_liabilities` + flag xử lý mẫu số $\le 0$ |
| `config/metric_definitions.yaml` | Modify | Khai báo metadata tiếng Việt cho `ocf_to_total_liabilities` |
| `src/finintel/storage/schemas.py` | Modify | Khai báo `PIT_VALUATION_MART_TABLE = "pit_valuation_mart"` và `PIT_VALUATION_MART_SCHEMA` trong `MANAGED_MART_TABLES` |
| `src/finintel/serving/queries/screener.py` | Modify (`< 1,000` LOC) | Join `pit_valuation_mart` + hỗ trợ filter dải tỷ số/định giá/sàn/cụm/ngoại lai + projection cột mới |
| `src/finintel/serving/routers/screener_v3.py` | Create (`<= 300` LOC) | Router mới hoặc mở rộng `ScreenerFilterParams` dependency (không tăng ròng `> +10` LOC vào `src/finintel/serving/api.py`) |
| `frontend/src/app/screener/page.tsx` & `frontend/src/components/screener/` | Modify / Create | Bộ lọc đa tiêu chí (nhóm Sinh lời / Đòn bẩy / Dòng tiền / Định giá / Sàn / Cụm / Rủi ro) + cột bảng `EPS`, `Debt Ratio`, `CFO`, `P/E`, `P/B`, `Vốn hóa` |
| `tests/unit/test_v3_valuation_mart_screener.py` | Create | Test PIT `close_raw`, zero look-ahead, filter combination, latency `p95 <= 200ms` lạnh / `<= 50ms` ấm |

---

## 5. Boundary Interfaces & Contracts

### 5.1. `pit_valuation_mart` Schema (DuckLake Managed Table)
| Column | Polars Type | Rule |
|---|---|---|
| `issuer_id`, `ticker`, `statement_scope`, `period_type`, `period_end_date` | `String / Date` | Khóa chính khớp `ratios` |
| `effective_trade_date` | `Date` | Khớp bằng `join_asof(..., strategy="forward")` từ `effective_available_at` |
| `close_raw_vnd` | `Float64` | Giá đóng cửa **chưa điều chỉnh** (`close_raw`); cấm dùng `close_adj` |
| `pit_shares_outstanding` | `Float64` | Ưu tiên thị trường PIT / BCTC có loại cổ phiếu quỹ |
| `market_cap_vnd` | `Float64` | `close_raw_vnd * pit_shares_outstanding` (`NULL` nếu thiếu) |
| `pe_ttm`, `pb_ratio` | `Float64` | `NULL` + `pe_flag`/`pb_flag` nếu `EPS <= 0` hoặc `parent_equity <= 0` |
| `pe_flag`, `pb_flag`, `market_cap_flag` | `String` | `OK \| NEGATIVE_DENOMINATOR \| ZERO_DENOMINATOR \| MISSING_PRICE \| MISSING_SHARES` |

### 5.2. Extended Query Params (`GET /api/v1/screener` & `/api/v1/screener/export`)
- **Backward-compatible 100%:** Toàn bộ params cũ giữ nguyên; thêm các params optional:
  - `exchange: Literal["HOSE", "HNX", "UPCOM"] | None`
  - `cluster_id: int | None`, `anomaly_flag: bool | None`
  - Range filters (`min_*` / `max_*`): `roe_parent`, `roa`, `eps_ttm_estimated`, `liabilities_to_assets`, `debt_to_equity`, `cfo_ttm`, `ocf_to_total_liabilities`, `revenue_growth_yoy`, `pe_ttm`, `pb_ratio`, `market_cap_bn_vnd`
- **Extended `ScreenerItem`:** Bổ sung `eps_ttm_estimated`, `liabilities_to_assets`, `cfo_ttm`, `fcf_ttm`, `ocf_to_total_liabilities`, `market_cap_vnd`, `pe_ttm`, `pb_ratio`, `pe_flag`, `pb_flag`.

---

## 6. Code Style & Architectural Invariants

- **Cấm tính công thức tài chính trong `serving/` hay `frontend/`.** Mọi phép tính `P/E`, `P/B`, `Market Cap`, `ocf_to_total_liabilities` thực hiện 100% bằng Polars vectorized trong `src/finintel/analytics/`.
- **Cấm `.fill_null(0)`** trên bất kỳ chỉ tiêu tài chính/định giá nào; không dùng `iter_rows(`.
- **An toàn `DuckLakeStore.publish_snapshot()` (`B-03`):** Đăng ký `pit_valuation_mart` vào `MANAGED_MART_TABLES`, tuyệt đối không đụng chạm `regulatory_events` hoặc `market_prices`.
- **Giữ kích thước file:** `src/finintel/serving/queries/screener.py` bắt buộc `< 1.000` LOC (tách helper `_screener_filters.py` nếu gần ngưỡng).

---

## 7. Testing Strategy

- **Unit & PIT Tests (`pytest`):**
  - Chứng minh dùng `close_raw` (không dùng `close_adj`) trên fixture cổ phiếu chia tách 2:1.
  - Chứng minh công bố BCTC vào Thứ Bảy/Chủ Nhật join sang phiên giao dịch Thứ Hai (`strategy="forward"`, 0% look-ahead).
  - Kiểm tra `EPS <= 0` → `pe_ttm = None, pe_flag = "NEGATIVE_DENOMINATOR"`.
  - Kiểm tra mọi tổ hợp filter + sort + CSV export trả đúng tập kết quả, không rò rỉ SQL injection (parameterized DuckDB queries).
- **Latency Gate:** Xác nhận `test_serving_latency_budget.py` giữ `p95 <= 50 ms` (Valkey ấm) và `<= 200 ms` (DuckLake lạnh).
- **Frontend Tests:** Vitest cho bộ lọc mới + URL sync + Saved Screens tương thích ngược + Playwright E2E không tràn ngang ở `375px` & `1366px`.

---

## 8. Boundaries

- **Always:** Dùng `placeholderData: keepPreviousData` cho `useScreener`; đồng bộ `ValkeyResponseCache` cache key đủ mọi tham số lọc mới.
- **Ask first:** Trước khi thay đổi khóa chính của `ratios` hoặc xóa cột cũ khỏi `ScreenerItem`.
- **Never:** Không ghi trực tiếp vào `data/live_fhsc_universe/` hay `data/universe_600_800/`; không tăng `api.py` quá `+10` LOC; không lọc theo `close_adj`.

---

## 9. Success Criteria

- [ ] `pit_valuation_mart` materialize thành công trong pipeline publish và tồn tại trong DuckLake snapshot trên `tmp_path` test fixture.
- [ ] `GET /api/v1/screener` hỗ trợ đầy đủ 14 nhóm lọc mới (tỷ số, định giá, sàn, cụm, ngoại lai) và `GET /api/v1/screener/export` xuất đủ các cột mới.
- [ ] `ocf_to_total_liabilities` có mặt trong `ratios`, `metric_definitions.yaml`, `/api/v2/meta/dictionary` và `SourceDrawer`.
- [ ] Độ trễ `p95` Screener giữ đúng ngưỡng `CONSTRAINTS.md`; `0` lỗi `mypy --strict`, `ruff`, `check_constraints.py`, `npm run check`.

---

## 10. Git Worktree & Commit Contract

```powershell
git worktree add ../finintel-v3-valuation-mart-and-screener -b feat/v3-valuation-mart-and-screener main
```
- Tách atomic commits:
  1. `feat(analytics): add ocf_to_total_liabilities and vectorized pit_valuation_mart builder`
  2. `feat(storage,orchestration): register pit_valuation_mart in DuckLake snapshot and Dagster publish`
  3. `feat(serving): extend screener filters and valuation projections with Valkey cache keys`
  4. `feat(frontend): add multi-criteria financial/valuation screener filters and columns`

---

## 11. Out of Scope (Deferred)

- Trình tự tạo công thức tùy biến (`#52` Custom Ratio Formula Builder — Deferred to V4).
- Biểu đồ phân tán 2D/3D PCA trong Screener (`#31` — Not Doing).

## 12. Open Questions

- Xác nhận đơn vị nhập bộ lọc `Vốn hóa` trên UI Screener là **tỷ VND** (khớp với chuẩn hiển thị BCTC toàn hệ thống).
