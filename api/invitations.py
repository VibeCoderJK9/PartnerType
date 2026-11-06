"""
PartnerType -- Partner Invitation Module

"A password? Am I supposed to give the partner a password? No, I send him an
invitation by e-mail and he registers himself" (a vendor, in user testing) --
the producer supplies an e-mail address
alone; the invited partner sets their own password.

The database stores only a sha256 hash of the one-time secret, never the
secret itself (partner_invitations.token_hash, api/db.py). A database read
therefore cannot be replayed into an account takeover.
"""
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote

import bcrypt

from api import audit
from api import email_service
from api import notifications as notif
from api import users
from api.authz import is_staff
from api.db import get_conn
from api.validation import (
    parse_allowed_fields,
    parse_email,
    parse_enum,
    parse_optional_string,
    parse_password,
    parse_required_string,
)
from api.i18n import N_, t

logger = logging.getLogger(__name__)

# No "password" and no "account_status": the producer supplies
# neither.
INVITE_FIELDS = ("name", "email", "company", "role", "level", "phone", "bio", "tier_id")
INVITATION_TTL_DAYS = 7
_DEFAULT_BASE_URL = "http://localhost:8000"
_INVITATION_INVALID_MESSAGE = (
    N_("The invitation is invalid, has expired or has already been used.")
)
_INVITE_ROLE_FORBIDDEN_MESSAGE = N_("A channel manager can invite partners only.")


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _validate_invite_fields(data, require_company=True):
    """Shared field validation for both invite doors.

    ``require_company`` defaults to True for the partner path: a
    partner genuinely belongs to some other company, so the producer must
    name it. The team path passes False -- a channel manager is by
    definition a person from the producer's own firm, and the acting
    administrator's own company is derived server-side (see
    handle_create_team_invite) and may legitimately be empty, as it is for
    an owner created by tools/bootstrap.py. parse_required_string would
    reject that empty value with "is required", which is why the team
    path uses parse_optional_string instead.
    """
    values = parse_allowed_fields(data, INVITE_FIELDS, N_("Invitation data"))
    company_value = values.get("company")
    if require_company:
        company = parse_required_string(company_value, "company", max_length=200)
    else:
        company = parse_optional_string(company_value, "company", max_length=200)
    return {
        "name": parse_required_string(values.get("name"), "name", max_length=200),
        "email": parse_email(values.get("email"), "email").lower(),
        "company": company,
        "phone": parse_optional_string(values.get("phone"), "phone", max_length=50),
        "bio": parse_optional_string(values.get("bio"), "bio", max_length=2000),
        "role": parse_enum(values.get("role", "partner"), "role", users.VALID_ROLES),
        "level": parse_enum(values.get("level", "bronze"), "level", users.VALID_LEVELS),
        # The commission tier the producer picked in the invite form. Optional
        # -- users.create_user validates the id and falls back to the legacy
        # `level` word when nothing was picked.
        "tier_id": values.get("tier_id"),
    }


def build_invite_link(raw_token: str) -> str:
    """Return the absolute set-password link carrying the raw secret.

    Reads only from the PARTNERTYPE_BASE_URL environment variable, never from
    the request's Host header: an attacker able to reach the invite endpoint
    could otherwise plant a link pointing at their own host and harvest the
    secret when the partner follows it.
    """
    base = os.environ.get("PARTNERTYPE_BASE_URL", "").strip()
    if not base or not (base.startswith("http://") or base.startswith("https://")):
        base = _DEFAULT_BASE_URL
    base = base.rstrip("/")
    return f"{base}/set-password.html?token={quote(raw_token, safe='')}"


# ─── CORE ─────────────────────────────────────────────────────────────────────

