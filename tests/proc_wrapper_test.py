import json
import logging
import os
import platform
import random
import string
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import quote_plus

import pytest
from dateutil.relativedelta import relativedelta
from dotenv import dotenv_values
from freezegun import freeze_time
from pytest_httpserver import HTTPServer

from proc_wrapper import ProcWrapper, ProcWrapperParams, make_arg_parser
from proc_wrapper.common_constants import FORMAT_JSON
from proc_wrapper.common_utils import write_data_to_file
from proc_wrapper.runtime_metadata import (
    EXECUTION_METHOD_TYPE_AWS_ECS,
    EXECUTION_METHOD_TYPE_AWS_LAMBDA,
)

from .test_commons import (
    ACCEPT_JSON_HEADERS,
    TEST_ECS_CONTAINER_METADATA,
    TEST_ECS_TASK_METADATA,
    FakeAwsLambdaContext,
    make_capturing_handler,
    make_fake_aws_lambda_env,
)

TEST_API_PORT = 6777
TEST_API_KEY = "SOMEAPIKEY"
DEFAULT_TASK_UUID = "13b4cfbc-6ed5-4fd5-85e8-73e84e2f1b82"
DEFAULT_TASK_EXECUTION_UUID = "d9554f00-eaeb-4a16-96e4-9adda91a2750"
DEFAULT_TASK_VERSION_SIGNATURE = "43cfd2b905d5cb4f2e8fc941c7a1289002be9f7f"

CLIENT_HEADERS = {
    **ACCEPT_JSON_HEADERS,
    **{
        "Authorization": f"Bearer {TEST_API_KEY}",
        "Content-Type": "application/json",
    },
}

RESOLVE_ENV_BASE_ENV = {
    "PROC_WRAPPER_TASK_NAME": "Foo",
    "PROC_WRAPPER_API_KEY": "XXX",
    "PROC_WRAPPER_RESOLVE_SECRETS": "TRUE",
    "PROC_WRAPPER_SECRETS_AWS_REGION": "us-east-2",
}


def make_wrapped_mode_proc_wrapper(
    env: Mapping[str, str], args: list[str] = []
) -> ProcWrapper:
    main_parser = make_arg_parser()
    params = main_parser.parse_args(
        args=args, namespace=ProcWrapperParams(embedded_mode=False, env=env)
    )
    return ProcWrapper(params=params, env_override=env, override_params_from_env=True)


def make_online_base_env(port: int, command: Optional[str] = "echo") -> dict[str, str]:
    env = {
        "PROC_WRAPPER_LOG_LEVEL": "DEBUG",
        "PROC_WRAPPER_TASK_UUID": DEFAULT_TASK_UUID,
        "PROC_WRAPPER_TASK_VERSION_SIGNATURE": DEFAULT_TASK_VERSION_SIGNATURE,
        "PROC_WRAPPER_API_BASE_URL": f"http://localhost:{port}",
        "PROC_WRAPPER_API_KEY": TEST_API_KEY,
        "PROC_WRAPPER_API_TASK_CREATION_ERROR_TIMEOUT_SECONDS": "1",
        "PROC_WRAPPER_API_TASK_CREATION_CONFLICT_TIMEOUT_SECONDS": "1",
        "PROC_WRAPPER_API_TASK_CREATION_CONFLICT_RETRY_DELAY_SECONDS": "1",
        "PROC_WRAPPER_API_FINAL_UPDATE_TIMEOUT_SECONDS": "1",
        "PROC_WRAPPER_API_RETRY_DELAY_SECONDS": "1",
        "PROC_WRAPPER_API_RESUME_DELAY_SECONDS": "-1",
    }

    if command:
        env["PROC_WRAPPER_TASK_COMMAND"] = command

    return env


def make_online_params(port: int) -> ProcWrapperParams:
    params = ProcWrapperParams()
    params.task_uuid = DEFAULT_TASK_UUID
    params.task_version_signature = DEFAULT_TASK_VERSION_SIGNATURE
    params.api_base_url = f"http://localhost:{port}"
    params.api_key = TEST_API_KEY
    params.auto_create_task = True
    params.auto_create_task_run_environment_name = "myenv"
    params.process_max_retries = 2
    params.process_retry_delay = 1
    params.api_request_timeout = 5
    params.api_task_execution_creation_error_timeout = 1
    params.api_final_update_timeout = 5
    params.api_retry_delay = 1
    params.api_resume_delay = 1
    return params


def make_temp_filename(suffix: Optional[str] = None, base: Optional[str] = None) -> str:
    if not base:
        base = "".join(random.choices(string.ascii_letters + string.digits, k=12))

    filename = (Path(tempfile.gettempdir()) / base).resolve().as_posix()

    if suffix is not None:
        filename = f"{filename}.{suffix}"

    return filename


def test_wrapped_offline_mode():
    env_override = {
        "PROC_WRAPPER_LOG_LEVEL": "DEBUG",
        "PROC_WRAPPER_OFFLINE_MODE": "TRUE",
        "PROC_WRAPPER_TASK_COMMAND": "echo",
    }

    wrapper = make_wrapped_mode_proc_wrapper(env=env_override)
    assert wrapper.run() == 0


