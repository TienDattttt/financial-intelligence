# Spec: `v3-private-issuer-and-hitl` — Parser BCTC DN Chưa Niêm Yết & Hàng Đợi HITL — V3.1

> **Module ID:** `v3-private-issuer-and-hitl`  
> **Wave / Build Order:** Phase 3 — **Wave 3** (chạy song song với `v3-b2b-commercial-api`, `v3-fx-stress-and-bond-signals`)  
> **Depends On:** `v3-identity-and-workspace`, `v3-analyst-depth-and-ops-qa`  
> **Consumers:** Cán bộ thẩm định tín dụng ngân hàng / doanh nghiệp (B2B) & Đội ngũ phân tích rủi ro nội bộ  
> **MoSCoW / Audit Refs:** `#42` Upload BCTC bổ sung (Excel/CSV/PDF Parser đa cột chuẩn VAS) + luồng chấm điểm DN chưa niêm yết, `#55` Cơ chế phản hồi chuyên gia (Human-in-the-Loop False Positive Triage).

---

## 1. Objective

Giải quyết bài toán giá trị cao cho phân khúc **Thẩm định Tín dụng Ngân hàng / B2B** mà vẫn bảo vệ tuyệt đối sự trong sạch khoa học của tập dữ liệu niêm yết:
1. **Phân hệ Upload & Bóc tách BCTC Doanh nghiệp Chưa Niêm Yết (`#42` — `src/finintel/private_credit/`):**
   - Khác với `FileAdapter` cũ (chỉ đọc file YAML 2 cột `raw_metric_key/raw_value`), xây dựng bộ **VAS Multi-Column Statement Parser** chấp nhận file Excel (`.xlsx`) mẫu chuẩn TT200/TT99 (tải sẵn template chuẩn + nhận diện mã số chỉ tiêu VAS `100..440`, `01..70`, `01..70` trên 3 sheet BS/IS/CF nhiều kỳ) và PDF bảng số (qua `PyMuPDF` + Vision table extraction có xác nhận người dùng trước khi tính!).
   - Chạy qua đúng bộ kiểm định kế toán **Layer 6 QG1** (cân bằng CĐKT, tiểu tổng, đơn vị tiền tệ) rồi đưa vào **luồng chấm điểm cô lập (`PrivateCreditScoringService`)**: tính 38 chỉ số tài chính, DuPont 5 nhân tố, 10 Cờ đỏ VAS, Altman Z'', Piotroski F-Score, Beneish M-Score, Stress Test IMF WP/15/216 và chấm điểm bằng mô hình `xgboost-layer4b-v2` (chế độ **Accounting-Only**, xử lý `NULL` tường minh cho các biến thị trường/TT96 không tồn tại ở DN tư nhân kèm cảnh báo giới hạn độ phủ rõ ràng).
   - **Cách ly dữ liệu tuyệt đối:** Dữ liệu BCTC DN chưa niêm yết lưu riêng trong `finintel_app` theo `user_id` (tự xóa theo chính sách retention / mã hóa khi lưu), **tuyệt đối không trộn vào DuckLake công khai hay tập huấn luyện ML**.
2. **Hàng đợi Phản hồi Chuyên gia — HITL False Positive Triage (`#55` — `src/finintel/hitl/`):**
   - Cho phép chuyên viên phân tích (`PRO_ANALYST`, `ENTERPRISE_TEAM`, `INTERNAL_OPS`) đánh dấu một cảnh báo Cờ đỏ (`RF_01..RF_10`) hoặc Ngoại lai đa biến là `CONFIRMED_RISK` hay `BENIGN_DOMAIN_EXCEPTION` kèm lý do chuẩn hóa (vd: *Xây lắp hạch toán theo tiến độ*, *Mô hình Holding nhận cổ tức công ty liên kết*) và trích dẫn trang thuyết minh.
   - **Kỷ luật MRM (SR 11-7):** Phản hồi HITL **tuyệt đối KHÔNG tự động ghi đè nhãn huấn luyện ML**; chỉ lưu vào bảng triage để đội lượng hóa đánh giá định kỳ (tinh chỉnh ngoại lệ quy tắc cờ đỏ & từ điển ngành).

---

## 2. Tech Stack

