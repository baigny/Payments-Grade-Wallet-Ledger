# Isolation lab

Captured on PostgreSQL 16 using two independent database sessions. The same
commands can be pasted into two `psql` terminals in the displayed order.
The lab uses disposable tables, not wallet balances. Reproduce with an owner
connection configured through PG* variables or DATABASE_URL:

```sh
python scripts/isolation_lab.py --output docs/isolation.md
```

For a manual run, create a scratch schema and set both sessions' search_path to it,
then create and seed these tables once:

```sql
CREATE TABLE lab_users (id BIGINT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE lab_accounts (id BIGINT PRIMARY KEY, user_id BIGINT, balance BIGINT);
INSERT INTO lab_users VALUES (1, 'Meera');
INSERT INTO lab_accounts VALUES (1, 1, 1000);
```

Before each section, with both sessions outside a transaction, reset in session B:

```sql
UPDATE lab_accounts SET balance = 1000 WHERE id = 1;
UPDATE lab_users SET name = 'Meera' WHERE id = 1;
```

Read Committed sees the second committed balance. Repeatable Read preserves its
first snapshot, including the joined user/account query. The Serializable write
fails with SQLSTATE 40001 after another transaction changes the row; the entire
transaction must be retried. That same concurrent-write rejection can also occur
at Repeatable Read, so this example is not unique evidence of Serializable's
additional protection against write skew.

## Read Committed

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
[(1000,)]
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
COMMIT;
```

```text
COMMIT
```

Session A

```sql
SELECT balance FROM lab_accounts WHERE id = 1;
```

```text
[(2000,)]
```

Session A

```sql
COMMIT;
```

```text
COMMIT
```

## Repeatable Read

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
[(1000,)]
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
COMMIT;
```

```text
COMMIT
```

Session A

```sql
SELECT balance FROM lab_accounts WHERE id = 1;
```

```text
[(1000,)]
```

Session A

```sql
COMMIT;
```

```text
COMMIT
```

## Repeatable Read with a join

Session A

```sql
BEGIN ISOLATION LEVEL REPEATABLE READ;
```

```text
BEGIN
```

Session A

```sql
SELECT u.name, a.balance FROM lab_users u JOIN lab_accounts a ON a.user_id = u.id;
```

```text
[('Meera', 1000)]
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
SELECT u.name, a.balance FROM lab_users u JOIN lab_accounts a ON a.user_id = u.id;
```

```text
[('Meera', 1000)]
```

Session A

```sql
COMMIT;
```

```text
COMMIT
```

## Serializable concurrent write

Session A

```sql
BEGIN ISOLATION LEVEL SERIALIZABLE;
```

```text
BEGIN
```

Session B

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
[(1000,)]
```

Session B

```sql
SELECT balance FROM lab_accounts WHERE id = 1;
```

```text
[(1000,)]
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
ERROR 40001: could not serialize access due to concurrent update
```

Session B

```sql
ROLLBACK;
```

```text
ROLLBACK
```
