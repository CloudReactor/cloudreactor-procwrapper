#!/bin/bash

set -e

SCRIPT_DIR=`dirname "$0"`
SCRIPT_ABS_DIR=`readlink -e $SCRIPT_DIR`
BASE_DIR="$SCRIPT_DIR/.."
VERSION=`awk '/^version = "[^"]+"/ { print $3  }' ../pyproject.toml  | sed 's/\"//g'`
DEST_DIR="$BASE_DIR/bin/pyinstaller/al2/$VERSION"

mkdir -p $DEST_DIR

IMAGE_NAME=cloudreactor-proc-wrapper-pyinstaller-al2

docker build -f "$SCRIPT_DIR/Dockerfile-al2" -t $IMAGE_NAME $BASE_DIR
docker run --rm $IMAGE_NAME
#docker run -ti --rm --entrypoint=bash $IMAGE_NAME

TEMP_CONTAINER_NAME="$IMAGE_NAME-temp"

docker rm $TEMP_CONTAINER_NAME || true
docker create --name $TEMP_CONTAINER_NAME $IMAGE_NAME
docker cp $TEMP_CONTAINER_NAME:/home/appuser/dist/proc_wrapper $DEST_DIR/proc_wrapper.bin

docker rm $TEMP_CONTAINER_NAME

echo "Finished PyInstaller build!"
