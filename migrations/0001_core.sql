CREATE TABLE users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(btrim(name)) > 0),
    email TEXT NOT NULL CHECK (length(btrim(email)) > 0),
    phone TEXT NOT NULL CHECK (length(btrim(phone)) > 0),
    kyc_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (kyc_status IN ('pending', 'verified', 'rejected')),
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX users_email_unique ON users (lower(email));
CREATE UNIQUE INDEX users_phone_unique ON users (phone);

CREATE TABLE accounts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT REFERENCES users (id),
    name TEXT NOT NULL CHECK (length(btrim(name)) > 0),
    account_type TEXT NOT NULL
        CHECK (account_type IN ('wallet', 'cash_in', 'cash_out', 'fee_revenue')),
    currency TEXT NOT NULL DEFAULT 'INR' CHECK (currency = 'INR'),
    balance BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT account_ownership CHECK (
        (account_type = 'wallet' AND user_id IS NOT NULL)
        OR (account_type <> 'wallet' AND user_id IS NULL)
    ),
    CONSTRAINT wallet_nonnegative CHECK (account_type <> 'wallet' OR balance >= 0)
);
CREATE INDEX accounts_user_id ON accounts (user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX accounts_system_type ON accounts (account_type)
    WHERE account_type <> 'wallet';

CREATE TABLE transfers (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    initiated_by BIGINT REFERENCES users (id),
    source_account_id BIGINT NOT NULL REFERENCES accounts (id),
    destination_account_id BIGINT NOT NULL REFERENCES accounts (id),
    type TEXT NOT NULL CHECK (type IN ('top_up', 'peer', 'withdrawal', 'fee', 'reversal')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'held', 'posted', 'rejected')),
    amount BIGINT NOT NULL CHECK (amount > 0),
    currency TEXT NOT NULL DEFAULT 'INR' CHECK (currency = 'INR'),
    reversal_of BIGINT UNIQUE REFERENCES transfers (id),
    note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (source_account_id <> destination_account_id),
    CHECK ((type = 'reversal') = (reversal_of IS NOT NULL)),
    CHECK (reversal_of IS NULL OR reversal_of <> id)
);
CREATE INDEX transfers_source_time ON transfers (source_account_id, created_at DESC, id DESC);
CREATE INDEX transfers_destination_time
    ON transfers (destination_account_id, created_at DESC, id DESC);
CREATE INDEX transfers_held ON transfers (created_at, id) WHERE status = 'held';

CREATE TABLE ledger_entries (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transfer_id BIGINT NOT NULL REFERENCES transfers (id),
    account_id BIGINT NOT NULL REFERENCES accounts (id),
    direction TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    amount BIGINT NOT NULL CHECK (amount > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ledger_entries_transfer ON ledger_entries (transfer_id);
CREATE INDEX ledger_entries_statement
    ON ledger_entries (account_id, created_at DESC, id DESC);

CREATE TABLE idempotency_keys (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users (id),
    key UUID NOT NULL,
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    status TEXT NOT NULL DEFAULT 'processing' CHECK (status IN ('processing', 'completed')),
    response_code INTEGER CHECK (response_code BETWEEN 100 AND 599),
    response_body JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, key),
    CHECK (
        (status = 'processing' AND response_code IS NULL AND response_body IS NULL)
        OR (status = 'completed' AND response_code IS NOT NULL AND response_body IS NOT NULL)
    )
);

CREATE TABLE transfer_status_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transfer_id BIGINT NOT NULL REFERENCES transfers (id),
    previous_status TEXT CHECK (previous_status IN ('pending', 'held', 'posted', 'rejected')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'held', 'posted', 'rejected')),
    actor_id BIGINT REFERENCES users (id),
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('user', 'system')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((actor_kind = 'user') = (actor_id IS NOT NULL))
);
CREATE INDEX transfer_status_events_transfer
    ON transfer_status_events (transfer_id, created_at, id);

INSERT INTO accounts (name, account_type) VALUES
    ('Cash in', 'cash_in'), ('Cash out', 'cash_out'), ('Fee revenue', 'fee_revenue');
