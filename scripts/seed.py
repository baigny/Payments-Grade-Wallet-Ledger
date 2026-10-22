"""Load a deterministic review dataset into an empty, migrated database."""

import argparse
import json

from wallet.core.db import connect
from wallet.reconciliation.queries import CHECKS, PROBLEMS


def seed(conn, users=1000, wallets=2000, transfers=100000):
    if users < 1 or wallets < max(users, 2) or transfers < wallets:
        raise ValueError("Require users >= 1, wallets >= max(users, 2), transfers >= wallets")
    with conn.transaction():
        # Fail closed rather than mixing synthetic money with an existing installation.
        conn.execute("LOCK TABLE users, accounts, transfers IN SHARE ROW EXCLUSIVE MODE")
        if conn.execute("SELECT EXISTS (SELECT 1 FROM users)").fetchone()[0]:
            raise ValueError("Seed requires an empty database: users already exist")
        if conn.execute("SELECT EXISTS (SELECT 1 FROM transfers)").fetchone()[0]:
            raise ValueError("Seed requires an empty database: transfers already exist")
        conn.execute(
            "INSERT INTO users (name, email, phone, kyc_status) "
            "SELECT 'Review User ' || n, 'review.' || n || '@example.test', "
            "'+918' || lpad(n::text, 9, '0'), 'verified' FROM generate_series(1, %s) n",
            (users,),
        )
        conn.execute(
            "CREATE TEMP TABLE seed_users ON COMMIT DROP AS "
            "SELECT id, row_number() OVER (ORDER BY id) AS n FROM users"
        )
        conn.execute(
            "INSERT INTO accounts (user_id, name, account_type) "
            "SELECT u.id, 'Wallet ' || g.n, 'wallet' FROM generate_series(1, %s) AS g(n) "
            "JOIN seed_users u ON u.n = ((g.n - 1) %% %s) + 1",
            (wallets, users),
        )
        conn.execute(
            "CREATE TEMP TABLE seed_wallets ON COMMIT DROP AS "
            "SELECT id, user_id, row_number() OVER (ORDER BY id) AS n "
            "FROM accounts WHERE account_type = 'wallet'"
        )
        conn.execute(
            "INSERT INTO transfers (source_account_id, destination_account_id, type, amount, "
            "note, created_at) SELECT s.id, w.id, 'top_up', 100000, 'Review opening funding', "
            "'2026-01-01'::timestamptz + w.n * interval '1 second' "
            "FROM seed_wallets w CROSS JOIN accounts s WHERE s.account_type = 'cash_in'"
        )
        conn.execute(
            "INSERT INTO transfers (initiated_by, source_account_id, destination_account_id, "
            "type, amount, note, created_at) SELECT src.user_id, src.id, dst.id, 'peer', 100, "
            "'Review peer payment ' || g.n, '2026-02-01'::timestamptz + g.n * interval '1 second' "
            "FROM generate_series(1, %s) AS g(n) "
            "JOIN seed_wallets src ON src.n = ((g.n - 1) %% %s) + 1 "
            "JOIN seed_wallets dst ON dst.n = (g.n %% %s) + 1",
            (transfers - wallets, wallets, wallets),
        )
        conn.execute(
            "INSERT INTO ledger_entries (transfer_id, account_id, direction, amount, created_at) "
            "SELECT id, source_account_id, 'debit', amount, created_at FROM transfers UNION ALL "
            "SELECT id, destination_account_id, 'credit', amount, created_at FROM transfers"
        )
        conn.execute(
            "UPDATE accounts a SET balance = totals.balance FROM "
            "(SELECT account_id, sum(CASE WHEN direction = 'credit' THEN amount ELSE -amount END) "
            "AS balance FROM ledger_entries GROUP BY account_id) totals "
            "WHERE a.id = totals.account_id"
        )
        conn.execute("UPDATE transfers SET status = 'posted'")
        # Force all deferred posting guards before reporting successful validation.
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        for queries in (CHECKS, PROBLEMS):
            for name, query in queries.items():
                if conn.execute(query).fetchone() is not None:
                    raise AssertionError("Seed failed reconciliation: " + name)
        counts = conn.execute(
            "SELECT (SELECT count(*) FROM users), "
            "(SELECT count(*) FROM accounts WHERE account_type = 'wallet'), "
            "(SELECT count(*) FROM transfers), (SELECT count(*) FROM ledger_entries), "
            "(SELECT count(*) FROM transfer_status_events)"
        ).fetchone()
        if counts != (users, wallets, transfers, 2 * transfers, 2 * transfers):
            raise AssertionError("Unexpected seed or audit counts: " + repr(counts))
    return dict(
        zip(
            ("users", "wallets", "transfers", "ledger_entries", "status_events"),
            counts,
            strict=True,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=1000)
    parser.add_argument("--wallets", type=int, default=2000)
    parser.add_argument("--transfers", type=int, default=100000)
    args = parser.parse_args()
    with connect(autocommit=True) as conn:
        print(json.dumps(seed(conn, args.users, args.wallets, args.transfers), indent=2))


if __name__ == "__main__":
    main()
