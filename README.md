# Wallet Ledger

An INR wallet backend using FastAPI, PostgreSQL 16, and explicit SQL.

The backend includes the accounting schema, bearer authentication, user and wallet
creation, balance reads, paginated statements, and atomic money movement.
See [Plan.md](Plan.md) for the roadmap.

Milestone 1 includes database constraints for balanced postings, immutable ledger
and status history, valid transfer states, full reversals, and nonnegative stored
wallet balances. The tests cover invalid direct SQL as well as application health.

Milestone 2 adds explicit Read Committed transactions, account locks acquired by
ascending ID, and rollback-safe posting. See [the money path](docs/transactions.md)
for SQL, permissions, and the isolation-level trade-off.
The [two-session isolation lab](docs/isolation.md) includes captured commands and
output. [Concurrency results](docs/concurrency.md) cover the unsafe counterexample,
20 stampede rounds, opposite-direction payments, and concurrent reversals.

## Run with Docker

```sh
docker compose up --build
```

PostgreSQL becomes healthy first, the migration service creates the schema, and
the API and worker start only after migrations finish. The worker is currently an
idle process with graceful shutdown; scheduled reconciliation comes later.

Local services:

- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- OpenAPI JSON: http://localhost:8000/openapi.json
- PostgreSQL: `localhost:55432`

Optional overrides are listed in [.env.example](.env.example). The default
passwords are local development values. PostgreSQL and the API bind to localhost.
The API and worker use `wallet_app`; migrations and tests use the owner login.

## Database design

**Do we store balances, compute them, or both?** We store one cached balance per
account for constant-time reads and funds checks, and compute the authoritative
movement total from immutable ledger entries when verifying the books. Computing
every read from history costs a sum over that account's entries. Caching adds a
balance write and contention on a busy wallet, and requires atomic updates with
the ledger. Posting does both writes in one transaction; independent nightly
reconciliation is milestone 4.

**Why share one table for wallets and system accounts?** Both are endpoints of
the same money movement. One table gives ledger entries one account foreign key
and one posting path for top-ups, payments, withdrawals and fees. Checks require
wallet ownership and nonnegative wallet balances while allowing ownerless system
accounts to go negative.

**Why aren't email or phone primary keys?** Names, email addresses and phone
numbers originate outside the database and can change or be corrected. KYC status
also changes over time. Generated BIGINT IDs keep relationships stable. Unique
email/phone constraints handle identity collisions without making those mutable
values the primary key.

**Why Read Committed with locks?** Each transfer acquires both immutable account
IDs in ascending order, checks the locked balances, and posts entries and caches
atomically. Competing requests wait and then use the latest committed balance.
Serializable would add dependency tracking and possible SQLSTATE `40001` aborts,
requiring whole-transaction retries and reducing useful throughput under conflicts.
It is appropriate for broader cross-row invariants; the write-skew lab shows why.
No numerical throughput comparison is claimed without measurement.

See [docs/schema.md](docs/schema.md) for the noun inventory, cardinalities, ER
diagram, accounting conventions, and index rationale. Migrations live in
`migrations/` and are applied with checksums and a transaction advisory lock.

```sh
docker compose run --rm migrate
docker compose --profile test run --build --rm test
docker compose --profile test run --rm test ruff check wallet tests scripts
```

Database tests create an isolated schema, roll back test data, and remove that
schema on completion. They never truncate application tables. Tests need an
owner connection; without `PGHOST` or `DATABASE_URL`, database tests are skipped.
CI runs the full suite against PostgreSQL through Compose.

## Swagger and Postman

Swagger is served directly from the application's OpenAPI definition. Import
[the Postman collection](postman/Wallet-Ledger.postman_collection.json) and
[local environment](postman/Local.postman_environment.json). Bootstrap the first
administrator and paste its generated token into the environment's `admin_token`:

```sh
docker compose run --rm api python -m wallet.bootstrap
```

The command prints the token once and refuses to overwrite an existing admin.
After an automated demo, or when an additional local credential is needed, run
`docker compose run --rm api python -m wallet.bootstrap --issue-token`. This
requires database access and preserves existing tokens; it is not an HTTP endpoint.
Tokens are random opaque credentials; only SHA-256 hashes are stored. User
creation is admin-only and returns a new user's token once. Wallet reads and
writes require ownership, with an explicit admin override. Revoked tokens fail
authentication. The collection saves user and wallet IDs locally as requests run.

The collection creates two users and wallets, funds the sender, sends a peer
payment, withdraws funds, charges a fee, reverses the peer payment, and checks the
final sender balance. Swagger offers request examples for every movement type.
To run this flow automatically, install Node.js and run:

```sh
python scripts/run_postman.py
```

On a fresh local database the runner bootstraps the admin automatically. If an
admin already exists, supply its token through `WALLET_ADMIN_TOKEN`. Credentials
are passed to Newman through a temporary environment file and are not logged.
CI runs this same Postman flow after the Python suite and application startup.

Every POST requires a UUID `Idempotency-Key`. This milestone validates the header;
Milestone 3 will add persistence and exact response replay. Use a fresh key for
each operation and do not rely on retries being deduplicated yet.

The versioned [OpenAPI export](docs/api/openapi.json) and Postman collection cover
implemented routes only. Regenerate them whenever the API changes:

```sh
python scripts/export_api.py
python scripts/export_api.py --check
```

Tests fail if either export differs from the running application's contract.

## Milestones 1 and 2 verification

[The brief coverage review](docs/milestones-1-2-review.md) maps each requirement
to its implementation and evidence. The required 20 rounds of 100 concurrent
requests remain in the database integration suite. For a lightweight real HTTP
check from a separate container:

```sh
docker compose build api
python scripts/run_network_checks.py --output docs/evidence/network-smoke.json
```

The default runs one round of 20 requests: ten post and ten fail for insufficient
funds. It checks cached balances, statement totals and every successful payment's
matching debit/credit pair. Each run uses a unique Compose project, its own database
volume and an internal network without published ports. Temporary services and
data are removed even when verification fails. The development database is untouched.
Only counts and balances are saved; credentials remain in memory.

For an explicitly requested heavier network run, use
`python scripts/run_network_checks.py --stress` (20 rounds of 100 requests).
CI uses the small smoke test. Network POST failures fail the run without retries;
idempotent replay is a later milestone.

The actual two-psql-process lab, including write skew, is reproducible with:

```sh
python scripts/psql_lab.py --output docs/isolation.md
```

Further database tests cover mixed peer/withdrawal/fee spending, twenty-recipient
fan-out, a three-wallet cycle, unrelated-wallet progress under a held lock,
timeout rollback and recovery, and failures before credit insertion and after
the first balance update. These extend the required stampede rather than replace it.

## Local Python setup

Requires Python 3.12 or later.

```sh
python -m venv .venv
# Activate .venv using the command for your shell.
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
uvicorn wallet.main:app --reload
```

`GET /health/live` checks the application. `GET /health/ready` also checks database
connectivity and the presence of the account schema. Every response includes an
`X-Request-ID` header.

```sh
pytest tests/test_health.py -q
ruff check wallet tests scripts
```
