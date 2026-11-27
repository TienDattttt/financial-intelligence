# FinIntel V3.0 — Atomic Implementation Task Checklist (`tasks/todo.md`)

> **Source Plan:** [`tasks/plan.md`](./plan.md) · **Capability Map:** [`spec/v3/capability-map.md`](../spec/v3/capability-map.md)  
> **Quy ước chung cho mọi Task:** Kích thước `XS/S/M` (`<= 5` tệp), chạy trên `git worktree` riêng của module, giữ tuyệt đối kỷ luật `CONSTRAINTS.md` (`0` `.fill_null(0)`, `0` công thức tài chính trong `serving/` hoặc `frontend/`, `0` ghi vào `data/live_fhsc_universe/` và `data/universe_600_800/`, lazy-import thư viện `platform`).

---

# PHASE 1 — WAVE 1A (3 Parallel Streams)

## Stream 1A-1: `v3-compliance-and-mrm` (`feat/v3-compliance-and-mrm`)

### Task V3-01: Add VN 2026 compliance policy docs and PDPL consent contracts
**Description:** Tạo tài liệu ma trận tuân thủ pháp lý VN 2025–2026 (NĐ 88/2014, Luật CK 56/2024, Luật 91/2025 kèm mẫu TIA Điều 20) và module contract `ConsentPurpose` / `ComplianceNotice` trong tầng domain để các module downstream (`v3-identity-and-workspace`, `v3-export-and-tearsheet`, `v3-scaled-rag-and-billing`) dùng chung.

**Acceptance criteria:**
- [x] `src/finintel/domain/compliance.py` định nghĩa rõ `ConsentPurpose` (`ACCOUNT_CORE`, `ALERT_NOTIFICATIONS`, `CROSS_BORDER_LLM_RAG`), `ConsentPolicyVersion`, `ComplianceNotice` đạt `mypy --strict`.
- [x] `docs/compliance/VN_REGULATORY_COMPLIANCE.md` ghi đầy đủ checklist NĐ 88/2014, Luật CK 56/2024 và quy trình TIA Điều 20 Luật 91/2025.
- [x] Unit tests kiểm tra serialization và validate phiên bản consent pass 100% không cần Postgres/Valkey.

**Verification:**
- [x] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_compliance_mrm.py -q`
- [x] Static checks: `.\.venv\Scripts\python.exe -m ruff check src tests && .\.venv\Scripts\python.exe -m mypy --strict src/finintel/domain/compliance.py tests/unit/test_v3_compliance_mrm.py`
- [x] Constraints: `.\.venv\Scripts\python.exe scripts/check_constraints.py`

**Dependencies:** None  
**Commit type:** `feat(domain,docs)`  
**Files likely touched:**
- `src/finintel/domain/compliance.py`
- `docs/compliance/VN_REGULATORY_COMPLIANCE.md`
- `tests/unit/test_v3_compliance_mrm.py`

**Estimated scope:** Small (3 files)

---

### Task V3-02: Implement TT200-to-TT99 MRM drift gate and wire into Model Card
**Description:** Xây dựng module `src/finintel/ml/mrm_tt99.py` đánh giá cổng drift `TT200 → TT99` theo 4 trạng thái `TT99GateVerdict` (`INSUFFICIENT_TT99_SAMPLES`, `SHADOW_MONITORING`, `READY_FOR_GA`, `DRIFT_BREACH`) và nối kết quả vào `model_card_meta` (không làm tăng quá `+10` dòng ở `train.py`).

**Acceptance criteria:**
- [x] `evaluate_tt99_mrm_gate()` phân loại chính xác biên `n < 30`, `30 <= n < 150`, `n >= 150`, `max_psi >= 0.25`, `max_missing_jump >= 0.15` và xử lý an toàn `NaN`/`inf`.
- [x] `model_card_meta` và `GET /api/v2/meta/model-card` xuất tường minh trường `tt99_mrm_gate` (thay vì `taxonomy_drift = null` mờ nhạt) mà vẫn tương thích ngược schema cũ.
- [x] Không bịa dữ liệu TT99 giả; snapshot chưa đủ mẫu TT99 trả trung thực `INSUFFICIENT_TT99_SAMPLES`.

**Verification:**
- [x] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_compliance_mrm.py tests/unit/test_layer4_ml*.py -q`
- [x] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/ml/mrm_tt99.py tests/unit/test_v3_compliance_mrm.py`
- [x] Constraints: `.\.venv\Scripts\python.exe scripts/check_constraints.py`

**Dependencies:** `V3-01`  
**Commit type:** `feat(ml)`  
**Files likely touched:**
- `src/finintel/ml/mrm_tt99.py`
- `src/finintel/ml/supervised/train.py` *(<= +10 LOC)*
- `src/finintel/serving/schemas.py`
- `tests/unit/test_v3_compliance_mrm.py`

**Estimated scope:** Medium (4 files)

---

### Task V3-03: Enforce ND 88/2014 risk wording across frontend and CI static guard
**Description:** Chuẩn hóa hằng số miễn trừ pháp lý tại `frontend/src/lib/compliance.ts`, xóa sạch các chuỗi *"Bậc tín nhiệm" / "Xếp hạng tín nhiệm"* còn sót trong `frontend/src/lib/domain-labels.ts` & `frontend/PRODUCT.md`, và thêm quy tắc kiểm soát tĩnh vào `scripts/check_constraints.py`.

**Acceptance criteria:**
- [x] `scripts/check_constraints.py --selftest` và `scripts/check_constraints.py` pass với quy tắc mới cấm *"Xếp hạng tín nhiệm"*, *"Bậc tín nhiệm"*, *"Khuyến nghị mua/bán"* trong `frontend/src/`.
- [x] `frontend/src/lib/compliance.ts` xuất `COMPLIANCE_DISCLAIMER_VI` và `RISK_TIER_LEGAL_LABEL_VI` dùng chung cho UI & Export.
- [x] `frontend/tests/unit/compliance_labels.test.ts` xác nhận `0` chuỗi bị cấm trên toàn bộ `domain-labels.ts`.

**Verification:**
- [x] Backend/CI guard: `.\.venv\Scripts\python.exe scripts/check_constraints.py --selftest && .\.venv\Scripts\python.exe scripts/check_constraints.py`
- [x] Frontend build & tests: `fnm exec --using=default -- npm.cmd --prefix frontend run check && fnm exec --using=default -- npm.cmd --prefix frontend run test:unit`

**Dependencies:** `V3-01`  
**Commit type:** `feat(frontend,ci)`  
**Files likely touched:**
- `frontend/src/lib/compliance.ts`
- `frontend/src/lib/domain-labels.ts`
- `frontend/PRODUCT.md`
- `scripts/check_constraints.py`
- `frontend/tests/unit/compliance_labels.test.ts`

**Estimated scope:** Medium (5 files)

### Checkpoint 1A-1: After Tasks `V3-01..V3-03` (`v3-compliance-and-mrm`)
- [x] Toàn bộ `pytest` liên quan + `check_constraints.py` + `npm --prefix frontend run check` + `test:unit` xanh 100%.
- [x] Nhánh `feat/v3-compliance-and-mrm` sẵn sàng merge & unblock `v3-identity-and-workspace`, `v3-export-and-tearsheet`.

---

## Stream 1A-2: `v3-valuation-mart-and-screener` (`feat/v3-valuation-mart-and-screener`)

### Task V3-04: Add ocf_to_total_liabilities and vectorized pit_valuation_mart builder
**Description:** Bổ sung chỉ số dòng tiền `ocf_to_total_liabilities` vào Layer 4A (`ratios.py` + `metric_definitions.yaml`) và xây dựng module `src/finintel/analytics/valuation_mart.py` tính bảng `pit_valuation_mart` toàn thị trường bằng Polars vectorized (dùng `close_raw` + `join_asof(..., strategy="forward")`, tuyệt đối không dùng `close_adj`).

**Acceptance criteria:**
- [x] `ocf_to_total_liabilities` trả `NULL` + `RatioStatusFlag` khi `BS_TOTAL_LIABILITIES <= 0` hoặc thiếu dữ liệu (0 `.fill_null(0)`).
- [x] `valuation_mart.py` chứng minh `0%` look-ahead cuối tuần và tính đúng `market_cap_vnd`, `pe_ttm`, `pb_ratio` theo `close_raw` trên fixture chia tách 2:1.
- [x] `EPS <= 0` hoặc `parent_equity <= 0` trả `NULL` kèm `pe_flag`/`pb_flag = "NEGATIVE_DENOMINATOR"`.

**Verification:**
- [x] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_valuation_mart_screener.py tests/unit/test_layer4_analytics.py -q`
- [x] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/analytics/valuation_mart.py src/finintel/analytics/ratios.py`
- [x] Constraints: `.\.venv\Scripts\python.exe scripts/check_constraints.py`

**Dependencies:** None  
**Commit type:** `feat(analytics)`  
**Files likely touched:**
- `src/finintel/analytics/valuation_mart.py`
- `src/finintel/analytics/ratios.py`
- `config/metric_definitions.yaml`
- `tests/unit/test_v3_valuation_mart_screener.py`

**Estimated scope:** Medium (4 files)

---

### Task V3-05: Register pit_valuation_mart in DuckLake and extend Screener backend API
**Description:** Đăng ký `pit_valuation_mart` vào `MANAGED_MART_TABLES` trong `storage/schemas.py` và bước publish, sau đó mở rộng `serving/queries/screener.py` (tách helper lọc nếu gần 1.000 LOC) + `ScreenerItem` + Valkey cache keys để hỗ trợ lọc/trả về 14 nhóm tiêu chí mới (`ROE/ROA/EPS/Debt Ratio/D/E/CFO/OCF-Nợ/DT YoY/P/E/P/B/Vốn hóa/Sàn/Cụm/Ngoại lai`).

**Acceptance criteria:**
- [x] `DuckLakeStore.publish_snapshot()` quản lý `pit_valuation_mart` an toàn mà không chạm `regulatory_events` hoặc `market_prices` (`B-03`).
- [x] `GET /api/v1/screener` và `GET /api/v1/screener/export` giữ tương thích ngược 100%, hỗ trợ đầy đủ 14 bộ lọc mới bằng DuckDB parameterized query.
- [x] `test_serving_latency_budget.py` đạt `p95 <= 50ms` (Valkey ấm) và `<= 200ms` (DuckLake lạnh); mọi file trong `serving/queries/` giữ `< 1.000` LOC.

**Verification:**
- [x] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_valuation_mart_screener.py tests/unit/test_layer3_ducklake_storage.py tests/unit/test_layer5_fastapi_serving.py tests/unit/test_serving_latency_budget.py -q`
- [x] Constraints: `.\.venv\Scripts\python.exe scripts/check_constraints.py`