def test_wrapped_offline_mode_with_env_output_and_exit():
    output_filename = make_temp_filename(suffix="env")
    env_override = {
        "PROC_WRAPPER_LOG_LEVEL": "DEBUG",
        "PROC_WRAPPER_OFFLINE_MODE": "TRUE",
        "SOME_VALUE_FOR_PROC_WRAPPER_TO_RESOLVE": "PLAIN:xyz",
    }

    wrapper = make_wrapped_mode_proc_wrapper(
        env=env_override,
        args=[
            "--exit-after-writing-variables",
            "--env-output-filename",
            output_filename,
        ],
    )

    assert wrapper.run() == 0

    output_env = dotenv_values(output_filename)

    assert output_env["SOME_VALUE"] == "xyz"
    os.remove(output_filename)


def test_wrapped_offline_mode_with_env_json_output_and_exit():
    output_filename = make_temp_filename()
    env_override = {
        "PROC_WRAPPER_LOG_LEVEL": "DEBUG",
        "PROC_WRAPPER_OFFLINE_MODE": "TRUE",
        "SOME_VALUE_FOR_PROC_WRAPPER_TO_RESOLVE": "PLAIN:xyz",
        "PROC_WRAPPER_ENV_OUTPUT_FORMAT": "json",
    }

    wrapper = make_wrapped_mode_proc_wrapper(
        env=env_override,
        args=[
            "--exit-after-writing-variables",
            "--env-output-filename",
            output_filename,
        ],
    )

    assert wrapper.run() == 0

    with open(output_filename, "r") as f:
        output_env = json.load(f)

    assert output_env["SOME_VALUE"] == "xyz"
    os.remove(output_filename)


def test_wrapped_offline_mode_with_env_output_and_deletion():
    output_filename = "output.env"
    env_override = {
        "PROC_WRAPPER_OFFLINE_MODE": "TRUE",
        "PROC_WRAPPER_ENV_OUTPUT_FILENAME": output_filename,
    }

    if platform.system() == "Windows":
        command = "dir"
    else:
        command = "ls"

    wrapper = make_wrapped_mode_proc_wrapper(
        env=env_override, args=[command, output_filename]
    )

    assert wrapper.run() == 0

    assert not os.path.exists(output_filename)


def expect_task_execution_request(
    httpserver: HTTPServer,
    response_data: Optional[dict[str, Any]] = None,
    status: Optional[int] = None,
    update: bool = True,
    uuid: Optional[str] = None,
):
    method = "PATCH" if update else "POST"
    url = "/api/v1/task_executions/"
    uuid = uuid or DEFAULT_TASK_EXECUTION_UUID

    if not update and response_data is None:
        response_data = {
            "uuid": uuid,
            "task": {"uuid": DEFAULT_TASK_UUID, "name": "A Task"},
        }

    if status is None:
        expected_status = 204 if update else 201
    else:
        expected_status = status

    if update:
        url += quote_plus(uuid) + "/"

    handler, fetch_captured_request_data = make_capturing_handler(
        response_data=response_data, status=expected_status
    )

    print(f"Expect order request to {url}")

    httpserver.expect_oneshot_request(
        url, method=method, headers=CLIENT_HEADERS
    ).respond_with_handler(handler)

    return fetch_captured_request_data