def create_invitation(data: dict, acting_user: dict, require_company: bool = True):
    """Create -- or re-invite -- a pending partner and one fresh invitation row.

    Calls the canonical api.users.create_user for a brand-new address:
    e-mail uniqueness, bcrypt hashing, tier resolution and the public
    projection all already live there and must not be duplicated here. If
    the address already belongs to a pending, not-yet-accepted account (an
    earlier invitation that was never redeemed), that account is reused
    instead of failing on create_user's duplicate-email check, and its
    previous invitation rows are invalidated.

    ``require_company`` is forwarded to ``_validate_invite_fields`` --
    handle_create_team_invite passes False; every other caller keeps
    the default True (the partner path).
    """
    if not is_staff(acting_user):
        raise PermissionError(t("Only an administrator can invite partners."))

    fields = _validate_invite_fields(data, require_company=require_company)

    # is_staff admits the caller, but a non-administrator staff
    # member (channel manager) may only ever request a partner invitation through
    # this door. Without this, a manager session could post role=admin (or
    # role=manager) to the partner-invite route and mint a pending
    # administrator -- or a second channel manager -- bypassing the dedicated
    # admin-only team-invite door entirely. Read the acting
    # role defensively so a caller dict lacking the key is refused rather
    # than crashing. This must run before the first get_conn() call below
    # so a refused attempt writes no users row, no partner_invitations row
    # and mints no token.
    if acting_user.get("role") != "admin" and fields["role"] != "partner":
        raise PermissionError(t(_INVITE_ROLE_FORBIDDEN_MESSAGE))

    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT * FROM users WHERE email = ?", (fields["email"],)
        ).fetchone()
    finally:
        conn.close()

    if existing and existing["account_status"] != "pending":
        raise ValueError(t("A user with this e-mail already exists."))

    # A pending account is reused only for the role it was created with.
    # Re-inviting it under any other role would hand a fresh link for, say, a
    # pending administrator to whoever may invite partners, and accepting that
    # link activates the account with its original role. Together with the
    # role check above, this means a non-administrator can only ever reuse a
    # pending partner.
    if existing and existing["role"] != fields["role"]:
        raise ValueError(t("This e-mail address already belongs to another pending account."))

    if existing:
        user = users.get_user(existing["id"])
    else:
        create_payload = dict(fields)
        # Two independently generated random strings: the
        # producer types neither. The password here only exists to satisfy
        # create_user's contract and is discarded immediately -- nobody
        # reads it, nobody stores it anywhere else, and it never leaves this
        # function.
        create_payload["password"] = secrets.token_urlsafe(32)
        create_payload["account_status"] = "pending"
        user = users.create_user(create_payload, require_company=require_company)
        audit.log(acting_user, "user.create", "user", str(user["id"]),
                  "invitation: role=%s" % user["role"])

    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    expires_at = (datetime.utcnow() + timedelta(days=INVITATION_TTL_DAYS)).isoformat()

    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM partner_invitations WHERE user_id = ? AND accepted_at IS NULL",
            (user["id"],),
        )
        conn.execute(
            """INSERT INTO partner_invitations (token_hash, user_id, created_by, expires_at)
               VALUES (?,?,?,?)""",
            (token_hash, user["id"], acting_user["id"], expires_at),
        )
        conn.commit()
    finally:
        conn.close()

    return user, raw_token


