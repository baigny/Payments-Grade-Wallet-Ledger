ALTER TABLE transfer_status_events ADD COLUMN reason TEXT NOT NULL DEFAULT '';
CREATE OR REPLACE FUNCTION audit_transfer_status() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE actor BIGINT; previous TEXT;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.status = OLD.status THEN RETURN NEW; END IF;
        previous := OLD.status;
    END IF;
    actor := NULLIF(current_setting('wallet.actor_id', true), '')::BIGINT;
    IF actor IS NULL AND TG_OP = 'INSERT' THEN actor := NEW.initiated_by; END IF;
    INSERT INTO transfer_status_events
        (transfer_id, previous_status, status, actor_id, actor_kind, reason)
    VALUES (NEW.id, previous, NEW.status, actor,
        CASE WHEN actor IS NULL THEN 'system' ELSE 'user' END,
        coalesce(current_setting('wallet.review_reason', true), ''));
    RETURN NEW;
END;
$$;

CREATE TABLE risk_rule_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule_id BIGINT NOT NULL REFERENCES risk_rules (id),
    actor_id BIGINT NOT NULL REFERENCES users (id),
    previous_config JSONB NOT NULL,
    new_config JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER risk_rule_events_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON risk_rule_events
    FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();

CREATE FUNCTION protect_risk_flag_facts() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.id, NEW.transfer_id, NEW.rule_id, NEW.action, NEW.rule_snapshot, NEW.created_at)
        IS DISTINCT FROM
       (OLD.id, OLD.transfer_id, OLD.rule_id, OLD.action, OLD.rule_snapshot, OLD.created_at) THEN
        RAISE EXCEPTION 'Risk decision facts are immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER risk_flags_facts BEFORE UPDATE ON risk_flags
    FOR EACH ROW EXECUTE FUNCTION protect_risk_flag_facts();
CREATE TRIGGER risk_flags_no_delete BEFORE DELETE OR TRUNCATE ON risk_flags
    FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();
CREATE INDEX transfers_risk_history ON transfers (source_account_id, created_at DESC)
    INCLUDE (amount) WHERE status IN ('posted', 'held');
CREATE INDEX risk_flags_transfer ON risk_flags (transfer_id, id);
INSERT INTO job_locks (name) VALUES ('risk_explanations');