@pytest.mark.parametrize(
    """
    env_override, command, expected_exit_code, expect_api_server_use, expected_final_status,
    response_data, sent_input_value, sent_output_value
    """,
    [
        ({}, "echo", 0, True, ProcWrapper.STATUS_SUCCEEDED, None, None, None),
        (
            {},
            None,
            ProcWrapper._EXIT_CODE_CONFIGURATION_ERROR,
            False,
            ProcWrapper.STATUS_FAILED,
            None,
            None,
            None,
        ),
        (
            {"PROC_WRAPPER_PROCESS_TIMEOUT_SECONDS": "1"},
            "sleep 60",
            1,
            True,
            ProcWrapper.STATUS_TERMINATED_AFTER_TIME_OUT,
            None,
            None,
            None,
        ),
        (
            {"PROC_WRAPPER_API_MANAGED_PROBABILITY": "0.0000000001"},
            "echo",
            0,
            False,
            None,
            None,
            None,
            None,
        ),
        (
            {"PROC_WRAPPER_API_MANAGED_PROBABILITY": "0.0000000001"},
            "fakecmdo",
            ProcWrapper._EXIT_CODE_CONFIGURATION_ERROR,
            False,
            ProcWrapper.STATUS_FAILED,
            None,
            None,
            None,
        ),
        (
            {
                "PROC_WRAPPER_API_MANAGED_PROBABILITY": "0.0000000001",
                "PROC_WRAPPER_API_FAILURE_REPORT_PROBABILITY": "0.0000000001",
            },
            "ls -zsfadsgadsg",
            None,
            False,
            None,
            None,
            None,
            None,
        ),
        (
            {
                "PROC_WRAPPER_PROCESS_TIMEOUT_SECONDS": "1",
                "PROC_WRAPPER_API_MANAGED_PROBABILITY": "0.0000000001",
                "PROC_WRAPPER_API_TIMEOUT_REPORT_PROBABILITY": "0.0000000001",
            },
            "sleep 60",
            1,
            False,
            None,
            None,
            None,
            None,
        ),
        (
            {
                "PROC_WRAPPER_SEND_INPUT_VALUE": "1",
                "PROC_WRAPPER_INPUT_VALUE": "is cool",
            },
            "echo $PROC_WRAPPER_INPUT_VALUE",
            0,
            True,
            ProcWrapper.STATUS_SUCCEEDED,
            None,
            "is cool",
            None,
        ),
        (
            {
                "PROC_WRAPPER_INPUT_ENV_VAR_NAME": "THE_INPUT",
                "PROC_WRAPPER_SEND_INPUT_VALUE": "1",
                "THE_INPUT": "is cool",
            },
            "echo $THE_INPUT",
            0,
            True,
            ProcWrapper.STATUS_SUCCEEDED,
            None,
            "is cool",
            None,
        ),
        (
            {
                "PROC_WRAPPER_INPUT_ENV_VAR_NAME": "THE_INPUT",
                "PROC_WRAPPER_INPUT_VALUE_FORMAT": "json",
                "PROC_WRAPPER_SEND_INPUT_VALUE": "1",
                "THE_INPUT": """{"a":7}""",
            },
            "echo $THE_INPUT",
            0,
            True,
            ProcWrapper.STATUS_SUCCEEDED,
            None,
            {"a": 7},
            None,
        ),
        (
            {
                "PROC_WRAPPER_INPUT_VALUE": """{"a":7}""",
                "PROC_WRAPPER_INPUT_VALUE_FORMAT": "json",
                "PROC_WRAPPER_SEND_INPUT_VALUE": "1",
            },
            "echo $PROC_WRAPPER_INPUT_VALUE",
            0,
            True,
            ProcWrapper.STATUS_SUCCEEDED,
            None,
            {"a": 7},
            None,
        ),
        (
            {
                "PROC_WRAPPER_INPUT_FILENAME": make_temp_filename(),
                "PROC_WRAPPER_INPUT_VALUE_FORMAT": "json",
                "PROC_WRAPPER_SEND_INPUT_VALUE": "1",
            },
            "echo $PROC_WRAPPER_INPUT_VALUE",
            0,
            True,
            ProcWrapper.STATUS_SUCCEEDED,
            None,
            {"a": 7},
            None,
        ),
        (
            {
                "PROC_WRAPPER_INPUT_FILENAME": make_temp_filename(suffix="json"),
                "PROC_WRAPPER_SEND_INPUT_VALUE": "1",
            },
            "echo $PROC_WRAPPER_INPUT_VALUE",
            0,
            True,
            ProcWrapper.STATUS_SUCCEEDED,
            None,
            {"a": 7},
            None,
        ),
        (
            {
                "PROC_WRAPPER_INPUT_ENV_VAR_NAME": "THE_INPUT",
                "PROC_WRAPPER_INPUT_VALUE_FORMAT": "json",
                "THE_INPUT": """{"a":7}""",
            },
            "echo $PROC_WRAPPER_INPUT_VALUE",
            0,
            True,
            ProcWrapper.STATUS_SUCCEEDED,
            None,
            None,
            None,
        ),
        (
            {
                "PROC_WRAPPER_RESULT_FILENAME": make_temp_filename(),
                "PROC_WRAPPER_RESULT_VALUE_FORMAT": "json",
            },
            "echo '{\"b\":8}' > $PROC_WRAPPER_RESULT_FILENAME",
            0,
            True,
            ProcWrapper.STATUS_SUCCEEDED,
            None,
            None,
            {"b": 8},
        ),
        (
            {
                "PROC_WRAPPER_RESULT_FILENAME": make_temp_filename(suffix="json"),
            },
            "echo '{\"b\":8}' > $PROC_WRAPPER_RESULT_FILENAME",
            0,
            True,
            ProcWrapper.STATUS_SUCCEEDED,
            None,
            None,
            {"b": 8},
        ),
        (
            {
                "PROC_WRAPPER_RESULT_FILENAME": make_temp_filename(),
                "PROC_WRAPPER_RESULT_VALUE_FORMAT": "json",
                "PROC_WRAPPER_CLEANUP_RESULT_FILE": "0",
            },
            "echo '{\"b\":8}' > $PROC_WRAPPER_RESULT_FILENAME",
            0,
            True,
            ProcWrapper.STATUS_SUCCEEDED,
            None,
            None,
            {"b": 8},
        ),
    ],
)
def test_wrapped_mode_with_server(
    httpserver: HTTPServer,
    env_override: dict[str, str],
    command: Optional[str],
    expected_exit_code: Optional[int],
    expect_api_server_use: bool,
    expected_final_status: Optional[str],
    response_data: Optional[Any],
    sent_input_value: Optional[Any],
    sent_output_value: Optional[Any],
) -> None:
    env = make_online_base_env(httpserver.port, command=command)
    env.update(env_override)

    result_filename = env.get("PROC_WRAPPER_RESULT_FILENAME")

    # Windows seems to have issues writing the file using the > operator
    if result_filename and (platform.system() == "Windows"):
        return

    wrapper = make_wrapped_mode_proc_wrapper(env=env)

    input_filename = env.get("PROC_WRAPPER_INPUT_FILENAME")

    if input_filename and sent_input_value:
        write_data_to_file(
            filename=input_filename, data=sent_input_value, format=FORMAT_JSON
        )

    fetch_creation_request_data: Optional[Any] = None
    if expect_api_server_use or expected_final_status:
        fetch_creation_request_data = expect_task_execution_request(
            httpserver=httpserver, update=False, response_data=response_data
        )

    fetch_update_request_data: Optional[Any] = None
    if expect_api_server_use:
        fetch_update_request_data = expect_task_execution_request(httpserver=httpserver)

    expected_started_at = datetime.now(timezone.utc)

    rv = wrapper.run()

    if expected_exit_code is not None:
        assert rv == expected_exit_code

    process_env = wrapper.make_process_env()

    if expect_api_server_use:
        copied_prop_names = [
            "PROC_WRAPPER_TASK_UUID",
            "PROC_WRAPPER_TASK_VERSION_SIGNATURE",
            "PROC_WRAPPER_API_BASE_URL",
            "PROC_WRAPPER_API_KEY",
            "PROC_WRAPPER_API_RETRY_DELAY_SECONDS",
        ]

        for p in copied_prop_names:
            assert process_env[p] == env[p], p

        expected_props = {
            "PROC_WRAPPER_OFFLINE_MODE": "FALSE",
            "PROC_WRAPPER_API_ERROR_TIMEOUT_SECONDS": "300",
            "PROC_WRAPPER_API_REQUEST_TIMEOUT_SECONDS": "30",
            "PROC_WRAPPER_API_RETRY_DELAY_SECONDS": "1",
            # original value is -1, but negative values get limited to 0
            "PROC_WRAPPER_API_RESUME_DELAY_SECONDS": "0",
            "PROC_WRAPPER_ENABLE_STATUS_UPDATE_LISTENER": "FALSE",
            "PROC_WRAPPER_PROCESS_TIMEOUT_SECONDS": env_override.get(
                "PROC_WRAPPER_PROCESS_TIMEOUT_SECONDS"
            )
            or "-1",
            "PROC_WRAPPER_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS": "30",
            "PROC_WRAPPER_MAX_CONCURRENCY": "-1",
            "PROC_WRAPPER_PREVENT_OFFLINE_EXECUTION": "FALSE",
        }

        if expect_api_server_use:
            expected_props[
                "PROC_WRAPPER_TASK_EXECUTION_UUID"
            ] = DEFAULT_TASK_EXECUTION_UUID

        for k, v in expected_props.items():
            assert process_env[k] == v, k

        assert process_env

    httpserver.check_assertions()

    if expect_api_server_use and fetch_creation_request_data:
        crd = fetch_creation_request_data()

        expected_status = ProcWrapper.STATUS_RUNNING

        if (not expect_api_server_use) and expected_final_status:
            expected_status = expected_final_status

        assert crd["status"] == expected_status
        assert crd["is_service"] is False
        assert crd["wrapper_version"] == ProcWrapper.VERSION
        assert crd["wrapper_family"] == ProcWrapper.WRAPPER_FAMILY
        assert crd["embedded_mode"] is False
        task = crd["task"]
        assert task["uuid"] == DEFAULT_TASK_UUID

        last_rd = crd

        if expect_api_server_use and fetch_update_request_data:
            urd = fetch_update_request_data()
            assert urd.get("failed_attempts") is None
            last_rd = urd
        else:
            started_at_str = crd["started_at"]
            started_at = datetime.fromisoformat(started_at_str)
            assert abs((expected_started_at - started_at).seconds) < 10

        assert last_rd["status"] == expected_final_status

        expected_timed_out_attempts: Optional[int] = None
        if expected_final_status == ProcWrapper.STATUS_TERMINATED_AFTER_TIME_OUT:
            expected_timed_out_attempts = 1

        assert last_rd.get("timed_out_attempts") == expected_timed_out_attempts

        finished_at_str = last_rd["finished_at"]
        finished_at = datetime.fromisoformat(finished_at_str)
        assert (abs(datetime.now(timezone.utc) - finished_at)).seconds < 10

        if sent_input_value is not None:
            assert crd["input_value"] == sent_input_value

        if sent_output_value is not None:
            assert last_rd["output_value"] == sent_output_value

        if result_filename:
            if env.get("PROC_WRAPPER_CLEANUP_RESULT_FILE") == "0":
                assert os.path.exists(result_filename)
                os.remove(result_filename)
            else:
                assert not os.path.exists(result_filename)


