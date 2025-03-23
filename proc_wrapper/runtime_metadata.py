import copy
import json
import logging
import platform
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .common_utils import safe_get, string_to_int
from .proc_wrapper_params import ProcWrapperParams

EXECUTION_METHOD_TYPE_UNKNOWN = "Unknown"
EXECUTION_METHOD_TYPE_AWS_ECS = "AWS ECS"
EXECUTION_METHOD_TYPE_AWS_LAMBDA = "AWS Lambda"
EXECUTION_METHOD_TYPE_AWS_CODEBUILD = "AWS CodeBuild"
EXECUTION_METHOD_TYPE_AWS_EC2 = "AWS EC2"

INFRASTRUCTURE_TYPE_UNKNOWN = "Unknown"
INFRASTRUCTURE_TYPE_AWS = "AWS"

AWS_ECS_METADATA_TIMEOUT_SECONDS = 60


_logger = logging.getLogger(__name__)
_logger.addHandler(logging.NullHandler())


AWS_STS_ROLE_ARN_REGEX = r"^arn:aws:sts::(\d{12}):assumed-role/([^/]+)/[^/]+"


def get_current_aws_role_arn() -> Optional[str]:
    try:
        import boto3

        sts_client = boto3.client("sts")
        response = sts_client.get_caller_identity()
        arn = response.get("Arn")
        _logger.debug(f"Caller identity ARN: '{arn}'")

        m = re.match(AWS_STS_ROLE_ARN_REGEX, arn)
        if m:
            account_id = m.group(1)
            role_name = m.group(2)
            return f"arn:aws:iam::{account_id}:role/{role_name}"
        else:
            _logger.info(f"Unable to parse AWS role ARN '{arn}'")
    except Exception:
        _logger.info("Error getting current AWS role", exc_info=True)

    return None


@dataclass
class CommonConfiguration:
    execution_method_type: str = EXECUTION_METHOD_TYPE_UNKNOWN
    infrastructure_type: Optional[str] = INFRASTRUCTURE_TYPE_UNKNOWN
    infrastructure_settings: Optional[dict[str, Any]] = None
    allocated_cpu_units: Optional[int] = None
    allocated_memory_mb: Optional[int] = None
    ip_v4_addresses: Optional[list[str]] = None


@dataclass
class TaskConfiguration(CommonConfiguration):
    execution_method_capability_details: Optional[dict[str, Any]] = None


@dataclass
class TaskExecutionConfiguration(CommonConfiguration):
    execution_method_details: Optional[dict[str, Any]] = None


@dataclass
class RuntimeMetadata:
    task_execution_configuration: TaskExecutionConfiguration
    task_configuration: TaskConfiguration
    raw: dict[str, Any]
    derived: dict[str, Any]
    is_execution_status_source: bool = False
    exit_code: Optional[int] = None
    host_addresses: Optional[list[str]] = None
    host_names: Optional[list[str]] = None
    monitor_host_addresses: Optional[list[str]] = None
    monitor_host_names: Optional[list[str]] = None
    monitor_process_env_additions: Optional[Mapping[str, str]] = None
    default_refresh_interval: Optional[int] = None


class RuntimeMetadataFetcher:
    def fetch(
        self, env: Mapping[str, str], context: Optional[Any] = None, force: bool = False
    ) -> Optional[RuntimeMetadata]:
        return None


def populate_dict_from_env(
    dest: dict[str, Any], env: Mapping[str, str], attrs: list[str], env_prefix: str = ""
) -> dict[str, Any]:
    for attr in attrs:
        env_name = env_prefix + attr.upper()
        dest[attr] = env.get(env_name)

    return dest


