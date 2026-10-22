from uuid import uuid4


def ask(api, user, question, key=None):
    return api.request(
        "POST",
        f"/users/{user['id']}/statement/ask",
        token=user["token"],
        key=str(key or uuid4()),
        json={"question": question},
    )
