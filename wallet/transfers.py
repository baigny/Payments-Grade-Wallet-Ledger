"""All money posting uses explicit SQL, ordered account locks, and one transaction."""

import psycopg

from wallet.auth import Actor, authorize_owner, require_admin
from wallet.errors import DomainError
from wallet.models import MAX_MONEY, TransferCreate


def _system_account(conn, kind: str) -> int:
    return conn.execute("SELECT id FROM accounts WHERE account_type = %s", (kind,)).fetchone()["id"]


def _lock_accounts(conn, source_id: int, destination_id: int):
    if source_id == destination_id:
        raise DomainError("SAME_ACCOUNT", "Source and destination must differ", 422)
    rows = conn.execute(
        "SELECT id, user_id, account_type, balance FROM accounts "
        "WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
        ([source_id, destination_id],),
    ).fetchall()
    if len(rows) != 2:
        raise DomainError("NOT_FOUND", "Account was not found", 404)
    by_id = {row["id"]: row for row in rows}
    return rows, by_id[source_id], by_id[destination_id]


def _post_locked(conn, actor, rows, source, destination, amount, kind, note, reversal_of=None):
    # The balances in source/destination were read while acquiring FOR UPDATE locks.
    balances = {
        source["id"]: source["balance"] - amount,
        destination["id"]: destination["balance"] + amount,
    }
    if source["account_type"] == "wallet" and balances[source["id"]] < 0:
        raise DomainError("INSUFFICIENT_FUNDS", "Wallet has insufficient funds", 409)
    if any(value < -MAX_MONEY - 1 or value > MAX_MONEY for value in balances.values()):
        raise DomainError("BALANCE_OVERFLOW", "Resulting balance is outside supported bounds", 422)

    conn.execute("SELECT set_config('wallet.actor_id', %s, true)", (str(actor.id),))
    transfer = conn.execute(
        "INSERT INTO transfers (initiated_by, source_account_id, destination_account_id, "
        "type, amount, note, reversal_of) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (actor.id, source["id"], destination["id"], kind, amount, note, reversal_of),
    ).fetchone()
    conn.execute(
        "INSERT INTO ledger_entries (transfer_id, account_id, direction, amount) "
        "VALUES (%s, %s, 'debit', %s), (%s, %s, 'credit', %s)",
        (transfer["id"], source["id"], amount, transfer["id"], destination["id"], amount),
    )
    for row in rows:  # Already locked and ordered by account ID.
        conn.execute(
            "UPDATE accounts SET balance = %s WHERE id = %s", (balances[row["id"]], row["id"])
        )
    return conn.execute(
        "UPDATE transfers SET status = 'posted' WHERE id = %s "
        "RETURNING id, source_account_id, destination_account_id, type, status, "
        "amount, currency, reversal_of, note, created_at",
        (transfer["id"],),
    ).fetchone()


def _configure_transaction(conn):
    conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
    conn.execute("SET LOCAL lock_timeout = '5s'")
    conn.execute("SET LOCAL statement_timeout = '15s'")


def create_transfer(conn, actor: Actor, data: TransferCreate):
    if data.type in ("top_up", "fee"):
        require_admin(actor)
    source_id, destination_id = data.source_account_id, data.destination_account_id
    if data.type == "peer" and (source_id is None or destination_id is None):
        raise DomainError("INVALID_TRANSFER", "Peer transfers require both account IDs", 422)
    if data.type == "top_up" and (destination_id is None or source_id is not None):
        raise DomainError("INVALID_TRANSFER", "Top-up requires only a destination wallet", 422)
    if data.type in ("withdrawal", "fee") and (source_id is None or destination_id is not None):
        raise DomainError(
            "INVALID_TRANSFER", "Withdrawal or fee requires only a source wallet", 422
        )

    if data.type == "top_up":
        source_id = _system_account(conn, "cash_in")
    elif data.type in ("withdrawal", "fee"):
        destination_id = _system_account(
            conn, "cash_out" if data.type == "withdrawal" else "fee_revenue"
        )
    rows, source, destination = _lock_accounts(conn, source_id, destination_id)
    if source["account_type"] == "wallet":
        authorize_owner(actor, source["user_id"])
    if (data.type != "top_up" and source["account_type"] != "wallet") or (
        data.type in ("peer", "top_up") and destination["account_type"] != "wallet"
    ):
        raise DomainError("INVALID_TRANSFER", "Expected a user wallet account", 422)
    result = _post_locked(conn, actor, rows, source, destination, data.amount, data.type, data.note)
    return result


def reverse_transfer(conn, actor: Actor, transfer_id: int):
    require_admin(actor)
    try:
        original = conn.execute("SELECT * FROM transfers WHERE id = %s", (transfer_id,)).fetchone()
        if original is None:
            raise DomainError("NOT_FOUND", "Transfer was not found", 404)
        if original["status"] != "posted" or original["type"] == "reversal":
            raise DomainError("INVALID_REVERSAL", "Only original posted transfers reverse", 409)
        rows, source, destination = _lock_accounts(
            conn, original["destination_account_id"], original["source_account_id"]
        )
        if conn.execute(
            "SELECT id FROM transfers WHERE reversal_of = %s", (transfer_id,)
        ).fetchone():
            raise DomainError("ALREADY_REVERSED", "Transfer already has a reversal", 409)
        result = _post_locked(
            conn,
            actor,
            rows,
            source,
            destination,
            original["amount"],
            "reversal",
            f"Reversal of transfer {transfer_id}",
            reversal_of=transfer_id,
        )
    except psycopg.errors.UniqueViolation as exc:
        raise DomainError("ALREADY_REVERSED", "Transfer already has a reversal", 409) from exc
    return result