- **Backend (`Python 3.11`):** `polars==1.35.2`, `openpyxl==3.1.5` (đọc `.xlsx` đa sheet với ZIP-bomb guard kế thừa từ `file_adapter.py`), `pymupdf==2.1`, `fastapi==0.165.0`, `pydantic==2.14.4`, `psycopg[binary]==3.3.9`.
- **Frontend (`Node 24+`):** `next==16.4.6`, `react==19.2.4`, màn hình Preview & Xác nhận số liệu bóc tách (Human Verification Step) trước khi bấm chấm điểm.

---

## 3. Commands

```powershell
# Backend
.\.venv\Scripts\python.exe scripts/check_constraints.py
.\.venv\Scripts\python.exe -m ruff format --check src scripts tests
.\.venv\Scripts\python.exe -m ruff check src scripts tests
.\.venv\Scripts\python.exe -m mypy --strict src/finintel/private_credit src/finintel/hitl tests/unit/test_v3_private_hitl.py
.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_private_hitl.py -q

# Frontend
fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types
fnm exec --using=default -- npm.cmd --prefix frontend run check
fnm exec --using=default -- npm.cmd --prefix frontend run test:unit
```

---

## 4. Project Structure & File Ownership

| Path | Action | Responsibility |
|---|---|---|
| `config/migrations/app_v3_006_private_hitl.sql` | Create | DDL `private_credit_dossiers`, `private_credit_facts`, `hitl_flag_feedback` |
| `src/finintel/private_credit/` (`vas_excel_parser.py`, `scorer.py`, `router.py`, `schemas.py`) | Create (mỗi file `< 450` LOC) | Tải mẫu Excel VAS chuẩn TT200/TT99, parse đa kỳ + chống ZIP-bomb, chạy QG1 + Analytics + ML scoring cô lập |
| `src/finintel/hitl/` (`service.py`, `router.py`, `schemas.py`) | Create (mỗi file `< 300` LOC) | Ghi nhận, tổng hợp thống kê tỷ lệ False Positive theo từng mã cờ đỏ `RF_01..RF_10` và từng ngành ICB |
| `src/finintel/serving/api.py` | Modify (`<= 2` dòng) | Mount `private_credit_router` và `hitl_router` |
| `frontend/src/app/private-scoring/page.tsx` | Create (`<= 450` LOC) | Upload Excel/PDF → Xem trước bảng bóc tách & lỗi cân bằng QG1 → Xem hồ sơ rủi ro tức thì (`ENTERPRISE_TEAM`) |
| `frontend/src/components/financial/RedFlagChecklist.tsx` | Modify (small) | Nút *"Phản hồi chuyên gia"* nhỏ gọn trên từng cờ đỏ mở dialog gửi HITL feedback |
| `tests/unit/test_v3_private_hitl.py` | Create | Test parse Excel VAS 3 bảng, ZIP-bomb rejection, QG1 imbalance block, private-scoring isolation (0 ghi vào DuckLake), HITL RBAC & aggregation |

---

## 5. Boundary Interfaces & Contracts

### 5.1. Private Issuer Scoring Endpoints (`/api/v3/private-credit/*` — `ENTERPRISE_TEAM` / `INTERNAL_OPS`)
- `GET /api/v3/private-credit/template.xlsx?taxonomy=VAS_TT99_2025` — Tải file mẫu Excel chuẩn VAS có mã dòng `100..440` khóa sẵn.
- `POST /api/v3/private-credit/parse` (multipart `<= 15 MB`) → trả JSON preview 3 bảng + báo cáo cân bằng QG1 (`PASS | FAIL` kèm dòng lệch).
- `POST /api/v3/private-credit/dossiers/{id}/score` → chạy tính toán cô lập và trả bộ kết quả chuẩn hóa (tỷ số, DuPont, cờ đỏ, Altman/F-Score/Beneish, PD có gắn cờ `ACCOUNTING_ONLY_MODE`, Stress Test).
- `DELETE /api/v3/private-credit/dossiers/{id}` — Xóa vĩnh viễn hồ sơ khách hàng theo yêu cầu bảo mật ngân hàng.

### 5.2. HITL Feedback Endpoints (`/api/v3/hitl/*`)
- `POST /api/v3/hitl/feedback` (`ticker`, `period_end_date`, `signal_code`, `verdict: CONFIRMED_RISK | BENIGN_DOMAIN_EXCEPTION`, `reason_category`, `note`, `citation_page?`)
- `GET /api/v3/hitl/summary` (`INTERNAL_OPS` only — bảng tỷ lệ báo động giả theo `signal_code × icb_code_level2`).

---

## 6. Code Style & Architectural Invariants

