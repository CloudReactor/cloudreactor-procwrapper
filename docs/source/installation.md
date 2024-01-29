# Installation

## In a Linux/AMD64 or Windows 64 environment

Standalone executables for 64-bit Linux and Windows are available,
located in `bin/pyinstaller/`. These executables bundle python
so you don't need to have python installed on your machine. They also bundle
all optional library dependencies so you can fetch secrets from AWS
Secrets Manager and extract them with jsonpath-ng, for example.

On a debian buster machine, the following packages (with known supported versions)
must be installed:

      openssl=1.1.1d-0+deb10u5
      libexpat1=2.2.6-2+deb10u1
      ca-certificates=20200601~deb10u2

See the example [Dockerfile](tests/integration/standalone_executable/docker_context_linux_amd64/Dockerfile) for a known working
environment.

Special thanks to [PyInstaller](https://www.pyinstaller.org/),
[wine](https://www.winehq.org/), and
[PyInstaller Docker Images](https://github.com/cdrx/docker-pyinstaller)
for making this possible!

## When python is available

The package is published on [PyPI](https://pypi.org/)
and can be installed with `pip` (or any equivalent):

```bash
pip install cloudreactor-procwrapper
```

Fetching secrets from AWS Secrets Manager requires that
[boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html) is available to import in your python environment.

JSON Path transformation of environment variables requires that [jsonpath-ng](https://github.com/h2non/jsonpath-ng)
be available to import in your python environment.

You can get the tested versions of both dependencies in
[proc_wrapper-requirements.in](https://github.com/CloudReactor/cloudreactor-procwrapper/blob/main/proc_wrapper-requirements.in)
(suitable for use by [https://github.com/jazzband/pip-tools/](pip-tools)) or the resolved requirements in
[proc_wrapper-requirements.txt](https://github.com/CloudReactor/cloudreactor-procwrapper/blob/main/proc_wrapper-requirements.txt).
