# Changelog

All notable changes to FinIntel are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2026-11-27

Phase 2 Free Cloud General Availability (GA) release adding deterministic early warning alerts, non-commercial fair-use quota & text-first scaled PDF RAG, and analyst depth & immutable Ops QA (`V3-16..V3-24`).

### Added
- **Early Warning Alerts & Multi-Channel Dispatch (`V3-16..V3-18`)**:
  - Added PostgreSQL alert schema migration (`config/migrations/app_v3_002_alerts.sql`) and Polars snapshot diff detector (`src/finintel/alerts/detector.py`) emitting 5 deterministic `AlertEventType` classes (`NEW_FILING_PUBLISHED`, `RISK_TIER_UPGRADE`, `NEW_RED_FLAG_TRIGGERED`, `REGULATORY_EVENT_ISSUED`, `MERTON_DTD_BREACH`) with SHA-256 `dedup_key` and NĐ 88/2014 compliant titles.
  - Added idempotent multi-channel dispatcher (`src/finintel/alerts/dispatcher.py`) enforcing `ConsentPurpose.ALERT_NOTIFICATIONS`, In-App inbox, Telegram Bot delivery, Resend Free Daily Digest quota protection (`<= 80` emails/day), and HMAC-SHA256 signed webhooks with SSRF/redirect guards.
  - Exposed `/api/v3/alerts/*` endpoints (`src/finintel/alerts/router.py`) and built `/alerts` notification center + `NotificationBell` on `AppShell`.
- **Fair-Use Free Quota & Text-First Scaled RAG (`V3-19..V3-21`)**:
  - Added `config/migrations/app_v3_003_fair_use_quota.sql` and `src/finintel/quota/` enforcing atomic daily fair-use limits (`USER`: 15 RAG / 10 `.xlsx`; `RESEARCHER`: 50 RAG / 30 `.xlsx`; `00:00 Asia/Ho_Chi_Minh` reset; zero payment/billing code) and an `85%` global Gemini Free Tier circuit breaker (`HTTP 503`).
  - Added text-first `PyMuPDF` batch PDF RAG pipeline (`src/finintel/rag/batch_pipeline.py`) prioritizing `100%` `R4/R5` + `100%` red-flag issuers + Top 300 market cap with `content_sha256` deduplication and graceful `DEFERRED_VISION_CAP` stop at `150` Vision pages/day.
  - Wired `CROSS_BORDER_LLM_RAG` PDPL consent checks, prompt PII anonymization, cache-hit/5xx quota refunds into `src/finintel/rag/router.py`, and added `QuotaBadge` to `DocumentInvestigationPanel`.
- **Analyst Depth & Immutable Ops QA (`V3-22..V3-24`)**:
  - Added taxonomy-safe discrete `QoQ` growth (`NULL` with `MIXED_TAXONOMY` across `TT200 -> TT99`), ICB Level 3/2 scale-matched peer suggester (4 peers), and Top-3 anomaly `|sector_z|` driver extractor (`src/finintel/analytics/qoq_and_peers.py`).
  - Added `config/migrations/app_v3_004_ops_qa.sql` and `src/finintel/ops/` exposing `/api/v3/companies/{ticker}/suggested-peers`, `/api/v3/companies/{ticker}/anomaly-drivers`, `INTERNAL_OPS`-only append-only `audit_quarantine` revision workflow (`revision_no + 1` with `source_url`/`raw_file_sha256`), and issuer lifecycle endpoints.
  - Built interactive `DuPontTreeChart` (`RatiosTab` -> `SourceDrawer`), 1-click Peer Auto-Suggest on `/compare`, local `QoQ` toggle with Q4 seasonal & taxonomy warnings (`M-04` global toggle untouched), and `/ops/qa` workbench.

### Fixed
- Eliminated `ValkeyBackedQuotaStore` split-brain by backing `get_subject_used`, `get_system_budget`, `record_global_gemini_call`, and `consume_vision_pages` with Valkey keys/Lua scripts across FastAPI and Dagster processes.
- Preserved intentionally empty `event_types` lists in `PostgresAlertStore._row_to_subscription` (falling back to defaults only on `NULL`/non-list DB values).
- Added `pytest.importorskip("argon2")` and `pytest.importorskip("fastapi")` guards in `tests/unit/test_v3_early_warning_alerts.py` for offline `mvp-checks` CI compatibility and synchronized combined Wave 2 OpenAPI schemas into `frontend/src/types/api.generated.ts`.

## [3.0.0-beta.1] - 2026-11-27