@pytest.mark.parametrize(
    """
    env_override, command,
    expected_debug_log_tail, expected_error_log_tail
    """,
    [
        ({"PROC_WRAPPER_NUM_LOG_LINES_SENT_ON_SUCCESS": "2"}, "echo hi", "hi", None),
        (
            {"PROC_WRAPPER_NUM_LOG_LINES_SENT_ON_FAILURE": "2"},
            "ls notafile",
            "notafile",
            None,
        ),
        (
            {"PROC_WRAPPER_NUM_LOG_LINES_SENT_ON_FAILURE": "2"},
            "echo aya; echo bus; echo cab; badcmd",
            "cab",
            None,
        ),
        (
            {
                "PROC_WRAPPER_NUM_LOG_LINES_SENT_ON_FAILURE": "2",
                "PROC_WRAPPER_MERGE_STDOUT_AND_STDERR_LOGS": "0",
            },
            "ls notafile",
            None,
            "notafile",
        ),
        (
            {
                "PROC_WRAPPER_PROCESS_TIMEOUT_SECONDS": "2",
                "PROC_WRAPPER_NUM_LOG_LINES_SENT_ON_TIMEOUT": "2",
            },
            "echo agh; sleep 5;",
            "agh",
            None,
        ),
    ],
)
def test_wrapped_mode_with_logs_sent_to_server(
    httpserver: HTTPServer,
    env_override: dict[str, str],
    command: Optional[str],
    expected_debug_log_tail: Optional[str],
    expected_error_log_tail: Optional[str],
) -> None:
    env = make_online_base_env(httpserver.port, command=command)
    env.update(env_override)

    wrapper = make_wrapped_mode_proc_wrapper(env=env)

    expect_task_execution_request(httpserver=httpserver, update=False)

    fetch_update_request_data = expect_task_execution_request(httpserver=httpserver)

    wrapper.run()

    httpserver.check_assertions()

    urd = fetch_update_request_data()

    if expected_debug_log_tail:
        assert urd["debug_log_tail"].index(expected_debug_log_tail) >= 0
    else:
        assert "debug_log_tail" not in urd

    if expected_error_log_tail:
        assert urd["error_log_tail"].index(expected_error_log_tail) >= 0
    else:
        assert "error_log_tail" not in urd


