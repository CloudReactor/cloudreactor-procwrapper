import json
from typing import Any, Dict, List, Mapping, Optional

from werkzeug.wrappers import Request, Response

ACCEPT_JSON_HEADERS = {
    "Accept": "application/json",
}

TEST_ECS_TASK_METADATA = {
    "Cluster": "default",
    "TaskARN": "arn:aws:ecs:us-east-2:012345678910:task/9781c248-0edd-4cdb-9a93-f63cb662a5d3",
    "Family": "nginx",
    "Revision": "5",
    "DesiredStatus": "RUNNING",
    "KnownStatus": "RUNNING",
    "Limits": {"CPU": 0.25, "Memory": 512},
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
                "com.amazonaws.ecs.task-definition-version": "5",
            },
            "DesiredStatus": "RESOURCES_PROVISIONED",
            "KnownStatus": "RESOURCES_PROVISIONED",
            "Limits": {"CPU": 0, "Memory": 0},
            "CreatedAt": "2018-02-01T20:55:08.366329616Z",
            "StartedAt": "2018-02-01T20:55:09.058354915Z",
            "Type": "CNI_PAUSE",
            "Networks": [{"NetworkMode": "awsvpc", "IPv4Addresses": ["10.0.2.106"]}],
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
                "com.amazonaws.ecs.task-definition-version": "5",
            },
            "DesiredStatus": "RUNNING",
            "KnownStatus": "RUNNING",
            "Limits": {"CPU": 512, "Memory": 512},
            "CreatedAt": "2018-02-01T20:55:10.554941919Z",
            "StartedAt": "2018-02-01T20:55:11.064236631Z",
            "Type": "NORMAL",
            "Networks": [{"NetworkMode": "awsvpc", "IPv4Addresses": ["10.0.2.106"]}],
        },
    ],
    "PullStartedAt": "2018-02-01T20:55:09.372495529Z",
    "PullStoppedAt": "2018-02-01T20:55:10.552018345Z",
    "AvailabilityZone": "us-east-2b",
}


TEST_ECS_CONTAINER_METADATA = {
    "DockerId": "cd189a933e5849daa93386466019ab50-2495160603",
    "Name": "curl",
    "DockerName": "curl",
    "Image": "111122223333.dkr.ecr.us-west-2.amazonaws.com/curltest:latest",
    "ImageID": "sha256:25f3695bedfb454a50f12d127839a68ad3caf91e451c1da073db34c542c4d2cb",
    "Labels": {
        "com.amazonaws.ecs.cluster": "arn:aws:ecs:us-west-2:111122223333:cluster/default",
        "com.amazonaws.ecs.container-name": "curl",
        "com.amazonaws.ecs.task-arn": "arn:aws:ecs:us-west-2:111122223333:task/default/cd189a933e5849daa93386466019ab50",
        "com.amazonaws.ecs.task-definition-family": "curltest",
        "com.amazonaws.ecs.task-definition-version": "2",
    },
    "DesiredStatus": "RUNNING",
    "KnownStatus": "RUNNING",
    "Limits": {"CPU": 10, "Memory": 128},
    "CreatedAt": "2020-10-08T20:09:11.44527186Z",
    "StartedAt": "2020-10-08T20:09:11.44527186Z",
    "Type": "NORMAL",
    "Networks": [
        {
            "NetworkMode": "awsvpc",
            "IPv4Addresses": ["192.0.2.3"],
            "AttachmentIndex": 0,
            "MACAddress": "0a:de:f6:10:51:e5",
            "IPv4SubnetCIDRBlock": "192.0.2.0/24",
            "DomainNameServers": ["192.0.2.2"],
            "DomainNameSearchList": ["us-west-2.compute.internal"],
            "PrivateDNSName": "ip-10-0-0-222.us-west-2.compute.internal",
            "SubnetGatewayIpv4Address": "192.0.2.0/24",
        }
    ],
    "ContainerARN": "arn:aws:ecs:us-west-2:111122223333:container/05966557-f16c-49cb-9352-24b3a0dcd0e1",
    "LogOptions": {
        "awslogs-create-group": "true",
        "awslogs-group": "/ecs/containerlogs",
        "awslogs-region": "us-west-2",
        "awslogs-stream": "ecs/curl/cd189a933e5849daa93386466019ab50",
    },
    "LogDriver": "awslogs",
}


def make_capturing_handler(response_data: Optional[Dict[str, Any]], status: int = 200):
    captured_request_data: List[Optional[Dict[str, Any]]] = [None]

    def handler(request: Request) -> Response:
        if request.data:
            captured_request_data[0] = json.loads(request.data)

        if response_data:
            return Response(
                json.dumps(response_data), status, None, content_type="application/json"
            )
        else:
            return Response(None, status, None)

    def fetch_captured_request_data() -> Optional[Dict[str, Any]]:
        return captured_request_data[0]

    return handler, fetch_captured_request_data


