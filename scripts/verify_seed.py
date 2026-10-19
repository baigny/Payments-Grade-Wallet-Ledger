"""Verify the full submission seed in a disposable schema, leaving application data untouched."""

import json
from uuid import uuid4

from psycopg import sql

from scripts.seed import seed
from wallet.db import connect
from wallet.migrate import migrate


def main():
    name = "seed_check_" + uuid4().hex
    with connect(autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        try:
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(name)))
            migrate(conn)
            print(json.dumps(seed(conn), indent=2), flush=True)
        finally:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


if __name__ == "__main__":
    main()
