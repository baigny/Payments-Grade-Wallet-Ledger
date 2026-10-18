import pytest

from wallet.auth import token_hash

pytestmark = pytest.mark.database


def test_user_token_is_stored_only_as_hash(api):
    user = api.user()
    with api.owner() as conn:
        stored = conn.execute(
            "SELECT token_hash FROM auth_tokens WHERE user_id = %s", (user["id"],)
        )
        assert stored.fetchone()[0] == token_hash(user["token"])


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


def test_only_admin_can_create_users(api):
    user = api.user()
    response = api.request(
        "POST",
        "/users",
        token=user["token"],
        json={"name": "Other", "email": "other@example.test", "phone": "+919012345678"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("token", [None, "invalid"])
def test_authentication_is_required(api, token):
    response = api.request("GET", "/users/1/wallets", token=token)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_revoked_token_is_rejected(api):
    user = api.user()
    with api.owner() as conn:
        conn.execute("UPDATE auth_tokens SET revoked_at = now() WHERE user_id = %s", (user["id"],))
    assert (
        api.request("GET", f"/users/{user['id']}/wallets", token=user["token"]).status_code == 401
    )


@pytest.mark.parametrize("key", [None, "not-a-uuid"])
def test_posts_require_uuid_key(api, key):
    user = api.user()
    response = api.request(
        "POST", f"/users/{user['id']}/wallets", token=user["token"], key=key, json={"name": "Main"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_IDEMPOTENCY_KEY"


def test_validation_and_errors_share_one_shape(api):
    user = api.user()
    response = api.request(
        "POST", f"/users/{user['id']}/wallets", token=user["token"], json={"name": " "}
    )
    assert response.status_code == 422
    assert set(response.json()) == {"code", "message", "request_id"}
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_statement_pagination_is_bounded(api):
    user = api.user()
    wallet = api.wallet(user)
    for query in ("limit=101", "offset=-1", "limit=0", f"offset={2**63}"):
        response = api.request(
            "GET", f"/wallets/{wallet['id']}/statement?{query}", token=user["token"]
        )
        assert response.status_code == 422
