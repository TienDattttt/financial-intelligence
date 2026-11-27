-- FinIntel V3.0 — Identity, RBAC, PDPL Consents & Workspace Schema (`finintel_app`)
-- Target PostgreSQL: 16+
-- Compliance: PDPL Law 91/2025/QH15 (explicit per-purpose consent & Right to Erasure)

CREATE TABLE IF NOT EXISTS users (
    user_id UUID PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    email_sha256 CHAR(64) NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'USER' CHECK (role IN ('USER', 'RESEARCHER', 'INTERNAL_OPS')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    email_verified_at TIMESTAMPTZ NULL,
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS user_consents (
    consent_id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    purpose TEXT NOT NULL CHECK (
        purpose IN ('ACCOUNT_CORE', 'ALERT_NOTIFICATIONS', 'CROSS_BORDER_LLM_RAG')
    ),
    policy_version TEXT NOT NULL,
    granted BOOLEAN NOT NULL,
    source_channel TEXT NOT NULL DEFAULT 'web',
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    withdrawn_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_user_consents_user_purpose
    ON user_consents (user_id, purpose, recorded_at DESC);

CREATE TABLE IF NOT EXISTS user_sessions (
    session_id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    token_sha256 CHAR(64) NOT NULL UNIQUE,
    user_agent_hash CHAR(64) NULL,
    ip_hash CHAR(64) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user_active
    ON user_sessions (user_id, revoked_at, expires_at DESC);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    reset_id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    token_sha256 CHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user_exp
    ON password_reset_tokens (user_id, expires_at DESC);

CREATE TABLE IF NOT EXISTS watchlists (
    watchlist_id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_watchlists_user
    ON watchlists (user_id, created_at ASC);

CREATE TABLE IF NOT EXISTS watchlist_items (
    watchlist_id UUID NOT NULL REFERENCES watchlists(watchlist_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (watchlist_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_items_user_ticker
    ON watchlist_items (user_id, ticker);

CREATE TABLE IF NOT EXISTS analyst_notes (
    note_id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    period_end_date DATE NULL,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_analyst_notes_user_ticker
    ON analyst_notes (user_id, ticker, updated_at DESC);

CREATE TABLE IF NOT EXISTS saved_screens (
    screen_id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    filters_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_saved_screens_user
    ON saved_screens (user_id, updated_at DESC);

-- Append-only security & workspace audit log.
-- Intentionally omits ON DELETE CASCADE so audit history remains intact while PII
-- subject identifiers are anonymized under Law 91/2025/QH15 erasure requests.
CREATE TABLE IF NOT EXISTS user_audit_logs (
    audit_id BIGSERIAL PRIMARY KEY,
    actor_user_id UUID NULL,
    actor_subject_hash CHAR(64) NOT NULL,
    actor_role TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL DEFAULT '',
    resource_id TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT 'SUCCESS' CHECK (outcome IN ('SUCCESS', 'FAILURE', 'DENIED')),
    ip_hash CHAR(64) NOT NULL DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_audit_logs_actor_time
    ON user_audit_logs (actor_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_user_audit_logs_action_time
    ON user_audit_logs (action, created_at DESC);
