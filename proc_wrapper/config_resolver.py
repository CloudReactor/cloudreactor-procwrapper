import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional, cast

from .common_constants import (
    EXTENSION_TO_FORMAT,
    FORMAT_DOTENV,
    FORMAT_JSON,
    FORMAT_YAML,
)
from .common_utils import (
    best_effort_deep_merge,
    coalesce,
    parse_data_string,
    stringify_value,
    strip_after,
)
from .proc_wrapper_params import (
    CONFIG_MERGE_STRATEGY_DEEP,
    DEFAULT_CONFIG_MERGE_STRATEGY,
    NATIVE_CONFIG_MERGE_STRATEGIES,
    ConfigResolverParams,
)
from .runtime_metadata import RuntimeMetadata

FILE_URL_PREFIX = "file://"
FILE_URL_PREFIX_LENGTH = len(FILE_URL_PREFIX)

AWS_S3_VERSION_SEPARATOR = "#"

# By default JSON Path expressions that resolve to an single element
# result in using the single element as the value.
# This suffix after JSON Path expression indicates that a single-valued
# JSON Path result should be kept an array value.
SPLAT_AFTER_JSON_PATH_SUFFIX = "[*]"

SECRET_PROVIDER_PLAIN = "PLAIN"
SECRET_PROVIDER_ENV = "ENV"
SECRET_PROVIDER_CONFIG = "CONFIG"
SECRET_PROVIDER_FILE = "FILE"
SECRET_PROVIDER_AWS_SYSTEMS_MANAGER_PARAMETER_STORE = "AWS_SSM"
SECRET_PROVIDER_AWS_SECRETS_MANAGER = "AWS_SM"
SECRET_PROVIDER_AWS_S3 = "AWS_S3"
SECRET_PROVIDER_AWS_APP_CONFIG = "AWS_APP_CONFIG"

SECRET_PROVIDER_FILE_VALUE_PREFIX = "file://"
AWS_SYSTEMS_MANAGER_PARAMETER_STORE_PREFIX = "ssm:"
AWS_SYSTEMS_MANAGER_PARAMETER_STORE_ARN_PREFIX = "arn:aws:ssm:"
AWS_SECRETS_MANAGER_PREFIX = "arn:aws:secretsmanager:"
AWS_S3_PREFIX = "arn:aws:s3:::"
AWS_APP_CONFIG_PREFIX = "aws:appconfig:"

DEFAULT_TRANSFORM_SEPARATOR = "|"
SELF_TRANSFORM_VALUE = "SELF"
JSON_PATH_TRANSFORM_PREFIX = "JP:"

ALL_SUPPORTED_FORMATS = [FORMAT_DOTENV, FORMAT_JSON, FORMAT_YAML]

DEFAULT_FORMAT_SEPARATOR = "!"

MIME_TYPE_TO_FORMAT = {
    "application/json": FORMAT_JSON,
    "text/x-json": FORMAT_JSON,
    "application/x-yaml": FORMAT_YAML,
    "application/yaml": FORMAT_YAML,
    "text/vnd.yaml": FORMAT_YAML,
    "text/yaml": FORMAT_YAML,
    "text/x-yaml": FORMAT_YAML,
}


_logger = logging.getLogger(__name__)
_logger.addHandler(logging.NullHandler())


def transform_value(
    parsed_value: Any,
    string_value: Optional[str],
    transform_expr_str: str,
    log_secrets: bool = False,
) -> Any:
    result: Any = None

    if transform_expr_str == SELF_TRANSFORM_VALUE:
        return parsed_value

    if transform_expr_str.startswith(JSON_PATH_TRANSFORM_PREFIX):
        try:
            import jsonpath_ng  # type: ignore
        except ImportError as import_error:
            _logger.exception(
                "jsonpath_ng is not available to import, please install it in your python environment"
            )
            raise import_error

        jsonpath_expr_str = transform_expr_str[len(JSON_PATH_TRANSFORM_PREFIX) :]

        _logger.debug(f"jsonpath_expr_str = '{jsonpath_expr_str}'")

        should_splat = False
        if jsonpath_expr_str.endswith(SPLAT_AFTER_JSON_PATH_SUFFIX):
            should_splat = True
            jsonpath_expr_str = jsonpath_expr_str[
                0 : -len(SPLAT_AFTER_JSON_PATH_SUFFIX)
            ]

        try:
            jsonpath_expr = jsonpath_ng.parse(jsonpath_expr_str)
        except Exception as ex:
            _logger.exception(
                f"Could not parse '{jsonpath_expr_str}' as a JSON Path expression"
            )
            raise ex

        results = jsonpath_expr.find(parsed_value)

        if log_secrets:
            _logger.debug(f"json path results = {results}")

        results_len = len(results)

        if results_len == 0:
            msg = f"Got no results for value '{string_value or parsed_value}' with JSON path '{jsonpath_expr_str}'"
            _logger.info(msg)
            raise ValueNotFoundException(msg)
        else:
            transformed_values = [r.value for r in results]
            result = transformed_values

            if (not should_splat) and (results_len == 1):
                result = transformed_values[0]

        return result
    else:
        raise ValueError(
            f"Unknown transform for value '{string_value or parsed_value}'"
        )


