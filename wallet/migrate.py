import argparse
import hashlib
import os
from pathlib import Path

from psycopg import sql

from wallet.core.db import connect

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def migrate(conn, directory=MIGRATIONS):
    """Apply immutable SQL files atomically; serialize concurrent migration runners."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(728194610)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
            "name TEXT NOT NULL UNIQUE, checksum TEXT NOT NULL, "
            "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        applied = dict(conn.execute("SELECT name, checksum FROM schema_migrations").fetchall())
        for path in sorted(directory.glob("*.sql")):
            content = path.read_bytes()
            checksum = hashlib.sha256(content).hexdigest()
            if path.name in applied:
                if applied[path.name] != checksum:
                    raise RuntimeError(f"Applied migration changed: {path.name}")
                continue
            conn.execute(content.decode("utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (name, checksum) VALUES (%s, %s)",
                (path.name, checksum),
            )
            print(f"Applied {path.name}", flush=True)


def provision_app_role(conn):
    password = os.environ["WALLET_APP_PASSWORD"]
    with conn.transaction():
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'wallet_app'").fetchone():
            conn.execute("CREATE ROLE wallet_app LOGIN")
        conn.execute(sql.SQL("ALTER ROLE wallet_app PASSWORD {}").format(sql.Literal(password)))
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        conn.execute("GRANT USAGE ON SCHEMA public TO wallet_app")
        conn.execute("GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO wallet_app")
        conn.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO wallet_app")
        conn.execute("REVOKE INSERT, UPDATE ON schema_migrations FROM wallet_app")
        conn.execute("REVOKE INSERT ON job_locks FROM wallet_app")
        conn.execute("GRANT DELETE ON llm_requests TO wallet_app")
        conn.execute("REVOKE UPDATE ON ledger_entries, transfer_status_events FROM wallet_app")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provision-app-role", action="store_true")
    args = parser.parse_args()
    with connect(autocommit=True) as conn:
        migrate(conn)
        if args.provision_app_role:
            provision_app_role(conn)


if __name__ == "__main__":
    main()
