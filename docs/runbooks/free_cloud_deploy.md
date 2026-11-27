# FinIntel V3.0 — Free Cloud Production Deployment Runbook (`$0/month`)

> **Scope:** Non-commercial public deployment for FinIntel V3.0 (Vietnam Corporate Financial Intelligence) at **`$0/month`** infrastructure cost, with strict security isolation, automated backups, and 80% free-tier safety guards.

---

## Option 1 (Recommended — `$0/month`, Zero Cold-Start, Full Dagster)

### Architecture Stack
| Component | Provider | Always Free Quota | Role |
|---|---|---|---|
| **Frontend (Next.js 16)** | **Vercel Hobby** | `$0`, global CDN, auto GitHub deploy | Serves `frontend/` without consuming backend RAM |
| **DNS / WAF / TLS** | **Cloudflare Free** | `$0`, Edge DDoS, SSL, WAF rate-limit | Masks OCI origin IP, protects API edge |
| **Backend + DB + Cache + Dagster** | **Oracle Cloud (OCI) Always Free** *(Singapore)* | **2 ARM Ampere A1 OCPUs, 12 GB RAM, 200 GB Block Disk** | Runs `docker-compose.free.yml` (`< 8 GB` `mem_limit` total) |
| **Object Storage & Backups** | **Cloudflare R2 Free** | **10 GB storage, 1M write / 10M read ops/mo, `$0` egress** | Stores Parquet snapshots & `pg_dump` backups via `s3://` |
| **Transactional Email & Alerts** | **Resend Free + Telegram Bot API** | `3,000` emails/mo (`100`/day) + unlimited Telegram | Auth emails + Daily Digest + Watchlist alerts |
| **LLM / Vision OCR** | **Google AI Studio Gemini Free Tier** | Rate-limited Free Tier (`Text-first PyMuPDF`) | Grounded PDF RAG with `daily_vision_page_cap = 150` |

### Step-by-Step Deployment (OCI + Vercel + Cloudflare R2)

1. **Provision Cloudflare R2 & DNS:**
   - Create R2 bucket `finintel-lakehouse` (set alert at **8 GB / 10 GB**).
   - Create an R2 API Token (Object Read & Write scoped strictly to `finintel-lakehouse`).
   - Record `<ACCOUNT_ID>` -> endpoint `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` (`region="auto"`).
   - Point `api.<yourdomain>` to the OCI Public IP through Cloudflare Proxy (orange cloud, SSL mode **Full (Strict)**).

2. **Provision OCI Always Free VM (Singapore `ap-singapore-1`):**
   - Shape: `VM.Standard.A1.Flex` (`2 OCPUs`, `12 GB RAM`, `Ubuntu 24.04 Minimal aarch64`, `100–200 GB` boot volume).
   - OCI Security List / UFW firewall: allow **ONLY** TCP `22` (restrict to admin IP), `80`, and `443`. **Never** open `5432` (Postgres), `6379` (Valkey), `8000` (FastAPI), or `3000` (Dagster).

3. **Configure Environment (`.env` — never commit to git):**
   ```env
   FININTEL_API_DOMAIN=api.yourdomain.com
   FININTEL_PG_USER=finintel
   FININTEL_PG_PASSWORD=<strong-random-32-char-secret>
   FININTEL_CORS_ORIGINS=https://finintel.vercel.app
   FININTEL_CORS_ALLOW_CREDENTIALS=1
   FININTEL_WORKERS=1
   FININTEL_OBJECT_STORE_BACKEND=r2
   FININTEL_OBJECT_STORE_BUCKET=finintel-lakehouse
   FININTEL_OBJECT_STORE_PREFIX=finintel/lakehouse
   FININTEL_R2_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
   FININTEL_S3_REGION=auto
   FININTEL_S3_ACCESS_KEY_ID=<r2-access-key-id>
   FININTEL_S3_SECRET_ACCESS_KEY=<r2-secret-access-key>
   ```

4. **Launch Services & Verify Budget (`< 8 GB` RAM):**
   ```bash
   docker compose -f docker-compose.free.yml config -q
   docker compose -f docker-compose.free.yml up -d --build
   docker stats --no-stream
   curl -fsS https://api.yourdomain.com/health/ready
   ```
   - Verify `/metrics` returns `403` publicly (`Caddyfile.free`).
   - Access Dagster Webserver strictly via SSH tunnel:
     ```bash
     ssh -L 3001:127.0.0.1:3000 ubuntu@<OCI_PUBLIC_IP>
     ```

