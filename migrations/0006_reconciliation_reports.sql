ALTER TABLE reconciliation_runs
    ADD COLUMN trigger_type TEXT NOT NULL DEFAULT 'manual'
        CHECK (trigger_type IN ('manual', 'scheduled', 'cli')),
    ADD COLUMN scheduled_for DATE UNIQUE,
    ADD COLUMN problem_count BIGINT NOT NULL DEFAULT 0 CHECK (problem_count >= 0),
    ADD CHECK ((trigger_type = 'scheduled') = (scheduled_for IS NOT NULL));

-- Keep raw invariant violations separate from comparisons against business events.
-- A changed entry can violate global balance, transfer balance and a cached balance.
CREATE TABLE reconciliation_problems (
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
CREATE INDEX reconciliation_problems_run ON reconciliation_problems (run_id, id);
CREATE INDEX reconciliation_findings_run_page ON reconciliation_findings (run_id, id);
DROP INDEX reconciliation_findings_run;

CREATE TRIGGER reconciliation_findings_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON reconciliation_findings
    FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER reconciliation_problems_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON reconciliation_problems
    FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();
