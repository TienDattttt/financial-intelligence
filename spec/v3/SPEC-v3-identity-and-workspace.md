# Spec: `v3-identity-and-workspace` — Domestic Auth/RBAC, Multi-Watchlist/Notes & Audit Log — V3.0

> **Module ID:** `v3-identity-and-workspace`  
> **Wave / Build Order:** Phase 1 — **Wave 1B** (chạy song song với `v3-export-and-tearsheet`, sau khi `v3-compliance-and-mrm` xong contract consent)  
> **Depends On:** `v3-compliance-and-mrm`  
> **Consumers:** `v3-early-warning-alerts`, `v3-scaled-rag-and-billing`, `v3-analyst-depth-and-ops-qa`, `v3-private-issuer-and-hitl`  
> **MoSCoW / Audit Refs:** `#1` Đăng ký, `#2` Đăng nhập/Phiên, `#3` Quên/Đổi mật khẩu, `#4` Hồ sơ cá nhân, `#5` RBAC, `#40` Multi-Watchlist & Ghi chú phân tích server-side, `#56` User/Admin Activity Audit Log.

---

## 1. Objective

Bổ sung lớp định danh, phân quyền và không gian làm việc cá nhân hóa lưu tại **PostgreSQL nội địa Việt Nam (`finintel_app`)**, biến FinIntel từ công cụ đọc-chỉ lưu `localStorage` thành nền tảng SaaS thương mại an toàn và tuân thủ Luật Bảo vệ dữ liệu cá nhân `91/2025/QH15`:
1. **Auth & Session an toàn (`#1`, `#2`, `#3`, `#4`):** Đăng ký (email verification + consent tường minh), đăng nhập, phiên HttpOnly Secure SameSite Cookie + Refresh Token Rotation (lưu hash token trong DB, hỗ trợ thu hồi phiên), quên/đổi mật khẩu (OTP/token hết hạn 15 phút), hồ sơ cá nhân & quyền xóa tài khoản/dữ liệu cá nhân (Right to Erasure).
2. **RBAC phân tầng SaaS (`#5`):** 4 vai trò rõ ràng: `FREE`, `PRO_ANALYST`, `ENTERPRISE_TEAM`, `INTERNAL_OPS` (vẫn giữ trải nghiệm xem công khai không cần đăng nhập cho trang cơ bản, nhưng yêu cầu đăng nhập cho Multi-Watchlist server, Ghi chú, Alerts, RAG quota cao và Export Pro).
3. **Server-Side Workspace (`#40`):** Chuyển `Watchlist` (hỗ trợ tối đa 10 danh mục với gói Pro, 1 danh mục với gói Free) + **Ghi chú phân tích có mốc thời gian (`ticker_notes`)** + **`Saved Screens`** sang PostgreSQL `finintel_app`, kèm luồng **tự động import từ `localStorage`** lần đầu đăng nhập để không mất dữ liệu người dùng cũ.
4. **User & Admin Activity Audit Logs (`#56`):** Ghi nhật ký bất biến (`append-only`) mọi sự kiện bảo mật và thao tác nhạy cảm (đăng nhập/thất bại, đổi mật khẩu, thay đổi quyền, xuất file, xóa tài khoản, thao tác Ops).

---

## 2. Tech Stack

- **Backend (`Python 3.11`):** `fastapi==0.165.0`, `pydantic==2.14.4`, `psycopg[binary]==3.3.9`, `redis==8.1.0` (rate-limit auth + session revocation), bổ sung 2 thư viện chuẩn bảo mật ghim cứng vào group `platform`: `argon2-cffi==23.1.0` (hash mật khẩu OWASP khuyến nghị) và `pyjwt==2.10.1` (hoặc opaque session token lưu SHA-256 trong Postgres — khuyến nghị **Opaque HttpOnly Session Cookie** cho Web App để thu hồi tức thì!).
- **Database:** Thêm database thứ 4 `finintel_app` trên cùng cụm `pgvector/pgvector:pg16` (tách biệt hoàn toàn khỏi `ducklake_catalog`, `dagster_state`, `finintel_rag`).
- **Frontend (`Node 24+`):** `next==16.4.6`, `react==19.2.4`, `zod==^4.1.0`, `@tanstack/react-query==^5.90.0`.

---

## 3. Commands

```powershell
# Backend
.\.venv\Scripts\python.exe scripts/check_constraints.py
.\.venv\Scripts\python.exe -m ruff format --check src scripts tests
.\.venv\Scripts\python.exe -m ruff check src scripts tests
.\.venv\Scripts\python.exe -m mypy --strict src/finintel/auth src/finintel/workspace tests/unit/test_v3_identity_workspace.py
.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_identity_workspace.py -q

# Frontend
fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types
fnm exec --using=default -- npm.cmd --prefix frontend run check
fnm exec --using=default -- npm.cmd --prefix frontend run test:unit
```

