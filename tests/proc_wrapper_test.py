from datetime import datetime
from typing import Any, Dict, Mapping, Optional
from urllib.parse import quote_plus

import pytest
from dateutil.relativedelta import relativedelta
from pytest_httpserver import HTTPServer

from proc_wrapper import ProcWrapper, ProcWrapperParams, make_arg_parser
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


def make_wrapped_mode_proc_wrapper(env: Mapping[str, str]) -> ProcWrapper:
    main_parser = make_arg_parser()
    params = main_parser.parse_args(
        args=[], namespace=ProcWrapperParams(embedded_mode=False)
    )
    return ProcWrapper(params=params, env_override=env, override_params_from_env=True)


def make_online_base_env(port: int, command: Optional[str] = "echo") -> Dict[str, str]:
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


def test_wrapped_offline_mode():
    env_override = {
        "PROC_WRAPPER_LOG_LEVEL": "DEBUG",
        "PROC_WRAPPER_OFFLINE_MODE": "TRUE",
        "PROC_WRAPPER_TASK_COMMAND": "echo",
    }

    wrapper = make_wrapped_mode_proc_wrapper(env=env_override)
    assert wrapper.run() == 0


def expect_task_execution_request(
    httpserver: HTTPServer,
    response_data: Optional[Dict[str, Any]] = None,
    status: Optional[int] = None,
    update: bool = True,
    uuid: Optional[str] = None,
):
    method = "PATCH" if update else "POST"
    url = "/api/v1/task_executions/"
    uuid = uuid or DEFAULT_TASK_EXECUTION_UUID

    if response_data is None:
        if update:
            response_data = {}
        else:
            response_data = {
                "uuid": uuid,
                "task": {"uuid": DEFAULT_TASK_UUID, "name": "A Task"},
            }

    if status is None:
        expected_status = 200 if update else 201
    else:
        expected_status = status

    if update:
        url += quote_plus(uuid) + "/"

    handler, fetch_captured_request_data = make_capturing_handler(
        response_data=response_data, status=expected_status
    )

    print(f"Expect order request to {url}")

    httpserver.expect_ordered_request(
        url, method=method, headers=CLIENT_HEADERS
    ).respond_with_handler(handler)

    return fetch_captured_request_data


@pytest.mark.parametrize(
    """
    env_override, command, expected_exit_code, expect_api_server_use
    """,
    [
        ({}, "echo", 0, True),
        ({}, None, ProcWrapper._EXIT_CODE_CONFIGURATION_ERROR, False),
    ],
)
def test_wrapped_mode_with_server(
    httpserver: HTTPServer,
    env_override: Dict[str, str],
    command: Optional[str],
    expected_exit_code: int,
    expect_api_server_use: bool,
):
    env = make_online_base_env(httpserver.port, command=command)
    env.update(env_override)

    wrapper = make_wrapped_mode_proc_wrapper(env=env)

    if expect_api_server_use:
        fetch_creation_request_data = expect_task_execution_request(
            httpserver=httpserver, update=False
        )

        fetch_update_request_data = expect_task_execution_request(httpserver=httpserver)

    assert wrapper.run() == expected_exit_code

    if expect_api_server_use:
        httpserver.check_assertions()

        crd = fetch_creation_request_data()
        assert crd["status"] == ProcWrapper.STATUS_RUNNING
        assert crd["is_service"] is False
        assert crd["wrapper_version"] == ProcWrapper.VERSION
        assert crd["wrapper_family"] == ProcWrapper.WRAPPER_FAMILY
        assert crd["embedded_mode"] is False
        task = crd["task"]
        assert task["uuid"] == DEFAULT_TASK_UUID

        urd = fetch_update_request_data()
        assert urd["status"] == ProcWrapper.STATUS_SUCCEEDED
        assert urd.get("failed_attempts") is None
        assert urd.get("timed_out_attempts") is None

        finished_at_str = urd["finished_at"]
        finished_at = datetime.fromisoformat(finished_at_str)
        assert (datetime.now() - finished_at).seconds < 10


def callback(wrapper: ProcWrapper, cbdata: str, config: Dict[str, str]) -> str:
    return "super" + cbdata


def test_embedded_offline_mode_success():
    env_override = {
        "PROC_WRAPPER_OFFLINE_MODE": "TRUE",
    }
    wrapper = ProcWrapper(env_override=env_override)
    assert wrapper.managed_call(callback, "duper") == "superduper"


def bad_callback(wrapper: ProcWrapper, cbdata: str, config: Dict[str, str]) -> str:
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
    wrapper: ProcWrapper, cbdata: Dict[str, Any], config: Dict[str, str]
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
        (0, datetime.utcnow() - relativedelta(minutes=3)),
        (1, datetime.utcnow() - relativedelta(minutes=10)),
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
        assert str(err).find("you failed") >= 0
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
        assert (datetime.now() - last_app_heartbeat_at).seconds < 10


def callback_with_params_from_config(
    wrapper: ProcWrapper, cbdata: int, config: Dict[str, Any]
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
    wrapper: ProcWrapper, cbdata: int, config: Dict[str, str]
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


def callback_with_env_in_config(
    wrapper: ProcWrapper, cbdata: str, config: Dict[str, Any]
) -> str:
    return "super" + cbdata + config["ENV"]["ANOTHER_ENV"]


def test_env_pass_through():
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override["ANOTHER_ENV"] = "250"

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

    httpserver.expect_ordered_request(
        "/aws/ecs/task", method="GET", headers=ACCEPT_JSON_HEADERS
    ).respond_with_handler(ecs_task_metadata_handler)

    (
        ecs_container_metadata_handler,
        _fetch_ecs_container_metadata_request_data,
    ) = make_capturing_handler(response_data=TEST_ECS_CONTAINER_METADATA, status=200)

    httpserver.expect_ordered_request(
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
    assert task_dict.get("execution_method_type") is None
    assert task_dict.get("execution_method_details") is None
    assert task_dict["was_auto_created"] is True

    # Defaults to the value of auto-created
    assert task_dict["passive"] is True
