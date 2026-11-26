# Spec: `v3-b2b-commercial-api` — Open Research & Developer API, Webhooks & Python SDK — V3.1

> **Module ID:** `v3-b2b-commercial-api` *(giữ nguyên ID ổn định theo Capability Map)*  
> **Wave / Build Order:** Phase 3 — **Wave 3** (chạy song song với `v3-private-issuer-and-hitl`, `v3-fx-stress-and-bond-signals`)  
> **Depends On:** `v3-early-warning-alerts`, `v3-scaled-rag-and-billing`  
> **Consumers:** Nhà nghiên cứu, giảng viên/sinh viên tài chính, nhà phát triển tích hợp dữ liệu rủi ro phi thương mại  
> **Quyết định đã khóa:** Dự án **phi thương mại (`$0/tháng`)** — mở API miễn phí có **API Key + Fair-Use Rate-Limit** để bảo vệ hạ tầng cloud free, mặc định chỉ phục vụ chỉ báo phái sinh (`read:derived_risk`) nhằm tôn trọng điều khoản nguồn dữ liệu gốc.

---

## 1. Objective

Đóng gói các endpoint phân tích đọc-chỉ của FinIntel và luồng `AlertEvent` thành **Open Research API & Python SDK** miễn phí, an toàn cho hạ tầng cloud free:
1. **API Key Lifecycle & Scoped Access:** Tạo/xoay/thu hồi API Key dạng `fi_res_<prefix>_<random>` (chỉ hiện plaintext 1 lần khi tạo, lưu `SHA-256` hash trong `finintel_app`), gắn phạm vi `read:derived_risk`, `write:webhooks`, `read:rag` (cấp miễn phí cho vai trò `RESEARCHER` & `INTERNAL_OPS`).
2. **Upstream Data Respect Gate (`fail-closed`):** Mặc định `FININTEL_ENABLE_RAW_REDISTRIBUTION=False` — API công khai chỉ xuất các chỉ báo phái sinh do FinIntel tự tính (`R1–R5`, `1Y PD`, TreeSHAP, `RF_01..RF_10`, Stress Test), không tái phân phối hàng loạt dữ liệu BCTC/giá gốc của bên thứ ba.
3. **Free-Tier Protecting Rate Limiting & Webhooks:** Sliding-Window trên Valkey/Upstash (mặc định `30 req/phút`, `1.000 req/ngày/key`, trả `X-RateLimit-*` & `ETag` khuyến khích cache client) + đăng ký Webhook nhận `AlertEvent` từ `v3-early-warning-alerts` (HMAC-SHA256 + SSRF guard).
4. **Python Research SDK & `/developers` Portal:** Thư viện `finintel-sdk` nhẹ (`httpx` + `pydantic`, tự tôn trọng `ETag`/`304` và backoff `429`) kèm trang `/developers` tạo key và mẫu code Python/Jupyter Notebook.

---

## 2. Tech Stack

- **Backend (`Python 3.11`):** `fastapi==0.165.0`, `pydantic==2.14.4`, `psycopg[binary]==3.3.9`, `redis==8.1.0`, `httpx==0.28.1`. Không thêm dependency mới.
- **SDK (`sdk/python/`):** Package Python độc lập chỉ phụ thuộc `httpx>=0.28` & `pydantic>=2.10`.
- **Frontend (`Node 24+`):** `next==16.4.6`, `react==19.2.4` cho trang `/developers`.

---

## 3. Commands

```powershell
# Backend
.\.venv\Scripts\python.exe scripts/check_constraints.py
.\.venv\Scripts\python.exe -m ruff format --check src scripts tests sdk
.\.venv\Scripts\python.exe -m ruff check src scripts tests sdk
.\.venv\Scripts\python.exe -m mypy --strict src/finintel/api_gateway sdk/python/src tests/unit/test_v3_b2b_api.py
.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_b2b_api.py -q

# Frontend
fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types
fnm exec --using=default -- npm.cmd --prefix frontend run check
fnm exec --using=default -- npm.cmd --prefix frontend run test:unit
```

---

## 4. Project Structure & File Ownership

| Path | Action | Responsibility |
|---|---|---|
| `config/migrations/app_v3_005_b2b_api.sql` | Create | DDL `api_keys`, `api_key_usage_daily`, `b2b_webhook_endpoints` |
| `src/finintel/api_gateway/` (`keys.py`, `rate_limiter.py`, `middleware.py`, `router.py`, `schemas.py`) | Create (mỗi file `< 350` LOC) | Xác thực `X-API-Key`, scope check, `FININTEL_ENABLE_RAW_REDISTRIBUTION` gate, Valkey sliding-window rate limiter, usage logger |
| `src/finintel/serving/api.py` | Modify (`<= 2` dòng) | Mount `api_gateway_router` |
| `sdk/python/` (`pyproject.toml`, `src/finintel_sdk/client.py`, `models.py`, `README.md`) | Create | Python SDK chuẩn hóa cho tổ chức (company risk, screener, stress test, webhook signature verify helper) |
| `frontend/src/app/developers/page.tsx` | Create (`<= 400` LOC) | Quản lý Research API Keys (`RESEARCHER` / `INTERNAL_OPS`), biểu đồ usage theo ngày, cấu hình Webhook & ví dụ cURL/Python |
| `tests/unit/test_v3_b2b_api.py` | Create | Test API Key hash/revoke, sliding-window rate limit, raw redistribution gate, SDK mock-transport retry & ETag |

