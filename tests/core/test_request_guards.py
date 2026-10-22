import pytest

pytestmark = pytest.mark.database


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
