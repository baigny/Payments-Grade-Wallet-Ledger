def seed_postings(conn, count=10000):
    """Valid set-based fixture; no disabled integrity checks during seeding."""
    user = conn.execute(
        "INSERT INTO users (name, email, phone) VALUES "
        "('Seed', 'seed@example.test', '+919999999999') RETURNING id"
    ).fetchone()[0]
    account = conn.execute(
        "INSERT INTO accounts (user_id, name, account_type) "
        "VALUES (%s, 'Seed', 'wallet') RETURNING id",
        (user,),
    ).fetchone()[0]
    system = conn.execute("SELECT id FROM accounts WHERE account_type = 'cash_in'").fetchone()[0]
    with conn.transaction():
        conn.execute(
            "INSERT INTO transfers (source_account_id, destination_account_id, type, amount) "
            "SELECT %s, %s, 'top_up', 10 FROM generate_series(1, %s)",
            (system, account, count),
        )
        conn.execute(
            "INSERT INTO ledger_entries (transfer_id, account_id, direction, amount) "
            "SELECT id, source_account_id, 'debit', amount FROM transfers UNION ALL "
            "SELECT id, destination_account_id, 'credit', amount FROM transfers"
        )
        conn.execute(
            "UPDATE accounts SET balance = CASE WHEN id = %s THEN %s ELSE %s END "
            "WHERE id = ANY(%s)",
            (account, count * 10, -count * 10, [account, system]),
        )
        conn.execute("UPDATE transfers SET status = 'posted'")
    return account