---

## 5. Boundary Interfaces & Contracts

### 5.1. B2B API Authentication & Headers
- Header: `X-API-Key: fi_live_<8-char-prefix>_<32-byte-token>`
- Response headers chuẩn hóa: `X-Snapshot-ID`, `ETag`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, `X-FinIntel-License-Scope`.
- Lỗi chuẩn RFC 7807 / JSON nhất quán: `401` (invalid/revoked key), `403` (missing scope hoặc raw redistribution bị khóa bởi license flag), `429` (rate limit / monthly quota exceeded + `Retry-After`).

### 5.2. Management Endpoints (`/api/v3/b2b/*`)
- `POST /api/v3/b2b/keys` (trả plaintext secret **duy nhất 1 lần**), `GET /api/v3/b2b/keys` (chỉ trả prefix & metadata), `DELETE /api/v3/b2b/keys/{id}` (thu hồi tức thì + xóa cache Valkey).
- `GET /api/v3/b2b/usage?from=...&to=...`
- `POST/GET/DELETE /api/v3/b2b/webhooks` (kế thừa cơ chế HMAC-SHA256 + SSRF guard từ `v3-early-warning-alerts`).

---

## 6. Code Style & Architectural Invariants

- **Bảo mật API Key:** Dùng `secrets.token_urlsafe(32)`, so sánh hằng thời gian `hmac.compare_digest`, tuyệt đối không log plaintext key ở middleware hay `structlog`.
- **License Gate mặc định AN TOÀN (`fail-closed`):** `FININTEL_ENABLE_RAW_REDISTRIBUTION` mặc định `False` — chỉ trả `read:derived_risk` cho đến khi tổ chức cấu hình bật kèm mã hợp đồng license.
- **Versioning B2B:** Mọi breaking change trên `/api/v1/*`, `/api/v2/*`, `/api/v3/*` bắt buộc tăng MAJOR version và có cửa sổ deprecation tối thiểu 90 ngày (`deprecation-and-migration`).

---

## 7. Testing Strategy

- **Backend (`pytest`):**
  - Tạo key → gọi endpoint được cấp phép (`200`) → gọi endpoint raw BCTC khi `FININTEL_ENABLE_RAW_REDISTRIBUTION=False` (`403`) → thu hồi key → gọi lại ngay lập tức (`401`).
  - Rate limit: vượt ngưỡng `60 req/min` → trả `429` có `Retry-After` dương.
  - Python SDK test với `httpx.MockTransport`: xác nhận tự retry trên `503`/`429`, dùng `If-None-Match` cho `304`, và hàm `verify_webhook_signature()` chặn payload bị chỉnh sửa.

---

## 8. Boundaries

- **Always:** Ghi mọi thao tác tạo/thu hồi API Key vào `user_audit_logs`; chỉ cho phép `RESEARCHER` hoặc `INTERNAL_OPS` tạo key.
- **Ask first:** Trước khi mở `FININTEL_ENABLE_RAW_REDISTRIBUTION=True` trên môi trường production (phải có hợp đồng tái phân phối dữ liệu ký kết).
- **Never:** Không lưu plaintext API key trong DB/log/Valkey; không bỏ qua SSRF check cho B2B webhook URLs.

---

## 9. Success Criteria

- [ ] Tài khoản `RESEARCHER` / `INTERNAL_OPS` tạo, xoay, thu hồi API Key và xem thống kê usage trực tiếp trên `/developers`.
- [ ] Cổng `FININTEL_ENABLE_RAW_REDISTRIBUTION` chặn sạch truy cập BCTC/giá gốc khi chưa có license nhưng vẫn cho khai thác mượt nhóm chỉ báo rủi ro phái sinh (`read:derived_risk`).
- [ ] `sdk/python` cài đặt và chạy xanh 100% unit tests; `0` lỗi `ruff`, `mypy --strict`, `check_constraints.py`, `npm run check`.

---

## 10. Git Worktree & Commit Contract

```powershell
git worktree add ../finintel-v3-b2b-commercial-api -b feat/v3-b2b-commercial-api main
```
- Tách atomic commits:
  1. `feat(api-gateway): add API key lifecycle, license scope gate, and Valkey rate limiter`
  2. `feat(sdk): add standalone Python client SDK with ETag caching and HMAC verification`
  3. `feat(frontend): add /developers portal for API keys, usage charts, and webhooks`

---

## 11. Out of Scope (Deferred)

- GraphQL / WebSocket streaming API (Deferred to V4).
- Multi-tenant IP allowlist & mTLS client certificates cho ngân hàng lớn (Deferred to V3.2).

## 12. Open Questions

- Xác nhận tình trạng đàm phán bản quyền tái phân phối dữ liệu (Data Redistribution Rights) với nhà cung cấp feed để chốt gói API Phase 3 chỉ bán `Derived Risk Intelligence` hay bán cả `Full Financial Statements`.
