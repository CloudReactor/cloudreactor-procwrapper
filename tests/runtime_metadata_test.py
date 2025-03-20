import copy
from datetime import datetime
from typing import Optional

import pytest
from moto import mock_aws
from pytest_httpserver import HTTPServer

from proc_wrapper import DefaultRuntimeMetadataFetcher, ProcWrapperParams
from proc_wrapper.runtime_metadata import AwsLambdaRuntimeMetadataFetcher

from .test_commons import (
    ACCEPT_JSON_HEADERS,
    TEST_ECS_CONTAINER_METADATA,
    TEST_ECS_TASK_METADATA,
    FakeAwsLambdaContext,
    make_capturing_handler,
    make_fake_aws_codebuild_env,
    make_fake_aws_lambda_env,
)


@pytest.mark.parametrize(
    ("main_container_name", "current_container_index", "exit_code"),
    [
        (None, 1, None),
        ("nginx-curl", 1, None),
        ("nginx-curl", 1, 1),
        ("nginx-curl", 0, 255),
    ],
)
@mock_aws
def test_aws_ecs_runtime_metadata(
    main_container_name: Optional[str],
    current_container_index: int,
    exit_code: Optional[int],
    httpserver: HTTPServer,
):
    env = {"ECS_CONTAINER_METADATA_URI": f"http://localhost:{httpserver.port}/aws/ecs"}

    task_response_data = TEST_ECS_TASK_METADATA
    container_response_data = TEST_ECS_TASK_METADATA["Containers"][
        current_container_index
    ]

    params = ProcWrapperParams(embedded_mode=False)
    params.main_container_name = main_container_name

    if exit_code is not None:
        task_response_data = copy.deepcopy(task_response_data)
        task_response_data["Containers"][1]["ExitCode"] = exit_code
        container_response_data = copy.deepcopy(container_response_data)
        container_response_data["ExitCode"] = exit_code

    task_metadata_handler, _fetch_task_metadata_request_data = make_capturing_handler(
        response_data=task_response_data, status=200
    )

    httpserver.expect_ordered_request(
        "/aws/ecs/task", method="GET", headers=ACCEPT_JSON_HEADERS
    ).respond_with_handler(task_metadata_handler)

    if len(task_response_data["Containers"]) > 1:
        (
            container_metadata_handler,
            _fetch_container_metadata_request_data,
        ) = make_capturing_handler(response_data=container_response_data, status=200)

        httpserver.expect_ordered_request(
            "/aws/ecs", method="GET", headers=ACCEPT_JSON_HEADERS
        ).respond_with_handler(container_metadata_handler)

    fetcher = DefaultRuntimeMetadataFetcher(params=params)

    metadata = fetcher.fetch(env=env)

    assert metadata is not None

    tc = metadata.task_configuration
    tec = metadata.task_execution_configuration

    for t in [tc, tec]:
        assert t.execution_method_type == "AWS ECS"
        assert t.infrastructure_type == "AWS"

    em = tec.execution_method_details
    assert em is not None
    assert em["task_arn"] == TEST_ECS_TASK_METADATA["TaskARN"]

    container = em["containers"][1]
    assert (
        container["docker_id"]
        == "43481a6ce4842eec8fe72fc28500c6b52edcc0917f105b83379f88cac1ff3946"
    )
    assert container["name"] == "nginx-curl"
    assert container["docker_name"] == "ecs-nginx-5-nginx-curl-ccccb9f49db0dfe0d901"
    assert container["image_name"] == "nrdlngr/nginx-curl"
    assert (
        container["image_id"]
        == "sha256:2e00ae64383cfc865ba0a2ba37f61b50a120d2d9378559dcd458dc0de47bc165"
    )
    assert container["labels"] == TEST_ECS_CONTAINER_METADATA["Labels"]

    emc = tc.execution_method_capability_details
    assert emc is not None

    for x in [em, emc]:
        assert (
            x["task_definition_arn"]
            == "arn:aws:ecs:us-east-2:012345678910:task-definition/nginx:5"
        )

        assert x["cluster_arn"] == TEST_ECS_TASK_METADATA["Cluster"]
        assert x["allocated_cpu_units"] == round(
            TEST_ECS_TASK_METADATA["Limits"]["CPU"] * 1024
        )
        assert x["allocated_memory_mb"] == TEST_ECS_TASK_METADATA["Limits"]["Memory"]
        assert x["main_container_name"] == TEST_ECS_CONTAINER_METADATA["Name"]
        assert (
            x["main_container_cpu_units"]
            == TEST_ECS_CONTAINER_METADATA["Limits"]["CPU"]
        )
        assert (
            x["main_container_memory_mb"]
            == TEST_ECS_CONTAINER_METADATA["Limits"]["Memory"]
        )

    for aws in [tc.infrastructure_settings, tec.infrastructure_settings]:
        assert aws is not None
        network = aws["network"]
        assert network["region"] == "us-east-2"

        networks = network["networks"]

        nw_0 = networks[0]
        assert nw_0["network_mode"] == "awsvpc"
        assert nw_0["ip_v4_subnet_cidr_block"] == "192.0.2.0/24"
        assert nw_0["dns_servers"] == ["192.0.2.2"]
        assert nw_0["dns_search_list"] == ["us-west-2.compute.internal"]
        assert nw_0["private_dns_name"] == "ip-10-0-0-222.us-west-2.compute.internal"
        assert nw_0["subnet_gateway_ip_v4_address"] == "192.0.2.0/24"

        logging_props = aws["logging"]
        assert logging_props["driver"] == "awslogs"
        log_options = logging_props["options"]
        assert log_options["create_group"] == "true"
        assert log_options["group"] == "/ecs/containerlogs"
        assert log_options["region"] == "us-west-2"
        assert log_options["stream"] == "ecs/curl/cd189a933e5849daa93386466019ab50"

    aws = tec.infrastructure_settings
    assert aws is not None
    network = aws["network"]
    assert network["availability_zone"] == "us-east-2b"
    nw_0 = network["networks"][0]
    assert nw_0 is not None
    assert nw_0["network_mode"] == "awsvpc"
    assert nw_0["ip_v4_addresses"] == ["192.0.2.3"]
    assert nw_0["mac_address"] == "0a:de:f6:10:51:e5"

    aws = tc.infrastructure_settings
    assert aws is not None
    nw_0 = aws["network"]["networks"][0]
    assert nw_0 is not None
    assert nw_0.get("ip_v4_addresses") is None
    assert nw_0.get("mac_address") is None

    assert metadata.is_execution_status_source == (current_container_index == 0)

    if exit_code is not None:
        assert metadata.exit_code == exit_code

    assert metadata.host_addresses == ["192.0.2.3"]
    assert metadata.host_names == ["ip-10-0-0-222.us-west-2.compute.internal"]

    assert metadata.monitor_process_env_additions is not None
    assert (
        metadata.monitor_process_env_additions["PROC_WRAPPER_MAIN_CONTAINER_NAME"]
        == "nginx-curl"
    )

    if current_container_index == 0:
        assert metadata.monitor_host_addresses == ["10.0.2.106"]
        assert metadata.monitor_host_names == []
        assert (
            metadata.monitor_process_env_additions[
                "PROC_WRAPPER_MONITOR_CONTAINER_NAME"
            ]
            == "~internal~ecs~pause"
        )
        assert (
            metadata.monitor_process_env_additions[
                "PROC_WRAPPER_SIDECAR_CONTAINER_MODE"
            ]
            == "TRUE"
        )
    else:
        assert metadata.monitor_host_addresses == ["192.0.2.3"]
        assert metadata.monitor_host_names == [
            "ip-10-0-0-222.us-west-2.compute.internal"
        ]
        assert (
            metadata.monitor_process_env_additions[
                "PROC_WRAPPER_MONITOR_CONTAINER_NAME"
            ]
            == "nginx-curl"
        )
        assert (
            metadata.monitor_process_env_additions[
                "PROC_WRAPPER_SIDECAR_CONTAINER_MODE"
            ]
            == "FALSE"
        )


