# Wallet Ledger

An INR wallet backend using FastAPI, PostgreSQL 16 and explicit SQL. It supports
wallets, top-ups, peer payments, withdrawals, reversals, fraud review and
ledger-backed statement questions. Transfers use double-entry accounting,
ordered row locks and caller-scoped idempotency.

## Demo video

Watch the [project demo on Google Drive](https://drive.google.com/file/d/13-AY8npkHTVOsVq-VgqlgNWwNEJ6cCnu/view?usp=drive_link).

## Quick start

Requirements: Git and Docker with Docker Compose. Start Docker before running
these commands:

```sh
git clone https://github.com/baigny/Payments-Grade-Wallet-Ledger.git
cd Payments-Grade-Wallet-Ledger
docker compose up
```

From an existing checkout, run only `docker compose up` in the repository
root. This starts PostgreSQL, applies migrations and runs the full test suite.
The API and nightly worker start after every test passes. Test results appear
in the terminal; allow several minutes before opening Swagger. A test failure
blocks fresh API/worker startup. The tests always use the offline mock and never
need a Groq key, even if your API is configured for Groq in local `.env`.
After pulling code changes, use `docker compose up --build` to rebuild images.

Open [Swagger UI](http://localhost:8000/docs) to inspect and try the API.

No second command is required to run the tests. To rerun only the suite later:

```sh
docker compose run --rm test
```

For authenticated requests, create the first administrator and copy the generated
token into Swagger's **Authorize** dialog or Postman's `admin_token` variable:

```sh
docker compose exec -T api python -m wallet.bootstrap
```

The command prints the token once. If an administrator already exists, use
`docker compose exec -T api python -m wallet.bootstrap --issue-token` to issue
another local credential. Every POST also requires a UUID `Idempotency-Key`.

Stop the services while keeping database data:

```sh
docker compose down
```

| Local service | Address |
| --- | --- |
| API | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| OpenAPI JSON | http://localhost:8000/openapi.json |
| PostgreSQL | `localhost:55432` |

Optional overrides are listed in [.env.example](.env.example). The default
passwords are local development values. PostgreSQL and the API bind to localhost.
The API and worker use `wallet_app`; migrations and tests use the owner login.
For hosted Groq configuration and verification, see [the model guide](docs/live-llm.md).

## Project structure

The application is grouped by feature: users, wallets, transfers, fraud,
statements and reconciliation. Each feature owns its routes, request/response
schemas and service functions. Shared infrastructure lives in `wallet/core/`.
See [the package structure](docs/architecture.md) and [Plan.md](Plan.md).

Database constraints protect balanced postings, immutable ledger and status
history, valid transfer states, full reversals and nonnegative wallet balances.
The [money path](docs/transactions.md) explains transaction boundaries, lock order,
SQL permissions and isolation choices. The [two-session lab](docs/isolation.md)
and [concurrency results](docs/concurrency.md) record the unsafe counterexample,
20 stampede rounds, opposite-direction payments and concurrent reversals.

## Database design

**Do we store balances, compute them, or both?** We store one cached balance per
account for constant-time reads and funds checks, and compute the authoritative
movement total from immutable ledger entries when verifying the books. Computing
every read from history costs a sum over that account's entries. Caching adds a
balance write and contention on a busy wallet, and requires atomic updates with
the ledger. Posting does both writes in one transaction; independent nightly
reconciliation verifies them without repairing financial records.

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
docker compose run --rm test ruff check wallet tests scripts
```

Database tests create an isolated schema, roll back test data, and remove that
schema on completion. They never truncate application tables. Tests need an
owner connection; without `PGHOST` or `DATABASE_URL`, database tests are skipped.
CI runs the full suite against PostgreSQL through Compose.

## Swagger and Postman

Swagger is served directly from the application's OpenAPI definition. Import
[the Postman collection](postman/Wallet-Ledger.postman_collection.json) and
[local environment](postman/Local.postman_environment.json). Bootstrap the first
administrator using the [quick-start command](#quick-start) and paste its generated
token into the environment's `admin_token`.

The bootstrap command refuses to overwrite an existing admin.
After an automated demo, or when an additional local credential is needed, run
`docker compose exec -T api python -m wallet.bootstrap --issue-token`. This
requires database access and preserves existing tokens; it is not an HTTP endpoint.
Authentication stores SHA-256 hashes of random opaque tokens. User creation is
admin-only; its saved idempotency response retains the generated token so an
identical admin retry can recover it. Treat the key table and backups as sensitive. Wallet reads and
writes require ownership, with an explicit admin override. Revoked tokens fail
authentication. The collection saves user and wallet IDs locally as requests run.

The collection creates two users and wallets, funds the sender, sends a peer
payment, replays it, rejects a changed-amount retry, withdraws funds, charges a fee,
reverses the peer payment, and checks the
final sender balance. Swagger offers request examples for every movement type.
To run this flow automatically, install Node.js and use a Python 3.12+
environment with the dependencies from requirements.lock installed, then run:

```sh
python scripts/run_postman.py
```

On a fresh local database the runner bootstraps the admin automatically. If an
admin already exists, supply its token through `WALLET_ADMIN_TOKEN`. Credentials
are passed to Newman through a temporary environment file and are not logged.
CI runs this same Postman flow after the Python suite and application startup.

Every POST requires a UUID `Idempotency-Key`, unique per authenticated caller.
Use a new key for each operation and the same key for an identical retry. The
original status and JSON bytes are replayed without new writes; changed requests
return 422. See [idempotency](docs/idempotency.md) for concurrent duplicates, saved
failures, rollback behavior and response-header semantics.

The versioned [OpenAPI export](docs/api/openapi.json) and Postman collection cover
implemented routes only. Regenerate them whenever the API changes:

```sh
python scripts/export_api.py
python scripts/export_api.py --check
```

Tests fail if either export differs from the running application's contract.

## Project setup and demo guide

These commands are for **Windows Command Prompt (CMD)**, run from the repository
root. Each step includes a caption you can use in the demo recording. PostgreSQL
is required; the startup command below hides its logs, rather than disabling it.

Use a fresh demo dataset for the expected balances below. On an existing
installation, email addresses and phone numbers must be unique. Reuse the IDs
of existing demo users instead of creating duplicate users. Do not delete the
database to repeat a recording.

### 1. Clone and open the project

**Caption:** Clone the wallet backend and open its repository.

```cmd
git clone https://github.com/baigny/Payments-Grade-Wallet-Ledger.git
cd Payments-Grade-Wallet-Ledger
```

If already cloned, open CMD in your existing repository instead.

### 2. Verify Docker

**Caption:** Check that the Docker engine is available.

```cmd
docker version
```

Start Docker Desktop first if the server connection fails.

### 3. Start the complete stack

**Caption:** Start PostgreSQL, apply migrations, run every test, then start the API and worker.

```cmd
docker compose up --no-attach db
```

This is the normal startup with database log streaming hidden. Plain
`docker compose up` also works and includes database logs. Keep this terminal
running; open a second CMD window in the same repository for subsequent commands.

### 4. Verify services, tests and readiness

**Caption:** Confirm the tests passed and the application is ready.

```cmd
docker compose ps -a
docker compose logs --tail 5 test
curl.exe -sS http://localhost:8000/health/ready
```

The database, API and worker should be running. The one-time `migrate` and
`test` containers should show `Exited (0)`. A failed test blocks fresh API and
worker startup. Swagger is available at http://localhost:8000/docs.

### 5. Issue an admin token

**Caption:** Generate a local administrator credential for authenticated requests.

```cmd
docker compose exec -T api python -m wallet.bootstrap --issue-token
```

**Blur the token printed by this command in the video.** Real credentials do
not belong in the README, screenshots or committed files.

Run the following command unchanged, press Enter, paste only the newly printed
token at the prompt, then press Enter again. **Blur this input too.** Do not put
the token inside the prompt text or include the words `Bearer token`.

```cmd
set /p "ADMIN_TOKEN=Paste newly issued admin token: "
cls
```

The remaining requests use `%ADMIN_TOKEN%`; CMD substitutes the value without
printing it in the command text. Avoid `echo %ADMIN_TOKEN%` and verbose curl
options that expose authorization headers.

### 6. Set a recording prefix

**Caption:** Give each operation its own idempotency key.

```cmd
set "DEMO_RUN=20261024-0003"
```

For a later recording, choose a new prefix such as `20261024-0004`. Keep the
same prefix for a transfer and its identical retry. New keys execute new
operations: they do not reset balances or bypass unique email constraints.
Replaying an old rule update returns its saved response without applying it again.

### 7. Create the sender

**Caption:** Register nabil_sender with a unique email address and phone number.

```cmd
curl.exe -i -X POST "http://localhost:8000/users" -H "Authorization: Bearer %ADMIN_TOKEN%" -H "Idempotency-Key: %DEMO_RUN%-4000-8000-000000000001" -H "Content-Type: application/json" --data "{\"name\":\"nabil_sender\",\"email\":\"baigny@gmail.com\",\"phone\":\"+919800002401\"}"
```

Blur the `token` field in the user response. Run this command unchanged and
enter the numeric `id` returned in that response when prompted:

```cmd
set /p "SENDER_ID=Enter sender user id: "
```

### 8. Create the receiver

**Caption:** Register nabil_receiver as the payment recipient.

```cmd
curl.exe -i -X POST "http://localhost:8000/users" -H "Authorization: Bearer %ADMIN_TOKEN%" -H "Idempotency-Key: %DEMO_RUN%-4000-8000-000000000002" -H "Content-Type: application/json" --data "{\"name\":\"nabil_receiver\",\"email\":\"baigny+100@gmail.com\",\"phone\":\"+919800002402\"}"
```

Blur the response's `token` field. Enter the receiver's numeric `id`:

```cmd
set /p "RECEIVER_ID=Enter receiver user id: "
```

### 9. Create the sender wallet

**Caption:** Create an INR wallet owned by the sender.

```cmd
curl.exe -i -X POST "http://localhost:8000/users/%SENDER_ID%/wallets" -H "Authorization: Bearer %ADMIN_TOKEN%" -H "Idempotency-Key: %DEMO_RUN%-4000-8000-000000000003" -H "Content-Type: application/json" --data "{\"name\":\"Main\"}"
set /p "SENDER_WALLET=Enter sender wallet id: "
```

Enter the wallet response's `id`, not its `user_id`. IDs vary between databases;
the guide does not assume a particular user or wallet number.

### 10. Create the receiver wallet

**Caption:** Create an INR wallet owned by the receiver.

```cmd
curl.exe -i -X POST "http://localhost:8000/users/%RECEIVER_ID%/wallets" -H "Authorization: Bearer %ADMIN_TOKEN%" -H "Idempotency-Key: %DEMO_RUN%-4000-8000-000000000004" -H "Content-Type: application/json" --data "{\"name\":\"Main\"}"
set /p "RECEIVER_WALLET=Enter receiver wallet id: "
```

### 11. Inspect and prepare the amount rule

**Caption:** Show the amount, velocity and daily-total fraud rules.

```cmd
curl.exe -sS "http://localhost:8000/admin/risk-rules" -H "Authorization: Bearer %ADMIN_TOKEN%"
set /p "AMOUNT_RULE_ID=Enter the id of the amount rule: "
```

Choose the item with `"rule_type":"amount"`. The default amount threshold is
100000 paise. Set it to that value before funding; earlier demonstrations may
have lowered it. This guide restores that default after the held-transfer demo.

```cmd
curl.exe -i -X POST "http://localhost:8000/admin/risk-rules/%AMOUNT_RULE_ID%" -H "Authorization: Bearer %ADMIN_TOKEN%" -H "Idempotency-Key: %DEMO_RUN%-4000-8000-000000000005" -H "Content-Type: application/json" --data "{\"threshold\":100000,\"window_seconds\":null,\"action\":\"hold\",\"enabled\":true}"
```

### 12. Fund the sender

**Caption:** Top up the sender wallet with Rs 250, stored as 25,000 paise.

```cmd
curl.exe -i -X POST "http://localhost:8000/transfers" -H "Authorization: Bearer %ADMIN_TOKEN%" -H "Idempotency-Key: %DEMO_RUN%-4000-8000-000000000006" -H "Content-Type: application/json" --data "{\"type\":\"top_up\",\"destination_account_id\":%SENDER_WALLET%,\"amount\":25000}"
curl.exe -sS "http://localhost:8000/wallets/%SENDER_WALLET%/balance" -H "Authorization: Bearer %ADMIN_TOKEN%"
```

Expect `posted` and a balance of `25000` for a new empty wallet.

### 13. Send a peer payment

**Caption:** Transfer Rs 10 atomically from the sender to the receiver.

```cmd
curl.exe -i -X POST "http://localhost:8000/transfers" -H "Authorization: Bearer %ADMIN_TOKEN%" -H "Idempotency-Key: %DEMO_RUN%-4000-8000-000000000007" -H "Content-Type: application/json" --data "{\"type\":\"peer\",\"source_account_id\":%SENDER_WALLET%,\"destination_account_id\":%RECEIVER_WALLET%,\"amount\":1000}"
```

Expect `posted`. Note the returned transfer `id` for comparison with the retry.

### 14. Retry the identical payment

**Caption:** Retry the same request without moving money a second time.

```cmd
curl.exe -i -X POST "http://localhost:8000/transfers" -H "Authorization: Bearer %ADMIN_TOKEN%" -H "Idempotency-Key: %DEMO_RUN%-4000-8000-000000000007" -H "Content-Type: application/json" --data "{\"type\":\"peer\",\"source_account_id\":%SENDER_WALLET%,\"destination_account_id\":%RECEIVER_WALLET%,\"amount\":1000}"
```

Show the same transfer ID and `idempotent-replay: true`.

### 15. Verify both balances

**Caption:** Confirm the sender has Rs 240 and the receiver has Rs 10.

```cmd
curl.exe -sS "http://localhost:8000/wallets/%SENDER_WALLET%/balance" -H "Authorization: Bearer %ADMIN_TOKEN%"
curl.exe -sS "http://localhost:8000/wallets/%RECEIVER_WALLET%/balance" -H "Authorization: Bearer %ADMIN_TOKEN%"
```

Expected balances are `24000` and `1000` paise for the new wallets above.

### 16. Trigger fraud review

**Caption:** Lower the threshold to Rs 5 and submit a separate Rs 10 payment.

```cmd
curl.exe -i -X POST "http://localhost:8000/admin/risk-rules/%AMOUNT_RULE_ID%" -H "Authorization: Bearer %ADMIN_TOKEN%" -H "Idempotency-Key: %DEMO_RUN%-4000-8000-000000000008" -H "Content-Type: application/json" --data "{\"threshold\":500,\"window_seconds\":null,\"action\":\"hold\",\"enabled\":true}"
curl.exe -i -X POST "http://localhost:8000/transfers" -H "Authorization: Bearer %ADMIN_TOKEN%" -H "Idempotency-Key: %DEMO_RUN%-4000-8000-000000000009" -H "Content-Type: application/json" --data "{\"type\":\"peer\",\"source_account_id\":%SENDER_WALLET%,\"destination_account_id\":%RECEIVER_WALLET%,\"amount\":1000}"
```

Expect `"status":"held"`. This is a new transfer with a new key.

### 17. Verify held money has not moved

**Caption:** The held transfer creates no ledger entries and leaves balances unchanged.

```cmd
curl.exe -sS "http://localhost:8000/wallets/%SENDER_WALLET%/balance" -H "Authorization: Bearer %ADMIN_TOKEN%"
curl.exe -sS "http://localhost:8000/wallets/%RECEIVER_WALLET%/balance" -H "Authorization: Bearer %ADMIN_TOKEN%"
```

Expected balances remain `24000` and `1000` paise.

### 18. Run reconciliation

**Caption:** Check the ledger and cached balances for inconsistencies.

```cmd
docker compose exec -T api python -m wallet.reconciliation
```

This checks the whole database and saves a report without repairing money
records. Exit codes: `0` passed, `2` integrity problems, `3` check error,
`4` another run is busy. See [the reconciliation guide](docs/reconciliation.md)
for the checks and the worker's nightly schedule.

### 19. Ask a statement question

**Caption:** Answer a statement question with supporting posted transfer IDs.

For a real model demonstration, configure the ignored local `.env` using
[the hosted-model guide](docs/live-llm.md) before recording, then recreate API
and worker. Keep the API key and the contents of `.env` off screen. The default
mock is an offline test adapter; do not present its response as a live model call.

```cmd
curl.exe -i -X POST "http://localhost:8000/users/%SENDER_ID%/statement/ask" -H "Authorization: Bearer %ADMIN_TOKEN%" -H "Idempotency-Key: %DEMO_RUN%-4000-8000-000000000010" -H "Content-Type: application/json" --data "{\"question\":\"How many transfers from 2026-01-01 to 2026-12-31?\"}"
```

For new demo users and payments within that date range, expect `value: 2`:
the posted top-up and peer payment. The retry adds no transfer and the held
payment is excluded. Adjust the date range for recordings in another year.
The model supplies validated intent; application-owned SQL computes the answer.
Provider failures return an explicit error rather than a fabricated answer.

### 20. Restore the default fraud threshold

**Caption:** Restore the amount rule to Rs 1,000 after the demonstration.

```cmd
curl.exe -i -X POST "http://localhost:8000/admin/risk-rules/%AMOUNT_RULE_ID%" -H "Authorization: Bearer %ADMIN_TOKEN%" -H "Idempotency-Key: %DEMO_RUN%-4000-8000-000000000011" -H "Content-Type: application/json" --data "{\"threshold\":100000,\"window_seconds\":null,\"action\":\"hold\",\"enabled\":true}"
```

This does not release the held transfer; it remains awaiting admin review.
Review endpoints and their balance rechecks are documented in
[fraud policies](docs/fraud.md).

### 21. Verify the required seed dataset

**Caption:** Verify 1,000 users, 2,000 wallets and 100,000 transfers without changing demo data.

```cmd
docker compose run --rm migrate python -m scripts.verify_seed
```

The verifier applies migrations and loads the full dataset in a disposable
schema, then removes that schema. The live application's users and wallets are
untouched. For loading an empty application database instead, see
[the seed guide](docs/seed.md).

### 22. Stop the project

**Caption:** Stop the services while preserving the database for the next session.

```cmd
docker compose down
```

Do not add `-v` when you want to preserve database data. Close the demo CMD
window afterward to clear its token variable.

## Trade-offs and next improvements

Top-ups and withdrawals simulate external settlement; KYC has no production
integration. Fees are explicit admin transfers and reversals are full. Held
payments reserve no money, so release rechecks funds. Statement dates use
inclusive UTC days. See [fraud policies](docs/fraud.md) for the default rules.

Cached balances make reads cheap but add writes and contention; reconciliation
verifies them independently. Ordered locks keep posting explicit, while busy
wallets and shared system accounts still serialize. Hosted model calls share
database-backed quotas, but currently span a database transaction. These choices
do not establish production throughput.

Next improvements are a bounded connection pool, recoverable model jobs that
call providers outside database transactions, and metrics for lock waits,
latency and reconciliation freshness. Production deployment also needs managed
secrets, HTTPS and verified backup/restore. Partitioning requires a measured
design. Optional idempotency expiry remains disabled to preserve replay history.
