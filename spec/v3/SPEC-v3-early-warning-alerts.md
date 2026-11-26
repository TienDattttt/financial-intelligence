# Spec: `v3-early-warning-alerts` — Hệ Thống Cảnh Báo Sớm Tự Động — V3.0

> **Module ID:** `v3-early-warning-alerts`  
> **Wave / Build Order:** Phase 2 — **Wave 2** (chạy song song với `v3-scaled-rag-and-billing`, `v3-analyst-depth-and-ops-qa`)  
> **Depends On:** `v3-identity-and-workspace`, `v3-infra-ha-storage`  
> **Consumers:** `v3-b2b-commercial-api`, End-users (Watchlist Retention Engine)  
> **MoSCoW / Audit Refs:** `#51` Hệ thống Cảnh báo Sớm Tự động (Automated Early Warning Alerts).

---

## 1. Objective

Xây dựng động cơ cảnh báo chủ động theo sự kiện (Event-Driven Alerting Engine) biến FinIntel từ công cụ tra cứu thụ động thành hệ thống giám sát rủi ro liên tục:
1. **Phát hiện thay đổi tất định (Snapshot Diff Detector):** Khi Dagster publish snapshot mới (`prev_snapshot_id → new_snapshot_id`) hoặc cập nhật `regulatory_events`, tự động so sánh để phát sinh 5 loại sự kiện rủi ro chuẩn hóa cho từng mã:
   - `NEW_FILING_PUBLISHED`: Có BCTC quý/năm mới (kèm chênh lệch tự lập vs kiểm toán nếu có).
   - `RISK_TIER_UPGRADE`: Nhảy bậc rủi ro lên cao hơn (đặc biệt `R1..R3 → R4/R5`).
   - `NEW_RED_FLAG_TRIGGERED`: Phát sinh cờ đỏ mới trong `RF_01..RF_10` (ưu tiên cao cho `RF_10_POST_AUDIT_PROFIT_DOWNGRADE`).
   - `REGULATORY_EVENT_ISSUED`: Sự kiện pháp lý sở GDCK (Cảnh báo, Kiểm soát, Hạn chế GD, Đình chỉ, Hủy NY, Ý kiến kiểm toán Trái ngược/Từ chối).
   - `MERTON_DTD_BREACH`: `merton_dtd` giảm xuống dưới ngưỡng cấu trúc cấu hình (mặc định `< 1.5`).
2. **Định tuyến đa kênh chống trùng lặp (Idempotent Fan-out):** Khớp sự kiện với `watchlists` & quy tắc nhận thông báo của người dùng (`ConsentPurpose.ALERT_NOTIFICATIONS`), gửi qua **In-App Notification Center**, **Email digest/instant**, **Telegram Bot** hoặc **Outgoing Webhook** (ký `HMAC-SHA256`, SSRF guard bắt buộc!).
3. **Chống bão thông báo (Deduplication & Cooldown):** Mỗi `(user_id, ticker, event_type, period_end_date_or_event_id)` chỉ phát tối đa 1 lần qua khóa idempotency trong PostgreSQL `finintel_app`.

---

## 2. Tech Stack

- **Backend (`Python 3.11`):** `polars==1.35.2` (so sánh snapshot), `psycopg[binary]==3.3.9`, `httpx==0.28.1` (Telegram/Webhook delivery qua `ssrf_guard`), `dagster==1.13.30` (sensor kích hoạt fan-out sau khi snapshot publish), `fastapi==0.165.0`.
- **Database:** Mở rộng schema trong `finintel_app` (`alert_rules`, `alert_events`, `alert_deliveries`).
- **Frontend (`Node 24+`):** `next==16.4.6`, `react==19.2.4`, `@tanstack/react-query==^5.90.0`.

---

## 3. Commands

```powershell
# Backend
.\.venv\Scripts\python.exe scripts/check_constraints.py
.\.venv\Scripts\python.exe -m ruff format --check src scripts tests
.\.venv\Scripts\python.exe -m ruff check src scripts tests
.\.venv\Scripts\python.exe -m mypy --strict src/finintel/alerts tests/unit/test_v3_early_warning_alerts.py
.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_early_warning_alerts.py -q

# Frontend
fnm exec --using=default -- npm.cmd --prefix frontend run gen:api-types
fnm exec --using=default -- npm.cmd --prefix frontend run check
fnm exec --using=default -- npm.cmd --prefix frontend run test:unit
```

---

## 4. Project Structure & File Ownership

| Path | Action | Responsibility |
|---|---|---|
| `config/migrations/app_v3_002_alerts.sql` | Create | DDL cho `alert_subscriptions`, `market_alert_events`, `user_notifications`, `webhook_deliveries` |
| `src/finintel/alerts/detector.py` | Create (`<= 350` LOC) | Polars diff giữa 2 snapshot (`prev` vs `curr`) phát sinh `AlertEvent` tất định |
| `src/finintel/alerts/dispatcher.py` | Create (`<= 400` LOC) | Fan-out đa kênh (In-App, Email adapter, Telegram, Webhook có HMAC-SHA256 + `ssrf_guard` + retry backoff + dead-letter) |
| `src/finintel/alerts/router.py` | Create (`<= 300` LOC) | API quản lý rule cảnh báo, hộp thư thông báo In-App, đánh dấu đã đọc, test webhook |
| `src/finintel/serving/api.py` | Modify (`<= 2` dòng) | Mount `alerts_router` |
| `frontend/src/app/alerts/page.tsx` & `frontend/src/components/alerts/NotificationBell.tsx` | Create | Trung tâm cảnh báo + Chuông thông báo unread trên `AppShell` + cấu hình kênh nhận |
| `tests/unit/test_v3_early_warning_alerts.py` | Create | Test diff 2 snapshot, idempotency 0 gửi trùng, SSRF block trên webhook URL nội bộ, HMAC verification |

