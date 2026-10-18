# Idempotent POST operations

Every POST requires an authenticated caller and a UUID `Idempotency-Key`.
Use a fresh key for a new operation; retain the same key when retrying after a
timeout or lost response. Keys are unique per authenticated user, not globally.

| Request | Result |
|---|---|
| Missing or malformed key | 400 `INVALID_IDEMPOTENCY_KEY` |
| New key | Execute once and save the response in the same transaction |
| Same key and request | Original status and identical JSON bytes; `Idempotent-Replay: true` |
| Same key, different body, endpoint or resource | 422 `IDEMPOTENCY_KEY_REUSED`; no new operation |
| Original request still holds the key after the lock timeout | 409 `IDEMPOTENCY_IN_PROGRESS`; retry the same key |

The request fingerprint is SHA-256 over canonical JSON containing the method,
concrete URL path and validated body. JSON field ordering and whitespace do not
matter; model defaults and normalization are applied before hashing. Reversal
has no defined request body. POST routes currently have no query parameters.
Keys share one namespace across all POST routes for a caller.

## Transaction and concurrency

`wallet/idempotency.py` owns the explicit transaction. The account and transfer
services receive its connection and do not open or commit a second connection.

```sql
BEGIN;
SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15s';
INSERT INTO idempotency_keys (user_id, key, request_hash)
VALUES ($1, $2, $3)
ON CONFLICT (user_id, key) DO NOTHING RETURNING id;
-- If claimed: run the business operation inside a savepoint, then save its result.
-- Money posting locks both accounts in ascending ID order as before.
UPDATE idempotency_keys SET status = 'completed', response_code = $4,
    response_body = $5 WHERE id = $6;
COMMIT;
-- Only now send the response.
```

The unique index makes simultaneous duplicates wait for the first transaction.
After it commits, a separate SELECT at Read Committed sees the saved response.
If it rolls back, a waiting insertion can claim the key and execute instead.
No application mutex or durable half-finished processing marker is needed.
Only a timeout while claiming the key becomes the in-progress 409; account-lock
and other database failures retain the existing rollback/503 behavior.

Migration `0005_idempotency_guards.sql` rejects an incomplete key at commit and
prevents rewriting a completed response or changing a key's identity. Deferred
ledger checks and key completion must all pass before any response is returned.

## Failures and exact replay

A domain failure during execution rolls business writes back to a savepoint;
its 4xx response is then saved on the key. For example, insufficient funds still
replays the original 409 after a subsequent top-up. A new attempt needs a new key.
These failures leave no transfer, ledger or transfer-status events.

Request validation, authentication and route-level permission checks happen
before claiming a key. They are not saved. Unexpected exceptions, database
timeouts, response validation failures and deferred commit failures roll back
the entire transaction including the key. A retry can then claim it again.

First responses and replays use the same sorted JSON encoding, independent of
JSONB's key order. The status and body remain identical; transport headers such
as `X-Request-ID` are fresh per request. A replayed error body retains its original
request ID, while its header identifies the retry. The initial saved response
has `Idempotent-Replay: false`.

Authentication and route-level authorization run on every retry, including admin
checks. Revoked tokens cannot replay responses. User creation replays the same
generated token, so its saved response contains a credential: treat the key table
and backups as sensitive. Authentication itself still compares SHA-256 hashes.
No public endpoint lists saved responses. Keys are retained indefinitely for now;
optional expiry belongs to the later maintenance job.

This provides one durable database operation per retained caller/key. It does
not make a promise about external payment providers, which are not called here.

## Verification

`tests/test_idempotency.py` covers five sequential retries, ten parallel retries,
changed amounts, exact response bytes, all POST resources and saved failures.
The generated Postman collection includes a peer replay and a changed-amount
rejection, followed by a final balance check.

`tests/test_idempotency_recovery.py` adds transaction and deferred-commit fault
injection, waiting-claim rollback recovery, a bounded busy-key response,
simultaneous different-body contenders, caller/resource/endpoint separation,
revoked credentials and admin permission checks, new credentials for the same
caller, Unicode/default-value normalization and immutable saved results.
Parallel same-key reversals also return one posting.

The isolated network smoke adds ten simultaneous replays of an already posted
payment and one changed-amount retry. It compares actual HTTP response bytes and
then verifies unchanged balances and matching ledger pairs. Its disposable data
never reaches the development database. The heavier network stress mode remains
opt-in.
