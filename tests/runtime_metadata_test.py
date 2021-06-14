from proc_wrapper.runtime_metadata import fetch_ecs_container_metadata

from pytest_httpserver import HTTPServer

from .test_commons import (
    ACCEPT_JSON_HEADERS,
    TEST_ECS_TASK_METADATA,
    make_capturing_handler
)


def test_ecs_runtime_metadata(httpserver: HTTPServer):
    env = {
        'ECS_CONTAINER_METADATA_URI': f'http://localhost:{httpserver.port}/aws/ecs'
    }

    ecs_metadata_handler, fetch_ecs_metadata_request_data = make_capturing_handler(
            response_data=TEST_ECS_TASK_METADATA, status=200)

    httpserver.expect_ordered_request('/aws/ecs/task',
            method='GET', headers=ACCEPT_JSON_HEADERS) \
            .respond_with_handler(ecs_metadata_handler)

    metadata = fetch_ecs_container_metadata(env=env)

    assert metadata is not None

    em = metadata.execution_method
    assert em['type'] == 'AWS ECS'
    assert em['task_arn'] == TEST_ECS_TASK_METADATA['TaskARN']
    assert em['task_definition_arn'] == \
            'arn:aws:ecs:us-east-2:012345678910:task-definition/nginx:5'
    assert em['cluster_arn'] == TEST_ECS_TASK_METADATA['Cluster']
    assert em['allocated_cpu_units'] == 256
    assert em['allocated_memory_mb'] == 512

    emc = metadata.execution_method_capability
    assert emc['type'] == 'AWS ECS'
    assert emc['task_definition_arn'] == em['task_definition_arn']
    assert emc['default_cluster_arn'] == TEST_ECS_TASK_METADATA['Cluster']
    assert emc['allocated_cpu_units'] == 256
    assert emc['allocated_memory_mb'] == 512

    aws = metadata.derived['aws']
    network = aws['network']
    assert network['availability_zone'] == 'us-east-2b'
    assert network['region'] == 'us-east-2'
