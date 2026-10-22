"""Deterministic database-configured decisions; no model is called on the money path."""

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from wallet.core import db
from wallet.core.errors import DomainError

RULE_COLUMNS = "id, name, rule_type, threshold, window_seconds, action, enabled"


def evaluate(conn, source, amount):
    rules = conn.execute(
        f"SELECT {RULE_COLUMNS} FROM risk_rules WHERE enabled ORDER BY id"
    ).fetchall()
    evaluated_at = conn.execute("SELECT clock_timestamp() AS at").fetchone()["at"]
    flags = []
    for rule in rules:
        observed = amount
        if rule["rule_type"] != "amount":
            if source["account_type"] != "wallet":
                continue
            history = conn.execute(
                "SELECT count(*) attempts, coalesce(sum(amount), 0) total FROM transfers "
                "WHERE source_account_id = %s AND status IN ('posted', 'held') "
                "AND created_at >= %s - make_interval(secs => %s)",
                (source["id"], evaluated_at, rule["window_seconds"]),
            ).fetchone()
            observed = (
                history["attempts"] + 1
                if rule["rule_type"] == "velocity"
                else history["total"] + amount
            )
        if observed > rule["threshold"]:
            flags.append(
                {**rule, "observed": int(observed), "evaluated_at": evaluated_at.isoformat()}
            )
    return flags


def save_flags(conn, transfer_id, flags):
    for flag in flags:
        conn.execute(
            "INSERT INTO risk_flags (transfer_id, rule_id, action, rule_snapshot) "
            "VALUES (%s, %s, %s, %s)",
            (transfer_id, flag["id"], flag["action"], Jsonb(flag)),
        )


def rules():
    with db.connect(autocommit=True, row_factory=dict_row) as conn:
        return {
            "items": conn.execute(f"SELECT {RULE_COLUMNS} FROM risk_rules ORDER BY id").fetchall()
        }


def update_rule(conn, actor, rule_id, data):
    previous = conn.execute(
        f"SELECT {RULE_COLUMNS} FROM risk_rules WHERE id = %s FOR UPDATE", (rule_id,)
    ).fetchone()
    if previous is None:
        raise DomainError("NOT_FOUND", "Rule was not found", 404)
    if (previous["rule_type"] == "amount") != (data.window_seconds is None):
        raise DomainError(
            "INVALID_RULE", "Amount rules have no window; other rules require one", 422
        )
    result = conn.execute(
        "UPDATE risk_rules SET threshold = %s, window_seconds = %s, action = %s, enabled = %s "
        f"WHERE id = %s RETURNING {RULE_COLUMNS}",
        (data.threshold, data.window_seconds, data.action, data.enabled, rule_id),
    ).fetchone()
    conn.execute(
        "INSERT INTO risk_rule_events (rule_id, actor_id, previous_config, new_config) "
        "VALUES (%s, %s, %s, %s)",
        (rule_id, actor.id, Jsonb(previous), Jsonb(result)),
    )
    return result


def held(limit, offset):
    from wallet.transfers.service import TRANSFER_COLUMNS

    with db.connect(autocommit=True, row_factory=dict_row) as conn:
        items = conn.execute(
            f"SELECT {TRANSFER_COLUMNS} FROM transfers WHERE status = 'held' "
            "ORDER BY created_at, id LIMIT %s OFFSET %s",
            (limit, offset),
        ).fetchall()
    return {"items": items, "limit": limit, "offset": offset}


def flags(transfer_id):
    with db.connect(autocommit=True, row_factory=dict_row) as conn:
        if (
            conn.execute("SELECT id FROM transfers WHERE id = %s", (transfer_id,)).fetchone()
            is None
        ):
            raise DomainError("NOT_FOUND", "Transfer was not found", 404)
        return {
            "items": conn.execute(
                "SELECT id, transfer_id, rule_id, action, rule_snapshot, explanation, label, "
                "explained_at "
                "FROM risk_flags WHERE transfer_id = %s ORDER BY id",
                (transfer_id,),
            ).fetchall()
        }