**Dependencies:** `V3-04`  
**Commit type:** `feat(storage,serving)`  
**Files likely touched:**
- `src/finintel/storage/schemas.py`
- `src/finintel/serving/queries/_screener_filters.py`
- `src/finintel/serving/queries/screener.py`
- `src/finintel/serving/schemas.py`
- `src/finintel/serving/api.py` *(<= +10 LOC)*

**Estimated scope:** Medium (5 files)

---

### Task V3-06: Upgrade Frontend Screener filters, valuation columns and OpenAPI types
**Description:** Đồng bộ `gen:api-types`, bổ sung nhóm bộ lọc tỷ số tài chính, định giá (`P/E`, `P/B`, `Vốn hóa` tỷ VND), sàn (`HOSE/HNX/UPCOM`), cụm và ngoại lai cùng các cột bảng (`EPS`, `Debt Ratio`, `CFO`, `P/E`, `P/B`, `Vốn hóa`) trên `/screener`, đồng bộ URL & Saved Screens.

**Acceptance criteria:**
- [x] Người dùng lọc kết hợp được đồng thời điều kiện rủi ro ML + tỷ số cơ bản + định giá + sàn mà không nhấp nháy bảng (`keepPreviousData`).
- [x] Xuất CSV từ `/screener` chứa đủ các cột mới; Saved Screens cũ vẫn đọc bình thường.
- [x] Đạt WCAG 2.2 AA (`0` tràn ngang ở `375px` & `1366px`, Light & Dark Mode).

**Verification:**
- [x] API types & FE build: `fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types && fnm exec --using=default -- npm.cmd --prefix frontend run check`
- [x] Unit tests: `fnm exec --using=default -- npm.cmd --prefix frontend run test:unit`
- [x] Constraints: `.\.venv\Scripts\python.exe scripts/check_constraints.py`

**Dependencies:** `V3-05`  
**Commit type:** `feat(frontend)`  
**Files likely touched:**
- `frontend/src/types/api.generated.ts`
- `frontend/src/components/screener/ScreenerFinancialFilters.tsx`
- `frontend/src/app/screener/page.tsx`
- `frontend/tests/unit/ui05.test.ts`

**Estimated scope:** Medium (4 files)

### Checkpoint 1A-2: After Tasks `V3-04..V3-06` (`v3-valuation-mart-and-screener`)
- [x] Backend + Frontend checks & unit tests xanh 100%, `check_constraints.py` = `0` lỗi.
- [x] Nhánh `feat/v3-valuation-mart-and-screener` sẵn sàng merge.

---

## Stream 1A-3: `v3-infra-ha-storage` (`feat/v3-infra-ha-storage`)

### Task V3-07: Add Cloudflare R2/S3 ObjectStoreConfig and DuckDB httpfs adapter
**Description:** Xây dựng `src/finintel/storage/object_store.py` hỗ trợ đọc/ghi Parquet snapshots trên **Cloudflare R2 (`region="auto"`, `s3://`, `$0` egress)** với mặc định fallback `local` disk cho dev/tests, không bao giờ ghi log secret keys.

**Acceptance criteria:**
- [x] `ObjectStoreConfig` validate đầy đủ cấu hình `local | r2 | s3`, sinh câu lệnh DuckDB `CREATE SECRET` đúng cú pháp và ẩn 100% credentials trong `repr`/`log`.
- [x] `DuckLakeStore` giữ nguyên 100% hành vi `local` mặc định khi không bật R2.
- [x] Unit tests offline pass 100% không cần truy cập mạng hay cài `boto3`.

**Verification:**
- [x] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_infra_ha_storage.py tests/unit/test_layer3_ducklake_storage.py -q`
- [x] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/storage/object_store.py tests/unit/test_v3_infra_ha_storage.py`

**Dependencies:** None  
**Commit type:** `feat(storage)`  
**Files likely touched:**
- `src/finintel/storage/object_store.py`
- `src/finintel/storage/ducklake_store.py`
- `tests/unit/test_v3_infra_ha_storage.py`

**Estimated scope:** Small (3 files)

---

### Task V3-08: Add snapshot rollover L1B cache sync and strict security headers middleware
**Description:** Bổ sung cơ chế tự kiểm tra active snapshot pointer từ PostgreSQL (`finintel_active_snapshot`) để tự evict L1B cache khi rollover, đồng thời gắn middleware Security Headers (CSP, HSTS, `X-Content-Type-Options`, `Referrer-Policy`) và CORS khóa theo domain Vercel cụ thể.

