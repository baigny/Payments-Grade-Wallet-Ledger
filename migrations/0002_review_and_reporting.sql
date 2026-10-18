CREATE TABLE risk_rules (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    rule_type TEXT NOT NULL CHECK (rule_type IN ('amount', 'velocity', 'daily_total')),
    threshold BIGINT NOT NULL CHECK (threshold > 0),
    window_seconds INTEGER CHECK (window_seconds > 0),
    action TEXT NOT NULL CHECK (action IN ('hold', 'flag')),
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((rule_type = 'amount' AND window_seconds IS NULL)
        OR (rule_type <> 'amount' AND window_seconds IS NOT NULL))
);

CREATE TABLE risk_flags (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transfer_id BIGINT NOT NULL REFERENCES transfers (id),
    rule_id BIGINT NOT NULL REFERENCES risk_rules (id),
    action TEXT NOT NULL CHECK (action IN ('hold', 'flag')),
    rule_snapshot JSONB NOT NULL CHECK (jsonb_typeof(rule_snapshot) = 'object'),
    explanation TEXT,
    label TEXT CHECK (label IN ('likely_ok', 'review', 'likely_fraud')),
    explained_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (transfer_id, rule_id)
);
CREATE INDEX risk_flags_unexplained ON risk_flags (created_at, id)
    WHERE explained_at IS NULL;

CREATE TABLE reconciliation_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'passed', 'failed', 'error')),
    finding_count BIGINT NOT NULL DEFAULT 0 CHECK (finding_count >= 0),
    account_count BIGINT NOT NULL DEFAULT 0 CHECK (account_count >= 0),
    transfer_count BIGINT NOT NULL DEFAULT 0 CHECK (transfer_count >= 0),
    entry_count BIGINT NOT NULL DEFAULT 0 CHECK (entry_count >= 0),
    error_message TEXT,
    CHECK ((status = 'running' AND ended_at IS NULL)
        OR (status <> 'running' AND ended_at IS NOT NULL AND ended_at >= started_at))
);

CREATE TABLE reconciliation_findings (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES reconciliation_runs (id),
    check_name TEXT NOT NULL,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('global', 'account', 'transfer', 'entry')),
    entity_id BIGINT,
    expected JSONB NOT NULL,
    actual JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((entity_type = 'global') = (entity_id IS NULL))
);
CREATE INDEX reconciliation_findings_run ON reconciliation_findings (run_id);

CREATE TABLE job_locks (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
INSERT INTO job_locks (name) VALUES ('reconciliation');

INSERT INTO risk_rules (name, rule_type, threshold, window_seconds, action) VALUES
    ('Large outgoing payment', 'amount', 100000, NULL, 'hold'),
    ('Outgoing payment velocity', 'velocity', 5, 60, 'hold'),
    ('Daily outgoing amount', 'daily_total', 500000, 86400, 'flag');
