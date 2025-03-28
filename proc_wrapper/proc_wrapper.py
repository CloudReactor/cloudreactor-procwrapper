#!/usr/local/bin/python

# Copyright (c) 2021-present Machine Intelligence Services, Inc.
# All rights reserved.
#
# This software is provided "as is," without warranty of any kind,
# express or implied. In no event shall the author or contributors
# be held liable for any damages arising in any way from the use of
# this software.
#
# This software is dual-licensed under open source and commercial licenses:
#
# 1. The software can be licensed under the terms of the Mozilla Public
# License Version 2.0:
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# 2. The commercial license gives you the full rights to create
# and distribute software on your own terms without any open source license
# obligations.

import atexit
import json
import logging
import math
import os
import platform
import random
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from io import RawIOBase
from subprocess import Popen, TimeoutExpired
from typing import Any, Mapping, Optional, TextIO, Union
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from .common_constants import UNSET_VALUE
from .common_utils import (
    best_effort_deep_merge,
    coalesce,
    encode_int,
    parse_data_file,
    parse_data_string,
    string_to_bool,
    stringify_value,
    truncate,
    write_data_to_file,
)
from .config_resolver import ConfigResolver
from .proc_wrapper_params import ProcWrapperParams, ProcWrapperParamValidationErrors
from .runtime_metadata import (
    CommonConfiguration,
    DefaultRuntimeMetadataFetcher,
    RuntimeMetadata,
    RuntimeMetadataFetcher,
)

_logger = logging.getLogger(__name__)
_logger.addHandler(logging.NullHandler())

caught_sigterm = False


def _exit_handler(wrapper: "ProcWrapper"):
    # Prevent re-entrancy and changing of the exit code

    _logger.info("In _exit_handler")

    atexit.unregister(_exit_handler)
    wrapper.handle_exit()


def _signal_handler(signum, frame):
    global caught_sigterm
    caught_sigterm = True
    _logger.info(f"Caught signal {signum}, exiting")

    # This will cause the exit handler to be executed, if it is registered.
    # TODO: use different exit code if configured
    sys.exit(0)


def _tee_log_stream(
    process_stream: TextIO,
    sys_stream: TextIO,
    line_queue: deque[str],
    max_line_length: int,
) -> None:
    for line in iter(process_stream.readline, None):
        if line:
            sys_stream.write(line)
            line_queue.append(truncate(line, max_line_length))
        else:
            return


def _extract_log_lines(line_queue: deque[str], max_lines: int, separator: str) -> str:
    """
    Extract log lines from the queue, up to a maximum number of lines.
    """
    num_lines = len(line_queue)
    num_lines_to_remove = max(0, num_lines - max_lines)

    while num_lines_to_remove > 0:
        try:
            line_queue.popleft()
            num_lines_to_remove -= 1
        except IndexError:
            # Just in case we have fewer lines than expected
            break

    rv = separator.join(line_queue)

    line_queue.clear()

    return rv