**Acceptance criteria:**
- [x] Giả lập 2 instance `DashboardQueryService` cùng trỏ 1 pointer: khi xoay `snap_1 → snap_2`, cả 2 instance đều evict L1B và trả `X-Snapshot-ID: snap_2`.
- [x] Response trả đủ security headers chuẩn OWASP; CORS từ chối wildcard `*` khi có credentials/cookies.

**Verification:**
- [x] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_infra_ha_storage.py tests/unit/test_layer5_fastapi_serving.py -q`
- [x] Constraints: `.\.venv\Scripts\python.exe scripts/check_constraints.py`

**Dependencies:** `V3-07`  
**Commit type:** `feat(serving)`  
**Files likely touched:**
- `src/finintel/serving/dependencies.py`
- `tests/unit/test_v3_infra_ha_storage.py`

**Estimated scope:** Small (2 files)

---

### Task V3-09: Add OCI Free Cloud docker-compose, Caddyfile, R2 backup script and deploy runbook
**Description:** Tạo `docker-compose.free.yml` khóa `mem_limit` tổng `< 8 GB` cho **OCI Always Free (`2 ARM OCPU / 12 GB RAM`)**, `config/gateway/Caddyfile.free` (auto-HTTPS, khóa port nội bộ), `scripts/backup_to_r2.py` (xoay vòng giữ `< 8 GB / 10 GB` R2), workflow dự phòng `.github/workflows/free-etl-fallback.yml` và sổ tay `docs/runbooks/free_cloud_deploy.md`.

**Acceptance criteria:**
- [x] `docker compose -f docker-compose.free.yml config -q` hợp lệ, chỉ mở cổng `80/443` qua Caddy, khóa Postgres/Valkey/Dagster trong `internal_net`, dùng `pgvector/pgvector:pg16` (ARM64 compatible).
- [x] `scripts/backup_to_r2.py --dry-run` tính đúng vòng quay retention dưới ngưỡng `8 GB` và pass unit test.
- [x] `docs/runbooks/free_cloud_deploy.md` hướng dẫn đầy đủ deploy `$0/tháng` cho Phương án 1 (OCI + Vercel + R2) và dự phòng Phương án 2.

**Verification:**
- [x] Compose syntax: `docker compose -f docker-compose.free.yml config -q`
- [x] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_infra_ha_storage.py -q`
- [x] Constraints: `.\.venv\Scripts\python.exe scripts/check_constraints.py`

**Dependencies:** `V3-07`, `V3-08`  
**Commit type:** `chore(infra)`  
**Files likely touched:**
- `docker-compose.free.yml`
- `config/gateway/Caddyfile.free`
- `scripts/backup_to_r2.py`
- `.github/workflows/free-etl-fallback.yml`
- `docs/runbooks/free_cloud_deploy.md`

**Estimated scope:** Medium (5 files)

### Checkpoint 1A-3 & WAVE 1A GATE: After Tasks `V3-01..V3-09`
- [x] Cả 3 nhánh Wave 1A (`v3-compliance-and-mrm`, `v3-valuation-mart-and-screener`, `v3-infra-ha-storage`) merge sạch vào nhánh tích hợp.
- [x] Toàn bộ bộ lệnh `AGENTS.md` §3 (`check_constraints`, `ruff`, `mypy --strict`, `pytest -q`, `frontend check`, `frontend test:unit`) PASS 100%.

---

# PHASE 1 — WAVE 1B (2 Parallel Streams)

## Stream 1B-1: `v3-identity-and-workspace` (`feat/v3-identity-and-workspace`)

### Task V3-10: Add finintel_app DDL, argon2-cffi pin and core session Auth/RBAC backend
**Description:** Bổ sung DB `finintel_app`, migration `app_v3_001_identity_workspace.sql`, ghim `argon2-cffi==23.1.0` trong group `platform`, và xây dựng gói `src/finintel/auth/` (Đăng ký kèm consent `ACCOUNT_CORE`, Đăng nhập HttpOnly Secure Cookie lưu SHA-256 session, Rate-limit chống brute-force 5 lần/15p, Quên/Đổi mật khẩu qua Resend adapter, RBAC 3 vai trò `USER/RESEARCHER/INTERNAL_OPS`, Xóa tài khoản Luật 91/2025).

**Acceptance criteria:**
- [x] Argon2id hash/verify, opaque session cookie (`HttpOnly; Secure; SameSite=Lax`), chống email enumeration (`202` trung tính), khóa brute-force sau 5 lần sai.
- [x] `require_role(UserRole.USER | RESEARCHER | INTERNAL_OPS)` chặn chính xác quyền truy cập; luồng khách ẩn danh cũ không bị ảnh hưởng.
- [x] Toàn bộ thư viện `argon2`/`psycopg`/`redis` lazy-import nên `mvp-checks` chạy xanh không cần Postgres.

**Verification:**
- [x] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_identity_workspace.py -q`
- [x] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/auth tests/unit/test_v3_identity_workspace.py`
- [x] Constraints: `.\.venv\Scripts\python.exe scripts/check_constraints.py`

**Dependencies:** `V3-01`  
**Commit type:** `feat(auth)`  
**Files likely touched:**
- `pyproject.toml` *(pin `argon2-cffi==23.1.0` in `platform`)*
- `config/postgres-init/01-create-dbs.sql`
- `config/migrations/app_v3_001_identity_workspace.sql`
- `src/finintel/auth/` *(`passwords.py`, `service.py`, `dependencies.py`, `router.py`, `schemas.py`)*
- `tests/unit/test_v3_identity_workspace.py`

**Estimated scope:** Medium (5 logical units)

---

### Task V3-11: Add PostgreSQL Multi-Watchlist, Analyst Notes, Saved Screens and Audit Log API
**Description:** Xây dựng gói `src/finintel/workspace/` hỗ trợ CRUD Multi-Watchlist (kèm import tự động từ `localStorage`), Ghi chú phân tích theo mã/kỳ (`analyst_notes`), Saved Screens server-side, và `record_audit_event()` ghi `user_audit_logs` bất biến (`append-only`), mount vào `api.py` (`<= +4` LOC).

**Acceptance criteria:**
- [x] Mọi query SQL đều lọc cứng `WHERE user_id = %(user_id)s` (0% IDOR giữa các tài khoản).
- [x] `POST /api/v3/workspace/watchlists/import-local` chống trùng ticker và tôn trọng giới hạn vai trò (`USER` vs `RESEARCHER`).
- [x] `user_audit_logs` ghi vết 100% sự kiện auth & mutating actions; `/api/v3/ops/audit-logs` chỉ mở cho `INTERNAL_OPS`.

**Verification:**
- [x] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_identity_workspace.py -q`
- [x] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/workspace`
- [x] Constraints: `.\.venv\Scripts\python.exe scripts/check_constraints.py`

**Dependencies:** `V3-10`  
**Commit type:** `feat(workspace)`  
**Files likely touched:**
- `src/finintel/workspace/service.py`
- `src/finintel/workspace/audit.py`
- `src/finintel/workspace/router.py`
- `src/finintel/workspace/schemas.py`
- `src/finintel/serving/api.py` *(<= +4 LOC)*

**Estimated scope:** Medium (5 files)

---

