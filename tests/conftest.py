import os
from uuid import uuid4

import pytest
from psycopg import sql

from wallet.db import connect
from wallet.migrate import migrate


@pytest.fixture(scope="session")
def schema_name():
    if not os.getenv("PGHOST") and not os.getenv("DATABASE_URL"):
        pytest.skip("Set PGHOST or DATABASE_URL to run PostgreSQL tests")
    name = "test_" + uuid4().hex
    with connect(autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(name)))
        migrate(conn)
    yield name
    with connect(autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture
def db(schema_name):
    with connect(autocommit=True) as conn:
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name)))
        # Each test rolls back its data; shared migrations are applied just once.
        conn.execute("BEGIN")
        try:
            yield conn
        finally:
            conn.execute("ROLLBACK")


@pytest.fixture
def wallets(db):
    ids = []
    for name in ("Meera", "Dev"):
        user_id = db.execute(
            "INSERT INTO users (name, email, phone) VALUES (%s, %s, %s) RETURNING id",
            (name, f"{name.lower()}@example.test", f"+91910000000{len(ids)}"),
        ).fetchone()[0]
        account_id = db.execute(
            "INSERT INTO accounts (user_id, name, account_type) "
            "VALUES (%s, 'Main', 'wallet') RETURNING id",
            (user_id,),
        ).fetchone()[0]
        ids.append((user_id, account_id))
    return ids


@pytest.fixture
def api(monkeypatch, schema_name):
    from tests.api_helpers import API
    from wallet import db as database
    from wallet.auth import token_hash

    base_connect = database.connect
    name = "api_" + uuid4().hex
    token = "test-admin-" + uuid4().hex
    with base_connect(autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(name)))
        migrate(conn)
        user_id = conn.execute(
            "INSERT INTO users (name, email, phone, role) "
            "VALUES ('Admin', 'admin@example.test', '+919000000001', 'admin') RETURNING id"
        ).fetchone()[0]
        # Existing money-path scenarios keep rules active with non-triggering thresholds.
        # Fraud tests explicitly configure boundary/hold behavior in their own schema.
        conn.execute("UPDATE risk_rules SET threshold = 9223372036854775807")
        conn.execute(
            "INSERT INTO auth_tokens (user_id, token_hash) VALUES (%s, %s)",
            (user_id, token_hash(token)),
        )
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO wallet_app").format(sql.Identifier(name)))
        conn.execute(
            sql.SQL("GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA {} TO wallet_app").format(
                sql.Identifier(name)
            )
        )
        conn.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO wallet_app").format(
                sql.Identifier(name)
            )
        )
        conn.execute("REVOKE UPDATE ON ledger_entries, transfer_status_events FROM wallet_app")
        conn.execute("REVOKE INSERT, UPDATE ON schema_migrations FROM wallet_app")

    def owner(**kwargs):
        return base_connect(autocommit=True, options=f"-csearch_path={name}", **kwargs)

    def runtime(**kwargs):
        kwargs["options"] = f"-csearch_path={name}"
        conn = base_connect(**kwargs)
        conn.execute("SET ROLE wallet_app")
        return conn

    monkeypatch.setattr(database, "connect", runtime)
    yield API(token, owner)
    with base_connect(autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))
