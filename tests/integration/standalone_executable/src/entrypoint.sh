#!/bin/bash

set -e

source ./common_env.sh;
source ./secret_env.sh;
./proc_wrapper "$@"

