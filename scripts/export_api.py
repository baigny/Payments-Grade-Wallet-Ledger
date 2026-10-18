"""Export the current OpenAPI contract and matching Postman requests."""

import argparse
import json
from pathlib import Path

from wallet.main import app

ROOT = Path(__file__).resolve().parent.parent
METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def artifacts():
    spec = app.openapi()
    items = []
    for path, operations in sorted(spec["paths"].items()):
        for method, operation in sorted(operations.items()):
            if method not in METHODS:
                continue
            items.append(
                {
                    "name": operation.get("summary", path),
                    "request": {
                        "method": method.upper(),
                        "header": [{"key": "Accept", "value": "application/json"}],
                        "url": "{{base_url}}" + path,
                        "description": operation.get("description", operation.get("summary", "")),
                    },
                    "event": [
                        {
                            "listen": "test",
                            "script": {
                                "type": "text/javascript",
                                "exec": [
                                    'pm.test("Service is healthy", function () {',
                                    "    pm.response.to.have.status(200);",
                                    "});",
                                    'pm.test("Response includes request ID", function () {',
                                    '    pm.expect(pm.response.headers.get("X-Request-ID"))',
                                    '        .to.be.a("string").and.not.empty;',
                                    "});",
                                    'pm.test("Response matches health status", function () {',
                                    "    pm.expect(pm.response.json().status)",
                                    '        .to.eql("'
                                    + ("ok" if path.endswith("/live") else "ready")
                                    + '");',
                                    "});",
                                ],
                            },
                        }
                    ],
                }
            )
    collection = {
        "info": {
            "name": "Wallet Ledger",
            "description": "Milestone 1 health checks. Start Docker Compose before running.",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "variable": [{"key": "base_url", "value": "http://localhost:8000", "type": "string"}],
        "item": items,
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
