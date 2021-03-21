#!/bin/bash

set -e

eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"

pyenv deactivate || true
pyenv activate process_wrapper_python_dev_base

pip-compile --allow-unsafe --generate-hashes dev-base-requirements.in --output-file dev-base-requirements.txt
pip-sync proc_wrapper-requirements.txt dev-base-requirements.txt
