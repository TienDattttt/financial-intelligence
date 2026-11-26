# Spec: `v3-b2b-commercial-api` — B2B Commercial API Platform, Webhooks & Python SDK — V3.1

> **Module ID:** `v3-b2b-commercial-api`  
> **Wave / Build Order:** Phase 3 — **Wave 3** (chạy song song với `v3-private-issuer-and-hitl`, `v3-fx-stress-and-bond-signals`)  
> **Depends On:** `v3-early-warning-alerts`, `v3-scaled-rag-and-billing`  
> **Consumers:** Khách hàng B2B (Ngân hàng, CTCK, Quỹ đầu tư, FinTech tích hợp dữ liệu rủi ro)  
> **MoSCoW / Audit Refs:** `#57` RESTful API Trích xuất Dữ liệu thương mại + Cảnh báo rào cản pháp lý **Data Redistribution / Derived-Data License**.

---

## 1. Objective

Đóng gói 21 data endpoints đọc-chỉ hiện hữu của FinIntel cùng luồng sự kiện cảnh báo (`v3-early-warning-alerts`) thành sản phẩm **B2B Commercial API & Python SDK** (mô hình cạnh tranh trực tiếp với WiFeed API & FiinQuant):
1. **API Key Lifecycle & Scoped Access:** Tạo/xoay/thu hồi API Key dạng `fi_live_<prefix>_<random>` (chỉ hiển thị plaintext đúng 1 lần khi tạo, lưu `SHA-256` hash trong `finintel_app`), gắn phạm vi quyền rõ ràng (`read:derived_risk`, `read:raw_financials`, `write:webhooks`, `read:rag`).
2. **Data Redistribution License Gate (Bắt buộc về pháp lý):** Phân tách cứng 2 nhóm scope API:
   - `read:derived_risk` (điểm rủi ro `R1–R5`, `1Y PD`, TreeSHAP, 10 cờ đỏ `RF_01..RF_10`, Stress Test — **tài sản phái sinh sở hữu trí tuệ của FinIntel**).
   - `read:raw_financials` & `read:market_prices` (BCTC gốc, giá EOD — **chỉ mở cờ `FININTEL_ENABLE_RAW_REDISTRIBUTION=true` sau khi ký hợp đồng Data Redistribution License hợp lệ với nhà cung cấp dữ liệu gốc**).
3. **Rate Limiting, Usage Metering & Outgoing Risk Webhooks:** Giới hạn tốc độ Sliding-Window trên Valkey theo từng API Key (trả chuẩn `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`), đếm usage nối vào `v3-scaled-rag-and-billing`, và đăng ký B2B Webhook nhận sự kiện `AlertEvent` từ `v3-early-warning-alerts`.
4. **Python SDK & Developer Portal:** Đóng gói client nhẹ `finintel-sdk` (`httpx` + `pydantic`, tự retry `429/5xx`, hỗ trợ ETag caching) và trang `/developers` quản lý API Key, xem OpenAPI docs và usage dashboard.

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
| `frontend/src/app/developers/page.tsx` | Create (`<= 400` LOC) | Quản lý API Keys (`ENTERPRISE_TEAM`), biểu đồ usage theo ngày, cấu hình Webhook & ví dụ cURL/Python |
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

- **Always:** Ghi mọi thao tác tạo/thu hồi API Key vào `user_audit_logs`; chỉ cho phép `ENTERPRISE_TEAM` hoặc `INTERNAL_OPS` tạo key.
- **Ask first:** Trước khi mở `FININTEL_ENABLE_RAW_REDISTRIBUTION=True` trên môi trường production (phải có hợp đồng tái phân phối dữ liệu ký kết).
- **Never:** Không lưu plaintext API key trong DB/log/Valkey; không bỏ qua SSRF check cho B2B webhook URLs.

---

## 9. Success Criteria

- [ ] Khách hàng `ENTERPRISE_TEAM` tạo, xoay, thu hồi API Key và xem thống kê usage trực tiếp trên `/developers`.
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
