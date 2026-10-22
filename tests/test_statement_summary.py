from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.database


def summary(api, user, wallet, start, end):
    return api.request(
        "GET",
        f"/wallets/{wallet['id']}/statement/summary?from_date={start}&to_date={end}",
        token=user["token"],
    )


def test_summary_uses_ledger_and_preserves_opening_balance(api):
    user = api.user()
    wallet = api.wallet(user)
    api.fund(wallet, 1000)
    result = api.transfer(user, type="withdrawal", source_account_id=wallet["id"], amount=250)
    assert result.status_code == 201
    with api.owner() as conn:
        today = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        # A deliberately wrong cache must not alter a ledger statement.
        conn.execute("UPDATE accounts SET balance = 900 WHERE id = %s", (wallet["id"],))
    body = summary(api, user, wallet, today, today).json()
    assert (
        body["opening_balance"],
        body["credit_total"],
        body["debit_total"],
        body["closing_balance"],
        body["transfer_count"],
    ) == (0, 1000, 250, 750, 2)
    tomorrow = today + timedelta(days=1)
    body = summary(api, user, wallet, tomorrow, tomorrow).json()
    assert (
        body["opening_balance"],
        body["credit_total"],
        body["debit_total"],
        body["closing_balance"],
        body["transfer_count"],
    ) == (750, 0, 0, 750, 0)


def test_summary_empty_wallet_ownership_and_invalid_dates(api):
    user, stranger = api.user(), api.user()
    wallet = api.wallet(user)
    today = datetime.now(UTC).date()
    body = summary(api, user, wallet, today, today).json()
    assert body["closing_balance"] == body["opening_balance"] == body["transfer_count"] == 0
    assert summary(api, stranger, wallet, today, today).status_code == 404
    for start, end in (
        ("2026-02-01", "2026-01-01"),
        ("bad", "2026-01-01"),
        ("2026-01-01", "9999-12-31"),
    ):
        assert summary(api, user, wallet, start, end).status_code == 422


def test_summary_keeps_snapshot_when_transfer_commits_during_read(api, monkeypatch):
    from wallet import accounts

    user = api.user()
    wallet = api.wallet(user)
    api.fund(wallet, 1000)
    original = accounts.get_wallet

    def post_after_snapshot(conn, actor, wallet_id):
        row = original(conn, actor, wallet_id)
        api.fund(wallet, 500)
        return row

    monkeypatch.setattr(accounts, "get_wallet", post_after_snapshot)
    today = datetime.now(UTC).date()
    body = summary(api, user, wallet, today, today).json()
    assert body["credit_total"] == body["closing_balance"] == 1000
    with api.owner() as conn:
        assert (
            conn.execute("SELECT balance FROM accounts WHERE id = %s", (wallet["id"],)).fetchone()[
                0
            ]
            == 1500
        )
