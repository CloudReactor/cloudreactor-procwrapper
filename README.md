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

Wraps the execution of processes so that an API server
([CloudReactor](https://cloudreactor.io/))
can monitor and manage them.
Available as a standalone executable or as a python module.

## Features

* Runs either processes started with a command line or a python function you
supply
* Implements retries and time limits on wrapped processes
* Injects secrets from AWS Secrets Manager, AWS S3, or local files and extracts
them into the process environment (for command-lines) or configuration (for
functions)
* When used with the CloudReactor service:
  * Reports when a process/function starts and when it exits, along with the
  exit code
  * Sends heartbeats, optionally with status information like the number of
  items processed
  * Prevents too many concurrent executions
  * Stops execution when manually stopped in the CloudReactor dashboard

## How it works

First, secrets and other configuration are fetched and resolved from
providers like AWS Secrets Manager, AWS S3, or the local filesystem.

Just before your code runs, the module requests the API server to create a
Task Execution associated with the Task name or UUID which you pass to the
module.
The API server may reject the request if too many instances of the Task are
currently running, but otherwise records that a Task Execution has started.
The module then passes control to your code.

While your code is running, it may report progress to the API server,
and the API server may signal that your Task stop execution (due to
user manually stopping the Task Execution), in which
case the module terminates your code and exits.

After your code finishes, the module informs the API server of
the exit code or result. CloudReactor monitors Tasks to ensure they
are still responsive, and keeps a history of the Executions of Tasks,
allowing you to view failures and run durations in the past.

### Auto-created Tasks

Before your Task is run (including this module),
the [AWS ECS CloudReactor Deployer](https://github.com/CloudReactor/aws-ecs-cloudreactor-deployer)
can be used to set it up in AWS ECS,
and inform CloudReactor of details of your Task.
That way CloudReactor can start and schedule your Task, and setup your
Task as a service.
See [CloudReactor python ECS QuickStart](https://github.com/CloudReactor/cloudreactor-python-ecs-quickstart)
for an example.

However, it may not be possible or desired to change your deployment process.
Instead, you may configure the Task to be *auto-created*.

Auto-created Tasks are created the first time your Task runs.
This means there is no need to inform the API server of the Task details
(during deployment) before it runs.
Instead, each time the module runs, it informs the API server of the
Task details at the same time as it requests the creation of a Task Execution.
One disadvantage of auto-created Tasks is that they are not available
in the CloudReactor dashboard until the first time they run.

When configuring a Task to be auto-created, you must specify the name
or UUID of the Run Environment in CloudReactor that the Task is
associated with. The Run Environment must be created ahead of time,
either by the Cloudreactor AWS Setup Wizard,
or manually in the CloudReactor dashboard.

You can also specify more Task properties, such as Alert Methods and
external links in the dashboard, by setting the environment variable
`PROC_WRAPPER_AUTO_CREATE_TASK_PROPS` set to a JSON-encoded object that has the
[CloudReactor Task schema](https://apidocs.cloudreactor.io/#operation/api_v1_tasks_create).

### Execution Methods

CloudReactor currently supports two Execution Methods:

1) [AWS ECS (in Fargate)](https://aws.amazon.com/fargate/)
2) Unknown

If a Task is running in AWS ECS, CloudReactor is able to run additional
Task Executions, provided the details of running the Task is provided
during deployment with the AWS ECS CloudReactor Deployer, or if the
Task is configured to be auto-created, and this module is run. In the
second case, this module uses the ECS Metadata endpoint to detect
the ECS Task settings, and sends them to the API server. CloudReactor
can also schedule Tasks or setup long-running services using Tasks,
provided they are run in AWS ECS.

However, a Task may use the Unknown execution method if it is not running
in AWS ECS. If that is the case, CloudReactor won't be able to
start the Task in the dashboard or as part of a Workflow,
schedule the Task, or setup a service with the Task. But the advantage is
that the Task code can be executed by any method available to you,
such as bare metal servers, VM's, Docker, AWS Lambda, or Kubernetes.
All Tasks in CloudReactor, regardless of execution method, have their
history kept and are monitored.

This module detects which of the two Execution Methods your Task is
running with and sends that information to the API server, provided
you configure your Task to be auto-created.

### Passive Tasks

Passive Tasks are Tasks that CloudReactor does not manage. This means
scheduling and service setup must be handled by other means
(cron jobs, [supervisord](http://supervisord.org/), etc).
However, Tasks marked as services or that have a schedule will still be
monitored by CloudReactor, which will send notifications if
a service Task goes down or a Task does not run on schedule.

The module reports to the API server that auto-created Tasks are passive,
unless you specify the `--force-task-passive` commmand-line option or
set the environment variable `PROC_WRAPPER_TASK_IS_PASSIVE` to `FALSE`.
If a Task uses the Unknown Execution Method, it must be marked as passive,
because CloudReactor does not know how to manage it.

## Pre-requisites

If you just want to use this module to retry processes, limit execution time,
or fetch secrets, you can use offline mode, in which case no CloudReactor API
key is required. But CloudReactor offers a free tier so we hope you
[sign up](https://dash.cloudreactor.io/signup)
or a free account to enable monitoring and/or management.

If you want CloudReactor to be able to start your Tasks, you should use the
[Cloudreactor AWS Setup Wizard](https://github.com/CloudReactor/cloudreactor-aws-setup-wizard)
to configure your AWS environment to run Tasks in ECS Fargate.
You can skip this step if running in passive mode is OK for you.

If you want to use CloudReactor to manage or just monitor your Tasks,
you need to create a Run Environment and an API key in the CloudReactor
dashboard. The API key can be scoped to the Run Environment if you
wish. The key must have at least the Task access level, but for
an auto-created Task, it must have at least the Developer access level.

## Installation

### In a Linux/AMD64 or Windows 64 environment

Standalone executables for 64-bit Linux and Windows are available,
located in `pyinstaller_build/platforms`. These executables bundle python
so you don't need to have python installed on your machine. They also bundle
all optional library dependencies so you can fetch secrets from AWS
Secrets Manager and extract them with jsonpath-ng, for example.

On a debian buster machine, the following packages (with known supported versions)
must be installed:

      openssl=1.1.1d-0+deb10u5
      libexpat1=2.2.6-2+deb10u1
      ca-certificates=20200601~deb10u2

See the example [Dockerfile](tests/integration/standalone_executable/docker_context_linux_amd64/Dockerfile) for a known working
environment.

Special thanks to [PyInstaller](https://www.pyinstaller.org/),
[wine](https://www.winehq.org/), and
[PyInstaller Docker Images](https://github.com/cdrx/docker-pyinstaller)
for making this possible!

### When python is available

Install this module via pip (or your favourite package manager):

`pip install cloudreactor-procwrapper`

Fetching secrets from AWS Secrets Manager requires that
[boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html) is available to import in your python environment.

JSON Path transformation requires that [jsonpath-ng](https://github.com/h2non/jsonpath-ng)
be available to import in your python environment.

You can get the tested versions of both dependencies in
[proc_wrapper-requirements.in](https://github.com/CloudReactor/cloudreactor-procwrapper/blob/main/proc_wrapper-requirements.in)
(suitable for use by [pip-tools](https://github.com/jazzband/pip-tools/)) or the resolved requirements in
[proc_wrapper-requirements.txt](https://github.com/CloudReactor/cloudreactor-procwrapper/blob/main/proc_wrapper-requirements.txt).

## Usage

There are two ways of using the module: wrapped mode and embedded mode.

### Wrapped mode

In wrapped mode, you pass a command line to the module which it
executes in a child process. The command can be implemented in whatever
programming language the running machine supports.

Instead of running

    somecommand --somearg x

you would run

    ./proc_wrapper somecommand --somearg x

assuming that are using a standalone executable, and that
you configure the program using environment variables.

Or, if you have python installed:

    python -m proc_wrapper somecommand --somearg x

Here are all the options:

    usage: proc_wrapper [-h] [-v] [-n TASK_NAME] [--task-uuid TASK_UUID] [-a]
                        [--auto-create-task-run-environment-name AUTO_CREATE_TASK_RUN_ENVIRONMENT_NAME]
                        [--auto-create-task-run-environment-uuid AUTO_CREATE_TASK_RUN_ENVIRONMENT_UUID]
                        [--auto-create-task-props AUTO_CREATE_TASK_PROPS]
                        [--force-task-active]
                        [--task-execution-uuid TASK_EXECUTION_UUID]
                        [--task-version-number TASK_VERSION_NUMBER]
                        [--task-version-text TASK_VERSION_TEXT]
                        [--task-version-signature TASK_VERSION_SIGNATURE]
                        [--execution-method-props EXECUTION_METHOD_PROPS]
                        [--task-instance-metadata TASK_INSTANCE_METADATA] [-s]
                        [--schedule SCHEDULE] [--max-concurrency MAX_CONCURRENCY]
                        [--max-conflicting-age MAX_CONFLICTING_AGE]
                        [--api-base-url API_BASE_URL] [-k API_KEY]
                        [--api-heartbeat-interval API_HEARTBEAT_INTERVAL]
                        [--api-error-timeout API_ERROR_TIMEOUT]
                        [--api-final-update-timeout API_FINAL_UPDATE_TIMEOUT]
                        [--api-retry-delay API_RETRY_DELAY]
                        [--api-resume-delay API_RESUME_DELAY]
                        [--api-task-execution-creation-error-timeout API_TASK_EXECUTION_CREATION_ERROR_TIMEOUT]
                        [--api-task-execution-creation-conflict-timeout API_TASK_EXECUTION_CREATION_CONFLICT_TIMEOUT]
                        [--api-task-execution-creation-conflict-retry-delay API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY]
                        [--api-request-timeout API_REQUEST_TIMEOUT] [-o] [-p]
                        [-d DEPLOYMENT] [--send-pid] [--send-hostname]
                        [--no-send-runtime-metadata]
                        [-l {DEBUG,INFO,WARNING,ERROR,CRITICAL}] [--log-secrets]
                        [-w WORK_DIR] [-t PROCESS_TIMEOUT]
                        [-r PROCESS_MAX_RETRIES]
                        [--process-retry-delay PROCESS_RETRY_DELAY]
                        [--process-check-interval PROCESS_CHECK_INTERVAL]
                        [--process-termination-grace-period PROCESS_TERMINATION_GRACE_PERIOD]
                        [--enable-status-update-listener]
                        [--status-update-socket-port STATUS_UPDATE_SOCKET_PORT]
                        [--status-update-message-max-bytes STATUS_UPDATE_MESSAGE_MAX_BYTES]
                        [--status-update-interval STATUS_UPDATE_INTERVAL]
                        [-e ENV_LOCATIONS] [-c CONFIG_LOCATIONS]
                        [--config-merge-strategy {SHALLOW,REPLACE,ADDITIVE,TYPESAFE_REPLACE,TYPESAFE_ADDITIVE}]
                        [--overwrite_env_during_resolution]
                        [--config-ttl CONFIG_TTL]
                        [--no-fail-fast-config-resolution]
                        [--resolved-env-var-name-prefix RESOLVED_ENV_VAR_NAME_PREFIX]
                        [--resolved-env-var-name-suffix RESOLVED_ENV_VAR_NAME_SUFFIX]
                        [--resolved-config-property-name-prefix RESOLVED_CONFIG_PROPERTY_NAME_PREFIX]
                        [--resolved-config-property-name-suffix RESOLVED_CONFIG_PROPERTY_NAME_SUFFIX]
                        [--env-var-name-for-config ENV_VAR_NAME_FOR_CONFIG]
                        [--config-property-name-for-env CONFIG_PROPERTY_NAME_FOR_ENV]
                        [--rollbar-access-token ROLLBAR_ACCESS_TOKEN]
                        [--rollbar-retries ROLLBAR_RETRIES]
                        [--rollbar-retry-delay ROLLBAR_RETRY_DELAY]
                        [--rollbar-timeout ROLLBAR_TIMEOUT]
                        ...

    Wraps the execution of processes so that a service API endpoint (CloudReactor)
    is optionally informed of the progress. Also implements retries, timeouts, and
    secret injection into the environment.

    positional arguments:
      command

    optional arguments:
      -h, --help            show this help message and exit
      -v, --version         Print the version and exit

    task:
      Task settings

      -n TASK_NAME, --task-name TASK_NAME
                            Name of Task (either the Task Name or the Task UUID
                            must be specified
      --task-uuid TASK_UUID
                            UUID of Task (either the Task Name or the Task UUID
                            must be specified)
      -a, --auto-create-task
                            Create the Task even if not known by the API server
      --auto-create-task-run-environment-name AUTO_CREATE_TASK_RUN_ENVIRONMENT_NAME
                            Name of the Run Environment to use if auto-creating
                            the Task (either the name or UUID of the Run
                            Environment must be specified if auto-creating the
                            Task). Defaults to the deployment name if the Run
                            Environment UUID is not specified.
      --auto-create-task-run-environment-uuid AUTO_CREATE_TASK_RUN_ENVIRONMENT_UUID
                            UUID of the Run Environment to use if auto-creating
                            the Task (either the name or UUID of the Run
                            Environment must be specified if auto-creating the
                            Task)
      --auto-create-task-props AUTO_CREATE_TASK_PROPS
                            Additional properties of the auto-created Task, in
                            JSON format. See https://apidocs.cloudreactor.io/#oper
                            ation/api_v1_tasks_create for the schema.
      --force-task-active   Indicates that the auto-created Task should be
                            scheduled and made a service by the API server, if
                            applicable. Otherwise, auto-created Tasks are marked
                            passive.
      --task-execution-uuid TASK_EXECUTION_UUID
                            UUID of Task Execution to attach to
      --task-version-number TASK_VERSION_NUMBER
                            Numeric version of the Task's source code
      --task-version-text TASK_VERSION_TEXT
                            Human readable version of the Task's source code
      --task-version-signature TASK_VERSION_SIGNATURE
                            Version signature of the Task's source code (such as a
                            git commit hash)
      --execution-method-props EXECUTION_METHOD_PROPS
                            Additional properties of the execution method, in JSON
                            format. See https://apidocs.cloudreactor.io/#operation
                            /api_v1_task_executions_create for the schema.
      --task-instance-metadata TASK_INSTANCE_METADATA
                            Additional metadata about the Task instance, in JSON
                            format
      -s, --service         Indicate that this is a Task that should run
                            indefinitely
      --schedule SCHEDULE   Run schedule reported to the API server
      --max-concurrency MAX_CONCURRENCY
                            Maximum number of concurrent Task Executions allowed
                            with the same Task UUID. Defaults to 1.
      --max-conflicting-age MAX_CONFLICTING_AGE
                            Maximum age of conflicting Tasks to consider, in
                            seconds. -1 means no limit. Defaults to the heartbeat
                            interval, plus 60 seconds for services that send
                            heartbeats. Otherwise, defaults to no limit.

    api:
      API client settings

      --api-base-url API_BASE_URL
                            Base URL of API server. Defaults to
                            https://api.cloudreactor.io
      -k API_KEY, --api-key API_KEY
                            API key. Must have at least the Task access level, or
                            Developer access level for auto-created Tasks.
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
      -o, --offline-mode    Do not communicate with or rely on an API server
      -p, --prevent-offline-execution
                            Do not start processes if the API server is
                            unavailable.
      -d DEPLOYMENT, --deployment DEPLOYMENT
                            Deployment name (production, staging, etc.)
      --send-pid            Send the process ID to the API server
      --send-hostname       Send the hostname to the API server
      --no-send-runtime-metadata
                            Do not send metadata about the runtime environment

    log:
      Logging settings

      -l {DEBUG,INFO,WARNING,ERROR,CRITICAL}, --log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                            Log level
      --log-secrets         Log sensitive information

    process:
      Process settings

      -w WORK_DIR, --work-dir WORK_DIR
                            Working directory. Defaults to the current directory.
      -t PROCESS_TIMEOUT, --process-timeout PROCESS_TIMEOUT
                            Timeout for process, in seconds. -1 means no timeout,
                            which is the default.
      -r PROCESS_MAX_RETRIES, --process-max-retries PROCESS_MAX_RETRIES
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

    updates:
      Status update settings

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

    configuration:
      Environment/configuration resolution settings

      -e ENV_LOCATIONS, --env ENV_LOCATIONS
                            Location of either local file, AWS S3 ARN, or AWS
                            Secrets Manager ARN containing properties used to
                            populate the environment for embedded mode, or the
                            process environment for wrapped mode. By default, the
                            file format is assumed to be dotenv. Specify multiple
                            times to include multiple locations.
      -c CONFIG_LOCATIONS, --config CONFIG_LOCATIONS
                            Location of either local file, AWS S3 ARN, or AWS
                            Secrets Manager ARN containing properties used to
                            populate the configuration for embedded mode. By
                            default, the file format is assumed to be in JSON.
                            Specify multiple times to include multiple locations.
      --config-merge-strategy {SHALLOW,REPLACE,ADDITIVE,TYPESAFE_REPLACE,TYPESAFE_ADDITIVE}
                            Merge strategy for merging config files with
                            mergedeep. Defaults to SHALLOW, which does not require
                            mergedeep. All other strategies require the mergedeep
                            python package to be installed.
      --overwrite_env_during_resolution
                            Do not overwrite existing environment variables when
                            resolving them
      --config-ttl CONFIG_TTL
                            Number of seconds to cache resolved environment
                            variables and configuration properties instead of
                            refreshing them when a process restarts. -1 means to
                            never refresh. Defaults to -1.
      --no-fail-fast-config-resolution
                            Exit immediately if an error occurs resolving the
                            configuration
      --resolved-env-var-name-prefix RESOLVED_ENV_VAR_NAME_PREFIX
                            Required prefix for names of environment variables
                            that should resolved. The prefix will be removed in
                            the resolved variable name. Defaults to ''.
      --resolved-env-var-name-suffix RESOLVED_ENV_VAR_NAME_SUFFIX
                            Required suffix for names of environment variables
                            that should resolved. The suffix will be removed in
                            the resolved variable name. Defaults to
                            '_FOR_PROC_WRAPPER_TO_RESOLVE'.
      --resolved-config-property-name-prefix RESOLVED_CONFIG_PROPERTY_NAME_PREFIX
                            Required prefix for names of configuration properties
                            that should resolved. The prefix will be removed in
                            the resolved property name. Defaults to ''.
      --resolved-config-property-name-suffix RESOLVED_CONFIG_PROPERTY_NAME_SUFFIX
                            Required suffix for names of configuration properties
                            that should resolved. The suffix will be removed in
                            the resolved property name. Defaults to
                            '__to_resolve'.
      --env-var-name-for-config ENV_VAR_NAME_FOR_CONFIG
                            The name of the environment variable used to set to
                            the value of the JSON encoded configuration. Defaults
                            to not setting any environment variable.
      --config-property-name-for-env CONFIG_PROPERTY_NAME_FOR_ENV
                            The name of the configuration property used to set to
                            the value of the JSON encoded environment. Defaults to
                            not setting any property.

    rollbar:
      Rollbar settings

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
* PROC_WRAPPER_AUTO_CREATE_TASK (TRUE or FALSE)
* PROC_WRAPPER_AUTO_CREATE_TASK_RUN_ENVIRONMENT_NAME
* PROC_WRAPPER_AUTO_CREATE_TASK_RUN_ENVIRONMENT_UUID
* PROC_WRAPPER_AUTO_CREATE_TASK_PROPS (JSON encoded property map)
* PROC_WRAPPER_TASK_IS_PASSIVE (TRUE OR FALSE)
* PROC_WRAPPER_TASK_IS_SERVICE (TRUE or FALSE)
* PROC_WRAPPER_EXECUTION_METHOD_PROPS (JSON encoded property map)
* PROC_WRAPPER_TASK_MAX_CONCURRENCY (can be set to -1 to indicate no limit)
* PROC_WRAPPER_PREVENT_OFFLINE_EXECUTION (TRUE or FALSE)
* PROC_WRAPPER_TASK_VERSION_NUMBER
* PROC_WRAPPER_TASK_VERSION_TEXT
* PROC_WRAPPER_TASK_VERSION_SIGNATURE
* PROC_WRAPPER_TASK_INSTANCE_METADATA
* PROC_WRAPPER_LOG_LEVEL
* PROC_WRAPPER_LOG_SECRETS
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
* PROC_WRAPPER_ENV_LOCATIONS
* PROC_WRAPPER_CONFIG_LOCATIONS
* PROC_WRAPPER_OVERWRITE_ENV_WITH_SECRETS
* PROC_WRAPPER_RESOLVE_SECRETS
* PROC_WRAPPER_MAX_CONFIG_RESOLUTION_DEPTH
* PROC_WRAPPER_MAX_CONFIG_RESOLUTION_ITERATIONS
* PROC_WRAPPER_CONFIG_TTL_SECONDS
* PROC_WRAPPER_FAIL_FAST_CONFIG_RESOLUTION
* PROC_WRAPPER_RESOLVABLE_ENV_VAR_NAME_PREFIX
* PROC_WRAPPER_RESOLVABLE_ENV_VAR_NAME_SUFFIX
* PROC_WRAPPER_RESOLVABLE_CONFIG_PROPERTY_NAME_PREFIX
* PROC_WRAPPER_RESOLVABLE_CONFIG_PROPERTY_NAME_SUFFIX
* PROC_WRAPPER_ENV_VAR_NAME_FOR_CONFIG
* PROC_WRAPPER_CONFIG_PROPERTY_NAME_FOR_ENV
* PROC_WRAPPER_SEND_PID (TRUE or FALSE)
* PROC_WRAPPER_SEND_HOSTNAME (TRUE or FALSE)
* PROC_WRAPPER_SEND_RUNTIME_METADATA (TRUE or FALSE)
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

With the exception of the settings for Secret Fetching and Resolution,
these environment variables are read after Secret Fetching so that they can
come from secret values.

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
* PROC_WRAPPER_TASK_MAX_CONCURRENCY
* PROC_WRAPPER_PREVENT_OFFLINE_EXECUTION
* PROC_WRAPPER_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS
* PROC_WRAPPER_ENABLE_STATUS_UPDATE_LISTENER
* PROC_WRAPPER_STATUS_UPDATE_SOCKET_PORT
* PROC_WRAPPER_STATUS_UPDATE_INTERVAL_SECONDS
* PROC_WRAPPER_STATUS_UPDATE_MESSAGE_MAX_BYTES

Wrapped mode is suitable for running in a shell on your own (virtual) machine
or in a Docker container. It requires multi-process support, as the module
runs at the same time as the command it wraps.

### Embedded mode

Embedded mode works for executing python code in the same process.
You include the `proc_wrapper` package in your python project's
dependencies. To run a task you want to be monitored:

    from typing import Any, Dict, Mapping

    from proc_wrapper import ProcWrapper, ProcWrapperParams


    def fun(wrapper: ProcWrapper, cbdata: Dict[str, int],
            config: Mapping[str, Any]) -> int:
        print(cbdata)
        return cbdata['a']

    params = ProcWrapperParams()
    params.auto_create_task = True
    params.auto_create_run_environment_name = 'production'
    params.task_name = 'embedded_test'

    # For example only, in the real world you would use Secret Fetching;
    # see below.
    params.api_key = 'YOUR_CLOUDREACTOR_API_KEY'
    proc_wrapper = ProcWrapper(params=params)
    x = proc_wrapper.managed_call(fun, {'a': 1, 'b': 2})
    # Should print 1
    print(x)


This is suitable for running in single-threaded environments like
AWS Lambda, or as part of a larger process that executes
sub-routines that should be monitored.

Currently, Tasks running as Lambdas must be marked as
passive Tasks, as the execution method is Unknown. In the near future,
CloudReactor will support running and managing Tasks that run as
Lambdas.

## Secret Fetching and Resolution

A common requirement is that deployed code / images do not contain secrets
internally which could be decompiled. Instead, programs should fetch secrets
from an external source in a secure manner. If your program runs in AWS, it
can make use of AWS's roles that have permission to access data in
Secrets Manager or S3. However, in many scenarios, having your program access
AWS directly has the following disadvantages:

1) Your program becomes coupled to AWS, so it is difficult to run locally or
switch to another infrastructure provider
2) You need to write code or use a library for each programming language you
use, so secret fetching is done in a non-uniform way
3) Writing code to merge and parse secrets from different sources is tedious

Therefore, proc_wrapper implements Secret Fetching and Resolution to solve
these problems so your programs don't have to. Both usage modes can fetch secrets from
[AWS Secrets Manager](https://aws.amazon.com/secrets-manager/),
AWS S3, or the local filesystem, and optionally extract embedded data
into the environment or a configuration dictionary. The environment is used to
pass values to processes run in wrapped mode,
while the configuration dictionary is passed to the callback function in
embedded mode.

proc_wrapper parses secret location strings that specify the how to resolve
a secret value. Each secret location string has the format:

`[PROVIDER_CODE:]<Provider specific address>[!FORMAT][|JP:<JSON Path expression>]`

### Secret Providers

Providers indicate the raw source of the secret data. The table below lists the
supported providers:

| Provider Code 	| Value Prefix              	| Provider                     	| Example Address                                             	| Required libs                                                               	| Notes                                                         	|
|---------------	|---------------------------	|------------------------------	|-------------------------------------------------------------	|-----------------------------------------------------------------------------	|---------------------------------------------------------------	|
| `AWS_SM`      	| `arn:aws:secretsmanager:` 	| AWS Secrets Manager          	| `arn:aws:secretsmanager:us-east-2:1234567890:secret:config` 	| [boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html) 	| Can also include version suffix like `-PPrpY`                 	|
| `AWS_S3`      	| `arn:aws:s3:::`           	| AWS S3 Object                	| `arn:aws:s3:::examplebucket/staging/app1/config.json`       	| [boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html) 	|                                                               	|
| `FILE`        	| `file://`                 	| Local file                   	| `file:///home/appuser/app/.env`                                    	|                                                                             	| The default provider if no provider is auto-detected          	|
| `ENV`         	|                           	| The process environment      	| `SOME_TOKEN`                                                	|                                                                             	| The name of another environment variable                      	|
| `CONFIG`      	|                           	| The configuration dictionary 	| `$.db`                                                      	| [jsonpath-ng](https://github.com/h2non/jsonpath-ng)                         	| JSON path expression to extract the data in the configuration 	|
| `PLAIN`       	|                           	| Plaintext                    	| `{"user": "postgres", "password": "badpassword"}`           	|                                                                             	|                                                               	|

If you don't specify an explicit provider prefix in a secret location
(e.g. `AWS_SM:`), the provider can be auto-detected from the address portion
using the Value Prefix. For example the secret location
`arn:aws:s3:::examplebucket/staging/app1/config.json` will be auto-detected
to with the AWS_S3 provider because it starts with `arn:aws:s3:::`.

### Secret Formats

Formats indicate how the raw string data is parsed into a secret value (which may be
a string, number, boolean, dictionary, or array). The table below lists the
supported formats:

| Format Code 	| Extensions      	| MIME types                                                                            	| Required libs                                        	| Notes                                            	|
|-------------	|-----------------	|---------------------------------------------------------------------------------------	|------------------------------------------------------	|--------------------------------------------------	|
| `dotenv`    	| `.env`          	| None                                                                                  	| [dotenv](https://github.com/theskumar/python-dotenv) 	| Also auto-detected if location includes `.env.`  	|
| `json`      	| `.json`         	| `application/json`, `text/x-json`                                                     	|                                               	|  	|
| `yaml`      	| `.yaml`, `.yml` 	| `application/x-yaml`, `application/yaml`, `text/vnd.yaml`, `text/yaml`, `text/x-yaml` 	| [pyyaml](https://pyyaml.org/)                        	| `safe_load()` is used for security               	|

The format of a secret value can be auto-detected from the extension or by the
MIME type if available. Otherwise, you may need to an explicit format code
(e.g. `!yaml`).

#### AWS Secrets Manager / S3 Notes

[boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)
is used to fetch secrets. It will try to access to AWS Secrets Manager
or S3 using environment variables `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`
if they are set, or use the EC2 instance role, ECS task role, or Lambda execution role
if available.

For Secrets Manager, you can also use "partial ARNs"
(without the hyphened suffix) as keys.
In the example above

    arn:aws:secretsmanager:us-east-2:1234567890:secret:config

could be used to fetch the same secret, provided there are no conflicting secret ARNs.
This allows you to get the latest version of the secret.

If the secret was stored in Secrets Manager as binary, the
corresponding value will be set to the Base-64 encoded value.

### Secret Tranformation

Fetching secrets can be relatively expensive and it makes sense to group related
secrets together. Therefore it is common to store dictionaries (formatted
as JSON or YAML) as secrets. However, each desired environment variable
or configuration property may only consist of a fragment of the dictionary.
For example, given the JSON-formatted dictionary

    {
      "username": "postgres",
      "password": "badpassword"
    }

you may want to populate the environment variable `DB_USERNAME` with
`postgres`.

To facilitate this, dictionary fragments can be extracted to individual
environment variables using [jsonpath-ng](https://github.com/h2non/jsonpath-ng).
To specify that a variable be extracted from a dictionary using
a JSON Path expression, append `|JP:` followed by the JSON Path expression
to the secret location string. For example, if the AWS Secrets Manager
ARN

    arn:aws:secretsmanager:us-east-2:1234567890:secret:db-PPrpY

contains the dictionary above, then the secret location string

    arn:aws:secretsmanager:us-east-2:1234567890:secret:db-PPrpY|JP:$.username

will resolve to `postgres` as desired.

If you do something similar to get the password from the same JSON value,
proc_wrapper is smart enough to cache the fetched dictionary, so that the
raw data is only fetched once.

Since JSON path expressions yield a list of results, the secrets fetcher
implements the following rules to transform the list to the final value:

1. If the list of results has a single value, that value is used as the
final value, unless `[*]` is appended to the JSON path expression.
2. Otherwise, the final value is the list of results

#### Fetching from another environment variable

In some deployment scenarios, multiple secrets can be injected into a
single environment variable as a JSON encoded object. In that case,
the module can extract secrets using the *ENV* secret source. For example,
you may have arranged to have the environment variable DB_CONFIG injected
with the JSON encoded value:

    { "username": "postgres", "password": "nohackme" }

Then to extract the username to the environment variable DB_USERNAME you
you would add the environment variable DB_USER_FOR_PROC_WRAPPER_TO_RESOLVE
set to

    ENV:DB_CONFIG|JP:$.username

### Secret injection into environment and configuration

Now let's use secret location strings to
inject the values into the environment (for wrapped mode)
and/or the the configuration dictionary (for embedded mode). proc_wrapper
supports two methods of secret injection which can be combined together:

* Top-level fetching
* Secrets Resolution

### Top-level fetching

Top-level fetching refers to fetching a dictionary that contains multiple secrets
and populating the environment / configuration dictionary with it.
To use top-level fetching, you specify the secret locations
from which you want to fetch the secrets and the corresponding values are
merged together into the environment / configuration.

To use top-level fetching in wrapped mode, populate the
environment variables `PROC_WRAPPER_ENV_LOCATIONS` with a comma-separated
list of secret locations, or use the command-line option
`--env-locations <secret_location>` one or more times. Secret location
strings passed in via `PROC_WRAPPER_ENV_LOCATIONS` or `--env-locations`
will be parsed as `dotenv` files unless format is auto-detected or
explicitly specified.

To use top-level fetching in embedded mode, set the `ProcWrapperParams` property
`config_locations` to a list of secret locations. Secret location values
will be parsed as JSON unless the format is auto-detected or explicitly
specified. The `config` argument
passed to the your callback function will contain a merged dictionary of all
fetched and parsed dictionary values. For example:

    def callback(wrapper: ProcWrapper, cbdata: str,
            config: Dict[str, str]) -> str:
        return 'super' + cbdata + config['username']


    def main():
        params = ProcWrapperParams()
        params.config_locations = [
            'arn:aws:secretsmanager:us-east-2:1234567890:secret:db-PPrpY',
            # More secret locations can be added here, and their values will
            # be merged
        ]

        wrapper = ProcWrapper(params=params)

        # Returns 'superduperpostgres'
        return wrapper.managed_call(callback, 'duper')

#### Merging Secrets

Top-level fetching can potentially fetch multiple dictionaries which are
merged together in the final environment / configuration dictionary.
The default merge strategy (`SHALLOW`) is just to overwrite top-level keys, with later
secret locations taking precedence. However, if you include the
[mergedeep](https://github.com/clarketm/mergedeep) library, you can also
set the merge strategy to one of:

* `REPLACE`
* `ADDITIVE`
* `TYPESAFE_REPLACE`
* `TYPESAFE_ADDITIVE`

so that nested dictionaries / lists will be merged as well. In wrapped mode,
the merge strategy can be set with the `--config-merge-strategy` command-line
argument or `PROC_WRAPPER_CONFIG_MERGE_STRATEGY` environment variable. In
embedded mode, the merge strategy can be set in the
`config_merge_strategy` string property of `ProcWrapperParams`.

### Secret Resolution

Secret Resolution substitutes configuration or environment values that are
secret location strings with the computed values of those strings. Compared
to Secret Fetching, Secret Resolution is more useful when you want more
control over the names of variables or when you have secret values deep
inside your configuration.

In wrapped mode, if you want to set the environment variable `MY_SECRET` with
a value fetched
from AWS Secrets Manager, you would set the environment variable
`MY_SECRET_FOR_PROC_WRAPPER_TO_RESOLVE` to a secret location string
which is ARN of the secret, for example:

    arn:aws:secretsmanager:us-east-2:1234567890:secret:db-PPrpY

(The `_FOR_PROC_WRAPPER_TO_RESOLVE` suffix of environment variable names is
removed during resolution. It can also be configured with the `PROC_WRAPPER_RESOLVABLE_ENV_VAR_NAME_SUFFIX` environment variable.)

In embedded mode, if you want the final configuration dictionary to look like:

    {
      "db_username": "postgres",
      "db_password": "badpassword",
      ...
    }


The initial configuration dictionary would look like:

    {
      "db_username__to_resolve": "arn:aws:secretsmanager:us-east-2:1234567890:secret:db-PPrpY|JP:$.username",
      "db_password__to_resolve": "arn:aws:secretsmanager:us-east-2:1234567890:secret:db-PPrpY|JP:$.password",

      ...
    }

(The `__to_resolve` suffix (with 2 underscores!) of keys is removed during
resolution. It can also be configured with the `resolved_config_property_name_suffix`
property of `ProcWrapperParams`.)

proc_wrapper can also resolve keys in embedded dictionaries, like:

    {
      "db": {
        "username__to_resolve": "arn:aws:secretsmanager:us-east-2:1234567890:secret:config-PPrpY|JP:$.username",
        "password__to_resolve":
        "arn:aws:secretsmanager:us-east-2:1234567890:secret:config-PPrpY|JP:$.password",
        ...
      },
      ...
    }

up to a maximum depth that you can control with `ProcWrapperParams.max_config_resolution_depth` (which defaults to 5). That would resolve to

    {
      "db": {
        "username": "postgres",
        "password": "badpassword"
        ...
      },
      ...
    }

You can also inject entire dictionaries, like:

    {
      "db__to_resolve": "arn:aws:secretsmanager:us-east-2:1234567890:secret:config-PPrpY",
      ...
    }

which would resolve to

    {
      "db": {
        "username": "postgres",
        "password": "badpassword"
      },
      ...
    }

To enable secret resolution in wrapped mode, set environment variable `PROC_WRAPPER_RESOLVE_SECRETS` to `TRUE`. In embedded mode, secret
resolution is enabled by default; set the
`max_config_resolution_iterations` property of `ProcWrapperParams` to `0`
to disable resolution.

Secret resolution is run multiple times so that if a resolved value contains
a secret location string, it will be resolved on the next pass. By default,
proc_wrapper limits the maximum number of resolution passes to 3 but you
can control this with the environment variable
`PROC_WRAPPER_MAX_CONFIG_RESOLUTION_ITERATIONS` in embedded mode,
or by setting the `max_config_resolution_iterations` property of
`ProcWrapperParams` in wrapped mode.

### Environment Projection

During secret fetching and secret resolution, proc_wrapper internally maintains
the computed environment as a dictionary which may have embedded lists and
dictionaries. However, the final environment passed to process is a flat
dictionary containing only string values. So proc_wrapper converts
all top-level values to strings using these rules:

* Lists and dictionaries are converted to their JSON-encoded string value
* Boolean values are converted to their upper-cased string representation
(e.g. the string `FALSE` for the boolean value `false`)
* The `None` value is converted to the empty string
* All other values are converted using python's `str()` function

### Secrets Refreshing

You can set a Time to Live (TTL) on the duration that secret values are cached.
Caching helps reduce expensive lookups of secrets and bandwidth usage.

In wrapped mode, set the TTL of environment variables set from secret locations
using the `--config-ttl` command-line argument or
`PROC_WRAPPER_CONFIG_TTL_SECONDS` environment variable.
If the process exits, you have configured the script to retry,
and the TTL has expired since the last fetch,
proc_wrapper will re-fetch the secrets
and resolve them again, for the environment passed to the next invocation of
your process.

In embedded mode, set the TTL of configuration dictionary values set from
secret locations by setting the `config_ttl` property of
`ProcWrapperParams`. If 1) your callback function raises an exception, 2) you have
configured the script to retry; and 3) the TTL has expired since the last fetch,
proc_wrapper will re-fetch the secrets
and resolve them again, for the configuration passed to the next invocation of
the callback function.

## Status Updates

### Status Updates in Wrapped Mode

While your process in running, you can send status updates to
CloudReactor by using the StatusUpdater class. Status updates are shown in
the CloudReactor dashboard and allow you to track the current progress of a
Task and also how many items are being processed in multiple executions
over time.

In wrapped mode, your application code would send updates to the
proc_wrapper program via UDP port 2373 (configurable with the PROC_WRAPPER_STATUS_UPDATE_PORT environment variable).
If your application code is in python, you can use the provided
StatusUpdater class to do this:

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

### Status Updates in Embedded Mode

In embedded mode, your callback in python code can use the wrapper instance to send updates:

    from typing import Any, Dict, Mapping

    import proc_wrapper
    from proc_wrapper import ProcWrapper

    def fun(wrapper: ProcWrapper, cbdata: Dict[str, int],
            config: Mapping[str, Any]) -> int:
        wrapper.send_update(status_message='Starting the fun ...')

        for i in range(100):
            try:
                do_work()
                success_count += 1
            except Exception:
                failed_count += 1

            # Coalesce updates to avoid using too much bandwidth / API credits
            if (success_count + failed_count) % 10 == 0:
                wrapper.send_update(success_count=success_count,
                        failed_count=failed_count)

        wrapper.send_update(status_message='The fun is over.')

        return cbdata['a']

    params = ProcWrapperParams()
    params.auto_create_task = True
    params.auto_create_run_environment_name = 'production'
    params.task_name = 'embedded_test'
    params.api_key = 'YOUR_CLOUDREACTOR_API_KEY'
    proc_wrapper = ProcWrapper(params=params)
    proc_wrapper.managed_call(fun, {'a': 1, 'b': 2})

## Example Project

The [cloudreactor-python-ecs-quickstart](https://github.com/CloudReactor/cloudreactor-python-ecs-quickstart)
project uses this library to deploy some sample tasks, written in python,
to CloudReactor, running using AWS ECS Fargate.

## License

This software is dual-licensed under open source (MPL 2.0) and commercial licenses. See `LICENSE` for details.

## Contributors ✨

Thanks goes to these wonderful people ([emoji key](https://allcontributors.org/docs/en/emoji-key)):

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tr>
    <td align="center"><a href="https://github.com/jtsay362"><img src="https://avatars0.githubusercontent.com/u/1079646?s=460&v=4?s=80" width="80px;" alt=""/><br /><sub><b>Jeff Tsay</b></sub></a><br /><a href="https://github.com/CloudReactor/cloudreactor-procwrapper/commits?author=jtsay362" title="Code">💻</a> <a href="https://github.com/CloudReactor/cloudreactor-procwrapper/commits?author=jtsay362" title="Documentation">📖</a> <a href="#infra-jtsay362" title="Infrastructure (Hosting, Build-Tools, etc)">🚇</a> <a href="#maintenance-jtsay362" title="Maintenance">🚧</a></td>
    <td align="center"><a href="https://github.com/mwaldne"><img src="https://avatars0.githubusercontent.com/u/40419?s=460&u=3a5266861feeb27db392622371ecc57ebca09f32&v=4?s=80" width="80px;" alt=""/><br /><sub><b>Mike Waldner</b></sub></a><br /><a href="https://github.com/CloudReactor/cloudreactor-procwrapper/commits?author=mwaldne" title="Code">💻</a></td>
    <td align="center"><a href="https://browniebroke.com/"><img src="https://avatars.githubusercontent.com/u/861044?v=4?s=80" width="80px;" alt=""/><br /><sub><b>Bruno Alla</b></sub></a><br /><a href="https://github.com/CloudReactor/cloudreactor-procwrapper/commits?author=browniebroke" title="Code">💻</a> <a href="#ideas-browniebroke" title="Ideas, Planning, & Feedback">🤔</a> <a href="https://github.com/CloudReactor/cloudreactor-procwrapper/commits?author=browniebroke" title="Documentation">📖</a></td>
  </tr>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

This project follows the [all-contributors](https://github.com/all-contributors/all-contributors) specification. Contributions of any kind welcome!

## Credits

This package was created with
[Cookiecutter](https://github.com/audreyr/cookiecutter) and the
[browniebroke/cookiecutter-pypackage](https://github.com/browniebroke/cookiecutter-pypackage)
project template.
