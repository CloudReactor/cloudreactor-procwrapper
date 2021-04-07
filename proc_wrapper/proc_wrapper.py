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

import argparse
import atexit
from datetime import datetime
import json
import logging
import math
import os
import signal
import socket
import sys
import time
from http import HTTPStatus
from io import RawIOBase
from subprocess import Popen, TimeoutExpired
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Tuple, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

_DEFAULT_LOG_LEVEL = 'WARNING'

HEARTBEAT_DELAY_TOLERANCE_SECONDS = 60

DEFAULT_API_BASE_URL = 'https://api.cloudreactor.io'
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

DEFAULT_STATUS_UPDATE_SOCKET_PORT = 2373

DEFAULT_ROLLBAR_TIMEOUT_SECONDS = 30
DEFAULT_ROLLBAR_RETRIES = 2
DEFAULT_ROLLBAR_RETRY_DELAY_SECONDS = 120

DEFAULT_TASK_NAME = '[Unnamed Task]'
DEFAULT_PROCESS_TIMEOUT_SECONDS = None
DEFAULT_PROCESS_CHECK_INTERVAL_SECONDS = 10
DEFAULT_PROCESS_RETRY_DELAY_SECONDS = 60
DEFAULT_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS = 30

DEFAULT_STATUS_UPDATE_MESSAGE_MAX_BYTES = 64 * 1024

DEFAULT_RESOLVABLE_ENV_VAR_PREFIX = ''
DEFAULT_RESOLVABLE_ENV_VAR_SUFFIX = '_FOR_PROC_WRAPPER_TO_RESOLVE'

JSON_PATH_TRANSFORM_PREFIX = 'JP:'

# By default JSON Path expressions that resolve to an single element
# result in using the single element as the value.
# This suffix after JSON Path expression indicates that a single-valued
# JSON Path result should be kept an array value.
SPLAT_AFTER_JSON_PATH_SUFFIX = '[*]'

SECRET_PROVIDER_PLAIN = 'PLAIN'
SECRET_PROVIDER_ENV = 'ENV'
SECRET_PROVIDER_AWS_SECRETS_MANAGER = 'AWS_SM'

SECRET_PROVIDER_INFO: Dict[str, Dict[str, Any]] = {
    SECRET_PROVIDER_PLAIN: {},
    SECRET_PROVIDER_ENV: {},
    SECRET_PROVIDER_AWS_SECRETS_MANAGER: {},
}

DEFAULT_TRANSFORM_SEPARATOR = '|'

AWS_ECS_METADATA_TIMEOUT_SECONDS = 60

_logger = logging.getLogger(__name__)
_logger.addHandler(logging.NullHandler())

caught_sigterm = False


def _exit_handler(wrapper: 'ProcWrapper'):
    # Prevent re-entrancy and changing of the exit code
    atexit.unregister(_exit_handler)
    wrapper._handle_exit()


def _signal_handler(signum, frame):
    global caught_sigterm
    caught_sigterm = True
    # This will cause the exit handler to be executed, if it is registered.
    raise RuntimeError('Caught SIGTERM, exiting.')


# From glglgl on
# https://stackoverflow.com/questions/4978738/is-there-a-python-equivalent-of-the-c-sharp-null-coalescing-operator
def _coalesce(*arg):
    return next((a for a in arg if a is not None), None)


def _string_to_bool(s: Optional[str],
        default_value: Optional[bool] = None) -> Optional[bool]:
    if s is None:
        return default_value

    trimmed = s.strip()
    if trimmed == '':
        return default_value

    return (trimmed.upper() == 'TRUE')


def _string_to_int(s: Optional[Any],
        default_value: Optional[int] = None,
        negative_value: Optional[int] = None) -> Optional[int]:
    if s is None:
        return default_value
    else:
        trimmed = str(s).strip()

        if trimmed == '':
            return default_value

        x = int(trimmed)

        if x < 0:
            return negative_value

        return x


def _encode_int(x: Optional[int], empty_value: Optional[int] = None) -> \
        Optional[int]:
    if x is None:
        return empty_value
    else:
        return x


class CachedEnvValueEntry(NamedTuple):
    string_value: str
    parsed_value: Any
    fetched_at: float

    def parsed(self) -> 'CachedEnvValueEntry':
        if self.parsed_value:
            return self

        parsed = json.loads(self.string_value)
        return CachedEnvValueEntry(string_value=self.string_value,
                parsed_value=parsed, fetched_at=self.fetched_at)

    def is_stale(self, ttl_seconds: Optional[int]) -> bool:
        if ttl_seconds is None:
            return False

        return (time.time() - self.fetched_at) > ttl_seconds


