# Wallet Ledger

An INR wallet backend using FastAPI, PostgreSQL 16, and explicit SQL.

The first milestone establishes the application foundation and accounting schema.
Payment routes and authentication will follow with the transaction milestone.
See [Plan.md](Plan.md) for the roadmap.

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
[local environment](postman/Local.postman_environment.json), then run the two
health requests. The readiness request expects the migrated database to be up.

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
