"""
PartnerType — Users Module
User management: list, create, activate/suspend, profile update, password change.
"""

import logging
import os

import bcrypt
from datetime import datetime
from api.authz import can_manage_users, is_staff
from api.db import get_conn
from api.http_security import client_ip
from api import audit
from api import notifications as notif
from api import registration_fields
from api.validation import (
    parse_allowed_fields,
    parse_email,
    parse_enum,
    parse_integer_id,
    parse_optional_string,
    parse_password,
    parse_required_string,
)
from api.i18n import N_, t

logger = logging.getLogger(__name__)

VALID_LEVELS  = ("bronze", "silver", "gold", "platinum")
VALID_STATUS  = ("active", "pending", "suspended", "rejected")
VALID_ROLES   = ("admin", "partner", "manager", "observer")

# Roles a producer can hand to somebody on their own team. "partner" is the
# other side of the program and is never granted from the team screen;
# "admin" is granted only by an existing admin, guarded separately.
TEAM_ROLES    = ("manager", "observer")

# `tier_id` / `tier_name` / `tier_color` / `tier_name_en` appear only for
# callers whose query joins commission_tiers (get_user below); _user_pub
# projects what the row actually carries, so the team roster and the user
# list are unaffected. tier_name_en is the producer's own English wording of
# the tier -- Ui.producerLabel(user, 'tier_name') reads it.
_PUBLIC_USER_FIELDS = (
    "id", "email", "name", "role", "company", "level", "phone", "bio",
    "account_status", "active", "created_at", "rejected_at",
    "tier_id", "tier_name", "tier_color", "tier_name_en",
)
_CREATE_FIELDS = ("name", "email", "password", "company", "role", "level", "phone", "bio", "account_status", "tier_id")
_REGISTER_FIELDS = (
    "name", "email", "password", "company", "phone", "bio", "custom_fields",
)
# The four the producer always needs. `phone` is required so a producer can
# call back a partner who mistyped their address.
_REGISTER_REQUIRED = ("name", "email", "company", "phone")
_UPDATE_FIELDS = ("name", "company", "phone", "bio", "level", "account_status", "role")
_PROFILE_COLUMNS = {
    "name": "name",
    "company": "company",
    "phone": "phone",
    "bio": "bio",
}


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _user_pub(row):
    """Project a database row onto the code-owned public user shape."""
    values = dict(row)
    return {field: values[field] for field in _PUBLIC_USER_FIELDS if field in values}


def _now():
    return datetime.utcnow().isoformat()


def _validate_user_create(data, registration=False, require_company=True):
    """``require_company`` is False only for the Team-tab invite path
    (api/invitations.py's create_invitation): a channel manager is by
    definition from the producer's own firm, and the acting administrator's
    own company -- derived server-side, never client-supplied here -- may
    legitimately be empty. Every other caller keeps the default True.
    """
    allowed = _REGISTER_FIELDS if registration else _CREATE_FIELDS
    values = parse_allowed_fields(data, allowed, N_("User data"))
    company_value = values.get("company")
    if require_company:
        company = parse_required_string(company_value, N_("Company"), max_length=200)
    else:
        company = parse_optional_string(company_value, N_("Company"), max_length=200)
    normalized = {
        "name": parse_required_string(values.get("name"), N_("Full name"), max_length=200),
        "email": parse_email(values.get("email"), N_("E-mail")).lower(),
        "password": parse_password(values.get("password"), N_("Password"), max_length=72),
        "company": company,
        "phone": parse_optional_string(values.get("phone"), N_("Phone"), max_length=50),
        "bio": parse_optional_string(values.get("bio"), "bio", max_length=2000),
    }
    if registration:
        # Name, company, e-mail and phone are the four a producer always
        # needs. The parsers above already require three of them; stating all
        # four keeps the rule readable in one place. The message uses human
        # field names, never the technical key.
        labels = {"name": N_("Full name"), "email": N_("E-mail"),
                  "company": N_("Company"), "phone": N_("Phone")}
        for field in _REGISTER_REQUIRED:
            if not normalized.get(field):
                raise ValueError(t("Field '{field}' is required.", field=t(labels[field])))
        # Carried through untouched: registration_fields.save_answers is what
        # validates each answer, against the length its own field declares.
        normalized["custom_fields"] = values.get("custom_fields") or {}
    if not registration:
        normalized["role"] = parse_enum(values.get("role", "partner"), "role", VALID_ROLES)
        normalized["level"] = parse_enum(values.get("level", "bronze"), "level", VALID_LEVELS)
        # The tier the producer picked in the invite form. Optional: an absent
        # or empty value means "no tier yet", and create_user then falls back
        # to resolving the legacy `level` word. Existence is checked there,
        # against the database; here only the shape.
        tier_value = values.get("tier_id")
        normalized["tier_id"] = (
            parse_integer_id(tier_value, "tier_id")
            if tier_value not in (None, "")
            else None
        )
        normalized["account_status"] = parse_enum(
            values.get("account_status", "active"), "account_status", VALID_STATUS
        )
    return normalized


