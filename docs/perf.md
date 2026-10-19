# Reconciliation query plans

Dataset: 500,000 transfers, 1,000,000 ledger entries.
Fresh isolated PostgreSQL; no host ports; temporary services and data removed.
Synthetic top-ups share one wallet. These are single-run query measurements,
not production latency estimates or concurrent posting benchmarks.

| Group | Query | Execution ms | Rows | Shared hits | Shared reads | Temp writes |
|---|---|---:|---:|---:|---:|---:|
| checks | books_balance | 137.494 | 0 | 7670 | 1676 | 0 |
| checks | transfer_balance | 1679.899 | 0 | 1000247 | 2488 | 0 |
| checks | stored_balance | 275.942 | 0 | 9363 | 0 | 0 |
| checks | overdraft | 401.051 | 0 | 9363 | 0 | 0 |
| checks | orphan_transfer | 2510.135 | 0 | 1002738 | 7539 | 0 |
| checks | orphan_entry | 751.625 | 0 | 13132 | 2544 | 0 |
| problems | entry_mismatch | 2166.032 | 0 | 13071 | 2448 | 9621 |
| problems | entry_count | 3423.569 | 0 | 998557 | 11720 | 0 |
| problems | stored_balance | 718.871 | 0 | 8199 | 4148 | 0 |
| problems | overdraft | 1107.849 | 0 | 8327 | 4020 | 0 |
| problems | orphan_entry | 460.645 | 0 | 13712 | 1964 | 0 |

All queries returned zero discrepancies. Statistics were collected before measurement.
Full EXPLAIN ANALYZE/BUFFERS plans and PostgreSQL version are in
[the JSON artifact](evidence/reconciliation-plans.json). The fixture bypasses
triggers only while bulk-loading a new private schema; it then validates every
invariant and comparison. This does not measure trigger or transfer throughput.

Global sums and full nightly aggregates must read the ledger; sequential scans
are reasonable. Transfer grouping and orphan checks can use ledger_entries(transfer_id);
account grouping and missing-leg lookups can use the account/time index
and the transfer index. The planner may still choose scans/hash joins for a full audit.
Primary-key indexes support orphan joins. Reports use (run_id, id) for pagination.
No new ledger index was added just for this plan: every index costs posting writes.

Reproduce: `docker compose build api`, then
`python scripts/run_reconciliation_benchmark.py --entries 1000000`.
