import argparse
import os

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

DEFAULT_PROCESS_CHECK_INTERVAL_SECONDS = 10
DEFAULT_PROCESS_RETRY_DELAY_SECONDS = 60
DEFAULT_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS = 30

DEFAULT_STATUS_UPDATE_MESSAGE_MAX_BYTES = 64 * 1024


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
            choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
            default=os.environ.get('PROC_WRAPPER_LOG_LEVEL', _DEFAULT_LOG_LEVEL),
            help='Log level')
    parser.add_argument('--log-secrets', action='store_true', help='Log sensitive information')
    parser.add_argument('-w', '--work-dir', help='Working directory. Defaults to the current directory.')
    parser.add_argument('-t', '--process-timeout',
            help='Timeout for process, in seconds. -1 means no timeout, which is the default.')
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
    parser.add_argument('--env-file', action='append',
            help="""
    Location of either local file, AWS S3 ARN, or AWS Secrets Manager ARN
    of file used to populate environment. Specify multiple times to include
    multiple files.""")
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


def make_default_args():
    return make_arg_parser(require_command=False).parse_args([])
