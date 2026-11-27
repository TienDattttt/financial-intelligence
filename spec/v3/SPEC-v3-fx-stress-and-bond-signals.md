# Spec: `v3-fx-stress-and-bond-signals` — Stress Test Tỷ Giá FX & Tín Hiệu Trái Phiếu Doanh Nghiệp — V3.1

> **Module ID:** `v3-fx-stress-and-bond-signals`  
> **Wave / Build Order:** Phase 3 — **Wave 3** (chạy song song với `v3-b2b-commercial-api`, `v3-private-issuer-and-hitl`)  
> **Depends On:** `v3-compliance-and-mrm`, `v3-valuation-mart-and-screener`  
> **Consumers:** Trang `/stress-test`, Hồ sơ doanh nghiệp `/companies/[ticker]`, Cảnh báo sớm (`v3-early-warning-alerts`)  
> **MoSCoW / Audit Refs:** Nâng cấp chuẩn cấu trúc thay thế cho `#58` What-If phi thực tế + Khoảng trống đặc thù thị trường tín dụng VN (rủi ro tỷ giá USD/VND & đáo hạn trái phiếu doanh nghiệp).

---

## 1. Objective

Bổ sung 2 tín hiệu rủi ro thực chiến có sức nặng nhất đối với doanh nghiệp phi tài chính Việt Nam hiện nay (nơi đòn bẩy trái phiếu và nợ ngoại tệ là nguyên nhân trực tiếp dẫn tới mất thanh khoản trước khi BCTC quý phản ánh hết):
1. **Mở rộng Stress Test Cấu trúc IMF WP/15/216 thêm Cú sốc Tỷ giá FX (`src/finintel/analytics/stress_fx.py`):**
   - Bổ sung tham số tùy chọn `fx_depreciation_pct` (vd: `+3%`, `+5%`, `+10%` VND mất giá so với USD) vào `StressScenario` (giữ mặc định `0.0` để tương thích ngược 100%).
   - Chỉ kích hoạt lỗ chênh lệch tỷ giá (FX loss) đối với doanh nghiệp có **dữ liệu nợ vay ngoại tệ ròng đã trích xuất từ thuyết minh / bảng phụ trợ (`net_fx_debt_vnd`)** — nếu chưa có dữ liệu nợ ngoại tệ thì giữ `fx_impact = NULL` và hiển thị trung thực *"Chưa có dữ liệu phân tách nợ ngoại tệ"* (tuyệt đối không giả định bịa tỷ lệ nợ USD!).
   - Tính toán tất định: `Stressed EBIT`, `Stressed Borrowing Cost + FX Revaluation Impact`, `Stressed ICR` và `Debt-at-Risk` hoàn toàn bảo toàn đẳng thức kế toán.
2. **Extension Port 4 — Tín hiệu Trái phiếu Doanh nghiệp & Giao dịch Nội bộ Lớn (`src/finintel/ports/bonds.py`):**
   - Xây dựng hợp đồng bảng mở rộng mới `corporate_bond_signals` (nằm ngoài `MANAGED_MART_TABLES` để không bị `publish_snapshot()` xóa nhầm theo nguyên tắc `B-03`), lưu dữ liệu thật có `source_url` & `payload_sha256` từ nguồn công bố thông tin trái phiếu (HNX Cbonds / nguồn thương mại): tổng dư nợ trái phiếu lưu hành, giá trị đáo hạn trong 12 tháng tới (`bond_maturity_12m_vnd`), tỷ lệ `bond_maturity_12m / total_cash_and_st_investments`, và lịch sử chậm trả gốc/lãi (`bond_payment_delay_flag`).
   - Hiển thị thẻ **"Áp lực đáo hạn trái phiếu 12 tháng"** trên `OverviewTab` / `QualityTab` khi doanh nghiệp có dư nợ trái phiếu thật.

---

## 2. Tech Stack

- **Backend (`Python 3.11`):** `polars==1.35.2`, `duckdb==1.4.2`, `fastapi==0.165.0`, `pydantic==2.14.4`, `httpx==0.28.1` (qua `ssrf_guard`), `dagster==1.13.30`. Không thêm thư viện mới.
- **Frontend (`Node 24+`):** `next==16.4.6`, `react==19.2.4`, `echarts==^6.0.0`.

---

