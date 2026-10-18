"""Create the first local administrator; print the generated token once."""

import secrets

from wallet import db
from wallet.auth import token_hash


def main():
    token = secrets.token_urlsafe(32)
    with db.connect(autocommit=True) as conn, conn.transaction():
        if conn.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone():
            raise SystemExit("An administrator already exists; no token was changed.")
        user_id = conn.execute(
            "INSERT INTO users (name, email, phone, role, kyc_status) "
            "VALUES ('Local admin', 'admin@wallet.local', '+919000000000', 'admin', 'verified') "
            "RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO auth_tokens (user_id, token_hash) VALUES (%s, %s)",
            (user_id, token_hash(token)),
        )
    print(f"Admin ID: {user_id}\nBearer token (shown once): {token}")


if __name__ == "__main__":
    main()