def get_invitation(raw_token):
    """Return {"name", "email"} for a fresh secret, or None for every failure mode.

    Unknown, expired and already-accepted tokens are indistinguishable to
    the caller -- none of the three is an existence oracle for whether an
    e-mail address was ever invited.
    """
    if not isinstance(raw_token, str) or not raw_token:
        return None
    token_hash = _hash_token(raw_token)
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT u.name AS name, u.email AS email
               FROM partner_invitations i
               JOIN users u ON u.id = i.user_id
               WHERE i.token_hash = ?
                 AND i.accepted_at IS NULL
                 AND i.expires_at > ?
                 AND u.account_status IN ('pending', 'active')""",
            (token_hash, datetime.utcnow().isoformat()),
        ).fetchone()
        return {"name": row["name"], "email": row["email"]} if row else None
    finally:
        conn.close()


def accept_invitation(raw_token, password):
    """Redeem a fresh, single-use secret by setting the invitee's own password.

    All of it happens in one connection, committed once: the password hash,
    the active status, the invitation's accepted timestamp and the session
    wipe are one atomic transition.

    Only an account still waiting for its invitee (pending) or one the
    producer has already approved (active) can be redeemed. A suspended,
    rejected or erased account stays closed: the link must not reopen it.
    """
    if not isinstance(raw_token, str) or not raw_token:
        raise ValueError(t(_INVITATION_INVALID_MESSAGE))
    token_hash = _hash_token(raw_token)
    # Imported here: api.gateway pulls in api.auth, which this module's
    # importers may still be loading.
    from api.gateway import keys as gateway_keys

    conn = get_conn()
    try:
        conn.begin()
        row = conn.execute(
            """SELECT i.id AS invitation_id, i.user_id, i.created_by,
                      u.name AS name, u.email AS email, u.role AS role
               FROM partner_invitations i
               JOIN users u ON u.id = i.user_id
               WHERE i.token_hash = ?
                 AND i.accepted_at IS NULL
                 AND i.expires_at > ?
                 AND u.account_status IN ('pending', 'active') FOR UPDATE OF i""",
            (token_hash, datetime.utcnow().isoformat()),
        ).fetchone()
        if not row:
            raise ValueError(t(_INVITATION_INVALID_MESSAGE))

        password = parse_password(password, N_("Password"), min_length=8, max_length=72)

        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        now = datetime.utcnow().isoformat()
        conn.execute(
            "UPDATE users SET password = ?, account_status = 'active', active = 1 WHERE id = ?",
            (hashed, row["user_id"]),
        )
        conn.execute(
            "UPDATE partner_invitations SET accepted_at = ? WHERE id = ?",
            (now, row["invitation_id"]),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
        # Like a password reset: the account changes hands, so no key or OAuth
        # connection minted before survives it.
        counts = gateway_keys.revoke_all_for_user(conn, row["user_id"], "invitation_accepted")
        conn.commit()
        created_by, name, email = row["created_by"], row["name"], row["email"]
        user_id, role = row["user_id"], row["role"]
    finally:
        conn.close()

    # Written as the invitee: they are the one who set the password.
    invitee = {"id": user_id, "name": name, "role": role}
    audit.log(invitee, "user.invitation_accepted", "user", str(user_id),
              "invitation from user %s" % (created_by or "—"))
    gateway_keys.audit_revocation(invitee, user_id, counts)

    # Let the inviting administrator learn their invitation was accepted.
    # Failure-isolated: this runs
    # after the acceptance's own connection is committed and closed, wrapped
    # in its own try/except that logs only the exception class name, so a
    # notification failure can never turn a successful acceptance into a
    # failed one.
    if created_by:
        try:
            notif.create_notification(
                created_by,
                "success",
                t("The invited partner activated the account"),
                t("{name} ({email}) set a password and can sign in now.", name=name, email=email),
                "users.html",
                title_key="ntf_invite_redeemed_title",
                body_key="ntf_invite_redeemed_body",
                params={"name": name, "email": email},
            )
        except Exception as exc:
            logger.error(
                "invitation_acceptance_notification_failed exception=%s",
                type(exc).__name__,
            )


# ─── HTTP HANDLERS ─────────────────────────────────────────────────────────────

def handle_create(handler, user: dict, body: dict):
    try:
        new_user, raw_token = create_invitation(body, user)
    except PermissionError as e:
        return 403, {"error": str(e)}
    except ValueError as e:
        return 400, {"error": str(e)}

    # The link is returned unconditionally, on both mail outcomes -- the
    # producer may want to pass it on even when mail worked, and the
    # fresh-install case where SMTP is unconfigured (smtp_enabled='0',
    # smtp_host='') is exactly why:
    # the invitation must still be usable without mail.
    invite_url = build_invite_link(raw_token)
    mail_result = email_service.send_partner_invitation(
        new_user["email"], new_user["name"], user.get("name", ""), invite_url
    )
    response = {
        "user": new_user,
        "invite_url": invite_url,
        "sent": bool(mail_result.get("sent")),
    }
    if not mail_result.get("sent") and mail_result.get("reason"):
        response["reason"] = mail_result["reason"]
    return 201, response


def handle_create_team_invite(handler, user: dict, body: dict):
    """The Team tab's own entry point -- its own path and its
    own place in the interface, sharing every piece of the invitation
    machinery above without sharing its screen.

    This inline administrator-only comparison is deliberate and required:
    a channel manager cannot mint another one -- and is why this module
    still carries exactly one inline admin-only guard after the
    partner-invite guard above was widened to the shared staff predicate.
    api/tests/unit/test_authz_sweep.py's allowlist depends on this residual
    guard staying exactly this shape; do not convert it.
    """
    if not user or user.get("role") != "admin":
        return 403, {"error": t("Only an administrator can invite the team.")}

    body = dict(body)
    # Server-validated, never server-trusted: the client may ask for one of
    # the two team roles (moderator / observer), and
    # anything else --- "admin" above all --- is refused here rather than
    # reaching create_invitation. Defaulting to "manager" keeps every caller
    # written before observers existed working unchanged.
    requested_role = body.get("role") or "manager"
    if requested_role not in users.TEAM_ROLES:
        return 400, {
            "error": t("The role must be one of: Moderator, Observer.")
        }
    body["role"] = requested_role
    # The invitee is by definition a person from the producer's own
    # firm, so this door must never ask (or trust) a company from the
    # client -- overwrite whatever the caller sent, the same way role is
    # forced above. Derived from the acting administrator's own account,
    # tolerating an empty acting company: an owner created by
    # tools/bootstrap.py holds company == '', and _validate_invite_fields is told
    # not to require one on this path (see require_company below).
    body["company"] = user.get("company") or ""

    try:
        email = parse_email(body.get("email"), "email").lower()
    except ValueError as e:
        return 400, {"error": str(e)}

    # create_invitation's own pending-account reuse branch only checks
    # account_status, not role -- letting it run across roles here would
    # silently convert a person invited as a partner into a member of the
    # producer's staff (or vice-versa). Refuse before it ever runs.
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT role, account_status FROM users WHERE email = ?", (email,)
        ).fetchone()
    finally:
        conn.close()
    if (
        existing
        and existing["account_status"] == "pending"
        and existing["role"] not in users.TEAM_ROLES
    ):
        return 400, {
            "error": t("This e-mail address already belongs to another pending account.")
        }

    try:
        new_user, raw_token = create_invitation(body, user, require_company=False)
    except PermissionError as e:
        return 403, {"error": str(e)}
    except ValueError as e:
        return 400, {"error": str(e)}

    invite_url = build_invite_link(raw_token)
    mail_result = email_service.send_team_invitation(
        new_user["email"], new_user["name"], user.get("name", ""), invite_url
    )
    response = {
        "user": new_user,
        "invite_url": invite_url,
        "sent": bool(mail_result.get("sent")),
    }
    if not mail_result.get("sent") and mail_result.get("reason"):
        response["reason"] = mail_result["reason"]
    return 201, response


def handle_preview(handler, token: str):
    invite = get_invitation(token)
    if not invite:
        return 404, {"error": t(_INVITATION_INVALID_MESSAGE)}
    return 200, {"invitation": invite}


def handle_accept(handler, token: str, body: dict):
    try:
        body = parse_allowed_fields(body, ("password",), N_("Invitation acceptance data"))
        accept_invitation(token, body.get("password"))
    except ValueError as e:
        status = 404 if str(e) == t(_INVITATION_INVALID_MESSAGE) else 400
        return status, {"error": str(e)}
    return 200, {"ok": True, "message": t("The password was set. You can sign in now.")}