## 3. Commands

```powershell
# Backend
.\.venv\Scripts\python.exe scripts/check_constraints.py
.\.venv\Scripts\python.exe -m ruff format --check src scripts tests
.\.venv\Scripts\python.exe -m ruff check src scripts tests
.\.venv\Scripts\python.exe -m mypy --strict src/finintel/analytics/stress_fx.py src/finintel/ports/bonds.py tests/unit/test_v3_fx_stress_bonds.py
.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_fx_stress_bonds.py tests/unit/test_layer4_forensic_and_stress.py -q

# Frontend
fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types
fnm exec --using=default -- npm.cmd --prefix frontend run check
fnm exec --using=default -- npm.cmd --prefix frontend run test:unit
```

---

## 4. Project Structure & File Ownership

| Path | Action | Responsibility |
|---|---|---|
| `src/finintel/ports/bonds.py` | Create (`<= 300` LOC) | Contract Extension Port 4 (`corporate_bond_signals`, `fx_debt_exposure`), schema validation, bảo vệ khỏi `DROP TABLE` (`B-03`) |
| `src/finintel/ingestion/bond_signals_collector.py` | Create (`<= 350` LOC) | Thu thập/nạp dữ liệu trái phiếu doanh nghiệp thật (HNX Cbonds / commercial feed) kèm `source_url`, `payload_sha256`, `ssrf_guard` |
| `src/finintel/analytics/stress_fx.py` | Create (`<= 300` LOC) | Vectorized Polars FX shock propagation (`fx_depreciation_pct` × `net_fx_debt_vnd`) nối gọn vào `stress_test.py` |
| `src/finintel/analytics/stress_test.py` | Modify (minimal) | Thêm field optional `fx_depreciation_pct: float = 0.0` vào `StressScenario` (100% backward-compatible) |
| `src/finintel/serving/routers/bonds_v3.py` | Create (`<= 250` LOC) | `GET /api/v3/companies/{ticker}/bond-signals` |
| `src/finintel/serving/api.py` | Modify (`<= 2` dòng) | Mount `bonds_v3_router` |
| `frontend/src/app/stress-test/page.tsx` | Modify | Thêm thanh trượt cú sốc tỷ giá `fx_depreciation_pct` (kèm chú thích chỉ áp dụng cho DN có công bố nợ ngoại tệ, đồng bộ URL share) |
| `frontend/src/components/company/BondMaturityCard.tsx` | Create (`<= 250` LOC) | Thẻ hiển thị dư nợ trái phiếu, giá trị đáo hạn 12T so với tiền mặt, cờ chậm trả gốc/lãi (ẩn gọn hoặc ghi *"Không phát hành trái phiếu lưu hành"* khi không có) |
| `tests/unit/test_v3_fx_stress_bonds.py` | Create | Test FX shock exact math, NULL preservation khi thiếu `net_fx_debt_vnd`, Port 4 snapshot safety (`B-03`), bond liquidity coverage |

---

## 5. Boundary Interfaces & Contracts

### 5.1. Extension Port 4 Schema (`corporate_bond_signals.parquet`)
| Column | Polars Type | Constraint |
|---|---|---|
| `issuer_id`, `ticker` | `String` | Khớp chuẩn `issuers` |
| `as_of_date` | `Date` | Ngày trích xuất chuẩn PIT (không look-ahead) |
| `outstanding_bond_vnd` | `Float64` | Tổng dư nợ trái phiếu lưu hành (`>= 0`) |
| `bond_maturity_12m_vnd` | `Float64` | Giá trị đáo hạn trong 12 tháng tới (`>= 0`) |
| `bond_payment_delay_flag` | `Boolean` | `True` nếu có công bố chậm thanh toán gốc/lãi thật trên HNX |
| `source_provider`, `source_url`, `payload_sha256` | `String` | Bắt buộc truy nguồn 100% (Zero-Mock) |

### 5.2. Extended `POST /api/v1/stress-test` Request
- Thêm field optional: `fx_depreciation_pct: float = Field(default=0.0, ge=-0.30, le=0.50)`
- Response bổ sung cho mỗi DN: `net_fx_debt_vnd: float | None`, `fx_revaluation_loss_vnd: float | None`, `fx_coverage_status: Literal["REPORTED", "NOT_DISCLOSED"]`.

---

