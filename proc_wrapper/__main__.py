import logging
import os
import sys

from proc_wrapper import ProcWrapper, ProcWrapperParams
from proc_wrapper.proc_wrapper_params import DEFAULT_LOG_LEVEL, make_arg_parser


def main():
    main_parser = make_arg_parser()
    main_args = main_parser.parse_args(namespace=ProcWrapperParams(embedded_mode=False))

    if main_args.version:
        print(f"""{ProcWrapper.WRAPPER_FAMILY} v{ProcWrapper.VERSION}""")
        sys.exit(0)

    log_level = (os.environ.get("PROC_WRAPPER_LOG_LEVEL", main_args.log_level)).upper()
    numeric_log_level = getattr(logging, log_level, None)

    if not isinstance(numeric_log_level, int):
        logging.warning(
            f"Invalid log level: {log_level}, defaulting to {DEFAULT_LOG_LEVEL}"
        )
        numeric_log_level = getattr(logging, DEFAULT_LOG_LEVEL, logging.INFO)

    logging.basicConfig(
        level=numeric_log_level,
        format="PROC_WRAPPER: %(asctime)s %(levelname)s: %(message)s",
    )

    if (numeric_log_level is None) or (numeric_log_level < logging.INFO):
        # Disable botocore DEBUG logging because it leaks secrets
        logging.getLogger("botocore").setLevel(logging.INFO)

    ProcWrapper(params=main_args).run()


if __name__ == "__main__":
    main()
