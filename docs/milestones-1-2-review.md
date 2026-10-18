# Milestones 1 and 2: brief coverage review

The review compares the implementation to the supplied wallet brief, including
its ground rules, schema deliverables, isolation exercises and exact stampede
acceptance test. Requirements belonging to later milestones remain separate.

This records the milestones 1 and 2 verification snapshot. Milestone 3 now adds
[persistent POST replay](idempotency.md), with its own recovery tests and updated
Swagger/Postman contracts; the counts below describe the earlier review run.

## Milestone 1

| Brief requirement | Implementation and evidence |
|---|---|
| Noun inventory and table/non-table reasoning | `docs/schema.md` lists users, accounts, transfers, entries, keys, audit, risk and reporting entities, plus attributes that do not need tables |
| Attributes, plural snake_case tables, generated BIGINT primary keys | SQL migrations; catalog tests verify every entity's generated BIGINT primary key and money column types |
| Cardinalities and foreign-key placement | Schema documentation and Mermaid ER diagram; foreign keys and mapping tables in migrations |
| Many wallets per user; INR only; ownerless system accounts | Ownership/currency checks, partial system-account uniqueness and seeded clearing/revenue accounts |
| Positive amounts with a separate direction | BIGINT amount checks and constrained debit/credit direction |
| Nonnegative wallet balance; system accounts may be negative | Conditional balance check, immutable account classification and tests |
| Append-only ledger; correction by reversal | Runtime permissions, update/delete/truncate guards and exact full-reversal validation |
| Idempotency key unique per user | Unique `(user_id, key)` constraint; replay behavior remains milestone 3 |
| Query-driven indexes and write-cost explanation | Statement/history/review indexes and the index rationale table |
| README answers on stored/computed balances, shared accounts and external identifiers | Explicit design answers in the README, with detail in schema documentation |
| Schema from an empty database | Ordered checksum-protected migrations; CI starts a fresh PostgreSQL database, and tests migrate empty isolated schemas |

## Milestone 2

| Brief requirement | Implementation and evidence |
|---|---|
| Read Committed sees a concurrent committed change | Two actual psql processes and a separate psycopg-session test |
| Repeatable Read preserves its snapshot | Captured psql output and automated assertions |
| Serializable conflicting read/write reports an error | Captured SQLSTATE 40001 and rollback |
| Repeatable Read guarantee applies to a join | User/account join stays unchanged after session B updates both tables |
| Unsafe read/check/update demonstrates a race | Test-only 100-attempt lost-update example; stored 45,000 versus computed -50,000 paise |
| Lock both accounts in ascending ID order | Explicit `ORDER BY id FOR UPDATE` in the posting service; opposite-direction and three-wallet-cycle tests |
| Check funds while holding locks | Funds check uses the row version returned by the locking query |
| Atomic transfer, entries and balance changes | One psycopg transaction with deferred posting validation; failures at intermediate steps and commit all roll back |
| README isolation choice and Serializable costs | Direct README answer plus detailed transaction document; no invented benchmark numbers |
| Exactly 50 successes from 100 concurrent 1,000-paise spends against 50,000 paise | Twenty in-process API rounds against PostgreSQL; separate lightweight HTTP smoke test against Uvicorn |
| End at zero, never overdraw, balance the ledger | Database check on each wallet balance write, final balance/ledger comparisons, per-transfer matching entries |

## Stronger scenarios

The review found one input-validation defect: an offset larger than PostgreSQL's
signed BIGINT range reached the statement query and returned HTTP 500. The API now
rejects that input with the standard HTTP 422 error before running SQL, with a
regression test and the bound reflected in OpenAPI.

- Two actual psql processes demonstrate write skew on different rows: Repeatable
  Read permits an aggregate-rule violation; Serializable aborts one transaction.
- Peer payments, withdrawals and admin fees compete for the same funds.
- One busy wallet pays twenty distinct recipient wallets; each recipient's cache
  is checked against the successful payments it actually received.
- A three-wallet payment cycle completes without deadlock.
- A blocked hot-wallet payment does not prevent an unrelated pair from posting.
- A lock timeout leaves no partial posting, and a subsequent request succeeds.
- Failures before credit insertion, after the first balance update and at commit
  preserve all caches, ledger entries, transfers and audit history.
- Isolated network smoke results are saved in `docs/evidence/network-smoke.json`
  (20 requests: ten posted and ten rejected). Full network stress is opt-in.
- An earlier repeated live-network run failed with an HTTP transport read error;
  it is not counted as passing evidence. The runner now disables connection reuse
  and uses a fresh isolated stack. The smoke result does not certify sustained load.

## Verification results

- Full PostgreSQL integration suite: 131 tests passed, including the twenty
  100-request stampede rounds and seven additional contention/failure scenarios.
- Postman: 15 requests and 33 assertions passed against the running local API.
- Isolated HTTP smoke: 10 posted, 10 insufficient-funds responses, sender balance
  zero, matching statement totals and debit/credit pairs; temporary stack removed.
- Actual two-psql-process lab passed twice after merging output streams inside
  the container to preserve error/command ordering.
- Ruff lint, formatting and generated OpenAPI/Postman export checks passed.

## Qualifications and next milestones

Initial health-check scaffolding preceded the schema during setup; money-posting
code was added after the schema and protections. Git history is preserved rather
than reordered to imply a different chronology.

The database enforces the listed schema constraints. Cache/ledger coherence also
depends on using the trusted atomic posting path; the unsafe demonstration makes
that distinction explicit. Owner/superuser tampering is not prevented, and nightly
independent reconciliation belongs to milestone 4.

Milestones 1 and 2 do not complete the whole brief. Exact idempotency replay is
milestone 3; scheduled reconciliation is milestone 4; fraud decisions and statement
Q&A are milestone 5. Large-ledger query plans, the full seed dataset and the final
demo are later delivery work. Their supporting tables are not claimed as working
features.
