import json
import logging
import time
from typing import Any, Dict, Optional

import boto3
import botocore
import pytest
import yaml
from moto import mock_aws

from proc_wrapper import (
    CONFIG_MERGE_STRATEGY_SHALLOW,
    ConfigResolver,
    ConfigResolverParams,
)
from proc_wrapper.config_resolver import DEFAULT_FORMAT_SEPARATOR

FORMAT_METHOD_EXTENSION = "extension"
FORMAT_METHOD_CONTENT_TYPE = "content_type"
FORMAT_METHOD_SUFFIX = "suffix"

S3_FORMAT_METHODS = [
    FORMAT_METHOD_EXTENSION,
    FORMAT_METHOD_CONTENT_TYPE,
    FORMAT_METHOD_SUFFIX,
]

RESOLVE_ENV_BASE_ENV = {
    "PROC_WRAPPER_LOG_LEVEL": "DEBUG",
    "PROC_WRAPPER_TASK_NAME": "Foo",
    "PROC_WRAPPER_API_KEY": "XXX",
    "PROC_WRAPPER_RESOLVE_SECRETS": "TRUE",
    "PROC_WRAPPER_SECRETS_AWS_REGION": "us-east-2",
}


@pytest.mark.parametrize("format", ["json", ""])
def test_resolve_env_from_plaintext_with_json_path(format: str):
    env_override = RESOLVE_ENV_BASE_ENV.copy()

    value = 'PLAIN:{"a": "bug"}'

    if format:
        value += DEFAULT_FORMAT_SEPARATOR + format

    env_override["SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE"] = value + "|JP:$.a"
    params = ConfigResolverParams()
    resolver = ConfigResolver(params=params, env_override=env_override)
    resolved_env, bad_vars = resolver.fetch_and_resolve_env()
    assert resolved_env["SOME_ENV"] == "bug"
    assert bad_vars == []


@pytest.mark.parametrize(
    ("locations"),
    [
        (""),
        ("  "),
        ("PLAIN:{}"),
        ("PLAIN:{}!json"),
        (","),
    ],
)
def test_empty_env_locations_fetching(locations: str):
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override["PROC_WRAPPER_ENV_LOCATIONS"] = locations

    params = ConfigResolverParams()
    params.override_resolver_params_from_env(env=env_override)
    resolver = ConfigResolver(params=params, env_override=env_override)

    resolved_env, bad_vars = resolver.fetch_and_resolve_env()
    assert bad_vars == []


def test_resolve_env_from_config():
    env_override = RESOLVE_ENV_BASE_ENV.copy()

    env_override["SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE"] = "CONFIG:$.a"
    params = ConfigResolverParams()
    params.initial_config = {"a": "bug"}
    resolver = ConfigResolver(params=params, env_override=env_override)
    resolved_env, bad_vars = resolver.fetch_and_resolve_env()
    assert resolved_env["SOME_ENV"] == "bug"
    assert bad_vars == []


def put_aws_ssm_secret(
    ssm_client, name: str, value: str, typ: Optional[str] = None
) -> str:
    typ = typ or "string"
    response = ssm_client.put_parameter(
        Name=name, Value=value, Type=typ, Overwrite=True
    )

    return f"ssm:{name}:{response['Version']}"


def put_aws_sm_secret(sm_client, name: str, value: str) -> str:
    return sm_client.create_secret(
        Name=name,
        SecretString=value,
    )["ARN"]


def put_aws_s3_file(
    s3_client,
    name: str,
    value: str,
    content_type: Optional[str] = None,
    region_name: Optional[str] = None,
) -> str:
    content_type = content_type or "text/plain"
    region_name = region_name or "us-east-2"

    try:
        s3_client.create_bucket(
            Bucket="bucket",
            CreateBucketConfiguration={"LocationConstraint": region_name},
        )
    except botocore.exceptions.ClientError:
        logging.exception("Got error creating bucket")

    s3_client.put_object(
        Bucket="bucket",
        Key=name,
        Body=value.encode("utf-8"),
        ContentEncoding="utf-8",
        ContentType=content_type,
    )

    return f"arn:aws:s3:::bucket/{name}"


def test_env_in_aws_secrets_manager():
    # Disable botocore DEBUG logging because it leaks secrets
    logging.getLogger("botocore").setLevel(logging.INFO)

    env_override = RESOLVE_ENV_BASE_ENV.copy()

    params = ConfigResolverParams()

    with mock_aws():
        sm = boto3.client("secretsmanager", region_name="us-east-2")
        secret_arn = put_aws_sm_secret(
            sm,
            "envs",
            """
            USERNAME=theuser
            PASSWORD=thepass
        """,
        )

        params.env_locations = [secret_arn]

        resolver = ConfigResolver(params=params, env_override=env_override)

        resolved_env, bad_vars = resolver.fetch_and_resolve_env()
        assert resolved_env["USERNAME"] == "theuser"
        assert resolved_env["PASSWORD"] == "thepass"
        assert bad_vars == []


