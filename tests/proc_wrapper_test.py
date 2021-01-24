import json
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Request, Response

from proc_wrapper import ProcWrapper

TEST_API_PORT = 6777
TEST_API_KEY = 'SOMEAPIKEY'
DEFAULT_TASK_UUID = '13b4cfbc-6ed5-4fd5-85e8-73e84e2f1b82'
DEFAULT_TASK_EXECUTION_UUID = 'd9554f00-eaeb-4a16-96e4-9adda91a2750'

CLIENT_HEADERS = {
    'Authorization': f'Token {TEST_API_KEY}',
    'Content-Type': 'application/json',
    'Accept': 'application/json',
}

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
        captured_request_data[0] = json.loads(request.data)
        return Response(json.dumps(response_data), status, None, content_type='application/json')

    def fetch_captured_request_data() -> Optional[Dict[str, Any]]:
        return captured_request_data[0]

    return handler, fetch_captured_request_data


def test_wrapped_offline_mode():
    args = ProcWrapper.make_arg_parser().parse_args(['echo'])

    env_override = {
        'PROC_WRAPPER_LOG_LEVEL': 'DEBUG',
        'PROC_WRAPPER_OFFLINE_MODE': 'TRUE',
    }
    proc_wrapper = ProcWrapper(args=args, env_override=env_override,
            embedded_mode=False)
    proc_wrapper.run()


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
    proc_wrapper = ProcWrapper(args=args, env_override=env_override,
            embedded_mode=False)
    proc_wrapper.run()

    httpserver.check_assertions()

    crd = fetch_creation_request_data()
    assert crd['status'] == ProcWrapper.STATUS_RUNNING
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
    proc_wrapper = ProcWrapper(env_override=env_override)
    assert proc_wrapper.managed_call(callback, 'duper') == 'superduper'


def bad_callback(wrapper: ProcWrapper, cbdata: str,
        config: Dict[str, str]) -> str:
    raise RuntimeError('Nope!')


def test_embedded_offline_mode_failure():
    env_override = {
        'PROC_WRAPPER_OFFLINE_MODE': 'TRUE',
    }
    proc_wrapper = ProcWrapper(env_override=env_override)

    try:
        proc_wrapper.managed_call(bad_callback, 'duper')
    except RuntimeError as err:
        assert str(err).find('Nope!') >= 0
    else:
        assert False


def test_resolve_env_with_json_path():
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override['SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = '{"a": "bug"}|JP:$.a'
    proc_wrapper = ProcWrapper(env_override=env_override)
    assert proc_wrapper.make_process_env()['SOME_ENV'] == 'bug'


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

        proc_wrapper = ProcWrapper(env_override=env_override)
        process_env = proc_wrapper.make_process_env()
        assert process_env['SOME_ENV'] == 'Secret PW'
        assert process_env['ANOTHER_ENV'] == 'Secret PW 2'


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

        proc_wrapper = ProcWrapper(env_override=env_override)
        process_env = proc_wrapper.make_process_env()

        assert process_env['SOME_ENV'] == 'food'
        assert process_env['ANOTHER_ENV'] == '250'
        assert process_env['YET_ANOTHER_ENV'] == 'FALSE'

        assert proc_wrapper.managed_call(callback_with_config,
                'duper') == 'superduper250'
