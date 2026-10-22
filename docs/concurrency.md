# Concurrency verification

## Unsafe counterexample

`test_unsafe_stale_balance_update_loses_money_in_cache` is an intentionally broken
test-only transfer. It reads and checks a balance without a lock, then overwrites
the stored balance using that stale value. Twenty worker sessions synchronize
their reads before writing, across 100 transfer attempts.

Observed with a 50,000-paise starting balance and 100 outgoing 1,000-paise postings:

| Measure | Unsafe result |
|---|---:|
| Posted outgoing transfers | 100 |
| Stored sender balance | 45,000 |
| Sender balance computed from ledger | -50,000 |
| Cache/ledger mismatch | 95,000 |

Every individual posting still balances, and the stored balance remains
nonnegative. Neither invariant catches a stale application-level balance check.
The test demonstrates why cached balances require ordered locks and atomic writes;
it is never imported by production code or exposed as an HTTP route.

## Safe HTTP stampede

`test_stampede_100_requests` submits 100 asynchronous HTTP requests against the
real application and PostgreSQL, each with a distinct UUID key. Authentication
and posting use the restricted runtime role. Each request sends 1,000 paise from
a wallet funded with 50,000 paise.

Twenty separate rounds passed. Every round returned:

- Exactly 50 posted transfers and 50 `INSUFFICIENT_FUNDS` responses.
- A sender balance of zero and recipient balance of 50,000 paise.
- No negative stored user-wallet balance; PostgreSQL also checks this on every write.
- Matching debit and credit totals, with every transfer individually balanced.
- A zero sum across wallet and system balances and no rows for failed attempts.

This is concurrency testing, not a throughput benchmark: FastAPI's synchronous
database work runs through its bounded thread pool. All 100 HTTP requests are
submitted together; they do not require 100 simultaneously open database sessions.

Further tests run 100 opposite-direction payments without deadlock and ten
simultaneous reversal requests with exactly one success. The deferred commit
failure test verifies that balances, entries, transfer rows and audit events all
roll back together.

## Reproduce

```sh
docker compose --profile test run --build --rm test pytest tests/transfers/test_concurrency.py -q -s
docker compose --profile test run --rm test pytest tests/database/test_isolation.py -q
```

These tests create disposable schemas and remove them afterward. The two-session
commands and captured output are in [isolation.md](isolation.md). Application SQL
and isolation trade-offs are described in [transactions.md](transactions.md).
