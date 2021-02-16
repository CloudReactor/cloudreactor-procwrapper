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

Wraps the execution of processes so that a service API endpoint (
[CloudReactor](https://cloudreactor.io/))
can monitor and manage them. Also implements retries, timeouts, and
secret injection from AWS into the environment.

## Installation

Install this via pip (or your favourite package manager):

`pip install cloudreactor-procwrapper`

Fetching secrets from AWS Secrets Manager requires that
[boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html) is available to import in your python environment.

JSON Path transformation requires that [jsonpath-ng](https://github.com/h2non/jsonpath-ng)
be available to import in your python environment.

You can get the tested versions of both dependencies in
[proc_wrapper-requirements.in](https://github.com/CloudReactor/cloudreactor-procwrapper/blob/main/proc_wrapper-requirements.in)
(suitable for use by [https://github.com/jazzband/pip-tools/](pip-tools)) or the resolved requirements in
[proc_wrapper-requirements.txt](https://github.com/CloudReactor/cloudreactor-procwrapper/blob/main/proc_wrapper-requirements.txt).


## Usage

### Wrapped mode

In wrapped mode, you run the module with a command line which it
executes in a child process. The command can be implemented in whatever
programming language the running machine supports.

Instead of running

    somecommand --somearg x

you would run

    python -m proc_wrapper somecommand --somearg x

Here are all the options:

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
                                  [--api-error-timeout API_ERROR_TIMEOUT]
                                  [--api-final-update-timeout API_FINAL_UPDATE_TIMEOUT]
                                  [--api-retry-delay API_RETRY_DELAY]
                                  [--api-resume-delay API_RESUME_DELAY]
                                  [--api-task-execution-creation-error-timeout API_TASK_EXECUTION_CREATION_ERROR_TIMEOUT]
                                  [--api-task-execution-creation-conflict-timeout API_TASK_EXECUTION_CREATION_CONFLICT_TIMEOUT]
                                  [--api-task-execution-creation-conflict-retry-delay API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY]
                                  [--api-request-timeout API_REQUEST_TIMEOUT]
                                  [--offline-mode] [--service]
                                  [--max-concurrency MAX_CONCURRENCY]
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
                                  [--no-send-runtime-metadata]
                                  [--deployment DEPLOYMENT] [--schedule SCHEDULE]
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
                            Defaults to 30 for concurrency limited services, 300
                            otherwise.
      --api-error-timeout API_ERROR_TIMEOUT
                            Number of seconds to wait while receiving recoverable
                            errors from the API server. Defaults to 300.
      --api-final-update-timeout API_FINAL_UPDATE_TIMEOUT
                            Number of seconds to wait while receiving recoverable
                            errors from the API server when sending the final
                            update before exiting. Defaults to 1800.
      --api-retry-delay API_RETRY_DELAY
                            Number of seconds to wait before retrying an API
                            request. Defaults to 120.
      --api-resume-delay API_RESUME_DELAY
                            Number of seconds to wait before resuming API
                            requests, after retries are exhausted. Defaults to
                            600. -1 means no resumption.
      --api-task-execution-creation-error-timeout API_TASK_EXECUTION_CREATION_ERROR_TIMEOUT
                            Number of seconds to keep retrying Task Execution
                            creation while receiving error responses from the API
                            server. -1 means to keep trying indefinitely. Defaults
                            to 300.
      --api-task-execution-creation-conflict-timeout API_TASK_EXECUTION_CREATION_CONFLICT_TIMEOUT
                            Number of seconds to keep retrying Task Execution
                            creation while conflict is detected by the API server.
                            -1 means to keep trying indefinitely. Defaults to 1800
                            for concurrency limited services, 0 otherwise.
      --api-task-execution-creation-conflict-retry-delay API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY
                            Number of seconds between attempts to retry Task
                            Execution creation after conflict is detected.
                            Defaults to 60 for concurrency-limited services, 120
                            otherwise.
      --api-request-timeout API_REQUEST_TIMEOUT
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
                            Defaults to 60.
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
      --no-send-runtime-metadata
                            Do not send metadata about the runtime environment
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
                            request. Defaults to 120.
      --rollbar-timeout ROLLBAR_TIMEOUT
                            Timeout for contacting Rollbar server, in seconds.
                            Defaults to 30.

These environment variables take precedence over command-line arguments:

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
* PROC_WRAPPER_API_ERROR_TIMEOUT_SECONDS
* PROC_WRAPPER_API_RETRY_DELAY_SECONDS
* PROC_WRAPPER_API_RESUME_DELAY_SECONDS
* PROC_WRAPPER_API_TASK_EXECUTION_CREATION_ERROR_TIMEOUT_SECONDS
* PROC_WRAPPER_API_TASK_EXECUTION_CREATION_CONFLICT_TIMEOUT_SECONDS
* PROC_WRAPPER_API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY_SECONDS
* PROC_WRAPPER_API_FINAL_UPDATE_TIMEOUT_SECONDS
* PROC_WRAPPER_API_REQUEST_TIMEOUT_SECONDS
* PROC_WRAPPER_SEND_PID
* PROC_WRAPPER_SEND_HOSTNAME
* PROC_WRAPPER_SEND_RUNTIME_METADATA
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
* PROC_WRAPPER_API_ERROR_TIMEOUT_SECONDS
* PROC_WRAPPER_API_RETRY_DELAY_SECONDS
* PROC_WRAPPER_API_RESUME_DELAY_SECONDS
* PROC_WRAPPER_API_REQUEST_TIMEOUT_SECONDS
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
* PROC_WRAPPER_PREVENT_OFFLINE_EXECUTION
* PROC_WRAPPER_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS
* PROC_WRAPPER_ENABLE_STATUS_UPDATE_LISTENER
* PROC_WRAPPER_STATUS_UPDATE_SOCKET_PORT
* PROC_WRAPPER_STATUS_UPDATE_INTERVAL_SECONDS
* PROC_WRAPPER_STATUS_UPDATE_MESSAGE_MAX_BYTES

Wrapped mode is suitable for running in a shell on your own (virtual) machine or
in a Docker container.

### Embedded mode

In embedded mode, you include this package in your python project's
dependencies. To run a task you want to be monitored:

    from proc_wrapper import ProcWrapper

    def fun(data, config):
        print(data)
        return data['a']

    args = ProcWrapper.make_default_args()
    args.offline_mode = True
    args.task_name = 'embedded_test'
    proc_wrapper = ProcWrapper(args=args)
    proc_wrapper.managed_call(fun, {'a': 1, 'b': 2})

This is suitable for running in single-threaded environments like
AWS Lambda. We do not yet support monitoring your task in embedded mode
with CloudReactor, but will do so in the near future.

### Secret Fetching

Both usage modes can fetch secrets from
[AWS Secrets Manager](https://aws.amazon.com/secrets-manager/), optionally extract
embedded data, then inject them into the environment (in the case of wrapped mode)
or a configuration dictionary (in the case of embedded mode).

To enable secret resolution, set environment variable

    PROC_WRAPPER_RESOLVE_SECRETS

to

    TRUE

Then to resolve the target environment variable `MY_SECRET`
by fetching from AWS Secrets Manager, define the environment variable

    AWS_SM_MY_SECRET_FOR_PROC_WRAPPER_TO_RESOLVE

set to the ARN of the secret, for example:

    arn:aws:secretsmanager:us-east-2:1234567890:secret:config-PPrpY

Then when the wrapped process is run, it will see the environment variable
MY_SECRET resolved to the value of the secret in Secrets Manager. Or, if
running in embedded mode, the `config` dict argument will have the key
`MY_SECRET` mapped to the value of the secret.

If the secret was stored in Secrets Manager as binary, the
corresponding environment variable will be set to the Base-64 encoded value.

boto3 is used to fetch secrets. It will try to access to AWS Secrets Manager
using environment variables AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY if they
are set, or use the EC2 instance role, ECS task role, or Lambda execution role
if available.

You can also use "partial ARNs" (without the hyphened suffix) as keys.
In the example above

    arn:aws:secretsmanager:us-east-2:1234567890:secret:config

could be used to fetch the same secret, provided there are no conflicting secret ARNs.

### Secret Tranformation

Secret fetching is relatively expensive and it makes sense to group related
secrets together. Therefore it is common to store JSON values as secrets.
To facilitate this, pieces of JSON values can be extracted to individual
environment variables using [jsonpath-ng](https://github.com/h2non/jsonpath-ng).
To specify that a variable be extracted from a JSON value using
a JSON Path expression, append "|JP:" followed by the JSON Path expression
in the environment variable value. For example, if the AWS Secrets Manager
ARN is

    arn:aws:secretsmanager:us-east-2:1234567890:secret:dbconfig-PPrpY

and the value is

    {
      "username": "postgres",
      "password": "badpassword"
    }

Then you can populate the environment variable `DB_USERNAME` by setting the
environment variable

    AWS_SM_DB_USERNAME_FOR_PROC_WRAPPER_TO_RESOLVE

to

    arn:aws:secretsmanager:us-east-2:1234567890:secret:dbconfig-PPrpY|JP:$.username

If you do something similar to get the password from the same JSON value, proc_wrapper is
smart enough to cache the JSON value, so that the secret is only fetched once.

Since JSON path expressions yield a list of results, we implement the following rules to
transform the list to the environment variable value:

1. If the list of results has a single value, that value is used as the environment variable value,
unless `[*]` is appended to the JSON path expression. If the value is boolean, the value
will be converted to either "TRUE" or "FALSE". If the value is a string or number, it will
be simply left/converted to a string. Otherwise, the value is serialized to a JSON string
and set to the environment variable value.
2. Otherwise, the list of results is serialized to a JSON string and set to the environment variable value.

### Secrets Refreshing

You can set a Time to Live (TTL) on the duration that secrets are cached,
using the --resolved-env-ttl command argument or PROC_WRAPPER_RESOLVED_ENV_TTL_SECONDS environment variable.

If your process exits, you have configured the script to retry, and the TTL has expired since the last fetch, proc_wrapper will re-fetch the secrets
and resolve them again, for the environment passed to the next invocation of
your process.

### Status Updates

As your process or function runs, you can send status updates to CloudReactor by using
the StatusUpdater class. Status updates are shown in the CloudReactor dashboard and
allow you to track the current progress of a Task and also how many items are being
processed in multiple executions over time.

In wrapped mode, your application code would send updates to the proc_wrapper
prorgram via UDP port 2373 (configurable with the PROC_WRAPPER_STATUS_UPDATE_PORT
environment variable). If your application code is in python, you can use the
provided StatusUpdater class to do this:

    from proc_wrapper import StatusUpdater

    with StatusUpdater() as updater:
        updater.send_update(last_status_message='Starting ...')
        success_count = 0

        for i in range(100):
            try:
                do_work()
                success_count += 1
                updater.send_update(success_count=success_count)
            except Exception:
                failed_count += 1
                updater.send_update(failed_count=failed_count)

        updater.send_update(last_status_message='Finished!')


## Example Project

The [cloudreactor-ecs-quickstart](https://github.com/CloudReactor/cloudreactor-ecs-quickstart)
project uses this library to deploy some sample tasks, written in python, to CloudReactor,
running using AWS ECS Fargate.

## License

This software is dual-licensed under open source (MPL 2.0) and commercial licenses. See `LICENSE` for details.

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
