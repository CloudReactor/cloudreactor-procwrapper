from typing import Dict, Mapping

from urllib.parse import quote_plus

import pytest

from pytest_httpserver import HTTPServer

from proc_wrapper import (
    ConfigResolver,
    ProcWrapper, ProcWrapperParams,
    RuntimeMetadataFetcher,
    make_arg_parser
)

from .test_commons import (
    ACCEPT_JSON_HEADERS, TEST_ECS_TASK_METADATA,
    make_capturing_handler
)

TEST_API_PORT = 6777
TEST_API_KEY = 'SOMEAPIKEY'
DEFAULT_TASK_UUID = '13b4cfbc-6ed5-4fd5-85e8-73e84e2f1b82'
DEFAULT_TASK_EXECUTION_UUID = 'd9554f00-eaeb-4a16-96e4-9adda91a2750'
DEFAULT_TASK_VERSION_SIGNATURE = '43cfd2b905d5cb4f2e8fc941c7a1289002be9f7f'

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

def make_wrapped_mode_proc_wrapper(env: Mapping[str, str]) -> ProcWrapper:
    main_parser = make_arg_parser(require_command=True)
    params = main_parser.parse_args(args=['echo'], namespace=ProcWrapperParams(
        embedded_mode=False))
    runtime_metadata_fetcher = RuntimeMetadataFetcher()
    runtime_metadata = runtime_metadata_fetcher.fetch(env=env)
    params.override_resolver_params_from_env(env=env)

    config_resolver = ConfigResolver(params=params,
        runtime_metadata=runtime_metadata,
        env_override=env)

    resolved_env, _failed_var_names = config_resolver.fetch_and_resolve_env()

    params.override_proc_wrapper_params_from_env(resolved_env,
            mutable_only=False, runtime_metadata=runtime_metadata)

    return ProcWrapper(params=params,
            runtime_metadata_fetcher=runtime_metadata_fetcher,
            config_resolver=config_resolver)

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


def test_wrapped_offline_mode():
    env_override = {
        'PROC_WRAPPER_LOG_LEVEL': 'DEBUG',
        'PROC_WRAPPER_OFFLINE_MODE': 'TRUE',
    }

    wrapper = make_wrapped_mode_proc_wrapper(env=env_override)
    wrapper.run()


def test_wrapped_mode_with_server(httpserver: HTTPServer):
    env_override = make_online_base_env(httpserver.port)

    creation_handler, fetch_creation_request_data = make_capturing_handler(
            response_data={
                'uuid': DEFAULT_TASK_EXECUTION_UUID,
                'task': {
                    'name': 'atask',
                    'uuid': DEFAULT_TASK_UUID
                }
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

    wrapper = make_wrapped_mode_proc_wrapper(env=env_override)
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


def callback_with_config(wrapper: ProcWrapper, cbdata: str,
        config: Dict[str, str]) -> str:
    return 'super' + cbdata + config['ENV']['ANOTHER_ENV']


def test_env_pass_through():
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override['ANOTHER_ENV'] = '250'

    wrapper = ProcWrapper(env_override=env_override)
    process_env = wrapper.make_process_env()

    assert process_env['ANOTHER_ENV'] == '250'

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

    ecs_metadata_handler, fetch_ecs_metadata_request_data = make_capturing_handler(
            response_data=TEST_ECS_TASK_METADATA, status=200)

    httpserver.expect_ordered_request('/aws/ecs/task',
            method='GET', headers=ACCEPT_JSON_HEADERS) \
            .respond_with_handler(ecs_metadata_handler)

    creation_handler, fetch_creation_request_data = make_capturing_handler(
            response_data={
                'uuid': DEFAULT_TASK_EXECUTION_UUID,
                'task': {
                    'name': 'A Task'
                },
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

    wrapper = make_wrapped_mode_proc_wrapper(env_override)
    wrapper.run()

    httpserver.check_assertions()

    crd = fetch_creation_request_data()
    em = crd['execution_method']
    assert em['type'] == 'AWS ECS'
    assert em['task_arn'] == TEST_ECS_TASK_METADATA['TaskARN']
    assert em['task_definition_arn'] == \
            'arn:aws:ecs:us-east-2:012345678910:task-definition/nginx:5'
    assert em['cluster_arn'] == TEST_ECS_TASK_METADATA['Cluster']
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
        assert emc['default_cluster_arn'] == TEST_ECS_TASK_METADATA['Cluster']
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


    wrapper = make_wrapped_mode_proc_wrapper(env_override)
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
