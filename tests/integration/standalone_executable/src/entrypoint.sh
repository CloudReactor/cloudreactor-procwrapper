#!/bin/bash

set -e

exec ./proc_wrapper -e common.env -e secret.env "$@"
