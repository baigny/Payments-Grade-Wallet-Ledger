import base64
import json

import pytest
from psycopg.rows import dict_row

from wallet.core.auth import Actor
from wallet.transfers.schemas import TransferCreate
from wallet.transfers.service import create_transfer

pytestmark = pytest.mark.database


def statement(api, user, wallet, query=""):
    return api.request("GET", f"/wallets/{wallet['id']}/statement?{query}", token=user["token"])


def test_seek_pages_have_no_duplicates_with_tied_times_and_new_posting(api):
    user = api.user()
    wallet = api.wallet(user)
    # now() is constant within a transaction: all five entries share a timestamp.
    with api.owner(row_factory=dict_row) as conn, conn.transaction():
        admin_id = conn.execute("SELECT id FROM users WHERE role = 'admin'").fetchone()["id"]
        for _ in range(5):
            create_transfer(
                conn,
                Actor(admin_id, "admin"),
                TransferCreate(type="top_up", destination_account_id=wallet["id"], amount=100),
            )
    expected = statement(api, user, wallet).json()["items"]
    first = statement(api, user, wallet, "limit=2").json()
    assert len({r["created_at"] for r in expected}) == 1
    api.fund(wallet, 999)
    rows = first["items"]
    cursor = first["next_cursor"]
    while cursor:
        page = statement(api, user, wallet, "limit=2&cursor=" + cursor).json()
        rows.extend(page["items"])
        cursor = page["next_cursor"]
    assert [r["id"] for r in rows] == [r["id"] for r in expected]
    assert statement(api, user, wallet, "limit=2&offset=2").json()["items"] == expected[1:3]


def test_cursor_validation_and_ownership(api):
    user = api.user()
    wallet = api.wallet(user)
    other = api.wallet(user, "Other")
    for _ in range(3):
        api.fund(wallet, 100)
    cursor = statement(api, user, wallet, "limit=1").json()["next_cursor"]
    assert statement(api, user, other, "cursor=" + cursor).status_code == 422
    assert statement(api, user, wallet, "offset=1&cursor=" + cursor).status_code == 422
    for bad in ("!", "e30", "a" * 1025, ""):
        assert statement(api, user, wallet, "cursor=" + bad).status_code == 422
    raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    raw["entry_id"] = True
    bad = base64.urlsafe_b64encode(json.dumps(raw).encode()).decode()
    assert statement(api, user, wallet, "cursor=" + bad).status_code == 422
    stranger = api.user()
    assert statement(api, stranger, wallet, "cursor=" + cursor).status_code == 404
    assert statement(api, user, other).json()["next_cursor"] is None
