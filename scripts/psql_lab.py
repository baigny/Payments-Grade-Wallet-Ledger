"""Capture two real psql processes, including a Repeatable Read/Serializable write-skew lab."""

import argparse
import subprocess
from pathlib import Path
from uuid import uuid4


class Session:
    def __init__(self):
        self.process = subprocess.Popen(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "db",
                "sh",
                "-c",
                # Merge inside the container so errors precede the command's echo marker.
                "exec psql -X -A -t -P pager=off -v VERBOSITY=verbose "
                '-U "$POSTGRES_USER" -d "$POSTGRES_DB" 2>&1',
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

    def execute(self, query):
        marker = "end_" + uuid4().hex
        self.process.stdin.write(query + ";\n\\echo " + marker + "\n")
        self.process.stdin.flush()
        lines = []
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError("psql exited before completing a command: " + "".join(lines))
            if line.strip() == marker:
                return "".join(lines).strip()
            lines.append(line)

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.write("\\q\n")
            self.process.stdin.flush()
            self.process.communicate(timeout=10)


def run_lab():
    sessions = {"A": Session(), "B": Session()}
    schema = "psql_lab_" + uuid4().hex
    transcript = [
        """# Two-session psql isolation lab

This is captured output from two persistent `psql` processes connected to
PostgreSQL through Docker Compose. Commands were sent in the displayed order;
the outputs are from psql, not reconstructed expected results. Reproduce with:

```sh
python scripts/psql_lab.py --output docs/isolation.md
```

For manual practice, open two terminals with
`docker compose exec db psql -U wallet_owner -d wallet`. Run each labeled command
in its matching terminal. The generated scratch schema below keeps the lab
separate from application tables. Set both sessions' search_path as shown.
The shared-reserve rule in the write-skew section is a teaching example, not a
new wallet requirement. All lab amounts are integer paise.
"""
    ]

    def step(label, query, allow_serialization=False):
        result = sessions[label].execute(query)
        transcript.append(f"Session {label}\n\n```sql\n{query};\n```\n\n```text\n{result}\n```\n")
        if "ERROR:" in result and not (allow_serialization and "40001" in result):
            raise AssertionError(result)
        return result

    def reset():
        step("B", "UPDATE lab_accounts SET balance = 1000")
        step("B", "UPDATE lab_users SET name = 'Meera' WHERE id = 1")

    try:
        step("A", f"CREATE SCHEMA {schema}")
        for label in sessions:
            step(label, f"SET search_path TO {schema}")
        step("A", "CREATE TABLE lab_users (id BIGINT PRIMARY KEY, name TEXT NOT NULL)")
        step(
            "A", "CREATE TABLE lab_accounts (id BIGINT PRIMARY KEY, user_id BIGINT, balance BIGINT)"
        )
        step("A", "INSERT INTO lab_users VALUES (1, 'Meera')")
        step("A", "INSERT INTO lab_accounts VALUES (1, 1, 1000), (2, 1, 1000)")

        for isolation, expected in (("READ COMMITTED", "2000"), ("REPEATABLE READ", "1000")):
            transcript.append(f"## {isolation.title()}\n")
            reset()
            step("A", f"BEGIN ISOLATION LEVEL {isolation}")
            assert step("A", "SELECT balance FROM lab_accounts WHERE id = 1") == "1000"
            step("B", "UPDATE lab_accounts SET balance = 2000 WHERE id = 1")
            assert step("A", "SELECT balance FROM lab_accounts WHERE id = 1") == expected
            step("A", "COMMIT")

        transcript.append("## Repeatable Read with a join\n")
        reset()
        join = (
            "SELECT u.name, a.balance FROM lab_users u JOIN lab_accounts a ON a.user_id = u.id "
            "WHERE a.id = 1"
        )
        step("A", "BEGIN ISOLATION LEVEL REPEATABLE READ")
        assert step("A", join) == "Meera|1000"
        step("B", "BEGIN")
        step("B", "UPDATE lab_accounts SET balance = 2000 WHERE id = 1")
        step("B", "UPDATE lab_users SET name = 'Meera updated' WHERE id = 1")
        step("B", "COMMIT")
        assert step("A", join) == "Meera|1000"
        step("A", "COMMIT")

        transcript.append("## Serializable: competing writes to the same row\n")
        reset()
        for label in sessions:
            step(label, "BEGIN ISOLATION LEVEL SERIALIZABLE")
            assert step(label, "SELECT balance FROM lab_accounts WHERE id = 1") == "1000"
        step("A", "UPDATE lab_accounts SET balance = 900 WHERE id = 1")
        step("A", "COMMIT")
        assert "40001" in step("B", "UPDATE lab_accounts SET balance = 900 WHERE id = 1", True)
        step("B", "ROLLBACK")

        transcript.append("""## Stronger example: write skew across different rows

Both sessions see 2,000 paise combined. Each decides it can spend 1,000 from its
own row while retaining a combined reserve of at least 1,000. They update different
rows, so this example distinguishes snapshot isolation from serializable execution.
""")
        for isolation, expected in (("REPEATABLE READ", "0"), ("SERIALIZABLE", "1000")):
            transcript.append(f"### {isolation.title()} shared-reserve experiment\n")
            reset()
            for label in sessions:
                step(label, f"BEGIN ISOLATION LEVEL {isolation}")
                assert step(label, "SELECT sum(balance) FROM lab_accounts") == "2000"
            step("A", "UPDATE lab_accounts SET balance = 0 WHERE id = 1")
            outcomes = [step("B", "UPDATE lab_accounts SET balance = 0 WHERE id = 2", True)]
            outcomes.append(step("A", "COMMIT", True))
            outcomes.append(step("B", "COMMIT", True))
            if isolation == "SERIALIZABLE":
                assert sum("40001" in output for output in outcomes) == 1, repr(outcomes)
            else:
                assert not any("40001" in output for output in outcomes)
            assert step("A", "SELECT sum(balance) FROM lab_accounts") == expected
        transcript.append("""Repeatable Read allowed both different-row writes and broke the shared
reserve rule. Serializable rejected one transaction with SQLSTATE 40001 and
preserved the reserve. A real Serializable service must retry the entire failed
transaction, re-read the aggregate, and re-evaluate its decision. The production
two-account posting service uses ordered row locks at Read Committed; it does not
apply this hypothetical aggregate policy.
""")
    finally:
        for session in sessions.values():
            session.execute("ROLLBACK")
        step("A", f"DROP SCHEMA {schema} CASCADE")
        for session in sessions.values():
            session.close()
    return "\n".join(transcript)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    document = run_lab()
    if args.output:
        args.output.write_text(document, encoding="utf-8", newline="\n")
    else:
        print(document)


if __name__ == "__main__":
    main()
