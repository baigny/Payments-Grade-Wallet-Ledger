"""Read one consistent snapshot, record evidence, and never repair financial records."""

import os

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from wallet.core import db
from wallet.core.errors import DomainError
from wallet.reconciliation.queries import CHECKS, PROBLEMS

RUN_COLUMNS = (
    "id, started_at, ended_at, status, finding_count, problem_count, "
    "account_count, transfer_count, entry_count, trigger_type, scheduled_for, error_message"
)


def configure(conn):
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    timeout = int(os.getenv("RECONCILIATION_STATEMENT_TIMEOUT_MS", "300000"))
    if not 1 <= timeout <= 3600000:
        raise ValueError("RECONCILIATION_STATEMENT_TIMEOUT_MS must be 1..3600000")
    conn.execute("SELECT set_config('statement_timeout', %s, true)", (str(timeout),))
    conn.execute("SET LOCAL lock_timeout = '5s'")


def _save_checks(conn, run_id, table, queries):
    count = 0
    for name, query in queries.items():
        statement = sql.SQL(
            "INSERT INTO {} (run_id, check_name, entity_type, entity_id, expected, actual) "
            "SELECT %s, %s, q.* FROM ({}) q"
        ).format(sql.Identifier(table), sql.SQL(query))
        count += conn.execute(statement, (run_id, name)).rowcount
    return count


def run(conn, trigger_type="manual", scheduled_for=None):
    try:
        locked = conn.execute(
            "SELECT id FROM job_locks WHERE name = 'reconciliation' FOR UPDATE NOWAIT"
        ).fetchone()
    except psycopg.errors.LockNotAvailable as exc:
        raise DomainError(
            "RECONCILIATION_BUSY", "Another reconciliation is running", 409, cacheable=False
        ) from exc
    if locked is None:
        raise RuntimeError("Reconciliation job lock is missing")
    if (
        scheduled_for
        and conn.execute(
            "SELECT id FROM reconciliation_runs WHERE scheduled_for = %s", (scheduled_for,)
        ).fetchone()
    ):
        return None
    run_id = conn.execute(
        "INSERT INTO reconciliation_runs (trigger_type, scheduled_for) "
        "VALUES (%s, %s) RETURNING id",
        (trigger_type, scheduled_for),
    ).fetchone()["id"]
    try:
        # An error discards partial evidence, but the outer transaction records an error run.
        with conn.transaction():
            findings = _save_checks(conn, run_id, "reconciliation_findings", CHECKS)
            problems = _save_checks(conn, run_id, "reconciliation_problems", PROBLEMS)
            counts = conn.execute(
                "SELECT (SELECT count(*) FROM accounts) accounts, "
                "(SELECT count(*) FROM transfers) transfers, "
                "(SELECT count(*) FROM ledger_entries) entries"
            ).fetchone()
    except psycopg.Error as exc:
        conn.execute(
            "UPDATE reconciliation_runs SET ended_at = clock_timestamp(), status = 'error', "
            "error_message = %s WHERE id = %s",
            (f"Check failed: SQLSTATE {exc.sqlstate}", run_id),
        )
    else:
        conn.execute(
            "UPDATE reconciliation_runs SET ended_at = clock_timestamp(), status = %s, "
            "finding_count = %s, problem_count = %s, account_count = %s, transfer_count = %s, "
            "entry_count = %s WHERE id = %s",
            (
                "failed" if findings or problems else "passed",
                findings,
                problems,
                counts["accounts"],
                counts["transfers"],
                counts["entries"],
                run_id,
            ),
        )
    return conn.execute(
        f"SELECT {RUN_COLUMNS} FROM reconciliation_runs WHERE id = %s", (run_id,)
    ).fetchone()


def execute(trigger_type="cli", scheduled_for=None):
    with db.connect(autocommit=True, row_factory=dict_row) as conn, conn.transaction():
        configure(conn)
        return run(conn, trigger_type, scheduled_for)


def report(run_id):
    with db.connect(autocommit=True, row_factory=dict_row) as conn:
        row = conn.execute(
            f"SELECT {RUN_COLUMNS} FROM reconciliation_runs WHERE id = %s", (run_id,)
        ).fetchone()
    if row is None:
        raise DomainError("NOT_FOUND", "Reconciliation run was not found", 404)
    return row


def findings(run_id, limit, offset, problems=False):
    report(run_id)
    table = "reconciliation_problems" if problems else "reconciliation_findings"
    with db.connect(autocommit=True, row_factory=dict_row) as conn:
        items = conn.execute(
            sql.SQL(
                "SELECT id, run_id, check_name, entity_type, entity_id, expected, actual, "
                "created_at "
                "FROM {} WHERE run_id = %s ORDER BY id LIMIT %s OFFSET %s"
            ).format(sql.Identifier(table)),
            (run_id, limit, offset),
        ).fetchall()
    return {"items": items, "limit": limit, "offset": offset}


def main():
    from wallet.reconciliation.schemas import ReconciliationRun

    try:
        result = execute()
    except DomainError as exc:
        print(exc.code)
        raise SystemExit(4) from exc
    print(ReconciliationRun.model_validate(result).model_dump_json())
    raise SystemExit({"passed": 0, "failed": 2, "error": 3}[result["status"]])


if __name__ == "__main__":
    main()
