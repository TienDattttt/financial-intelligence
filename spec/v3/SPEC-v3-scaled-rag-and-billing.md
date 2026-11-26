# Spec: `v3-scaled-rag-and-billing` — Cost-Controlled Scaled PDF RAG & Billing/Quota — V3.0

> **Module ID:** `v3-scaled-rag-and-billing`  
> **Wave / Build Order:** Phase 2 — **Wave 2** (chạy song song với `v3-early-warning-alerts`, `v3-analyst-depth-and-ops-qa`)  
> **Depends On:** `v3-identity-and-workspace`, `v3-infra-ha-storage`  
> **Consumers:** `v3-b2b-commercial-api`, SaaS Monetization  
> **MoSCoW / Audit Refs:** `#61` mở rộng quy mô từ `99 PDF / 88 DN` lên toàn nhóm trọng yếu kèm kiểm soát Unit Economics/COGS + Billing/Quota/Usage Metering.

---

## 1. Objective

Biến lợi thế cạnh tranh lớn nhất của FinIntel (**Grounded PDF Footnote RAG** hiện có 99 PDF / 88 DN trong `data/curated_pdfs/curated_pdfs_manifest.parquet`) thành tính năng thương mại sinh lời mà không vỡ chi phí Vision OCR / LLM:
1. **Batch PDF RAG Pipeline kiểm soát COGS (`src/finintel/rag/batch_pipeline.py`):**
   - Tự động ưu tiên thu thập & lập chỉ mục PDF Thuyết minh BCTC kiểm toán/soát xét cho **100% DN nhóm `R4/R5` + 100% DN có cờ đỏ `RF_01..RF_10` + Top 300–500 vốn hóa**.
   - **Định tuyến tiết kiệm chi phí (Text-First Routing):** Mọi trang PDF đi qua `PyMuPDF` trước; chỉ chuyển sang Vision OCR khi mật độ ký tự thấp (trang scan/đóng dấu đỏ), lỗi font VNI/TCVN3 hoặc bảng bị vỡ — đi kèm **trần ngân sách ngày (`daily_vision_page_cap`)** và cache theo `content_sha256` để không bao giờ OCR lại cùng 1 PDF 2 lần.
   - Bắt buộc kiểm tra `ConsentPurpose.CROSS_BORDER_LLM_RAG` (Luật 91/2025/QH15) trước khi gửi truy vấn người dùng tới API LLM nước ngoài (và tự động ẩn danh `user_id`/`email` khỏi prompt).
2. **Billing, Plan Tiers & Usage Metering (`src/finintel/billing/`):**
   - Quản lý 3 gói thương mại (`FREE`, `PRO_ANALYST`, `ENTERPRISE_TEAM`), bảng hạn mức hàng tháng (`rag_queries_per_month`, `xlsx_exports_per_month`, `max_watchlists`, `max_watchlist_tickers`, `alert_channels`), đếm usage nguyên tử (Valkey + PostgreSQL ledger) và webhook thanh toán chuẩn (abstraction `PaymentProvider` hỗ trợ VNPay/PayOS/Stripe + idempotency key).

---

## 2. Tech Stack

- **Backend (`Python 3.11`):** Kế thừa `pymupdf==2.1`, `pgvector==0.5.1`, `psycopg[binary]==3.3.9`, `redis==8.1.0`, `dagster==1.13.30`, `fastapi==0.165.0`, `httpx==0.28.1` (lazy-import trong core).
- **Database:** Bảng billing/metering trong `finintel_app`; chunk/embedding giữ nguyên trong `finintel_rag` (`pgvector:pg16`, HNSW + `unaccent` FTS).
- **Frontend (`Node 24+`):** `next==16.4.6`, `react==19.2.4`, hiển thị thanh mức dùng (Quota meter) ngay trên `DocumentInvestigationPanel` và trang `/billing`.

---

## 3. Commands

```powershell
# Backend
.\.venv\Scripts\python.exe scripts/check_constraints.py
.\.venv\Scripts\python.exe -m ruff format --check src scripts tests
.\.venv\Scripts\python.exe -m ruff check src scripts tests
.\.venv\Scripts\python.exe -m mypy --strict src/finintel/billing src/finintel/rag/batch_pipeline.py tests/unit/test_v3_scaled_rag_billing.py
.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_scaled_rag_billing.py tests/unit/test_rag*.py -q

# Frontend
fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types
fnm exec --using=default -- npm.cmd --prefix frontend run check
fnm exec --using=default -- npm.cmd --prefix frontend run test:unit
```

