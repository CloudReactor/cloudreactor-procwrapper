import json
import logging
import os
import time
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Tuple

from .common_utils import coalesce, string_to_bool
from .arg_parser import make_default_args
from .runtime_metadata import RuntimeMetadata

DEFAULT_RESOLVABLE_ENV_VAR_PREFIX = ''
DEFAULT_RESOLVABLE_ENV_VAR_SUFFIX = '_FOR_PROC_WRAPPER_TO_RESOLVE'

JSON_PATH_TRANSFORM_PREFIX = 'JP:'

# By default JSON Path expressions that resolve to an single element
# result in using the single element as the value.
# This suffix after JSON Path expression indicates that a single-valued
# JSON Path result should be kept an array value.
SPLAT_AFTER_JSON_PATH_SUFFIX = '[*]'

SECRET_PROVIDER_PLAIN = 'PLAIN'
SECRET_PROVIDER_ENV = 'ENV'
SECRET_PROVIDER_AWS_SECRETS_MANAGER = 'AWS_SM'

SECRET_PROVIDER_INFO: Dict[str, Dict[str, Any]] = {
    SECRET_PROVIDER_PLAIN: {},
    SECRET_PROVIDER_ENV: {},
    SECRET_PROVIDER_AWS_SECRETS_MANAGER: {},
}

DEFAULT_TRANSFORM_SEPARATOR = '|'

_logger = logging.getLogger(__name__)
_logger.addHandler(logging.NullHandler())


def _json_path_results_to_env_value(results: List,
        should_splat: bool, env_name: str, path_expr: Optional[str]) -> str:
    results_len = len(results)

    if results_len == 0:
        _logger.warning(f"Got no results for environment variable '{env_name}' with JSON path '{path_expr}'")
        if should_splat:
            return '[]'

        return ''

    if (results_len == 1) and not should_splat:
        v = results[0].value
        if isinstance(v, bool):
            # Boolean values get transformed to environment value TRUE or FALSE
            return str(v).upper()

        if isinstance(v, (dict, list)):
            # Collections get serialized as JSON
            return json.dumps(v)

        return str(v)

    # Multiple values get serialized as a JSON array
    # TODO: implement a way to output comma-separated values
    return json.dumps([r.value for r in results])


class CachedEnvValueEntry(NamedTuple):
    string_value: str
    parsed_value: Any
    fetched_at: float

    def parsed(self) -> 'CachedEnvValueEntry':
        if self.parsed_value:
            return self

        parsed = json.loads(self.string_value)
        return CachedEnvValueEntry(string_value=self.string_value,
                parsed_value=parsed, fetched_at=self.fetched_at)

    def is_stale(self, ttl_seconds: Optional[int]) -> bool:
        if ttl_seconds is None:
            return False

        return (time.time() - self.fetched_at) > ttl_seconds


