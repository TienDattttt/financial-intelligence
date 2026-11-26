# Spec: `v3-scaled-rag-and-billing` — Cost-Controlled Scaled PDF RAG & Fair-Use Free Quota — V3.0

> **Module ID:** `v3-scaled-rag-and-billing` *(giữ nguyên ID ổn định theo Capability Map)*  
> **Wave / Build Order:** Phase 2 — **Wave 2** (chạy song song với `v3-early-warning-alerts`, `v3-analyst-depth-and-ops-qa`)  
> **Depends On:** `v3-identity-and-workspace`, `v3-infra-ha-storage`  
> **Consumers:** `v3-b2b-commercial-api`, Người dùng cuối  
> **Quyết định đã khóa:** Dự án **phi thương mại (`$0/tháng`)** — **loại bỏ hoàn toàn cổng thanh toán trả phí (VNPay/PayOS/Stripe)**, chuyển trọng tâm sang **Fair-Use Free Quota & Free-Tier Budget Guard** để bảo vệ hạn mức miễn phí của Google AI Studio (Gemini Free Tier) và Cloudflare R2 (`10 GB`).

---

## 1. Objective

Mở rộng lợi thế lớn nhất của FinIntel (**Grounded PDF Footnote RAG** — hiện có 99 PDF / 88 DN trong `data/curated_pdfs/curated_pdfs_manifest.parquet`) lên quy mô toàn nhóm doanh nghiệp rủi ro & vốn hóa lớn mà **vẫn vận hành 100% ở mức chi phí `$0/tháng`**:
1. **Batch PDF RAG Pipeline tối ưu Free-Tier (`src/finintel/rag/batch_pipeline.py`):**
   - Ưu tiên tự động lập chỉ mục PDF Thuyết minh BCTC cho **100% DN nhóm `R4/R5` + 100% DN có cờ đỏ `RF_01..RF_10` + Top 300 vốn hóa**.
   - **Định tuyến Text-First (`PyMuPDF` = `$0`):** Mọi trang text sạch trích xuất 100% cục bộ không tốn 1 token nào; chỉ gửi trang scan/bảng vỡ sang **Gemini Free Tier** với trần cứng **`daily_vision_page_cap` (mặc định `150` trang/ngày)** và dedup tuyệt đối theo `content_sha256` để không bao giờ OCR lại cùng 1 PDF 2 lần.
   - Ẩn danh PII khỏi prompt và kiểm tra `ConsentPurpose.CROSS_BORDER_LLM_RAG` (Luật 91/2025/QH15) trước khi gọi API Gemini.
2. **Fair-Use Free Quota & Resource Guard (`src/finintel/quota/`):**
   - Thay thế billing thương mại bằng cơ chế **giới hạn sử dụng công bằng (Fair-Use Quota)** chống spam & chống cạn free tier:
     - `USER` (đã đăng nhập miễn phí): `15` lượt điều tra RAG/ngày, `10` lượt xuất `.xlsx`/ngày, `5` Watchlists × `50` mã.
     - `RESEARCHER` (tài khoản nghiên cứu được Ops duyệt miễn phí): `50` lượt RAG/ngày, `30` lượt xuất `.xlsx`/ngày, `20` Watchlists × `100` mã, được cấp Research API Key.
     - `INTERNAL_OPS`: không giới hạn (vẫn tuân thủ `global_daily_gemini_cap`).
   - Đếm quota nguyên tử qua Valkey/Upstash + bảng `usage_counters` trong `finintel_app`, kết hợp **Global Circuit Breaker** khi chạm `85%` hạn mức Gemini Free Tier trong ngày.

---

## 2. Tech Stack

- **Backend (`Python 3.11`):** `pymupdf==2.1`, `pgvector==0.5.1`, `psycopg[binary]==3.3.9`, `redis==8.1.0`, `dagster==1.13.30`, `fastapi==0.165.0` (lazy-import trong core). Không thêm thư viện thanh toán nào.
- **Database:** Bảng `usage_counters`, `rag_cost_ledger` trong `finintel_app`; vector embeddings giữ trong `finintel_rag` (`pgvector:pg16`, tối ưu kích thước chỉ mục để nằm gọn dưới `1 GB` nếu chạy Neon Free hoặc `2 GB` trên OCI).
- **Frontend (`Node 24+`):** `next==16.4.6`, `react==19.2.4` — hiển thị badge lượt dùng miễn phí còn lại trong ngày trên `DocumentInvestigationPanel` & `/profile`.

---

## 3. Commands

```powershell
# Backend
.\.venv\Scripts\python.exe scripts/check_constraints.py
.\.venv\Scripts\python.exe -m ruff format --check src scripts tests
.\.venv\Scripts\python.exe -m ruff check src scripts tests
.\.venv\Scripts\python.exe -m mypy --strict src/finintel/quota src/finintel/rag/batch_pipeline.py tests/unit/test_v3_scaled_rag_quota.py
.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_scaled_rag_quota.py tests/unit/test_rag*.py -q

# Frontend
fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types
fnm exec --using=default -- npm.cmd --prefix frontend run check
fnm exec --using=default -- npm.cmd --prefix frontend run test:unit
```

---

## 4. Project Structure & File Ownership

