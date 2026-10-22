import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from tests.support.transfers import setup_pair

pytestmark = pytest.mark.database


async def requests(api, actor, bodies):
    async with api.client() as client:
        return await asyncio.gather(
            *[
                client.post(
                    "/transfers",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {actor['token']}",
                        "Idempotency-Key": str(uuid4()),
                    },
                )
                for body in bodies
            ]
        )


@pytest.mark.parametrize("round_number", range(1, 21))
def test_stampede_100_requests(api, round_number):
    sender, _, source, destination = setup_pair(api, amount=50000)
    body = {
        "type": "peer",
        "source_account_id": source["id"],
        "destination_account_id": destination["id"],
        "amount": 1000,
    }
    responses = asyncio.run(requests(api, sender, [body] * 100))
    assert sum(response.status_code == 201 for response in responses) == 50, (
        round_number,
        [
            (response.status_code, response.text)
            for response in responses
            if response.status_code not in (201, 409)
        ],
    )
    assert sum(response.status_code == 409 for response in responses) == 50
    assert all(
        response.json()["code"] == "INSUFFICIENT_FUNDS"
        for response in responses
        if response.status_code == 409
    )
    with api.owner() as conn:
        balances = dict(conn.execute("SELECT id, balance FROM accounts").fetchall())
        assert balances[source["id"]] == 0
        assert balances[destination["id"]] == 50000
        assert sum(balances.values()) == 0
        assert (
            conn.execute(
                "SELECT min(balance) FROM accounts WHERE account_type = 'wallet'"
            ).fetchone()[0]
            >= 0
        )
        assert conn.execute(
            "SELECT sum(amount) FILTER (WHERE direction = 'debit'), "
            "sum(amount) FILTER (WHERE direction = 'credit') FROM ledger_entries"
        ).fetchone() == (100000, 100000)
        assert (
            conn.execute(
                "SELECT count(*) FROM (SELECT transfer_id FROM ledger_entries GROUP BY transfer_id "
                "HAVING sum(CASE WHEN direction = 'credit' THEN amount ELSE -amount END) <> 0) x"
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT count(*) FROM transfers WHERE type = 'peer'").fetchone()[0] == 50
        )
    print(f"round {round_number}: 50 posted, 50 insufficient, balance 0, books balanced")


def test_opposite_direction_transfers_do_not_deadlock(api):
    first, second, left, right = setup_pair(api)
    api.fund(right)

    async def run():
        async with api.client() as client:
            tasks = []
            for i in range(100):
                actor, source, destination = (
                    (first, left, right) if i % 2 else (second, right, left)
                )
                tasks.append(
                    client.post(
                        "/transfers",
                        headers={
                            "Authorization": f"Bearer {actor['token']}",
                            "Idempotency-Key": str(uuid4()),
                        },
                        json={
                            "type": "peer",
                            "source_account_id": source["id"],
                            "destination_account_id": destination["id"],
                            "amount": 100,
                        },
                    )
                )
            return await asyncio.gather(*tasks)

    responses = asyncio.run(run())
    assert all(response.status_code == 201 for response in responses)
    with api.owner() as conn:
        assert conn.execute(
            "SELECT balance FROM accounts WHERE account_type = 'wallet' ORDER BY id"
        ).fetchall() == [(50000,), (50000,)]


def test_concurrent_reversal_posts_only_once(api):
    user = api.user()
    wallet = api.wallet(user)
    original = api.fund(wallet)

    async def run():
        async with api.client() as client:
            return await asyncio.gather(
                *[
                    client.post(
                        f"/transfers/{original['id']}/reversal",
                        headers={
                            "Authorization": f"Bearer {api.admin_token}",
                            "Idempotency-Key": str(uuid4()),
                        },
                    )
                    for _ in range(10)
                ]
            )

    responses = asyncio.run(run())
    assert sum(response.status_code == 201 for response in responses) == 1
    assert sum(response.status_code == 409 for response in responses) == 9
    assert all(
        response.json()["code"] == "ALREADY_REVERSED"
        for response in responses
        if response.status_code == 409
    )


def test_unsafe_stale_balance_update_loses_money_in_cache(api):
    # Educational counterexample only. No application route imports this code.
    sender, _, source, destination = setup_pair(api)
    barrier = Barrier(20)

    def unsafe_transfer(_):
        with api.owner() as conn, conn.transaction():
            old_balance = conn.execute(
                "SELECT balance FROM accounts WHERE id = %s", (source["id"],)
            ).fetchone()[0]
            assert old_balance >= 1000
            barrier.wait(timeout=30)  # Force twenty requests to check the same stale balance.
            transfer_id = conn.execute(
                "INSERT INTO transfers (initiated_by, source_account_id, destination_account_id, "
                "type, amount) VALUES (%s, %s, %s, 'peer', 1000) RETURNING id",
                (sender["id"], source["id"], destination["id"]),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO ledger_entries (transfer_id, account_id, direction, amount) "
                "VALUES (%s, %s, 'debit', 1000), (%s, %s, 'credit', 1000)",
                (transfer_id, source["id"], transfer_id, destination["id"]),
            )
            conn.execute(
                "UPDATE accounts SET balance = %s WHERE id = %s", (old_balance - 1000, source["id"])
            )
            conn.execute(
                "UPDATE accounts SET balance = balance + 1000 WHERE id = %s", (destination["id"],)
            )
            conn.execute("UPDATE transfers SET status = 'posted' WHERE id = %s", (transfer_id,))

    with ThreadPoolExecutor(max_workers=20) as executor:
        list(executor.map(unsafe_transfer, range(100)))
    with api.owner() as conn:
        cached = conn.execute(
            "SELECT balance FROM accounts WHERE id = %s", (source["id"],)
        ).fetchone()[0]
        computed = conn.execute(
            "SELECT sum(CASE WHEN direction = 'credit' THEN amount ELSE -amount END) "
            "FROM ledger_entries WHERE account_id = %s",
            (source["id"],),
        ).fetchone()[0]
        assert computed == -50000
        assert cached == 45000
        assert cached != computed
    print(f"unsafe: 100 posted; cached {cached}; ledger {computed}; mismatch {cached - computed}")