---

## 4. Project Structure & File Ownership

| Path | Action | Responsibility |
|---|---|---|
| `config/migrations/app_v3_003_billing_quota.sql` | Create | DDL cho `plans`, `subscriptions`, `usage_counters`, `billing_transactions`, `rag_cost_ledger` |
| `src/finintel/billing/` (`plans.py`, `quota.py`, `webhooks.py`, `router.py`, `schemas.py`) | Create (mỗi file `< 350` LOC) | Định nghĩa gói, kiểm tra & trừ quota nguyên tử (`enforce_quota`), xử lý webhook thanh toán idempotent |
| `src/finintel/rag/batch_pipeline.py` | Create (`<= 450` LOC) | Bộ lập lịch & ưu tiên 300–500 DN, Text-first router, `content_sha256` dedup, `daily_vision_page_cap`, ghi `rag_cost_ledger` |
| `src/finintel/rag/router.py` | Modify (minimal) | Gắn dependency `enforce_quota("rag_query")` + kiểm tra `ConsentPurpose.CROSS_BORDER_LLM_RAG` |
| `src/finintel/serving/api.py` | Modify (`<= 2` dòng) | Mount `billing_router` |
| `frontend/src/app/billing/page.tsx` & `frontend/src/components/billing/QuotaBadge.tsx` | Create | Bảng giá minh bạch, trạng thái gói hiện tại, lịch sử sử dụng & thanh toán, nút nâng cấp |
| `tests/unit/test_v3_scaled_rag_billing.py` | Create | Test Text-first vs Vision routing, `daily_vision_page_cap` dừng an toàn, quota concurrency race, webhook signature & idempotency |

---

## 5. Boundary Interfaces & Contracts

### 5.1. Plan Entitlements Matrix (Cấp cho toàn bộ module SaaS & `v3-b2b-commercial-api`)
| Capability | `FREE` | `PRO_ANALYST` | `ENTERPRISE_TEAM` |
|---|---:|---:|---:|
| Lượt điều tra PDF RAG / tháng | `10` | `200` | `2.000` (hoặc custom) |
| Lượt xuất `.xlsx` / tháng | `5` | `Không giới hạn` | `Không giới hạn` |
| Số Watchlist / Mã mỗi Watchlist | `1 / 10` | `10 / 50` | `50 / 200` |
| Kênh cảnh báo sớm | `In-App` | `In-App + Email + Telegram` | `+ Webhook` |
| API Access (`v3-b2b-commercial-api`) | `Không` | `Không` | `Có (theo hợp đồng License)` |

### 5.2. Shared Quota Guard (`src/finintel/billing/quota.py`)
```python
def enforce_and_consume_quota(
    *,
    user: AuthenticatedUser,
    meter: Literal["rag_query", "xlsx_export", "api_call"],
    units: int = 1,
) -> QuotaReceipt:
    """Atomically check plan limit and increment monthly counter; raises HTTP 429 with reset_at on exhaustion."""
```

### 5.3. Billing HTTP Endpoints (`/api/v3/billing/*`)
- `GET /api/v3/billing/plans` (public catalog)
- `GET /api/v3/billing/subscription` (current plan + monthly quota usage)
- `POST /api/v3/billing/checkout` (tạo phiên thanh toán qua `PaymentProvider`)
- `POST /api/v3/billing/webhooks/{provider}` (xác thực chữ ký + idempotency key, tự cập nhật `users.role` & ghi `user_audit_logs`).

---

## 6. Code Style & Architectural Invariants

