import argparse
import json
import logging
import os
import re
from typing import Any, Dict, List, NamedTuple, Optional, cast

from .common_utils import coalesce, encode_int, string_to_bool, string_to_int
from .runtime_metadata import RuntimeMetadata

_DEFAULT_LOG_LEVEL = "WARNING"

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

CONFIG_MERGE_STRATEGY_SHALLOW = "SHALLOW"
DEFAULT_CONFIG_MERGE_STRATEGY = CONFIG_MERGE_STRATEGY_SHALLOW
DEFAULT_MAX_CONFIG_RESOLUTION_ITERATIONS = 3
DEFAULT_MAX_CONFIG_RESOLUTION_DEPTH = 5
DEFAULT_ENV_VAR_NAME_FOR_CONFIG = "TASK_CONFIG"
DEFAULT_CONFIG_VAR_NAME_FOR_ENV = "ENV"
DEFAULT_RESOLVABLE_ENV_VAR_NAME_PREFIX = ""
DEFAULT_RESOLVABLE_ENV_VAR_NAME_SUFFIX = "_FOR_PROC_WRAPPER_TO_RESOLVE"
DEFAULT_RESOLVABLE_CONFIG_PROPERTY_NAME_PREFIX = ""
DEFAULT_RESOLVABLE_CONFIG_PROPERTY_NAME_SUFFIX = "__to_resolve"


DEFAULT_STATUS_UPDATE_SOCKET_PORT = 2373
DEFAULT_STATUS_UPDATE_MESSAGE_MAX_BYTES = 64 * 1024

DEFAULT_ROLLBAR_TIMEOUT_SECONDS = 30
DEFAULT_ROLLBAR_RETRIES = 2
DEFAULT_ROLLBAR_RETRY_DELAY_SECONDS = 120

DEFAULT_PROCESS_CHECK_INTERVAL_SECONDS = 10
DEFAULT_PROCESS_RETRY_DELAY_SECONDS = 60
DEFAULT_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS = 30

UNSET_INT_VALUE = -1000000


_logger = logging.getLogger(__name__)
_logger.addHandler(logging.NullHandler())


class ProcWrapperParamValidationErrors(NamedTuple):
    process_errors: Dict[str, List[str]]
    process_warnings: Dict[str, List[str]]
    task_errors: Dict[str, List[str]]
    task_warnings: Dict[str, List[str]]

    def log(self):
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
    def __init__(self):
        self.log_secrets: bool = False
        self.env_locations: List[str] = []
        self.config_locations: List[str] = []
        self.config_merge_strategy: str = CONFIG_MERGE_STRATEGY_SHALLOW
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

    def override_resolver_params_from_env(self, env: Dict[str, str]) -> None:
        self.log_secrets = (
            string_to_bool(
                env.get("PROC_WRAPPER_LOG_SECRETS"), default_value=self.log_secrets
            )
            or False
        )

        env_locations_in_env = env.get("PROC_WRAPPER_ENV_LOCATIONS")
        if env_locations_in_env is not None:
            self.env_locations = self.split_location_string(env_locations_in_env)

        config_locations_in_env = env.get("PROC_WRAPPER_CONFIG_LOCATIONS")
        if config_locations_in_env is not None:
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

    def split_location_string(self, locations: str) -> List[str]:
        # Use , or ; to split locations, except they may be escaped by
        # backslashes. Any occurrence of , or ; in a location string
        # must be backslash escaped. This doesn't handle the weird case
        # when a location contains "\," or "\;".
        return [
            location.replace(r"\,", ",").replace(r"\;", ";").replace(r"\\\\", r"\\")
            for location in re.split(r"\s*(?<!(?<!\\)\\)[,;]\s*", locations)
        ]


