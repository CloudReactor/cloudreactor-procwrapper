#!/bin/bash

set -e

eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"

pyenv deactivate || true
pyenv activate process_wrapper_python

pip-compile --allow-unsafe --generate-hashes --output-file=proc_wrapper-requirements.txt proc_wrapper-requirements.in
pip-sync proc_wrapper-requirements.txt