def _validate_user_update(data):
    """Validate the SHAPE of an update payload, with no knowledge of who is
    asking. Privilege filtering happens separately in update_user(), once the
    target row says whether the acting user may touch it.

    Splitting the two is what keeps the "malformed input never reaches the
    database" guarantee (TestUpdateUser::
    test_invalid_admin_update_fails_before_connection): an unknown field, a
    2001-character bio or a role of "superadmin" is rejected here, before any
    connection is opened, regardless of the caller's role."""
    values = parse_allowed_fields(data, _UPDATE_FIELDS, N_("Update data"))
    normalized = {}
    if "name" in values and values["name"] is not None:
        normalized["name"] = parse_required_string(values["name"], "name", max_length=200)
    for field, maximum in (("company", 200), ("phone", 50), ("bio", 2000)):
        if field in values and values[field] is not None:
            normalized[field] = parse_optional_string(values[field], field, max_length=maximum)
    if "level" in values:
        normalized["level"] = parse_enum(values["level"], "level", VALID_LEVELS)
    if "account_status" in values:
        normalized["account_status"] = parse_enum(
            values["account_status"], "account_status", VALID_STATUS
        )
    if "role" in values:
        normalized["role"] = parse_enum(values["role"], "role", VALID_ROLES)
    if not normalized:
        raise ValueError(t("No data to update."))
    return normalized


# ─── LIST ─────────────────────────────────────────────────────────────────────

def list_users(role_filter=None, status_filter=None, q=None):
    conn = get_conn()
    try:
        where, params = [], []
        if role_filter:
            where.append("role = ?"); params.append(role_filter)
        if status_filter:
            where.append("account_status = ?"); params.append(status_filter)
        if q:
            qp = f"%{q}%"
            where.append("(name ILIKE ? OR email ILIKE ? OR company ILIKE ?)")
            params.extend([qp, qp, qp])

        sql = "SELECT * FROM users"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC"

        rows = conn.execute(sql, params).fetchall()
        return [_user_pub(r) for r in rows]
    finally:
        conn.close()


# ─── TEAM ROSTER ───────────────────────────────────────────

def list_team():
    """Every account in the producer's company: administrators, moderators
    and observers together, partners never. Ordered administrators
    first, then moderators, then observers; within a role by name compared
    case-insensitively, then id -- the id tiebreak is what keeps two
    identically named team members in a fixed order across reloads."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT * FROM users
               WHERE role IN ('admin', 'manager', 'observer')
               ORDER BY CASE role
                          WHEN 'admin'   THEN 0
                          WHEN 'manager' THEN 1
                          ELSE 2
                        END,
                        LOWER(name), id"""
        ).fetchall()
        return [_user_pub(r) for r in rows]
    finally:
        conn.close()


# ─── GET ONE ──────────────────────────────────────────────────────────────────

def get_user(user_id):
    conn = get_conn()
    try:
        # The commission tier comes along with the account: the profile screen
        # names the partner's tier from `tier_name`, never from the legacy
        # `level` word (same join as api/partners.py::get_partners).
        # tier_name_en is the producer's own English wording of that same
        # tier -- Ui.producerLabel(user, 'tier_name') reads it.
        row = conn.execute(
            """SELECT u.*, t.name AS tier_name, t.color AS tier_color,
                      t.name_en AS tier_name_en
               FROM users u
               LEFT JOIN commission_tiers t ON t.id = u.tier_id
               WHERE u.id = ?""",
            (user_id,)
        ).fetchone()
        return _user_pub(row) if row else None
    finally:
        conn.close()


