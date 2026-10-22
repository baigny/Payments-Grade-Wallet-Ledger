"""EXPLAIN reconciliation queries on synthetic data in a disposable schema only."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from psycopg import sql

from wallet.core.db import connect
from wallet.migrate import migrate
from wallet.reconciliation.queries import CHECKS, PROBLEMS


def benchmark(entries, statement=False):
    schema = "benchmark_" + uuid4().hex
    with connect(autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            migrate(conn, Path(__file__).resolve().parents[1] / "migrations")
            with conn.transaction():
                # Only a newly generated private schema is populated, never application data.
                # Bypass per-row trigger validation for synthetic bulk loading; verify afterward.
                conn.execute("SET LOCAL session_replication_role = replica")
                user = conn.execute(
                    "INSERT INTO users (name, email, phone) VALUES "
                    "('Benchmark', 'benchmark@example.test', '+919000000099') "
                    "RETURNING id"
                ).fetchone()[0]
                account = conn.execute(
                    "INSERT INTO accounts (user_id, name, account_type) "
                    "VALUES (%s, 'Benchmark', 'wallet') RETURNING id",
                    (user,),
                ).fetchone()[0]
                system = conn.execute(
                    "SELECT id FROM accounts WHERE account_type = 'cash_in'"
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO transfers (source_account_id, destination_account_id, "
                    "type, status, amount) SELECT %s, %s, 'top_up', 'posted', 10 "
                    "FROM generate_series(1, %s)",
                    (system, account, entries // 2),
                )
                conn.execute(
                    "INSERT INTO ledger_entries (transfer_id, account_id, direction, amount) "
                    "SELECT id, source_account_id, 'debit', amount FROM transfers UNION ALL "
                    "SELECT id, destination_account_id, 'credit', amount FROM transfers"
                )
                conn.execute(
                    "UPDATE accounts SET balance = CASE WHEN id = %s THEN %s ELSE %s END "
                    "WHERE id = ANY(%s)",
                    (account, entries * 5, -entries * 5, [account, system]),
                )
            conn.execute("ANALYZE")
            if statement:
                from scripts.statement_benchmark import compare

                return {
                    "postgres": conn.execute("SELECT version()").fetchone()[0],
                    "entries": entries,
                    "wallet_entries": entries // 2,
                    "completed_at": datetime.now(UTC).isoformat(),
                    **compare(conn, account),
                }
            plans = {}
            for group, queries in (("checks", CHECKS), ("problems", PROBLEMS)):
                plans[group] = {}
                for name, query in queries.items():
                    plan = conn.execute(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query
                    ).fetchone()[0][0]
                    if plan["Plan"]["Actual Rows"] != 0:
                        raise AssertionError("Synthetic fixture failed " + name)
                    plans[group][name] = plan
            return {
                "postgres": conn.execute("SELECT version()").fetchone()[0],
                "entries": conn.execute("SELECT count(*) FROM ledger_entries").fetchone()[0],
                "transfers": conn.execute("SELECT count(*) FROM transfers").fetchone()[0],
                "completed_at": datetime.now(UTC).isoformat(),
                "fixture": "synthetic balanced top-ups; triggers disabled only in a new schema",
                "plans": plans,
            }
        finally:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries", type=int, default=1000000)
    parser.add_argument("--statement", action="store_true")
    args = parser.parse_args()
    if args.entries < 2 or args.entries % 2:
        parser.error("entries must be a positive even number")
    # Migration progress goes to stderr, keeping stdout machine-readable.
    import contextlib
    import sys

    with contextlib.redirect_stdout(sys.stderr):
        evidence = benchmark(args.entries, statement=args.statement)
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
