"""The one field filter for everything that leaves through the gateway.

MCP tools and /api/v1 both project records through this module, so the two
surfaces cannot drift apart in what they reveal. The projection is an
ALLOWLIST: it builds a new dict from the keys a group names and never copies a
row and deletes keys -- with a delete-list, every column added to `leads`
later would leak by default.

`base` is always on. The administrator switches the other groups on in
Settings › Integrations; all of them are off by default.
Payout data, partner earnings and settlement fields are in no group at all and
therefore can never be exposed.
"""
from api import authz

FIELD_GROUPS = {
    "base": ("id", "company", "value", "product", "quantity", "status",
             "registration_status", "partner", "created_at", "updated_at", "won_at"),
    # leads.client_name is the contact PERSON; the client company is `company`.
    "contact": ("client_name", "client_email", "client_phone"),
    "notes": ("notes", "comments"),
    "client_ids": ("sector", "nip", "website"),
    "custom": ("custom_fields",),
    "commissions": ("commission",),
}

SWITCHABLE_GROUPS = frozenset(name for name in FIELD_GROUPS if name != "base")

TEXT_LIMIT = 500
_ELLIPSIS = "…"


def truncate_text(value, limit=TEXT_LIMIT):
    """Cap a string at `limit` characters (ellipsis included); other values pass."""
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[:max(0, limit - len(_ELLIPSIS))] + _ELLIPSIS


def _partner_label(row):
    company = (row.get("partner_company") or "").strip()
    if company:
        return company
    return f"Partner #{row.get('partner_id')}"


def project_lead(row, groups) -> dict:
    """The externally visible form of one lead row.

    `groups` is the set of enabled switchable groups (settings.enabled_field_groups()).
    Base keys are always present; a switched-on group adds only the keys the
    row actually carries.
    """
    row = dict(row)
    out = {}
    for key in FIELD_GROUPS["base"]:
        value = _partner_label(row) if key == "partner" else row.get(key)
        out[key] = truncate_text(value)
    for group in sorted(set(groups or ()) & SWITCHABLE_GROUPS):
        for key in FIELD_GROUPS[group]:
            if key in row:
                out[key] = truncate_text(row[key])
    return out


def project_comment(row, viewer) -> dict:
    """One lead comment as the gateway shows it.

    Which comments a viewer may see at all is decided by
    comments.list_comments (partners and observers never get internal ones);
    `is_internal` is shown only to staff, who are the only ones who can have
    both kinds.
    """
    row = dict(row)
    out = {
        "body": truncate_text(row.get("body")),
        "created_at": row.get("created_at"),
        "author_name": truncate_text(row.get("author_name")),
        "author_role": row.get("author_role"),
    }
    if authz.is_staff(viewer):
        out["is_internal"] = bool(row.get("is_internal"))
    return out


def _truncate_value(value):
    if isinstance(value, list):
        return [truncate_text(v) for v in value[:50]]
    return truncate_text(value)


def project_custom_fields(values) -> list:
    """Producer-defined lead fields: the question, the answer, whether it is live."""
    out = []
    for item in values or ():
        item = dict(item)
        out.append({
            "label": truncate_text(item.get("label")),
            "value": _truncate_value(item.get("value")),
            "active": bool(item.get("active")),
        })
    return out


# ─── Statistics ──────────────────────────────────────────────────────
# Counts only. Earnings, payouts, trends, monthly series and partner rankings
# are never projected: the trends and series carry money, and a ranking is
# ordered by earnings. total_commissions (a staff sum of lead commissions) is
# added only when the administrator enabled the commissions group.
STAFF_STATS_KEYS = ("total_leads", "new_leads", "pending_leads", "accepted_leads",
                    "rejected_leads", "overdue_leads", "total_partners",
                    "status_dist", "registration_dist", "won_today")
PARTNER_STATS_KEYS = ("total_leads", "pending_leads", "accepted_leads", "rejected_leads",
                      "status_dist", "registration_dist", "won_today")


def _count(value):
    return 0 if value is None else value


def project_stats(data, viewer, groups) -> dict:
    """Allowlisted lead statistics for `viewer`'s role."""
    data = dict(data or {})
    staff = authz.is_staff(viewer)
    keys = STAFF_STATS_KEYS if staff else PARTNER_STATS_KEYS
    out = {}
    for key in keys:
        value = data.get(key)
        out[key] = dict(value) if isinstance(value, dict) else _count(value)
    if staff and "commissions" in set(groups or ()):
        out["total_commissions"] = _count(data.get("total_commissions"))
    return out


# ─── Partners, products ─────────────────────────────────────────────────────
_PARTNER_BASE = ("id", "level", "tier_name", "tier_name_en", "joined_at",
                 "total_leads", "accepted_leads")
_PARTNER_CONTACT = ("name", "email")


def project_partner(row, groups) -> dict:
    """One partner, without earnings; the person's name and e-mail only with
    the contact group. `company` falls back to "Partner #<id>" (A3)."""
    row = dict(row)
    out = {}
    for key in _PARTNER_BASE:
        value = row.get(key)
        if key in ("total_leads", "accepted_leads"):
            value = _count(value)
        out[key] = truncate_text(value)
    company = (row.get("company") or "").strip()
    out["company"] = truncate_text(company) if company else f"Partner #{row.get('id')}"
    if "contact" in set(groups or ()):
        for key in _PARTNER_CONTACT:
            out[key] = truncate_text(row.get(key))
    return out


def project_product(row) -> dict:
    row = dict(row)
    return {
        "id": row.get("id"),
        "name": truncate_text(row.get("name")),
        "name_en": truncate_text(row.get("name_en")),
        "category": row.get("category"),
        "base_price": row.get("base_price"),
        "offered": bool(row.get("offered")),
        "active": bool(row.get("active")),
    }


# ─── Reminders (block notifications) ────────────────────────────────────────
# Params by allowlist. `partner` (a person's name) and the stored title/body
# sentences -- which contain that name -- leave only with the contact group.
REMINDER_PARAMS = frozenset({"days", "date"})


def project_reminder(row, groups) -> dict:
    row = dict(row)
    params = row.get("params") if isinstance(row.get("params"), dict) else {}
    contact = "contact" in set(groups or ())
    out_params = {key: truncate_text(params[key]) for key in sorted(REMINDER_PARAMS)
                  if key in params}
    if contact and "partner" in params:
        out_params["partner"] = truncate_text(params["partner"])
    out = {
        "id": row.get("id"),
        "kind": row.get("title_key"),
        "params": out_params,
        "link": row.get("link"),
        "created_at": row.get("created_at"),
        "is_read": bool(row.get("is_read")),
    }
    if contact:
        out["title"] = truncate_text(row.get("title"))
        out["body"] = truncate_text(row.get("body"))
    return out
