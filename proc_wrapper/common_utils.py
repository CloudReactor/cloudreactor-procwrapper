from typing import Any, Optional, Tuple


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
