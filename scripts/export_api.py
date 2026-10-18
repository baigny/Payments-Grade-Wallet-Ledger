"""Export OpenAPI and a Postman collection for the implemented routes."""

import argparse
import json
import re
from pathlib import Path

from wallet.main import app

ROOT = Path(__file__).resolve().parent.parent
METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
ORDER = {
    ("get", "/health/live"): 0,
    ("get", "/health/ready"): 1,
    ("post", "/users"): 2,
    ("post", "/users/{user_id}/wallets"): 3,
    ("get", "/users/{user_id}/wallets"): 4,
    ("get", "/wallets/{wallet_id}/balance"): 5,
    ("get", "/wallets/{wallet_id}/statement"): 6,
    ("post", "/transfers"): 7,
    ("post", "/transfers/{transfer_id}/reversal"): 8,
}


def postman_item(spec, path, method, operation):
    status = next(int(code) for code in operation["responses"] if code.startswith("2"))
    headers = [{"key": "Accept", "value": "application/json"}]
    request = {
        "method": method.upper(),
        "header": headers,
        "url": "{{base_url}}" + re.sub(r"\{(\w+)\}", r"{{\1}}", path),
        "description": operation.get("description", operation.get("summary", "")),
    }
    if operation.get("security"):
        token = "admin_token" if path in ("/users", "/transfers") else "user_token"
        request["auth"] = {
            "type": "bearer",
            "bearer": [{"key": "token", "value": "{{" + token + "}}"}],
        }
    if method == "post":
        headers.append({"key": "Idempotency-Key", "value": "{{$guid}}"})
    if "requestBody" in operation:
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        if "$ref" in schema:
            schema = spec["components"]["schemas"][schema["$ref"].split("/")[-1]]
        body = schema.get("example")
        if body is None:
            raise ValueError(f"Add an OpenAPI request example for {method.upper()} {path}")
        body = dict(body)
        if path == "/users":
            body.update(email="{{new_email}}", phone="{{new_phone}}")
        if path == "/transfers":
            body = {"type": "top_up", "destination_account_id": "__WALLET_ID__", "amount": 25000}
        raw = json.dumps(body, indent=2).replace('"__WALLET_ID__"', "{{wallet_id}}")
        headers.append({"key": "Content-Type", "value": "application/json"})
        request["body"] = {"mode": "raw", "raw": raw, "options": {"raw": {"language": "json"}}}
    checks = [
        f'pm.test("Expected status", () => pm.response.to.have.status({status}));',
        'pm.test("Request ID", () => pm.expect(pm.response.headers.get("X-Request-ID"))',
        '    .to.be.a("string").and.not.empty);',
    ]
    if path.startswith("/health/"):
        health = "ok" if path.endswith("/live") else "ready"
        checks.append(
            'pm.test("Health status", () => pm.expect(pm.response.json().status)'
            f'.to.eql("{health}"));'
        )
    if path == "/users" and method == "post":
        checks += [
            "if (pm.response.code === 201) {",
            '    pm.collectionVariables.set("user_id", pm.response.json().id);',
            '    pm.collectionVariables.set("user_token", pm.response.json().token);',
            "}",
        ]
    if path.endswith("/wallets") and method == "post":
        checks += [
            "if (pm.response.code === 201) {",
            '    pm.collectionVariables.set("wallet_id", pm.response.json().id);',
            "}",
        ]
    if path == "/transfers" and method == "post":
        checks += [
            "if (pm.response.code === 201) {",
            '    pm.collectionVariables.set("transfer_id", pm.response.json().id);',
            "}",
        ]
    events = [{"listen": "test", "script": {"type": "text/javascript", "exec": checks}}]
    if path == "/users" and method == "post":
        events.insert(
            0,
            {
                "listen": "prerequest",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        'pm.collectionVariables.set("new_email", '
                        'pm.variables.replaceIn("{{$guid}}")'
                        ' + "@example.test");',
                        'pm.collectionVariables.set("new_phone", '
                        '"+919" + Date.now().toString().slice(-9));',
                    ],
                },
            },
        )
    return {"name": operation.get("summary", path), "request": request, "event": events}


def artifacts():
    spec = app.openapi()
    operations = [
        (path, method, operation)
        for path, methods in spec["paths"].items()
        for method, operation in methods.items()
        if method in METHODS
    ]
    operations.sort(key=lambda item: (ORDER.get((item[1], item[0]), 99), item[0], item[1]))
    collection = {
        "info": {
            "name": "Wallet Ledger",
            "description": (
                "Bootstrap an admin and set admin_token before running. Requests create a user "
                "and wallet, then exercise implemented endpoints. Every POST uses a fresh UUID; "
                "replay protection is milestone 3. Returned tokens stay local."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "variable": [
            {"key": "base_url", "value": "http://localhost:8000", "type": "string"},
            *[
                {"key": key, "value": "", "type": "string"}
                for key in ("admin_token", "user_token", "user_id", "wallet_id", "transfer_id")
            ],
        ],
        "item": [postman_item(spec, *operation) for operation in operations],
    }
    return {
        ROOT / "docs/api/openapi.json": spec,
        ROOT / "postman/Wallet-Ledger.postman_collection.json": collection,
    }


def serialized(value):
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if checked-in exports are stale")
    args = parser.parse_args()
    stale = []
    for path, value in artifacts().items():
        content = serialized(value)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    if stale:
        parser.exit(1, "Stale API exports: " + ", ".join(stale) + "\n")


if __name__ == "__main__":
    main()
