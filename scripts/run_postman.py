"""Run the Postman demo against local Compose services without logging credentials."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def main():
    npx = shutil.which("npx.cmd" if os.name == "nt" else "npx")
    if not npx:
        raise SystemExit("Node.js and npx are required to run Newman.")
    token = os.getenv("WALLET_ADMIN_TOKEN")
    if not token:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "run",
                "--rm",
                "api",
                "python",
                "-m",
                "wallet.bootstrap",
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise SystemExit(
                "Set WALLET_ADMIN_TOKEN for an existing local admin, "
                "or run against a fresh local database."
            )
        token = json.loads(result.stdout)["token"]
    with tempfile.TemporaryDirectory(prefix="wallet-postman-") as directory:
        environment = Path(directory) / "environment.json"
        environment.write_text(
            json.dumps(
                {
                    "name": "Smoke test",
                    "values": [{"key": "admin_token", "value": token, "enabled": True}],
                }
            ),
            encoding="utf-8",
        )
        environment.chmod(0o600)
        subprocess.run(
            [
                npx,
                "--yes",
                "newman@6.2.1",
                "run",
                "postman/Wallet-Ledger.postman_collection.json",
                "--environment",
                str(environment),
                "--bail",
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
