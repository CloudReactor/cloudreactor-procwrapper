#!/bin/bash
eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"

pyenv deactivate
pyenv activate pyinstaller

DEST_DIR=pyinstaller_build/platforms/linux-amd64

mkdir -p $DEST_DIR
pyinstaller -F --name proc_wrapper --workpath pyinstaller_build \
  --specpath pyinstaller_build --distpath $DEST_DIR \
  --clean proc_wrapper/__main__.py
