from proc_wrapper import EnvResolver


RESOLVE_ENV_BASE_ENV = {
    'PROC_WRAPPER_TASK_NAME': 'Foo',
    'PROC_WRAPPER_API_KEY': 'XXX',
    'PROC_WRAPPER_RESOLVE_SECRETS': 'TRUE',
    'PROC_WRAPPER_SECRETS_AWS_REGION': 'us-east-2',
}


def test_resolve_env_with_json_path():
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override['SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = '{"a": "bug"}|JP:$.a'
    resolver = EnvResolver(env_override=env_override)
    resolved_env, bad_vars = resolver.resolve_env()
    assert resolved_env['SOME_ENV'] == 'bug'
    assert bad_vars == []


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

        resolver = EnvResolver(env_override=env_override)
        resolved_env, bad_vars = resolver.resolve_env()
        assert resolved_env['SOME_ENV'] == 'Secret PW'
        assert resolved_env['ANOTHER_ENV'] == 'Secret PW 2'
        assert bad_vars == []


def test_resolve_env_with_env_reference():
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override['ENV_SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = 'ANOTHER_VAR'
    env_override['ANOTHER_VAR'] = 'env resolution works'

    resolver = EnvResolver(env_override=env_override)
    resolved_env, bad_vars = resolver.resolve_env()
    assert resolved_env['SOME_ENV'] == 'env resolution works'
    assert bad_vars == []


def test_resolve_env_with_env_reference_and_json_path():
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override['ENV_SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = 'ANOTHER_VAR|JP:$.password'
    env_override['ANOTHER_VAR'] = '{"password": "foobar"}'

    resolver = EnvResolver(env_override=env_override)
    resolved_env, bad_vars = resolver.resolve_env()
    assert resolved_env['SOME_ENV'] == 'foobar'
    assert bad_vars == []