### Task V3-12: Build Auth/Profile pages and server-backed Multi-Watchlist with Analyst Notes UI
**Description:** Xây dựng màn hình `/auth/login`, `/auth/register` (có checkbox đồng ý Luật 91/2025), `/auth/reset-password`, `/profile` (quản lý phiên & nút xóa tài khoản), và nâng cấp `/watchlist` hỗ trợ nhiều danh mục + ghi chú phân tích + đồng bộ 1 chạm từ `localStorage`.

**Acceptance criteria:**
- [x] Người dùng ẩn danh vẫn xem & dùng `localStorage` Watchlist bình thường; khi đăng nhập tự động chuyển sang server-backed Multi-Watchlist + Notes.
- [x] Toàn bộ form Auth/Profile/Watchlist đạt WCAG 2.2 AA (`aria-describedby` lỗi trường nhập, focus management, Light & Dark Mode).
- [x] `0` lưu token vào `localStorage` (100% dùng HttpOnly Cookie).

**Verification:**
- [x] API types & FE check: `fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types && fnm exec --using=default -- npm.cmd --prefix frontend run check`
- [x] Unit tests: `fnm exec --using=default -- npm.cmd --prefix frontend run test:unit`

**Dependencies:** `V3-11`  
**Commit type:** `feat(frontend)`  
**Files likely touched:**
- `frontend/src/app/auth/login/page.tsx`
- `frontend/src/app/auth/register/page.tsx`
- `frontend/src/app/profile/page.tsx`
- `frontend/src/app/watchlist/page.tsx`
- `frontend/src/lib/watchlist-store.ts`

**Estimated scope:** Medium (5 files)

### Checkpoint 1B-1: After Tasks `V3-10..V3-12` (`v3-identity-and-workspace`)
- [x] Kiểm thử toàn luồng Đăng ký → Đăng nhập → Tạo 2 Watchlist + Ghi chú → Xem Audit Log → Xóa tài khoản pass 100%.

---

## Stream 1B-2: `v3-export-and-tearsheet` (`feat/v3-export-and-tearsheet`)

### Task V3-13: Pin xlsxwriter and build formula-safe multi-sheet Excel exporter
**Description:** Ghim `xlsxwriter==3.2.2` vào group `platform` (lazy-import) và xây dựng `src/finintel/serving/exports/excel_builder.py` xuất workbook `.xlsx` 4 sheet cho 1 doanh nghiệp (`Tong_Quan_Rui_Ro`, `BCTC_Chuan_Hoa`, `Chi_So_Va_DuPont`, `Co_Do_Va_Nguon_Du_Lieu`) và 1 sheet cho Screener, bảo toàn kiểu số numeric (`None` = ô trống, không `0`), chống Excel Formula Injection và gắn footer miễn trừ từ `v3-compliance-and-mrm`.

**Acceptance criteria:**
- [x] Đọc lại file `.xlsx` sinh ra xác nhận đủ 4 sheet, số âm lưu dạng numeric float (không thành text), ô trống cho giá trị `NULL`.
- [x] Chuỗi độc hại bắt đầu bằng `=`, `+`, `-`, `@` bị trung hòa hoàn toàn; footer chứa `snapshot_id` và `COMPLIANCE_DISCLAIMER_VI`.
- [x] Sinh file 1 DN 20 kỳ `< 300ms` trong `BytesIO` (không ghi đĩa).

**Verification:**
- [x] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_export_tearsheet.py -q`
- [x] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/serving/exports`

**Dependencies:** `V3-01`, `V3-05`  
**Commit type:** `feat(serving)`  
**Files likely touched:**
- `pyproject.toml` *(pin `xlsxwriter==3.2.2` in `platform`)*
- `src/finintel/serving/exports/__init__.py`
- `src/finintel/serving/exports/excel_builder.py`
- `tests/unit/test_v3_export_tearsheet.py`

**Estimated scope:** Medium (4 files)

---

### Task V3-14: Expose Excel export API endpoints with lineage headers
**Description:** Tạo `src/finintel/serving/exports/router.py` cung cấp `GET /api/v3/exports/companies/{ticker}.xlsx` và `GET /api/v3/exports/screener.xlsx` (chặn trần `<= 2.000` dòng chống OOM), đính kèm `X-Snapshot-ID`, `Content-Disposition` an toàn và mount vào `api.py` (`<= +2` LOC).

**Acceptance criteria:**
- [x] Cả 2 endpoint trả đúng MIME `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `X-Snapshot-ID` và tên file chứa mã CK + snapshot rút gọn.
- [x] Mã CK không tồn tại trả `404` chuẩn sạch (không lộ traceback); giới hạn cứng `2.000` dòng trên Screener export.

**Verification:**
- [x] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_export_tearsheet.py tests/unit/test_layer5_fastapi_serving.py -q`
- [x] Constraints: `.\.venv\Scripts\python.exe scripts/check_constraints.py`

**Dependencies:** `V3-13`  
**Commit type:** `feat(serving)`  
**Files likely touched:**
- `src/finintel/serving/exports/router.py`
- `src/finintel/serving/api.py` *(<= +2 LOC)*
- `tests/unit/test_v3_export_tearsheet.py`

**Estimated scope:** Small (3 files)

---

### Task V3-15: Build ExportActionMenu and A4 print-ready institutional PDF Tear-Sheet route
**Description:** Xây dựng `frontend/src/components/export/ExportActionMenu.tsx` (tải `.xlsx` / `.csv` / in Tear-Sheet) và trang `/companies/[ticker]/tear-sheet` với CSS `@media print` chuẩn A4 (1–2 trang, Light Mode cứng khi in, đầy đủ 6 khối định lượng, nhãn rủi ro hợp chuẩn NĐ 88/2014 và tuyên bố miễn trừ).

**Acceptance criteria:**
- [x] Menu xuất file gắn gọn trên `CompanyHeader` và `/screener` không làm vỡ layout ở `375px` & `1366px`.
- [x] `/companies/[ticker]/tear-sheet` hiển thị đủ kết luận rủi ro `R1–R5`, chỉ số vs ngành, TreeSHAP, cờ đỏ `RF_01..RF_10`, định giá PIT, `snapshot_id` và `COMPLIANCE_DISCLAIMER_VI`.
- [x] `0` từ cấm NĐ 88/2014, `0` lỗi `axe-core` và `vitest` pass 100%.

**Verification:**
- [x] FE check & tests: `fnm exec --using=default -- npm.cmd --prefix frontend run check && fnm exec --using=default -- npm.cmd --prefix frontend run test:unit`
- [x] Constraints: `.\.venv\Scripts\python.exe scripts/check_constraints.py`

**Dependencies:** `V3-03`, `V3-14`  
**Commit type:** `feat(frontend)`  
**Files likely touched:**
- `frontend/src/components/export/ExportActionMenu.tsx`
- `frontend/src/app/companies/[ticker]/tear-sheet/page.tsx`
- `frontend/src/components/company/CompanyHeader.tsx`
- `frontend/tests/unit/export_tearsheet.test.tsx`

**Estimated scope:** Medium (4 files)

### Checkpoint 1B-2 & PHASE 1 RELEASE GATE (`v3.0.0-beta.1`)
- [x] Cả 5 module Giai đoạn 1 đã merge xanh 100% (`pytest -q`, `mypy --strict`, `ruff`, `check_constraints.py`, `frontend check`, `test:unit`, `test:e2e`).
- [x] Cập nhật `CHANGELOG.md` và gắn tag bất biến `v3.0.0-beta.1` (Public Beta sẵn sàng deploy lên OCI Always Free + Vercel Hobby + Cloudflare R2).

---

# PHASE 2 — WAVE 2 (3 Parallel Streams)

## Stream 2-1: `v3-early-warning-alerts` (`feat/v3-early-warning-alerts`)

