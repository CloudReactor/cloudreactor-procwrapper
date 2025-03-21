import argparse
import json
import logging
import os
import re
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Optional, Union, cast

from .common_constants import (
    EXTENSION_TO_FORMAT,
    FORMAT_DOTENV,
    FORMAT_JSON,
    FORMAT_TEXT,
    FORMAT_YAML,
    UNSET_VALUE,
)
from .common_utils import (
    best_effort_deep_merge,
    coalesce,
    encode_int,
    string_to_bool,
    string_to_float,
    string_to_int,
)

if TYPE_CHECKING:
    from .runtime_metadata import RuntimeMetadata

DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_MAX_LOG_LINE_LENGTH = 1000

HEARTBEAT_DELAY_TOLERANCE_SECONDS = 60

DEFAULT_API_BASE_URL = "https://api.cloudreactor.io"
DEFAULT_API_REQUEST_TIMEOUT_SECONDS = 30
DEFAULT_API_ERROR_TIMEOUT_SECONDS = 300
DEFAULT_API_RETRY_DELAY_SECONDS = 120
DEFAULT_API_RESUME_DELAY_SECONDS = 600
DEFAULT_API_TASK_EXECUTION_CREATION_TIMEOUT_SECONDS = 300
DEFAULT_API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY_SECONDS = 120
DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_CREATION_TIMEOUT_SECONDS = 1800
DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_CREATION_CONFLICT_RETRY_DELAY_SECONDS = 60
DEFAULT_API_HEARTBEAT_INTERVAL_SECONDS = 300
DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_HEARTBEAT_INTERVAL_SECONDS = 30
DEFAULT_API_FINAL_UPDATE_TIMEOUT_SECONDS = 1800

CONFIG_MERGE_STRATEGY_DEEP = "DEEP"
CONFIG_MERGE_STRATEGY_SHALLOW = "SHALLOW"
DEFAULT_CONFIG_MERGE_STRATEGY = CONFIG_MERGE_STRATEGY_DEEP
NATIVE_CONFIG_MERGE_STRATEGIES = [
    CONFIG_MERGE_STRATEGY_DEEP,
    CONFIG_MERGE_STRATEGY_SHALLOW,
]
DEFAULT_MAX_CONFIG_RESOLUTION_ITERATIONS = 3
DEFAULT_MAX_CONFIG_RESOLUTION_DEPTH = 5
DEFAULT_ENV_VAR_NAME_FOR_CONFIG = "TASK_CONFIG"
DEFAULT_CONFIG_VAR_NAME_FOR_ENV = "ENV"
DEFAULT_RESOLVABLE_ENV_VAR_NAME_PREFIX = ""
DEFAULT_RESOLVABLE_ENV_VAR_NAME_SUFFIX = "_FOR_PROC_WRAPPER_TO_RESOLVE"
DEFAULT_RESOLVABLE_CONFIG_PROPERTY_NAME_PREFIX = ""
DEFAULT_RESOLVABLE_CONFIG_PROPERTY_NAME_SUFFIX = "__to_resolve"

CLOUDREACTOR_CONTEXT_INPUT_PROPERTY_NAME = "cloudreactor_context"
PROC_WRAPPER_PARAMS_CONFIG_PROPERTY_NAME = "proc_wrapper_params"

CONFIG_RESOLVER_PROPERTIES_COPIED_FROM_CONFIG = [
    "log_secrets",
    "env_locations",
    "config_locations",
    "config_merge_strategy",
    "overwrite_env_during_resolution",
    "max_config_resolution_depth",
    "max_config_resolution_iterations",
    "config_ttl",
    "fail_fast_config_resolution",
    "resolved_env_var_name_prefix",
    "resolved_env_var_name_suffix",
    "resolved_config_property_name_prefix",
    "resolved_config_property_name_suffix",
    "env_var_name_for_config",
    "config_property_name_for_env",
    "env_output_filename",
    "env_output_format",
    "config_output_filename",
    "config_output_format",
]

IMMUTABLE_PROPERTIES_COPIED_FROM_CONFIG = [
    "api_managed_probability",
    "api_failure_report_probability",
    "api_timeout_report_probability",
    "schedule",
    "max_concurrency",
    "max_conflicting_age",
    "offline_mode",
    "prevent_offline_execution",
    "service",
    "deployment",
    "api_base_url",
    "api_heartbeat_interval",
    "enable_status_update_listener",
    "status_update_socket_port",
    "status_update_message_max_bytes",
    "status_update_interval",
    "log_level",
    "include_timestamps_in_log",
    "exit_after_writing_variables",
    "input_env_var_name",
    "input_filename",
    "cleanup_input_file",
    "input_value_format",
    "send_input_value",
    "log_input_value",
    "result_filename",
    "result_value_format",
    "cleanup_result_file",
    "log_result_value",
    "num_log_lines_sent_on_failure",
    "num_log_lines_sent_on_timeout",
    "num_log_lines_sent_on_success",
    # "log_lines_sent_on_heartbeat",
    "max_log_line_length",
    "merge_stdout_and_stderr_logs",
    "ignore_stdout",
    "ignore_stderr",
]

MUTABLE_PROPERTIES_COPIED_FROM_CONFIG = [
    "api_key",
    "api_request_timeout",
    "api_error_timeout",
    "api_retry_delay",
    "api_resume_delay",
    "api_task_execution_creation_error_timeout",
    "api_task_execution_creation_conflict_timeout",
    "api_task_execution_creation_conflict_retry_delay",
    "process_timeout",
    "process_max_retries",
    "process_retry_delay",
    "command",
    "command_line",
    "shell_mode",
    "strip_shell_wrapping",
    "work_dir",
    "process_termination_grace_period",
    "process_check_interval",
    "process_group_termination",
    "send_pid",
    "send_hostname",
    "send_runtime_metadata",
    "runtime_metadata_refresh_interval",
]


PROPERTIES_COPIED_FROM_ROLLBAR_CONFIG = [
    "access_token",
    "retries",
    "retry_delay",
    "timeout",
]

SHELL_MODE_AUTO = "auto"
SHELL_MODE_FORCE_ENABLE = "enable"
SHELL_MODE_FORCE_DISABLE = "disable"
DEFAULT_SHELL_MODE = SHELL_MODE_AUTO

DEFAULT_STATUS_UPDATE_SOCKET_PORT = 2373
DEFAULT_STATUS_UPDATE_MESSAGE_MAX_BYTES = 64 * 1024

DEFAULT_ROLLBAR_TIMEOUT_SECONDS = 30
DEFAULT_ROLLBAR_RETRIES = 2
DEFAULT_ROLLBAR_RETRY_DELAY_SECONDS = 120

DEFAULT_PROCESS_CHECK_INTERVAL_SECONDS = 10
DEFAULT_PROCESS_RETRY_DELAY_SECONDS = 60
DEFAULT_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS = 30

UNSET_INT_VALUE = -1000000

SHELL_COMMAND_REGEX = re.compile(r"[^\w\-/. ]")

# Detects sh, bash, csh, zsh, ash, dash, and fish shells
SHELL_WRAPPER_REGEX = re.compile(r"^\s*(/\w+)*/(ba|c|z|fi|d?a)?sh\s+-c\s+")

_logger = logging.getLogger(__name__)
_logger.addHandler(logging.NullHandler())


@dataclass
class ConfigResolverParamValidationErrors:
    config_resolver_errors: dict[str, list[str]]
    config_resolver_warnings: dict[str, list[str]]

    def log(self):
        if len(self.config_resolver_errors) > 0:
            _logger.error(f"config resolver errors = {self.config_resolver_errors}")
        else:
            _logger.debug("No config resolver errors")

        if len(self.config_resolver_warnings) > 0:
            _logger.error(f"config resolver warnings = {self.config_resolver_warnings}")
        else:
            _logger.debug("No config resolver warnings")


@dataclass
class ProcWrapperParamValidationErrors(ConfigResolverParamValidationErrors):
    process_errors: dict[str, list[str]]
    process_warnings: dict[str, list[str]]
    task_errors: dict[str, list[str]]
    task_warnings: dict[str, list[str]]

    def log(self):
        ConfigResolverParamValidationErrors(
            config_resolver_errors=self.config_resolver_errors,
            config_resolver_warnings=self.config_resolver_warnings,
        ).log()

        if len(self.process_errors) > 0:
            _logger.error(f"proc_wrapper param process errors = {self.process_errors}")
        else:
            _logger.debug("No proc_wrapper param process errors")

        if len(self.process_warnings) > 0:
            _logger.error(
                f"proc_wrapper param process warnings = {self.process_warnings}"
            )
        else:
            _logger.debug("No proc_wrapper param process warnings")

        if len(self.task_errors) > 0:
            _logger.error(f"proc_wrapper param Task errors = {self.task_errors}")
        else:
            _logger.debug("No proc_wrapper param Task errors")

        if len(self.task_warnings) > 0:
            _logger.error(f"proc_wrapper param Task warnings = {self.task_warnings}")
        else:
            _logger.debug("No proc_wrapper param Task warnings")

    def can_start_process(self) -> bool:
        return len(self.process_errors) == 0

    def can_start_task_execution(self) -> bool:
        return len(self.task_errors) == 0


