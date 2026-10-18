"""Capture a reproducible isolation lab using two real PostgreSQL sessions."""

import argparse
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

from wallet.db import connect


def run_lab():
    transcript = []
    results = {}
    name = "isolation_" + uuid4().hex
    with connect(autocommit=True) as a, connect(autocommit=True) as b:
        a.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        for conn in (a, b):
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(name)))
        a.execute("CREATE TABLE lab_users (id BIGINT PRIMARY KEY, name TEXT NOT NULL)")
        a.execute(
            "CREATE TABLE lab_accounts (id BIGINT PRIMARY KEY, user_id BIGINT, balance BIGINT)"
        )
        a.execute("INSERT INTO lab_users VALUES (1, 'Meera')")
        a.execute("INSERT INTO lab_accounts VALUES (1, 1, 1000)")

        def step(label, query):
            conn = a if label == "A" else b
            try:
                cursor = conn.execute(query)
                output = repr(cursor.fetchall()) if cursor.description else cursor.statusmessage
            except psycopg.errors.SerializationFailure as exc:
                output = f"ERROR {exc.sqlstate}: {exc.diag.message_primary}"
                results["serialization_sqlstate"] = exc.sqlstate
            transcript.append(
                f"Session {label}\n\n```sql\n{query};\n```\n\n```text\n{output}\n```\n"
            )
            return output

        def reset():
            b.execute("UPDATE lab_accounts SET balance = 1000 WHERE id = 1")
            b.execute("UPDATE lab_users SET name = 'Meera' WHERE id = 1")

        try:
            for isolation in ("READ COMMITTED", "REPEATABLE READ"):
                reset()
                transcript.append(f"## {isolation.title()}\n")
                step("A", f"BEGIN ISOLATION LEVEL {isolation}")
                results[isolation + " first"] = step(
                    "A", "SELECT balance FROM lab_accounts WHERE id = 1"
                )
                step("B", "BEGIN")
                step("B", "UPDATE lab_accounts SET balance = 2000 WHERE id = 1")
                step("B", "COMMIT")
                results[isolation + " second"] = step(
                    "A", "SELECT balance FROM lab_accounts WHERE id = 1"
                )
                step("A", "COMMIT")

            reset()
            transcript.append("## Repeatable Read with a join\n")
            join = (
                "SELECT u.name, a.balance FROM lab_users u JOIN lab_accounts a ON a.user_id = u.id"
            )
            step("A", "BEGIN ISOLATION LEVEL REPEATABLE READ")
            results["join first"] = step("A", join)
            step("B", "BEGIN")
            step("B", "UPDATE lab_accounts SET balance = 2000 WHERE id = 1")
            step("B", "UPDATE lab_users SET name = 'Meera updated' WHERE id = 1")
            step("B", "COMMIT")
            results["join second"] = step("A", join)
            step("A", "COMMIT")

            reset()
            transcript.append("## Serializable concurrent write\n")
            step("A", "BEGIN ISOLATION LEVEL SERIALIZABLE")
            step("B", "BEGIN ISOLATION LEVEL SERIALIZABLE")
            step("A", "SELECT balance FROM lab_accounts WHERE id = 1")
            step("B", "SELECT balance FROM lab_accounts WHERE id = 1")
            step("A", "UPDATE lab_accounts SET balance = 900 WHERE id = 1")
            step("A", "COMMIT")
            step("B", "UPDATE lab_accounts SET balance = 900 WHERE id = 1")
            step("B", "ROLLBACK")
        finally:
            a.execute("ROLLBACK")
            b.execute("ROLLBACK")
            a.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))

    intro = """# Isolation lab

Captured on PostgreSQL 16 using two independent database sessions. The same
commands can be pasted into two `psql` terminals in the displayed order.
The lab uses disposable tables, not wallet balances. Reproduce with an owner
connection configured through PG* variables or DATABASE_URL:

```sh
python scripts/isolation_lab.py
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

"""
    return intro + "\n".join(transcript), results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    document, _ = run_lab()
    if args.output:
        args.output.write_text(document, encoding="utf-8", newline="\n")
    else:
        print(document)


if __name__ == "__main__":
    main()
