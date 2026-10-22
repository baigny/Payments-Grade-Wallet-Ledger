"""Recovery, isolation and authorization checks for retained POST responses."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from tests.support.idempotency import assert_replayed, payment, post
from tests.support.ledger import assert_books_match
from tests.support.transfers import setup_pair
from wallet.core.auth import token_hash
from wallet.transfers import service as transfers

pytestmark = pytest.mark.database


def assert_no_attempt(conn, key):
    assert (
        conn.execute("SELECT count(*) FROM idempotency_keys WHERE key = %s", (key,)).fetchone()[0]
        == 0
    )
    assert conn.execute("SELECT count(*) FROM transfers WHERE type = 'peer'").fetchone()[0] == 0
    assert_books_match(conn)


def test_same_uuid_is_independent_for_different_callers(api):
    users, key = [api.user(), api.user()], uuid4()
    responses = [
        post(api, u["token"], key, {"name": "Main"}, f"/users/{u['id']}/wallets") for u in users
    ]
    assert all(r.status_code == 201 for r in responses)
    assert responses[0].json()["id"] != responses[1].json()["id"]
    with api.owner() as conn:
        assert (
            conn.execute("SELECT count(*) FROM idempotency_keys WHERE key = %s", (key,)).fetchone()[
                0
            ]
            == 2
        )


@pytest.mark.parametrize("different_resource", [False, True])
def test_key_cannot_cross_endpoints_or_resources(api, different_resource):
    users, key = [api.user(), api.user()], uuid4()
    first = post(api, api.admin_token, key, {"name": "Main"}, f"/users/{users[0]['id']}/wallets")
    assert first.status_code == 201
    if different_resource:
        path, body = f"/users/{users[1]['id']}/wallets", {"name": "Main"}
    else:
        path, body = (
            "/transfers",
            {"type": "top_up", "destination_account_id": first.json()["id"], "amount": 1000},
        )
    response = post(api, api.admin_token, key, body, path)
    assert response.status_code == 422
    assert response.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM transfers").fetchone()[0] == 0
        assert (
            conn.execute("SELECT count(*) FROM accounts WHERE account_type = 'wallet'").fetchone()[
                0
            ]
            == 1
        )


def test_json_order_whitespace_defaults_and_unicode_replay(api):
    sender, _, source, destination = setup_pair(api)
    body, key = payment(source, destination), uuid4()
    body["note"] = "Lunch \u20b9 \u2014 caf\u00e9"
    first = post(api, sender["token"], key, body)
    body = {"destination_account_id": destination["id"], **dict(reversed(list(body.items())))}

    async def perform():
        async with api.client() as client:
            return await client.post(
                "/transfers",
                content=json.dumps(body, indent=4),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {sender['token']}",
                    "Idempotency-Key": str(key),
                },
            )

    repeated = asyncio.run(perform())
    assert_replayed(first, repeated)
    # Explicit default values have the same validated representation as omitted defaults.
    key, body = uuid4(), payment(source, destination)
    first = post(api, sender["token"], key, body)
    assert_replayed(first, post(api, sender["token"], key, {**body, "note": ""}))


@pytest.mark.parametrize("kind", ["top_up", "withdrawal", "fee"])
def test_every_money_operation_replays(api, kind):
    sender, _, source, _ = setup_pair(api)
    key = uuid4()
    body = {"type": kind, "amount": 1000}
    body["destination_account_id" if kind == "top_up" else "source_account_id"] = source["id"]
    token = sender["token"] if kind == "withdrawal" else api.admin_token
    first = post(api, token, key, body)
    assert first.status_code == 201
    assert_replayed(first, post(api, token, key, body))
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM transfers").fetchone()[0] == 2
        assert_books_match(conn)


def test_concurrent_different_payloads_choose_one_winner(api):
    sender, _, source, destination = setup_pair(api)
    key = uuid4()

    async def perform():
        async with api.client() as client:
            return await asyncio.gather(
                *[
                    client.post(
                        "/transfers",
                        json=payment(source, destination, amount),
                        headers={
                            "Authorization": f"Bearer {sender['token']}",
                            "Idempotency-Key": str(key),
                        },
                    )
                    for amount in [1000, 2000] * 5
                ]
            )

    results = asyncio.run(perform())
    accepted = [r for r in results if r.status_code == 201]
    rejected = [r for r in results if r.status_code == 422]
    assert len(accepted) == len(rejected) == 5
    assert len({r.content for r in accepted}) == 1
    assert all(r.json()["code"] == "IDEMPOTENCY_KEY_REUSED" for r in rejected)
    assert sum(r.headers["Idempotent-Replay"] == "false" for r in accepted) == 1
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM transfers WHERE type = 'peer'").fetchone()[0] == 1
        assert_books_match(conn)


def test_revoked_token_cannot_recover_a_saved_response(api):
    user, key = api.user(), uuid4()
    path = f"/users/{user['id']}/wallets"
    assert post(api, user["token"], key, {"name": "Main"}, path).status_code == 201
    with api.owner() as conn:
        conn.execute(
            "UPDATE auth_tokens SET revoked_at = now() WHERE token_hash = %s",
            (token_hash(user["token"]),),
        )
    assert post(api, user["token"], key, {"name": "Main"}, path).status_code == 401


def test_admin_permissions_are_checked_before_user_token_replay(api):
    key = uuid4()
    body = {"name": "Rhea", "email": f"{uuid4().hex}@example.test", "phone": "+919876543210"}
    assert post(api, api.admin_token, key, body, "/users").status_code == 201
    with api.owner() as conn:
        conn.execute("UPDATE users SET role = 'user' WHERE role = 'admin'")
    response = post(api, api.admin_token, key, body, "/users")
    assert response.status_code == 403
    assert "token" not in response.json()


def test_unexpected_failure_rolls_back_key_and_all_posting_rows(api, monkeypatch):
    sender, _, source, destination = setup_pair(api)
    key, body = uuid4(), payment(source, destination)
    original = transfers._post_locked

    def fail_after_post(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Injected failure after all financial writes")

    with monkeypatch.context() as patch:
        patch.setattr(transfers, "_post_locked", fail_after_post)
        assert post(api, sender["token"], key, body).status_code == 500
    with api.owner() as conn:
        assert_no_attempt(conn, key)
    assert post(api, sender["token"], key, body).status_code == 201


def test_commit_failure_rolls_back_saved_success_and_can_retry(api):
    sender, _, source, destination = setup_pair(api)
    key, body = uuid4(), payment(source, destination)
    with api.owner() as conn:
        conn.execute(
            "CREATE FUNCTION fail_at_commit() RETURNS TRIGGER LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'Injected commit failure' USING ERRCODE = '23514'; END; $$"
        )
        conn.execute(
            "CREATE CONSTRAINT TRIGGER injected_failure AFTER INSERT ON ledger_entries "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fail_at_commit()"
        )
    assert post(api, sender["token"], key, body).status_code == 500
    with api.owner() as conn:
        assert_no_attempt(conn, key)
        conn.execute("DROP TRIGGER injected_failure ON ledger_entries")
    assert post(api, sender["token"], key, body).status_code == 201


def test_busy_key_returns_conflict_without_poisoning_retry(api, monkeypatch):
    sender, _, source, destination = setup_pair(api)
    key, body = uuid4(), payment(source, destination)
    configure = transfers._configure_transaction

    def shorter_timeout(conn):
        configure(conn)
        conn.execute("SET LOCAL lock_timeout = '100ms'")

    monkeypatch.setattr(transfers, "_configure_transaction", shorter_timeout)
    with api.owner() as conn:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO idempotency_keys (user_id, key, request_hash) VALUES (%s, %s, %s)",
            (sender["id"], key, "0" * 64),
        )
        try:
            response = post(api, sender["token"], key, body)
            assert response.status_code == 409
            assert response.json()["code"] == "IDEMPOTENCY_IN_PROGRESS"
        finally:
            conn.execute("ROLLBACK")
        assert_no_attempt(conn, key)
    assert post(api, sender["token"], key, body).status_code == 201


def test_waiting_duplicate_claims_key_if_first_transaction_rolls_back(api):
    sender, _, source, destination = setup_pair(api)
    key, body = uuid4(), payment(source, destination)
    with api.owner() as conn, ThreadPoolExecutor(max_workers=1) as executor:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO idempotency_keys (user_id, key, request_hash) VALUES (%s, %s, %s)",
            (sender["id"], key, "0" * 64),
        )
        pid = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
        pending = executor.submit(post, api, sender["token"], key, body)
        try:
            deadline = time.monotonic() + 3
            while not conn.execute(
                "SELECT 1 FROM pg_locks WHERE NOT granted AND %s = ANY(pg_blocking_pids(pid))",
                (pid,),
            ).fetchone():
                assert time.monotonic() < deadline, "Duplicate should wait for the first key claim"
                time.sleep(0.01)
        finally:
            conn.execute("ROLLBACK")
        assert pending.result(timeout=10).status_code == 201
        assert_books_match(conn)


def test_validation_does_not_consume_the_key(api):
    sender, _, source, destination = setup_pair(api)
    key = uuid4()
    assert post(api, sender["token"], key, payment(source, destination, 0)).status_code == 422
    with api.owner() as conn:
        assert_no_attempt(conn, key)
    assert post(api, sender["token"], key, payment(source, destination)).status_code == 201


def test_incomplete_key_cannot_commit(api):
    user, key = api.user(), uuid4()
    with api.owner() as conn:
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO idempotency_keys (user_id, key, request_hash) VALUES (%s, %s, %s)",
                (user["id"], key, "0" * 64),
            )
        assert (
            conn.execute("SELECT count(*) FROM idempotency_keys WHERE key = %s", (key,)).fetchone()[
                0
            ]
            == 0
        )


@pytest.mark.parametrize(
    "assignment",
    [
        "response_code = 202",
        "request_hash = repeat('0', 64)",
        "status = 'processing', response_code = NULL, response_body = NULL",
    ],
)
def test_saved_response_and_identity_cannot_be_rewritten(api, assignment):
    sender, _, source, destination = setup_pair(api)
    key, body = uuid4(), payment(source, destination)
    first = post(api, sender["token"], key, body)
    with api.owner() as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE idempotency_keys SET " + assignment + " WHERE key = %s", (key,))
    assert_replayed(first, post(api, sender["token"], key, body))


def test_new_credential_for_same_caller_replays_the_saved_response(api):
    user, key = api.user(), uuid4()
    path = f"/users/{user['id']}/wallets"
    first = post(api, user["token"], key, {"name": "Main"}, path)
    token = "replacement-" + uuid4().hex
    with api.owner() as conn:
        conn.execute(
            "INSERT INTO auth_tokens (user_id, token_hash) VALUES (%s, %s)",
            (user["id"], token_hash(token)),
        )
        stored = conn.execute(
            "SELECT status, response_code, response_body FROM idempotency_keys "
            "WHERE user_id = %s AND key = %s",
            (user["id"], key),
        ).fetchone()
        assert stored == ("completed", first.status_code, first.json())
    assert_replayed(first, post(api, token, key, {"name": "Main"}, path))


def test_parallel_same_key_reversals_return_the_same_posting(api):
    _, _, source, _ = setup_pair(api)
    funding = api.fund(source, 1000)
    path, key = f"/transfers/{funding['id']}/reversal", uuid4()

    async def perform():
        async with api.client() as client:
            return await asyncio.gather(
                *[
                    client.post(
                        path,
                        headers={
                            "Authorization": f"Bearer {api.admin_token}",
                            "Idempotency-Key": str(key),
                        },
                    )
                    for _ in range(10)
                ]
            )

    responses = asyncio.run(perform())
    assert all(r.status_code == 201 for r in responses)
    assert len({r.content for r in responses}) == 1
    assert sum(r.headers["Idempotent-Replay"] == "false" for r in responses) == 1
    with api.owner() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM transfers WHERE reversal_of = %s", (funding["id"],)
            ).fetchone()[0]
            == 1
        )
        assert_books_match(conn)