class ConfigResolverParams:
    def __init__(self, env: Optional[Mapping[str, str]] = None):
        self.initial_config: dict[str, Any] = {}
        self.log_secrets: bool = False
        self.env_locations: list[str] = []
        self.config_locations: list[str] = []
        self.config_merge_strategy: str = DEFAULT_CONFIG_MERGE_STRATEGY
        self.overwrite_env_during_resolution: bool = False
        self.max_config_resolution_depth: int = DEFAULT_MAX_CONFIG_RESOLUTION_DEPTH
        self.max_config_resolution_iterations: int = (
            DEFAULT_MAX_CONFIG_RESOLUTION_ITERATIONS
        )
        self.config_ttl: Optional[int] = None
        self.fail_fast_config_resolution: bool = True
        self.resolved_env_var_name_prefix: str = DEFAULT_RESOLVABLE_ENV_VAR_NAME_PREFIX
        self.resolved_env_var_name_suffix: str = DEFAULT_RESOLVABLE_ENV_VAR_NAME_SUFFIX
        self.resolved_config_property_name_prefix: str = (
            DEFAULT_RESOLVABLE_CONFIG_PROPERTY_NAME_PREFIX
        )
        self.resolved_config_property_name_suffix: str = (
            DEFAULT_RESOLVABLE_CONFIG_PROPERTY_NAME_SUFFIX
        )

        self.env_var_name_for_config: Optional[str] = DEFAULT_ENV_VAR_NAME_FOR_CONFIG
        self.config_property_name_for_env: Optional[
            str
        ] = DEFAULT_CONFIG_VAR_NAME_FOR_ENV

        self.env_output_filename: Optional[str] = None
        self.env_output_format: Optional[str] = None
        self.config_output_filename: Optional[str] = None
        self.config_output_format: Optional[str] = None

        if env is not None:
            self.override_resolver_params_from_env(env=env)

    def override_resolver_params_from_env(
        self, env: Optional[Mapping[str, str]] = None
    ) -> None:
        if env is None:
            env = dict(os.environ)

        self.log_secrets = (
            string_to_bool(
                env.get("PROC_WRAPPER_LOG_SECRETS"), default_value=self.log_secrets
            )
            or False
        )

        env_locations_in_env = env.get("PROC_WRAPPER_ENV_LOCATIONS")
        if env_locations_in_env:
            self.env_locations = self.split_location_string(env_locations_in_env)

        config_locations_in_env = env.get("PROC_WRAPPER_CONFIG_LOCATIONS")
        if config_locations_in_env:
            self.config_locations = self.split_location_string(config_locations_in_env)

        self.config_merge_strategy = env.get(
            "PROC_WRAPPER_CONFIG_MERGE_STRATEGY", self.config_merge_strategy
        )

        self.overwrite_env_during_resolution = (
            string_to_bool(
                env.get("PROC_WRAPPER_OVERWRITE_ENV_WITH_SECRETS"),
                default_value=self.overwrite_env_during_resolution,
            )
            or False
        )

        should_resolve_secrets = string_to_bool(
            env.get("PROC_WRAPPER_RESOLVE_SECRETS"),
            (self.max_config_resolution_depth > 0),
        )

        if not should_resolve_secrets:
            _logger.debug("Secrets resolution is disabled.")
            self.max_config_resolution_depth = 0
            self.max_config_resolution_iterations = 0
        else:
            self.max_config_resolution_depth = (
                coalesce(
                    string_to_int(
                        env.get("PROC_WRAPPER_MAX_CONFIG_RESOLUTION_DEPTH"),
                        negative_value=0,
                    ),
                    self.max_config_resolution_depth,
                )
                or 0
            )

            self.max_config_resolution_iterations = (
                string_to_int(
                    env.get("PROC_WRAPPER_MAX_CONFIG_RESOLUTION_ITERATIONS"),
                    negative_value=0,
                    default_value=self.max_config_resolution_iterations,
                )
                or 0
            )

        self.config_ttl = string_to_int(
            env.get("PROC_WRAPPER_CONFIG_TTL_SECONDS"), default_value=self.config_ttl
        )

        self.fail_fast_config_resolution = (
            string_to_bool(
                env.get("PROC_WRAPPER_FAIL_FAST_CONFIG_RESOLUTION"),
                default_value=self.fail_fast_config_resolution,
            )
            or False
        )

        self.resolved_env_var_name_prefix = (
            coalesce(
                env.get("PROC_WRAPPER_RESOLVABLE_ENV_VAR_NAME_PREFIX"),
                self.resolved_env_var_name_prefix,
            )
            or ""
        )

        self.resolved_env_var_name_suffix = (
            coalesce(
                env.get("PROC_WRAPPER_RESOLVABLE_ENV_VAR_NAME_SUFFIX"),
                self.resolved_env_var_name_suffix,
            )
            or ""
        )

        self.resolved_config_property_name_prefix = (
            coalesce(
                env.get("PROC_WRAPPER_RESOLVABLE_CONFIG_PROPERTY_NAME_PREFIX"),
                self.resolved_config_property_name_prefix,
            )
            or ""
        )

        self.resolved_config_property_name_suffix = (
            coalesce(
                env.get("PROC_WRAPPER_RESOLVABLE_CONFIG_PROPERTY_NAME_SUFFIX"),
                self.resolved_config_property_name_suffix,
            )
            or ""
        )

        self.env_var_name_for_config = coalesce(
            env.get("PROC_WRAPPER_ENV_VAR_NAME_FOR_CONFIG"),
            self.env_var_name_for_config,
        )

        self.config_property_name_for_env = coalesce(
            env.get("PROC_WRAPPER_CONFIG_PROPERTY_NAME_FOR_ENV"),
            self.config_property_name_for_env,
        )

        self.env_output_filename = coalesce(
            env.get("PROC_WRAPPER_ENV_OUTPUT_FILENAME"), self.env_output_filename
        )

        self.env_output_format = coalesce(
            env.get("PROC_WRAPPER_ENV_OUTPUT_FORMAT"), self.env_output_format
        )

        self.config_output_filename = coalesce(
            env.get("PROC_WRAPPER_CONFIG_OUTPUT_FILENAME"), self.config_output_filename
        )

        self.config_output_format = coalesce(
            env.get("PROC_WRAPPER_CONFIG_OUTPUT_FORMAT"), self.config_output_format
        )

    def override_resolver_params_from_config(self, config: Mapping[str, Any]) -> None:
        params = config.get(PROC_WRAPPER_PARAMS_CONFIG_PROPERTY_NAME)
        if not isinstance(params, dict):
            _logger.debug(
                f"override_resolver_params_from_config(): {PROC_WRAPPER_PARAMS_CONFIG_PROPERTY_NAME} is not a dict"
            )
            return None

        return self.override_resolver_params_from_dict(params=params)

    def override_resolver_params_from_dict(self, params: Mapping[str, Any]) -> None:
        for attr in CONFIG_RESOLVER_PROPERTIES_COPIED_FROM_CONFIG:
            if attr in params:
                setattr(self, attr, params[attr])

    def guess_format_from_filename(self, filename: str) -> Optional[str]:
        if ".env." in filename:
            return FORMAT_DOTENV

        last_dot_index = filename.rfind(".")
        if last_dot_index < 0:
            _logger.info(
                f"No file extension found in filename '{filename}', can't guess format"
            )
            return None

        extension = filename[last_dot_index + 1 :].lower()

        return EXTENSION_TO_FORMAT.get(extension)

    def sanitize_and_validate(
        self, runtime_metadata: Optional["RuntimeMetadata"] = None
    ) -> ConfigResolverParamValidationErrors:
        config_resolver_errors: dict[str, list[str]] = {}
        config_resolver_warnings: dict[str, list[str]] = {}

        if self.env_output_filename:
            self.env_output_filename = self.env_output_filename.strip()

        if self.env_output_filename:
            if not self.env_output_format:
                self.env_output_format = (
                    self.guess_format_from_filename(self.env_output_filename)
                    or FORMAT_DOTENV
                )
        else:
            if self.env_output_format:
                self.env_output_format = self.env_output_format.strip().lower()

            if self.env_output_format == FORMAT_DOTENV:
                self.env_output_filename = ".env"
            elif self.env_output_format == FORMAT_JSON:
                self.env_output_filename = "env.json"
            elif self.env_output_format == FORMAT_YAML:
                self.env_output_filename = "env.yml"
            elif self.env_output_format:
                config_resolver_errors["env_output_format"] = [
                    f"Unknown output format '{self.env_output_format}'"
                ]

        if self.config_output_filename:
            self.config_output_filename = self.config_output_filename.strip()

        if self.config_output_filename:
            if not self.config_output_format:
                self.config_output_format = (
                    self.guess_format_from_filename(self.config_output_filename)
                    or FORMAT_JSON
                )
        else:
            if self.config_output_format:
                self.config_output_format = self.config_output_format.strip().lower()

            if self.config_output_format == FORMAT_DOTENV:
                self.config_output_filename = "config.env"
            elif self.config_output_format == FORMAT_JSON:
                self.config_output_filename = "config.json"
            elif self.config_output_format == FORMAT_YAML:
                self.config_output_filename = "config.yml"
            elif self.config_output_format:
                config_resolver_errors["config_output_format"] = [
                    f"Unknown output format '{self.config_output_format}'"
                ]

        return ConfigResolverParamValidationErrors(
            config_resolver_errors=config_resolver_errors,
            config_resolver_warnings=config_resolver_warnings,
        )

    def log_configuration(self) -> None:
        _logger.debug(f"Log secrets = {self.log_secrets}")
        _logger.debug(f"Env locations = {self.env_locations}")
        _logger.debug(f"Config locations = {self.config_locations}")
        _logger.debug(f"Config merge strategy = {self.config_merge_strategy}")
        _logger.debug(
            f"Overwrite env during resolution = {self.overwrite_env_during_resolution}"
        )
        _logger.debug(f"Config TTL = {self.config_ttl}")
        _logger.debug(
            f"Resolved env var name prefix = '{self.resolved_env_var_name_prefix}'"
        )
        _logger.debug(
            f"Resolved env var name suffix = '{self.resolved_env_var_name_suffix}'"
        )
        _logger.debug(
            f"Resolved config property name prefix = '{self.resolved_config_property_name_prefix}'"
        )
        _logger.debug(
            f"Resolved config property name suffix = '{self.resolved_config_property_name_suffix}'"
        )
        _logger.debug(
            f"Max config resolution depth = {self.max_config_resolution_depth}"
        )
        _logger.debug(
            f"Max config resolution iterations = {self.max_config_resolution_iterations}"
        )
        _logger.debug(
            f"Fail fast config resolution = {self.fail_fast_config_resolution}"
        )
        _logger.debug(f"Env var name for config = '{self.env_var_name_for_config}'")
        _logger.debug(
            f"Config var property for env = '{self.config_property_name_for_env}'"
        )
        _logger.debug(f"env output filename = '{self.env_output_filename}'")
        _logger.debug(f"env output format = '{self.env_output_format}'")
        _logger.debug(f"config output filename = '{self.config_output_filename}'")
        _logger.debug(f"config output format = '{self.config_output_format}'")

    def split_location_string(self, locations: str) -> list[str]:
        # Use , or ; to split locations, except they may be escaped by
        # backslashes. Any occurrence of , or ; in a location string
        # must be backslash escaped. This doesn't handle the weird case
        # when a location contains "\," or "\;".
        locations = locations.strip()

        if not locations:
            return []

        return [
            x
            for x in [
                location.replace(r"\,", ",")
                .replace(r"\;", ";")
                .replace(r"\\\\", r"\\")
                .strip()
                for location in re.split(r"\s*(?<!(?<!\\)\\)[,;]\s*", locations)
            ]
            if x
        ]