TEST_DATETIME = datetime(2021, 8, 16, 14, 26, 54, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    """
    headers, expected_delay_seconds
    """,
    [
        ({"Retry-After": "55"}, 55.0),
        (
            {
                "Retry-After": format_datetime(
                    TEST_DATETIME + timedelta(seconds=100), usegmt=True
                )
            },
            100.0,
        ),
        ({}, None),
    ],
)
@freeze_time(TEST_DATETIME)
def test_extract_retry_delay_seconds(
    headers: dict[str, str], expected_delay_seconds: float
) -> None:
    delay_seconds = ProcWrapper._extract_retry_delay_seconds(headers)

    if expected_delay_seconds is None:
        assert delay_seconds is None
    else:
        assert delay_seconds is not None
        assert abs(delay_seconds - expected_delay_seconds) <= 1.0


def callback(wrapper: ProcWrapper, cbdata: str, config: dict[str, str]) -> str:
    return "super" + cbdata


def test_embedded_offline_mode_success():
    env_override = {
        "PROC_WRAPPER_OFFLINE_MODE": "TRUE",
    }
    wrapper = ProcWrapper(env_override=env_override)
    assert wrapper.managed_call(callback, "duper") == "superduper"


def bad_callback(wrapper: ProcWrapper, cbdata: str, config: dict[str, str]) -> str:
    raise RuntimeError("Nope!")


def test_embedded_offline_mode_failure():
    env_override = {
        "PROC_WRAPPER_OFFLINE_MODE": "TRUE",
    }
    wrapper = ProcWrapper(env_override=env_override)

    try:
        wrapper.managed_call(bad_callback, "duper")
    except RuntimeError as err:
        assert str(err).find("Nope!") >= 0
    else:
        assert False


def callback_with_update(
    wrapper: ProcWrapper, cbdata: dict[str, Any], config: dict[str, str]
) -> str:
    failed_attempts = cbdata["failed_attempts"]
    last_app_heartbeat_at_override = cbdata["last_app_heartbeat_at_override"]

    if wrapper.failed_count < failed_attempts:
        raise RuntimeError("you failed this test")

    wrapper.update_status(
        success_count=4,
        error_count=5,
        skipped_count=6,
        expected_count=7,
        last_status_message="hello baby!",
        extra_status_props={"extra": "yo"},
        last_app_heartbeat_at=last_app_heartbeat_at_override,
    )

    return "noice!"