| Path | Action | Responsibility |
|---|---|---|
| `config/migrations/app_v3_003_fair_use_quota.sql` | Create | DDL `quota_policies`, `usage_counters`, `rag_usage_ledger` |
| `src/finintel/quota/` (`policies.py`, `guard.py`, `router.py`, `schemas.py`) | Create (mỗi file `< 300` LOC) | Chính sách Fair-Use theo role (`USER/RESEARCHER/INTERNAL_OPS`), kiểm tra & trừ quota nguyên tử + Global Gemini Circuit Breaker |
| `src/finintel/rag/batch_pipeline.py` | Create (`<= 450` LOC) | Hàng đợi ưu tiên PDF (`R4/R5` + cờ đỏ + Top 300), Text-first `PyMuPDF`, `content_sha256` dedup, `daily_vision_page_cap` |
| `src/finintel/rag/router.py` | Modify (minimal) | Gắn `enforce_quota("rag_query")` + `ConsentPurpose.CROSS_BORDER_LLM_RAG` |
| `src/finintel/serving/api.py` | Modify (`<= 2` dòng) | Mount `quota_router` |
| `frontend/src/components/quota/QuotaBadge.tsx` | Create (`<= 180` LOC) | Hiển thị lượt RAG / Export còn lại trong ngày, tự ẩn nút mượt khi đạt trần kèm thời điểm reset 00:00 UTC+7 |
| `tests/unit/test_v3_scaled_rag_quota.py` | Create | Test Text-first vs Vision routing, `daily_vision_page_cap`, atomic quota concurrency race, global circuit breaker |

---

## 5. Boundary Interfaces & Contracts

### 5.1. Fair-Use Quota Guard (`src/finintel/quota/guard.py`)
```python
def enforce_and_consume_quota(
    *,
    user: AuthenticatedUser,
    meter: Literal["rag_query", "xlsx_export", "api_call"],
    units: int = 1,
) -> QuotaReceipt:
    """Atomically check daily fair-use limit & global free-tier circuit breaker; raises HTTP 429 with reset_at."""
```

### 5.2. Quota Endpoints (`/api/v3/quota/*`)
- `GET /api/v3/quota/me` → trả lượt đã dùng / tối đa theo ngày cho `rag_query`, `xlsx_export`, `api_call` và thời điểm reset (`00:00 Asia/Ho_Chi_Minh`).
- `GET /api/v3/ops/quota/system-budget` (`INTERNAL_OPS`) → xem mức tiêu thụ `daily_vision_page_cap` và `global_daily_gemini_cap`.

---

## 6. Code Style & Architectural Invariants

- **Bảo toàn 100% luật Grounded RAG:** Bắt buộc trích dẫn `[Tài liệu, Trang X]` + nguyên văn excerpt và từ chối trung thực khi thiếu căn cứ; giữ `asyncio.Semaphore` bulkhead & Valkey cache (cache HIT **không trừ quota** người dùng!).
- **Cắt sạch code thanh toán:** Không tích hợp Stripe/VNPay/PayOS hay bảng giá thu tiền.
- **Bảo vệ ổ đĩa & Free Tier:** Lưu file PDF gốc nén trên Cloudflare R2 (không phình ổ đĩa DB); theo dõi dung lượng `finintel_rag` để luôn nằm dưới ngưỡng an toàn.

---

## 7. Testing Strategy

- **Unit & Concurrency (`pytest`):**
  - Test `batch_pipeline.py`: trang text sạch dùng `PyMuPDF` (`0` lần gọi Vision), PDF trùng `content_sha256` bỏ qua 100%, chạm `daily_vision_page_cap` dừng lịch sự ở trạng thái `DEFERRED_VISION_CAP`.
  - Test `enforce_and_consume_quota`: 20 request đồng thời với limit `15` → đúng 15 thành công, 5 trả `429`; cache HIT trả về kết quả không trừ thêm quota.
  - Test Global Circuit Breaker: khi tổng hệ thống chạm `85%` free-tier cap, trả `503` thân thiện kèm thời điểm mở lại.

---

## 8. Boundaries

- **Always:** Không trừ quota khi kết quả RAG trả về từ Valkey response cache hoặc khi hệ thống gặp lỗi `5xx`.
- **Ask first:** Trước khi thay đổi mô hình embedding (làm tăng kích thước vector trong `pgvector` và cần re-index toàn bộ `finintel_rag`).
- **Never:** Không thêm luồng thu tiền/checkout trong dự án phi thương mại; không gửi email/user_id sang API LLM.

---

## 9. Success Criteria

- [ ] `batch_pipeline.py` tự động mở rộng chỉ mục PDF Thuyết minh cho nhóm `R4/R5` + cờ đỏ + Top 300 trong giới hạn `$0/tháng` (tỷ lệ trang cần Vision `< 25%`).
- [ ] `enforce_and_consume_quota` chống spam nguyên tử (`0` race condition) và bảo vệ hạn mức miễn phí của Gemini/R2.
- [ ] `0` lỗi `ruff`, `mypy --strict`, `check_constraints.py`, `pytest`, `npm run check`.

---

## 10. Git Worktree & Commit Contract

```powershell
git worktree add ../finintel-v3-scaled-rag-and-billing -b feat/v3-scaled-rag-and-billing main
```
- Tách atomic commits:
  1. `feat(quota): add non-commercial fair-use quota guard and free-tier circuit breaker`
  2. `feat(rag): add text-first PyMuPDF batch pipeline with daily Vision cap and SHA-256 dedup`
  3. `feat(rag,frontend): wire quota guard, PDPL consent check, and QuotaBadge UI`

---

## 11. Out of Scope (Deferred)

- Thanh toán thương mại, hóa đơn điện tử, gói trả phí (Not Applicable — dự án phi thương mại).
- GPU self-hosted LLM cluster.

## 12. Open Questions

- Xác nhận mức trần mặc định `daily_vision_page_cap` (`150` trang/ngày) và `rag_queries_per_day` (`15` lượt/ngày cho `USER`) có phù hợp với tốc độ sử dụng thử nghiệm của bạn không.
