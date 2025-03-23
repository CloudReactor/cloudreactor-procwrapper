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

Wraps the execution of processes so that an API server such as
([CloudReactor](https://cloudreactor.io/)) can monitor and manage them.
Available as a standalone executable or as a python module.

## Features

* Runs either processes started with a command line or a python function you
supply
* Implements retries and time limits
* Injects secrets from AWS Secrets Manager, AWS Systems Manager Parameter Store (SSM),
AWS AppConfig, AWS S3, or local files and extracts them into the process
environment (for command-lines) or configuration (for functions)
* When used with the CloudReactor service:
  * Reports when a process/function starts and when it exits, along with the
  exit code and runtime metadata (if running in AWS EC2, AWS ECS, AWS Lambda,
  or AWS CodeBuild)
  * Sends heartbeats, optionally with status information like the number of
  items processed
  * After execution finishes, sends a tail of logs
  * Prevents too many concurrent executions
  * Accepts input values and sends output values, for history and/or Workflow data flow
  * Stops execution when manually stopped in the CloudReactor dashboard

## How it works

First, secrets and other configuration are fetched and resolved from
providers like AWS Secrets Manager or the local filesystem.

Just before your code runs, the module requests the Task Management server to create a
Task Execution associated with the Task name or UUID which you pass to the
module.
The Task Management server may reject the request if too many instances of the Task are
currently running, but otherwise records that a Task Execution has started.
The module then passes control to your code.

While your code is running, it may report progress to the Task Management server,
and the Task Management server may signal that your Task Execution stop
execution (due to user manually stopping it), in which
case the module terminates your code and exits.

After your code finishes, the module informs the Task Management server of
the exit code and/or result. CloudReactor monitors Tasks to ensure they
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
This means there is no need to inform the Task Management server of the Task details
(during deployment) before it runs.
Instead, each time the module runs, it informs the Task Management server of the
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
[CloudReactor Task schema](https://apidocs.cloudreactor.io/#operation/tasks_create).

### Execution Methods

proc_wrapper attempts detects the execution method your Task is
running with and sends that information to the Task Management server, provided
you configure your Task to be auto-created.

CloudReactor currently has special support for four Execution Methods:

1) [AWS ECS (in Fargate)](https://aws.amazon.com/fargate/)
2) [AWS Lambda](https://aws.amazon.com/lambda/)
3) [AWS EC2](https://aws.amazon.com/ec2/)
4) [AWS CodeBuild](https://aws.amazon.com/codebuild/)

If a Task is running in AWS ECS, CloudReactor is able to run additional
Task Executions, provided the details of running the Task is provided
during deployment with the AWS ECS CloudReactor Deployer, or if the
Task is configured to be auto-created, and this module is run. In the
second case, this module uses the ECS Metadata endpoint to detect
the ECS Task settings, and sends them to the Task Management server. CloudReactor
can also schedule Tasks or setup long-running services using Tasks,
provided they are run in AWS ECS.

If a Task is running in AWS Lambda, CloudReactor is able to run additional
Task Executions after the first run of the function.

If a Task is running in AWS EC2, the `execution_method_type` setting should be
set to `AWS EC2` so that proc_wrapper can pull EC2 metadata and send it back
to the Task Management server. Also, the `ec2` extra is required when installing
the module.

If proc_wrapper cannot automatically detect the execution method type,
it will mark the Task as using the Unknown execution method. If that is the case, CloudReactor won't be able to start the Task in the dashboard or as part of a
Workflow, schedule the Task, or setup a service with the Task. But the advantage
is that the Task code can be executed by any method available to you,
such as bare metal servers, VM's, or Kubernetes.
All Tasks in CloudReactor, regardless of execution method, have their
history kept and are monitored.


### Passive Tasks

Passive Tasks are Tasks that CloudReactor does not manage. This means
scheduling and service setup must be handled by other means
(cron jobs, [supervisord](http://supervisord.org/), etc).
However, Tasks marked as services or that have a schedule will still be
monitored by CloudReactor, which will send notifications if
a service Task goes down or a Task does not run on schedule.

The module reports to the Task Management server that auto-created Tasks are
passive, unless you specify the `--force-task-passive` commmand-line option or
set the environment variable `PROC_WRAPPER_TASK_IS_PASSIVE` to `FALSE`.
If a Task uses the Unknown Execution Method, it must be marked as passive,
because CloudReactor does not know how to manage it.

## Pre-requisites

If you just want to use this module to retry processes, limit execution time,
or fetch secrets, you can use offline mode, in which case no CloudReactor API
key is required. But CloudReactor offers a free tier so we hope you
[sign up](https://dash.cloudreactor.io/signup)
for a free account to enable monitoring and/or management.

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

### PyInstaller

Standalone executables built by [PyInstaller](https://www.pyinstaller.org/) for 64-bit Linux and Windows are available, located in `bin/pyinstaller`.
These executables bundle
python so you don't need to have python installed on your machine. They also
bundle all optional extras so you can fetch secrets from AWS
Secrets Manager and extract them with jsonpath-ng, for example.

#### RHEL or derivatives

To download and run the wrapper on a RHEL/Fedora/Amazon Linux 2 machine:

    RUN wget -nv https://github.com/CloudReactor/cloudreactor-procwrapper/raw/6.0.0/bin/pyinstaller/al2/6.0.0/proc_wrapper.bin
    ENTRYPOINT ["proc_wrapper.bin"]

Example Dockerfiles of known working environments are available for
[Amazon Linux 2](tests/integration/pyinstaller_executable/docker_context_al2_amd64/)
and
[Fedora](tests/integration/pyinstaller_executable/docker_context_al2_amd64/Dockerfile).

Fedora 27 or later are supported.

#### Debian based machines

On a Debian based (including Ubuntu) machine:

    RUN wget -nv https://github.com/CloudReactor/cloudreactor-procwrapper/raw/6.0.0/bin/pyinstaller/debian-amd64/6.0.0/proc_wrapper.bin
    ENTRYPOINT ["proc_wrapper.bin"]

See the example
[Dockerfile](tests/integration/pyinstaller_executable/docker_context_debian_amd64/Dockerfile) for a known working Debian environment.

Debian 10 (Buster) or later are supported.

Special thanks to
[wine](https://www.winehq.org/) and
[PyInstaller Docker Images](https://github.com/cdrx/docker-pyinstaller)
for making it possible to cross-compile!

### When python is available

Install this module via pip (or your favorite package manager):

`pip install cloudreactor-procwrapper`

`cloudreactor-procwrapper` doesn't have any required dependencies, but it can
be installed with the following extras:

* `aws`: Support for fetching secrets from AWS Secrets Manager,
Systems Manager Parameter Store (SSM), App Config, or S3, and
determining the assumed role, implemented by the
[boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)
library. Note that you don't need this extra when running in AWS Lambda,
as it's included already in the Lambda environment.
* `jsonpath`: Support for secret resolution using JSON Path, implemented by the
[jsonpath-ng](https://github.com/h2non/jsonpath-ng) library
* `yaml`: Support for configuration files in YAML format, implemented by the
[pyyaml](https://pyyaml.org/) library
* `dotenv`: Support for environment variables defined in the dotenv format,
implemented by the [dotenv](https://github.com/theskumar/python-dotenv) library
* `mergedeep`: Support for secret object value merging using alternative
strategies, implemented by the
[mergedeep](https://github.com/clarketm/mergedeep) library
* `ec2`: Support for fetching EC2 metadata using the [ec2-metadata](https://github.com/adamchainz/ec2-metadata) library

Use brackets after `cloudreactor-procwrapper` to enable support for the desired
functionality. For example, to install AWS support, JSON Path secret resolution,
and support for dotenv files:

    pip install cloudreactor-procwrapper[aws,jsonpath,dotenv]

Or, to install support for everything:

    pip install cloudreactor-procwrapper[allextras]

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

assuming that are using the PyInstaller standalone executable, and that
you configure the program using environment variables.

Or, if you have python installed:

    python -m proc_wrapper somecommand --somearg x

Here are all the options:

```
usage: proc_wrapper [-h] [-v] [-n TASK_NAME] [--task-uuid TASK_UUID] [-a] [--auto-create-task-run-environment-name AUTO_CREATE_TASK_RUN_ENVIRONMENT_NAME]
                    [--auto-create-task-run-environment-uuid AUTO_CREATE_TASK_RUN_ENVIRONMENT_UUID] [--auto-create-task-props AUTO_CREATE_TASK_PROPS] [--force-task-active] [--task-execution-uuid TASK_EXECUTION_UUID]
                    [--task-version-number TASK_VERSION_NUMBER] [--task-version-text TASK_VERSION_TEXT] [--task-version-signature TASK_VERSION_SIGNATURE] [--build-task-execution-uuid BUILD_TASK_EXECUTION_UUID]
                    [--deployment-task-execution-uuid DEPLOYMENT_TASK_EXECUTION_UUID] [--execution-method-props EXECUTION_METHOD_PROPS] [--task-instance-metadata TASK_INSTANCE_METADATA] [-s] [--schedule SCHEDULE]
                    [--max-concurrency MAX_CONCURRENCY] [--max-conflicting-age MAX_CONFLICTING_AGE] [--api-base-url API_BASE_URL] [-k API_KEY] [--api-heartbeat-interval API_HEARTBEAT_INTERVAL]
                    [--api-error-timeout API_ERROR_TIMEOUT] [--api-final-update-timeout API_FINAL_UPDATE_TIMEOUT] [--api-retry-delay API_RETRY_DELAY] [--api-resume-delay API_RESUME_DELAY]
                    [--api-task-execution-creation-error-timeout API_TASK_EXECUTION_CREATION_ERROR_TIMEOUT] [--api-task-execution-creation-conflict-timeout API_TASK_EXECUTION_CREATION_CONFLICT_TIMEOUT]
                    [--api-task-execution-creation-conflict-retry-delay API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY] [--api-request-timeout API_REQUEST_TIMEOUT] [-o] [-p] [-m API_MANAGED_PROBABILITY]
                    [--api-failure-report-probability API_FAILURE_REPORT_PROBABILITY] [--api-timeout-report-probability API_TIMEOUT_REPORT_PROBABILITY] [-d DEPLOYMENT] [--send-input-value] [--send-pid] [--send-hostname]
                    [--no-send-runtime-metadata] [--runtime-metadata-refresh-interval RUNTIME_METADATA_REFRESH_INTERVAL] [-i INPUT_VALUE] [--input-env-var-name INPUT_ENV_VAR_NAME] [--input-filename INPUT_FILENAME]
                    [--cleanup-input-file] [--input-value-format INPUT_VALUE_FORMAT] [--result-filename RESULT_FILENAME] [--result-value-format RESULT_VALUE_FORMAT] [--no-cleanup-result-file]
                    [-l {DEBUG,INFO,WARNING,ERROR,CRITICAL}] [--log-secrets] [--log-input-value] [--log-result-value] [--exclude-timestamps-in-log] [-w WORK_DIR] [-c COMMAND_LINE] [--shell-mode {auto,enable,disable}]
                    [--no-strip-shell-wrapping] [--no-process-group-termination] [-t PROCESS_TIMEOUT] [-r PROCESS_MAX_RETRIES] [--process-retry-delay PROCESS_RETRY_DELAY] [--process-check-interval PROCESS_CHECK_INTERVAL]
                    [--process-termination-grace-period PROCESS_TERMINATION_GRACE_PERIOD] [--enable-status-update-listener] [--status-update-socket-port STATUS_UPDATE_SOCKET_PORT]
                    [--status-update-message-max-bytes STATUS_UPDATE_MESSAGE_MAX_BYTES] [--status-update-interval STATUS_UPDATE_INTERVAL] [-e ENV_LOCATIONS] [--config CONFIG_LOCATIONS]
                    [--config-merge-strategy {DEEP,SHALLOW,REPLACE,ADDITIVE,TYPESAFE_REPLACE,TYPESAFE_ADDITIVE}] [--overwrite-env-during-resolution] [--config-ttl CONFIG_TTL] [--no-fail-fast-config-resolution]
                    [--resolved-env-var-name-prefix RESOLVED_ENV_VAR_NAME_PREFIX] [--resolved-env-var-name-suffix RESOLVED_ENV_VAR_NAME_SUFFIX] [--resolved-config-property-name-prefix RESOLVED_CONFIG_PROPERTY_NAME_PREFIX]
                    [--resolved-config-property-name-suffix RESOLVED_CONFIG_PROPERTY_NAME_SUFFIX] [--env-var-name-for-config ENV_VAR_NAME_FOR_CONFIG] [--config-property-name-for-env CONFIG_PROPERTY_NAME_FOR_ENV]
                    [--env-output-filename ENV_OUTPUT_FILENAME] [--env-output-format ENV_OUTPUT_FORMAT] [--config-output-filename CONFIG_OUTPUT_FILENAME] [--config-output-format CONFIG_OUTPUT_FORMAT]
                    [--exit-after-writing-variables] [--main-container-name MAIN_CONTAINER_NAME] [--monitor-container-name MONITOR_CONTAINER_NAME] [--sidecar-container-mode] [--rollbar-access-token ROLLBAR_ACCESS_TOKEN]
                    [--rollbar-retries ROLLBAR_RETRIES] [--rollbar-retry-delay ROLLBAR_RETRY_DELAY] [--rollbar-timeout ROLLBAR_TIMEOUT]
                    ...

Wraps the execution of processes so that a service API endpoint (CloudReactor) is optionally informed of the progress. Also implements retries, timeouts, and secret injection into the environment.

positional arguments:
  command

optional arguments:
  -h, --help            show this help message and exit
  -v, --version         Print the version and exit

task:
  Task settings

  -n TASK_NAME, --task-name TASK_NAME
                        Name of Task (either the Task Name or the Task UUID must be specified
  --task-uuid TASK_UUID
                        UUID of Task (either the Task Name or the Task UUID must be specified)
  -a, --auto-create-task
                        Create the Task even if not known by the Task Management server
  --auto-create-task-run-environment-name AUTO_CREATE_TASK_RUN_ENVIRONMENT_NAME
                        Name of the Run Environment to use if auto-creating the Task (either the name or UUID of the Run Environment must be specified if auto-creating the Task). Defaults to the deployment name if the Run
                        Environment UUID is not specified.
  --auto-create-task-run-environment-uuid AUTO_CREATE_TASK_RUN_ENVIRONMENT_UUID
                        UUID of the Run Environment to use if auto-creating the Task (either the name or UUID of the Run Environment must be specified if auto-creating the Task)
  --auto-create-task-props AUTO_CREATE_TASK_PROPS
                        Additional properties of the auto-created Task, in JSON format. See https://apidocs.cloudreactor.io/#operation/api_v1_tasks_create for the schema.
  --force-task-active   Indicates that the auto-created Task should be scheduled and made a service by the Task Management server, if applicable. Otherwise, auto-created Tasks are marked passive.
  --task-execution-uuid TASK_EXECUTION_UUID
                        UUID of Task Execution to attach to
  --task-version-number TASK_VERSION_NUMBER
                        Numeric version of the Task's source code
  --task-version-text TASK_VERSION_TEXT
                        Human readable version of the Task's source code
  --task-version-signature TASK_VERSION_SIGNATURE
                        Version signature of the Task's source code (such as a git commit hash)
  --build-task-execution-uuid BUILD_TASK_EXECUTION_UUID
                        UUID of Task Execution that built this Task's source code
  --deployment-task-execution-uuid DEPLOYMENT_TASK_EXECUTION_UUID
                        UUID of Task Execution that deployed this Task to the Runtime Environment
  --execution-method-props EXECUTION_METHOD_PROPS
                        Additional properties of the execution method, in JSON format. See https://apidocs.cloudreactor.io/#operation/api_v1_task_executions_create for the schema.
  --task-instance-metadata TASK_INSTANCE_METADATA
                        Additional metadata about the Task instance, in JSON format
  -s, --service         Indicate that this is a Task that should run indefinitely
  --schedule SCHEDULE   Execution schedule reported to the Task Management server
  --max-concurrency MAX_CONCURRENCY
                        Maximum number of concurrent Task Executions of the same Task. Defaults to 1.
  --max-conflicting-age MAX_CONFLICTING_AGE
                        Maximum age of conflicting Tasks to consider, in seconds. -1 means no limit. Defaults to the heartbeat interval, plus 60 seconds for services that send heartbeats. Otherwise, defaults to no limit.

api:
  API client settings

  --api-base-url API_BASE_URL
                        Base URL of API server. Defaults to https://api.cloudreactor.io
  -k API_KEY, --api-key API_KEY
                        API key. Must have at least the Task access level, or Developer access level for auto-created Tasks.
  --api-heartbeat-interval API_HEARTBEAT_INTERVAL
                        Number of seconds to wait between sending heartbeats to the Task Management server. -1 means to not send heartbeats. Defaults to 30 for concurrency limited services, 300 otherwise.
  --api-error-timeout API_ERROR_TIMEOUT
                        Number of seconds to wait while receiving recoverable errors from the API server. Defaults to 300.
  --api-final-update-timeout API_FINAL_UPDATE_TIMEOUT
                        Number of seconds to wait while receiving recoverable errors from the Task Management server when sending the final update before exiting. Defaults to 1800.
  --api-retry-delay API_RETRY_DELAY
                        Number of seconds to wait before retrying an API request. Defaults to 120.
  --api-resume-delay API_RESUME_DELAY
                        Number of seconds to wait before resuming API requests, after retries are exhausted. Defaults to 600. -1 means to never resume.
  --api-task-execution-creation-error-timeout API_TASK_EXECUTION_CREATION_ERROR_TIMEOUT
                        Number of seconds to keep retrying Task Execution creation while receiving error responses from the Task Management server. -1 means to keep trying indefinitely. Defaults to 300.
  --api-task-execution-creation-conflict-timeout API_TASK_EXECUTION_CREATION_CONFLICT_TIMEOUT
                        Number of seconds to keep retrying Task Execution creation while conflict is detected by the Task Management server. -1 means to keep trying indefinitely. Defaults to 1800 for concurrency limited
                        services, 0 otherwise.
  --api-task-execution-creation-conflict-retry-delay API_TASK_EXECUTION_CREATION_CONFLICT_RETRY_DELAY
                        Number of seconds between attempts to retry Task Execution creation after conflict is detected. Defaults to 60 for concurrency-limited services, 120 otherwise.
  --api-request-timeout API_REQUEST_TIMEOUT
                        Timeout for contacting API server, in seconds. Defaults to 30.
  -o, --offline-mode    Do not communicate with or rely on an API server
  -p, --prevent-offline-execution
                        Do not start processes if the Task Management server is unavailable or the wrapper is misconfigured.
  -m API_MANAGED_PROBABILITY, --api-managed-probability API_MANAGED_PROBABILITY
                        Sample notifications to the Task Management server with a given probability when starting an execution. Defaults to 1.0 (always send notifications).
  --api-failure-report-probability API_FAILURE_REPORT_PROBABILITY
                        If the notification of an execution was not previously sent on startup and the execution fails, notify the Task Management server with the given probability. Defaults to 1.0 (always send failure
                        notifications).
  --api-timeout-report-probability API_TIMEOUT_REPORT_PROBABILITY
                        If the notification of an execution was not previously sent on startup and the execution times out, notify the Task Management server with given probability. Defaults to 1.0 (always send timeout
                        notifications).
  -d DEPLOYMENT, --deployment DEPLOYMENT
                        Deployment name (production, staging, etc.)
  --send-input-value    Send the input value the Task Management server
  --send-pid            Send the process ID to the Task Management server
  --send-hostname       Send the hostname to the Task Management server
  --no-send-runtime-metadata
                        Do not send metadata about the runtime environment
  --runtime-metadata-refresh-interval RUNTIME_METADATA_REFRESH_INTERVAL
                        Refresh interval for runtime metadata, in seconds. The default value depends on the execution method.

io:
  Input and result settings

  -i INPUT_VALUE, --input-value INPUT_VALUE
                        The input value
  --input-env-var-name INPUT_ENV_VAR_NAME
                        The value of this environment variable is used as the input value for the wrapped process or embedded function. The value is sent back to the API server as the input value of the Task Execution.
  --input-filename INPUT_FILENAME
                        The name of the file containing the value used as the input value for the wrapped process or embedded function. The contents of the file are sent back to the API server as the input value of the
                        Task Execution.
  --cleanup-input-file  Remove the input file before exit. If this parameter is omitted, the input file will only be removed if it was written by the wrapper.
  --input-value-format INPUT_VALUE_FORMAT
                        The format of the value used as the input value for the Task Execution. Options are 'json', 'yaml', or 'text'. Defaults to 'text'.
  --result-filename RESULT_FILENAME
                        The name of the file the wrapped process will write with the result value. The contents of the file are sent back to the API server as the result value of the Task Execution.
  --result-value-format RESULT_VALUE_FORMAT
                        The format of the file that the wrapped process will write with the result value. Options are 'json', 'yaml', or 'text'. Defaults to 'text'.
  --no-cleanup-result-file
                        Do not delete the result file after the Task Execution completes. If this parameter is omitted, the result file will be deleted.

log:
  Logging settings

  -l {DEBUG,INFO,WARNING,ERROR,CRITICAL}, --log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                        Log level
  --log-secrets         Log sensitive information
  --log-input-value     Log input value
  --log-result-value    Log result value
  --exclude-timestamps-in-log
                        Exclude timestamps in log (possibly because the log stream will be enriched by timestamps automatically by a logging service like AWS
                        CloudWatch Logs)
  --num-log-lines-sent-on-failure NUM_LOG_LINES_SENT_ON_FAILURE
                        The number of trailing log lines to send to the API server if the Task Execution fails. Defaults to 0 (no log lines are sent).
  --num-log-lines-sent-on-timeout NUM_LOG_LINES_SENT_ON_TIMEOUT
                        The number of trailing log lines to send to the API server if the Task Execution fails. Defaults to 0 (no log lines are sent).
  --num-log-lines-sent-on-success NUM_LOG_LINES_SENT_ON_SUCCESS
                        The number of trailing log lines to send to the API server if the Task Execution succeeds. Defaults to 0 (no log lines are sent).
  --max-log-line-length MAX_LOG_LINE_LENGTH
                        The maximum number of characters in a saved log line. If a line is longer than this value, it will be truncated. Defaults to 1000.
  --separate-stdout-and-stderr-logs
                        Separate stdout and stderr streams when reporting log lines. Otherwise, the streams are merged into the stdout stream.
  --ignore-stdout       Do send stdout log lines to the API server
  --ignore-stderr       Do send stderr log lines to the API server

process:
  Process settings

  -w WORK_DIR, --work-dir WORK_DIR
                        Working directory. Defaults to the current directory.
  -c COMMAND_LINE, --command-line COMMAND_LINE
                        Command line to execute
  --shell-mode {auto,enable,disable}
                        Indicates if the process command should be executed in a shell. Executing in a shell allows shell scripts, commands, and expressions to be used, with the disadvantage that termination signals may
                        not be propagated to child processes. Options are: enable -- Force the command to be executed in a shell; disable -- Force the command to be executed without a shell; auto -- Auto-detect the shell
                        mode by analyzing the command.
  --no-strip-shell-wrapping
                        Do not strip the command-line of shell wrapping like "/bin/sh -c" that can be introduced by Docker when using shell form of ENTRYPOINT and CMD.
  --no-process-group-termination
                        Send termination and kill signals to the wrapped process only, instead of its process group (which is the default). Sending to the process group allows all child processes to receive the signals,
                        even if the wrapped process does not forward signals. However, if your wrapped process manually handles and forwards signals to its child processes, you probably want to send signals to only your
                        wrapped process.
  -t PROCESS_TIMEOUT, --process-timeout PROCESS_TIMEOUT
                        Timeout for process completion, in seconds. -1 means no timeout, which is the default.
  -r PROCESS_MAX_RETRIES, --process-max-retries PROCESS_MAX_RETRIES
                        Maximum number of times to retry failed processes. -1 means to retry forever. Defaults to 0.
  --process-retry-delay PROCESS_RETRY_DELAY
                        Number of seconds to wait before retrying a process. Defaults to 60.
  --process-check-interval PROCESS_CHECK_INTERVAL
                        Number of seconds to wait between checking the status of processes. Defaults to 10.
  --process-termination-grace-period PROCESS_TERMINATION_GRACE_PERIOD
                        Number of seconds to wait after sending SIGTERM to a process, but before killing it with SIGKILL. Defaults to 30.

updates:
  Status update settings

  --enable-status-update-listener
                        Listen for status updates from the process, sent on the status socket port via UDP. If not specified, status update messages will not be read.
  --status-update-socket-port STATUS_UPDATE_SOCKET_PORT
                        The port used to receive status updates from the process. Defaults to 2373.
  --status-update-message-max-bytes STATUS_UPDATE_MESSAGE_MAX_BYTES
                        The maximum number of bytes status update messages can be. Defaults to 65536.
  --status-update-interval STATUS_UPDATE_INTERVAL
                        Minimum of number of seconds to wait between sending status updates to the API server. -1 means to not send status updates except with heartbeats. Defaults to -1.

configuration:
  Environment/configuration resolution settings

  -e ENV_LOCATIONS, --env ENV_LOCATIONS
                        Location of either local file, AWS S3 ARN, AWS Secrets Manager ARN, or AWS Systems Manager Parameter Store identifier containing properties used to populate the environment for embedded mode, or
                        the process environment for wrapped mode. By default, the file format is assumed to be dotenv. Specify multiple times to include multiple locations.
  --config CONFIG_LOCATIONS
                        Location of either local file, AWS S3 ARN, AWS Secrets Manager ARN, or AWS Systems Manager Parameter Store identifier containing properties used to populate the configuration for embedded mode. By
                        default, the file format is assumed to be in JSON. Specify multiple times to include multiple locations.
  --config-merge-strategy {DEEP,SHALLOW,REPLACE,ADDITIVE,TYPESAFE_REPLACE,TYPESAFE_ADDITIVE}
                        Merge strategy for merging configurations. Defaults to 'DEEP', which does not require mergedeep. Besides the 'SHALLOW' strategy, all other strategies require the mergedeep extra to be installed.
  --overwrite-env-during-resolution
                        Overwrite existing environment variables when resolving them
  --config-ttl CONFIG_TTL
                        Number of seconds to cache resolved environment variables and configuration properties instead of refreshing them when a process restarts. -1 means to never refresh. Defaults to -1.
  --no-fail-fast-config-resolution
                        Continue execution even if an error occurs resolving the configuration
  --resolved-env-var-name-prefix RESOLVED_ENV_VAR_NAME_PREFIX
                        Required prefix for names of environment variables that should resolved. The prefix will be removed in the resolved variable name. Defaults to ''.
  --resolved-env-var-name-suffix RESOLVED_ENV_VAR_NAME_SUFFIX
                        Required suffix for names of environment variables that should resolved. The suffix will be removed in the resolved variable name. Defaults to '_FOR_PROC_WRAPPER_TO_RESOLVE'.
  --resolved-config-property-name-prefix RESOLVED_CONFIG_PROPERTY_NAME_PREFIX
                        Required prefix for names of configuration properties that should resolved. The prefix will be removed in the resolved property name. Defaults to ''.
  --resolved-config-property-name-suffix RESOLVED_CONFIG_PROPERTY_NAME_SUFFIX
                        Required suffix for names of configuration properties that should resolved. The suffix will be removed in the resolved property name. Defaults to '__to_resolve'.
  --env-var-name-for-config ENV_VAR_NAME_FOR_CONFIG
                        The name of the environment variable used to set to the value of the JSON encoded configuration. Defaults to not setting any environment variable.
  --config-property-name-for-env CONFIG_PROPERTY_NAME_FOR_ENV
                        The name of the configuration property used to set to the value of the JSON encoded environment. Defaults to not setting any property.
  --env-output-filename ENV_OUTPUT_FILENAME
                        The filename to write the resolved environment variables to. Defaults to '.env' if the env output format is 'dotenv', 'env.json' if the env output format is 'json', and 'env.yml' if the config
                        output format is 'yaml'.
  --env-output-format ENV_OUTPUT_FORMAT
                        The format used to write the resolved environment variables file. One of 'dotenv', 'json', or 'yaml'. Will be auto-detected from the filename of the env output filename if possible. Defaults to
                        'dotenv' if the env output filename is set but the format cannot be auto-detected from the filename.
  --config-output-filename CONFIG_OUTPUT_FILENAME
                        The filename to write the resolved configuration to. Defaults to 'config.json' if config output format is 'json', 'config.yml' if the config output format is 'yaml', and 'config.env' if the config
                        output format is 'dotenv'.
  --config-output-format CONFIG_OUTPUT_FORMAT
                        The format used to write the resolved configuration file. One of 'dotenv', 'json', or 'yaml'. Will be auto-detected from the filename of the config output filename if possible. Defaults to 'json'
                        if the config output filename is set but the format cannot be auto-detected from the filename.
  --exit-after-writing-variables
                        Exit after writing the resolved environment variables and configuration

container:
  Container settings

  --main-container-name MAIN_CONTAINER_NAME
                        The name of the container that is monitored
  --monitor-container-name MONITOR_CONTAINER_NAME
                        The name of the container that will monitor the main container
  --sidecar-container-mode
                        Indicates that the current container is a sidecar container that will monitor the main container

rollbar:
  Rollbar settings

  --rollbar-access-token ROLLBAR_ACCESS_TOKEN
                        Access token for Rollbar (used to report error when communicating with API server)
  --rollbar-retries ROLLBAR_RETRIES
                        Number of retries per Rollbar request. Defaults to 2.
  --rollbar-retry-delay ROLLBAR_RETRY_DELAY
                        Number of seconds to wait before retrying a Rollbar request. Defaults to 120.
  --rollbar-timeout ROLLBAR_TIMEOUT
                        Timeout for contacting Rollbar server, in seconds. Defaults to 30.
```

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
* PROC_WRAPPER_EXECUTION_METHOD_TYPE
* PROC_WRAPPER_EXECUTION_METHOD_PROPS (JSON encoded property map)
* PROC_WRAPPER_TASK_MAX_CONCURRENCY (set to -1 to indicate no limit)
* PROC_WRAPPER_PREVENT_OFFLINE_EXECUTION (TRUE or FALSE)
* PROC_WRAPPER_TASK_VERSION_NUMBER
* PROC_WRAPPER_TASK_VERSION_TEXT
* PROC_WRAPPER_TASK_VERSION_SIGNATURE
* PROC_WRAPPER_TASK_INSTANCE_METADATA (JSON encoded property map)
* PROC_WRAPPER_LOG_LEVEL (TRACE, DEBUG, INFO, WARNING, ERROR, or CRITICAL)
* PROC_WRAPPER_LOG_SECRETS (TRUE or FALSE)
* PROC_WRAPPER_LOG_INPUT_VALUE (TRUE or FALSE)
* PROC_WRAPPER_LOG_RESULT_VALUE (TRUE or FALSE)
* PROC_WRAPPER_INCLUDE_TIMESTAMPS_IN_LOG (TRUE or FALSE)
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
* PROC_WRAPPER_API_MANAGED_PROBABILITY
* PROC_WRAPPER_API_FAILURE_REPORT_PROBABILITY
* PROC_WRAPPER_API_TIMEOUT_REPORT_PROBABILITY
* PROC_WRAPPER_ENV_LOCATIONS (comma-separated list of locations)
* PROC_WRAPPER_CONFIG_LOCATIONS (comma-separated list of locations)
* PROC_WRAPPER_OVERWRITE_ENV_WITH_SECRETS (TRUE or FALSE)
* PROC_WRAPPER_RESOLVE_SECRETS (TRUE or FALSE)
* PROC_WRAPPER_MAX_CONFIG_RESOLUTION_DEPTH
* PROC_WRAPPER_MAX_CONFIG_RESOLUTION_ITERATIONS
* PROC_WRAPPER_CONFIG_TTL_SECONDS
* PROC_WRAPPER_FAIL_FAST_CONFIG_RESOLUTION (TRUE or FALSE)
* PROC_WRAPPER_RESOLVABLE_ENV_VAR_NAME_PREFIX
* PROC_WRAPPER_RESOLVABLE_ENV_VAR_NAME_SUFFIX
* PROC_WRAPPER_RESOLVABLE_CONFIG_PROPERTY_NAME_PREFIX
* PROC_WRAPPER_RESOLVABLE_CONFIG_PROPERTY_NAME_SUFFIX
* PROC_WRAPPER_ENV_VAR_NAME_FOR_CONFIG
* PROC_WRAPPER_CONFIG_PROPERTY_NAME_FOR_ENV
* PROC_WRAPPER_ENV_OUTPUT_FILENAME
* PROC_WRAPPER_ENV_OUTPUT_FORMAT
* PROC_WRAPPER_CONFIG_OUTPUT_FILENAME
* PROC_WRAPPER_CONFIG_OUTPUT_FORMAT
* PROC_WRAPPER_SEND_PID (TRUE or FALSE)
* PROC_WRAPPER_SEND_HOSTNAME (TRUE or FALSE)
* PROC_WRAPPER_SEND_INPUT_VALUE (TRUE or FALSE)
* PROC_WRAPPER_SEND_RUNTIME_METADATA (TRUE or FALSE)
* PROC_WRAPPER_MAX_CONFLICTING_AGE_SECONDS
* PROC_WRAPPER_TASK_COMMAND
* PROC_WRAPPER_SHELL_MODE (TRUE or FALSE)
* PROC_WRAPPER_STRIP_SHELL_WRAPPING (TRUE or FALSE)
* PROC_WRAPPER_WORK_DIR
* PROC_WRAPPER_PROCESS_MAX_RETRIES
* PROC_WRAPPER_PROCESS_TIMEOUT_SECONDS
* PROC_WRAPPER_PROCESS_RETRY_DELAY_SECONDS
* PROC_WRAPPER_PROCESS_CHECK_INTERVAL_SECONDS
* PROC_WRAPPER_PROCESS_TERMINATION_GRACE_PERIOD_SECONDS
* PROC_WRAPPER_PROCESS_GROUP_TERMINATION (TRUE or FALSE)
* PROC_WRAPPER_INPUT_VALUE
* PROC_WRAPPER_INPUT_ENV_VAR_NAME
* PROC_WRAPPER_INPUT_FILENAME
* PROC_WRAPPER_INPUT_VALUE_FORMAT
* PROC_WRAPPER_CLEANUP_INPUT_FILE (TRUE or FALSE)
* PROC_WRAPPER_RESULT_VALUE_FORMAT
* PROC_WRAPPER_RESULT_FILENAME
* PROC_WRAPPER_CLEANUP_RESULT_FILE (TRUE or FALSE)
* PROC_WRAPPER_NUM_LOG_LINES_SENT_ON_FAILURE
* PROC_WRAPPER_NUM_LOG_LINES_SENT_ON_TIMEOUT
* PROC_WRAPPER_NUM_LOG_LINES_SENT_ON_SUCCESS
* PROC_WRAPPER_MAX_LOG_LINE_LENGTH
* PROC_WRAPPER_MERGE_STDOUT_AND_STDERR_LOGS (TRUE or FALSE)
* PROC_WRAPPER_IGNORE_STDOUT (TRUE or FALSE)
* PROC_WRAPPER_IGNORE_STDERR (TRUE or FALSE)
* PROC_WRAPPER_STATUS_UPDATE_SOCKET_PORT
* PROC_WRAPPER_STATUS_UPDATE_MESSAGE_MAX_BYTES
* PROC_WRAPPER_ROLLBAR_ACCESS_TOKEN
* PROC_WRAPPER_MAIN_CONTAINER_NAME
* PROC_WRAPPER_MONITOR_CONTAINER_NAME
* PROC_WRAPPER_SIDECAR_CONTAINER_MODE (TRUE or FALSE)
* PROC_WRAPPER_ROLLBAR_TIMEOUT_SECONDS
* PROC_WRAPPER_ROLLBAR_RETRIES
* PROC_WRAPPER_ROLLBAR_RETRY_DELAY_SECONDS

With the exception of the settings for Secret Fetching and Resolution,
these environment variables are read after Secret Fetching so that they can
come from secret values.

The command is executed with the same environment that the wrapper script gets,
except that these properties are copied/overridden:

* PROC_WRAPPER_DEPLOYMENT
* PROC_WRAPPER_ENV_OUTPUT_FILENAME
* PROC_WRAPPER_ENV_OUTPUT_FORMAT
* PROC_WRAPPER_CONFIG_OUTPUT_FILENAME
* PROC_WRAPPER_CONFIG_OUTPUT_FORMAT
* PROC_WRAPPER_API_BASE_URL
* PROC_WRAPPER_API_KEY
* PROC_WRAPPER_API_ERROR_TIMEOUT_SECONDS
* PROC_WRAPPER_API_RETRY_DELAY_SECONDS
* PROC_WRAPPER_API_RESUME_DELAY_SECONDS
* PROC_WRAPPER_API_REQUEST_TIMEOUT_SECONDS
* PROC_WRAPPER_API_MANAGED_PROBABILITY
* PROC_WRAPPER_API_FAILURE_REPORT_PROBABILITY
* PROC_WRAPPER_API_TIMEOUT_REPORT_PROBABILITY
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
* PROC_WRAPPER_INPUT_VALUE
* PROC_WRAPPER_INPUT_FILENAME
* PROC_WRAPPER_INPUT_VALUE_FORMAT
* PROC_WRAPPER_RESULT_FILENAME
* PROC_WRAPPER_RESULT_VALUE_FORMAT
* PROC_WRAPPER_ENABLE_STATUS_UPDATE_LISTENER
* PROC_WRAPPER_STATUS_UPDATE_SOCKET_PORT
* PROC_WRAPPER_STATUS_UPDATE_INTERVAL_SECONDS
* PROC_WRAPPER_STATUS_UPDATE_MESSAGE_MAX_BYTES

Wrapped mode is suitable for running in a shell on your own (virtual) machine
or in a Docker container. It requires multi-process support, as the module
runs at the same time as the command it wraps.

In AWS ECS, it is possible to run proc_wrapper in sidecar container that
monitors another (main) container. In that case, proc_wrapper` will report the
status and runtime metadata of the main container. The advantage of running
proc_wrapper in a sidecar container is that the main container doesn't need
to be modified to run proc_wrapper. To configure proc_wrapper as a sidecar,
set the main container name and enable sidecar container mode.

### Embedded mode

You can use embedded mode to execute python code from inside a python program.
Include the `proc_wrapper` package in your python project's
dependencies. To run a task you want to be monitored:

    from typing import Any, Mapping

    from proc_wrapper import ProcWrapper, ProcWrapperParams


    def fun(wrapper: ProcWrapper, cbdata: dict[str, int],
            config: Mapping[str, Any]) -> int:
        print(cbdata)
        return cbdata['a']


    # This is the function signature of a function invoked by AWS Lambda.
    def entrypoint(event: Any, context: Any) -> int:
        params = ProcWrapperParams()
        params.auto_create_task = True

        # If the Task Execution is running in AWS Lambda, CloudReactor can make
        # the associated Task available to run (non-passive) in the CloudReactor
        # dashboard or by API, after the wrapper reports its first execution.
        proc_wrapper_params.task_is_passive = False

        params.task_name = 'embedded_test_production'
        params.auto_create_task_run_environment_name = 'production'

        # For example only, in the real world you would use Secret Fetching;
        # see below.
        params.api_key = 'YOUR_CLOUDREACTOR_API_KEY'

        # In an AWS Lambda environment, passing the context and event allows
        # CloudReactor to monitor and manage this Task.
        proc_wrapper = ProcWrapper(params=params, runtime_context=context,
            input_value=event)

        x = proc_wrapper.managed_call(fun, {'a': 1, 'b': 2})
        # Should print 1
        print(x)

        return x

This is suitable for running in single-threaded environments like
AWS Lambda, or as part of a larger process that executes
sub-routines that should be monitored. See
[cloudreactor-python-lambda-quickstart](https://github.com/CloudReactor/cloudreactor-python-lambda-quickstart)
for an example project that uses proc_wrapper in a function run by AWS Lambda.

#### Embedded mode configuration

In embedded mode, besides setting properties of `ProcWrapperParams` in code,
`ProcWrapper` can be also configured in two ways:

First, using environment variables, as in wrapped mode.

Second, using the configuration dictionary. If the configuration dictionary
contains the key `proc_wrapper_params` and its value is a dictionary, the
keys and values in the dictionary will be used to to set these attributes in
`ProcWrapperParams`:

| Key                              	               | Type      	| Mutable 	| Uses Resolved Config 	 |
|--------------------------------------------------|-----------	|---------	|------------------------|
| log_secrets                      	               | bool      	| No      	| No                   	 |
| env_locations                    	               | list[str] 	| No      	| No                   	 |
| config_locations                 	               | list[str] 	| No      	| No                   	 |
| config_merge_strategy            	               | str       	| No      	| No                   	 |
| overwrite_env_during_resolution  	               | bool      	| No      	| No                   	 |
| max_config_resolution_depth      	               | int       	| No      	| No                   	 |
| max_config_resolution_iterations 	               | int       	| No      	| No                   	 |
| config_ttl                       	               | int       	| No      	| No                   	 |
| fail_fast_config_resolution      	               | bool      	| No      	| No                   	 |
| resolved_env_var_name_prefix     	               | str       	| No      	| No                   	 |
| resolved_env_var_name_suffix     	               | str       	| No      	| No                   	 |
| resolved_config_property_name_prefix             | str     	| No      	| No                   	 |
| resolved_config_property_name_suffix             | str    	| No      	| No                   	 |
| env_output_filename                              | str      | No        | No
| env_output_format                                | str      | No        | No
| config_output_filename                           | str      | No        | No
| config_output_format                             | str      | No        | No
| schedule                                         | str       	| No      	| Yes                  	 |
| max_concurrency                                  | int       	| No      	| Yes                  	 |
| max_conflicting_age                              | int       	| No      	| Yes                  	 |
| offline_mode                                     | bool       	| No      	| Yes                  	 |
| prevent_offline_execution                        | bool       	| No      	| Yes                  	 |
| service                                          | bool       	| No      	| Yes                  	 |
| deployment                                       | str       	| No      	| Yes                  	 |
| api_base_url                                     | str       	| No      	| Yes                  	 |
| api_heartbeat_interval                           | int       	| No      	| Yes                  	 |
| enable_status_listener                           | bool      	| No      	| Yes                  	 |
| status_update_socket_port                        | int        	| No      	| Yes                  	 |
| status_update_message_max_bytes                  | int        	| No      	| Yes                  	 |
| status_update_interval                           | int        	| No      	| Yes                  	 |
| log_level                                        | str        	| No      	| Yes                  	 |
| include_timestamps_in_log                        | bool      	| No      	| Yes                  	 |
| log_input_value                                  | bool      	| No      	| Yes                  	 |
| log_result_value                                 | bool      	| No      	| Yes                  	 |
| num_log_lines_sent_on_failure                    | int      	| No      	| Yes                  	 |
| num_log_lines_sent_on_timeout                    | int      	| No      	| Yes                  	 |
| num_log_lines_sent_on_success                    | int      	| No      	| Yes                  	 |
| max_log_line_length                              | int      	| No      	| Yes                  	 |
| merge_stdout_and_stderr_logs                     | bool      	| No      	| Yes                  	 |
| ignore_stdout                                    | bool      	| No      	| Yes                  	 |
| ignore_stderr                                    | bool      	| No      	| Yes                  	 |
| api_key                                          | str        	| Yes     	| Yes                  	 |
| api_request_timeout                              | int        	| Yes      	| Yes                  	 |
| api_error_timeout                                | int        	| Yes     	| Yes                  	 |
| api_retry_delay                                  | int        	| Yes     	| Yes                  	 |
| api_resume_delay                                 | int        	| Yes     	| Yes                  	 |
| api_task_execution_creation_error_timeout        | int | Yes     	| Yes                  	 |
| api_task_execution_creation_conflict_timeout     | int | Yes    | Yes                  	 |
| api_task_execution_creation_conflict_retry_delay | int| Yes	| Yes                  	 |
| api_managed_probability                          | float         | No        | Yes                    |
| api_failure_report_probability                   | float         | No        | Yes                    |
| api_timeout_report_probability                   | float         | No        | Yes                    |
| process_timeout                                  | int        	| Yes     	| Yes                  	 |
| process_max_retries                              | int        	| Yes     	| Yes                  	 |
| process_retry_delay                              | int        	| Yes     	| Yes                  	 |
| command                                          | list[str]  	| Yes     	| Yes                  	 |
| command_line                                     | str        	| Yes     	| Yes                  	 |
| shell_mode                                       | bool      	| Yes     	| Yes                  	 |
| strip_shell_wrapping                             | bool      	| Yes     	| Yes                  	 |
| work_dir                                         | str       	| Yes     	| Yes                  	 |
| process_termination_grace_period                 | int       	| Yes     	| Yes                  	 |
| send_pid                                         | bool      	| Yes     	| Yes                  	 |
| send_hostname                                    | bool      	| Yes     	| Yes                  	 |
| send_input_value                                 | bool      	| No       	| No                  	 |
| send_result_value                                | bool      	| No       	| No                  	 |
| send_runtime_metadata                            | bool      	| Yes     	| Yes                  	 |

Keys that are marked with "Mutable" -- "No" in the table above can be
overridden when the configuration is reloaded after the `config_ttl` expires.

Keys that are marked as "Uses Resolved Config" -- "Yes" in the table above can
come from the resolved configuration after secret resolution (see below).

## Secret Fetching and Resolution

A common requirement is that deployed code / images do not contain secrets
internally which could be decompiled. Instead, programs should fetch secrets
from an external source in a secure manner. If your program runs in AWS, it
can make use of AWS's roles that have permission to access data in
Secrets Manager, Systems Manager Parameter Store, AppConfig, or S3. However, in
many scenarios, having your program access AWS directly has the following
disadvantages:

1) Your program becomes coupled to AWS, so it is difficult to run locally or
switch to another infrastructure provider
2) You need to write code or use a library for each programming language you
use, so secret fetching is done in a non-uniform way
3) Writing code to merge and parse secrets from different sources is tedious

Therefore, proc_wrapper implements Secret Fetching and Resolution to solve
these problems so your programs don't have to. Both usage modes can fetch secrets from
[AWS Secrets Manager](https://aws.amazon.com/secrets-manager/),
[AWS Systems Manager Parameter Store](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html),
[AWS AppConfig](https://docs.aws.amazon.com/appconfig/),
[AWS S3](https://docs.aws.amazon.com/s3/), or the local filesystem, and
optionally extract embedded data
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

| Provider Code 	| Value Prefix              	| Provider                     	| Example Address                                             	| Required extras            | Notes                                                         	|
|---------------	|---------------------------	|------------------------------	|-------------------------------------------------------------	|-----------------------------------------------------------------------------	|---------------------------------------------------------------	|
| `AWS_SM`      	| `arn:aws:secretsmanager:` 	| AWS Secrets Manager          	| `arn:aws:secretsmanager:us-east-1:123456789012:secret:config` 	| aws 	| Can also include version suffix like `-PPrpY`|
| `AWS_SSM`      	| `ssm:` or `arn:aws:ssm:` | AWS Systems Manager Parameter Store | `ssm:/MyOrg/MyApp/config.json`, `arn:aws:ssm:us-east-1:123456789012:parameter/MyOrg/MyApp/config.json` | aws 	| Can also include version suffix like `:36`|
| `AWS_APPCONFIG` | `aws:appconfig` | AWS App Config | `aws:appconfig:app_id/env_id/config_id` 	| aws	||
| `AWS_S3`      	| `arn:aws:s3:::`           	| AWS S3 Object                	| `arn:aws:s3:::examplebucket/staging/app1/config.json`       	| aws 	|                                                               	|
| `FILE`        	| `file://`                 	| Local file                   	| `file:///home/appuser/app/.env`                                    	|                                                                             	| The default provider if no provider is auto-detected          	|
| `ENV`         	|                           	| The process environment      	| `SOME_TOKEN`                                                	|                                                                             	| The name of another environment variable                      	|
| `CONFIG`      	|                           	| The configuration dictionary 	| `$.db`                                                      	| jsonpath         	| JSON path expression to extract the data in the configuration 	|
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

| Format Code 	| Extensions      	| MIME types                                                                            	| Required extras                                        	| Notes                                            	|
|-------------	|-----------------	|---------------------------------------------------------------------------------------	|------------------------------------------------------	|--------------------------------------------------	|
| `dotenv`    	| `.env`          	| None                                                                                  	| dotenv 	| Also auto-detected if location includes `.env.`  	|
| `json`      	| `.json`         	| `application/json`, `text/x-json`                                                     	|                                               	|  	|
| `yaml`      	| `.yaml`, `.yml` 	| `application/x-yaml`, `application/yaml`, `text/vnd.yaml`, `text/yaml`, `text/x-yaml` 	| yaml                        	| pyyaml's `safe_load()` is used for security               	|

The format of a secret value can be auto-detected from the extension or by the
MIME type if available. Otherwise, you may need to an explicit format code
(e.g. `!yaml`).

#### AWS Secrets Manager / SSM / AppConfig / S3 notes

[boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)
is used to fetch secrets when the `aws` extra is installed. It will try to
access to AWS Secrets Manager, Systems Manager Parameter Store, AppConfig, and
S3 using environment variables `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`
if they are set, or use the EC2 instance role, ECS task role,
or Lambda execution role if available.

For Secrets Manager, you can also use "partial ARNs" (without the hyphened
suffix) as keys. In the example above

    arn:aws:secretsmanager:us-east-2:1234567890:secret:config

could be used to fetch the same secret, provided there are no conflicting secret ARNs.
This allows you to get the latest version of the secret.

If the secret was stored in Secrets Manager as binary, the
corresponding value will be set to the Base-64 encoded value.

For SSM, you can include/exclude a version suffix like `:14` to either pin
the version (if included) or get the latest version (if excluded).

If you're deploying a python function using AWS Lambda, we strongly encourage
you to add:

    logging.getLogger("botocore").setLevel(logging.INFO)

to your code if you are using proc_wrapper for secrets resolution. This
prevent secrets from Secrets Manager from being leaked. For details, see this
[issue](https://github.com/boto/boto3/issues/2292).

### Secret Transformation

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
environment variables using [jsonpath-ng](https://github.com/h2non/jsonpath-ng)
when the `jsonpath` extra is installed.
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
would add the environment variable DB_USER_FOR_PROC_WRAPPER_TO_RESOLVE
set to

    ENV:DB_CONFIG|JP:$.username

### Secret injection into environment and configuration

Now let's use secret location strings to
inject the values into the environment (for wrapped mode)
and/or the configuration dictionary (for embedded mode). proc_wrapper
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
`config_locations` to a list of secret locations. Alternatively, you can set
the environment variable `PROC_WRAPPER_CONFIG_LOCATIONS` to a comma-separated
list, and this will be picked up automatically. Secret location values
will be parsed as JSON unless the format is auto-detected or explicitly
specified. The `config` argument
passed to the your callback function will contain a merged dictionary of all
fetched and parsed dictionary values. For example:

```python
def callback(wrapper: ProcWrapper, cbdata: str,
        config: dict[str, Any]) -> str:
    return "super" + cbdata + config["username"]


def main():
    params = ProcWrapperParams()

    # Optional: you can set an initial configuration dictionary which will
    # have its values included in the final configuration unless overridden.
    params.initial_config = {
        "log_level": "DEBUG"
    }

    # You can omit this if you set PROC_WRAPPER_CONFIG_LOCATIONS environment
    # variable to the same ARN
    params.config_locations = [
        "arn:aws:secretsmanager:us-east-2:1234567890:secret:db-PPrpY",
        # More secret locations can be added here, and their values will
        # be merged
    ]

    wrapper = ProcWrapper(params=params)

    # Returns "superduperpostgres"
    return wrapper.managed_call(callback, "duper")
```

#### Merging Secrets

Top-level fetching can potentially fetch multiple dictionaries which are
merged together in the final environment / configuration dictionary.
The default merge strategy (`DEEP`) merges recursively, even dictionaries
in lists. The `SHALLOW` merge strategy just overwrites top-level keys, with later
secret locations taking precedence. However, if you install the
[mergedeep](https://github.com/clarketm/mergedeep) library with the `mergedeep`
extra, you can also set the merge strategy to one of:

* `REPLACE`
* `ADDITIVE`
* `TYPESAFE_REPLACE`
* `TYPESAFE_ADDITIVE`

so that nested lists can be appended to instead of replaced (in the case of the
`ADDITIVE` strategies), or errors will be raised if incompatibly-typed values
are merged (in the case of the `TYPESAFE` strategies). In wrapped mode,
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

```js
{
  "db_username": "postgres",
  "db_password": "badpassword",
  ...
}
```

The initial configuration dictionary would look like:

```js
{
  "db_username__to_resolve": "arn:aws:secretsmanager:us-east-2:1234567890:secret:db-PPrpY|JP:$.username",
  "db_password__to_resolve": "arn:aws:secretsmanager:us-east-2:1234567890:secret:db-PPrpY|JP:$.password",

  ...
}
```

(The `__to_resolve` suffix (with 2 underscores!) of keys is removed during
resolution. It can also be configured with the `resolved_config_property_name_suffix`
property of `ProcWrapperParams`.)

proc_wrapper can also resolve keys in embedded dictionaries, like:

```js
{
  "db": {
    "username__to_resolve": "arn:aws:secretsmanager:us-east-2:1234567890:secret:config-PPrpY|JP:$.username",
    "password__to_resolve":
    "arn:aws:secretsmanager:us-east-2:1234567890:secret:config-PPrpY|JP:$.password",
    ...
  },
  ...
}
```

up to a maximum depth that you can control with `ProcWrapperParams.max_config_resolution_depth` (which defaults to 5). That would resolve to

```js
{
  "db": {
    "username": "postgres",
    "password": "badpassword"
    ...
  },
  ...
}
```

You can also inject entire dictionaries, like:

```js
{
  "db__to_resolve": "arn:aws:secretsmanager:us-east-2:1234567890:secret:config-PPrpY",
  ...
}
```

which would resolve to


```js
{
  "db": {
    "username": "postgres",
    "password": "badpassword"
  },
  ...
}
```

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
dictionaries. However, the final environment passed to the process is a flat
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

### Variable output

You can have proc_wrapper output the resolved environment and/or
configuration as a file by specifying the `--env-output-filename` and/or
`--config-output-filename` parameters, respectively. The output format
is one of `dotenv`, `json`, and `yaml` and will be auto-detected from the
filename if possible. It can be overridden with the
`--env-output-format` and `--config-output-format` parameters, otherwise
it defaults to `dotenv` for the resolved environment, and `json` for the
resolved configuration.

For security, the variable output files are deleted after all executions
of the process have completed. However, for debugging, the
`--exit-after-variable-resolution` parameter can be used which will cause
proc_wrapper to skip executing a process, and skip the deletion of the
variable output files.

## Input and Result Values

CloudReactor tracks the input and result values of Task Executions, and
also passes result values to downstream Tasks in Workflows, as part of their
input value.

For embedded functions, the input (argument) and result (return value) are
automatically sent to the Task Management server.

For wrapped processes, proc_wrapper simulates functional behavior using
environment variables and files. Input values can be passed to wrapped processes
in four ways:

1. By passing a value after the command-line option `--input-value`
2. By setting the `PROC_WRAPPER_INPUT_VALUE` environment variable to a
stringified version of the input value
3. By setting the `PROC_WRAPPER_INPUT_ENV_VAR_NAME` environment variable to the
name of an environment variable, and the value of the environment variable to
a stringified version of the input value
4. By setting the `PROC_WRAPPER_INPUT_FILENAME` to the name of a file containing
the input value

To pass a result value from a wrapped process back to CloudReactor, write the
result to a file. The filename of the result file can be passed to
proc_wrapper either with the `--result-filename` command-line option, or the
 environment variable `PROC_WRAPPER_RESULT_FILENAME`.

Both input and result values are passed as text in all the methods above.
However, the actual values can be any data structure expressable as JSON,
such as a dictionary or an array. Conversion to and from text values to the
actual values is controlled by the value format parameters,
`--input-value-format` and `--result-value-format`, or the corresponding
environment variables `PROC_WRAPPER_INPUT_VALUE_FORMAT` and
`PROC_WRAPPER_RESULT_VALUE_FORMAT`. Value formats may be `text`, `json`, or
`yaml` (requires the `pyyaml` library). If not specified, the value format will be
auto-detected from the input or result filename if possible, falling back to
`text` if auto-detection fails.

For security, neither the input or result values are logged by default, but that
can be changed with the `--log-input-value` and `--log-result-value` parameters,
or the corresponding environment variables `PROC_WRAPPER_LOG_INPUT_VALUE` and
`PROC_WRAPPER_LOG_RESULT_VALUE` set to `TRUE`. Also, if a result filename was
specified, the result file is deleted before exiting, unless
`--no-cleanup-result-file` is specified.

## Log Capture

proc_wrapper can capture the last few lines (tail) of the output that a
wrapped process writes to stdout and/or stderr, and send it to the API Server
for debugging / searching purposes. To enable this feature, set the
`num_log_lines_sent_on_failure`, `num_log_lines_sent_on_timeout`, and/or
`num_log_lines_sent_on_success` which determine the number of lines sent back
if the process fails, times out, or succeeds, respectively. stdout always goes
to the debug log of a Task Execution. By default,
stdout and stderr are merged into the debug log, but you can disable by
setting the environment variable `PROC_WRAPPER_MERGE_STDOUT_AND_STDERR_LOGS` to
`FALSE`. If disabled, stderr will be saved in the error log of the Task Execution.

In embedded mode, instances of ProcWrapper provides two methods to capture
logs:

* `debug_output(msg)`: adds the message to the debug log
* `get_embedded_logging_handler()` returns a logging handler to be used with
python's logging package. Messages sent to this handler are added to the
error log, which is by default merged with the debug log. Example usage:

```python
def callback(wrapper: ProcWrapper, cbdata: Any, config[dict[str, str]]) -> str:
    logger = logging.getLogger("myapp")
    logger.setLevel(logging.INFO)
    log_handler = wrapper.get_embedded_logging_handler()
    log_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(log_handler)

    # Adds the message to the Task Execution's debug log if the
    # merge_stdout_with_stderr setting is False (the default). Otherwise, this
    # adds the message to the error log.
    logger.info("I am logging!")

    # Always adds the message to the debug log
    wrapper.debug_output("Debugging is hard")

    return 'Done!'
```

## Status Updates

### Status Updates in Wrapped Mode

While your process in running, you can send status updates to
CloudReactor by using the `StatusUpdater` class. Status updates are shown in
the CloudReactor dashboard and allow you to track the current progress of a
Task and also how many items are being processed in multiple executions
over time.

In wrapped mode, your application code would send updates to the
proc_wrapper program via UDP port 2373 (configurable with the
`PROC_WRAPPER_STATUS_UPDATE_PORT` environment variable).
If your application code is in python, you can use the provided
`StatusUpdater` class to do this:

```python
from proc_wrapper import StatusUpdater

with StatusUpdater() as updater:
    updater.send_update(last_status_message="Starting ...")
    success_count = 0

    for i in range(100):
        try:
            do_work()
            success_count += 1
            updater.send_update(success_count=success_count)
        except Exception:
            failed_count += 1
            updater.send_update(failed_count=failed_count)

    updater.send_update(last_status_message="Finished!")
```

### Status Updates in Embedded Mode

In embedded mode, your callback in python code can use the wrapper instance to
send updates:

```python
from typing import Any, Mapping

import proc_wrapper
from proc_wrapper import ProcWrapper

def fun(wrapper: ProcWrapper, cbdata: dict[str, int],
        config: Mapping[str, Any]) -> int:
    wrapper.update_status(last_status_message="Starting the fun ...")

    success_count = 0
    error_count = 0
    for i in range(100):
        try:
            do_work()
            success_count += 1
        except Exception:

            error_count += 1
        wrapper.update_status(success_count=success_count,
                error_count=error_count)

    wrapper.update_status(last_status_message="The fun is over.")

    return cbdata["a"]

params = ProcWrapperParams()
params.auto_create_task = True
params.auto_create_task_run_environment_name = "production"
params.task_name = "embedded_test"
params.api_key = "YOUR_CLOUDREACTOR_API_KEY"

proc_wrapper = ProcWrapper(params=params)
proc_wrapper.managed_call(fun, {"a": 1, "b": 2})
```

## Sampling

In case the wrapped process or embedded function will be executed very frequently,
it may be advantageous to skip notifications to the Task Management server in order to avoid the
overhead (delay, bandwidth) of communication with the Task Management server, or to reduce usage of
API credits. For this purpose, you can configure proc_wrapper to communicate with the API
server with a  certain probability using the `api-managed-probability` setting. If a
generated random  number between 0 and 1 is above the probability threshold, the initial
notification to create a Task Execution will be skipped. If the wrapped process or
embedded function succeeds, no communication with the Task Management server will be sent.

However, if the wrapped process or embedded function fails, proc_wrapper will communicate
with the Task Management server with probability `api-failure-notification-probability`, creating
a Task Exection at that point with a `FAILED` status. Similarly, if the wrapped process
times out, proc_wrapper will communicate with the Task Management server with probability
`api-timeout-notification-probability`, creating
a Task Exection at that point with a `TERMINATED_AFTER_TIME_OUT` status.
(Currently, timing out embedded functions is not supported.)

## Example Projects

These projects contain sample Tasks that use this library to report their
execution status and results to CloudReactor:

* [cloudreactor-python-ecs-quickstart](https://github.com/CloudReactor/cloudreactor-python-ecs-quickstart)
runs python code in a Docker container in AWS ECS Fargate (wrapped mode)
* [cloudreactor-python-lambda-quickstart](https://github.com/CloudReactor/cloudreactor-python-lambda-quickstart)
runs python code in AWS Lambda (embedded mode)
* [cloudreactor-java-ecs-quickstart](https://github.com/CloudReactor/cloudreactor-java-ecs-quickstart)
runs Java code in a Docker container in AWS ECS Fargate (wrapped mode)
* [aws-otel-collector-cloudreactor](https://github.com/CloudReactor/aws-otel-collector-cloudreactor)
enhances the AWS OTEL collector to report execution status to CloudReactor, and
can be used as a sidecar container so that the main container doesn't need to
run proc_wrapper.

## License

This software is dual-licensed under open source (MPL 2.0) and commercial
licenses. See `LICENSE` for details.

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