class ValueNotFoundException(Exception):
    pass


class SecretProvider:
    def __init__(
        self,
        name: str,
        value_prefixes: Optional[list[str]] = None,
        should_cache: bool = True,
        top_level: bool = True,
        format_separator: Optional[str] = DEFAULT_FORMAT_SEPARATOR,
        transform_separator: str = DEFAULT_TRANSFORM_SEPARATOR,
    ):
        self.name = name
        self.value_prefixes = [name + ":"]

        if value_prefixes:
            self.value_prefixes.extend(value_prefixes)

        self.should_cache = should_cache
        self.top_level = top_level
        self.format_separator = format_separator
        self.transform_separator = transform_separator

    def supports_value(self, value: str) -> bool:
        for value_prefix in self.value_prefixes:
            if value.startswith(value_prefix):
                return True

        return False

    def cache_key_for_value(self, value: str) -> str:
        _logger.debug(f"Cache key for input value '{value}'")

        value, _format = self.extract_explicit_format(value)

        for value_prefix in self.value_prefixes:
            if value.startswith(value_prefix):
                value = value[len(value_prefix) :]
                break

        _logger.debug(f"Cache key for output value '{value}'")

        return value

    def fetch_data(
        self, location: str, config: dict[str, Any], env: dict[str, str]
    ) -> tuple[str, Optional[str], Optional[Any]]:
        if location.startswith(self.name + ":"):
            location = location[len(self.name) + 1 :]

        location, explicit_format = self.extract_explicit_format(location)

        data_string, format, parsed_data = self.fetch_internal(
            location=location, config=config, env=env, explicit_format=explicit_format
        )

        format = explicit_format or format

        if format is None:
            format = self.guess_format_from_location(location)

        return (data_string, format, parsed_data)

    def extract_explicit_format(self, location: str) -> tuple[str, Optional[str]]:
        explicit_format: Optional[str] = None
        if self.format_separator:
            lower_full_location = location.lower()
            for trial_format in ALL_SUPPORTED_FORMATS:
                search_string = self.format_separator + trial_format
                if lower_full_location.endswith(search_string):
                    location = location[0 : -len(search_string)]
                    explicit_format = trial_format
                    break

        return (location, explicit_format)

    def fetch_internal(
        self,
        location: str,
        config: dict[str, Any],
        env: dict[str, str],
        explicit_format: Optional[str],
    ) -> tuple[str, Optional[str], Optional[Any]]:
        raise NotImplementedError()

    def guess_format_from_location(self, location: str) -> Optional[str]:
        if ".env." in location:
            return FORMAT_DOTENV

        location, _suffix = strip_after(location, "#")
        last_dot_index = location.rfind(".")
        if last_dot_index < 0:
            _logger.info(
                f"No file extension found in location '{location}', can't guess format"
            )
            return None

        extension = location[last_dot_index + 1 :].lower()

        return EXTENSION_TO_FORMAT.get(extension)

    def guess_format_from_mime_type(self, mime_type: str) -> Optional[str]:
        mime_type, _content_encoding = strip_after(mime_type, ";")
        mime_type = mime_type.strip().lower()
        return MIME_TYPE_TO_FORMAT.get(mime_type)


class PlainSecretProvider(SecretProvider):
    def __init__(self):
        super().__init__(name=SECRET_PROVIDER_PLAIN, should_cache=True, top_level=True)

    def fetch_internal(
        self,
        location: str,
        config: dict[str, Any],
        env: dict[str, str],
        explicit_format: Optional[str],
    ) -> tuple[str, Optional[str], Optional[Any]]:
        return (location, explicit_format, None)


class EnvSecretProvider(SecretProvider):
    def __init__(self) -> None:
        super().__init__(name=SECRET_PROVIDER_ENV, should_cache=False, top_level=False)

    def fetch_internal(
        self,
        location: str,
        config: dict[str, Any],
        env: dict[str, str],
        explicit_format: Optional[str],
    ) -> tuple[str, Optional[str], Optional[Any]]:
        string_value = env.get(location)

        if string_value is None:
            raise ValueNotFoundException(location)

        return (string_value, explicit_format, None)


class ConfigSecretProvider(SecretProvider):
    def __init__(self, log_secrets: bool = False):
        super().__init__(
            name=SECRET_PROVIDER_CONFIG, should_cache=False, top_level=False
        )
        self.log_secrets = log_secrets

    def fetch_internal(
        self,
        location: str,
        config: dict[str, Any],
        env: dict[str, str],
        explicit_format: Optional[str],
    ) -> tuple[str, Optional[str], Optional[Any]]:
        transformed = transform_value(
            parsed_value=config,
            string_value="<config>",
            transform_expr_str=JSON_PATH_TRANSFORM_PREFIX + location,
            log_secrets=self.log_secrets,
        )

        if transformed is None:
            raise ValueNotFoundException(location)

        return (json.dumps(transformed), FORMAT_JSON, transformed)


