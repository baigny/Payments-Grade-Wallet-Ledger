"""Verify concurrent spending over HTTP; a small smoke test unless --stress is selected."""

import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import UTC, datetime
from uuid import uuid4

import httpx


async def run(stress=False):
    rounds, requests = (20, 100) if stress else (1, 20)
    expected = requests // 2
    funding = expected * 1000
    admin_token = os.environ["WALLET_ADMIN_TOKEN"]
    base_url = os.getenv("API_BASE_URL", "http://localhost:8000")
    limits = httpx.Limits(max_connections=requests, max_keepalive_connections=0)
    async with httpx.AsyncClient(base_url=base_url, limits=limits, timeout=30) as client:

        async def call(method, path, token, body=None, key=None):
            headers = {"Authorization": f"Bearer {token}"}
            if method == "POST":
                headers["Idempotency-Key"] = str(key or uuid4())
            return await client.request(method, path, headers=headers, json=body)

        async def user(name):
            response = await call(
                "POST",
                "/users",
                admin_token,
                {
                    "name": name,
                    "email": f"{uuid4().hex}@example.test",
                    "phone": "+91" + str(uuid4().int % 10**10).zfill(10),
                },
            )
            assert response.status_code == 201, "Network test user creation failed"
            return response.json()

        sender, recipient = await user("Rhea"), await user("Kabir")
        evidence = []
        for number in range(1, rounds + 1):
            wallets = []
            for actor in (sender, recipient):
                response = await call(
                    "POST",
                    f"/users/{actor['id']}/wallets",
                    actor["token"],
                    {"name": f"Network round {number}"},
                )
                assert response.status_code == 201, "Network test wallet creation failed"
                wallets.append(response.json()["id"])
            source, destination = wallets
            funded = await call(
                "POST",
                "/transfers",
                admin_token,
                {
                    "type": "top_up",
                    "destination_account_id": source,
                    "amount": funding,
                },
            )
            assert funded.status_code == 201, "Network test funding failed"
            keys = [uuid4() for _ in range(requests)]
            peer_body = {
                "type": "peer",
                "source_account_id": source,
                "destination_account_id": destination,
                "amount": 1000,
            }
            responses = await asyncio.gather(
                *[
                    call(
                        "POST",
                        "/transfers",
                        sender["token"],
                        peer_body,
                        key,
                    )
                    for key in keys
                ]
            )
            posted = [r.json()["id"] for r in responses if r.status_code == 201]
            rejected = [r for r in responses if r.status_code == 409]
            status_counts = dict(Counter(r.status_code for r in responses))
            assert len(posted) == len(set(posted)) == expected, (
                f"Round {number}: expected {expected} postings, got statuses {status_counts}"
            )
            assert len(rejected) == expected, f"Round {number}: expected {expected} rejections"
            assert all(r.json()["code"] == "INSUFFICIENT_FUNDS" for r in rejected)

            original, key = next(
                (response, key)
                for response, key in zip(responses, keys, strict=True)
                if response.status_code == 201
            )
            retries = await asyncio.gather(
                *[call("POST", "/transfers", sender["token"], peer_body, key) for _ in range(10)]
            )
            assert all(
                r.status_code == original.status_code
                and r.content == original.content
                and r.headers["Idempotent-Replay"] == "true"
                for r in retries
            ), "Parallel network retries must return the exact saved success"
            changed = await call(
                "POST", "/transfers", sender["token"], {**peer_body, "amount": 2000}, key
            )
            assert changed.status_code == 422
            assert changed.json()["code"] == "IDEMPOTENCY_KEY_REUSED"

            entries, balances = [], []
            for actor, wallet_id in ((sender, source), (recipient, destination)):
                balance = await call("GET", f"/wallets/{wallet_id}/balance", actor["token"])
                statement = await call(
                    "GET", f"/wallets/{wallet_id}/statement?limit=100", actor["token"]
                )
                assert balance.status_code == statement.status_code == 200
                lines = statement.json()["items"]
                cached = balance.json()["balance"]
                computed = sum(
                    line["amount"] if line["direction"] == "credit" else -line["amount"]
                    for line in lines
                )
                assert cached == computed and cached >= 0
                balances.append(cached)
                entries.extend(line for line in lines if line["transfer_id"] in posted)
            assert balances == [0, funding]
            for transfer_id in posted:
                lines = [line for line in entries if line["transfer_id"] == transfer_id]
                assert sorted((line["direction"], line["amount"]) for line in lines) == [
                    ("credit", 1000),
                    ("debit", 1000),
                ]
            evidence.append(
                {
                    "round": number,
                    "requests": requests,
                    "posted": expected,
                    "insufficient_funds": expected,
                    "sender_balance": 0,
                    "recipient_balance": funding,
                    "peer_debits": funding,
                    "peer_credits": funding,
                    "cache_matches_ledger": True,
                    "replay_requests": 10,
                    "identical_saved_responses": True,
                    "changed_request_status": 422,
                }
            )
        return {
            "mode": "stress" if stress else "smoke",
            "transport": "HTTP over Docker network to Uvicorn",
            "rounds": evidence,
            "completed_at": datetime.now(UTC).isoformat(),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stress", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.stress)), indent=2))


if __name__ == "__main__":
    main()
