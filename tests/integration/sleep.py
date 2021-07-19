#!/usr/local/bin/python
import logging
import os
import signal
import time


def signal_handler(signum, frame):
    # This will cause the exit handler to be executed, if it is registered.
    raise RuntimeError("Caught SIGTERM, exiting.")


logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s %(levelname)s: %(message)s"
)

signal.signal(signal.SIGTERM, signal_handler)

max_rows = int(os.environ.get("MAX_ROWS", "10"))

row_to_fail_at = int(os.environ.get("ROW_TO_FAIL_AT", "-1"))

i = 0
while (max_rows < 0) or (i < max_rows):
    if i == row_to_fail_at:
        logging.error(f"Failed on row {i}, exiting!")
        exit(1)
    else:
        print(f"sleeping {i} ...")
        time.sleep(2)
        print("done sleeping")
        i += 1

status = {"last_status_message": "im woke"}
