"""Second factor for the producer's own accounts.

Codes come from an authenticator app (RFC 6238), not from e-mail: the password
reset already travels by e-mail, so a mailbox in someone else's hands would
otherwise hand over both factors at once. Nothing here talks to an outside
service, so no new processor appears in the data-processing agreement.
"""
import base64
import hashlib
import hmac
import io
import os
import secrets
from datetime import datetime, timezone

import pyotp
import segno
from cryptography.fernet import Fernet, InvalidToken

from api import audit
from api.authz import can_manage_users
from api.config import secret as config_secret
from api.db import get_conn
from api.i18n import N_, t

ISSUER = "PartnerType"
BACKUP_CODE_COUNT = 8
# Roles that see every lead, every partner and every amount. A partner sees
# only their own work, so for them this stays voluntary -- requiring an
# authenticator app to join a partner programme costs more sign-ups than it
# prevents incidents.
MFA_REQUIRED_ROLES = ("admin", "manager", "observer")

_INVALID_CODE = N_("Invalid code. Try again.")
_TOO_MANY_CODES = N_("Too many failed code attempts. Try again in a quarter of an hour.")

# Wrong codes allowed per account inside the window before every code, right
# or wrong, is refused until the window has passed. Six digits leave a
# million combinations, so without a limit anybody who knows the password
# could simply try them all.
MFA_MAX_FAILURES = 5
MFA_WINDOW_MINUTES = 15  # never above the one-day sweep of login_attempts in api/db.py


class TooManyCodeAttempts(ValueError):
    """Too many wrong second-factor codes for this account recently."""

    def __init__(self):
        super().__init__(t(_TOO_MANY_CODES))


def _cipher():
    """Key derived from the instance session secret, never stored separately.

    Rotating that secret makes existing enrolments unreadable, which logs
    people out of their second factor rather than silently accepting anything.
    """
    material = config_secret("PARTNERTYPE_SESSION_SECRET", True).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().replace("-", "").upper().encode()).hexdigest()


def enforcement_enabled() -> bool:
    """Whether a missing second factor actually blocks a staff account's way in.

    This is the producer's own decision, stored as `mfa_required_for_staff`
    and off by default, so the account owner can set up the factor on their
    own phone first and is never locked out of turning it off.

    The environment variable still wins, and is what the gate's own tests set;
    it also gives an operator a way to lift the requirement on an instance
    whose settings table cannot be reached.
    """
    override = os.environ.get("PARTNERTYPE_REQUIRE_MFA", "").strip().lower()
    if override in ("0", "false", "no"):
        return False
    if override in ("1", "true", "yes"):
        return True
    from api import settings_api  # imported here to avoid a circular import

    return settings_api.mfa_required_for_staff()


def is_required_for(role: str) -> bool:
    return role in MFA_REQUIRED_ROLES and enforcement_enabled()


def status_for(user_id: int):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT confirmed_at FROM user_mfa WHERE user_id = ?", (user_id,)
        ).fetchone()
        unused = conn.execute(
            """SELECT COUNT(*) AS count FROM user_mfa_backup_codes
               WHERE user_id = ? AND used_at IS NULL""",
            (user_id,),
        ).fetchone()["count"]
    finally:
        conn.close()
    return {
        "enabled": bool(row and row["confirmed_at"]),
        "pending": bool(row and not row["confirmed_at"]),
        "backup_codes_left": unused,
    }


def is_enabled(user_id: int) -> bool:
    return status_for(user_id)["enabled"]