### Task V3-16: Add alert schema migration and Polars snapshot diff detector
**Description:** Tạo migration `config/migrations/app_v3_002_alerts.sql` và module `src/finintel/alerts/detector.py` so sánh 2 snapshot liên tiếp bằng Polars để phát sinh 5 loại `AlertEvent` tất định (`NEW_FILING_PUBLISHED`, `RISK_TIER_UPGRADE`, `NEW_RED_FLAG_TRIGGERED`, `REGULATORY_EVENT_ISSUED`, `MERTON_DTD_BREACH`) có khóa `dedup_key` SHA-256.

**Acceptance criteria:**
- [ ] Phát hiện chính xác cả 5 loại biến động trên 2 snapshot giả lập ở `tmp_path` và trả `0` sự kiện thừa cho mã không đổi.
- [ ] Chạy lặp detector trên cùng cặp snapshot sinh `dedup_key` giống hệt 100% (idempotent).
- [ ] Nội dung `title_vi` tuân thủ 100% quy tắc NĐ 88/2014 & Luật CK (không khuyến nghị mua/bán).

**Verification:**
- [ ] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_early_warning_alerts.py -q`
- [ ] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/alerts/detector.py`

**Dependencies:** `V3-11`, `V3-08`  
**Commit type:** `feat(alerts)`  
**Files likely touched:**
- `config/migrations/app_v3_002_alerts.sql`
- `src/finintel/alerts/detector.py`
- `tests/unit/test_v3_early_warning_alerts.py`

**Estimated scope:** Small (3 files)

---

### Task V3-17: Implement idempotent multi-channel dispatcher with SSRF and HMAC guards
**Description:** Xây dựng `src/finintel/alerts/dispatcher.py` định tuyến `AlertEvent` khớp với Watchlist & `ConsentPurpose.ALERT_NOTIFICATIONS` qua In-App, **Telegram Bot (miễn phí không giới hạn)**, **Resend Free Daily Digest** (`< 80`/ngày) và **Outgoing Webhook** (ký `X-FinIntel-Signature: sha256=...`, chặn SSRF bằng `ssrf_guard`, retry backoff + dead-letter).

**Acceptance criteria:**
- [ ] Từ chối 100% Webhook trỏ IP private/loopback/metadata (`127.0.0.1`, `169.254.169.254`) hoặc redirect `3xx`.
- [ ] Không gửi cho tài khoản thiếu `ConsentPurpose.ALERT_NOTIFICATIONS`; không gửi trùng khi retry (`dedup_key` unique constraint).
- [ ] Chữ ký HMAC-SHA256 khớp raw bytes payload; Resend adapter tự gộp Daily Digest bảo vệ hạn mức `100` email/ngày.

**Verification:**
- [ ] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_early_warning_alerts.py -q`
- [ ] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/alerts/dispatcher.py`

**Dependencies:** `V3-16`  
**Commit type:** `feat(alerts)`  
**Files likely touched:**
- `src/finintel/alerts/dispatcher.py`
- `src/finintel/alerts/__init__.py`
- `tests/unit/test_v3_early_warning_alerts.py`

**Estimated scope:** Small (3 files)

---

### Task V3-18: Expose alerts API router and build Notification Center UI
**Description:** Tạo `src/finintel/alerts/router.py` (`/api/v3/alerts/notifications`, `/subscriptions`, `/webhooks/test`), mount vào `api.py` (`<= +2` LOC), và xây dựng trang `/alerts` + `NotificationBell.tsx` trên `AppShell`.

**Acceptance criteria:**
- [ ] Người dùng cấu hình được kênh nhận cảnh báo (In-App / Telegram / Email Digest / Webhook), bấm test webhook và đánh dấu đã đọc.
- [ ] Chuông thông báo hiển thị số unread chính xác, hoạt động hoàn chỉnh bằng bàn phím chuẩn WCAG 2.2 AA.

**Verification:**
- [ ] Backend tests: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_early_warning_alerts.py -q`
- [ ] FE check & tests: `fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types && fnm exec --using=default -- npm.cmd --prefix frontend run check && fnm exec --using=default -- npm.cmd --prefix frontend run test:unit`

**Dependencies:** `V3-17`  
**Commit type:** `feat(alerts,frontend)`  
**Files likely touched:**
- `src/finintel/alerts/router.py`
- `src/finintel/serving/api.py` *(<= +2 LOC)*
- `frontend/src/app/alerts/page.tsx`
- `frontend/src/components/alerts/NotificationBell.tsx`

**Estimated scope:** Medium (4 files)

### Checkpoint 2-1: After Tasks `V3-16..V3-18` (`v3-early-warning-alerts`)
- [ ] Toàn bộ test detector, dispatcher (SSRF/HMAC/idempotency), router và UI pass 100%.

---

## Stream 2-2: `v3-scaled-rag-and-billing` (`feat/v3-scaled-rag-and-billing`)

### Task V3-19: Add non-commercial Fair-Use Quota guard and Global Gemini Circuit Breaker
**Description:** Tạo migration `app_v3_003_fair_use_quota.sql` và gói `src/finintel/quota/` (`policies.py`, `guard.py`, `router.py`, `schemas.py`) áp đặt hạn mức sử dụng công bằng theo vai trò (`USER`: 15 RAG/ngày, 10 `.xlsx`/ngày; `RESEARCHER`: 50 RAG/ngày, 30 `.xlsx`/ngày) với đếm nguyên tử + Global Circuit Breaker ở `85%` Gemini Free Tier (không có code thanh toán).

**Acceptance criteria:**
- [ ] Test đồng thời 20 request với limit 15 → đúng 15 thành công, 5 trả `429` kèm `reset_at` (`00:00 Asia/Ho_Chi_Minh`).
- [ ] Cache HIT hoặc lỗi `5xx` tuyệt đối không trừ quota của người dùng.
- [ ] Global Circuit Breaker tự kích hoạt trả `503` thân thiện khi tổng hệ thống chạm `85%` ngân sách ngày.

**Verification:**
- [ ] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_scaled_rag_quota.py -q`
- [ ] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/quota tests/unit/test_v3_scaled_rag_quota.py`

**Dependencies:** `V3-10`, `V3-08`  
**Commit type:** `feat(quota)`  
**Files likely touched:**
- `config/migrations/app_v3_003_fair_use_quota.sql`
- `src/finintel/quota/policies.py`
- `src/finintel/quota/guard.py`
- `src/finintel/quota/router.py`
- `tests/unit/test_v3_scaled_rag_quota.py`

**Estimated scope:** Medium (5 files)

---

### Task V3-20: Build Text-first PyMuPDF batch PDF RAG pipeline with daily Vision cap
**Description:** Xây dựng `src/finintel/rag/batch_pipeline.py` tự động xếp hàng ưu tiên PDF Thuyết minh cho 100% DN `R4/R5` + 100% có cờ đỏ + Top 300 vốn hóa, định tuyến Text-first (`PyMuPDF` = `$0`), dedup theo `content_sha256` và dừng lịch sự ở trạng thái `DEFERRED_VISION_CAP` khi đạt `daily_vision_page_cap = 150`.

**Acceptance criteria:**
- [ ] Trang text sạch không gọi bất kỳ API Vision nào (`0` calls); PDF trùng `content_sha256` bỏ qua 100%.
- [ ] Chạm `daily_vision_page_cap` vẫn hoàn thành index các trang text sạch và ghi `rag_usage_ledger` (không crash Dagster job).

**Verification:**
- [ ] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_scaled_rag_quota.py tests/unit/test_rag*.py -q`
- [ ] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/rag/batch_pipeline.py`

**Dependencies:** `V3-19`  
**Commit type:** `feat(rag)`  
**Files likely touched:**
- `src/finintel/rag/batch_pipeline.py`
- `tests/unit/test_v3_scaled_rag_quota.py`

**Estimated scope:** Small (2 files)

---

### Task V3-21: Wire quota and PDPL consent guards into RAG router and add QuotaBadge UI
**Description:** Gắn `enforce_and_consume_quota("rag_query")` + kiểm tra `ConsentPurpose.CROSS_BORDER_LLM_RAG` (ẩn danh PII khỏi prompt) vào `src/finintel/rag/router.py`, mount `quota_router` vào `api.py` (`<= +2` LOC), và hiển thị `QuotaBadge.tsx` trên `DocumentInvestigationPanel`.

**Acceptance criteria:**
- [ ] Yêu cầu RAG không có `CROSS_BORDER_LLM_RAG` consent bị chặn lịch sự kèm hướng dẫn bật đồng ý trong `/profile`.
- [ ] `QuotaBadge.tsx` hiển thị chính xác lượt còn lại/ngày và thời điểm reset, `0` lỗi a11y.

**Verification:**
- [ ] Backend tests: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_scaled_rag_quota.py -q`
- [ ] FE check & tests: `fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types && fnm exec --using=default -- npm.cmd --prefix frontend run check && fnm exec --using=default -- npm.cmd --prefix frontend run test:unit`

