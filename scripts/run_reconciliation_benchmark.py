"""Run the million-entry benchmark in an isolated Docker database without host ports."""

import argparse
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries", type=int, default=1000000)
    args = parser.parse_args()
    config = json.loads(
        subprocess.check_output(
            ["docker", "compose", "config", "--format", "json"],
            cwd=ROOT,
            text=True,
        )
    )
    image = config["services"]["api"].get("image", f"{config['name']}-api")
    project = "wallet-benchmark-" + uuid4().hex[:12]
    compose = ["docker", "compose", "-p", project, "-f", str(ROOT / "compose.network.yml")]
    env = {**os.environ, "WALLET_TEST_IMAGE": image}

    def run(*command):
        result = subprocess.run(
            [*compose, *command], cwd=ROOT, env=env, text=True, capture_output=True
        )
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout

    try:
        print(f"Starting isolated benchmark: {args.entries:,} entries.", flush=True)
        run("up", "-d", "--wait", "db")
        evidence = json.loads(
            run(
                "run",
                "--rm",
                "--no-deps",
                "-e",
                "PGUSER=wallet_owner",
                "-e",
                "PGPASSWORD=network-owner-password",
                "api",
                "python",
                "scripts/reconciliation_benchmark.py",
                "--entries",
                str(args.entries),
            )
        )
    finally:
        run("down", "--volumes", "--remove-orphans")
    destination = ROOT / "docs/evidence/reconciliation-plans.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Reconciliation query plans",
        "",
        f"Dataset: {evidence['transfers']:,} transfers, {evidence['entries']:,} ledger entries.",
        "Fresh isolated PostgreSQL; no host ports; temporary services and data removed.",
        "Synthetic top-ups share one wallet. These are single-run query measurements,",
        "not production latency estimates or concurrent posting benchmarks.",
        "",
        "| Group | Query | Execution ms | Rows | Shared hits | Shared reads | Temp writes |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for group, plans in evidence["plans"].items():
        for name, plan in plans.items():
            node = plan["Plan"]
            lines.append(
                f"| {group} | {name} | {plan['Execution Time']:.3f} | "
                f"{node['Actual Rows']} | {node.get('Shared Hit Blocks', 0)} | "
                f"{node.get('Shared Read Blocks', 0)} | {node.get('Temp Written Blocks', 0)} |"
            )
    lines += [
        "",
        "All queries returned zero discrepancies. Statistics were collected before measurement.",
        "Full EXPLAIN ANALYZE/BUFFERS plans and PostgreSQL version are in",
        "[the JSON artifact](evidence/reconciliation-plans.json). The fixture bypasses",
        "triggers only while bulk-loading a new private schema; it then validates every",
        "invariant and comparison. This does not measure trigger or transfer throughput.",
        "",
        "Global sums and full nightly aggregates must read the ledger; sequential scans",
        "are reasonable. Transfer grouping and orphan checks can use ledger_entries(transfer_id);",
        "account grouping and missing-leg lookups can use the account/time index",
        "and the transfer index. The planner may still choose scans/hash joins for a full audit.",
        "Primary-key indexes support orphan joins. Reports use (run_id, id) for pagination.",
        "No new ledger index was added just for this plan: every index costs posting writes.",
        "",
        "Reproduce: `docker compose build api`, then",
        "`python scripts/run_reconciliation_benchmark.py --entries 1000000`.",
        "",
    ]
    (ROOT / "docs/perf.md").write_text("\n".join(lines), encoding="utf-8")
    print("Benchmark passed; plans saved; isolated database removed.")


if __name__ == "__main__":
    main()
