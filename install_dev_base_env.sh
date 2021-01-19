#!/bin/bash
pip-compile --allow-unsafe --generate-hashes dev-base-requirements.in --output-file dev-base-requirements.txt
pip-sync dev-base-requirements.txt
