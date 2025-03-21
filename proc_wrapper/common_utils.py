import json
import logging
from io import StringIO
from typing import Any, Optional

from .common_constants import FORMAT_DOTENV, FORMAT_JSON, FORMAT_TEXT, FORMAT_YAML

_logger = logging.getLogger(__name__)


# From glglgl on
# https://stackoverflow.com/questions/4978738/is-there-a-python-equivalent-of-the-c-sharp-null-coalescing-operator
def coalesce(*arg):
    return next((a for a in arg if a is not None), None)


def string_to_bool(
    s: Optional[str], default_value: Optional[bool] = None
) -> Optional[bool]:
    if s is None:
        return default_value

    trimmed = s.strip()
    if trimmed == "":
        return default_value

    return (trimmed == "1") or (trimmed.upper() == "TRUE")


def string_to_int(
    s: Optional[Any],
    default_value: Optional[int] = None,
    negative_value: Optional[int] = None,
) -> Optional[int]:
    if s is None:
        return default_value
    else:
        trimmed = str(s).strip()

        if trimmed == "":
            return default_value

        x = int(trimmed)

        if x < 0:
            return negative_value

        return x


def encode_int(x: Optional[int], empty_value: Optional[int] = None) -> Optional[int]:
    if x is None:
        return empty_value
    else:
        return x


def string_to_float(
    s: Optional[Any],
    default_value: Optional[float] = None,
    negative_value: Optional[float] = None,
) -> Optional[float]:
    if s is None:
        return default_value
    else:
        trimmed = str(s).strip()

        if trimmed == "":
            return default_value

        x = float(trimmed)

        if x < 0.0:
            return negative_value

        return x


def stringify_value(value: Any) -> str:
    string_value = ""

    if value is None:
        string_value = ""
    elif isinstance(value, bool):
        # Boolean values get transformed to environment values TRUE or FALSE
        string_value = str(value).upper()
    elif isinstance(value, (dict, list)):
        # Collections get serialized as JSON
        string_value = json.dumps(value)
    else:
        string_value = str(value)

    return string_value


def strip_after(s: str, partial_suffix: str) -> tuple[str, Optional[str]]:
    index = s.find(partial_suffix)
    if index >= 0:
        return (s[0:index], s[index + len(partial_suffix) :])

    return (s, None)


def truncate(s: str, max_length: int) -> str:
    return (s[: (max_length - 3)] + "...") if len(s) > max_length else s


def safe_get(
    obj: Any, prop_name: str, default_value: Optional[Any] = None
) -> Optional[Any]:
    if hasattr(obj, prop_name):
        return getattr(obj, prop_name)

    return default_value


def deepmerge_with_lists_pair(dest: Any, src: Any) -> Any:
    """
    Merge deeply, returning the merged value.
    dest or any collections inside dest might be modified in-place.
    This only handles dict, list, strings, and primtives, enough for JSON object
    merging.
    """
    if isinstance(dest, dict):
        if isinstance(src, dict):
            for k, v in src.items():
                if k in dest:
                    dest[k] = deepmerge_with_lists_pair(dest[k], v)
                else:
                    dest[k] = v

            return dest

        _logger.warning(f"Attempt to merge dict {dest} with non-dict {src}")
        return src

    if isinstance(dest, list):
        if isinstance(src, list):
            x_len = len(dest)
            y_len = len(src)
            for i, v in enumerate(dest):
                if i < y_len:
                    dest[i] = deepmerge_with_lists_pair(dest[i], src[i])
                else:
                    break

            i = x_len
            while i < y_len:
                dest.append(src[i])
                i += 1

            return dest
        else:
            _logger.warning(f"Attempt to merge iterable {dest} with non-iterable {src}")
            return src

    return src


def best_effort_deep_merge(
    dest: Optional[dict[str, Any]], src: Optional[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    if src is not None:
        dest = dest or {}
        try:
            return deepmerge_with_lists_pair(dest, src)
        except ImportError:
            dest.update(src)

    return dest


def parse_dot_env(data: str) -> dict[str, Any]:
    from dotenv import dotenv_values

    return dotenv_values(stream=StringIO(data))


def parse_json(data: str) -> Optional[Any]:
    return json.loads(data)


def parse_yaml(data: str) -> dict[str, Any]:
    from yaml import safe_load

    return safe_load(data)


def parse_data_string(data_string: str, format: Optional[str] = None) -> Optional[Any]:
    if (not format) or (format == FORMAT_TEXT):
        return data_string

    if format == FORMAT_JSON:
        return parse_json(data_string)

    if format == FORMAT_YAML:
        return parse_yaml(data_string)

    if format == FORMAT_DOTENV:
        return parse_dot_env(data_string)

    return None


def parse_data_file(
    filename: str, format: Optional[str], encoding: Optional[str] = None
) -> Optional[Any]:
    encoding = encoding or "utf-8"

    if format == FORMAT_JSON:
        with open(filename, "r", encoding=encoding) as f:
            return json.load(f)
    elif format == FORMAT_YAML:
        from yaml import safe_load

        with open(filename, "r", encoding=encoding) as f:
            return safe_load(f)
    elif format == FORMAT_DOTENV:
        from dotenv import dotenv_values

        return dotenv_values(dotenv_path=filename, encoding=encoding)
    elif (not format) or (format == FORMAT_TEXT):
        with open(filename, "r", encoding=encoding) as f:
            return f.read()
    else:
        raise ValueError(f"Unsupported input value {format=}")


def write_data_to_file(
    filename: str,
    data: Any,
    format: Optional[str] = None,
    encoding: Optional[str] = None,
) -> None:
    """
    Write a data to a file in the specified format.
    """
    if (format == FORMAT_JSON) or ((not format) and not isinstance(data, str)):
        with open(filename, "w", encoding=encoding) as f:
            json.dump(data, f)
    elif format == FORMAT_DOTENV:
        from dotenv import set_key

        for k, v in data.items():
            set_key(filename, k, stringify_value(v))
    elif format == FORMAT_YAML:
        from yaml import dump

        with open(filename, "w", encoding=encoding) as f:
            dump(data, f)
    elif (not format) or (format == FORMAT_TEXT):
        with open(filename, "w") as f:
            f.write(str(data))
    else:
        raise ValueError(f"write_data_to_file(): Unsupported format '{format}'")
