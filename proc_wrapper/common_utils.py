import logging
from typing import Any, Dict, Optional, Tuple

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

    return trimmed.upper() == "TRUE"


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


def strip_after(s: str, partial_suffix: str) -> Tuple[str, Optional[str]]:
    index = s.find(partial_suffix)
    if index >= 0:
        return (s[0:index], s[index + len(partial_suffix) :])

    return (s, None)


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
    dest: Optional[Dict[str, Any]], src: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    if src is not None:
        dest = dest or {}
        try:
            return deepmerge_with_lists_pair(dest, src)
        except ImportError:
            dest.update(src)

    return dest
