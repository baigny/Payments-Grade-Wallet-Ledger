"""Run reconciliation once per UTC schedule date, with graceful shutdown."""

import logging
import os
import signal
import threading
from datetime import UTC, datetime, timedelta

from wallet.core.errors import DomainError
from wallet.fraud import explanations
from wallet.reconciliation import service as reconciliation


def settings():
    enabled = os.getenv("RECONCILIATION_ENABLED", "true").lower()
    hour = int(os.getenv("RECONCILIATION_UTC_HOUR", "2"))
    minute = int(os.getenv("RECONCILIATION_UTC_MINUTE", "0"))
    if enabled not in ("true", "false") or not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Reconciliation requires enabled=true/false, UTC hour=0..23, minute=0..59")
    return enabled == "true", hour, minute


def due_date(now, hour, minute):
    if now.tzinfo is None:
        raise ValueError("Schedule requires a timezone-aware timestamp")
    now = now.astimezone(UTC)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (target if now >= target else target - timedelta(days=1)).date()


def tick(now, hour, minute):
    scheduled_for = due_date(now, hour, minute)
    result = reconciliation.execute("scheduled", scheduled_for)
    cutoff = datetime.combine(scheduled_for, datetime.min.time(), UTC).replace(
        hour=hour, minute=minute
    )
    try:
        limit = int(os.getenv("EXPLANATION_BATCH_SIZE", "1"))
        if not 1 <= limit <= 100:
            raise ValueError("EXPLANATION_BATCH_SIZE must be 1..100")
        explained = explanations.execute(limit, cutoff)
        if explained["explained"]:
            logging.info("risk explanations saved=%s", explained["explained"])
    except Exception as exc:
        # Provider failure cannot invalidate an already saved reconciliation report.
        logging.error("risk explanations unavailable: %s", type(exc).__name__)
    return result


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    enabled, hour, minute = settings()
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    logging.info("worker ready; reconciliation=%s; schedule=%02d:%02d UTC", enabled, hour, minute)
    while not stopped.is_set():
        if enabled:
            try:
                result = tick(datetime.now(UTC), hour, minute)
                if result:
                    logging.info(
                        "reconciliation run=%s status=%s findings=%s problems=%s",
                        result["id"],
                        result["status"],
                        result["finding_count"],
                        result["problem_count"],
                    )
            except DomainError as exc:
                logging.info("reconciliation skipped: %s", exc.code)
            except Exception as exc:
                # Do not put database connection strings or financial rows in logs.
                logging.error("reconciliation unavailable: %s", type(exc).__name__)
        stopped.wait(60)


if __name__ == "__main__":
    main()
