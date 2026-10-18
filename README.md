# Wallet Ledger

An INR wallet backend using FastAPI, PostgreSQL 16, and explicit SQL.

The first milestone establishes the application foundation and accounting schema.
Payment routes and authentication will follow with the transaction milestone.
See [Plan.md](Plan.md) for the roadmap.

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
ruff check wallet tests
```
