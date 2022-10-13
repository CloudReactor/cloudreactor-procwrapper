#!/bin/bash

set -e

SCRIPT_DIR=`dirname "$0"`
SCRIPT_ABS_DIR=`readlink -f $SCRIPT_DIR`
BASE_DIR="$SCRIPT_DIR/.."
VERSION=`awk '/^version = "[^"]+"/ { print $3  }' ../pyproject.toml  | sed 's/\"//g'`

echo "Building dir: $BASE_DIR"

IMAGE_NAME=cloudreactor-proc-wrapper-nuitka-debian

# create builder
# this needs to be run once before the build
# docker buildx create --name debian-builder --use

docker buildx use debian-builder

docker buildx inspect --bootstrap

# build both images
# --push here will push the manifest to the registry
docker buildx build \
    --platform linux/arm64,linux/amd64 \
    -t $IMAGE_NAME \
    --file "$SCRIPT_DIR/Dockerfile-debian" \
    $BASE_DIR

# install arm64 locally
docker buildx build \
    --platform linux/arm64 \
    -t "$IMAGE_NAME:arm64" \
    --load \
    --file "$SCRIPT_DIR/Dockerfile-debian" \
    $BASE_DIR

# install amd64 locally
docker buildx build \
    --platform linux/amd64 \
    -t "$IMAGE_NAME:amd64" \
    --load \
    --file "$SCRIPT_DIR/Dockerfile-debian" \
    $BASE_DIR


# push both platforms as one image manifest list
#docker buildx build \
#    --platform linux/arm64,linux/amd64 \
#    -t $IMAGE_NAME \
#    --push \
#    --file "$SCRIPT_DIR/Dockerfile-debian" \
#    $BASE_DIR


# below can be cleaned up

AMD64_IMAGE_NAME="$IMAGE_NAME:amd64"
docker run -ti --rm $AMD64_IMAGE_NAME
TEMP_CONTAINER_NAME="$IMAGE_NAME-amd64-temp"
docker rm $TEMP_CONTAINER_NAME | true
docker create --name $TEMP_CONTAINER_NAME $AMD64_IMAGE_NAME
DEST_DIR="$BASE_DIR/bin/nuitka/debian-amd64/$VERSION"
mkdir -p $DEST_DIR
docker cp $TEMP_CONTAINER_NAME:/home/appuser/proc_wrapper.bin $DEST_DIR

echo "removing container $TEMP_CONTAINER_NAME"
docker rm $TEMP_CONTAINER_NAME

ARM64_IMAGE_NAME="$IMAGE_NAME:arm64"
docker run -ti --rm $ARM64_IMAGE_NAME
TEMP_CONTAINER_NAME="$IMAGE_NAME-arm64-temp"
docker rm $TEMP_CONTAINER_NAME | true
docker create --name $TEMP_CONTAINER_NAME $ARM64_IMAGE_NAME
DEST_DIR="$BASE_DIR/bin/nuitka/debian-arm64/$VERSION"
mkdir -p $DEST_DIR
docker cp $TEMP_CONTAINER_NAME:/home/appuser/proc_wrapper.bin $DEST_DIR

echo "removing container $TEMP_CONTAINER_NAME"
docker rm $TEMP_CONTAINER_NAME

