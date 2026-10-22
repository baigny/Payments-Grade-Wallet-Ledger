import pytest

from scripts.seed import seed

pytestmark = pytest.mark.database


def test_seed_is_balanced_audited_and_refuses_second_load(db):
    assert seed(db, 3, 6, 30) == {
        "users": 3,
        "wallets": 6,
        "transfers": 30,
        "ledger_entries": 60,
        "status_events": 60,
    }
    assert (
        db.execute("SELECT min(balance) FROM accounts WHERE account_type = 'wallet'").fetchone()[0]
        >= 0
    )
    with pytest.raises(ValueError, match="empty database"):
        seed(db, 3, 6, 30)
    assert db.execute("SELECT count(*) FROM transfers").fetchone()[0] == 30


def test_seed_rejects_invalid_shape_before_writes(db):
    with pytest.raises(ValueError):
        seed(db, 3, 2, 30)
    assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 0
