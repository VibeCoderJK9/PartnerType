"""
PartnerType — System Settings Module
Key-value store for admin-configurable system parameters.
"""
from datetime import datetime
import os
import math

from api import audit
from api.authz import can_configure, can_manage_users
from api.db import get_conn
from api.validation import ValidationError
from api.i18n import t

DEFAULTS = {
    "company_name":          "PartnerType",
    "company_email":         "",
    "company_url":           "",
    "commission_bronze":     "5",
    "commission_silver":     "6",
    "commission_gold":       "7",
    "commission_platinum":   "10",
    "currency":              "PLN",
    "lead_auto_commission":  "1",
    "lead_duplicate_policy": "queue",
    "lead_queue_shares":     "100,50,25,10,0",
    "lead_queue_max":        "5",
    # Days the producer has to accept or reject a submitted lead. Past this,
    # the lead counts as overdue on the dashboard and in the leads filter.
    "lead_action_days":      "2",
    # Whether a moderator or observer is refused entry without a second factor.
    # Off by default: everyone decides for their
    # own account, and the producer turns this on when more than one person is
    # looking at real partner data. It has never applied to partners, who see
    # only their own work.
    "mfa_required_for_staff": "0",
    # Where the producer keeps the terms of HIS partner programme. The partner
    # sign-up form links "regulamin programu partnerskiego" here; empty means
    # the words stay plain text rather than a link to nowhere.
    "partner_terms_url": "",
}

# Mail server settings are not the producer's: the operator
# configures one mailbox for the whole server (api/email_service.py). Rows an
# older instance still carries under these keys are neither shown nor editable.
_RETIRED_PREFIX = "smtp_"

EDITABLE_KEYS = set(DEFAULTS.keys())
# Security policy for the whole team: only the owner may change it. A moderator
# who could switch the staff second-factor requirement off could then take
# their own factor off as well. A moderator's save that carries the current
# value unchanged still goes through, because the settings form sends every
# field at once.
ADMIN_ONLY_KEYS = {"mfa_required_for_staff"}
_SWITCH_VALUES = {"0", "1"}
_DUPLICATE_POLICIES = {"queue", "reject", "allow"}


def _parse_queue_shares(value):
    raw = str(value)
    parts = [part.strip() for part in raw.split(",")]
    if not 1 <= len(parts) <= 5 or any(not part for part in parts):
        raise ValidationError(t("The queue shares must hold 1 to 5 comma-separated values."))
    try:
        shares = [float(part) for part in parts]
    except ValueError:
        raise ValidationError(t("Every queue share must be a finite number from 0 to 100."))
    if any(not math.isfinite(share) or share < 0 or share > 100 for share in shares):
        raise ValidationError(t("Every queue share must be a finite number from 0 to 100."))
    if any(later > earlier for earlier, later in zip(shares, shares[1:])):
        raise ValidationError(t("Queue shares cannot grow for later submissions."))
    if shares[-1] != 0:
        raise ValidationError(t("The last queue share must be exactly 0."))
    return shares


def _parse_queue_max(value):
    raw = str(value).strip()
    if not raw.isdigit():
        raise ValidationError(t("The maximum number of submissions in the queue must be an "
                                "integer from 1 to 5."))
    maximum = int(raw)
    if not 1 <= maximum <= 5:
        raise ValidationError(t("The maximum number of submissions in the queue must be "
                                "from 1 to 5."))
    return maximum


def _parse_action_days(value):
    raw = str(value).strip()
    if not raw.isdigit():
        raise ValidationError(t("The lead review deadline must be a whole number of days."))
    days = int(raw)
    if not 1 <= days <= 90:
        raise ValidationError(t("The lead review deadline must be from 1 to 90 days."))
    return days


def _validate_action_days(updates):
    if "lead_action_days" in updates:
        _parse_action_days(updates["lead_action_days"])


def _validate_terms_url(updates):
    """Empty, or one http(s) address: it becomes a link on a public page."""
    if "partner_terms_url" not in updates:
        return
    value = str(updates["partner_terms_url"] or "").strip()
    updates["partner_terms_url"] = value
    if not value:
        return
    if (len(value) > 500 or not value.lower().startswith(("https://", "http://"))
            or any(ch.isspace() or ch in '"<>' for ch in value)):
        raise ValidationError(t("The terms address must be a full web address, e.g. "
                                "https://yourcompany.com/terms."))


def _validate_switches(updates):
    if "mfa_required_for_staff" in updates:
        value = updates["mfa_required_for_staff"]
        if isinstance(value, bool):
            value = "1" if value else "0"
        value = str(value).strip()
        if value not in _SWITCH_VALUES:
            raise ValidationError(t("The second-factor requirement must be 0 or 1."))
        updates["mfa_required_for_staff"] = value


