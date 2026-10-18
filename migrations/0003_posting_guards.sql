CREATE FUNCTION reject_mutation() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is immutable; operation % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER ledger_entries_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger_entries
    FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER transfer_status_events_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON transfer_status_events
    FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER transfers_no_delete
    BEFORE DELETE OR TRUNCATE ON transfers
    FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER job_locks_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON job_locks
    FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();

CREATE FUNCTION protect_account_identity() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.balance <> 0 THEN
            RAISE EXCEPTION 'Accounts must start with zero balance' USING ERRCODE = '23514';
        END IF;
    ELSIF ROW(NEW.id, NEW.user_id, NEW.account_type, NEW.currency, NEW.created_at)
        IS DISTINCT FROM ROW(OLD.id, OLD.user_id, OLD.account_type, OLD.currency, OLD.created_at) THEN
        RAISE EXCEPTION 'Account ownership, type and currency cannot change' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER accounts_identity
    BEFORE INSERT OR UPDATE ON accounts
    FOR EACH ROW EXECUTE FUNCTION protect_account_identity();

CREATE FUNCTION protect_transfer() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    original transfers%ROWTYPE;
    source_type TEXT;
    destination_type TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'pending' THEN
            RAISE EXCEPTION 'Transfers must start pending' USING ERRCODE = '23514';
        END IF;
        SELECT account_type INTO source_type FROM accounts WHERE id = NEW.source_account_id;
        SELECT account_type INTO destination_type FROM accounts WHERE id = NEW.destination_account_id;
        IF (NEW.type = 'top_up' AND (source_type <> 'cash_in' OR destination_type <> 'wallet'))
            OR (NEW.type = 'peer' AND (source_type <> 'wallet' OR destination_type <> 'wallet'))
            OR (NEW.type = 'withdrawal' AND (source_type <> 'wallet' OR destination_type <> 'cash_out'))
            OR (NEW.type = 'fee' AND (source_type <> 'wallet' OR destination_type <> 'fee_revenue')) THEN
            RAISE EXCEPTION 'Account types do not match transfer type' USING ERRCODE = '23514';
        END IF;
        IF NEW.type = 'reversal' THEN
            SELECT * INTO original FROM transfers WHERE id = NEW.reversal_of;
            IF NOT FOUND OR original.status <> 'posted' OR original.type = 'reversal'
                OR original.amount <> NEW.amount
                OR original.source_account_id <> NEW.destination_account_id
                OR original.destination_account_id <> NEW.source_account_id THEN
                RAISE EXCEPTION 'Reversal must exactly reverse an original posted transfer'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
    ELSE
        IF ROW(NEW.id, NEW.initiated_by, NEW.source_account_id, NEW.destination_account_id,
               NEW.type, NEW.amount, NEW.currency, NEW.reversal_of, NEW.note, NEW.created_at)
            IS DISTINCT FROM
           ROW(OLD.id, OLD.initiated_by, OLD.source_account_id, OLD.destination_account_id,
               OLD.type, OLD.amount, OLD.currency, OLD.reversal_of, OLD.note, OLD.created_at) THEN
            RAISE EXCEPTION 'Transfer details cannot change' USING ERRCODE = '23514';
        END IF;
        IF NEW.status <> OLD.status AND NOT (
            (OLD.status = 'pending' AND NEW.status IN ('held', 'posted', 'rejected'))
            OR (OLD.status = 'held' AND NEW.status IN ('posted', 'rejected'))
        ) THEN
            RAISE EXCEPTION 'Invalid status transition: % -> %', OLD.status, NEW.status
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER transfers_protected
    BEFORE INSERT OR UPDATE ON transfers
    FOR EACH ROW EXECUTE FUNCTION protect_transfer();

CREATE FUNCTION audit_transfer_status() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    actor BIGINT;
    previous TEXT;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.status = OLD.status THEN
            RETURN NEW;
        END IF;
        previous := OLD.status;
    END IF;
    actor := NULLIF(current_setting('wallet.actor_id', true), '')::BIGINT;
    IF actor IS NULL AND TG_OP = 'INSERT' THEN
        actor := NEW.initiated_by;
    END IF;
    INSERT INTO transfer_status_events (transfer_id, previous_status, status, actor_id, actor_kind)
        VALUES (NEW.id, previous, NEW.status, actor,
                CASE WHEN actor IS NULL THEN 'system' ELSE 'user' END);
    RETURN NEW;
END;
$$;
CREATE TRIGGER transfers_audit
    AFTER INSERT OR UPDATE ON transfers
    FOR EACH ROW EXECUTE FUNCTION audit_transfer_status();

CREATE FUNCTION protect_entry_insert() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    parent_status TEXT;
BEGIN
    -- Serialize entry appends and status changes on the same transfer row.
    SELECT status INTO parent_status FROM transfers WHERE id = NEW.transfer_id FOR UPDATE;
    IF FOUND AND parent_status NOT IN ('pending', 'held') THEN
        RAISE EXCEPTION 'Cannot append entries to a completed transfer' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER ledger_entries_insert
    BEFORE INSERT ON ledger_entries
    FOR EACH ROW EXECUTE FUNCTION protect_entry_insert();

CREATE FUNCTION validate_posting() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    parent transfers%ROWTYPE;
    target_id BIGINT;
    entry_count BIGINT;
    debit_total NUMERIC;
    credit_total NUMERIC;
    wrong_endpoint BOOLEAN;
BEGIN
    IF TG_TABLE_NAME = 'transfers' THEN
        target_id := NEW.id;
    ELSE
        target_id := NEW.transfer_id;
    END IF;
    SELECT * INTO parent FROM transfers WHERE id = target_id;
    SELECT count(*),
           COALESCE(sum(amount) FILTER (WHERE direction = 'debit'), 0),
           COALESCE(sum(amount) FILTER (WHERE direction = 'credit'), 0),
           COALESCE(bool_or(
               (direction = 'debit' AND account_id <> parent.source_account_id)
               OR (direction = 'credit' AND account_id <> parent.destination_account_id)
           ), false)
        INTO entry_count, debit_total, credit_total, wrong_endpoint
        FROM ledger_entries WHERE transfer_id = target_id;
    IF parent.status = 'posted' THEN
        IF entry_count < 2 OR debit_total <> parent.amount OR credit_total <> parent.amount
            OR wrong_endpoint THEN
            RAISE EXCEPTION 'Transfer % must have balanced entries matching its amount and accounts',
                target_id USING ERRCODE = '23514';
        END IF;
    ELSIF entry_count <> 0 THEN
        RAISE EXCEPTION 'Unposted transfer % cannot have ledger entries', target_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER transfers_posting_valid
    AFTER INSERT OR UPDATE ON transfers DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validate_posting();
CREATE CONSTRAINT TRIGGER ledger_entries_posting_valid
    AFTER INSERT ON ledger_entries DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validate_posting();
