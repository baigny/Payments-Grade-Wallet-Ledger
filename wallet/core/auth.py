import hashlib
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from wallet.core import db as db
from wallet.core.errors import DomainError

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Actor:
    id: int
    role: str


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_actor(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Actor:
    if credentials is None:
        raise DomainError("UNAUTHENTICATED", "A valid bearer token is required", 401)
    with db.connect(autocommit=True, connect_timeout=5) as conn:
        row = conn.execute(
            "SELECT u.id, u.role FROM auth_tokens t JOIN users u ON u.id = t.user_id "
            "WHERE t.token_hash = %s AND t.revoked_at IS NULL",
            (token_hash(credentials.credentials),),
        ).fetchone()
    if row is None:
        raise DomainError("UNAUTHENTICATED", "A valid bearer token is required", 401)
    return Actor(*row)


def require_admin(actor: Actor):
    if actor.role != "admin":
        raise DomainError("FORBIDDEN", "Administrator access is required", 403)


def authorize_owner(actor: Actor, user_id: int):
    if actor.role != "admin" and actor.id != user_id:
        raise DomainError("NOT_FOUND", "Wallet or user was not found", 404)


def require_write_key(key: Annotated[UUID, Header(alias="Idempotency-Key")]) -> UUID:
    """Require a caller-supplied UUID for every POST operation."""
    return key


CurrentActor = Annotated[Actor, Depends(current_actor)]
WriteKey = Annotated[UUID, Depends(require_write_key)]
