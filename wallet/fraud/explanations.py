"""Advisory flag explanations run separately from money and reconciliation transactions."""

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from wallet.core import db, llm
from wallet.core.errors import DomainError
from wallet.fraud.schemas import FlagExplanation


def explain(conn, limit=10, cutoff=None):
    try:
        locked = conn.execute(
            "SELECT id FROM job_locks WHERE name = 'risk_explanations' FOR UPDATE NOWAIT"
        ).fetchone()
    except psycopg.errors.LockNotAvailable as exc:
        raise DomainError(
            "EXPLANATIONS_BUSY", "Another explanation batch is running", 409, cacheable=False
        ) from exc
    if locked is None:
        raise RuntimeError("Risk explanation job lock is missing")
    flags = conn.execute(
        "SELECT f.id, f.rule_snapshot, t.id transfer_id, t.created_at, "
        "CASE WHEN a.account_type = 'wallet' THEN t.source_account_id "
        "ELSE t.destination_account_id END wallet_id FROM risk_flags f "
        "JOIN transfers t ON t.id = f.transfer_id JOIN accounts a ON a.id = t.source_account_id "
        "WHERE f.explained_at IS NULL AND (%s::timestamptz IS NULL OR f.created_at <= %s) "
        "ORDER BY f.created_at, f.id "
        "LIMIT %s FOR UPDATE OF f SKIP LOCKED",
        (cutoff, cutoff, limit),
    ).fetchall()
    ids = []
    for flag in flags:
        history = conn.execute(
            "SELECT id, type, amount, status, note, created_at FROM transfers "
            "WHERE (source_account_id = %s OR destination_account_id = %s) "
            "AND (created_at, id) < (%s, %s) ORDER BY created_at DESC, id DESC LIMIT 10",
            (flag["wallet_id"], flag["wallet_id"], flag["created_at"], flag["transfer_id"]),
        ).fetchall()
        payload = {
            "rule": flag["rule_snapshot"],
            "history": [{**row, "created_at": row["created_at"].isoformat()} for row in history],
        }
        try:
            output = llm.provider().generate(
                "explain_flag", payload, FlagExplanation.model_json_schema()
            )
            result = FlagExplanation.model_validate_json(output)
        except (ValidationError, ValueError, TypeError) as exc:
            raise DomainError(
                "INVALID_EXPLANATION", "Provider explanation did not pass validation", 503
            ) from exc
        conn.execute(
            "UPDATE risk_flags SET explanation = %s, label = %s, explained_at = clock_timestamp() "
            "WHERE id = %s",
            (result.explanation, result.label, flag["id"]),
        )
        ids.append(flag["id"])
    return {"explained": len(ids), "flag_ids": ids}


def execute(limit=100, cutoff=None):
    with db.connect(autocommit=True, row_factory=dict_row) as conn, conn.transaction():
        conn.execute("SET LOCAL statement_timeout = '15s'")
        return explain(conn, limit, cutoff)