# ─── CREATE (admin invites partner) ───────────────────────────────────────────

def _default_tier_id(conn):
    """The producer's entry tier: the first active one on his own list.

    A partner with no tier earns 0 % on every win, and nothing on any screen
    says so. Every path that leaves a partner active therefore lands them on
    the entry tier unless one was
    chosen. None only when the producer has not created any tier at all.
    """
    row = conn.execute(
        "SELECT id FROM commission_tiers WHERE active = 1 ORDER BY sort_order, base_rate, id LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def create_user(data: dict, require_company: bool = True):
    """Create an active user from normalized, allowlisted admin input."""
    data = _validate_user_create(data, require_company=require_company)
    conn = get_conn()
    try:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (data["email"],)).fetchone()
        if existing:
            raise ValueError(t("A user with this e-mail already exists."))

        hashed = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()
        # The tier comes from the invite form when the producer picked one --
        # that is the only tier system the app shows. An unknown or inactive
        # id is refused rather than silently dropped, so a stale form cannot
        # create a partner whose tier does not exist.
        tier_row = None
        if data.get("tier_id") is not None:
            tier_row = conn.execute(
                "SELECT id FROM commission_tiers WHERE id = ? AND active = 1",
                (data["tier_id"],),
            ).fetchone()
            if tier_row is None:
                raise ValueError(t("The selected commission tier does not exist."))
        if tier_row is None:
            # No tier picked: resolve the legacy `level` word instead. Without
            # this a new partner is stored with a level but no tier_id, and
            # then reads as e.g. "Silver" on their dashboard while their
            # earnings screen reports "No tier / 0%" -- one partner, two
            # answers, until a server restart backfills it.
            tier_row = conn.execute(
                "SELECT id FROM commission_tiers WHERE lower(name)=lower(?) AND active=1 ORDER BY id LIMIT 1",
                (data["level"],),
            ).fetchone()
        tier_id = tier_row["id"] if tier_row else None
        if tier_id is None and data["role"] == "partner":
            tier_id = _default_tier_id(conn)
        conn.execute(
            """INSERT INTO users (email, password, name, role, company, level, phone, bio, account_status, active, tier_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["email"],
                hashed,
                data["name"],
                data["role"],
                data["company"],
                data["level"],
                data["phone"],
                data["bio"],
                data["account_status"],
                1 if data["account_status"] == "active" else 0,
                tier_id,
            )
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (data["email"],)).fetchone()
        return _user_pub(row)
    finally:
        conn.close()


# ─── KEEPING THE PROGRAMME OWNED ──────────────────────────────────────────────

LAST_ADMIN_MESSAGE = (
    N_("This is the last administrator account. Without it nobody can manage users.")
)


def ensure_another_admin_remains(conn, user_id: int):
    """Refuse a change that would leave no active administrator.

    User management, the audit log and data requests are admin-only, so an
    instance without an active administrator can only be repaired from the
    server's shell. Every path that removes, demotes, deactivates or erases an
    account goes through this check.
    """
    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = 1 AND id <> ?",
        (user_id,),
    ).fetchone()["n"]
    if not remaining:
        raise ValueError(t(LAST_ADMIN_MESSAGE))


# ─── UPDATE (admin edits any, user edits own profile) ─────────────────────────

def update_user(user_id: int, data: dict, acting_user: dict):
    """Apply allowlisted profile/admin changes with explicit authorization."""
    user_id = parse_integer_id(user_id, "user_id")
    is_admin  = acting_user["role"] == "admin"
    is_self   = acting_user["id"] == user_id

    # Shape first, privileges second — malformed data never opens a connection.
    data = _validate_user_update(data)

    # Refused before the lookup: a partner or observer may only edit himself,
    # and row-level security would hide the other account anyway, turning a
    # clear "no" into a misleading "does not exist".
    if not is_admin and not is_self and acting_user["role"] != "manager":
        raise PermissionError(t("You are not allowed to edit this account."))

    conn = get_conn()
    try:
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise ValueError(t("The user does not exist."))

        # A moderator runs the partner channel, so they may accept,
        # suspend, reject and restore a PARTNER account. They may not touch a
        # staff account at all (that is user management, admin-only) and they
        # may never set `role` on anybody --- the two limits that stop the
        # role from escalating itself.
        manages_this_partner = (
            acting_user["role"] == "manager" and target["role"] == "partner"
        )
        can_set_account_fields = is_admin or manages_this_partner

        if not is_admin and not is_self and not manages_this_partner:
            raise PermissionError(t("You are not allowed to edit this account."))

        # Drop what this caller may not set, rather than refusing the whole
        # request: a partner PATCHing {name, role} still gets their name
        # changed and their role ignored, the behaviour every existing caller
        # relies on. A payload left empty by this filtering falls
        # through to the "No data to update." guard below.
        if not can_set_account_fields:
            data.pop("level", None)
            data.pop("account_status", None)
        if not is_admin:
            data.pop("role", None)

        if is_admin:
            changes_role = "role" in data and data["role"] != target["role"]
            changes_status = (
                "account_status" in data
                and data["account_status"] != target["account_status"]
            )
            # An administrator cannot demote, suspend or reject themselves: the
            # session would end at the next request and the change could not
            # be undone from inside the application.
            if is_self and (changes_role or changes_status):
                raise ValueError(t("You cannot change the role or status of your own account."))
            if target["role"] == "admin" and (
                changes_role
                or data.get("account_status", "active") != "active"
            ):
                ensure_another_admin_remains(conn, user_id)

        updates, params = [], []

        # Fields everyone can update on own profile
        for field, column in _PROFILE_COLUMNS.items():
            if field in data:
                updates.append(f"{column} = ?"); params.append(data[field])

        if can_set_account_fields:
            if "level" in data:
                updates.append("level = ?"); params.append(data["level"])
            if "account_status" in data:
                new_status = data["account_status"]
                updates.append("account_status = ?"); params.append(new_status)
                # Mirror active flag
                updates.append("active = ?")
                params.append(1 if new_status == "active" else 0)
                # Start the retention clock on rejection, and stop it on any
                # move away from 'rejected' --- restoring a partner has to
                # clear the stamp, or the boot-time sweep in api/db.py would
                # delete an account somebody deliberately brought back.
                updates.append("rejected_at = ?")
                params.append(_now() if new_status == "rejected" else None)
                # Approving a partner who has no tier (a self-registration
                # carries none) lands him on the entry tier, never on 0 %.
                if (new_status == "active" and target["role"] == "partner"
                        and target["tier_id"] is None):
                    default_tier = _default_tier_id(conn)
                    if default_tier is not None:
                        updates.append("tier_id = ?"); params.append(default_tier)

        if is_admin and "role" in data:
            updates.append("role = ?"); params.append(data["role"])

        if not updates:
            raise ValueError(t("No data to update."))

        params.append(user_id)
        conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
        if data.get("account_status") in ("suspended", "rejected") and can_set_account_fields:
            # A suspended or rejected account must not be reopened by an
            # invitation link that is still lying in somebody's mailbox.
            conn.execute(
                "DELETE FROM partner_invitations WHERE user_id = ? AND accepted_at IS NULL",
                (user_id,),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()

    detail = _update_detail(target, row)
    if detail:
        audit.log(acting_user, "user.update", "user", str(user_id), detail)
    return _user_pub(row)


def _update_detail(before, after):
    """What changed, for the audit trail.

    Role, status and level are written with their old and new values, since
    those are the changes an incident review looks for. Profile fields are
    named but their contents are left out: they are personal data and the
    current value is on the account anyway.
    """
    parts = []
    for field in ("role", "account_status", "level"):
        if before[field] != after[field]:
            parts.append(f"{field}: {before[field] or '—'} → {after[field] or '—'}")
    profile = [field for field in _PROFILE_COLUMNS if before[field] != after[field]]
    if profile:
        parts.append("profile: " + ", ".join(profile))
    return "; ".join(parts)


# ─── DELETING AN ACCOUNT ──────────────────────────────────────────────────────

# What makes an account undeletable. Every other reference to users(id) either
# CASCADEs (sessions, notifications, an invitation that was never accepted) or
# is SET NULL (the audit trail, who assigned a bonus) -- those are safe to lose
# or safe to anonymise. These are not: they are the financial
# and decision history the producer may have to answer for later, and SQLite
# would refuse the DELETE anyway. Refusing with the reason beats a foreign-key
# error the screen cannot explain.
_DELETE_BLOCKERS = (
    ("leads", "partner_id",
     N_("they submitted leads that are in the system")),
    ("leads", "settlement_reviewed_by",
     N_("they reviewed a commission settlement")),
    ("leads", "win_confirmed_by",
     N_("they confirmed won sales")),
    # Comments would go with the account (the foreign key cascades), and they
    # are the record of decisions taken on each lead.
    ("lead_comments", "user_id",
     N_("they wrote comments on leads")),
    ("commission_payouts", "partner_id",
     N_("they have recorded commission payouts")),
    ("commission_payouts", "paid_by",
     N_("they recorded a commission payout")),
    ("commission_payouts", "reversed_by",
     N_("they reversed a commission payout")),
)


def _deletion_blockers(conn, user_id: int):
    found = []
    for table, column, reason in _DELETE_BLOCKERS:
        row = conn.execute(
            "SELECT 1 FROM %s WHERE %s = ? LIMIT 1" % (table, column), (user_id,)
        ).fetchone()
        if row:
            found.append(t(reason))
    return found


def delete_user(user_id: int, acting_user: dict):
    """Permanently remove a staff account.

    Four refusals, each of them a way the producer could otherwise lock
    themselves out or lose history:

    - not yourself. Deleting your own account mid-session leaves nobody holding
      the program.
    - not the last administrator. can_manage_users is admin-only, so removing
      the final admin makes user management unreachable for good.
    - not a partner. Partner accounts have their own lifecycle on the Partners
      screen -- reject, seven days to restore, then the retention sweep -- and
      going around it would delete somebody a producer could still bring back.
    - not an account carrying financial or decision history (see
      _DELETE_BLOCKERS). Deactivation exists for that case and is reversible.
    """
    user_id = parse_integer_id(user_id, "user_id")
    if not can_manage_users(acting_user):
        raise PermissionError(t("You are not allowed to delete accounts."))
    if acting_user["id"] == user_id:
        raise ValueError(t("You cannot delete your own account."))

    conn = get_conn()
    try:
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise ValueError(t("The user does not exist."))
        if target["role"] == "partner":
            raise ValueError(
                t("Partner accounts are deleted from the Partners screen, where a rejected "
                  "partner can be restored for 7 days.")
            )
        if target["role"] == "admin":
            ensure_another_admin_remains(conn, user_id)

        blockers = _deletion_blockers(conn, user_id)
        if blockers:
            raise ValueError(t(
                "This account cannot be deleted because {reasons}. Instead of deleting it "
                "you can deactivate it, and its history stays intact.",
                reasons=t(" and ").join(blockers),
            ))

        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()

    audit.log(
        acting_user, "user.delete", "user", str(user_id),
        detail="email=%s role=%s" % (target["email"], target["role"]),
    )
    return {"deleted": True, "id": user_id}


# ─── CHANGE PASSWORD ──────────────────────────────────────────────────────────

def change_password(user_id: int, current_pw: str, new_pw: str, acting_user: dict):
    """Replace a bcrypt password after authorization and bounded validation."""
    user_id = parse_integer_id(user_id, "user_id")
    is_admin = acting_user["role"] == "admin"
    is_self  = acting_user["id"] == user_id

    if not is_admin and not is_self:
        raise PermissionError(t("Not allowed."))

    # Your own password always needs the current one, administrators
    # included: otherwise anybody holding an unlocked or stolen admin session
    # could set a new owner password and keep access after it ends. The
    # administrator's exemption covers only other people's accounts.
    needs_current = is_self or not is_admin

    new_pw = parse_password(new_pw, N_("New password"), min_length=8, max_length=72)
    if needs_current:
        current_pw = parse_password(
            current_pw, N_("Current password"), min_length=1, max_length=72
        )

    # Imported here, like api/auth.py does: the gateway imports the app's modules.
    from api.gateway import keys as gateway_keys

    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError(t("The user does not exist."))

        if needs_current:
            if not bcrypt.checkpw(current_pw.encode(), row["password"].encode()):
                raise ValueError(t("The current password is incorrect."))

        new_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
        conn.execute("UPDATE users SET password = ? WHERE id = ?", (new_hash, user_id))
        # Invalidate all sessions for this user (force re-login)
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        # And every API key and OAuth connection: an
        # administrator setting a new password for a compromised account must
        # not leave the attacker's key or refresh token working. Same
        # transaction, as in "Sign out everywhere" and the e-mail reset.
        counts = gateway_keys.revoke_all_for_user(conn, user_id, "password_change")
        conn.commit()
    finally:
        conn.close()

    audit.log(acting_user, "user.password", "user", str(user_id),
              t("own password") if is_self else t("set by an administrator"))
    gateway_keys.audit_revocation(acting_user, user_id, counts)


# ─── REGISTER (self-service, status=pending) ──────────────────────────────────

_REGISTERED_MESSAGE = (
    N_("The account was registered. Wait for an administrator to activate it.")
)


class AccountExists(ValueError):
    """The address already has an account. Never shown to the registrant."""

    def __init__(self, email, name):
        super().__init__(t("An account with this e-mail address already exists."))
        self.email = email
        self.name = name


def register_partner(data: dict):
    """Register a validated pending partner without accepting role overrides."""
    data = _validate_user_create(data, registration=True)
    custom_fields = data.pop("custom_fields", {})
    # Hashed before the existence check, so a known address costs the same
    # time as a new one and the response time does not reveal who has an
    # account.
    hashed = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT id, name, email FROM users WHERE email = ?", (data["email"],)
        ).fetchone()
        if existing:
            raise AccountExists(existing["email"], existing["name"])

        conn.execute(
            """INSERT INTO users (email, password, name, role, company, phone, bio, account_status, active)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                data["email"],
                hashed,
                data["name"],
                "partner",
                data["company"],
                data["phone"],
                data["bio"],
                "pending",
                0,    # inactive until admin approves
            )
        )
        conn.commit()
        new_user_id = conn.execute(
            "SELECT id FROM users WHERE email = ?", (data["email"],)
        ).fetchone()["id"]
    finally:
        conn.close()

    # The producer's own extra questions. Saved after the account so a
    # malformed answer can never cost somebody their registration --- and
    # answers to a question switched off in the meantime are ignored, not
    # refused, for the same reason.
    try:
        registration_fields.save_answers(new_user_id, custom_fields)
    except Exception as exc:
        logger.error(
            "registration_custom_fields_failed exception=%s", type(exc).__name__
        )

    # Let the producer learn that a partner is waiting.
    # Registration wrote the account and nothing else before this; a
    # notification failure must never turn a successful registration into a
    # failed one, so this runs after the registration connection is closed
    # and any exception is caught and logged, never raised or surfaced to
    # the registrant.
    try:
        notif.notify_all_admins(
            "warning",
            t("A new partner is waiting for approval"),
            t("{name} ({company}) registered and is waiting for the account to be activated.",
              name=data['name'], company=data['company']),
            "users.html?status=pending",
            title_key="ntf_partner_pending_title",
            body_key="ntf_partner_pending_body",
            params={"name": data["name"], "company": data["company"]},
        )
    except Exception as exc:
        logger.error(
            "registration_notification_failed exception=%s", type(exc).__name__
        )


