# Two-session psql isolation lab

This is captured output from two persistent `psql` processes connected to
PostgreSQL through Docker Compose. Commands were sent in the displayed order;
the outputs are from psql, not reconstructed expected results. Reproduce with:

```sh
python scripts/psql_lab.py --output docs/isolation.md
```

For manual practice, open two terminals with
`docker compose exec db psql -U wallet_owner -d wallet`. Run each labeled command
in its matching terminal. The generated scratch schema below keeps the lab
separate from application tables. Set both sessions' search_path as shown.
The shared-reserve rule in the write-skew section is a teaching example, not a
new wallet requirement. All lab amounts are integer paise.

Session A

```sql
CREATE SCHEMA psql_lab_a1148c5cd1e64b5b9f4d02f180762f44;
```

```text
CREATE SCHEMA
```

Session A

```sql
SET search_path TO psql_lab_a1148c5cd1e64b5b9f4d02f180762f44;
```

```text
SET
```

Session B

```sql
SET search_path TO psql_lab_a1148c5cd1e64b5b9f4d02f180762f44;
```

```text
SET
```

Session A

```sql
CREATE TABLE lab_users (id BIGINT PRIMARY KEY, name TEXT NOT NULL);
```

```text
CREATE TABLE
```

Session A

```sql
CREATE TABLE lab_accounts (id BIGINT PRIMARY KEY, user_id BIGINT, balance BIGINT);
```

```text
CREATE TABLE
```

Session A

```sql
INSERT INTO lab_users VALUES (1, 'Meera');
```

```text
INSERT 0 1
```

Session A

```sql
INSERT INTO lab_accounts VALUES (1, 1, 1000), (2, 1, 1000);
```

```text
INSERT 0 2
```

## Read Committed

Session B

```sql
UPDATE lab_accounts SET balance = 1000;
```

```text
UPDATE 2
```

Session B

```sql
UPDATE lab_users SET name = 'Meera' WHERE id = 1;
```

```text
UPDATE 1
```

Session A

```sql
BEGIN ISOLATION LEVEL READ COMMITTED;
```

```text
BEGIN
```

Session A

```sql
SELECT balance FROM lab_accounts WHERE id = 1;
```

```text
1000
```

Session B

```sql
UPDATE lab_accounts SET balance = 2000 WHERE id = 1;
```

```text
UPDATE 1
```

Session A

```sql
SELECT balance FROM lab_accounts WHERE id = 1;
```

```text
2000
```

Session A

```sql
COMMIT;
```

```text
COMMIT
```

## Repeatable Read

Session B

```sql
UPDATE lab_accounts SET balance = 1000;
```

```text
UPDATE 2
```

Session B

```sql
UPDATE lab_users SET name = 'Meera' WHERE id = 1;
```

```text
UPDATE 1
```

Session A

```sql
BEGIN ISOLATION LEVEL REPEATABLE READ;
```

```text
BEGIN
```

Session A

```sql
SELECT balance FROM lab_accounts WHERE id = 1;
```

```text
1000
```

Session B

```sql
UPDATE lab_accounts SET balance = 2000 WHERE id = 1;
```

```text
UPDATE 1
```

Session A

```sql
SELECT balance FROM lab_accounts WHERE id = 1;
```

```text
1000
```

Session A

```sql
COMMIT;
```

```text
COMMIT
```

## Repeatable Read with a join

Session B

```sql
UPDATE lab_accounts SET balance = 1000;
```

```text
UPDATE 2
```

Session B

```sql
UPDATE lab_users SET name = 'Meera' WHERE id = 1;
```

```text
UPDATE 1
```

Session A

```sql
BEGIN ISOLATION LEVEL REPEATABLE READ;
```

```text
BEGIN
```

Session A

```sql
SELECT u.name, a.balance FROM lab_users u JOIN lab_accounts a ON a.user_id = u.id WHERE a.id = 1;
```

```text
Meera|1000
```

Session B

```sql
BEGIN;
```

```text
BEGIN
```

Session B

```sql
UPDATE lab_accounts SET balance = 2000 WHERE id = 1;
```

```text
UPDATE 1
```

Session B

```sql
UPDATE lab_users SET name = 'Meera updated' WHERE id = 1;
```

```text
UPDATE 1
```

Session B

```sql
COMMIT;
```

```text
COMMIT
```

Session A

```sql
SELECT u.name, a.balance FROM lab_users u JOIN lab_accounts a ON a.user_id = u.id WHERE a.id = 1;
```

```text
Meera|1000
```

Session A

```sql
COMMIT;
```

```text
COMMIT
```

## Serializable: competing writes to the same row

Session B

```sql
UPDATE lab_accounts SET balance = 1000;
```

```text
UPDATE 2
```

Session B

