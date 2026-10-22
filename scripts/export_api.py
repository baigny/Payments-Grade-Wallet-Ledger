"""Export OpenAPI and a Postman collection for the implemented routes."""

import argparse
import copy
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
    ("post", "/admin/reconciliation/run"): 9,
    ("get", "/admin/reconciliation/runs/{run_id}"): 10,
    ("get", "/admin/reconciliation/runs/{run_id}/findings"): 11,
    ("get", "/admin/reconciliation/runs/{run_id}/problems"): 12,
    ("get", "/transfers/{transfer_id}"): 13,
    ("get", "/admin/risk-rules"): 14,
    ("post", "/admin/risk-rules/{rule_id}"): 15,
    ("get", "/admin/transfers/held"): 16,
    ("get", "/admin/transfers/{transfer_id}/risk-flags"): 17,
    ("post", "/admin/transfers/{transfer_id}/release"): 18,
    ("post", "/admin/transfers/{transfer_id}/reject"): 19,
    ("post", "/admin/risk-flags/explain"): 20,
    ("post", "/users/{user_id}/statement/ask"): 21,
    ("get", "/wallets/{wallet_id}/statement/summary"): 22,
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
    if path.endswith("/statement/summary"):
        request["url"] += "?from_date=2020-01-01&to_date=2030-12-31"
    if operation.get("security"):
        token = (
            "admin_token"
            if path in ("/users", "/transfers")
            or path.endswith("/reversal")
            or path.startswith("/admin/")
            else "user_token"
        )
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
    if path.endswith("/statement/summary"):
        checks += [
            "const s = pm.response.json();",
            'pm.test("Ledger summary balances", () => pm.expect(s.closing_balance)',
            "    .to.eql(s.opening_balance + s.credit_total - s.debit_total));",
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
    if path == "/admin/reconciliation/run":
        checks += [
            'pm.collectionVariables.set("run_id", pm.response.json().id);',
            'pm.test("Clean books", () => pm.expect(pm.response.json().status).to.eql("passed"));',
            'pm.test("No findings", () => pm.expect(pm.response.json().finding_count).to.eql(0));',
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
    items = [postman_item(spec, *operation) for operation in operations]
    if "/transfers" in spec["paths"]:
        items = transfer_demo(items)
    if "/admin/risk-rules" in spec["paths"]:
        items = fraud_demo(items)
    items = statement_pages(items)
    collection = {
        "info": {
            "name": "Wallet Ledger",
            "description": (
                "Bootstrap an admin and set admin_token before running. Requests create a user "
                "and wallet, then exercise payments, exact replay and changed-body rejection. "
                "Every POST needs a UUID key. Reuse a key only for an identical retry. "
                "Returned tokens stay local."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "variable": [
            {"key": "base_url", "value": "http://localhost:8000", "type": "string"},
            *[
                {"key": key, "value": "", "type": "string"}
                for key in (
                    "admin_token",
                    "user_token",
                    "user_id",
                    "wallet_id",
                    "transfer_id",
                    "recipient_user_id",
                    "recipient_token",
                    "recipient_wallet_id",
                )
            ],
        ],
        "item": items,
    }
    return {
        ROOT / "docs/api/openapi.json": spec,
        ROOT / "postman/Wallet-Ledger.postman_collection.json": collection,
    }


def statement_pages(items):
    template = next(
        item
        for item in items
        if item["request"]["url"] == "{{base_url}}/wallets/{{wallet_id}}/statement"
    )
    first = copy.deepcopy(template)
    first["name"] = "Read first statement cursor page"
    first["request"]["url"] += "?limit=1"
    first["event"][0]["script"]["exec"] += [
        'pm.test("Next cursor", () => pm.expect(pm.response.json().next_cursor)',
        '    .to.be.a("string"));',
        'pm.collectionVariables.set("statement_cursor", pm.response.json().next_cursor);',
        'pm.collectionVariables.set("first_entry", pm.response.json().items[0].id);',
    ]
    second = copy.deepcopy(template)
    second["name"] = "Read next statement cursor page"
    second["request"]["url"] += "?limit=1&cursor={{statement_cursor}}"
    second["event"][0]["script"]["exec"] += [
        'pm.test("Distinct next entry", () => pm.expect(pm.response.json().items[0].id)',
        '    .not.to.eql(Number(pm.collectionVariables.get("first_entry"))));',
    ]
    return [*items, first, second]


def transfer_demo(items):
    """Extend endpoint coverage into a runnable two-user payment scenario."""
    create_user = next(item for item in items if item["request"]["url"] == "{{base_url}}/users")
    create_wallet = next(
        item
        for item in items
        if item["request"]["method"] == "POST" and item["request"]["url"].endswith("/wallets")
    )
    top_up = next(item for item in items if item["request"]["url"] == "{{base_url}}/transfers")

    def renamed(item, replacements):
        text = json.dumps(item)
        for old, new in replacements.items():
            text = text.replace(old, new)
        return json.loads(text)

    recipient = renamed(
        create_user, {"user_id": "recipient_user_id", "user_token": "recipient_token"}
    )
    recipient["name"] = "Create recipient user"
    recipient_wallet = renamed(
        create_wallet,
        {
            "user_id": "recipient_user_id",
            "user_token": "recipient_token",
            "wallet_id": "recipient_wallet_id",
        },
    )
    recipient_wallet["name"] = "Create recipient wallet"
    variations = []
    for kind, amount, actor, saved_id in (
        ("peer", 1000, "user_token", "transfer_id"),
        ("withdrawal", 750, "user_token", "withdrawal_id"),
        ("fee", 100, "admin_token", "fee_id"),
    ):
        item = copy.deepcopy(top_up)
        item["name"] = f"Post {kind}"
        item["request"]["auth"]["bearer"][0]["value"] = "{{" + actor + "}}"
        body = {"type": kind, "source_account_id": "__SOURCE__", "amount": amount}
        if kind == "peer":
            body["destination_account_id"] = "__DESTINATION__"
        item["request"]["body"]["raw"] = (
            json.dumps(body, indent=2)
            .replace('"__SOURCE__"', "{{wallet_id}}")
            .replace('"__DESTINATION__"', "{{recipient_wallet_id}}")
        )
        if saved_id != "transfer_id":
            item = renamed(item, {"transfer_id": saved_id})
        variations.append(item)
        if kind == "peer":
            next(h for h in item["request"]["header"] if h["key"] == "Idempotency-Key")["value"] = (
                "{{peer_key}}"
            )
            item["event"].insert(
                0,
                {
                    "listen": "prerequest",
                    "script": {
                        "type": "text/javascript",
                        "exec": [
                            'pm.collectionVariables.set("peer_key", '
                            'pm.variables.replaceIn("{{$guid}}"));'
                        ],
                    },
                },
            )
            item["event"][-1]["script"]["exec"].append(
                'pm.collectionVariables.set("peer_response", pm.response.text());'
            )
            replay = copy.deepcopy(item)
            replay["name"] = "Replay peer payment"
            replay["event"] = [
                {
                    "listen": "test",
                    "script": {
                        "type": "text/javascript",
                        "exec": [
                            'pm.test("Saved status", () => pm.response.to.have.status(201));',
                            'pm.test("Exact saved body", () => pm.expect(pm.response.text())'
                            '.to.eql(pm.collectionVariables.get("peer_response")));',
                            'pm.test("Replay header", () => pm.expect('
                            'pm.response.headers.get("Idempotent-Replay")).to.eql("true"));',
                        ],
                    },
                }
            ]
            changed = copy.deepcopy(replay)
            changed["name"] = "Reject changed amount with the same key"
            changed["request"]["body"]["raw"] = changed["request"]["body"]["raw"].replace(
                '"amount": 1000', '"amount": 2000'
            )
            changed["event"][0]["script"]["exec"] = [
                'pm.test("Conflict status", () => pm.response.to.have.status(422));',
                'pm.test("Key reuse rejected", () => pm.expect(pm.response.json().code)'
                '.to.eql("IDEMPOTENCY_KEY_REUSED"));',
            ]
            variations.extend([replay, changed])
    index = items.index(top_up)
    final_balance = copy.deepcopy(
        next(item for item in items if item["request"]["url"].endswith("/balance"))
    )
    final_balance["name"] = "Verify final sender balance"
    final_balance["event"][0]["script"]["exec"].append(
        'pm.test("Final balance", () => pm.expect(pm.response.json().balance).to.eql(24150));'
    )
    return (
        items[:index]
        + [recipient, recipient_wallet, top_up]
        + variations
        + items[index + 1 :]
        + [final_balance]
    )


def serialized(value):
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def fraud_demo(items):
    templates = {item["request"]["url"].removeprefix("{{base_url}}"): item for item in items}
    result = [
        item
        for item in items
        if not item["request"]["url"].startswith(
            (
                "{{base_url}}/admin/risk-rules",
                "{{base_url}}/admin/transfers/",
                "{{base_url}}/admin/risk-flags/explain",
            )
        )
    ]
    rules = copy.deepcopy(templates["/admin/risk-rules"])
    rules["event"][0]["script"]["exec"] += [
        'const rule = pm.response.json().items.find(r => r.rule_type === "amount");',
        'pm.collectionVariables.set("rule_id", rule.id);',
        'pm.collectionVariables.set("original_rule", JSON.stringify({threshold: rule.threshold,',
        " window_seconds: rule.window_seconds, action: rule.action, enabled: rule.enabled}));",
    ]
    update = copy.deepcopy(templates["/admin/risk-rules/{{rule_id}}"])
    update["request"]["body"]["raw"] = json.dumps(
        {
            "threshold": 500,
            "window_seconds": None,
            "action": "hold",
            "enabled": True,
        }
    )
    create = copy.deepcopy(next(item for item in items if item["name"] == "Post peer"))
    create["name"] = "Create held peer payment"
    create["request"]["header"] = [
        h if h["key"] != "Idempotency-Key" else {"key": "Idempotency-Key", "value": "{{$guid}}"}
        for h in create["request"]["header"]
    ]
    create["event"] = [copy.deepcopy(create["event"][-1])]
    create["event"][0]["script"]["exec"].append(
        'pm.test("Held without posting", () => '
        'pm.expect(pm.response.json().status).to.eql("held"));'
    )
    release = copy.deepcopy(templates["/admin/transfers/{{transfer_id}}/release"])
    reject = copy.deepcopy(templates["/admin/transfers/{{transfer_id}}/reject"])
    restore = copy.deepcopy(update)
    restore["name"] = "Restore amount rule"
    restore["request"]["body"]["raw"] = "{{original_rule}}"
    result += [
        rules,
        update,
        create,
        templates["/admin/transfers/held"],
        templates["/admin/transfers/{{transfer_id}}/risk-flags"],
        templates["/admin/risk-flags/explain"],
        release,
        copy.deepcopy(create),
        reject,
        restore,
    ]
    return result


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
