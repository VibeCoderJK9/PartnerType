"""
PartnerType — Auth Module
Login, logout, token generation, session validation.
"""

import hashlib
import hmac
from api.config import secret

import secrets
import uuid
import bcrypt
from datetime import datetime, timedelta
from api.db import get_conn
from api.postgres import system_context
from api.http_security import (
    build_session_cookie, build_session_cookie_expiry, client_ip,
)
from api import audit
from api.i18n import t


SESSION_TTL_HOURS = 24
# The sliding window above keeps an active person signed in; this is the wall
# it cannot slide past. Without it a session that is used once a day lives
# forever, which is exactly what an attacker holding a stolen one wants.
SESSION_ABSOLUTE_DAYS = 7
# Long enough to open an authenticator app, short enough that a stolen
# half-finished login is worthless by the time anyone finds it.
MFA_CHALLENGE_MINUTES = 5


# The login screen has two doors, not one per role:
# Partner and Vendor. Everyone on the producer's own side of the program --
# the account owner, their moderators, their observers -- comes in through the same
# Vendor door, because which staff role you hold is something the producer
# decided and recorded on your account, not something you should have to know
# and re-declare at a login form.
#
# Concrete role names still resolve to themselves, so a caller that posts
# role="admin" keeps its meaning -- including the "right password, wrong
# role -> 401" contract in api/tests/integration/test_auth_flow.py.
LOGIN_DOORS = {
    "producent": ("admin", "manager", "observer"),
    "partner":   ("partner",),
}

KNOWN_ROLES = ("admin", "partner", "manager", "observer")


def resolve_login_roles(door: str):
    """Which user roles the given login door admits.

    Returns a tuple of role names, or () when the value is neither a known
    door nor a known role -- the caller turns that into the same generic
    failure as a bad password, so probing this field tells an attacker
    nothing.
    """
    key = (door or "").strip().lower()
    if key in LOGIN_DOORS:
        return LOGIN_DOORS[key]
    if key in KNOWN_ROLES:
        return (key,)
    return ()


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _user_to_dict(row):
    return {
        "id":      row["id"],
        "email":   row["email"],
        "name":    row["name"],
        "role":    row["role"],
        "company": row["company"],
        "level":   row["level"],
    }


def _make_token():
    return hmac.new(secret("PARTNERTYPE_SESSION_SECRET", True).encode(),
                    uuid.uuid4().bytes, hashlib.sha256).hexdigest()


def _now_str():
    return datetime.utcnow().isoformat()


def _expiry_str():
    return (datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS)).isoformat()


def _absolute_expiry_str():
    return (datetime.utcnow() + timedelta(days=SESSION_ABSOLUTE_DAYS)).isoformat()


def _hash_session_token(raw_token: str) -> str:
    """Store the fingerprint, never the key itself.

    Invitation and reset secrets were already kept this way; sessions were the
    one place where reading the database handed over live access.
    """
    return hashlib.sha256(raw_token.encode()).hexdigest()


# ─── LOGIN RATE LIMIT ─────────────────────────────────────────────────────────

LOGIN_WINDOW_MINUTES = 15
LOGIN_MAX_FAILURES_PER_EMAIL = 5
LOGIN_MAX_FAILURES_PER_IP = 20


class MfaRequired(Exception):
    """The password was right and a second factor is still owed.

    Not a ValueError: every existing caller treats those as "login refused",
    and this is the opposite -- the account is fine, the login is unfinished.
    """

    def __init__(self, challenge):
        super().__init__(t("A code from the authenticator app is required."))
        self.challenge = challenge


class TooManyAttemptsError(ValueError):
    """Too many recent failures for this address or account.

    A ValueError so that any caller which already treats a refused login as one
    keeps working; handlers that can say 429 catch this first.
    """


def _record_attempt(conn, email, ip, succeeded):
    conn.execute(
        "INSERT INTO login_attempts (email, ip, succeeded) VALUES (?,?,?)",
        (email[:320], ip[:64], succeeded),
    )
    conn.commit()


