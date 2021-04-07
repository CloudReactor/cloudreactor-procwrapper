import json
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import pytest

from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Request, Response

from proc_wrapper import ProcWrapper

TEST_API_PORT = 6777
TEST_API_KEY = 'SOMEAPIKEY'
DEFAULT_TASK_UUID = '13b4cfbc-6ed5-4fd5-85e8-73e84e2f1b82'
DEFAULT_TASK_EXECUTION_UUID = 'd9554f00-eaeb-4a16-96e4-9adda91a2750'
DEFAULT_TASK_VERSION_SIGNATURE = '43cfd2b905d5cb4f2e8fc941c7a1289002be9f7f'

ACCEPT_JSON_HEADERS = {
    'Accept': 'application/json',
}

CLIENT_HEADERS = {**ACCEPT_JSON_HEADERS, **{
    'Authorization': f'Token {TEST_API_KEY}',
    'Content-Type': 'application/json',
}}

RESOLVE_ENV_BASE_ENV = {
    'PROC_WRAPPER_TASK_NAME': 'Foo',
    'PROC_WRAPPER_API_KEY': 'XXX',
    'PROC_WRAPPER_RESOLVE_SECRETS': 'TRUE',
    'PROC_WRAPPER_SECRETS_AWS_REGION': 'us-east-2',
}


def make_online_base_env(port: int) -> Dict[str, str]:
    return {
        'PROC_WRAPPER_LOG_LEVEL': 'DEBUG',
        'PROC_WRAPPER_TASK_UUID': DEFAULT_TASK_UUID,
        'PROC_WRAPPER_TASK_VERSION_SIGNATURE': DEFAULT_TASK_VERSION_SIGNATURE,
        'PROC_WRAPPER_API_BASE_URL': f'http://localhost:{port}',
        'PROC_WRAPPER_API_KEY': TEST_API_KEY,
        'PROC_WRAPPER_API_TASK_CREATION_ERROR_TIMEOUT_SECONDS': '1',
        'PROC_WRAPPER_API_TASK_CREATION_CONFLICT_TIMEOUT_SECONDS': '1',
        'PROC_WRAPPER_API_TASK_CREATION_CONFLICT_RETRY_DELAY_SECONDS': '1',
        'PROC_WRAPPER_API_FINAL_UPDATE_TIMEOUT_SECONDS': '1',
        'PROC_WRAPPER_API_RETRY_DELAY_SECONDS': '1',
        'PROC_WRAPPER_API_RESUME_DELAY_SECONDS': '-1',
    }


def make_capturing_handler(response_data: Dict[str, Any],
        status: int = 200):
    captured_request_data: List[Optional[Dict[str, Any]]] = [None]

    def handler(request: Request) -> Response:
        if request.data:
            captured_request_data[0] = json.loads(request.data)

        return Response(json.dumps(response_data), status, None,
                content_type='application/json')

    def fetch_captured_request_data() -> Optional[Dict[str, Any]]:
        return captured_request_data[0]

    return handler, fetch_captured_request_data


def test_wrapped_offline_mode():
    args = ProcWrapper.make_arg_parser().parse_args(['echo'])

    env_override = {
        'PROC_WRAPPER_LOG_LEVEL': 'DEBUG',
        'PROC_WRAPPER_OFFLINE_MODE': 'TRUE',
    }
    wrapper = ProcWrapper(args=args, env_override=env_override,
            embedded_mode=False)
    wrapper.run()


def test_wrapped_mode_with_server(httpserver: HTTPServer):
    env_override = make_online_base_env(httpserver.port)

    creation_handler, fetch_creation_request_data = make_capturing_handler(
            response_data={
                'uuid': DEFAULT_TASK_EXECUTION_UUID
            }, status=201)

    httpserver.expect_ordered_request('/api/v1/task_executions/',
            method='POST', headers=CLIENT_HEADERS) \
            .respond_with_handler(creation_handler)

    update_handler, fetch_update_request_data = make_capturing_handler(
            response_data={}, status=200)

    httpserver.expect_ordered_request(
            '/api/v1/task_executions/' + quote_plus(DEFAULT_TASK_EXECUTION_UUID) + '/',
            method='PATCH', headers=CLIENT_HEADERS) \
            .respond_with_handler(update_handler)

    args = ProcWrapper.make_arg_parser().parse_args(['echo'])
    wrapper = ProcWrapper(args=args, env_override=env_override,
            embedded_mode=False)
    wrapper.run()

    httpserver.check_assertions()

    crd = fetch_creation_request_data()
    assert crd['status'] == ProcWrapper.STATUS_RUNNING
    assert crd['is_service'] is False
    assert crd['wrapper_version'] == ProcWrapper.VERSION
    assert crd['embedded_mode'] is False
    task = crd['task']
    assert task['uuid'] == DEFAULT_TASK_UUID

    urd = fetch_update_request_data()
    assert urd['status'] == ProcWrapper.STATUS_SUCCEEDED
    assert urd['exit_code'] == 0
    assert urd['failed_attempts'] == 0
    assert urd['timed_out_attempts'] == 0