- **Phòng thủ Upload file:** Giới hạn dung lượng `<= 15 MB`, giải nén `.xlsx` tối đa `50 MB` (`MAX_XLSX_UNCOMPRESSED_BYTES`), tối đa `5.000` dòng/sheet, từ chối file chứa macro (`.xlsm`) và symlink.
- **Bảo mật dữ liệu khách hàng tư nhân (Luật 91/2025/QH15):** BCTC doanh nghiệp chưa niêm yết tải lên ở chế độ Excel được xử lý **100% cục bộ trên server nội địa** (không gửi sang API LLM nước ngoài); nếu người dùng chọn bóc tách PDF scan bằng Vision LLM phải hiển thị cảnh báo & yêu cầu xác nhận `CROSS_BORDER_LLM_RAG` tường minh.
- **Zero Contamination:** Tuyệt đối không ghi bất kỳ dòng dữ liệu tư nhân nào vào `data/live_fhsc_universe/`, `data/universe_600_800/`, hay `published_financial_marts`.

---

## 7. Testing Strategy

- **Backend (`pytest`):**
  - Fixture `.xlsx` chuẩn TT200 & TT99 (tự sinh trong `tmp_path` từ mã số VAS chuẩn): xác nhận parse đúng 100% chỉ tiêu, chạy QG1 PASS và tính ra đúng ROA/ROE/DuPont/Altman/F-Score/Cờ đỏ.
  - Fixture `.xlsx` bị lệch `Tổng Tài sản != Nợ + VCSH`: QG1 trả `FAIL` chi tiết dòng lệch và chặn bước chấm điểm cho tới khi người dùng sửa file.
  - Fixture ZIP-bomb / file `.xlsm` / file giả đuôi: trả `400/413` an toàn.
  - Test HITL: xác nhận feedback không làm thay đổi bất kỳ bảng nào trong DuckLake hay `regulatory_events.parquet`.

---

## 8. Boundaries

- **Always:** Tái sử dụng trực tiếp các hàm tính toán thuần Polars từ `src/finintel/analytics/` và `src/finintel/quality/` (không viết trùng lặp công thức tài chính!).
- **Ask first:** Trước khi bổ sung chế độ tự động lưu vĩnh viễn file gốc PDF/Excel của khách hàng ngân hàng lên S3 (mặc định nên có TTL tự xóa sau 30 ngày nếu người dùng không chọn lưu).
- **Never:** Không trộn dữ liệu tư nhân vào kho công khai; không để HITL tự động chỉnh sửa nhãn huấn luyện ML.

---

## 9. Success Criteria

- [ ] Cán bộ tín dụng tải file Excel mẫu TT200/TT99, điền BCTC 2 năm của 1 doanh nghiệp tư nhân, tải lên và nhận đủ báo cáo 38 chỉ số + DuPont + Cờ đỏ + Stress Test trong `< 3 giây`.
- [ ] Cờ `ACCOUNTING_ONLY_MODE` hiển thị minh bạch cho phần điểm ML vì DN tư nhân không có dữ liệu thị trường/mốc nộp TT96.
- [ ] Chuyên gia gửi được phản hồi HITL trên cờ đỏ và đội Ops xem được bảng tổng hợp tỷ lệ False Positive theo ngành; `0` lỗi `check`, `pytest`, `check_constraints.py`.

---

## 10. Git Worktree & Commit Contract

```powershell
git worktree add ../finintel-v3-private-issuer-and-hitl -b feat/v3-private-issuer-and-hitl main
```
- Tách atomic commits:
  1. `feat(private-credit): add VAS TT200/TT99 multi-column Excel parser with ZIP-bomb guards`
  2. `feat(private-credit): wire isolated QG1, analytics, and accounting-only ML scoring endpoints`
  3. `feat(hitl): add expert false-positive triage API and Ops analytics summary`
  4. `feat(frontend): add /private-scoring workbench and RedFlagChecklist HITL feedback action`

---

## 11. Out of Scope (Deferred)

- Tích hợp đọc trực tiếp phần mềm kế toán MISA / FAST / SAP ERP (Deferred to V4).
- OCR tự động 100% không cần người dùng kiểm duyệt số liệu cho ảnh chụp điện thoại mờ (luôn yêu cầu bước Human Verification sau parse).

## 12. Open Questions

- Xác nhận chính sách thời hạn lưu trữ mặc định (`retention_days`, đề xuất `30` ngày) cho hồ sơ BCTC doanh nghiệp tư nhân do khách hàng ngân hàng tải lên.