def _refuse_if_throttled(conn, email, ip):
    """Count recent failures before any password is checked.

    Counting failures only, and clearing them on success, means a person who
    mistypes twice and then gets in is not punished, while a machine grinding
    through passwords is stopped whichever account it aims at.
    """
    window = f"{LOGIN_WINDOW_MINUTES} minutes"
    conn.execute(
        f"DELETE FROM login_attempts WHERE created_at < now() - INTERVAL '{window}'")
    conn.commit()
    row = conn.execute(
        f"""SELECT
              COUNT(*) FILTER (WHERE email = ?) AS by_email,
              COUNT(*) FILTER (WHERE ip = ? AND ? <> '') AS by_ip
            FROM login_attempts
            WHERE succeeded = false AND created_at >= now() - INTERVAL '{window}'""",
        (email, ip, ip),
    ).fetchone()
    if (row["by_email"] >= LOGIN_MAX_FAILURES_PER_EMAIL
            or row["by_ip"] >= LOGIN_MAX_FAILURES_PER_IP):
        raise TooManyAttemptsError(
            t("Too many sign-in attempts. Try again in a quarter of an hour.")
        )


_DUMMY_HASH = None


def _dummy_hash() -> bytes:
    """A bcrypt hash of nothing anybody can type, at the same cost as real ones."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt())
    return _DUMMY_HASH


# ─── CORE ─────────────────────────────────────────────────────────────────────

def login(email: str, password: str, role: str, ip: str = ""):
    """
    Validate credentials, create session, return token + user dict.

    `role` is the login door the form offered -- "producent" or "partner" --
    or, for older callers, a single concrete role name. See LOGIN_DOORS.

    Returns (user_dict, token) on success, raises ValueError on failure.
    """
    if not isinstance(email, str) or not isinstance(password, str) or not isinstance(role, str):
        raise ValueError(t("Invalid e-mail, password or role."))
    # One spelling of the address for the lookup, the failure count and the
    # failure record alike: recording " victim@x.pl" while counting
    # "victim@x.pl" would let surrounding spaces dodge the per-account limit.
    email = email.strip()
    if not email or not password:
        raise ValueError(t("E-mail and password are required."))

    roles = resolve_login_roles(role)
    if not roles:
        raise ValueError(t("Invalid e-mail, password or role."))

    conn = get_conn()
    try:
        _refuse_if_throttled(conn, email, ip)

        # Purge expired sessions first
        conn.execute(
            "DELETE FROM sessions WHERE expires_at < ?",
            (_now_str(),)
        )
        conn.commit()

        # bcrypt refuses more than 72 bytes, and no stored password is longer
        # (api/validation.py). A longer one is an ordinary wrong password:
        # the same time, the same answer, and it counts toward the limit.
        if len(password.encode()) > 72:
            bcrypt.checkpw(b"", _dummy_hash())
            _record_attempt(conn, email, ip, False)
            raise ValueError(t("Invalid e-mail, password or role."))

        placeholders = ",".join("?" for _ in roles)
        user = conn.execute(
            f"SELECT * FROM users WHERE email = ? AND role IN ({placeholders}) AND active = 1",
            (email, *roles)
        ).fetchone()

        if user is None:
            # Spend the same bcrypt time as a real check, so how long the
            # refusal takes does not say whether the address has an account.
            bcrypt.checkpw(password.encode(), _dummy_hash())
            _record_attempt(conn, email, ip, False)
            raise ValueError(t("Invalid e-mail, password or role."))

        if not bcrypt.checkpw(password.encode(), user["password"].encode()):
            _record_attempt(conn, email, ip, False)
            raise ValueError(t("Invalid e-mail, password or role."))

        from api import mfa

        # The password is proven; the failures before it must not keep counting
        # against the person who just typed it correctly.
        conn.execute("DELETE FROM login_attempts WHERE email = ?", (email,))

        if mfa.is_enabled(user["id"]):
            # Half a login: no session exists until the code is accepted.
            challenge = start_mfa_challenge(conn, user["id"])
            conn.commit()
            _record_attempt(conn, email, ip, True)
            raise MfaRequired(challenge)

        token = _issue_session(conn, user)
        conn.commit()
        _record_attempt(conn, email, ip, True)

        return _user_to_dict(user), token

    finally:
        conn.close()


def logout(token: str):
    """Delete session by token."""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (_hash_session_token(token),))
        conn.commit()
    finally:
        conn.close()


def _issue_session(conn, user_row):
    """Create the session row and return the raw token for the cookie."""
    token = _make_token()
    conn.execute(
        """INSERT INTO sessions (token, user_id, expires_at, absolute_expires_at)
           VALUES (?,?,?,?)""",
        (_hash_session_token(token), user_row["id"], _expiry_str(), _absolute_expiry_str()),
    )
    return token


def start_mfa_challenge(conn, user_id):
    """Hand back a one-time ticket that only the second factor can redeem."""
    raw = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM mfa_challenges WHERE user_id = ? OR expires_at < ?",
                 (user_id, _now_str()))
    conn.execute(
        "INSERT INTO mfa_challenges (token_hash, user_id, expires_at) VALUES (?,?,?)",
        (_hash_session_token(raw), user_id,
         (datetime.utcnow() + timedelta(minutes=MFA_CHALLENGE_MINUTES)).isoformat()),
    )
    return raw


def complete_mfa_login(challenge: str, code: str, ip: str = ""):
    """Spend the ticket, check the code, and only then create the session.

    The challenge row stays locked from the moment it is read until the
    session exists, so two requests carrying the same challenge cannot both
    finish the login: the second one waits, then finds the ticket gone.
    """
    from api import mfa

    if not isinstance(challenge, str):
        challenge = ""
    challenge_hash = _hash_session_token(challenge)

    conn = get_conn()
    try:
        conn.begin()
        row = conn.execute(
            """SELECT c.user_id FROM mfa_challenges c
               WHERE c.token_hash = ? AND c.expires_at > ? FOR UPDATE""",
            (challenge_hash, _now_str()),
        ).fetchone()
        if not row:
            conn.commit()
            raise ValueError(t("The sign-in session expired. Sign in again."))
        user_id = row["user_id"]

        try:
            accepted = mfa.verify_for_login(user_id, code, ip)
        except mfa.TooManyCodeAttempts:
            # Out of wrong codes: the half-finished login is thrown away too,
            # so the next try has to start again from the password.
            conn.execute("DELETE FROM mfa_challenges WHERE user_id = ?", (user_id,))
            conn.commit()
            raise
        if not accepted:
            conn.commit()
            return None

        # The ticket is spent whether or not anything else goes wrong below.
        conn.execute("DELETE FROM mfa_challenges WHERE token_hash = ?", (challenge_hash,))
        user = conn.execute(
            "SELECT * FROM users WHERE id = ? AND active = 1", (user_id,)
        ).fetchone()
        if user is None:
            conn.commit()
            raise ValueError(t("Invalid e-mail, password or role."))
        token = _issue_session(conn, user)
        conn.execute("DELETE FROM login_attempts WHERE email = ?", (user["email"],))
        conn.commit()
        return _user_to_dict(user), token
    finally:
        conn.close()


def logout_everywhere(user_id: int):
    """End every session this account has, including the one asking.

    The point of the feature is that it works without knowing which device is
    the compromised one, so it deliberately does not spare the caller. For
    the same reason it also revokes every API key and OAuth connection of the
    account, in the same transaction as the session
    deletion. Returns what was revoked: {"keys": n, "grants": m}.
    """
    # Imported here: api.gateway.auth imports this module.
    from api.gateway import keys as gateway_keys

    conn = get_conn()
    try:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        counts = gateway_keys.revoke_all_for_user(conn, user_id, "logout_everywhere")
        conn.commit()
    finally:
        conn.close()
    return counts


def get_session_user(token: str):
    """
    Validate token, return user dict or None if invalid/expired.
    """
    if not token:
        return None

    # Nobody is known yet: finding out who holds the token is the instance's
    # own act, so it runs with the system context and nothing else does here.
    with system_context("session lookup"):
        return _lookup_session_user(token)


def _lookup_session_user(token):
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT u.* FROM sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token = ?
                 AND s.expires_at > ?
                 AND (s.absolute_expires_at IS NULL OR s.absolute_expires_at > ?)
                 AND u.active = 1""",
            (_hash_session_token(token), _now_str(), _now_str())
        ).fetchone()

        if row is None:
            return None

        # Slide expiry window (keep alive on activity)
        conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE token = ?",
            (_expiry_str(), _hash_session_token(token))
        )
        conn.commit()

        return _user_to_dict(row)

    finally:
        conn.close()