class EnvResolver:
    def __init__(self, resolved_env_ttl: Optional[int] = None,
            log_secrets: bool = False,
            runtime_metadata: Optional[RuntimeMetadata] = None,
            args: Any = None,
            env_override: Optional[Mapping[str, Any]] = None) -> None:
        self.resolved_env_ttl = resolved_env_ttl
        self.log_secrets = log_secrets
        self.runtime_metadata = runtime_metadata

        # Dictionary from SECRET_PROVIDER_XXX constants to caches from lookup values
        # to resolved values and metadata
        self.secret_cache: Dict[str, Dict[str, CachedEnvValueEntry]] = {}
        self.aws_secrets_manager_client = None
        self.aws_secrets_manager_client_create_attempted_at: Optional[float] = None

        if env_override:
            self.env = dict(env_override)
        else:
            self.env = os.environ.copy()

        self.overwrite_env_during_secret_resolution = string_to_bool(
                self.env.get('PROC_WRAPPER_OVERWRITE_ENV_WITH_SECRETS'),
                default_value=False)

        if args:
            self.args = args
        else:
            self.args = make_default_args()

    def resolve_env(self) -> Tuple[Dict[str, str], List[str]]:
        """
          Resolve environment variables, returning a dictionary of the environment,
          and a list of variable names that failed to be resolved.
        """
        _logger.debug('Starting secrets resolution ...')

        if not string_to_bool(
                self.env.get('PROC_WRAPPER_RESOLVE_SECRETS'), False):
            _logger.debug('Secrets resolution is disabled.')
            return (self.env, [])

        prefix = coalesce(self.env.get('PROC_WRAPPER_RESOLVABLE_ENV_VAR_PREFIX'),
                DEFAULT_RESOLVABLE_ENV_VAR_PREFIX)
        prefix_length = len(prefix)

        suffix = coalesce(self.env.get('PROC_WRAPPER_RESOLVABLE_ENV_VAR_SUFFIX'),
                DEFAULT_RESOLVABLE_ENV_VAR_SUFFIX)
        suffix_length = len(suffix)

        resolved = {}
        failed_env_names = []
        for name, value in self.env.items():
            if name.startswith(prefix) and name.endswith(suffix):
                key = name[prefix_length:len(name) - suffix_length]
                env_name = key

                _logger.debug(f"Resolving key '{key}' with value '{value}' ...")
                try:
                    env_name, env_value = self.resolve_env_var(key, value)

                    if env_name is None:
                        pass
                    elif (not self.overwrite_env_during_secret_resolution) \
                            and (env_name in self.env):
                        _logger.info(f"Skipping overwriting of environment variable '{key}'")
                    else:
                        resolved[env_name] = env_value
                except Exception:
                    msg = f"Failed to resolve environment variable '{env_name}'"
                    if self.log_secrets:
                        msg += f" which had value '{value}'"
                    _logger.exception(msg)
                    failed_env_names.append(env_name)
            else:
                resolved[name] = value

        return resolved, failed_env_names

    def resolve_env_var(self, name: str, value: str) -> Tuple[str, str]:
        env_name = name
        secret_provider = None
        secret_provider_info = {}

        for sp, spi in SECRET_PROVIDER_INFO.items():
            if name.startswith(sp + '_'):
                env_name = name[len(sp) + 1:]
                secret_provider = sp
                secret_provider_info = spi
                break

        secret_provider = secret_provider or SECRET_PROVIDER_PLAIN

        if self.log_secrets:
            _logger.debug(f"Secret provider = '{secret_provider}', name = '{name}', value = '{value}'")

        value_to_lookup = value
        jsonpath_expr_str = None
        jsonpath_expr = None
        should_splat = False

        transform_separator = secret_provider_info.get('transform_separator',
                DEFAULT_TRANSFORM_SEPARATOR)
        separator_index = value.find(transform_separator)
        if (separator_index > 0) and (separator_index < len(value) - 2):
            transform_expr_str = value_to_lookup[(separator_index + 1):]

            if transform_expr_str.startswith(JSON_PATH_TRANSFORM_PREFIX):
                try:
                    import jsonpath_ng  # type: ignore
                except ImportError as import_error:
                    _logger.exception('jsonpath_ng is not available to import, please install it in your python environment')
                    raise import_error

                jsonpath_expr_str = transform_expr_str[len(JSON_PATH_TRANSFORM_PREFIX):]

                _logger.debug(f"jsonpath_expr_str = '{jsonpath_expr_str}'")

                if jsonpath_expr_str.endswith(SPLAT_AFTER_JSON_PATH_SUFFIX):
                    should_splat = True
                    jsonpath_expr_str = jsonpath_expr_str[0:-len(SPLAT_AFTER_JSON_PATH_SUFFIX)]

                try:
                    jsonpath_expr = jsonpath_ng.parse(jsonpath_expr_str)
                except Exception as ex:
                    _logger.exception(f"Could not parse '{jsonpath_expr_str}' as a JSON Path expression")
                    raise ex

                value_to_lookup = value_to_lookup[0:separator_index]
            else:
                raise RuntimeError(f"Unknown transform for value '{value}'")

        cached_env_value_entry: Optional[CachedEnvValueEntry] = None

        cache = self.secret_cache.get(secret_provider)

        if cache is None:
            cache = {}
            self.secret_cache[secret_provider] = cache
        else:
            cached_env_value_entry = cache.get(value_to_lookup)

        if (cached_env_value_entry is None) \
                or cached_env_value_entry.is_stale(self.resolved_env_ttl):
            secret_value: Optional[str] = None
            if secret_provider == SECRET_PROVIDER_AWS_SECRETS_MANAGER:
                secret_value = self.fetch_aws_secrets_manager_secret(value_to_lookup)
            elif secret_provider == SECRET_PROVIDER_PLAIN:
                secret_value = value_to_lookup
            elif secret_provider == SECRET_PROVIDER_ENV:
                secret_value = self.env.get(value_to_lookup)
            else:
                raise RuntimeError(f'Unknown secret provider: {secret_provider}')

            if secret_value is None:
                raise RuntimeError(f"Missing secret value for lookup value '{value_to_lookup}'")

            cached_env_value_entry = CachedEnvValueEntry(string_value=secret_value,
                    parsed_value=None, fetched_at=time.time())

            cache[value_to_lookup] = cached_env_value_entry

        if self.log_secrets:
            _logger.debug(f"value_to_lookup = '{value_to_lookup}', cache entry = '{cached_env_value_entry}'")

        if jsonpath_expr is None:
            return (env_name, cached_env_value_entry.string_value)

        try:
            parsed_entry = cached_env_value_entry.parsed()
        except Exception as ex:
            _logger.exception(f"Error parsing value for key '{value_to_lookup}' for JSON path lookup")
            raise ex

        # Save the parsed value so we don't have to reparse.
        cache[value_to_lookup] = parsed_entry

        results = jsonpath_expr.find(parsed_entry.parsed_value)

        if self.log_secrets:
            _logger.debug(f"json path results = {results}")

        env_value = _json_path_results_to_env_value(results,
                should_splat=should_splat,
                env_name=env_name, path_expr=jsonpath_expr_str)

        if self.log_secrets:
            _logger.debug(f"resolved env_name = '{env_name}', resolved env_value = '{env_value}'")

        return env_name, env_value

    def get_or_create_aws_secrets_manager_client(self):
        if not self.aws_secrets_manager_client:
            if self.aws_secrets_manager_client_create_attempted_at:
                return None

            self.aws_secrets_manager_client_create_attempted_at = time.time()

            try:
                import boto3
            except ImportError as import_error:
                _logger.exception('boto3 is not available to import, please install it in your python environment')
                raise import_error

            region_name = self.env.get('PROC_WRAPPER_SECRETS_AWS_REGION') or \
                    self.env.get('AWS_REGION') or self.env.get('AWS_DEFAULT_REGION')

            if not region_name:
                if self.runtime_metadata \
                        and ('aws' in self.runtime_metadata.derived):
                    region_name = self.runtime_metadata.derived['aws'].get('region')

            self.aws_secrets_manager_client = boto3.client(
                service_name='secretsmanager',
                region_name=region_name
            )

        return self.aws_secrets_manager_client

    def fetch_aws_secrets_manager_secret(self, value: str) -> str:
        client = self.get_or_create_aws_secrets_manager_client()

        if client is None:
            raise RuntimeError("Can't create AWS Secrets Manager client")

        _logger.info(f"Looking up Secrets Manager secret '{value}'")
        response = client.get_secret_value(SecretId=value)

        # Binary secrets are left Base-64 encoded
        return response.get('SecretString') or response['SecretBinary']
