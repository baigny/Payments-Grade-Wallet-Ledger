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