def test_env_in_aws_parameter_store():
    # Disable botocore DEBUG logging because it leaks secrets
    logging.getLogger("botocore").setLevel(logging.INFO)

    env_override = RESOLVE_ENV_BASE_ENV.copy()

    params = ConfigResolverParams()

    with mock_aws():
        ssm = boto3.client("ssm")
        ssm_id = put_aws_ssm_secret(
            ssm_client=ssm,
            name="envs",
            value="""
            USERNAME=theuser
            PASSWORD=thepass
        """,
        )

        # Test with and without the version suffix
        for location in ["ssm:envs", ssm_id]:
            params.env_locations = [location]

            resolver = ConfigResolver(params=params, env_override=env_override)

            resolved_env, bad_vars = resolver.fetch_and_resolve_env()
            assert resolved_env["USERNAME"] == "theuser"
            assert resolved_env["PASSWORD"] == "thepass"
            assert bad_vars == []


def test_string_list_in_aws_parameter_store():
    # Disable botocore DEBUG logging because it leaks secrets
    logging.getLogger("botocore").setLevel(logging.INFO)

    env_override = RESOLVE_ENV_BASE_ENV.copy()

    params = ConfigResolverParams()

    with mock_aws():
        ssm = boto3.client("ssm")
        ssm_id = put_aws_ssm_secret(
            ssm_client=ssm, name="hosts", value="""host1,host2""", typ="StringList"
        )

        # Test with and without the version suffix
        for location in ["ssm:hosts", ssm_id]:
            params.initial_config = {"hosts__to_resolve": ssm_id}

            resolver = ConfigResolver(params=params, env_override=env_override)

            resolved_config, bad = resolver.fetch_and_resolve_config()

            hosts = resolved_config.get("hosts")
            expected_hosts = ["host1", "host2"]
            assert hosts == expected_hosts
            assert len(bad) == 0


@pytest.mark.skip(reason="moto does not support AWS AppConfig yet")
def test_json_in_aws_app_config():
    # Disable botocore DEBUG logging because it leaks secrets
    logging.getLogger("botocore").setLevel(logging.INFO)

    env_override = RESOLVE_ENV_BASE_ENV.copy()

    params = ConfigResolverParams()

    with mock_aws():
        app_config = boto3.client("appconfig")

        response = app_config.create_application(Name="myapp")
        app_id = response["Id"]

        response = app_config.create_environment(
            ApplicationId=app_id,
            Name="staging",
        )
        env_id = response["Id"]

        response = app_config.create_configuration_profile(
            ApplicationId=app_id,
            Name="myconfig",
            LocationUri="hosted",
            Type="AWS.FreeForm",
        )

        profile_id = response["Id"]

        response = app_config.create_hosted_configuration_version(
            ApplicationId=app_id,
            ConfigurationProfileId=profile_id,
            Content=json.dumps(
                {
                    "username": "theuser",
                    "password": "thepass",
                }
            ),
            ContentType="application/json",
            VersionLabel="v1.0.0",
        )

        response = app_config.create_deployment_strategy(
            Name="mystrategy",
            DeploymentDurationInMinutes=1,
            GrowthFactor=100.0,
        )

        strategy_id = response["Id"]

        response = app_config.start_deployment(
            ApplicationId=app_id,
            EnvironmentId=env_id,
            DeploymentStrategyId=strategy_id,
            ConfigurationProfileId=profile_id,
            ConfigurationVersion="1",
        )

        deployment_number = response["DeploymentNumber"]

        deployed = False

        while not deployed:
            response = app_config.get_deployment(
                ApplicationId=app_id,
                EnvironmentId=env_id,
                DeploymentNumber=deployment_number,
            )

            state = response["State"]
            deployed = state == "DEPLOYED"

            if deployed:
                print("Deployed AppConfig data")
            else:
                print(f"Waiting for AppConfig data to be deployed, {state=}")
                time.sleep(5)

        params.env_locations = ["aws:appconfig:myapp/staging/myconfig"]

        resolver = ConfigResolver(params=params, env_override=env_override)

        resolved_config, bad_vars = resolver.fetch_and_resolve_config()
        assert resolved_config["username"] == "theuser"
        assert resolved_config["password"] == "thepass"
        assert bad_vars == []