class ProcWrapperParams(ConfigResolverParams):
    def __init__(self, embedded_mode: bool = True):
        super().__init__()

        self.embedded_mode = embedded_mode

        self.task_name: Optional[str] = None
        self.task_uuid: Optional[str] = None
        self.auto_create_task: bool = False
        self.auto_create_task_props: Optional[Dict[str, Any]] = None
        self.execution_method_props: Optional[Dict[str, Any]] = None
        self.auto_create_task_run_environment_name: Optional[str] = None
        self.auto_create_task_run_environment_uuid: Optional[str] = None
        self.auto_create_task_run_environment_task_props: Optional[str] = None
        self.force_task_active: Optional[bool] = None
        self.task_is_passive: bool = True
        self.task_execution_uuid: Optional[str] = None
        self.task_version_number: Optional[int] = None
        self.task_version_text: Optional[str] = None
        self.task_version_signature: Optional[str] = None
        self.schedule: Optional[str] = None
        self.max_concurrency: Optional[int] = None
        self.max_conflicting_age: Optional[int] = None
        self.task_instance_metadata: Optional[Dict[str, Any]] = None

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
        self.api_request_timeout: Optional[int] = None
        self.send_pid: bool = False
        self.send_hostname: bool = False
        self.send_runtime_metadata: bool = True

        self.command: Optional[List[str]] = None
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

        self.rollbar_access_token: Optional[str] = None
        self.rollbar_retries: Optional[int] = DEFAULT_ROLLBAR_RETRIES
        self.rollbar_retry_delay: int = DEFAULT_ROLLBAR_RETRY_DELAY_SECONDS
        self.rollbar_timeout: int = DEFAULT_ROLLBAR_TIMEOUT_SECONDS

    def override_proc_wrapper_params_from_env(
        self,
        env: Dict[str, str],
        mutable_only: bool = False,
        runtime_metadata: Optional[RuntimeMetadata] = None,
    ) -> None:
        if not mutable_only:
            self._override_immutable_from_env(env, runtime_metadata=runtime_metadata)

        self._override_mutable_from_env(env)

    def validation_errors(
        self, runtime_metadata: Optional[RuntimeMetadata] = None
    ) -> ProcWrapperParamValidationErrors:
        process_errors: Dict[str, List[str]] = {}
        process_warnings: Dict[str, List[str]] = {}
        task_errors: Dict[str, List[str]] = {}
        task_warnings: Dict[str, List[str]] = {}

        errors = ProcWrapperParamValidationErrors(
            process_errors=process_errors,
            process_warnings=process_warnings,
            task_errors=task_errors,
            task_warnings=task_warnings,
        )

        if not self.embedded_mode:
            if not self.command:
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

        if self.offline_mode:
            if self.prevent_offline_execution:
                self._push_error(
                    process_errors,
                    "prevent_offline_execution",
                    "Offline mode and offline execution prevention cannot both be enabled.",
                )
        else:
            if not self.api_key:
                self._push_error(task_errors, "api_key", "No API key specified.")

            if (not self.task_uuid) and (not self.task_name):
                self._push_error(
                    task_errors, "task_name", "No Task UUID or name specified."
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
                    or (runtime_metadata.execution_method_capability is None)
                ):
                    self._push_error(
                        task_warnings,
                        "force_task_passive",
                        "Task may not be active unless execution method capability can be determined.",
                    )
                    self.task_is_passive = True

        return errors

    def run_mode_label(self) -> str:
        return "embedded" if self.embedded_mode else "wrapped"

    def log_configuration(self) -> None:
        _logger.info(f"Run mode = {self.run_mode_label()}")
        _logger.info(f"Task Execution UUID = {self.task_execution_uuid}")
        _logger.info(f"Task UUID = {self.task_uuid}")
        _logger.info(f"Task name = {self.task_name}")
        _logger.info(f"Deployment = '{self.deployment}'")

        _logger.info(f"Task version number = {self.task_version_number}")
        _logger.info(f"Task version text = {self.task_version_text}")
        _logger.info(f"Task version signature = {self.task_version_signature}")

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

        _logger.info(f"Task instance metadata = {self.task_instance_metadata}")

        _logger.debug(f"Task is a service = {self.service}")
        _logger.debug(f"Max concurrency = {self.max_concurrency}")
        _logger.debug(f"Offline mode = {self.offline_mode}")
        _logger.debug(f"Prevent offline execution = {self.prevent_offline_execution}")
        _logger.debug(f"Process retries = {self.process_max_retries}")
        _logger.debug(f"Process retry delay = {self.process_retry_delay}")
        _logger.debug(f"Process check interval = {self.process_check_interval}")

        _logger.debug(
            f"Maximum age of conflicting processes = {self.max_conflicting_age}"
        )

        if not self.offline_mode:
            _logger.debug(f"API base URL = '{self.api_base_url}'")

            if self.log_secrets:
                _logger.debug(f"API key = '{self.api_key}'")

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

        if self.rollbar_access_token:
            if self.log_secrets:
                _logger.debug(f"Rollbar API key = '{self.rollbar_access_token}'")

            _logger.debug(f"Rollbar timeout = {self.rollbar_timeout}")
            _logger.debug(f"Rollbar retries = {self.rollbar_retries}")
            _logger.debug(f"Rollbar retry delay = {self.rollbar_retry_delay}")
        else:
            _logger.debug("Rollbar is disabled")

        if not self.embedded_mode:
            _logger.info(f"Command = {self.command}")
            _logger.info(f"Work dir = '{self.work_dir}'")

            enable_status_update_listener = self.enable_status_update_listener
            _logger.debug(f"Enable status listener = {enable_status_update_listener}")

            if enable_status_update_listener:
                _logger.debug(f"Status socket port = {self.status_update_socket_port}")
                _logger.debug(
                    f"Status update message max bytes = {self.status_update_message_max_bytes}"
                )

        _logger.debug(f"Status update interval = {self.status_update_interval}")

    def populate_env(self, env: Dict[str, str]) -> None:
        if self.deployment:
            env["PROC_WRAPPER_DEPLOYMENT"] = self.deployment

        env["PROC_WRAPPER_OFFLINE_MODE"] = str(self.offline_mode).upper()

        if not self.offline_mode:
            env["PROC_WRAPPER_API_BASE_URL"] = self.api_base_url
            env["PROC_WRAPPER_API_KEY"] = str(self.api_key)
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

            env["PROC_WRAPPER_TASK_EXECUTION_UUID"] = str(self.task_execution_uuid)

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

    def _override_immutable_from_env(
        self, env: Dict[str, str], runtime_metadata: Optional[RuntimeMetadata]
    ) -> None:
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

        self.deployment = env.get("PROC_WRAPPER_DEPLOYMENT", self.deployment)

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

        if self.offline_mode:
            return

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
                    True,
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
        self.task_name = env.get(
            "PROC_WRAPPER_TASK_NAME", auto_create_task_props.get("name", self.task_name)
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

        # Properties to be reported to CloudRector
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

    def _override_mutable_from_env(self, env: Dict[str, str]) -> None:
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

            self.command = self.command
            self.work_dir = env.get("PROC_WRAPPER_WORK_DIR", self.work_dir)

        task_instance_metadata_str = env.get("PROC_WRAPPER_TASK_INSTANCE_METADATA")

        # This could be logged for debugging, so still load it even if we
        # don't send it to the API server.
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

        self.send_runtime_metadata = (
            string_to_bool(
                env.get("PROC_WRAPPER_SEND_RUNTIME_METADATA"),
                default_value=self.send_runtime_metadata,
            )
            or False
        )

    @staticmethod
    def _push_error(errors: Dict[str, List[str]], name: str, error: str) -> None:
        error_list = errors.get(name)
        if error_list is None:
            errors[name] = [error]
        else:
            error_list.append(error)


def json_encoded(s: str):
    return json.loads(s)


def make_arg_parser(require_command=True):
    parser = argparse.ArgumentParser(
        prog="proc_wrapper",
        description="""
Wraps the execution of processes so that a service API endpoint (CloudReactor)
is optionally informed of the progress.
Also implements retries, timeouts, and secret injection into the
environment.
    """,
    )

    if require_command:
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
        help="Create the Task even if not known by the API server",
    )
    task_group.add_argument(
        "--auto-create-task-run-environment-name",
        help="Name of the Run Environment to use if auto-creating the Task (either the name or UUID of the Run Environment must be specified if auto-creating the Task). Defaults to the deployment name if the Run Environment UUID is not specified.",
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
the API server, if applicable. Otherwise, auto-created Tasks are marked
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
        "--execution-method-props",
        type=json_encoded,
        help="""
Additional properties of the execution method, in JSON format.
See https://apidocs.cloudreactor.io/#operation/api_v1_task_executions_create
for the schema.""",
    )
    task_group.add_argument(
        "--task-instance-metadata",
        type=json_encoded,
        help="Additional metadata about the Task instance, in JSON format",
    )
    task_group.add_argument(
        "-s",
        "--service",
        action="store_true",
        help="Indicate that this is a Task that should run indefinitely",
    )
    task_group.add_argument(
        "--schedule", help="Run schedule reported to the API server"
    )
    task_group.add_argument(
        "--max-concurrency",
        help="""
Maximum number of concurrent Task Executions allowed with the same Task UUID.
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
Number of seconds to wait between sending heartbeats to the API server.
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
Number of seconds to wait while receiving recoverable errors from the API server
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
-1 means no resumption.""",
    )
    api_group.add_argument(
        "--api-task-execution-creation-error-timeout",
        help=f"""
Number of seconds to keep retrying Task Execution creation while receiving
error responses from the API server. -1 means to keep trying indefinitely.
Defaults to {DEFAULT_API_TASK_EXECUTION_CREATION_TIMEOUT_SECONDS}.""",
    )
    api_group.add_argument(
        "--api-task-execution-creation-conflict-timeout",
        default=DEFAULT_API_TASK_EXECUTION_CREATION_TIMEOUT_SECONDS,
        help=f"""
Number of seconds to keep retrying Task Execution creation while conflict is
detected by the API server. -1 means to keep trying indefinitely. Defaults to
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
        help="Do not start processes if the API server is unavailable.",
    )
    api_group.add_argument(
        "-d", "--deployment", help="Deployment name (production, staging, etc.)"
    )
    api_group.add_argument(
        "--send-pid", action="store_true", help="Send the process ID to the API server"
    )
    api_group.add_argument(
        "--send-hostname",
        action="store_true",
        help="Send the hostname to the API server",
    )
    api_group.add_argument(
        "--no-send-runtime-metadata",
        action="store_false",
        dest="send_runtime_metadata",
        help="Do not send metadata about the runtime environment",
    )

    log_group = parser.add_argument_group("log", "Logging settings")
    log_group.add_argument(
        "-l",
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=os.environ.get("PROC_WRAPPER_LOG_LEVEL", _DEFAULT_LOG_LEVEL),
        help="Log level",
    )
    log_group.add_argument(
        "--log-secrets", action="store_true", help="Log sensitive information"
    )

    process_group = parser.add_argument_group("process", "Process settings")
    process_group.add_argument(
        "-w",
        "--work-dir",
        default=".",
        help="Working directory. Defaults to the current directory.",
    )
    process_group.add_argument(
        "-t",
        "--process-timeout",
        help="""
Timeout for process, in seconds. -1 means no timeout, which is the default.""",
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
Minimum of seconds to wait between sending status updates to the API server.
-1 means to not send status updates except with heartbeats. Defaults to -1.""",
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
    Location of either local file, AWS S3 ARN, or AWS Secrets Manager ARN
    containing properties used to populate the environment for embedded mode,
    or the process environment for wrapped mode. By default, the file format
    is assumed to be dotenv. Specify multiple times to include multiple
    locations.""",
    )

    config_group.add_argument(
        "-c",
        "--config",
        action="append",
        dest="config_locations",
        help="""
Location of either local file, AWS S3 ARN, or AWS Secrets Manager ARN
containing properties used to populate the configuration for embedded mode.
By default, the file format is assumed to be in JSON. Specify multiple times
to include multiple locations.""",
    )

    config_group.add_argument(
        "--config-merge-strategy",
        choices=[
            CONFIG_MERGE_STRATEGY_SHALLOW,
            "REPLACE",
            "ADDITIVE",
            "TYPESAFE_REPLACE",
            "TYPESAFE_ADDITIVE",
        ],
        default=DEFAULT_CONFIG_MERGE_STRATEGY,
        help=f"""
Merge strategy for merging config files with mergedeep.
Defaults to {DEFAULT_CONFIG_MERGE_STRATEGY}, which does not require mergedeep.
All other strategies require the mergedeep python package to be installed.
            """,
    )

    config_group.add_argument(
        "--overwrite_env_during_resolution",
        action="store_true",
        help="""
Do not overwrite existing environment variables when resolving them""",
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
Exit immediately if an error occurs resolving the configuration""",
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
