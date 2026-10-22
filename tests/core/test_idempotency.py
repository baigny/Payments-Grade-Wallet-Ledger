import asyncio
from uuid import uuid4

import pytest

from tests.support.idempotency import assert_replayed, payment, post
from tests.support.transfers import setup_pair

pytestmark = pytest.mark.database


def test_five_sequential_retries_post_once(api):
    sender, _, source, destination = setup_pair(api)
    key, body = uuid4(), payment(source, destination)
    responses = [post(api, sender["token"], key, body) for _ in range(5)]
    assert responses[0].status_code == 201
    assert responses[0].headers["Idempotent-Replay"] == "false"
    for response in responses[1:]:
        assert_replayed(responses[0], response)
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM transfers WHERE type = 'peer'").fetchone()[0] == 1
        assert (
            conn.execute("SELECT balance FROM accounts WHERE id = %s", (source["id"],)).fetchone()[
                0
            ]
            == 49000
        )
        assert conn.execute("SELECT count(*) FROM ledger_entries").fetchone()[0] == 4


def test_ten_parallel_retries_return_one_saved_response(api):
    sender, _, source, destination = setup_pair(api)
    key, body = uuid4(), payment(source, destination)

    async def perform():
        async with api.client() as client:
            return await asyncio.gather(
                *[
                    client.post(
                        "/transfers",
                        json=body,
                        headers={
                            "Authorization": f"Bearer {sender['token']}",
                            "Idempotency-Key": str(key),
                        },
                    )
                    for _ in range(10)
                ]
            )

    responses = asyncio.run(perform())
    assert all(response.status_code == 201 for response in responses)
    assert len({response.content for response in responses}) == 1
    assert sum(r.headers["Idempotent-Replay"] == "false" for r in responses) == 1
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM transfers WHERE type = 'peer'").fetchone()[0] == 1
        assert (
            conn.execute("SELECT count(*) FROM idempotency_keys WHERE key = %s", (key,)).fetchone()[
                0
            ]
            == 1
        )


def test_changed_amount_rejects_without_posting(api):
    sender, _, source, destination = setup_pair(api)
    key = uuid4()
    first = post(api, sender["token"], key, payment(source, destination))
    changed = post(api, sender["token"], key, payment(source, destination, 2000))
    assert changed.status_code == 422
    assert changed.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert_replayed(first, post(api, sender["token"], key, payment(source, destination)))
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM transfers WHERE type = 'peer'").fetchone()[0] == 1


def test_user_creation_replays_the_same_token(api):
    key = uuid4()
    body = {"name": "Rhea", "email": f"{uuid4().hex}@example.test", "phone": "+919876543210"}
    first = post(api, api.admin_token, key, body, "/users")
    assert first.status_code == 201
    assert_replayed(first, post(api, api.admin_token, key, body, "/users"))
    with api.owner() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM auth_tokens WHERE user_id = %s", (first.json()["id"],)
            ).fetchone()[0]
            == 1
        )


def test_wallet_creation_and_reversal_replay(api):
    user = api.user()
    key, path, body = uuid4(), f"/users/{user['id']}/wallets", {"name": "Main"}
    first = post(api, user["token"], key, body, path)
    assert first.status_code == 201
    funding = api.fund(first.json())
    # The replay is the original creation snapshot, even though the current balance changed.
    assert_replayed(first, post(api, user["token"], key, body, path))
    key, path = uuid4(), f"/transfers/{funding['id']}/reversal"
    first = post(api, api.admin_token, key, None, path)
    assert first.status_code == 201
    assert_replayed(first, post(api, api.admin_token, key, None, path))
    with api.owner() as conn:
        assert (
            conn.execute("SELECT count(*) FROM accounts WHERE account_type = 'wallet'").fetchone()[
                0
            ]
            == 1
        )
        assert (
            conn.execute("SELECT count(*) FROM transfers WHERE type = 'reversal'").fetchone()[0]
            == 1
        )


def test_insufficient_funds_response_stays_saved_after_top_up(api):
    sender, _, source, destination = setup_pair(api)
    key, body = uuid4(), payment(source, destination, 60000)
    first = post(api, sender["token"], key, body)
    assert first.status_code == 409
    api.fund(source, 20000)
    assert_replayed(first, post(api, sender["token"], key, body))
    assert post(api, sender["token"], uuid4(), body).status_code == 201