# ─── HTTP HANDLERS ─────────────────────────────────────────────────────────────

def handle_list(handler, user: dict, qs: dict):
    if not can_manage_users(user):
        return 403, {"error": t("Access denied.")}
    if qs.get("scope", [None])[0] == "team":
        # The Team tab's roster. Reuses this handler's own
        # administrator-only guard above -- no new guard code; an
        # channel manager cannot reach Settings at all.
        return 200, {"users": list_team()}
    users = list_users(
        role_filter=qs.get("role", [None])[0],
        status_filter=qs.get("status", [None])[0],
        q=qs.get("q", [None])[0],
    )
    return 200, {"users": users}


def handle_create(handler, user: dict, body: dict):
    if not can_manage_users(user):
        return 403, {"error": t("Access denied.")}
    try:
        new_user = create_user(body)
    except ValueError as e:
        return 400, {"error": str(e)}
    audit.log(user, "user.create", "user", str(new_user["id"]),
              "role=%s status=%s" % (new_user["role"], new_user["account_status"]))
    return 201, {"user": new_user}


def handle_get(handler, user: dict, user_id: int):
    # Everyone may read their own account and an administrator may read any.
    # Other staff (moderators, observers) see partner details, which open in
    # place on the Partners screen, but never another staff account: the
    # team roster is user management, which is admin-only.
    is_self = user["id"] == user_id
    if not is_self and not can_manage_users(user) and not is_staff(user):
        return 403, {"error": t("Access denied.")}
    u = get_user(user_id)
    if not u:
        return 404, {"error": t("User not found.")}
    if not is_self and not can_manage_users(user) and u.get("role") != "partner":
        return 403, {"error": t("Access denied.")}
    return 200, {"user": u}