- **Bảo toàn 100% luật Grounded RAG của FinIntel:** Mọi câu trả lời RAG bắt buộc trích dẫn `[Tài liệu, Trang X]` + nguyên văn excerpt và từ chối trung thực khi thiếu căn cứ; giữ `asyncio.Semaphore` bulkhead & Valkey response cache; không bao giờ để LLM tự tính tỷ số tài chính.
- **Bảo vệ COGS cứng:** Nếu đạt `daily_vision_page_cap`, pipeline dừng các trang scan ở trạng thái `DEFERRED_VISION_CAP` (vẫn index sạch các trang text), ghi metric Prometheus `finintel_rag_vision_pages_total` và không gây crash job Dagster.
- **Cấm gửi PII người dùng sang LLM:** Chỉ gửi câu hỏi tài chính + mã CK + ngữ cảnh đoạn trích PDF tới `src/finintel/rag/_gemini.py`.

---

## 7. Testing Strategy

- **Unit & Concurrency (`pytest`):**
  - Test `batch_pipeline.py` trên fixture PDF text sạch vs PDF scan giả lập: xác nhận `0` lời gọi Vision cho trang text sạch, cache hit 100% khi lặp `content_sha256`, dừng đúng tại `daily_vision_page_cap`.
  - Test `enforce_and_consume_quota`: gửi 15 request đồng thời với limit 10 → đúng 10 thành công, 5 trả `429 Too Many Requests` kèm header `X-RateLimit-Remaining: 0`.
  - Test Webhook thanh toán: chữ ký sai → `401`; gửi trùng `event_id` 2 lần → chỉ cộng gói 1 lần (idempotent).
- **Frontend (`vitest`):** Hiển thị quota còn lại chính xác, cảnh báo lịch sự (không popup spam) khi hết lượt gói `FREE`.

---

## 8. Boundaries

- **Always:** Lưu rõ đơn vị tiền tệ (`VND`) và lịch sử giao dịch bất biến (`billing_transactions` append-only).
- **Ask first:** Trước khi chốt giá niêm yết VND cụ thể cho gói Pro/Enterprise và chọn cổng thanh toán thực (VNPay vs PayOS vs Stripe).
- **Never:** Không lưu số thẻ tín dụng/CVV trên DB FinIntel (chỉ dùng hosted checkout của cổng thanh toán đạt PCI-DSS); không bỏ qua kiểm tra `CROSS_BORDER_LLM_RAG` consent.

---

## 9. Success Criteria

- [ ] `batch_pipeline.py` lập chỉ mục tự động được nhóm ưu tiên (`R4/R5` + cờ đỏ + Top vốn hóa) với tỷ lệ trang cần gọi Vision `< 25%` và có bảng theo dõi chi phí `rag_cost_ledger`.
- [ ] `enforce_and_consume_quota` chặn chính xác vượt hạn mức ở mức nguyên tử (`0` race condition) và tích hợp vào `/api/v2/documents/{ticker}/investigate` & `/api/v3/exports/*`.
- [ ] Luồng checkout + webhook nâng cấp gói `FREE → PRO_ANALYST` hoạt động idempotent và ghi đầy đủ `user_audit_logs`.
- [ ] `0` lỗi `ruff`, `mypy --strict`, `check_constraints.py`, `pytest`, `npm run check`.

---

## 10. Git Worktree & Commit Contract

```powershell
git worktree add ../finintel-v3-scaled-rag-and-billing -b feat/v3-scaled-rag-and-billing main
```
- Tách atomic commits:
  1. `feat(billing): add plan matrix, atomic quota metering, and idempotent payment webhooks`
  2. `feat(rag): add text-first COGS-capped batch PDF RAG pipeline and cost ledger`
  3. `feat(rag,billing): wire quota and PDPL cross-border consent guards into investigate endpoint`
  4. `feat(frontend): add billing page and quota usage indicators`

---

## 11. Out of Scope (Deferred)

- Hóa đơn điện tử tự động (tích hợp MISA/VNPT e-Invoice — bổ sung khi có pháp nhân thương mại chính thức).
- Tự vận hành mô hình LLM open-weights on-premise (đánh giá ở V3.2 khi sản lượng truy vấn > 500.000 lượt/tháng).

## 12. Open Questions

- Xác nhận cổng thanh toán ưu tiên tại Việt Nam (`PayOS` VietQR cho chuyển khoản nhanh nội địa + `VNPay` hay `Stripe` cho thẻ quốc tế) và mức trần `daily_vision_page_cap` mong muốn (đề xuất ban đầu: `1.500` trang/ngày).
