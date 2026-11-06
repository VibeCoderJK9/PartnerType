"""The gateway call log -- who called what, when, and how it ended.

One row per authenticated gateway request, written from `pipeline.guarded`
(and `pipeline.record_denied` for refusals after the token was identified).
A row holds the connection, the user, the surface, the operation NAME (a tool
name or a route template such as `/leads/{id}`), the status code, the outcome,
how many records went out and how long it took. Nothing else: `record()` has
no parameter through which arguments, query strings, bodies, CRM content or
tokens could reach the table, and the table has no column for them
(migration 0013). ARCHITECTURE.md: prompt text and transcripts never reach a log.

`record()` never raises -- like `api.audit.log`, a logging failure must not
turn an answered request into a 500. It logs only the exception type.

Rows older than `RETENTION_DAYS` are removed by `api.db.run_maintenance`.
"""
import logging

from api.db import get_conn

logger = logging.getLogger(__name__)

RETENTION_DAYS = 90

MAX_OPERATION_CHARS = 100

_COLUMNS = ("id, created_at, connection_kind, connection_id, user_id, surface, "
            "operation, status_code, outcome, records, duration_ms")


def _non_negative_int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def record(principal, surface, operation, status_code, outcome, records=0,
           duration_ms=0) -> None:
    """Write one call-log row for `principal`. Never raises."""
    try:
        user_id = (principal.user or {}).get("id")
        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO api_call_log
                   (connection_kind, connection_id, user_id, surface, operation,
                    status_code, outcome, records, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (principal.kind, int(principal.connection_id), user_id, surface,
                 str(operation or "")[:MAX_OPERATION_CHARS], int(status_code), outcome,
                 _non_negative_int(records), _non_negative_int(duration_ms)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- the log must never break a request
        logger.error("gateway_calllog_error exception=%s", type(exc).__name__)


def _connection_filter(connection_kind, connection_id):
    clauses, params = [], []
    if connection_kind is not None:
        clauses.append("connection_kind = ?")
        params.append(connection_kind)
    if connection_id is not None:
        clauses.append("connection_id = ?")
        params.append(int(connection_id))
    return clauses, params


def list_calls(limit=50, offset=0, connection_kind=None, connection_id=None) -> list:
    """Call-log rows, newest first (created_at DESC, id DESC)."""
    limit = min(max(int(limit), 1), 200)
    offset = max(int(offset), 0)
    clauses, params = _connection_filter(connection_kind, connection_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""SELECT {_COLUMNS} FROM api_call_log {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def usage_summary(days=30, connection_kind=None, connection_id=None) -> list:
    """Daily usage rows for the last `days` Europe/Warsaw days, newest day first.

    A connection that never called has no rows, so its summary is `[]`.
    """
    days = min(max(int(days), 1), 400)
    clauses, params = _connection_filter(connection_kind, connection_id)
    clauses.insert(0, "day > (now() AT TIME ZONE 'Europe/Warsaw')::date - CAST(? AS integer)")
    params.insert(0, days)
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""SELECT connection_kind, connection_id, day, calls, denied, records
                FROM api_usage_daily
                WHERE {" AND ".join(clauses)}
                ORDER BY day DESC, connection_kind, connection_id""",
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
