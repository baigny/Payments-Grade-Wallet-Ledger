-- Shared by API replicas and the explanation worker; contains no prompts or keys.
CREATE TABLE llm_budgets (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope TEXT NOT NULL UNIQUE,
    blocked_until TIMESTAMPTZ NOT NULL DEFAULT '1970-01-01 00:00:00+00'
);

CREATE TABLE llm_requests (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    budget_id BIGINT NOT NULL REFERENCES llm_budgets(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    tokens BIGINT NOT NULL CHECK (tokens > 0)
);
CREATE INDEX llm_requests_budget_time ON llm_requests (budget_id, created_at);
