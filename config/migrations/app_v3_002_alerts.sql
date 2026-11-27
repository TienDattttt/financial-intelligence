-- FinIntel V3.0 — Early Warning Alerts & Multi-Channel Delivery Schema (`finintel_app`)
-- Target PostgreSQL: 16+
-- Compliance: PDPL Law 91/2025/QH15 (requires ConsentPurpose.ALERT_NOTIFICATIONS before dispatch)

CREATE TABLE IF NOT EXISTS alert_subscriptions (
    subscription_id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    watchlist_id UUID NULL REFERENCES watchlists(watchlist_id) ON DELETE SET NULL,
    event_types JSONB NOT NULL DEFAULT '["NEW_FILING_PUBLISHED","RISK_TIER_UPGRADE","NEW_RED_FLAG_TRIGGERED","REGULATORY_EVENT_ISSUED","MERTON_DTD_BREACH"]'::jsonb,
    in_app_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    email_digest_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    email_frequency TEXT NOT NULL DEFAULT 'DAILY_DIGEST' CHECK (
        email_frequency IN ('INSTANT', 'DAILY_DIGEST')
    ),
    telegram_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    telegram_chat_id TEXT NULL,
    webhook_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    webhook_url TEXT NULL,
    webhook_secret_hmac TEXT NULL,
    merton_dtd_threshold DOUBLE PRECISION NOT NULL DEFAULT 1.5,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS idx_alert_subscriptions_active
    ON alert_subscriptions (is_active, user_id);

CREATE TABLE IF NOT EXISTS market_alert_events (
    event_id UUID PRIMARY KEY,
    dedup_key CHAR(64) NOT NULL UNIQUE,
    snapshot_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'NEW_FILING_PUBLISHED',
            'RISK_TIER_UPGRADE',
            'NEW_RED_FLAG_TRIGGERED',
            'REGULATORY_EVENT_ISSUED',
            'MERTON_DTD_BREACH'
        )
    ),
    severity TEXT NOT NULL CHECK (
        severity IN ('INFO', 'MEDIUM', 'HIGH', 'CRITICAL')
    ),
    title_vi TEXT NOT NULL,
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_market_alert_events_ticker_time
    ON market_alert_events (ticker, detected_at DESC);

CREATE TABLE IF NOT EXISTS user_notifications (
    notification_id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    event_id UUID NULL REFERENCES market_alert_events(event_id) ON DELETE SET NULL,
    dedup_key TEXT NOT NULL,
    ticker TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'NEW_FILING_PUBLISHED',
            'RISK_TIER_UPGRADE',
            'NEW_RED_FLAG_TRIGGERED',
            'REGULATORY_EVENT_ISSUED',
            'MERTON_DTD_BREACH'
        )
    ),
    severity TEXT NOT NULL CHECK (
        severity IN ('INFO', 'MEDIUM', 'HIGH', 'CRITICAL')
    ),
    title_vi TEXT NOT NULL,
    company_url TEXT NOT NULL,
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    read_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, dedup_key)
);

CREATE INDEX IF NOT EXISTS idx_user_notifications_inbox
    ON user_notifications (user_id, is_read, created_at DESC);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    delivery_id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    event_id UUID NULL REFERENCES market_alert_events(event_id) ON DELETE SET NULL,
    channel TEXT NOT NULL CHECK (
        channel IN ('IN_APP', 'TELEGRAM', 'EMAIL_DIGEST', 'WEBHOOK')
    ),
    dedup_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'PENDING',
            'SENT',
            'FAILED',
            'DEAD_LETTER',
            'SKIPPED_NO_CONSENT',
            'SKIPPED_QUOTA'
        )
    ),
    attempts INTEGER NOT NULL DEFAULT 0,
    http_status INTEGER NULL,
    error_code TEXT NULL,
    last_error TEXT NULL,
    next_retry_at TIMESTAMPTZ NULL,
    delivered_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, channel, dedup_key)
);

CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_status_retry
    ON webhook_deliveries (status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_user_created
    ON webhook_deliveries (user_id, created_at DESC);
