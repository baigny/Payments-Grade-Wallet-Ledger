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
Tokens are random opaque credentials; only SHA-256 hashes are stored. User
creation is admin-only and returns a new user's token once. Wallet reads and
writes require ownership, with an explicit admin override. Revoked tokens fail
authentication. The collection saves user and wallet IDs locally as requests run.

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