class AwsEcsRuntimeMetadataFetcher(RuntimeMetadataFetcher):
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

    @dataclass
    class ClassifiedContainers:
        main_container_metadata: Optional[dict[str, Any]]
        monitor_container_metadata: Optional[dict[str, Any]]
        current_container_metadata: Optional[dict[str, Any]]

    def __init__(
        self,
        monitor_container_name: Optional[str] = None,
        main_container_name: Optional[str] = None,
        sidecar_mode: Optional[bool] = None,
    ):
        self.monitor_container_name = monitor_container_name
        self.main_container_name = main_container_name

        if sidecar_mode is None:
            if main_container_name:
                if monitor_container_name:
                    sidecar_mode = monitor_container_name != main_container_name
            elif not monitor_container_name:
                sidecar_mode = False

        self.sidecar_mode = sidecar_mode

    def fetch(
        self, env: Mapping[str, str], context: Optional[Any] = None, force: bool = False
    ) -> Optional[RuntimeMetadata]:
        container_metadata_url = env.get("ECS_CONTAINER_METADATA_URI_V4") or env.get(
            "ECS_CONTAINER_METADATA_URI"
        )

        if container_metadata_url is None:
            _logger.debug("No ECS metadata URL found")
            return None

        task_metadata: Optional[dict[str, Any]] = None

        task_metadata_url = f"{container_metadata_url}/task"

        _logger.debug(f"Fetching ECS task metadata from '{task_metadata_url}' ...")

        headers = {"Accept": "application/json"}
        try:
            req = Request(task_metadata_url, method="GET", headers=headers)
            resp = urlopen(req, timeout=AWS_ECS_METADATA_TIMEOUT_SECONDS)
            response_body = resp.read().decode("utf-8")
            task_metadata = json.loads(response_body)
        except HTTPError as http_error:
            status_code = http_error.code
            _logger.warning(
                f"Unable to fetch ECS task metadata endpoint. Response code = {status_code}."
            )
        except Exception:
            _logger.exception(
                "Unable to fetch ECS task metadata endpoint or convert metadata."
            )

        if task_metadata is None:
            return None

        containers = (task_metadata or {}).get("Containers", [])
        container_count = len(containers)

        _logger.debug(f"Found {container_count} containers in ECS task metadata")

        current_container_metadata: Optional[dict[str, Any]] = None

        if container_count == 1:
            current_container_metadata = containers[0]

            if self.sidecar_mode is None:
                self.sidecar_mode = False
            elif self.sidecar_mode:
                _logger.warning("sidecar mode requires at least 2 containers")
                return None
        else:
            _logger.debug(
                f"Fetching ECS container metadata from '{container_metadata_url}' ..."
            )

            try:
                req = Request(container_metadata_url, method="GET", headers=headers)
                resp = urlopen(req, timeout=AWS_ECS_METADATA_TIMEOUT_SECONDS)
                response_body = resp.read().decode("utf-8")
                current_container_metadata = json.loads(response_body)
            except HTTPError as http_error:
                status_code = http_error.code
                _logger.warning(
                    f"Unable to fetch ECS container metadata endpoint. Response code = {status_code}."
                )
            except Exception:
                _logger.exception(
                    "Unable to fetch ECS container metadata endpoint or convert metadata."
                )

        classified_containers = self.classify_containers(
            current_container_metadata=current_container_metadata, containers=containers
        )

        return self.convert_ecs_metadata(
            task_metadata=task_metadata, classified_containers=classified_containers
        )

    def classify_containers(
        self,
        current_container_metadata: Optional[dict[str, Any]],
        containers: list[dict[str, Any]],
    ) -> ClassifiedContainers:
        container_count = len(containers)
        current_container_name: Optional[str] = None
        main_container_metadata: Optional[dict[str, Any]] = None
        monitor_container_metadata: Optional[dict[str, Any]] = None

        if current_container_metadata:
            current_container_name = current_container_metadata.get("Name")

            if current_container_name:
                if current_container_name == self.main_container_name:
                    main_container_metadata = current_container_metadata

                    if self.sidecar_mode:
                        _logger.warning(
                            f"Main container '{self.main_container_name}' can't be the sidecar monitor container"
                        )
                    else:
                        self.sidecar_mode = False
                if current_container_name == self.monitor_container_name:
                    monitor_container_metadata = current_container_metadata

                    if (self.sidecar_mode is None) and (
                        self.main_container_name is not None
                    ):
                        self.sidecar_mode = True

        for container in containers:
            container_name = container.get("Name")
            if (
                (main_container_metadata is None)
                and (self.main_container_name is not None)
                and (container_name == self.main_container_name)
            ):
                _logger.debug(f"Found main container '{self.main_container_name}'")
                main_container_metadata = container

            if (
                (monitor_container_metadata is None)
                and (self.monitor_container_name is not None)
                and (container_name == self.monitor_container_name)
            ):
                _logger.debug(
                    f"Found monitor container '{self.monitor_container_name}'"
                )
                monitor_container_metadata = container

        if main_container_metadata is None:
            if self.main_container_name:
                _logger.warning(
                    f"Unable to find main container '{self.main_container_name}'"
                )
            elif self.sidecar_mode:
                if (container_count == 2) and (
                    (current_container_metadata or monitor_container_metadata)
                    is not None
                ):
                    candidate_monitor_container_name = cast(
                        dict[str, Any],
                        current_container_metadata or monitor_container_metadata,
                    ).get("Name")

                    if self.monitor_container_name and (
                        candidate_monitor_container_name != self.monitor_container_name
                    ):
                        _logger.warning(
                            "Unable to determine main container in sidecar mode"
                        )
                    elif containers[0].get("Name") == candidate_monitor_container_name:
                        main_container_metadata = containers[1]
                        monitor_container_metadata = (
                            monitor_container_metadata or containers[0]
                        )
                    else:
                        main_container_metadata = containers[0]
                        monitor_container_metadata = (
                            monitor_container_metadata or containers[1]
                        )
                else:
                    _logger.warning("Can't find main container in sidecar mode")
            elif (current_container_metadata is not None) and (container_count == 1):
                main_container_metadata = current_container_metadata

        if (main_container_metadata is not None) and (not self.main_container_name):
            self.main_container_name = main_container_metadata.get("Name")

        if monitor_container_metadata is None:
            if self.monitor_container_name:
                _logger.warning(
                    f"Unable to find monitor container '{self.monitor_container_name}'"
                )
            elif self.sidecar_mode:
                if (container_count == 2) and (
                    (current_container_metadata or main_container_metadata) is not None
                ):
                    first_container_name = containers[0].get("Name")

                    if current_container_metadata is None:
                        # main_container_metadata is not None
                        if first_container_name == self.main_container_name:
                            monitor_container_metadata = containers[1]
                        else:
                            monitor_container_metadata = containers[0]

                        current_container_metadata = main_container_metadata
                        current_container_name = self.main_container_name
                    else:
                        # current_container_metadata is not None
                        # main_container_metadata may or may not be None
                        if first_container_name == self.main_container_name:
                            _logger.warning(
                                "The sidecar monitor container can't be the main container {self.main_container_name}"
                            )
                        elif first_container_name == current_container_name:
                            monitor_container_metadata = current_container_metadata
                        else:
                            monitor_container_metadata = containers[1]
                else:
                    _logger.warning("Can't find monitor container in sidecar mode")
            elif current_container_metadata is not None:
                if len(containers) > 1:
                    _logger.info(
                        "Assuming that monitor container is the current container"
                    )

                monitor_container_metadata = current_container_metadata

        if (main_container_metadata is None) and (not self.main_container_name):
            _logger.info("Assuming that main container is the current container")
            main_container_metadata = current_container_metadata

        if (main_container_metadata is not None) and (not self.main_container_name):
            self.main_container_name = main_container_metadata.get("Name")

        if (monitor_container_metadata is None) and (not self.monitor_container_name):
            _logger.info("Assuming that monitor container is the current container")
            monitor_container_metadata = current_container_metadata

        if (monitor_container_metadata is not None) and (
            not self.monitor_container_name
        ):
            self.monitor_container_name = monitor_container_metadata.get("Name")

        if (
            (self.sidecar_mode is None)
            and self.main_container_name
            and self.monitor_container_name
        ):
            self.sidecar_mode = self.monitor_container_name != self.main_container_name

        return self.ClassifiedContainers(
            main_container_metadata=main_container_metadata,
            monitor_container_metadata=monitor_container_metadata,
            current_container_metadata=current_container_metadata,
        )

    def convert_ecs_metadata(
        self, task_metadata: dict[str, Any], classified_containers: ClassifiedContainers
    ) -> RuntimeMetadata:
        task_configuration = TaskConfiguration(
            execution_method_type=EXECUTION_METHOD_TYPE_AWS_ECS,
            infrastructure_type=INFRASTRUCTURE_TYPE_AWS,
        )
        task_execution_configuration = TaskExecutionConfiguration(
            execution_method_type=EXECUTION_METHOD_TYPE_AWS_ECS,
            infrastructure_type=INFRASTRUCTURE_TYPE_AWS,
        )

        cluster_arn = task_metadata.get("Cluster") or ""
        task_arn = task_metadata.get("TaskARN") or ""
        task_definition_arn = self.compute_ecs_task_definition_arn(task_metadata) or ""

        common_props: dict[str, Any] = {
            "task_definition_arn": task_definition_arn,
            "cluster_arn": cluster_arn,
        }

        execution_method: dict[str, Any] = {
            "task_arn": task_arn,
        }

        execution_method_capability: dict[str, Any] = {}

        launch_type = task_metadata.get("LaunchType")
        if launch_type:
            common_props["launch_type"] = launch_type
            common_props["supported_launch_types"] = [launch_type]

        cpu_units, memory_mb = self.extract_cpu_and_memory_limits(
            task_metadata, is_task=True
        )

        if cpu_units is not None:
            common_props["allocated_cpu_units"] = cpu_units
            task_execution_configuration.allocated_cpu_units = cpu_units
            task_configuration.allocated_cpu_units = cpu_units

        if memory_mb is not None:
            common_props["allocated_memory_mb"] = memory_mb
            task_execution_configuration.allocated_memory_mb = memory_mb
            task_configuration.allocated_memory_mb = memory_mb

        monitor_host_addresses: Optional[list[str]] = None
        monitor_host_names: Optional[list[str]] = None
        monitor_process_env_additions: dict[str, str] = {}
        monitor_container_metadata = classified_containers.monitor_container_metadata
        if monitor_container_metadata is not None:
            name = cast(str, monitor_container_metadata.get("Name"))
            common_props["monitor_container_name"] = name
            monitor_process_env_additions["PROC_WRAPPER_MONITOR_CONTAINER_NAME"] = name

            networks = monitor_container_metadata.get("Networks") or []
            monitor_host_addresses = []
            monitor_host_names = []
            for network in networks:
                ip_v4_addresses = network.get("IPv4Addresses") or []
                monitor_host_addresses += ip_v4_addresses
                hostname = network.get("PrivateDNSName")
                if hostname:
                    monitor_host_names.append(hostname)

        main_container_metadata = classified_containers.main_container_metadata
        if main_container_metadata is not None:
            name = cast(str, main_container_metadata.get("Name"))
            common_props["main_container_name"] = name
            monitor_process_env_additions["PROC_WRAPPER_MAIN_CONTAINER_NAME"] = name
            (
                common_props["main_container_cpu_units"],
                common_props["main_container_memory_mb"],
            ) = self.extract_cpu_and_memory_limits(main_container_metadata)

        if self.sidecar_mode is not None:
            monitor_process_env_additions["PROC_WRAPPER_SIDECAR_CONTAINER_MODE"] = str(
                self.sidecar_mode
            ).upper()

        containers = (task_metadata or {}).get("Containers", [])

        container_props = []
        for container in containers:
            container_prop = {}

            for k, v in self.AWS_ECS_FARGATE_CONTAINER_PROPERTY_MAPPINGS.items():
                container_prop[v] = container.get(k)

            (
                container_prop["cpu_units"],
                container_prop["memory_mb"],
            ) = self.extract_cpu_and_memory_limits(container)

            container_props.append(container_prop)

        common_props["containers"] = container_props

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

        task_aws_props = aws_props
        exit_code: Optional[int] = None
        host_addresses: Optional[list[str]] = None
        host_names: Optional[list[str]] = None

        if main_container_metadata is not None:
            exit_code = main_container_metadata.get("ExitCode")
            driver = main_container_metadata.get("LogDriver")

            logging_props = {
                "driver": driver,
            }

            prefix_to_remove: Optional[str] = None

            if driver:
                prefix_to_remove = driver + "-"

            input_log_options = main_container_metadata.get("LogOptions")

            if input_log_options:
                transformed_log_options: dict[str, Any] = {}
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

            container_networks = main_container_metadata.get("Networks")
            if container_networks is not None:
                task_execution_networks = []
                task_networks = []
                host_addresses = []
                host_names = []

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

                    ip_v4_addresses = container_network.get("IPv4Addresses") or []
                    container_network_props["ip_v4_addresses"] = ip_v4_addresses
                    host_addresses += ip_v4_addresses
                    hostname = container_network.get("PrivateDNSName")
                    if hostname:
                        host_names.append(hostname)

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
        task_configuration.infrastructure_settings = task_aws_props

        task_execution_configuration.execution_method_details = execution_method
        task_execution_configuration.infrastructure_settings = aws_props

        return RuntimeMetadata(
            task_execution_configuration=task_execution_configuration,
            task_configuration=task_configuration,
            raw={
                "task": task_metadata,
                "main_container": main_container_metadata,
                "monitor_container": monitor_container_metadata,
                "current_container": classified_containers.current_container_metadata,
            },
            derived=derived,
            is_execution_status_source=(self.sidecar_mode is True),
            exit_code=exit_code,
            host_addresses=host_addresses,
            host_names=host_names,
            monitor_host_addresses=monitor_host_addresses,
            monitor_host_names=monitor_host_names,
            monitor_process_env_additions=monitor_process_env_additions,
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
        self, task_metadata: dict[str, Any]
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

    def extract_cpu_and_memory_limits(
        self, task_or_container_metadata: dict[str, Any], is_task: bool = False
    ) -> tuple[Optional[int], Optional[int]]:
        limits = task_or_container_metadata.get("Limits")
        cpu_units: Optional[int] = None
        memory_mb: Optional[int] = None
        if isinstance(limits, dict):
            cpu_units = limits.get("CPU")
            if (
                is_task
                and (cpu_units is not None)
                and isinstance(cpu_units, (float, int))
            ):
                cpu_units = round(cpu_units * 1024)

            memory_mb = limits.get("Memory")

        return (
            cpu_units,
            memory_mb,
        )


class AwsLambdaRuntimeMetadataFetcher(RuntimeMetadataFetcher):
    AWS_LAMBDA_CLIENT_METADATA_PROPERTIES = [
        "installation_id",
        "app_title",
        "app_version_name",
        "app_version_code",
        "app_package_name",
    ]

    def fetch(
        self, env: Mapping[str, str], context: Optional[Any] = None, force: bool = False
    ) -> Optional[RuntimeMetadata]:
        # https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars.html

        if not env.get("LAMBDA_TASK_ROOT"):
            return None

        _logger.info("AWS Lambda environment detected")

        task_configuration = TaskConfiguration(
            execution_method_type=EXECUTION_METHOD_TYPE_AWS_LAMBDA
        )
        task_execution_configuration = TaskExecutionConfiguration(
            execution_method_type=EXECUTION_METHOD_TYPE_AWS_LAMBDA
        )

        allocated_memory_mb = string_to_int(env.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE"))

        task_configuration.allocated_memory_mb = allocated_memory_mb
        task_execution_configuration.allocated_memory_mb = allocated_memory_mb

        common_props: dict[str, Any] = {
            "runtime_id": env.get("AWS_EXECUTION_ENV"),
            "function_name": env.get("AWS_LAMBDA_FUNCTION_NAME"),
            "function_version": env.get("AWS_LAMBDA_FUNCTION_VERSION"),
            "init_type": env.get("AWS_LAMBDA_INITIALIZATION_TYPE"),
            "dotnet_prejit": env.get("AWS_LAMBDA_DOTNET_PREJIT"),
            "function_memory_mb": allocated_memory_mb,
            "time_zone_name": env.get("TZ"),
        }

        execution_method: dict[str, Any] = {}
        execution_method_capability: dict[str, Any] = {}

        # _HANDLER – The handler location configured on the function.
        aws_region = env.get("AWS_REGION")

        aws_props: dict[str, Any] = {
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
            extracted_identity: Optional[dict[str, Any]] = None

            if identity:
                extracted_identity = {
                    "id": safe_get(identity, "cognito_identity_id"),
                    "pool_id": safe_get(identity, "cognito_identity_pool_id"),
                }

            execution_method["cognito_identity"] = extracted_identity

            client_context = safe_get(context, "client_context")
            extracted_client_context: Optional[dict[str, Any]] = None

            if client_context:
                client = safe_get(client_context, "client")
                extracted_client: Optional[dict[str, Any]] = None

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


class AwsCodeBuildRuntimeMetadataFetcher(RuntimeMetadataFetcher):
    AWS_CODEBUILD_EXECUTION_METHOD_CAPABILITY_ATTRIBUTES = [
        "build_arn",
        "build_image",
        "batch_identifier",
        "source_repo_url",
        "source_version",
        "kms_key_id",
        "initiator",
    ]

    AWS_CODEBUILD_EXECUTION_METHOD_ATTRIBUTES = [
        "build_id",
        "batch_build_identifier",
        "public_build_url",
        "resolved_source_version",
        "src_dir",
    ]

    AWS_CODEBUILD_WEBHOOK_ATTRIBUTES = [
        "actor_account_id",
        "base_ref",
        "event",
        "merge_commit",
        "prev_commit",
        "head_ref",
        "trigger",
    ]

    def fetch(
        self, env: Mapping[str, str], context: Optional[Any] = None, force: bool = False
    ) -> Optional[RuntimeMetadata]:
        # https://docs.aws.amazon.com/codebuild/latest/userguide/build-env-ref-env-vars.html

        build_arn = env.get("CODEBUILD_BUILD_ARN")

        if not build_arn:
            return None

        _logger.info("AWS CodeBuild environment detected")

        task_configuration = TaskConfiguration(
            execution_method_type=EXECUTION_METHOD_TYPE_AWS_CODEBUILD
        )
        task_execution_configuration = TaskExecutionConfiguration(
            execution_method_type=EXECUTION_METHOD_TYPE_AWS_CODEBUILD
        )

        common_props = populate_dict_from_env(
            dest={},
            env=env,
            attrs=self.AWS_CODEBUILD_EXECUTION_METHOD_CAPABILITY_ATTRIBUTES,
            env_prefix="CODEBUILD_",
        )

        assumed_role_arn = get_current_aws_role_arn()

        if assumed_role_arn:
            common_props["assumed_role_arn"] = assumed_role_arn

        execution_method_capability: dict[str, Any] = {
            **common_props,
        }

        build_arn = common_props.get("build_arn")

        #  Strip the build ID from the full ARN for a generic ARN that can
        #  be used to start the build again.
        if build_arn:
            last_colon_index = build_arn.rfind(":")
            if last_colon_index > 0:
                execution_method_capability["build_arn"] = build_arn[0:last_colon_index]

        execution_method = populate_dict_from_env(
            dest=common_props.copy(),
            env=env,
            attrs=self.AWS_CODEBUILD_EXECUTION_METHOD_ATTRIBUTES,
            env_prefix="CODEBUILD_",
        )

        build_number_str = env.get("CODEBUILD_BUILD_NUMBER")

        if build_number_str:
            try:
                execution_method["build_number"] = int(build_number_str)
            except ValueError:
                _logger.warning(
                    f"Error parsing CODEBUILD_BUILD_NUMBER '{build_number_str}' as integer"
                )

        build_succeeding_str = env.get("CODEBUILD_BUILD_SUCCEEDING")

        if build_succeeding_str is not None:
            execution_method["build_succeeding"] = build_succeeding_str == "1"

        start_time_str = env.get("CODEBUILD_START_TIME")

        if start_time_str:
            execution_method["start_time"] = datetime.fromtimestamp(
                float(start_time_str) * 0.001
            ).isoformat()

        webhook = populate_dict_from_env(
            dest={},
            env=env,
            attrs=self.AWS_CODEBUILD_WEBHOOK_ATTRIBUTES,
            env_prefix="CODEBUILD_WEBHOOK_",
        )

        execution_method["webhook"] = webhook

        aws_region = env.get("AWS_REGION")

        aws_props: dict[str, Any] = {
            "region": aws_region,
            "network": {
                "region": aws_region,
            },
        }

        log_stream = env.get("CODEBUILD_LOG_PATH")

        if log_stream:
            aws_props["logging"] = {
                "driver": "awslogs",
                "options": {
                    "region": aws_region,
                },
            }

        task_infrastructure_settings = copy.deepcopy(aws_props)

        if log_stream:
            aws_props["logging"]["options"]["stream"] = log_stream

        derived = {"aws": aws_props}

        task_configuration.execution_method_capability_details = (
            execution_method_capability
        )
        task_configuration.infrastructure_type = INFRASTRUCTURE_TYPE_AWS
        task_configuration.infrastructure_settings = task_infrastructure_settings

        task_execution_configuration.execution_method_details = execution_method
        task_execution_configuration.infrastructure_type = INFRASTRUCTURE_TYPE_AWS
        task_execution_configuration.infrastructure_settings = aws_props

        return RuntimeMetadata(
            task_execution_configuration=task_execution_configuration,
            task_configuration=task_configuration,
            raw={},
            derived=derived,
        )


class AwsEc2RuntimeMetadataFetcher(RuntimeMetadataFetcher):
    COPIED_COMMON_PROPERTIES = [
        "ami_id",
        "ami_manifest_path",
        "instance_type",
        "reservation_id",
    ]

    COPIED_EXECUTION_METHOD_PROPERTIES = [
        "ami_launch_index",
        "autoscaling_target_lifecycle_state",
        "instance_action",
        "instance_id",
        "instance_life_cycle",
        "kernel_id"
    ]

    EXECUTION_METHOD_NETWORK_PROPERTY_MAPPINGS = {
        "device_number": "device_number",
        "interface_id": "eni_id",
        "mac": "mac_address",
        "private_hostname": "private_dns_name",
        "public_hostname": "public_dns_name",
        "private_ipv4s": "private_ip_v4_addresses",
        "public_ipv4s": "public_ip_v4_addresses",
        "ipv6s": "ip_v6_addresses",
    }

    def fetch(
        self, env: Mapping[str, str], context: Optional[Any] = None, force: bool = False
    ) -> Optional[RuntimeMetadata]:
        if not force:
            return None

        if env.get("AWS_EC2_METADATA_DISABLED") == "true":
            return None

        try:
            from ec2_metadata import ec2_metadata

            return self._fetch_internal(ec2_metadata)
        except ImportError:
            _logger.warning("ec2_metadata package not found")
            return None

    def _fetch_internal(self, ec2_metadata: Any) -> Optional[RuntimeMetadata]:
        if not ec2_metadata:
            return None

        task_configuration = TaskConfiguration(
            execution_method_type=EXECUTION_METHOD_TYPE_AWS_EC2,
            infrastructure_type=INFRASTRUCTURE_TYPE_AWS,
        )
        task_execution_configuration = TaskExecutionConfiguration(
            execution_method_type=EXECUTION_METHOD_TYPE_AWS_EC2,
            infrastructure_type=INFRASTRUCTURE_TYPE_AWS,
        )

        region = ec2_metadata.region

        common_props: dict[str, Any] = {}

        for p in self.COPIED_COMMON_PROPERTIES:
            common_props[p] = getattr(ec2_metadata, p)

        if ec2_metadata.instance_profile_id:
            common_props["instance_profile"] = {
                "id": ec2_metadata.instance_profile_id,
                "name": ec2_metadata.instance_profile_name,
                "arn": ec2_metadata.instance_profile_arn,
            }

        execution_method: dict[str, Any] = {}

        for p in self.COPIED_EXECUTION_METHOD_PROPERTIES:
            execution_method[p] = getattr(ec2_metadata, p)

        if ec2_metadata.spot_instance_action:
            execution_method[
                "spot_instance_action"
            ] = ec2_metadata.spot_instance_action.action

            if ec2_metadata.spot_instance_action.time:
                execution_method[
                    "spot_instance_action_time"
                ] = ec2_metadata.spot_instance_action.time.isoformat()

        execution_method_capability: dict[str, Any] = {}

        execution_method.update(common_props)
        execution_method_capability.update(common_props)

        task_execution_networks = []
        task_networks = []
        host_addresses = []
        host_names = []

        for name, iface in ec2_metadata.network_interfaces.items():
            network_props: dict[str, Any] = {
                "name": name,
                "aws_account_id": iface.owner_id,
                "vpc": {
                    "id": iface.vpc_id,
                    "ip_v4_cidr_blocks": [b for b in iface.vpc_ipv4_cidr_blocks if b],
                    "ip_v6_cidr_blocks": iface.vpc_ipv6_cidr_blocks,
                },
                "subnet": {
                    "id": iface.subnet_id,
                    "ip_v4_cidr_blocks": [iface.subnet_ipv4_cidr_block],
                    "ip_v6_cidr_blocks": iface.subnet_ipv6_cidr_blocks,
                },
            }

            sgs = []
            for t in zip(iface.security_group_ids, iface.security_groups):
                sgs.append(
                    {
                        "id": t[0],
                        "name": t[1],
                    }
                )

            network_props["security_groups"] = sgs

            task_networks.append(network_props.copy())

            for in_prop, out_prop in self.EXECUTION_METHOD_NETWORK_PROPERTY_MAPPINGS.items():
                network_props[out_prop] = getattr(iface, in_prop)

            ip_v4_addresses = list(iface.ipv4_associations.keys())
            network_props["ip_v4_addresses"] = ip_v4_addresses
            host_addresses += ip_v4_addresses

            private_hostname = iface.private_hostname
            if private_hostname:
                host_names.append(private_hostname)

            public_hostname = iface.public_hostname
            if public_hostname:
                host_names.append(public_hostname)

            task_execution_networks.append(network_props)

        all_network_props = {
            "region": region,
            "availability_zone": ec2_metadata.availability_zone,
        }

        aws_props = {
            "aws_account_id": ec2_metadata.account_id,
            "domain": ec2_metadata.domain,
            "partition": ec2_metadata.partition,
        }

        task_aws_props = aws_props.copy()
        task_network_props = all_network_props.copy()
        task_network_props["networks"] = task_networks
        task_aws_props["network"] = task_network_props

        all_network_props["networks"] = task_execution_networks
        all_network_props["mac_address"] = ec2_metadata.mac
        aws_props["network"] = all_network_props

        try:
            aws_props["tags"] = dict(ec2_metadata.tags)
        except Exception:
            _logger.info("tags not available in EC2 metadata, this must be enabled")

        derived = {"aws": aws_props}

        task_configuration.execution_method_capability_details = (
            execution_method_capability
        )
        task_configuration.infrastructure_settings = task_aws_props

        task_execution_configuration.execution_method_details = execution_method
        task_execution_configuration.infrastructure_settings = aws_props

        return RuntimeMetadata(
            task_execution_configuration=task_execution_configuration,
            task_configuration=task_configuration,
            raw={},
            derived=derived,
            host_addresses=host_addresses,
            host_names=host_names,
        )


class GenericRuntimeMetadataFetcher(RuntimeMetadataFetcher):
    def fetch(
        self, env: Mapping[str, str], context: Optional[Any] = None, force: bool = False
    ) -> Optional[RuntimeMetadata]:
        host_names: list[str] = []

        uname = platform.uname()

        hostname = uname.node
        if hostname:
            host_names.append(hostname)

        return RuntimeMetadata(
            task_execution_configuration=TaskExecutionConfiguration(),
            task_configuration=TaskConfiguration(),
            raw={},
            derived={},
            host_names=host_names,
        )


class DefaultRuntimeMetadataFetcher(RuntimeMetadataFetcher):
    def __init__(self, params: Optional[ProcWrapperParams] = None):
        self.execution_method_type: Optional[str] = (
            params.execution_method_type if params else None
        )
        self.monitor_container_name: Optional[str] = (
            params.monitor_container_name if params else None
        )
        self.main_container_name: Optional[str] = (
            params.main_container_name if params else None
        )
        self.sidecar_container_mode: Optional[bool] = (
            params.sidecar_container_mode if params else None
        )

        self.fetcher: Optional[RuntimeMetadataFetcher] = None

    def fetch(
        self, env: Mapping[str, str], context: Optional[Any] = None, force: bool = False
    ) -> Optional[RuntimeMetadata]:
        _logger.debug("Entering fetch_runtime_metadata() ...")

        if self.fetcher:
            return self.fetcher.fetch(env=env, context=context, force=True)

        fetchers = {
            EXECUTION_METHOD_TYPE_AWS_CODEBUILD: AwsCodeBuildRuntimeMetadataFetcher(),
            EXECUTION_METHOD_TYPE_AWS_ECS: AwsEcsRuntimeMetadataFetcher(
                monitor_container_name=self.monitor_container_name,
                main_container_name=self.main_container_name,
                sidecar_mode=self.sidecar_container_mode,
            ),
            EXECUTION_METHOD_TYPE_AWS_LAMBDA: AwsLambdaRuntimeMetadataFetcher(),
            EXECUTION_METHOD_TYPE_AWS_EC2: AwsEc2RuntimeMetadataFetcher(),
            EXECUTION_METHOD_TYPE_UNKNOWN: GenericRuntimeMetadataFetcher(),
        }

        runtime_metadata: Optional[RuntimeMetadata] = None

        if self.execution_method_type:
            fetcher = fetchers.get(self.execution_method_type)

            if fetcher:
                self.fetcher = fetcher
                runtime_metadata = fetcher.fetch(env=env, context=context, force=True)

        if not self.fetcher:
            for fetcher in fetchers.values():
                runtime_metadata = fetcher.fetch(env=env, context=context, force=False)
                if runtime_metadata:
                    self.fetcher = fetcher
                    break

        _logger.debug(
            f"Done fetching runtime metadata, got {runtime_metadata or 'N/A'}"
        )
        return runtime_metadata