def begin_enrolment(user):
    """Issue a fresh secret and the QR that carries it.

    Re-enrolling replaces any unconfirmed attempt: somebody who lost the phone
    mid-setup must be able to start over without an administrator.
    """
    raw_secret = pyotp.random_base32()
    stored = _cipher().encrypt(raw_secret.encode()).decode()
    conn = get_conn()
    try:
        conn.begin()
        existing = conn.execute(
            "SELECT confirmed_at FROM user_mfa WHERE user_id = ? FOR UPDATE",
            (user["id"],),
        ).fetchone()
        if existing and existing["confirmed_at"]:
            raise ValueError(t("The second factor is already on."))
        if existing:
            conn.execute(
                "UPDATE user_mfa SET secret = ?, created_at = ? WHERE user_id = ?",
                (stored, datetime.utcnow().isoformat(), user["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO user_mfa (user_id, secret) VALUES (?,?)",
                (user["id"], stored),
            )
        conn.commit()
    finally:
        conn.close()

    uri = pyotp.TOTP(raw_secret).provisioning_uri(name=user["email"], issuer_name=ISSUER)
    buffer = io.BytesIO()
    segno.make(uri, error="m").save(buffer, kind="svg", scale=4, dark="#111827")
    return {
        # The secret in text form too: some people type it into a password
        # manager instead of pointing a camera at the screen.
        "secret": raw_secret,
        "qr_svg": buffer.getvalue().decode(),
    }


def _matching_steps(raw_secret: str, code) -> list:
    """The time steps whose code equals the one typed, oldest first.

    One step of tolerance either way: phone clocks drift, and a person typing
    six digits at the turn of a 30-second window is not an attacker.
    """
    if not isinstance(code, str):
        return []
    typed = code.strip().replace(" ", "")
    totp = pyotp.TOTP(raw_secret)
    current = totp.timecode(datetime.now(timezone.utc))
    return [step for step in (current - 1, current, current + 1)
            if hmac.compare_digest(totp.generate_otp(step), typed)]


def _verify_code(raw_secret: str, code: str) -> bool:
    return bool(_matching_steps(raw_secret, code))


def _spend_app_code(conn, user_id: int, raw_secret: str, code) -> bool:
    """Accept an app code at most once.

    The step of the accepted code is stored, and a code from that step or an
    earlier one is refused afterwards, so a code somebody saw being typed
    cannot be used again while it is still within its ninety seconds. The
    conditional update makes two simultaneous uses of one code accept only
    one of them.
    """
    for step in _matching_steps(raw_secret, code):
        row = conn.execute(
            """UPDATE user_mfa SET last_used_step = ?
               WHERE user_id = ? AND confirmed_at IS NOT NULL
                 AND (last_used_step IS NULL OR last_used_step < ?)
               RETURNING user_id""",
            (step, user_id, step),
        ).fetchone()
        conn.commit()
        if row:
            return True
    return False


def _secret_for(conn, user_id, *, confirmed):
    row = conn.execute(
        "SELECT secret, confirmed_at FROM user_mfa WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row:
        return None
    if confirmed and not row["confirmed_at"]:
        return None
    if not confirmed and row["confirmed_at"]:
        return None
    try:
        return _cipher().decrypt(row["secret"].encode()).decode()
    except InvalidToken:
        return None


def _replace_backup_codes(conn, user_id: int):
    """Throw away whatever codes exist and hand back a fresh printed set.

    Inside the caller's transaction on purpose: a half-applied swap would leave
    somebody holding codes the database no longer knows, which is the same as
    holding nothing at the moment they need them.
    """
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}".upper()
             for _ in range(BACKUP_CODE_COUNT)]
    conn.execute("DELETE FROM user_mfa_backup_codes WHERE user_id = ?", (user_id,))
    for code_value in codes:
        conn.execute(
            "INSERT INTO user_mfa_backup_codes (user_id, code_hash) VALUES (?,?)",
            (user_id, _hash_code(code_value)),
        )
    return codes


def confirm_enrolment(user, code: str):
    """Prove the app works before the factor starts guarding the account."""
    conn = get_conn()
    try:
        raw_secret = _secret_for(conn, user["id"], confirmed=False)
        if not raw_secret:
            raise ValueError(t("Start setting up the second factor first."))
        if not _verify_code(raw_secret, code):
            raise ValueError(t(_INVALID_CODE))

        conn.begin()
        conn.execute(
            "UPDATE user_mfa SET confirmed_at = ? WHERE user_id = ?",
            (datetime.utcnow().isoformat(), user["id"]),
        )
        codes = _replace_backup_codes(conn, user["id"])
        conn.commit()
    finally:
        conn.close()
    audit.log(user, "user.mfa_enabled", "user", user["id"])
    # Shown exactly once. There is no way to read them back afterwards.
    return codes


def _failure_key(user_id: int) -> str:
    """The login_attempts key wrong codes are counted under.

    It can never be an e-mail address, so the password limiter never mixes the
    two up, and a correct password, which clears that account's password
    failures, leaves the wrong codes counted.
    """
    return f"#mfa:{int(user_id)}"


def _recent_failures(conn, user_id: int) -> int:
    return conn.execute(
        f"""SELECT COUNT(*) AS n FROM login_attempts
            WHERE email = ? AND succeeded = false
              AND created_at >= now() - INTERVAL '{MFA_WINDOW_MINUTES} minutes'""",
        (_failure_key(user_id),),
    ).fetchone()["n"]


def _accept_code(conn, user_id: int, code) -> bool:
    """Accept either a current app code or one unused backup code."""
    if not isinstance(code, str):
        return False
    raw_secret = _secret_for(conn, user_id, confirmed=True)
    if raw_secret and _spend_app_code(conn, user_id, raw_secret, code):
        return True

    conn.begin()
    row = conn.execute(
        """SELECT id FROM user_mfa_backup_codes
           WHERE user_id = ? AND code_hash = ? AND used_at IS NULL
           FOR UPDATE""",
        (user_id, _hash_code(code)),
    ).fetchone()
    if not row:
        conn.commit()
        return False
    conn.execute(
        "UPDATE user_mfa_backup_codes SET used_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), row["id"]),
    )
    conn.commit()
    return True


def verify_for_login(user_id: int, code: str, ip: str = "") -> bool:
    """Check a second-factor code, counting the wrong ones.

    Every place that takes a code (finishing a login, switching the factor
    off, a new set of backup codes) goes through here, so they share one
    limit. Raises TooManyCodeAttempts once the account has used up its wrong
    codes for the window, including on the attempt that uses up the last one.
    """
    conn = get_conn()
    try:
        if _recent_failures(conn, user_id) >= MFA_MAX_FAILURES:
            raise TooManyCodeAttempts()
        if _accept_code(conn, user_id, code):
            conn.execute("DELETE FROM login_attempts WHERE email = ?", (_failure_key(user_id),))
            conn.commit()
            return True
        conn.execute(
            "INSERT INTO login_attempts (email, ip, succeeded) VALUES (?,?,?)",
            (_failure_key(user_id), (ip or "")[:64], False),
        )
        conn.commit()
        if _recent_failures(conn, user_id) >= MFA_MAX_FAILURES:
            raise TooManyCodeAttempts()
        return False
    finally:
        conn.close()


def regenerate_backup_codes(user, code: str):
    """A fresh printed set, without taking the factor off and putting it back.

    Eight codes run out -- each one is spent the moment it is used -- and
    disabling the factor just to enrol again is a strange thing to ask of
    somebody whose phone still works.
    Proving a current code first is what keeps this from being a way for a
    stolen session to mint itself a permanent way back in.
    """
    if not verify_for_login(user["id"], code):
        raise ValueError(t(_INVALID_CODE))
    conn = get_conn()
    try:
        conn.begin()
        if not conn.execute(
            "SELECT 1 FROM user_mfa WHERE user_id = ? AND confirmed_at IS NOT NULL",
            (user["id"],),
        ).fetchone():
            raise ValueError(t("The second factor is not on."))
        codes = _replace_backup_codes(conn, user["id"])
        conn.commit()
    finally:
        conn.close()
    audit.log(user, "user.mfa_codes_regenerated", "user", user["id"])
    return codes


def reset_for(actor, target_user_id: int):
    """Take somebody else's second factor off, so they can set a new one up.

    For the person who lost the phone AND the printed codes -- until now that
    was an account nobody could reopen without a hand in the database. Only the
    producer may do it, and never to his own account: a session in the wrong
    hands would otherwise strip the factor with one click, which is exactly what
    the factor exists to prevent. The producer's own lockout is recovered from
    the server's shell instead (tools/instance.py mfa-reset).
    """
    # Through the shared predicate, not an inline role comparison: authorization
    # lives in api/authz.py so a widened guard is visible in one
    # place rather than hidden in a module that happens to need it.
    if not can_manage_users(actor):
        raise PermissionError(t("Only the owner can reset the second factor."))
    if int(actor["id"]) == int(target_user_id):
        raise PermissionError(
            t("You cannot reset your own second factor here. Use the command on the server."))
    conn = get_conn()
    try:
        conn.begin()
        row = conn.execute("SELECT id, email FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if not row:
            raise ValueError(t("There is no such user."))
        conn.execute("DELETE FROM user_mfa WHERE user_id = ?", (target_user_id,))
        conn.execute("DELETE FROM user_mfa_backup_codes WHERE user_id = ?", (target_user_id,))
        conn.commit()
        email = row["email"]
    finally:
        conn.close()
    audit.log(actor, "user.mfa_reset", "user", str(target_user_id), email)


def disable(user, code: str):
    """Turning the factor off requires proving you still hold it."""
    if not verify_for_login(user["id"], code):
        raise ValueError(t(_INVALID_CODE))
    conn = get_conn()
    try:
        conn.begin()
        conn.execute("DELETE FROM user_mfa WHERE user_id = ?", (user["id"],))
        conn.execute("DELETE FROM user_mfa_backup_codes WHERE user_id = ?", (user["id"],))
        conn.commit()
    finally:
        conn.close()
    audit.log(user, "user.mfa_disabled", "user", user["id"])


# ─── HTTP HANDLERS ────────────────────────────────────────────────────────────

def handle_status(handler, user):
    status = status_for(user["id"])
    status["required"] = is_required_for(user["role"])
    return 200, status


def handle_begin(handler, user):
    try:
        return 200, begin_enrolment(user)
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_confirm(handler, user, body):
    if not isinstance(body, dict):
        return 400, {"error": t("Invalid request.")}
    try:
        return 200, {"backup_codes": confirm_enrolment(user, body.get("code"))}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_backup_codes(handler, user, body):
    if not isinstance(body, dict):
        return 400, {"error": t("Invalid request.")}
    try:
        return 200, {"backup_codes": regenerate_backup_codes(user, body.get("code"))}
    except TooManyCodeAttempts as exc:
        return 429, {"error": str(exc), "code": "too_many_attempts"}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_reset(handler, user, user_id):
    try:
        reset_for(user, int(user_id))
    except PermissionError as exc:
        return 403, {"error": str(exc)}
    except (ValueError, TypeError) as exc:
        return 400, {"error": str(exc)}
    return 200, {"ok": True}


def handle_disable(handler, user, body):
    if not isinstance(body, dict):
        return 400, {"error": t("Invalid request.")}
    if is_required_for(user["role"]):
        return 403, {"error": t("This role requires the second factor.")}
    try:
        disable(user, body.get("code"))
    except TooManyCodeAttempts as exc:
        return 429, {"error": str(exc), "code": "too_many_attempts"}
    except ValueError as exc:
        return 400, {"error": str(exc)}
    return 200, {"ok": True}
