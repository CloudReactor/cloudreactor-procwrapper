# Installation

The package is published on [PyPI](https://pypi.org/project/deezer-python/)
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
