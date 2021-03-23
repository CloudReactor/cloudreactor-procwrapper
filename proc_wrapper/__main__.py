import logging
import os
import sys

from proc_wrapper import ProcWrapper

_DEFAULT_LOG_LEVEL = 'WARNING'


main_parser = ProcWrapper.make_arg_parser(require_command=True)
main_args = main_parser.parse_args()

if main_args.version:
    print(f"proc_wrapper version {ProcWrapper.VERSION}. See https://cloudreactor.io for more info.")
    sys.exit(0)

log_level = (main_args.log_level
        or os.environ.get('PROC_WRAPPER_LOG_LEVEL', _DEFAULT_LOG_LEVEL)) \
        .upper()
numeric_log_level = getattr(logging, log_level, None)
if not isinstance(numeric_log_level, int):
    logging.warning(f"Invalid log level: {log_level}, defaulting to {_DEFAULT_LOG_LEVEL}")
    numeric_log_level = getattr(logging, _DEFAULT_LOG_LEVEL, None)

logging.basicConfig(level=numeric_log_level,
    format="PROC_WRAPPER: %(asctime)s %(levelname)s: %(message)s")

ProcWrapper(args=main_args, embedded_mode=False).run()
