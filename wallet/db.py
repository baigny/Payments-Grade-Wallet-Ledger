import os

import psycopg


def connect(**kwargs):
    """Use DATABASE_URL when supplied; otherwise use standard libpq PG* variables."""
    return psycopg.connect(os.getenv("DATABASE_URL", ""), **kwargs)
