import asyncio
from uuid import uuid4

import pytest

from tests.support.reconciliation import seed_postings
from tests.support.transfers import setup_pair
from wallet.reconciliation import service as reconciliation

pytestmark = pytest.mark.database


def run(api, key=None):
    return api.request(
        "POST", "/admin/reconciliation/run", token=api.admin_token, key=str(key or uuid4())
    )


def test_clean_books_and_saved_reports(api):
    sender, _, source, destination = setup_pair(api)
    assert (
        api.transfer(
            sender,
            type="peer",
            source_account_id=source["id"],
            destination_account_id=destination["id"],
            amount=1000,
        ).status_code
        == 201
    )
    response = run(api)
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["status"] == "passed"
    assert report["finding_count"] == report["problem_count"] == 0
    assert report["transfer_count"] == 2
    assert report["entry_count"] == 4
    for suffix in ("", "/findings", "/problems"):
        result = api.request(
            "GET", f"/admin/reconciliation/runs/{report['id']}{suffix}", token=api.admin_token
        )
        assert result.status_code == 200
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM transfers").fetchone()[0] == 2


def test_manual_replay_runs_checks_once(api):
    key = uuid4()
    first, repeated = run(api, key), run(api, key)
    assert first.status_code == 201, first.text
    assert repeated.content == first.content
    assert repeated.headers["Idempotent-Replay"] == "true"
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM reconciliation_runs").fetchone()[0] == 1


def test_admin_required_for_runs_and_reports(api):
    user = api.user()
    result = api.request("POST", "/admin/reconciliation/run", token=user["token"])
    assert result.status_code == 403
    assert (
        api.request("GET", "/admin/reconciliation/runs/1", token=user["token"]).status_code == 403
    )


def test_busy_job_exits_without_creating_a_run(api):
    key = uuid4()
    with api.owner() as conn:
        conn.execute("BEGIN")
        conn.execute("SELECT id FROM job_locks WHERE name = 'reconciliation' FOR UPDATE")
        try:
            response = run(api, key)
            assert response.status_code == 409, response.text
            assert response.json()["code"] == "RECONCILIATION_BUSY"
        finally:
            conn.execute("ROLLBACK")
        assert conn.execute("SELECT count(*) FROM reconciliation_runs").fetchone()[0] == 0
    assert run(api, key).json()["status"] == "passed"


def test_snapshot_remains_consistent_while_new_payment_commits(api, monkeypatch):
    sender, _, source, destination = setup_pair(api)
    original = reconciliation._save_checks
    called = False

    def concurrent_post(conn, run_id, table, queries):
        nonlocal called
        assert (
            conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
            == "repeatable read"
        )
        if not called:
            called = True
            assert (
                api.transfer(
                    sender,
                    type="peer",
                    source_account_id=source["id"],
                    destination_account_id=destination["id"],
                    amount=1000,
                ).status_code
                == 201
            )
        return original(conn, run_id, table, queries)

    monkeypatch.setattr(reconciliation, "_save_checks", concurrent_post)
    response = run(api)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "passed"
    assert response.json()["transfer_count"] == 1
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM transfers").fetchone()[0] == 2


def test_failed_check_records_error_and_discards_partial_findings(api, monkeypatch):
    monkeypatch.setattr(
        reconciliation,
        "CHECKS",
        {
            "first": "SELECT 'global', NULL::bigint, '{}'::jsonb, '{}'::jsonb",
            "broken": "SELECT 'global', NULL::bigint, "
            "jsonb_build_object('value', 1 / 0), '{}'::jsonb",
        },
    )
    response = run(api)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "error"
    assert "22012" in response.json()["error_message"]
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM reconciliation_findings").fetchone()[0] == 0


def test_parallel_same_key_manual_runs_replay(api):
    key = str(uuid4())

    async def perform():
        async with api.client() as client:
            return await asyncio.gather(
                *[
                    client.post(
                        "/admin/reconciliation/run",
                        headers={
                            "Authorization": f"Bearer {api.admin_token}",
                            "Idempotency-Key": key,
                        },
                    )
                    for _ in range(5)
                ]
            )

    responses = asyncio.run(perform())
    assert all(r.status_code == 201 for r in responses), [r.text for r in responses]
    assert len({r.content for r in responses}) == 1


