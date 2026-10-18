import psycopg
import pytest

from wallet.db import connect

pytestmark = pytest.mark.database


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE public.ledger_entries SET amount = amount",
        "DELETE FROM public.ledger_entries WHERE false",
        "TRUNCATE public.ledger_entries",
        "UPDATE public.transfer_status_events SET status = status",
        "DELETE FROM public.transfer_status_events WHERE false",
        "DELETE FROM public.transfers WHERE false",
        "UPDATE public.schema_migrations SET checksum = checksum",
        "CREATE TABLE public.not_allowed (id BIGINT)",
    ],
)
def test_runtime_role_cannot_change_protected_data(schema_name, statement):
    with connect(autocommit=True) as conn:
        with conn.transaction():
            conn.execute("SET LOCAL ROLE wallet_app")
            with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                conn.execute(statement)


def test_runtime_role_can_acquire_job_lock(schema_name):
    with connect(autocommit=True) as conn:
        with conn.transaction():
            conn.execute("SET LOCAL ROLE wallet_app")
            assert conn.execute(
                "SELECT name FROM public.job_locks WHERE name = 'reconciliation' FOR UPDATE NOWAIT"
            ).fetchone() == ("reconciliation",)


def test_runtime_role_is_not_owner_or_superuser(schema_name):
    with connect(autocommit=True) as conn:
        row = conn.execute(
            "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = 'wallet_app'"
        ).fetchone()
        assert row == (False, False, False)
