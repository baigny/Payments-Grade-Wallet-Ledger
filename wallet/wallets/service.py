from psycopg.rows import dict_row

from wallet.core import db
from wallet.core.auth import Actor, authorize_owner
from wallet.core.errors import DomainError

WALLET_COLUMNS = "id, user_id, name, currency, balance"


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
