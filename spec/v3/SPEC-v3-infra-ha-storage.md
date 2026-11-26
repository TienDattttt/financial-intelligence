# Spec: `v3-infra-ha-storage` — Shared Object Storage (S3/MinIO), HA Multi-Replica & DR — V3.0

> **Module ID:** `v3-infra-ha-storage`  
> **Wave / Build Order:** Phase 1 — **Wave 1A** (chạy song song với `v3-compliance-and-mrm`, `v3-valuation-mart-and-screener`)  
> **Depends On:** `—`  
> **Consumers:** `v3-early-warning-alerts`, `v3-scaled-rag-and-billing`  
> **MoSCoW / Audit Refs:** Production Go-Live HA/DR (`shipping-and-launch`), giải quyết mâu thuẫn Single-Node disk vs Multi-Replica Serving, tách mạng Dagster/Grafana.

---

## 1. Objective

Nâng cấp hạ tầng từ Single-Node local-disk sang kiến trúc **High-Availability Production** sẵn sàng chịu tải nhiều người dùng đồng thời mà vẫn giữ gọn lõi Polars + DuckDB/DuckLake (không phình thành Hadoop/Spark):
1. **Shared Object Storage (S3 / MinIO):** Cho phép `DuckLakeStore` và `ParquetRepository` đọc/ghi Parquet snapshots trên Object Storage dùng chung qua DuckDB `httpfs` (giữ nguyên local filesystem fallback cho dev/offline test).
2. **Multi-Replica Stateless FastAPI + Cross-Replica Cache Sync:** Nhiều replica FastAPI chỉ-đọc chạy song song phía sau Reverse Proxy/WAF, đồng bộ con trỏ snapshot qua `public.finintel_active_snapshot` (PostgreSQL), tự động evict L1B cục bộ khi `snapshot_id` đổi và chia sẻ Valkey L2 cache + Prometheus `MultiProcessCollector`.
3. **Network Isolation & Security Hardening:** Tách hoàn toàn Dagster Webserver, Grafana, Prometheus, Tempo, PostgreSQL, Valkey vào internal network (chỉ truy cập qua VPN/Zero-Trust); áp đặt Rate-Limiting phân tầng (IP/route), Security Headers (CSP, HSTS, X-Frame-Options) và CORS theo danh sách origin cụ thể.
4. **Backup / Restore & Disaster Recovery (DR) + Staged Rollout:** Tự động hóa backup PostgreSQL (`ducklake_catalog`, `dagster_state`, `finintel_rag`, `finintel_app`) + S3 snapshot versioning với mục tiêu `RPO <= 1 giờ`, `RTO <= 30 phút`, kèm runbook rollback và đo RUM Core Web Vitals thực tế.

---

## 2. Tech Stack

- **Storage & Infra:** `MinIO` (tương thích AWS S3 / Cloudflare R2 / Viettel/VNPT S3), `pgvector/pgvector:pg16`, `valkey:8`, `caddy` hoặc `nginx` (reverse proxy + TLS + rate limit), `duckdb==1.4.2` (`httpfs` extension), `prometheus-client==0.26.0`.
- **Secrets:** Environment injection qua Docker Secrets / Vault / Cloud KMS (cấm file `.env` chứa secret thật trong git).

---

## 3. Commands

```powershell
# Backend unit & offline storage contract tests
.\.venv\Scripts\python.exe scripts/check_constraints.py
.\.venv\Scripts\python.exe -m ruff format --check src scripts tests
.\.venv\Scripts\python.exe -m ruff check src scripts tests
.\.venv\Scripts\python.exe -m mypy --strict src/finintel/storage/object_store.py tests/unit/test_v3_infra_ha_storage.py
.\.venv\Scripts\python.exe -m pytest tests/unit/test_v3_infra_ha_storage.py tests/unit/test_layer3_ducklake_storage.py tests/unit/test_layer5_fastapi_serving.py -q

# Docker production compose validation (syntax)
docker compose -f docker-compose.prod.yml config -q
```

---

## 4. Project Structure & File Ownership

| Path | Action | Responsibility |
|---|---|---|
| `src/finintel/storage/object_store.py` | Create (`<= 350` LOC) | Cấu hình S3/MinIO endpoint, credential resolver, DuckDB `CREATE SECRET` (TYPE S3), URI builder `s3://...` vs `file://...` |
| `src/finintel/storage/ducklake_store.py` | Modify (minimal) | Nhận `ObjectStoreConfig` optional, giữ 100% tương thích local disk mặc định |
| `src/finintel/serving/dependencies.py` | Modify (minimal) | Kích hoạt `PROMETHEUS_MULTIPROC_DIR` khi `workers > 1` & sync con trỏ snapshot từ Postgres cho multi-replica |
| `docker-compose.prod.yml` | Create | Stack HA: `proxy` (public), `api` (`replicas: 2`), `web` (`replicas: 2`), `minio`, `postgres`, `valkey`, `dagster` + `grafana` (internal network only) |
| `config/gateway/nginx.prod.conf` (hoặc `Caddyfile.prod`) | Create | TLS, CSP/HSTS headers, rate limiting, chặn truy cập công khai tới `/metrics`, Dagster, Grafana |
| `scripts/backup_restore_prod.py` | Create (`<= 300` LOC) | Script backup & verify restore PostgreSQL (`pg_dump --format=custom`) + kiểm tra checksum S3 snapshot |
| `docs/runbooks/production_ha.md` | Create | Sổ tay Staging → Canary → Production, Trigger rollback, RPO/RTO drill checklist |
| `tests/unit/test_v3_infra_ha_storage.py` | Create | Unit tests cấu hình S3 secret sanitization, multi-replica snapshot rollover L1B eviction, security headers |

