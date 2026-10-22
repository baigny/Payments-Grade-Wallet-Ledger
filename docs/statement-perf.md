# Statement pagination plans

Dataset: 1,000,000 ledger entries; 500,000 entries in the measured wallet.
Isolated PostgreSQL, actual statement join/projection, LIMIT 51 (50 plus lookahead).
Both queries returned exactly the same 51 entry IDs and values.

| Query | Execution ms | Shared hits | Shared reads |
|---|---:|---:|---:|
| offset_100000 | 140.703 | 404260 | 1 |
| cursor | 0.217 | 209 | 0 |

OFFSET must visit and discard 100,000 rows before returning the page.
Seek pagination uses the existing (account_id, created_at DESC, id DESC) index
and a strict timestamp/id boundary to start near the next page. No new ledger
index or posting write cost is introduced. The id breaks timestamp ties.

The cursor boundary is taken from the preceding page. Its OFFSET-based lookup
here only constructs an equivalent benchmark position; it is not part of a
normal cursor request. Queries run sequentially with warm-cache effects; these
are single-machine plan measurements, not production latency guarantees.

Synthetic balanced top-ups are bulk-loaded only into a disposable schema with
triggers temporarily bypassed, as in the reconciliation benchmark. This is a
read-plan fixture, not an API or payment-throughput benchmark.

Cursors are wallet-bound seek positions, not authentication or database snapshots.
Newer postings do not shift subsequent pages. A late-committing, older-timestamp
entry can still appear later; pagination retains no transaction across requests.
Authorization is rechecked on every page. Invalid positions return 422.

Full plans: [JSON](evidence/statement-plans.json). Reproduce after building the API:
`python scripts/run_reconciliation_benchmark.py --statement --entries 1000000`.
