"""
PartnerType — Bonus Definitions Module
Manages global bonus pool: up to 10 percent bonuses + 5 cash bonuses.
"""
from datetime import datetime
from api.authz import can_configure
from api.db import get_conn
from api import audit
from api.validation import (
    parse_allowed_fields,
    parse_boolean_flag,
    parse_enum,
    parse_finite_number,
    parse_integer_id,
    parse_non_negative_integer,
    parse_optional_string,
    parse_required_string,
)
from api.i18n import N_, t

MAX_PERCENT = 10
MAX_CASH    = 5
MAX_SORT_ORDER = 10000
MAX_CASH_VALUE = 1000000000
# name_en / description_en are the producer's OWN English wording of the bonus
# he invented. Empty means "not translated yet"; readers fall back to the
# Polish original through Ui.producerLabel rather than showing a blank chip.
BONUS_FIELDS = (
    "name", "name_en", "type", "default_value", "description",
    "description_en", "sort_order", "active"
)


def _limit_reached(btype, limit):
    if btype == "percent":
        return t("The limit of {limit} percentage bonuses has been reached.", limit=limit)
    return t("The limit of {limit} cash bonuses has been reached.", limit=limit)


def _require_configure(user):
    """Refuse a write before anything is read or written.

    The HTTP handlers below already answer 403, but they are not the only
    caller: the assistant's confirmed actions (api/agent_actions.py) and any
    future script reach these functions directly. Putting the check in the
    canonical writer means a new entry point cannot forget it.
    """
    if not can_configure(user):
        raise PermissionError(t("Access denied."))


def _validated_bonus_data(data, *, creating=False):
    clean = parse_allowed_fields(data, BONUS_FIELDS, N_("Bonus definition"))
    if creating or "name" in clean:
        clean["name"] = parse_required_string(
            clean.get("name"), N_("Bonus name"), max_length=100
        )
    if creating or "type" in clean:
        clean["type"] = parse_enum(
            clean.get("type", "percent"), N_("Bonus type"), ("percent", "cash")
        )
    value_type = clean.get("type")
    if "default_value" in clean:
        maximum = 100 if value_type == "percent" else MAX_CASH_VALUE
        clean["default_value"] = parse_finite_number(
            clean["default_value"], N_("Default value"), minimum=0, maximum=maximum
        )
    if "name_en" in clean:
        clean["name_en"] = parse_optional_string(
            clean["name_en"], N_("English bonus name"), max_length=100
        )
    if "description" in clean:
        clean["description"] = parse_optional_string(
            clean["description"], N_("Description"), max_length=1000
        )
    if "description_en" in clean:
        clean["description_en"] = parse_optional_string(
            clean["description_en"], N_("English bonus description"), max_length=1000
        )
    if "sort_order" in clean:
        clean["sort_order"] = parse_non_negative_integer(
            clean["sort_order"], N_("Order"), maximum=MAX_SORT_ORDER
        )
    if "active" in clean:
        clean["active"] = parse_boolean_flag(clean["active"], N_("Active"))
    return clean


def get_all():
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM bonus_definitions ORDER BY type, sort_order, id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_by_id(bonus_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM bonus_definitions WHERE id=?", (bonus_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _count_active(conn, btype):
    return conn.execute(
        "SELECT COUNT(*) FROM bonus_definitions WHERE type=? AND active=1", (btype,)
    ).fetchone()[0]


def create_bonus(data, user):
    """Create a bounded bonus definition from explicit public fields."""
    _require_configure(user)
    data = _validated_bonus_data(data, creating=True)
    btype = data["type"]
    name = data["name"]
    default_value = data.get("default_value", 0)
    active = data.get("active", 1)

    conn = get_conn()
    try:
        limit = MAX_PERCENT if btype == "percent" else MAX_CASH
        current = _count_active(conn, btype)
        if active and current >= limit:
            raise ValueError(_limit_reached(btype, limit))

        sort_order = data.get("sort_order", current)
        description = data.get("description", "")
        name_en = data.get("name_en", "")
        description_en = data.get("description_en", "")
        now = datetime.utcnow().isoformat()

        c = conn.execute(
            "INSERT INTO bonus_definitions "
            "(name, name_en, type, default_value, description, description_en, sort_order, active, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (name, name_en, btype, default_value, description, description_en,
             sort_order, active, now)
        )
        conn.commit()
        bonus_id = c.lastrowid
        audit.log(user, "bonus.create", "bonus_def", str(bonus_id), f"{name} {btype} {default_value}")
        return get_by_id(bonus_id)
    finally:
        conn.close()