@pytest.mark.parametrize(
    """
    failed_attempts, last_app_heartbeat_at_override
    """,
    [
        (0, None),
        (1, None),
        (2, None),
        (0, datetime.now(timezone.utc) - relativedelta(minutes=3)),
        (1, datetime.now(timezone.utc) - relativedelta(minutes=10)),
    ],
)
def test_embedded_mode_with_server(
    failed_attempts: int,
    last_app_heartbeat_at_override: Optional[datetime],
    httpserver: HTTPServer,
):
    params = make_online_params(httpserver.port)
    wrapper = ProcWrapper(params=params)

    fetch_creation_request_data = expect_task_execution_request(
        httpserver=httpserver, update=False
    )

    reported_failures = failed_attempts

    failed_fetchers = []

    for i in range(reported_failures):
        fetch_failed_update_request_data = expect_task_execution_request(
            httpserver=httpserver
        )
        failed_fetchers.append(fetch_failed_update_request_data)

    should_succeed = failed_attempts <= params.process_max_retries

    fetch_update_request_data = None
    if failed_attempts == 0:
        fetch_update_request_data = expect_task_execution_request(httpserver=httpserver)

    fetch_final_update_request_data = expect_task_execution_request(
        httpserver=httpserver
    )

    cbdata = {
        "failed_attempts": failed_attempts,
        "last_app_heartbeat_at_override": last_app_heartbeat_at_override,
    }

    try:
        wrapper.managed_call(callback_with_update, cbdata) == "noice"
        assert should_succeed
    except RuntimeError as err:
        assert not should_succeed
        assert "you failed" in str(err)
    except Exception as ex:
        print(ex)
        assert False

    httpserver.check_assertions()

    crd = fetch_creation_request_data()
    assert crd["status"] == ProcWrapper.STATUS_RUNNING
    assert crd["is_service"] is False
    assert crd["wrapper_version"] == ProcWrapper.VERSION
    assert crd["wrapper_family"] == ProcWrapper.WRAPPER_FAMILY
    assert crd["embedded_mode"] is True
    task = crd["task"]
    assert task["uuid"] == DEFAULT_TASK_UUID

    for i, fetcher in enumerate(failed_fetchers):
        furd = fetcher()
        assert furd["failed_attempts"] == i + 1

    urd = {}
    if fetch_update_request_data:
        urd = fetch_update_request_data()

    furd = fetch_final_update_request_data()

    if not fetch_update_request_data:
        urd = furd

    assert (
        furd["status"] == ProcWrapper.STATUS_SUCCEEDED
        if should_succeed
        else ProcWrapper.STATUS_FAILED
    )

    assert urd["success_count"] == 4
    assert urd["error_count"] == 5
    assert urd["skipped_count"] == 6
    assert urd["expected_count"] == 7
    assert urd["last_status_message"] == "hello baby!"
    assert urd["extra"] == "yo"

    if should_succeed:
        assert furd.get("failed_attempts") is None
    else:
        assert furd["failed_attempts"] == failed_attempts

    last_app_heartbeat_at_str = urd[
        ProcWrapper._STATUS_UPDATE_KEY_LAST_APP_HEARTBEAT_AT
    ]
    last_app_heartbeat_at = datetime.fromisoformat(last_app_heartbeat_at_str)

    if last_app_heartbeat_at_override:
        assert (last_app_heartbeat_at_override - last_app_heartbeat_at).seconds <= 1
    else:
        assert (datetime.now(timezone.utc) - last_app_heartbeat_at).seconds < 10


def callback_with_params_from_config(
    wrapper: ProcWrapper, cbdata: int, config: dict[str, Any]
) -> int:
    return config["app_stuff"]["a"] + cbdata


def test_embedded_mode_with_params_from_config(httpserver: HTTPServer):
    port = httpserver.port

    task_name = "embedded_mode_with_params_from_config"

    config = {
        "proc_wrapper_params": {
            "task": {
                "name": task_name,
                "was_auto_created": True,
                "passive": True,
                "run_environment": {"name": "myenv"},
            },
            "log_level": "DEBUG",
            "api_base_url": f"http://localhost:{port}",
            "api_key": TEST_API_KEY,
        },
        "app_stuff": {"a": 42},
    }

    params = ProcWrapperParams()
    params.initial_config = config

    wrapper = ProcWrapper(params=params)

    assert params.api_base_url == f"http://localhost:{port}"
    assert params.api_key == TEST_API_KEY
    assert params.task_name == task_name
    assert params.log_level == "DEBUG"

    fetch_creation_request_data = expect_task_execution_request(
        httpserver=httpserver, update=False
    )

    fetch_final_update_request_data = expect_task_execution_request(
        httpserver=httpserver
    )

    assert wrapper.managed_call(callback_with_params_from_config, 58) == 100

    httpserver.check_assertions()

    crd = fetch_creation_request_data()
    assert crd["status"] == ProcWrapper.STATUS_RUNNING

    furd = fetch_final_update_request_data()
    assert furd["status"] == ProcWrapper.STATUS_SUCCEEDED


def callback_with_params_from_input(
    wrapper: ProcWrapper, cbdata: int, config: dict[str, str]
) -> int:
    return cbdata


@pytest.mark.parametrize(
    """
    input, expect_extraction
    """,
    [
        (None, False),
        ({}, False),
        (
            {
                "cloudreactor_context": {
                    "proc_wrapper_params": {
                        "task_execution": {"uuid": "UUID-FROM-INPUT"}
                    }
                }
            },
            True,
        ),
    ],
)
def test_embedded_mode_with_params_from_input(
    input: Optional[Any], expect_extraction: bool, httpserver: HTTPServer
):
    params = make_online_params(httpserver.port)
    wrapper = ProcWrapper(params=params, input_value=input)
    te_uuid = "UUID-FROM-INPUT"

    fetch_creation_request_data = expect_task_execution_request(
        httpserver=httpserver, update=expect_extraction, uuid=te_uuid
    )

    fetch_final_update_request_data = expect_task_execution_request(
        httpserver=httpserver, uuid=te_uuid
    )

    assert wrapper.managed_call(callback_with_params_from_input, 69) == 69

    httpserver.check_assertions()

    crd = fetch_creation_request_data()
    assert crd["status"] == ProcWrapper.STATUS_RUNNING

    furd = fetch_final_update_request_data()
    assert furd["status"] == ProcWrapper.STATUS_SUCCEEDED


