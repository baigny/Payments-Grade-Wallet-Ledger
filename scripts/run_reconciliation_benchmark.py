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
    parser.add_argument("--fraud", action="store_true", help="Measure per-rule posting overhead")
    parser.add_argument("--statement", action="store_true", help="Compare statement pagination")
    args = parser.parse_args()
    if args.statement and (args.fraud or args.entries < 200102):
        parser.error("Statement comparison requires >= 200102 entries and excludes --fraud")
    compose = (
        [os.environ["WALLET_COMPOSE_BIN"]]
        if os.getenv("WALLET_COMPOSE_BIN")
        else ["docker", "compose"]
    )
    config = json.loads(
        subprocess.check_output(
            [*compose, "config", "--format", "json"],
            cwd=ROOT,
            text=True,
        )
    )
    image = config["services"]["api"].get("image", f"{config['name']}-api")
    project = "wallet-benchmark-" + uuid4().hex[:12]
    compose += ["-p", project, "-f", str(ROOT / "compose.network.yml")]
    env = {**os.environ, "WALLET_TEST_IMAGE": image}

    def run(*command):
        result = subprocess.run(
            [*compose, *command], cwd=ROOT, env=env, text=True, capture_output=True
        )
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout

    try:
        print(
            "Starting isolated fraud benchmark."
            if args.fraud
            else f"Starting isolated benchmark: {args.entries:,} entries.",
            flush=True,
        )
        run("up", "-d", "--wait", "db")
        command = (
            ["-m", "scripts.fraud_benchmark"]
            if args.fraud
            else ["-m", "scripts.reconciliation_benchmark", "--entries", str(args.entries)]
        )
        if args.statement:
            command.append("--statement")
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
                *command,
            )
        )
    finally:
        run("down", "--volumes", "--remove-orphans")
    if args.statement:
        destination = ROOT / "docs/evidence/statement-plans.json"
        destination.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        lines = [
            "# Statement pagination plans",
            "",
            f"Dataset: {evidence['entries']:,} ledger entries; "
            f"{evidence['wallet_entries']:,} entries in the measured wallet.",
            "Isolated PostgreSQL, actual statement join/projection, LIMIT 51 (50 plus lookahead).",
            "Both queries returned exactly the same 51 entry IDs and values.",
            "",
            "| Query | Execution ms | Shared hits | Shared reads |",
            "|---|---:|---:|---:|",
        ]
        for name, plan in evidence["plans"].items():
            node = plan["Plan"]
            lines.append(
                f"| {name} | {plan['Execution Time']:.3f} | "
                f"{node.get('Shared Hit Blocks', 0)} | {node.get('Shared Read Blocks', 0)} |"
            )
        lines += [
            "",
            "OFFSET must visit and discard 100,000 rows before returning the page.",
            "Seek pagination uses the existing (account_id, created_at DESC, id DESC) index",
            "and a strict timestamp/id boundary to start near the next page. No new ledger",
            "index or posting write cost is introduced. The id breaks timestamp ties.",
            "",
            "The cursor boundary is taken from the preceding page. Its OFFSET-based lookup",
            "here only constructs an equivalent benchmark position; it is not part of a",
            "normal cursor request. Queries run sequentially with warm-cache effects; these",
            "are single-machine plan measurements, not production latency guarantees.",
            "",
            "Synthetic balanced top-ups are bulk-loaded only into a disposable schema with",
            "triggers temporarily bypassed, as in the reconciliation benchmark. This is a",
            "read-plan fixture, not an API or payment-throughput benchmark.",
            "",
            "Cursors are wallet-bound seek positions, not authentication or database snapshots.",
            "Newer postings do not shift subsequent pages. A late-committing, older-timestamp",
            "entry can still appear later; pagination retains no transaction across requests.",
            "Authorization is rechecked on every page. Invalid positions return 422.",
            "",
            "Full plans: [JSON](evidence/statement-plans.json). Reproduce after building the API:",
            "`python scripts/run_reconciliation_benchmark.py --statement --entries 1000000`.",
            "",
        ]
        (ROOT / "docs/statement-perf.md").write_text("\n".join(lines), encoding="utf-8")
        print("Statement plans verified and saved; isolated database removed.")
        return
    if args.fraud:
        destination = ROOT / "docs/evidence/fraud-overhead.json"
        destination.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        lines = [
            "# Fraud rule overhead",
            "",
            "Isolated PostgreSQL, 10,000 prior outgoing payments, 30 measured commits per case",
            "after three warm-ups. Rules are enabled with non-triggering thresholds.",
            "Baseline bypasses fraud SQL. Cases run sequentially on one growing fixture.",
            "These single-machine samples exclude HTTP, authentication and idempotency.",
            "",
            "| Case | Mean ms | Median ms | P95 ms | Mean minus baseline ms |",
            "|---|---:|---:|---:|---:|",
        ]
        baseline = evidence["results"]["baseline"]["mean_ms"]
        for name, values in evidence["results"].items():
            lines.append(
                f"| {name} | {values['mean_ms']:.3f} | {values['median_ms']:.3f} | "
                f"{values['p95_ms']:.3f} | {values['mean_ms'] - baseline:.3f} |"
            )
        lines += [
            "",
            "Differences can include cache and timing noise; this is not a production SLA.",
            "The bulk fixture bypasses triggers only in its disposable schema; measured",
            "transfers use the real transaction, ledger/audit triggers and commit checks.",
            "Velocity and rolling totals use the partial source/time index, including amount.",
            "Full evidence: [JSON](evidence/fraud-overhead.json). Reproduce with",
            "`python scripts/run_reconciliation_benchmark.py --fraud`.",
            "",
        ]
        (ROOT / "docs/fraud-perf.md").write_text("\n".join(lines), encoding="utf-8")
        print("Fraud benchmark passed; isolated database removed.")
        return
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
