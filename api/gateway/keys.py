"""Personal API keys.

A key is `ptk_` + 256 random bits. It is shown to its owner exactly once, in the
return value of `create_key`; the database keeps its SHA-256 fingerprint and a
short recognisable prefix, nothing else. Every key expires (30, 90 or 365
days) -- there is no "never" -- and carries the `read` scope only; the
scopes column is there so a `propose` scope can be added without a
structural migration.

Each user creates keys for themself, within their own role. The admin path
(seeing and revoking every key) is gated by the Integrations screen
(api/gateway/admin_api.py), which also writes the audit entries
for create/revoke.

`revoke_all_for_user` ends every key AND every OAuth connection of a user
inside the caller's transaction; "Sign out everywhere", a completed password
reset and every password change (own or set by an administrator) call it (a stolen key or a
30-day refresh token must not survive the owner taking the account back).
"""
import re

from api import audit
from api.db import get_conn
from api.gateway import tokens
from api.i18n import t

EXPIRY_CHOICES = (30, 90, 365)
# Live (unrevoked, unexpired) keys one user may hold at a time.
MAX_LIVE_KEYS_PER_USER = 10
DEFAULT_EXPIRY_DAYS = 90
NAME_MAX_LENGTH = 100

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")

_PUBLIC_COLUMNS = (
    "id, user_id, name, display_prefix, scopes, created_at, expires_at,"
    " last_used_at, revoked_at, revoked_by"
)


def _parse_name(name):
    if not isinstance(name, str):
        raise ValueError(t("Give the key a name."))
    name = name.strip()
    if not name:
        raise ValueError(t("Give the key a name."))
    if len(name) > NAME_MAX_LENGTH:
        raise ValueError(t("The key name can have at most {max_length} characters.", max_length=NAME_MAX_LENGTH))
    if _CONTROL_RE.search(name):
        raise ValueError(t("The key name contains characters that are not allowed."))
    return name


def _parse_expiry(days):
    # bool is an int in Python; True must not quietly mean "1 day".
    if isinstance(days, bool) or not isinstance(days, int) or days not in EXPIRY_CHOICES:
        raise ValueError(t("Key validity: 30, 90 or 365 days."))
    return days


def create_key(user, name, expires_in_days=DEFAULT_EXPIRY_DAYS) -> dict:
    """Create a key for `user` and return it -- the only time the raw key exists.

    Refused (PermissionError) whenever the same user would be refused at the
    gateway itself: integrations off, a role that may not connect, a partner
    while partners may not connect, or a missing required second factor. A key
    that could never be used must not be minted. ValueError when the user
    already holds MAX_LIVE_KEYS_PER_USER live keys.
    """
    from api.gateway.auth import connection_refusal

    if not user or not user.get("id"):
        raise PermissionError(t("Sign in to create a key."))
    refusal = connection_refusal(user)
    if refusal is not None:
        raise PermissionError(refusal.description)
    name = _parse_name(name)
    days = _parse_expiry(expires_in_days)

    raw = tokens.new_token(tokens.KEY_PREFIX)
    with get_conn() as conn:
        conn.begin()
        # Serialise key creation per user, so two parallel requests cannot
        # both pass the count below.
        conn.execute("SELECT id FROM users WHERE id = ? FOR UPDATE", (user["id"],))
        live = conn.execute(
            """SELECT COUNT(*) AS n FROM api_keys
               WHERE user_id = ? AND revoked_at IS NULL AND expires_at > now()""",
            (user["id"],),
        ).fetchone()["n"]
        if live >= MAX_LIVE_KEYS_PER_USER:
            raise ValueError(
                t("You already have {count} active keys. Revoke an unused key before you "
                  "create a new one.", count=MAX_LIVE_KEYS_PER_USER))
        row = conn.execute(
            """INSERT INTO api_keys
                   (user_id, name, display_prefix, token_hash, scopes, expires_at)
               VALUES (?, ?, ?, ?, 'read', now() + make_interval(days => CAST(? AS integer)))
               RETURNING id, name, display_prefix, scopes, created_at, expires_at""",
            (user["id"], name, tokens.display_prefix(raw), tokens.token_hash(raw), days),
        ).fetchone()
    result = dict(row)
    result["key"] = raw
    return result


def _parse_key_id(key_id):
    if isinstance(key_id, bool):
        raise ValueError(t("Invalid key ID."))
    try:
        value = int(key_id)
    except (TypeError, ValueError):
        raise ValueError(t("Invalid key ID.")) from None
    if value < 1:
        raise ValueError(t("Invalid key ID."))
    return value


def revoke_key(actor, key_id, *, any_owner=False) -> bool:
    """Revoke one key. Returns False when nothing matched.

    Without `any_owner` only the actor's own unrevoked key can match, so a
    guessed id belonging to someone else is indistinguishable from a missing
    one. The admin path (`any_owner=True`) is authorised by its caller.
    """
    if not actor or not actor.get("id"):
        raise PermissionError(t("Sign in to revoke a key."))
    key_id = _parse_key_id(key_id)
    sql = ("UPDATE api_keys SET revoked_at = now(), revoked_by = ?"
           " WHERE id = ? AND revoked_at IS NULL")
    params = [actor["id"], key_id]
    if not any_owner:
        sql += " AND user_id = ?"
        params.append(actor["id"])
    with get_conn() as conn:
        return conn.execute(sql, params).rowcount > 0


def list_keys(user_id=None) -> list:
    """Keys newest first, without their fingerprints. All keys when no user."""
    sql = f"SELECT {_PUBLIC_COLUMNS} FROM api_keys"
    params = []
    if user_id is not None:
        sql += " WHERE user_id = ?"
        params.append(user_id)
    sql += " ORDER BY created_at DESC, id DESC"
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def revoke_all_for_user(conn, user_id, reason) -> dict:
    """Revoke every live key and OAuth grant (with all its tokens) of `user_id`.

    Runs on the caller's connection and does NOT commit: the revocation must
    land or roll back together with the session deletion that asked for it.
    Returns {"keys": n, "grants": m} -- what this call actually revoked.
    """
    revoked_keys = conn.execute(
        """UPDATE api_keys SET revoked_at = now(), revoked_by = ?
           WHERE user_id = ? AND revoked_at IS NULL""",
        (user_id, user_id),
    ).rowcount
    revoked_grants = conn.execute(
        """UPDATE oauth_grants
           SET revoked_at = now(), revoked_reason = ?, revoked_by = ?
           WHERE user_id = ? AND revoked_at IS NULL
           RETURNING id""",
        (str(reason)[:64], user_id, user_id),
    ).fetchall()
    conn.execute(
        """UPDATE oauth_tokens SET revoked_at = now()
           WHERE revoked_at IS NULL
             AND grant_id IN (SELECT id FROM oauth_grants WHERE user_id = ?)""",
        (user_id,),
    )
    return {"keys": max(0, revoked_keys or 0), "grants": len(revoked_grants)}


def audit_revocation(actor, user_id, counts) -> None:
    """One audit entry for a bulk revocation, and only when something was revoked."""
    revoked_keys = int((counts or {}).get("keys", 0))
    revoked_grants = int((counts or {}).get("grants", 0))
    if revoked_keys + revoked_grants <= 0:
        return
    audit.log(actor, "integration.credentials_revoked", "user", str(user_id),
              t("keys: {keys}, connections: {grants}", keys=revoked_keys, grants=revoked_grants))
