#!/bin/bash
pip-compile --allow-unsafe --generate-hashes dev-base-requirements.in --output-file dev-base-requirements.txt
pip-compile --allow-unsafe --generate-hashes dev-full-requirements.in --output-file dev-full-requirements.txt
pip-sync proc_wrapper-requirements.txt dev-base-requirements.txt dev-full-requirements.txt
