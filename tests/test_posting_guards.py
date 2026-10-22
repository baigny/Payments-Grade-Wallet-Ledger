import psycopg
import pytest

from wallet.core.db import connect

pytestmark = pytest.mark.database


def transfer(db, source, destination, amount=1500, kind="peer", reversal_of=None):
    return db.execute(
        "INSERT INTO transfers "
        "(source_account_id, destination_account_id, amount, type, reversal_of) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (source, destination, amount, kind, reversal_of),
    ).fetchone()[0]


def entry(db, transfer_id, account_id, direction, amount=1500):
    db.execute(
        "INSERT INTO ledger_entries (transfer_id, account_id, direction, amount) "
        "VALUES (%s, %s, %s, %s)",
        (transfer_id, account_id, direction, amount),
    )


def post(db, source, destination, amount=1500, kind="peer", reversal_of=None):
    transfer_id = transfer(db, source, destination, amount, kind, reversal_of)
    entry(db, transfer_id, source, "debit", amount)
    entry(db, transfer_id, destination, "credit", amount)
    db.execute("UPDATE transfers SET status = 'posted' WHERE id = %s", (transfer_id,))
    return transfer_id


def validate(db):
    db.execute("SET CONSTRAINTS ALL IMMEDIATE")
    db.execute("SET CONSTRAINTS ALL DEFERRED")


def test_balanced_transfer_can_be_posted(db, wallets):
    cash_in = db.execute("SELECT id FROM accounts WHERE account_type = 'cash_in'").fetchone()[0]
    posted = post(db, cash_in, wallets[0][1], kind="top_up")
    db.execute("UPDATE accounts SET balance = balance - 1500 WHERE id = %s", (cash_in,))
    db.execute("UPDATE accounts SET balance = balance + 1500 WHERE id = %s", (wallets[0][1],))
    validate(db)
    assert (
        db.execute("SELECT status FROM transfers WHERE id = %s", (posted,)).fetchone()[0]
        == "posted"
    )


@pytest.mark.parametrize("amount", [0, -1, 2**63])
def test_invalid_transfer_amount(db, wallets, amount):
    with pytest.raises((psycopg.IntegrityError, psycopg.DataError)), db.transaction():
        transfer(db, wallets[0][1], wallets[1][1], amount)


def test_missing_credit_is_rejected(db, wallets):
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        transfer_id = transfer(db, wallets[0][1], wallets[1][1])
        entry(db, transfer_id, wallets[0][1], "debit")
        db.execute("UPDATE transfers SET status = 'posted' WHERE id = %s", (transfer_id,))
        validate(db)


def test_equal_but_wrong_amount_is_rejected(db, wallets):
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        transfer_id = transfer(db, wallets[0][1], wallets[1][1], amount=1500)
        entry(db, transfer_id, wallets[0][1], "debit", amount=1600)
        entry(db, transfer_id, wallets[1][1], "credit", amount=1600)
        db.execute("UPDATE transfers SET status = 'posted' WHERE id = %s", (transfer_id,))
        validate(db)


def test_unequal_totals_are_rejected(db, wallets):
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        transfer_id = transfer(db, wallets[0][1], wallets[1][1])
        entry(db, transfer_id, wallets[0][1], "debit")
        entry(db, transfer_id, wallets[1][1], "credit", amount=1400)
        db.execute("UPDATE transfers SET status = 'posted' WHERE id = %s", (transfer_id,))
        validate(db)


def test_wrong_account_is_rejected(db, wallets):
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        transfer_id = transfer(db, wallets[0][1], wallets[1][1])
        entry(db, transfer_id, wallets[0][1], "debit")
        entry(db, transfer_id, wallets[0][1], "credit")
        db.execute("UPDATE transfers SET status = 'posted' WHERE id = %s", (transfer_id,))
        validate(db)


def test_posted_transfer_cannot_be_empty(db, wallets):
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        transfer_id = transfer(db, wallets[0][1], wallets[1][1])
        db.execute("UPDATE transfers SET status = 'posted' WHERE id = %s", (transfer_id,))
        validate(db)


@pytest.mark.parametrize("status", ["pending", "held", "rejected"])
def test_unposted_transfer_cannot_keep_entries(db, wallets, status):
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        transfer_id = transfer(db, wallets[0][1], wallets[1][1])
        if status != "pending":
            db.execute("UPDATE transfers SET status = %s WHERE id = %s", (status, transfer_id))
        entry(db, transfer_id, wallets[0][1], "debit")
        validate(db)


def test_held_transfer_without_entries_is_valid(db, wallets):
    transfer_id = transfer(db, wallets[0][1], wallets[1][1])
    db.execute("UPDATE transfers SET status = 'held' WHERE id = %s", (transfer_id,))
    validate(db)


@pytest.mark.parametrize("amount", [0, -1])
def test_nonpositive_entry_amount_is_rejected(db, wallets, amount):
    transfer_id = transfer(db, wallets[0][1], wallets[1][1])
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        entry(db, transfer_id, wallets[0][1], "debit", amount)