**Dependencies:** `V3-19`, `V3-20`  
**Commit type:** `feat(rag,frontend)`  
**Files likely touched:**
- `src/finintel/rag/router.py`
- `src/finintel/serving/api.py` *(<= +2 LOC)*
- `frontend/src/components/quota/QuotaBadge.tsx`
- `frontend/src/components/rag/DocumentInvestigationPanel.tsx`

**Estimated scope:** Medium (4 files)

### Checkpoint 2-2: After Tasks `V3-19..V3-21` (`v3-scaled-rag-and-billing`)
- [ ] Quota race tests, Vision cap tests, PDPL consent tests và Frontend check xanh 100%.

---

## Stream 2-3: `v3-analyst-depth-and-ops-qa` (`feat/v3-analyst-depth-and-ops-qa`)

### Task V3-22: Add taxonomy-safe discrete QoQ growth, ICB L3 peer matcher and anomaly Z-drivers
**Description:** Tạo `src/finintel/analytics/qoq_and_peers.py` tính tăng trưởng `QoQ` cục bộ chỉ trên quý rời rạc liên tiếp (trả `NULL` nếu vượt biên `TT200 → TT99`), hàm gợi ý 4 đối thủ cùng `icb_code_level3` quy mô tương đương (fallback L2 khi `< 5` mã), và hàm trích top 3 tỷ số có `|sector_z|` lớn nhất cho ngoại lai đa biến Top 5%.

**Acceptance criteria:**
- [ ] `QoQ` trả `NULL` (`MIXED_TAXONOMY`) ở quý chuyển giao Q4/2025 → Q1/2026 và khi mẫu số `<= 0`.
- [ ] Peer matcher chọn đúng 4 mã gần nhất về `market_cap_vnd` (fallback `BS_TOTAL_ASSETS`) cùng ngành L3, tự động fallback L2 kèm cờ `peer_basis = "ICB_LEVEL_2"`.

**Verification:**
- [ ] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_analyst_ops.py -q`
- [ ] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/analytics/qoq_and_peers.py`

**Dependencies:** `V3-05`  
**Commit type:** `feat(analytics)`  
**Files likely touched:**
- `src/finintel/analytics/qoq_and_peers.py`
- `tests/unit/test_v3_analyst_ops.py`

**Estimated scope:** Small (2 files)

---

### Task V3-23: Add INTERNAL_OPS immutable revision QA queue and issuer lifecycle API
**Description:** Tạo migration `app_v3_004_ops_qa.sql` và gói `src/finintel/ops/` cung cấp API gợi ý peer, anomaly Z-drivers, hàng đợi duyệt `audit_quarantine` (chỉ chấp nhận nạp bản đính chính `revision_no + 1` kèm `source_url`/`raw_file_sha256`, cấm tuyệt đối UPDATE đè số cũ) và theo dõi vòng đời mã niêm yết (`INTERNAL_OPS` only).

**Acceptance criteria:**
- [ ] `USER` / `RESEARCHER` gọi `/api/v3/ops/*` nhận `403 Forbidden`.
- [ ] Duyệt revision tạo `revision_no = 2` giữ nguyên `revision_no = 1` bất biến và ghi đầy đủ vào `user_audit_logs`.

**Verification:**
- [ ] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_analyst_ops.py -q`
- [ ] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/ops`
- [ ] Constraints: `.\.venv\Scripts\python.exe scripts/check_constraints.py`

**Dependencies:** `V3-11`, `V3-22`  
**Commit type:** `feat(ops)`  
**Files likely touched:**
- `config/migrations/app_v3_004_ops_qa.sql`
- `src/finintel/ops/qa_service.py`
- `src/finintel/ops/router.py`
- `src/finintel/ops/schemas.py`
- `src/finintel/serving/api.py` *(<= +2 LOC)*

**Estimated scope:** Medium (5 files)

---

### Task V3-24: Build interactive DuPontTreeChart, Peer Auto-Suggest, local QoQ and Ops QA UI
**Description:** Tạo `DuPontTreeChart.tsx` (cây 5/6 nhân tố bấm mở `SourceDrawer`), nút *"Gợi ý 4 đối thủ cùng ngành"* trên `/compare`, hiển thị `QoQ` cục bộ kèm cảnh báo mùa vụ Q4 trên `FinancialTable`/`GrowthComboChart` (không đụng thanh kỳ toàn cục `M-04`), và màn hình `/ops/qa` cho `INTERNAL_OPS`.

**Acceptance criteria:**
- [ ] Click từng nút trên `DuPontTreeChart` mở đúng `SourceDrawer`; đạt WCAG 2.2 AA và dùng token màu `echarts-theme.ts` (không tím/cyan).
- [ ] Nút gợi ý trên `/compare` nạp tự động 4 mã cùng ngành; thanh kỳ toàn cục vẫn khóa chỉ `TTM` vs `Annual` (`M-04`).

**Verification:**
- [ ] FE check & tests: `fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types && fnm exec --using=default -- npm.cmd --prefix frontend run check && fnm exec --using=default -- npm.cmd --prefix frontend run test:unit`
- [ ] Constraints: `.\.venv\Scripts\python.exe scripts/check_constraints.py`

**Dependencies:** `V3-23`  
**Commit type:** `feat(frontend)`  
**Files likely touched:**
- `frontend/src/components/charts/DuPontTreeChart.tsx`
- `frontend/src/components/company/RatiosTab.tsx`
- `frontend/src/app/compare/page.tsx`
- `frontend/src/app/ops/qa/page.tsx`
- `frontend/tests/unit/analyst_ops.test.tsx`

**Estimated scope:** Medium (5 files)

### Checkpoint 2-3 & PHASE 2 RELEASE GATE (`v3.0.0` GA)
- [ ] Cả 3 nhánh Wave 2 merge sạch, toàn bộ `AGENTS.md` §3 + Playwright E2E + `wcag22aa` + `CWV` PASS 100%.
- [ ] Cập nhật `CHANGELOG.md` và gắn tag bất biến `v3.0.0` (Free Cloud GA).

---

# PHASE 3 — WAVE 3 (3 Parallel Streams)

