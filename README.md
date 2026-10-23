# Wallet Ledger

An INR wallet backend using FastAPI, PostgreSQL 16 and explicit SQL. It supports
wallets, top-ups, peer payments, withdrawals, reversals, fraud review and
ledger-backed statement questions. Transfers use double-entry accounting,
ordered row locks and caller-scoped idempotency.

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
To run this flow automatically, install Node.js and use the
[local Python tools](#local-python-tools) environment, then run:

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
volume and an internal network without published ports. It also sends ten parallel
retries of a successful payment and rejects a changed amount with that key, then
checks the balances again. Temporary services and data are removed even when
verification fails. The development database is untouched. Only verification
results are saved; credentials remain in memory.

For an explicitly requested heavier network run, use
`python scripts/run_network_checks.py --stress` (20 rounds of 100 requests).
CI uses the small smoke test. Network POST failures fail the run without retries;
reuse the same key when retrying a request whose response was lost.

The actual two-psql-process lab, including write skew, is reproducible with:

```sh
python scripts/psql_lab.py --output docs/isolation.md
```

Further database tests cover mixed peer/withdrawal/fee spending, twenty-recipient
fan-out, a three-wallet cycle, unrelated-wallet progress under a held lock,
timeout rollback and recovery, and failures before credit insertion and after
the first balance update. These extend the required stampede rather than replace it.

## Local Python tools

The API, migrations and worker run through the Docker commands above. Python
3.12 or later is needed locally only for the optional host-side scripts below,
such as API export, Postman, network verification and the hosted-model check.
Run commands from the repository root.

Create a virtual environment:

```sh
python -m venv .venv
```

Activate it with `.venv\Scripts\Activate.ps1` in PowerShell or
`source .venv/bin/activate` in Bash, then install this project's dependencies:

```sh
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
```

The `python scripts/...` commands in this README use that active environment.
For verification entirely inside Docker, use the full-suite command in
[Quick start](#quick-start); local Python installation is not required.

`GET /health/live` checks the application. `GET /health/ready` also checks database
connectivity and the presence of the account schema. Every response includes an
`X-Request-ID` header.

## Reconciliation

Milestone 4 checks the books in one Repeatable Read snapshot and saves reports.
Admin users can call `POST /admin/reconciliation/run` with an Idempotency-Key,
then read its summary, raw findings and comparisons against posted business events.
See [the reconciliation protocol](docs/reconciliation.md) for report semantics,
permissions, error behavior and corruption acceptance checks.

Compose automatically reads the local `.env` (ignored by Git). Use `.env.example`
for a new checkout. The nightly schedule defaults to **02:00 UTC (07:30 India)**:
`RECONCILIATION_ENABLED`, `RECONCILIATION_UTC_HOUR`, `RECONCILIATION_UTC_MINUTE`
and `RECONCILIATION_STATEMENT_TIMEOUT_MS` control it. Recreate the worker after
changing settings: `docker compose up -d --force-recreate worker`.

The worker catches up the most recent due schedule date on startup, records at
most one run for that date, and polls once per minute. It skips overlapping jobs
and retries contention/outages on the next poll. Older missed dates are not
backfilled. A completed error report consumes that schedule date; manual runs
can investigate immediately without waiting for the next night.

Run checks from a terminal with `docker compose exec -T api python -m wallet.reconciliation`.
Exit codes are 0 (passed), 2 (integrity problems), 3 (check error), and 4 (busy).
The command prints a report summary. It never changes balances or ledger entries.

The brief's one-million-entry query benchmark runs only on explicit request in
an isolated Docker database: `python scripts/run_reconciliation_benchmark.py`.
Full-ledger sums and nightly grouped checks can afford sequential scans; orphan
joins and transfer lookups benefit from primary-key and ledger transfer indexes.
Report pagination uses `(run_id, id)`. See [measured query plans](docs/perf.md).
CI keeps the normal integration suite and lightweight network smoke test.

## Fraud review and statement questions

Milestone 5 evaluates the database's amount, velocity and rolling-total rules in
the locked money transaction. Any hold action creates a transfer without moving
funds. Admin release/reject endpoints require a reason, record the actor and
recheck current balances. Flag-only actions post normally. Rule management and
review are available in Swagger and the Postman collection.

See [fraud policies](docs/fraud.md), [rule-overhead measurements](docs/fraud-perf.md)
and [the question/explanation protocol](docs/statement-questions.md).

`POST /users/{id}/statement/ask` supports total sent to a named person, total
received, the largest posted movement and movement counts in an inclusive UTC
date range. Each answer contains an integer and the contributing transfer IDs.
Amounts come from the authorized user's ledger entries. The model returns only
validated intent; it cannot choose accounts or write SQL. The ten sample
questions and expected fixture answers are checked in CI.

The default `LLM_PROVIDER=mock` is an offline deterministic adapter with fixed
JSON responses for tests. For a hosted model, set `LLM_PROVIDER=hosted`,
`LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` in ignored `.env`, then recreate
API and worker. `.env.example` includes Groq's compatible endpoint and
`openai/gpt-oss-20b`; free-tier quotas apply, with no automatic fallback.
Credentials are server configuration and never part of an API request.

The [provider acceptance check](docs/live-llm.md) covers all four question types
and an advisory explanation against PostgreSQL. Offline CI always runs the same
flow through a loopback HTTP protocol fixture. A separate explicit command uses
the real hosted model; fixture success is not a claim of live model success.
The [recorded Groq check](docs/evidence/hosted-llm.json) passed all four answers
and the explanation using synthetic data, with held money unchanged.
API and worker share rolling request/token budgets in PostgreSQL. Quota
exhaustion or provider throttling returns `503 LLM_BUSY` with `Retry-After`,
without saving an idempotent failure or automatically retrying the model call.
See [quota settings and limitations](docs/live-llm.md#quotas-and-cooldowns).

The nightly worker explains eligible risk flags using the rule snapshot and that
wallet's last ten prior transfers. It saves two sentences and an advisory label.
Notes are treated as untrusted data. This runs after reconciliation in a separate
transaction and never releases, rejects or posts a payment. Model failures remain
visible and are retried later; no fabricated answer is substituted.

Fraud defaults intentionally hold high-velocity spending. Money-path stampede
tests keep rules active with non-triggering thresholds; dedicated tests cover
threshold boundaries, simultaneous velocity checks and admin-review races.
The lightweight network test uses the same explicitly configured acceptance policy.

## Review dataset

The [seed guide](docs/seed.md) provides the required 1,000 users, 2,000 wallets
and 100,000 transfers. The full-size dataset has been verified in a disposable
schema with all posting and audit triggers enabled: 200,000 ledger entries,
200,000 status events and no reconciliation discrepancies. The loader refuses
an existing installation and never resets application data.

Statements also accept a `cursor` returned as `next_cursor` by the previous page.
For example, request `/wallets/4/statement?limit=50`, then pass its cursor to
`/wallets/4/statement?limit=50&cursor=...`. Keep offset at zero when using a cursor.
The timestamp/id boundary handles ties and avoids page shifts from newer postings.
Each page rechecks wallet ownership. A cursor is a seek position, not a snapshot
or a credential. See [measured query plans](docs/statement-perf.md).

For a date-range overview, use
`GET /wallets/4/statement/summary?from_date=2026-01-01&to_date=2026-12-31`.
It returns opening balance, gross credits/debits, closing balance and transfer
count in integer paise, from the ledger in one read-only Repeatable Read snapshot.
Dates are inclusive UTC entry dates. Holds contribute nothing; reversals appear
as new entries. The summary rechecks ownership and does not use cached balances.

If a Windows Python runner cannot discover the Compose plugin, set
`WALLET_COMPOSE_BIN` to the installed `docker-compose.exe` path. This override is
supported by benchmark, Postman and network runners; ordinary Docker commands
and Linux CI continue to use `docker compose`.


## Assumptions and next steps

Top-ups and withdrawals simulate external settlement; no bank or payment gateway
is connected. KYC status is stored without a production KYC integration. Top-ups
and fees require an admin; fees are explicit transfers rather than an automatic
withdrawal surcharge. Reversals are full and must still respect available funds.
Statement dates use inclusive UTC days; received totals include posted credits,
and counts deduplicate transfers touching more than one of the user's wallets.
Held transfers reserve no money, so release rechecks funds. Default fraud
thresholds are documented in [fraud policies](docs/fraud.md), since the supplied
brief's rule table is blank.

Stored balances make reads and funds checks cheap but add writes and contention;
reconciliation independently verifies them. Explicit locks keep the money path
readable; a busy wallet and shared system accounts still serialize. Provider
requests use a shared budget, but inference currently spans a database transaction.
These trade-offs do not establish production throughput.

The next improvements are a bounded connection pool, model jobs that call the
provider outside database transactions with recoverable leases, and metrics for
lock waits, request latency and reconciliation freshness. Production deployment
would additionally need managed secrets, HTTPS and verified backup/restore.
Partitioning and cross-shard settlement need a separate measured design. The
optional 24-hour idempotency purge is disabled to preserve replay protection.

The [brief checklist](Plan.md#brief-verification) maps each requirement to evidence
and records the missing source tables, schema-first chronology qualification and
the demo video excluded at the user's request.
