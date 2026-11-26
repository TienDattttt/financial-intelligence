# Spec: `v3-infra-ha-storage` — Hạ Tầng Cloud Free (`$0/tháng`) Phi Thương Mại & Backup — V3.0

> **Module ID:** `v3-infra-ha-storage`  
> **Wave / Build Order:** Phase 1 — **Wave 1A** (chạy song song với `v3-compliance-and-mrm`, `v3-valuation-mart-and-screener`)  
> **Depends On:** `—`  
> **Consumers:** `v3-early-warning-alerts`, `v3-scaled-rag-and-billing`  
> **Quyết định đã khóa:** Dự án **phi thương mại**, mục tiêu chi phí hạ tầng **`$0/tháng`** (Always Free Cloud Stack, không dùng paid cluster).

---

## 1. Objective

Đóng gói hạ tầng triển khai công khai (go-live production) cho FinIntel V3.0 với **chi phí `$0/tháng`** mà vẫn đảm bảo tốc độ mượt từ Việt Nam, HTTPS chuẩn, bảo mật mạng nội bộ và sao lưu an toàn:

### Kiến Trúc Khuyến Nghị Số 1 — All-in-One Always Free (`0` Cold-Start, Chạy Đủ Dagster)
| Thành phần | Dịch vụ Cloud Free (Xác minh 2026) | Hạn mức Free thực tế | Vai trò trong FinIntel |
|---|---|---|---|
| **Frontend (Next.js 16)** | **Vercel Hobby** *(chuyên cho dự án phi thương mại)* | `$0`, CDN toàn cầu, tự động CI/CD từ GitHub | Chạy `frontend/` với độ trễ thấp, không tốn RAM backend |
| **DNS / WAF / TLS** | **Cloudflare Free** | `$0`, DDoS protection, SSL, WAF rules | Che IP gốc backend, chặn bot/rate-limit ở tầng Edge |
| **Backend + DB + Cache + Dagster** | **Oracle Cloud (OCI) Always Free** *(Region Singapore — ~35ms từ VN)* | **2 ARM Ampere A1 OCPUs, 12 GB RAM, 200 GB Block Disk** *(cập nhật giữa 2026)* | Chạy `docker-compose.free.yml`: `Caddy` (HTTPS) + `FastAPI` + `pgvector/pgvector:pg16` + `Valkey 8` + `Dagster Daemon` (dư sức vì cả Lakehouse `< 2 GB` RAM!) |
| **Object Storage & Backup** | **Cloudflare R2 Free** | **10 GB storage, 1M write / 10M read ops/tháng, `$0` egress** | Lưu bản sao DuckLake/Parquet snapshots, PDF curated & backup `pg_dump` tự động (tương thích S3 / DuckDB `httpfs`) |
| **Email & Cảnh báo** | **Resend Free** + **Telegram Bot API** | `3.000` email/tháng (`100`/ngày) + Telegram không giới hạn | Gửi email xác thực/quên mật khẩu + cảnh báo Watchlist tức thì |
| **LLM / Vision OCR** | **Google AI Studio Gemini Free Tier** | Free tier có rate-limit (`Text-first PyMuPDF` cắt >75% chi phí) | Phục vụ RAG Thuyết minh PDF với `daily_vision_page_cap` |

### Phương Án Dự Phòng Số 2 — Zero-Card Serverless Hybrid (`$0/tháng`, nếu không tạo được tài khoản OCI)
- **Frontend:** Vercel Hobby (`$0`)
- **FastAPI Read-Only:** **Hugging Face Spaces Docker CPU Free** (**2 vCPU, 16 GB RAM**, lưu ý có cold-start khi ngủ đông sau thời gian không có truy cập)
- **PostgreSQL + `pgvector`:** **Neon Free** (`1 GB` storage/project, `100 CU-hours/tháng`, hỗ trợ sẵn `pgvector` + `unaccent`)
- **Cache / Rate-Limit:** **Upstash Redis Free** (`250 MB`, `500.000` lệnh/tháng)
- **Parquet / DuckLake Storage:** **Cloudflare R2 Free** (`10 GB`, `$0` egress)
- **Scheduled ETL / Publish:** **GitHub Actions Free** (chạy cron hàng ngày/tuần thay Dagster Daemon 24/7, đẩy snapshot Parquet lên R2 & cập nhật pointer trên Neon).

---

## 2. Tech Stack

