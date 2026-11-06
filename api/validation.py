"""Reusable normalization and validation for attacker-controlled API values.

These helpers validate shape and business bounds only. They deliberately do
not remove HTML-like text: renderers remain responsible for context-specific
output encoding.
"""

import math
import re

from api.i18n import N_, t


def _field_error(message, field, **values):
    """`message` in the reader's language, with the field label translated too.

    Field labels reach these helpers as English text marked with `N_()` (or a
    bare JSON key such as "name", which has no translation and stays as is).
    """
    return t(message, field=t(field), **values)


class ValidationError(ValueError):
    """A rule the caller broke and can fix, with a message safe to show them.

    Everything else that escapes a handler becomes a generic 500 with a request
    id, which is right for bugs but wrong for "this value must be 1-5": the
    admin was told "unexpected server error" and had no way to know what to
    change. api/router.py maps this one exception to 400 and passes its message
    through, so only deliberately written text ever reaches the client.

    Subclasses ValueError so existing `except ValueError` call sites and tests
    keep working unchanged.
    """


_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)
_HOSTNAME_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def parse_required_string(value, field: str, max_length: int, min_length: int = 1) -> str:
    """Return a trimmed, bounded non-empty string or raise ``ValueError``."""
    if not isinstance(value, str):
        raise ValueError(_field_error("Field '{field}' must be text.", field))
    normalized = value.strip()
    if len(normalized) < min_length:
        raise ValueError(_field_error("Field '{field}' is required.", field))
    if len(normalized) > max_length:
        raise ValueError(_field_error("Field '{field}' can have at most {max_length} characters.", field, max_length=max_length))
    return normalized


def parse_optional_string(value, field: str, max_length: int) -> str:
    """Return a trimmed bounded string, treating ``None`` as an empty value."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(_field_error("Field '{field}' must be text.", field))
    normalized = value.strip()
    if len(normalized) > max_length:
        raise ValueError(_field_error("Field '{field}' can have at most {max_length} characters.", field, max_length=max_length))
    return normalized


def parse_password(
    value,
    field: str = N_("Password"),
    min_length: int = 8,
    max_length: int = 72,
) -> str:
    """Return a bounded password without trimming or other normalization.

    72 bytes is bcrypt's own limit; it refuses anything longer.
    """
    if not isinstance(value, str):
        raise ValueError(_field_error("Field '{field}' must be text.", field))
    if len(value) < min_length:
        raise ValueError(_field_error("Field '{field}' must have at least {min_length} characters.", field, min_length=min_length))
    if len(value) > max_length or len(value.encode("utf-8")) > max_length:
        raise ValueError(_field_error("Field '{field}' can have at most {max_length} characters.", field, max_length=max_length))
    return value


def parse_email(value, field: str = N_("E-mail"), max_length: int = 254) -> str:
    """Return a normalized email address with a conservative valid shape."""
    normalized = parse_required_string(value, field, max_length=max_length)
    if not _EMAIL_RE.fullmatch(normalized) or ".." in normalized:
        raise ValueError(_field_error("Field '{field}' must contain a valid e-mail address.", field))
    return normalized


def parse_integer_id(value, field: str, maximum: int = None) -> int:
    """Return a positive integer ID, accepting digit strings from query input."""
    if isinstance(value, bool):
        raise ValueError(_field_error("Field '{field}' must be a positive integer id.", field))
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized.isdigit():
            raise ValueError(_field_error("Field '{field}' must be a positive integer id.", field))
        value = int(normalized)
    if not isinstance(value, int) or value < 1:
        raise ValueError(_field_error("Field '{field}' must be a positive integer id.", field))
    if maximum is not None and value > maximum:
        raise ValueError(_field_error("Field '{field}' is above the allowed range.", field))
    return value


def parse_integer_id_list(value, field: str, maximum_items: int = 500) -> list:
    """Return a non-empty, bounded list of unique positive IDs in input order."""
    if not isinstance(value, list) or not value or len(value) > maximum_items:
        raise ValueError(_field_error(
            "Field '{field}' must be a non-empty list of at most {maximum_items} ids.",
            field, maximum_items=maximum_items,
        ))
    result = []
    seen = set()
    for item in value:
        parsed = parse_integer_id(item, field)
        if parsed not in seen:
            seen.add(parsed)
            result.append(parsed)
    return result


def parse_non_negative_integer(value, field: str, maximum: int = None) -> int:
    """Return a non-negative integer, accepting digit strings from JSON/query input."""
    if isinstance(value, bool):
        raise ValueError(_field_error("Field '{field}' must be a non-negative integer.", field))
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized.isdigit():
            raise ValueError(_field_error("Field '{field}' must be a non-negative integer.", field))
        value = int(normalized)
    if not isinstance(value, int) or value < 0:
        raise ValueError(_field_error("Field '{field}' must be a non-negative integer.", field))
    if maximum is not None and value > maximum:
        raise ValueError(_field_error("Field '{field}' is above the allowed range.", field))
    return value


def parse_boolean_flag(value, field: str) -> int:
    """Return ``0`` or ``1`` without accepting truthy string/object coercions."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    raise ValueError(_field_error("Field '{field}' must be a boolean.", field))


