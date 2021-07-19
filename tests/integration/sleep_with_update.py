#!/usr/local/bin/python
import logging
import os
import random
import signal
import time

from proc_wrapper import StatusUpdater


def signal_handler(signum, frame):
    # This will cause the exit handler to be executed, if it is registered.
    raise RuntimeError("Caught SIGTERM, exiting.")


logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s %(levelname)s: %(message)s"
)

signal.signal(signal.SIGTERM, signal_handler)

row_to_fail_at = int(os.environ.get("ROW_TO_FAIL_AT", "-1"))

updater = StatusUpdater()

with StatusUpdater() as updater:
    updater.send_update(
        last_status_message="sleeping", expected_count=random.randrange(5, 15)
    )
    success_count = 0
    for i in range(5):
        if i == row_to_fail_at:
            updater.send_update(failed_count=1)
            logging.error(f"Failed on row {i}, exiting!")
            exit(1)
        else:
            print(f"sleeping {i} ...")
            time.sleep(2)
            success_count += random.randrange(1, 10)

            try:
                updater.send_update(success_count=success_count)
            except Exception:
                logging.info("Failed to send update")

            print("done sleeping")

    updater.send_update(last_status_message="woken up")
