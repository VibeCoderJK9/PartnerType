"""Fixed-window request counters in PostgreSQL.

`consume(bucket, limit, window_seconds)` counts one hit in the current window
of a named bucket and says whether it is still within the limit. The counter
lives in `api_rate_windows`, not in process memory: Gunicorn runs one process
today, but a restart must not hand every caller a fresh allowance, and a
second process later must not double it.

One statement does the whole job -- insert-or-increment and read back -- so
two concurrent hits can never both see the same count. The window start is
computed by the database clock, the same clock every expiry in the gateway
uses.

It serves the per-IP limit on dynamic client registration, and the
per-connection limits on top:

- a minute limit: fixed 60-second windows keyed by their start, so a full
  window in the previous minute never blocks the first call of the next one;
- a day limit counted per Europe/Warsaw calendar day in `api_usage_daily`,
  the same row that keeps calls served, denied requests and records returned
  for each connection (the basis for any later paid package);
- both limits are the administrator's values capped by the operator's
  (`settings.effective_limits`);
- the same two limits also apply to each user across all of their keys and
  OAuth connections, so creating more connections never multiplies them.

A refusal raises `auth.RateLimited` (429) with an integer `Retry-After`: the
seconds left in the minute window, or the seconds until the next Warsaw
midnight for the day limit.
"""
import math
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from api.db import get_conn
from api.gateway import settings
from api.gateway.auth import RateLimited

WARSAW = ZoneInfo("Europe/Warsaw")
MINUTE_WINDOW_SECONDS = 60
# Retention, swept by api.db.run_maintenance.
USAGE_RETENTION_DAYS = 400
WINDOW_RETENTION_DAYS = 2

# The usage day is the database's own Warsaw day, the same expression in
# every statement so a call and its records land on one row.
_WARSAW_DAY = "(now() AT TIME ZONE 'Europe/Warsaw')::date"
# The instant the current Warsaw day began: the window of a per-user day count.
_WARSAW_MIDNIGHT = f"(({_WARSAW_DAY})::timestamp AT TIME ZONE 'Europe/Warsaw')"

_CONSUME_SQL = """
    INSERT INTO api_rate_windows (bucket, window_start, hits)
    VALUES (?,
            to_timestamp(floor(extract(epoch FROM now()) / CAST(? AS integer))
                         * CAST(? AS integer)),
            1)
    ON CONFLICT (bucket, window_start)
    DO UPDATE SET hits = api_rate_windows.hits + 1
    RETURNING hits,
              extract(epoch FROM (api_rate_windows.window_start
                                  + make_interval(secs => CAST(? AS integer))
                                  - now())) AS remaining
"""


def consume(bucket, limit, window_seconds):
    """Count one hit; return (allowed, retry_after_seconds).

    `retry_after` is the time left in the current window, at least 1 second,
    so it can go straight into a `Retry-After` header.
    """
    if not isinstance(bucket, str) or not bucket:
        raise ValueError("A rate bucket needs a name")
    window = int(window_seconds)
    if window < 1:
        raise ValueError("A rate window must be at least one second")
    with get_conn() as conn:
        row = conn.execute(_CONSUME_SQL, (bucket[:200], window, window, window)).fetchone()
    hits = int(row["hits"])
    remaining = float(row["remaining"] or 0)
    return hits <= int(limit), max(1, math.ceil(remaining))


def seconds_to_warsaw_midnight(now=None) -> int:
    """Whole seconds until the next Europe/Warsaw midnight, rounded up, >= 1.

    Computed in UTC between two aware instants, so a DST change (a 23- or
    25-hour day) is counted correctly.
    """
    current = now or datetime.now(timezone.utc)
    local = current.astimezone(WARSAW)
    midnight = datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=WARSAW)
    delta = midnight.astimezone(timezone.utc) - current.astimezone(timezone.utc)
    return max(1, math.ceil(delta.total_seconds()))


def _minute_bucket(principal) -> str:
    return f"conn:{principal.kind}:{principal.connection_id}:m"


def _user_bucket(principal, period) -> str:
    return f"user:{int(principal.user['id'])}:{period}"


class _DayLimitReached(Exception):
    pass


def enforce(principal) -> None:
    """Count one call for the connection and its user, or raise RateLimited (429).

    The minute windows (connection, then user) are checked first. A call that
    passes them is counted against the Warsaw day for the user and for the
    connection in one transaction of conditional UPDATEs, so concurrent calls
    can never push either count past the day limit, and a refused call is
    counted in neither.
    """
    limit = settings.effective_limits()
    for bucket in (_minute_bucket(principal), _user_bucket(principal, "m")):
        allowed, retry_after = consume(bucket, limit["per_minute"], MINUTE_WINDOW_SECONDS)
        if not allowed:
            raise RateLimited("Per-minute limit exceeded", retry_after=retry_after,
                              principal=principal)
    key = (principal.kind, int(principal.connection_id))
    day_limit = int(limit["per_day"])
    user_day = _user_bucket(principal, "d")
    try:
        with get_conn() as conn:
            conn.begin()
            conn.execute(
                f"""INSERT INTO api_rate_windows (bucket, window_start, hits)
                    VALUES (?, {_WARSAW_MIDNIGHT}, 0)
                    ON CONFLICT (bucket, window_start) DO NOTHING""",
                (user_day,))
            if conn.execute(
                    f"""UPDATE api_rate_windows SET hits = hits + 1
                        WHERE bucket = ? AND window_start = {_WARSAW_MIDNIGHT} AND hits < ?
                        RETURNING hits""",
                    (user_day, day_limit)).fetchone() is None:
                raise _DayLimitReached()
            conn.execute(
                f"""INSERT INTO api_usage_daily (connection_kind, connection_id, day)
                    VALUES (?, ?, {_WARSAW_DAY})
                    ON CONFLICT (connection_kind, connection_id, day) DO NOTHING""",
                key)
            if conn.execute(
                    f"""UPDATE api_usage_daily SET calls = calls + 1
                        WHERE connection_kind = ? AND connection_id = ?
                          AND day = {_WARSAW_DAY} AND calls < ?
                        RETURNING calls""",
                    (*key, day_limit)).fetchone() is None:
                raise _DayLimitReached()
    except _DayLimitReached:
        raise RateLimited("Daily limit exceeded", retry_after=seconds_to_warsaw_midnight(),
                          principal=principal) from None


def _bump(principal, column, amount):
    with get_conn() as conn:
        conn.execute(
            f"""INSERT INTO api_usage_daily (connection_kind, connection_id, day, {column})
                VALUES (?, ?, {_WARSAW_DAY}, ?)
                ON CONFLICT (connection_kind, connection_id, day)
                DO UPDATE SET {column} = api_usage_daily.{column} + EXCLUDED.{column}""",
            (principal.kind, int(principal.connection_id), int(amount)))


def add_records(principal, records) -> None:
    """Add the number of records a call returned to today's usage row."""
    amount = int(records or 0)
    if amount > 0:
        _bump(principal, "records", amount)


def count_denied(principal) -> None:
    """Count one refused request (403 or 429) on today's usage row."""
    _bump(principal, "denied", 1)