def parse_color(value, field: str = N_("Colour")) -> str:
    """Return a normalized six-digit hexadecimal color."""
    normalized = parse_required_string(value, field, max_length=7)
    if not _HEX_COLOR_RE.fullmatch(normalized):
        raise ValueError(_field_error("Field '{field}' must be a colour in the #RRGGBB format.", field))
    return normalized


def parse_enum(value, field: str, allowed) -> str:
    """Return a trimmed string present in the code-owned ``allowed`` values."""
    if not isinstance(value, str):
        raise ValueError(_field_error("Field '{field}' has an invalid value.", field))
    normalized = value.strip()
    if normalized not in allowed:
        raise ValueError(_field_error("Field '{field}' has an invalid value: {value}", field, value=normalized))
    return normalized


def parse_finite_number(
    value,
    field: str,
    minimum: float = None,
    maximum: float = None,
) -> float:
    """Return a finite float within optional inclusive business bounds."""
    if isinstance(value, bool) or isinstance(value, (list, dict, tuple)) or value is None:
        raise ValueError(_field_error("Field '{field}' must be a number.", field))
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(_field_error("Field '{field}' must be a number.", field))
    if not math.isfinite(parsed):
        raise ValueError(_field_error("Field '{field}' must be a finite number.", field))
    if minimum is not None and parsed < minimum:
        raise ValueError(_field_error("Field '{field}' is below the allowed range.", field))
    if maximum is not None and parsed > maximum:
        raise ValueError(_field_error("Field '{field}' is above the allowed range.", field))
    return parsed


def parse_nip(value, field: str = "NIP") -> str:
    """Return a normalized, checksum-valid ten-digit Polish NIP.

    Accepts an optional leading ``PL`` prefix (either letter case) and any
    spaces/hyphens used as visual separators; both are stripped before the
    checksum is verified. Returns the bare ten-digit string.
    """
    if not isinstance(value, str):
        raise ValueError(_field_error("Field '{field}' must be text.", field))
    normalized = value.strip().replace(" ", "").replace("-", "")
    if normalized[:2].upper() == "PL":
        normalized = normalized[2:]
    if len(normalized) != 10 or not normalized.isdigit():
        raise ValueError(_field_error("Field '{field}' must consist of 10 digits.", field))
    digits = [int(d) for d in normalized]
    checksum = sum(w * d for w, d in zip(_NIP_WEIGHTS, digits)) % 11
    if checksum == 10 or checksum != digits[9]:
        raise ValueError(_field_error("Field '{field}' has an invalid checksum.", field))
    return normalized


def parse_website(value, field: str = N_("Website")) -> str:
    """Return a normalized bare hostname (no scheme, no ``www.``, no path).

    Strips a leading ``http://``/``https://``, truncates at the first
    ``/``, ``?`` or ``#``, drops a leading ``www.``, and requires a
    dot-separated hostname shape bounded at 253 characters.
    """
    if not isinstance(value, str):
        raise ValueError(_field_error("Field '{field}' must be text.", field))
    normalized = value.strip().lower()
    if normalized.startswith("http://"):
        normalized = normalized[len("http://"):]
    elif normalized.startswith("https://"):
        normalized = normalized[len("https://"):]
    for sep in ("/", "?", "#"):
        idx = normalized.find(sep)
        if idx != -1:
            normalized = normalized[:idx]
    if normalized.startswith("www."):
        normalized = normalized[len("www."):]
    if len(normalized) > 253 or not _HOSTNAME_RE.fullmatch(normalized):
        raise ValueError(_field_error("Field '{field}' must be a valid domain.", field))
    return normalized


def parse_allowed_fields(data, allowed, field: str = N_("Data")) -> dict:
    """Return a shallow copy after rejecting non-object input and unknown keys."""
    if not isinstance(data, dict):
        raise ValueError(_field_error("Field '{field}' must be an object.", field))
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ValueError(_field_error("Field '{field}' contains fields that are not allowed: {fields}", field, fields=', '.join(unknown)))
    return dict(data)
