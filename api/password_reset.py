"""Self-service password recovery.

The only way back into an account today is the producer re-inviting the person,
which does not exist for the producer's own account at all. The token mechanics
mirror api/invitations.py deliberately -- same hashing, same single use, same
"the raw secret exists only in the link" rule -- but they are a separate table
and a separate module, because accepting an invitation also activates a pending
account and a reset must never do that.
"""
import hashlib
import os
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote

import bcrypt

from api import audit, email_service
from api.db import get_conn
from api.validation import parse_email, parse_password
from api.i18n import N_, t

RESET_TTL_MINUTES = 60
# A person who asks twice because the first mail was slow is normal; a script
# pumping the address with mail is not.
MAX_RESETS_PER_HOUR = 3
_DEFAULT_BASE_URL = "http://localhost:8000"
_GENERIC_ANSWER = (
    N_("If an account with this address exists, we have sent a link to set the password.")
)
_INVALID_MESSAGE = (
    N_("The link is invalid, has expired or has already been used.")
)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def build_reset_link(raw_token: str) -> str:
    """The absolute link carrying the raw secret.

    Reads the configured base URL only, never the request's Host header: a
    planted header would otherwise send the secret to somebody else's server.
    """
    base = os.environ.get("PARTNERTYPE_BASE_URL", "").strip()
    if not base or not (base.startswith("http://") or base.startswith("https://")):
        base = _DEFAULT_BASE_URL
    return f"{base.rstrip('/')}/set-password.html?token={quote(raw_token, safe='')}&reset=1"


def request_reset(email: str, ip: str = ""):
    """Create and send a reset link, or quietly do nothing.

    Returns the raw token when one was issued, and None otherwise, so tests and
    the caller can tell the difference -- the HTTP handler must not.
    """
    try:
        address = parse_email(email, N_("E-mail")).lower()
    except ValueError:
        return None

    conn = get_conn()
    try:
        conn.begin()
        user = conn.execute(
            "SELECT id, name, email FROM users WHERE email = ? AND active = 1",
            (address,),
        ).fetchone()
        if user is None:
            conn.commit()
            return None

        recent = conn.execute(
            """SELECT COUNT(*) AS count FROM password_resets
               WHERE user_id = ? AND created_at > ?""",
            (user["id"], (datetime.utcnow() - timedelta(hours=1)).isoformat(sep=" ")[:19]),
        ).fetchone()
        if recent["count"] >= MAX_RESETS_PER_HOUR:
            conn.commit()
            audit.log(None, "user.password_reset_throttled", "user", user["id"], "", ip)
            return None

        raw_token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (?,?,?)",
            (user["id"], _hash_token(raw_token),
             (datetime.utcnow() + timedelta(minutes=RESET_TTL_MINUTES)).isoformat()),
        )
        conn.commit()
        name, address = user["name"], user["email"]
    finally:
        conn.close()

    audit.log(None, "user.password_reset_requested", "user", user["id"], "", ip)
    email_service.send_password_reset(address, name, build_reset_link(raw_token))
    return raw_token


def complete_reset(raw_token: str, password: str, ip: str = ""):
    """Redeem the secret, set the new password and end every existing session.

    Ending the sessions is the point as much as the new password is: if the
    reason for the reset was somebody else holding the old one, leaving their
    session alive would make the whole exercise pointless.
    """
    if not isinstance(raw_token, str) or not raw_token:
        raise ValueError(t(_INVALID_MESSAGE))
    # Imported here: api.gateway pulls in api.auth, which this module's
    # importers may still be loading.
    from api.gateway import keys as gateway_keys

    conn = get_conn()
    try:
        conn.begin()
        row = conn.execute(
            """SELECT r.id AS reset_id, r.user_id
               FROM password_resets r
               WHERE r.token_hash = ?
                 AND r.used_at IS NULL
                 AND r.expires_at > ? FOR UPDATE OF r""",
            (_hash_token(raw_token), datetime.utcnow().isoformat()),
        ).fetchone()
        if not row:
            raise ValueError(t(_INVALID_MESSAGE))

        password = parse_password(password, N_("Password"), min_length=8, max_length=72)
        now = datetime.utcnow().isoformat()
        conn.execute(
            "UPDATE users SET password = ? WHERE id = ?",
            (bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(), row["user_id"]),
        )
        conn.execute(
            "UPDATE password_resets SET used_at = ? WHERE id = ?", (now, row["reset_id"]))
        # Any other link sent before this one is spent too.
        conn.execute(
            "UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
            (now, row["user_id"]),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
        # And every API key and OAuth connection: a stolen key or
        # refresh token must not outlive the reset. Same transaction, so a
        # reset that fails below leaves them exactly as they were.
        counts = gateway_keys.revoke_all_for_user(conn, row["user_id"], "password_reset")
        # The account is back in its owner's hands; old failures must not keep
        # the lockout from api/auth.py running against them.
        conn.execute(
            "DELETE FROM login_attempts WHERE email = (SELECT email FROM users WHERE id = ?)",
            (row["user_id"],),
        )
        conn.commit()
        user_id = row["user_id"]
    finally:
        conn.close()

    audit.log(None, "user.password_reset", "user", user_id, "", ip)
    gateway_keys.audit_revocation(None, user_id, counts)
    return user_id


# ─── HTTP HANDLERS ────────────────────────────────────────────────────────────

def handle_request(handler, body: dict):
    """Always the same answer, so the form cannot be used to find accounts."""
    from api.http_security import client_ip

    if not isinstance(body, dict):
        return 400, {"error": t("Invalid request.")}
    # The lookup, the token and the SMTP conversation happen only for a real
    # account, so doing them before answering would make the response time
    # say whether the address exists. They run after the answer instead.
    email_service.run_in_background(request_reset, body.get("email", ""), client_ip(handler))
    return 200, {"message": t(_GENERIC_ANSWER)}


def handle_complete(handler, token: str, body: dict):
    from api.http_security import client_ip

    if not isinstance(body, dict):
        return 400, {"error": t("Invalid request.")}
    try:
        complete_reset(token, body.get("password"), client_ip(handler))
    except ValueError as exc:
        return 400, {"error": str(exc)}
    return 200, {"message": t("The password was changed. Sign in with the new password.")}