class RuntimeMetadata(NamedTuple):
    execution_method: Dict[str, Any]
    execution_method_capability: Dict[str, Any]
    raw: Dict[str, Any]
    derived: Dict[str, Any]


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
        parser = argparse.ArgumentParser(prog='proc_wrapper',
                description="""
Wraps the execution of processes so that a service API endpoint (CloudReactor)
is optionally informed of the progress.
Also implements retries, timeouts, and secret injection into the
environment.
        """)

        if require_command:
            parser.add_argument('command', nargs=argparse.REMAINDER)

        parser.add_argument('-v', '--version', action='store_true',
                help='Print the version and exit')
        parser.add_argument('-n', '--task-name',
                help='Name of Task (either the Task Name or the Task UUID must be specified')
        parser.add_argument('--task-uuid',
                help='UUID of Task (either the Task Name or the Task UUID must be specified)')
        parser.add_argument('-a', '--auto-create-task', action='store_true',
                help='Create the Task even if not known by the API server')
        parser.add_argument('--auto-create-task-run-environment-name',
                help='Name of the Run Environment to use if auto-creating the Task (either the name or UUID of the Run Environment must be specified if auto-creating the Task). Defaults to the deployment name if the Run Environment UUID is not specified.')
        parser.add_argument('--auto-create-task-run-environment-uuid',
                help='UUID of the Run Environment to use if auto-creating the Task (either the name or UUID of the Run Environment must be specified if auto-creating the Task)')
        parser.add_argument('--auto-create-task-props',
                help='Additional properties of the auto-created Task, in JSON format. See https://apidocs.cloudreactor.io/#operation/api_v1_tasks_create for the schema.')
        parser.add_argument('--force-task-active', action='store_const', const=True,
                help='Indicates that the auto-created Task should be scheduled and made a service by the API server, if applicable. Otherwise, auto-created Tasks are marked passive.')
        parser.add_argument('--task-execution-uuid', help='UUID of Task Execution to attach to')
        parser.add_argument('--task-version-number', help="Numeric version of the Task's source code")
        parser.add_argument('--task-version-text', help="Human readable version of the Task's source code")
        parser.add_argument('--task-version-signature', help="Version signature of the Task's source code")
        parser.add_argument('--execution-method-props',
                help='Additional properties of the execution method, in JSON format')
        parser.add_argument('--task-instance-metadata', help="Additional metadata about the Task instance, in JSON format")
        parser.add_argument('--api-base-url',
                help=f'Base URL of API server. Defaults to {DEFAULT_API_BASE_URL}')
        parser.add_argument('-k', '--api-key', help='API key. Must have at least the Task access level, or Developer access level for auto-created Tasks.')
        parser.add_argument('--api-heartbeat-interval',
                help=f"Number of seconds to wait between sending heartbeats to the API server. -1 means to not send heartbeats. Defaults to {DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_HEARTBEAT_INTERVAL_SECONDS} for concurrency limited services, {DEFAULT_API_HEARTBEAT_INTERVAL_SECONDS} otherwise.")
        parser.add_argument('--api-error-timeout',
                help=f"Number of seconds to wait while receiving recoverable errors from the API server. Defaults to {DEFAULT_API_ERROR_TIMEOUT_SECONDS}.")
        parser.add_argument('--api-final-update-timeout',
                help=f"Number of seconds to wait while receiving recoverable errors from the API server when sending the final update before exiting. Defaults to {DEFAULT_API_FINAL_UPDATE_TIMEOUT_SECONDS}.")
        parser.add_argument('--api-retry-delay',
                help=f"Number of seconds to wait before retrying an API request. Defaults to {DEFAULT_API_RETRY_DELAY_SECONDS}.")
        parser.add_argument('--api-resume-delay',
                help=f"Number of seconds to wait before resuming API requests, after retries are exhausted. Defaults to {DEFAULT_API_RESUME_DELAY_SECONDS}. -1 means no resumption.")
        parser.add_argument('--api-task-execution-creation-error-timeout',
                help=f"Number of seconds to keep retrying Task Execution creation while receiving error responses from the API server. -1 means to keep trying indefinitely. Defaults to {DEFAULT_API_TASK_EXECUTION_CREATION_TIMEOUT_SECONDS}.")
        parser.add_argument('--api-task-execution-creation-conflict-timeout',
                help=f"Number of seconds to keep retrying Task Execution creation while conflict is detected by the API server. -1 means to keep trying indefinitely. Defaults to {DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_CREATION_TIMEOUT_SECONDS} for concurrency limited services, 0 otherwise.")
        parser.add_argument('--api-task-execution-creation-conflict-retry-delay',
                help=f"Number of seconds between attempts to retry Task Execution creation after conflict is detected. Defaults to {DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_CREATION_CONFLICT_RETRY_DELAY_SECONDS} for concurrency-limited services, {DEFAULT_API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY_SECONDS} otherwise.")
        parser.add_argument('--api-request-timeout',
                help=f"Timeout for contacting API server, in seconds. Defaults to {DEFAULT_API_REQUEST_TIMEOUT_SECONDS}.")
        parser.add_argument('-o', '--offline-mode', action='store_true',
                help='Do not communicate with or rely on an API server')
        parser.add_argument('-s', '--service', action='store_true',
                help='Indicate that this is a Task that should run indefinitely')
        parser.add_argument('--max-concurrency',
                help='Maximum number of concurrent Task Executions allowed with the same Task UUID. Defaults to 1.')
        parser.add_argument('--max-conflicting-age',
                help=f"Maximum age of conflicting Tasks to consider, in seconds. -1 means no limit. Defaults to the heartbeat interval, plus {HEARTBEAT_DELAY_TOLERANCE_SECONDS} seconds for services that send heartbeats. Otherwise, defaults to no limit.")
        parser.add_argument('-p', '--prevent-offline-execution', action='store_true',
                help='Do not start processes if the API server is unavailable.')
        parser.add_argument('-l', '--log-level',
                help=f"Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL). Defaults to {_DEFAULT_LOG_LEVEL}.")
        parser.add_argument('--log-secrets', action='store_true', help='Log sensitive information')
        parser.add_argument('-w', '--work-dir', help='Working directory. Defaults to the current directory.')
        parser.add_argument('-t', '--process-timeout',
                help=f"Timeout for process, in seconds. Defaults to {DEFAULT_PROCESS_TIMEOUT_SECONDS} for non-services, infinite for services. -1 means no timeout.")
        parser.add_argument('-r', '--process-max-retries',
                help='Maximum number of times to retry failed processes. -1 means to retry forever. Defaults to 0.')
        parser.add_argument('--process-retry-delay',
                help=f"Number of seconds to wait before retrying a process. Defaults to {DEFAULT_PROCESS_RETRY_DELAY_SECONDS}.")
        parser.add_argument('--process-check-interval',
                help=f"Number of seconds to wait between checking the status of processes. Defaults to {DEFAULT_PROCESS_CHECK_INTERVAL_SECONDS}.")
        parser.add_argument('--process-termination-grace-period',
                help=f"Number of seconds to wait after sending SIGTERM to a process, but before killing it with SIGKILL. Defaults to {DEFAULT_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS}.")
        parser.add_argument('--enable-status-update-listener', action='store_true',
                help='Listen for status updates from the process, sent on the status socket port via UDP. If not specified, status update messages will not be read.')
        parser.add_argument('--status-update-socket-port',
                help=f"The port used to receive status updates from the process. Defaults to {DEFAULT_STATUS_UPDATE_SOCKET_PORT}.")
        parser.add_argument('--status-update-message-max-bytes',
                help=f"The maximum number of bytes status update messages can be. Defaults to {DEFAULT_STATUS_UPDATE_MESSAGE_MAX_BYTES}.")
        parser.add_argument('--status-update-interval',
                help='Minimum of seconds to wait between sending status updates to the API server. -1 means to not send status updates except with heartbeats. Defaults to -1.')
        parser.add_argument('--send-pid', action='store_true', help='Send the process ID to the API server')
        parser.add_argument('--send-hostname', action='store_true', help='Send the hostname to the API server')
        parser.add_argument('--no-send-runtime-metadata', action='store_true',
                help='Do not send metadata about the runtime environment')
        parser.add_argument('-d', '--deployment',
                help='Deployment name (production, staging, etc.)')
        parser.add_argument('--schedule', help='Run schedule reported to the API server')
        parser.add_argument('--resolved-env-ttl',
                help='Number of seconds to cache resolved environment variables instead of refreshing them when a process restarts. -1 means to never refresh. Defaults to -1.')
        parser.add_argument('--rollbar-access-token',
                help='Access token for Rollbar (used to report error when communicating with API server)')
        parser.add_argument('--rollbar-retries',
                help=f"Number of retries per Rollbar request. Defaults to {DEFAULT_ROLLBAR_RETRIES}.")
        parser.add_argument('--rollbar-retry-delay',
                help=f"Number of seconds to wait before retrying a Rollbar request. Defaults to {DEFAULT_ROLLBAR_RETRY_DELAY_SECONDS}.")
        parser.add_argument('--rollbar-timeout',
                help=f"Timeout for contacting Rollbar server, in seconds. Defaults to {DEFAULT_ROLLBAR_TIMEOUT_SECONDS}.")
        return parser

    @staticmethod
    def make_default_args():
        return ProcWrapper.make_arg_parser(require_command=False).parse_args([])

    def __init__(self, args=None, embedded_mode=True,
            env_override: Optional[Mapping[str, Any]] = None) -> None:
        _logger.info('Creating ProcWrapper instance ...')

        if env_override:
            self.env = dict(env_override)
        else:
            self.env = os.environ.copy()

        if not args:
            args = ProcWrapper.make_default_args()

        self.args = args

        self.log_secrets = _string_to_bool(
                self.env.get('PROC_WRAPPER_API_LOG_SECRETS'),
                default_value=_coalesce(args.log_secrets, False))

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

        self.resolved_env_ttl: Optional[int] = _string_to_int(
                self.env.get('PROC_WRAPPER_RESOLVED_ENV_TTL_SECONDS'),
                default_value=args.resolved_env_ttl)

        self.overwrite_env_during_secret_resolution = _string_to_bool(
                self.env.get('PROC_WRAPPER_OVERWRITE_ENV_WITH_SECRETS'),
                default_value=False)

        # Dictionary from SECRET_PROVIDER_XXX constants to caches from lookup values
        # to resolved values and metadata
        self.secret_cache: Dict[str, Dict[str, CachedEnvValueEntry]] = {}
        self.aws_secrets_manager_client = None
        self.aws_secrets_manager_client_create_attempted_at: Optional[float] = None

        self.resolved_env, self.failed_env_names = self.resolve_env()

        self.exit_handler_installed = False
        self.in_pytest = False

        self.initialize_fields(mutable_only=False)

    def initialize_fields(self, mutable_only: bool = True) -> None:
        resolved_env = self.resolved_env
        args = self.args

        self.offline_mode = _string_to_bool(
            resolved_env.get('PROC_WRAPPER_OFFLINE_MODE'),
            default_value=_coalesce(args.offline_mode, False))

        self.deployment = _coalesce(resolved_env.get('PROC_WRAPPER_DEPLOYMENT'),
                args.deployment)

        self.rollbar_access_token = resolved_env.get('PROC_WRAPPER_ROLLBAR_ACCESS_TOKEN')

        if self.rollbar_access_token:
            self.rollbar_retries = _string_to_int(
                    resolved_env.get('PROC_WRAPPER_ROLLBAR_RETRIES'),
                    default_value=_coalesce(args.rollbar_retries,
                            DEFAULT_ROLLBAR_RETRIES))

            self.rollbar_retry_delay = _string_to_int(
                    resolved_env.get('PROC_WRAPPER_ROLLBAR_RETRY_DELAY_SECONDS'),
                    default_value=_coalesce(args.rollbar_retry_delay,
                            DEFAULT_ROLLBAR_RETRY_DELAY_SECONDS))

            self.rollbar_timeout = _string_to_int(
                    resolved_env.get('PROC_WRAPPER_ROLLBAR_TIMEOUT_SECONDS'),
                    default_value=_coalesce(args.rollbar_timeout,
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

            self.auto_create_task = _string_to_bool(
                    resolved_env.get('PROC_WRAPPER_AUTO_CREATE_TASK'),
                    default_value=_coalesce(args.auto_create_task,
                    self.auto_create_task_overrides.get('was_auto_created'))) \
                    or task_overrides_loaded

            if self.auto_create_task:
                override_run_env = self.auto_create_task_overrides.get('run_environment', {})

                self.auto_create_task_run_environment_uuid = resolved_env.get(
                        'PROC_WRAPPER_AUTO_CREATE_TASK_RUN_ENVIRONMENT_UUID',
                        override_run_env.get('uuid',
                        args.auto_create_task_run_environment_uuid))

                self.auto_create_task_run_environment_name = _coalesce(
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
                        return

                args_forced_passive = None if (args.force_task_active is None) \
                        else (not args.force_task_active)

                self.task_is_passive = cast(bool, _string_to_bool(
                        resolved_env.get('PROC_WRAPPER_TASK_IS_PASSIVE'),
                        default_value=_coalesce(
                                self.auto_create_task_overrides.get('passive'),
                                args_forced_passive, self.auto_create_task)))

                if not self.task_is_passive:
                    runtime_metadata = self.fetch_runtime_metadata()

                    if (runtime_metadata is None) or \
                            (runtime_metadata.execution_method_capability is None):
                        _logger.critical('Task may not be active unless execution method capability can be determined.')
                        self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
                        return

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
                return

            count = self.auto_create_task_overrides.get('min_service_instance_count')
            override_is_service = None if count is None else (count > 0)

            api_base_url = resolved_env.get('PROC_WRAPPER_API_BASE_URL') \
                    or args.api_base_url or DEFAULT_API_BASE_URL
            self.api_base_url = api_base_url.rstrip('/')

        if not mutable_only:
            self.task_is_service = _string_to_bool(
                resolved_env.get('PROC_WRAPPER_TASK_IS_SERVICE'),
                default_value=_coalesce(override_is_service, args.service))

            self.max_concurrency = _string_to_int(
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
                return

            self.api_error_timeout = cast(int, _string_to_int(
                    resolved_env.get('PROC_WRAPPER_API_ERROR_TIMEOUT_SECONDS'),
                    default_value=_coalesce(args.api_error_timeout,
                    DEFAULT_API_ERROR_TIMEOUT_SECONDS)))

            self.api_task_execution_creation_error_timeout = _string_to_int(
                    resolved_env.get('PROC_WRAPPER_API_TASK_CREATION_ERROR_TIMEOUT_SECONDS'),
                    default_value=_coalesce(
                            args.api_task_execution_creation_error_timeout,
                            DEFAULT_API_TASK_EXECUTION_CREATION_TIMEOUT_SECONDS))

            default_task_execution_creation_conflict_timeout = 0
            default_task_execution_creation_conflict_retry_delay = DEFAULT_API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY_SECONDS
            if self.is_concurrency_limited_service:
                default_task_execution_creation_conflict_timeout = \
                        DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_CREATION_TIMEOUT_SECONDS
                default_task_execution_creation_conflict_retry_delay = \
                        DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_CREATION_CONFLICT_RETRY_DELAY_SECONDS

            self.api_task_execution_creation_conflict_timeout = _string_to_int(
                    resolved_env.get('PROC_WRAPPER_API_TASK_EXECUTION_CREATION_CONFLICT_TIMEOUT_SECONDS'),
                    default_value=_coalesce(
                            args.api_task_execution_creation_conflict_timeout,
                            default_task_execution_creation_conflict_timeout))

            self.api_final_update_timeout = _string_to_int(
                    resolved_env.get('PROC_WRAPPER_API_FINAL_UPDATE_TIMEOUT_SECONDS'),
                    default_value=_coalesce(args.api_final_update_timeout,
                            DEFAULT_API_FINAL_UPDATE_TIMEOUT_SECONDS))

            self.api_retry_delay = cast(int, _string_to_int(
                    resolved_env.get('PROC_WRAPPER_API_RETRY_DELAY_SECONDS'),
                    default_value=_coalesce(args.api_retry_delay,
                            DEFAULT_API_RETRY_DELAY_SECONDS),
                    negative_value=0))

            self.api_resume_delay = cast(int, _string_to_int(
                    resolved_env.get('PROC_WRAPPER_API_RESUME_DELAY_SECONDS'),
                    default_value=_coalesce(args.api_resume_delay,
                            DEFAULT_API_RESUME_DELAY_SECONDS)))

            self.api_task_execution_creation_conflict_retry_delay = _string_to_int(
                    resolved_env.get('PROC_WRAPPER_API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY_SECONDS'),
                    default_value=args.api_task_execution_creation_conflict_retry_delay) \
                    or default_task_execution_creation_conflict_retry_delay

            self.api_request_timeout = _string_to_int(
                    resolved_env.get('PROC_WRAPPER_API_REQUEST_TIMEOUT_SECONDS'),
                    default_value=_coalesce(args.api_request_timeout,
                            DEFAULT_API_REQUEST_TIMEOUT_SECONDS))

        other_instance_metadata_str = _coalesce(
                resolved_env.get('PROC_WRAPPER_TASK_INSTANCE_METADATA'),
                args.task_instance_metadata)

        self.other_instance_metadata = None
        if other_instance_metadata_str:
            try:
                self.other_instance_metadata = json.loads(other_instance_metadata_str)
            except Exception:
                _logger.exception(f"Failed to parse instance metadata: '{other_instance_metadata_str}'")

        self.in_pytest = _string_to_bool(os.environ.get('IN_PYTEST')) or False

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
            self.send_pid = _string_to_bool(
                    resolved_env.get('PROC_WRAPPER_SEND_PID'),
                    default_value=args.send_pid) or False

            self.send_hostname = _string_to_bool(
                    resolved_env.get('PROC_WRAPPER_SEND_HOSTNAME'),
                    default_value=args.send_hostname) or False

            self.send_runtime_metadata = _string_to_bool(
                    resolved_env.get('PROC_WRAPPER_SEND_RUNTIME_METADATA'),
                    default_value=not _coalesce(args.no_send_runtime_metadata, False)) \
                    or False

        env_process_timeout_seconds = resolved_env.get('PROC_WRAPPER_PROCESS_TIMEOUT_SECONDS')

        if self.task_is_service:
            if (args.process_timeout is not None) \
                    and ((_string_to_int(args.process_timeout) or 0) > 0):
                _logger.warning(
                        'Ignoring argument --process-timeout since Task is a service')

            if env_process_timeout_seconds \
                    and ((_string_to_int(env_process_timeout_seconds) or 0) > 0):
                _logger.warning(
                        'Ignoring environment variable PROC_WRAPPER_PROCESS_TIMEOUT_SECONDS since Task is a service')
        else:
            self.process_timeout = _string_to_int(
                    env_process_timeout_seconds,
                    default_value=args.process_timeout)

        self.prevent_offline_execution = _string_to_bool(
                resolved_env.get('PROC_WRAPPER_PREVENT_OFFLINE_EXECUTION'),
                default_value=args.prevent_offline_execution) or False

        if self.offline_mode and self.prevent_offline_execution:
            _logger.critical('Offline mode and offline execution prevention cannot both be enabled.')
            return self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)

        self.process_max_retries = _string_to_int(
                resolved_env.get('PROC_WRAPPER_TASK_MAX_RETRIES'),
                default_value=_coalesce(args.process_max_retries, 0))

        self.process_retry_delay = cast(int, _string_to_int(
                resolved_env.get('PROC_WRAPPER_PROCESS_RETRY_DELAY_SECONDS'),
                default_value=_coalesce(args.process_retry_delay,
                        DEFAULT_PROCESS_RETRY_DELAY_SECONDS),
                negative_value=0))

        self.process_termination_grace_period_seconds = cast(int, _string_to_int(
                resolved_env.get('PROC_WRAPPER_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS'),
                default_value=_coalesce(args.process_termination_grace_period,
                        DEFAULT_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS),
                negative_value=0))

        default_heartbeat_interval = DEFAULT_API_HEARTBEAT_INTERVAL_SECONDS

        if self.is_concurrency_limited_service:
            default_heartbeat_interval = DEFAULT_API_CONCURRENCY_LIMITED_SERVICE_HEARTBEAT_INTERVAL_SECONDS

        self.api_heartbeat_interval = _string_to_int(
                resolved_env.get('PROC_WRAPPER_API_HEARTBEAT_INTERVAL_SECONDS'),
                default_value=_coalesce(args.api_heartbeat_interval,
                        default_heartbeat_interval))

        default_max_conflicting_age_seconds = None

        if self.task_is_service and self.api_heartbeat_interval:
            default_max_conflicting_age_seconds = self.api_heartbeat_interval + HEARTBEAT_DELAY_TOLERANCE_SECONDS

        self.max_conflicting_age = _string_to_int(
                resolved_env.get('PROC_WRAPPER_MAX_CONFLICTING_AGE_SECONDS'),
                default_value=_coalesce(args.max_conflicting_age,
                        default_max_conflicting_age_seconds))

        self.status_update_interval = _string_to_int(
                resolved_env.get('PROC_WRAPPER_STATUS_UPDATE_INTERVAL_SECONDS'),
                default_value=args.status_update_interval)

        # Properties to be reported to CloudRector
        self.schedule = _coalesce(resolved_env.get('PROC_WRAPPER_SCHEDULE'),
                args.schedule, '')

        if not self._embedded_mode:
            self.process_check_interval = cast(int, _string_to_int(
                    resolved_env.get('PROC_WRAPPER_PROCESS_CHECK_INTERVAL_SECONDS'),
                    default_value=_coalesce(args.process_check_interval,
                            DEFAULT_PROCESS_CHECK_INTERVAL_SECONDS),
                    negative_value=-1))

            if self.process_check_interval <= 0:
                _logger.critical(f"Process check interval {self.process_check_interval} must be positive.")
                self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
                return

            # TODO: allow command override if API key has developer permission
            if not args.command:
                _logger.critical('Command expected in wrapped mode, but not found')
                self._exit_or_raise(self._EXIT_CODE_CONFIGURATION_ERROR)
                return

            self.command = args.command
            self.working_dir = resolved_env.get('PROC_WRAPPER_WORK_DIR') \
                  or args.work_dir or '.'

            # We don't support changing the status update listener parameters
            if (not mutable_only) and (not self.offline_mode):
                enable_status_update_listener = _string_to_bool(
                        resolved_env.get('PROC_WRAPPER_ENABLE_STATUS_UPDATE_LISTENER'),
                        default_value=_coalesce(args.enable_status_update_listener, False))

                if enable_status_update_listener:
                    self.status_update_socket_port = _string_to_int(_coalesce(
                          resolved_env.get('PROC_WRAPPER_STATUS_UPDATE_SOCKET_PORT'),
                          args.status_update_socket_port),
                          default_value=DEFAULT_STATUS_UPDATE_SOCKET_PORT,
                          negative_value=DEFAULT_STATUS_UPDATE_SOCKET_PORT)
                    self._status_buffer = bytearray(self._STATUS_BUFFER_SIZE)
                    self._status_message_so_far = bytearray()
                    self.status_update_message_max_bytes = _string_to_int(_coalesce(
                            resolved_env.get('PROC_WRAPPER_STATUS_UPDATE_MESSAGE_MAX_BYTES'),
                            args.status_update_message_max_bytes),
                            default_value=DEFAULT_STATUS_UPDATE_MESSAGE_MAX_BYTES,
                            negative_value=DEFAULT_STATUS_UPDATE_MESSAGE_MAX_BYTES)

        self.log_configuration()

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

    def resolve_env(self) -> Tuple[Dict[str, str], List[str]]:
        """
          Resolve environment variables, returning a dictionary of the environment,
          and a list of variable names that failed to be resolved.
        """
        _logger.debug('Starting secrets resolution ...')

        if not _string_to_bool(
                self.env.get('PROC_WRAPPER_RESOLVE_SECRETS'), False):
            _logger.debug('Secrets resolution is disabled.')
            return (self.env, [])

        prefix = _coalesce(self.env.get('PROC_WRAPPER_RESOLVABLE_ENV_VAR_PREFIX'),
                DEFAULT_RESOLVABLE_ENV_VAR_PREFIX)
        prefix_length = len(prefix)

        suffix = _coalesce(self.env.get('PROC_WRAPPER_RESOLVABLE_ENV_VAR_SUFFIX'),
                DEFAULT_RESOLVABLE_ENV_VAR_SUFFIX)
        suffix_length = len(suffix)

        resolved = {}
        failed_env_names = []
        for name, value in self.env.items():
            if name.startswith(prefix) and name.endswith(suffix):
                key = name[prefix_length:len(name) - suffix_length]
                env_name = key

                _logger.debug(f"Resolving key '{key}' with value '{value}' ...")
                try:
                    env_name, env_value = self.resolve_env_var(key, value)

                    if env_name is None:
                        pass
                    elif (not self.overwrite_env_during_secret_resolution) \
                            and (env_name in self.env):
                        _logger.info(f"Skipping overwriting of environment variable '{key}'")
                    else:
                        resolved[env_name] = env_value
                except Exception:
                    msg = f"Failed to resolve environment variable '{env_name}'"
                    if self.log_secrets:
                        msg += f" which had value '{value}'"
                    _logger.exception(msg)
                    failed_env_names.append(env_name)
            else:
                resolved[name] = value

        return resolved, failed_env_names

    def resolve_env_var(self, name: str, value: str) -> Tuple[str, str]:
        env_name = name
        secret_provider = None
        secret_provider_info = {}

        for sp, spi in SECRET_PROVIDER_INFO.items():
            if name.startswith(sp + '_'):
                env_name = name[len(sp) + 1:]
                secret_provider = sp
                secret_provider_info = spi
                break

        secret_provider = secret_provider or SECRET_PROVIDER_PLAIN

        if self.log_secrets:
            _logger.debug(f"Secret provider = '{secret_provider}', name = '{name}', value = '{value}'")

        value_to_lookup = value
        jsonpath_expr_str = None
        jsonpath_expr = None
        should_splat = False

        transform_separator = secret_provider_info.get('transform_separator',
                DEFAULT_TRANSFORM_SEPARATOR)
        separator_index = value.find(transform_separator)
        if (separator_index > 0) and (separator_index < len(value) - 2):
            transform_expr_str = value_to_lookup[(separator_index + 1):]

            if transform_expr_str.startswith(JSON_PATH_TRANSFORM_PREFIX):
                try:
                    import jsonpath_ng  # type: ignore
                except ImportError as import_error:
                    _logger.exception('jsonpath_ng is not available to import, please install it in your python environment')
                    raise import_error

                jsonpath_expr_str = transform_expr_str[len(JSON_PATH_TRANSFORM_PREFIX):]

                _logger.debug(f"jsonpath_expr_str = '{jsonpath_expr_str}'")

                if jsonpath_expr_str.endswith(SPLAT_AFTER_JSON_PATH_SUFFIX):
                    should_splat = True
                    jsonpath_expr_str = jsonpath_expr_str[0:-len(SPLAT_AFTER_JSON_PATH_SUFFIX)]

                try:
                    jsonpath_expr = jsonpath_ng.parse(jsonpath_expr_str)
                except Exception as ex:
                    _logger.exception(f"Could not parse '{jsonpath_expr_str}' as a JSON Path expression")
                    raise ex

                value_to_lookup = value_to_lookup[0:separator_index]
            else:
                raise RuntimeError(f"Unknown transform for value '{value}'")

        cached_env_value_entry: Optional[CachedEnvValueEntry] = None

        cache = self.secret_cache.get(secret_provider)

        if cache is None:
            cache = {}
            self.secret_cache[secret_provider] = cache
        else:
            cached_env_value_entry = cache.get(value_to_lookup)

        if (cached_env_value_entry is None) \
                or cached_env_value_entry.is_stale(self.resolved_env_ttl):
            secret_value: Optional[str] = None
            if secret_provider == SECRET_PROVIDER_AWS_SECRETS_MANAGER:
                secret_value = self.fetch_aws_secrets_manager_secret(value_to_lookup)
            elif secret_provider == SECRET_PROVIDER_PLAIN:
                secret_value = value_to_lookup
            elif secret_provider == SECRET_PROVIDER_ENV:
                secret_value = self.env.get(value_to_lookup)
            else:
                raise RuntimeError(f'Unknown secret provider: {secret_provider}')

            if secret_value is None:
                raise RuntimeError(f"Missing secret value for lookup value '{value_to_lookup}'")

            cached_env_value_entry = CachedEnvValueEntry(string_value=secret_value,
                    parsed_value=None, fetched_at=time.time())

            cache[value_to_lookup] = cached_env_value_entry

        if self.log_secrets:
            _logger.debug(f"value_to_lookup = '{value_to_lookup}', cache entry = '{cached_env_value_entry}'")

        if jsonpath_expr is None:
            return (env_name, cached_env_value_entry.string_value)

        try:
            parsed_entry = cached_env_value_entry.parsed()
        except Exception as ex:
            _logger.exception(f"Error parsing value for key '{value_to_lookup}' for JSON path lookup")
            raise ex

        # Save the parsed value so we don't have to reparse.
        cache[value_to_lookup] = parsed_entry

        results = jsonpath_expr.find(parsed_entry.parsed_value)

        if self.log_secrets:
            _logger.debug(f"json path results = {results}")

        env_value = self._json_path_results_to_env_value(results,
                should_splat=should_splat,
                env_name=env_name, path_expr=jsonpath_expr_str)

        if self.log_secrets:
            _logger.debug(f"resolved env_name = '{env_name}', resolved env_value = '{env_value}'")

        return env_name, env_value

    def _json_path_results_to_env_value(self, results: List,
            should_splat: bool, env_name: str, path_expr: Optional[str]) -> str:
        results_len = len(results)

        if results_len == 0:
            _logger.warning(f"Got no results for environment variable '{env_name}' with JSON path '{path_expr}'")
            if should_splat:
                return '[]'

            return ''

        if (results_len == 1) and not should_splat:
            v = results[0].value
            if isinstance(v, bool):
                # Boolean values get transformed to environment value TRUE or FALSE
                return str(v).upper()

            if isinstance(v, (dict, list)):
                # Collections get serialized as JSON
                return json.dumps(v)

            return str(v)

        # Multiple values get serialized as a JSON array
        # TODO: implement a way to output comma-separated values
        return json.dumps([r.value for r in results])

    def get_or_create_aws_secrets_manager_client(self):
        if not self.aws_secrets_manager_client:
            if self.aws_secrets_manager_client_create_attempted_at:
                return None

            self.aws_secrets_manager_client_create_attempted_at = time.time()

            try:
                import boto3
            except ImportError as import_error:
                _logger.exception('boto3 is not available to import, please install it in your python environment')
                raise import_error

            region_name = self.env.get('PROC_WRAPPER_SECRETS_AWS_REGION') or \
                    self.env.get('AWS_REGION') or self.env.get('AWS_DEFAULT_REGION')

            if not region_name:
                runtime_metadata = self.fetch_runtime_metadata()

                if runtime_metadata and ('aws' in runtime_metadata.derived):
                    region_name = runtime_metadata.derived['aws'].get('region')

            self.aws_secrets_manager_client = boto3.client(
                service_name='secretsmanager',
                region_name=region_name
            )

        return self.aws_secrets_manager_client

    def fetch_aws_secrets_manager_secret(self, value: str) -> str:
        client = self.get_or_create_aws_secrets_manager_client()

        if client is None:
            raise RuntimeError("Can't create AWS Secrets Manager client")

        _logger.info(f"Looking up Secrets Manager secret '{value}'")
        response = client.get_secret_value(SecretId=value)

        # Binary secrets are left Base-64 encoded
        return response.get('SecretString') or response['SecretBinary']

    def fetch_runtime_metadata(self) -> Optional[RuntimeMetadata]:
        _logger.debug('Entering fetch_runtime_metadata() ...')

        # Don't refetch if we have already attempted previously
        if self.runtime_metadata or self.fetched_runtime_metadata_at:
            _logger.debug('Runtime metadata already fetched, returning existing metadata.')
            return self.runtime_metadata

        self.runtime_metadata = self.fetch_ecs_container_metadata()

        self.fetched_runtime_metadata_at = time.time()

        _logger.debug(f'Done fetching runtime metadata, got {self.runtime_metadata}')
        return self.runtime_metadata

    def fetch_ecs_container_metadata(self) -> Optional[RuntimeMetadata]:
        task_metadata_url = self.env.get('ECS_CONTAINER_METADATA_URI_V4') \
                or self.env.get('ECS_CONTAINER_METADATA_URI')

        if task_metadata_url:
            url = f'{task_metadata_url}/task'

            _logger.debug(f"Fetching ECS task metadata from '{task_metadata_url}' ...")

            headers = {
                'Accept': 'application/json'
            }
            try:
                req = Request(url, method='GET', headers=headers)
                resp = urlopen(req, timeout=AWS_ECS_METADATA_TIMEOUT_SECONDS)
                response_body = resp.read().decode('utf-8')
                parsed_metadata = json.loads(response_body)
                return self.convert_ecs_task_metadata(parsed_metadata)
            except HTTPError as http_error:
                status_code = http_error.code
                _logger.warning(f'Unable to fetch ECS task metadata endpoint. Response code = {status_code}.')
            except Exception:
                _logger.exception('Unable to fetch ECS task metadata endpoint or convert metadata.')
        else:
            _logger.debug('No ECS metadata URL found')

        return None

    def convert_ecs_task_metadata(self, metadata: Dict[str, Any]) -> RuntimeMetadata:
        cluster_arn = metadata.get('Cluster') or ''
        task_arn = metadata.get('TaskARN') or ''
        task_definition_arn = self.compute_ecs_task_definition_arn(metadata) or ''

        common_props: Dict[str, Any] = {
            'type': 'AWS ECS',
            'task_definition_arn': task_definition_arn,
        }

        execution_method: Dict[str, Any] = {
            'task_arn': task_arn,
            'cluster_arn': cluster_arn,
        }

        execution_method_capability: Dict[str, Any] = {
            'default_cluster_arn': cluster_arn
        }

        launch_type = metadata.get('LaunchType')
        if launch_type:
            execution_method['launch_type'] = launch_type
            execution_method_capability['default_launch_type'] = launch_type
            execution_method_capability['supported_launch_types'] = [launch_type]

        limits = metadata.get('Limits')
        if isinstance(limits, dict):
            cpu_fraction = limits.get('CPU')
            if (cpu_fraction is not None) \
                    and isinstance(cpu_fraction, (float, int)):
                common_props['allocated_cpu_units'] = round(cpu_fraction * 1024)

            memory_mb = limits.get('Memory')
            if memory_mb is not None:
                common_props['allocated_memory_mb'] = memory_mb

        execution_method.update(common_props)
        execution_method_capability.update(common_props)

        # Only available for Fargate platform 1.4+
        az = metadata.get('AvailabilityZone')

        if az:
            # Remove the last character, e.g. "a" from "us-west-1a"
            region = az[0:-1]
        else:
            region = self.compute_region_from_ecs_cluster_arn(cluster_arn)

        aws_props = {
            'network': {
                'availability_zone': az,
                'region': region,
            },
        }

        derived = {
            'aws': aws_props
        }

        return RuntimeMetadata(execution_method=execution_method,
                execution_method_capability=execution_method_capability,
                raw=metadata, derived=derived)

    def compute_ecs_task_definition_arn(self, metadata: Dict[str, Any]) -> Optional[str]:
        task_arn = metadata.get('TaskARN')
        family = metadata.get('Family')
        revision = metadata.get('Revision')

        if not (task_arn and family and revision):
            _logger.warning("Can't compute ECS task definition ARN: task_arn = {task_arn}, family = {family}, revision = {revision}")
            return None

        prefix_end_index = task_arn.find(':task/')

        if prefix_end_index < 0:
            _logger.warning("Can't compute ECS task definition ARN: task_arn = {task_arn} has an unexpected format")
            return None

        return task_arn[0:prefix_end_index] + ':task-definition/' + family + ':' + revision

    def compute_region_from_ecs_cluster_arn(self, cluster_arn: Optional[str]) -> Optional[str]:
        if not cluster_arn:
            return None

        if cluster_arn.startswith('arn:aws:ecs:'):
            parts = cluster_arn.split(':')
            return parts[3]

        _logger.warning(f"Can't determine AWS region from cluster ARN '{cluster_arn}'")
        return None

    @property
    def max_execution_attempts(self) -> float:
        if self.process_max_retries is None:
            return math.inf
        else:
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
            self.fetch_runtime_metadata()

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

        config = self.make_process_env()

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
                        self.resolved_env, self.failed_env_names = self.resolve_env()

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
                        self.resolved_env, self.failed_env_names = self.resolve_env()

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

    def _handle_exit(self) -> None:
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
            self._handle_exit()
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
