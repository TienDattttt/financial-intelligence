-- FinIntel V3.0 — Non-Commercial Fair-Use Quota & Free-Tier Budget Guard (`finintel_app`)
-- Target PostgreSQL: 16+
-- Notes: Non-commercial fair-use quota only; protects Gemini Free Tier & R2 free-tier limits.

CREATE TABLE IF NOT EXISTS usage_quota_daily (
    quota_date DATE NOT NULL,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    meter TEXT NOT NULL CHECK (meter IN ('rag_query', 'xlsx_export', 'api_call')),
    used_count INTEGER NOT NULL DEFAULT 0 CHECK (used_count >= 0),
    daily_limit INTEGER NOT NULL CHECK (daily_limit >= 0),
    reset_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (quota_date, user_id, meter)
);

CREATE INDEX IF NOT EXISTS idx_usage_quota_daily_user_date
    ON usage_quota_daily (user_id, quota_date DESC);

CREATE TABLE IF NOT EXISTS global_llm_budget_daily (
    budget_date DATE PRIMARY KEY,
    gemini_requests_used INTEGER NOT NULL DEFAULT 0 CHECK (gemini_requests_used >= 0),
    gemini_requests_cap INTEGER NOT NULL DEFAULT 1500 CHECK (gemini_requests_cap > 0),
    vision_pages_used INTEGER NOT NULL DEFAULT 0 CHECK (vision_pages_used >= 0),
    vision_pages_cap INTEGER NOT NULL DEFAULT 150 CHECK (vision_pages_cap > 0),
    circuit_breaker_ratio NUMERIC(4, 3) NOT NULL DEFAULT 0.850
        CHECK (circuit_breaker_ratio > 0 AND circuit_breaker_ratio <= 1.000),
    circuit_open_until TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rag_usage_ledger (
    ledger_id BIGSERIAL PRIMARY KEY,
    usage_date DATE NOT NULL,
    actor_subject_hash CHAR(64) NOT NULL DEFAULT '',
    operation TEXT NOT NULL CHECK (
        operation IN (
            'rag_query',
            'batch_digital',
            'batch_vision_ocr',
            'batch_skipped_dedup',
            'batch_deferred_cap'
        )
    ),
    ticker TEXT NOT NULL DEFAULT '',
    filing_id TEXT NOT NULL DEFAULT '',
    pdf_sha256 CHAR(64) NOT NULL DEFAULT '',
    digital_pages INTEGER NOT NULL DEFAULT 0 CHECK (digital_pages >= 0),
    vision_pages INTEGER NOT NULL DEFAULT 0 CHECK (vision_pages >= 0),
    llm_calls INTEGER NOT NULL DEFAULT 0 CHECK (llm_calls >= 0),
    cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'COMPLETED',
    estimated_cost_usd NUMERIC(10, 6) NOT NULL DEFAULT 0.000000,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rag_usage_ledger_date_op
    ON rag_usage_ledger (usage_date DESC, operation, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_rag_usage_ledger_pdf_sha
    ON rag_usage_ledger (pdf_sha256);
