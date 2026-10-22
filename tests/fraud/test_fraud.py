import asyncio
from uuid import uuid4

import pytest

from tests.support.fraud import peer, rule
from tests.support.transfers import setup_pair

pytestmark = pytest.mark.database


def review(api, transfer_id, action="release", key=None, token=None):
    return api.request(
        "POST",
        f"/admin/transfers/{transfer_id}/{action}",
        token=token or api.admin_token,
        key=str(key or uuid4()),
        json={"reason": "Confirmed with the wallet owner"},
    )


def test_hold_moves_no_money_and_release_posts_original_once(api):
    sender, _, source, destination = setup_pair(api)
    rule(api)
    held = peer(api, sender, source, destination)
    assert held.status_code == 201, held.text
    assert held.json()["status"] == "held"
    transfer_id = held.json()["id"]
    with api.owner() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM ledger_entries WHERE transfer_id = %s", (transfer_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT balance FROM accounts WHERE id = %s", (source["id"],)).fetchone()[
                0
            ]
            == 50000
        )
    released = review(api, transfer_id)
    assert released.json()["status"] == "posted", released.text
    assert released.json()["id"] == transfer_id
    assert review(api, transfer_id).status_code == 409
    with api.owner() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM ledger_entries WHERE transfer_id = %s", (transfer_id,)
            ).fetchone()[0]
            == 2
        )
        events = conn.execute(
            "SELECT status, reason FROM transfer_status_events WHERE transfer_id = %s ORDER BY id",
            (transfer_id,),
        ).fetchall()
        assert [r[0] for r in events] == ["pending", "held", "posted"]
        assert events[-1][1] == "Confirmed with the wallet owner"


def test_reject_has_no_entries_and_is_terminal(api):
    sender, _, source, destination = setup_pair(api)
    rule(api)
    transfer = peer(api, sender, source, destination).json()
    assert review(api, transfer["id"], "reject").json()["status"] == "rejected"
    assert review(api, transfer["id"]).status_code == 409
    with api.owner() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM ledger_entries WHERE transfer_id = %s", (transfer["id"],)
            ).fetchone()[0]
            == 0
        )


def test_flag_action_posts_money_and_preserves_rule_snapshot(api):
    sender, _, source, destination = setup_pair(api)
    rule(api, action="flag")
    result = peer(api, sender, source, destination).json()
    assert result["status"] == "posted"
    rule(api, threshold=99999)
    with api.owner() as conn:
        snapshot = conn.execute(
            "SELECT rule_snapshot FROM risk_flags WHERE transfer_id = %s", (result["id"],)
        ).fetchone()[0]
        assert snapshot["threshold"] == 500
        assert snapshot["observed"] == 1000


@pytest.mark.parametrize(
    "kind,threshold,window",
    [("amount", 1000, None), ("velocity", 1, 60), ("daily_total", 1000, 86400)],
)
def test_threshold_boundary_and_next_request(api, kind, threshold, window):
    sender, _, source, destination = setup_pair(api)
    rule(api, kind, threshold, window=window)
    assert peer(api, sender, source, destination).json()["status"] == "posted"
    assert (
        peer(api, sender, source, destination, 1001 if kind == "amount" else 1000).json()["status"]
        == "held"
    )


def test_release_rechecks_funds_after_another_payment(api):
    sender, _, source, destination = setup_pair(api)
    rule(api)
    transfer = peer(api, sender, source, destination, 40000).json()
    rule(api, threshold=999999)
    assert peer(api, sender, source, destination, 20000).json()["status"] == "posted"
    result = review(api, transfer["id"])
    assert result.status_code == 409
    assert result.json()["code"] == "INSUFFICIENT_FUNDS"
    assert (
        api.request("GET", f"/transfers/{transfer['id']}", token=sender["token"]).json()["status"]
        == "held"
    )


def test_review_is_admin_only_and_owner_can_read_current_state(api):
    sender, _, source, destination = setup_pair(api)
    rule(api)
    transfer = peer(api, sender, source, destination).json()
    assert review(api, transfer["id"], token=sender["token"]).status_code == 403
    outsider = api.user()
    assert (
        api.request("GET", f"/transfers/{transfer['id']}", token=outsider["token"]).status_code
        == 404
    )


def test_parallel_release_and_reject_have_one_winner(api):
    sender, _, source, destination = setup_pair(api)
    rule(api)
    transfer = peer(api, sender, source, destination).json()

    async def perform():
        async with api.client() as client:
            return await asyncio.gather(
                *[
                    client.post(
                        f"/admin/transfers/{transfer['id']}/{action}",
                        headers={
                            "Authorization": f"Bearer {api.admin_token}",
                            "Idempotency-Key": str(uuid4()),
                        },
                        json={"reason": "Reviewed by administrator"},
                    )
                    for action in ["release", "reject"] * 5
                ]
            )

    responses = asyncio.run(perform())
    assert sum(r.status_code == 201 for r in responses) == 1
    assert sum(r.status_code == 409 for r in responses) == 9
    with api.owner() as conn:
        count = conn.execute(
            "SELECT count(*) FROM ledger_entries WHERE transfer_id = %s", (transfer["id"],)
        ).fetchone()[0]
        assert count in (0, 2)


def test_parallel_velocity_counts_committed_predecessors(api):
    sender, _, source, destination = setup_pair(api)
    rule(api, "velocity", 5, window=60)

    async def perform():
        async with api.client() as client:
            return await asyncio.gather(
                *[
                    client.post(
                        "/transfers",
                        headers={
                            "Authorization": f"Bearer {sender['token']}",
                            "Idempotency-Key": str(uuid4()),
                        },
                        json={
                            "type": "peer",
                            "source_account_id": source["id"],
                            "destination_account_id": destination["id"],
                            "amount": 100,
                        },
                    )
                    for _ in range(20)
                ]
            )

    responses = asyncio.run(perform())
    assert all(r.status_code == 201 for r in responses)
    assert sum(r.json()["status"] == "posted" for r in responses) == 5
    assert sum(r.json()["status"] == "held" for r in responses) == 15


def test_release_retry_and_creation_retry_keep_their_original_snapshots(api):
    sender, _, source, destination = setup_pair(api)
    rule(api)
    key = str(uuid4())
    body = {
        "type": "peer",
        "source_account_id": source["id"],
        "destination_account_id": destination["id"],
        "amount": 1000,
    }
    first = api.request("POST", "/transfers", token=sender["token"], key=key, json=body)
    release_key = str(uuid4())
    released = review(api, first.json()["id"], key=release_key)
    assert review(api, first.json()["id"], key=release_key).content == released.content
    repeated = api.request("POST", "/transfers", token=sender["token"], key=key, json=body)
    assert repeated.content == first.content
    assert repeated.json()["status"] == "held"
