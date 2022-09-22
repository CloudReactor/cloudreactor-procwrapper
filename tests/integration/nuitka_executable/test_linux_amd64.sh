#!/bin/bash

set -e

SCRIPT_DIR=`dirname "$0"`
INTEGRATION_DIR=$SCRIPT_DIR/..
BASE_DIR=$INTEGRATION_DIR/../..
VERSION=`awk '/^version = "[^"]+"/ { print $3  }' $BASE_DIR/pyproject.toml  | sed 's/\"//g'`
CONTEXT_DIR=$SCRIPT_DIR/docker_context_linux_amd64/

IMAGE_NAME=cloudreactor-proc-wrapper-nuitka-test-linux-amd64

mkdir -p "$SCRIPT_DIR/docker_context_linux_amd64"
cp "$BASE_DIR/bin/nuitka/linux-amd64/$VERSION/proc_wrapper.bin" $CONTEXT_DIR
cp "$INTEGRATION_DIR/common.env" "$CONTEXT_DIR/"
cp "$INTEGRATION_DIR/secret.env" "$CONTEXT_DIR/"
docker build -t $IMAGE_NAME "$SCRIPT_DIR/docker_context_linux_amd64"
export PROC_WRAPPER_TASK_COMMAND='echo "nuitka linux-amd64 executable passed!"'
docker run --rm -e PROC_WRAPPER_LOG_LEVEL=DEBUG -e PROC_WRAPPER_TASK_COMMAND $IMAGE_NAME
