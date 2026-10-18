from uuid import uuid4

import psycopg
import pytest

from wallet.migrate import migrate

pytestmark = pytest.mark.database


def test_migrations_are_repeatable(db):
    before = db.execute("SELECT name, checksum FROM schema_migrations ORDER BY name").fetchall()
    migrate(db)
    after = db.execute("SELECT name, checksum FROM schema_migrations ORDER BY name").fetchall()
    assert after == before
    assert len(before) >= 2


def test_system_accounts_start_at_zero(db):
    assert db.execute(
        "SELECT account_type, balance, user_id FROM accounts ORDER BY account_type"
    ).fetchall() == [("cash_in", 0, None), ("cash_out", 0, None), ("fee_revenue", 0, None)]


def test_user_can_own_multiple_wallets(db, wallets):
    user_id, _ = wallets[0]
    db.execute(
        "INSERT INTO accounts (user_id, name, account_type) VALUES (%s, 'Reserve', 'wallet')",
        (user_id,),
    )
    count = db.execute("SELECT count(*) FROM accounts WHERE user_id = %s", (user_id,)).fetchone()[0]
    assert count == 2


def test_negative_wallet_balance_is_rejected(db, wallets):
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute("UPDATE accounts SET balance = -1 WHERE id = %s", (wallets[0][1],))


def test_system_balance_may_be_negative(db):
    db.execute("UPDATE accounts SET balance = -10000 WHERE account_type = 'cash_in'")


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO accounts (name, account_type) VALUES ('Ownerless', 'wallet')",
        "INSERT INTO accounts (name, account_type, currency) VALUES ('USD', 'cash_in', 'USD')",
        "INSERT INTO accounts (name, account_type) VALUES ('Duplicate', 'cash_in')",
    ],
)
def test_invalid_accounts_are_rejected(db, statement):
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        db.execute(statement)


def test_email_uniqueness_is_case_insensitive(db, wallets):
    with pytest.raises(psycopg.errors.UniqueViolation), db.transaction():
        db.execute(
            "INSERT INTO users (name, email, phone) "
            "VALUES ('Other', 'MEERA@example.test', '+919199999999')"
        )


def test_idempotency_key_is_unique_per_user(db, wallets):
    key = uuid4()
    query = "INSERT INTO idempotency_keys (user_id, key, request_hash) VALUES (%s, %s, %s)"
    db.execute(query, (wallets[0][0], key, "a" * 64))
    db.execute(query, (wallets[1][0], key, "a" * 64))
    with pytest.raises(psycopg.errors.UniqueViolation), db.transaction():
        db.execute(query, (wallets[0][0], key, "a" * 64))


def test_completed_key_requires_response(db, wallets):
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute(
            "INSERT INTO idempotency_keys (user_id, key, request_hash, status) "
            "VALUES (%s, %s, %s, 'completed')",
            (wallets[0][0], uuid4(), "a" * 64),
        )


def test_job_lock_is_seeded_once(db):
    assert db.execute("SELECT name FROM job_locks").fetchall() == [("reconciliation",)]
