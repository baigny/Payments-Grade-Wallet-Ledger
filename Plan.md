# Plan: Digital Wallet and Ledger Backend

## Goal

Build an INR wallet backend where money movements are atomic, every debit has a matching credit, retries never charge twice, and users can access only their own wallets and statements.

## Technology Stack

- Python with FastAPI.
- PostgreSQL 16 with explicit SQL through psycopg.
- Docker Compose for the database, API, migrations, and background worker.
- pytest and real PostgreSQL for automated verification.
- A small LLM provider interface with an offline mock.

## Build Stages

### 1. Set Up the Project

Create the application structure, configuration, Docker services, database connection, health checks, and CI. Make `docker compose up` start the application from a clean checkout.

Group related routes, request/response schemas, and service functions into feature packages. Keep shared authentication, database access, errors, and provider interfaces in a core package.

### 2. Design the Schema

Model users, wallet and system accounts, transfers, ledger entries, idempotency keys, audit events, risk rules, and reconciliation reports. Add migrations and document relationships in `docs/schema.md`.

Store amounts as integer paise in `BIGINT`. Enforce balanced postings, nonnegative user balances, and immutable ledger entries in PostgreSQL. Keep stored balances for fast reads and verify them against the ledger.

### 3. Build Safe Wallet Operations

Add authentication, wallet creation, top-ups, peer transfers, withdrawals, fees, reversals, balance reads, and paginated statements. Enforce ownership and admin permissions.

Use explicit transactions and lock affected account rows in ascending ID order. Record transfer status changes in an audit log. Document isolation experiments in `docs/isolation.md`.

### 4. Protect Against Duplicate Requests

Require an `Idempotency-Key` on every POST. Save the request fingerprint and response atomically with database changes. Replay the saved response for an identical request and reject a different request using the same key with `422`.

### 5. Add Reconciliation

Run nightly and manually triggered checks for unbalanced transfers, incorrect stored balances, overdrafts, and missing ledger records. Use a consistent Repeatable Read snapshot and prevent overlapping runs. Save findings without automatically changing money records.

### 6. Add Fraud Review

Store configurable amount and velocity rules in the database. Hold risky transfers before posting money, allow admins to release or reject them, and audit every decision. Recheck available funds before release.

### 7. Add Statement Questions

Support totals sent to a person, totals received, the largest transfer, and transfer counts within a date range. Validate structured LLM output and run application-owned parameterized queries limited to the caller's accounts. Return database results with supporting transfer IDs.

Generate explanations for risk flags without allowing the model to make payment decisions. Treat transfer notes as untrusted input.

Provide date-range statement summaries with opening balance, gross credits and debits, closing balance, and transfer counts from one consistent ledger snapshot. Support cursor pagination alongside offset pagination and verify behavior during concurrent postings.

### 8. Verify and Document

Add reproducible seed data, query benchmarks, setup instructions, and design trade-offs. Compare deep-offset and cursor statement plans against a large ledger and keep Swagger and Postman aligned with the implemented API.

## Key Acceptance Checks

- Fresh migrations and application startup work with Docker Compose.
- A wallet with 50,000 paise handles 100 concurrent outgoing transfers of 1,000 paise with exactly 50 successes, a zero final balance, and balanced entries; repeat 20 times.
- Sequential and parallel retries create one operation and return identical saved responses.
- Failed transactions leave no partial money movements.
- Reconciliation detects deliberately corrupted test data and produces no findings for clean data.
- Held transfers move no money until released and cannot post twice.
- Unauthorized access and prompt injection cannot expose another user's data.
- LLM tests run without network access or API keys.
- Seed 1,000 users, 2,000 wallets, and 100,000 transfers; benchmark reconciliation against one million ledger entries and compare offset with cursor pagination.

## Initial Boundaries

Support INR only. Simulate external top-up and withdrawal settlement. Leave real payment-provider integration, production KYC integration, partial reversals, and sharding for later work.