class ProcWrapperParams(ConfigResolverParams):
    """
    Represents the parameters for the ProcWrapper.

    Args:
        embedded_mode (bool, optional): Flag indicating whether the ProcWrapper is running in embedded mode. Defaults to True.
        override_from_env (bool, optional): Flag indicating whether to override the parameters from the environment variables. Defaults to True.
    """

    def __init__(
        self,
        embedded_mode: bool = True,
        override_from_env: bool = True,
        env: Optional[Mapping[str, str]] = None,
    ):
        override_env: Optional[Mapping[str, str]] = (
            coalesce(env, os.environ) if override_from_env else None
        )
        super().__init__(env=override_env)

        self.embedded_mode = embedded_mode

        self.exit_after_writing_variables: bool = False

        self.task_name: Optional[str] = None
        self.task_uuid: Optional[str] = None
        self.auto_create_task: bool = False
        self.auto_create_task_props: Optional[dict[str, Any]] = None
        self.execution_method_type: Optional[str] = None
        self.execution_method_props: Optional[dict[str, Any]] = None
        self.auto_create_task_run_environment_name: Optional[str] = None
        self.auto_create_task_run_environment_uuid: Optional[str] = None
        self.force_task_active: Optional[bool] = None
        self.task_is_passive: bool = True
        self.task_execution_uuid: Optional[str] = None
        self.task_version_number: Optional[int] = None
        self.task_version_text: Optional[str] = None
        self.task_version_signature: Optional[str] = None
        self.build_task_execution_uuid: Optional[str] = None
        self.deployment_task_execution_uuid: Optional[str] = None

        self.schedule: Optional[str] = None
        self.max_concurrency: Optional[int] = None
        self.max_conflicting_age: Optional[int] = None
        self.task_instance_metadata: Optional[dict[str, Any]] = None

        self.offline_mode: bool = False
        self.prevent_offline_execution: bool = False
        self.service: bool = False
        self.deployment: Optional[str] = None
        self.api_base_url: str = DEFAULT_API_BASE_URL
        self.api_key: Optional[str] = None
        self.api_heartbeat_interval: Optional[
            int
        ] = DEFAULT_API_HEARTBEAT_INTERVAL_SECONDS
        self.api_error_timeout: Optional[int] = DEFAULT_API_ERROR_TIMEOUT_SECONDS
        self.api_final_update_timeout: Optional[
            int
        ] = DEFAULT_API_FINAL_UPDATE_TIMEOUT_SECONDS
        self.api_retry_delay: int = DEFAULT_API_RETRY_DELAY_SECONDS
        self.api_resume_delay: int = DEFAULT_API_RESUME_DELAY_SECONDS
        self.api_task_execution_creation_error_timeout: Optional[
            int
        ] = DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_CREATION_TIMEOUT_SECONDS
        self.api_task_execution_creation_conflict_timeout: Optional[
            int
        ] = DEFAULT_API_TASK_EXECUTION_CREATION_TIMEOUT_SECONDS
        self.api_task_execution_creation_conflict_retry_delay: int = (
            DEFAULT_API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY_SECONDS
        )
        self.api_request_timeout: Optional[int] = DEFAULT_API_REQUEST_TIMEOUT_SECONDS
        self.api_managed_probability: float = 1.0
        self.api_failure_report_probability: float = 1.0
        self.api_timeout_report_probability: float = 1.0
        self.send_pid: bool = False
        self.send_hostname: bool = False
        self.send_runtime_metadata: bool = True
        self.runtime_metadata_refresh_interval: Optional[int] = None
        self.input_value: Optional[Any] = UNSET_VALUE
        self.input_env_var_name: Optional[str] = None
        self.input_filename: Optional[str] = None
        self.log_input_value: bool = False
        self.cleanup_input_file: Optional[bool] = None
        self.input_value_format: Optional[str] = None
        self.send_input_value: bool = False
        self.result_filename: Optional[str] = None
        self.result_value_format: Optional[str] = None
        self.log_result_value: bool = False
        self.cleanup_result_file: bool = True

        self.command: Optional[list[str]] = None
        self.command_line: Optional[str] = None
        self.shell_mode: str = SHELL_MODE_AUTO
        self.strip_shell_wrapping: bool = True
        self.process_group_termination: bool = True
        self.work_dir: str = "."

        self.process_timeout: Optional[int] = None
        self.process_max_retries: int = 0
        self.process_retry_delay: int = 0
        self.process_check_interval: int = DEFAULT_PROCESS_CHECK_INTERVAL_SECONDS
        self.process_termination_grace_period: Optional[
            int
        ] = DEFAULT_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS

        self.enable_status_update_listener: bool = False
        self.status_update_socket_port: int = DEFAULT_STATUS_UPDATE_SOCKET_PORT
        self.status_update_message_max_bytes: int = (
            DEFAULT_STATUS_UPDATE_MESSAGE_MAX_BYTES
        )
        self.status_update_interval: Optional[int] = None

        self.log_level = DEFAULT_LOG_LEVEL
        self.include_timestamps_in_log: bool = True
        self.num_log_lines_sent_on_failure: int = 0
        self.num_log_lines_sent_on_timeout: int = 0
        self.num_log_lines_sent_on_success: int = 0
        # self.num_log_lines_sent_on_heartbeat: int = 0
        self.max_log_line_length: int = DEFAULT_MAX_LOG_LINE_LENGTH
        self.merge_stdout_and_stderr_logs: bool = True
        self.ignore_stdout: bool = False
        self.ignore_stderr: bool = False

        self.monitor_container_name: Optional[str] = None
        self.main_container_name: Optional[str] = None
        self.sidecar_container_mode: Optional[bool] = None

        self.rollbar_access_token: Optional[str] = None
        self.rollbar_retries: Optional[int] = DEFAULT_ROLLBAR_RETRIES
        self.rollbar_retry_delay: int = DEFAULT_ROLLBAR_RETRY_DELAY_SECONDS
        self.rollbar_timeout: int = DEFAULT_ROLLBAR_TIMEOUT_SECONDS

        if override_env is not None:
            self.override_early_params_from_env(env=override_env)

    def override_early_params_from_env(self, env: Mapping[str, str]) -> None:
        self.execution_method_type = env.get(
            "PROC_WRAPPER_EXECUTION_METHOD_TYPE",
            (self.auto_create_task_props or {}).get(
                "execution_method_type", self.execution_method_type
            ),
        )

        self.main_container_name = env.get(
            "PROC_WRAPPER_MAIN_CONTAINER_NAME", self.main_container_name
        )

        self.monitor_container_name = env.get(
            "PROC_WRAPPER_MONITOR_CONTAINER_NAME", self.monitor_container_name
        )

        self.sidecar_container_mode = coalesce(
            string_to_bool(env.get("PROC_WRAPPER_SIDECAR_CONTAINER_MODE")),
            self.sidecar_container_mode,
        )

        if (
            (self.sidecar_container_mode is None)
            and self.main_container_name
            and self.monitor_container_name
            and (self.main_container_name != self.monitor_container_name)
        ):
            self.sidecar_container_mode = True

    def override_params_from_env(
        self, env: dict[str, str], mutable_only: bool = False
    ) -> None:
        if not mutable_only:
            self._override_immutable_from_env(env)

        self._override_mutable_from_env(env)

    def override_params_from_config(
        self, config: dict[str, Any], mutable_only: bool = False
    ) -> Optional[dict[str, str]]:
        params = config.get(PROC_WRAPPER_PARAMS_CONFIG_PROPERTY_NAME)
        if not isinstance(params, dict):
            _logger.debug(
                f"override_params_from_config(): {PROC_WRAPPER_PARAMS_CONFIG_PROPERTY_NAME} is not a dict"
            )
            return None

        return self.override_params_from_dict(params=params, mutable_only=mutable_only)

    def override_params_from_dict(
        self, params: dict[str, Any], mutable_only: bool = False
    ) -> Optional[dict[str, str]]:
        _logger.debug("Starting override_params_from_dict() ...")

        if not mutable_only:
            task_execution = params.get("task_execution")
            if isinstance(task_execution, dict):
                self.task_execution_uuid = task_execution.get(
                    "uuid", self.task_execution_uuid
                )
                self.task_version_number = task_execution.get(
                    "version_number", self.task_version_number
                )
                self.task_version_text = task_execution.get(
                    "version_text", self.task_version_text
                )
                self.task_version_signature = task_execution.get(
                    "version_signature", self.task_version_signature
                )
                self.build_task_execution_uuid = (
                    task_execution.get("build", {})
                    .get("task_execution", {})
                    .get("uuid", self.build_task_execution_uuid)
                )
                self.deployment_task_execution_uuid = (
                    task_execution.get("deploy", {})
                    .get("task_execution", {})
                    .get("uuid", self.deployment_task_execution_uuid)
                )

                # Task properties can appear either embedded in Task Execution
                # properties or at the config level.
                task = task_execution.get("task")
                if isinstance(task, dict):
                    self._override_proc_wrapper_params_from_task_dict(task)

            task = params.get("task")

            if isinstance(task, dict):
                self._override_proc_wrapper_params_from_task_dict(task)

            for attr in IMMUTABLE_PROPERTIES_COPIED_FROM_CONFIG:
                if attr in params:
                    setattr(self, attr, params[attr])

        for attr in MUTABLE_PROPERTIES_COPIED_FROM_CONFIG:
            if attr in params:
                setattr(self, attr, params[attr])

        rollbar_params = params.get("rollbar")
        if isinstance(rollbar_params, dict):
            for attr in PROPERTIES_COPIED_FROM_ROLLBAR_CONFIG:
                if attr in rollbar_params:
                    setattr(self, "rollbar_" + attr, rollbar_params[attr])

        env_override = params.get("env_override")
        if isinstance(env_override, dict):
            _logger.info("env_override found in config")
            return env_override

        _logger.info("No env_override found in config")
        return None

    def override_params_from_input(
        self, input: Optional[Any]
    ) -> Optional[dict[str, str]]:
        """
        Override parameters from the input. For now, don't trust the
        input except for providing the TaskExecution UUID, which should
        be harmless if injected by an attacker. In the future we may
        allow more parameters and the environment to be overridden, if
        we can ensure the input comes from a trusted source.
        """

        if not isinstance(input, dict):
            _logger.debug(
                "override_proc_wrapper_params_from_input(): input is missing or not a dict"
            )
            return None

        context = input.get(CLOUDREACTOR_CONTEXT_INPUT_PROPERTY_NAME)

        if not isinstance(context, dict):
            _logger.debug(
                "override_proc_wrapper_params_from_input(): context not found"
            )
            return None

        params = context.get(PROC_WRAPPER_PARAMS_CONFIG_PROPERTY_NAME)
        if not isinstance(params, dict):
            _logger.debug(
                "override_proc_wrapper_params_from_input(): proc_wrapper_params not found in context"
            )
            return None

        task_execution = params.get("task_execution")
        if isinstance(task_execution, dict):
            te_uuid = task_execution.get("uuid")
            if te_uuid:
                _logger.info(f"Found Task Execution {te_uuid} in input")
                self.task_execution_uuid = te_uuid
            else:
                _logger.debug("No UUID found in Task Execution")

        else:
            _logger.debug(
                "override_proc_wrapper_params_from_input(): task_execution not found in proc_wrapper_params"
            )

        # In the future we may allow the input to override the environment,
        # but we need to secure this against attackers that inject properties
        # into the input.
        return None

    def guess_format_from_filename(self, filename: str) -> Optional[str]:
        if ".env." in filename:
            return FORMAT_DOTENV

        last_dot_index = filename.rfind(".")
        if last_dot_index < 0:
            _logger.info(
                f"No file extension found in filename '{filename}', can't guess format"
            )
            return None

        extension = filename[last_dot_index + 1 :].lower()

        return EXTENSION_TO_FORMAT.get(extension)

    def sanitize_and_validate(
        self, runtime_metadata: Optional["RuntimeMetadata"] = None
    ) -> ProcWrapperParamValidationErrors:
        super_validation_errors = super().sanitize_and_validate(
            runtime_metadata=runtime_metadata
        )

        process_errors: dict[str, list[str]] = {}
        process_warnings: dict[str, list[str]] = {}
        task_errors: dict[str, list[str]] = {}
        task_warnings: dict[str, list[str]] = {}

        rv = ProcWrapperParamValidationErrors(
            config_resolver_errors=super_validation_errors.config_resolver_errors,
            config_resolver_warnings=super_validation_errors.config_resolver_warnings,
            process_errors=process_errors,
            process_warnings=process_warnings,
            task_errors=task_errors,
            task_warnings=task_warnings,
        )

        if self.exit_after_writing_variables:
            return rv

        if self.input_filename:
            if not self.input_value_format:
                self.input_value_format = self.guess_format_from_filename(
                    self.input_filename
                )

        if self.result_filename:
            if not self.result_value_format:
                self.result_value_format = self.guess_format_from_filename(
                    self.result_filename
                )

        if self.embedded_mode:
            if self.command_line or self.command:
                self._push_error(
                    process_errors,
                    "command",
                    "Command not supported in embedded mode",
                )

            if self.sidecar_container_mode:
                self._push_error(
                    process_errors,
                    "sidecar_container_mode",
                    "Sidecar container mode not supported in embedded mode",
                )
        else:
            runtime_metadata_is_execution_status_source = (
                runtime_metadata and runtime_metadata.is_execution_status_source
            )

            if (
                (not self.command)
                and (not self.command_line)
                and (not runtime_metadata_is_execution_status_source)
            ):
                self._push_error(
                    process_errors,
                    "command",
                    "Command expected in wrapped mode, but not found",
                )

            if self.process_check_interval <= 0:
                self._push_error(
                    process_warnings,
                    "process_check_interval",
                    f"Process check interval {self.process_check_interval} must be positive.",
                )
                self.process_check_interval = DEFAULT_PROCESS_CHECK_INTERVAL_SECONDS

            if runtime_metadata_is_execution_status_source and (
                self.process_max_retries > 0
            ):
                self._push_error(
                    process_warnings,
                    "process_max_retries",
                    f"Process retries {self.process_max_retries} must be 0 when monitoring an external process.",
                )
                self.process_max_retries = 0

        if self.service:
            if self.process_timeout is not None:
                self._push_error(
                    process_warnings,
                    "process_timeout",
                    f"Ignoring process timeout {self.process_timeout} because Task is a service.",
                )
                self.process_timeout = None

        if (self.process_timeout is not None) and (self.process_timeout <= 0):
            self._push_error(
                process_warnings,
                "process_timeout",
                f"Process timeout {self.process_timeout} must be positive or not specified.",
            )
            self.process_timeout = None

        if self.task_execution_uuid and (self.api_managed_probability < 1.0):
            _logger.info(
                "API managed probability was set to 1.0 since Task Execution UUID was provided."
            )
            self.api_managed_probability = 1.0

        if (not self.offline_mode) and (
            (self.api_managed_probability <= 0.0)
            and (self.api_failure_report_probability <= 0.0)
            and (self.api_timeout_report_probability <= 0.0)
        ):
            _logger.info(
                "Setting offline mode to true since all report probabilities are 0"
            )
            self.offline_mode = True

        if self.offline_mode:
            if self.prevent_offline_execution:
                self._push_error(
                    process_errors,
                    "prevent_offline_execution",
                    "Offline mode and offline execution prevention cannot both be enabled.",
                )
        else:
            if (not self.task_uuid) and (not self.task_name):
                self._push_error(
                    task_errors, "task_name", "No Task UUID or name specified."
                )

            if not self.api_key:
                self._push_error(task_errors, "api_key", "No API key specified.")

            if self.prevent_offline_execution and (self.api_managed_probability < 1.0):
                self._push_error(
                    task_errors,
                    "prevent_offline_execution",
                    "API managed probability must be 1.0 when preventing offline execution.",
                )

            self._validate_probability(
                task_errors, self.api_managed_probability, "api_managed_probability"
            )
            self._validate_probability(
                task_errors,
                self.api_failure_report_probability,
                "api_failure_report_probability",
            )
            self._validate_probability(
                task_errors,
                self.api_timeout_report_probability,
                "api_timeout_report_probability",
            )

            if self.auto_create_task:
                if not (
                    self.auto_create_task_run_environment_name
                    or self.auto_create_task_run_environment_uuid
                ):
                    self._push_error(
                        task_errors,
                        "auto_create_task",
                        "No Run Environment UUID or name for auto-created Task specified.",
                    )

                if not self.task_is_passive and (
                    (runtime_metadata is None)
                    or (
                        runtime_metadata.task_configuration.execution_method_capability_details
                        is None
                    )
                ):
                    self._push_error(
                        task_warnings,
                        "force_task_passive",
                        "Task may not be active unless execution method capability can be determined.",
                    )
                    self.task_is_passive = True

        return rv

    def run_mode_label(self) -> str:
        return "embedded" if self.embedded_mode else "wrapped"

    def resolve_command_and_shell_flag(self) -> tuple[Union[str, list[str]], bool]:
        resolved_command: Union[str, list[str]] = (
            self.command_line or self.command or ""
        )

        command_line = (
            self.command_line if self.command_line else " ".join(self.command or "")
        )

        found_shell_wrapping = False

        if self.strip_shell_wrapping:
            done = False
            while not done:
                done = True
                command_line_remainder, n = SHELL_WRAPPER_REGEX.subn("", command_line)

                if n > 0:
                    try:
                        # Unquote
                        command_remainder = shlex.split(command_line_remainder)

                        # Only handle a single shell expression
                        if len(command_remainder) == 1:
                            # Switch to single string form
                            resolved_command = command_remainder[0]

                            _logger.info(
                                f"Stripped shell wrapping from '{command_line}' to '{resolved_command}'"
                            )

                            command_line = resolved_command
                            found_shell_wrapping = True
                            done = False
                    except Exception:
                        _logger.exception(
                            f"Error unquoting shell command '{command_line}'"
                        )

        shell_flag = True
        if self.shell_mode == SHELL_MODE_FORCE_DISABLE:
            shell_flag = False
        elif (not found_shell_wrapping) and (self.shell_mode == SHELL_MODE_AUTO):
            shell_flag = SHELL_COMMAND_REGEX.search(command_line) is not None

        if (not shell_flag) and isinstance(resolved_command, str):
            resolved_command = re.split(r"\s+", resolved_command)

        return (resolved_command, shell_flag)

    def log_configuration(self) -> None:
        super().log_configuration()

        _logger.info(f"Run mode = {self.run_mode_label()}")

        if not self.embedded_mode:
            _logger.info(
                f"Exit after writing variables = {self.exit_after_writing_variables}"
            )
            if self.exit_after_writing_variables:
                return

        _logger.info(f"Task Execution UUID = {self.task_execution_uuid}")
        _logger.info(f"Task UUID = {self.task_uuid}")
        _logger.info(f"Task name = {self.task_name}")
        _logger.info(f"Deployment = '{self.deployment}'")

        _logger.info(f"Task version number = {self.task_version_number}")
        _logger.info(f"Task version text = {self.task_version_text}")
        _logger.info(f"Task version signature = {self.task_version_signature}")

        _logger.info(f"Execution method type = {self.execution_method_type}")
        _logger.info(f"Execution method props = {self.execution_method_props}")

        _logger.info(f"Auto create task = {self.auto_create_task}")

        if self.auto_create_task:
            _logger.info(
                f"Auto create task Run Environment name = {self.auto_create_task_run_environment_name}"
            )
            _logger.info(
                f"Auto create task Run Environment UUID = {self.auto_create_task_run_environment_uuid}"
            )
            _logger.info(f"Auto create task props = {self.auto_create_task_props}")

        _logger.info(f"Passive task = {self.task_is_passive}")

        _logger.info(f"Input filename = '{self.input_filename}'")
        _logger.info(f"Input env var name = '{self.input_env_var_name}'")
        _logger.info(f"Input value format = '{self.input_value_format}'")
        _logger.info(f"Send input value = {self.send_input_value}")

        _logger.info(f"Result filename = '{self.result_filename}'")
        _logger.info(f"Result value format = '{self.result_value_format}'")

        _logger.info(f"Task instance metadata = {self.task_instance_metadata}")
        _logger.info(f"Send runtime metadata = {self.send_runtime_metadata}")
        _logger.debug(
            f"Runtime metadata refresh interval = {self.runtime_metadata_refresh_interval}"
        )

        _logger.debug(f"Task is a service = {self.service}")
        _logger.debug(f"Max concurrency = {self.max_concurrency}")
        _logger.info(f"Offline mode = {self.offline_mode}")
        _logger.info(f"Prevent offline execution = {self.prevent_offline_execution}")
        _logger.debug(f"Process retries = {self.process_max_retries}")
        _logger.debug(f"Process retry delay = {self.process_retry_delay}")
        _logger.debug(f"Process check interval = {self.process_check_interval}")

        _logger.debug(
            f"Maximum age of conflicting processes = {self.max_conflicting_age}"
        )

        if not self.offline_mode:
            _logger.info(f"API base URL = '{self.api_base_url}'")

            if self.log_secrets:
                _logger.debug(f"API key = '{self.api_key}'")

            _logger.debug(f"API managed probability = {self.api_managed_probability}")
            _logger.debug(
                f"API failure report probability = {self.api_failure_report_probability}"
            )
            _logger.debug(
                f"API timeout report probability = {self.api_timeout_report_probability}"
            )

            _logger.debug(f"API error timeout = {self.api_error_timeout}")
            _logger.debug(f"API retry delay = {self.api_retry_delay}")
            _logger.debug(f"API resume delay = {self.api_resume_delay}")
            _logger.debug(
                f"API Task Execution creation error timeout = {self.api_task_execution_creation_error_timeout}"
            )
            _logger.debug(
                f"API Task Execution creation conflict timeout = {self.api_task_execution_creation_conflict_timeout}"
            )
            _logger.debug(
                f"API Task Execution creation conflict retry delay = {self.api_task_execution_creation_conflict_retry_delay}"
            )
            _logger.debug(
                f"API timeout for final update = {self.api_final_update_timeout}"
            )
            _logger.debug(f"API request timeout = {self.api_request_timeout}")
            _logger.debug(f"API heartbeat interval = {self.api_heartbeat_interval}")

        super().log_configuration()

        _logger.debug(f"Execution method type = {self.execution_method_type}")

        if not self.embedded_mode:
            command, shell = self.resolve_command_and_shell_flag()
            _logger.info(f"Command = {command}")
            _logger.info(f"Use shell = {shell} (shell mode = {self.shell_mode})")
            _logger.info(f"Work dir = '{self.work_dir}'")

            _logger.debug(f"Main container name = {self.main_container_name}")
            _logger.debug(f"Monitor container name = {self.monitor_container_name}")
            _logger.debug(f"Sidecar container mode = {self.sidecar_container_mode}")

            enable_status_update_listener = self.enable_status_update_listener
            _logger.debug(f"Enable status listener = {enable_status_update_listener}")

            if enable_status_update_listener:
                _logger.debug(f"Status socket port = {self.status_update_socket_port}")
                _logger.debug(
                    f"Status update message max bytes = {self.status_update_message_max_bytes}"
                )

        _logger.debug(f"Status update interval = {self.status_update_interval}")

        _logger.debug(f"Log input value = {self.log_input_value}")
        _logger.debug(f"Log result value = {self.log_result_value}")

        _logger.debug(
            f"Num log lines sent on failure = {self.num_log_lines_sent_on_failure}"
        )
        _logger.debug(
            f"Num log lines sent on timeout = {self.num_log_lines_sent_on_timeout}"
        )
        _logger.debug(
            f"Num log lines sent on success = {self.num_log_lines_sent_on_success}"
        )
        _logger.debug(f"Max log line length = {self.max_log_line_length}")
        _logger.debug(
            f"Merge stdout and stderr logs = {self.merge_stdout_and_stderr_logs}"
        )
        _logger.debug(f"Ignore stdout = {self.ignore_stdout}")
        _logger.debug(f"Ignore stderr = {self.ignore_stderr}")

        if self.rollbar_access_token:
            if self.log_secrets:
                _logger.debug(f"Rollbar API key = '{self.rollbar_access_token}'")

            _logger.debug(f"Rollbar timeout = {self.rollbar_timeout}")
            _logger.debug(f"Rollbar retries = {self.rollbar_retries}")
            _logger.debug(f"Rollbar retry delay = {self.rollbar_retry_delay}")
        else:
            _logger.debug("Rollbar is disabled")

    def populate_env(self, env: dict[str, str]) -> None:
        if self.env_output_filename:
            env["PROC_WRAPPER_ENV_OUTPUT_FILENAME"] = self.env_output_filename

        if self.env_output_format:
            env["PROC_WRAPPER_ENV_OUTPUT_FORMAT"] = self.env_output_format

        if self.config_output_filename:
            env["PROC_WRAPPER_CONFIG_OUTPUT_FILENAME"] = self.config_output_filename

        if self.config_output_format:
            env["PROC_WRAPPER_CONFIG_OUTPUT_FORMAT"] = self.config_output_format

        if self.input_filename:
            env["PROC_WRAPPER_INPUT_FILENAME"] = self.input_filename

        if self.input_value_format:
            env["PROC_WRAPPER_INPUT_VALUE_FORMAT"] = self.input_value_format

        if self.input_env_var_name:
            env["PROC_WRAPPER_INPUT_ENV_VAR_NAME"] = self.input_env_var_name

        if self.result_filename:
            env["PROC_WRAPPER_RESULT_FILENAME"] = self.result_filename

        if self.result_value_format:
            env["PROC_WRAPPER_RESULT_VALUE_FORMAT"] = self.result_value_format

        if self.deployment:
            env["PROC_WRAPPER_DEPLOYMENT"] = self.deployment

        env["PROC_WRAPPER_OFFLINE_MODE"] = str(self.offline_mode).upper()

        if not self.offline_mode:
            env["PROC_WRAPPER_API_BASE_URL"] = self.api_base_url
            env["PROC_WRAPPER_API_KEY"] = str(self.api_key)
            env["PROC_WRAPPER_API_MANAGED_PROBABILITY"] = str(
                self.api_managed_probability
            )
            env["PROC_WRAPPER_API_FAILURE_REPORT_PROBABILITY"] = str(
                self.api_failure_report_probability
            )
            env["PROC_WRAPPER_API_TIMEOUT_REPORT_PROBABILITY"] = str(
                self.api_timeout_report_probability
            )
            env["PROC_WRAPPER_API_ERROR_TIMEOUT_SECONDS"] = str(
                encode_int(self.api_error_timeout, empty_value=-1)
            )
            env["PROC_WRAPPER_API_RETRY_DELAY_SECONDS"] = str(self.api_retry_delay)
            env["PROC_WRAPPER_API_RESUME_DELAY_SECONDS"] = str(
                encode_int(self.api_resume_delay)
            )
            env["PROC_WRAPPER_API_REQUEST_TIMEOUT_SECONDS"] = str(
                encode_int(self.api_request_timeout, empty_value=-1)
            )

            enable_status_update_listener = self.enable_status_update_listener
            env["PROC_WRAPPER_ENABLE_STATUS_UPDATE_LISTENER"] = str(
                enable_status_update_listener
            ).upper()
            if enable_status_update_listener:
                env["PROC_WRAPPER_STATUS_UPDATE_SOCKET_PORT"] = str(
                    self.status_update_socket_port
                )
                env["PROC_WRAPPER_STATUS_UPDATE_INTERVAL_SECONDS"] = str(
                    self.status_update_interval
                )
                env["PROC_WRAPPER_STATUS_UPDATE_MESSAGE_MAX_BYTES"] = str(
                    self.status_update_message_max_bytes
                )

            if self.task_execution_uuid:
                env["PROC_WRAPPER_TASK_EXECUTION_UUID"] = self.task_execution_uuid

            if self.task_uuid:
                env["PROC_WRAPPER_TASK_UUID"] = self.task_uuid

            if self.task_name:
                env["PROC_WRAPPER_TASK_NAME"] = self.task_name

        if self.rollbar_access_token:
            env["PROC_WRAPPER_ROLLBAR_ACCESS_TOKEN"] = self.rollbar_access_token
            env["PROC_WRAPPER_ROLLBAR_TIMEOUT"] = str(self.rollbar_timeout)
            env["PROC_WRAPPER_ROLLBAR_RETRIES"] = str(self.rollbar_retries)
            env["PROC_WRAPPER_ROLLBAR_RETRY_DELAY_SECONDS"] = str(
                self.rollbar_retry_delay
            )

        if self.task_version_number is not None:
            env["PROC_WRAPPER_TASK_VERSION_NUMBER"] = str(self.task_version_number)

        if self.task_version_text:
            env["PROC_WRAPPER_TASK_VERSION_TEXT"] = self.task_version_text

        if self.task_version_signature:
            env["PROC_WRAPPER_TASK_VERSION_SIGNATURE"] = self.task_version_signature

        if self.task_instance_metadata:
            env["PROC_WRAPPER_TASK_INSTANCE_METADATA"] = json.dumps(
                self.task_instance_metadata
            )

        env["PROC_WRAPPER_PROCESS_TIMEOUT_SECONDS"] = str(
            encode_int(self.process_timeout, empty_value=-1)
        )
        env["PROC_WRAPPER_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS"] = str(
            self.process_termination_grace_period
        )

        env["PROC_WRAPPER_MAX_CONCURRENCY"] = str(
            encode_int(self.max_concurrency, empty_value=-1)
        )

        env["PROC_WRAPPER_PREVENT_OFFLINE_EXECUTION"] = str(
            self.prevent_offline_execution
        ).upper()

    def log_buffer_size(self) -> int:
        """
        Returns required number of lines in the log buffer.
        """
        if (self.max_log_line_length <= 0) or (
            self.ignore_stdout and self.ignore_stderr
        ):
            return 0

        return max(
            self.num_log_lines_sent_on_failure,
            self.num_log_lines_sent_on_timeout,
            self.num_log_lines_sent_on_success,
        )

    def _override_immutable_from_env(self, env: dict[str, str]) -> None:
        self.offline_mode = (
            string_to_bool(
                env.get("PROC_WRAPPER_OFFLINE_MODE"), default_value=self.offline_mode
            )
            or False
        )

        self.prevent_offline_execution = (
            string_to_bool(
                env.get("PROC_WRAPPER_PREVENT_OFFLINE_EXECUTION"),
                default_value=self.prevent_offline_execution,
            )
            or False
        )

        self.task_version_number = coalesce(
            string_to_int(env.get("PROC_WRAPPER_TASK_VERSION_NUMBER")),
            self.task_version_number,
        )

        self.task_version_text = env.get(
            "PROC_WRAPPER_TASK_VERSION_TEXT", self.task_version_text
        )
        self.task_version_signature = env.get(
            "PROC_WRAPPER_TASK_VERSION_SIGNATURE", self.task_version_signature
        )

        self.build_task_execution_uuid = env.get(
            "PROC_WRAPPER_BUILD_TASK_EXECUTION_UUID", self.build_task_execution_uuid
        )

        self.deployment_task_execution_uuid = env.get(
            "PROC_WRAPPER_DEPLOYMENT_TASK_EXECUTION_UUID",
            self.deployment_task_execution_uuid,
        )

        self.include_timestamps_in_log = (
            string_to_bool(
                env.get("PROC_WRAPPER_INCLUDE_TIMESTAMPS_IN_LOG"),
                default_value=self.include_timestamps_in_log,
            )
            or False
        )

        self.input_env_var_name = coalesce(
            env.get("PROC_WRAPPER_INPUT_ENV_VAR_NAME"), self.input_env_var_name
        )

        self.input_filename = coalesce(
            env.get("PROC_WRAPPER_INPUT_FILENAME"), self.input_filename
        )

        self.input_value_format = coalesce(
            env.get("PROC_WRAPPER_INPUT_VALUE_FORMAT"), self.input_value_format
        )

        self.log_input_value = cast(
            bool,
            string_to_bool(
                env.get("PROC_WRAPPER_LOG_INPUT_VALUE"),
                default_value=self.log_input_value,
            ),
        )

        self.cleanup_input_file = cast(
            bool,
            string_to_bool(
                env.get("PROC_WRAPPER_CLEANUP_INPUT_FILE"),
                default_value=self.cleanup_input_file,
            ),
        )

        if self.offline_mode:
            return

        self.deployment = env.get("PROC_WRAPPER_DEPLOYMENT", self.deployment)

        task_overrides_str = env.get("PROC_WRAPPER_AUTO_CREATE_TASK_PROPS")
        if task_overrides_str:
            try:
                self.auto_create_task_props = json.loads(task_overrides_str)
            except json.JSONDecodeError:
                _logger.warning(
                    f"Failed to parse Task props: '{task_overrides_str}', ensure it is valid JSON."
                )
                self.auto_create_task_props = None

        auto_create_task_props = self.auto_create_task_props or {}

        self.auto_create_task = (self.auto_create_task_props is not None) or coalesce(
            string_to_bool(env.get("PROC_WRAPPER_AUTO_CREATE_TASK")),
            auto_create_task_props.get("was_auto_created"),
            self.auto_create_task,
        )

        self.task_name = env.get(
            "PROC_WRAPPER_TASK_NAME", auto_create_task_props.get("name", self.task_name)
        )

        max_concurrency = string_to_int(
            env.get("PROC_WRAPPER_TASK_MAX_CONCURRENCY"), negative_value=-1
        )

        if max_concurrency is None:
            if "max_concurrency" in auto_create_task_props:
                # May be None, if so, keep it that way
                self.max_concurrency = auto_create_task_props["max_concurrency"]
        else:
            self.max_concurrency = coalesce(max_concurrency, self.max_concurrency)

        args_forced_passive = (
            None if (self.force_task_active is None) else (not self.force_task_active)
        )

        if self.auto_create_task:
            override_run_env = auto_create_task_props.get("run_environment", {})

            self.auto_create_task_run_environment_uuid = env.get(
                "PROC_WRAPPER_AUTO_CREATE_TASK_RUN_ENVIRONMENT_UUID",
                override_run_env.get(
                    "uuid", self.auto_create_task_run_environment_uuid
                ),
            )

            self.auto_create_task_run_environment_name = coalesce(
                env.get("PROC_WRAPPER_AUTO_CREATE_TASK_RUN_ENVIRONMENT_NAME"),
                override_run_env.get("name"),
                self.auto_create_task_run_environment_name,
            )

            if (not self.auto_create_task_run_environment_name) and (
                not self.auto_create_task_run_environment_uuid
            ):
                self.auto_create_task_run_environment_name = self.deployment

            self.task_is_passive = (
                coalesce(
                    string_to_bool(env.get("PROC_WRAPPER_TASK_IS_PASSIVE")),
                    auto_create_task_props.get("passive"),
                    args_forced_passive,
                    self.task_is_passive,
                )
                or False
            )

            em_overrides_str = env.get("PROC_WRAPPER_EXECUTION_METHOD_PROPS")
            if em_overrides_str:
                try:
                    self.execution_method_props = json.loads(em_overrides_str)
                except json.JSONDecodeError:
                    _logger.warning(
                        f"Failed to parse auto-create task execution props: '{em_overrides_str}', ensure it is valid JSON."
                    )
        else:
            self.task_is_passive = (
                coalesce(
                    string_to_bool(env.get("PROC_WRAPPER_TASK_IS_PASSIVE")),
                    args_forced_passive,
                )
                or False
            )

        self.task_execution_uuid = env.get(
            "PROC_WRAPPER_TASK_EXECUTION_UUID", self.task_execution_uuid
        )

        self.task_uuid = env.get(
            "PROC_WRAPPER_TASK_UUID", auto_create_task_props.get("uuid", self.task_uuid)
        )

        count = auto_create_task_props.get("min_service_instance_count")
        override_is_service = None if count is None else (count > 0)

        self.service = cast(
            bool,
            string_to_bool(
                env.get("PROC_WRAPPER_TASK_IS_SERVICE"),
                default_value=coalesce(override_is_service, self.service),
            ),
        )

        api_base_url = env.get("PROC_WRAPPER_API_BASE_URL") or self.api_base_url
        self.api_base_url = api_base_url.rstrip("/")

        self.api_managed_probability = coalesce(
            string_to_float(
                env.get("PROC_WRAPPER_API_MANAGED_PROBABILITY"),
                default_value=self.api_managed_probability,
            ),
            1.0,
        )

        self.api_failure_report_probability = coalesce(
            string_to_float(
                env.get("PROC_WRAPPER_API_FAILURE_REPORT_PROBABILITY"),
                default_value=self.api_failure_report_probability,
            )
        )

        self.api_timeout_report_probability = coalesce(
            string_to_float(
                env.get("PROC_WRAPPER_API_TIMEOUT_REPORT_PROBABILITY"),
                default_value=self.api_timeout_report_probability,
            )
        )

        self.result_filename = coalesce(
            env.get("PROC_WRAPPER_RESULT_FILENAME"), self.result_filename
        )

        self.result_value_format = coalesce(
            env.get("PROC_WRAPPER_RESULT_VALUE_FORMAT"), self.result_value_format
        )

        self.log_result_value = cast(
            bool,
            string_to_bool(
                env.get("PROC_WRAPPER_LOG_RESULT_VALUE"),
                default_value=self.log_result_value,
            ),
        )

        self.cleanup_result_file = cast(
            bool,
            string_to_bool(
                env.get("PROC_WRAPPER_CLEANUP_RESULT_FILE"),
                default_value=self.cleanup_result_file,
            ),
        )

        self.num_log_lines_sent_on_failure = (
            string_to_int(
                env.get("PROC_WRAPPER_NUM_LOG_LINES_SENT_ON_FAILURE"),
                default_value=self.num_log_lines_sent_on_failure,
            )
            or 0
        )

        self.num_log_lines_sent_on_timeout = (
            string_to_int(
                env.get("PROC_WRAPPER_NUM_LOG_LINES_SENT_ON_TIMEOUT"),
                default_value=self.num_log_lines_sent_on_timeout,
            )
            or 0
        )

        self.num_log_lines_sent_on_success = (
            string_to_int(
                env.get("PROC_WRAPPER_NUM_LOG_LINES_SENT_ON_SUCCESS"),
                default_value=self.num_log_lines_sent_on_success,
            )
            or 0
        )

        # Future use, if we want to log lines sent on heartbeat
        # self.num_log_lines_sent_on_heartbeat = string_to_int(
        #     env.get("PROC_WRAPPER_LOG_LINES_SENT_ON_HEARTBEAT"),
        #     default_value=self.num_log_lines_sent_on_heartbeat,
        # ) or 0

        self.max_log_line_length = (
            string_to_int(
                env.get("PROC_WRAPPER_MAX_LOG_LINE_LENGTH"),
                default_value=self.max_log_line_length,
            )
            or 0
        )

        self.merge_stdout_and_stderr_logs = (
            string_to_bool(
                env.get("PROC_WRAPPER_MERGE_STDOUT_AND_STDERR_LOGS"),
                default_value=self.merge_stdout_and_stderr_logs,
            )
            or False
        )

        self.ignore_stdout = (
            string_to_bool(
                env.get("PROC_WRAPPER_IGNORE_STDOUT"),
                default_value=self.ignore_stdout,
            )
            or False
        )

        self.ignore_stderr = (
            string_to_bool(
                env.get("PROC_WRAPPER_IGNORE_STDERR"),
                default_value=self.ignore_stderr,
            )
            or False
        )

        # Properties to be reported to CloudReactor
        self.schedule = env.get("PROC_WRAPPER_SCHEDULE") or self.schedule

        self.enable_status_update_listener = (
            string_to_bool(
                env.get("PROC_WRAPPER_ENABLE_STATUS_UPDATE_LISTENER"),
                default_value=self.enable_status_update_listener,
            )
            or False
        )

        if self.enable_status_update_listener:
            self.status_update_socket_port = (
                string_to_int(
                    env.get("PROC_WRAPPER_STATUS_UPDATE_SOCKET_PORT"),
                    negative_value=DEFAULT_STATUS_UPDATE_SOCKET_PORT,
                )
                or self.status_update_socket_port
            )

            self.status_update_message_max_bytes = (
                string_to_int(
                    env.get("PROC_WRAPPER_STATUS_UPDATE_MESSAGE_MAX_BYTES"),
                    negative_value=DEFAULT_STATUS_UPDATE_MESSAGE_MAX_BYTES,
                )
                or self.status_update_message_max_bytes
            )

    def _override_proc_wrapper_params_from_task_dict(
        self, task: dict[str, Any]
    ) -> None:
        self.task_name = task.get("name", self.task_name)
        self.task_uuid = task.get("uuid", self.task_uuid)
        self.task_instance_metadata = task.get(
            "other_metadata", self.task_instance_metadata
        )
        self.auto_create_task = task.get("was_auto_created", self.auto_create_task)

        if self.auto_create_task:
            self.auto_create_task_props = (
                best_effort_deep_merge(self.auto_create_task_props or {}, task) or {}
            )

            self.task_is_passive = coalesce(
                task.get("passive"),
                self.auto_create_task_props.get("passive"),
                self.task_is_passive,
            )

            run_env = task.get("run_environment")
            if isinstance(run_env, dict):
                self.auto_create_task_run_environment_name = run_env.get(
                    "name", self.auto_create_task_run_environment_name
                )

                self.auto_create_task_run_environment_uuid = run_env.get(
                    "uuid", self.auto_create_task_run_environment_uuid
                )

        self.build_task_execution_uuid = (
            task.get("build", {})
            .get("task_execution", {})
            .get("uuid", self.build_task_execution_uuid)
        )
        self.deployment_task_execution_uuid = (
            task.get("deployment", {})
            .get("task_execution", {})
            .get("uuid", self.build_task_execution_uuid)
        )

    def _override_mutable_from_env(self, env: dict[str, str]) -> None:
        self.rollbar_access_token = env.get(
            "PROC_WRAPPER_ROLLBAR_ACCESS_TOKEN", self.rollbar_access_token
        )

        if self.rollbar_access_token:
            self.rollbar_retries = string_to_int(
                env.get("PROC_WRAPPER_ROLLBAR_RETRIES"),
                default_value=self.rollbar_retries,
            )

            self.rollbar_retry_delay = coalesce(
                string_to_int(env.get("PROC_WRAPPER_ROLLBAR_RETRY_DELAY_SECONDS")),
                self.rollbar_retry_delay,
            )

            self.rollbar_timeout = coalesce(
                string_to_int(
                    env.get("PROC_WRAPPER_ROLLBAR_TIMEOUT_SECONDS"),
                    self.rollbar_timeout,
                )
            )

        env_process_timeout_seconds = env.get("PROC_WRAPPER_PROCESS_TIMEOUT_SECONDS")

        self.process_timeout = string_to_int(
            env_process_timeout_seconds, default_value=self.process_timeout
        )

        self.process_max_retries = cast(
            int,
            coalesce(
                string_to_int(
                    env.get("PROC_WRAPPER_TASK_MAX_RETRIES"), negative_value=0
                ),
                self.process_max_retries,
            ),
        )

        self.process_retry_delay = cast(
            int,
            coalesce(
                string_to_int(
                    env.get("PROC_WRAPPER_PROCESS_RETRY_DELAY_SECONDS"),
                    negative_value=0,
                ),
                self.process_retry_delay,
            ),
        )

        self.process_termination_grace_period = string_to_int(
            env.get("PROC_WRAPPER_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS"),
            default_value=self.process_termination_grace_period,
        )

        if not self.embedded_mode:
            self.process_check_interval = cast(
                int,
                string_to_int(
                    env.get("PROC_WRAPPER_PROCESS_CHECK_INTERVAL_SECONDS"),
                    default_value=self.process_check_interval,
                    negative_value=-1,
                ),
            )

            self.command_line = env.get("PROC_WRAPPER_TASK_COMMAND", self.command_line)

            self.shell_mode = env.get("PROC_WRAPPER_SHELL_MODE", self.shell_mode)

            self.strip_shell_wrapping = (
                string_to_bool(
                    env.get("PROC_WRAPPER_STRIP_SHELL_WRAPPING"),
                    default_value=self.strip_shell_wrapping,
                )
                or False
            )

            self.process_group_termination = coalesce(
                string_to_bool(env.get("PROC_WRAPPER_TERMINATE_PROCESS_GROUP")),
                self.process_group_termination,
                True,
            )

            self.work_dir = env.get("PROC_WRAPPER_WORK_DIR", self.work_dir)

        task_instance_metadata_str = env.get("PROC_WRAPPER_TASK_INSTANCE_METADATA")

        # This could be logged for debugging, so still load it even if we
        # don't send it to the Task Management server.
        if task_instance_metadata_str:
            try:
                self.task_instance_metadata = json.loads(task_instance_metadata_str)
            except Exception:
                _logger.exception(
                    f"Failed to parse instance metadata: '{task_instance_metadata_str}'"
                )
                self.task_instance_metadata = None

        # API key and timeouts can be refreshed, so no mutable check
        if self.offline_mode:
            return

        default_heartbeat_interval: Optional[
            int
        ] = DEFAULT_API_HEARTBEAT_INTERVAL_SECONDS

        is_concurrency_limited_service = (
            self.service
            and (self.max_concurrency is not None)
            and (self.max_concurrency > 0)
        )

        if is_concurrency_limited_service:
            default_heartbeat_interval = (
                DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_HEARTBEAT_INTERVAL_SECONDS
            )

        if self.api_heartbeat_interval != UNSET_INT_VALUE:
            default_heartbeat_interval = self.api_heartbeat_interval

        self.api_heartbeat_interval = string_to_int(
            env.get("PROC_WRAPPER_API_HEARTBEAT_INTERVAL_SECONDS"),
            default_value=default_heartbeat_interval,
        )

        default_max_conflicting_age_seconds = None

        if self.max_conflicting_age != UNSET_INT_VALUE:
            default_max_conflicting_age_seconds = self.max_conflicting_age
        elif self.service and self.api_heartbeat_interval:
            default_max_conflicting_age_seconds = (
                self.api_heartbeat_interval + HEARTBEAT_DELAY_TOLERANCE_SECONDS
            )

        self.max_conflicting_age = string_to_int(
            env.get("PROC_WRAPPER_MAX_CONFLICTING_AGE_SECONDS"),
            default_value=default_max_conflicting_age_seconds,
        )

        if self.enable_status_update_listener:
            self.status_update_interval = string_to_int(
                env.get("PROC_WRAPPER_STATUS_UPDATE_INTERVAL_SECONDS"),
                default_value=self.status_update_interval,
            )

        self.api_key = env.get("PROC_WRAPPER_API_KEY", self.api_key)

        self.api_error_timeout = string_to_int(
            env.get("PROC_WRAPPER_API_ERROR_TIMEOUT_SECONDS"),
            default_value=self.api_error_timeout,
        )

        self.api_task_execution_creation_error_timeout = string_to_int(
            env.get("PROC_WRAPPER_API_TASK_CREATION_ERROR_TIMEOUT_SECONDS"),
            default_value=self.api_task_execution_creation_error_timeout,
        )

        default_task_execution_creation_conflict_timeout: Optional[int] = 0
        default_task_execution_creation_conflict_retry_delay = (
            DEFAULT_API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY_SECONDS
        )

        if is_concurrency_limited_service:
            default_task_execution_creation_conflict_timeout = (
                DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_CREATION_TIMEOUT_SECONDS
            )
            default_task_execution_creation_conflict_retry_delay = DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_CREATION_CONFLICT_RETRY_DELAY_SECONDS

        if self.api_task_execution_creation_conflict_timeout != UNSET_INT_VALUE:
            default_task_execution_creation_conflict_timeout = (
                self.api_task_execution_creation_conflict_timeout
            )

        if self.api_task_execution_creation_conflict_retry_delay != UNSET_INT_VALUE:
            default_task_execution_creation_conflict_retry_delay = (
                self.api_task_execution_creation_conflict_retry_delay
            )

        self.api_task_execution_creation_conflict_timeout = string_to_int(
            env.get(
                "PROC_WRAPPER_API_TASK_EXECUTION_CREATION_CONFLICT_TIMEOUT_SECONDS"
            ),
            default_value=default_task_execution_creation_conflict_timeout,
        )

        self.api_final_update_timeout = string_to_int(
            env.get("PROC_WRAPPER_API_FINAL_UPDATE_TIMEOUT_SECONDS"),
            default_value=self.api_final_update_timeout,
        )

        self.api_retry_delay = cast(
            int,
            string_to_int(
                env.get("PROC_WRAPPER_API_RETRY_DELAY_SECONDS"),
                default_value=self.api_retry_delay,
                negative_value=0,
            ),
        )

        self.api_resume_delay = cast(
            int,
            string_to_int(
                env.get("PROC_WRAPPER_API_RESUME_DELAY_SECONDS"),
                default_value=self.api_resume_delay,
                negative_value=0,
            ),
        )

        self.api_task_execution_creation_conflict_retry_delay = (
            string_to_int(
                env.get(
                    "PROC_WRAPPER_API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY_SECONDS"
                ),
                default_value=default_task_execution_creation_conflict_retry_delay,
            )
            or 0
        )

        self.api_request_timeout = string_to_int(
            env.get("PROC_WRAPPER_API_REQUEST_TIMEOUT_SECONDS"),
            default_value=self.api_request_timeout,
        )

        self.send_pid = (
            string_to_bool(
                env.get("PROC_WRAPPER_SEND_PID"), default_value=self.send_pid
            )
            or False
        )

        self.send_hostname = (
            string_to_bool(
                env.get("PROC_WRAPPER_SEND_HOSTNAME"), default_value=self.send_hostname
            )
            or False
        )

        self.send_input_value = (
            string_to_bool(
                env.get("PROC_WRAPPER_SEND_INPUT_VALUE"),
                default_value=self.send_input_value,
            )
            or False
        )

        self.send_runtime_metadata = (
            string_to_bool(
                env.get("PROC_WRAPPER_SEND_RUNTIME_METADATA"),
                default_value=self.send_runtime_metadata,
            )
            or False
        )

        self.runtime_metadata_refresh_interval = string_to_int(
            env.get("PROC_WRAPPER_RUNTIME_METADATA_REFRESH_INTERVAL_SECONDS"),
            default_value=self.runtime_metadata_refresh_interval,
        )

    @staticmethod
    def _push_error(errors: dict[str, list[str]], name: str, error: str) -> None:
        error_list = errors.get(name)
        if error_list is None:
            errors[name] = [error]
        else:
            error_list.append(error)

    @classmethod
    def _validate_probability(
        cls, errors: dict[str, list[str]], p: float, param_name: str
    ) -> None:
        if p < 0.0 or p > 1.0:
            cls._push_error(
                errors, param_name, "Probability must be between 0.0 and 1.0"
            )


