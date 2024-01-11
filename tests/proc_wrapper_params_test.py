from typing import Any, Dict, List, Mapping, Optional, Union

import pytest

from proc_wrapper import (
    ConfigResolver,
    DefaultRuntimeMetadataFetcher,
    ProcWrapperParams,
    make_arg_parser,
)
from proc_wrapper.proc_wrapper_params import (
    DEFAULT_API_BASE_URL,
    DEFAULT_LOG_LEVEL,
    IMMUTABLE_PROPERTIES_COPIED_FROM_CONFIG,
    MUTABLE_PROPERTIES_COPIED_FROM_CONFIG,
    PROPERTIES_COPIED_FROM_ROLLBAR_CONFIG,
    SHELL_MODE_AUTO,
    SHELL_MODE_FORCE_DISABLE,
    SHELL_MODE_FORCE_ENABLE,
)


def make_proc_wrapper_params(
    env: Optional[Mapping[str, str]] = None, embedded_mode: bool = True
) -> ProcWrapperParams:
    env = env or {}
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

    params.override_params_from_env(resolved_env, mutable_only=False)

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


def make_proc_wrapper_params_dict() -> Dict[str, Any]:
    return {
        "schedule": "",
        "max_concurrency": 1,
        "max_conflicting_age": 2000,
        "offline_mode": True,
        "prevent_offline_execution": False,
        "service": True,
        "deployment": "theatre",
        "api_base_url": "https://api.nasty.com",
        "api_heartbeat_interval": 40,
        "enable_status_update_listener": True,
        "status_update_socket_port": 5000,
        "status_update_message_max_bytes": 10000,
        "status_update_interval": 60,
        "log_level": "DEBUG",
        "include_timestamps_in_log": False,
        "api_key": "SOMEKEY",
        "api_request_timeout": 30,
        "api_error_timeout": 70,
        "api_retry_delay": 71,
        "api_resume_delay": 72,
        "api_task_execution_creation_error_timeout": 73,
        "api_task_execution_creation_conflict_timeout": 74,
        "api_task_execution_creation_conflict_retry_delay": 75,
        "process_timeout": 76,
        "process_max_retries": 77,
        "process_retry_delay": 78,
        "send_pid": True,
        "send_hostname": True,
        "send_runtime_metadata": True,
    }


def make_config_with_proc_wrapper_params(
    params_dict: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    params_dict = params_dict or make_proc_wrapper_params_dict()
    return {"proc_wrapper_params": params_dict}


@pytest.mark.parametrize(
    """
    mutable_only,
    include_task_execution,
    include_task,
    include_rollbar,
    include_env_override,
    """,
    [
        (False, True, True, False, False),
        (False, False, True, False, False),
        (False, True, False, False, False),
        (False, True, True, False, False),
        (False, True, True, True, False),
        (False, False, False, False, True),
        (True, False, False, False, False),
        (True, False, False, False, False),
        (True, True, False, False, False),
        (True, False, True, False, False),
        (True, False, False, True, False),
    ],
)
def test_override_from_config(
    mutable_only: bool,
    include_task_execution: bool,
    include_task: bool,
    include_rollbar: bool,
    include_env_override,
):
    params = make_proc_wrapper_params(embedded_mode=True)

    params_dict = make_proc_wrapper_params_dict()

    if include_task_execution:
        params_dict["task_execution"] = {
            "uuid": "abcde",
            "version_number": 17,
            "version_text": "17 Super",
            "version_signature": "deadbee",
        }

    if include_task:
        params_dict["task"] = {
            "name": "A task",
            "uuid": "TASK-UUID",
            "was_auto_created": True,
            "passive": True,
            "other_metadata": {"a": 1, "b": ["x", "y"]},
        }

    if include_rollbar:
        params_dict["rollbar"] = {
            "access_token": "RB_FAKE_TOKEN",
            "retries": 50,
            "retry_delay": 90,
            "timeout": 120,
        }

    if include_env_override:
        params_dict["env_override"] = {"ENV_X": "X", "ENV_Y": "Y"}

    config = make_config_with_proc_wrapper_params(params_dict=params_dict)
    env_override = params.override_params_from_config(
        config=config, mutable_only=mutable_only
    )

    if mutable_only:
        assert params.api_base_url == DEFAULT_API_BASE_URL
        assert params.log_level == DEFAULT_LOG_LEVEL
        assert params.service is False
    else:
        for attr in IMMUTABLE_PROPERTIES_COPIED_FROM_CONFIG:
            if attr in params_dict:
                assert getattr(params, attr) == params_dict[attr]

    if include_task_execution and (not mutable_only):
        assert params.task_execution_uuid == "abcde"
        assert params.task_version_number == 17
        assert params.task_version_text == "17 Super"
        assert params.task_version_signature == "deadbee"
    else:
        assert params.task_execution_uuid is None
        assert params.task_version_number is None
        assert params.task_version_text is None
        assert params.task_version_signature is None

    if include_task and (not mutable_only):
        assert params.task_name == "A task"
        assert params.task_uuid == "TASK-UUID"
        assert params.auto_create_task is True
        assert params.task_is_passive is True
        assert params.task_instance_metadata == params_dict["task"]["other_metadata"]
    else:
        assert params.task_name is None
        assert params.task_uuid is None
        assert params.auto_create_task is False
        assert params.task_is_passive is True
        assert params.task_instance_metadata is None

    for attr in MUTABLE_PROPERTIES_COPIED_FROM_CONFIG:
        if attr in params_dict:
            assert getattr(params, attr) == params_dict[attr]

    if include_rollbar:
        for attr in PROPERTIES_COPIED_FROM_ROLLBAR_CONFIG:
            if attr in params_dict:
                assert (
                    getattr(params, "rollbar_" + attr) == params_dict["rollbar"][attr]
                )
    else:
        assert params.rollbar_access_token is None

    if include_env_override:
        assert env_override == params_dict["env_override"]
    else:
        assert env_override is None


@pytest.mark.parametrize(
    """
    input,
    expect_override
    """,
    [
        (None, False),
        ("astring", False),
        (["a", "b"], False),
        ({}, False),
        ({"cloudreactor_context": "meh"}, False),
        ({"cloudreactor_context": {}}, False),
        ({"cloudreactor_context": {"proc_wrapper_params": None}}, False),
        ({"cloudreactor_context": {"proc_wrapper_params": {}}}, False),
        (
            {"cloudreactor_context": {"proc_wrapper_params": {"task_execution": True}}},
            False,
        ),
        (
            {"cloudreactor_context": {"proc_wrapper_params": {"task_execution": {}}}},
            False,
        ),
        (
            {
                "cloudreactor_context": {
                    "proc_wrapper_params": {"task_execution": {"uuid": "TE-UUID"}}
                }
            },
            True,
        ),
    ],
)
def test_override_from_input(input: Optional[Any], expect_override: bool):
    params = make_proc_wrapper_params(embedded_mode=True)

    params.override_params_from_input(input)

    if expect_override:
        assert params.task_execution_uuid == "TE-UUID"
    else:
        assert params.task_execution_uuid is None
