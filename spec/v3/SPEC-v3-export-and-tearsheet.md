# Spec: `v3-export-and-tearsheet` — Multi-Sheet Excel `.xlsx` & PDF Tear-Sheet — V3.0

> **Module ID:** `v3-export-and-tearsheet`  
> **Wave / Build Order:** Phase 1 — **Wave 1B** (chạy song song với `v3-identity-and-workspace`, sau khi `v3-compliance-and-mrm` và `v3-valuation-mart-and-screener` xong)  
> **Depends On:** `v3-compliance-and-mrm`, `v3-valuation-mart-and-screener`  
> **Consumers:** End-users (Chuyên viên phân tích tín dụng, Kiểm toán, Nhà đầu tư giá trị)  
> **MoSCoW / Audit Refs:** `#41` Xuất Báo cáo Đánh giá (Excel / PDF), Roadmap `UI-05b`.

---

## 1. Objective

Bổ sung khả năng kết xuất hồ sơ phân tích chuyên nghiệp chuẩn tổ chức tài chính (điểm thiếu quan trọng nhất khi chuyên viên cần đưa số liệu vào mô hình Excel hoặc đính kèm báo cáo trình cấp trên):
1. **Xuất Excel `.xlsx` nhiều sheet có định dạng chuẩn kế toán VAS:** Cho 1 doanh nghiệp (`4` sheet: `Tong_Quan_Rui_Ro`, `BCTC_Chuan_Hoa`, `Chi_So_Va_DuPont`, `Co_Do_Va_Nguon_Du_Lieu`) và cho kết quả Screener/Compare. Chống tấn công **CSV/Excel Formula Injection** (`=`, `+`, `-`, `@` prefix sanitization trên mọi chuỗi văn bản).
2. **Xuất PDF Institutional Tear-Sheet (1–2 trang A4 gọn sắc nét):** Trình bày tóm tắt định lượng 1 doanh nghiệp gồm: Thông tin DN & Độ phủ dữ liệu, Nhóm cảnh báo rủi ro định lượng `R1–R5` + Xác suất kiệt quệ 1 năm + Vị thế cùng kỳ, Bảng chỉ số chính vs Trung vị ngành, Top yếu tố tác động TreeSHAP, Danh sách cờ đỏ `RF_01..RF_10`, Giá EOD & Định giá PIT, kèm **dòng truy vết `snapshot_id`, ngày cập nhật và tuyên bố miễn trừ pháp lý chuẩn (`v3-compliance-and-mrm`)**.

---

## 2. Tech Stack

- **Backend (`Python 3.11`):** Bổ sung vào group `platform` (lazy-import): `openpyxl==3.1.5` (hoặc `xlsxwriter==3.2.2` — khuyến nghị **`xlsxwriter==3.2.2`** vì tốc độ ghi nhanh, nhẹ RAM và hỗ trợ format số kế toán chuẩn xác).
- **PDF Tear-Sheet:** Dùng **Frontend Print-Ready A4 Route (`/companies/[ticker]/tear-sheet`)** với `@media print` CSS chuẩn A4 (không cần kéo Chromium/WeasyPrint nặng vào container FastAPI, `0` chi phí CPU server, giữ nguyên 100% typography & biểu đồ vector) + nút `Tải PDF / In hồ sơ` (`window.print()`). *(Nếu sau này B2B API cần PDF server-side sẽ bổ sung worker riêng ở V3.2).*

---

## 3. Commands

```powershell
# Backend
.\.venv\Scripts\python.exe scripts/check_constraints.py
.\.venv\Scripts\python.exe -m ruff format --check src scripts tests
.\.venv\Scripts\python.exe -m ruff check src scripts tests
.\.venv\Scripts\python.exe -m mypy --strict src/finintel/serving/exports tests/unit/test_v3_export_tearsheet.py
.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_export_tearsheet.py -q

# Frontend
fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types
fnm exec --using=default -- npm.cmd --prefix frontend run check
fnm exec --using=default -- npm.cmd --prefix frontend run test:unit
```

---

## 4. Project Structure & File Ownership

| Path | Action | Responsibility |
|---|---|---|
| `src/finintel/serving/exports/__init__.py` | Create | Export router & service |
| `src/finintel/serving/exports/excel_builder.py` | Create (`<= 450` LOC) | Sinh workbook `.xlsx` 4 sheet (company) & 1 sheet (screener/compare), format số `#,##0.0;(#,##0.0);"-"`, formula-injection guard |
| `src/finintel/serving/exports/router.py` | Create (`<= 250` LOC) | `GET /api/v3/exports/companies/{ticker}.xlsx`, `GET /api/v3/exports/screener.xlsx` |
| `src/finintel/serving/api.py` | Modify (`<= 2` dòng) | Mount `exports_router` |
| `frontend/src/components/export/ExportActionMenu.tsx` | Create (`<= 200` LOC) | Menu tải `.xlsx` / `.csv` / Mở Tear-Sheet in PDF |
| `frontend/src/app/companies/[ticker]/tear-sheet/page.tsx` | Create (`<= 350` LOC) | Trang bố cục A4 sạch (`@media print`, Light mode cứng khi in, không sidebar/nav, đầy đủ disclaimer NĐ 88 & Luật CK) |
| `tests/unit/test_v3_export_tearsheet.py` | Create | Verify workbook sheets, numeric cell types (không lưu số dạng text), formula injection neutralization, compliance disclaimer cell |
| `frontend/tests/unit/export_tearsheet.test.tsx` | Create | Verify Tear-Sheet render đủ khối định lượng, nhãn rủi ro hợp chuẩn và disclaimer |

