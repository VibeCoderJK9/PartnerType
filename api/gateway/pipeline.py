"""The single seam every gateway call goes through.

Both surfaces (/api/v1 and /mcp) run an authenticated operation as
`guarded(principal, surface, operation, fn)`. Here, and only here:

1. the per-connection minute and Warsaw-day limits are enforced
   (`limits.enforce`, 429 + Retry-After);
2. `fn()` runs and returns `(status, payload, records)`: the HTTP status, the
   JSON-ready payload and how many records went out. It may add a fourth
   value, the status to LOG when it differs from the one answered -- MCP
   answers an in-band JSON-RPC error (a forbidden tool, an internal error)
   with HTTP 200, but it must be logged and counted as the failure it is;
3. the records are added to the connection's daily usage row;
4. exactly one call-log row is written -- operation name, status, outcome,
   record count, duration. Never arguments, bodies or tokens.

An MCP notification needs no answer and writes no call-log row, but it is
still counted against the same limits through `enforce_unlogged`.

A refusal decided before `guarded` runs (a 403 from the authenticator after
the token was identified) is logged by the surface through `record_denied`.
A 401 has no identifiable connection and writes nothing.
"""
import logging
import time

from api.gateway import calllog, limits
from api.gateway.auth import GatewayError
from api.postgres import user_context

logger = logging.getLogger(__name__)

_LOGGED = "_gateway_logged"


def _elapsed_ms(started) -> int:
    return int((time.monotonic() - started) * 1000)


def _safe_count_denied(principal):
    try:
        limits.count_denied(principal)
    except Exception as exc:  # noqa: BLE001 -- counting must not change the answer
        logger.error("gateway_usage_error exception=%s", type(exc).__name__)


def _outcome(status) -> str:
    if status == 403:
        return "denied"
    if status == 429:
        return "rate_limited"
    return "ok" if status < 400 else "error"


def _status_of(exc) -> int:
    """The HTTP status a surface answers for an exception raised inside fn()."""
    if isinstance(exc, GatewayError):
        return exc.status
    # agent_tools.GatewayToolError, checked by shape to keep this module free
    # of the tool registry.
    if hasattr(exc, "forbidden") and hasattr(exc, "code"):
        if exc.forbidden:
            return 403
        return 404 if exc.code in ("not_found", "unknown_tool") else 400
    if isinstance(exc, PermissionError):
        return 403
    if isinstance(exc, ValueError):
        return 400
    return 500


def _mark_logged(exc):
    try:
        setattr(exc, _LOGGED, True)
    except Exception:  # noqa: BLE001 -- an exception without a __dict__
        pass


def _enforce(principal, surface, operation, started):
    """Count one call against the limits; log and re-raise a refusal."""
    try:
        limits.enforce(principal)
    except GatewayError as exc:
        if exc.status == 429:
            _safe_count_denied(principal)
        calllog.record(principal, surface, operation, exc.status, _outcome(exc.status),
                       0, _elapsed_ms(started))
        _mark_logged(exc)
        raise


def enforce_unlogged(principal, surface, operation):
    """Count a message that needs no answer (an MCP notification) against the limits.

    It goes through the same minute and Warsaw-day limits as every other
    call and lands in `api_usage_daily.calls`, so a flood of notifications
    is throttled and visible in the usage counters. An allowed
    one writes no call-log row -- it carries no operation worth listing; a
    refused one is logged like any other 429 and raises RateLimited.
    """
    _enforce(principal, surface, operation, time.monotonic())


def guarded(principal, surface, operation, fn):
    """Run one gateway operation for `principal` and return (status, payload, records)."""
    started = time.monotonic()
    _enforce(principal, surface, operation, started)
    try:
        # The operation reads as the connection's owner; row-level security
        # (migration 0016) holds even if a read forgot its partner filter.
        with user_context(principal.user):
            status, payload, records, *rest = fn()
    except Exception as exc:
        status = _status_of(exc)
        if status in (403, 429):
            _safe_count_denied(principal)
        calllog.record(principal, surface, operation, status, _outcome(status),
                       0, _elapsed_ms(started))
        _mark_logged(exc)
        raise
    log_status = rest[0] if rest else status
    if log_status in (403, 429):
        _safe_count_denied(principal)
    try:
        limits.add_records(principal, records)
    except Exception as exc:  # noqa: BLE001 -- the answer is already computed
        logger.error("gateway_usage_error exception=%s", type(exc).__name__)
    calllog.record(principal, surface, operation, log_status, _outcome(log_status), records,
                   _elapsed_ms(started))
    return status, payload, records


def record_denied(error, surface, operation) -> None:
    """Log a 403 refused after the token was identified. Never raises.

    Does nothing for a 401 (no connection to attribute it to), for any other
    status, or for a refusal `guarded` already logged.
    """
    principal = getattr(error, "principal", None)
    if principal is None or getattr(error, "status", None) != 403:
        return
    if getattr(error, _LOGGED, False):
        return
    _safe_count_denied(principal)
    calllog.record(principal, surface, operation, 403, "denied")
    _mark_logged(error)