## Stream 3-1: `v3-b2b-commercial-api` (`feat/v3-b2b-commercial-api`)

### Task V3-25: Add Open Research API key lifecycle, fail-closed data gate and Valkey rate limiter
**Description:** Tạo migration `app_v3_005_b2b_api.sql` và gói `src/finintel/api_gateway/` quản lý API Key `fi_res_*` (lưu `SHA-256`, `hmac.compare_digest`), khóa mặc định `FININTEL_ENABLE_RAW_REDISTRIBUTION=False` (chỉ mở `read:derived_risk`), và Valkey sliding-window rate limiter (`30 req/phút`, `1.000 req/ngày/key`).

**Acceptance criteria:**
- [ ] Key hợp lệ truy cập được `read:derived_risk` (`200`), bị chặn `403` khi gọi raw BCTC nếu cờ redistribution là `False`, thu hồi key lập tức trả `401`.
- [ ] Vượt ngưỡng rate limit trả `429` kèm `Retry-After` dương và `X-RateLimit-*` headers; `0` log plaintext API key.

**Verification:**
- [ ] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_b2b_api.py -q`
- [ ] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/api_gateway tests/unit/test_v3_b2b_api.py`

**Dependencies:** `V3-18`, `V3-19`  
**Commit type:** `feat(api-gateway)`  
**Files likely touched:**
- `config/migrations/app_v3_005_b2b_api.sql`
- `src/finintel/api_gateway/keys.py`
- `src/finintel/api_gateway/rate_limiter.py`
- `src/finintel/api_gateway/router.py`
- `src/finintel/serving/api.py` *(<= +2 LOC)*

**Estimated scope:** Medium (5 files)

---

### Task V3-26: Build standalone Python Research SDK with ETag caching and HMAC verify
**Description:** Tạo gói độc lập `sdk/python/` (`finintel-sdk`) chỉ phụ thuộc `httpx` & `pydantic`, cung cấp client đồng bộ/bất đồng bộ hỗ trợ `If-None-Match` (`304`), retry `429/5xx` theo `Retry-After`, và helper `verify_webhook_signature()`.

**Acceptance criteria:**
- [ ] Unit tests với `httpx.MockTransport` xác nhận ETag caching, retry backoff và phát hiện payload webhook bị chỉnh sửa.
- [ ] `mypy --strict sdk/python/src` và `ruff check sdk` đạt `0` lỗi.

**Verification:**
- [ ] Tests & lint: `.\.venv\Scripts\python.exe -m ruff check sdk && .\.venv\Scripts\python.exe -m mypy --strict sdk/python/src && .\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_b2b_api.py -q`

**Dependencies:** `V3-25`  
**Commit type:** `feat(sdk)`  
**Files likely touched:**
- `sdk/python/pyproject.toml`
- `sdk/python/src/finintel_sdk/client.py`
- `sdk/python/src/finintel_sdk/models.py`
- `sdk/python/README.md`

**Estimated scope:** Medium (4 files)

---

### Task V3-27: Build /developers portal for Research API keys, usage and webhooks
**Description:** Tạo trang `/developers` cho vai trò `RESEARCHER` & `INTERNAL_OPS` tạo/xoay/thu hồi API Key (hiện secret đúng 1 lần kèm nút copy), xem biểu đồ usage ngày, đăng ký webhook và đoạn mã mẫu Python SDK / cURL.

**Acceptance criteria:**
- [ ] Secret key chỉ hiển thị duy nhất 1 lần ngay sau khi tạo và không thể đọc lại sau refresh trang.
- [ ] Trang `/developers` chặn gọn người dùng chưa có vai trò `RESEARCHER` với hướng dẫn đăng ký duyệt nghiên cứu; `0` lỗi `check` & `vitest`.

**Verification:**
- [ ] FE check & tests: `fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types && fnm exec --using=default -- npm.cmd --prefix frontend run check && fnm exec --using=default -- npm.cmd --prefix frontend run test:unit`

**Dependencies:** `V3-25`, `V3-26`  
**Commit type:** `feat(frontend)`  
**Files likely touched:**
- `frontend/src/app/developers/page.tsx`
- `frontend/src/types/api.generated.ts`
- `frontend/tests/unit/developers_portal.test.tsx`

**Estimated scope:** Small (3 files)

### Checkpoint 3-1: After Tasks `V3-25..V3-27` (`v3-b2b-commercial-api`)
- [ ] API gateway, SDK và `/developers` pass 100% tests; `FININTEL_ENABLE_RAW_REDISTRIBUTION` xác nhận mặc định `False`.

---

## Stream 3-2: `v3-private-issuer-and-hitl` (`feat/v3-private-issuer-and-hitl`)

### Task V3-28: Build VAS TT200/TT99 multi-column Excel parser with ZIP-bomb guards
**Description:** Ghim `openpyxl==3.1.5` trong group `platform` (lazy-import), tạo migration `app_v3_006_private_hitl.sql` và xây dựng `src/finintel/private_credit/vas_excel_parser.py` cung cấp file mẫu Excel VAS chuẩn TT200/TT99 và parse bảng BS/IS/CF đa kỳ kèm phòng thủ file upload (`<= 15 MB`, giải nén `<= 50 MB`, chặn `.xlsm`/macro/symlink).

**Acceptance criteria:**
- [ ] Parse chính xác 100% mã số VAS chuẩn từ fixture `.xlsx` 3 bảng 2 năm trong `tmp_path`.
- [ ] Từ chối file `.xlsm`, ZIP-bomb, vượt `15 MB` hoặc bảng lệch cân bằng QG1 kèm thông báo dòng lệch cụ thể.

**Verification:**
- [ ] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_private_hitl.py -q`
- [ ] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/private_credit/vas_excel_parser.py`

**Dependencies:** `V3-11`, `V3-23`  
**Commit type:** `feat(private-credit)`  
**Files likely touched:**
- `pyproject.toml` *(pin `openpyxl==3.1.5` in `platform`)*
- `config/migrations/app_v3_006_private_hitl.sql`
- `src/finintel/private_credit/vas_excel_parser.py`
- `tests/unit/test_v3_private_hitl.py`

**Estimated scope:** Medium (4 files)

---

### Task V3-29: Wire isolated private-issuer scoring and HITL false-positive triage API
**Description:** Xây dựng `src/finintel/private_credit/scorer.py` + `router.py` tái sử dụng 100% Polars analytics/QG1 hiện có để chấm điểm DN tư nhân ở chế độ `ACCOUNTING_ONLY_MODE` (cô lập hoàn toàn khỏi DuckLake công khai), cùng `src/finintel/hitl/` ghi nhận & thống kê phản hồi chuyên gia False Positive (không bao giờ ghi đè nhãn ML).

**Acceptance criteria:**
- [ ] Chấm điểm DN tư nhân chạy `< 3s`, xuất đầy đủ 38 chỉ số + DuPont + Cờ đỏ + Stress Test + gắn cờ `ACCOUNTING_ONLY_MODE`, và `0` ghi vào DuckLake / `data/`.
- [ ] `POST /api/v3/hitl/feedback` và `GET /api/v3/hitl/summary` (`INTERNAL_OPS`) hoạt động độc lập, không chạm bảng ML training labels.

**Verification:**
- [ ] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_private_hitl.py -q`
- [ ] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/private_credit src/finintel/hitl`
- [ ] Constraints: `.\.venv\Scripts\python.exe scripts/check_constraints.py`

**Dependencies:** `V3-28`  
**Commit type:** `feat(private-credit,hitl)`  
**Files likely touched:**
- `src/finintel/private_credit/scorer.py`
- `src/finintel/private_credit/router.py`
- `src/finintel/hitl/service.py`
- `src/finintel/hitl/router.py`
- `src/finintel/serving/api.py` *(<= +2 LOC)*

