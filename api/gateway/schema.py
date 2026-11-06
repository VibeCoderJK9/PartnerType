"""One argument validator for every gateway surface.

The MCP `inputSchema` a model reads and the query parameters /api/v1 accepts
come from the SAME schema in `agent_tools.GATEWAY_TOOL_REGISTRY`, and both are
checked here, so what the documentation promises and what the server accepts
cannot drift apart.

Only the JSON-schema subset the registry uses is supported: an object with
`properties`, `required` and `additionalProperties: false`; property types
`string` (maxLength, pattern, enum), `integer` and `number` (minimum,
maximum) and `boolean`. Anything outside that subset in a schema is a
programming error, not a caller error, and raises TypeError.

Messages are English: they are read by models and scripts, not by people in
the app.
"""
import math
import re

_INT_TEXT = re.compile(r"-?[0-9]{1,15}")
_NUMBER_TEXT = re.compile(r"-?[0-9]{1,15}(\.[0-9]{1,15})?")
_TRUE_TEXT = ("1", "true")
_FALSE_TEXT = ("0", "false")


class ArgumentError(ValueError):
    """The caller's arguments do not satisfy the tool's input schema."""


def _check_string(name, spec, value):
    if not isinstance(value, str):
        raise ArgumentError(f"{name} must be a string")
    max_length = spec.get("maxLength")
    if max_length is not None and len(value) > max_length:
        raise ArgumentError(f"{name} must be at most {max_length} characters")
    pattern = spec.get("pattern")
    if pattern is not None and not re.search(pattern, value):
        raise ArgumentError(f"{name} has an invalid format")
    return value


def _check_range(name, spec, value):
    minimum = spec.get("minimum")
    maximum = spec.get("maximum")
    if minimum is not None and value < minimum:
        raise ArgumentError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ArgumentError(f"{name} must be <= {maximum}")
    return value


def _check_integer(name, spec, value, from_query):
    if from_query and isinstance(value, str):
        if not _INT_TEXT.fullmatch(value):
            raise ArgumentError(f"{name} must be an integer")
        value = int(value)
    # bool is a subclass of int in Python; JSON `true` is not an integer.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArgumentError(f"{name} must be an integer")
    return _check_range(name, spec, value)


def _check_number(name, spec, value, from_query):
    if from_query and isinstance(value, str):
        if not _NUMBER_TEXT.fullmatch(value):
            raise ArgumentError(f"{name} must be a number")
        value = float(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArgumentError(f"{name} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ArgumentError(f"{name} must be a finite number")
    return _check_range(name, spec, value)


def _check_boolean(name, value, from_query):
    if from_query and isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_TEXT:
            return True
        if text in _FALSE_TEXT:
            return False
        raise ArgumentError(f"{name} must be true or false")
    if not isinstance(value, bool):
        raise ArgumentError(f"{name} must be a boolean")
    return value


def _check_value(name, spec, value, from_query):
    kind = spec.get("type")
    if kind == "string":
        value = _check_string(name, spec, value)
    elif kind == "integer":
        value = _check_integer(name, spec, value, from_query)
    elif kind == "number":
        value = _check_number(name, spec, value, from_query)
    elif kind == "boolean":
        value = _check_boolean(name, value, from_query)
    else:
        raise TypeError(f"unsupported schema type for {name}: {kind!r}")
    if "enum" in spec and value not in spec["enum"]:
        raise ArgumentError(f"{name} must be one of: " + ", ".join(map(str, spec["enum"])))
    return value


def validate_arguments(schema, args, *, from_query=False) -> dict:
    """Return the cleaned arguments for `schema`, or raise ArgumentError.

    `from_query=True` is for /api/v1 query strings, where every value arrives
    as text: integers, numbers and booleans ("1"/"0"/"true"/"false") are
    coerced. Without it the types must already be right (MCP JSON arguments).
    An optional argument sent as null is treated as absent -- models do send
    explicit nulls for "no filter", and that is not a different request.
    """
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ArgumentError("arguments must be an object")
    if schema.get("type", "object") != "object":
        raise TypeError("only object schemas are supported")
    properties = schema.get("properties", {})
    required = set(schema.get("required", ()))

    if schema.get("additionalProperties") is False:
        unknown = sorted(str(key) for key in args if key not in properties)
        if unknown:
            raise ArgumentError("unknown argument: " + ", ".join(unknown))

    clean = {}
    for name, spec in properties.items():
        if name not in args or (args[name] is None and name not in required):
            continue
        clean[name] = _check_value(name, spec, args[name], from_query)

    missing = sorted(required - set(clean))
    if missing:
        raise ArgumentError("missing required argument: " + ", ".join(missing))
    return clean