```sql
UPDATE lab_users SET name = 'Meera' WHERE id = 1;
```

```text
UPDATE 1
```

Session A

```sql
BEGIN ISOLATION LEVEL SERIALIZABLE;
```

```text
BEGIN
```

Session A

```sql
SELECT balance FROM lab_accounts WHERE id = 1;
```

```text
1000
```

Session B

```sql
BEGIN ISOLATION LEVEL SERIALIZABLE;
```

```text
BEGIN
```

Session B

```sql
SELECT balance FROM lab_accounts WHERE id = 1;
```

```text
1000
```

Session A

```sql
UPDATE lab_accounts SET balance = 900 WHERE id = 1;
```

```text
UPDATE 1
```

Session A

```sql
COMMIT;
```

```text
COMMIT
```

Session B

```sql
UPDATE lab_accounts SET balance = 900 WHERE id = 1;
```

```text
ERROR:  40001: could not serialize access due to concurrent update
LOCATION:  ExecUpdate, nodeModifyTable.c:2418
```

Session B

```sql
ROLLBACK;
```

```text
ROLLBACK
```

## Stronger example: write skew across different rows

Both sessions see 2,000 paise combined. Each decides it can spend 1,000 from its
own row while retaining a combined reserve of at least 1,000. They update different
rows, so this example distinguishes snapshot isolation from serializable execution.

### Repeatable Read shared-reserve experiment

Session B

```sql
UPDATE lab_accounts SET balance = 1000;
```

```text
UPDATE 2
```

Session B

```sql
UPDATE lab_users SET name = 'Meera' WHERE id = 1;
```

```text
UPDATE 1
```

Session A

```sql
BEGIN ISOLATION LEVEL REPEATABLE READ;
```

```text
BEGIN
```

Session A

```sql
SELECT sum(balance) FROM lab_accounts;
```

```text
2000
```

Session B

```sql
BEGIN ISOLATION LEVEL REPEATABLE READ;
```

```text
BEGIN
```

Session B

```sql
SELECT sum(balance) FROM lab_accounts;
```

```text
2000
```

Session A

```sql
UPDATE lab_accounts SET balance = 0 WHERE id = 1;
```

```text
UPDATE 1
```

Session B

```sql
UPDATE lab_accounts SET balance = 0 WHERE id = 2;
```

```text
UPDATE 1
```

Session A

```sql
COMMIT;
```

```text
COMMIT
```

Session B

```sql
COMMIT;
```

```text
COMMIT
```

Session A

```sql
SELECT sum(balance) FROM lab_accounts;
```

```text
0
```

### Serializable shared-reserve experiment

Session B

```sql
UPDATE lab_accounts SET balance = 1000;
```

```text
UPDATE 2
```

Session B

```sql
UPDATE lab_users SET name = 'Meera' WHERE id = 1;
```

```text
UPDATE 1
```

Session A

```sql
BEGIN ISOLATION LEVEL SERIALIZABLE;
```

```text
BEGIN
```

Session A

```sql
SELECT sum(balance) FROM lab_accounts;
```

```text
2000
```

Session B

```sql
BEGIN ISOLATION LEVEL SERIALIZABLE;
```

```text
BEGIN
```

Session B

```sql
SELECT sum(balance) FROM lab_accounts;
```

```text
2000
```

Session A

```sql
UPDATE lab_accounts SET balance = 0 WHERE id = 1;
```

```text
UPDATE 1
```

Session B

```sql
UPDATE lab_accounts SET balance = 0 WHERE id = 2;
```

```text
UPDATE 1
```

Session A

```sql
COMMIT;
```

```text
COMMIT
```

Session B

```sql
COMMIT;
```

```text
ERROR:  40001: could not serialize access due to read/write dependencies among transactions
DETAIL:  Reason code: Canceled on identification as a pivot, during commit attempt.
HINT:  The transaction might succeed if retried.
LOCATION:  PreCommit_CheckForSerializationFailure, predicate.c:4662
```

Session A

```sql
SELECT sum(balance) FROM lab_accounts;
```

```text
1000
```

Repeatable Read allowed both different-row writes and broke the shared
reserve rule. Serializable rejected one transaction with SQLSTATE 40001 and
preserved the reserve. A real Serializable service must retry the entire failed
transaction, re-read the aggregate, and re-evaluate its decision. The production
two-account posting service uses ordered row locks at Read Committed; it does not
apply this hypothetical aggregate policy.

Session A

```sql
DROP SCHEMA psql_lab_a1148c5cd1e64b5b9f4d02f180762f44 CASCADE;
```

```text
NOTICE:  00000: drop cascades to 2 other objects
DETAIL:  drop cascades to table lab_users
drop cascades to table lab_accounts
LOCATION:  reportDependentObjects, dependency.c:1221
DROP SCHEMA
```