---

## 5. Boundary Interfaces & Contracts

### 5.1. Canonical `AlertEvent` Contract (Cấp cho `v3-b2b-commercial-api` Webhooks)
```python
class AlertEventType(StrEnum):
    NEW_FILING_PUBLISHED = "NEW_FILING_PUBLISHED"
    RISK_TIER_UPGRADE = "RISK_TIER_UPGRADE"
    NEW_RED_FLAG_TRIGGERED = "NEW_RED_FLAG_TRIGGERED"
    REGULATORY_EVENT_ISSUED = "REGULATORY_EVENT_ISSUED"
    MERTON_DTD_BREACH = "MERTON_DTD_BREACH"

@dataclass(frozen=True)
class AlertEvent:
    dedup_key: str           # SHA-256(ticker|event_type|period_or_event_date|detail_code)
    snapshot_id: str
    ticker: str
    event_type: AlertEventType
    severity: Literal["INFO", "MEDIUM", "HIGH", "CRITICAL"]
    title_vi: str            # Không dùng từ cấm NĐ 88 / khuyến nghị mua-bán
    evidence: dict[str, Any] # Số liệu tất định + link nguồn
    detected_at: datetime
```

### 5.2. Alerts HTTP Endpoints (`/api/v3/alerts/*`)
- `GET /api/v3/alerts/notifications?unread_only=true&limit=50`
- `POST /api/v3/alerts/notifications/{id}/read` & `POST /api/v3/alerts/notifications/read-all`
- `GET/PUT /api/v3/alerts/subscriptions` (chọn Watchlist, loại sự kiện, kênh Email/Telegram/Webhook, tần suất Instant vs Daily Digest)
- `POST /api/v3/alerts/webhooks/test` (gửi payload mẫu có chữ ký `X-FinIntel-Signature: sha256=...`).

---

## 6. Code Style & Architectural Invariants

- **Bắt buộc kiểm tra SSRF cho mọi Outgoing Webhook URL:** Chặn toàn bộ IP private/loopback/link-local/metadata (`169.254.169.254`), chỉ cho phép `https://` cổng `443`, timeout cứng `<= 5s`, tối đa `3` lần retry exponential backoff.
- **Không gửi thông báo nếu thiếu `ConsentPurpose.ALERT_NOTIFICATIONS`** (Luật 91/2025/QH15).
- **Nội dung cảnh báo 100% tất định:** Không gọi LLM sinh câu cảnh báo; gắn kèm đường dẫn trực tiếp về `/companies/{ticker}` và miễn trừ trách nhiệm ở chân Email.

---

## 7. Testing Strategy

- **Unit (`pytest`):**
  - Tạo 2 snapshot giả lập trên `tmp_path`: DN A nhảy `R2 → R4`, DN B phát sinh `RF_10`, DN C không đổi → xác nhận `detector.py` phát đúng 2 `AlertEvent` và chạy lại lần 2 không sinh duplicate.
  - Kiểm tra Webhook SSRF: URL `http://127.0.0.1`, `https://169.254.169.254`, redirect `302` tới private IP đều bị từ chối.
  - Kiểm tra chữ ký `X-FinIntel-Signature` khớp HMAC-SHA256 của raw payload bytes.
- **Frontend (`vitest`):** Badge unread hiển thị đúng, thao tác đánh dấu đã đọc bằng bàn phím đạt WCAG 2.2 AA.

---

## 8. Boundaries

- **Always:** Tách quá trình gửi Email/Webhook ra worker nền (Dagster sensor hoặc async queue), tuyệt đối không block HTTP request của FastAPI.
- **Ask first:** Trước khi tích hợp kênh Zalo OA (đòi hỏi phê duyệt mẫu ZNS và chi phí riêng theo tin nhắn).
- **Never:** Không bắn cảnh báo giá khớp lệnh realtime; không gửi email khi người dùng đã rút consent hoặc xóa tài khoản.

---

## 9. Success Criteria

- [ ] Detector phát hiện chính xác 5 loại sự kiện rủi ro khi xoay snapshot và đạt `0%` duplicate trên lần chạy lặp lại.
- [ ] Kênh In-App, Email adapter, Telegram và Webhook (HMAC + SSRF guard) hoạt động với retry & dead-letter logging.
- [ ] Giao diện `/alerts` và chuông thông báo hoạt động mượt trên cả Light/Dark Mode, `0` lỗi `check`, `pytest`, `check_constraints.py`.

---

## 10. Git Worktree & Commit Contract

```powershell
git worktree add ../finintel-v3-early-warning-alerts -b feat/v3-early-warning-alerts main
```
- Tách atomic commits:
  1. `feat(alerts): add snapshot diff detector and finintel_app alert schema`
  2. `feat(alerts): add idempotent multi-channel dispatcher with SSRF and HMAC guards`
  3. `feat(alerts,frontend): expose alerts API router and notification center UI`

---

## 11. Out of Scope (Deferred)

- Mobile Push Notification (FCM/APNs) & Zalo ZNS (Deferred to V3.2).
- Cảnh báo biến động giá theo phút/giây trong phiên giao dịch (Not Doing).

## 12. Open Questions

- Xác nhận kênh gửi cảnh báo mặc định cho dự án phi thương mại: ưu tiên **In-App + Telegram Bot (miễn phí không giới hạn)** làm kênh instant chính, và dùng **Resend Free (`100` email/ngày)** cho **Daily Digest** gộp 1 email/ngày để không vượt hạn mức free.
