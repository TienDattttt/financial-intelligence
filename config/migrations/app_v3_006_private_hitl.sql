-- FinIntel V3.1 — Isolated Private Issuer Credit Dossiers & HITL Expert Feedback (`finintel_app`)
-- Target PostgreSQL: 16+
-- Invariants:
--   1. Private issuer financial statements are strictly isolated per user_id in finintel_app
--      and NEVER written to DuckLake published_financial_marts or ML training datasets.
--   2. hitl_flag_feedback NEVER overwrites ML training labels or regulatory_events;
--      ON DELETE SET NULL preserves anonymized triage statistics under Law 91/2025/QH15.

CREATE TABLE IF NOT EXISTS private_credit_dossiers (
    dossier_id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    company_name TEXT NOT NULL,
    tax_code TEXT NULL,
    icb_code_level2 TEXT NULL,
    statement_scope TEXT NOT NULL DEFAULT 'SEPARATE'
        CHECK (statement_scope IN ('CONSOLIDATED', 'SEPARATE')),
    taxonomy_version TEXT NOT NULL DEFAULT 'VAS_TT99_2025'
        CHECK (taxonomy_version IN ('VAS_TT200_2014', 'VAS_TT99_2025')),
    currency_unit TEXT NOT NULL DEFAULT 'VND',
    unit_multiplier BIGINT NOT NULL DEFAULT 1
        CHECK (unit_multiplier IN (1, 1000, 1000000, 1000000000)),
    source_filename TEXT NOT NULL DEFAULT '',
    source_sha256 CHAR(64) NOT NULL,
    qg1_status TEXT NOT NULL CHECK (qg1_status IN ('PASS', 'WARNING', 'FAIL')),
    qg1_report_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    periods_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    latest_score_json JSONB NULL,
    retention_expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '30 days'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_private_credit_dossiers_user_created
    ON private_credit_dossiers (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_private_credit_dossiers_retention
    ON private_credit_dossiers (retention_expires_at);

CREATE TABLE IF NOT EXISTS private_credit_facts (
    fact_id UUID PRIMARY KEY,
    dossier_id UUID NOT NULL REFERENCES private_credit_dossiers(dossier_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    statement_type TEXT NOT NULL
        CHECK (statement_type IN ('BALANCE_SHEET', 'INCOME_STATEMENT', 'CASH_FLOW')),
    vas_code TEXT NOT NULL,
    metric_id TEXT NOT NULL,
    line_label_vn TEXT NOT NULL DEFAULT '',
    period_end_date DATE NOT NULL,
    fiscal_year INTEGER NOT NULL,
    value DOUBLE PRECISION NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (dossier_id, statement_type, metric_id, period_end_date)
);

CREATE INDEX IF NOT EXISTS idx_private_credit_facts_dossier_user
    ON private_credit_facts (dossier_id, user_id, period_end_date DESC);

CREATE TABLE IF NOT EXISTS hitl_flag_feedback (
    feedback_id UUID PRIMARY KEY,
    user_id UUID NULL REFERENCES users(user_id) ON DELETE SET NULL,
    ticker TEXT NOT NULL,
    icb_code_level2 TEXT NULL,
    period_end_date DATE NOT NULL,
    signal_code TEXT NOT NULL,
    verdict TEXT NOT NULL
        CHECK (verdict IN ('CONFIRMED_RISK', 'BENIGN_DOMAIN_EXCEPTION')),
    reason_category TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    citation_page INTEGER NULL CHECK (citation_page IS NULL OR citation_page >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hitl_feedback_signal_icb
    ON hitl_flag_feedback (signal_code, icb_code_level2, verdict);

CREATE INDEX IF NOT EXISTS idx_hitl_feedback_ticker_period
    ON hitl_flag_feedback (ticker, period_end_date DESC, created_at DESC);
