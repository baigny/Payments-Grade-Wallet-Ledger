"""Claim a caller-scoped key before mutations and commit the saved response with them."""

import hashlib
import json

import psycopg
from fastapi.responses import Response
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from wallet import db, transfers
from wallet.errors import DomainError


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def execute(actor, key, request, model, operation, body=None, configure=None):
    # Repeatable Read can abort a duplicate key insertion after its snapshot changes.
    # SQLSTATE 40001 guarantees rollback, so retrying the whole transaction is safe.
    for attempt in range(3):
        try:
            return _execute(actor, key, request, model, operation, body, configure)
        except psycopg.errors.SerializationFailure:
            if attempt == 2:
                raise


def _execute(actor, key, request, model, operation, body, configure):
    # Include the concrete path: a key cannot silently replay across resources or endpoints.
    fingerprint = hashlib.sha256(
        canonical_json(
            {
                "method": request.method,
                "path": request.url.path,
                "body": body,
            }
        ).encode()
    ).hexdigest()
    replay = False
    with db.connect(autocommit=True, row_factory=dict_row) as conn, conn.transaction():
        (configure or transfers._configure_transaction)(conn)
        try:
            claimed = conn.execute(
                "INSERT INTO idempotency_keys (user_id, key, request_hash) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id, key) DO NOTHING RETURNING id",
                (actor.id, key, fingerprint),
            ).fetchone()
        except psycopg.errors.LockNotAvailable as exc:
            raise DomainError(
                "IDEMPOTENCY_IN_PROGRESS",
                "Request is still processing; retry with the same key",
                409,
            ) from exc
        if claimed is None:
            saved = conn.execute(
                "SELECT request_hash, status, response_code, response_body FROM idempotency_keys "
                "WHERE user_id = %s AND key = %s",
                (actor.id, key),
            ).fetchone()
            if saved is None:
                raise DomainError("IDEMPOTENCY_IN_PROGRESS", "Retry with the same key", 409)
            if saved["request_hash"] != fingerprint:
                raise DomainError(
                    "IDEMPOTENCY_KEY_REUSED", "Key was used for a different request", 422
                )
            if saved["status"] != "completed":
                raise DomainError("IDEMPOTENCY_IN_PROGRESS", "Retry with the same key", 409)
            status, body = saved["response_code"], saved["response_body"]
            replay = True
        else:
            try:
                # Roll back domain failures before storing their stable response on the key.
                with conn.transaction():
                    result = operation(conn)
                    body = model.model_validate(result).model_dump(mode="json")
                    status = 201
            except DomainError as exc:
                if exc.status >= 500 or not exc.cacheable:
                    raise
                status = exc.status
                body = {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request.state.request_id,
                }
            conn.execute(
                "UPDATE idempotency_keys SET status = 'completed', response_code = %s, "
                "response_body = %s WHERE id = %s",
                (status, Jsonb(body), claimed["id"]),
            )
    # Both first response and replay use identical encoding, independent of JSONB key order.
    # The outer COMMIT (including deferred ledger checks) has succeeded before returning.
    return Response(
        content=canonical_json(body),
        status_code=status,
        media_type="application/json",
        headers={"Idempotent-Replay": str(replay).lower()},
    )
