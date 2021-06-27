import logging
import json
import yaml

import pytest

import botocore
import boto3

from moto import mock_secretsmanager, mock_s3

from proc_wrapper import EnvResolver


RESOLVE_ENV_BASE_ENV = {
    'PROC_WRAPPER_TASK_NAME': 'Foo',
    'PROC_WRAPPER_API_KEY': 'XXX',
    'PROC_WRAPPER_RESOLVE_SECRETS': 'TRUE',
    'PROC_WRAPPER_SECRETS_AWS_REGION': 'us-east-2',
}


def test_resolve_env_with_json_path_and_plain_format():
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override['SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = 'PLAIN:{"a": "bug"}|JP:$.a'
    resolver = EnvResolver(env_override=env_override)
    resolved_env, bad_vars = resolver.fetch_and_resolve_env()
    assert resolved_env['SOME_ENV'] == 'bug'
    assert bad_vars == []


def put_aws_sm_secret(sm_client, name: str, value: str) -> str:
    return sm_client.create_secret(
        Name=name,
        SecretString=value,
    )['ARN']

def put_aws_s3_file(s3_client, name: str, value: str,
      content_type: str = 'text/plain',
      region_name='us-east-2') -> str:
    try:
        s3_client.create_bucket(Bucket='bucket',
                CreateBucketConfiguration={
                  'LocationConstraint': region_name
                })
    except botocore.exceptions.ClientError as error:
        logging.exception('Got error creating bucket')

    s3_client.put_object(
        Bucket='bucket',
        Key=name,
        Body=value.encode('utf-8'),
        ContentEncoding='utf-8',
        ContentType=content_type,
    )

    return f'arn:aws:s3:::bucket/{name}'

def test_env_in_aws_secrets_manager():
    env_override = RESOLVE_ENV_BASE_ENV.copy()

    with mock_secretsmanager():
        sm = boto3.client('secretsmanager', region_name='us-east-2')
        secret_arn = put_aws_sm_secret(sm, 'envs', """
            USERNAME=theuser
            PASSWORD=thepass
        """)
        resolver = EnvResolver(env_override=env_override,
            env_locations=[secret_arn])

        resolved_env, bad_vars = resolver.fetch_and_resolve_env()
        assert resolved_env['USERNAME'] == 'theuser'
        assert resolved_env['PASSWORD'] == 'thepass'
        assert bad_vars == []


def test_yaml_config_in_aws_s3():
    env_override = RESOLVE_ENV_BASE_ENV.copy()

    h = {
        'db': {
            'username': 'yuser',
            'password': 'ypw',
        }
    }

    yaml_string = yaml.dump(h)

    with mock_s3():
        s3_client = boto3.client('s3', region_name='us-east-2')
        s3_arn = put_aws_s3_file(s3_client, 'db.yaml', yaml_string)
        resolver = EnvResolver(env_override=env_override,
                config_locations=[s3_arn])

        resolved_config, bad_vars = resolver.fetch_and_resolve_config()
        assert resolved_config['db']['username'] == 'yuser'
        assert resolved_config['db']['password'] == 'ypw'
        assert bad_vars == []

@pytest.mark.parametrize('prefix', [
  'file://',
  ''
])
def test_json_config_file(prefix: str):
    env_override = RESOLVE_ENV_BASE_ENV.copy()

    resolver = EnvResolver(env_override=env_override,
        env_locations=[prefix + 'tests/data/config.json'])

    resolved_env, bad_vars = resolver.fetch_and_resolve_env()
    assert resolved_env['animal'] == 'dog'
    assert resolved_env['dimensions'] == json.dumps({
        "height": 26,
        "weight": 66
    })
    assert bad_vars == []


@pytest.mark.parametrize('prefix', [
  'AWS_SM_',
  ''
])
def test_resolve_env_with_aws_secrets_manager(prefix: str):
    env_override = RESOLVE_ENV_BASE_ENV.copy()

    with mock_secretsmanager():
        sm = boto3.client('secretsmanager', region_name='us-east-2')
        secret_arn = put_aws_sm_secret(sm, 'mypass', 'Secret PW')

        env_override[prefix + 'SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = secret_arn

        secret_arn = put_aws_sm_secret(sm, 'anotherpass', 'Secret PW 2')

        env_override[prefix + 'ANOTHER_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = secret_arn

        resolver = EnvResolver(env_override=env_override)
        resolved_env, bad_vars = resolver.fetch_and_resolve_env()
        assert resolved_env['SOME_ENV'] == 'Secret PW'
        assert resolved_env['ANOTHER_ENV'] == 'Secret PW 2'
        assert bad_vars == []


def test_resolve_env_with_env_reference():
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override['ENV_SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = 'ANOTHER_VAR'
    env_override['ANOTHER_VAR'] = 'env resolution works'

    resolver = EnvResolver(env_override=env_override)
    resolved_env, bad_vars = resolver.fetch_and_resolve_env()
    assert resolved_env['SOME_ENV'] == 'env resolution works'
    assert bad_vars == []


def test_resolve_env_with_env_reference_and_json_path():
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override['ENV_SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE'] = 'ANOTHER_VAR|JP:$.password'
    env_override['ANOTHER_VAR'] = '{"password": "foobar"}'

    resolver = EnvResolver(env_override=env_override)
    resolved_env, bad_vars = resolver.fetch_and_resolve_env()
    assert resolved_env['SOME_ENV'] == 'foobar'
    assert bad_vars == []