def update_bonus(bonus_id, data, user):
    """Update only code-owned bonus columns after validating ID and values."""
    _require_configure(user)
    bonus_id = parse_integer_id(bonus_id, N_("Bonus ID"))
    data = _validated_bonus_data(data)
    if not data:
        raise ValueError(t("No data to update."))
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM bonus_definitions WHERE id=?", (bonus_id,)).fetchone()
        if not row:
            raise ValueError(t("The bonus does not exist."))

        target_type = data.get("type", row["type"])
        target_value = data.get("default_value", row["default_value"])
        if target_type == "percent" and target_value > 100:
            raise ValueError(t("Field 'Default value' is above the allowed range."))
        target_active = data.get("active", row["active"])
        if target_active and (not row["active"] or target_type != row["type"]):
            limit = MAX_PERCENT if target_type == "percent" else MAX_CASH
            current = conn.execute(
                "SELECT COUNT(*) FROM bonus_definitions "
                "WHERE type=? AND active=1 AND id<>?",
                (target_type, bonus_id),
            ).fetchone()[0]
            if current >= limit:
                raise ValueError(_limit_reached(target_type, limit))

        updates = []
        params = []

        if "name" in data:
            updates.append("name=?")
            params.append(data["name"])

        if "name_en" in data:
            updates.append("name_en=?")
            params.append(data["name_en"])

        if "type" in data:
            updates.append("type=?")
            params.append(data["type"])

        if "default_value" in data:
            updates.append("default_value=?")
            params.append(data["default_value"])

        if "description" in data:
            updates.append("description=?")
            params.append(data["description"])

        if "description_en" in data:
            updates.append("description_en=?")
            params.append(data["description_en"])

        if "sort_order" in data:
            updates.append("sort_order=?")
            params.append(data["sort_order"])

        if "active" in data:
            updates.append("active=?")
            params.append(data["active"])

        params.append(bonus_id)
        conn.execute(f"UPDATE bonus_definitions SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
        audit.log(user, "bonus.update", "bonus_def", str(bonus_id), str(data))
        return get_by_id(bonus_id)
    finally:
        conn.close()


def delete_bonus(bonus_id, user):
    """Delete an unused bonus identified by a positive integer ID."""
    _require_configure(user)
    bonus_id = parse_integer_id(bonus_id, N_("Bonus ID"))
    conn = get_conn()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM partner_bonus_assignments WHERE bonus_def_id=?", (bonus_id,)
        ).fetchone()[0]
        if count > 0:
            raise ValueError(t("Cannot delete: the bonus is assigned to {count} partners.", count=count))
        conn.execute("DELETE FROM bonus_definitions WHERE id=?", (bonus_id,))
        conn.commit()
        audit.log(user, "bonus.delete", "bonus_def", str(bonus_id), "")
        return True
    finally:
        conn.close()


# ─── HTTP HANDLERS ────────────────────────────────────────────────────────────

def handle_list(handler, user, qs):
    return 200, {"bonuses": get_all()}


def handle_create(handler, user, body):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    try:
        bonus = create_bonus(body, user)
        return 201, {"bonus": bonus}
    except PermissionError:
        return 403, {"error": t("Access denied.")}
    except ValueError as e:
        return 400, {"error": str(e)}


def handle_update(handler, user, bonus_id, body):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    try:
        bonus = update_bonus(bonus_id, body, user)
        return 200, {"bonus": bonus}
    except PermissionError:
        return 403, {"error": t("Access denied.")}
    except ValueError as e:
        return 400, {"error": str(e)}


def handle_delete(handler, user, bonus_id):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    try:
        delete_bonus(bonus_id, user)
        return 200, {"ok": True}
    except PermissionError:
        return 403, {"error": t("Access denied.")}
    except ValueError as e:
        return 400, {"error": str(e)}