## 6. Code Style & Architectural Invariants

- **Zero-Mock tuyệt đối:** Tuyệt đối không giả lập dư nợ trái phiếu hay tỷ lệ nợ USD cho bất kỳ mã nào. Khi `net_fx_debt_vnd` là `NULL`, giữ nguyên `fx_revaluation_loss_vnd = None` và báo `fx_coverage_status = "NOT_DISCLOSED"`.
- **Bảo toàn Đẳng thức Kế toán & Chặn OOD:** Không nối slider FX trực tiếp vào XGBoost (giữ nguyên nguyên tắc phản biện `#58`); mọi tác động đi qua động cơ cân bằng KQKD/ICR tất định.
- **DuckLake Safety (`B-03`):** Khai báo `corporate_bond_signals` và `fx_debt_exposure` là Extension Port tables ngoài `MANAGED_MART_TABLES` để `DuckLakeStore.publish_snapshot()` không bao giờ `DROP TABLE` nhầm.

---

## 7. Testing Strategy

- **Backend (`pytest`):**
  - Test `fx_depreciation_pct = 0.0` cho kết quả giống hệt 100% Stress Test IMF V2.0 cũ (0 regression).
  - Test `fx_depreciation_pct = 0.10` với DN có `net_fx_debt_vnd = 1.000 tỷ`: xác nhận `fx_revaluation_loss_vnd = 100 tỷ`, `stressed_icr` giảm đúng công thức; DN không công bố nợ ngoại tệ giữ `fx_coverage_status = "NOT_DISCLOSED"` và không bị lỗi `NaN`.
  - Test `DuckLakeStore.publish_snapshot()` giữ nguyên bảng `corporate_bond_signals` sau khi xoay snapshot mới.
- **Frontend (`vitest`):** Test `BondMaturityCard` hiển thị chuẩn tiếng Việt (`vi-VN`), màu ngữ nghĩa rõ ràng (cấm tím/cyan), URL sync cho tham số `fx` trên `/stress-test`.

---

## 8. Boundaries

- **Always:** Kèm `source_url` và ngày cập nhật cho mọi số liệu trái phiếu; kiểm tra `ssrf_guard` cho collector.
- **Ask first:** Trước khi bổ sung các nguồn trái phiếu tư nhân/ngoài HNX Cbonds yêu cầu tài khoản trả phí riêng.
- **Never:** Không bịa/ước lượng tỷ lệ nợ ngoại tệ bằng hằng số ngành; không xóa nhầm bảng Extension Ports khi publish DuckLake.

---

## 9. Success Criteria

- [ ] `POST /api/v1/stress-test` hỗ trợ `fx_depreciation_pct` tương thích ngược 100% và tính chính xác trên DN có công bố nợ ngoại tệ, trả `NOT_DISCLOSED` trung thực với DN chưa công bố.
- [ ] Extension Port 4 `corporate_bond_signals` được bảo vệ an toàn trong DuckLake (`B-03`) và hiển thị trực quan qua `BondMaturityCard`.
- [ ] `0` lỗi `ruff`, `mypy --strict`, `check_constraints.py`, `pytest`, `npm run check`.

---

## 10. Git Worktree & Commit Contract

```powershell
git worktree add ../finintel-v3-fx-stress-and-bond-signals -b feat/v3-fx-stress-and-bond-signals main
```
- Tách atomic commits:
  1. `feat(ports,ingestion): add Extension Port 4 corporate_bond_signals schema and collector`
  2. `feat(analytics,serving): extend IMF stress test with deterministic FX shock and bond API`
  3. `feat(frontend): add FX shock control in /stress-test and BondMaturityCard component`

---

## 11. Out of Scope (Deferred)

- Mô phỏng Monte Carlo đa biến nối vào XGBoost PD (chỉ xem xét sau khi có mô hình cân bằng CĐKT khép kín 3 bảng cho V4).
- Định giá trái phiếu (Yield-to-Maturity / Duration / Credit Spread curve) cho sàn giao dịch trái phiếu thứ cấp (Deferred to V4).

## 12. Open Questions

- Xác nhận nguồn dữ liệu trái phiếu doanh nghiệp chính thức cho Production (HNX Cbonds công khai cho bản Beta hay hợp đồng FiinData Bond / WiFeed cho bản thương mại).
