def payment(source, destination, amount=1000):
    return {
        "type": "peer",
        "source_account_id": source["id"],
        "destination_account_id": destination["id"],
        "amount": amount,
    }


def post(api, token, key, body, path="/transfers"):
    return api.request("POST", path, token=token, key=str(key), json=body)


def assert_replayed(first, repeated):
    assert repeated.status_code == first.status_code
    assert repeated.content == first.content
    assert repeated.headers["Idempotent-Replay"] == "true"
    assert repeated.headers["X-Request-ID"] != first.headers["X-Request-ID"]
