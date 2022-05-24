import json
import logging
import time
from typing import Any, Dict, Mapping, NamedTuple, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .common_utils import safe_get, string_to_int

AWS_ECS_METADATA_TIMEOUT_SECONDS = 60


_logger = logging.getLogger(__name__)
_logger.addHandler(logging.NullHandler())


class RuntimeMetadata(NamedTuple):
    execution_method: Dict[str, Any]
    execution_method_capability: Dict[str, Any]
    raw: Dict[str, Any]
    derived: Dict[str, Any]


class RuntimeMetadataFetcher:
    def fetch(
        self, env: Mapping[str, str], context: Optional[Any] = None
    ) -> Optional[RuntimeMetadata]:
        return None


class DefaultRuntimeMetadataFetcher(RuntimeMetadataFetcher):
    AWS_LAMBDA_CLIENT_METADATA_PROPERTIES = [
        "installation_id",
        "app_title",
        "app_version_name",
        "app_version_code",
        "app_package_name",
    ]

    def __init__(self):
        self.runtime_metadata: Optional[RuntimeMetadata] = None
        self.fetched_at: Optional[float] = None

    def fetch(
        self, env: Mapping[str, str], context: Optional[Any] = None
    ) -> Optional[RuntimeMetadata]:
        _logger.debug("Entering fetch_runtime_metadata() ...")

        # Don't refetch if we have already attempted previously
        if self.runtime_metadata or self.fetched_at:
            _logger.debug(
                "Runtime metadata already fetched, returning existing metadata."
            )
            return self.runtime_metadata

        self.runtime_metadata = self.fetch_ecs_container_metadata(env=env)

        if not self.runtime_metadata:
            self.runtime_metadata = self.fetch_aws_lambda_metadata(
                env=env, context=context
            )

        self.fetched_at = time.time()

        _logger.debug(f"Done fetching runtime metadata, got {self.runtime_metadata}")
        return self.runtime_metadata

    def fetch_ecs_container_metadata(
        self, env: Mapping[str, str]
    ) -> Optional[RuntimeMetadata]:
        task_metadata_url = env.get("ECS_CONTAINER_METADATA_URI_V4") or env.get(
            "ECS_CONTAINER_METADATA_URI"
        )

        if task_metadata_url:
            url = f"{task_metadata_url}/task"

            _logger.debug(f"Fetching ECS task metadata from '{task_metadata_url}' ...")

            headers = {"Accept": "application/json"}
            try:
                req = Request(url, method="GET", headers=headers)
                resp = urlopen(req, timeout=AWS_ECS_METADATA_TIMEOUT_SECONDS)
                response_body = resp.read().decode("utf-8")
                parsed_metadata = json.loads(response_body)
                return self.convert_ecs_task_metadata(parsed_metadata)
            except HTTPError as http_error:
                status_code = http_error.code
                _logger.warning(
                    f"Unable to fetch ECS task metadata endpoint. Response code = {status_code}."
                )
            except Exception:
                _logger.exception(
                    "Unable to fetch ECS task metadata endpoint or convert metadata."
                )
        else:
            _logger.debug("No ECS metadata URL found")

        return None

    def convert_ecs_task_metadata(self, metadata: Dict[str, Any]) -> RuntimeMetadata:
        cluster_arn = metadata.get("Cluster") or ""
        task_arn = metadata.get("TaskARN") or ""
        task_definition_arn = self.compute_ecs_task_definition_arn(metadata) or ""

        common_props: Dict[str, Any] = {
            "type": "AWS ECS",
            "task_definition_arn": task_definition_arn,
        }

        execution_method: Dict[str, Any] = {
            "task_arn": task_arn,
            "cluster_arn": cluster_arn,
        }

        execution_method_capability: Dict[str, Any] = {
            "default_cluster_arn": cluster_arn
        }

        launch_type = metadata.get("LaunchType")
        if launch_type:
            execution_method["launch_type"] = launch_type
            execution_method_capability["default_launch_type"] = launch_type
            execution_method_capability["supported_launch_types"] = [launch_type]

        limits = metadata.get("Limits")
        if isinstance(limits, dict):
            cpu_fraction = limits.get("CPU")
            if (cpu_fraction is not None) and isinstance(cpu_fraction, (float, int)):
                common_props["allocated_cpu_units"] = round(cpu_fraction * 1024)

            memory_mb = limits.get("Memory")
            if memory_mb is not None:
                common_props["allocated_memory_mb"] = memory_mb

        execution_method.update(common_props)
        execution_method_capability.update(common_props)

        # Only available for Fargate platform 1.4+
        az = metadata.get("AvailabilityZone")

        if az:
            # Remove the last character, e.g. "a" from "us-west-1a"
            region = az[0:-1]
        else:
            region = self.compute_region_from_ecs_cluster_arn(cluster_arn)

        aws_props = {
            "network": {
                "availability_zone": az,
                "region": region,
            },
        }

        derived = {"aws": aws_props}

        return RuntimeMetadata(
            execution_method=execution_method,
            execution_method_capability=execution_method_capability,
            raw=metadata,
            derived=derived,
        )

    def compute_region_from_ecs_cluster_arn(
        self, cluster_arn: Optional[str]
    ) -> Optional[str]:
        if not cluster_arn:
            return None

        if cluster_arn.startswith("arn:aws:ecs:"):
            parts = cluster_arn.split(":")
            return parts[3]

        _logger.warning(f"Can't determine AWS region from cluster ARN '{cluster_arn}'")
        return None

    def compute_ecs_task_definition_arn(
        self, metadata: Dict[str, Any]
    ) -> Optional[str]:
        task_arn = metadata.get("TaskARN")
        family = metadata.get("Family")
        revision = metadata.get("Revision")

        if not (task_arn and family and revision):
            _logger.warning(
                "Can't compute ECS task definition ARN: task_arn = {task_arn}, family = {family}, revision = {revision}"
            )
            return None

        prefix_end_index = task_arn.find(":task/")

        if prefix_end_index < 0:
            _logger.warning(
                "Can't compute ECS task definition ARN: task_arn = {task_arn} has an unexpected format"
            )
            return None

        return (
            task_arn[0:prefix_end_index] + ":task-definition/" + family + ":" + revision
        )

    def fetch_aws_lambda_metadata(
        self, env: Mapping[str, str], context: Optional[Any]
    ) -> Optional[RuntimeMetadata]:
        # https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars.html

        if not env.get("LAMBDA_TASK_ROOT"):
            return None

        _logger.debug("AWS Lambda environment detected")

        common_props: Dict[str, Any] = {
            "type": "AWS Lambda",
            "runtime_id": env.get("AWS_EXECUTION_ENV"),
            "function_name": env.get("AWS_LAMBDA_FUNCTION_NAME"),
            "function_version": env.get("AWS_LAMBDA_FUNCTION_VERSION"),
            "init_type": env.get("AWS_LAMBDA_INITIALIZATION_TYPE"),
            "dotnet_prejit": env.get("AWS_LAMBDA_DOTNET_PREJIT"),
            "allocated_memory_mb": string_to_int(
                env.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE")
            ),
            "time_zone_name": env.get("TZ"),
        }

        execution_method: Dict[str, Any] = {}
        execution_method_capability: Dict[str, Any] = {}

        # _HANDLER – The handler location configured on the function.
        aws_region = env.get("AWS_REGION")

        aws_props = {
            "network": {
                "region": aws_region,
            },
            "xray": {
                "trace_id": env.get("_X_AMZN_TRACE_ID"),
            },
        }

        log_group_name = env.get("AWS_LAMBDA_LOG_GROUP_NAME")

        if log_group_name:
            aws_props["logging"] = {
                "group_name": log_group_name,
                "stream_name": env.get("AWS_LAMBDA_LOG_STREAM_NAME"),
            }

        execution_method_capability["aws"] = aws_props.copy()

        aws_props["xray"]["context_missing"] = env.get("AWS_XRAY_CONTEXT_MISSING")

        execution_method["aws"] = aws_props

        if context and hasattr(context, "invoked_function_arn"):
            # https://docs.aws.amazon.com/lambda/latest/dg/python-context.html
            common_props["function_arn"] = context.invoked_function_arn

            execution_method["aws_request_id"] = safe_get(context, "aws_request_id")

            identity = safe_get(context, "identity")
            extracted_identity: Optional[Dict[str, Any]] = None

            if identity:
                extracted_identity = {
                    "id": safe_get(identity, "cognito_identity_id"),
                    "pool_id": safe_get(identity, "cognito_identity_pool_id"),
                }

            execution_method["cognito_identity"] = extracted_identity

            client_context = safe_get(context, "client_context")
            extracted_client_context: Optional[Dict[str, Any]] = None

            if client_context:
                client = safe_get(client_context, "client")
                extracted_client: Optional[Dict[str, Any]] = None

                if client:
                    extracted_client = {}
                    for p in self.AWS_LAMBDA_CLIENT_METADATA_PROPERTIES:
                        extracted_client[p] = safe_get(client, p)

                extracted_client_context = {"client": extracted_client}

            execution_method["client_context"] = extracted_client_context

            execution_method.update(common_props)
            execution_method_capability.update(common_props)

            derived = {"aws": aws_props}

            return RuntimeMetadata(
                execution_method=execution_method,
                execution_method_capability=execution_method_capability,
                raw={},
                derived=derived,
            )

        _logger.warning(
            """
Detected AWS Lambda environment, but unexpected context found. \
Ensure you pass it to the Lambda entrypoint to the constructor of \
ProcWrapper.
"""
        )
        return None
