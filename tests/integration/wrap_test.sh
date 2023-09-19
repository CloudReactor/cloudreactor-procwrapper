#!/bin/bash

set -e

SCRIPT_DIR=`dirname "$0"`
SCRIPT_ABS_DIR=`readlink -e $SCRIPT_DIR`
BASE_DIR="$SCRIPT_DIR/../.."

PROC_WRAPPER_TASK_COMMAND="python $SCRIPT_ABS_DIR/sleep.py"

if [[ "$#" -gt 0 ]];
then
    PROC_WRAPPER_TASK_COMMAND=$*
fi

echo "PROC_WRAPPER_TASK_COMMAND = '$PROC_WRAPPER_TASK_COMMAND'"

export PROC_WRAPPER_TASK_COMMAND

export PROC_WRAPPER_TASK_IS_PASSIVE=TRUE

if [[ "$SIMULATE_CODEBUILD" == "1" ]]; then
    source $SCRIPT_ABS_DIR/codebuild.sh
fi

echo "PROC_WRAPPER_TASK_IS_PASSIVE = $PROC_WRAPPER_TASK_IS_PASSIVE"

pushd .
cd $BASE_DIR
poetry run proc_wrapper -l DEBUG --log-secrets -e $SCRIPT_ABS_DIR/common.env -e $SCRIPT_ABS_DIR/secret.env
popd
