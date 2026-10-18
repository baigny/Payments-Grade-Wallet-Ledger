import pytest

pytestmark = pytest.mark.database


def setup_pair(api, amount=50000):
    sender, recipient = api.user("Nila"), api.user("Ishan")
    source, destination = api.wallet(sender), api.wallet(recipient)
    api.fund(source, amount)
    return sender, recipient, source, destination


def test_peer_transfer_updates_balances_ledger_and_actor(api):
    sender, recipient, source, destination = setup_pair(api)
    response = api.transfer(
        sender,
        type="peer",
        source_account_id=source["id"],
        destination_account_id=destination["id"],
        amount=1250,
        note="Lunch",
    )
    assert response.status_code == 201
    posted = response.json()
    assert posted["status"] == "posted"
    with api.owner() as conn:
        assert (
            conn.execute("SELECT balance FROM accounts WHERE id = %s", (source["id"],)).fetchone()[
                0
            ]
            == 48750
        )
        assert (
            conn.execute(
                "SELECT balance FROM accounts WHERE id = %s", (destination["id"],)
            ).fetchone()[0]
            == 1250
        )
        assert conn.execute(
            "SELECT direction, amount FROM ledger_entries WHERE transfer_id = %s ORDER BY id",
            (posted["id"],),
        ).fetchall() == [("debit", 1250), ("credit", 1250)]
        assert conn.execute(
            "SELECT actor_id, status FROM transfer_status_events "
            "WHERE transfer_id = %s ORDER BY id",
            (posted["id"],),
        ).fetchall() == [(sender["id"], "pending"), (sender["id"], "posted")]
    statement = api.request(
        "GET", f"/wallets/{destination['id']}/statement", token=recipient["token"]
    )
    assert statement.json()["items"][0]["note"] == "Lunch"


def test_insufficient_funds_leaves_no_transfer_or_entries(api):
    sender, _, source, destination = setup_pair(api, amount=500)
    with api.owner() as conn:
        before = conn.execute("SELECT count(*) FROM transfers").fetchone()[0]
    result = api.transfer(
        sender,
        type="peer",
        source_account_id=source["id"],
        destination_account_id=destination["id"],
        amount=501,
    )
    assert result.status_code == 409
    assert result.json()["code"] == "INSUFFICIENT_FUNDS"
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM transfers").fetchone()[0] == before
        assert conn.execute("SELECT count(*) FROM ledger_entries").fetchone()[0] == 2


def test_cannot_spend_another_users_funds(api):
    _, recipient, source, destination = setup_pair(api)
    response = api.transfer(
        recipient,
        type="peer",
        source_account_id=source["id"],
        destination_account_id=destination["id"],
        amount=1000,
    )
    assert response.status_code == 404


@pytest.mark.parametrize("kind,system", [("withdrawal", "cash_out"), ("fee", "fee_revenue")])
def test_withdrawal_and_fee_use_server_selected_accounts(api, kind, system):
    sender, _, source, _ = setup_pair(api)
    token_user = sender if kind == "withdrawal" else {"token": api.admin_token}
    response = api.transfer(token_user, type=kind, source_account_id=source["id"], amount=700)
    assert response.status_code == 201
    with api.owner() as conn:
        assert (
            conn.execute(
                "SELECT balance FROM accounts WHERE account_type = %s", (system,)
            ).fetchone()[0]
            == 700
        )


@pytest.mark.parametrize("kind", ["top_up", "fee"])
def test_system_movements_require_admin(api, kind):
    user = api.user()
    wallet = api.wallet(user)
    field = "destination_account_id" if kind == "top_up" else "source_account_id"
    assert api.transfer(user, type=kind, amount=100, **{field: wallet["id"]}).status_code == 403


@pytest.mark.parametrize("amount", [0, -1, 1.5, True, "1000", 2**63])
def test_money_must_be_a_positive_bigint(api, amount):
    user = api.user()
    response = api.transfer(
        user, type="peer", source_account_id=4, destination_account_id=5, amount=amount
    )
    assert response.status_code == 422


