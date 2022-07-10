import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, NamedTuple, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .common_utils import safe_get, string_to_int

EXECUTION_METHOD_TYPE_UNKNOWN = "Unknown"
EXECUTION_METHOD_TYPE_AWS_ECS = "AWS ECS"
EXECUTION_METHOD_TYPE_AWS_LAMBDA = "AWS Lambda"

INFRASTRUCTURE_TYPE_AWS = "AWS"

AWS_ECS_METADATA_TIMEOUT_SECONDS = 60


_logger = logging.getLogger(__name__)
_logger.addHandler(logging.NullHandler())


@dataclass
class CommonConfiguration:
    execution_method_type: str = EXECUTION_METHOD_TYPE_UNKNOWN
    infrastructure_type: Optional[str] = None
    infrastructure_settings: Optional[Dict[str, Any]] = None
    allocated_cpu_units: Optional[int] = None
    allocated_memory_mb: Optional[int] = None
    ip_v4_addresses: Optional[List[str]] = None


@dataclass
class TaskConfiguration(CommonConfiguration):
    execution_method_capability_details: Optional[Dict[str, Any]] = None


@dataclass
class TaskExecutionConfiguration(CommonConfiguration):
    execution_method_details: Optional[Dict[str, Any]] = None


class RuntimeMetadata(NamedTuple):
    task_execution_configuration: TaskExecutionConfiguration
    task_configuration: TaskConfiguration
    raw: Dict[str, Any]
    derived: Dict[str, Any]


class RuntimeMetadataFetcher:
    def fetch(
        self, env: Mapping[str, str], context: Optional[Any] = None
    ) -> Optional[RuntimeMetadata]:
        return None


