import secrets
from datetime import UTC, date, datetime, time, timedelta

import psycopg
from psycopg.rows import dict_row

from wallet import db, pagination
from wallet.auth import Actor, authorize_owner, require_admin, token_hash
from wallet.errors import DomainError
from wallet.models import UserCreate

WALLET_COLUMNS = "id, user_id, name, currency, balance"


def create_user(conn, actor: Actor, data: UserCreate):
    require_admin(actor)
    token = secrets.token_urlsafe(32)
    try:
        user = conn.execute(
            "INSERT INTO users (name, email, phone) VALUES (%s, %s, %s) "
            "RETURNING id, name, email, phone, kyc_status",
            (data.name, data.email.lower(), data.phone),
        ).fetchone()
        conn.execute(
            "INSERT INTO auth_tokens (user_id, token_hash) VALUES (%s, %s)",
            (user["id"], token_hash(token)),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise DomainError("USER_EXISTS", "Email or phone is already registered", 409) from exc
    return {**user, "token": token}


def create_wallet(conn, actor: Actor, user_id: int, name: str):
    authorize_owner(actor, user_id)
    if conn.execute("SELECT id FROM users WHERE id = %s", (user_id,)).fetchone() is None:
        raise DomainError("NOT_FOUND", "User was not found", 404)
    return conn.execute(
        "INSERT INTO accounts (user_id, name, account_type) VALUES (%s, %s, 'wallet') "
        f"RETURNING {WALLET_COLUMNS}",
        (user_id, name),
    ).fetchone()


def list_wallets(actor: Actor, user_id: int):
    authorize_owner(actor, user_id)
    with db.connect(autocommit=True, row_factory=dict_row) as conn:
        if conn.execute("SELECT id FROM users WHERE id = %s", (user_id,)).fetchone() is None:
            raise DomainError("NOT_FOUND", "User was not found", 404)
        return {
            "items": conn.execute(
                f"SELECT {WALLET_COLUMNS} FROM accounts WHERE user_id = %s ORDER BY id", (user_id,)
            ).fetchall()
        }


def get_wallet(conn, actor: Actor, wallet_id: int):
    wallet = conn.execute(
        f"SELECT {WALLET_COLUMNS} FROM accounts WHERE id = %s AND account_type = 'wallet'",
        (wallet_id,),
    ).fetchone()
    if wallet is None:
        raise DomainError("NOT_FOUND", "Wallet was not found", 404)
    authorize_owner(actor, wallet["user_id"])
    return wallet


def balance(actor: Actor, wallet_id: int):
    with db.connect(autocommit=True, row_factory=dict_row) as conn:
        return get_wallet(conn, actor, wallet_id)


def statement(actor: Actor, wallet_id: int, limit: int, offset: int, cursor: str | None = None):
    with db.connect(autocommit=True, row_factory=dict_row) as conn:
        get_wallet(conn, actor, wallet_id)
        if cursor is not None and offset:
            raise DomainError("INVALID_CURSOR", "Use either cursor or a nonzero offset", 422)
        position = pagination.decode(cursor, wallet_id) if cursor is not None else None
        boundary = "AND (e.created_at, e.id) < (%s, %s) " if position else ""
        params = [wallet_id]
        if position:
            params.extend([position.created_at, position.entry_id])
        params.extend([limit + 1, offset])
        items = conn.execute(
            "SELECT e.id, e.transfer_id, e.direction, e.amount, e.created_at, t.type, t.note "
            "FROM ledger_entries e JOIN transfers t ON t.id = e.transfer_id "
            "WHERE e.account_id = %s "
            + boundary
            + "ORDER BY e.created_at DESC, e.id DESC LIMIT %s OFFSET %s",
            params,
        ).fetchall()
    next_cursor = pagination.encode(wallet_id, items[limit - 1]) if len(items) > limit else None
    return {
        "wallet_id": wallet_id,
        "currency": "INR",
        "limit": limit,
        "offset": offset,
        "items": items[:limit],
        "next_cursor": next_cursor,
    }


def statement_summary(actor: Actor, wallet_id: int, from_date: date, to_date: date):
    if from_date > to_date or to_date == date.max:
        raise DomainError("INVALID_DATE_RANGE", "Use ordered dates ending before 9999-12-31", 422)
    start = datetime.combine(from_date, time(), UTC)
    end = datetime.combine(to_date + timedelta(days=1), time(), UTC)
    with db.connect(autocommit=True, row_factory=dict_row) as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        get_wallet(conn, actor, wallet_id)
        totals = conn.execute(
            "SELECT coalesce(sum(CASE WHEN direction = 'credit' THEN amount ELSE -amount END) "
            "FILTER (WHERE created_at < %(start)s), 0) opening_balance, "
            "coalesce(sum(amount) FILTER (WHERE direction = 'credit' "
            "AND created_at >= %(start)s), 0) credit_total, "
            "coalesce(sum(amount) FILTER (WHERE direction = 'debit' "
            "AND created_at >= %(start)s), 0) debit_total, "
            "coalesce(sum(CASE WHEN direction = 'credit' THEN amount ELSE -amount END), 0) "
            "closing_balance, "
            "count(DISTINCT transfer_id) FILTER (WHERE created_at >= %(start)s) transfer_count "
            "FROM ledger_entries WHERE account_id = %(wallet)s AND created_at < %(end)s",
            {"wallet": wallet_id, "start": start, "end": end},
        ).fetchone()
    return {
        "wallet_id": wallet_id,
        "currency": "INR",
        "from_date": from_date,
        "to_date": to_date,
        **{key: int(value) for key, value in totals.items()},
    }