def test_same_account_and_missing_endpoint_are_rejected(api):
    user = api.user()
    wallet = api.wallet(user)
    assert (
        api.transfer(
            user,
            type="peer",
            source_account_id=wallet["id"],
            destination_account_id=wallet["id"],
            amount=1,
        ).status_code
        == 422
    )
    assert (
        api.transfer(user, type="peer", source_account_id=wallet["id"], amount=1).status_code == 422
    )


def test_full_reversal_preserves_original_and_restores_balances(api):
    sender, _, source, destination = setup_pair(api)
    original = api.transfer(
        sender,
        type="peer",
        source_account_id=source["id"],
        destination_account_id=destination["id"],
        amount=1200,
    ).json()
    reversal = api.request("POST", f"/transfers/{original['id']}/reversal", token=api.admin_token)
    assert reversal.status_code == 201
    assert reversal.json()["reversal_of"] == original["id"]
    assert (
        api.request("POST", f"/transfers/{original['id']}/reversal", token=api.admin_token).json()[
            "code"
        ]
        == "ALREADY_REVERSED"
    )
    with api.owner() as conn:
        assert (
            conn.execute("SELECT balance FROM accounts WHERE id = %s", (source["id"],)).fetchone()[
                0
            ]
            == 50000
        )
        assert (
            conn.execute(
                "SELECT balance FROM accounts WHERE id = %s", (destination["id"],)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT status FROM transfers WHERE id = %s", (original["id"],)
            ).fetchone()[0]
            == "posted"
        )


def test_reversal_rechecks_funds(api):
    sender, _, source, _ = setup_pair(api, amount=500)
    with api.owner() as conn:
        original_id = conn.execute("SELECT id FROM transfers ORDER BY id LIMIT 1").fetchone()[0]
    assert (
        api.transfer(
            sender, type="withdrawal", source_account_id=source["id"], amount=500
        ).status_code
        == 201
    )
    response = api.request("POST", f"/transfers/{original_id}/reversal", token=api.admin_token)
    assert response.json()["code"] == "INSUFFICIENT_FUNDS"


def test_failure_at_commit_rolls_back_balances_entries_and_audit(api):
    sender, _, source, destination = setup_pair(api)
    with api.owner() as conn:
        conn.execute(
            "CREATE FUNCTION fail_posting() RETURNS TRIGGER LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'Injected commit failure' USING ERRCODE = '23514'; "
            "END; $$"
        )
        conn.execute(
            "CREATE CONSTRAINT TRIGGER fail_posting AFTER UPDATE ON transfers "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fail_posting()"
        )
    response = api.transfer(
        sender,
        type="peer",
        source_account_id=source["id"],
        destination_account_id=destination["id"],
        amount=1000,
    )
    assert response.status_code == 500
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM transfers").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM ledger_entries").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM transfer_status_events").fetchone()[0] == 2
        assert (
            conn.execute("SELECT balance FROM accounts WHERE id = %s", (source["id"],)).fetchone()[
                0
            ]
            == 50000
        )


def test_statement_has_stable_time_and_id_pagination(api):
    sender, _, source, _ = setup_pair(api)
    for _ in range(3):
        assert (
            api.transfer(
                sender, type="withdrawal", source_account_id=source["id"], amount=10
            ).status_code
            == 201
        )
    path = f"/wallets/{source['id']}/statement?limit=2"
    first = api.request("GET", path, token=sender["token"]).json()["items"]
    second = api.request("GET", path + "&offset=2", token=sender["token"]).json()["items"]
    combined = first + second
    assert len({row["id"] for row in combined}) == 4
    assert combined == sorted(
        combined, key=lambda row: (row["created_at"], row["id"]), reverse=True
    )


def test_receiver_overflow_rejects_without_partial_posting(api):
    sender, recipient = api.user(), api.user()
    source, destination = api.wallet(sender), api.wallet(recipient)
    api.fund(destination, 2**63 - 1)
    api.fund(source, 1)
    response = api.transfer(
        sender,
        type="peer",
        source_account_id=source["id"],
        destination_account_id=destination["id"],
        amount=1,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "BALANCE_OVERFLOW"
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM transfers").fetchone()[0] == 2
        assert (
            conn.execute("SELECT balance FROM accounts WHERE id = %s", (source["id"],)).fetchone()[
                0
            ]
            == 1
        )
