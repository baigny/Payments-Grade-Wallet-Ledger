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

Use a configurable OpenAI-compatible hosted provider with an offline mock. Exercise the provider HTTP contract in every test run and verify real model behavior separately with synthetic PostgreSQL data. Keep credentials in local environment configuration and bound explanation batches for hosted quotas.

Coordinate hosted request and token budgets across the API and worker. Reserve quota before calling the model, respect provider cooldowns, and return retryable errors without consuming idempotency keys or changing money.

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


## Brief Verification

Checked against the supplied brief on 23 October 2026. This checklist separates
implemented behavior from process qualifications, missing source material and
explicitly excluded work. Benchmark results below are the recorded measurements;
they are not fresh production-capacity claims.

### Ground Rules and Domain Requirements

| Brief item | Status and evidence |
| --- | --- |
| PostgreSQL 15 or later; chosen backend framework | Complete: PostgreSQL 16, FastAPI and psycopg in `compose.yml` and `pyproject.toml` |
| Visible queries, transaction boundaries and row locks | Complete: `wallet/transfers/service.py` and `wallet/core/idempotency.py`; no ORM |
| One command starts services and shows test results | Complete: default Compose runs migrations and the full test service; API/worker require test success. See [Quick start](README.md#quick-start) |
| Whole paise in BIGINT; no floating-point money | Complete: migrations and `tests/database/test_schema.py` |
| User name, email, phone and KYC status | Complete: users schema, request validation and user tests |
| Multiple INR wallets per user | Complete: accounts constraints and wallet tests |
| Ownerless cash-in, cash-out and fee accounts | Complete: migrations seed all three; system accounts may be negative |
| Top-up, peer, withdrawal, fee and reversal transfers with status/amount/time | Complete: transfer schema, routes and transfer tests |
| Balanced entries and nonnegative user balances | Complete: posting constraints and `tests/database/test_posting_guards.py`; held/rejected transfers legitimately have no entries |
| Immutable ledger; corrections reference the original through a reversal | Complete: triggers, runtime permissions and reversal tests |
| Caller-chosen idempotency, replay and changed-body rejection | Complete: `wallet/core/idempotency.py` and core replay tests |
| Balance and newest-first paginated statement | Complete: wallet/statement services and statement tests |
| Nightly discrepancy reports; fraud review; scoped statement questions | Complete: reconciliation, fraud and statement feature packages |
| Actor/time audit for every transfer status change | Complete: immutable status-event triggers and audit tests; system actors may be null |

### Milestone 1 ? Schema

| Brief item | Status and evidence |
| --- | --- |
| Noun list, storage/non-storage decisions and attributes | Complete: [schema design](docs/schema.md) |
| Generated BIGINT primary keys and snake_case naming | Complete: SQL migrations and catalog tests for every table |
| Cardinalities, foreign keys and mapping entities | Complete: schema relationship decisions and Mermaid diagrams |
| Positive entries with separate debit/credit direction | Complete: CHECK constraints and database tests |
| Nonnegative wallets; immutable ledger; unique per-user keys | Complete: database checks, mutation guards and uniqueness tests |
| Indexes for actual queries and their write costs | Complete: schema index rationale, fraud timings and pagination plans |
| README explains cached/computed balances, shared accounts and external identifiers | Complete: [database design answers](README.md#database-design) |
| Migrations create an empty installation | Complete: migration tests and fresh Compose startup |
| Schema designed before any application code | Qualification: initial health scaffolding preceded the schema. Money-posting code followed it; history has not been rewritten |

### Milestone 2 ? Transactions and Concurrency

| Brief item | Status and evidence |
| --- | --- |
| Two-session Read Committed demonstration | Complete: captured commands/output in [isolation lab](docs/isolation.md) and database tests |
| Repeatable Read demonstration, including a join | Complete: the same lab and isolation assertions |
| Serializable conflict and its error | Complete: captured SQLSTATE 40001 and write-skew tests |
| Unsafe implementation demonstrates lost updates/overdraft | Complete: test-only counterexample and [concurrency evidence](docs/concurrency.md) |
| Ascending account locks, locked balance check and atomic posting | Complete: explicit SQL in transfer service and rollback/failure tests |
| Explain isolation choice and Serializable retry cost | Complete: README and [transaction design](docs/transactions.md); no unmeasured throughput claim |
| 100 concurrent spends of 1,000 paise from 50,000; exactly 50 succeed | Complete: 20 parameterized stampede rounds, zero final balance and balanced entries |
| Wallet never goes negative, including under contention | Complete: every balance write is constrained; mixed-operation, opposite-direction and reversal race tests |

### Milestone 3 ? Idempotency

| Brief item | Status and evidence |
| --- | --- |
| UUID Idempotency-Key required on every POST; missing/invalid is 400 | Complete: write-key dependency and authenticated request-guard tests |
| Per-user unique key, SHA-256 request hash, state and saved response | Complete: migrations and idempotency service; hash also binds method/path |
| Claim before work; operation and response commit together | Complete: explicit transaction/savepoint implementation and rollback recovery tests |
| Exact completed status/body replay | Complete: canonical bytes on first response and replay; sequential/parallel assertions |
| Five sequential and ten parallel retries move money once | Complete: `tests/core/test_idempotency.py` |
| Different amount with the same key gives 422 and no further movement | Complete: replay tests and Postman flow |
| Optional deletion after 24 hours | Not enabled: retaining keys preserves replay protection; not a mandatory requirement |
| Missing response-behavior table in supplied brief | Unverifiable: prose requirements are implemented; absent table cells cannot be checked |

### Milestone 4 ? Reconciliation

| Brief item | Status and evidence |
| --- | --- |
| SQL for global balance, per-transfer balance, caches, overdrafts and orphans | Complete: six raw checks in `wallet/reconciliation/queries.py` |
| One Repeatable Read snapshot for all checks | Complete: service configuration and concurrent-posting snapshot test |
| Reports never repair money; persist run and expected/actual mismatches | Complete: immutable runs/findings/problems and reconciliation tests |
| One run at a time using a job row and FOR UPDATE NOWAIT | Complete: job-lock SQL and overlapping-run recovery tests |
| Scheduled execution and manual admin endpoint | Complete: worker, scheduled-date uniqueness and route tests |
| 10,000-transfer corruption fixture detects three deliberate changes | Complete with interpretation: three primary problems, plus separately retained downstream raw findings; see [report semantics](docs/reconciliation.md) |
| EXPLAIN ANALYZE against one million ledger entries | Complete: [recorded plans](docs/perf.md) and JSON evidence for every check |
| README explains scans versus indexes | Complete: reconciliation section and linked plan rationale |

### Milestone 5 ? Fraud and Model Safety

| Brief item | Status and evidence |
| --- | --- |
| SQL rules run inside transfer transaction with database thresholds | Complete: amount, velocity and rolling-total policies in `wallet/fraud/service.py` |
| Held transfers move no money until admin release | Complete: hold/release/reject, rechecked funds and concurrent-review tests |
| Index and measured cost for each rule | Complete: [fraud policies](docs/fraud.md), [overhead measurements](docs/fraud-perf.md) and JSON evidence |
| Nightly explanations use rule plus wallet's last ten prior transfers as JSON | Complete: explanation payload and wallet-isolation/cutoff tests; eligible backlog drains in bounded batches |
| Two sentences and one allowed advisory label, saved in risk_flags | Complete: schema validation, explanation tests and live Groq check |
| Four supported question types and validated structured intent | Complete: statement schema/service and ten checked samples |
| Application-owned parameterized SQL always scoped to caller's accounts | Complete: owner checks, unknown-field rejection and SQL-looking-name/injection tests |
| Return the database value and contributing transfer IDs | Complete: all four sample types and hosted-provider acceptance check |
| Injection in a transaction note cannot widen access | Complete: statement and explanation history-isolation tests |
| Small provider interface plus fixed JSON mock without external API/key | Complete: mock, hosted adapter and always-run loopback HTTP protocol test |
| Real hosted model | Complete: recorded Groq acceptance evidence; quota/cooldown tests do not call Groq |
| Missing fraud-rule table in supplied brief | Unverifiable: implemented defaults are explicit assumptions; exact absent thresholds/actions cannot be certified |

### API and Submission

| Brief item | Status and evidence |
| --- | --- |
| Integer-paise amounts and a UUID key on every POST | Complete: relevant amount schemas, router dependencies and API contract checks |
| Statement ORDER BY created_at DESC, id DESC with LIMIT/OFFSET | Complete: statement SQL, boundary tests and API examples |
| OFFSET 100000 measured; bonus cursor comparison | Complete: [recorded statement plans](docs/statement-perf.md), seek cursor API and concurrency/boundary tests |
| One JSON error shape with code, message and request_id | Complete: shared handlers and error-shape tests; provider failures have specific safe codes |
| Minimum-endpoint table | Unverifiable: absent from supplied text; prose operations are implemented and exported in Swagger/Postman |
| One repository and passing stampede/idempotency/reconciliation/injection CI | Complete: grouped test suite and `.github/workflows/ci.yml` |
| Exact seed: 1,000 users, 2,000 wallets, 100,000 transfers | Complete: seed defaults, disposable full-size verifier and [seed evidence](docs/seed.md); normal startup does not populate review data |
| Schema and isolation documentation | Complete: linked deliverables above |
| README design answers, assumptions, trade-offs and next steps | Complete: required design answers, assumptions and next steps are in README |
| Five-minute demo video | Excluded at the user's request; not delivered |
| Sharding/consistent hashing | Optional stretch, not implemented |
| Public production deployment | Not required by the supplied brief; current Compose configuration is for local review |

### Verification Boundaries

The stampede isolates locking behavior using active rules with non-triggering
thresholds; separate fraud tests exercise the configured defaults. Performance
artifacts use documented synthetic fixtures. Test schemas and the isolated
startup verification never reset the application database. Hosted provider
verification is explicit and separate from offline CI. The checklist does not
certify unavailable table contents or claim the excluded video was produced.


Verification for this audit: a fresh snapshot without `.env` ran all 271 tests
through plain `docker compose up`, then started a healthy API and the worker.
The isolated Postman flow passed 36 requests and 83 assertions. An intentional
failing-test override returned exit 1 and left API/worker unstarted. Temporary
verification data was removed. Lint, formatting and API-export checks passed;
previous benchmark artifacts were inspected rather than remeasured.