---

## 4. Project Structure & File Ownership

| Path | Action | Responsibility |
|---|---|---|
| `config/postgres-init/01-create-dbs.sql` | Modify | Thêm `CREATE DATABASE finintel_app;` |
| `config/migrations/app_v3_001_identity_workspace.sql` | Create | DDL cho `users`, `user_sessions`, `password_reset_tokens`, `user_consents`, `watchlists`, `watchlist_items`, `analyst_notes`, `saved_screens`, `user_audit_logs` |
| `src/finintel/auth/` (`service.py`, `passwords.py`, `dependencies.py`, `router.py`, `schemas.py`) | Create (mỗi file `< 400` LOC) | Đăng ký, đăng nhập, session cookie, password reset, profile, RBAC dependency `require_role(...)`, auth rate limiter |
| `src/finintel/workspace/` (`service.py`, `router.py`, `schemas.py`, `audit.py`) | Create (mỗi file `< 400` LOC) | CRUD Multi-Watchlist, Analyst Notes, Saved Screens, `record_audit_event()` |
| `src/finintel/serving/api.py` | Modify (`<= 4` dòng) | Mount `auth_router` và `workspace_router` (lazy-enabled khi `FININTEL_APP_PG_DSN` được cấu hình để không phá chế độ read-only offline test) |
| `frontend/src/app/auth/` (`login/page.tsx`, `register/page.tsx`, `reset-password/page.tsx`) | Create | Giao diện Auth chuẩn WCAG 2.2 AA, Light/Dark Mode, hộp tick đồng ý Luật 91/2025 |
| `frontend/src/app/profile/page.tsx` | Create | Hồ sơ cá nhân, quản lý phiên đăng nhập, quản lý đồng ý dữ liệu & nút xóa tài khoản |
| `frontend/src/app/watchlist/page.tsx` & `frontend/src/lib/watchlist-store.ts` | Modify | Chuyển sang server-backed khi đã đăng nhập + đa danh mục + ghi chú phân tích + tự đồng bộ từ `localStorage` lần đầu |
| `tests/unit/test_v3_identity_workspace.py` | Create | Unit/integration test Argon2id, brute-force rate limit, CSRF/Cookie flags, RBAC enforcement, Watchlist/Notes isolation, Audit Log |

---

## 5. Boundary Interfaces & Contracts

### 5.1. Auth & Profile HTTP Endpoints (`/api/v3/auth/*`)
- `POST /api/v3/auth/register` — Validate email/password (`>= 12` ký tự), bắt buộc `accepted_consent_version` (`ConsentPurpose.ACCOUNT_CORE`), trả `201`.
- `POST /api/v3/auth/login` — Set `HttpOnly; Secure; SameSite=Lax` cookie `finintel_sid`; khóa tạm sau `5` lần sai liên tiếp/15 phút theo `(ip, email_hash)`.
- `POST /api/v3/auth/logout` & `POST /api/v3/auth/logout-all` — Thu hồi phiên tức thì.
- `POST /api/v3/auth/password-reset/request` & `POST /api/v3/auth/password-reset/confirm` — Luôn trả `202` trung tính (chống user enumeration).
- `GET /api/v3/auth/me`, `PATCH /api/v3/auth/me`, `DELETE /api/v3/auth/me` (Right to Erasure + anonymize audit PII).

### 5.2. Workspace HTTP Endpoints (`/api/v3/workspace/*`)
- `GET/POST /api/v3/workspace/watchlists`, `PATCH/DELETE /api/v3/workspace/watchlists/{id}`
- `PUT/DELETE /api/v3/workspace/watchlists/{id}/items/{ticker}` (kèm `POST /api/v3/workspace/watchlists/import-local`)
- `GET/POST/PATCH/DELETE /api/v3/workspace/notes/{ticker}` (Ghi chú cá nhân kèm `period_end_date` tùy chọn & `updated_at`)
- `GET/POST/DELETE /api/v3/workspace/saved-screens`
- `GET /api/v3/audit/my-activity` (người dùng xem lịch sử đăng nhập) & `GET /api/v3/ops/audit-logs` (`INTERNAL_OPS` only).