def handle_update(handler, user: dict, user_id: int, body: dict):
    try:
        updated = update_user(user_id, body, user)
        return 200, {"user": updated}
    except PermissionError as e:
        return 403, {"error": str(e)}
    except ValueError as e:
        return 400, {"error": str(e)}


def handle_delete(handler, user: dict, user_id: int):
    try:
        return 200, delete_user(user_id, user)
    except PermissionError as e:
        return 403, {"error": str(e)}
    except ValueError as e:
        return 400, {"error": str(e)}


def handle_password(handler, user: dict, user_id: int, body: dict):
    try:
        body = parse_allowed_fields(
            body, ("current_password", "new_password"), N_("Password change data")
        )
        change_password(
            user_id,
            body.get("current_password", ""),
            body.get("new_password", ""),
            user,
        )
        return 200, {"ok": True, "message": t("The password was changed. Sign in again.")}
    except PermissionError as e:
        return 403, {"error": str(e)}
    except ValueError as e:
        return 400, {"error": str(e)}


# Self-registration is public: without a limit a script created 30 pending
# partners in 7 s and buried the producer in 30 "partner waiting" notifications.
#Each address may register a few partners
# per window -- a real agency signing up colleagues fits, a bot does not.
REGISTRATIONS_PER_IP = 5
REGISTRATION_WINDOW_MINUTES = 15  # never above auth.LOGIN_WINDOW_MINUTES: its sweep prunes the rows
# Registrations are counted in login_attempts under a marker that can never be
# an e-mail address, stored as succeeded=true so the login limiter (which
# counts failures only) never mistakes a registration for a guessed password.
_REGISTRATION_MARKER = "#partner-registration"


