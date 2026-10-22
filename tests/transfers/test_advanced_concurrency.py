import asyncio
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from psycopg import sql

from tests.support.ledger import assert_books_match
from tests.support.transfers import setup_pair
from wallet.transfers import service as transfers

pytestmark = pytest.mark.database


async def parallel(api, jobs):
    async with api.client() as client:
        return await asyncio.gather(
            *[
                client.post(
                    "/transfers",
                    headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
                    json=body,
                )
                for token, body in jobs
            ]
        )


def test_mixed_peer_withdrawal_and_fee_compete_for_same_funds(api):
    sender, _, source, destination = setup_pair(api)
    jobs = []
    for index in range(100):
        kind = "peer" if index < 40 else "withdrawal" if index < 70 else "fee"
        body = {"type": kind, "source_account_id": source["id"], "amount": 1000}
        if kind == "peer":
            body["destination_account_id"] = destination["id"]
        jobs.append((api.admin_token if kind == "fee" else sender["token"], body))
    responses = asyncio.run(parallel(api, jobs))
    assert sum(r.status_code == 201 for r in responses) == 50
    assert sum(r.status_code == 409 for r in responses) == 50
    assert all(r.json()["code"] == "INSUFFICIENT_FUNDS" for r in responses if r.status_code == 409)
    with api.owner() as conn:
        assert_books_match(conn)
        assert (
            conn.execute("SELECT balance FROM accounts WHERE id = %s", (source["id"],)).fetchone()[
                0
            ]
            == 0
        )


def test_fan_out_to_twenty_wallets_preserves_each_recipient_balance(api):
    sender, recipient, source, first = setup_pair(api)
    destinations = [first, *[api.wallet(recipient, f"Reserve {index}") for index in range(19)]]
    jobs = [
        (
            sender["token"],
            {
                "type": "peer",
                "source_account_id": source["id"],
                "destination_account_id": destinations[index % 20]["id"],
                "amount": 1000,
            },
        )
        for index in range(100)
    ]
    responses = asyncio.run(parallel(api, jobs))
    assert sum(r.status_code == 201 for r in responses) == 50
    assert sum(r.status_code == 409 for r in responses) == 50
    counts = Counter(r.json()["destination_account_id"] for r in responses if r.status_code == 201)
    with api.owner() as conn:
        assert_books_match(conn)
        for destination in destinations:
            assert (
                conn.execute(
                    "SELECT balance FROM accounts WHERE id = %s", (destination["id"],)
                ).fetchone()[0]
                == counts[destination["id"]] * 1000
            )


def test_three_wallet_payment_cycle_has_no_deadlock(api):
    actors = [api.user(name) for name in ("Rhea", "Kabir", "Tara")]
    wallets = [api.wallet(actor) for actor in actors]
    for wallet in wallets:
        api.fund(wallet, 3000)
    jobs = [
        (
            actors[index % 3]["token"],
            {
                "type": "peer",
                "amount": 100,
                "source_account_id": wallets[index % 3]["id"],
                "destination_account_id": wallets[(index + 1) % 3]["id"],
            },
        )
        for index in range(90)
    ]
    responses = asyncio.run(parallel(api, jobs))
    assert all(r.status_code == 201 for r in responses)
    with api.owner() as conn:
        assert_books_match(conn)
        assert conn.execute(
            "SELECT balance FROM accounts WHERE account_type = 'wallet' ORDER BY id"
        ).fetchall() == [(3000,), (3000,), (3000,)]


def test_locked_wallet_does_not_block_an_unrelated_payment(api):
    sender, _, source, destination = setup_pair(api)
    other, _, free_source, free_destination = setup_pair(api)
    with api.owner() as lock, ThreadPoolExecutor(max_workers=1) as executor:
        lock.execute("BEGIN")
        lock.execute("SELECT id FROM accounts WHERE id = %s FOR UPDATE", (source["id"],))
        pid = lock.execute("SELECT pg_backend_pid()").fetchone()[0]
        blocked = executor.submit(
            api.transfer,
            sender,
            type="peer",
            amount=100,
            source_account_id=source["id"],
            destination_account_id=destination["id"],
        )
        try:
            deadline = time.monotonic() + 3
            while not lock.execute(
                "SELECT 1 FROM pg_locks WHERE NOT granted AND %s = ANY(pg_blocking_pids(pid))",
                (pid,),
            ).fetchone():
                assert time.monotonic() < deadline, "Expected the first payment to wait on the lock"
                time.sleep(0.01)
            response = api.transfer(
                other,
                type="peer",
                amount=100,
                source_account_id=free_source["id"],
                destination_account_id=free_destination["id"],
            )
            assert response.status_code == 201
            assert not blocked.done()
        finally:
            lock.execute("ROLLBACK")
        assert blocked.result(timeout=10).status_code == 201
        assert_books_match(lock)


def test_lock_timeout_rolls_back_and_next_payment_recovers(api, monkeypatch):
    sender, _, source, destination = setup_pair(api)
    configure = transfers._configure_transaction

    def shorter_timeout(conn):
        configure(conn)
        conn.execute("SET LOCAL lock_timeout = '100ms'")

    monkeypatch.setattr(transfers, "_configure_transaction", shorter_timeout)
    with api.owner() as lock:
        lock.execute("BEGIN")
        lock.execute("SELECT id FROM accounts WHERE id = %s FOR UPDATE", (source["id"],))
        try:
            response = api.transfer(
                sender,
                type="peer",
                amount=100,
                source_account_id=source["id"],
                destination_account_id=destination["id"],
            )
            assert response.status_code == 503
            assert response.json()["code"] == "DATABASE_UNAVAILABLE"
            assert lock.execute("SELECT count(*) FROM transfers").fetchone()[0] == 1
            assert_books_match(lock)
        finally:
            lock.execute("ROLLBACK")
        assert (
            api.transfer(
                sender,
                type="peer",
                amount=100,
                source_account_id=source["id"],
                destination_account_id=destination["id"],
            ).status_code
            == 201
        )
        assert_books_match(lock)


@pytest.mark.parametrize("failure_point", ["credit_entry", "second_balance_update"])
def test_mid_posting_failure_rolls_back_every_financial_row(api, failure_point):
    sender, _, source, destination = setup_pair(api)
    with api.owner() as conn:
        conn.execute(
            "CREATE FUNCTION injected_failure() RETURNS TRIGGER LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'Injected failure' USING ERRCODE = '23514'; END; $$"
        )
        if failure_point == "credit_entry":
            conn.execute(
                "CREATE TRIGGER injected_failure BEFORE INSERT ON ledger_entries "
                "FOR EACH ROW WHEN (NEW.direction = 'credit') "
                "EXECUTE FUNCTION injected_failure()"
            )
        else:
            conn.execute(
                sql.SQL(
                    "CREATE TRIGGER injected_failure BEFORE UPDATE ON accounts "
                    "FOR EACH ROW WHEN (NEW.id = {}) EXECUTE FUNCTION injected_failure()"
                ).format(sql.Literal(destination["id"]))
            )
    response = api.transfer(
        sender,
        type="peer",
        amount=1000,
        source_account_id=source["id"],
        destination_account_id=destination["id"],
    )
    assert response.status_code == 500
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM transfers").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM ledger_entries").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM transfer_status_events").fetchone()[0] == 2
        assert_books_match(conn)