class DefaultRuntimeMetadataFetcher(RuntimeMetadataFetcher):
    AWS_ECS_FARGATE_CONTAINER_PROPERTY_MAPPINGS = {
        "DockerId": "docker_id",
        "Name": "name",
        "DockerName": "docker_name",
        "Image": "image_name",
        "ImageID": "image_id",
        "Labels": "labels",
        "ContainerARN": "container_arn",
    }

    AWS_ECS_FARGATE_CONTAINER_NETWORK_PROPERTY_MAPPINGS = {
        "NetworkMode": "network_mode",
        "IPv4SubnetCIDRBlock": "ip_v4_subnet_cidr_block",
        "DomainNameServers": "dns_servers",
        "DomainNameSearchList": "dns_search_list",
        "PrivateDNSName": "private_dns_name",
        "SubnetGatewayIpv4Address": "subnet_gateway_ip_v4_address",
    }

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
        container_metadata_url = env.get("ECS_CONTAINER_METADATA_URI_V4") or env.get(
            "ECS_CONTAINER_METADATA_URI"
        )

        if container_metadata_url:
            parsed_task_metadata: Optional[dict[str, Any]] = None
            parsed_container_metadata: Optional[dict[str, Any]] = None

            task_metadata_url = f"{container_metadata_url}/task"

            _logger.debug(f"Fetching ECS task metadata from '{task_metadata_url}' ...")

            headers = {"Accept": "application/json"}
            try:
                req = Request(task_metadata_url, method="GET", headers=headers)
                resp = urlopen(req, timeout=AWS_ECS_METADATA_TIMEOUT_SECONDS)
                response_body = resp.read().decode("utf-8")
                parsed_task_metadata = json.loads(response_body)
            except HTTPError as http_error:
                status_code = http_error.code
                _logger.warning(
                    f"Unable to fetch ECS task metadata endpoint. Response code = {status_code}."
                )
            except Exception:
                _logger.exception(
                    "Unable to fetch ECS task metadata endpoint or convert metadata."
                )

            if parsed_task_metadata is None:
                return None

            _logger.debug(
                f"Fetching ECS container metadata from '{container_metadata_url}' ..."
            )

            try:
                req = Request(container_metadata_url, method="GET", headers=headers)
                resp = urlopen(req, timeout=AWS_ECS_METADATA_TIMEOUT_SECONDS)
                response_body = resp.read().decode("utf-8")
                parsed_container_metadata = json.loads(response_body)
            except HTTPError as http_error:
                status_code = http_error.code
                _logger.warning(
                    f"Unable to fetch ECS container metadata endpoint. Response code = {status_code}."
                )
            except Exception:
                _logger.exception(
                    "Unable to fetch ECS container metadata endpoint or convert metadata."
                )

            return self.convert_ecs_metadata(
                parsed_task_metadata, parsed_container_metadata
            )
        else:
            _logger.debug("No ECS metadata URL found")

        return None

    def convert_ecs_metadata(
        self,
        task_metadata: Dict[str, Any],
        container_metadata: Optional[Dict[str, Any]],
    ) -> RuntimeMetadata:
        task_configuration = TaskConfiguration(
            execution_method_type=EXECUTION_METHOD_TYPE_AWS_ECS
        )
        task_execution_configuration = TaskExecutionConfiguration(
            execution_method_type=EXECUTION_METHOD_TYPE_AWS_ECS
        )

        cluster_arn = task_metadata.get("Cluster") or ""
        task_arn = task_metadata.get("TaskARN") or ""
        task_definition_arn = self.compute_ecs_task_definition_arn(task_metadata) or ""

        common_props: Dict[str, Any] = {
            "task_definition_arn": task_definition_arn,
            "cluster_arn": cluster_arn,
        }

        execution_method: Dict[str, Any] = {
            "task_arn": task_arn,
        }

        execution_method_capability: Dict[str, Any] = {}

        launch_type = task_metadata.get("LaunchType")
        if launch_type:
            common_props["launch_type"] = launch_type
            common_props["supported_launch_types"] = [launch_type]

        limits = task_metadata.get("Limits")
        if isinstance(limits, dict):
            cpu_fraction = limits.get("CPU")
            if (cpu_fraction is not None) and isinstance(cpu_fraction, (float, int)):
                cpu_units = round(cpu_fraction * 1024)
                common_props["allocated_cpu_units"] = cpu_units
                task_execution_configuration.allocated_cpu_units = cpu_units
                task_configuration.allocated_cpu_units = cpu_units

            memory_mb = limits.get("Memory")
            if memory_mb is not None:
                common_props["allocated_memory_mb"] = memory_mb
                task_execution_configuration.allocated_memory_mb = memory_mb
                task_configuration.allocated_memory_mb = memory_mb

        execution_method.update(common_props)
        execution_method_capability.update(common_props)

        # Only available for Fargate platform 1.4+
        az = task_metadata.get("AvailabilityZone")

        if az:
            # Remove the last character, e.g. "a" from "us-west-1a"
            region = az[0:-1]
        else:
            region = self.compute_region_from_ecs_cluster_arn(cluster_arn)

        network_props = {"region": region}

        aws_props = {"network": network_props}

        if container_metadata is not None:
            container_props = {}

            for (
                in_key,
                out_key,
            ) in self.AWS_ECS_FARGATE_CONTAINER_PROPERTY_MAPPINGS.items():
                container_props[out_key] = container_metadata.get(in_key)

            execution_method["container"] = container_props

            driver = container_metadata.get("LogDriver")

            logging_props = {
                "driver": driver,
            }

            prefix_to_remove: Optional[str] = None

            if driver:
                prefix_to_remove = driver + "-"

            input_log_options = container_metadata.get("LogOptions")

            if input_log_options:
                transformed_log_options: Dict[str, Any] = {}
                for k, v in input_log_options.items():
                    transformed_key = k

                    # Use stripprefix() once the min python version is 3.9
                    if prefix_to_remove and k.startswith(prefix_to_remove):
                        transformed_key = k[len(prefix_to_remove) :]

                    # create-group => create_group
                    transformed_key = transformed_key.replace("-", "_")
                    transformed_log_options[transformed_key] = v

                logging_props["options"] = transformed_log_options

            aws_props["logging"] = logging_props

            task_aws_props = aws_props.copy()
            task_network_props = network_props.copy()
            task_aws_props["network"] = task_network_props

            if az:
                network_props["availability_zone"] = az

            container_networks = container_metadata.get("Networks")
            if container_networks is not None:
                task_execution_networks = []
                task_networks = []

                for container_network in container_networks:
                    container_network_props = {
                        "network_mode": container_network.get("NetworkMode")
                    }

                    for (
                        in_key,
                        out_key,
                    ) in (
                        self.AWS_ECS_FARGATE_CONTAINER_NETWORK_PROPERTY_MAPPINGS.items()
                    ):
                        container_network_props[out_key] = container_network.get(in_key)

                    task_networks.append(container_network_props.copy())

                    ip_v4_addresses = container_network.get("IPv4Addresses")
                    container_network_props["ip_v4_addresses"] = ip_v4_addresses

                    if task_execution_configuration.ip_v4_addresses is None:
                        task_execution_configuration.ip_v4_addresses = []

                    task_execution_configuration.ip_v4_addresses += ip_v4_addresses

                    container_network_props["mac_address"] = container_network.get(
                        "MACAddress"
                    )

                    task_execution_networks.append(container_network_props)

                network_props["networks"] = task_execution_networks
                task_network_props["networks"] = task_networks

        derived = {"aws": aws_props}

        task_configuration.execution_method_capability_details = (
            execution_method_capability
        )
        task_configuration.infrastructure_type = INFRASTRUCTURE_TYPE_AWS
        task_configuration.infrastructure_settings = task_aws_props

        task_execution_configuration.execution_method_details = execution_method
        task_execution_configuration.infrastructure_type = INFRASTRUCTURE_TYPE_AWS
        task_execution_configuration.infrastructure_settings = aws_props

        return RuntimeMetadata(
            task_execution_configuration=task_execution_configuration,
            task_configuration=task_configuration,
            raw={"task": task_metadata, "container": container_metadata},
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
        self, task_metadata: Dict[str, Any]
    ) -> Optional[str]:
        task_arn = task_metadata.get("TaskARN")
        family = task_metadata.get("Family")
        revision = task_metadata.get("Revision")

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

        task_configuration = TaskConfiguration(
            execution_method_type=EXECUTION_METHOD_TYPE_AWS_LAMBDA
        )
        task_execution_configuration = TaskExecutionConfiguration(
            execution_method_type=EXECUTION_METHOD_TYPE_AWS_LAMBDA
        )

        allocated_memory_mb = string_to_int(env.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE"))

        task_configuration.allocated_memory_mb = allocated_memory_mb
        task_execution_configuration.allocated_memory_mb = allocated_memory_mb

        common_props: Dict[str, Any] = {
            "runtime_id": env.get("AWS_EXECUTION_ENV"),
            "function_name": env.get("AWS_LAMBDA_FUNCTION_NAME"),
            "function_version": env.get("AWS_LAMBDA_FUNCTION_VERSION"),
            "init_type": env.get("AWS_LAMBDA_INITIALIZATION_TYPE"),
            "dotnet_prejit": env.get("AWS_LAMBDA_DOTNET_PREJIT"),
            "function_memory_mb": allocated_memory_mb,
            "time_zone_name": env.get("TZ"),
        }

        execution_method: Dict[str, Any] = {}
        execution_method_capability: Dict[str, Any] = {}

        # _HANDLER – The handler location configured on the function.
        aws_region = env.get("AWS_REGION")

        aws_props: Dict[str, Any] = {
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
                "driver": "awslogs",
                "options": {
                    "group": log_group_name,
                    "region": aws_region,
                    "stream": env.get("AWS_LAMBDA_LOG_STREAM_NAME"),
                },
            }

        task_infrastructure_settings = aws_props.copy()

        aws_props["xray"]["context_missing"] = env.get("AWS_XRAY_CONTEXT_MISSING")

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

            task_configuration.execution_method_capability_details = (
                execution_method_capability
            )
            task_configuration.infrastructure_type = INFRASTRUCTURE_TYPE_AWS
            task_configuration.infrastructure_settings = task_infrastructure_settings

            task_execution_configuration.execution_method_details = execution_method
            task_execution_configuration.infrastructure_type = INFRASTRUCTURE_TYPE_AWS
            task_execution_configuration.infrastructure_settings = aws_props

            derived = {"aws": aws_props}

            return RuntimeMetadata(
                task_execution_configuration=task_execution_configuration,
                task_configuration=task_configuration,
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