**Estimated scope:** Medium (5 files)

---

### Task V3-30: Build /private-scoring workbench and RedFlagChecklist HITL feedback UI
**Description:** Xây dựng trang `/private-scoring` (`RESEARCHER` / `INTERNAL_OPS`: tải mẫu VAS → upload → xem trước số liệu & kiểm tra QG1 → xem kết quả rủi ro + xóa hồ sơ) và nút *"Phản hồi chuyên gia"* gọn trên `RedFlagChecklist.tsx`.

**Acceptance criteria:**
- [ ] Luồng Upload → Preview QG1 → Score → Delete dossier hoạt động mượt, hiển thị rõ cảnh báo `ACCOUNTING_ONLY_MODE` bằng tiếng Việt sạch.
- [ ] Chuyên gia gửi được phản hồi HITL ngay trên từng cờ đỏ trong `RedFlagChecklist.tsx`; `0` lỗi `check` & `vitest`.

**Verification:**
- [ ] FE check & tests: `fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types && fnm exec --using=default -- npm.cmd --prefix frontend run check && fnm exec --using=default -- npm.cmd --prefix frontend run test:unit`

**Dependencies:** `V3-29`  
**Commit type:** `feat(frontend)`  
**Files likely touched:**
- `frontend/src/app/private-scoring/page.tsx`
- `frontend/src/components/financial/RedFlagChecklist.tsx`
- `frontend/src/types/api.generated.ts`
- `frontend/tests/unit/private_hitl.test.tsx`

**Estimated scope:** Medium (4 files)

### Checkpoint 3-2: After Tasks `V3-28..V3-30` (`v3-private-issuer-and-hitl`)
- [ ] Parser bảo mật, chấm điểm cô lập và HITL triage xanh 100%; xác nhận `0` ô nhiễm dữ liệu công khai.

---

## Stream 3-3: `v3-fx-stress-and-bond-signals` (`feat/v3-fx-stress-and-bond-signals`)

### Task V3-31: Add Extension Port 4 corporate_bond_signals schema and collector
**Description:** Tạo `src/finintel/ports/bonds.py` (Extension Port 4 nằm ngoài `MANAGED_MART_TABLES` để `publish_snapshot()` không bao giờ `DROP TABLE` theo `B-03`) và `src/finintel/ingestion/bond_signals_collector.py` thu thập dữ liệu trái phiếu doanh nghiệp thật (HNX Cbonds) kèm `source_url`, `payload_sha256` và `ssrf_guard`.

**Acceptance criteria:**
- [ ] Schema `corporate_bond_signals` & `fx_debt_exposure` validate chặt `source_url` + `payload_sha256` (Zero-Mock).
- [ ] Test xác nhận `DuckLakeStore.publish_snapshot()` bảo toàn nguyên vẹn bảng Port 4 sau khi xoay snapshot.

**Verification:**
- [ ] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_fx_stress_bonds.py tests/unit/test_layer3_ducklake_storage.py -q`
- [ ] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/ports/bonds.py src/finintel/ingestion/bond_signals_collector.py`

**Dependencies:** `V3-01`, `V3-05`  
**Commit type:** `feat(ports,ingestion)`  
**Files likely touched:**
- `src/finintel/ports/bonds.py`
- `src/finintel/ingestion/bond_signals_collector.py`
- `tests/unit/test_v3_fx_stress_bonds.py`

**Estimated scope:** Small (3 files)

---

### Task V3-32: Extend IMF stress test with deterministic FX shock and bond API router
**Description:** Xây dựng `src/finintel/analytics/stress_fx.py` và thêm trường optional `fx_depreciation_pct: float = 0.0` vào `StressScenario` (tính lỗ chênh lệch tỷ giá chỉ khi có `net_fx_debt_vnd` thật, trả `NOT_DISCLOSED` khi thiếu — tuyệt đối không bịa tỷ lệ USD), cùng `src/finintel/serving/routers/bonds_v3.py` (`GET /api/v3/companies/{ticker}/bond-signals`).

**Acceptance criteria:**
- [ ] `fx_depreciation_pct = 0.0` cho kết quả giống hệt 100% Stress Test IMF V2.0 (`0` regression).
- [ ] `fx_depreciation_pct = 0.10` tính đúng `fx_revaluation_loss_vnd` cho DN có công bố nợ ngoại tệ và trả `fx_coverage_status = "NOT_DISCLOSED"` (không `NaN`) cho DN chưa công bố.

**Verification:**
- [ ] Tests pass: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_fx_stress_bonds.py tests/unit/test_layer4_forensic_and_stress.py -q`
- [ ] Static checks: `.\.venv\Scripts\python.exe -m mypy --strict src/finintel/analytics/stress_fx.py src/finintel/serving/routers/bonds_v3.py`

**Dependencies:** `V3-31`  
**Commit type:** `feat(analytics,serving)`  
**Files likely touched:**
- `src/finintel/analytics/stress_fx.py`
- `src/finintel/analytics/stress_test.py`
- `src/finintel/serving/routers/bonds_v3.py`
- `src/finintel/serving/api.py` *(<= +2 LOC)*
- `tests/unit/test_v3_fx_stress_bonds.py`

**Estimated scope:** Medium (5 files)

---

### Task V3-33: Add FX shock control in /stress-test and BondMaturityCard component
**Description:** Bổ sung thanh trượt cú sốc tỷ giá `fx_depreciation_pct` (đồng bộ URL chia sẻ, kèm chú thích phạm vi công bố nợ ngoại tệ) trên `/stress-test` và tạo `BondMaturityCard.tsx` hiển thị áp lực đáo hạn trái phiếu 12 tháng so với tiền mặt trên hồ sơ doanh nghiệp.

**Acceptance criteria:**
- [ ] Thay đổi `fx_depreciation_pct` trên `/stress-test` đồng bộ vào URL và so sánh song song được giữa kịch bản A vs B.
- [ ] `BondMaturityCard.tsx` hiển thị rõ dư nợ, đáo hạn 12T/tiền mặt, cờ chậm trả (hoặc trạng thái sạch *"Không có dư nợ trái phiếu lưu hành"*), `0` lỗi WCAG 2.2 AA & `vi-VN` formatting.

**Verification:**
- [ ] FE check & tests: `fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types && fnm exec --using=default -- npm.cmd --prefix frontend run check && fnm exec --using=default -- npm.cmd --prefix frontend run test:unit`
- [ ] Constraints: `.\.venv\Scripts\python.exe scripts/check_constraints.py`

**Dependencies:** `V3-32`  
**Commit type:** `feat(frontend)`  
**Files likely touched:**
- `frontend/src/app/stress-test/page.tsx`
- `frontend/src/components/company/BondMaturityCard.tsx`
- `frontend/src/components/company/OverviewTab.tsx`
- `frontend/tests/unit/fx_bonds.test.tsx`

**Estimated scope:** Medium (4 files)

### Checkpoint 3-3 & FINAL RELEASE GATE (`v3.1.0`)
- [ ] Toàn bộ 11/11 module (`33/33` tasks) đã hoàn thành, merge xanh và xóa toàn bộ nhánh/worktree tạm.
- [ ] Toàn bộ bộ kiểm chứng `AGENTS.md` §3 + `frontend run test:e2e` (`wcag22aa` + `CWV`) PASS 100%.
- [ ] Cập nhật `CHANGELOG.md` và gắn tag bất biến `v3.1.0`.
