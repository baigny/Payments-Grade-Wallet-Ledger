from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from psycopg.rows import dict_row

from wallet import reconciliation, worker


def test_due_date_handles_midnight_and_the_exact_boundary():
    assert worker.due_date(datetime(2026, 1, 1, 1, 59, tzinfo=UTC), 2, 0) == date(2025, 12, 31)
    assert worker.due_date(datetime(2026, 1, 1, 2, 0, tzinfo=UTC), 2, 0) == date(2026, 1, 1)
    india = timezone(timedelta(hours=5, minutes=30))
    assert worker.due_date(datetime(2026, 1, 1, 7, 30, tzinfo=india), 2, 0) == date(2026, 1, 1)
    with pytest.raises(ValueError):
        worker.due_date(datetime(2026, 1, 1), 2, 0)


@pytest.mark.parametrize(
    "name,value",
    [
        ("RECONCILIATION_ENABLED", "maybe"),
        ("RECONCILIATION_UTC_HOUR", "24"),
        ("RECONCILIATION_UTC_MINUTE", "60"),
    ],
)
def test_invalid_schedule_fails_fast(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        worker.settings()


def test_disabled_schedule(monkeypatch):
    monkeypatch.setenv("RECONCILIATION_ENABLED", "false")
    assert worker.settings()[0] is False


@pytest.mark.database
def test_restart_and_second_worker_do_not_repeat_a_schedule_date(api):
    target = date(2026, 1, 2)
    first = reconciliation.execute("scheduled", target)
    assert first["status"] == "passed"
    assert reconciliation.execute("scheduled", target) is None
    second = reconciliation.execute("scheduled", target + timedelta(days=1))
    assert second["id"] != first["id"]
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM reconciliation_runs").fetchone()[0] == 2


@pytest.mark.database
def test_missing_lock_never_allows_an_unlocked_run(api):
    with api.owner() as conn, conn.transaction():
        conn.execute("SET LOCAL session_replication_role = replica")
        conn.execute("DELETE FROM job_locks")
    with pytest.raises(RuntimeError, match="job lock is missing"):
        reconciliation.execute()


@pytest.mark.database
def test_report_rows_are_immutable(api):
    import psycopg

    with api.owner(row_factory=dict_row) as conn:
        result = reconciliation.execute()
        conn.execute(
            "INSERT INTO reconciliation_findings "
            "(run_id, check_name, entity_type, expected, actual) "
            "VALUES (%s, 'test', 'global', '{}', '{}')",
            (result["id"],),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE reconciliation_findings SET actual = '{\"changed\":true}'")
