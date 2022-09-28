#!/bin/bash

set -e

SCRIPT_DIR=`dirname "$0"`
INTEGRATION_DIR=$SCRIPT_DIR/..
BASE_DIR=$INTEGRATION_DIR/../..
VERSION=`awk '/^version = "[^"]+"/ { print $3  }' $BASE_DIR/pyproject.toml  | sed 's/\"//g'`
CONTEXT_DIR=$SCRIPT_DIR/docker_context_fedora_amd64/

IMAGE_NAME=cloudreactor-proc-wrapper-pyinstaller-test-fedora-amd64

mkdir -p "$SCRIPT_DIR/docker_context_fedora_amd64"
cp "$BASE_DIR/bin/pyinstaller/al2/$VERSION/proc_wrapper.bin" $CONTEXT_DIR/
cp "$INTEGRATION_DIR/common.env" "$CONTEXT_DIR/"
cp "$INTEGRATION_DIR/secret.env" "$CONTEXT_DIR/"
docker build -t $IMAGE_NAME "$SCRIPT_DIR/docker_context_fedora_amd64"
export PROC_WRAPPER_TASK_COMMAND='echo "PyInstaller fedora executable passed!"'
#docker run --rm -e PROC_WRAPPER_TASK_COMMAND -it --entrypoint=bash $IMAGE_NAME
docker run --rm -e PROC_WRAPPER_LOG_LEVEL=DEBUG -e PROC_WRAPPER_TASK_COMMAND $IMAGE_NAME
