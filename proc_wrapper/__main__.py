import logging
import os
import sys
from typing import Optional

from proc_wrapper import (
    ConfigResolver,
    ProcWrapper,
    ProcWrapperParams,
    RuntimeMetadata,
    RuntimeMetadataFetcher,
    make_arg_parser,
)

_DEFAULT_LOG_LEVEL = "WARNING"


# TODO Don't fail immediately if command is not present
main_parser = make_arg_parser(require_command=True)
main_args = main_parser.parse_args(namespace=ProcWrapperParams(embedded_mode=False))

if main_args.version:
    print(
        f"""
proc_wrapper version {ProcWrapper.VERSION}.
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

runtime_metadata_fetcher = RuntimeMetadataFetcher()
runtime_metadata: Optional[RuntimeMetadata] = None
try:
    runtime_metadata = runtime_metadata_fetcher.fetch(env=os.environ)
except Exception:
    logging.exception("Failed to fetch runtime metadata")

main_args.override_resolver_params_from_env(os.environ)

config_resolver = ConfigResolver(params=main_args, runtime_metadata=runtime_metadata)

resolved_env, failed_var_names = config_resolver.fetch_and_resolve_env()

main_args.override_proc_wrapper_params_from_env(
    resolved_env, mutable_only=False, runtime_metadata=runtime_metadata
)

ProcWrapper(
    params=main_args,
    runtime_metadata_fetcher=runtime_metadata_fetcher,
    config_resolver=config_resolver,
    env_override=resolved_env,
).run()
