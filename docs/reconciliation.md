# Reconciliation

Reconciliation reports discrepancies; it never edits transfers, ledger entries
or account balances. All checks and counts use one Repeatable Read snapshot.
The immutable `job_locks` row is acquired with `FOR UPDATE NOWAIT` before a run
is created. Another running check returns 409 without creating a report or
consuming the manual request's idempotency key.

## Checks and saved evidence

The six queries in `wallet/reconciliation/queries.py` check global debit/credit
totals, each transfer's debit/credit totals, cached versus ledger balances,
computed wallet overdrafts, posted transfers with fewer than two entries, and
entries without a transfer or account. Held/rejected transfers legitimately
have no entries. System accounts can have negative balances.

`reconciliation_findings` preserves every raw invariant violation. One changed
entry can fail several checks, so finding_count is not a count of tampering
actions. `reconciliation_problems` separately compares entry amounts/endpoints
and cardinality against posted transfer records and compares cached balances
against those records' money movements. Nothing is silently suppressed.

For the brief's fixture (one changed amount, one missing credit, one altered
cache), the problems view identifies exactly those three discrepancies, while
the findings view also exposes downstream balance failures. These comparisons
assume the posted business events are the reference; they do not prove which
record a privileged attacker changed when several sources disagree.

Each run records start/end, trigger, status, counts and safe SQLSTATE error
information. A failed SQL check rolls partial evidence back to a savepoint and
records status `error`; integrity discrepancies yield `failed`; clean books yield
`passed`. An unavailable database cannot persist its own outage report. Worker
logs report failures outside the database transaction.

## API

- `POST /admin/reconciliation/run`: authenticated admin plus UUID Idempotency-Key.
- `GET /admin/reconciliation/runs/{run_id}`: saved summary.
- `GET /admin/reconciliation/runs/{run_id}/findings`: raw violations.
- `GET /admin/reconciliation/runs/{run_id}/problems`: comparisons with transfer records.

Lists accept limit 1..100 and nonnegative BIGINT offset. Exact manual retries
return the original run, without rescanning. Use a new key for a new check.
A saved `error` report is still a completed run; investigating or retrying checks
requires a new key. Snapshot serialization failures retry the entire rolled-back
transaction up to three times, including concurrent duplicate manual requests.

Local `.env` is ignored by Git; `.env.example` documents supported settings.
`RECONCILIATION_STATEMENT_TIMEOUT_MS` bounds each SQL statement (default five
minutes). It is validated as 1..3,600,000 milliseconds. Ordinary transfer timeouts
and isolation remain unchanged. Optional idempotency-key expiry is not enabled;
retaining keys preserves replay protection.

## Schedule and operations

The worker runs at the configured UTC hour/minute, with at most one scheduled
run per date enforced by a unique database key. On startup it catches up only
the most recent due date, then checks every minute. Multiple worker processes
share the same NOWAIT lock. A busy job is retried on a later poll. A completed
error report reserves its date, so a persistent SQL error does not flood reports;
manual runs remain available. The optional cleanup of old idempotency keys is
not enabled.

The nightly worker also drains eligible flag explanations in a separate transaction.
That provider step cannot authorize payments or invalidate a saved integrity report.
See [the explanation boundary](statement-questions.md).

SIGTERM/SIGINT stop future polls and allow the active transaction to finish;
forced termination rolls back an unfinished report. A database outage cannot
produce a durable error row, so the worker logs a safe error class and retries.
Run `python -m wallet.reconciliation` inside Compose for a CLI summary.

The 10,000-transfer corruption test runs in a temporary test schema with valid
postings before tampering. Only the schema owner disables triggers locally for
the three deliberate corruptions. Runtime API credentials cannot do this. Tests
also cover computed overdrafts, orphan entries after privileged FK bypass, held
transfers, repeatable snapshots, busy-key recovery and partial-report rollback.

The explicit benchmark creates a separate Docker database and one synthetic
schema. [Measured plans](perf.md) contain all six raw checks and the five event
comparisons. This performance fixture bypasses per-row triggers only during
bulk loading and then checks every invariant. It measures query execution,
not posting throughput. The temporary database and volume are removed afterward.
