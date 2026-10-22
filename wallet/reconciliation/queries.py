"""Each invariant is independently executable SQL and usable with EXPLAIN ANALYZE."""

LEDGER_BALANCES = """
SELECT account_id, sum(CASE WHEN direction = 'credit' THEN amount ELSE -amount END) balance
FROM ledger_entries GROUP BY account_id
"""
EVENT_BALANCES = """
SELECT account_id, sum(amount) balance FROM (
    SELECT source_account_id account_id, -amount::numeric amount FROM transfers WHERE status =
        'posted'
    UNION ALL
    SELECT destination_account_id, amount FROM transfers WHERE status = 'posted'
) movement GROUP BY account_id
"""

# Every query returns entity_type, entity_id, expected, actual.
CHECKS = {
    "books_balance": """
        SELECT 'global', NULL::bigint, jsonb_build_object('debits', debits),
               jsonb_build_object('credits', credits) FROM (
            SELECT coalesce(sum(amount) FILTER (WHERE direction = 'debit'), 0) debits,
                   coalesce(sum(amount) FILTER (WHERE direction = 'credit'), 0) credits
            FROM ledger_entries
        ) totals WHERE debits <> credits
    """,
    "transfer_balance": """
        SELECT 'transfer', transfer_id,
               jsonb_build_object('debits', coalesce(sum(amount) FILTER (WHERE direction =
        'debit'), 0)),
               jsonb_build_object('credits', coalesce(sum(amount) FILTER (WHERE direction =
        'credit'), 0))
        FROM ledger_entries GROUP BY transfer_id
        HAVING coalesce(sum(amount) FILTER (WHERE direction = 'debit'), 0)
            <> coalesce(sum(amount) FILTER (WHERE direction = 'credit'), 0)
    """,
    "stored_balance": f"""
        SELECT 'account', a.id, jsonb_build_object('balance', coalesce(b.balance, 0)),
               jsonb_build_object('balance', a.balance)
        FROM accounts a LEFT JOIN ({LEDGER_BALANCES}) b ON b.account_id = a.id
        WHERE a.balance <> coalesce(b.balance, 0)
    """,
    "overdraft": f"""
        SELECT 'account', a.id, jsonb_build_object('minimum', 0),
               jsonb_build_object('computed_balance', b.balance)
        FROM accounts a JOIN ({LEDGER_BALANCES}) b ON b.account_id = a.id
        WHERE a.account_type = 'wallet' AND b.balance < 0
    """,
    "orphan_transfer": """
        SELECT 'transfer', t.id, jsonb_build_object('minimum_entries', 2),
               jsonb_build_object('entries', count(e.id))
        FROM transfers t LEFT JOIN ledger_entries e ON e.transfer_id = t.id
        WHERE t.status = 'posted' GROUP BY t.id HAVING count(e.id) < 2
    """,
    "orphan_entry": """
        SELECT 'entry', e.id, jsonb_build_object('transfer_exists', true, 'account_exists', true),
               jsonb_build_object('transfer_exists', t.id IS NOT NULL, 'account_exists', a.id
        IS NOT NULL)
        FROM ledger_entries e LEFT JOIN transfers t ON t.id = e.transfer_id
        LEFT JOIN accounts a ON a.id = e.account_id WHERE t.id IS NULL OR a.id IS NULL
    """,
}

PROBLEMS = {
    "entry_mismatch": """
        SELECT 'entry', e.id,
            jsonb_build_object('amount', t.amount, 'account_id',
                CASE WHEN e.direction = 'debit' THEN t.source_account_id ELSE
        t.destination_account_id END,
                'status', 'posted'),
            jsonb_build_object('amount', e.amount, 'account_id', e.account_id, 'status', t.status)
        FROM ledger_entries e JOIN transfers t ON t.id = e.transfer_id
        WHERE e.amount <> t.amount OR t.status <> 'posted' OR e.account_id <>
            CASE WHEN e.direction = 'debit' THEN t.source_account_id ELSE
        t.destination_account_id END
    """,
    "entry_count": """
        SELECT 'transfer', t.id, jsonb_build_object('direction', leg.direction, 'entries', 1),
               jsonb_build_object('direction', leg.direction, 'entries', count(e.id))
        FROM transfers t CROSS JOIN LATERAL
            (VALUES ('debit', t.source_account_id), ('credit', t.destination_account_id))
            leg(direction, account_id)
        LEFT JOIN ledger_entries e ON e.transfer_id = t.id AND e.direction = leg.direction
            AND e.account_id = leg.account_id
        WHERE t.status = 'posted' GROUP BY t.id, leg.direction HAVING count(e.id) <> 1
    """,
    "stored_balance": f"""
        SELECT 'account', a.id, jsonb_build_object('event_balance', coalesce(b.balance, 0)),
               jsonb_build_object('stored_balance', a.balance)
        FROM accounts a LEFT JOIN ({EVENT_BALANCES}) b ON b.account_id = a.id
        WHERE a.balance <> coalesce(b.balance, 0)
    """,
    "overdraft": f"""
        SELECT 'account', a.id, jsonb_build_object('minimum', 0),
               jsonb_build_object('event_balance', b.balance)
        FROM accounts a JOIN ({EVENT_BALANCES}) b ON b.account_id = a.id
        WHERE a.account_type = 'wallet' AND b.balance < 0
    """,
    "orphan_entry": CHECKS["orphan_entry"],
}
