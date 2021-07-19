#!/usr/local/bin/python
import logging
import sys
import time

import proc_wrapper
from proc_wrapper import ProcWrapper


def fun(ew, data):
    print(f"data = {data}")
    print("sleeping ...")
    ew.update_status(success_count=0)
    time.sleep(1)
    print("done sleeping")
    ew.update_status(success_count=1)
    time.sleep(1)
    ew.update_status(success_count=2)

    if len(sys.argv) > 1:
        raise RuntimeError(sys.argv[1])


logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s %(levelname)s: %(message)s"
)

args = proc_wrapper.make_default_args()
args.offline_mode = True
args.status_update_interval = 5
args.task_name = "managed_call_test"
proc_wrapper = ProcWrapper(params=args)

proc_wrapper.managed_call(fun, {"a": 1, "b": 2})

print("done")
