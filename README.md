# cloudreactor-procwrapper

<p align="center">
  <a href="https://github.com/CloudReactor/cloudreactor-procwrapper/actions?query=workflow%3ACI">
    <img src="https://img.shields.io/github/workflow/status/CloudReactor/cloudreactor-procwrapper/CI/main?label=CI&logo=github&style=flat-square" alt="CI Status" >
  </a>
  <a href="https://cloudreactor-procwrapper.readthedocs.io">
    <img src="https://img.shields.io/readthedocs/cloudreactor-procwrapper.svg?logo=read-the-docs&logoColor=fff&style=flat-square" alt="Documentation Status">
  </a>
  <a href="https://codecov.io/gh/CloudReactor/cloudreactor-procwrapper">
    <img src="https://img.shields.io/codecov/c/github/CloudReactor/cloudreactor-procwrapper.svg?logo=codecov&logoColor=fff&style=flat-square" alt="Test coverage percentage">
  </a>
</p>
<p align="center">
  <a href="https://python-poetry.org/">
    <img src="https://img.shields.io/badge/packaging-poetry-299bd7?style=flat-square&logo=data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAA4AAAASCAYAAABrXO8xAAAACXBIWXMAAAsTAAALEwEAmpwYAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAJJSURBVHgBfZLPa1NBEMe/s7tNXoxW1KJQKaUHkXhQvHgW6UHQQ09CBS/6V3hKc/AP8CqCrUcpmop3Cx48eDB4yEECjVQrlZb80CRN8t6OM/teagVxYZi38+Yz853dJbzoMV3MM8cJUcLMSUKIE8AzQ2PieZzFxEJOHMOgMQQ+dUgSAckNXhapU/NMhDSWLs1B24A8sO1xrN4NECkcAC9ASkiIJc6k5TRiUDPhnyMMdhKc+Zx19l6SgyeW76BEONY9exVQMzKExGKwwPsCzza7KGSSWRWEQhyEaDXp6ZHEr416ygbiKYOd7TEWvvcQIeusHYMJGhTwF9y7sGnSwaWyFAiyoxzqW0PM/RjghPxF2pWReAowTEXnDh0xgcLs8l2YQmOrj3N7ByiqEoH0cARs4u78WgAVkoEDIDoOi3AkcLOHU60RIg5wC4ZuTC7FaHKQm8Hq1fQuSOBvX/sodmNJSB5geaF5CPIkUeecdMxieoRO5jz9bheL6/tXjrwCyX/UYBUcjCaWHljx1xiX6z9xEjkYAzbGVnB8pvLmyXm9ep+W8CmsSHQQY77Zx1zboxAV0w7ybMhQmfqdmmw3nEp1I0Z+FGO6M8LZdoyZnuzzBdjISicKRnpxzI9fPb+0oYXsNdyi+d3h9bm9MWYHFtPeIZfLwzmFDKy1ai3p+PDls1Llz4yyFpferxjnyjJDSEy9CaCx5m2cJPerq6Xm34eTrZt3PqxYO1XOwDYZrFlH1fWnpU38Y9HRze3lj0vOujZcXKuuXm3jP+s3KbZVra7y2EAAAAAASUVORK5CYII=" alt="Poetry">
  </a>
  <a href="https://github.com/pre-commit/pre-commit">
    <img src="https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white&style=flat-square" alt="pre-commit">
  </a>
</p>
<p align="center">
  <a href="https://pypi.org/project/cloudreactor-procwrapper/">
    <img src="https://img.shields.io/pypi/v/cloudreactor-procwrapper.svg?logo=python&logoColor=fff&style=flat-square" alt="PyPI Version">
  </a>
  <img src="https://img.shields.io/pypi/pyversions/cloudreactor-procwrapper.svg?style=flat-square&logo=python&amp;logoColor=fff" alt="Supported Python versions">
  <img src="https://img.shields.io/pypi/l/cloudreactor-procwrapper.svg?style=flat-square" alt="License">
</p>

Wraps the execution of processes so that a service API endpoint (CloudReactor)
is optionally informed of the progress. Also implements retries, timeouts, and
secret injection from AWS into the environment.

## Installation

Install this via pip (or your favourite package manager):

`pip install cloudreactor-procwrapper`

## Usage

### Wrapped mode

