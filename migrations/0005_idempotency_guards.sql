-- A key and its response must become durable together; no abandoned processing rows.
CREATE FUNCTION require_completed_idempotency_key() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM idempotency_keys WHERE id = NEW.id AND status <> 'completed') THEN
        RAISE EXCEPTION 'Idempotency key must have a saved response before commit'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER idempotency_completed_at_commit
AFTER INSERT OR UPDATE ON idempotency_keys DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION require_completed_idempotency_key();

CREATE FUNCTION protect_idempotency_response() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'completed' OR
       (NEW.id, NEW.user_id, NEW.key, NEW.request_hash, NEW.created_at) IS DISTINCT FROM
       (OLD.id, OLD.user_id, OLD.key, OLD.request_hash, OLD.created_at) THEN
        RAISE EXCEPTION 'Idempotency identity and completed responses are immutable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER idempotency_response_immutable BEFORE UPDATE ON idempotency_keys
FOR EACH ROW EXECUTE FUNCTION protect_idempotency_response();