class FileSecretProvider(SecretProvider):
    def __init__(self):
        super().__init__(
            name=SECRET_PROVIDER_FILE,
            value_prefixes=[FILE_URL_PREFIX],
            should_cache=True,
        )

    def supports_value(self, value: str) -> bool:
        return len(value.strip()) > 0

    def fetch_internal(
        self,
        location: str,
        config: dict[str, Any],
        env: dict[str, str],
        explicit_format: Optional[str],
    ) -> tuple[str, Optional[str], Optional[Any]]:
        if location.startswith(FILE_URL_PREFIX):
            location = location[FILE_URL_PREFIX_LENGTH:]

        # TODO: allow different encodings
        with open(location, "r", encoding="utf-8") as f:
            return (f.read(), None, None)


class AwsSecretProvider(SecretProvider):
    def __init__(self, name: str, value_prefixes: Optional[list[str]]):
        super().__init__(name=name, value_prefixes=value_prefixes, should_cache=True)

    def make_client_config(self) -> Any:
        from botocore.config import Config

        return Config(retries={"max_attempts": 5, "mode": "standard"})


class AwsSecretsManagerSecretProvider(AwsSecretProvider):
    def __init__(self, aws_region_name: str):
        super().__init__(
            name=SECRET_PROVIDER_AWS_SECRETS_MANAGER,
            value_prefixes=[AWS_SECRETS_MANAGER_PREFIX],
        )
        self.aws_secrets_manager_client = None
        self.aws_secrets_manager_client_create_attempted_at: Optional[float] = None
        self.aws_region_name = aws_region_name

        if not aws_region_name:
            _logger.debug("Cannot determine AWS region to use with Secrets Manager")

    def fetch_internal(
        self,
        location: str,
        config: dict[str, Any],
        env: dict[str, str],
        explicit_format: Optional[str],
    ) -> tuple[str, Optional[str], Optional[Any]]:
        if not self.aws_region_name:
            raise RuntimeError(
                "Can't use AWS Secrets Manager without AWS region setting"
            )

        client = self.get_or_create_aws_secrets_manager_client()

        if client is None:
            raise RuntimeError("Can't create AWS Secrets Manager client")

        try:
            _logger.info(f"Looking up Secrets Manager secret '{location}'")
            response = client.get_secret_value(SecretId=location)

            # Binary secrets are left Base-64 encoded
            string_value = response.get("SecretString") or response["SecretBinary"]

            return (string_value, explicit_format, None)
        finally:
            client.close()

    def get_or_create_aws_secrets_manager_client(self) -> Optional[Any]:
        if not self.aws_secrets_manager_client:
            if self.aws_secrets_manager_client_create_attempted_at:
                return None

            self.aws_secrets_manager_client_create_attempted_at = time.time()

            try:
                import boto3
            except ImportError as import_error:
                _logger.exception(
                    "boto3 is not available to import, please install it in your python environment"
                )
                raise import_error

            self.aws_secrets_manager_client = boto3.client(
                service_name="secretsmanager",
                region_name=self.aws_region_name,
                config=self.make_client_config(),
            )

        return self.aws_secrets_manager_client


class AwsSystemsManagerParameterStoreSecretProvider(AwsSecretProvider):
    def __init__(self, aws_region_name: str) -> None:
        super().__init__(
            name=SECRET_PROVIDER_AWS_SYSTEMS_MANAGER_PARAMETER_STORE,
            value_prefixes=[
                AWS_SYSTEMS_MANAGER_PARAMETER_STORE_PREFIX,
                AWS_SYSTEMS_MANAGER_PARAMETER_STORE_ARN_PREFIX,
            ],
        )
        self.aws_region_name = aws_region_name

    def fetch_internal(
        self,
        location: str,
        config: dict[str, Any],
        env: dict[str, str],
        explicit_format: Optional[str],
    ) -> tuple[str, Optional[str], Optional[Any]]:
        client: Optional[Any] = None
        try:
            region_name = self.aws_region_name
            if location.startswith(AWS_SYSTEMS_MANAGER_PARAMETER_STORE_ARN_PREFIX):
                s = location[len(AWS_SYSTEMS_MANAGER_PARAMETER_STORE_ARN_PREFIX) :]
                parts = s.split(":")
                region_name = parts[0]
                # Use the ARN as is
            elif location.startswith(AWS_SYSTEMS_MANAGER_PARAMETER_STORE_PREFIX):
                location = location[len(AWS_SYSTEMS_MANAGER_PARAMETER_STORE_PREFIX) :]
                region_name = self.aws_region_name

            client = self.get_or_create_aws_ssm_client(region_name=region_name)
            if client is None:
                raise RuntimeError("Can't create AWS SSM client")

            _logger.info(f"Looking up SSM parameter '{location}'")
            response = client.get_parameter(Name=location, WithDecryption=True)

            parameter_obj = response["Parameter"]
            raw_value = parameter_obj["Value"]
            value_type = parameter_obj["Type"]

            if value_type == "StringList":
                parsed_value = raw_value.split(",")
                string_value = json.dumps(parsed_value)
                return (string_value, FORMAT_JSON, parsed_value)
            else:
                return (raw_value, explicit_format, None)
        finally:
            if client:
                client.close()

    def get_or_create_aws_ssm_client(self, region_name: Optional[str]) -> Optional[Any]:
        try:
            import boto3
        except ImportError as import_error:
            _logger.exception(
                "boto3 is not available to import, please install it in your python environment"
            )
            raise import_error

        return boto3.client(
            service_name="ssm",
            region_name=(region_name or self.aws_region_name),
            config=self.make_client_config(),
        )