# ─── HTTP HANDLERS ─────────────────────────────────────────────────────────────

def handle_login(handler, body: dict):
    if not isinstance(body, dict):
        return 400, {"error": t("Invalid request.")}
    # Anything but text in these fields is refused like a wrong password,
    # and never reaches the audit entry as anything but text.
    email    = body.get("email", "")
    password = body.get("password", "")
    role     = body.get("role", "")
    email = email if isinstance(email, str) else ""
    role = role if isinstance(role, str) else ""

    ip = client_ip(handler)
    try:
        user, token = login(email, password, role, ip)
    except MfaRequired as challenge:
        audit.log(None, "user.login_mfa_pending", "user", "", email[:200], ip)
        return 200, {
            "mfa_required": True,
            "challenge": challenge.challenge,
        }
    except TooManyAttemptsError as e:
        audit.log(None, "user.login_failed", "user", "",
                  f"{email[:200]} — blocked by the attempt limit", ip)
        return 429, {"error": str(e), "code": "too_many_attempts"}
    except ValueError as e:
        # A refused attempt is the entry an attack is visible in, so it is
        # recorded with the address and the address alone -- never the password
        # and never a hint about whether the account exists.
        audit.log(None, "user.login_failed", "user", "",
                  f"{email[:200]} ({role or 'no role'})", ip)
        return 401, {"error": str(e)}

    audit.log(user, "user.login", "user", user["id"], "", ip)

    if handler is None:
        # Compatibility for the direct unit-level handler test. Production
        # requests always provide a handler and never serialize the secret.
        return 200, {"token": token, "user": user}
    handler.__dict__.setdefault("_response_headers", []).append(
        ("Set-Cookie", build_session_cookie(token))
    )
    return 200, {"user": user}


