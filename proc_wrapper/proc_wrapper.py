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
from datetime import datetime
import json
import logging
import math
import os
import re
import signal
import socket
import sys
import time
from http import HTTPStatus
from io import RawIOBase
from subprocess import Popen, TimeoutExpired
from typing import Any, Dict, List, Mapping, Optional, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from .common_utils import coalesce, string_to_bool, string_to_int
from .arg_parser import (
    DEFAULT_API_BASE_URL,
    DEFAULT_API_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_API_ERROR_TIMEOUT_SECONDS,
    DEFAULT_API_RETRY_DELAY_SECONDS,
    DEFAULT_API_RESUME_DELAY_SECONDS,
    DEFAULT_API_TASK_EXECUTION_CREATION_TIMEOUT_SECONDS,
    DEFAULT_API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY_SECONDS,
    DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_CREATION_TIMEOUT_SECONDS,
    DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_CREATION_CONFLICT_RETRY_DELAY_SECONDS,
    DEFAULT_API_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_API_FINAL_UPDATE_TIMEOUT_SECONDS,
    DEFAULT_CONFIG_RESOLUTION_MAX_DEPTH,
    DEFAULT_STATUS_UPDATE_SOCKET_PORT,
    DEFAULT_ROLLBAR_TIMEOUT_SECONDS,
    DEFAULT_ROLLBAR_RETRIES,
    DEFAULT_ROLLBAR_RETRY_DELAY_SECONDS,
    DEFAULT_PROCESS_CHECK_INTERVAL_SECONDS,
    DEFAULT_PROCESS_RETRY_DELAY_SECONDS,
    DEFAULT_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS,
    DEFAULT_STATUS_UPDATE_MESSAGE_MAX_BYTES,
    HEARTBEAT_DELAY_TOLERANCE_SECONDS,
    make_arg_parser,
    make_default_args
)

from .runtime_metadata import RuntimeMetadata
from .env_resolver import EnvResolver
from .runtime_metadata import fetch_runtime_metadata


_logger = logging.getLogger(__name__)
_logger.addHandler(logging.NullHandler())

caught_sigterm = False


def _exit_handler(wrapper: 'ProcWrapper'):
    # Prevent re-entrancy and changing of the exit code
    atexit.unregister(_exit_handler)
    wrapper.handle_exit()


def _signal_handler(signum, frame):
    global caught_sigterm
    caught_sigterm = True
    # This will cause the exit handler to be executed, if it is registered.
    raise RuntimeError('Caught SIGTERM, exiting.')


def _encode_int(x: Optional[int], empty_value: Optional[int] = None) -> \
        Optional[int]:
    if x is None:
        return empty_value
    else:
        return x


