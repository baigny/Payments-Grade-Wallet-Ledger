"""LLM translates intent only; all numbers and contributing IDs come from scoped SQL."""

import os
from datetime import UTC, datetime, time, timedelta

from pydantic import ValidationError

from wallet.core import llm
from wallet.core.auth import authorize_owner
from wallet.core.errors import DomainError
from wallet.statements.schemas import QuestionSpec

SUPPORTED = (
    "Only total sent to a person, total received, largest transfer "
    "and transfer counts are supported"
)


def ask(conn, actor, user_id, question):
    authorize_owner(actor, user_id)
    if conn.execute("SELECT id FROM users WHERE id = %s", (user_id,)).fetchone() is None:
        raise DomainError("NOT_FOUND", "User was not found", 404)
    today = conn.execute("SELECT (clock_timestamp() AT TIME ZONE 'UTC')::date AS today").fetchone()[
        "today"
    ]
    try:
        output = llm.provider().generate(
            "statement_question",
            {
                "question": question,
                "reference_date": today.isoformat(),
            },
            QuestionSpec.model_json_schema(),
        )
        spec = QuestionSpec.model_validate_json(output)
    except (ValidationError, ValueError, TypeError) as exc:
        raise DomainError("UNSUPPORTED_QUESTION", SUPPORTED, 422) from exc
    start = datetime.combine(spec.from_date, time(), UTC)
    end = datetime.combine(spec.to_date + timedelta(days=1), time(), UTC)
    # Ownership is always supplied by authenticated application code, never model output.
    conditions = [
        "owned.user_id = %s",
        "t.status = 'posted'",
        "t.created_at >= %s",
        "t.created_at < %s",
    ]
    params = [user_id, start, end]
    if spec.type == 1:
        conditions += ["e.direction = 'debit'", "lower(u.name) = lower(%s)", "t.type = 'peer'"]
        params += [spec.counterparty]
    elif spec.type == 2:
        conditions += ["e.direction = 'credit'"]
    rows = conn.execute(
        "SELECT t.id, greatest(coalesce(sum(e.amount) FILTER (WHERE e.direction = 'credit'), 0), "
        "coalesce(sum(e.amount) FILTER (WHERE e.direction = 'debit'), 0)) amount, "
        "d.user_id counterparty_id FROM ledger_entries e "
        "JOIN accounts owned ON owned.id = e.account_id JOIN transfers t ON t.id = e.transfer_id "
        "JOIN accounts d ON d.id = t.destination_account_id "
        "LEFT JOIN users u ON u.id = d.user_id WHERE "
        + " AND ".join(conditions)
        + " GROUP BY t.id, d.user_id ORDER BY t.id LIMIT 1001",
        params,
    ).fetchall()
    if len(rows) > 1000:
        raise DomainError(
            "QUESTION_TOO_BROAD", "Narrow the date range to at most 1000 movements", 422
        )
    if spec.type == 1 and len({row["counterparty_id"] for row in rows}) > 1:
        raise DomainError("AMBIGUOUS_COUNTERPARTY", "More than one counterparty has that name", 422)
    if spec.type == 3:
        maximum = max((row["amount"] for row in rows), default=0)
        rows = [row for row in rows if row["amount"] == maximum]
        value = maximum
    else:
        value = len(rows) if spec.type == 4 else sum(row["amount"] for row in rows)
    return {
        "type": spec.type,
        "value": int(value),
        "unit": "transfers" if spec.type == 4 else "paise",
        "currency": "INR",
        "transfer_ids": [row["id"] for row in rows],
        "query": spec,
        "provider": os.getenv("LLM_PROVIDER", "mock"),
    }
