"""The app's own control surface for the gateway: /api/integrations/*.

Session-authenticated through the legacy router (api/router.py), so every
request here has already passed the Origin/Referer CSRF check and the MFA
gate. These handlers are thin: the rules live in api/gateway/settings.py,
api/gateway/keys.py and api/gateway/oauth.py, and the role decision in
api/authz.py::can_manage_integrations (administrator only).

Two audiences:
- the administrator: switches, field groups, limits, the
  list of every key and OAuth connection with one-click revoke,
  and the call log / daily usage read-outs;
- every logged-in user: their own keys and connections.

A raw key leaves the server exactly once, in the 201 answer of key creation.
No response here ever carries a key's or token's fingerprint. Names that a
client registered for itself (DCR `client_name`) are returned as data; the
browser renders them with textContent.
"""
from urllib.parse import urlsplit
import json

from api import audit, authz
from api.config import Config
from api.db import get_conn
from api.gateway import auth, keys, settings
from api.validation import ValidationError
from api.i18n import t

def _forbidden():
    return 403, {"error": t("Only an administrator manages integrations.")}


def _iso(value):
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _base_url():
    return Config.load().base_url


def _host(uri):
    try:
        return urlsplit(uri).hostname or ""
    except (TypeError, ValueError):
        return ""


def _grant_hosts(approved_uri, registered_raw):
    """Where a connection's code went: the approved redirect host.

    Grants made before the approved URI was stored fall back to every host
    the client registered, so no host is ever hidden.
    """
    host = _host(approved_uri) if approved_uri else ""
    return [host] if host else _redirect_hosts(registered_raw)


def _redirect_hosts(raw):
    """Hosts of a client's registered redirect URIs, in registration order."""
    try:
        uris = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    hosts = []
    for uri in uris if isinstance(uris, list) else []:
        if not isinstance(uri, str):
            continue
        try:
            host = urlsplit(uri).hostname or ""
        except ValueError:
            host = ""
        if host and host not in hosts:
            hosts.append(host)
    return hosts


# ─── views ─────────────

_KEY_FIELDS = ("id", "name", "display_prefix", "scopes", "created_at", "expires_at",
               "last_used_at", "revoked_at")
_TIME_FIELDS = frozenset({"created_at", "expires_at", "last_used_at", "revoked_at"})


def _key_view(row) -> dict:
    return {name: (_iso(row.get(name)) if name in _TIME_FIELDS else row.get(name))
            for name in _KEY_FIELDS}


def _own_connections(user_id) -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT g.id, c.client_name, c.redirect_uris, g.redirect_uri, g.created_at,
                      g.last_used_at, g.revoked_at
               FROM oauth_grants g
               JOIN oauth_clients c ON c.client_id = g.client_id
               WHERE g.user_id = ?
               ORDER BY g.created_at DESC, g.id DESC""",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    result = []
    for row in rows:
        hosts = _grant_hosts(row["redirect_uri"], row["redirect_uris"])
        result.append({
            "id": row["id"],
            "client_name": row["client_name"],
            "redirect_host": ", ".join(hosts),
            "created_at": _iso(row["created_at"]),
            "last_used_at": _iso(row["last_used_at"]),
            "revoked_at": _iso(row["revoked_at"]),
        })
    return result


# ─── settings (admin) ────────────────────────────────────────────────────────

def handle_settings_get(handler, user):
    if not authz.can_manage_integrations(user):
        return _forbidden()
    return 200, settings.snapshot(_base_url())


def handle_settings_update(handler, user, body):
    try:
        settings.set_integration_settings(user, body)
    except PermissionError as exc:
        return 403, {"error": str(exc)}
    except ValidationError as exc:
        return 400, {"error": str(exc)}
    return 200, settings.snapshot(_base_url())


# ─── self-service ────────────────────────────────────────────────────────────

def handle_me(handler, user):
    """The caller's own door to the gateway: may they connect, and with what."""
    refusal = auth.connection_refusal(user)
    return 200, {
        "can_connect": refusal is None,
        "refusal_code": refusal.error if refusal is not None else None,
        "mcp_url": auth.resource_url(_base_url(), "mcp"),
        "keys": [_key_view(row) for row in keys.list_keys(user["id"])],
        "connections": _own_connections(user["id"]),
    }