class DequeLoggingHandler(logging.Handler):
    def __init__(self, dq: deque[str], max_line_length: int) -> None:
        """
        Initialize the logging handler with a deque to store log messages.
        """
        super().__init__()
        self.dq = dq
        self.max_line_length = max_line_length

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record to the deque.
        """
        # This method is called by the logging framework when a log record is created.
        # We append the formatted log message to the deque.
        line = self.format(record)
        self.dq.append(truncate(line, self.max_line_length))


class ProcWrapper:
    """
    A class that wraps the execution of a process and provides functionality for managing the process,
    updating its status, and handling errors.

    Attributes:
      WRAPPER_FAMILY (str): The family of the proc wrapper.
      VERSION (str): The version of the proc wrapper.
      STATUS_RUNNING (str): The status indicating that the process is running.
      STATUS_SUCCEEDED (str): The status indicating that the process has succeeded.
      STATUS_FAILED (str): The status indicating that the process has failed.
      STATUS_TERMINATED_AFTER_TIME_OUT (str): The status indicating that the process was terminated after a timeout.
      STATUS_MARKED_DONE (str): The status indicating that the process has been marked as done.
      STATUS_EXITED_AFTER_MARKED_DONE (str): The status indicating that the process has exited after being marked as done.
      STATUS_ABORTED (str): The status indicating that the process has been aborted.
    """

    WRAPPER_FAMILY = "CloudReactor python proc_wrapper"
    VERSION = getattr(sys.modules[__package__], "__version__")

    STATUS_RUNNING = "RUNNING"
    STATUS_SUCCEEDED = "SUCCEEDED"
    STATUS_FAILED = "FAILED"
    STATUS_TERMINATED_AFTER_TIME_OUT = "TERMINATED_AFTER_TIME_OUT"
    STATUS_MARKED_DONE = "MARKED_DONE"
    STATUS_EXITED_AFTER_MARKED_DONE = "EXITED_AFTER_MARKED_DONE"
    STATUS_ABORTED = "ABORTED"

    _ALLOWED_FINAL_STATUSES = frozenset(
        [
            STATUS_SUCCEEDED,
            STATUS_FAILED,
            STATUS_TERMINATED_AFTER_TIME_OUT,
            STATUS_EXITED_AFTER_MARKED_DONE,
            STATUS_ABORTED,
        ]
    )

    _EXIT_CODE_SUCCESS = 0
    _EXIT_CODE_GENERIC_ERROR = 1
    _EXIT_CODE_CONFIGURATION_ERROR = 78

    _RESPONSE_CODE_TO_EXIT_CODE = {
        409: 75,  # temp failure
        403: 77,  # permission denied
    }

    _RETRYABLE_HTTP_STATUS_CODES = frozenset(
        [
            HTTPStatus.TOO_MANY_REQUESTS.value,
            HTTPStatus.SERVICE_UNAVAILABLE.value,
            HTTPStatus.BAD_GATEWAY.value,
            HTTPStatus.GATEWAY_TIMEOUT.value,
        ]
    )

    _MIN_HTTP_REQUEST_DELAY_SECONDS = 5
    _MAX_HTTP_REQUEST_DELAY_SECONDS = 600

    _STATUS_BUFFER_SIZE = 4096

    _STATUS_UPDATE_KEY_LAST_APP_HEARTBEAT_AT = "last_app_heartbeat_at"

    _COPIED_RUNTIME_METADATA_PROPERTY_NAMES = [
        "execution_method_type",
        "infrastructure_type",
        "allocated_cpu_units",
        "allocated_memory_mb",
        "ip_v4_addresses",
    ]

    _SEND_RESULT_SUCCESS = 1
    _SEND_RESULT_NON_FATAL_FAILURE = 2
    _SEND_RESULT_SKIPPED = 3

    _LOG_READER_TIMEOUT_SECONDS = 30

    def __init__(
        self,
        params: Optional[ProcWrapperParams] = None,
        runtime_metadata_fetcher: Optional[RuntimeMetadataFetcher] = None,
        config_resolver: Optional[ConfigResolver] = None,
        env_override: Optional[Mapping[str, str]] = None,
        override_params_from_env: bool = True,
        runtime_context: Optional[Any] = None,
        input_value: Optional[Any] = UNSET_VALUE,
        override_params_from_input: bool = True,
        override_params_from_config: Optional[bool] = None,
    ) -> None:
        _logger.info("Creating ProcWrapper instance ...")

        self.input_value = input_value

        self.runtime_context = runtime_context
        self.override_params_from_env = override_params_from_env
        self.override_params_from_config = override_params_from_config

        if env_override:
            self.env = dict(env_override)
        else:
            self.env = os.environ.copy()

        self.is_windows = platform.system() == "Windows"

        self.param_errors: Optional[ProcWrapperParamValidationErrors] = None
        self.offline_mode: bool = False
        self.started_at: Optional[datetime] = None
        self.skip_start_notification: bool = False
        self.task_uuid: Optional[str] = None
        self.task_name: Optional[str] = None
        self.task_execution_uuid: Optional[str] = None
        self.was_conflict: bool = False
        self.called_exit: bool = False
        self.reported_final_status: bool = False
        self.attempt_count: int = 0
        self.failed_count: int = 0
        self.timeout_count: int = 0
        self.timed_out: bool = False
        self.hostname: Optional[str] = None
        self.refresh_runtime_metadata_interval: Optional[int] = None
        self.process_env: Optional[dict[str, str]] = None
        self.process: Optional[Popen[bytes]] = None
        self.is_execution_status_from_runtime_metadata: bool = False
        self.api_server_retries_exhausted: bool = False
        self.last_api_request_failed_at: Optional[float] = None
        self.last_api_request_data: Optional[dict[str, Any]] = None
        self.config_last_reloaded_at: Optional[float] = None
        self.wrote_input_file: bool = False

        self.status_dict: dict[str, Any] = {}
        self._status_socket: Optional[socket.socket] = None
        self._status_buffer: Optional[bytearray] = None
        self._status_message_so_far: Optional[bytearray] = None
        self.last_update_sent_at: Optional[float] = None
        self.last_app_heartbeat_at: Optional[datetime] = None
        self.runtime_metadata: Optional[RuntimeMetadata] = None
        self.runtime_metadata_last_refreshed_at: Optional[float] = None
        self.runtime_metadata_last_sent_at: Optional[float] = None
        self.stdout_log_line_deque: Optional[deque[str]] = None
        self.stderr_log_line_deque: Optional[deque[str]] = None
        self.stdout_reader_thread: Optional[threading.Thread] = None
        self.stderr_reader_thread: Optional[threading.Thread] = None

        self.resolved_env: dict[str, str] = self.env
        self.failed_env_names: list[str] = []
        self.resolved_config: dict[str, Any] = {}
        self.failed_config_props: list[str] = []

        self.rollbar_retries_exhausted = False
        self.exit_handler_installed = False
        self.in_pytest = string_to_bool(os.environ.get("IN_PYTEST")) or False

        if params:
            self.params = params
        else:
            self.params = ProcWrapperParams(override_from_env=True, env=self.env)

        if input_value == UNSET_VALUE:
            input_value = self.params.input_value

        self.runtime_metadata_fetcher: RuntimeMetadataFetcher = (
            runtime_metadata_fetcher or DefaultRuntimeMetadataFetcher(params=params)
        )

        self._fetch_runtime_metadata_if_necessary(force=True)

        if self.runtime_metadata:
            self.is_execution_status_from_runtime_metadata = (
                self.runtime_metadata.is_execution_status_source
            )

            self.refresh_runtime_metadata_interval = coalesce(
                self.params.runtime_metadata_refresh_interval,
                self.runtime_metadata.default_refresh_interval,
            )

        if config_resolver:
            self.config_resolver = config_resolver
        else:
            self.config_resolver = ConfigResolver(
                params=self.params,
                runtime_metadata=self.runtime_metadata,
                env_override=self.env,
            )

        (
            self.resolved_env,
            self.failed_env_names,
            self.resolved_config,
            self.failed_config_props,
        ) = self.config_resolver.fetch_and_resolve_env_and_config(
            want_env=True, want_config=self.params.embedded_mode
        )

        self.override_params_from_config = coalesce(
            override_params_from_config, self.params.embedded_mode
        )

        self._override_params_from_env_and_config(mutable_only=False)

        if "PROC_WRAPPER_INPUT_VALUE" in self.resolved_env:
            self.input_value = parse_data_string(
                data_string=self.resolved_env["PROC_WRAPPER_INPUT_VALUE"],
                format=self.params.input_value_format,
            )

        if override_params_from_input:
            config_override = self.params.override_params_from_input(
                input=self.input_value
            )

            if config_override:
                self.resolved_config.update(config_override)

        # Now we have enough info to try to send errors if problems happen below.

        if (
            (not self.exit_handler_installed)
            and (not self.params.embedded_mode)
            and (not self.params.exit_after_writing_variables)
            and (not self.in_pytest)
        ):
            _logger.debug("Installing exit handler and signal handler ...")

            atexit.register(_exit_handler, self)

            # The function registered with atexit.register() isn't called when python receives
            # most signals, include SIGTERM. So register a signal handler which will cause the
            # program to exit with an error, triggering the exit handler.
            signal.signal(signal.SIGTERM, _signal_handler)

            self.exit_handler_installed = True

            _logger.debug("Successfully installed exit handler and signal handler")
        else:
            _logger.debug("Skipping installation of exit handler and signal handler")

    def get_embedded_logging_handler(self) -> logging.Handler:
        """
        Create a logging handler for the embedded mode.
        """
        if not self.params.embedded_mode:
            raise RuntimeError("Not in embedded mode")

        dq: Optional[deque[str]] = (
            self.stdout_log_line_deque
            if self.params.merge_stdout_and_stderr_logs
            else self.stderr_log_line_deque
        )

        if dq is None:
            buffer_size = self.params.log_buffer_size()

            if buffer_size <= 0:
                return logging.NullHandler()

            dq = deque(maxlen=buffer_size)

            if self.params.merge_stdout_and_stderr_logs:
                self.stdout_log_line_deque = dq
            else:
                self.stderr_log_line_deque = dq

        return DequeLoggingHandler(
            dq=dq, max_line_length=self.params.max_log_line_length
        )

    def debug_output(self, msg: str) -> None:
        """
        Add a message to the debug log.
        """
        if self.stdout_log_line_deque is None:
            buffer_size = self.params.log_buffer_size()
            if buffer_size <= 0:
                return

            self.stdout_log_line_deque = deque(maxlen=buffer_size)

        self.stdout_log_line_deque.append(
            truncate(msg, self.params.max_log_line_length)
        )

    def log_configuration(self, initial: bool = False) -> None:
        if initial:
            _logger.info(f"Wrapper version = {ProcWrapper.VERSION}")

        self.params.log_configuration()

        if self.param_errors:
            self.param_errors.log()
        else:
            _logger.warning("No validated parameters?!")

    def write_variable_files(self) -> None:
        """
        Write the environment and configuration files to disk.
        """

        if self.params.env_output_filename and self.params.env_output_format:
            write_data_to_file(
                filename=self.params.env_output_filename,
                data=self.resolved_env,
                format=self.params.env_output_format,
            )

        if self.params.config_output_filename and self.params.config_output_format:
            write_data_to_file(
                filename=self.params.config_output_filename,
                data=self.resolved_config,
                format=self.params.config_output_format,
            )

    def remove_variable_files(self) -> None:
        if self.params.exit_after_writing_variables:
            _logger.info("Skipping removal of resolved variable files")
            return

        if self.params.env_output_filename:
            _logger.info(
                f"Removing env output file '{self.params.env_output_filename}'"
            )
            try:
                os.remove(self.params.env_output_filename)
            except OSError:
                _logger.warning(
                    f"Failed to remove env output file '{self.params.env_output_filename}'"
                )

        if self.params.config_output_filename:
            _logger.info(
                f"Removing config output file '{self.params.config_output_filename}'"
            )
            try:
                os.remove(self.params.config_output_filename)
            except OSError:
                _logger.warning(
                    f"Failed to remove config output file '{self.params.config_output_filename}'"
                )

    def remove_input_and_result_files(self) -> None:
        cleanup_input_file = coalesce(
            self.params.cleanup_input_file, self.wrote_input_file
        )

        if (
            cleanup_input_file
            and self.params.input_filename
            and os.path.exists(self.params.input_filename)
        ):
            _logger.info(f"Removing input file '{self.params.input_filename}'")
            try:
                os.remove(self.params.input_filename)
            except OSError:
                _logger.warning(
                    f"Failed to remove input file '{self.params.input_filename}'"
                )
        else:
            _logger.info("Skipping removal of input file")

        if (
            self.params.cleanup_result_file
            and self.params.result_filename
            and os.path.exists(self.params.result_filename)
        ):
            _logger.info(f"Removing result file '{self.params.result_filename}'")
            try:
                os.remove(self.params.result_filename)
            except OSError:
                _logger.warning(
                    f"Failed to remove result file '{self.params.result_filename}'"
                )
        else:
            _logger.info("Skipping removal of result file")

    def _resolve_input_value(self) -> Optional[Any]:
        if self.input_value == UNSET_VALUE:
            if self.params.embedded_mode or self.params.send_input_value:
                parsed_input_value: Optional[Any] = None
                if self.params.input_env_var_name:
                    raw_input_value = self.resolved_env.get(
                        self.params.input_env_var_name
                    )

                    if raw_input_value is not None:
                        parsed_input_value = parse_data_string(
                            data_string=raw_input_value,
                            format=self.params.input_value_format,
                        )
                elif self.params.input_filename:
                    parsed_input_value = parse_data_file(
                        filename=self.params.input_filename,
                        format=self.params.input_value_format,
                    )
                else:
                    raise ValueError(
                        "send_input_value is true, but no input value source specified"
                    )

                self.input_value = parsed_input_value
            else:
                _logger.info(
                    "wrapped mode with send_input_value=false, so not resolving input value"
                )
        else:
            if self.params.input_filename:
                write_data_to_file(
                    filename=self.params.input_filename,
                    data=self.input_value,
                    format=self.params.input_value_format,
                )
                self.wrote_input_file = True

        if self.params.log_input_value or (self.input_value == UNSET_VALUE):
            _logger.info(f"Input value = {stringify_value(self.input_value)}")

        return self.input_value

    def _read_result(self) -> Optional[Any]:
        result_value: Optional[Any] = UNSET_VALUE

        if self.params.result_filename:
            result_value = parse_data_file(
                filename=self.params.result_filename,
                format=self.params.result_value_format,
            )

        if self.params.log_result_value or (result_value == UNSET_VALUE):
            _logger.info(f"Result value = {stringify_value(result_value)}")

        return result_value

    def print_final_status(
        self,
        exit_code: Optional[int],
        first_attempt_started_at: Optional[float],
        latest_attempt_started_at: Optional[float],
    ):
        if latest_attempt_started_at is None:
            latest_attempt_started_at = first_attempt_started_at

        action = "failed due to wrapping error"

        if exit_code == self._EXIT_CODE_SUCCESS:
            action = "succeeded"
        elif exit_code is not None:
            action = f"failed with exit code {exit_code}"
        elif self.timed_out:
            action = "timed out"

        task_name = self.task_name or self.task_uuid or "[Unnamed]"
        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()
        max_attempts_str = (
            "infinity"
            if self.params.process_max_retries is None
            else str(self.params.process_max_retries + 1)
        )

        msg = (
            f"Task '{task_name}' {action} "
            + f"at {now.replace(microsecond=0).isoformat(sep=' ')} "
        )

        if latest_attempt_started_at is not None:
            latest_duration = now_ts - latest_attempt_started_at
            msg += f"in {round(latest_duration)} seconds "

        msg += "on attempt " + f"{self.attempt_count} / {max_attempts_str}"

        if first_attempt_started_at is not None:
            total_duration = now_ts - first_attempt_started_at
            msg += f", for a total duration of {round(total_duration)} second(s)"

        msg += "."

        _logger.info(msg)

    @property
    def max_execution_attempts(self) -> float:
        if self.params.process_max_retries is None:
            return math.inf

        return self.params.process_max_retries + 1

    def update_status(
        self,
        success_count: Optional[int] = None,
        error_count: Optional[int] = None,
        skipped_count: Optional[int] = None,
        expected_count: Optional[int] = None,
        last_status_message: Optional[str] = None,
        extra_status_props: Optional[dict[str, Any]] = None,
        last_app_heartbeat_at: Optional[datetime] = None,
    ) -> int:
        """
        Update the status of the Task. Send to the Task management
        server if the last status was sent more than status_update_interval
        seconds ago.

        Return a _STATUS_XXX constant indicating the result of the update.
        """
        return self._update_status(
            success_count=success_count,
            error_count=error_count,
            skipped_count=skipped_count,
            expected_count=expected_count,
            last_status_message=last_status_message,
            extra_status_props=extra_status_props,
            is_app_update=True,
            last_app_heartbeat_at=last_app_heartbeat_at,
        )

    def _fetch_runtime_metadata_if_necessary(
        self, force: bool = False
    ) -> Optional[RuntimeMetadata]:
        current_time = time.time()
        if (
            (not force)
            and self.runtime_metadata
            and self.runtime_metadata_last_refreshed_at
            and (
                (self.refresh_runtime_metadata_interval is None)
                or (self.refresh_runtime_metadata_interval < 0)
                or (
                    current_time - self.runtime_metadata_last_refreshed_at
                    <= self.refresh_runtime_metadata_interval
                )
            )
        ):
            return self.runtime_metadata

        runtime_metadata: Optional[RuntimeMetadata] = None

        try:
            runtime_metadata = self.runtime_metadata_fetcher.fetch(
                self.resolved_env, self.runtime_context
            )
        except Exception:
            _logger.exception("Failed to fetch runtime metadata")
            return None

        if runtime_metadata is None:
            return None

        self.runtime_metadata = runtime_metadata
        self.runtime_metadata_last_refreshed_at = current_time
        return runtime_metadata

    def _transfer_runtime_metadata(
        self,
        dest: dict[str, Any],
        runtime_metadata: Optional[RuntimeMetadata],
        for_task: bool,
        override_props: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        em_details: Optional[dict[str, Any]] = None
        infra_settings: Optional[dict[str, Any]] = None
        rm_config: Optional[CommonConfiguration] = None

        em_details_prop_name = (
            "execution_method_capability_details"
            if for_task
            else "execution_method_details"
        )

        if runtime_metadata:
            if for_task:
                rm_config = runtime_metadata.task_configuration
                em_details = rm_config.execution_method_capability_details
            else:
                rm_config = runtime_metadata.task_execution_configuration
                em_details = (
                    runtime_metadata.task_execution_configuration.execution_method_details
                )

            infra_settings = rm_config.infrastructure_settings

        if override_props:
            em_details = best_effort_deep_merge(
                em_details, override_props.get(em_details_prop_name)
            )

            infra_settings = best_effort_deep_merge(
                infra_settings, override_props.get("infrastructure_settings")
            )

        if em_details:
            dest[em_details_prop_name] = em_details

        if infra_settings:
            dest["infrastructure_settings"] = infra_settings

        for p in self._COPIED_RUNTIME_METADATA_PROPERTY_NAMES:
            x = None
            if rm_config:
                x = getattr(rm_config, p)

            if override_props:
                x = coalesce(override_props.get(p), x)

            if x is not None:
                dest[p] = x

        return dest

    def _compute_hostname(self) -> Optional[str]:
        rm = self.runtime_metadata

        if rm and (rm.host_addresses or rm.host_names):
            self.hostname = ((rm.host_addresses or []) + (rm.host_names or []))[0]

        if self.hostname is None:
            try:
                self.hostname = socket.gethostname()
            except Exception:
                _logger.warning("Can't get hostname", exc_info=True)

        _logger.debug(f"Hostname = '{self.hostname}'")

        return self.hostname

    def _compute_status_update_host(self) -> str:
        if (
            self.runtime_metadata
            and self.runtime_metadata.monitor_host_addresses
            and self.runtime_metadata.host_addresses
            and (
                self.runtime_metadata.monitor_host_addresses[0]
                != self.runtime_metadata.host_addresses[0]
            )
        ):
            return self.runtime_metadata.monitor_host_addresses[0]
        else:
            return "127.0.0.1"

    def _create_or_update_task_execution(
        self,
        status: Optional[str] = None,
        failed_attempts: Optional[int] = None,
        timed_out_attempts: Optional[int] = None,
        exit_code: Optional[int] = None,
        pid: Optional[int] = None,
        finished_at: Optional[datetime] = None,
        extra_runtime_metadata: Optional[Mapping[str, Any]] = None,
        output_value: Optional[Any] = UNSET_VALUE,
    ) -> int:
        """
        Make a request to the Task Management server to create a Task Execution
        for this Task. Retry and wait between requests if so configured
        and the maximum concurrency has not already been reached.

        Return a _SEND_RESULT_XXX constant indicating the result of the request.
        """

        if self.offline_mode:
            return self._SEND_RESULT_SKIPPED

        status = status or ProcWrapper.STATUS_RUNNING
        stop_reason: Optional[str] = None

        if self.param_errors:
            if not (
                self.param_errors.can_start_task_execution()
                and self.param_errors.can_start_process()
            ):
                status = ProcWrapper.STATUS_ABORTED
                # TODO: use a reason that indicate misconfiguration
                stop_reason = "FAILED_TO_START"

        is_running = status == ProcWrapper.STATUS_RUNNING
        should_send = True

        if not self.task_execution_uuid:
            if is_running or (status == ProcWrapper.STATUS_SUCCEEDED):
                should_send = not self.skip_start_notification
            elif status == ProcWrapper.STATUS_TERMINATED_AFTER_TIME_OUT:
                should_send = (self.params.api_timeout_report_probability >= 1.0) or (
                    random.random() < self.params.api_timeout_report_probability
                )
            else:
                should_send = (self.params.api_failure_report_probability >= 1.0) or (
                    random.random() < self.params.api_failure_report_probability
                )

        if not should_send:
            _logger.info("Skipping Task Execution creation")
            return self._SEND_RESULT_SKIPPED

        need_hostname = self.params.send_hostname and (self.hostname is None)

        if self.params.send_runtime_metadata or need_hostname:
            self._fetch_runtime_metadata_if_necessary(force=not is_running)

        if need_hostname:
            self._compute_hostname()

        try:
            url = f"{self.params.api_base_url}/api/v1/task_executions/"
            http_method = "POST"
            headers = self._make_headers()

            common_body = {
                "is_service": self.params.service,
                "schedule": self.params.schedule or "",
                "heartbeat_interval_seconds": encode_int(
                    self.params.api_heartbeat_interval, empty_value=-1
                ),
                "api_managed_probability": self.params.api_managed_probability,
                "api_failure_report_probability": self.params.api_failure_report_probability,
                "api_timeout_report_probability": self.params.api_timeout_report_probability,
            }

            self.started_at = self.started_at or datetime.now(timezone.utc)

            body = {
                "embedded_mode": self.params.embedded_mode,
                "started_at": self.started_at.isoformat(),
                "status": status,
                "task_version_number": self.params.task_version_number,
                "task_version_text": self.params.task_version_text,
                "task_version_signature": self.params.task_version_signature,
                "process_timeout_seconds": self.params.process_timeout,
                "process_max_retries": encode_int(
                    self.params.process_max_retries, empty_value=-1
                ),
                "process_retry_delay_seconds": self.params.process_retry_delay,
                "task_max_concurrency": self.params.max_concurrency,
                "max_conflicting_age_seconds": self.params.max_conflicting_age,
                "prevent_offline_execution": self.params.prevent_offline_execution,
                "process_termination_grace_period_seconds": self.params.process_termination_grace_period,
                "wrapper_log_level": logging.getLevelName(_logger.getEffectiveLevel()),
                "wrapper_family": ProcWrapper.WRAPPER_FAMILY,
                "wrapper_version": ProcWrapper.VERSION,
                "api_error_timeout_seconds": encode_int(
                    self.params.api_error_timeout, empty_value=-1
                ),
                "api_retry_delay_seconds": encode_int(self.params.api_retry_delay),
                "api_resume_delay_seconds": encode_int(self.params.api_resume_delay),
                "api_task_execution_creation_error_timeout_seconds": encode_int(
                    self.params.api_task_execution_creation_error_timeout,
                    empty_value=-1,
                ),
                "api_task_execution_creation_conflict_timeout_seconds": encode_int(
                    self.params.api_task_execution_creation_conflict_timeout,
                    empty_value=-1,
                ),
                "api_task_execution_creation_conflict_retry_delay_seconds": encode_int(
                    self.params.api_task_execution_creation_conflict_retry_delay
                ),
                "api_final_update_timeout_seconds": encode_int(
                    self.params.api_final_update_timeout, empty_value=-1
                ),
                "api_request_timeout_seconds": encode_int(
                    self.params.api_request_timeout, empty_value=-1
                ),
                "status_update_interval_seconds": encode_int(
                    self.params.status_update_interval
                ),
                "status_update_port": encode_int(self.params.status_update_socket_port),
                "status_update_message_max_bytes": encode_int(
                    self.params.status_update_message_max_bytes
                ),
                "num_log_lines_sent_on_failure": self.params.num_log_lines_sent_on_failure,
                "num_log_lines_sent_on_timeout": self.params.num_log_lines_sent_on_timeout,
                "num_log_lines_sent_on_success": self.params.num_log_lines_sent_on_success,
                "max_log_line_length": self.params.max_log_line_length,
                "merge_stdout_and_stderr_logs": self.params.merge_stdout_and_stderr_logs,
                "ignore_stdout": self.params.ignore_stdout,
                "ignore_stderr": self.params.ignore_stderr,
            }

            if self.params.send_input_value and (self.input_value != UNSET_VALUE):
                body["input_value"] = self.input_value

            # If we are creating or updating a Task Execution after skipping the initial
            # notification, include the post-execution properties
            if self.skip_start_notification:
                # Do not include runtime metadata since it will be set later, unconditionally
                body.update(
                    self._make_update_body(
                        status=status,
                        failed_attempts=failed_attempts,
                        timed_out_attempts=timed_out_attempts,
                        exit_code=exit_code,
                        pid=pid,
                        finished_at=finished_at,
                        output_value=output_value,
                        include_runtime_metadata=False,
                        extra_runtime_metadata=extra_runtime_metadata,
                    )
                )

            if self.params.build_task_execution_uuid:
                body["build"] = {
                    "task_execution": {"uuid": self.params.build_task_execution_uuid}
                }

            if self.params.deployment_task_execution_uuid:
                body["deploy"] = {
                    "task_execution": {
                        "uuid": self.params.deployment_task_execution_uuid
                    }
                }

            body.update(common_body)

            if stop_reason is not None:
                body["stop_reason"] = stop_reason

            rm = self.runtime_metadata if self.params.send_runtime_metadata else None

            if self.task_execution_uuid:
                # Manually started
                url += quote_plus(self.task_execution_uuid) + "/"
                http_method = "PATCH"
            else:
                if self.params.auto_create_task_props:
                    task_dict = self.params.auto_create_task_props.copy()
                    task_dict.update(common_body)
                else:
                    task_dict = common_body.copy()

                if self.task_uuid:
                    task_dict["uuid"] = self.task_uuid
                elif self.task_name:
                    task_dict["name"] = self.task_name
                else:
                    # This method should not have been called at all.
                    raise RuntimeError("Neither Task UUID or Task name were set.")

                task_dict.update(
                    {
                        "max_concurrency": encode_int(
                            self.params.max_concurrency, empty_value=-1
                        ),
                        "was_auto_created": self.params.auto_create_task,
                        "passive": self.params.task_is_passive,
                    }
                )

                run_env_dict = {}

                if self.params.auto_create_task_run_environment_name:
                    run_env_dict[
                        "name"
                    ] = self.params.auto_create_task_run_environment_name

                if self.params.auto_create_task_run_environment_uuid:
                    run_env_dict[
                        "uuid"
                    ] = self.params.auto_create_task_run_environment_uuid

                task_dict["run_environment"] = run_env_dict

                if self.params.execution_method_type:
                    task_dict[
                        "execution_method_type"
                    ] = self.params.execution_method_type

                self._transfer_runtime_metadata(
                    dest=task_dict,
                    runtime_metadata=rm,
                    override_props=self.params.auto_create_task_props,
                    for_task=True,
                )

                body["task"] = task_dict
                body["max_conflicting_age_seconds"] = self.params.max_conflicting_age

            if self.params.command:
                body["process_command"] = " ".join(self.params.command)

            if self.params.send_hostname and self.hostname:
                body["hostname"] = self.hostname

            if self.params.task_instance_metadata:
                body["other_instance_metadata"] = self.params.task_instance_metadata

            self._transfer_runtime_metadata(
                dest=body,
                runtime_metadata=rm,
                override_props={
                    "execution_method_type": self.params.execution_method_type,
                    "execution_method_details": self.params.execution_method_props,
                },
                for_task=False,
            )

            data = json.dumps(body).encode("utf-8")

            req = Request(url, data=data, headers=headers, method=http_method)

            is_final_update = status != self.STATUS_RUNNING

            f = self._send_api_request(
                req,
                is_task_execution_creation_request=True,
                is_final_update=is_final_update,
            )

            if f is None:
                _logger.warning("Task Execution creation request failed non-fatally")
                return self._SEND_RESULT_NON_FATAL_FAILURE

            response_body: str = ""
            with f:
                fd = f.read()

                if not fd:
                    raise RuntimeError(
                        "Unexpected None result of reading Task Execution creation response"
                    )

                response_body = fd.decode("utf-8")

            _logger.debug(f"Got creation response '{response_body}'")

            response_dict = json.loads(response_body)

            _logger.info("Task Execution creation request was successful.")

            if is_final_update:
                self.reported_final_status = True

            if rm:
                self.runtime_metadata_last_sent_at = time.time()

            if not self.task_execution_uuid:
                self.task_execution_uuid = response_dict.get("uuid")
                self._reconfigure_logger()

            self.task_uuid = self.task_uuid or response_dict["task"]["uuid"]
            self.task_name = self.task_name or response_dict["task"]["name"]

            # In case the input value couldn't be passed to this process,
            # get it from the Task Execution, writing it to the input file
            # if the input filename is set.
            response_input_value = response_dict.get("input_value")
            if (
                (self.input_value == UNSET_VALUE)
                and (response_input_value is not None)
                and (response_input_value != UNSET_VALUE)
            ):
                self.input_value = response_input_value
                self._resolve_input_value()

            return self._SEND_RESULT_SUCCESS
        except Exception as ex:
            _logger.exception(
                "_create_or_update_task_execution() failed with exception"
            )
            if self.params.prevent_offline_execution:
                raise ex

            _logger.info("Not preventing offline execution, so continuing")

            return self._SEND_RESULT_NON_FATAL_FAILURE

    def _update_status(
        self,
        failed_attempts: Optional[int] = None,
        timed_out_attempts: Optional[int] = None,
        exit_code: Optional[int] = None,
        pid: Optional[int] = None,
        success_count: Optional[int] = None,
        error_count: Optional[int] = None,
        skipped_count: Optional[int] = None,
        expected_count: Optional[int] = None,
        last_status_message: Optional[str] = None,
        extra_status_props: Optional[dict[str, Any]] = None,
        is_app_update: bool = False,
        last_app_heartbeat_at: Optional[datetime] = None,
    ) -> int:
        """
        Send the data about the Task to the Task Management service.

        Return a _STATUS_XXX constant indicating the result of the update.
        """

        _logger.debug(f"_update_status(), is_app_update = {is_app_update}")

        if is_app_update:
            self.last_app_heartbeat_at = last_app_heartbeat_at or datetime.now(
                timezone.utc
            )

        if self.offline_mode:
            return self._SEND_RESULT_SKIPPED

        if is_app_update:
            self.status_dict[
                self._STATUS_UPDATE_KEY_LAST_APP_HEARTBEAT_AT
            ] = self.last_app_heartbeat_at

        if success_count is not None:
            self.status_dict["success_count"] = int(success_count)

        if error_count is not None:
            self.status_dict["error_count"] = int(error_count)

        if skipped_count is not None:
            self.status_dict["skipped_count"] = int(skipped_count)

        if expected_count is not None:
            self.status_dict["expected_count"] = int(expected_count)

        if last_status_message is not None:
            self.status_dict["last_status_message"] = last_status_message

        if extra_status_props:
            self.status_dict.update(extra_status_props)

        if self.skip_start_notification:
            should_send = False
        # These are important updates that should be sent as long as we are notifying the API
        # server at all.
        elif not (
            (failed_attempts is None)
            and (timed_out_attempts is None)
            and (pid is None)
            and (exit_code is None)
        ):
            should_send = True
        else:
            should_send = self.is_status_update_due()

        if should_send:
            return self.send_update(
                failed_attempts=failed_attempts,
                timed_out_attempts=timed_out_attempts,
                exit_code=exit_code,
                pid=pid,
            )

        return self._SEND_RESULT_SKIPPED

    def send_completion(
        self,
        status: str,
        failed_attempts: Optional[int] = None,
        timed_out_attempts: Optional[int] = None,
        exit_code: Optional[int] = None,
        pid: Optional[int] = None,
        output_value: Optional[Any] = None,
    ) -> int:
        """
        Send the final result to the Task Management Server.
        Return a _STATUS_XXX constant indicating the result of the update.
        """

        if self.offline_mode:
            return self._SEND_RESULT_SKIPPED

        if status not in self._ALLOWED_FINAL_STATUSES:
            self._exit_or_raise(self._EXIT_CODE_GENERIC_ERROR)
            return self._SEND_RESULT_SKIPPED

        if (exit_code is None) and (not self.params.embedded_mode):
            if status == self.STATUS_SUCCEEDED:
                exit_code = self._EXIT_CODE_SUCCESS
            else:
                exit_code = self._EXIT_CODE_GENERIC_ERROR

        finished_at = datetime.now(timezone.utc)

        if self.skip_start_notification:
            # We did not create the Task Execution with the Task Management server when we
            # started, but we may need to create it now on failure or timeout.
            if (status == ProcWrapper.STATUS_SUCCEEDED) and (
                output_value == UNSET_VALUE
            ):
                _logger.debug("send_completion(): skipping Task Execution creation")
                return self._SEND_RESULT_SKIPPED
            else:
                _logger.debug("send_completion(): attempting Task Execution creation")

                return self._create_or_update_task_execution(
                    status=status,
                    failed_attempts=failed_attempts,
                    timed_out_attempts=timed_out_attempts,
                    exit_code=exit_code,
                    pid=pid,
                    finished_at=finished_at,
                    output_value=output_value,
                )

        else:
            return self.send_update(
                status=status,
                failed_attempts=failed_attempts,
                timed_out_attempts=timed_out_attempts,
                exit_code=exit_code,
                pid=pid,
                finished_at=finished_at,
                output_value=output_value,
            )

    def managed_call(self, fun, data: Optional[Any] = None) -> Optional[Any]:
        """
        Call the argument object, which must be callable, doing retries as necessary,
        and wrapping with calls to the Task Management server.
        """
        if not callable(fun):
            raise RuntimeError("managed_call() argument is not callable")

        if self.failed_env_names:
            _logger.critical(
                f"Failed to resolve one or more environment variables: {self.failed_env_names}"
            )
            self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
            return None

        if self.failed_config_props:
            _logger.critical(
                f"Failed to resolve one or more config props: {self.failed_config_props}"
            )
            self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
            return None

        self._reload_params()
        self.log_configuration(initial=True)

        self.write_variable_files()

        if self.params.exit_after_writing_variables:
            _logger.info("Exiting after writing variables")
            return None

        if ((data is None) or (data == UNSET_VALUE)) and (
            self.input_value == UNSET_VALUE
        ):
            data = self._resolve_input_value()

        try:
            self.started_at = datetime.now(timezone.utc)

            if not self._setup_task_execution():
                self._exit_or_raise(self._EXIT_CODE_GENERIC_ERROR)
                return None

            rv: Optional[Any] = None
            success = False
            saved_ex: Exception = RuntimeError()
            while self.attempt_count < self.max_execution_attempts:
                self.attempt_count += 1

                _logger.info(
                    f"Calling managed function (attempt {self.attempt_count}/{self.max_execution_attempts}) ..."
                )

                try:
                    rv = fun(self, data, self.resolved_config)

                    _logger.info(f"Managed function succeeded, return value = {rv}")

                    if (rv is None) or (rv == UNSET_VALUE):
                        _logger.info("Managed function returned None or UNSET_VALUE")

                        rv2 = self._read_result()
                        if rv2 != UNSET_VALUE:
                            _logger.info("Read result from result file")
                            rv = rv2

                    success = True
                except Exception as ex:
                    saved_ex = ex
                    self.failed_count += 1
                    _logger.exception("Managed function failed")

                    if self.attempt_count < self.max_execution_attempts:
                        self._update_status(failed_attempts=self.failed_count)

                        # TODO: check timeout

                        if self.params.process_retry_delay:
                            _logger.debug("Sleeping after managed function failed ...")
                            time.sleep(self.params.process_retry_delay)
                            _logger.debug(
                                "Done sleeping after managed function failed."
                            )

                        if self.params.config_ttl is not None:
                            self._reload_params()
                            self.log_configuration()
                            self.write_variable_files()

                if success:
                    try:
                        self.send_completion(
                            status=ProcWrapper.STATUS_SUCCEEDED, output_value=rv
                        )
                    except Exception:
                        _logger.warning(
                            "Failed to send completion to the Task Management server",
                            exc_info=True,
                        )

                    return rv

            self.send_completion(
                status=ProcWrapper.STATUS_FAILED, failed_attempts=self.failed_count
            )

            raise saved_ex
        finally:
            self.remove_variable_files()
            self.remove_input_and_result_files()

    def _override_params_from_env_and_config(self, mutable_only: bool) -> None:
        if self.override_params_from_env:
            self.params.override_params_from_env(
                env=self.resolved_env, mutable_only=mutable_only
            )

        if self.override_params_from_config:
            config_override = self.params.override_params_from_config(
                config=self.resolved_config, mutable_only=mutable_only
            )

            if config_override:
                self.resolved_config.update(config_override)

    def _reload_params(self) -> None:
        (
            self.resolved_env,
            self.failed_env_names,
            self.resolved_config,
            self.failed_config_props,
        ) = self.config_resolver.fetch_and_resolve_env_and_config(
            want_env=self.override_params_from_env,
            want_config=self.override_params_from_config,
        )

        try:
            rm = self.runtime_metadata_fetcher.fetch(
                env=self.resolved_env, context=self.runtime_context
            )

            if rm:
                self.runtime_metadata = rm
        except Exception:
            _logger.exception(
                "Failed to fetch runtime metadata during _reload_params()"
            )

        self._override_params_from_env_and_config(mutable_only=True)

        self.param_errors = self.params.sanitize_and_validate(
            runtime_metadata=self.runtime_metadata
        )

        self.config_last_reloaded_at = time.time()

    def _setup_task_execution(self) -> bool:
        self.task_uuid = self.params.task_uuid
        self.task_name = self.params.task_name
        self.task_execution_uuid = self.params.task_execution_uuid
        self.offline_mode = self.params.offline_mode or (
            (self.param_errors is not None)
            and not self.param_errors.can_start_task_execution()
        )
        self.skip_start_notification = (
            self.offline_mode
            or (self.params.api_managed_probability <= 0.0)
            or (
                (self.params.api_managed_probability < 1.0)
                and (random.random() > self.params.api_managed_probability)
            )
        )

        self.attempt_count = 0
        self.timeout_count = 0

        if self.task_execution_uuid:
            self._reconfigure_logger()

        if self.offline_mode:
            _logger.info("Starting in offline mode ...")
        elif self.skip_start_notification:
            _logger.info("Skipping API server notification ...")
        else:
            self._create_or_update_task_execution()

            if self.param_errors and (not self.param_errors.can_start_process()):
                _logger.info(
                    f"Created Task Execution {self.task_execution_uuid} with ABORTED status, not starting process."
                )
                return False
            else:
                _logger.info(
                    f"Created Task Execution {self.task_execution_uuid}, starting now."
                )

        return True

    def run(self) -> int:
        """
        Run the wrapped process, first requesting to start, then executing
        the process command line, retying for errors and timeouts.
        The return value is the exit code, but won't be actually returned
        except in testing.
        """
        self._ensure_non_embedded_mode()

        self._reload_params()

        self.log_configuration(initial=True)

        self.write_variable_files()

        if self.params.exit_after_writing_variables:
            _logger.info("Exiting after writing variables")
            return 0

        if self.param_errors and not self.param_errors.can_start_process():
            _logger.error("Exiting due to process configuration error")
            return self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)

        command, shell = self.params.resolve_command_and_shell_flag()

        self._resolve_input_value()

        self.started_at = datetime.now(timezone.utc)

        should_run = self._setup_task_execution()

        if not should_run:
            return self._exit_or_raise(self._EXIT_CODE_GENERIC_ERROR)

        popen_stdout: Optional[Any] = None
        popen_stderr: Optional[Any] = None

        if self.offline_mode:
            _logger.info("Not opening status socket or saving logs.")
        else:
            num_lines = self.params.log_buffer_size()
            if num_lines > 0:
                if not self.params.ignore_stdout:
                    self.stdout_log_line_deque = deque(maxlen=num_lines)
                    popen_stdout = subprocess.PIPE

                if not self.params.ignore_stderr:
                    if self.params.merge_stdout_and_stderr_logs:
                        popen_stderr = subprocess.STDOUT
                    else:
                        self.stderr_log_line_deque = deque(maxlen=num_lines)
                        popen_stderr = subprocess.PIPE

            if self.params.enable_status_update_listener:
                self._open_status_socket()

        first_attempt_started_at: Optional[float] = None
        latest_attempt_started_at: Optional[float] = None
        exit_code: Optional[int] = None

        while self.attempt_count < self.max_execution_attempts:
            self.attempt_count += 1
            self.timed_out = False

            current_time = time.time()

            process_finish_deadline = math.inf
            if self.params.process_timeout:
                process_finish_deadline = current_time + self.params.process_timeout

            next_heartbeat_time = math.inf
            if self.params.api_heartbeat_interval:
                next_heartbeat_time = current_time + self.params.api_heartbeat_interval

            next_process_check_time = math.inf
            if self.params.process_check_interval:
                next_process_check_time = (
                    current_time + self.params.process_check_interval
                )

            latest_attempt_started_at = current_time
            if first_attempt_started_at is None:
                first_attempt_started_at = latest_attempt_started_at

            monitor_process_exit_code: Optional[int] = None

            if command and (self.process is None):
                _logger.info(
                    f"Running process (attempt {self.attempt_count}/{self.max_execution_attempts}) ..."
                )
                try:
                    self._start_command(
                        command=command,
                        shell=shell,
                        popen_stdout=popen_stdout,
                        popen_stderr=popen_stderr,
                    )
                except Exception:
                    _logger.exception(f"Failed to start command '{command}'")
                    monitor_process_exit_code = self._EXIT_CODE_CONFIGURATION_ERROR

            self._start_log_capture()

            done_polling = False
            while not done_polling:
                self._read_from_status_socket()

                current_time = time.time()

                if command and self.process and (monitor_process_exit_code is None):
                    monitor_process_exit_code = self.process.poll()

                runtime_metadata = self._fetch_runtime_metadata_if_necessary()

                if self.is_execution_status_from_runtime_metadata:
                    if runtime_metadata:
                        exit_code = runtime_metadata.exit_code
                else:
                    exit_code = monitor_process_exit_code

                # None means the monitored process is still running
                if exit_code is None:
                    if current_time >= process_finish_deadline:
                        done_polling = True
                        self.timed_out = True
                        self.timeout_count += 1

                        if self.attempt_count < self.max_execution_attempts:
                            self._update_status(
                                timed_out_attempts=self.timeout_count, exit_code=None
                            )

                        if self.process:
                            _logger.warning(
                                f"Process timed out after {self.params.process_timeout} seconds, sending SIGTERM ..."
                            )
                            self._terminate_or_kill_process()
                    else:
                        if (self.params.process_check_interval is not None) and (
                            current_time >= next_process_check_time
                        ):
                            next_process_check_time = (
                                current_time + self.params.process_check_interval
                            )

                        next_runtime_metadata_refresh_time = math.inf
                        if self.refresh_runtime_metadata_interval and (
                            self.refresh_runtime_metadata_interval > 0
                        ):
                            next_runtime_metadata_refresh_time = (
                                self.runtime_metadata_last_refreshed_at or current_time
                            ) + self.refresh_runtime_metadata_interval

                        if self.params.api_heartbeat_interval:
                            if self.last_update_sent_at is not None:
                                next_heartbeat_time = max(
                                    next_heartbeat_time,
                                    self.last_update_sent_at
                                    + self.params.api_heartbeat_interval,
                                )

                            if current_time >= next_heartbeat_time:
                                self.send_update()
                                current_time = time.time()
                                next_heartbeat_time = (
                                    current_time + self.params.api_heartbeat_interval
                                )

                        sleep_until = min(
                            process_finish_deadline or math.inf,
                            next_heartbeat_time or math.inf,
                            next_process_check_time,
                            next_runtime_metadata_refresh_time,
                        )

                        sleep_duration = sleep_until - current_time

                        if sleep_duration > 0:
                            _logger.debug(
                                f"Waiting {round(sleep_duration)} seconds while process is running ..."
                            )

                            if self.process:
                                try:
                                    self.process.wait(sleep_duration)
                                except TimeoutExpired:
                                    _logger.debug(
                                        "Done waiting while process is running."
                                    )
                            else:
                                time.sleep(sleep_duration)

                else:
                    _logger.info(f"Process exited with exit code {exit_code}")

                    done_polling = True

                    self._end_log_capture()

                    if exit_code == self._EXIT_CODE_SUCCESS:
                        self.print_final_status(
                            exit_code=0,
                            first_attempt_started_at=first_attempt_started_at,
                            latest_attempt_started_at=latest_attempt_started_at,
                        )

                        return self._exit_or_raise(exit_code)

                    self.failed_count += 1

                    if self.is_execution_status_from_runtime_metadata or (
                        self.attempt_count >= self.max_execution_attempts
                    ):
                        self.print_final_status(
                            exit_code=exit_code,
                            first_attempt_started_at=first_attempt_started_at,
                            latest_attempt_started_at=latest_attempt_started_at,
                        )
                        return self._exit_or_raise(exit_code)

                    self._update_status(
                        failed_attempts=self.failed_count, exit_code=exit_code
                    )

                if monitor_process_exit_code is not None:
                    _logger.info(
                        f"Monitor process exit code = {monitor_process_exit_code}"
                    )

                    if command and self.params.process_retry_delay:
                        _logger.debug(
                            f"Sleeping {self.params.process_retry_delay} seconds after monitor process exited ..."
                        )
                        time.sleep(self.params.process_retry_delay)
                        _logger.debug("Done sleeping after monitor process exited.")

                if (self.params.config_ttl is not None) and (
                    (
                        (self.config_last_reloaded_at is None)
                        or (
                            current_time
                            >= self.config_last_reloaded_at + self.params.config_ttl
                        )
                    )
                    or (
                        (not self.is_execution_status_from_runtime_metadata)
                        and monitor_process_exit_code
                    )
                ):
                    self._reload_params()
                    self.log_configuration()
                    self.write_variable_files()
                    self.process_env = None
                    command, shell = self.params.resolve_command_and_shell_flag()

                if monitor_process_exit_code is not None:
                    self._start_command(
                        command=command,
                        shell=shell,
                        popen_stdout=popen_stdout,
                        popen_stderr=popen_stderr,
                    )

        if self.attempt_count >= self.max_execution_attempts:
            self.print_final_status(
                exit_code=exit_code,
                first_attempt_started_at=first_attempt_started_at,
                latest_attempt_started_at=latest_attempt_started_at,
            )

            return self._exit_or_raise(self._EXIT_CODE_GENERIC_ERROR)
        else:
            self.send_update(
                failed_attempts=self.attempt_count,
                timed_out_attempts=self.timeout_count,
            )

        # Exit handler will send update to API server
        return 0

    def handle_exit(self) -> None:
        _logger.debug(f"Exit handler, Task Execution UUID = {self.task_execution_uuid}")

        if self.called_exit:
            _logger.debug("Called exit already, returning early")
            return

        self.called_exit = True

        self.remove_variable_files()

        if self.was_conflict:
            _logger.debug(
                "Task execution update conflict detected, not notifying because server-side should have notified already."
            )
            return

        error_notification_required = (not self.reported_final_status) and not (
            self.offline_mode or self.skip_start_notification
        )

        _logger.debug(
            f"{error_notification_required=}, {self.skip_start_notification=}, {self.reported_final_status=}"
        )

        runtime_metadata = self._fetch_runtime_metadata_if_necessary(
            force=self.is_execution_status_from_runtime_metadata
            or self.params.send_runtime_metadata
        )

        exit_code: Optional[int] = None
        if self.is_execution_status_from_runtime_metadata:
            if runtime_metadata:
                exit_code = runtime_metadata.exit_code
        elif self.process:
            try:
                exit_code = self._terminate_or_kill_process()
            except Exception:
                _logger.exception("Failed to get exit code or terminate process")

        status = self.STATUS_FAILED
        output_value: Optional[Any] = UNSET_VALUE

        if exit_code is None:
            if caught_sigterm:
                status = self.STATUS_ABORTED
            elif self.timed_out:
                status = self.STATUS_TERMINATED_AFTER_TIME_OUT
        elif exit_code == 0:
            status = self.STATUS_SUCCEEDED

            try:
                output_value = self._read_result()
            except Exception:
                _logger.exception("Failed to read result")
                exit_code = self._EXIT_CODE_GENERIC_ERROR
                status = self.STATUS_FAILED

        self.remove_input_and_result_files()

        try:
            if (
                not (
                    self.reported_final_status
                    or self.offline_mode
                    or self.api_server_retries_exhausted
                )
            ) and (
                self.task_execution_uuid
                or (self.skip_start_notification and (status != self.STATUS_SUCCEEDED))
            ):
                _logger.debug("Sending completion to API server after process exit ...")
                send_result = self.send_completion(
                    status=status,
                    failed_attempts=self.failed_count,
                    timed_out_attempts=self.timeout_count,
                    exit_code=exit_code,
                    output_value=output_value,
                )

                error_notification_required = (
                    send_result != self._SEND_RESULT_SUCCESS
                ) and (send_result != self._SEND_RESULT_SKIPPED)

            else:
                _logger.debug(
                    "Skipping send of completion to API server after process exit ..."
                )
        except Exception:
            _logger.exception("Exception in final update")
        finally:
            self._close_status_socket()

        if error_notification_required:
            error_message = "API Server not configured"

            if self.was_conflict:
                error_message = "Failed to get permission to start Task due to conflict"
            elif self.api_server_retries_exhausted:
                error_message = "API Server not responding properly"
            elif not self.task_execution_uuid:
                error_message = "Exited before API Server notified of start"

            self._report_error(error_message, self.last_api_request_data)

    def _terminate_or_kill_process(self) -> Optional[int]:
        self._ensure_non_embedded_mode()

        if (not self.process) or (not self.process.pid):
            _logger.info("No process found, not terminating")
            return None

        pid = self.process.pid

        exit_code = self.process.poll()
        if exit_code is not None:
            _logger.info(
                f"Process {pid} already exited with code {exit_code}, not terminating"
            )
            return exit_code

        _logger.info("Trying to gracefully terminate the process ...")

        sent_termination_signal = False
        if self.params.process_group_termination:
            try:
                if hasattr(os, "killpg"):
                    _logger.info("Killing process group ...")
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                    sent_termination_signal = True
                    _logger.info("Killed process group successfully")
                elif self.is_windows:
                    _logger.info("Sending Ctrl-Break in Windows ...")
                    self.process.send_signal(getattr(signal, "CTRL_BREAK_EVENT"))
                    sent_termination_signal = True
                    _logger.info("Sent Ctrl-Break successfully")
            except Exception:
                _logger.exception(f"Could not terminate process group with {pid=}")

        if not sent_termination_signal:
            _logger.info("Sending SIGTERM ...")
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                _logger.exception(f"Could not send SIGTERM to process with {pid=}")

        try:
            self.process.communicate(
                timeout=self.params.process_termination_grace_period
            )

            _logger.info("Process terminated successfully after SIGTERM.")
        except TimeoutExpired:
            _logger.info("Timeout after SIGTERM expired, killing with SIGKILL ...")
            try:
                if self.params.process_group_termination and hasattr(os, "killpg"):
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                else:
                    os.kill(pid, signal.SIGKILL)

                self.process.communicate()
            except Exception:
                _logger.exception(f"Could not kill process with pid {pid}")

        self.process = None

        self._end_log_capture()

        return None

    def _ensure_non_embedded_mode(self):
        if self.params.embedded_mode:
            raise RuntimeError(
                "Called method is only for wrapped process (non-embedded) mode"
            )

    def _start_command(
        self,
        command: Union[str, list[str]],
        shell: bool,
        popen_stdout: Optional[Any],
        popen_stderr: Optional[Any],
    ):
        if self.process_env is None:
            self.process_env = self.make_process_env()
            if self.process_env and self.params.log_secrets:
                _logger.debug(f"process_env={self.process_env}")

        start_new_session = False
        creationflags = 0
        if self.params.process_group_termination:
            if self.is_windows:
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP")
            elif os.name == "posix":
                start_new_session = True

        bufsize = -1
        text = None

        if popen_stdout or popen_stderr:
            bufsize = 1
            text = True

        self.process = Popen(
            command,
            bufsize=bufsize,
            shell=shell,
            stdout=popen_stdout,
            stderr=popen_stderr,
            start_new_session=start_new_session,
            env=self.process_env,
            cwd=self.params.work_dir,
            text=text,
            creationflags=creationflags,
        )

        pid = self.process.pid
        _logger.info(f"{pid=}")

        if (
            pid
            and self.params.send_pid
            and (not self.is_execution_status_from_runtime_metadata)
        ):
            self._update_status(pid=pid)

    def _open_status_socket(self) -> Optional[socket.socket]:
        self._status_buffer = bytearray(self._STATUS_BUFFER_SIZE)
        self._status_message_so_far = bytearray()

        try:
            _logger.info("Opening status update socket ...")
            self._status_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            status_update_host = self._compute_status_update_host()
            self._status_socket.bind(
                (status_update_host, self.params.status_update_socket_port)
            )
            self._status_socket.setblocking(False)
            _logger.info("Successfully created status update socket")
            return self._status_socket
        except Exception:
            _logger.exception("Can't create status update socket")
            self._close_status_socket()
            return None

    def _read_from_status_socket(self):
        if self._status_socket is None:
            return

        nbytes = 1
        while nbytes > 0:
            try:
                nbytes = self._status_socket.recv_into(self._status_buffer)
            except OSError:
                # Happens when there is no data to be read, since the socket is non-blocking
                return

            start_index = 0
            end_index = 0

            while (end_index >= 0) and (start_index < nbytes):
                end_index = self._status_buffer.find(b"\n", start_index, nbytes)

                end_index_for_copy = end_index
                if end_index < 0:
                    end_index_for_copy = nbytes

                bytes_to_copy = end_index_for_copy - start_index
                if (
                    len(self._status_buffer) + bytes_to_copy
                    > self.params.status_update_message_max_bytes
                ):
                    _logger.warning(
                        f"Discarding status message which exceeded maximum size: {self._status_buffer}"
                    )
                    self._status_buffer.clear()
                else:
                    self._status_message_so_far.extend(
                        self._status_buffer[start_index:end_index_for_copy]
                    )
                    if end_index >= 0:
                        self._handle_status_message_complete()

                if end_index >= 0:
                    start_index = end_index + 1

    def _close_status_socket(self):
        if self._status_socket:
            try:
                self._status_socket.close()
            except Exception:
                _logger.warning("Can't close socket")

            self._status_socket = None

    def _handle_status_message_complete(self):
        try:
            message = self._status_message_so_far.decode("utf-8")

            _logger.debug(f"Got status message '{message}'")

            try:
                self.status_dict.update(json.loads(message))

                if self.is_status_update_due():
                    self.send_update()
            except json.JSONDecodeError:
                _logger.debug(
                    "Error decoding JSON, this can happen due to missing or out of order messages"
                )
        except UnicodeDecodeError:
            _logger.debug(
                "Error decoding message as UTF-8, this can happen due to missing or out of order messages"
            )
        finally:
            self._status_message_so_far.clear()

    def is_status_update_due(self) -> bool:
        return (self.last_update_sent_at is None) or (
            (self.params.status_update_interval is not None)
            and (
                time.time() - self.last_update_sent_at
                > self.params.status_update_interval
            )
        )

    def _make_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.params.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        return headers

    def make_process_env(self) -> dict[str, str]:
        process_env = self.resolved_env.copy()
        self.params.populate_env(process_env)

        if self.task_execution_uuid:
            process_env["PROC_WRAPPER_TASK_EXECUTION_UUID"] = self.task_execution_uuid

        if self.params.enable_status_update_listener:
            process_env[
                "PROC_WRAPPER_STATUS_UPDATE_HOST"
            ] = self._compute_status_update_host()

        if (
            self.runtime_metadata
            and self.runtime_metadata.monitor_process_env_additions
        ):
            process_env.update(self.runtime_metadata.monitor_process_env_additions)

        if self.params.input_env_var_name or (self.input_value != UNSET_VALUE):
            env_var_name = self.params.input_env_var_name or "PROC_WRAPPER_INPUT_VALUE"
            process_env[env_var_name] = stringify_value(self.input_value)

        return process_env

    def send_update(
        self,
        status: Optional[str] = None,
        failed_attempts: Optional[int] = None,
        timed_out_attempts: Optional[int] = None,
        exit_code: Optional[int] = None,
        pid: Optional[int] = None,
        finished_at: Optional[datetime] = None,
        output_value: Optional[Any] = None,
        extra_props: Optional[Mapping[str, Any]] = None,
    ) -> int:
        """
        Send an update to the Task Management server immediately. If status is omitted, it
        will default to STATUS_RUNNING.

        The caller may want to coalesce multiple updates together in order to
        save bandwidth / API usage.

        Return a _STATUS_XXX constant indicating the result of the update.

        This method is NOT meant to be called directly.
        """

        if self.offline_mode or self.skip_start_notification:
            return self._SEND_RESULT_SKIPPED

        if not self.task_execution_uuid:
            _logger.debug(
                "send_update() skipping update since no Task Execution UUID was available"
            )
            return self._SEND_RESULT_SKIPPED

        should_send_runtime_metadata = self._should_send_runtime_metadata()

        body = self._make_update_body(
            status=status,
            failed_attempts=failed_attempts,
            timed_out_attempts=timed_out_attempts,
            exit_code=exit_code,
            pid=pid,
            finished_at=finished_at,
            output_value=output_value,
            include_runtime_metadata=should_send_runtime_metadata,
            extra_runtime_metadata=extra_props,
        )

        url = (
            f"{self.params.api_base_url}/api/v1/task_executions/"
            + quote_plus(str(self.task_execution_uuid))
            + "/?content=false"
        )
        headers = self._make_headers()
        text_data = json.dumps(body)
        data = text_data.encode("utf-8")
        is_final_update = bool(status) and (status != self.STATUS_RUNNING)

        _logger.debug(f"Sending update '{text_data}' ...")

        req = Request(url, data=data, headers=headers, method="PATCH")
        f = self._send_api_request(req, is_final_update=is_final_update)
        if f is None:
            _logger.info("Update request failed non-fatally")
            return self._SEND_RESULT_NON_FATAL_FAILURE

        with f:
            _logger.info("Update sent successfully.")

            current_time = time.time()
            self.last_update_sent_at = current_time
            if should_send_runtime_metadata:
                self.runtime_metadata_last_sent_at = current_time

            self.status_dict = {}
            self.reported_final_status = is_final_update

        return self._SEND_RESULT_SUCCESS

    def _should_send_runtime_metadata(self) -> bool:
        return bool(
            self.params.send_runtime_metadata
            and self.runtime_metadata
            and self.runtime_metadata_last_refreshed_at
            and (
                (self.runtime_metadata_last_sent_at is None)
                or (
                    self.runtime_metadata_last_sent_at
                    < self.runtime_metadata_last_refreshed_at
                )
            )
        )

    def _make_update_body(
        self,
        status: Optional[str] = None,
        failed_attempts: Optional[int] = None,
        timed_out_attempts: Optional[int] = None,
        exit_code: Optional[int] = None,
        pid: Optional[int] = None,
        finished_at: Optional[datetime] = None,
        output_value: Optional[Any] = UNSET_VALUE,
        include_runtime_metadata: bool = False,
        extra_runtime_metadata: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        if status is None:
            status = self.STATUS_RUNNING

        body: dict[str, Any] = {"status": status}

        body.update(self.status_dict)

        unsent_last_app_heartbeat_at = self.status_dict.get(
            self._STATUS_UPDATE_KEY_LAST_APP_HEARTBEAT_AT
        )

        if unsent_last_app_heartbeat_at:
            body[
                self._STATUS_UPDATE_KEY_LAST_APP_HEARTBEAT_AT
            ] = unsent_last_app_heartbeat_at.isoformat()

        if failed_attempts:
            body["failed_attempts"] = failed_attempts

        if timed_out_attempts:
            body["timed_out_attempts"] = timed_out_attempts

        if exit_code is not None:
            body["exit_code"] = exit_code

        if pid is not None:
            body["pid"] = pid

        if finished_at:
            body["finished_at"] = finished_at.isoformat()

        if (status == self.STATUS_SUCCEEDED) and (output_value != UNSET_VALUE):
            body["output_value"] = output_value

        if extra_runtime_metadata:
            body["other_runtime_metadata"] = extra_runtime_metadata

        if include_runtime_metadata:
            self._transfer_runtime_metadata(
                dest=body,
                runtime_metadata=self.runtime_metadata,
                for_task=False,
            )

        num_log_lines = 0
        if status == self.STATUS_FAILED:
            num_log_lines = self.params.num_log_lines_sent_on_failure
        elif status == self.STATUS_SUCCEEDED:
            num_log_lines = self.params.num_log_lines_sent_on_success
        elif status != self.STATUS_RUNNING:
            num_log_lines = self.params.num_log_lines_sent_on_timeout

        if num_log_lines > 0:
            separator = "\n" if self.params.embedded_mode else ""
            if self.stdout_log_line_deque and (self.stdout_reader_thread is None):
                body["debug_log_tail"] = _extract_log_lines(
                    line_queue=self.stdout_log_line_deque,
                    max_lines=num_log_lines,
                    separator=separator,
                )

            if self.stderr_log_line_deque and (self.stderr_reader_thread is None):
                body["error_log_tail"] = _extract_log_lines(
                    line_queue=self.stderr_log_line_deque,
                    max_lines=num_log_lines,
                    separator=separator,
                )

        return body

    def _compute_successful_request_deadline(
        self,
        first_attempt_at: float,
        is_task_execution_creation_request: bool = False,
        for_task_execution_creation_conflict: bool = False,
        is_final_update: bool = False,
    ) -> Optional[float]:
        timeout: Optional[int] = self.params.api_error_timeout

        if is_task_execution_creation_request:
            if for_task_execution_creation_conflict:
                timeout = self.params.api_task_execution_creation_conflict_timeout
            else:
                timeout = self.params.api_task_execution_creation_error_timeout
        elif is_final_update:
            timeout = self.params.api_final_update_timeout

        if timeout is not None:
            return first_attempt_at + timeout

        return None

    def _send_api_request(
        self,
        req: Request,
        is_task_execution_creation_request: bool = False,
        is_final_update: bool = False,
    ) -> Optional[RawIOBase]:
        """
        Send an API request, with retries and error handling.
        """

        _logger.debug(
            f"Sending {req.method} request with body {str(req.data)} to {req.full_url} ...."
        )

        self._refresh_api_server_retries_exhausted()

        if self.api_server_retries_exhausted:
            _logger.debug("Not sending API request because all retries are exhausted")
            return None

        first_attempt_at = time.time()
        deadline = self._compute_successful_request_deadline(
            first_attempt_at=first_attempt_at,
            is_task_execution_creation_request=is_task_execution_creation_request,
            is_final_update=is_final_update,
        )

        attempt_count = 0

        api_request_data: dict[str, Any] = {
            "request": {
                "url": req.full_url,
                "method": req.method,
                "body": str(req.data),
            }
        }

        status_code: Optional[int] = None

        while (deadline is None) or (time.time() < deadline):
            attempt_count += 1
            retry_delay = self.params.api_retry_delay

            _logger.info(f"Sending API request (attempt {attempt_count}) ...")

            try:
                resp = urlopen(req, timeout=self.params.api_request_timeout)
                self.last_api_request_failed_at = None
                self.last_api_request_data = None

                if is_task_execution_creation_request:
                    self.was_conflict = False

                return resp
            except HTTPError as http_error:
                status_code = http_error.code

                response_body = None

                try:
                    response_body = str(http_error.read())
                except Exception:
                    _logger.warning("Can't read error response body")

                api_request_data.pop("error", None)
                api_request_data["response"] = {
                    "status_code": status_code,
                    "body": response_body,
                }

                if status_code in self._RETRYABLE_HTTP_STATUS_CODES:
                    if not self.last_api_request_failed_at:
                        deadline = self._compute_successful_request_deadline(
                            first_attempt_at=first_attempt_at,
                            is_task_execution_creation_request=is_task_execution_creation_request,
                            for_task_execution_creation_conflict=False,
                            is_final_update=is_final_update,
                        )

                    self.last_api_request_failed_at = time.time()

                    retry_delay = coalesce(
                        self._extract_retry_delay_seconds(http_error.headers),
                        retry_delay,
                    )
                    retry_delay = min(
                        max(retry_delay, self._MIN_HTTP_REQUEST_DELAY_SECONDS),
                        self._MAX_HTTP_REQUEST_DELAY_SECONDS,
                    )

                    error_message = f"Endpoint temporarily not available, status code = {status_code}"
                    _logger.warning(error_message)

                elif status_code == 409:
                    if is_task_execution_creation_request:
                        if not self.was_conflict:
                            self.was_conflict = True
                            deadline = self._compute_successful_request_deadline(
                                first_attempt_at=first_attempt_at,
                                is_task_execution_creation_request=True,
                                for_task_execution_creation_conflict=True,
                            )
                        retry_delay = (
                            self.params.api_task_execution_creation_conflict_retry_delay
                        )

                        _logger.info(
                            "Got response code = 409 during Task Execution creation"
                        )
                    else:
                        self.last_api_request_failed_at = time.time()
                        _logger.error(
                            "Got response code 409 after Task Execution started, exiting."
                        )
                        self.was_conflict = True
                        exit_code = self._RESPONSE_CODE_TO_EXIT_CODE.get(
                            status_code, self._EXIT_CODE_GENERIC_ERROR
                        )
                        self._exit_or_raise(exit_code)
                        return None
                else:
                    self.last_api_request_failed_at = time.time()

                    error_message = f"Got error response code = {status_code}, body = '{response_body}'"
                    self._report_error(error_message, api_request_data)

                    if self.params.prevent_offline_execution:
                        _logger.critical(
                            f"Response code = {status_code}, exiting since we are preventing offline execution."
                        )
                        self.last_api_request_data = api_request_data
                        exit_code = self._RESPONSE_CODE_TO_EXIT_CODE.get(
                            status_code, self._EXIT_CODE_GENERIC_ERROR
                        )
                        self._exit_or_raise(exit_code)
                        return None

                    _logger.warning(
                        f"Response code = {status_code}, but continuing since we are allowing offline execution."
                    )
                    return None
            except Exception as ex:
                if not self.last_api_request_failed_at:
                    deadline = self._compute_successful_request_deadline(
                        first_attempt_at=first_attempt_at,
                        is_task_execution_creation_request=is_task_execution_creation_request,
                        for_task_execution_creation_conflict=False,
                        is_final_update=is_final_update,
                    )

                self.last_api_request_failed_at = time.time()

                api_request_data.pop("response", None)

                if isinstance(ex, URLError):
                    error_message = f"URL error: {ex}"
                    api_request_data["error"] = {"reason": str(ex.reason)}
                else:
                    error_message = f"Unhandled exception: {ex}"

                self._report_error(error_message, api_request_data)

            if (deadline is None) or (time.time() < deadline):
                _logger.debug(f"Sleeping {retry_delay} seconds after request error ...")
                time.sleep(retry_delay)
                _logger.debug("Done sleeping after request error.")

        self.api_server_retries_exhausted = True

        if is_task_execution_creation_request:
            if self.params.prevent_offline_execution or self.was_conflict:
                _logger.critical(
                    "Exiting because Task Execution creation timed out and offline execution is prevented or there was a conflict."
                )
                exit_code = self._RESPONSE_CODE_TO_EXIT_CODE.get(
                    status_code or 0, self._EXIT_CODE_GENERIC_ERROR
                )
                self._exit_or_raise(exit_code)
            else:
                self.skip_start_notification = True

        self._report_error(
            "Exhausted retry timeout, not sending any more API requests.",
            api_request_data,
        )
        return None

    @staticmethod
    def _extract_retry_delay_seconds(headers) -> Optional[float]:
        retry_after = headers.get("Retry-After")
        if retry_after:
            retry_delay: Optional[float] = None
            try:
                retry_delay = float(retry_after)
            except ValueError:
                try:
                    retry_after_date = parsedate_to_datetime(retry_after)
                    retry_delay = (
                        retry_after_date - datetime.now(timezone.utc)
                    ).total_seconds()
                except Exception:
                    _logger.warning(
                        f"Can't parse Retry-After header value '{retry_after}'",
                        exc_info=True,
                    )
                    return None

            _logger.info(
                f"Computed retry delay {retry_delay} from Retry-After header {retry_after}"
            )
            return retry_delay

        return None

    def _refresh_api_server_retries_exhausted(self) -> bool:
        if not self.api_server_retries_exhausted:
            return False

        if self.last_api_request_failed_at is None:
            self.api_server_retries_exhausted = False
        else:
            elapsed_time = time.time() - self.last_api_request_failed_at

            if elapsed_time > self.params.api_resume_delay:
                _logger.info(
                    f"Resuming API requests after {int(elapsed_time)} seconds after the last bad request"
                )
                self.api_server_retries_exhausted = False

        return self.api_server_retries_exhausted

    def _reconfigure_logger(self) -> None:
        format = "PROC_WRAPPER: "

        if self.task_execution_uuid:
            uuid_signature = self.task_execution_uuid
            if len(uuid_signature) > 8:
                uuid_signature = self.task_execution_uuid[0:8]

            format += f"[{uuid_signature}] "

        if self.params.include_timestamps_in_log:
            format += "%(asctime)s "

        format += "%(levelname)s: %(message)s"

        formatter = logging.Formatter(format)
        logger_to_configure = _logger

        if not self.params.embedded_mode:
            # Root logger
            logger_to_configure = logging.getLogger()

        for handler in logger_to_configure.handlers:
            try:
                handler.setFormatter(formatter)
            except Exception:
                _logger.warning("Can't set formatter on logger")

    def _start_log_capture(self) -> None:
        self._end_log_capture()

        if self.process:
            if self.process.stdout and (self.stdout_log_line_deque is not None):
                self.stdout_reader_thread = threading.Thread(
                    target=_tee_log_stream,
                    args=(
                        self.process.stdout,
                        sys.stdout,
                        self.stdout_log_line_deque,
                        self.params.max_log_line_length,
                    ),
                    daemon=True,
                )
                self.stdout_reader_thread.start()

            if self.process.stderr and (self.stderr_log_line_deque is not None):
                self.stderr_reader_thread = threading.Thread(
                    target=_tee_log_stream,
                    args=(
                        self.process.stderr,
                        sys.stderr,
                        self.stderr_log_line_deque,
                        self.params.max_log_line_length,
                    ),
                    daemon=True,
                )
                self.stderr_reader_thread.start()
        else:
            _logger.error("No process to start log capture on")

    def _end_log_capture(self) -> None:
        if self.stdout_reader_thread:
            self.stdout_reader_thread.join(timeout=self._LOG_READER_TIMEOUT_SECONDS)
            self.stdout_reader_thread = None

        if self.stderr_reader_thread:
            self.stderr_reader_thread.join(timeout=self._LOG_READER_TIMEOUT_SECONDS)
            self.stderr_reader_thread = None

    def _exit_or_raise(self, exit_code: int) -> int:
        if self.params.embedded_mode:
            raise RuntimeError(
                f"Raising an error in embedded mode, exit code {exit_code}"
            )

        if self.in_pytest:
            self.handle_exit()
        else:
            if self.called_exit:
                raise RuntimeError(
                    f"exit() called already; raising exception instead of exiting with exit code {exit_code}"
                )

            sys.exit(exit_code)

        return exit_code

    def _report_error(self, message: str, data: Optional[dict[str, Any]]) -> None:
        _logger.error(message)

        if self.params.rollbar_access_token:
            self._send_rollbar_error(message, data)

    def _send_rollbar_error(self, message: str, data=None, level="error") -> bool:
        if not self.params.rollbar_access_token:
            _logger.warning(
                f"Not sending '{message}' to Rollbar since no access token found"
            )
            return False

        if self.rollbar_retries_exhausted:
            _logger.debug(
                f"Not sending '{message}' to Rollbar since all retries are exhausted"
            )
            return False

        payload = {
            "data": {
                "environment": self.params.deployment or "Unknown",
                "body": {
                    "message": {
                        "body": message,
                        "task": {
                            "uuid": self.task_uuid,
                            "name": self.task_name,
                            "version_number": self.params.task_version_number,
                            "version_text": self.params.task_version_text,
                            "version_signature": self.params.task_version_signature,
                        },
                        "task_execution": {
                            "uuid": self.task_execution_uuid,
                        },
                        "task_instance_metadata": self.params.task_instance_metadata,
                        "wrapper_version": ProcWrapper.VERSION,
                        "was_conflict": self.was_conflict,
                        "api_server_retries_exhausted": self.api_server_retries_exhausted,
                        "data": data,
                    }
                },
                "level": level,
                "timestamp": time.time(),
                "code_version": self.params.task_version_signature,
                "context": "proc_wrapper",
                "server": {"host": self.hostname},
            },
        }

        request_body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Rollbar-Access-Token": self.params.rollbar_access_token,
        }
        req = Request(
            "https://api.rollbar.com/api/1/item/",
            data=request_body,
            headers=headers,
            method="POST",
        )

        attempt_count = 0

        if self.params.rollbar_retries is None:
            max_attempts = -1
        else:
            max_attempts = self.params.rollbar_retries + 1

        while (max_attempts < 0) or (attempt_count < max_attempts):
            attempt_count += 1

            _logger.info(
                f"Sending Rollbar request attempt {attempt_count}/{max_attempts}) ..."
            )

            try:
                with urlopen(req, timeout=self.params.rollbar_timeout) as f:
                    response_body = f.read().decode("utf-8")
                    _logger.debug(f"Got Rollbar response '{response_body}'")

                    response_dict = json.loads(response_body)
                    uuid = response_dict["result"]["uuid"]

                    _logger.debug(f"Rollbar request returned UUID {uuid}.")

                    return True
            except HTTPError as http_error:
                status_code = http_error.code
                _logger.critical(f"Rollbar response code = {status_code}, giving up.")
                return False
            except URLError as url_error:
                _logger.error(f"Rollbar URL error: {url_error}")

                if self.params.rollbar_retry_delay:
                    _logger.debug("Sleeping after Rollbar request error ...")
                    time.sleep(self.params.rollbar_retry_delay)
                    _logger.debug("Done sleeping after Rollbar request error.")

        self.rollbar_retries_exhausted = True
        _logger.error("Exhausted all retries, giving up.")
        return False