---

## 5. Boundary Interfaces & Contracts

### 5.1. Export Endpoints (`/api/v3/exports/*`)
- `GET /api/v3/exports/companies/{ticker}.xlsx?statement_scope=CONSOLIDATED&period_type=QUARTER`
  - Response: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
  - Headers: `Content-Disposition: attachment; filename="finintel-{ticker}-{snapshot_short}.xlsx"`, `X-Snapshot-ID`, `Cache-Control: private, max-age=300`.
- `GET /api/v3/exports/screener.xlsx?...` (nhận cùng query params như `/api/v1/screener`, giới hạn cứng `<= 2.000` dòng để chống OOM).

### 5.2. Spreadsheet Formula Neutralization Contract
Mọi giá trị kiểu `str` ghi vào ô Excel (tên công ty, ngành, diễn giải cờ đỏ, tên cụm) bắt đầu bằng `=`, `+`, `-`, `@`, `\t`, `\r` phải được tiền tố dấu `'` hoặc ghi qua `write_string()` an toàn.

---

## 6. Code Style & Architectural Invariants

- **Cấm tính lại công thức tài chính trong `excel_builder.py`:** Chỉ đọc dữ liệu đã tính từ `DashboardQueryService` (Layer 5 đọc-chỉ).
- **Kế toán VAS:** Ô thiếu dữ liệu (`None`) ghi ô rỗng (`None`) kèm cột cờ lý do, tuyệt đối không điền `0`.
- **Lazy-import `xlsxwriter`:** Giữ CI `mvp-checks` xanh khi chưa cài group `platform`.
- **Nhãn pháp lý:** Nhúng `COMPLIANCE_DISCLAIMER_VI` từ `v3-compliance-and-mrm` vào footer trang đầu của `.xlsx` và chân trang PDF Tear-Sheet; cấm dùng cụm *"Bậc tín nhiệm"* hay *"Khuyến nghị"*.

---

## 7. Testing Strategy

- **Backend unit (`pytest`):** Đọc lại file `.xlsx` xuất ra (qua `zipfile`/XML parser hoặc `openpyxl` trong test) xác nhận đủ 4 sheet, đúng số kỳ, số âm giữ kiểu numeric float (không thành chuỗi), ô chuỗi độc hại `=CMD|' /C calc'!A0` bị vô hiệu hóa, chứa `snapshot_id` và disclaimer.
- **Performance:** Xuất `.xlsx` 1 doanh nghiệp 20 kỳ `< 300 ms`, xuất Screener 685 dòng `< 1.000 ms`, đỉnh RAM `< 50 MB`.
- **Frontend unit (`vitest`):** Tear-Sheet hiển thị đúng trong Light Mode, 0 lỗi `axe-core`, không lộ backend tokens.

---

## 8. Boundaries

- **Always:** Đính kèm `snapshot_id`, ngày công bố mới nhất và tuyên bố miễn trừ trên mọi file xuất.
- **Ask first:** Trước khi bổ sung headless Chromium PDF rendering phía backend (tốn RAM & tăng attack surface).
- **Never:** Không ghi file tạm ra đĩa gốc repo (ghi vào `BytesIO` trong RAM rồi stream response); không sinh văn bản nhận định tự do bằng LLM nhồi vào Tear-Sheet.

---

## 9. Success Criteria

- [ ] Người dùng tải được file `.xlsx` 4 sheet của bất kỳ mã hợp lệ nào (`HPG`, `FPT`, `HBC`, ...) mở sạch không cảnh báo lỗi định dạng trên Microsoft Excel & LibreOffice Calc.
- [ ] Trang `/companies/[ticker]/tear-sheet` in ra đúng 1–2 trang A4 gọn gàng, số liệu `tabular-nums`, đủ 6 khối phân tích cốt lõi và tuyên bố miễn trừ pháp lý.
- [ ] `0` lỗi formula injection, `0` lỗi `mypy --strict`, `ruff`, `check_constraints.py`, `npm run check`.

---

## 10. Git Worktree & Commit Contract

```powershell
git worktree add ../finintel-v3-export-and-tearsheet -b feat/v3-export-and-tearsheet main
```
- Tách atomic commits:
  1. `chore(deps): pin xlsxwriter in platform dependency group`
  2. `feat(serving): add multi-sheet company and screener .xlsx export endpoints`
  3. `feat(frontend): add export menu and A4 print-ready institutional tear-sheet route`

---

## 11. Out of Scope (Deferred)

- Xuất PowerPoint (`.pptx`) hoặc server-side headless PDF batch rendering (Deferred to V3.2 Enterprise).
- Custom white-label logo của công ty chứng khoán/ngân hàng (Deferred to V3.2 Enterprise).

## 12. Open Questions

- Xác nhận giới hạn xuất `.xlsx` cho gói `FREE` (ví dụ: giới hạn 3 năm gần nhất hoặc chỉ cho phép gói `PRO_ANALYST`, sẽ nối với quota ở `v3-scaled-rag-and-billing` Wave 2).
