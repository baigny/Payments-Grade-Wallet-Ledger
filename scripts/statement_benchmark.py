"""Compare deep-offset and seek plans on the actual statement projection."""


def compare(conn, account):
    projection = (
        "SELECT e.id, e.transfer_id, e.direction, e.amount, e.created_at, t.type, t.note "
        "FROM ledger_entries e JOIN transfers t ON t.id = e.transfer_id "
        "WHERE e.account_id = %s "
    )
    order = "ORDER BY e.created_at DESC, e.id DESC LIMIT 51"
    anchor = conn.execute(
        "SELECT created_at, id FROM ledger_entries WHERE account_id = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 1 OFFSET 99999",
        (account,),
    ).fetchone()
    if anchor is None:
        raise ValueError("Statement benchmark requires more than 100,000 wallet entries")
    offset_query = projection + order + " OFFSET 100000"
    cursor_query = projection + "AND (e.created_at, e.id) < (%s, %s) " + order
    offset_rows = conn.execute(offset_query, (account,)).fetchall()
    cursor_rows = conn.execute(cursor_query, (account, *anchor)).fetchall()
    if len(offset_rows) != 51 or offset_rows != cursor_rows:
        raise AssertionError("Offset and cursor must return the same 51 rows")
    plans = {}
    for name, query, params in (
        ("offset_100000", offset_query, (account,)),
        ("cursor", cursor_query, (account, *anchor)),
    ):
        plans[name] = conn.execute(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query, params
        ).fetchone()[0][0]
    return {"matching_rows": len(offset_rows), "plans": plans}
