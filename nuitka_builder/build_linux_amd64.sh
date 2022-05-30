#!/bin/bash
set -e

eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"

pyenv deactivate || true
pyenv activate nuitka

VERSION=`awk '/^version = "[^"]+"/ { print $3  }' ../pyproject.toml  | sed 's/\"//g'`

echo "VERSION = $VERSION"

OUTPUT_DIR="bin/nuitka/linux-amd64/$VERSION"

pushd .
cd ..
mkdir -p $OUTPUT_DIR
python -m nuitka --standalone --onefile --remove-output -o $OUTPUT_DIR/proc_wrapper.bin proc_wrapper/__main__.py
popd