def read_config_callback(
    wrapper: ProcWrapper, cbdata: str, config: dict[str, str]
) -> str:
    with open("conf.json", "r") as f:
        c = json.load(f)
        return c["b"] + cbdata


def test_embedded_offline_mode_with_var_writing():
    params = ProcWrapperParams()
    params.offline_mode = True
    params.config_output_filename = "conf.json"
    params.initial_config = {
        "a": {
            "d": "bc",
        },
        "b__to_resolve": "CONFIG:$.a.d",
    }

    wrapper = ProcWrapper(params=params)
    assert wrapper.managed_call(read_config_callback, "duper") == "bcduper"

    assert not os.path.exists(params.config_output_filename)


@pytest.mark.parametrize(
    """
    fail, report_failure_p
    """,
    [
        (False, 0.0),
        (True, 1.0),
        (True, 1e-9),
    ],
)
def test_embedded_mode_with_sampling(
    fail: bool, report_failure_p: float, httpserver: HTTPServer
):
    params = make_online_params(httpserver.port)
    params.api_managed_probability = 1e-9
    params.api_failure_report_probability = report_failure_p
    wrapper = ProcWrapper(params=params)

    if fail and (report_failure_p == 1.0):
        expect_task_execution_request(httpserver=httpserver, update=False)

    cb = bad_callback if fail else callback
    rv = None
    try:
        rv = wrapper.managed_call(cb, "duper")
    except RuntimeError as err:
        assert fail
        assert str(err).find("Nope!") >= 0
    else:
        assert not fail
        assert rv == "superduper"

    httpserver.check_assertions()


def bad_callback_with_logging(
    wrapper: ProcWrapper, cbdata: str, config: dict[str, str]
) -> str:
    logger = logging.getLogger("proc_wrapper_test")

    logger.info("This should be truncated")
    logger.info("This is an info message")
    logger.error("This is an error message")
    logger.debug("This is an debug message")
    logger.error("This is another error message")

    raise RuntimeError("Nope!")


def test_embedded_mode_with_log_capture(httpserver: HTTPServer):
    params = make_online_params(httpserver.port)
    params.process_max_retries = 0
    params.num_log_lines_sent_on_failure = 3

    wrapper = ProcWrapper(params=params)

    fetch_creation_request_data = expect_task_execution_request(
        httpserver=httpserver, update=False
    )

    fetch_update_request_data = expect_task_execution_request(httpserver=httpserver)

    logger = logging.getLogger("proc_wrapper_test")
    logger.setLevel(logging.INFO)
    log_handler = wrapper.get_embedded_logging_handler()
    log_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(log_handler)

    expect_task_execution_request(httpserver=httpserver, update=False)

    cb = bad_callback_with_logging
    try:
        wrapper.managed_call(cb, "duper")
    except RuntimeError:
        pass
    else:
        assert False

    crd = fetch_creation_request_data()
    assert crd["num_log_lines_sent_on_failure"] == 3

    urd = fetch_update_request_data()
    log_tail = urd.get("debug_log_tail")

    assert log_tail is not None
    assert "truncated" not in log_tail
    assert "an info message\n" in log_tail
    assert "debug message" not in log_tail
    assert "another error message" in log_tail

    httpserver.check_assertions()


def callback_with_env_in_config(
    wrapper: ProcWrapper, cbdata: str, config: dict[str, Any]
) -> str:
    return "super" + cbdata + config["ENV"]["ANOTHER_ENV"]


def test_env_pass_through():
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override["ANOTHER_ENV"] = "250"
    env_override["PROC_WRAPPER_OFFLINE_MODE"] = "1"

    wrapper = ProcWrapper(env_override=env_override)
    process_env = wrapper.make_process_env()

    assert process_env["ANOTHER_ENV"] == "250"

    assert wrapper.managed_call(callback_with_env_in_config, "duper") == "superduper250"


