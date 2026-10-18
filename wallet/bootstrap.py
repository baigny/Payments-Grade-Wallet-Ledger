"""Create the first local administrator; print the generated token once."""

import argparse
import json
import secrets

from wallet import db
from wallet.auth import token_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Machine-readable one-time credentials")
    parser.add_argument(
        "--issue-token",
        action="store_true",
        help="Issue an additional token for an existing local administrator",
    )
    args = parser.parse_args()
    token = secrets.token_urlsafe(32)
    with db.connect(autocommit=True) as conn, conn.transaction():
        existing = conn.execute(
            "SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1"
        ).fetchone()
        if existing and not args.issue_token:
            raise SystemExit(
                "An administrator already exists; use --issue-token for a new credential."
            )
        if existing:
            user_id = existing[0]
        else:
            user_id = conn.execute(
                "INSERT INTO users (name, email, phone, role, kyc_status) "
                "VALUES ('Local admin', 'admin@wallet.local', '+919000000000', "
                "'admin', 'verified') "
                "RETURNING id"
            ).fetchone()[0]
        conn.execute(
            "INSERT INTO auth_tokens (user_id, token_hash) VALUES (%s, %s)",
            (user_id, token_hash(token)),
        )
    if args.json:
        print(json.dumps({"id": user_id, "token": token}))
    else:
        print(f"Admin ID: {user_id}\nBearer token (shown once): {token}")


if __name__ == "__main__":
    main()
