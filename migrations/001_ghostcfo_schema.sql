-- GhostCFO PostgreSQL Schema Migration
-- Run against the shared vaakshastra PostgreSQL instance.
-- Creates a new schema: ghostcfo

-- =====================================================
-- SCHEMA
-- =====================================================

CREATE SCHEMA IF NOT EXISTS ghostcfo;

-- =====================================================
-- USERS
-- =====================================================

CREATE TABLE IF NOT EXISTS ghostcfo.users (
    user_id             VARCHAR(20) PRIMARY KEY,   -- phone number
    phone_number        VARCHAR(20) NOT NULL,
    name                VARCHAR(100) DEFAULT '',
    language            VARCHAR(5) DEFAULT 'en',
    timezone            VARCHAR(30) DEFAULT 'Asia/Kolkata',
    business_type       VARCHAR(20) DEFAULT 'freelancer'
                        CHECK (business_type IN ('freelancer','consultant','agency','solo_founder','other')),
    gst_registered      BOOLEAN DEFAULT FALSE,
    gstin               VARCHAR(15),
    preferred_briefing_time VARCHAR(5) DEFAULT '08:00',
    voice_briefing_enabled  BOOLEAN DEFAULT FALSE,
    gmail_connected     BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =====================================================
-- TRANSACTIONS (partitioned by year)
-- =====================================================

CREATE TABLE IF NOT EXISTS ghostcfo.transactions (
    transaction_id      VARCHAR(16) NOT NULL,
    user_id             VARCHAR(20) NOT NULL REFERENCES ghostcfo.users(user_id),
    txn_date            DATE NOT NULL,
    amount              NUMERIC(15,2) NOT NULL CHECK (amount >= 0),
    direction           VARCHAR(6) NOT NULL CHECK (direction IN ('credit','debit')),

    -- Encrypted fields (stored as base64 ciphertext)
    description_enc     TEXT NOT NULL DEFAULT '',
    cleaned_desc_enc    TEXT DEFAULT '',
    counterparty_enc    TEXT DEFAULT '',
    raw_source_enc      TEXT DEFAULT '',

    category            VARCHAR(30) NOT NULL DEFAULT 'unknown',
    subcategory         VARCHAR(50),
    is_income           BOOLEAN NOT NULL DEFAULT FALSE,
    is_recurring        BOOLEAN DEFAULT FALSE,
    recurrence_pattern  VARCHAR(20),
    source              VARCHAR(20) NOT NULL DEFAULT 'bank_pdf'
                        CHECK (source IN ('bank_pdf','upi_csv','gmail_invoice','manual')),
    confidence          REAL DEFAULT 0.0,
    is_encrypted        BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (transaction_id, txn_date)
) PARTITION BY RANGE (txn_date);

-- Create partitions for 2024, 2025, 2026
CREATE TABLE IF NOT EXISTS ghostcfo.transactions_2024
    PARTITION OF ghostcfo.transactions
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');

CREATE TABLE IF NOT EXISTS ghostcfo.transactions_2025
    PARTITION OF ghostcfo.transactions
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');

CREATE TABLE IF NOT EXISTS ghostcfo.transactions_2026
    PARTITION OF ghostcfo.transactions
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

-- Indexes
CREATE INDEX IF NOT EXISTS idx_txn_user_date
    ON ghostcfo.transactions (user_id, txn_date DESC);
CREATE INDEX IF NOT EXISTS idx_txn_category
    ON ghostcfo.transactions (user_id, category);
CREATE INDEX IF NOT EXISTS idx_txn_counterparty
    ON ghostcfo.transactions (user_id, is_income) WHERE is_income = TRUE;

-- =====================================================
-- INVOICES
-- =====================================================

CREATE TABLE IF NOT EXISTS ghostcfo.invoices (
    invoice_id          VARCHAR(16) PRIMARY KEY,
    user_id             VARCHAR(20) NOT NULL REFERENCES ghostcfo.users(user_id),
    client_name         VARCHAR(200) NOT NULL,
    client_email        VARCHAR(200),
    amount              NUMERIC(15,2) NOT NULL CHECK (amount > 0),
    currency            VARCHAR(3) DEFAULT 'INR',
    invoice_date        DATE NOT NULL,
    due_date            DATE NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'sent'
                        CHECK (status IN ('sent','partially_paid','paid','overdue','disputed')),
    payment_received    NUMERIC(15,2) DEFAULT 0,
    linked_txn_id       VARCHAR(16),
    source              VARCHAR(20) DEFAULT 'manual'
                        CHECK (source IN ('gmail_detected','manual','razorpay','instamojo')),
    raw_email_id        VARCHAR(100),
    gst_number          VARCHAR(15),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invoice_user_status
    ON ghostcfo.invoices (user_id, status);
CREATE INDEX IF NOT EXISTS idx_invoice_overdue
    ON ghostcfo.invoices (user_id, due_date)
    WHERE status NOT IN ('paid');

-- =====================================================
-- FINANCIAL SNAPSHOTS (daily, JSONB for flexibility)
-- =====================================================

CREATE TABLE IF NOT EXISTS ghostcfo.financial_snapshots (
    snapshot_id         VARCHAR(16) PRIMARY KEY,
    user_id             VARCHAR(20) NOT NULL REFERENCES ghostcfo.users(user_id),
    snapshot_date       DATE NOT NULL,
    current_balance     NUMERIC(15,2) NOT NULL,
    runway_days         INTEGER DEFAULT 0,
    monthly_burn_rate   NUMERIC(15,2) DEFAULT 0,
    monthly_income_rate NUMERIC(15,2) DEFAULT 0,
    total_receivables   NUMERIC(15,2) DEFAULT 0,
    overdue_receivables NUMERIC(15,2) DEFAULT 0,
    gst_liability       NUMERIC(15,2) DEFAULT 0,
    tds_liability       NUMERIC(15,2) DEFAULT 0,
    health_score        INTEGER DEFAULT 50,
    snapshot_data       JSONB DEFAULT '{}',     -- Full snapshot as JSON
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_user_date
    ON ghostcfo.financial_snapshots (user_id, snapshot_date DESC);

-- =====================================================
-- ALERTS
-- =====================================================

CREATE TABLE IF NOT EXISTS ghostcfo.alerts (
    alert_id            VARCHAR(16) PRIMARY KEY,
    user_id             VARCHAR(20) NOT NULL REFERENCES ghostcfo.users(user_id),
    alert_type          VARCHAR(30) NOT NULL,
    severity            VARCHAR(10) NOT NULL CHECK (severity IN ('info','warning','critical')),
    title               VARCHAR(300) NOT NULL,
    evidence            TEXT NOT NULL,
    recommended_action  TEXT NOT NULL,
    dedup_key           VARCHAR(12),
    triggered_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_acknowledged     BOOLEAN DEFAULT FALSE,
    expires_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_alert_user_active
    ON ghostcfo.alerts (user_id, is_acknowledged)
    WHERE is_acknowledged = FALSE;
CREATE INDEX IF NOT EXISTS idx_alert_dedup
    ON ghostcfo.alerts (user_id, dedup_key, triggered_at DESC);

-- =====================================================
-- BRIEFING HISTORY
-- =====================================================

CREATE TABLE IF NOT EXISTS ghostcfo.briefing_history (
    briefing_id         VARCHAR(16) PRIMARY KEY,
    user_id             VARCHAR(20) NOT NULL REFERENCES ghostcfo.users(user_id),
    briefing_text       TEXT NOT NULL,
    snapshot_id         VARCHAR(16) REFERENCES ghostcfo.financial_snapshots(snapshot_id),
    health_score        INTEGER,
    alerts_included     INTEGER DEFAULT 0,
    tone                VARCHAR(20) DEFAULT 'balanced',
    sent_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivery_channel    VARCHAR(20) DEFAULT 'whatsapp_text',
    delivery_status     VARCHAR(20) DEFAULT 'sent'
);

CREATE INDEX IF NOT EXISTS idx_briefing_user_date
    ON ghostcfo.briefing_history (user_id, sent_at DESC);

-- =====================================================
-- TAX CALENDAR
-- =====================================================

CREATE TABLE IF NOT EXISTS ghostcfo.tax_calendar (
    id                  SERIAL PRIMARY KEY,
    user_id             VARCHAR(20) NOT NULL REFERENCES ghostcfo.users(user_id),
    deadline_type       VARCHAR(20) NOT NULL,  -- 'GSTR-1', 'GSTR-3B', 'TDS', 'ADVANCE_TAX'
    due_date            DATE NOT NULL,
    estimated_liability NUMERIC(15,2) DEFAULT 0,
    is_filed            BOOLEAN DEFAULT FALSE,
    reminder_sent       BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tax_cal_upcoming
    ON ghostcfo.tax_calendar (user_id, due_date)
    WHERE is_filed = FALSE;

-- =====================================================
-- OAUTH TOKENS (encrypted)
-- =====================================================

CREATE TABLE IF NOT EXISTS ghostcfo.oauth_tokens (
    user_id             VARCHAR(20) PRIMARY KEY REFERENCES ghostcfo.users(user_id),
    provider            VARCHAR(20) NOT NULL DEFAULT 'gmail',
    access_token_enc    TEXT NOT NULL,
    refresh_token_enc   TEXT NOT NULL,
    token_expiry        TIMESTAMPTZ,
    scopes              TEXT DEFAULT 'gmail.readonly',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =====================================================
-- AUTO-UPDATE TRIGGER
-- =====================================================

CREATE OR REPLACE FUNCTION ghostcfo.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply trigger to all tables with updated_at
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOR tbl IN
        SELECT table_name FROM information_schema.columns
        WHERE table_schema = 'ghostcfo' AND column_name = 'updated_at'
        GROUP BY table_name
    LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS trg_updated_at ON ghostcfo.%I; '
            'CREATE TRIGGER trg_updated_at BEFORE UPDATE ON ghostcfo.%I '
            'FOR EACH ROW EXECUTE FUNCTION ghostcfo.update_updated_at()',
            tbl, tbl
        );
    END LOOP;
END;
$$;