def json_encoded(s: str):
    return json.loads(s)


def make_arg_parser():
    parser = argparse.ArgumentParser(
        prog="proc_wrapper",
        description="""
Wraps the execution of processes so that a service API endpoint (CloudReactor)
is optionally informed of the progress.
Also implements retries, timeouts, and secret injection into the
environment.
    """,
    )

    parser.add_argument("command", nargs=argparse.REMAINDER)

    parser.add_argument(
        "-v", "--version", action="store_true", help="Print the version and exit"
    )

    task_group = parser.add_argument_group("task", "Task settings")
    task_group.add_argument(
        "-n",
        "--task-name",
        help="""
Name of Task (either the Task Name or the Task UUID must be specified""",
    )
    task_group.add_argument(
        "--task-uuid",
        help="""
UUID of Task (either the Task Name or the Task UUID must be specified)""",
    )
    task_group.add_argument(
        "-a",
        "--auto-create-task",
        action="store_true",
        help="Create the Task even if not known by the Task Management server",
    )
    task_group.add_argument(
        "--auto-create-task-run-environment-name",
        help="""
Name of the Run Environment to use if auto-creating the Task (either the name or
UUID of the Run Environment must be specified if auto-creating the Task).
Defaults to the deployment name if the Run Environment UUID is not specified.""",
    )
    task_group.add_argument(
        "--auto-create-task-run-environment-uuid",
        help="""
UUID of the Run Environment to use if auto-creating the Task (either the name or
UUID of the Run Environment must be specified if auto-creating the Task)""",
    )
    task_group.add_argument(
        "--auto-create-task-props",
        type=json_encoded,
        help="""
Additional properties of the auto-created Task, in JSON format.
See https://apidocs.cloudreactor.io/#operation/api_v1_tasks_create for the
schema.""",
    )
    task_group.add_argument(
        "--force-task-active",
        action="store_const",
        const=True,
        help="""
Indicates that the auto-created Task should be scheduled and made a service by
the Task Management server, if applicable. Otherwise, auto-created Tasks are marked
passive.""",
    )
    task_group.add_argument(
        "--task-execution-uuid", help="UUID of Task Execution to attach to"
    )
    task_group.add_argument(
        "--task-version-number", help="Numeric version of the Task's source code"
    )
    task_group.add_argument(
        "--task-version-text", help="Human readable version of the Task's source code"
    )
    task_group.add_argument(
        "--task-version-signature",
        help="""
Version signature of the Task's source code (such as a git commit hash)""",
    )
    task_group.add_argument(
        "--build-task-execution-uuid",
        help="UUID of Task Execution that built this Task's source code",
    )
    task_group.add_argument(
        "--deployment-task-execution-uuid",
        help="UUID of Task Execution that deployed this Task to the Runtime Environment",
    )
    task_group.add_argument(
        "--execution-method-props",
        type=json_encoded,
        help="""
Additional properties of the execution method, in JSON format.
See https://apidocs.cloudreactor.io/#operation/api_v1_task_executions_create
for the schema.""",
    )
    task_group.add_argument(
        "-s",
        "--service",
        action="store_true",
        help="Indicate that this is a Task that should run indefinitely",
    )
    task_group.add_argument(
        "--schedule", help="Execution schedule reported to the Task Management server"
    )
    task_group.add_argument(
        "--max-concurrency",
        help="""
Maximum number of concurrent Task Executions of the same Task.
Defaults to 1.""",
    )
    task_group.add_argument(
        "--max-conflicting-age",
        default=UNSET_INT_VALUE,
        help=f"""
Maximum age of conflicting Tasks to consider, in seconds. -1 means no limit.
Defaults to the heartbeat interval, plus {HEARTBEAT_DELAY_TOLERANCE_SECONDS}
seconds for services that send heartbeats. Otherwise, defaults to no limit.""",
    )
    task_group.add_argument(
        "--execution_method_type",
        help="""
Known execution method type, used to determine how to fetch runtime metadata
""",
    )
    task_group.add_argument(
        "--task-instance-metadata",
        type=json_encoded,
        help="Additional metadata about the Task instance, in JSON format",
    )

    api_group = parser.add_argument_group("api", "API client settings")
    api_group.add_argument(
        "--api-base-url",
        default=DEFAULT_API_BASE_URL,
        help=f"Base URL of API server. Defaults to {DEFAULT_API_BASE_URL}",
    )
    api_group.add_argument(
        "-k",
        "--api-key",
        help="""
API key. Must have at least the Task access level, or Developer access level for
auto-created Tasks.""",
    )
    api_group.add_argument(
        "--api-heartbeat-interval",
        default=UNSET_INT_VALUE,
        help=f"""
Number of seconds to wait between sending heartbeats to the Task Management server.
-1 means to not send heartbeats.
Defaults to {DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_HEARTBEAT_INTERVAL_SECONDS}
for concurrency limited services, {DEFAULT_API_HEARTBEAT_INTERVAL_SECONDS}
otherwise.""",
    )
    api_group.add_argument(
        "--api-error-timeout",
        default=DEFAULT_API_ERROR_TIMEOUT_SECONDS,
        help=f"""
Number of seconds to wait while receiving recoverable errors from the API
server. Defaults to {DEFAULT_API_ERROR_TIMEOUT_SECONDS}.""",
    )
    api_group.add_argument(
        "--api-final-update-timeout",
        default=DEFAULT_API_FINAL_UPDATE_TIMEOUT_SECONDS,
        help=f"""
Number of seconds to wait while receiving recoverable errors from the Task Management server
when sending the final update before exiting. Defaults to
{DEFAULT_API_FINAL_UPDATE_TIMEOUT_SECONDS}.""",
    )
    api_group.add_argument(
        "--api-retry-delay",
        default=DEFAULT_API_FINAL_UPDATE_TIMEOUT_SECONDS,
        help=f"""
Number of seconds to wait before retrying an API request.
Defaults to {DEFAULT_API_RETRY_DELAY_SECONDS}.""",
    )
    api_group.add_argument(
        "--api-resume-delay",
        default=DEFAULT_API_RESUME_DELAY_SECONDS,
        help=f"""
Number of seconds to wait before resuming API requests, after retries are
exhausted. Defaults to {DEFAULT_API_RESUME_DELAY_SECONDS}.
-1 means to never resume.""",
    )
    api_group.add_argument(
        "--api-task-execution-creation-error-timeout",
        help=f"""
Number of seconds to keep retrying Task Execution creation while receiving
error responses from the Task Management server. -1 means to keep trying indefinitely.
Defaults to {DEFAULT_API_TASK_EXECUTION_CREATION_TIMEOUT_SECONDS}.""",
    )
    api_group.add_argument(
        "--api-task-execution-creation-conflict-timeout",
        default=DEFAULT_API_TASK_EXECUTION_CREATION_TIMEOUT_SECONDS,
        help=f"""
Number of seconds to keep retrying Task Execution creation while conflict is
detected by the Task Management server. -1 means to keep trying indefinitely. Defaults to
{DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_CREATION_TIMEOUT_SECONDS} for
concurrency limited services, 0 otherwise.""",
    )
    api_group.add_argument(
        "--api-task-execution-creation-conflict-retry-delay",
        default=UNSET_INT_VALUE,
        help=f"""
Number of seconds between attempts to retry Task Execution creation after
conflict is detected. Defaults to
{DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_CREATION_CONFLICT_RETRY_DELAY_SECONDS}
for concurrency-limited services,
{DEFAULT_API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY_SECONDS}
otherwise.""",
    )
    api_group.add_argument(
        "--api-request-timeout",
        default=DEFAULT_API_REQUEST_TIMEOUT_SECONDS,
        help=f"""
Timeout for contacting API server, in seconds. Defaults to
{DEFAULT_API_REQUEST_TIMEOUT_SECONDS}.""",
    )
    api_group.add_argument(
        "-o",
        "--offline-mode",
        action="store_true",
        help="Do not communicate with or rely on an API server",
    )
    api_group.add_argument(
        "-p",
        "--prevent-offline-execution",
        action="store_true",
        help="""
Do not start processes if the Task Management server is unavailable or the wrapper is
misconfigured.""",
    )
    api_group.add_argument(
        "-m",
        "--api-managed-probability",
        type=float,
        default=1.0,
        dest="api_managed_probability",
        help="""
Sample notifications to the Task Management server with a given probability when starting
an execution. Defaults to 1.0 (always send notifications).""",
    )
    api_group.add_argument(
        "--api-failure-report-probability",
        type=float,
        default=1.0,
        dest="api_failure_report_probability",
        help="""
If the notification of an execution was not previously sent on startup and the
execution fails, notify the Task Management server with the given probability. Defaults to
1.0 (always send failure notifications).""",
    )
    api_group.add_argument(
        "--api-timeout-report-probability",
        type=float,
        default=1.0,
        dest="api_timeout_report_probability",
        help="""
If the notification of an execution was not previously sent on startup and the
execution times out, notify the Task Management server with given probability. Defaults to
1.0 (always send timeout notifications).""",
    )
    api_group.add_argument(
        "-d", "--deployment", help="Deployment name (production, staging, etc.)"
    )
    api_group.add_argument(
        "--send-input-value",
        action="store_true",
        help="Send the input value the Task Management server",
    )
    api_group.add_argument(
        "--send-pid",
        action="store_true",
        help="Send the process ID to the Task Management server",
    )
    api_group.add_argument(
        "--send-hostname",
        action="store_true",
        help="Send the hostname to the Task Management server",
    )
    api_group.add_argument(
        "--no-send-runtime-metadata",
        action="store_false",
        dest="send_runtime_metadata",
        help="Do not send metadata about the runtime environment",
    )
    api_group.add_argument(
        "--runtime-metadata-refresh-interval",
        help="""
Refresh interval for runtime metadata, in seconds. The default value depends on
the execution method.
""",
    )

    io_group = parser.add_argument_group("io", "Input and result settings")
    io_group.add_argument(
        "-i",
        "--input-value",
        help="The input value",
    )
    io_group.add_argument(
        "--input-env-var-name",
        help="""
The value of this environment variable is used as the input value for the
wrapped process or embedded function. The value is sent back to the API server
as the input value of the Task Execution.""",
    )
    io_group.add_argument(
        "--input-filename",
        help="""
The name of the file containing the value used as the input value for the
wrapped process or embedded function. The contents of the file are sent back to
the API server as the input value of the Task Execution.""",
    )
    io_group.add_argument(
        "--cleanup-input-file",
        action="store_true",
        dest="cleanup_input_file",
        help="""
Remove the input file before exit. If this parameter is omitted, the input file
will only be removed if it was written by the wrapper.""",
    )
    # Maybe add --no-cleanup-input-file to force skip of removal?
    io_group.add_argument(
        "--input-value-format",
        help=f"""
The format of the value used as the input value for the Task Execution.
Options are '{FORMAT_JSON}', '{FORMAT_YAML}', or '{FORMAT_TEXT}'.
Defaults to '{FORMAT_TEXT}'.""",
    )

    io_group.add_argument(
        "--result-filename",
        help="""
The name of the file the wrapped process will write with the result value. The
contents of the file are sent back to the API server as the result value of the
Task Execution.""",
    )
    io_group.add_argument(
        "--result-value-format",
        help=f"""
The format of the file that the wrapped process will write with the result
value. Options are '{FORMAT_JSON}', '{FORMAT_YAML}', or '{FORMAT_TEXT}'.
Defaults to '{FORMAT_TEXT}'.""",
    )
    io_group.add_argument(
        "--no-cleanup-result-file",
        action="store_false",
        dest="cleanup_result_file",
        help="""
Do not delete the result file after the Task Execution completes. If this
parameter is omitted, the result file will be deleted.""",
    )

    log_group = parser.add_argument_group("log", "Logging settings")
    log_group.add_argument(
        "-l",
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=DEFAULT_LOG_LEVEL,
        help="Log level",
    )
    log_group.add_argument(
        "--log-secrets", action="store_true", help="Log sensitive information"
    )
    log_group.add_argument(
        "--log-input-value", action="store_true", help="Log input value"
    )
    log_group.add_argument(
        "--log-result-value", action="store_true", help="Log result value"
    )
    log_group.add_argument(
        "--exclude-timestamps-in-log",
        action="store_false",
        dest="include_timestamps_in_log",
        help="""
Exclude timestamps in log (possibly because the log stream will be enriched by
timestamps automatically by a logging service like AWS CloudWatch Logs)""",
    )
    log_group.add_argument(
        "--num-log-lines-sent-on-failure",
        default=0,
        help="""
The number of trailing log lines to send to the API server if the Task
Execution fails. Defaults to 0 (no log lines are sent).""",
    )
    log_group.add_argument(
        "--num-log-lines-sent-on-timeout",
        default=0,
        help="""
The number of trailing log lines to send to the API server if the Task Execution
fails. Defaults to 0 (no log lines are sent).""",
    )
    log_group.add_argument(
        "--num-log-lines-sent-on-success",
        default=0,
        help="""
The number of trailing log lines to send to the API server if the Task Execution
succeeds. Defaults to 0 (no log lines are sent).""",
    )
    # Future use, if we want to log lines sent on heartbeat
    #     log_group.add_argument(
    #         "--log-lines-sent-on-heartbeat",
    #         default=0,
    #         help="""
    # The number of trailing log lines to send to the API server when sending
    # heartbeats. Defaults to 0 (no log lines are sent).""",
    #     )
    log_group.add_argument(
        "--max-log-line-length",
        default=DEFAULT_MAX_LOG_LINE_LENGTH,
        help=f"""
The maximum number of characters in a saved log line. If a line is longer than
this value, it will be truncated. Defaults to {DEFAULT_MAX_LOG_LINE_LENGTH}.""",
    )
    log_group.add_argument(
        "--separate-stdout-and-stderr-logs",
        action="store_false",
        dest="merge_stdout_and_stderr_logs",
        help="""
Separate stdout and stderr streams when reporting log lines. Otherwise, the
streams are merged into the stdout stream.""",
    )
    log_group.add_argument(
        "--ignore-stdout",
        action="store_true",
        help="""Do send stdout log lines to the API server""",
    )
    log_group.add_argument(
        "--ignore-stderr",
        action="store_true",
        help="""Do send stderr log lines to the API server""",
    )
    process_group = parser.add_argument_group("process", "Process settings")
    process_group.add_argument(
        "-w",
        "--work-dir",
        default=".",
        help="Working directory. Defaults to the current directory.",
    )
    process_group.add_argument(
        "-c",
        "--command-line",
        default=".",
        help="Command line to execute",
    )
    process_group.add_argument(
        "--shell-mode",
        choices=[
            SHELL_MODE_AUTO,
            SHELL_MODE_FORCE_ENABLE,
            SHELL_MODE_FORCE_DISABLE,
        ],
        default=SHELL_MODE_AUTO,
        help=f"""
Indicates if the process command should be executed in a shell.
Executing in a shell allows shell scripts, commands, and expressions to be used,
with the disadvantage that termination signals may not be propagated to
child processes.

Options are:
{SHELL_MODE_FORCE_ENABLE} -- Force the command to be executed in a shell;
{SHELL_MODE_FORCE_DISABLE} -- Force the command to be executed without a shell;
{SHELL_MODE_AUTO} -- Auto-detect the shell mode by analyzing the command.
""",
    )
    process_group.add_argument(
        "--no-strip-shell-wrapping",
        action="store_false",
        dest="strip_shell_wrapping",
        help="""
Do not strip the command-line of shell wrapping like "/bin/sh -c" that can be
introduced by Docker when using shell form of ENTRYPOINT and CMD.
""",
    )
    process_group.add_argument(
        "--no-process-group-termination",
        action="store_false",
        dest="process_group_termination",
        help="""
Send termination and kill signals to the wrapped process only, instead of its
process group (which is the default). Sending to the process group allows all
child processes to receive the signals, even if the wrapped process does not
forward signals. However, if your wrapped process manually handles and forwards
signals to its child processes, you probably want to send signals to only
your wrapped process.
""",
    )

    process_group.add_argument(
        "-t",
        "--process-timeout",
        help="""
Timeout for process completion, in seconds. -1 means no timeout, which is the
default.""",
    )
    process_group.add_argument(
        "-r",
        "--process-max-retries",
        default=0,
        help="""
Maximum number of times to retry failed processes. -1 means to retry forever.
Defaults to 0.""",
    )
    process_group.add_argument(
        "--process-retry-delay",
        default=DEFAULT_PROCESS_RETRY_DELAY_SECONDS,
        help=f"""
Number of seconds to wait before retrying a process. Defaults to
{DEFAULT_PROCESS_RETRY_DELAY_SECONDS}.""",
    )
    process_group.add_argument(
        "--process-check-interval",
        default=DEFAULT_PROCESS_CHECK_INTERVAL_SECONDS,
        help=f"""
Number of seconds to wait between checking the status of processes.
Defaults to {DEFAULT_PROCESS_CHECK_INTERVAL_SECONDS}.""",
    )
    process_group.add_argument(
        "--process-termination-grace-period",
        default=DEFAULT_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS,
        help=f"""
Number of seconds to wait after sending SIGTERM to a process, but before killing
it with SIGKILL.
Defaults to {DEFAULT_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS}.""",
    )

    update_group = parser.add_argument_group("updates", "Status update settings")

    update_group.add_argument(
        "--enable-status-update-listener",
        action="store_true",
        help="""
Listen for status updates from the process, sent on the status socket port via
UDP. If not specified, status update messages will not be read.""",
    )
    update_group.add_argument(
        "--status-update-socket-port",
        help=f"""
The port used to receive status updates from the process.
Defaults to {DEFAULT_STATUS_UPDATE_SOCKET_PORT}.""",
    )
    update_group.add_argument(
        "--status-update-message-max-bytes",
        help=f"""
The maximum number of bytes status update messages can be. Defaults to
{DEFAULT_STATUS_UPDATE_MESSAGE_MAX_BYTES}.""",
    )
    update_group.add_argument(
        "--status-update-interval",
        help="""
Minimum of number of seconds to wait between sending status updates to the API
server. -1 means to not send status updates except with heartbeats. Defaults to
-1.""",
    )

    config_group = parser.add_argument_group(
        "configuration", "Environment/configuration resolution settings"
    )

    config_group.add_argument(
        "-e",
        "--env",
        action="append",
        dest="env_locations",
        help="""
    Location of either local file, AWS S3 ARN, AWS Secrets Manager ARN, or
    AWS Systems Manager Parameter Store identifier containing properties used
    to populate the environment for embedded mode, or the process environment
    for wrapped mode. By default, the file format is assumed to be dotenv.
    Specify multiple times to include multiple locations.""",
    )

    config_group.add_argument(
        "--config",
        action="append",
        dest="config_locations",
        help="""
Location of either local file, AWS S3 ARN, AWS Secrets Manager ARN, or
AWS Systems Manager Parameter Store identifier containing properties used to
populate the configuration for embedded mode. By default, the file format is
assumed to be in JSON. Specify multiple times to include multiple locations.""",
    )

    config_group.add_argument(
        "--config-merge-strategy",
        choices=[
            CONFIG_MERGE_STRATEGY_DEEP,
            CONFIG_MERGE_STRATEGY_SHALLOW,
            "REPLACE",
            "ADDITIVE",
            "TYPESAFE_REPLACE",
            "TYPESAFE_ADDITIVE",
        ],
        default=DEFAULT_CONFIG_MERGE_STRATEGY,
        help=f"""
Merge strategy for merging configurations.
Defaults to '{DEFAULT_CONFIG_MERGE_STRATEGY}', which does not require mergedeep.
Besides the '{CONFIG_MERGE_STRATEGY_SHALLOW}' strategy, all other strategies
require the mergedeep extra to be installed.
            """,
    )

    config_group.add_argument(
        "--overwrite-env-during-resolution",
        action="store_true",
        help="""
Overwrite existing environment variables when resolving them""",
    )

    config_group.add_argument(
        "--config-ttl",
        help="""
Number of seconds to cache resolved environment variables and configuration
properties instead of refreshing them when a process restarts. -1 means
to never refresh. Defaults to -1.""",
    )

    config_group.add_argument(
        "--no-fail-fast-config-resolution",
        action="store_false",
        dest="fail_fast_config_resolution",
        help="""
Continue execution even if an error occurs resolving the configuration""",
    )

    config_group.add_argument(
        "--resolved-env-var-name-prefix",
        default=DEFAULT_RESOLVABLE_ENV_VAR_NAME_PREFIX,
        help=f"""
Required prefix for names of environment variables that should resolved.
The prefix will be removed in the resolved variable name.
Defaults to '{DEFAULT_RESOLVABLE_ENV_VAR_NAME_PREFIX}'.""",
    )

    config_group.add_argument(
        "--resolved-env-var-name-suffix",
        default=DEFAULT_RESOLVABLE_ENV_VAR_NAME_SUFFIX,
        help=f"""
Required suffix for names of environment variables that should resolved.
The suffix will be removed in the resolved variable name.
Defaults to '{DEFAULT_RESOLVABLE_ENV_VAR_NAME_SUFFIX}'.""",
    )

    config_group.add_argument(
        "--resolved-config-property-name-prefix",
        default=DEFAULT_RESOLVABLE_CONFIG_PROPERTY_NAME_PREFIX,
        help=f"""
Required prefix for names of configuration properties that should resolved.
The prefix will be removed in the resolved property name.
Defaults to '{DEFAULT_RESOLVABLE_CONFIG_PROPERTY_NAME_PREFIX}'.""",
    )

    config_group.add_argument(
        "--resolved-config-property-name-suffix",
        default=DEFAULT_RESOLVABLE_CONFIG_PROPERTY_NAME_SUFFIX,
        help=f"""
Required suffix for names of configuration properties that should resolved.
The suffix will be removed in the resolved property name.
Defaults to '{DEFAULT_RESOLVABLE_CONFIG_PROPERTY_NAME_SUFFIX}'.""",
    )

    config_group.add_argument(
        "--env-var-name-for-config",
        help="""
The name of the environment variable used to set to the value of the
JSON encoded configuration. Defaults to not setting any environment variable.""",
    )

    config_group.add_argument(
        "--config-property-name-for-env",
        help="""
The name of the configuration property used to set to the value of the
JSON encoded environment. Defaults to not setting any property.""",
    )

    config_group.add_argument(
        "--env-output-filename",
        help=f"""
The filename to write the resolved environment variables to.
Defaults to '.env' if the env output format is '{FORMAT_DOTENV}',
'env.json' if the env output format is '{FORMAT_JSON}', and
'env.yml' if the config output format is '{FORMAT_YAML}'.
""",
    )

    config_group.add_argument(
        "--env-output-format",
        help=f"""
The format used to write the resolved environment variables file.
One of '{FORMAT_DOTENV}', '{FORMAT_JSON}', or '{FORMAT_YAML}'. Will be
auto-detected from the filename of the env output filename if possible. Defaults
to '{FORMAT_DOTENV}' if the env output filename is set but the format cannot be
auto-detected from the filename.
""",
    )

    config_group.add_argument(
        "--config-output-filename",
        help=f"""
The filename to write the resolved configuration to.
Defaults to 'config.json' if config output format is '{FORMAT_JSON}',
'config.yml' if the config output format is '{FORMAT_YAML}', and
'config.env' if the config output format is '{FORMAT_DOTENV}'.
""",
    )

    config_group.add_argument(
        "--config-output-format",
        help=f"""
The format used to write the resolved configuration file. One of
'{FORMAT_DOTENV}', '{FORMAT_JSON}', or '{FORMAT_YAML}'. Will be auto-detected
from the filename of the config output filename if possible. Defaults to
'{FORMAT_JSON}' if the config output filename is set but the format cannot be
auto-detected from the filename.
""",
    )

    config_group.add_argument(
        "--exit-after-writing-variables",
        action="store_true",
        help="""
Exit after writing the resolved environment variables and configuration""",
    )

    container_group = parser.add_argument_group("container", "Container settings")
    container_group.add_argument(
        "--main-container-name",
        help="""The name of the container that is monitored""",
    )
    container_group.add_argument(
        "--monitor-container-name",
        help="""The name of the container that will monitor the main container""",
    )
    container_group.add_argument(
        "--sidecar-container-mode",
        action="store_true",
        help="""Indicates that the current container is a sidecar container that will monitor the main container""",
    )

    rollbar_group = parser.add_argument_group("rollbar", "Rollbar settings")
    rollbar_group.add_argument(
        "--rollbar-access-token",
        help="""
Access token for Rollbar (used to report error when communicating with API
server)""",
    )
    rollbar_group.add_argument(
        "--rollbar-retries",
        help=f"""
Number of retries per Rollbar request.
Defaults to {DEFAULT_ROLLBAR_RETRIES}.""",
    )
    rollbar_group.add_argument(
        "--rollbar-retry-delay",
        default=DEFAULT_ROLLBAR_RETRY_DELAY_SECONDS,
        help=f"""
Number of seconds to wait before retrying a Rollbar request. Defaults to
{DEFAULT_ROLLBAR_RETRY_DELAY_SECONDS}.""",
    )
    rollbar_group.add_argument(
        "--rollbar-timeout",
        default=DEFAULT_ROLLBAR_TIMEOUT_SECONDS,
        help=f"""
Timeout for contacting Rollbar server, in seconds. Defaults to
{DEFAULT_ROLLBAR_TIMEOUT_SECONDS}.""",
    )

    return parser
