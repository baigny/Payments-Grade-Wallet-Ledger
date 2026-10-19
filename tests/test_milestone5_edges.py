from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from tests.test_fraud import peer, rule
from tests.test_reconciliation import seed_postings
from tests.test_statement_questions import ask
from tests.test_transfers_api import setup_pair
from wallet import explanations, llm

pytestmark = pytest.mark.database


def test_rule_updates_are_admin_only_audited_and_idempotent(api):
    user = api.user()
    with api.owner() as conn:
        rule_id = conn.execute("SELECT id FROM risk_rules WHERE rule_type = 'amount'").fetchone()[0]
    body = {"threshold": 500, "window_seconds": None, "action": "hold", "enabled": True}
    path = f"/admin/risk-rules/{rule_id}"
    assert api.request("POST", path, token=user["token"], json=body).status_code == 403
    key = "5917999a-fb9b-458d-a382-c2275e1fd813"
    first = api.request("POST", path, token=api.admin_token, key=key, json=body)
    repeated = api.request("POST", path, token=api.admin_token, key=key, json=body)
    assert first.status_code == 201
    assert first.content == repeated.content
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM risk_rule_events").fetchone()[0] == 1


def test_old_history_does_not_trigger_current_velocity(api):
    sender, _, source, destination = setup_pair(api)
    rule(api, "velocity", 1, window=60)
    first = peer(api, sender, source, destination).json()
    with api.owner() as conn, conn.transaction():
        conn.execute("SET LOCAL session_replication_role = replica")
        conn.execute(
            "UPDATE transfers SET created_at = now() - interval '2 minutes' WHERE id = %s",
            (first["id"],),
        )
    assert peer(api, sender, source, destination).json()["status"] == "posted"


def test_insufficient_funds_does_not_create_a_hold_or_flag(api):
    sender, _, source, destination = setup_pair(api)
    rule(api)
    result = peer(api, sender, source, destination, 60000)
    assert result.status_code == 409
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM risk_flags").fetchone()[0] == 0


def test_risk_snapshot_cannot_be_rewritten(api):
    sender, _, source, destination = setup_pair(api)
    rule(api)
    peer(api, sender, source, destination)
    with api.owner() as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE risk_flags SET rule_snapshot = '{}'")


def test_internal_wallet_transfer_is_counted_once(api):
    user = api.user()
    first, second = api.wallet(user), api.wallet(user)
    api.fund(first, 1000)
    assert peer(api, user, first, second).status_code == 201
    result = ask(api, user, "Count transfers from 2026-01-01 to 2026-12-31")
    assert result.status_code == 201, result.text
    assert result.json()["value"] == 2
    assert len(result.json()["transfer_ids"]) == 2


def test_duplicate_matching_counterparty_names_require_disambiguation(api):
    user = api.user()
    wallet = api.wallet(user)
    api.fund(wallet)
    for _ in range(2):
        other = api.user("Rahul")
        assert peer(api, user, wallet, api.wallet(other)).status_code == 201
    response = ask(api, user, "Total sent to Rahul from 2026-01-01 to 2026-12-31")
    assert response.status_code == 422
    assert response.json()["code"] == "AMBIGUOUS_COUNTERPARTY"


def test_overly_broad_questions_fail_without_truncating_the_total(api):
    with api.owner() as conn:
        wallet = seed_postings(conn, 1001)
        user_id = conn.execute("SELECT user_id FROM accounts WHERE id = %s", (wallet,)).fetchone()[
            0
        ]
    response = ask(
        api,
        {"id": user_id, "token": api.admin_token},
        "Count transfers from 2026-01-01 to 2026-12-31",
    )
    assert response.status_code == 422
    assert response.json()["code"] == "QUESTION_TOO_BROAD"


def test_nightly_cutoff_defers_new_flags_until_the_next_night(api):
    sender, _, source, destination = setup_pair(api)
    rule(api)
    peer(api, sender, source, destination)
    assert explanations.execute(cutoff=datetime.now(UTC) - timedelta(days=1))["explained"] == 0
    assert explanations.execute(cutoff=datetime.now(UTC) + timedelta(seconds=1))["explained"] == 1


def test_system_source_explanations_never_include_other_wallet_history(api, monkeypatch):
    sender, _, source, _ = setup_pair(api)
    outsider = api.user()
    outside_funding = api.fund(api.wallet(outsider))
    rule(api)
    held = api.fund(source, 1000)
    assert held["status"] == "held"
    provider = llm.MockProvider()
    monkeypatch.setattr(llm, "provider", lambda: provider)
    result = explanations.execute()
    assert result["explained"] == 1
    history = provider.calls[0][1]["history"]
    assert history
    assert outside_funding["id"] not in {row["id"] for row in history}
    assert all(
        set(row) == {"id", "type", "amount", "status", "note", "created_at"} for row in history
    )
