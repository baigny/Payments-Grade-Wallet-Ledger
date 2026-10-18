import logging
import signal
import threading

from wallet.db import connect


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    with connect(connect_timeout=5) as conn:
        conn.execute("SELECT 1")
    logging.info('{"worker":"ready","scheduled_jobs":0}')
    # Reconciliation scheduling is added with the reconciliation milestone.
    while not stopped.wait(30):
        logging.info('{"worker":"idle"}')


if __name__ == "__main__":
    main()
