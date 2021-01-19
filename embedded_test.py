#!/usr/local/bin/python
import logging
import sys
import time

from proc_wrapper import ProcWrapper

logging.basicConfig(level=logging.DEBUG,
                    format=f"%(asctime)s %(levelname)s: %(message)s")

args = ProcWrapper.make_default_args()
args.process_name = 'embedded_test'
args.process_type_id = 44
args.enable_status_listener = True

proc_wrapper = ProcWrapper(args=args)

proc_wrapper.request_process_start_if_max_concurrency_ok()

status = ProcWrapper.STATUS_FAILED
try:
    for i in range(5):
        print(f"sleeping {i} ...")
        time.sleep(2)
        print("done sleeping")
        proc_wrapper.update_status(success_count=i, expected_count=5)

        if len(sys.argv) > 1:
            raise RuntimeError(sys.argv[1])


    status = ProcWrapper.STATUS_SUCCEEDED
finally:
    proc_wrapper.send_completion(status)

print('done')
