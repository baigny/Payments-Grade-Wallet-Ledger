def rule(api, kind="amount", threshold=500, action="hold", window=None):
    with api.owner() as conn:
        conn.execute(
            "UPDATE risk_rules SET threshold = %s, action = %s, window_seconds = %s "
            "WHERE rule_type = %s",
            (threshold, action, window, kind),
        )


def peer(api, actor, source, destination, amount=1000):
    return api.transfer(
        actor,
        type="peer",
        source_account_id=source["id"],
        destination_account_id=destination["id"],
        amount=amount,
    )
