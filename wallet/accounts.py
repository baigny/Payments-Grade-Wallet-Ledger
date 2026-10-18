import secrets

import psycopg
from psycopg.rows import dict_row

from wallet import db
from wallet.auth import Actor, authorize_owner, require_admin, token_hash
from wallet.errors import DomainError
from wallet.models import UserCreate

WALLET_COLUMNS = "id, user_id, name, currency, balance"


def create_user(actor: Actor, data: UserCreate):
    require_admin(actor)
    token = secrets.token_urlsafe(32)
    try:
        with db.connect(autocommit=True, row_factory=dict_row) as conn, conn.transaction():
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


def create_wallet(actor: Actor, user_id: int, name: str):
    authorize_owner(actor, user_id)
    with db.connect(autocommit=True, row_factory=dict_row) as conn, conn.transaction():
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


def statement(actor: Actor, wallet_id: int, limit: int, offset: int):
    with db.connect(autocommit=True, row_factory=dict_row) as conn:
        get_wallet(conn, actor, wallet_id)
        items = conn.execute(
            "SELECT e.id, e.transfer_id, e.direction, e.amount, e.created_at, t.type, t.note "
            "FROM ledger_entries e JOIN transfers t ON t.id = e.transfer_id "
            "WHERE e.account_id = %s ORDER BY e.created_at DESC, e.id DESC LIMIT %s OFFSET %s",
            (wallet_id, limit, offset),
        ).fetchall()
    return {
        "wallet_id": wallet_id,
        "currency": "INR",
        "limit": limit,
        "offset": offset,
        "items": items,
    }
