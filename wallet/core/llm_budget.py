"""Conservative rolling quota reservations shared across API and worker processes."""

import hashlib
import math
import os
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import psycopg
from psycopg.rows import dict_row

from wallet.core import db
from wallet.core.errors import DomainError


def unavailable(seconds):
    return DomainError(
        "LLM_BUSY",
        "Language provider is busy; retry later with the same idempotency key",
        503,
        cacheable=False,
        retry_after=max(1, min(86400, math.ceil(seconds))),
    )


def limits():
    try:
        values = tuple(
            int(os.getenv(name, default))
            for name, default in (
                ("LLM_REQUESTS_PER_MINUTE", "30"),
                ("LLM_REQUESTS_PER_DAY", "1000"),
                ("LLM_TOKENS_PER_MINUTE", "8000"),
                ("LLM_TOKENS_PER_DAY", "200000"),
            )
        )
        if min(values) < 1:
            raise ValueError("Limits must be positive")
        return values
    except ValueError as exc:
        raise DomainError(
            "LLM_INVALID_CONFIGURATION", "Language provider limits are invalid", 503
        ) from exc


def reserve(base_url, model, tokens):
    rpm, rpd, tpm, tpd = limits()
    # Do not store or fingerprint the secret. Keys sharing endpoint/model share a budget.
    scope = hashlib.sha256((base_url + "\n" + model).encode()).hexdigest()
    try:
        with db.connect(autocommit=True, row_factory=dict_row) as conn, conn.transaction():
            conn.execute("SET LOCAL lock_timeout = '250ms'")
            conn.execute("SET LOCAL statement_timeout = '2s'")
            conn.execute(
                "INSERT INTO llm_budgets (scope) VALUES (%s) ON CONFLICT DO NOTHING", (scope,)
            )
            budget = conn.execute(
                "SELECT id, blocked_until FROM llm_budgets WHERE scope = %s FOR UPDATE", (scope,)
            ).fetchone()
            now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if budget["blocked_until"] > now:
                raise unavailable((budget["blocked_until"] - now).total_seconds())
            usage = conn.execute(
                "SELECT count(*) FILTER "
                "(WHERE created_at > %s - interval '1 minute') minute_count, "
                "count(*) day_count, coalesce(sum(tokens) FILTER "
                "(WHERE created_at > %s - interval '1 minute'), 0) minute_tokens, "
                "coalesce(sum(tokens), 0) day_tokens, min(created_at) oldest "
                "FROM llm_requests WHERE budget_id = %s AND created_at > %s - interval '1 day'",
                (now, now, budget["id"], now),
            ).fetchone()
            if usage["day_count"] >= rpd or usage["day_tokens"] + tokens > tpd:
                delay = (
                    86400
                    if usage["oldest"] is None
                    else (usage["oldest"] - now).total_seconds() + 86400
                )
                raise unavailable(delay)
            if usage["minute_count"] >= rpm or usage["minute_tokens"] + tokens > tpm:
                # A conservative delay; no sleeping while the caller has a SQL transaction.
                raise unavailable(60)
            ticket = conn.execute(
                "INSERT INTO llm_requests (budget_id, created_at, tokens) "
                "VALUES (%s, %s, %s) RETURNING id",
                (budget["id"], now, tokens),
            ).fetchone()["id"]
            # Prune only this budget's expired reservations while holding its short row lock.
            conn.execute(
                "DELETE FROM llm_requests WHERE budget_id = %s "
                "AND created_at <= %s - interval '1 day'",
                (budget["id"], now),
            )
            return ticket, budget["id"]
    except psycopg.errors.LockNotAvailable as exc:
        raise unavailable(1) from exc


def complete(ticket, used):
    # Use reported actual usage only when credible; unknown/error usage keeps the full reserve.
    if type(used) is not int or not 1 <= used <= 1_000_000_000:
        return
    with db.connect(autocommit=True) as conn:
        conn.execute("UPDATE llm_requests SET tokens = %s WHERE id = %s", (used, ticket[0]))


def retry_seconds(value):
    try:
        delay = float(value)
        if not math.isfinite(delay):
            return 60
    except (TypeError, ValueError):
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 60
    return max(1, min(86400, math.ceil(delay)))


def block(ticket, retry_after):
    seconds = retry_seconds(retry_after)
    with db.connect(autocommit=True) as conn:
        conn.execute(
            "UPDATE llm_budgets SET blocked_until = greatest(blocked_until, "
            "clock_timestamp() + %s * interval '1 second') WHERE id = %s",
            (seconds, ticket[1]),
        )
    return unavailable(seconds)