@pytest.mark.parametrize(
    """
    auto_create
    """,
    [(True), (False)],
)
def test_ecs_runtime_metadata(auto_create: bool, httpserver: HTTPServer):
    env_override = make_online_base_env(httpserver.port)

    if auto_create:
        env_override["PROC_WRAPPER_AUTO_CREATE_TASK"] = "TRUE"
        env_override["PROC_WRAPPER_AUTO_CREATE_TASK_RUN_ENVIRONMENT_NAME"] = "myenv"

    env_override[
        "ECS_CONTAINER_METADATA_URI"
    ] = f"http://localhost:{httpserver.port}/aws/ecs"

    (
        ecs_task_metadata_handler,
        _fetch_ecs_task_metadata_request_data,
    ) = make_capturing_handler(response_data=TEST_ECS_TASK_METADATA, status=200)

    httpserver.expect_request(
        "/aws/ecs/task", method="GET", headers=ACCEPT_JSON_HEADERS
    ).respond_with_handler(ecs_task_metadata_handler)

    (
        ecs_container_metadata_handler,
        _fetch_ecs_container_metadata_request_data,
    ) = make_capturing_handler(response_data=TEST_ECS_CONTAINER_METADATA, status=200)

    httpserver.expect_request(
        "/aws/ecs", method="GET", headers=ACCEPT_JSON_HEADERS
    ).respond_with_handler(ecs_container_metadata_handler)

    fetch_creation_request_data = expect_task_execution_request(
        httpserver=httpserver, update=False
    )

    expect_task_execution_request(httpserver=httpserver)

    wrapper = make_wrapped_mode_proc_wrapper(env_override)
    wrapper.run()

    httpserver.check_assertions()

    crd = fetch_creation_request_data()

    assert crd["execution_method_type"] == EXECUTION_METHOD_TYPE_AWS_ECS
    em = crd["execution_method_details"]

    assert em["task_arn"] == TEST_ECS_TASK_METADATA["TaskARN"]
    assert (
        em["task_definition_arn"]
        == "arn:aws:ecs:us-east-2:012345678910:task-definition/nginx:5"
    )
    assert em["cluster_arn"] == TEST_ECS_TASK_METADATA["Cluster"]
    assert em["allocated_cpu_units"] == 256
    assert em["allocated_memory_mb"] == 512

    task_dict = crd["task"]

    if auto_create:
        assert task_dict["was_auto_created"] is True

        # Defaults to the value of auto-created
        assert task_dict["passive"] is True

        assert task_dict["run_environment"]["name"] == "myenv"
        assert task_dict["execution_method_type"] == EXECUTION_METHOD_TYPE_AWS_ECS
        emc = task_dict["execution_method_capability_details"]
        assert emc["task_definition_arn"] == em["task_definition_arn"]
        assert emc["cluster_arn"] == TEST_ECS_TASK_METADATA["Cluster"]
        assert emc["allocated_cpu_units"] == 256
        assert emc["allocated_memory_mb"] == 512
    else:
        assert task_dict["was_auto_created"] is not True
        assert task_dict["passive"] is not True

    for x in [crd, task_dict]:
        assert x["infrastructure_type"] == "AWS"
        aws = x["infrastructure_settings"]
        assert aws is not None
        aws_network = aws["network"]
        assert aws_network["region"] == "us-east-2"

        nw_0 = aws_network["networks"][0]
        assert nw_0["network_mode"] == "awsvpc"


def test_aws_lambda_metadata(httpserver: HTTPServer):
    params = make_online_params(httpserver.port)
    params.auto_create_task = True
    params.task_is_passive = True
    params.auto_create_task_run_environment_name = "stage"

    env = make_fake_aws_lambda_env()
    context = FakeAwsLambdaContext()

    wrapper = ProcWrapper(params=params, env_override=env, runtime_context=context)

    fetch_creation_request_data = expect_task_execution_request(
        httpserver=httpserver, update=False
    )
    fetch_final_update_request_data = expect_task_execution_request(
        httpserver=httpserver
    )

    cbdata = "yo"

    wrapper.managed_call(callback, cbdata) == "superyo"

    httpserver.check_assertions()

    crd = fetch_creation_request_data()
    assert crd["status"] == ProcWrapper.STATUS_RUNNING
    assert crd["is_service"] is False
    assert crd["wrapper_version"] == ProcWrapper.VERSION
    assert crd["wrapper_family"] == ProcWrapper.WRAPPER_FAMILY
    assert crd["embedded_mode"] is True
    task = crd["task"]
    assert task["uuid"] == DEFAULT_TASK_UUID

    for x in [crd, task]:
        assert x["allocated_memory_mb"] == 4096
        assert x["execution_method_type"] == EXECUTION_METHOD_TYPE_AWS_LAMBDA
        assert x["infrastructure_type"] == "AWS"
        aws = x["infrastructure_settings"]
        assert aws is not None
        aws_network = aws["network"]
        assert aws_network["region"] == "us-east-2"

    em = crd.get("execution_method_details")
    emc = task.get("execution_method_capability_details")

    for x in [em, emc]:
        assert x["function_name"] == "do_it_now"
        assert x["function_version"] == "3.3.7"
        assert x["function_memory_mb"] == 4096
        assert (
            x["function_arn"] == "arn:aws:lambda:us-east-2:123456789012:function:funky"
        )

    furd = fetch_final_update_request_data()

    assert furd["status"] == ProcWrapper.STATUS_SUCCEEDED


def test_passive_auto_created_task_with_unknown_em(httpserver: HTTPServer):
    env_override = make_online_base_env(httpserver.port)
    env_override["PROC_WRAPPER_AUTO_CREATE_TASK"] = "TRUE"
    env_override["PROC_WRAPPER_AUTO_CREATE_TASK_RUN_ENVIRONMENT_NAME"] = "myenv"

    fetch_creation_request_data = expect_task_execution_request(
        httpserver=httpserver, update=False
    )

    expect_task_execution_request(httpserver=httpserver)

    wrapper = make_wrapped_mode_proc_wrapper(env_override)
    wrapper.run()

    httpserver.check_assertions()

    crd = fetch_creation_request_data()
    assert crd.get("execution_method_details") is None

    task_dict = crd["task"]
    assert task_dict.get("execution_method_type") == "Unknown"
    assert task_dict.get("execution_method_details") is None
    assert task_dict["was_auto_created"] is True

    # Defaults to the value of auto-created
    assert task_dict["passive"] is True
