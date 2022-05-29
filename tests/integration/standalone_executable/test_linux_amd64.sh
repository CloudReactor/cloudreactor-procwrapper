#!/bin/bash

set -e

SCRIPT_DIR=`dirname "$0"`
INTEGRATION_DIR=$SCRIPT_DIR/..
BASE_DIR=$INTEGRATION_DIR/../..
CONTEXT_DIR=$SCRIPT_DIR/docker_context_linux_amd64/

IMAGE_NAME=cloudreactor-proc-wrapper-standalone-test-linux-amd64

mkdir -p "$SCRIPT_DIR/docker_context_linux_amd64"
cp "$BASE_DIR/pyinstaller_build/platforms/linux-amd64/proc_wrapper" $CONTEXT_DIR
cp "$INTEGRATION_DIR/common.env" "$CONTEXT_DIR/"
cp "$INTEGRATION_DIR/secret.env" "$CONTEXT_DIR/"
cp $SCRIPT_DIR/src/* "$CONTEXT_DIR/"
docker build -t $IMAGE_NAME "$SCRIPT_DIR/docker_context_linux_amd64"
docker run -ti --rm $IMAGE_NAME