class ProcWrapper:
    VERSION = getattr(sys.modules[__package__], '__version__')

    STATUS_RUNNING = 'RUNNING'
    STATUS_SUCCEEDED = 'SUCCEEDED'
    STATUS_FAILED = 'FAILED'
    STATUS_TERMINATED_AFTER_TIME_OUT = 'TERMINATED_AFTER_TIME_OUT'
    STATUS_MARKED_DONE = 'MARKED_DONE'
    STATUS_EXITED_AFTER_MARKED_DONE = 'EXITED_AFTER_MARKED_DONE'
    STATUS_ABORTED = 'ABORTED'

    _ALLOWED_FINAL_STATUSES = set([
        STATUS_SUCCEEDED, STATUS_FAILED, STATUS_TERMINATED_AFTER_TIME_OUT,
        STATUS_EXITED_AFTER_MARKED_DONE, STATUS_ABORTED
    ])

    _EXIT_CODE_SUCCESS = 0
    _EXIT_CODE_GENERIC_ERROR = 1
    _EXIT_CODE_CONFIGURATION_ERROR = 78

    _RESPONSE_CODE_TO_EXIT_CODE = {
        409: 75,  # temp failure
        403: 77  # permission denied
    }

    _RETRYABLE_HTTP_STATUS_CODES = set([
        HTTPStatus.SERVICE_UNAVAILABLE.value, HTTPStatus.BAD_GATEWAY.value,
        HTTPStatus.INTERNAL_SERVER_ERROR.value
    ])

    _STATUS_BUFFER_SIZE = 4096

    @staticmethod
    def make_arg_parser(require_command=True):
        """Deprecated in 2.2, use the top-level make_arg_parser() function."""
        make_arg_parser(require_command=require_command)

    @staticmethod
    def make_default_args():
        """Deprecated in 2.2, use the top-level make_default_args() function."""
        return make_default_args()

    def __init__(self, args=None, embedded_mode=True,
            env_override: Optional[Mapping[str, Any]] = None) -> None:
        _logger.info('Creating ProcWrapper instance ...')

        if env_override:
            self.env = dict(env_override)
        else:
            self.env = os.environ.copy()

        if not args:
            args = make_default_args()

        self.args = args

        self.log_secrets = string_to_bool(
                self.env.get('PROC_WRAPPER_API_LOG_SECRETS'),
                default_value=args.log_secrets) or False

        self.task_uuid: Optional[str] = None
        self.task_name: Optional[str] = None
        self.auto_create_task = False
        self.auto_create_task_run_environment_name: Optional[str] = None
        self.auto_create_task_run_environment_uuid: Optional[str] = None
        self.auto_create_task_overrides: Dict[str, Any] = {}
        self.task_is_passive = False
        self.task_execution_uuid: Optional[str] = None
        self.execution_method_overrides: Optional[Dict[str, Any]] = None
        self.schedule = ''
        self.max_conflicting_age: Optional[int] = None
        self.was_conflict = False
        self.called_exit = False
        self.attempt_count = 0
        self.failed_count = 0
        self.timeout_count = 0
        self.timed_out = False
        self.send_pid = False
        self.send_hostname = False
        self.hostname: Optional[str] = None
        self.send_runtime_metadata = True
        self.runtime_metadata: Optional[RuntimeMetadata] = None
        self.fetched_runtime_metadata_at: Optional[float] = None
        self.process: Optional[Popen[bytes]] = None
        self.process_timeout: Optional[int] = None
        self.process_check_interval: Optional[int] = None
        self.prevent_offline_execution = False
        self.process_max_retries: Optional[int] = 0
        self.process_retry_delay = DEFAULT_PROCESS_RETRY_DELAY_SECONDS
        self.command: Optional[List[str]] = None
        self.working_dir: str = '.'
        self.api_key: Optional[str] = None
        self.api_retry_delay = DEFAULT_API_RETRY_DELAY_SECONDS
        self.api_resume_delay = DEFAULT_API_RESUME_DELAY_SECONDS
        self.api_request_timeout: Optional[int] = \
                DEFAULT_API_REQUEST_TIMEOUT_SECONDS
        self.api_error_timeout: Optional[int] = \
                DEFAULT_API_ERROR_TIMEOUT_SECONDS
        self.api_task_execution_creation_error_timeout: Optional[int] = \
                DEFAULT_API_TASK_EXECUTION_CREATION_TIMEOUT_SECONDS
        self.api_task_execution_creation_conflict_timeout: Optional[int] = 0
        self.api_task_execution_creation_conflict_retry_delay = \
                DEFAULT_API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY_SECONDS
        self.api_final_update_timeout: Optional[int] = \
                DEFAULT_API_FINAL_UPDATE_TIMEOUT_SECONDS
        self.api_heartbeat_interval: Optional[int] = None
        self.api_server_retries_exhausted = False
        self.last_api_request_failed_at: Optional[float] = None
        self.last_api_request_data: Optional[Dict[str, Any]] = None

        self._embedded_mode = embedded_mode

        self.status_dict: Dict[str, Any] = {}
        self.status_update_interval: Optional[int] = None
        self.status_update_socket_port: Optional[int] = None
        self._status_socket: Optional[socket.socket] = None
        self._status_buffer: Optional[bytearray] = None
        self._status_message_so_far: Optional[bytearray] = None
        self.status_update_message_max_bytes: Optional[int] = None
        self.last_update_sent_at: Optional[float] = None

        self.rollbar_retries_exhausted = False
        self.exit_handler_installed = False
        self.in_pytest = False

        self.resolved_env_ttl = string_to_int(
                self.env.get('PROC_WRAPPER_RESOLVED_ENV_TTL_SECONDS'),
                default_value=args.resolved_env_ttl)


        config_resolution_depth = DEFAULT_CONFIG_RESOLUTION_MAX_DEPTH
        overwrite_env_during_secret_resolution = string_to_bool(
                self.env.get('PROC_WRAPPER_OVERWRITE_ENV_WITH_SECRETS')) or False

        should_resolve_secrets = string_to_bool(
                self.env.get('PROC_WRAPPER_RESOLVE_SECRETS'), False)

        if not should_resolve_secrets:
            config_resolution_depth = 0

        if not should_resolve_secrets:
            _logger.debug('Secrets resolution is disabled.')

        self.load_runtime_metadata()

        env_var_prefix = self.env.get('PROC_WRAPPER_RESOLVABLE_ENV_VAR_PREFIX')
        env_var_suffix = self.env.get('PROC_WRAPPER_RESOLVABLE_ENV_VAR_SUFFIX')
        config_var_prefix = self.env.get('PROC_WRAPPER_RESOLVABLE_CONFIG_VAR_PREFIX')
        config_var_suffix = self.env.get('PROC_WRAPPER_RESOLVABLE_CONFIG_VAR_SUFFIX')

        config_locations = args.config_locations or []

        config_locations_in_env = self.env.get('PROC_WRAPPER_CONFIG_LOCATIONS')

        if config_locations_in_env is not None:
            # Use , or ; to split locations, except they may be escaped by
            # backslashes. Any occurrence of , or ; in a location string
            # must be backslash escaped. This doesn't handle the weird case
            # when a location contains "\," or "\;".
            config_locations = [location.replace(r'\,', ',') \
                .replace(r'\;', ';').replace(r'\\\\', r'\\') for location in \
                re.split('\s*(?<!(?<!\\)\\)[,;]\s*', config_locations_in_env)]

        config_merge_strategy = self.env.get(
                'PROC_WRAPPER_CONFIG_MERGE_STRATEGY',
                args.config_merge_strategy)

        self.env_resolver = EnvResolver(resolved_env_ttl=self.resolved_env_ttl,
                should_log_values=self.log_secrets,
                runtime_metadata=self.runtime_metadata,
                config_locations=config_locations,
                config_merge_strategy=config_merge_strategy,
                max_depth=config_resolution_depth,
                env_var_prefix=env_var_prefix,
                env_var_suffix=env_var_suffix,
                config_var_prefix=config_var_prefix,
                config_var_suffix=config_var_suffix,
                should_overwrite_env_during_resolution=overwrite_env_during_secret_resolution,
                env_override=self.env)

        self.resolved_env, self.failed_env_names = \
                self.env_resolver.fetch_and_resolve_env()

        self.initialize_fields(mutable_only=False)

    def initialize_fields(self, mutable_only: bool = True) -> None:
        resolved_env = self.resolved_env
        args = self.args

        self.offline_mode = string_to_bool(
            resolved_env.get('PROC_WRAPPER_OFFLINE_MODE'),
            default_value=args.offline_mode) or False

        self.prevent_offline_execution = string_to_bool(
                resolved_env.get('PROC_WRAPPER_PREVENT_OFFLINE_EXECUTION'),
                default_value=args.prevent_offline_execution) or False

        if self.offline_mode and self.prevent_offline_execution:
            _logger.critical('Offline mode and offline execution prevention cannot both be enabled.')
            return self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)

        self.deployment = coalesce(resolved_env.get('PROC_WRAPPER_DEPLOYMENT'),
                args.deployment)

        self.rollbar_access_token = resolved_env.get('PROC_WRAPPER_ROLLBAR_ACCESS_TOKEN')

        if self.rollbar_access_token:
            self.rollbar_retries = string_to_int(
                    resolved_env.get('PROC_WRAPPER_ROLLBAR_RETRIES'),
                    default_value=coalesce(args.rollbar_retries,
                            DEFAULT_ROLLBAR_RETRIES))

            self.rollbar_retry_delay = string_to_int(
                    resolved_env.get('PROC_WRAPPER_ROLLBAR_RETRY_DELAY_SECONDS'),
                    default_value=coalesce(args.rollbar_retry_delay,
                            DEFAULT_ROLLBAR_RETRY_DELAY_SECONDS))

            self.rollbar_timeout = string_to_int(
                    resolved_env.get('PROC_WRAPPER_ROLLBAR_TIMEOUT_SECONDS'),
                    default_value=coalesce(args.rollbar_timeout,
                            DEFAULT_ROLLBAR_TIMEOUT_SECONDS))
        else:
            self.rollbar_retries = None
            self.rollbar_timeout = None

        self.rollbar_retries_exhausted = False

        if not mutable_only:
            self.task_version_number = resolved_env.get(
                    'PROC_WRAPPER_TASK_VERSION_NUMBER',
                    args.task_version_number)
            self.task_version_text = resolved_env.get(
                    'PROC_WRAPPER_TASK_VERSION_TEXT',
                    args.task_version_text)
            self.task_version_signature = resolved_env.get(
                    'PROC_WRAPPER_TASK_VERSION_SIGNATURE',
                    args.task_version_signature)

        override_is_service: Optional[bool] = None
        if not mutable_only and not self.offline_mode:
            task_overrides_str = resolved_env.get('PROC_WRAPPER_AUTO_CREATE_TASK_PROPS',
                args.auto_create_task_props)

            task_overrides_loaded = False
            if task_overrides_str:
                try:
                    self.auto_create_task_overrides = \
                            json.loads(task_overrides_str)
                    task_overrides_loaded = True
                except json.JSONDecodeError:
                    _logger.warning(f"Failed to parse Task props: '{task_overrides_str}', ensure it is valid JSON.")

            self.auto_create_task = string_to_bool(
                    resolved_env.get('PROC_WRAPPER_AUTO_CREATE_TASK'),
                    default_value=coalesce(args.auto_create_task,
                    self.auto_create_task_overrides.get('was_auto_created'))) \
                    or task_overrides_loaded

            if self.auto_create_task:
                override_run_env = self.auto_create_task_overrides.get('run_environment', {})

                self.auto_create_task_run_environment_uuid = resolved_env.get(
                        'PROC_WRAPPER_AUTO_CREATE_TASK_RUN_ENVIRONMENT_UUID',
                        override_run_env.get('uuid',
                        args.auto_create_task_run_environment_uuid))

                self.auto_create_task_run_environment_name = coalesce(
                        resolved_env.get('PROC_WRAPPER_AUTO_CREATE_TASK_RUN_ENVIRONMENT_NAME'),
                        override_run_env.get('name'),
                        args.auto_create_task_run_environment_name)

                if (not self.auto_create_task_run_environment_name) \
                        and (not self.auto_create_task_run_environment_uuid):
                    if self.deployment:
                        self.auto_create_task_run_environment_name = self.deployment
                    else:
                        _logger.critical('No Run Environment UUID or name for auto-created Task specified, exiting.')
                        self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
                        return None

                args_forced_passive = None if (args.force_task_active is None) \
                        else (not args.force_task_active)

                self.task_is_passive = cast(bool, string_to_bool(
                        resolved_env.get('PROC_WRAPPER_TASK_IS_PASSIVE'),
                        default_value=coalesce(
                                self.auto_create_task_overrides.get('passive'),
                                args_forced_passive, self.auto_create_task)))

                if not self.task_is_passive:
                    runtime_metadata = self.load_runtime_metadata()

                    if (runtime_metadata is None) or \
                            (runtime_metadata.execution_method_capability is None):
                        _logger.critical('Task may not be active unless execution method capability can be determined.')
                        self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
                        return None

                em_overrides_str = resolved_env.get(
                        'PROC_WRAPPER_EXECUTION_METHOD_PROPS',
                        args.execution_method_props)

                if em_overrides_str:
                    try:
                        self.execution_method_overrides = \
                                json.loads(em_overrides_str)
                    except json.JSONDecodeError:
                        _logger.warning(f"Failed to parse auto-create task execution props: '{em_overrides_str}', ensure it is valid JSON.")

            self.task_execution_uuid = resolved_env.get(
                    'PROC_WRAPPER_TASK_EXECUTION_UUID',
                    args.task_execution_uuid)

            self.task_uuid = resolved_env.get('PROC_WRAPPER_TASK_UUID',
                    self.auto_create_task_overrides.get('uuid',
                    args.task_uuid))
            self.task_name = resolved_env.get('PROC_WRAPPER_TASK_NAME',
                    self.auto_create_task_overrides.get('name',
                    args.task_name))

            if (not self.task_uuid) and (not self.task_name):
                _logger.critical('No Task UUID or name specified, exiting.')
                self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
                return None

            count = self.auto_create_task_overrides.get('min_service_instance_count')
            override_is_service = None if count is None else (count > 0)

            api_base_url = resolved_env.get('PROC_WRAPPER_API_BASE_URL') \
                    or args.api_base_url or DEFAULT_API_BASE_URL
            self.api_base_url = api_base_url.rstrip('/')

        if not mutable_only:
            self.task_is_service = string_to_bool(
                resolved_env.get('PROC_WRAPPER_TASK_IS_SERVICE'),
                default_value=coalesce(override_is_service, args.service))

            self.max_concurrency = string_to_int(
                    resolved_env.get('PROC_WRAPPER_TASK_MAX_CONCURRENCY'),
                    negative_value=-1)

            if self.max_concurrency is None:
                if 'max_concurrency' in self.auto_create_task_overrides:
                    # May be None, if so, keep it that way
                    self.max_concurrency = self.auto_create_task_overrides['max_concurrency']
                else:
                    self.max_concurrency = args.max_concurrency

            self.is_concurrency_limited_service = self.task_is_service \
                    and (self.max_concurrency is not None) \
                    and (self.max_concurrency > 0)

        # API key and timeouts can be refreshed, so no mutable check
        if not self.offline_mode:
            self.api_key = resolved_env.get('PROC_WRAPPER_API_KEY', args.api_key)

            if not self.api_key:
                _logger.critical('No API key specified, exiting.')
                self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
                return None

            self.api_error_timeout = cast(int, string_to_int(
                    resolved_env.get('PROC_WRAPPER_API_ERROR_TIMEOUT_SECONDS'),
                    default_value=coalesce(args.api_error_timeout,
                    DEFAULT_API_ERROR_TIMEOUT_SECONDS)))

            self.api_task_execution_creation_error_timeout = string_to_int(
                    resolved_env.get('PROC_WRAPPER_API_TASK_CREATION_ERROR_TIMEOUT_SECONDS'),
                    default_value=coalesce(
                            args.api_task_execution_creation_error_timeout,
                            DEFAULT_API_TASK_EXECUTION_CREATION_TIMEOUT_SECONDS))

            default_task_execution_creation_conflict_timeout = 0
            default_task_execution_creation_conflict_retry_delay = DEFAULT_API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY_SECONDS
            if self.is_concurrency_limited_service:
                default_task_execution_creation_conflict_timeout = \
                        DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_CREATION_TIMEOUT_SECONDS
                default_task_execution_creation_conflict_retry_delay = \
                        DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_CREATION_CONFLICT_RETRY_DELAY_SECONDS

            self.api_task_execution_creation_conflict_timeout = string_to_int(
                    resolved_env.get('PROC_WRAPPER_API_TASK_EXECUTION_CREATION_CONFLICT_TIMEOUT_SECONDS'),
                    default_value=coalesce(
                            args.api_task_execution_creation_conflict_timeout,
                            default_task_execution_creation_conflict_timeout))

            self.api_final_update_timeout = string_to_int(
                    resolved_env.get('PROC_WRAPPER_API_FINAL_UPDATE_TIMEOUT_SECONDS'),
                    default_value=coalesce(args.api_final_update_timeout,
                            DEFAULT_API_FINAL_UPDATE_TIMEOUT_SECONDS))

            self.api_retry_delay = cast(int, string_to_int(
                    resolved_env.get('PROC_WRAPPER_API_RETRY_DELAY_SECONDS'),
                    default_value=coalesce(args.api_retry_delay,
                            DEFAULT_API_RETRY_DELAY_SECONDS),
                    negative_value=0))

            self.api_resume_delay = cast(int, string_to_int(
                    resolved_env.get('PROC_WRAPPER_API_RESUME_DELAY_SECONDS'),
                    default_value=coalesce(args.api_resume_delay,
                            DEFAULT_API_RESUME_DELAY_SECONDS)))

            self.api_task_execution_creation_conflict_retry_delay = string_to_int(
                    resolved_env.get('PROC_WRAPPER_API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY_SECONDS'),
                    default_value=args.api_task_execution_creation_conflict_retry_delay) \
                    or default_task_execution_creation_conflict_retry_delay

            self.api_request_timeout = string_to_int(
                    resolved_env.get('PROC_WRAPPER_API_REQUEST_TIMEOUT_SECONDS'),
                    default_value=coalesce(args.api_request_timeout,
                            DEFAULT_API_REQUEST_TIMEOUT_SECONDS))

        other_instance_metadata_str = coalesce(
                resolved_env.get('PROC_WRAPPER_TASK_INSTANCE_METADATA'),
                args.task_instance_metadata)

        self.other_instance_metadata = None
        if other_instance_metadata_str:
            try:
                self.other_instance_metadata = json.loads(other_instance_metadata_str)
            except Exception:
                _logger.exception(f"Failed to parse instance metadata: '{other_instance_metadata_str}'")

        self.in_pytest = string_to_bool(os.environ.get('IN_PYTEST')) or False

        # Now we have enough info to try to send errors if problems happen below.

        if (not self.exit_handler_installed) and (not self._embedded_mode) \
                and (not self.in_pytest):
            atexit.register(_exit_handler, self)

            # The function registered with atexit.register() isn't called when python receives
            # most signals, include SIGTERM. So register a signal handler which will cause the
            # program to exit with an error, triggering the exit handler.
            signal.signal(signal.SIGTERM, _signal_handler)

            self.exit_handler_installed = True

        if not self.offline_mode:
            self.send_pid = string_to_bool(
                    resolved_env.get('PROC_WRAPPER_SEND_PID'),
                    default_value=args.send_pid) or False

            self.send_hostname = string_to_bool(
                    resolved_env.get('PROC_WRAPPER_SEND_HOSTNAME'),
                    default_value=args.send_hostname) or False

            self.send_runtime_metadata = string_to_bool(
                    resolved_env.get('PROC_WRAPPER_SEND_RUNTIME_METADATA'),
                    default_value=not coalesce(args.no_send_runtime_metadata, False)) \
                    or False

        env_process_timeout_seconds = resolved_env.get('PROC_WRAPPER_PROCESS_TIMEOUT_SECONDS')

        if self.task_is_service:
            if (args.process_timeout is not None) \
                    and ((string_to_int(args.process_timeout) or 0) > 0):
                _logger.warning(
                        'Ignoring argument --process-timeout since Task is a service')

            if env_process_timeout_seconds \
                    and ((string_to_int(env_process_timeout_seconds) or 0) > 0):
                _logger.warning(
                        'Ignoring environment variable PROC_WRAPPER_PROCESS_TIMEOUT_SECONDS since Task is a service')
        else:
            self.process_timeout = string_to_int(
                    env_process_timeout_seconds,
                    default_value=args.process_timeout)

        self.process_max_retries = string_to_int(
                resolved_env.get('PROC_WRAPPER_TASK_MAX_RETRIES'),
                default_value=coalesce(args.process_max_retries, 0))

        self.process_retry_delay = cast(int, string_to_int(
                resolved_env.get('PROC_WRAPPER_PROCESS_RETRY_DELAY_SECONDS'),
                default_value=coalesce(args.process_retry_delay,
                        DEFAULT_PROCESS_RETRY_DELAY_SECONDS),
                negative_value=0))

        self.process_termination_grace_period_seconds = cast(int, string_to_int(
                resolved_env.get('PROC_WRAPPER_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS'),
                default_value=coalesce(args.process_termination_grace_period,
                        DEFAULT_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS),
                negative_value=0))

        default_heartbeat_interval = DEFAULT_API_HEARTBEAT_INTERVAL_SECONDS

        if self.is_concurrency_limited_service:
            default_heartbeat_interval = DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_HEARTBEAT_INTERVAL_SECONDS

        self.api_heartbeat_interval = string_to_int(
                resolved_env.get('PROC_WRAPPER_API_HEARTBEAT_INTERVAL_SECONDS'),
                default_value=coalesce(args.api_heartbeat_interval,
                        default_heartbeat_interval))

        default_max_conflicting_age_seconds = None

        if self.task_is_service and self.api_heartbeat_interval:
            default_max_conflicting_age_seconds = self.api_heartbeat_interval + HEARTBEAT_DELAY_TOLERANCE_SECONDS

        self.max_conflicting_age = string_to_int(
                resolved_env.get('PROC_WRAPPER_MAX_CONFLICTING_AGE_SECONDS'),
                default_value=coalesce(args.max_conflicting_age,
                        default_max_conflicting_age_seconds))

        self.status_update_interval = string_to_int(
                resolved_env.get('PROC_WRAPPER_STATUS_UPDATE_INTERVAL_SECONDS'),
                default_value=args.status_update_interval)

        # Properties to be reported to CloudRector
        self.schedule = coalesce(resolved_env.get('PROC_WRAPPER_SCHEDULE'),
                args.schedule, '')

        if not self._embedded_mode:
            self.process_check_interval = cast(int, string_to_int(
                    resolved_env.get('PROC_WRAPPER_PROCESS_CHECK_INTERVAL_SECONDS'),
                    default_value=coalesce(args.process_check_interval,
                            DEFAULT_PROCESS_CHECK_INTERVAL_SECONDS),
                    negative_value=-1))

            if self.process_check_interval <= 0:
                _logger.critical(f"Process check interval {self.process_check_interval} must be positive.")
                self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
                return None

            # TODO: allow command override if API key has developer permission
            if not args.command:
                _logger.critical('Command expected in wrapped mode, but not found')
                self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
                return None

            self.command = args.command
            self.working_dir = resolved_env.get('PROC_WRAPPER_WORK_DIR') \
                  or args.work_dir or '.'

            # We don't support changing the status update listener parameters
            if (not mutable_only) and (not self.offline_mode):
                enable_status_update_listener = string_to_bool(
                        resolved_env.get('PROC_WRAPPER_ENABLE_STATUS_UPDATE_LISTENER'),
                        default_value=coalesce(args.enable_status_update_listener, False))

                if enable_status_update_listener:
                    self.status_update_socket_port = string_to_int(coalesce(
                          resolved_env.get('PROC_WRAPPER_STATUS_UPDATE_SOCKET_PORT'),
                          args.status_update_socket_port),
                          default_value=DEFAULT_STATUS_UPDATE_SOCKET_PORT,
                          negative_value=DEFAULT_STATUS_UPDATE_SOCKET_PORT)
                    self._status_buffer = bytearray(self._STATUS_BUFFER_SIZE)
                    self._status_message_so_far = bytearray()
                    self.status_update_message_max_bytes = string_to_int(coalesce(
                            resolved_env.get('PROC_WRAPPER_STATUS_UPDATE_MESSAGE_MAX_BYTES'),
                            args.status_update_message_max_bytes),
                            default_value=DEFAULT_STATUS_UPDATE_MESSAGE_MAX_BYTES,
                            negative_value=DEFAULT_STATUS_UPDATE_MESSAGE_MAX_BYTES)

        self.log_configuration()
        return None

    def log_configuration(self):
        _logger.info(f"Task Execution UUID = {self.task_execution_uuid}")
        _logger.info(f"Task UUID = {self.task_uuid}")
        _logger.info(f"Task name = {self.task_name}")
        _logger.info(f"Deployment = '{self.deployment}'")

        _logger.info(f"Task version number = {self.task_version_number}")
        _logger.info(f"Task version text = {self.task_version_text}")
        _logger.info(f"Task version signature = {self.task_version_signature}")

        _logger.info(f"Execution method props = {self.execution_method_overrides}")

        _logger.info(f"Auto create task = {self.auto_create_task}")

        if self.auto_create_task:
            _logger.info(f"Auto create task Run Environment name = {self.auto_create_task_run_environment_name}")
            _logger.info(f"Auto create task Run Environment UUID = {self.auto_create_task_run_environment_uuid}")
            _logger.info(f"Auto create task props = {self.auto_create_task_overrides}")

        _logger.info(f"Passive task = {self.task_is_passive}")

        _logger.info(f"Task instance metadata = {self.other_instance_metadata}")
        _logger.debug(f"Wrapper version = {ProcWrapper.VERSION}")
        _logger.debug(f"Task is a service = {self.task_is_service}")
        _logger.debug(f"Max concurrency = {self.max_concurrency}")
        _logger.debug(f"Offline mode = {self.offline_mode}")
        _logger.debug(f"Prevent offline execution = {self.prevent_offline_execution}")
        _logger.debug(f"Process retries = {self.process_max_retries}")
        _logger.debug(f"Process retry delay = {self.process_retry_delay}")
        _logger.debug(f"Process check interval = {self.process_check_interval}")

        _logger.debug(f"Maximum age of conflicting processes = {self.max_conflicting_age}")

        if not self.offline_mode:
            _logger.debug(f"API base URL = '{self.api_base_url}'")

            if self.log_secrets:
                _logger.debug(f"API key = '{self.api_key}'")

            _logger.debug(f"API error timeout = {self.api_error_timeout}")
            _logger.debug(f"API retry delay = {self.api_retry_delay}")
            _logger.debug(f"API resume delay = {self.api_resume_delay}")
            _logger.debug(f"API Task Execution creation error timeout = {self.api_task_execution_creation_error_timeout}")
            _logger.debug(f"API Task Execution creation conflict timeout = {self.api_task_execution_creation_conflict_timeout}")
            _logger.debug(f"API Task Execution creation conflict retry delay = {self.api_task_execution_creation_conflict_retry_delay}")
            _logger.debug(f"API timeout for final update = {self.api_final_update_timeout}")
            _logger.debug(f"API request timeout = {self.api_request_timeout}")
            _logger.debug(f"API heartbeat interval = {self.api_heartbeat_interval}")

        if self.rollbar_access_token:
            if self.log_secrets:
                _logger.debug(f"Rollbar API key = '{self.rollbar_access_token}'")

            _logger.debug(f"Rollbar timeout = {self.rollbar_timeout}")
            _logger.debug(f"Rollbar retries = {self.rollbar_retries}")
            _logger.debug(f"Rollbar retry delay = {self.rollbar_retry_delay}")
        else:
            _logger.debug('Rollbar is disabled')

        if not self._embedded_mode:
            _logger.info(f"Command = {self.command}")
            _logger.info(f"Work dir = '{self.working_dir}'")

            enable_status_update_listener = (self.status_update_socket_port is not None)
            _logger.debug(f"Enable status listener = {enable_status_update_listener}")

            if enable_status_update_listener:
                _logger.debug(f"Status socket port = {self.status_update_socket_port}")
                _logger.debug(f"Status update message max bytes = {self.status_update_message_max_bytes}")

        _logger.debug(f"Status update interval = {self.status_update_interval}")

        _logger.debug(f"Resolved environment variable TTL = {self.resolved_env_ttl}")

    def print_final_status(self, exit_code: Optional[int],
            first_attempt_started_at: Optional[float],
            latest_attempt_started_at: Optional[float]):
        if latest_attempt_started_at is None:
            latest_attempt_started_at = first_attempt_started_at

        action = 'failed due to wrapping error'

        if exit_code == 0:
            action = 'succeeded'
        elif exit_code is not None:
            action = f'failed with exit code {exit_code}'
        elif self.timed_out:
            action = 'timed out'

        task_name = self.task_name or self.task_uuid or '[Unnamed]'
        now = datetime.now()
        now_ts = now.timestamp()
        max_attempts_str = 'infinity' if self.process_max_retries is None \
                else str(self.process_max_retries + 1)

        msg = f"Task '{task_name}' {action} " + \
          f"at {now.replace(microsecond=0).isoformat(sep=' ')} "

        if latest_attempt_started_at is not None:
            latest_duration = now_ts - latest_attempt_started_at
            msg += f'in {round(latest_duration)} seconds '

        msg += 'on attempt ' + \
          f"{self.attempt_count} / {max_attempts_str}"

        if first_attempt_started_at is not None:
            total_duration = now_ts - first_attempt_started_at
            msg += f', for a total duration of {round(total_duration)} seconds'

        msg += '.'

        print(msg)

    def load_runtime_metadata(self) -> Optional[RuntimeMetadata]:
        _logger.debug('Entering load_runtime_metadata() ...')

        # Don't refetch if we have already attempted previously
        if self.runtime_metadata or self.fetched_runtime_metadata_at:
            _logger.debug('Runtime metadata already fetched, returning existing metadata.')
            return self.runtime_metadata

        self.runtime_metadata = fetch_runtime_metadata(env=self.env)

        self.fetched_runtime_metadata_at = time.time()

        _logger.debug(f'Done loading runtime metadata, got {self.runtime_metadata}')
        return self.runtime_metadata

    @property
    def max_execution_attempts(self) -> float:
        if self.process_max_retries is None:
            return math.inf

        return self.process_max_retries + 1

    def request_task_execution_start(self) -> None:
        """
        Make a request to the API server to create a Task Execution
        for this Task. Retry and wait between requests if so configured
        and the maximum concurrency has already been reached.
        """

        if self.offline_mode:
            return

        if self.send_hostname and (self.hostname is None):
            try:
                self.hostname = socket.gethostname()
                _logger.debug(f"Hostname = '{self.hostname}'")
            except Exception:
                _logger.warning("Can't get hostname", exc_info=True)

        if self.send_runtime_metadata:
            self.load_runtime_metadata()

        try:
            url = f"{self.api_base_url}/api/v1/task_executions/"
            http_method = 'POST'
            headers = self._make_headers()

            common_body = {
                'is_service': self.task_is_service,
                'schedule': self.schedule,
                'heartbeat_interval_seconds': _encode_int(self.api_heartbeat_interval, empty_value=-1),
            }

            body = {
                'status': ProcWrapper.STATUS_RUNNING,
                'task_version_number': self.task_version_number,
                'task_version_text': self.task_version_text,
                'task_version_signature': self.task_version_signature,
                'process_timeout_seconds': self.process_timeout,
                'process_max_retries': _encode_int(self.process_max_retries,
                        empty_value=-1),
                'process_retry_delay_seconds': self.process_retry_delay,
                'task_max_concurrency': self.max_concurrency,
                'max_conflicting_age_seconds': self.max_conflicting_age,
                'prevent_offline_execution': self.prevent_offline_execution,
                'process_termination_grace_period_seconds': self.process_termination_grace_period_seconds,
                'wrapper_log_level': logging.getLevelName(_logger.getEffectiveLevel()),
                'wrapper_version': ProcWrapper.VERSION,
                'api_error_timeout_seconds': _encode_int(self.api_error_timeout, empty_value=-1),
                'api_retry_delay_seconds': _encode_int(self.api_retry_delay),
                'api_resume_delay_seconds': _encode_int(self.api_resume_delay),
                'api_task_execution_creation_error_timeout_seconds': _encode_int(
                        self.api_task_execution_creation_error_timeout,
                        empty_value=-1),
                'api_task_execution_creation_conflict_timeout_seconds': _encode_int(
                        self.api_task_execution_creation_conflict_timeout,
                        empty_value=-1),
                'api_task_execution_creation_conflict_retry_delay_seconds': _encode_int(
                        self.api_task_execution_creation_conflict_retry_delay),
                'api_final_update_timeout_seconds': _encode_int(
                        self.api_final_update_timeout, empty_value=-1),
                'api_request_timeout_seconds': _encode_int(self.api_request_timeout, empty_value=-1),
                'status_update_interval_seconds': _encode_int(self.status_update_interval,
                        empty_value=-1),
                'status_update_port': _encode_int(self.status_update_socket_port,
                        empty_value=-1),
                'status_update_message_max_bytes': _encode_int(self.status_update_message_max_bytes,
                        empty_value=-1),
                'embedded_mode': self._embedded_mode
            }
            body.update(common_body)

            if self.task_execution_uuid:
                # Manually started
                url += self.task_execution_uuid + '/'
                http_method = 'PATCH'
            else:
                task_dict = self.auto_create_task_overrides.copy()
                task_dict.update(common_body)

                if self.task_uuid:
                    task_dict['uuid'] = self.task_uuid
                elif self.task_name:
                    task_dict['name'] = self.task_name
                else:
                    # This method should not have been called at all.
                    raise RuntimeError('Neither Task UUID or Task name were set.')

                task_dict.update({
                    'max_concurrency': _encode_int(self.max_concurrency, empty_value=-1),
                    'was_auto_created': self.auto_create_task,
                    'passive': self.task_is_passive,
                })

                run_env_dict = {}

                if self.auto_create_task_run_environment_name:
                    run_env_dict['name'] = self.auto_create_task_run_environment_name

                if self.auto_create_task_run_environment_uuid:
                    run_env_dict['uuid'] = self.auto_create_task_run_environment_uuid

                task_dict['run_environment'] = run_env_dict

                task_emc: Optional[Dict[str, Any]] = None
                if self.send_runtime_metadata and self.runtime_metadata:
                    task_emc = self.runtime_metadata.execution_method_capability

                if self.auto_create_task_overrides:
                    override_emc = self.auto_create_task_overrides.get(
                           'execution_method_capability')
                    if override_emc:
                        task_emc = task_emc or {}
                        task_emc.update(override_emc)

                task_dict['execution_method_capability'] = task_emc or {
                    'type': 'Unknown'
                }

                body['task'] = task_dict
                body['max_conflicting_age_seconds'] = self.max_conflicting_age

            if self.command:
                body['process_command'] = ' '.join(self.command)

            if self.send_hostname and self.hostname:
                body['hostname'] = self.hostname

            if self.other_instance_metadata:
                body['other_instance_metadata'] = self.other_instance_metadata

            execution_method: Optional[Dict[str, Any]] = None
            if self.send_runtime_metadata and self.runtime_metadata:
                execution_method = self.runtime_metadata.execution_method

            if self.execution_method_overrides:
                execution_method = execution_method or {}
                execution_method.update(self.execution_method_overrides)

            if execution_method:
                body['execution_method'] = execution_method

            data = json.dumps(body).encode('utf-8')

            req = Request(url, data=data, headers=headers, method=http_method)

            f = self._send_api_request(req,
                    is_task_execution_creation_request=True)
            if f is None:
                _logger.warning('Task Execution creation request failed non-fatally')
            else:
                with f:
                    _logger.info("Task Execution creation request was successful.")

                    fd = f.read()

                    if not fd:
                        raise RuntimeError('Unexpected None result of reading Task Execution creation response')

                    response_body = fd.decode('utf-8')
                    _logger.debug(f"Got creation response '{response_body}'")

                    response_dict = json.loads(response_body)

                    if not self.task_execution_uuid:
                        self.task_execution_uuid = response_dict.get('uuid')

                    self.task_uuid = self.task_uuid or response_dict['task']['uuid']
                    self.task_name = self.task_name or response_dict['task'].get('name')

        except Exception as ex:
            _logger.exception('request_process_start_if_max_concurrency_ok() failed with exception')
            if self.prevent_offline_execution:
                raise ex

            _logger.info('Not preventing offline execution, so continuing')

    def update_status(self, failed_attempts=None, timed_out_attempts=None,
            pid=None, success_count=None, error_count=None,
            skipped_count=None, expected_count=None, last_status_message=None,
            extra_status_props=None) -> None:
        """Update the status of the process. Send to the process management
           server if the last status was sent more than status_update_interval
           seconds ago.
        """

        if not self.offline_mode:
            return

        if success_count is not None:
            self.status_dict['success_count'] = int(success_count)

        if error_count is not None:
            self.status_dict['error_count'] = int(error_count)

        if skipped_count is not None:
            self.status_dict['skipped_count'] = int(skipped_count)

        if expected_count is not None:
            self.status_dict['expected_count'] = int(expected_count)

        if last_status_message is not None:
            self.status_dict['last_status_message'] = last_status_message

        if extra_status_props:
            self.status_dict.update(extra_status_props)

        should_send = self.is_status_update_due()

        # These are important updates that should be sent no matter what.
        if not ((failed_attempts is None) and (timed_out_attempts is None)
                and (pid is None)) and (last_status_message is None):
            should_send = True

        if should_send:
            self.send_update(failed_attempts=failed_attempts,
                    timed_out_attempts=timed_out_attempts,
                    pid=pid)

    def send_completion(self, status: str,
                        failed_attempts: Optional[int] = None,
                        timed_out_attempts: Optional[int] = None,
                        exit_code: Optional[int] = None,
                        pid: Optional[int] = None) -> None:
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

        self.send_update(status=status, failed_attempts=failed_attempts,
                timed_out_attempts=timed_out_attempts,
                exit_code=exit_code, pid=pid)

    def managed_call(self, fun, data=None):
        """
        Call the argument object, which must be callable, doing retries as necessary,
        and wrapping with calls to the API server.
        """
        if not callable(fun):
            raise RuntimeError("managed_call() argument is not callable")

        if self.failed_env_names:
            _logger.critical(f'Failed to resolve one or more environment variables: {self.failed_env_names}')
            self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
            return

        self.request_task_execution_start()

        config, failed_var_names = self.env_resolver.fetch_and_resolve_config()

        self.attempt_count = 0
        self.failed_count = 0

        rv = None
        success = False
        saved_ex = None
        while self.attempt_count < self.max_execution_attempts:
            self.attempt_count += 1

            _logger.info(f"Calling managed function (attempt {self.attempt_count}/{self.max_execution_attempts}) ...")

            try:
                rv = fun(self, data, config)
                success = True
            except Exception as ex:
                saved_ex = ex
                self.failed_count += 1
                _logger.exception('Managed function failed')

                if self.attempt_count < self.max_execution_attempts:
                    self.send_update(failed_attempts=self.failed_count)

                    if self.process_retry_delay:
                        _logger.debug('Sleeping after managed function failed ...')
                        time.sleep(self.process_retry_delay)
                        _logger.debug('Done sleeping after managed function failed.')

                    if self.resolved_env_ttl is not None:
                        config, failed_var_names = \
                                self.env_resolver.fetch_and_resolve_config()

                        # In case API key(s) change
                        self.initialize_fields(mutable_only=True)

                        config = self.make_process_env()

            if success:
                self.send_completion(status=ProcWrapper.STATUS_SUCCEEDED,
                        failed_attempts=self.failed_count)
                return rv

        self.send_completion(status=ProcWrapper.STATUS_FAILED,
              failed_attempts=self.failed_count)

        raise saved_ex

    def run(self) -> None:
        """
        Run the wrapped process, first requesting to start, then executing
        the process command line, retying for errors and timeouts.
        """
        self._ensure_non_embedded_mode()

        if self.command is None:
            _logger.critical('Command is required to run in wrapped mode.')
            self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
            return

        if self.failed_env_names:
            _logger.critical(f'Failed to resolve one or more environment variables: {self.failed_env_names}')
            self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
            return

        if self.offline_mode:
            _logger.info('Starting in offline mode ...')
        else:
            self.request_task_execution_start()
            _logger.info(f"Created Task Execution {self.task_execution_uuid}, starting now.")

        process_env = self.make_process_env()

        if self.log_secrets:
            _logger.debug(f"Process environment = {process_env}")

        self.attempt_count = 0
        self.timeout_count = 0
        first_attempt_started_at: Optional[float] = None
        latest_attempt_started_at: Optional[float] = None
        exit_code: Optional[int] = None

        self._open_status_socket()

        while self.attempt_count < self.max_execution_attempts:
            self.attempt_count += 1
            self.timed_out = False

            _logger.info(f"Running process (attempt {self.attempt_count}/{self.max_execution_attempts}) ...")

            current_time = time.time()

            process_finish_deadline = math.inf
            if self.process_timeout:
                process_finish_deadline = current_time + self.process_timeout

            next_heartbeat_time = math.inf
            if self.api_heartbeat_interval:
                next_heartbeat_time = current_time + self.api_heartbeat_interval

            next_process_check_time = math.inf
            if self.process_check_interval:
                next_process_check_time = current_time + self.process_check_interval

            _logger.debug(f"command = {self.command}, cwd={self.working_dir}")

            if self.log_secrets:
                _logger.debug(f"process_env={process_env}")

            latest_attempt_started_at = time.time()
            if first_attempt_started_at is None:
                first_attempt_started_at = latest_attempt_started_at

            # Set the session ID so we can kill the process as a group, so we kill
            # all subprocesses. See https://stackoverflow.com/questions/4789837/how-to-terminate-a-python-subprocess-launched-with-shell-true
            # On Windows, os does not have setsid function.
            # TODO: allow non-shell mode
            self.process = Popen(' '.join(self.command), shell=True, stdout=None,
                    stderr=None, env=process_env, cwd=self.working_dir,
                    preexec_fn=getattr(os, 'setsid', None))

            pid = self.process.pid
            _logger.info(f"pid = {pid}")

            if pid and self.send_pid:
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
                            self.send_update(failed_attempts=self.failed_count,
                                              timed_out_attempts=self.timeout_count)

                        _logger.warning(f"Process timed out after {self.process_timeout} seconds, sending SIGTERM ...")
                        self._terminate_or_kill_process()
                    else:
                        if (self.process_check_interval is not None) \
                                and (current_time >= next_process_check_time):
                            next_process_check_time = current_time + self.process_check_interval

                        if self.api_heartbeat_interval:
                            if self.last_update_sent_at is not None:
                                next_heartbeat_time = max(next_heartbeat_time,
                                                          self.last_update_sent_at + self.api_heartbeat_interval)

                            if current_time >= next_heartbeat_time:
                                self.send_update()
                                current_time = time.time()
                                next_heartbeat_time = current_time + self.api_heartbeat_interval

                        sleep_until = min(process_finish_deadline or math.inf, next_heartbeat_time or math.inf,
                                          next_process_check_time)
                        sleep_duration = sleep_until - current_time

                        if sleep_duration > 0:
                            _logger.debug(f'Waiting {round(sleep_duration)} seconds while process is running ...')
                            try:
                                self.process.wait(sleep_duration)
                            except TimeoutExpired:
                                _logger.debug('Done waiting while process is running.')

                else:
                    _logger.info(f"Process exited with exit code {exit_code}")

                    done_polling = True

                    if exit_code == 0:
                        self.print_final_status(exit_code=0,
                                first_attempt_started_at=first_attempt_started_at,
                                latest_attempt_started_at=latest_attempt_started_at)
                        self._exit_or_raise(0)
                        return

                    self.failed_count += 1

                    if self.attempt_count >= self.max_execution_attempts:
                        self.print_final_status(exit_code=exit_code,
                                first_attempt_started_at=first_attempt_started_at,
                                latest_attempt_started_at=latest_attempt_started_at)
                        self._exit_or_raise(exit_code)
                        return

                    self.send_update(failed_attempts=self.failed_count,
                            exit_code=exit_code)

                    if self.process_retry_delay:
                        _logger.debug(f'Sleeping {self.process_retry_delay} seconds after process failed ...')
                        time.sleep(self.process_retry_delay)
                        _logger.debug('Done sleeping after process failed.')

                    if self.resolved_env_ttl is not None:
                        self.resolved_env, self.failed_env_names = \
                                  self.env_resolver.fetch_and_resolve_env()

                        # In case API key(s) change
                        self.initialize_fields(mutable_only=True)
                        process_env = self.make_process_env()

        if self.attempt_count >= self.max_execution_attempts:
            self.print_final_status(exit_code=exit_code,
                    first_attempt_started_at=first_attempt_started_at,
                    latest_attempt_started_at=latest_attempt_started_at)

            self._exit_or_raise(self._EXIT_CODE_GENERIC_ERROR)
        else:
            self.send_update(failed_attempts=self.attempt_count)

        # Exit handler will send update to API server

    def handle_exit(self) -> None:
        _logger.debug(f"Exit handler, Task Execution UUID = {self.task_execution_uuid}")

        if self.called_exit:
            _logger.debug('Called exit already, returning early')
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
            _logger.exception('Exception in process termination')
        finally:
            try:
                if self.task_execution_uuid:
                    if self.was_conflict:
                        _logger.debug('Task execution update conflict detected, not notifying because server-side should have notified already.')
                        notification_required = False
                        status = self.STATUS_ABORTED

                    if not self.offline_mode and not self.api_server_retries_exhausted:
                        self.send_completion(status=status,
                                             failed_attempts=self.failed_count,
                                             timed_out_attempts=self.timeout_count,
                                             exit_code=exit_code, pid=pid)
                        notification_required = False
            except Exception:
                _logger.exception('Exception in final update')
            finally:
                self._close_status_socket()

        if notification_required:
            error_message = 'API Server not configured'

            if self.api_server_retries_exhausted:
                error_message = 'API Server not responding properly'
            elif not self.task_execution_uuid:
                error_message = 'Task Execution ID not assigned'

            self._report_error(error_message, self.last_api_request_data)

    def _terminate_or_kill_process(self) -> Optional[int]:
        self._ensure_non_embedded_mode()

        if (not self.process) or (not self.process.pid):
            _logger.info('No process found, not terminating')
            return None

        pid = self.process.pid

        exit_code = self.process.poll()
        if exit_code is not None:
            _logger.info(f"Process {pid} already exited with code {exit_code}, not terminating")
            return exit_code

        _logger.warning('Sending SIGTERM ...')

        os.killpg(os.getpgid(pid), signal.SIGTERM)

        try:
            self.process.communicate(timeout=self.process_termination_grace_period_seconds)

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
        if self._embedded_mode:
            raise RuntimeError('Called method is only for wrapped process (non-embedded) mode')

    def _open_status_socket(self) -> Optional[socket.socket]:
        if self.offline_mode or (self.status_update_socket_port is None):
            _logger.info('Not opening status socket.')
            return None

        try:
            _logger.info('Opening status update socket ...')
            self._status_socket = socket.socket(socket.AF_INET,
                    socket.SOCK_DGRAM)
            self._status_socket.bind(('127.0.0.1', self.status_update_socket_port))
            self._status_socket.setblocking(False)
            _logger.info('Successfully created status update socket')
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
                end_index = self._status_buffer.find(b'\n', start_index, nbytes)

                end_index_for_copy = end_index
                if end_index < 0:
                    end_index_for_copy = nbytes

                bytes_to_copy = end_index_for_copy - start_index
                if len(self._status_buffer) + bytes_to_copy > self.status_update_message_max_bytes:
                    _logger.warning(f"Discarding status message which exceeded maximum size: {self._status_buffer}")
                    self._status_buffer.clear()
                else:
                    self._status_message_so_far.extend(self._status_buffer[start_index:end_index_for_copy])
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
            message = self._status_message_so_far.decode('utf-8')

            _logger.debug(f"Got status message '{message}'")

            try:
                self.status_dict.update(json.loads(message))

                if self.is_status_update_due():
                    self.send_update()
            except json.JSONDecodeError:
                _logger.debug('Error decoding JSON, this can happen due to missing out of order messages')
        except UnicodeDecodeError:
            _logger.debug('Error decoding message as UTF-8, this can happen due to missing out of order messages')
        finally:
            self._status_message_so_far.clear()

    def is_status_update_due(self) -> bool:
        return (self.last_update_sent_at is None) \
                or ((self.status_update_interval is not None)
                and (time.time() - self.last_update_sent_at > self.status_update_interval))

    def _make_headers(self) -> Dict[str, str]:
        headers = {
            'Authorization': f"Token {self.api_key}",
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        return headers

    def make_process_env(self) -> Dict[str, str]:
        process_env = self.resolved_env.copy()

        if self.deployment:
            process_env['PROC_WRAPPER_DEPLOYMENT'] = self.deployment

        process_env['PROC_WRAPPER_OFFLINE_MODE'] = str(self.offline_mode).upper()

        if not self.offline_mode:
            process_env['PROC_WRAPPER_API_BASE_URL'] = self.api_base_url
            process_env['PROC_WRAPPER_API_KEY'] = str(self.api_key)
            process_env['PROC_WRAPPER_API_ERROR_TIMEOUT_SECONDS'] = \
                    str(_encode_int(self.api_error_timeout, empty_value=-1))
            process_env['PROC_WRAPPER_API_RETRY_DELAY_SECONDS'] = \
                    str(self.api_retry_delay)
            process_env['PROC_WRAPPER_API_RESUME_DELAY_SECONDS'] = \
                    str(_encode_int(self.api_resume_delay))
            process_env['PROC_WRAPPER_API_REQUEST_TIMEOUT_SECONDS'] = \
                    str(_encode_int(self.api_request_timeout, empty_value=-1))

            enable_status_update_listener = (self.status_update_socket_port is not None)
            process_env['PROC_WRAPPER_ENABLE_STATUS_UPDATE_LISTENER'] = \
                    str(enable_status_update_listener).upper()
            if enable_status_update_listener:
                process_env['PROC_WRAPPER_STATUS_UPDATE_SOCKET_PORT'] = \
                    str(self.status_update_socket_port)
                process_env['PROC_WRAPPER_STATUS_UPDATE_INTERVAL_SECONDS'] = \
                    str(self.status_update_interval)
                process_env['PROC_WRAPPER_STATUS_UPDATE_MESSAGE_MAX_BYTES'] = \
                    str(self.status_update_message_max_bytes)

            process_env['PROC_WRAPPER_TASK_EXECUTION_UUID'] = str(self.task_execution_uuid)

            if self.task_uuid:
                process_env['PROC_WRAPPER_TASK_UUID'] = self.task_uuid

            if self.task_name:
                process_env['PROC_WRAPPER_TASK_NAME'] = self.task_name

        if self.rollbar_access_token:
            process_env['PROC_WRAPPER_ROLLBAR_ACCESS_TOKEN'] = str(self.rollbar_access_token)
            process_env['PROC_WRAPPER_ROLLBAR_TIMEOUT'] = str(self.rollbar_timeout)
            process_env['PROC_WRAPPER_ROLLBAR_RETRIES'] = str(self.rollbar_retries)
            process_env['PROC_WRAPPER_ROLLBAR_RETRY_DELAY_SECONDS'] = str(self.rollbar_retry_delay)

        if self.task_version_number:
            process_env['PROC_WRAPPER_TASK_VERSION_NUMBER'] = self.task_version_number

        if self.task_version_text:
            process_env['PROC_WRAPPER_TASK_VERSION_TEXT'] = self.task_version_text

        if self.task_version_signature:
            process_env['PROC_WRAPPER_TASK_VERSION_SIGNATURE'] = self.task_version_signature

        if self.other_instance_metadata:
            process_env['PROC_WRAPPER_TASK_INSTANCE_METADATA'] = \
                json.dumps(self.other_instance_metadata)

        process_env['PROC_WRAPPER_PROCESS_TIMEOUT_SECONDS'] = str(_encode_int(
                self.process_timeout, empty_value=-1))
        process_env['PROC_WRAPPER_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS'] = str(
            self.process_termination_grace_period_seconds)

        process_env['PROC_WRAPPER_MAX_CONCURRENCY'] = str(_encode_int(
              self.max_concurrency, empty_value=-1))

        process_env['PROC_WRAPPER_PREVENT_OFFLINE_EXECUTION'] = \
                str(self.prevent_offline_execution).upper()

        return process_env

    def send_update(self, status: Optional[str] = None,
            failed_attempts: Optional[int] = None,
            timed_out_attempts: Optional[int] = None,
            exit_code: Optional[int] = None,
            pid: Optional[int] = None,
            extra_props: Optional[Mapping[str, Any]] = None) -> None:
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
            _logger.debug('_send_update() skipping update since no Task Execution UUID was available')
            return

        if status is None:
            status = self.STATUS_RUNNING

        body: Dict[str, Any] = {
            'status': status
        }

        body.update(self.status_dict)

        if failed_attempts is not None:
            body['failed_attempts'] = failed_attempts

        if timed_out_attempts is not None:
            body['timed_out_attempts'] = timed_out_attempts

        if exit_code is not None:
            body['exit_code'] = exit_code

        if pid is not None:
            body['pid'] = pid

        if extra_props:
            body['other_runtime_metadata'] = extra_props

        url = f"{self.api_base_url}/api/v1/task_executions/{quote_plus(str(self.task_execution_uuid))}/"
        headers = self._make_headers()
        text_data = json.dumps(body)
        data = text_data.encode('utf-8')
        is_final_update = (status != self.STATUS_RUNNING)

        _logger.debug(f"Sending update '{text_data}' ...")

        req = Request(url, data=data, headers=headers, method='PATCH')
        f = self._send_api_request(req, is_final_update=is_final_update)
        if f is None:
            _logger.debug('Update request failed non-fatally')
        else:
            with f:
                _logger.debug('Update sent successfully.')
                self.last_update_sent_at = time.time()
                self.status_dict = {}

    def _compute_successful_request_deadline(self,
            first_attempt_at: float,
            is_task_execution_creation_request: bool = False,
            for_task_execution_creation_conflict: bool = False,
            is_final_update: bool = False) -> Optional[float]:
        timeout: Optional[int] = self.api_error_timeout

        if is_task_execution_creation_request:
            if for_task_execution_creation_conflict:
                timeout = self.api_task_execution_creation_conflict_timeout
            else:
                timeout = self.api_task_execution_creation_error_timeout
        elif is_final_update:
            timeout = self.api_final_update_timeout

        if timeout is not None:
            return first_attempt_at + timeout

        return None

    def _send_api_request(self, req: Request,
            is_task_execution_creation_request: bool = False,
            is_final_update: bool = False) -> \
            Optional[RawIOBase]:
        _logger.debug(f"Sending {req.method} request to {req.full_url} ....")

        self._refresh_api_server_retries_exhausted()

        if self.api_server_retries_exhausted:
            _logger.debug('Not sending API request because all retries are exhausted')
            return None

        first_attempt_at = time.time()
        deadline = self._compute_successful_request_deadline(
                first_attempt_at=first_attempt_at,
                is_task_execution_creation_request=is_task_execution_creation_request,
                is_final_update=is_final_update)

        attempt_count = 0

        api_request_data: Dict[str, Any] = {
            'request': {
                'url': req.full_url,
                'method': req.method,
                'body': str(req.data)
            }
        }

        status_code: Optional[int] = None

        while (deadline is None) or (time.time() < deadline):
            attempt_count += 1
            retry_delay = self.api_retry_delay

            _logger.info(f"Sending API request (attempt {attempt_count}) ...")

            try:
                resp = urlopen(req, timeout=self.api_request_timeout)
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

                api_request_data.pop('error', None)
                api_request_data['response'] = {
                    'status_code': status_code,
                    'body': response_body
                }

                if status_code in self._RETRYABLE_HTTP_STATUS_CODES:
                    if not self.last_api_request_failed_at:
                        deadline = self._compute_successful_request_deadline(
                                first_attempt_at=first_attempt_at,
                                is_task_execution_creation_request=is_task_execution_creation_request,
                                for_task_execution_creation_conflict=False,
                                is_final_update=is_final_update)

                    self.last_api_request_failed_at = time.time()

                    error_message = f"Endpoint temporarily not available, status code = {status_code}"
                    _logger.warning(error_message)

                    self._report_error(error_message, api_request_data)
                elif status_code == 409:
                    if is_task_execution_creation_request:
                        if not self.was_conflict:
                            self.was_conflict = True
                            deadline = self._compute_successful_request_deadline(first_attempt_at,
                                    is_task_execution_creation_request=True,
                                    for_task_execution_creation_conflict=True)
                        retry_delay = self.api_task_execution_creation_conflict_retry_delay

                        _logger.info('Got response code = 409 during Task Execution creation')
                    else:
                        self.last_api_request_failed_at = time.time()
                        _logger.error(
                                'Got response code 409 after Task Execution started, exiting.')
                        self.was_conflict = True
                        exit_code = self._RESPONSE_CODE_TO_EXIT_CODE.get(status_code,
                                self._EXIT_CODE_GENERIC_ERROR)
                        self._exit_or_raise(exit_code)
                        return None
                else:
                    self.last_api_request_failed_at = time.time()

                    error_message = f"Got error response code = {status_code}"
                    _logger.error(error_message)

                    self._report_error(error_message, api_request_data)

                    if self.prevent_offline_execution:
                        _logger.critical(f"Response code = {status_code}, exiting since we are preventing offline execution.")
                        self.last_api_request_data = api_request_data
                        exit_code = self._RESPONSE_CODE_TO_EXIT_CODE.get(status_code, self._EXIT_CODE_GENERIC_ERROR)
                        self._exit_or_raise(exit_code)
                        return None

                    _logger.warning(f"Response code = {status_code}, but continuing since we are allowing offline execution.")
                    return None
            except Exception as ex:
                if not self.last_api_request_failed_at:
                    deadline = self._compute_successful_request_deadline(
                            first_attempt_at=first_attempt_at,
                            is_task_execution_creation_request=is_task_execution_creation_request,
                            for_task_execution_creation_conflict=False,
                            is_final_update=is_final_update)

                self.last_api_request_failed_at = time.time()

                api_request_data.pop('response', None)

                if isinstance(ex, URLError):
                    error_message = f"URL error: {ex}"
                    api_request_data['error'] = {
                        'reason': str(ex.reason)
                    }
                    _logger.error(error_message)
                else:
                    error_message = f"Unhandled exception: {ex}"
                    _logger.exception(error_message)

                self._report_error(error_message, api_request_data)

            if (deadline is None) or (time.time() < deadline):
                _logger.debug(f"Sleeping {retry_delay} seconds after request error ...")
                time.sleep(retry_delay)
                _logger.debug('Done sleeping after request error.')

        if is_task_execution_creation_request \
                and (self.prevent_offline_execution or self.was_conflict):
            _logger.critical(
                    'Exiting because Task Execution creation timed out and offline execution is prevented or there was a conflict.')
            exit_code = self._RESPONSE_CODE_TO_EXIT_CODE.get(status_code or 0,
                    self._EXIT_CODE_GENERIC_ERROR)
            self._exit_or_raise(exit_code)

        _logger.error('Exhausted retry timeout, not sending any more API requests.')
        return None

    def _refresh_api_server_retries_exhausted(self) -> bool:
        if not self.api_server_retries_exhausted:
            return False

        if self.last_api_request_failed_at is None:
            self.api_server_retries_exhausted = False
        else:
            elapsed_time = time.time() - self.last_api_request_failed_at

            if elapsed_time > self.api_resume_delay:
                _logger.info(f"Resuming API requests after {int(elapsed_time)} seconds after the last bad request")
                self.api_server_retries_exhausted = False

        return self.api_server_retries_exhausted

    def _exit_or_raise(self, exit_code) -> None:
        if self._embedded_mode:
            raise RuntimeError(f"Raising an error in embedded mode, exit code {exit_code}")

        if self.in_pytest:
            self.handle_exit()
        else:
            if self.called_exit:
                raise RuntimeError(
                    f"exit() called already; raising exception instead of exiting with exit code {exit_code}")

            sys.exit(exit_code)

    def _report_error(self, message: str, data: Optional[Dict[str, Any]]) -> int:
        num_sinks_successful = 0
        if self.rollbar_access_token:
            if self._send_rollbar_error(message, data):
                num_sinks_successful += 1

        if num_sinks_successful == 0:
            _logger.info("Can't notify any valid error sink!")

        return num_sinks_successful

    def _send_rollbar_error(self, message: str, data=None, level='error') -> bool:
        if not self.rollbar_access_token:
            _logger.warning(f"Not sending '{message}' to Rollbar since no access token found")
            return False

        if self.rollbar_retries_exhausted:
            _logger.debug(f"Not sending '{message}' to Rollbar since all retries are exhausted")
            return False

        payload = {
            'access_token': self.rollbar_access_token,
            'data': {
                'environment': self.deployment or 'Unknown',
                'body': {
                    'message': {
                        'body': message,
                        'task': {
                            'uuid': self.task_uuid,
                            'name': self.task_name,
                            'version_number': self.task_version_number,
                            'version_text': self.task_version_text,
                            'version_signature': self.task_version_signature,
                        },
                        'task_execution': {
                            'uuid': self.task_execution_uuid,
                        },
                        'other_instance_metadata': self.other_instance_metadata,
                        'wrapper_version': ProcWrapper.VERSION,
                        'was_conflict': self.was_conflict,
                        'api_server_retries_exhausted': self.api_server_retries_exhausted,
                        'data': data
                    }
                },
                'level': level,
                'server': {
                    'host': self.hostname
                    # Could put code version here
                }
            }
        }

        request_body = json.dumps(payload).encode('utf-8')
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        req = Request('https://api.rollbar.com/api/1/item/', data=request_body,
                headers=headers, method='POST')

        attempt_count = 0

        if self.rollbar_retries is None:
            max_attempts = -1
        else:
            max_attempts = self.rollbar_retries + 1

        while (max_attempts < 0) or (attempt_count < max_attempts):
            attempt_count += 1

            _logger.info(f"Sending Rollbar request attempt {attempt_count}/{max_attempts}) ...")

            try:
                with urlopen(req, timeout=self.rollbar_timeout) as f:
                    response_body = f.read().decode('utf-8')
                    _logger.debug(f"Got Rollbar response '{response_body}'")

                    response_dict = json.loads(response_body)
                    uuid = response_dict['result']['uuid']

                    _logger.debug(f"Rollbar request returned UUID {uuid}.")

                    return True
            except HTTPError as http_error:
                status_code = http_error.code
                _logger.critical(f"Rollbar response code = {status_code}, giving up.")
                return False
            except URLError as url_error:
                _logger.error(f"Rollbar URL error: {url_error}")

                if self.rollbar_retry_delay:
                    _logger.debug('Sleeping after Rollbar request error ...')
                    time.sleep(self.rollbar_retry_delay)
                    _logger.debug('Done sleeping after Rollbar request error.')

        self.rollbar_retries_exhausted = True
        _logger.error("Exhausted all retries, giving up.")
        return False
