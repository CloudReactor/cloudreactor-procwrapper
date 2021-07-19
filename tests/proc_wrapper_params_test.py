from typing import Mapping

from proc_wrapper import (
    ConfigResolver,
    ProcWrapper,
    ProcWrapperParams,
    RuntimeMetadataFetcher,
    make_arg_parser,
)


def make_proc_wrapper_params(
    env: Mapping[str, str], embedded_mode: bool = True
) -> ProcWrapper:
    params = ProcWrapperParams(embedded_mode=embedded_mode)

    if embedded_mode:
        params.offline_mode = True
    else:
        main_parser = make_arg_parser(require_command=True)
        params = main_parser.parse_args(args=["echo"], namespace=params)

    runtime_metadata_fetcher = RuntimeMetadataFetcher()
    runtime_metadata = runtime_metadata_fetcher.fetch(env=env)
    params.override_resolver_params_from_env(env=env)

    config_resolver = ConfigResolver(
        params=params, runtime_metadata=runtime_metadata, env_override=env
    )

    resolved_env, _failed_var_names = config_resolver.fetch_and_resolve_env()

    params.override_proc_wrapper_params_from_env(
        resolved_env, mutable_only=False, runtime_metadata=runtime_metadata
    )

    return params


def test_rollbar_config():
    env_override = {
        "PROC_WRAPPER_LOG_LEVEL": "DEBUG",
        "PROC_WRAPPER_OFFLINE_MODE": "TRUE",
    }
    env_override["PROC_WRAPPER_ROLLBAR_ACCESS_TOKEN"] = "rbtoken"
    env_override["PROC_WRAPPER_ROLLBAR_RETRIES"] = "3"
    env_override["PROC_WRAPPER_ROLLBAR_RETRY_DELAY_SECONDS"] = "10"
    env_override["PROC_WRAPPER_ROLLBAR_RETRY_TIMEOUT_SECONDS"] = "30"
    params = make_proc_wrapper_params(embedded_mode=False, env=env_override)
    assert params.rollbar_access_token == "rbtoken"
    assert params.rollbar_retries == 3
    assert params.rollbar_retry_delay == 10
    assert params.rollbar_timeout == 30