- **Storage Adapter:** `duckdb==1.4.2` (`httpfs` extension hỗ trợ đọc/ghi trực tiếp `s3://` trỏ tới **Cloudflare R2** và fallback `local` disk).
- **Reverse Proxy & Security:** `Caddy 2` (tự động cấp chứng chỉ Let's Encrypt HTTPS, nén gzip/zstd, security headers CSP/HSTS, rate-limit).
- **Container & DB:** `pgvector/pgvector:pg16` (giữ nguyên image Debian/glibc theo `B-05`, tương thích cả `linux/arm64` cho OCI Ampere A1!), `valkey:8` (`arm64` native).

---

## 3. Commands

```powershell
# Backend unit & offline storage contract tests
.\.venv\Scripts\python.exe scripts/check_constraints.py
.\.venv\Scripts\python.exe -m ruff format --check src scripts tests
.\.venv\Scripts\python.exe -m ruff check src scripts tests
.\.venv\Scripts\python.exe -m mypy --strict src/finintel/storage/object_store.py tests/unit/test_v3_infra_ha_storage.py
.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_infra_ha_storage.py tests/unit/test_layer3_ducklake_storage.py tests/unit/test_layer5_fastapi_serving.py -q

# Validate Free Cloud Docker Compose syntax
docker compose -f docker-compose.free.yml config -q
```

---

## 4. Project Structure & File Ownership

| Path | Action | Responsibility |
|---|---|---|
| `src/finintel/storage/object_store.py` | Create (`<= 350` LOC) | Cấu hình `ObjectStoreConfig` hỗ trợ **Cloudflare R2 (`s3://`)** và `local` fallback, sinh DuckDB `CREATE SECRET (TYPE R2 / S3)` an toàn (không lộ key) |
| `src/finintel/storage/ducklake_store.py` | Modify (minimal) | Chấp nhận `ObjectStoreConfig` optional, giữ `local` là mặc định cho dev/tests |
| `src/finintel/serving/dependencies.py` | Modify (minimal) | Sync active snapshot pointer từ Postgres/Neon + tự evict L1B cache + middleware Security Headers (CSP, HSTS, CORS cụ thể cho domain Vercel) |
| `docker-compose.free.yml` | Create | Stack tối ưu cho **OCI Always Free (`2 ARM OCPU / 12 GB RAM`)**: giới hạn cứng `mem_limit` từng container (`postgres: 2G`, `api: 2G`, `dagster: 3G`, `valkey: 256M`, `caddy: 128M`), chỉ mở port `80/443` qua Caddy, khóa Dagster/DB/Valkey trong `internal_net` |
| `config/gateway/Caddyfile.free` | Create | Auto-HTTPS, security headers, chặn public truy cập `/metrics`, proxy `/api/*` tới FastAPI |
| `scripts/backup_to_r2.py` | Create (`<= 250` LOC) | Backup tự động `pg_dump --format=custom` + manifest snapshot lên **Cloudflare R2** (retention 7 ngày giữ dung lượng `< 10 GB` free tier) |
| `.github/workflows/free-etl-fallback.yml` | Create (manual/cron disabled by default) | Workflow dự phòng cho Phương án 2 (Serverless Hybrid): chạy ETL & publish snapshot lên Cloudflare R2 + Neon không cần VM 24/7 |
| `docs/runbooks/free_cloud_deploy.md` | Create | Hướng dẫn từng bước deploy `$0/tháng` cho cả **Phương án 1 (OCI + Vercel + R2)** và **Phương án 2 (Vercel + HF Spaces + Neon + Upstash + R2)** |
| `tests/unit/test_v3_infra_ha_storage.py` | Create | Unit test Cloudflare R2 config/secret sanitization, snapshot rollover cache eviction, security headers & CORS |

---

## 5. Boundary Interfaces & Contracts

### 5.1. `ObjectStoreConfig` (Tối ưu cho Cloudflare R2 & Local)
```python
@dataclass(frozen=True)
class ObjectStoreConfig:
    backend: Literal["local", "r2", "s3"] = "local"
    bucket: str | None = None
    prefix: str = "finintel/lakehouse"
    endpoint_url: str | None = None  # https://<account_id>.r2.cloudflarestorage.com
    region: str = "auto"             # Cloudflare R2 chuẩn dùng region="auto"
    use_ssl: bool = True
    # Đọc credentials từ FININTEL_S3_ACCESS_KEY_ID / FININTEL_S3_SECRET_ACCESS_KEY (không bao giờ log)
```

### 5.2. Memory Budget Contract cho OCI Always Free (`12 GB RAM` cứng)
| Container | `mem_limit` | Ghi chú |
|---|---:|---|
| `caddy` (HTTPS Reverse Proxy) | `128 MB` | Public ingress duy nhất (`80/443`) |
| `valkey` (`valkey:8`) | `256 MB` | `maxmemory 192mb allkeys-lru` |
| `postgres` (`pgvector/pgvector:pg16`) | `2.0 GB` | 4 DB (`ducklake_catalog`, `dagster_state`, `finintel_rag`, `finintel_app`) |
| `api` (FastAPI `FININTEL_WORKERS=1`, multi-thread DuckDB) | `2.0 GB` | Không tốn RAM nhân bản worker; Prometheus `rate()` không bị reset (`M-01`) |
| `dagster-daemon` + `dagster-webserver` (internal only) | `3.5 GB` | Chạy nền thu thập/tính toán (`pipeline_write_pool=1`); còn dư `~4 GB` headroom cho OS & burst |

---

## 6. Code Style & Architectural Invariants

- **Đảm bảo build đa kiến trúc `linux/amd64` & `linux/arm64`** trong `Dockerfile` (OCI Always Free dùng CPU ARM64 Ampere A1 — kiểm tra toàn bộ wheel `polars`, `duckdb`, `xgboost`, `psycopg[binary]`, `pymupdf` đều có wheel `aarch64` sẵn).
- **Giữ nguyên lazy-import** cho `psycopg`, `redis`, `opentelemetry`, `boto3` để CI `mvp-checks` xanh 100%.
- **Bảo mật Zero-Cost:** Tuyệt đối không phơi port Postgres (`5432`), Valkey (`6379`), Dagster (`3001`) ra internet; truy cập Dagster qua SSH tunnel (`ssh -L 3001:localhost:3001 ubuntu@<oci-ip>`).

---

## 7. Testing Strategy

- **Unit (`pytest`, offline):**
  - Test `ObjectStoreConfig` cho `backend="r2"` (`region="auto"`, R2 endpoint format, secret sanitization trong log/repr).
  - Test `backup_to_r2.py --dry-run` tính toán vòng quay xóa backup cũ giữ tổng dung lượng dưới ngưỡng cảnh báo `8 GB` (chừa 20% buffer cho mức `10 GB` free của R2).
  - Test Security Headers & CORS chỉ cho phép domain Vercel cụ thể (không `*`).
- **Compose Check:** `docker compose -f docker-compose.free.yml config -q` pass sạch.

---

## 8. Boundaries

- **Always:** Đặt cảnh báo dung lượng ở mức `80%` của mọi free tier (R2 `8 GB / 10 GB`, Neon `800 MB / 1 GB`, Upstash `400k / 500k cmds`, Resend `80 / 100 emails/day`) để không bao giờ phát sinh phí ẩn hay ngắt dịch vụ bất ngờ.
- **Ask first:** Trước khi bật bất kỳ dịch vụ cloud nào yêu cầu tính phí tự động vượt free tier.
- **Never:** Không commit file `.env` hay API token vào git; không bỏ `mem_limit` trong `docker-compose.free.yml` (tránh OOM-killer giết Postgres trên VM 12 GB).

---

## 9. Success Criteria

- [ ] `docker-compose.free.yml` chạy trọn gói hệ thống trên máy `2 OCPU / 12 GB RAM` (`arm64` hoặc `amd64`) với tổng RAM thực tế `< 8 GB`, tổng chi phí cloud **`$0/tháng`**.
- [ ] `object_store.py` & `backup_to_r2.py` hỗ trợ đồng bộ Parquet/Backup với Cloudflare R2 chuẩn S3 (`0` phí egress).
- [ ] Runbook `docs/runbooks/free_cloud_deploy.md` đủ chi tiết để deploy được theo cả Phương án 1 (OCI + Vercel + R2) và Phương án 2 (Vercel + HF Spaces + Neon + Upstash + R2).
- [ ] `0` lỗi `ruff`, `mypy --strict`, `check_constraints.py`, `pytest`.

---

## 10. Git Worktree & Commit Contract

```powershell
git worktree add ../finintel-v3-infra-ha-storage -b feat/v3-infra-ha-storage main
```
- Tách atomic commits:
  1. `feat(storage): add Cloudflare R2/S3 ObjectStoreConfig and DuckDB httpfs adapter`
  2. `feat(serving): add snapshot rollover L1B sync and strict CORS/security headers`
  3. `chore(infra): add docker-compose.free.yml, Caddyfile, R2 backup script, and free-cloud runbook`

---

## 11. Out of Scope (Deferred)

- Multi-replica Kubernetes / AWS ECS trả phí / VN IDC thuê riêng (không phù hợp mục tiêu phi thương mại `$0/tháng`).

## 12. Open Questions

- Bạn đã có tài khoản **Oracle Cloud Always Free** (cần thẻ thanh toán để xác minh danh tính nhưng không trừ tiền) để dùng **Phương án 1**, hay ưu tiên triển khai ngay **Phương án 2 (Zero-Card: Vercel + HF Spaces + Neon + Upstash + Cloudflare R2 + GitHub Actions)**? *(Spec hỗ trợ song song cả 2 phương án.)*
