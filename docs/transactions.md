# Money movement

The posting code is in `wallet/transfers.py`. It uses psycopg transactions and
explicit SQL; no ORM or balance-changing database trigger hides the money path.

## Transaction boundary

1. Start an explicit transaction at Read Committed isolation and claim the
   caller-scoped idempotency key before any business writes.
2. Resolve system endpoints from the transfer type.
3. Lock both account rows with the query below, in ascending ID order.
4. Check account types, sender ownership, available funds, and integer bounds
   using the balances returned while acquiring those locks.
5. Set the authenticated actor for the audit trigger, insert a pending transfer,
   and insert its debit and credit.
6. Update both cached balances while the account locks are held, then mark the
   transfer posted.
7. Save the response on the idempotency key, then commit. Deferred constraints
   check the complete posting and key completion at this boundary.

```sql
BEGIN;
SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15s';

SELECT id, user_id, account_type, balance
FROM accounts
WHERE id = ANY($1)
ORDER BY id
FOR UPDATE;

-- Claim the idempotency key before locking accounts (see idempotency.md).
-- Validate balances/ownership, insert transfer and both ledger lines,
-- update the two locked balances, and set status = 'posted'.
COMMIT;
```

Both account IDs are immutable, so sorting by ID gives every posting the same
lock order. In particular, an A-to-B transfer and a B-to-A transfer acquire the
same first lock. PostgreSQL updates the returned locked row version after waiting
for a competing committed update, avoiding a stale available-balance check.
There is no global application lock; unrelated account pairs can proceed together.

## Operations and permissions

| Operation | Caller | Supplied endpoints |
|---|---|---|
| Peer | Wallet owner, or admin | Source and destination wallets |
| Withdrawal | Wallet owner, or admin | Source wallet; server selects cash-out |
| Top-up | Admin | Destination wallet; server selects cash-in |
| Fee | Admin | Source wallet; server selects fee revenue |
| Full reversal | Admin | Original transfer ID; server swaps its endpoints |

Top-ups and withdrawals simulate settlement; no external payment provider is
called. Amounts must be strict positive integers in paise, within signed BIGINT
bounds. Same-account transfers, missing endpoints, unexpected system endpoints,
and unauthorized source wallets are rejected. KYC status is stored but is not yet
a payment eligibility rule.

Reversals lock the accounts in the same order and check available funds again.
They preserve the original posted transfer and append one full inverse posting.
The unique original-transfer reference prevents two reversals. A reversal that
would overdraw a user wallet is rejected, even for an admin.

## Isolation choice

Read Committed plus ordered row locks is sufficient for these two-account
invariants: every funds check and write uses locked rows, all entries and cache
updates share one transaction, and commit validates the posting. Repeatable Read
alone would still require concurrency control and can reject writes to rows
changed since the snapshot. Serializable adds dependency tracking and may abort
transactions with SQLSTATE `40001`, requiring whole-transaction retries. That is
useful for broader cross-row rules, but adds retry and monitoring costs without
replacing the explicit lock discipline here. No throughput comparison is claimed
without a benchmark.

Lock/statement timeouts fail and roll back the entire operation; the API returns
a standard error rather than partially posting. The key and saved response share
this transaction; a lost response can be recovered by retrying with the same key.
Business failures such as insufficient funds create no transfer, ledger or audit
rows, but their response is retained on the key after savepoint rollback. See
[idempotency](idempotency.md) for the full protocol.

## Verification

Integration tests cover all movement types, permissions, strict amount validation,
insufficient funds, reversals, and stable statement pagination. A deferred test
trigger fails at commit after entries and balance updates; the test verifies that
the entire posting, including audit events, disappears on rollback.

PostgreSQL references: [Read Committed and snapshot behavior](https://www.postgresql.org/docs/16/transaction-iso.html)
and [row locking and deadlocks](https://www.postgresql.org/docs/16/explicit-locking.html).
