# Fraud rule overhead

Isolated PostgreSQL, 10,000 prior outgoing payments, 30 measured commits per case
after three warm-ups. Rules are enabled with non-triggering thresholds.
Baseline bypasses fraud SQL. Cases run sequentially on one growing fixture.
These single-machine samples exclude HTTP, authentication and idempotency.

| Case | Mean ms | Median ms | P95 ms | Mean minus baseline ms |
|---|---:|---:|---:|---:|
| baseline | 6.783 | 6.655 | 8.625 | 0.000 |
| amount | 6.311 | 6.214 | 7.288 | -0.473 |
| velocity | 11.511 | 10.374 | 16.812 | 4.727 |
| daily_total | 10.211 | 9.789 | 13.343 | 3.427 |
| all | 10.881 | 10.786 | 12.051 | 4.098 |

Differences can include cache and timing noise; this is not a production SLA.
The bulk fixture bypasses triggers only in its disposable schema; measured
transfers use the real transaction, ledger/audit triggers and commit checks.
Velocity and rolling totals use the partial source/time index, including amount.
Full evidence: [JSON](evidence/fraud-overhead.json). Reproduce with
`python scripts/run_reconciliation_benchmark.py --fraud`.
