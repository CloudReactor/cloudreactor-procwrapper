#!/bin/bash

eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"

pyenv deactivate || true
pyenv activate pyinstaller
pip install pip-tools==5.5
pip-compile --allow-unsafe --generate-hashes proc_wrapper-requirements.in --output-file proc_wrapper-requirements.txt
pip-sync proc_wrapper-requirements.txt
pip install pyinstaller==4.2