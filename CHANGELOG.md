# Changelog

All notable changes to FinIntel are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