def handle_key_create(handler, user, body):
    """Create a key for the caller only; the raw key is in this answer and nowhere else."""
    body = body if isinstance(body, dict) else {}
    try:
        created = keys.create_key(user, body.get("name"),
                                  body.get("expires_in_days", keys.DEFAULT_EXPIRY_DAYS))
    except PermissionError as exc:
        return 403, {"error": str(exc)}
    except ValueError as exc:
        return 400, {"error": str(exc)}
    audit.log(user, "integration.key_created", "api_key", str(created["id"]),
              f"{created['name']} ({created['display_prefix']})")
    result = {name: (_iso(value) if name in _TIME_FIELDS else value)
              for name, value in created.items()}
    return 201, result


# ─── revocation: your own, or anyone's for the administrator ──────────

def _key_not_found():
    return 404, {"error": t("Key not found.")}


def _connection_not_found():
    return 404, {"error": t("Connection not found.")}


def _positive_id(value):
    text = str(value or "")
    if not (text.isascii() and text.isdigit()) or int(text) < 1:
        return None
    return int(text)


def handle_key_revoke(handler, user, key_id):
    """Revoke a key. Someone else's id is a plain 404 for a non-admin -- no oracle."""
    key_id = _positive_id(key_id)
    if key_id is None:
        return _key_not_found()
    try:
        revoked = keys.revoke_key(user, key_id,
                                  any_owner=authz.can_manage_integrations(user))
    except (PermissionError, ValueError):
        return _key_not_found()
    if not revoked:
        return _key_not_found()
    conn = get_conn()
    try:
        row = conn.execute("SELECT display_prefix FROM api_keys WHERE id = ?",
                           (key_id,)).fetchone()
    finally:
        conn.close()
    audit.log(user, "integration.key_revoked", "api_key", str(key_id),
              row["display_prefix"] if row else "")
    return 200, {"ok": True}


def handle_connection_revoke(handler, user, grant_id):
    """Revoke an OAuth connection and every token it issued (oauth.revoke_grant)."""
    from api.gateway import oauth

    grant_id = _positive_id(grant_id)
    if grant_id is None:
        return _connection_not_found()
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT g.id, g.user_id, g.revoked_at, c.client_name
               FROM oauth_grants g
               JOIN oauth_clients c ON c.client_id = g.client_id
               WHERE g.id = ?""",
            (grant_id,),
        ).fetchone()
    finally:
        conn.close()
    is_admin = authz.can_manage_integrations(user)
    if row is None or row["revoked_at"] is not None:
        return _connection_not_found()
    own = row["user_id"] == user["id"]
    if not own and not is_admin:
        return _connection_not_found()
    reason = "user_revoked" if own else "admin_revoked"
    if not oauth.revoke_grant(grant_id, reason, user["id"]):
        return _connection_not_found()
    audit.log(user, "integration.connection_revoked", "oauth_grant", str(grant_id),
              (row["client_name"] or "")[:100])
    return 200, {"ok": True}


# ─── the administrator's overview, log and usage ──────────

def handle_admin_connections(handler, user):
    """Every key and every OAuth connection, newest first, without any secret."""
    if not authz.can_manage_integrations(user):
        return _forbidden()
    conn = get_conn()
    try:
        key_rows = conn.execute(
            """SELECT k.id, k.name, k.display_prefix, k.scopes, k.created_at,
                      k.expires_at, k.last_used_at, k.revoked_at, k.user_id,
                      u.name AS user_name, u.role AS user_role, u.email AS user_email
               FROM api_keys k
               JOIN users u ON u.id = k.user_id
               ORDER BY k.created_at DESC, k.id DESC"""
        ).fetchall()
        grant_rows = conn.execute(
            """SELECT g.id, g.user_id, g.created_at, g.last_used_at, g.revoked_at,
                      g.revoked_reason, g.redirect_uri, c.client_name, c.redirect_uris,
                      u.name AS user_name, u.role AS user_role, u.email AS user_email
               FROM oauth_grants g
               JOIN oauth_clients c ON c.client_id = g.client_id
               JOIN users u ON u.id = g.user_id
               ORDER BY g.created_at DESC, g.id DESC"""
        ).fetchall()
    finally:
        conn.close()
    owner = ("user_id", "user_name", "user_role", "user_email")
    key_list = []
    for row in key_rows:
        row = dict(row)
        item = _key_view(row)
        item.update({name: row[name] for name in owner})
        key_list.append(item)
    connection_list = []
    for row in grant_rows:
        connection_list.append({
            "id": row["id"],
            **{name: row[name] for name in owner},
            "client_name": row["client_name"],
            "redirect_hosts": _grant_hosts(row["redirect_uri"], row["redirect_uris"]),
            "created_at": _iso(row["created_at"]),
            "last_used_at": _iso(row["last_used_at"]),
            "revoked_at": _iso(row["revoked_at"]),
            "revoked_reason": row["revoked_reason"],
        })
    return 200, {"keys": key_list, "connections": connection_list}


class _BadQuery(Exception):
    pass


def _int_params(qs, spec):
    """Strictly parse integer query parameters: {name: (default, low, high)}.

    Unknown names, repeated names and anything but plain digits within the
    range are refused -- the read-outs never guess what a caller meant.
    """
    qs = qs or {}
    if set(qs) - set(spec):
        raise _BadQuery()
    result = {}
    for name, (default, low, high) in spec.items():
        values = qs.get(name)
        if values is None:
            result[name] = default
            continue
        if len(values) != 1 or not (values[0].isascii() and values[0].isdigit()):
            raise _BadQuery()
        value = int(values[0])
        if not low <= value <= high:
            raise _BadQuery()
        result[name] = value
    return result


def _user_names(user_ids) -> dict:
    ids = sorted({uid for uid in user_ids if uid is not None})
    if not ids:
        return {}
    conn = get_conn()
    try:
        rows = conn.execute("SELECT id, name FROM users WHERE id = ANY(?)", (ids,)).fetchall()
    finally:
        conn.close()
    return {row["id"]: row["name"] for row in rows}


def handle_admin_log(handler, user, qs):
    """The gateway call log, newest first (calllog.list_calls) with the user's name."""
    from api.gateway import calllog

    if not authz.can_manage_integrations(user):
        return _forbidden()
    try:
        params = _int_params(qs, {"limit": (50, 1, 200), "offset": (0, 0, 1_000_000)})
    except _BadQuery:
        return 400, {"error": t("Invalid parameters: limit 1–200, offset from 0.")}
    rows = calllog.list_calls(limit=params["limit"], offset=params["offset"])
    names = _user_names(row["user_id"] for row in rows)
    calls = []
    for row in rows:
        item = dict(row)
        item["created_at"] = _iso(item["created_at"])
        item["user_name"] = names.get(item["user_id"], "")
        calls.append(item)
    return 200, {"calls": calls}


