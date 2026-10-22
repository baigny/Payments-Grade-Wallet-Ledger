import secrets

import psycopg

from wallet.core import db as db
from wallet.core.auth import Actor, require_admin, token_hash
from wallet.core.errors import DomainError
from wallet.statements import pagination as pagination
from wallet.users.schemas import UserCreate


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
