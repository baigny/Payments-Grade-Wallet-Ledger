from datetime import UTC, date, datetime, time, timedelta

from psycopg.rows import dict_row

from wallet.core import db
from wallet.core.auth import Actor
from wallet.core.errors import DomainError
from wallet.statements import pagination
from wallet.wallets.service import get_wallet


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
