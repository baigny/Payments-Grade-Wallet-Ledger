import pytest

from scripts.isolation_lab import run_lab

pytestmark = pytest.mark.database


def test_two_session_isolation_experiments(schema_name):
    _, results = run_lab()
    assert results["READ COMMITTED first"] == "[(1000,)]"
    assert results["READ COMMITTED second"] == "[(2000,)]"
    assert results["REPEATABLE READ first"] == results["REPEATABLE READ second"] == "[(1000,)]"
    assert results["join first"] == results["join second"] == "[('Meera', 1000)]"
    assert results["serialization_sqlstate"] == "40001"
