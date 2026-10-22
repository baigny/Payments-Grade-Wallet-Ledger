import pytest

from wallet.core.auth import token_hash

pytestmark = pytest.mark.database


def test_user_token_is_stored_only_as_hash(api):
    user = api.user()
    with api.owner() as conn:
        stored = conn.execute(
            "SELECT token_hash FROM auth_tokens WHERE user_id = %s", (user["id"],)
        )
        assert stored.fetchone()[0] == token_hash(user["token"])


def test_only_admin_can_create_users(api):
    user = api.user()
    response = api.request(
        "POST",
        "/users",
        token=user["token"],
        json={"name": "Other", "email": "other@example.test", "phone": "+919012345678"},
    )
    assert response.status_code == 403
