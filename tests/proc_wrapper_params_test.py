from typing import List, Mapping, Optional, Union

import pytest

from proc_wrapper import (
    ConfigResolver,
    DefaultRuntimeMetadataFetcher,
    ProcWrapperParams,
    make_arg_parser,
)
from proc_wrapper.proc_wrapper_params import (
    SHELL_MODE_AUTO,
    SHELL_MODE_FORCE_DISABLE,
    SHELL_MODE_FORCE_ENABLE,
)


def make_proc_wrapper_params(
    env: Mapping[str, str], embedded_mode: bool = True
) -> ProcWrapperParams:
    params = ProcWrapperParams(embedded_mode=embedded_mode)

    if embedded_mode:
        params.offline_mode = True
    else:
        main_parser = make_arg_parser()
        params = main_parser.parse_args(args=["echo"], namespace=params)

    runtime_metadata_fetcher = DefaultRuntimeMetadataFetcher()
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


@pytest.mark.parametrize(
    """
    command, command_line, shell_mode, strip_shell_wrapping,
    expected_command, expected_shell_flag
""",
    [
        (
            ["java", "-jar", "app.jar"],
            None,
            SHELL_MODE_AUTO,
            True,
            ["java", "-jar", "app.jar"],
            False,
        ),
        (
            ["java", "-jar", "app.jar"],
            None,
            SHELL_MODE_FORCE_ENABLE,
            True,
            ["java", "-jar", "app.jar"],
            True,
        ),
        (
            ["java", "-jar", "app.jar"],
            None,
            SHELL_MODE_FORCE_DISABLE,
            True,
            ["java", "-jar", "app.jar"],
            False,
        ),
        (
            None,
            "java -jar app.jar",
            SHELL_MODE_AUTO,
            True,
            ["java", "-jar", "app.jar"],
            False,
        ),
        (
            None,
            "java -jar app.jar",
            SHELL_MODE_FORCE_ENABLE,
            True,
            "java -jar app.jar",
            True,
        ),
        (
            None,
            "java -jar app.jar",
            SHELL_MODE_FORCE_DISABLE,
            True,
            ["java", "-jar", "app.jar"],
            False,
        ),
        (
            None,
            'echo "hello dude"',
            SHELL_MODE_AUTO,
            True,
            'echo "hello dude"',
            True,
        ),
        (
            None,
            'echo "hello dude"',
            SHELL_MODE_FORCE_DISABLE,
            True,
            ["echo", '"hello', 'dude"'],
            False,
        ),
        (
            ["echo", '"hello dude"'],
            None,
            SHELL_MODE_AUTO,
            True,
            ["echo", '"hello dude"'],
            True,
        ),
        (
            ["echo", '"hello dude"'],
            None,
            SHELL_MODE_FORCE_DISABLE,
            True,
            ["echo", '"hello dude"'],
            False,
        ),
        (
            ["/bin/sh", "-c", '"ls -la"'],
            None,
            SHELL_MODE_AUTO,
            True,
            "ls -la",
            True,
        ),
        (
            ["/bin/sh", "-c", '"ls -la"'],
            None,
            SHELL_MODE_FORCE_ENABLE,
            True,
            "ls -la",
            True,
        ),
        (
            ["/bin/sh", "-c", '"ls -la"'],
            None,
            SHELL_MODE_FORCE_DISABLE,
            True,
            ["ls", "-la"],
            False,
        ),
        (
            None,
            '/bin/sh -c "ls -la"',
            SHELL_MODE_AUTO,
            True,
            "ls -la",
            True,
        ),
        (
            None,
            '/bin/sh -c "ls -la"',
            SHELL_MODE_FORCE_ENABLE,
            True,
            "ls -la",
            True,
        ),
        (
            None,
            '/bin/sh -c "ls -la"',
            SHELL_MODE_FORCE_DISABLE,
            True,
            ["ls", "-la"],
            False,
        ),
        (
            None,
            '/bin/bash -c "ls -la"',
            SHELL_MODE_AUTO,
            True,
            "ls -la",
            True,
        ),
        (
            ["/bin/sh", "-c", '"/bin/sh -c \\"ls -la\\""'],
            None,
            SHELL_MODE_AUTO,
            True,
            "ls -la",
            True,
        ),
        (
            None,
            '/bin/sh -c "/bin/sh -c \\"ls -la\\""',
            SHELL_MODE_AUTO,
            True,
            "ls -la",
            True,
        ),
        (
            ["/bin/sh", "-c", '"ls -la"'],
            None,
            SHELL_MODE_AUTO,
            False,
            ["/bin/sh", "-c", '"ls -la"'],
            True,
        ),
        (
            None,
            '/bin/bash -c "ls -la"',
            SHELL_MODE_AUTO,
            False,
            '/bin/bash -c "ls -la"',
            True,
        ),
    ],
)
def test_resolve_command_and_shell_flag(
    command: Optional[List[str]],
    command_line: Optional[str],
    shell_mode: str,
    strip_shell_wrapping: bool,
    expected_command: Union[str, List[str]],
    expected_shell_flag: bool,
):
    params = ProcWrapperParams()

    if command:
        params.command = command

    if command_line:
        params.command_line = command_line

    params.shell_mode = shell_mode
    params.strip_shell_wrapping = strip_shell_wrapping

    resolved_command, resolved_shell_flag = params.resolve_command_and_shell_flag()
    assert resolved_command == expected_command
    assert resolved_shell_flag == expected_shell_flag


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