def _connection_labels(rows) -> dict:
    key_ids = sorted({r["connection_id"] for r in rows if r["connection_kind"] == "key"})
    grant_ids = sorted({r["connection_id"] for r in rows if r["connection_kind"] == "oauth"})
    labels = {}
    conn = get_conn()
    try:
        if key_ids:
            for row in conn.execute(
                    "SELECT id, name, display_prefix FROM api_keys WHERE id = ANY(?)",
                    (key_ids,)).fetchall():
                labels[("key", row["id"])] = f"{row['name']} ({row['display_prefix']})"
        if grant_ids:
            for row in conn.execute(
                    """SELECT g.id, c.client_name, u.name AS user_name
                       FROM oauth_grants g
                       JOIN oauth_clients c ON c.client_id = g.client_id
                       JOIN users u ON u.id = g.user_id
                       WHERE g.id = ANY(?)""",
                    (grant_ids,)).fetchall():
                labels[("oauth", row["id"])] = f"{row['client_name']} — {row['user_name']}"
    finally:
        conn.close()
    return labels


def handle_admin_usage(handler, user, qs):
    """Daily usage per connection (calllog.usage_summary), labelled for people."""
    from api.gateway import calllog

    if not authz.can_manage_integrations(user):
        return _forbidden()
    try:
        params = _int_params(qs, {"days": (30, 1, 400)})
    except _BadQuery:
        return 400, {"error": t("Invalid parameter: days 1–400.")}
    rows = calllog.usage_summary(days=params["days"])
    labels = _connection_labels(rows)
    usage = [{
        "day": _iso(row["day"]),
        "connection_kind": row["connection_kind"],
        "connection_id": row["connection_id"],
        "label": labels.get((row["connection_kind"], row["connection_id"]), ""),
        "calls": row["calls"],
        "denied": row["denied"],
        "records": row["records"],
    } for row in rows]
    return 200, {"usage": usage}
