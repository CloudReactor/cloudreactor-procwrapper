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
import signal
import socket
import sys
import time
from datetime import datetime
from http import HTTPStatus
from io import RawIOBase
from subprocess import Popen, TimeoutExpired
from typing import Any, Dict, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from .common_utils import encode_int, string_to_bool
from .config_resolver import ConfigResolver
from .proc_wrapper_params import ProcWrapperParams, ProcWrapperParamValidationErrors
from .runtime_metadata import RuntimeMetadata, RuntimeMetadataFetcher

_logger = logging.getLogger(__name__)
_logger.addHandler(logging.NullHandler())

caught_sigterm = False


def _exit_handler(wrapper: "ProcWrapper"):
    # Prevent re-entrancy and changing of the exit code
    atexit.unregister(_exit_handler)
    wrapper.handle_exit()


def _signal_handler(signum, frame):
    global caught_sigterm
    caught_sigterm = True
    # This will cause the exit handler to be executed, if it is registered.
    _logger.info(f"Caught signal {signum}, exiting")

    # TODO: use different exit code if configured
    sys.exit(0)


class ProcWrapper:
    VERSION = getattr(sys.modules[__package__], "__version__")

    STATUS_RUNNING = "RUNNING"
    STATUS_SUCCEEDED = "SUCCEEDED"
    STATUS_FAILED = "FAILED"
    STATUS_TERMINATED_AFTER_TIME_OUT = "TERMINATED_AFTER_TIME_OUT"
    STATUS_MARKED_DONE = "MARKED_DONE"
    STATUS_EXITED_AFTER_MARKED_DONE = "EXITED_AFTER_MARKED_DONE"
    STATUS_ABORTED = "ABORTED"

    _ALLOWED_FINAL_STATUSES = set(
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

    _RETRYABLE_HTTP_STATUS_CODES = set(
        [
            HTTPStatus.SERVICE_UNAVAILABLE.value,
            HTTPStatus.BAD_GATEWAY.value,
            HTTPStatus.INTERNAL_SERVER_ERROR.value,
        ]
    )

    _STATUS_BUFFER_SIZE = 4096

    def __init__(
        self,
        params: Optional[ProcWrapperParams] = None,
        runtime_metadata_fetcher: Optional[RuntimeMetadataFetcher] = None,
        config_resolver: Optional[ConfigResolver] = None,
        env_override: Optional[Mapping[str, Any]] = None,
    ) -> None:
        _logger.info("Creating ProcWrapper instance ...")

        if env_override:
            self.env = dict(env_override)
        else:
            self.env = os.environ.copy()

        self.param_errors: Optional[ProcWrapperParamValidationErrors] = None
        self.offline_mode = False
        self.task_uuid: Optional[str] = None
        self.task_name: Optional[str] = None
        self.task_execution_uuid: Optional[str] = None
        self.was_conflict = False
        self.called_exit = False
        self.reported_final_status = False
        self.attempt_count = 0
        self.failed_count = 0
        self.timeout_count = 0
        self.timed_out = False
        self.hostname: Optional[str] = None
        self.process: Optional[Popen[bytes]] = None
        self.api_server_retries_exhausted = False
        self.last_api_request_failed_at: Optional[float] = None
        self.last_api_request_data: Optional[Dict[str, Any]] = None

        self.status_dict: Dict[str, Any] = {}
        self._status_socket: Optional[socket.socket] = None
        self._status_buffer: Optional[bytearray] = None
        self._status_message_so_far: Optional[bytearray] = None
        self.last_update_sent_at: Optional[float] = None

        self.rollbar_retries_exhausted = False
        self.exit_handler_installed = False
        self.in_pytest = False

        self.runtime_metadata_fetcher: RuntimeMetadataFetcher = (
            runtime_metadata_fetcher or RuntimeMetadataFetcher()
        )

        runtime_metadata = self.runtime_metadata_fetcher.fetch(env=self.env)

        if params:
            self.params = params
        else:
            self.params = ProcWrapperParams()
            self.params.override_resolver_params_from_env(env=self.env)

        if config_resolver:
            self.config_resolver = config_resolver
        else:
            self.config_resolver = ConfigResolver(
                params=self.params,
                runtime_metadata=runtime_metadata,
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

        self.config_resolver.fetch_and_resolve_env()

        if params is None:
            self.params.override_proc_wrapper_params_from_env(
                env=self.resolved_env,
                mutable_only=False,
                runtime_metadata=runtime_metadata,
            )

        self.in_pytest = string_to_bool(os.environ.get("IN_PYTEST")) or False

        # Now we have enough info to try to send errors if problems happen below.

        if (
            (not self.exit_handler_installed)
            and (not self.params.embedded_mode)
            and (not self.in_pytest)
        ):
            atexit.register(_exit_handler, self)

            # The function registered with atexit.register() isn't called when python receives
            # most signals, include SIGTERM. So register a signal handler which will cause the
            # program to exit with an error, triggering the exit handler.
            signal.signal(signal.SIGTERM, _signal_handler)

            self.exit_handler_installed = True

        return None

    def log_configuration(self) -> None:
        _logger.debug(f"Wrapper version = {ProcWrapper.VERSION}")
        self.params.log_configuration()

    def print_final_status(
        self,
        exit_code: Optional[int],
        first_attempt_started_at: Optional[float],
        latest_attempt_started_at: Optional[float],
    ):
        if latest_attempt_started_at is None:
            latest_attempt_started_at = first_attempt_started_at

        action = "failed due to wrapping error"

        if exit_code == 0:
            action = "succeeded"
        elif exit_code is not None:
            action = f"failed with exit code {exit_code}"
        elif self.timed_out:
            action = "timed out"

        task_name = self.task_name or self.task_uuid or "[Unnamed]"
        now = datetime.now()
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
            msg += f", for a total duration of {round(total_duration)} seconds"

        msg += "."

        print(msg)

    @property
    def max_execution_attempts(self) -> float:
        if self.params.process_max_retries is None:
            return math.inf

        return self.params.process_max_retries + 1

    def _create_or_update_task_execution(self) -> None:
        """
        Make a request to the API server to create a Task Execution
        for this Task. Retry and wait between requests if so configured
        and the maximum concurrency has already been reached.
        """

        if self.offline_mode:
            return

        if self.params.send_hostname and (self.hostname is None):
            try:
                self.hostname = socket.gethostname()
                _logger.debug(f"Hostname = '{self.hostname}'")
            except Exception:
                _logger.warning("Can't get hostname", exc_info=True)

        runtime_metadata: Optional[RuntimeMetadata] = None
        if self.params.send_runtime_metadata:
            runtime_metadata = self.runtime_metadata_fetcher.fetch(
                env=self.resolved_env
            )

        status = ProcWrapper.STATUS_RUNNING
        stop_reason: Optional[str] = None

        if self.param_errors:
            if not (
                self.param_errors.can_start_task_execution()
                and self.param_errors.can_start_process()
            ):
                status = ProcWrapper.STATUS_ABORTED
                # TODO: use a reason that indicate misconfiguration
                stop_reason = "FAILED_TO_START"

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
            }

            body = {
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
                    self.params.status_update_interval, empty_value=-1
                ),
                "status_update_port": encode_int(
                    self.params.status_update_socket_port, empty_value=-1
                ),
                "status_update_message_max_bytes": encode_int(
                    self.params.status_update_message_max_bytes, empty_value=-1
                ),
                "embedded_mode": self.params.embedded_mode,
            }
            body.update(common_body)

            if stop_reason is not None:
                body["stop_reason"] = stop_reason

            if self.task_execution_uuid:
                # Manually started
                url += self.task_execution_uuid + "/"
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

                task_emc: Optional[Dict[str, Any]] = None
                if self.params.send_runtime_metadata and runtime_metadata:
                    task_emc = runtime_metadata.execution_method_capability

                if self.params.auto_create_task_props:
                    override_emc = self.params.auto_create_task_props.get(
                        "execution_method_capability"
                    )
                    if override_emc:
                        task_emc = task_emc or {}
                        task_emc.update(override_emc)

                task_dict["execution_method_capability"] = task_emc or {
                    "type": "Unknown"
                }

                body["task"] = task_dict
                body["max_conflicting_age_seconds"] = self.params.max_conflicting_age

            if self.params.command:
                body["process_command"] = " ".join(self.params.command)

            if self.params.send_hostname and self.hostname:
                body["hostname"] = self.hostname

            if self.params.task_instance_metadata:
                body["other_instance_metadata"] = self.params.task_instance_metadata

            execution_method: Optional[Dict[str, Any]] = None
            if self.params.send_runtime_metadata and runtime_metadata:
                execution_method = runtime_metadata.execution_method

            if self.params.execution_method_props:
                execution_method = execution_method or {}
                execution_method.update(self.params.execution_method_props)

            if execution_method:
                body["execution_method"] = execution_method

            data = json.dumps(body).encode("utf-8")

            req = Request(url, data=data, headers=headers, method=http_method)

            f = self._send_api_request(req, is_task_execution_creation_request=True)
            if f is None:
                _logger.warning("Task Execution creation request failed non-fatally")
            else:
                with f:
                    _logger.info("Task Execution creation request was successful.")

                    if status != self.STATUS_RUNNING:
                        self.reported_final_status = True

                    fd = f.read()

                    if not fd:
                        raise RuntimeError(
                            "Unexpected None result of reading Task Execution creation response"
                        )

                    response_body = fd.decode("utf-8")
                    _logger.debug(f"Got creation response '{response_body}'")

                    response_dict = json.loads(response_body)

                    if not self.task_execution_uuid:
                        self.task_execution_uuid = response_dict.get("uuid")

                    self.task_uuid = self.task_uuid or response_dict["task"]["uuid"]
                    self.task_name = self.task_name or response_dict["task"]["name"]

        except Exception as ex:
            _logger.exception(
                "request_process_start_if_max_concurrency_ok() failed with exception"
            )
            if self.params.prevent_offline_execution:
                raise ex

            _logger.info("Not preventing offline execution, so continuing")

    def update_status(
        self,
        failed_attempts=None,
        timed_out_attempts=None,
        pid=None,
        success_count=None,
        error_count=None,
        skipped_count=None,
        expected_count=None,
        last_status_message=None,
        extra_status_props=None,
    ) -> None:
        """Update the status of the process. Send to the process management
        server if the last status was sent more than status_update_interval
        seconds ago.
        """

        if not self.offline_mode:
            return

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

        should_send = self.is_status_update_due()

        # These are important updates that should be sent no matter what.
        if not (
            (failed_attempts is None) and (timed_out_attempts is None) and (pid is None)
        ) and (last_status_message is None):
            should_send = True

        if should_send:
            self.send_update(
                failed_attempts=failed_attempts,
                timed_out_attempts=timed_out_attempts,
                pid=pid,
            )

    def send_completion(
        self,
        status: str,
        failed_attempts: Optional[int] = None,
        timed_out_attempts: Optional[int] = None,
        exit_code: Optional[int] = None,
        pid: Optional[int] = None,
    ) -> None:
        """Send the final result to the API Server."""

        if self.offline_mode:
            return

        if status not in self._ALLOWED_FINAL_STATUSES:
            self._exit_or_raise(self._EXIT_CODE_GENERIC_ERROR)
            return

        if exit_code is None:
            if status == self.STATUS_SUCCEEDED:
                exit_code = self._EXIT_CODE_SUCCESS
            else:
                exit_code = self._EXIT_CODE_GENERIC_ERROR

        self.send_update(
            status=status,
            failed_attempts=failed_attempts,
            timed_out_attempts=timed_out_attempts,
            exit_code=exit_code,
            pid=pid,
        )

    def managed_call(self, fun, data=None):
        """
        Call the argument object, which must be callable, doing retries as necessary,
        and wrapping with calls to the API server.
        """
        if not callable(fun):
            raise RuntimeError("managed_call() argument is not callable")

        if self.failed_env_names:
            _logger.critical(
                f"Failed to resolve one or more environment variables: {self.failed_env_names}"
            )
            self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
            return

        if self.failed_config_props:
            _logger.critical(
                f"Failed to resolve one or more config props: {self.failed_config_props}"
            )
            self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
            return

        self._reload_params()
        self._setup_task_execution()

        rv = None
        success = False
        saved_ex = None
        while self.attempt_count < self.max_execution_attempts:
            self.attempt_count += 1

            _logger.info(
                f"Calling managed function (attempt {self.attempt_count}/{self.max_execution_attempts}) ..."
            )

            try:
                rv = fun(self, data, self.resolved_config)
                success = True
            except Exception as ex:
                saved_ex = ex
                self.failed_count += 1
                _logger.exception("Managed function failed")

                if self.attempt_count < self.max_execution_attempts:
                    self.send_update(failed_attempts=self.failed_count)

                    if self.params.process_retry_delay:
                        _logger.debug("Sleeping after managed function failed ...")
                        time.sleep(self.params.process_retry_delay)
                        _logger.debug("Done sleeping after managed function failed.")

                    if self.params.config_ttl is not None:
                        self._reload_params()

            if success:
                self.send_completion(
                    status=ProcWrapper.STATUS_SUCCEEDED,
                    failed_attempts=self.failed_count,
                )
                return rv

        self.send_completion(
            status=ProcWrapper.STATUS_FAILED, failed_attempts=self.failed_count
        )

        raise saved_ex

    def _reload_params(self) -> None:
        (
            self.resolved_env,
            self.failed_env_names,
            self.resolved_config,
            self.failed_config_props,
        ) = self.config_resolver.fetch_and_resolve_env_and_config(
            want_env=True, want_config=self.params.embedded_mode
        )

        runtime_metadata = self.runtime_metadata_fetcher.fetch(env=self.resolved_env)

        # In case API key(s) change
        self.params.override_proc_wrapper_params_from_env(
            env=self.resolved_env, mutable_only=True, runtime_metadata=runtime_metadata
        )

        self.param_errors = self.params.validation_errors(
            runtime_metadata=runtime_metadata
        )

    def _setup_task_execution(self) -> bool:
        self._reload_params()

        self.log_configuration()

        if self.param_errors:
            self.param_errors.log()
        else:
            _logger.warning("No validated parameters?!")

        self.task_uuid = self.params.task_uuid
        self.task_name = self.params.task_name
        self.task_execution_uuid = self.params.task_execution_uuid
        self.offline_mode = self.params.offline_mode or (
            (self.param_errors is not None)
            and not self.param_errors.can_start_task_execution()
        )
        self.attempt_count = 0
        self.timeout_count = 0

        if self.offline_mode:
            _logger.info("Starting in offline mode ...")
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

                if self.params.enable_status_update_listener:
                    self._status_buffer = bytearray(self._STATUS_BUFFER_SIZE)
                    self._status_message_so_far = bytearray()

        return True

    def run(self) -> None:
        """
        Run the wrapped process, first requesting to start, then executing
        the process command line, retying for errors and timeouts.
        """
        self._ensure_non_embedded_mode()

        should_run = self._setup_task_execution()

        if not self.params.command:
            _logger.error("No command found in wrapped mode")
            self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
            return

        if not should_run:
            self._exit_or_raise(self._EXIT_CODE_GENERIC_ERROR)

        if (not self.offline_mode) and self.params.enable_status_update_listener:
            self._status_buffer = bytearray(self._STATUS_BUFFER_SIZE)
            self._status_message_so_far = bytearray()

        process_env = self.make_process_env()

        if self.params.log_secrets:
            _logger.debug(f"Process environment = {process_env}")

        first_attempt_started_at: Optional[float] = None
        latest_attempt_started_at: Optional[float] = None
        exit_code: Optional[int] = None

        self._open_status_socket()

        while self.attempt_count < self.max_execution_attempts:
            self.attempt_count += 1
            self.timed_out = False

            _logger.info(
                f"Running process (attempt {self.attempt_count}/{self.max_execution_attempts}) ..."
            )

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

            _logger.debug(
                f"command = {self.params.command}, cwd={self.params.work_dir}"
            )

            if self.params.log_secrets:
                _logger.debug(f"process_env={process_env}")

            latest_attempt_started_at = time.time()
            if first_attempt_started_at is None:
                first_attempt_started_at = latest_attempt_started_at

            # Set the session ID so we can kill the process as a group, so we kill
            # all subprocesses. See https://stackoverflow.com/questions/4789837/how-to-terminate-a-python-subprocess-launched-with-shell-true
            # On Windows, os does not have setsid function.
            # TODO: allow non-shell mode
            self.process = Popen(
                " ".join(self.params.command),
                shell=True,
                stdout=None,
                stderr=None,
                env=process_env,
                cwd=self.params.work_dir,
                preexec_fn=getattr(os, "setsid", None),
            )

            pid = self.process.pid
            _logger.info(f"pid = {pid}")

            if pid and self.params.send_pid:
                self.send_update(pid=pid)

            done_polling = False
            while not done_polling:
                exit_code = self.process.poll()

                self._read_from_status_socket()

                current_time = time.time()

                # None means the process is still running
                if exit_code is None:
                    if current_time >= process_finish_deadline:
                        done_polling = True
                        self.timed_out = True
                        self.timeout_count += 1

                        if self.attempt_count < self.max_execution_attempts:
                            self.send_update(
                                failed_attempts=self.failed_count,
                                timed_out_attempts=self.timeout_count,
                            )

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
                        )
                        sleep_duration = sleep_until - current_time

                        if sleep_duration > 0:
                            _logger.debug(
                                f"Waiting {round(sleep_duration)} seconds while process is running ..."
                            )
                            try:
                                self.process.wait(sleep_duration)
                            except TimeoutExpired:
                                _logger.debug("Done waiting while process is running.")

                else:
                    _logger.info(f"Process exited with exit code {exit_code}")

                    done_polling = True

                    if exit_code == 0:
                        self.print_final_status(
                            exit_code=0,
                            first_attempt_started_at=first_attempt_started_at,
                            latest_attempt_started_at=latest_attempt_started_at,
                        )
                        self._exit_or_raise(0)
                        return

                    self.failed_count += 1

                    if self.attempt_count >= self.max_execution_attempts:
                        self.print_final_status(
                            exit_code=exit_code,
                            first_attempt_started_at=first_attempt_started_at,
                            latest_attempt_started_at=latest_attempt_started_at,
                        )
                        self._exit_or_raise(exit_code)
                        return

                    self.send_update(
                        failed_attempts=self.failed_count, exit_code=exit_code
                    )

                    if self.params.process_retry_delay:
                        _logger.debug(
                            f"Sleeping {self.params.process_retry_delay} seconds after process failed ..."
                        )
                        time.sleep(self.params.process_retry_delay)
                        _logger.debug("Done sleeping after process failed.")

                    if self.params.config_ttl is not None:
                        self._reload_params()
                        process_env = self.make_process_env()

        if self.attempt_count >= self.max_execution_attempts:
            self.print_final_status(
                exit_code=exit_code,
                first_attempt_started_at=first_attempt_started_at,
                latest_attempt_started_at=latest_attempt_started_at,
            )

            self._exit_or_raise(self._EXIT_CODE_GENERIC_ERROR)
        else:
            self.send_update(failed_attempts=self.attempt_count)

        # Exit handler will send update to API server

    def handle_exit(self) -> None:
        _logger.debug(f"Exit handler, Task Execution UUID = {self.task_execution_uuid}")

        if self.called_exit:
            _logger.debug("Called exit already, returning early")
            return

        self.called_exit = True

        status = self.STATUS_FAILED

        if caught_sigterm:
            status = self.STATUS_ABORTED
        elif self.timed_out:
            status = self.STATUS_TERMINATED_AFTER_TIME_OUT

        exit_code = None
        pid = None
        notification_required = True

        try:
            if self.process:
                exit_code = self._terminate_or_kill_process()
                if exit_code == 0:
                    status = self.STATUS_SUCCEEDED
        except Exception:
            _logger.exception("Exception in process termination")
        finally:
            try:
                if self.task_execution_uuid and (not self.reported_final_status):
                    if self.was_conflict:
                        _logger.debug(
                            "Task execution update conflict detected, not notifying because server-side should have notified already."
                        )
                        notification_required = False
                        status = self.STATUS_ABORTED

                    if not self.offline_mode and not self.api_server_retries_exhausted:
                        self.send_completion(
                            status=status,
                            failed_attempts=self.failed_count,
                            timed_out_attempts=self.timeout_count,
                            exit_code=exit_code,
                            pid=pid,
                        )
                        notification_required = False
            except Exception:
                _logger.exception("Exception in final update")
            finally:
                self._close_status_socket()

        if notification_required:
            error_message = "API Server not configured"

            if self.api_server_retries_exhausted:
                error_message = "API Server not responding properly"
            elif not self.task_execution_uuid:
                error_message = "Task Execution ID not assigned"

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

        _logger.warning("Sending SIGTERM ...")

        os.killpg(os.getpgid(pid), signal.SIGTERM)

        try:
            self.process.communicate(
                timeout=self.params.process_termination_grace_period
            )

            _logger.info("Process terminated successfully after SIGTERM.")
        except TimeoutExpired:
            _logger.info("Timeout after SIGTERM expired, killing with SIGKILL ...")
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
                self.process.communicate()
            except Exception:
                _logger.exception(f"Could not kill process with pid {pid}")

        self.process = None
        return None

    def _ensure_non_embedded_mode(self):
        if self.params.embedded_mode:
            raise RuntimeError(
                "Called method is only for wrapped process (non-embedded) mode"
            )

    def _open_status_socket(self) -> Optional[socket.socket]:
        if self.offline_mode or (not self.params.enable_status_update_listener):
            _logger.info("Not opening status socket.")
            return None

        try:
            _logger.info("Opening status update socket ...")
            self._status_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._status_socket.bind(
                ("127.0.0.1", self.params.status_update_socket_port)
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
                    "Error decoding JSON, this can happen due to missing out of order messages"
                )
        except UnicodeDecodeError:
            _logger.debug(
                "Error decoding message as UTF-8, this can happen due to missing out of order messages"
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

    def _make_headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Token {self.params.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        return headers

    def make_process_env(self) -> Dict[str, str]:
        process_env = self.resolved_env.copy()
        self.params.populate_env(process_env)
        return process_env

    def send_update(
        self,
        status: Optional[str] = None,
        failed_attempts: Optional[int] = None,
        timed_out_attempts: Optional[int] = None,
        exit_code: Optional[int] = None,
        pid: Optional[int] = None,
        extra_props: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """
        Send an update to the API server. If status is omitted, it will
        default to STATUS_RUNNING.

        The caller may want to coalesce multiple updates together in order to
        save bandwidth / API usage.

        This method is meant to be called directly in the callback function
        when running in embedded mode.
        """

        if self.offline_mode:
            return

        if not self.task_execution_uuid:
            _logger.debug(
                "_send_update() skipping update since no Task Execution UUID was available"
            )
            return

        if status is None:
            status = self.STATUS_RUNNING

        body: Dict[str, Any] = {"status": status}

        body.update(self.status_dict)

        if failed_attempts is not None:
            body["failed_attempts"] = failed_attempts

        if timed_out_attempts is not None:
            body["timed_out_attempts"] = timed_out_attempts

        if exit_code is not None:
            body["exit_code"] = exit_code

        if pid is not None:
            body["pid"] = pid

        if extra_props:
            body["other_runtime_metadata"] = extra_props

        url = f"{self.params.api_base_url}/api/v1/task_executions/{quote_plus(str(self.task_execution_uuid))}/"
        headers = self._make_headers()
        text_data = json.dumps(body)
        data = text_data.encode("utf-8")
        is_final_update = status != self.STATUS_RUNNING

        _logger.debug(f"Sending update '{text_data}' ...")

        req = Request(url, data=data, headers=headers, method="PATCH")
        f = self._send_api_request(req, is_final_update=is_final_update)
        if f is None:
            _logger.debug("Update request failed non-fatally")
        else:
            with f:
                _logger.debug("Update sent successfully.")
                self.last_update_sent_at = time.time()
                self.status_dict = {}
                self.reported_final_status = is_final_update

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
        _logger.debug(f"Sending {req.method} request to {req.full_url} ....")

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

        api_request_data: Dict[str, Any] = {
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

                    error_message = f"Endpoint temporarily not available, status code = {status_code}"
                    _logger.warning(error_message)

                    self._report_error(error_message, api_request_data)
                elif status_code == 409:
                    if is_task_execution_creation_request:
                        if not self.was_conflict:
                            self.was_conflict = True
                            deadline = self._compute_successful_request_deadline(
                                first_attempt_at,
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

                    error_message = f"Got error response code = {status_code}"
                    _logger.error(error_message)

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
                    _logger.error(error_message)
                else:
                    error_message = f"Unhandled exception: {ex}"
                    _logger.exception(error_message)

                self._report_error(error_message, api_request_data)

            if (deadline is None) or (time.time() < deadline):
                _logger.debug(f"Sleeping {retry_delay} seconds after request error ...")
                time.sleep(retry_delay)
                _logger.debug("Done sleeping after request error.")

        if is_task_execution_creation_request and (
            self.params.prevent_offline_execution or self.was_conflict
        ):
            _logger.critical(
                "Exiting because Task Execution creation timed out and offline execution is prevented or there was a conflict."
            )
            exit_code = self._RESPONSE_CODE_TO_EXIT_CODE.get(
                status_code or 0, self._EXIT_CODE_GENERIC_ERROR
            )
            self._exit_or_raise(exit_code)

        _logger.error("Exhausted retry timeout, not sending any more API requests.")
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

    def _exit_or_raise(self, exit_code) -> None:
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

    def _report_error(self, message: str, data: Optional[Dict[str, Any]]) -> int:
        num_sinks_successful = 0
        if self.params.rollbar_access_token:
            if self._send_rollbar_error(message, data):
                num_sinks_successful += 1

        if num_sinks_successful == 0:
            _logger.info("Can't notify any valid error sink!")

        return num_sinks_successful

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
            "access_token": self.params.rollbar_access_token,
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
                "server": {
                    "host": self.hostname
                    # Could put code version here
                },
            },
        }

        request_body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
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