### 5.3. Shared FastAPI Dependencies (Cấp cho các module downstream)
```python
class UserRole(StrEnum):
    FREE = "FREE"
    PRO_ANALYST = "PRO_ANALYST"
    ENTERPRISE_TEAM = "ENTERPRISE_TEAM"
    INTERNAL_OPS = "INTERNAL_OPS"

@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: UUID
    email: str
    role: UserRole
    consents: frozenset[ConsentPurpose]

def get_optional_user(...) -> AuthenticatedUser | None: ...
def require_authenticated_user(...) -> AuthenticatedUser: ...
def require_role(*roles: UserRole) -> Callable[..., AuthenticatedUser]: ...
```

---

## 6. Code Style & Architectural Invariants

- Toàn bộ thư viện `psycopg`, `argon2`, `redis` giữ **lazy-import** hoặc đặt trong module `auth`/`workspace` không được core analytics/ML import, bảo đảm CI `mvp-checks` (`--group ml --dev`) luôn xanh 100%.
- **Zero IDOR:** Mọi query tới `watchlists`, `analyst_notes`, `saved_screens` bắt buộc lọc `WHERE user_id = %(user_id)s` ở SQL layer (không kiểm tra sau khi đọc).
- **Bảo mật mật khẩu & phiên:** Hash bằng Argon2id; lưu `SHA-256(session_token)` trong DB (không lưu plaintext token); gắn bảo vệ CSRF (Origin/Referer check + `SameSite=Lax` + custom header `X-Requested-With` cho mutating requests).

---

## 7. Testing Strategy

- **Security & Auth tests (`pytest`):** Password strength, Argon2id verify, constant-time login response chống email enumeration, rate-limit lockout, session revocation, cross-user IDOR attempt trả `404`/`403`, audit log ghi đủ `actor_id`, `action`, `ip_hash`, `created_at`.
- **Workspace migration tests:** Import danh sách `localStorage` trùng lặp ticker không tạo duplicate, tôn trọng giới hạn gói `FREE` vs `PRO_ANALYST`.
- **Frontend tests (`vitest` + Playwright a11y):** Form đăng nhập/đăng ký đạt WCAG 2.2 AA (`aria-describedby` lỗi trường nhập, quản lý focus bàn phím).

---

## 8. Boundaries

- **Always:** Không làm gãy luồng khách ẩn danh (anonymous) hiện tại trên `/`, `/companies/[ticker]`, `/screener`, `/compare`, `/stress-test`, `/methodology`.
- **Ask first:** Trước khi thêm OAuth social login (Google/Apple) hoặc chọn dịch vụ gửi email transactional cụ thể (SES/Resend/SMTP nội địa).
- **Never:** Không lưu mật khẩu plaintext, không lưu token phiên plaintext trong DB, không lưu JWT trong `localStorage` (tránh XSS), không đẩy PII người dùng ra dịch vụ SaaS nước ngoài khi chưa hoàn tất TIA Điều 20 Luật 91/2025.

---

## 9. Success Criteria

- [ ] Luồng đầy đủ Đăng ký → Xác thực → Đăng nhập → Đổi/Quên mật khẩu → Xóa tài khoản chạy xanh với `0` lỗ hổng OWASP cơ bản (enumeration, IDOR, brute-force, XSS token leak).
- [ ] Người dùng đã đăng nhập tạo được nhiều Watchlist, ghi chú phân tích theo mã/kỳ, đồng bộ trên nhiều trình duyệt và tự import danh sách cũ từ `localStorage`.
- [ ] `require_role(...)` chặn đúng quyền giữa `FREE`, `PRO_ANALYST`, `ENTERPRISE_TEAM`, `INTERNAL_OPS`.
- [ ] `user_audit_logs` ghi vết 100% sự kiện auth & mutating actions; CI `mvp-checks` vẫn xanh không cần Postgres.

---

## 10. Git Worktree & Commit Contract

```powershell
git worktree add ../finintel-v3-identity-and-workspace -b feat/v3-identity-and-workspace main
```
- Tách atomic commits:
  1. `chore(deps,db): pin argon2-cffi in platform group and add finintel_app DDL`
  2. `feat(auth): implement session auth, RBAC dependencies, rate-limit, and audit logger`
  3. `feat(workspace): add PostgreSQL multi-watchlist, analyst notes, and saved screens API`
  4. `feat(frontend): add auth/profile pages and server-backed multi-watchlist with notes`

---

## 11. Out of Scope (Deferred)

- SSO doanh nghiệp (SAML 2.0 / Azure AD) cho khách hàng ngân hàng lớn (Deferred to V3.2).
- Không gian chia sẻ nhóm (Team Shared Watchlist/Comments — xem xét ở V3.2 sau khi ổn định single-user workspace).

## 12. Open Questions

- Xác nhận nhà cung cấp SMTP/Email transactional tại Việt Nam hoặc AWS SES cho luồng xác thực email & đặt lại mật khẩu.