class AwsAppConfigSecretProvider(AwsSecretProvider):
    def __init__(self, aws_region_name: str) -> None:
        super().__init__(
            name=SECRET_PROVIDER_AWS_APP_CONFIG,
            value_prefixes=[AWS_APP_CONFIG_PREFIX],
        )
        self.aws_region_name = aws_region_name
        self.aws_app_config_client: Optional[Any] = None
        self.aws_app_config_client_create_attempted_at: Optional[float] = None

    def fetch_internal(
        self,
        location: str,
        config: dict[str, Any],
        env: dict[str, str],
        explicit_format: Optional[str],
    ) -> tuple[str, Optional[str], Optional[Any]]:
        client = self.get_or_create_aws_app_config_client()

        if client is None:
            raise RuntimeError("Can't create AWS AppConfig client")

        try:
            if location.startswith(AWS_APP_CONFIG_PREFIX):
                location = location[len(AWS_APP_CONFIG_PREFIX) :]

            ids = location.split("/")

            if len(ids) != 3:
                raise ValueError(f"Invalid AWS AppConfig location '{location}'")

            _logger.info(
                f"Starting AWS AppConfig configuration session for '{location}'"
            )

            app_id, env_id, config_profile_id = ids
            response = client.start_configuration_session(
                ApplicationIdentifier=app_id,
                EnvironmentIdentifier=env_id,
                ConfigurationProfileIdentifier=config_profile_id,
            )

            token = response.get("InitialConfigurationToken")

            if token is None:
                raise RuntimeError("Failed to get initial configuration token")

            response = client.get_latest_configuration(ConfigurationToken=token)

            format: Optional[str] = None
            content_type = response.get("ContentType")
            if content_type:
                format = MIME_TYPE_TO_FORMAT.get(content_type)

            version = response.get("VersionLabel")

            _logger.info(
                f"Got version '{version}' and content type '{content_type}' for AWS AppConfig location '{location}'"
            )

            stream = response["Configuration"]

            with stream:
                raw_value = stream.read().decode("UTF-8")
                _logger.debug(f"Got configuration value {raw_value} for '{location}'")
                return (raw_value, explicit_format or format, None)
        finally:
            client.close()

    def get_or_create_aws_app_config_client(self) -> Optional[Any]:
        if not self.aws_app_config_client:
            if self.aws_app_config_client_create_attempted_at:
                return None

            self.aws_app_config_client_create_attempted_at = time.time()

            try:
                import boto3
            except ImportError as import_error:
                _logger.exception(
                    "boto3 is not available to import, please install it in your python environment"
                )
                raise import_error

            self.aws_app_config_client = boto3.client(
                service_name="appconfigdata",
                region_name=self.aws_region_name,
                config=self.make_client_config(),
            )

        return self.aws_app_config_client


@dataclass
class AwsS3Data:
    body: str
    content_length: int
    content_encoding: Optional[str]
    content_language: Optional[str]
    content_type: Optional[str]
    expires_at: Optional[float]


class AwsS3SecretProvider(AwsSecretProvider):
    def __init__(self) -> None:
        super().__init__(name=SECRET_PROVIDER_AWS_S3, value_prefixes=[AWS_S3_PREFIX])
        self.aws_s3_resource = None
        self.aws_s3_resource_create_attempted_at: Optional[float] = None

    def fetch_internal(
        self,
        location: str,
        config: dict[str, Any],
        env: dict[str, str],
        explicit_format: Optional[str],
    ) -> tuple[str, Optional[str], Optional[Any]]:
        s3_data = self.fetch_aws_s3_data(location)

        format: Optional[str] = None
        if s3_data.content_type:
            format = self.guess_format_from_mime_type(s3_data.content_type)

        return (s3_data.body, format, None)

    def get_or_create_aws_s3_resource(self):
        if not self.aws_s3_resource:
            if self.aws_s3_resource_create_attempted_at:
                return None

            self.aws_s3_resource_create_attempted_at = time.time()

            try:
                import boto3
            except ImportError as import_error:
                _logger.exception(
                    "boto3 is not available to import, please install it in your python environment"
                )
                raise import_error

            self.aws_s3_resource = boto3.resource(
                "s3", config=self.make_client_config()
            )

        return self.aws_s3_resource

    def fetch_aws_s3_data(self, s3_arn: str) -> AwsS3Data:
        aws_s3_resource = self.get_or_create_aws_s3_resource()

        if aws_s3_resource is None:
            raise RuntimeError("Can't create AWS S3 resource")

        _logger.info(f"Fetching S3 object with ARN '{s3_arn}'")

        parts = s3_arn.split(":::")
        bucket_and_key = parts[1]
        slash_index = bucket_and_key.index("/")
        bucket = bucket_and_key[0:slash_index]
        key = bucket_and_key[slash_index + 1 :]

        key, version = strip_after(key, AWS_S3_VERSION_SEPARATOR)
        if version:
            obj = aws_s3_resource.ObjectVersion(bucket, key, version)
        else:
            obj = aws_s3_resource.Object(bucket, key)

        content_encoding = obj.content_encoding

        # Get rid of ",aws-chunked" suffix
        if content_encoding and (content_encoding.find(",") >= 0):
            content_encoding, _ = content_encoding.split(",")

        _logger.info(
            f"Got content encoding of '{content_encoding}' for S3 ARN {s3_arn}"
        )

        response = obj.get()
        data = response["Body"].read().decode(content_encoding or "utf-8")

        return AwsS3Data(
            body=data,
            content_length=obj.content_length,
            content_encoding=content_encoding,
            content_language=obj.content_language,
            content_type=obj.content_type,
            # TODO: parse obj.expires_at
            expires_at=None,
        )


