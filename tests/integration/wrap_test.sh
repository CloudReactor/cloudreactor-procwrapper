#!/bin/bash

set -e

SCRIPT_DIR=`dirname "$0"`
SCRIPT_ABS_DIR=`readlink -e $SCRIPT_DIR`
BASE_DIR="$SCRIPT_DIR/../.."

source $SCRIPT_DIR/common_env.sh;
source $SCRIPT_DIR/secret_env.sh;

echo "SCRIPT_ABS_DIR = $SCRIPT_ABS_DIR"

CMD="python $SCRIPT_ABS_DIR/sleep.py"

if [[ "$#" -gt 0 ]];
then
    CMD=$1
fi

pushd .
cd $BASE_DIR
python3 -m proc_wrapper $CMD
popd