@pytest.mark.parametrize(
    ("format_method_suffix", "extension", "content_type"),
    [
        ("YAML", None, "text/plain"),
        (None, "yml", "text/plain"),
        (None, "yaml", "text/plain"),
        (None, None, "application/yaml"),
        (None, None, "application/x-yaml"),
        (None, None, "text/vnd.yaml"),
        (None, None, "text/yaml"),
        (None, None, "text/x-yaml"),
    ],
)
def test_yaml_config_in_aws_s3(
    format_method_suffix: Optional[str],
    extension: Optional[str],
    content_type: Optional[str],
):
    env_override = RESOLVE_ENV_BASE_ENV.copy()

    h = {
        "db": {
            "username": "yuser",
            "password": "ypw",
        }
    }

    yaml_string = yaml.dump(h)

    params = ConfigResolverParams()

    with mock_aws():
        s3_client = boto3.client("s3", region_name="us-east-2")

        name = "db"

        if extension:
            name += "." + extension

        s3_arn = put_aws_s3_file(
            s3_client, name, yaml_string, content_type=content_type
        )

        location = s3_arn

        if format_method_suffix:
            location = s3_arn + "!" + format_method_suffix

        params.config_locations = [location]

        resolver = ConfigResolver(params=params, env_override=env_override)

        resolved_config, bad_vars = resolver.fetch_and_resolve_config()
        assert resolved_config["db"]["username"] == "yuser"
        assert resolved_config["db"]["password"] == "ypw"
        assert bad_vars == []

    # Test one more with S3 support to check caching
    resolved_config, bad_vars = resolver.fetch_and_resolve_config()
    assert resolved_config["db"]["username"] == "yuser"
    assert resolved_config["db"]["password"] == "ypw"
    assert bad_vars == []


@pytest.mark.parametrize(
    ("prefix", "filename", "format_method_suffix"),
    [
        ("FILE:", "config.json", None),
        ("file://", "config.json", None),
        ("", "config.json", None),
        ("", "config_json.txt", "json"),
    ],
)
def test_json_config_file(
    prefix: str, filename: str, format_method_suffix: Optional[str]
):
    env_override = RESOLVE_ENV_BASE_ENV.copy()

    location = prefix + "tests/data/" + filename

    if format_method_suffix:
        location += DEFAULT_FORMAT_SEPARATOR + format_method_suffix

    params = ConfigResolverParams()
    params.env_locations = [location]

    resolver = ConfigResolver(params=params, env_override=env_override)

    resolved_env, bad_vars = resolver.fetch_and_resolve_env()
    assert resolved_env["animal"] == "dog"
    assert resolved_env["dimensions"] == json.dumps({"height": 26, "weight": 66})
    assert bad_vars == []


@pytest.mark.parametrize(
    ("merge_strategy", "dimensions"),
    [
        (
            CONFIG_MERGE_STRATEGY_SHALLOW,
            {
                "height": 23,
            },
        ),
        ("REPLACE", {"height": 23, "weight": 66}),
    ],
)
def test_env_merging(merge_strategy: str, dimensions: Dict[str, Any]):
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    params = ConfigResolverParams()
    params.env_locations = ["tests/data/config.json", "tests/data/test.env"]
    params.config_merge_strategy = merge_strategy
    resolver = ConfigResolver(params=params, env_override=env_override)

    resolved_env, bad_vars = resolver.fetch_and_resolve_env()
    assert resolved_env["animal"] == "cat"
    assert json.loads(resolved_env["dimensions"]) == dimensions
    assert bad_vars == []


@pytest.mark.parametrize("prefix", ["AWS_SM_", ""])
def test_resolve_env_with_aws_secrets_manager(prefix: str):
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    params = ConfigResolverParams()

    with mock_aws():
        sm = boto3.client("secretsmanager", region_name="us-east-2")
        secret_arn = put_aws_sm_secret(sm, "mypass", "Secret PW")

        env_override[prefix + "SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE"] = secret_arn

        secret_arn = put_aws_sm_secret(sm, "anotherpass", "Secret PW 2")

        env_override[prefix + "ANOTHER_ENV_FOR_PROC_WRAPPER_TO_RESOLVE"] = secret_arn

        resolver = ConfigResolver(params=params, env_override=env_override)
        resolved_env, bad_vars = resolver.fetch_and_resolve_env()
        assert resolved_env["SOME_ENV"] == "Secret PW"
        assert resolved_env["ANOTHER_ENV"] == "Secret PW 2"
        assert bad_vars == []


def test_resolve_env_with_env_reference():
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override["ENV_SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE"] = "ANOTHER_VAR"
    env_override["ANOTHER_VAR"] = "env resolution works"
    params = ConfigResolverParams()

    resolver = ConfigResolver(params=params, env_override=env_override)
    resolved_env, bad_vars = resolver.fetch_and_resolve_env()
    assert resolved_env["SOME_ENV"] == "env resolution works"
    assert bad_vars == []


def test_resolve_env_with_env_reference_and_json_path():
    env_override = RESOLVE_ENV_BASE_ENV.copy()
    env_override[
        "ENV_SOME_ENV_FOR_PROC_WRAPPER_TO_RESOLVE"
    ] = "ANOTHER_VAR|JP:$.password"
    env_override["ANOTHER_VAR"] = '{"password": "foobar"}'
    params = ConfigResolverParams()

    resolver = ConfigResolver(params=params, env_override=env_override)
    resolved_env, bad_vars = resolver.fetch_and_resolve_env()
    assert resolved_env["SOME_ENV"] == "foobar"
    assert bad_vars == []