@dataclass
class CachedValueEntry:
    string_value: Optional[str]
    parsed_value: Any
    fetched_at: float
    is_value_dict: Optional[bool]

    def is_stale(self, ttl_seconds: Optional[int]) -> bool:
        if ttl_seconds is None:
            return False

        return (time.time() - self.fetched_at) > ttl_seconds


@dataclass
class ResolutionResult:
    resolved_value: Any
    resolved_var_names: list[str]
    failed_var_names: list[str]
    unresolved_var_names: list[str]


class ConfigResolver:
    def __init__(
        self,
        params: ConfigResolverParams,
        runtime_metadata: Optional[RuntimeMetadata] = None,
        env_override: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.params = params
        self.runtime_metadata = runtime_metadata

        # Dictionary from SECRET_PROVIDER_XXX constants to caches from lookup values
        # to resolved values and metadata
        self.secret_cache: dict[str, dict[str, CachedValueEntry]] = {}

        if env_override:
            self.env = dict(env_override)
        else:
            self.env = os.environ.copy()

        self.env_var_prefix_length = len(self.params.resolved_env_var_name_prefix)
        self.env_var_suffix_length = len(self.params.resolved_env_var_name_suffix)
        self.config_var_prefix_length = len(
            self.params.resolved_config_property_name_prefix
        )
        self.config_var_suffix_length = len(
            self.params.resolved_config_property_name_suffix
        )

        self.merge_impl: Optional[Any] = None
        self.mergedeep_strategy: Optional[Any] = None

        merge_strategy = DEFAULT_CONFIG_MERGE_STRATEGY

        if params.config_merge_strategy:
            merge_strategy = params.config_merge_strategy

        if merge_strategy == CONFIG_MERGE_STRATEGY_DEEP:
            self.merge_impl = best_effort_deep_merge
        elif merge_strategy not in NATIVE_CONFIG_MERGE_STRATEGIES:
            import mergedeep  # type: ignore

            self.merge_impl = mergedeep.merge
            self.mergedeep_strategy = getattr(mergedeep.Strategy, merge_strategy)

        self.plain_secret_provider = PlainSecretProvider()

        aws_region_name = self.compute_aws_region_name() or ""

        self.secret_providers = [
            self.plain_secret_provider,
            EnvSecretProvider(),
            ConfigSecretProvider(log_secrets=self.params.log_secrets),
            AwsSecretsManagerSecretProvider(aws_region_name=aws_region_name),
            AwsSystemsManagerParameterStoreSecretProvider(
                aws_region_name=aws_region_name
            ),
            AwsSystemsManagerParameterStoreSecretProvider(
                aws_region_name=aws_region_name
            ),
            AwsAppConfigSecretProvider(aws_region_name=aws_region_name),
            AwsS3SecretProvider(),
            FileSecretProvider(),
        ]

    def fetch_and_resolve_env(self) -> tuple[dict[str, str], list[str]]:
        env, failed_var_names, _c, _fcn = self.fetch_and_resolve_env_and_config(
            want_config=False
        )
        return (env, failed_var_names)

    def fetch_and_resolve_config(self) -> tuple[dict[str, Any], list[str]]:
        _e, _fen, config, failed_var_names = self.fetch_and_resolve_env_and_config(
            want_env=False
        )
        return (config, failed_var_names)

    def fetch_and_resolve_env_and_config(
        self, want_env=True, want_config=True
    ) -> tuple[dict[str, str], list[str], dict[str, Any], list[str]]:
        """
        Fetch the configuration and environment from the sources in
        config_locations and env_locations, respectively. Then merge
        the configuration from all configuration sources, and the environment
        from all environment sources. Resolve variables in the configuration
        and environment. Flatten the environment so that all values are strings.
        Populate the variable with the name config_var_name_for_env
        in the configuration with the JSON-serialized configuration in
        the environment. Populate the variable with the name
        config_var_name_for_env in the environment with the JSON-serialized
        configuration in the environment.
        """
        config, env = self.fetch_and_merge()

        if self.params.max_config_resolution_depth <= 0:
            _logger.debug("Not resolving variables, returning merged config")
            return (env, [], config, [])

        unresolved_env_var_names: list[str] = []
        failed_env_var_names: list[str] = []
        unresolved_config_var_names: list[str] = []
        failed_config_var_names: list[str] = []

        for epoch in range(2):
            for iteration in range(self.params.max_config_resolution_iterations):
                _logger.debug(
                    f"Starting resolution iteration {iteration} of epoch {epoch} ..."
                )

                env_result = self.resolve_value(
                    value=env, config=config, env=env, is_env=True
                )

                env = cast(dict[str, Any], env_result.resolved_value)
                failed_env_var_names = env_result.failed_var_names

                if self.params.fail_fast_config_resolution and (
                    len(failed_env_var_names) > 0
                ):
                    _logger.warning(
                        f"Failing fast after finding failed env vars: {failed_env_var_names}"
                    )
                    return (env, failed_env_var_names, config, failed_config_var_names)

                unresolved_env_var_names = env_result.unresolved_var_names

                config_result = self.resolve_value(
                    value=config, config=config, env=env, is_env=False
                )

                config = cast(dict[str, Any], config_result.resolved_value)
                failed_config_var_names = config_result.failed_var_names

                if self.params.fail_fast_config_resolution and (
                    len(failed_config_var_names) > 0
                ):
                    _logger.warning(
                        f"Failing fast after finding failed config vars: {failed_config_var_names}"
                    )
                    return (env, failed_env_var_names, config, failed_config_var_names)

                unresolved_config_var_names = config_result.unresolved_var_names

                if (len(unresolved_env_var_names) == 0) and (
                    len(unresolved_config_var_names) == 0
                ):
                    break

            if iteration >= self.params.max_config_resolution_iterations:
                raise RuntimeError(
                    "Resolution iteration count of {iteration} exceeds maximum allowed"
                )

            # If we want to prevent the environment from being overwritten,
            # re-merge the environment so it overrides everything, then
            # re-run resolution.
            if (epoch > 0) or self.params.overwrite_env_during_resolution:
                break
            else:
                env.update(self.env)

        final_env: dict[str, str] = {}
        if want_env or self.params.config_property_name_for_env:
            final_env = self.flatten_env(env)

        env_for_config = final_env

        if want_env and self.params.env_var_name_for_config:
            if want_config and self.params.config_property_name_for_env:
                env_for_config = final_env.copy()
            final_env[self.params.env_var_name_for_config] = json.dumps(config)

        if want_config and self.params.config_property_name_for_env:
            config[self.params.config_property_name_for_env] = env_for_config

        return (final_env, failed_env_var_names, config, failed_config_var_names)

    def flatten_env(self, env: dict[str, Any]) -> dict[str, str]:
        flattened = {}

        for name, value in env.items():
            flattened[name] = stringify_value(value)

        return flattened

    def fetch_and_merge(self) -> tuple[dict[str, Any], dict[str, Any]]:
        merged_config: dict[str, Any] = self.params.initial_config.copy()

        if self.params.initial_config:
            self.params.override_resolver_params_from_config(self.params.initial_config)

        merged_env: dict[str, Any] = {}

        for env_file_location in self.params.env_locations:
            env = self.fetch_config_from_location(
                env_file_location, default_format=FORMAT_DOTENV
            )

            if env:
                merged_env = self.merge(merged_env, env)

        # Merge the current environment last
        merged_env.update(self.env)

        # If the environment has changed, update the resolver parameters
        # so that config locations can be set by the updated environment.
        if self.params.env_locations:
            self.params.override_resolver_params_from_env(env=merged_env)

        for config_location in self.params.config_locations:
            config = self.fetch_config_from_location(
                config_location, default_format=FORMAT_JSON
            )

            if config:
                merged_config = self.merge(merged_config, config)

        return (merged_config, merged_env)

    def fetch_config_from_location(
        self, location: str, default_format: str
    ) -> dict[str, Any]:
        _name, value = self.resolve_var(
            name="",
            value=location,
            config={},
            env=self.env,
            top_level=True,
            default_format=default_format,
        )

        if issubclass(type(value), dict):
            return value

        raise RuntimeError(f"Configuration value at {location} is not a dict!")

    def resolve_value(
        self,
        value: Any,
        config: dict[str, Any],
        env: dict[str, Any],
        is_env: bool = True,
        path: str = "",
        depth: int = 0,
    ) -> ResolutionResult:
        value_type = type(value)

        resolved_var_names = []
        unresolved_var_names = []
        failed_var_names = []

        if issubclass(value_type, dict):
            dict_value = cast(dict[str, Any], value)
            return self.resolve_dict(
                dict_value=dict_value,
                config=config,
                env=env,
                is_env=is_env,
                path=path,
                depth=depth,
            )
        elif issubclass(value_type, list) and (
            depth < self.params.max_config_resolution_depth
        ):
            list_value = cast(list[Any], value)
            resolved_value = []
            for index, element in enumerate(list_value):
                inner_result = self.resolve_value(
                    value=element,
                    config=config,
                    env=env,
                    is_env=is_env,
                    path=path + f"[{index}]",
                    depth=depth + 1,
                )

                resolved_value.append(inner_result.resolved_value)
                resolved_var_names.extend(inner_result.resolved_var_names)
                unresolved_var_names.extend(inner_result.unresolved_var_names)
                failed_var_names.extend(inner_result.failed_var_names)
                value = resolved_value

                if self.params.fail_fast_config_resolution and (
                    len(inner_result.failed_var_names) > 0
                ):
                    return ResolutionResult(
                        resolved_value=resolved_value,
                        resolved_var_names=resolved_var_names,
                        unresolved_var_names=unresolved_var_names,
                        failed_var_names=failed_var_names,
                    )

        return ResolutionResult(
            resolved_value=value,
            resolved_var_names=resolved_var_names,
            failed_var_names=failed_var_names,
            unresolved_var_names=unresolved_var_names,
        )

    @staticmethod
    def qualify_path(path: str, var_name: str) -> str:
        return f"{path}.{var_name}" if path else var_name

    def resolve_dict(
        self,
        dict_value: dict[str, Any],
        config: dict[str, Any],
        env: dict[str, Any],
        is_env: bool = True,
        path: str = "",
        depth: int = 0,
    ) -> ResolutionResult:
        """
        Resolve configuration.
        """
        if depth == 0:
            target = "environment" if is_env else "configuration"
            _logger.debug(f"Starting secrets resolution of {target} ...")
        elif depth >= self.params.max_config_resolution_depth:
            _logger.info(f"Reached max depth of {depth}, stopping further resolution")
            return ResolutionResult(
                resolved_value=dict_value,
                resolved_var_names=[],
                failed_var_names=[],
                unresolved_var_names=[],
            )

        resolved_dict_value: dict[str, Any] = {}
        resolved_var_names: list[str] = []
        failed_var_names: list[str] = []
        unresolved_var_names: list[str] = []

        if is_env:
            var_prefix = self.params.resolved_env_var_name_prefix
            var_prefix_length = self.env_var_prefix_length
            var_suffix = self.params.resolved_env_var_name_suffix
            var_suffix_length = self.env_var_suffix_length
        else:
            var_prefix = self.params.resolved_config_property_name_prefix
            var_prefix_length = self.config_var_prefix_length
            var_suffix = self.params.resolved_config_property_name_suffix
            var_suffix_length = self.config_var_suffix_length

        for name, value in dict_value.items():
            var_name = name
            inner_path = ConfigResolver.qualify_path(path, var_name)
            if (
                name.startswith(var_prefix)
                and name.endswith(var_suffix)
                and isinstance(value, str)
            ):
                var_name = name[var_prefix_length:-var_suffix_length]
                inner_path = ConfigResolver.qualify_path(path, var_name)
                try:
                    _logger.debug(
                        f"Resolving key '{var_name}' with value '{value}' ..."
                    )
                    var_name, var_value = self.resolve_var(
                        name=var_name, value=value, config=config, env=env
                    )

                    if var_name is None:
                        _logger.info(
                            f"Skipping setting of environment variable '{var_name}'"
                        )
                        continue
                    else:
                        value = var_value
                        inner_path = ConfigResolver.qualify_path(path, var_name)
                        resolved_var_names.append(inner_path)
                except ValueNotFoundException:
                    _logger.info(f"Variable '{var_name}' is unresolved")
                    unresolved_var_names.append(inner_path)
                except Exception:
                    msg = f"Failed to resolve environment variable '{var_name}'"
                    if self.params.log_secrets:
                        msg += f" which had value '{value}'"
                    _logger.exception(msg)
                    failed_var_names.append(inner_path)

                    if self.params.fail_fast_config_resolution:
                        _logger.warning(f"Failing fast after {msg}")
                        return ResolutionResult(
                            resolved_value=resolved_dict_value,
                            resolved_var_names=resolved_var_names,
                            unresolved_var_names=unresolved_var_names,
                            failed_var_names=failed_var_names,
                        )

            inner_result = self.resolve_value(
                value=value,
                config=config,
                env=env,
                is_env=is_env,
                path=inner_path,
                depth=depth + 1,
            )

            resolved_value = inner_result.resolved_value
            if self.merge_impl and issubclass(type(resolved_value), dict):
                # Looking up in resolved_dict_value might fix some cases, but
                # is non-deterministic due to key ordering.
                old_value = dict_value.get(var_name)
                if (old_value is not None) and issubclass(type(old_value), dict):
                    resolved_value = self.merge(old_value.copy(), resolved_value)

            resolved_dict_value[var_name] = resolved_value
            resolved_var_names.extend(inner_result.resolved_var_names)
            unresolved_var_names.extend(inner_result.unresolved_var_names)
            failed_var_names.extend(inner_result.failed_var_names)

        return ResolutionResult(
            resolved_value=resolved_dict_value,
            resolved_var_names=resolved_var_names,
            failed_var_names=failed_var_names,
            unresolved_var_names=unresolved_var_names,
        )

    def merge(self, dest: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
        if self.merge_impl:
            if self.mergedeep_strategy:
                dest = self.merge_impl(dest, src, strategy=self.mergedeep_strategy)
            else:
                dest = self.merge_impl(dest, src)
        else:
            # Shallow merge
            dest.update(src)

        return dest

    def resolve_var(
        self,
        name: str,
        value: str,
        config: dict[str, Any],
        env: dict[str, str],
        top_level: bool = False,
        default_format: Optional[str] = None,
    ) -> tuple[str, Any]:
        var_name = name
        secret_provider: Optional[SecretProvider] = None

        for sp in self.secret_providers:
            if top_level and not sp.top_level:
                continue

            sp_name = sp.name

            # Legacy method of indicating the secret provider type was to prefix
            # the variable name.
            if name.startswith(sp_name + "_"):
                var_name = name[len(sp_name) + 1 :]
                secret_provider = sp
                break

            if sp.supports_value(value):
                secret_provider = sp
                break

        if secret_provider is None:
            if top_level:
                raise RuntimeError(
                    "No matching secret provider (file is supposed to be catch all)?!"
                )
            else:
                _logger.warning(
                    f"""
No secret provider found for name = '{name}', value = '{value}', \
defaulting to plain"""
                )

                secret_provider = self.plain_secret_provider

        sp_name = secret_provider.name

        msg = f"Found secret provider = '{sp_name}', name = '{name}'"
        if self.params.log_secrets:
            msg += f", '{value}'"

        _logger.debug(msg)

        value_to_lookup = value
        transform_expr_str: Optional[str] = None
        transform_separator = secret_provider.transform_separator
        if transform_separator:
            separator_index = value.find(transform_separator)
            if (separator_index > 0) and (separator_index < len(value) - 2):
                transform_expr_str = value_to_lookup[(separator_index + 1) :]
                value_to_lookup = value_to_lookup[0:separator_index]

        string_value: Optional[str] = None
        parsed_value: Optional[Any] = None
        cache: Optional[dict[str, CachedValueEntry]] = None
        cache_key = value_to_lookup
        is_value_config_dict: Optional[bool] = None

        if secret_provider.should_cache:
            cache = self.secret_cache.get(sp_name)

            cached_value_entry: Optional[CachedValueEntry] = None
            cache_key = secret_provider.cache_key_for_value(value_to_lookup)
            if cache is None:
                cache = {}
                self.secret_cache[sp_name] = cache
            else:
                cached_value_entry = cache.get(cache_key)

            if (cached_value_entry is None) or cached_value_entry.is_stale(
                self.params.config_ttl
            ):
                _logger.debug(
                    f"Secret cache miss for '{cache_key}' from value '{value_to_lookup}' with provider {sp_name}"
                )
            else:
                string_value = cached_value_entry.string_value
                parsed_value = cached_value_entry.parsed_value
                is_value_config_dict = cached_value_entry.is_value_dict
                # Don't re-install in cache
                cache = None

        if (string_value is None) and (parsed_value is None):
            string_value, format, parsed_value = secret_provider.fetch_data(
                location=value_to_lookup, config=config, env=env
            )

            if parsed_value is None:
                if format is None:
                    if default_format:
                        _logger.warning(
                            f"Can't guess format from location, defaulting to {default_format}"
                        )
                        format = default_format

                if format:
                    config_data = parse_data_string(
                        data_string=string_value, format=format
                    )
                    is_value_config_dict = issubclass(type(config_data), dict)
                    parsed_value = coalesce(config_data, string_value)

        if self.params.log_secrets:
            _logger.debug(
                f"value_to_lookup = '{value_to_lookup}', resolved value = '{string_value}'"
            )

        if cache is not None:
            cache[cache_key] = CachedValueEntry(
                string_value=string_value,
                parsed_value=parsed_value,
                fetched_at=time.time(),
                is_value_dict=is_value_config_dict,
            )

        if not transform_expr_str:
            return (var_name, coalesce(parsed_value, string_value))

        if parsed_value is None:
            if string_value is None:
                raise RuntimeError("Cannot parse missing string value for transform")

            parsed_value = json.loads(string_value)

        resolved_value = transform_value(
            parsed_value=parsed_value,
            string_value=string_value,
            transform_expr_str=transform_expr_str,
            log_secrets=self.params.log_secrets,
        )

        if self.params.log_secrets:
            _logger.debug(
                f"resolved var_name = '{var_name}', resolved_value = '{resolved_value}'"
            )

        return (var_name, resolved_value)

    def compute_aws_region_name(self) -> Optional[str]:
        region_name = (
            self.env.get("PROC_WRAPPER_SECRETS_AWS_REGION")
            or self.env.get("AWS_REGION")
            or self.env.get("AWS_DEFAULT_REGION")
        )

        if not region_name:
            if self.runtime_metadata and ("aws" in self.runtime_metadata.derived):
                region_name = self.runtime_metadata.derived["aws"].get("region")

        return region_name
