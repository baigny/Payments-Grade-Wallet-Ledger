import pytest

pytestmark = pytest.mark.database


def test_user_has_multiple_empty_wallets(api):
    user = api.user()
    first = api.wallet(user)
    second = api.wallet(user, "Reserve")
    result = api.request("GET", f"/users/{user['id']}/wallets", token=user["token"])
    assert result.status_code == 200
    assert [row["id"] for row in result.json()["items"]] == [first["id"], second["id"]]
    assert first["balance"] == second["balance"] == 0


def test_balance_and_empty_statement(api):
    user = api.user()
    wallet = api.wallet(user)
    response = api.request("GET", f"/wallets/{wallet['id']}/balance", token=user["token"])
    assert response.json()["balance"] == 0
    statement = api.request("GET", f"/wallets/{wallet['id']}/statement", token=user["token"])
    assert statement.json()["items"] == []
    assert statement.json()["currency"] == "INR"


@pytest.mark.parametrize("endpoint", ["balance", "statement"])
def test_cannot_read_another_users_wallet(api, endpoint):
    owner = api.user()
    stranger = api.user()
    wallet = api.wallet(owner)
    response = api.request("GET", f"/wallets/{wallet['id']}/{endpoint}", token=stranger["token"])
    assert response.status_code == 404


def test_cannot_create_wallet_for_another_user(api):
    owner, stranger = api.user(), api.user()
    response = api.request(
        "POST", f"/users/{owner['id']}/wallets", token=stranger["token"], json={"name": "Other"}
    )
    assert response.status_code == 404


def test_statement_pagination_is_bounded(api):
    user = api.user()
    wallet = api.wallet(user)
    for query in ("limit=101", "offset=-1", "limit=0", f"offset={2**63}"):
        response = api.request(
            "GET", f"/wallets/{wallet['id']}/statement?{query}", token=user["token"]
        )
        assert response.status_code == 422
