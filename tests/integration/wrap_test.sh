#!/bin/bash

source test_env.sh;

CMD="python sleep.py"

if [[ "$#" -gt 0 ]];
then
    CMD=$1
fi

export PROC_WRAPPER_ENABLE_STATUS_UPDATE_LISTENER=TRUE
export PROC_WRAPPER_STATUS_UPDATE_INTERVAL_SECONDS=5
python3 -m proc_wrapper $CMD