class RegistrationThrottled(ValueError):
    pass


def _throttle_registration(ip):
    conn = get_conn()
    try:
        count = conn.execute(
            f"""SELECT COUNT(*) AS n FROM login_attempts
                WHERE email = ? AND ip = ?
                  AND created_at >= now() - INTERVAL '{REGISTRATION_WINDOW_MINUTES} minutes'""",
            (_REGISTRATION_MARKER, ip[:64]),
        ).fetchone()["n"]
        if count >= REGISTRATIONS_PER_IP:
            raise RegistrationThrottled(
                t("Too many registrations from this address. Try again in a quarter of an hour.")
            )
        conn.execute(
            "INSERT INTO login_attempts (email, ip, succeeded) VALUES (?,?,?)",
            (_REGISTRATION_MARKER, ip[:64], True),
        )
        conn.commit()
    finally:
        conn.close()


def _login_url() -> str:
    """The sign-in page, from the configured base URL only, never the Host header."""
    base = os.environ.get("PARTNERTYPE_BASE_URL", "").strip()
    if not base.startswith(("http://", "https://")):
        base = "http://localhost:8000"
    return base.rstrip("/") + "/index.html"


def handle_register(handler, body: dict):
    try:
        _throttle_registration(client_ip(handler) if handler is not None else "")
    except RegistrationThrottled as e:
        return 429, {"error": str(e), "code": "too_many_attempts"}
    try:
        register_partner(body)
    except AccountExists as existing:
        # The same answer as a new registration, so the form cannot be used
        # to find out who has an account. The owner of the address hears
        # about the attempt by mail instead, sent after the answer.
        from api import email_service

        email_service.run_in_background(
            email_service.send_existing_account_notice,
            existing.email, existing.name, _login_url(),
        )
        return 201, {"ok": True, "message": t(_REGISTERED_MESSAGE)}
    except ValueError as e:
        return 400, {"error": str(e)}
    return 201, {"ok": True, "message": t(_REGISTERED_MESSAGE)}
