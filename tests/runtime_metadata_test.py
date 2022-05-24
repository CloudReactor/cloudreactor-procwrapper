from pytest_httpserver import HTTPServer

from proc_wrapper import DefaultRuntimeMetadataFetcher

from .test_commons import (
    ACCEPT_JSON_HEADERS,
    TEST_ECS_TASK_METADATA,
    FakeAwsLambdaContext,
    make_capturing_handler,
    make_fake_aws_lambda_env,
)


def test_aws_ecs_runtime_metadata(httpserver: HTTPServer):
    env = {"ECS_CONTAINER_METADATA_URI": f"http://localhost:{httpserver.port}/aws/ecs"}

    ecs_metadata_handler, fetch_ecs_metadata_request_data = make_capturing_handler(
        response_data=TEST_ECS_TASK_METADATA, status=200
    )

    httpserver.expect_ordered_request(
        "/aws/ecs/task", method="GET", headers=ACCEPT_JSON_HEADERS
    ).respond_with_handler(ecs_metadata_handler)

    fetcher = DefaultRuntimeMetadataFetcher()

    metadata = fetcher.fetch(env=env)

    assert metadata is not None

    em = metadata.execution_method
    assert em["type"] == "AWS ECS"
    assert em["task_arn"] == TEST_ECS_TASK_METADATA["TaskARN"]
    assert (
        em["task_definition_arn"]
        == "arn:aws:ecs:us-east-2:012345678910:task-definition/nginx:5"
    )
    assert em["cluster_arn"] == TEST_ECS_TASK_METADATA["Cluster"]
    assert em["allocated_cpu_units"] == 256
    assert em["allocated_memory_mb"] == 512

    emc = metadata.execution_method_capability
    assert emc["type"] == "AWS ECS"
    assert emc["task_definition_arn"] == em["task_definition_arn"]
    assert emc["default_cluster_arn"] == TEST_ECS_TASK_METADATA["Cluster"]
    assert emc["allocated_cpu_units"] == 256
    assert emc["allocated_memory_mb"] == 512

    aws = metadata.derived["aws"]
    network = aws["network"]
    assert network["availability_zone"] == "us-east-2b"
    assert network["region"] == "us-east-2"


def test_aws_lambda_runtime_metadata():
    env = make_fake_aws_lambda_env()
    context = FakeAwsLambdaContext()

    fetcher = DefaultRuntimeMetadataFetcher()
    metadata = fetcher.fetch(env=env, context=context)

    assert metadata is not None

    em = metadata.execution_method
    emc = metadata.execution_method_capability

    for h in [em, emc]:
        assert h["type"] == "AWS Lambda"
        assert h["runtime_id"] == "AWS_Lambda_python3.9"
        assert h["function_name"] == "do_it_now"
        assert h["function_version"] == "3.3.7"
        assert h["init_type"] == "on-demand"
        assert h["dotnet_prejit"] is None
        assert h["allocated_memory_mb"] == 4096
        assert h["time_zone_name"] == "America/Los_Angeles"
        assert (
            h["function_arn"] == "arn:aws:lambda:us-east-2:123456789012:function:funky"
        )

        aws = h["aws"]
        network = aws["network"]
        assert network["region"] == "us-east-2"

        logging_info = aws["logging"]
        assert logging_info["group_name"] == "muh_log_group"
        assert logging_info["stream_name"] == "colorado-river"

    assert em["aws_request_id"] == context.aws_request_id

    em_client = em["client_context"]["client"]
    client = context.client_context.client
    for p in DefaultRuntimeMetadataFetcher.AWS_LAMBDA_CLIENT_METADATA_PROPERTIES:
        assert em_client[p] == getattr(client, p)

    xray = em["aws"]["xray"]
    assert xray["trace_id"] == "894diemsggt"
    assert xray["context_missing"] is None

    cognito = em["cognito_identity"]
    assert cognito["id"] == context.identity.cognito_identity_id
    assert cognito["pool_id"] == context.identity.cognito_identity_pool_id