def test_ten_thousand_postings_detect_exactly_three_corruption_problems(api):
    with api.owner() as conn:
        account = seed_postings(conn)
    assert run(api).json()["status"] == "passed"
    with api.owner() as conn, conn.transaction():
        # Only the disposable test schema permits deliberate owner-level tampering.
        conn.execute("SET LOCAL session_replication_role = replica")
        entries = conn.execute(
            "SELECT id FROM ledger_entries WHERE direction = 'credit' ORDER BY id LIMIT 2"
        ).fetchall()
        changed, deleted = entries[0][0], entries[1][0]
        conn.execute("UPDATE ledger_entries SET amount = amount + 1 WHERE id = %s", (changed,))
        conn.execute("DELETE FROM ledger_entries WHERE id = %s", (deleted,))
        conn.execute("UPDATE accounts SET balance = balance + 7 WHERE id = %s", (account,))
    response = run(api)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "failed"
    assert result["problem_count"] == 3
    problems = api.request(
        "GET", f"/admin/reconciliation/runs/{result['id']}/problems", token=api.admin_token
    ).json()["items"]
    assert {r["check_name"] for r in problems} == {
        "entry_mismatch",
        "entry_count",
        "stored_balance",
    }
    assert next(r for r in problems if r["check_name"] == "entry_mismatch")["entity_id"] == changed
    assert next(r for r in problems if r["check_name"] == "stored_balance")["entity_id"] == account
    assert result["finding_count"] > 3  # Downstream invariant failures remain visible.
    with api.owner() as conn:
        assert (
            conn.execute("SELECT balance FROM accounts WHERE id = %s", (account,)).fetchone()[0]
            == 100007
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM ledger_entries WHERE id = %s", (deleted,)
            ).fetchone()[0]
            == 0
        )


def test_findings_pagination_is_bounded(api):
    result = run(api).json()
    for suffix in ("findings", "problems"):
        path = f"/admin/reconciliation/runs/{result['id']}/{suffix}"
        for query in ("limit=0", "limit=101", "offset=-1", f"offset={2**63}"):
            assert api.request("GET", path + "?" + query, token=api.admin_token).status_code == 422


def test_computed_overdraft_is_detected_even_when_cache_is_nonnegative(api):
    _, _, source, _ = setup_pair(api)
    with api.owner() as conn, conn.transaction():
        conn.execute("SET LOCAL session_replication_role = replica")
        conn.execute(
            "UPDATE ledger_entries SET direction = 'debit' WHERE account_id = %s", (source["id"],)
        )
    result = run(api).json()
    assert result["status"] == "failed"
    with api.owner() as conn:
        assert conn.execute(
            "SELECT entity_id FROM reconciliation_findings "
            "WHERE run_id = %s AND check_name = 'overdraft'",
            (result["id"],),
        ).fetchall() == [(source["id"],)]
        assert (
            conn.execute("SELECT balance FROM accounts WHERE id = %s", (source["id"],)).fetchone()[
                0
            ]
            == 50000
        )


def test_orphan_entry_is_reported_after_privileged_fk_bypass(api):
    _, _, source, _ = setup_pair(api)
    with api.owner() as conn, conn.transaction():
        conn.execute("SET LOCAL session_replication_role = replica")
        entry = conn.execute(
            "INSERT INTO ledger_entries (transfer_id, account_id, direction, amount) "
            "VALUES (99999999, %s, 'credit', 10) RETURNING id",
            (source["id"],),
        ).fetchone()[0]
    result = run(api).json()
    with api.owner() as conn:
        assert conn.execute(
            "SELECT entity_id FROM reconciliation_findings "
            "WHERE run_id = %s AND check_name = 'orphan_entry'",
            (result["id"],),
        ).fetchall() == [(entry,)]


def test_held_transfer_without_entries_is_not_an_orphan(api):
    _, _, source, destination = setup_pair(api)
    with api.owner() as conn, conn.transaction():
        transfer = conn.execute(
            "INSERT INTO transfers (source_account_id, destination_account_id, type, amount) "
            "VALUES (%s, %s, 'peer', 100) RETURNING id",
            (source["id"], destination["id"]),
        ).fetchone()[0]
        conn.execute("UPDATE transfers SET status = 'held' WHERE id = %s", (transfer,))
    result = run(api).json()
    assert result["status"] == "passed"
    assert result["finding_count"] == result["problem_count"] == 0
