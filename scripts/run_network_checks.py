"""Run isolated HTTP smoke verification; --stress opts in to 20 x 100 requests."""

import argparse
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stress", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    compose = (
        [os.environ["WALLET_COMPOSE_BIN"]]
        if os.getenv("WALLET_COMPOSE_BIN")
        else ["docker", "compose"]
    )
    config = json.loads(
        subprocess.run(
            [*compose, "config", "--format", "json"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    image_name = config["services"]["api"].get("image", f"{config['name']}-api")
    inspected = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image_name],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    image = inspected.stdout.strip()
    if inspected.returncode or not image:
        parser.error("Build the current API image first: docker compose build api")
    project = "wallet-network-" + uuid4().hex[:12]
    env = {**os.environ, "WALLET_TEST_IMAGE": image}
    compose += ["-p", project, "-f", str(ROOT / "compose.network.yml")]

    def run(*command, extra_env=None):
        result = subprocess.run(
            [*compose, *command],
            cwd=ROOT,
            env={**env, **(extra_env or {})},
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise RuntimeError("Network verification failed:\n" + result.stderr)
        return result.stdout

    try:
        print(f"Starting isolated {project} ({'stress' if args.stress else 'smoke'}).", flush=True)
        run("up", "-d", "--wait", "db", "migrate", "api")
        credential = json.loads(
            run("run", "--no-deps", "--rm", "migrate", "python", "-m", "wallet.bootstrap", "--json")
        )
        command = ["python", "scripts/network_stampede.py"]
        if args.stress:
            command.append("--stress")
        evidence = json.loads(
            run(
                "run",
                "--no-deps",
                "--rm",
                "-e",
                "WALLET_ADMIN_TOKEN",
                "client",
                *command,
                extra_env={"WALLET_ADMIN_TOKEN": credential["token"]},
            )
        )
        evidence["isolated"] = True
    finally:
        # Only this randomly named project and its temporary database are removed.
        run("down", "--volumes", "--remove-orphans")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(
        f"Network {evidence['mode']}: {len(evidence['rounds'])} round(s) passed; "
        f"{evidence['rounds'][0]['requests']} requests per round; balances and ledger matched. "
        "Temporary services and data removed."
    )


if __name__ == "__main__":
    main()