@mock_aws
def test_aws_lambda_runtime_metadata():
    env = make_fake_aws_lambda_env()
    context = FakeAwsLambdaContext()

    fetcher = DefaultRuntimeMetadataFetcher()
    metadata = fetcher.fetch(env=env, context=context)

    assert metadata is not None

    tc = metadata.task_configuration
    tec = metadata.task_execution_configuration

    em = tec.execution_method_details
    emc = tc.execution_method_capability_details

    for t in [tc, tec]:
        assert t.execution_method_type == "AWS Lambda"
        assert t.infrastructure_type == "AWS"

    for h in [em, emc]:
        assert h["runtime_id"] == "AWS_Lambda_python3.9"
        assert h["function_name"] == "do_it_now"
        assert h["function_version"] == "3.3.7"
        assert h["init_type"] == "on-demand"
        assert h["dotnet_prejit"] is None
        assert h["function_memory_mb"] == 4096
        assert h["time_zone_name"] == "America/Los_Angeles"
        assert (
            h["function_arn"] == "arn:aws:lambda:us-east-2:123456789012:function:funky"
        )

    for aws in [tc.infrastructure_settings, tec.infrastructure_settings]:
        network = aws["network"]
        assert network["region"] == "us-east-2"

        logging_info = aws["logging"]
        assert logging_info["driver"] == "awslogs"
        logging_options = logging_info["options"]
        assert logging_options["group"] == "muh_log_group"
        assert logging_options["stream"] == "colorado-river"

    assert em["aws_request_id"] == context.aws_request_id

    em_client = em["client_context"]["client"]
    client = context.client_context.client
    for p in AwsLambdaRuntimeMetadataFetcher.AWS_LAMBDA_CLIENT_METADATA_PROPERTIES:
        assert em_client[p] == getattr(client, p)

    xray = tec.infrastructure_settings["xray"]
    assert xray["trace_id"] == "894diemsggt"
    assert xray["context_missing"] is None

    cognito = em["cognito_identity"]
    assert cognito["id"] == context.identity.cognito_identity_id
    assert cognito["pool_id"] == context.identity.cognito_identity_pool_id

    assert metadata.is_execution_status_source is False


