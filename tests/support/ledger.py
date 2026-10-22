def assert_books_match(conn):
    assert (
        conn.execute(
            "SELECT a.id FROM accounts a LEFT JOIN ledger_entries e ON e.account_id = a.id "
            "GROUP BY a.id HAVING a.balance <> COALESCE(sum(CASE WHEN e.direction = 'credit' "
            "THEN e.amount ELSE -e.amount END), 0)"
        ).fetchall()
        == []
    )
    assert conn.execute("SELECT sum(balance) FROM accounts").fetchone()[0] == 0
    assert (
        conn.execute(
            "SELECT id FROM accounts WHERE account_type = 'wallet' AND balance < 0"
        ).fetchall()
        == []
    )
    assert (
        conn.execute(
            "SELECT t.id FROM transfers t LEFT JOIN transfer_status_events e "
            "ON e.transfer_id = t.id GROUP BY t.id "
            "HAVING count(e.id) <> 2 OR bool_or(e.actor_id IS DISTINCT FROM t.initiated_by)"
        ).fetchall()
        == []
    )