Phase 1 Public Beta release covering regulatory compliance & MRM gates, market-wide PIT valuation & expanded screener, `$0/month` Free Cloud infrastructure (OCI Always Free + Vercel Hobby + Cloudflare R2), user identity/RBAC/workspace persistence, and institutional Excel & A4 PDF Tear-Sheet exports (`V3-01..V3-15`).

### Added
- **Compliance & MRM (`V3-01..V3-03`)**:
  - Added Vietnam 2025–2026 regulatory compliance policy matrix (`docs/compliance/VN_REGULATORY_COMPLIANCE.md`) covering Decree 88/2014/ND-CP, Securities Law 56/2024, and Law 91/2025 (PDPL) TIA Article 20 consent contracts (`src/finintel/domain/compliance.py`).
  - Added `TT200 -> TT99` model risk management drift gate (`src/finintel/ml/mrm_tt99.py`) exposed on `/api/v2/meta/model-card`.
  - Standardized NĐ 88/2014 legal risk labels and disclaimer (`frontend/src/lib/compliance.ts`) enforced by static CI guard (`scripts/check_constraints.py`).
- **PIT Valuation Mart & Full Screener (`V3-04..V3-06`)**:
  - Added `ocf_to_total_liabilities` ratio and vectorized point-in-time `pit_valuation_mart` (`src/finintel/analytics/valuation_mart.py`) joining raw EOD prices (`close_raw`) via forward `join_asof` with strict negative-denominator `NULL` preservation.
  - Registered `pit_valuation_mart` in `DuckLakeStore` and extended `/api/v1/screener` & `/screener` UI with 14 financial, valuation (`P/E`, `P/B`, `Market Cap`), exchange (`HOSE/HNX/UPCOM`), cluster, and anomaly filters.
- **Free Cloud HA & Object Storage (`V3-07..V3-09`)**:
  - Added Cloudflare R2/S3 `ObjectStoreConfig` and DuckDB `httpfs` adapter (`src/finintel/storage/object_store.py`) with `local` disk default fallback.
  - Added snapshot rollover L1B cache invalidation via PostgreSQL pointer and OWASP security headers + strict CORS middleware (`src/finintel/serving/dependencies.py`).
  - Added `docker-compose.free.yml` (`< 8 GB` RAM cap for OCI Always Free ARM64), `config/gateway/Caddyfile.free`, `scripts/backup_to_r2.py`, `.github/workflows/free-etl-fallback.yml`, and `docs/runbooks/free_cloud_deploy.md`.
- **Identity, Auth/RBAC & Workspace (`V3-10..V3-12`)**:
  - Added `finintel_app` PostgreSQL schema (`config/migrations/app_v3_001_identity_workspace.sql`) and `src/finintel/auth/` with Argon2id password hashing, SHA-256 opaque `HttpOnly; Secure; SameSite=Lax` session cookies, brute-force rate limiting, Resend password reset, RBAC (`USER`, `RESEARCHER`, `INTERNAL_OPS`), PDPL consent management, and Right-to-Erasure account deletion.
  - Added `src/finintel/workspace/` for user-scoped Multi-Watchlist (`localStorage` import), Analyst Notes, Saved Screens, and append-only `user_audit_logs`, plus `/auth/login`, `/auth/register`, `/auth/reset-password`, `/profile`, and upgraded `/watchlist` UI routes while keeping anonymous guest flows intact.
- **Multi-Sheet Excel & A4 Tear-Sheet Export (`V3-13..V3-15`)**:
  - Added formula-safe multi-sheet Excel workbook generator (`src/finintel/serving/exports/excel_builder.py`) and FastAPI endpoints `GET /api/v3/exports/companies/{ticker}.xlsx` and `GET /api/v3/exports/screener.xlsx` (`2,000`-row cap, `X-Snapshot-ID` lineage header).
  - Added `ExportActionMenu` and print-ready A4 Tear-Sheet route (`/companies/[ticker]/tear-sheet`) with forced Light Mode print styling, all 6 quantitative blocks, and NĐ 88/2014 compliance footer.

### Fixed
- Preserved Block 1 Tear-Sheet masthead and KPI conclusion `<header class="tear-sheet-header">` in `@media print` while hiding `AppShell` navigation header and footer via `.no-print`.
- Reset `failed_login_attempts` counter after 15-minute lockout cooldown expiry and unified cross-IP lockout `429` messaging in `AuthService.login`.
- Kept `platform`-group imports lazy across `auth`, `workspace`, and `exports` and guarded `xlsxwriter` workbook tests with `pytest.importorskip("xlsxwriter")` for offline `mvp-checks` CI compatibility.