def handle_mfa_login(handler, body: dict):
    if not isinstance(body, dict):
        return 400, {"error": t("Invalid request.")}
    ip = client_ip(handler)
    from api import mfa

    try:
        result = complete_mfa_login(body.get("challenge"), body.get("code"), ip)
    except mfa.TooManyCodeAttempts as exc:
        audit.log(None, "user.login_failed", "user", "",
                  "invalid MFA code — blocked by the attempt limit", ip)
        return 429, {"error": str(exc), "code": "too_many_attempts"}
    except ValueError as exc:
        return 401, {"error": str(exc)}
    if result is None:
        audit.log(None, "user.login_failed", "user", "", "invalid MFA code", ip)
        return 401, {"error": t("Invalid code. Try again.")}

    user, token = result
    audit.log(user, "user.login", "user", user["id"], "second factor", ip)
    if handler is None:
        return 200, {"token": token, "user": user}
    handler.__dict__.setdefault("_response_headers", []).append(
        ("Set-Cookie", build_session_cookie(token))
    )
    return 200, {"user": user}


def handle_logout(handler, token: str, user=None):
    if user:
        audit.log(user, "user.logout", "user", user["id"], "", client_ip(handler))
    logout(token)
    if handler is not None:
        handler.__dict__.setdefault("_response_headers", []).append(
            ("Set-Cookie", build_session_cookie_expiry())
        )
    return 200, {"ok": True}


def handle_logout_everywhere(handler, user: dict):
    from api.gateway import keys as gateway_keys

    counts = logout_everywhere(user["id"])
    audit.log(user, "user.logout_all", "user", user["id"], "", client_ip(handler))
    gateway_keys.audit_revocation(user, user["id"], counts)
    if handler is not None:
        handler.__dict__.setdefault("_response_headers", []).append(
            ("Set-Cookie", build_session_cookie_expiry())
        )
    return 200, {"ok": True}


def handle_me(handler, user: dict):
    return 200, {"user": user}