class FakeAwsCognitoIdentity:
    def __init__(self):
        self.cognito_identity_id = "cog ID"
        self.cognito_identity_pool_id = "cog Piss ID"


class FakeAwsMobileClient:
    def __init__(self):
        self.installation_id = "install ID 345"
        self.app_title = "Disruptor"
        self.app_version_name = "2.0"
        self.app_version_code = "deadfeed"
        self.app_package_name = "boxer-765"


class FakeAwsClientContext:
    def __init__(self):
        self.client = FakeAwsMobileClient()
        self.custom = {"a": "b"}
        self.env = {"SOME_AWS_SDK_VAR": "d"}


class FakeAwsLambdaContext:
    def __init__(self):
        self.function_name = "funky"
        self.function_version = "1.0.3F"
        self.invoked_function_arn = (
            "arn:aws:lambda:us-east-2:123456789012:function:funky"
        )
        self.memory_limit_in_mb = 1024
        self.aws_request_id = "SOME-REQ_ID"
        self.log_group_name = "staging/stuff"
        self.log_stream_name = "streamer"
        self.identity = FakeAwsCognitoIdentity()
        self.client_context = FakeAwsClientContext()


def make_fake_aws_lambda_env() -> Mapping[str, str]:
    return {
        "LAMBDA_TASK_ROOT": "/root/lambda/task",
        "AWS_REGION": "us-east-2",
        "AWS_EXECUTION_ENV": "AWS_Lambda_python3.9",
        "AWS_LAMBDA_FUNCTION_NAME": "do_it_now",
        "AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "4096",
        "AWS_LAMBDA_FUNCTION_VERSION": "3.3.7",
        "AWS_LAMBDA_INITIALIZATION_TYPE": "on-demand",
        "AWS_LAMBDA_LOG_GROUP_NAME": "muh_log_group",
        "AWS_LAMBDA_LOG_STREAM_NAME": "colorado-river",
        "_X_AMZN_TRACE_ID": "894diemsggt",
        "TZ": "America/Los_Angeles",
    }


def make_fake_aws_codebuild_env() -> Mapping[str, str]:
    return {
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_REGION": "us-east-1",
        "CODEBUILD_BATCH_BUILD_IDENTIFIER": "CBBBI",
        "CODEBUILD_BUILD_ARN": "arn:aws:codebuild:us-east-1:123456789012:build/codebuild-demo-project:b1e6661e-e4f2-4156-9ab9-82a19EXAMPLE",
        "CODEBUILD_BUILD_ID": "codebuild-demo-project:b1e6661e-e4f2-4156-9ab9-82a19EXAMPLE",
        "CODEBUILD_BUILD_IMAGE": "aws/codebuild/standard:2.0",
        "CODEBUILD_BUILD_NUMBER": "25",
        "CODEBUILD_BUILD_SUCCEEDING": "1",
        "CODEBUILD_INITIATOR": "codepipline/codebuild-demo-project",
        "CODEBUILD_KMS_KEY_ID": "arn:aws:kms:us-east-1:123456789012:key/key-ID",
        "CODEBUILD_LOG_PATH": "40b92e01-706b-422a-9305-8bdb16f7c269",
        "CODEBUILD_PUBLIC_BUILD_URL": "https://public.build.aws.com/codebuild-demo-project",
        "CODEBUILD_RESOLVED_SOURCE_VERSION": "3d6151b3ebc9ba70b83de319db596d7eda56e517",
        "CODEBUILD_SOURCE_REPO_URL": "https://github.com/aws/codebuild-demo-project",
        "CODEBUILD_SOURCE_VERSION": "arn:aws:s3:::bucket/pipeline/App/OGgJCVJ.zip",
        "CODEBUILD_SRC_DIR": "/tmp/src123456789/src",
        "CODEBUILD_START_TIME": "1693959305402",
        "CODEBUILD_WEBHOOK_ACTOR_ACCOUNT_ID": "123456789012",
        "CODEBUILD_WEBHOOK_BASE_REF": "CBWHBR",
        "CODEBUILD_WEBHOOK_EVENT": "CBWHE",
        "CODEBUILD_WEBHOOK_MERGE_COMMIT": "CBWHMC",
        "CODEBUILD_WEBHOOK_PREV_COMMIT": "CBWHPC",
        "CODEBUILD_WEBHOOK_HEAD_REF": "CBWHHR",
        "CODEBUILD_WEBHOOK_TRIGGER": "pr/12345",
        "HOME": "/root",
    }