def _validate_queue_updates(conn, updates):
    queue_keys = {"lead_duplicate_policy", "lead_queue_shares", "lead_queue_max"}
    if not queue_keys.intersection(updates):
        return

    policy = str(updates.get("lead_duplicate_policy", DEFAULTS["lead_duplicate_policy"]))
    if "lead_duplicate_policy" not in updates:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='lead_duplicate_policy'"
        ).fetchone()
        if row:
            policy = row["value"]
    if policy not in _DUPLICATE_POLICIES:
        raise ValidationError(t("The duplicate policy must be one of: queue, reject or allow."))

    effective = {}
    for key in ("lead_queue_shares", "lead_queue_max"):
        if key in updates:
            effective[key] = updates[key]
            continue
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        effective[key] = row["value"] if row else DEFAULTS[key]

    shares = _parse_queue_shares(effective["lead_queue_shares"])
    maximum = _parse_queue_max(effective["lead_queue_max"])
    if maximum != len(shares):
        raise ValidationError(t("The maximum number of submissions must equal the number of"
                                " queue shares."))


def get_all() -> dict:
    conn = get_conn()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        result = dict(DEFAULTS)  # start from defaults
        # brand_* is the logo (api/branding.py): served on its own, never in bulk.
        # integrations_* are the API gateway switches: written only by
        # api/gateway/settings.py, admin-only, and never readable through the
        # general Settings form a moderator also reaches.
        result.update({r["key"]: r["value"] for r in rows
                       if not r["key"].startswith((_RETIRED_PREFIX, "brand_", "integrations_"))})
        result["company_name"] = result.get("company_name") or os.environ.get("PARTNERTYPE_INSTANCE_NAME", "PartnerType")
        return result
    finally:
        conn.close()


def action_days() -> int:
    """Days the producer has to decide on a lead, as a number nobody re-parses.

    Three readers ask this question -- the dashboard's overdue card
    (api/stats.py), the leads list's "po terminie" filter (api/leads.py) and
    the settings screen -- and they must never disagree about where the
    deadline falls. A stored value that is missing, unparsable or below 1 falls
    back to the documented default rather than raising: a broken row in
    `settings` must not take down a list that only wants to sort leads.
    """
    raw = get_all().get("lead_action_days", DEFAULTS["lead_action_days"])
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return int(DEFAULTS["lead_action_days"])
    return days if days >= 1 else int(DEFAULTS["lead_action_days"])


def mfa_required_for_staff() -> bool:
    """Whether the producer requires a second factor from moderators/observers.

    One reader, like action_days() above, so the login gate, the profile screen
    and the settings form cannot disagree about what the stored value means.
    Anything that is not exactly an on-value is off: a setting that decides
    whether somebody can sign in must fail towards letting the owner in, not
    towards locking them out of their own instance.
    """
    return str(get_all().get("mfa_required_for_staff", "0")).strip() in ("1", "true", "yes")


def get(key: str) -> str:
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else DEFAULTS.get(key, "")
    finally:
        conn.close()


def set_many(updates: dict, user: dict):
    if not can_configure(user):
        raise PermissionError(t("Only an administrator can change the settings."))

    conn = get_conn()
    changes = []
    try:
        _validate_queue_updates(conn, updates)
        _validate_action_days(updates)
        _validate_terms_url(updates)
        _validate_switches(updates)
        previous = get_all()
        if not can_manage_users(user):
            for key in ADMIN_ONLY_KEYS.intersection(updates):
                if str(updates[key]) != str(previous.get(key, "")):
                    raise PermissionError(
                        t("Only the owner can change the team's second-factor requirement."))
        now = datetime.utcnow().isoformat()
        for key, value in updates.items():
            if key not in EDITABLE_KEYS:
                continue   # silently ignore unknown keys
            new_value = str(value)
            old_value = str(previous.get(key, ""))
            if new_value != old_value:
                changes.append((key, old_value, new_value))
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, str(value), now)
            )
        conn.commit()
    finally:
        conn.close()

    # A queue-share or duplicate-policy change moves real money between
    # partners, so it needs the same who/when/old->new record every other
    # consequential action writes. Logged after the write commits and the
    # connection closes, and only for values that actually changed.
    for key, old_value, new_value in changes:
        audit.log(user, "settings.update", "settings", key,
                  f"{key}: {old_value or '—'} → {new_value or '—'}")

    return get_all()


# ─── HTTP HANDLERS ────────────────────────────────────────────────────────────

def handle_get(handler, user, qs):
    if not can_configure(user):
        # Partners only get public settings
        s = get_all()
        return 200, {"settings": {
            "company_name":  s.get("company_name"),
            "currency":      s.get("currency"),
            "lead_queue_shares": s.get("lead_queue_shares"),
            "lead_queue_max": s.get("lead_queue_max"),
        }}
    return 200, {"settings": get_all()}


def handle_update(handler, user, body):
    if not can_configure(user):
        return 403, {"error": t("Only an administrator can change the settings.")}
    try:
        result = set_many(body, user)
    except PermissionError as e:
        return 403, {"error": str(e)}
    return 200, {"settings": result}