def callback(wrapper: ProcWrapper, cbdata: str,
        config: Dict[str, str]) -> str:
    return 'super' + cbdata


def test_embedded_offline_mode_success():
    env_override = {
        'PROC_WRAPPER_OFFLINE_MODE': 'TRUE',
    }
    wrapper = ProcWrapper(env_override=env_override)
    assert wrapper.managed_call(callback, 'duper') == 'superduper'


def bad_callback(wrapper: ProcWrapper, cbdata: str,
        config: Dict[str, str]) -> str:
    raise RuntimeError('Nope!')


def test_embedded_offline_mode_failure():
    env_override = {
        'PROC_WRAPPER_OFFLINE_MODE': 'TRUE',
    }
    wrapper = ProcWrapper(env_override=env_override)

    try:
        wrapper.managed_call(bad_callback, 'duper')
    except RuntimeError as err:
        assert str(err).find('Nope!') >= 0
    else:
        assert False


def test_resolve_env_with_json_path():
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override['SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = '{"a": "bug"}|JP:$.a'
    wrapper = ProcWrapper(env_override=env_override)
    assert wrapper.make_process_env()['SOME_ENV'] == 'bug'


def put_aws_sm_secret(sm_client, name: str, value: str) -> str:
    return sm_client.create_secret(
        Name=name,
        SecretString=value,
    )['ARN']


def test_resolve_env_with_aws_secrets_manager():
    import boto3
    from moto import mock_secretsmanager

    env_override = RESOLVE_ENV_BASE_ENV.copy()

    with mock_secretsmanager():
        sm = boto3.client('secretsmanager', region_name='us-east-2')
        secret_arn = put_aws_sm_secret(sm, 'mypass', 'Secret PW')

        env_override['AWS_SM_SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = secret_arn

        secret_arn = put_aws_sm_secret(sm, 'anotherpass', 'Secret PW 2')

        env_override['AWS_SM_ANOTHER_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = secret_arn

        wrapper = ProcWrapper(env_override=env_override)
        process_env = wrapper.make_process_env()
        assert process_env['SOME_ENV'] == 'Secret PW'
        assert process_env['ANOTHER_ENV'] == 'Secret PW 2'


def test_resolve_env_with_env_reference():
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override['ENV_SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = 'ANOTHER_VAR'
    env_override['ANOTHER_VAR'] = 'env resolution works'

    wrapper = ProcWrapper(env_override=env_override)
    process_env = wrapper.make_process_env()
    assert process_env['SOME_ENV'] == 'env resolution works'


def test_resolve_env_with_env_reference_and_json_path():
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override['ENV_SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = 'ANOTHER_VAR|JP:$.password'
    env_override['ANOTHER_VAR'] = '{"password": "foobar"}'

    wrapper = ProcWrapper(env_override=env_override)
    process_env = wrapper.make_process_env()
    assert process_env['SOME_ENV'] == 'foobar'


def callback_with_config(wrapper: ProcWrapper, cbdata: str,
        config: Dict[str, str]) -> str:
    return 'super' + cbdata + config['ANOTHER_ENV']


def test_resolve_env_with_aws_secrets_manager_and_json_path():
    import boto3
    from moto import mock_secretsmanager

    env_override = RESOLVE_ENV_BASE_ENV.copy()

    with mock_secretsmanager():
        sm = boto3.client('secretsmanager', region_name='us-east-2')

        secret_arn = put_aws_sm_secret(sm, 'config', '{"a": "food", "b": [false, 250]}')
        env_override['PROC_WRAPPER_OFFLINE_MODE'] = 'TRUE'
        env_override['AWS_SM_SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = \
                secret_arn + '|JP:$.a'
        env_override['AWS_SM_ANOTHER_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = \
                secret_arn + '|JP:$.b[1]'

        secret_arn = put_aws_sm_secret(sm, 'details', '{"b": {"c": false}}')
        env_override['AWS_SM_YET_ANOTHER_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = \
                secret_arn + '|JP:$.b.c'

        wrapper = ProcWrapper(env_override=env_override)
        process_env = wrapper.make_process_env()

        assert process_env['SOME_ENV'] == 'food'
        assert process_env['ANOTHER_ENV'] == '250'
        assert process_env['YET_ANOTHER_ENV'] == 'FALSE'

        assert wrapper.managed_call(callback_with_config,
                'duper') == 'superduper250'


@pytest.mark.parametrize("""
  auto_create
""", [
    (True),
    (False)
])
def test_ecs_runtime_metadata(auto_create: bool, httpserver: HTTPServer):
    env_override = make_online_base_env(httpserver.port)

    if auto_create:
        env_override['PROC_WRAPPER_AUTO_CREATE_TASK'] = 'TRUE'
        env_override['PROC_WRAPPER_AUTO_CREATE_TASK_RUN_ENVIRONMENT_NAME'] = 'myenv'

    env_override['ECS_CONTAINER_METADATA_URI'] = f'http://localhost:{httpserver.port}/aws/ecs'

    ecs_task_metadata = {
        "Cluster": "default",
        "TaskARN": "arn:aws:ecs:us-east-2:012345678910:task/9781c248-0edd-4cdb-9a93-f63cb662a5d3",
        "Family": "nginx",
        "Revision": "5",
        "DesiredStatus": "RUNNING",
        "KnownStatus": "RUNNING",
        "Limits": {
            "CPU": 0.25,
            "Memory": 512
        },
        "Containers": [
            {
                "DockerId": "731a0d6a3b4210e2448339bc7015aaa79bfe4fa256384f4102db86ef94cbbc4c",
                "Name": "~internal~ecs~pause",
                "DockerName": "ecs-nginx-5-internalecspause-acc699c0cbf2d6d11700",
                "Image": "amazon/amazon-ecs-pause:0.1.0",
                "ImageID": "",
                "Labels": {
                    "com.amazonaws.ecs.cluster": "default",
                    "com.amazonaws.ecs.container-name": "~internal~ecs~pause",
                    "com.amazonaws.ecs.task-arn": "arn:aws:ecs:us-east-2:012345678910:task/9781c248-0edd-4cdb-9a93-f63cb662a5d3",
                    "com.amazonaws.ecs.task-definition-family": "nginx",
                    "com.amazonaws.ecs.task-definition-version": "5"
                },
                "DesiredStatus": "RESOURCES_PROVISIONED",
                "KnownStatus": "RESOURCES_PROVISIONED",
                "Limits": {
                    "CPU": 0,
                    "Memory": 0
                },
                "CreatedAt": "2018-02-01T20:55:08.366329616Z",
                "StartedAt": "2018-02-01T20:55:09.058354915Z",
                "Type": "CNI_PAUSE",
                "Networks": [
                    {
                        "NetworkMode": "awsvpc",
                        "IPv4Addresses": [
                            "10.0.2.106"
                        ]
                    }
                ]
            },
            {
                "DockerId": "43481a6ce4842eec8fe72fc28500c6b52edcc0917f105b83379f88cac1ff3946",
                "Name": "nginx-curl",
                "DockerName": "ecs-nginx-5-nginx-curl-ccccb9f49db0dfe0d901",
                "Image": "nrdlngr/nginx-curl",
                "ImageID": "sha256:2e00ae64383cfc865ba0a2ba37f61b50a120d2d9378559dcd458dc0de47bc165",
                "Labels": {
                    "com.amazonaws.ecs.cluster": "default",
                    "com.amazonaws.ecs.container-name": "nginx-curl",
                    "com.amazonaws.ecs.task-arn": "arn:aws:ecs:us-east-2:012345678910:task/9781c248-0edd-4cdb-9a93-f63cb662a5d3",
                    "com.amazonaws.ecs.task-definition-family": "nginx",
                    "com.amazonaws.ecs.task-definition-version": "5"
                },
                "DesiredStatus": "RUNNING",
                "KnownStatus": "RUNNING",
                "Limits": {
                    "CPU": 512,
                    "Memory": 512
                },
                "CreatedAt": "2018-02-01T20:55:10.554941919Z",
                "StartedAt": "2018-02-01T20:55:11.064236631Z",
                "Type": "NORMAL",
                "Networks": [
                    {
                        "NetworkMode": "awsvpc",
                        "IPv4Addresses": [
                            "10.0.2.106"
                        ]
                    }
                ]
            }
        ],
        "PullStartedAt": "2018-02-01T20:55:09.372495529Z",
        "PullStoppedAt": "2018-02-01T20:55:10.552018345Z",
        "AvailabilityZone": "us-east-2b"
    }

    ecs_metadata_handler, fetch_ecs_metadata_request_data = make_capturing_handler(
            response_data=ecs_task_metadata, status=200)

    httpserver.expect_ordered_request('/aws/ecs/task',
            method='GET', headers=ACCEPT_JSON_HEADERS) \
            .respond_with_handler(ecs_metadata_handler)

    creation_handler, fetch_creation_request_data = make_capturing_handler(
            response_data={
                'uuid': DEFAULT_TASK_EXECUTION_UUID
            }, status=201)

    httpserver.expect_ordered_request('/api/v1/task_executions/',
            method='POST', headers=CLIENT_HEADERS) \
            .respond_with_handler(creation_handler)

    update_handler, fetch_update_request_data = make_capturing_handler(
            response_data={}, status=200)

    httpserver.expect_ordered_request(
            '/api/v1/task_executions/' + quote_plus(DEFAULT_TASK_EXECUTION_UUID) + '/',
            method='PATCH', headers=CLIENT_HEADERS) \
            .respond_with_handler(update_handler)

    args = ProcWrapper.make_arg_parser().parse_args(['echo'])
    wrapper = ProcWrapper(args=args, env_override=env_override,
            embedded_mode=False)
    wrapper.run()

    httpserver.check_assertions()

    crd = fetch_creation_request_data()
    em = crd['execution_method']
    assert em['type'] == 'AWS ECS'
    assert em['task_arn'] == ecs_task_metadata['TaskARN']
    assert em['task_definition_arn'] == \
            'arn:aws:ecs:us-east-2:012345678910:task-definition/nginx:5'
    assert em['cluster_arn'] == ecs_task_metadata['Cluster']
    assert em['allocated_cpu_units'] == 256
    assert em['allocated_memory_mb'] == 512

    task_dict = crd['task']

    if auto_create:
        assert task_dict['was_auto_created'] is True

        # Defaults to the value of auto-created
        assert task_dict['passive'] is True

        assert task_dict['run_environment']['name'] == 'myenv'
        emc = task_dict['execution_method_capability']
        assert emc['type'] == 'AWS ECS'
        assert emc['task_definition_arn'] == em['task_definition_arn']
        assert emc['default_cluster_arn'] == ecs_task_metadata['Cluster']
        assert emc['allocated_cpu_units'] == 256
        assert emc['allocated_memory_mb'] == 512
    else:
        assert task_dict['was_auto_created'] is not True
        assert task_dict['passive'] is not True


def test_passive_auto_created_task_with_unknown_em(httpserver: HTTPServer):
    env_override = make_online_base_env(httpserver.port)
    env_override['PROC_WRAPPER_AUTO_CREATE_TASK'] = 'TRUE'
    env_override['PROC_WRAPPER_AUTO_CREATE_TASK_RUN_ENVIRONMENT_NAME'] = 'myenv'

    creation_handler, fetch_creation_request_data = make_capturing_handler(
            response_data={
                'uuid': DEFAULT_TASK_EXECUTION_UUID
            }, status=201)

    httpserver.expect_ordered_request('/api/v1/task_executions/',
            method='POST', headers=CLIENT_HEADERS) \
            .respond_with_handler(creation_handler)

    update_handler, fetch_update_request_data = make_capturing_handler(
            response_data={}, status=200)

    httpserver.expect_ordered_request(
            '/api/v1/task_executions/' + quote_plus(DEFAULT_TASK_EXECUTION_UUID) + '/',
            method='PATCH', headers=CLIENT_HEADERS) \
            .respond_with_handler(update_handler)

    args = ProcWrapper.make_arg_parser().parse_args(['echo'])
    wrapper = ProcWrapper(args=args, env_override=env_override,
            embedded_mode=False)
    wrapper.run()

    httpserver.check_assertions()

    crd = fetch_creation_request_data()
    em = crd.get('execution_method')
    assert em is None

    task_dict = crd['task']

    assert task_dict['was_auto_created'] is True

    # Defaults to the value of auto-created
    assert task_dict['passive'] is True

    emc = task_dict.get('execution_method_capability')
    assert emc['type'] == 'Unknown'

def test_rollbar_config():
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override['PROC_WRAPPER_ROLLBAR_ACCESS_TOKEN'] = 'rbtoken'
    env_override['PROC_WRAPPER_ROLLBAR_RETRIES'] = '3'
    env_override['PROC_WRAPPER_ROLLBAR_RETRY_DELAY_SECONDS'] = '10'
    env_override['PROC_WRAPPER_ROLLBAR_RETRY_TIMEOUT_SECONDS'] = '30'
    wrapper = ProcWrapper(env_override=env_override)
    assert wrapper.rollbar_access_token == 'rbtoken'
    assert wrapper.rollbar_retries == 3
    assert wrapper.rollbar_retry_delay == 10
    assert wrapper.rollbar_timeout == 30
    assert wrapper.rollbar_retries_exhausted is False
