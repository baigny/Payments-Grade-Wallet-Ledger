import psycopg
import pytest

from wallet.migrate import migrate

pytestmark = pytest.mark.database


def test_applied_migrations_cannot_be_edited(db, tmp_path):
    (tmp_path / "0001_core.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Applied migration changed"), db.transaction():
        migrate(db, tmp_path)


def test_failed_migration_rolls_back_ddl_and_history(db, tmp_path):
    (tmp_path / "9999_broken.sql").write_text(
        "CREATE TABLE should_rollback (id BIGINT); SELECT 1 / 0;", encoding="utf-8"
    )
    with pytest.raises(psycopg.errors.DivisionByZero), db.transaction():
        migrate(db, tmp_path)
    assert db.execute("SELECT to_regclass('should_rollback')").fetchone()[0] is None
    assert (
        db.execute(
            "SELECT count(*) FROM schema_migrations WHERE name = '9999_broken.sql'"
        ).fetchone()[0]
        == 0
    )
