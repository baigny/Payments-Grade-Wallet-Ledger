"""Measure committed transfers with no fraud SQL, individual rules, and all rules."""

import argparse
import contextlib
import json
import statistics
import sys
from pathlib import Path
from time import perf_counter_ns
from uuid import uuid4

from psycopg import sql
from psycopg.rows import dict_row

from wallet.core.auth import Actor
from wallet.core.db import connect
from wallet.fraud import service as fraud
from wallet.migrate import migrate
from wallet.transfers import service as transfers
from wallet.transfers.schemas import TransferCreate


def benchmark(samples=30):
    schema = "fraud_benchmark_" + uuid4().hex
    original = fraud.evaluate
    with connect(autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            migrate(conn, Path(__file__).resolve().parents[1] / "migrations")
            with conn.transaction():
                conn.execute("SET LOCAL session_replication_role = replica")
                user = conn.execute(
                    "INSERT INTO users (name, email, phone) VALUES "
                    "('Benchmark', 'fraud@example.test', '+919000000088') RETURNING id"
                ).fetchone()[0]
                source = conn.execute(
                    "INSERT INTO accounts (user_id, name, account_type) "
                    "VALUES (%s, 'Benchmark', 'wallet') RETURNING id",
                    (user,),
                ).fetchone()[0]
                cash_in = conn.execute(
                    "SELECT id FROM accounts WHERE account_type = 'cash_in'"
                ).fetchone()[0]
                cash_out = conn.execute(
                    "SELECT id FROM accounts WHERE account_type = 'cash_out'"
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO transfers (source_account_id, destination_account_id, "
                    "type, status, amount) VALUES (%s, %s, 'top_up', 'posted', 50000)",
                    (cash_in, source),
                )
                conn.execute(
                    "INSERT INTO transfers (source_account_id, destination_account_id, "
                    "type, status, amount) SELECT %s, %s, 'withdrawal', 'posted', 1 "
                    "FROM generate_series(1, 10000)",
                    (source, cash_out),
                )
                conn.execute(
                    "INSERT INTO ledger_entries (transfer_id, account_id, direction, amount) "
                    "SELECT id, source_account_id, 'debit', amount FROM transfers UNION ALL "
                    "SELECT id, destination_account_id, 'credit', amount FROM transfers"
                )
                conn.execute(
                    "UPDATE accounts SET balance = CASE WHEN id = %s THEN 40000 "
                    "WHEN id = %s THEN -50000 ELSE 10000 END "
                    "WHERE id = ANY(%s)",
                    (source, cash_in, [source, cash_in, cash_out]),
                )
            conn.execute("ANALYZE")
            conn.row_factory = dict_row
            results = {}
            for case in ("baseline", "amount", "velocity", "daily_total", "all"):
                conn.execute(
                    "UPDATE risk_rules SET threshold = 9223372036854775807, "
                    "enabled = (rule_type = %s OR %s = 'all')",
                    (case, case),
                )
                fraud.evaluate = (lambda *args: []) if case == "baseline" else original
                timings = []
                for index in range(samples + 3):
                    started = perf_counter_ns()
                    with conn.transaction():
                        transfers._configure_transaction(conn)
                        result = transfers.create_transfer(
                            conn,
                            Actor(user, "user"),
                            TransferCreate(type="withdrawal", source_account_id=source, amount=1),
                        )
                    assert result["status"] == "posted"
                    if index >= 3:
                        timings.append((perf_counter_ns() - started) / 1000000)
                results[case] = {
                    "mean_ms": statistics.mean(timings),
                    "median_ms": statistics.median(timings),
                    "p95_ms": sorted(timings)[int(0.95 * (len(timings) - 1))],
                }
            return {
                "samples_per_case": samples,
                "initial_outgoing_history": 10000,
                "postgres": conn.execute("SELECT version() version").fetchone()["version"],
                "scope": "internal SQL and committed transaction; excludes HTTP/auth/idempotency",
                "results": results,
            }
        finally:
            fraud.evaluate = original
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    if not 5 <= args.samples <= 100:
        parser.error("samples must be 5..100")
    with contextlib.redirect_stdout(sys.stderr):
        evidence = benchmark(args.samples)
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
