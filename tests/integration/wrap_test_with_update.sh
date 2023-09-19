#!/bin/bash

SCRIPT_DIR=`dirname "$0"`
SCRIPT_ABS_DIR=`readlink -e $SCRIPT_DIR`
BASE_DIR="$SCRIPT_DIR/../.."

CMD="python tests/integration/sleep_with_update.py"

if [[ "$#" -gt 0 ]];
then
    CMD=$1
fi

export PROC_WRAPPER_TASK_COMMAND="$CMD"
exec poetry run proc_wrapper --log-secrets -e $SCRIPT_ABS_DIR/common.env -e $SCRIPT_ABS_DIR/secret.env
