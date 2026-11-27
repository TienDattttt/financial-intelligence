-- FinIntel V3.0 — Data Ops QA Queue, Immutable Revisions & Issuer Lifecycle (`finintel_app`)
-- Target PostgreSQL: 16+
-- Invariants:
--   1. ops_revision_submissions is strictly append-only (revision_no = 1..N never overwritten).
--   2. Every revision submission requires source_url or raw_file_sha256 evidence.

CREATE TABLE IF NOT EXISTS ops_quarantine_tickets (
    ticket_id UUID PRIMARY KEY,
    issuer_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    filing_id TEXT NOT NULL,
    statement_scope TEXT NOT NULL,
    period_type TEXT NOT NULL,
    period_end_date DATE NOT NULL,
    taxonomy_version TEXT NOT NULL,
    rule_code TEXT NOT NULL,
    severity TEXT NOT NULL,
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'IN_REVIEW', 'RESOLVED')),
    decision TEXT NULL CHECK (decision IN ('APPROVE_REINGEST', 'KEEP_QUARANTINED')),
    assigned_to_user_id UUID NULL REFERENCES users(user_id) ON DELETE SET NULL,
    root_cause_note TEXT NOT NULL DEFAULT '',
    latest_revision_no INTEGER NOT NULL DEFAULT 1 CHECK (latest_revision_no >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ NULL,
    resolved_by_user_id UUID NULL REFERENCES users(user_id) ON DELETE SET NULL,
    UNIQUE (
        issuer_id,
        filing_id,
        statement_scope,
        period_type,
        period_end_date,
        taxonomy_version,
        rule_code
    )
);

CREATE INDEX IF NOT EXISTS idx_ops_qa_tickets_status_updated
    ON ops_quarantine_tickets (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS ops_revision_submissions (
    submission_id UUID PRIMARY KEY,
    ticket_id UUID NOT NULL REFERENCES ops_quarantine_tickets(ticket_id) ON DELETE CASCADE,
    issuer_id TEXT NOT NULL,
    filing_id TEXT NOT NULL,
    revision_no INTEGER NOT NULL CHECK (revision_no >= 1),
    source_url TEXT NULL,
    raw_file_sha256 CHAR(64) NULL,
    reason TEXT NOT NULL,
    payload_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    reingest_status TEXT NOT NULL DEFAULT 'PENDING_REINGEST',
    dagster_run_id TEXT NULL,
    submitted_by_user_id UUID NULL REFERENCES users(user_id) ON DELETE SET NULL,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticket_id, revision_no),
    CHECK (
        revision_no = 1
        OR COALESCE(NULLIF(BTRIM(source_url), ''), NULLIF(BTRIM(raw_file_sha256), '')) IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_ops_revisions_ticket_rev
    ON ops_revision_submissions (ticket_id, revision_no ASC);

CREATE TABLE IF NOT EXISTS issuer_lifecycle_events (
    event_id UUID PRIMARY KEY,
    issuer_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    exchange TEXT NOT NULL CHECK (exchange IN ('HOSE', 'HNX', 'UPCOM', 'DELISTED')),
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'IPO_LISTING',
            'EXCHANGE_TRANSFER',
            'DELISTING',
            'FY_END_CHANGE',
            'METADATA_SYNC'
        )
    ),
    effective_date DATE NOT NULL,
    fy_end_month SMALLINT NOT NULL DEFAULT 12 CHECK (fy_end_month BETWEEN 1 AND 12),
    previous_exchange TEXT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'RECORDED' CHECK (
        status IN ('RECORDED', 'QUEUED_FOR_SYNC', 'SYNCED')
    ),
    recorded_by_user_id UUID NULL REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_issuer_lifecycle_ticker_date
    ON issuer_lifecycle_events (ticker, effective_date DESC, created_at DESC);
