"""
PartnerType — Commission Tiers Module
Manages partner commission tier definitions (up to 5 active tiers).
"""
from datetime import datetime
from api.authz import can_configure
from api.db import get_conn
from api import audit
from api.validation import (
    parse_allowed_fields,
    parse_boolean_flag,
    parse_color,
    parse_finite_number,
    parse_integer_id,
    parse_non_negative_integer,
    parse_optional_string,
    parse_required_string,
)
from api.i18n import N_, t

MAX_TIERS = 5
MAX_SORT_ORDER = 10000
# name_en is the producer's OWN English wording of a tier he named himself
# ("Diament", "Partner Premium"), following the same *_en + Ui.producerLabel
# + Settings switch pattern already shipped for bonus_definitions,
# lead_stages and the registration/lead question fields. Empty
# means "not translated yet"; readers fall back to the Polish original
# through Ui.producerLabel rather than showing a blank badge.
TIER_FIELDS = ("name", "name_en", "base_rate", "color", "sort_order", "active")


def _require_configure(user):
    """Refuse a write before anything is read or written.

    The HTTP handlers below already answer 403, but they are not the only
    caller: the assistant's confirmed actions and any future script reach
    these functions directly. Putting the check in the canonical writer means
    a new entry point cannot forget it.
    """
    if not can_configure(user):
        raise PermissionError(t("Access denied."))


def _validated_tier_data(data, *, creating=False):
    clean = parse_allowed_fields(data, TIER_FIELDS, N_("Commission tier"))
    if creating or "name" in clean:
        clean["name"] = parse_required_string(
            clean.get("name"), N_("Tier name"), max_length=100
        )
    if "name_en" in clean:
        clean["name_en"] = parse_optional_string(
            clean["name_en"], N_("English tier name"), max_length=100
        )
    if "base_rate" in clean:
        clean["base_rate"] = parse_finite_number(
            clean["base_rate"], N_("Rate"), minimum=0, maximum=100
        )
    if "color" in clean:
        clean["color"] = parse_color(clean["color"], N_("Colour"))
    if "sort_order" in clean:
        clean["sort_order"] = parse_non_negative_integer(
            clean["sort_order"], N_("Order"), maximum=MAX_SORT_ORDER
        )
    if "active" in clean:
        clean["active"] = parse_boolean_flag(clean["active"], N_("Active"))
    return clean


def get_all():
    """Return all tiers ordered by sort_order."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM commission_tiers ORDER BY sort_order, id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_by_id(tier_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM commission_tiers WHERE id=?", (tier_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_tier(data, user):
    """Create a bounded commission tier from an explicit public field contract."""
    _require_configure(user)
    data = _validated_tier_data(data, creating=True)
    conn = get_conn()
    try:
        active_count = conn.execute(
            "SELECT COUNT(*) FROM commission_tiers WHERE active=1"
        ).fetchone()[0]
        if active_count >= MAX_TIERS:
            raise ValueError(t("The maximum number of tiers is {count}.", count=MAX_TIERS))

        name = data["name"]
        name_en = data.get("name_en", "")
        base_rate = data.get("base_rate", 5.0)
        color = data.get("color", "#6246ea")
        sort_order = data.get("sort_order", active_count)
        active = data.get("active", 1)

        now = datetime.utcnow().isoformat()
        c = conn.execute(
            "INSERT INTO commission_tiers (name, name_en, base_rate, color, sort_order, active, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (name, name_en, base_rate, color, sort_order, active, now)
        )
        conn.commit()
        tier_id = c.lastrowid
        audit.log(user, "tier.create", "tier", str(tier_id), f"{name} {base_rate}%")
        return get_by_id(tier_id)
    finally:
        conn.close()


def update_tier(tier_id, data, user):
    """Update only code-owned tier columns after validating ID and values."""
    _require_configure(user)
    tier_id = parse_integer_id(tier_id, N_("Tier ID"))
    data = _validated_tier_data(data)
    if not data:
        raise ValueError(t("No data to update."))
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM commission_tiers WHERE id=?", (tier_id,)).fetchone()
        if not row:
            raise ValueError(t("The tier does not exist."))

        updates = []
        params = []

        if "name" in data:
            updates.append("name=?")
            params.append(data["name"])

        if "name_en" in data:
            updates.append("name_en=?")
            params.append(data["name_en"])

        if "base_rate" in data:
            updates.append("base_rate=?")
            params.append(data["base_rate"])

        if "color" in data:
            updates.append("color=?")
            params.append(data["color"])

        if "sort_order" in data:
            updates.append("sort_order=?")
            params.append(data["sort_order"])

        if "active" in data:
            updates.append("active=?")
            params.append(data["active"])

        params.append(tier_id)
        conn.execute(f"UPDATE commission_tiers SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
        audit.log(user, "tier.update", "tier", str(tier_id), str(data))
        return get_by_id(tier_id)
    finally:
        conn.close()


def delete_tier(tier_id, user):
    """Delete an unused tier identified by a positive integer ID."""
    _require_configure(user)
    tier_id = parse_integer_id(tier_id, N_("Tier ID"))
    conn = get_conn()
    try:
        # Check if any partner uses this tier
        count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE tier_id=? AND role='partner'", (tier_id,)
        ).fetchone()[0]
        if count > 0:
            raise ValueError(t("Cannot delete: {count} partners use this tier.", count=count))
        conn.execute("DELETE FROM commission_tiers WHERE id=?", (tier_id,))
        conn.commit()
        audit.log(user, "tier.delete", "tier", str(tier_id), "")
        return True
    finally:
        conn.close()


# ─── HTTP HANDLERS ────────────────────────────────────────────────────────────

def handle_list(handler, user, qs):
    return 200, {"tiers": get_all()}


def handle_create(handler, user, body):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    try:
        tier = create_tier(body, user)
        return 201, {"tier": tier}
    except PermissionError:
        return 403, {"error": t("Access denied.")}
    except ValueError as e:
        return 400, {"error": str(e)}


def handle_update(handler, user, tier_id, body):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    try:
        tier = update_tier(tier_id, body, user)
        return 200, {"tier": tier}
    except PermissionError:
        return 403, {"error": t("Access denied.")}
    except ValueError as e:
        return 400, {"error": str(e)}


def handle_delete(handler, user, tier_id):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    try:
        delete_tier(tier_id, user)
        return 200, {"ok": True}
    except PermissionError:
        return 403, {"error": t("Access denied.")}
    except ValueError as e:
        return 400, {"error": str(e)}
