import logging
import os
import sys

from proc_wrapper import ProcWrapper, ProcWrapperParams, make_arg_parser

_DEFAULT_LOG_LEVEL = "WARNING"


main_parser = make_arg_parser()
main_args = main_parser.parse_args(namespace=ProcWrapperParams(embedded_mode=False))

if main_args.version:
    print(
        f"""
{ProcWrapper.WRAPPER_FAMILY} v{ProcWrapper.VERSION}.
See https://cloudreactor.io for more info.
"""
    )
    sys.exit(0)

log_level = (
    main_args.log_level or os.environ.get("PROC_WRAPPER_LOG_LEVEL", _DEFAULT_LOG_LEVEL)
).upper()
numeric_log_level = getattr(logging, log_level, None)
if not isinstance(numeric_log_level, int):
    logging.warning(
        f"Invalid log level: {log_level}, defaulting to {_DEFAULT_LOG_LEVEL}"
    )
    numeric_log_level = getattr(logging, _DEFAULT_LOG_LEVEL, None)

logging.basicConfig(
    level=numeric_log_level,
    format="PROC_WRAPPER: %(asctime)s %(levelname)s: %(message)s",
)

ProcWrapper(params=main_args).run()