In wrapped mode, you run the module with a command line which it
executes in a child process. The command can be implemented in whatever
programming language the running machine supports.

    usage: python -m proc_wrapper [-h] [--task-name TASK_NAME]
                                  [--task-uuid TASK_UUID]
                                  [--task-execution-uuid TASK_EXECUTION_UUID]
                                  [--task-version-number TASK_VERSION_NUMBER]
                                  [--task-version-text TASK_VERSION_TEXT]
                                  [--task-version-signature TASK_VERSION_SIGNATURE]
                                  [--task-instance-metadata TASK_INSTANCE_METADATA]
                                  [--api-base-url API_BASE_URL]
                                  [--api-key API_KEY]
                                  [--api-heartbeat-interval API_HEARTBEAT_INTERVAL]
                                  [--api-retries API_RETRIES]
                                  [--api-retries-for-final-update API_RETRIES_FOR_FINAL_UPDATE]
                                  [--api-task-execution-creation-conflict-timeout API_TASK_EXECUTION_CREATION_CONFLICT_TIMEOUT]
                                  [--api-task-execution-creation-conflict-retry-delay API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY]
                                  [--api-retry-delay API_RETRY_DELAY]
                                  [--api-resume-delay API_RESUME_DELAY]
                                  [--api-timeout API_TIMEOUT] [--offline-mode]
                                  [--service] [--max-concurrency MAX_CONCURRENCY]
                                  [--max-conflicting-age MAX_CONFLICTING_AGE]
                                  [--prevent-offline-execution]
                                  [--log-level LOG_LEVEL] [--log-secrets]
                                  [--work-dir WORK_DIR]
                                  [--process-timeout PROCESS_TIMEOUT]
                                  [--process-max-retries PROCESS_MAX_RETRIES]
                                  [--process-retry-delay PROCESS_RETRY_DELAY]
                                  [--process-check-interval PROCESS_CHECK_INTERVAL]
                                  [--process-termination-grace-period PROCESS_TERMINATION_GRACE_PERIOD]
                                  [--enable-status-update-listener]
                                  [--status-update-socket-port STATUS_UPDATE_SOCKET_PORT]
                                  [--status-update-message-max-bytes STATUS_UPDATE_MESSAGE_MAX_BYTES]
                                  [--status-update-interval STATUS_UPDATE_INTERVAL]
                                  [--send-pid] [--send-hostname]
                                  [--send-aws-metadata] [--deployment DEPLOYMENT]
                                  [--schedule SCHEDULE]
                                  [--resolved-env-ttl RESOLVED_ENV_TTL]
                                  [--rollbar-access-token ROLLBAR_ACCESS_TOKEN]
                                  [--rollbar-retries ROLLBAR_RETRIES]
                                  [--rollbar-retry-delay ROLLBAR_RETRY_DELAY]
                                  [--rollbar-timeout ROLLBAR_TIMEOUT]
                                  ...

    Wraps the execution of processes so that a service API endpoint (CloudReactor)
    is optionally informed of the progress. Also implements retries, timeouts, and
    secret injection from AWS into the environment.

    positional arguments:
      command

    optional arguments:
      -h, --help            show this help message and exit
      --task-name TASK_NAME
                            Name of Task (either the Task Name or the Task UUID
                            must be specified
      --task-uuid TASK_UUID
                            UUID of Task (either the Task Name or the Task UUID
                            must be specified)
      --task-execution-uuid TASK_EXECUTION_UUID
                            UUID of Task Execution to attach to
      --task-version-number TASK_VERSION_NUMBER
                            Numeric version of the Task's source code (optional)
      --task-version-text TASK_VERSION_TEXT
                            Human readable version of the Task's source code
                            (optional)
      --task-version-signature TASK_VERSION_SIGNATURE
                            Version signature of the Task's source code (optional)
      --task-instance-metadata TASK_INSTANCE_METADATA
                            Additional metadata about the Task instance, in JSON
                            format (optional)
      --api-base-url API_BASE_URL
                            Base URL of API server
      --api-key API_KEY     API key
      --api-heartbeat-interval API_HEARTBEAT_INTERVAL
                            Number of seconds to wait between sending heartbeats
                            to the API server. -1 means to not send heartbeats.
                            Defaults to 30 for concurrency limited services, 600
                            otherwise.
      --api-retries API_RETRIES
                            Number of retries per API request. Defaults to 2.
      --api-retries-for-final-update API_RETRIES_FOR_FINAL_UPDATE
                            Number of retries for final process status update. -1
                            (the default) means to keep trying indefinitely.
      --api-task-execution-creation-conflict-timeout API_TASK_EXECUTION_CREATION_CONFLICT_TIMEOUT
                            Number of seconds to keep retrying Task Execution
                            creation after conflict is detected. -1 means to keep
                            trying indefinitely. Defaults to 1800 for concurrency
                            limited services, 300 otherwise.
      --api-task-execution-creation-conflict-retry-delay API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY
                            Number of seconds between attempts to retry Task
                            Execution creation after conflict is detected.
                            Defaults to 60 for concurrency-limited services, 120
                            otherwise.
      --api-retry-delay API_RETRY_DELAY
                            Number of seconds to wait before retrying an API
                            request. Defaults to 120.
      --api-resume-delay API_RESUME_DELAY
                            Number of seconds to wait before resuming API
                            requests, after retries are exhausted. Defaults to
                            600. -1 means no resumption.
      --api-timeout API_TIMEOUT
                            Timeout for contacting API server, in seconds.
                            Defaults to 30.
      --offline-mode        Do not communicate with or rely on an API server
      --service             Indicate that this is a Task that should run
                            indefinitely
      --max-concurrency MAX_CONCURRENCY
                            Maximum number of concurrent Task Executions allowed
                            with the same Task UUID. Defaults to 1.
      --max-conflicting-age MAX_CONFLICTING_AGE
                            Maximum age of conflicting processes to consider, in
                            seconds. -1 means no limit. Defaults to the heartbeat
                            interval, plus 60 seconds for services that send
                            heartbeats. Otherwise, defaults to no limit.
      --prevent-offline-execution
                            Do not start processes if the API server is
                            unavailable.
      --log-level LOG_LEVEL
                            Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
                            Defaults to WARNING.
      --log-secrets         Log sensitive information
      --work-dir WORK_DIR   Working directory
      --process-timeout PROCESS_TIMEOUT
                            Timeout for process, in seconds. Defaults to None for
                            non-services, infinite for services. -1 means no
                            timeout.
      --process-max-retries PROCESS_MAX_RETRIES
                            Maximum number of times to retry failed processes. -1
                            means to retry forever. Defaults to 0.
      --process-retry-delay PROCESS_RETRY_DELAY
                            Number of seconds to wait before retrying a process.
                            Defaults to 600.
      --process-check-interval PROCESS_CHECK_INTERVAL
                            Number of seconds to wait between checking the status
                            of processes. Defaults to 10.
      --process-termination-grace-period PROCESS_TERMINATION_GRACE_PERIOD
                            Number of seconds to wait after sending SIGTERM to a
                            process, but before killing it with SIGKILL. Defaults
                            to 30.
      --enable-status-update-listener
                            Listen for status updates from the process, sent on
                            the status socket port via UDP. If not specified,
                            status update messages will not be read.
      --status-update-socket-port STATUS_UPDATE_SOCKET_PORT
                            The port used to receive status updates from the
                            process. Defaults to 2373.
      --status-update-message-max-bytes STATUS_UPDATE_MESSAGE_MAX_BYTES
                            The maximum number of bytes status update messages can
                            be. Defaults to 65536.
      --status-update-interval STATUS_UPDATE_INTERVAL
                            Minimum of seconds to wait between sending status
                            updates to the API server. -1 means to not send status
                            updates except with heartbeats. Defaults to -1.
      --send-pid            Send the process ID to the API server
      --send-hostname       Send the hostname to the API server
      --send-aws-metadata   Send metadata from AWS about the runtime environment
      --deployment DEPLOYMENT
                            Deployment name (production, staging, etc.)
      --schedule SCHEDULE   Run schedule reported to the API server
      --resolved-env-ttl RESOLVED_ENV_TTL
                            Number of seconds to cache resolved environment
                            variables instead of refreshing them when a process
                            restarts. -1 means to never refresh. Defaults to -1.
      --rollbar-access-token ROLLBAR_ACCESS_TOKEN
                            Access token for Rollbar (used to report error when
                            communicating with API server)
      --rollbar-retries ROLLBAR_RETRIES
                            Number of retries per Rollbar request. Defaults to 2.
      --rollbar-retry-delay ROLLBAR_RETRY_DELAY
                            Number of seconds to wait before retrying a Rollbar
                            request. Defaults to 600.
      --rollbar-timeout ROLLBAR_TIMEOUT
                            Timeout for contacting Rollbar server, in seconds.
                            Defaults to 30.

Environment variables read, take precedence over command-line arguments:

* PROC_WRAPPER_TASK_NAME
* PROC_WRAPPER_TASK_UUID
* PROC_WRAPPER_TASK_EXECUTION_UUID
* PROC_WRAPPER_TASK_IS_SERVICE
* PROC_WRAPPER_MAX_CONCURRENCY
* PROC_WRAPPER_PREVENT_OFFLINE_EXECUTION
* PROC_WRAPPER_TASK_VERSION_NUMBER
* PROC_WRAPPER_TASK_VERSION_TEXT
* PROC_WRAPPER_TASK_VERSION_SIGNATURE
* PROC_WRAPPER_TASK_INSTANCE_METADATA
* PROC_WRAPPER_LOG_LEVEL
* PROC_WRAPPER_DEPLOYMENT
* PROC_WRAPPER_API_BASE_URL
* PROC_WRAPPER_API_KEY
* PROC_WRAPPER_API_HEARTBEAT_INTERVAL_SECONDS
* PROC_WRAPPER_API_RETRIES
* PROC_WRAPPER_API_RETRIES_FOR_TASK_CREATION_CONFLICT
* PROC_WRAPPER_API_RETRIES_FOR_FINAL_UPDATE
* PROC_WRAPPER_API_RETRY_DELAY_SECONDS
* PROC_WRAPPER_API_TIMEOUT_SECONDS
* PROC_WRAPPER_API_RESUME_DELAY_SECONDS
* PROC_WRAPPER_SEND_PID
* PROC_WRAPPER_SEND_HOSTNAME
* PROC_WRAPPER_ROLLBAR_ACCESS_TOKEN
* PROC_WRAPPER_ROLLBAR_TIMEOUT_SECONDS
* PROC_WRAPPER_ROLLBAR_RETRIES
* PROC_WRAPPER_ROLLBAR_RETRY_DELAY_SECONDS
* PROC_WRAPPER_MAX_CONFLICTING_AGE_SECONDS
* PROC_WRAPPER_WORK_DIR
* PROC_WRAPPER_PROCESS_MAX_RETRIES
* PROC_WRAPPER_PROCESS_TIMEOUT_SECONDS
* PROC_WRAPPER_PROCESS_RETRY_DELAY_SECONDS
* PROC_WRAPPER_PROCESS_CHECK_INTERVAL_SECONDS
* PROC_WRAPPER_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS
* PROC_WRAPPER_STATUS_UPDATE_SOCKET_PORT
* PROC_WRAPPER_STATUS_UPDATE_MESSAGE_MAX_BYTES

The command is executed with the same environment that the wrapper script gets,
except that these properties are copied/overridden:

* PROC_WRAPPER_DEPLOYMENT
* PROC_WRAPPER_API_BASE_URL
* PROC_WRAPPER_API_KEY
* PROC_WRAPPER_API_RETRIES
* PROC_WRAPPER_API_RETRY_DELAY_SECONDS
* PROC_WRAPPER_API_TIMEOUT_SECONDS
* PROC_WRAPPER_ROLLBAR_ACCESS_TOKEN
* PROC_WRAPPER_ROLLBAR_TIMEOUT_SECONDS
* PROC_WRAPPER_ROLLBAR_RETRIES
* PROC_WRAPPER_ROLLBAR_RETRY_DELAY_SECONDS
* PROC_WRAPPER_ROLLBAR_RESUME_DELAY_SECONDS
* PROC_WRAPPER_TASK_EXECUTION_UUID
* PROC_WRAPPER_TASK_UUID
* PROC_WRAPPER_TASK_NAME
* PROC_WRAPPER_TASK_VERSION_NUMBER
* PROC_WRAPPER_TASK_VERSION_TEXT
* PROC_WRAPPER_TASK_VERSION_SIGNATURE
* PROC_WRAPPER_TASK_INSTANCE_METADATA
* PROC_WRAPPER_SCHEDULE
* PROC_WRAPPER_PROCESS_TIMEOUT_SECONDS
* PROC_WRAPPER_MAX_CONCURRENCY
* PROC_WRAPPER_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS
* PROC_WRAPPER_ENABLE_STATUS_UPDATE_LISTENER
* PROC_WRAPPER_STATUS_UPDATE_SOCKET_PORT
* PROC_WRAPPER_STATUS_UPDATE_INTERVAL_SECONDS
* PROC_WRAPPER_STATUS_UPDATE_MESSAGE_MAX_BYTES

### Embedded mode

In embedded mode, you include this package in your python project's
dependencies. To run a task you want to be monitored:

    def fun(data):
        print(data)
        return data['a']

    args = ProcWrapper.make_default_args()
    args.offline_mode = True
    args.task_name = 'embedded_test'
    proc_wrapper = ProcWrapper(args=args)
    proc_wrapper.managed_call(fun, {'a': 1, 'b': 2})

## Contributors ✨

Thanks goes to these wonderful people ([emoji key](https://allcontributors.org/docs/en/emoji-key)):

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<!-- markdownlint-enable -->
<!-- prettier-ignore-end -->
<!-- ALL-CONTRIBUTORS-LIST:END -->

This project follows the [all-contributors](https://github.com/all-contributors/all-contributors) specification. Contributions of any kind welcome!

## Credits

This package was created with
[Cookiecutter](https://github.com/audreyr/cookiecutter) and the
[browniebroke/cookiecutter-pypackage](https://github.com/browniebroke/cookiecutter-pypackage)
project template.