5. **Deploy Frontend on Vercel Hobby:**
   - Root directory: `frontend`, Framework: Next.js 16.
   - Set `NEXT_PUBLIC_API_BASE_URL=https://api.yourdomain.com`.
   - Ensure `FININTEL_CORS_ORIGINS` on OCI matches the exact `https://...vercel.app` origin (never `*`).

6. **Automated Backups to Cloudflare R2 (Cron):**
   ```bash
   # Test offline retention & 8 GB budget first:
   uv run --no-sync python scripts/backup_to_r2.py --dry-run
   # Add daily 03:00 UTC cron on OCI host:
   # 0 3 * * * cd /opt/finintel && uv run --no-sync python scripts/backup_to_r2.py >> /var/log/finintel-backup.log 2>&1
   ```

---

## Option 2 (Fallback — Zero-Card Serverless Hybrid, `$0/month`)

Use this option if OCI Always Free ARM A1 capacity is temporarily unavailable in Singapore or no payment card is available for identity verification:

| Component | Free Serverless Service | Free Tier Guardrail (`80%` Alert) |
|---|---|---|
| **Frontend** | **Vercel Hobby** | Same Next.js 16 deployment |
| **API Serving** | **Hugging Face Spaces (Docker CPU Free)** | `2 vCPU / 16 GB RAM`; sleeps after inactivity (cold-start) |
| **PostgreSQL + pgvector** | **Neon Free** | `1 GB` storage (`800 MB` alarm), `100 CU-hours/mo`, `pgvector` + `unaccent` enabled |
| **L2 Cache / Rate-Limit** | **Upstash Redis Free** | `250 MB`, `500k` commands/mo (`400k` alarm) via `rediss://` |
| **Object Storage** | **Cloudflare R2 Free** | `10 GB` (`8 GB` alarm), `$0` egress |
| **Scheduled ETL** | **GitHub Actions (`.github/workflows/free-etl-fallback.yml`)** | Enable `schedule:` cron and set repository secrets |

### Option 2 Activation Steps
1. Create a Neon project, enable `vector` and `unaccent`, and run `config/postgres-init/01-create-dbs.sql` schemas.
2. Create an Upstash Redis database and copy its `rediss://...` URL into `FININTEL_VALKEY_URL`.
3. Configure GitHub Repository Secrets (`FININTEL_DUCKLAKE_PG_DSN`, `FININTEL_OBJECT_STORE_BUCKET`, `FININTEL_R2_ENDPOINT_URL`, `FININTEL_S3_ACCESS_KEY_ID`, `FININTEL_S3_SECRET_ACCESS_KEY`).
4. Trigger `.github/workflows/free-etl-fallback.yml` via `workflow_dispatch` (and uncomment `schedule:` for nightly runs).
5. Deploy the FastAPI `Dockerfile` to Hugging Face Spaces (`FININTEL_WORKERS=1`, `FININTEL_DUCKLAKE_CATALOG_DRIVER=postgres`, `FININTEL_OBJECT_STORE_BACKEND=r2`).

---

## Free-Tier 80% Monitoring & Rollback Plan

- **80% Budget Thresholds:**
  - Cloudflare R2: `8 GB / 10 GB` (`R2_WARN_BYTES` enforced in `scripts/backup_to_r2.py`).
  - Neon Postgres: `800 MB / 1 GB`.
  - Upstash Redis: `400,000 / 500,000` commands/month.
  - Resend Email: `80 / 100` emails/day.
- **Snapshot Rollback:**
  If a bad snapshot is published, roll back `public.finintel_active_snapshot` via `DuckLakeStore.rollback_snapshot("<prev_snap_id>")`. Both API workers automatically detect the pointer change via `SnapshotPointerSync`, evict L1B caches, and serve the restored snapshot within 1 second.
- **Database Restore from R2:**
  Download the latest `.dump` from `s3://<bucket>/finintel/lakehouse/backups/<stamp>/pg_primary.dump` and run `pg_restore --clean --if-exists --no-owner -d "$FININTEL_DUCKLAKE_PG_DSN" pg_primary.dump`.