@mock_aws
def test_aws_codebuild_runtime_metadata():
    env = make_fake_aws_codebuild_env()

    fetcher = DefaultRuntimeMetadataFetcher()
    metadata = fetcher.fetch(env=env)

    assert metadata is not None

    tc = metadata.task_configuration
    tec = metadata.task_execution_configuration

    em = tec.execution_method_details
    emc = tc.execution_method_capability_details

    for t in [tc, tec]:
        assert t.execution_method_type == "AWS CodeBuild"
        assert t.infrastructure_type == "AWS"

    for h in [em, emc]:
        assert h["build_image"] == "aws/codebuild/standard:2.0"
        assert h["kms_key_id"] == "arn:aws:kms:us-east-1:123456789012:key/key-ID"
        assert h["source_repo_url"] == "https://github.com/aws/codebuild-demo-project"
        assert h["source_version"] == "arn:aws:s3:::bucket/pipeline/App/OGgJCVJ.zip"
        assert h["initiator"] == "codepipeline/codebuild-demo-project"

    assert (
        em["build_id"] == "codebuild-demo-project:b1e6661e-e4f2-4156-9ab9-82a19EXAMPLE"
    )
    assert (
        em["build_arn"]
        == "arn:aws:codebuild:us-east-1:123456789012:build/codebuild-demo-project:b1e6661e-e4f2-4156-9ab9-82a19EXAMPLE"
    )
    assert em["batch_build_identifier"] == "CBBBI"
    assert em["build_number"] == 25
    assert em["resolved_source_version"] == "3d6151b3ebc9ba70b83de319db596d7eda56e517"
    assert (
        em["public_build_url"] == "https://public.build.aws.com/codebuild-demo-project"
    )
    assert em["build_succeeding"] is True
    assert em["start_time"] == datetime.fromtimestamp(1693959305.402).isoformat()

    webhook = em["webhook"]
    assert webhook["actor_account_id"] == "123456789012"
    assert webhook["base_ref"] == "CBWHBR"
    assert webhook["event"] == "CBWHE"
    assert webhook["merge_commit"] == "CBWHMC"
    assert webhook["prev_commit"] == "CBWHPC"
    assert webhook["head_ref"] == "CBWHHR"
    assert webhook["trigger"] == "pr/12345"

    assert (
        emc["build_arn"]
        == "arn:aws:codebuild:us-east-1:123456789012:build/codebuild-demo-project"
    )

    for aws in [tc.infrastructure_settings, tec.infrastructure_settings]:
        network = aws["network"]
        assert network["region"] == "us-east-1"

        logging_info = aws["logging"]
        assert logging_info["driver"] == "awslogs"
        logging_options = logging_info["options"]
        assert logging_options["region"] == "us-east-1"

    assert (
        tec.infrastructure_settings["logging"]["options"]["stream"]
        == "40b92e01-706b-422a-9305-8bdb16f7c269"
    )

    assert metadata.is_execution_status_source is False
