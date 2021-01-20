from typing import Dict

from proc_wrapper import ProcWrapper

RESOLVE_ENV_BASE_ENV = {
    'PROC_WRAPPER_TASK_NAME': 'Foo',
    'PROC_WRAPPER_API_KEY': 'XXX',
    'PROC_WRAPPER_RESOLVE_SECRETS': 'TRUE',
    'PROC_WRAPPER_SECRETS_AWS_REGION': 'us-east-2',
}


def test_wrapped_offline_mode():
    args = ProcWrapper.make_arg_parser().parse_args(['echo'])

    env_override = {
        'PROC_WRAPPER_LOG_LEVEL': 'DEBUG',
        'PROC_WRAPPER_OFFLINE_MODE': 'TRUE',
    }
    proc_wrapper = ProcWrapper(args=args, env_override=env_override,
            embedded_mode=False)
    proc_wrapper.run()


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

        print("secret_arn = " + secret_arn)

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
