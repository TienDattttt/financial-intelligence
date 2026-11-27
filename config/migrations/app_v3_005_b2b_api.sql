-- FinIntel V3.1 — Open Research & B2B Commercial API Schema (`finintel_app`)
-- Target PostgreSQL: 16+
-- Security: Stores ONLY SHA-256 hashes of API keys (never plaintext).

CREATE TABLE IF NOT EXISTS api_keys (
    key_id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    key_hash CHAR(64) NOT NULL UNIQUE,
    scopes JSONB NOT NULL DEFAULT '["read:derived_risk"]'::jsonb,
    rate_limit_per_min INTEGER NOT NULL DEFAULT 30 CHECK (rate_limit_per_min > 0),
    daily_limit INTEGER NOT NULL DEFAULT 1000 CHECK (daily_limit > 0),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_used_at TIMESTAMPTZ NULL,
    expires_at TIMESTAMPTZ NULL,
    revoked_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user_active
    ON api_keys (user_id, is_active, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_api_keys_hash_active
    ON api_keys (key_hash)
    WHERE is_active = TRUE AND revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS api_key_usage_daily (
    usage_date DATE NOT NULL,
    key_id UUID NOT NULL REFERENCES api_keys(key_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL,
    status_class TEXT NOT NULL CHECK (status_class IN ('2xx', '3xx', '4xx', '5xx')),
    request_count INTEGER NOT NULL DEFAULT 0 CHECK (request_count >= 0),
    cache_hits INTEGER NOT NULL DEFAULT 0 CHECK (cache_hits >= 0),
    last_request_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (usage_date, key_id, endpoint, status_class)
);

CREATE INDEX IF NOT EXISTS idx_api_key_usage_daily_user_date
    ON api_key_usage_daily (user_id, usage_date DESC);

CREATE TABLE IF NOT EXISTS b2b_webhook_endpoints (
    endpoint_id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    label TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    secret_hmac TEXT NOT NULL,
    event_types JSONB NOT NULL DEFAULT '["NEW_FILING_PUBLISHED","RISK_TIER_UPGRADE","NEW_RED_FLAG_TRIGGERED","REGULATORY_EVENT_ISSUED","MERTON_DTD_BREACH"]'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_status INTEGER NULL,
    last_error TEXT NULL,
    last_delivered_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_b2b_webhook_endpoints_user
    ON b2b_webhook_endpoints (user_id, is_active, created_at DESC);