def test_orphan_entry_is_rejected(db, wallets):
    with pytest.raises(psycopg.errors.ForeignKeyViolation), db.transaction():
        entry(db, -1, wallets[0][1], "debit")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ledger_entries SET amount = amount + 1",
        "DELETE FROM ledger_entries",
        "TRUNCATE ledger_entries",
    ],
)
def test_ledger_is_immutable_even_for_owner(db, wallets, statement):
    post(db, wallets[0][1], wallets[1][1])
    validate(db)
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute(statement)


def test_posted_transfer_rejects_appended_entries(db, wallets):
    transfer_id = post(db, wallets[0][1], wallets[1][1])
    validate(db)
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        entry(db, transfer_id, wallets[0][1], "debit")


def test_status_changes_are_automatically_audited(db, wallets):
    db.execute("SELECT set_config('wallet.actor_id', %s, true)", (str(wallets[0][0]),))
    transfer_id = transfer(db, wallets[0][1], wallets[1][1])
    db.execute("UPDATE transfers SET status = 'held' WHERE id = %s", (transfer_id,))
    db.execute("SELECT set_config('wallet.actor_id', %s, true)", (str(wallets[1][0]),))
    db.execute("UPDATE transfers SET status = 'rejected' WHERE id = %s", (transfer_id,))
    assert db.execute(
        "SELECT previous_status, status, actor_id, actor_kind FROM transfer_status_events "
        "WHERE transfer_id = %s ORDER BY id",
        (transfer_id,),
    ).fetchall() == [
        (None, "pending", wallets[0][0], "user"),
        ("pending", "held", wallets[0][0], "user"),
        ("held", "rejected", wallets[1][0], "user"),
    ]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE transfer_status_events SET status = 'rejected'",
        "DELETE FROM transfer_status_events",
        "TRUNCATE transfer_status_events",
        "DELETE FROM transfers",
        "TRUNCATE transfers CASCADE",
    ],
)
def test_transfer_history_cannot_be_removed(db, wallets, statement):
    transfer(db, wallets[0][1], wallets[1][1])
    validate(db)
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute(statement)


@pytest.mark.parametrize("column,value", [("status", "held"), ("note", "changed")])
def test_posted_transfer_cannot_be_rewritten(db, wallets, column, value):
    transfer_id = post(db, wallets[0][1], wallets[1][1])
    validate(db)
    # Column names are fixed test parameters, never request input.
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute(f"UPDATE transfers SET {column} = %s WHERE id = %s", (value, transfer_id))


def test_full_reversal_is_new_balanced_transfer(db, wallets):
    original = post(db, wallets[0][1], wallets[1][1])
    validate(db)
    post(db, wallets[1][1], wallets[0][1], kind="reversal", reversal_of=original)
    validate(db)
    assert db.execute("SELECT count(*) FROM ledger_entries").fetchone()[0] == 4


def test_second_full_reversal_is_rejected(db, wallets):
    original = post(db, wallets[0][1], wallets[1][1])
    post(db, wallets[1][1], wallets[0][1], kind="reversal", reversal_of=original)
    validate(db)
    with pytest.raises(psycopg.errors.UniqueViolation), db.transaction():
        transfer(db, wallets[1][1], wallets[0][1], kind="reversal", reversal_of=original)


def test_reversal_cannot_change_original_amount(db, wallets):
    original = post(db, wallets[0][1], wallets[1][1])
    validate(db)
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        transfer(db, wallets[1][1], wallets[0][1], 1000, "reversal", original)


def test_nonzero_opening_balance_is_rejected(db, wallets):
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute(
            "INSERT INTO accounts (user_id, name, account_type, balance) "
            "VALUES (%s, 'Prefunded', 'wallet', 100)",
            (wallets[0][0],),
        )


def test_account_cannot_be_reclassified_to_bypass_overdraft(db, wallets):
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute(
            "UPDATE accounts SET account_type = 'cash_out', user_id = NULL WHERE id = %s",
            (wallets[0][1],),
        )


def test_transfer_kind_requires_correct_account_types(db, wallets):
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        transfer(db, wallets[0][1], wallets[1][1], kind="top_up")


def test_invalid_posting_fails_at_real_commit(schema_name):
    # Use a real commit boundary, independently of the rollback fixture.
    with connect(autocommit=True) as conn:
        conn.execute("SELECT set_config('search_path', %s, false)", (schema_name,))
        before = conn.execute("SELECT count(*) FROM transfers").fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            user_id = conn.execute(
                "INSERT INTO users (name, email, phone) "
                "VALUES ('Commit check', 'commit@example.test', '+919188888888') RETURNING id"
            ).fetchone()[0]
            ids = []
            for name in ("One", "Two"):
                ids.append(
                    conn.execute(
                        "INSERT INTO accounts (user_id, name, account_type) "
                        "VALUES (%s, %s, 'wallet') RETURNING id",
                        (user_id, name),
                    ).fetchone()[0]
                )
            transfer_id = transfer(conn, *ids)
            conn.execute("UPDATE transfers SET status = 'posted' WHERE id = %s", (transfer_id,))
            # The statements succeed; the deferred guard rejects the commit.
        assert conn.execute("SELECT count(*) FROM transfers").fetchone()[0] == before
        assert (
            conn.execute(
                "SELECT count(*) FROM users WHERE email = 'commit@example.test'"
            ).fetchone()[0]
            == 0
        )