---

## 5. Boundary Interfaces & Contracts

### 5.1. `ObjectStoreConfig` (Env / Settings Contract)
```python
@dataclass(frozen=True)
class ObjectStoreConfig:
    backend: Literal["local", "s3"] = "local"
    bucket: str | None = None
    prefix: str = "finintel/lakehouse"
    endpoint_url: str | None = None  # MinIO / VN S3-compatible
    region: str = "ap-southeast-1"
    use_ssl: bool = True
    # Access key / secret key đọc từ env FININTEL_S3_ACCESS_KEY_ID / FININTEL_S3_SECRET_ACCESS_KEY (không log)
```

### 5.2. Cross-Replica Snapshot Rollover Event (Valkey Pub/Sub / Pointer Poll)
- Khi Dagster chạy `publish_snapshot()` và cập nhật `public.finintel_active_snapshot`:
  - Mọi replica FastAPI đọc con trỏ hoạt động theo TTL ngắn (`<= 5s`) hoặc nhận tín hiệu Valkey channel `finintel:snapshot:rollover` chứa `{"snapshot_id": "...", "published_at": "..."}`.
  - Khi `snapshot_id` thay đổi, replica lập tức gọi `_evict_stale_l1b_caches_locked()` và xoay `ETag` / `X-Snapshot-ID`.

---

## 6. Code Style & Architectural Invariants

- Giữ nguyên **lazy-import** cho `psycopg`, `redis`, `opentelemetry`, `boto3` (nếu dùng) để `mvp-checks` chạy xanh 100% không cần `--group platform`.
- Không bao giờ ghi log chứa `FININTEL_S3_SECRET_ACCESS_KEY`, `DATABASE_URL` password hoặc token (bọc qua `SecretStr`/sanitizer).
- Không đổi hành vi `StateStore` (`state.sqlite` WAL vẫn là single-writer ingestion ledger tại Dagster worker theo ADR-01).

---

## 7. Testing Strategy

- **Unit (`pytest`, offline):**
  - Kiểm tra `ObjectStoreConfig` sinh câu lệnh DuckDB S3 secret đúng, reject credential rỗng khi `backend="s3"`, và sanitize hoàn toàn khi serialize log/repr.
  - Giả lập 2 instance `DashboardQueryService` (2 replica) cùng trỏ vào 1 active pointer: cập nhật snapshot từ `snap_1` → `snap_2` và xác nhận cả 2 replica tự evict L1B cache và trả `X-Snapshot-ID: snap_2`.
  - Kiểm tra Security Headers Middleware (CSP, HSTS, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, CORS không dùng `*`).
- **Smoke / DR Verification:** Dry-run `docker compose -f docker-compose.prod.yml config -q` và bài tập `backup_restore_prod.py --dry-run`.

---

## 8. Boundaries

- **Always:** Đảm bảo `backend="local"` vẫn là mặc định cho dev và toàn bộ test suite hiện tại; xác nhận `/health/live` & `/health/ready` trả `200` khi DB+Valkey+Storage sẵn sàng và `503` khi lỗi.
- **Ask first:** Trước khi chọn nhà cung cấp cloud S3/VN cụ thể hoặc thay đổi mạng Docker nội bộ hiện có trong `docker-compose.yml` (dev).
- **Never:** Không phơi cổng `3001` (Dagster), `5432` (Postgres), `6379` (Valkey), `9090` (Prometheus), `3002` (Grafana) ra internet trong `docker-compose.prod.yml`; không dùng `postgres:16-alpine` (giữ `pgvector/pgvector:pg16` theo `B-05`).

---

## 9. Success Criteria

- [ ] `docker-compose.prod.yml` pass `docker compose config -q`, tách rõ `public_net` (chỉ reverse proxy) và `internal_net` (API/Web/DB/Valkey/MinIO/Dagster/Monitoring).
- [ ] Test multi-replica chứng minh 2 instance phục vụ nhất quán sau snapshot rollover với `0` dữ liệu cũ (stale L1B).
- [ ] `scripts/backup_restore_prod.py` và `docs/runbooks/production_ha.md` đầy đủ quy trình backup, restore test, canary threshold và rollback `< 5 phút`.
- [ ] `0` lỗi `ruff`, `mypy --strict`, `check_constraints.py`, `pytest`.

---

## 10. Git Worktree & Commit Contract

```powershell
git worktree add ../finintel-v3-infra-ha-storage -b feat/v3-infra-ha-storage main
```
- Tách atomic commits:
  1. `feat(storage): add S3/MinIO ObjectStoreConfig and DuckDB httpfs adapter`
  2. `feat(serving): add cross-replica snapshot rollover sync and security headers`
  3. `chore(infra): add docker-compose.prod.yml, gateway config, backup script, and HA runbook`

---

## 11. Out of Scope (Deferred)

- Kubernetes / Multi-region active-active cluster (quá nặng cho giai đoạn đầu; single-region multi-replica Docker/ECS là đủ cho 10.000+ DAU).
- CDN vendor-specific Terraform modules.

## 12. Open Questions

- Xác nhận nhà cung cấp Cloud đặt tại Việt Nam (Viettel IDC / VNPT Cloud / FPT Cloud / VNG Cloud hoặc AWS Singapore kèm TIA) để tối ưu độ trễ và tuân thủ Luật 91/2025/QH15.
