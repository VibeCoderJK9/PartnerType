"""
PartnerType — Sales Pipeline Module

Manages the producer-configurable SALES pipeline: up to six producer-defined
progress stages plus the two fixed terminals, Wygrany (win) and Przegrany
(lose). Mirrors the shape of api/commission_tiers.py.

Stages describe only how far the sale has got. Whether the producer approved
the deal is a separate fixed four-value column (leads.registration_status), so:

  - every stage here is part of the sale;
  - commission is projected at EVERY stage and becomes payable on the win
    terminal; see api/leads.py.
  - The producer defines these stages; the partner moves their own lead
    through them.
"""
import re
from datetime import datetime

from api.authz import can_configure
from api.db import get_conn
from api import audit
from api.validation import (
    parse_allowed_fields,
    parse_color,
    parse_enum,
    parse_integer_id,
    parse_non_negative_integer,
    parse_optional_string,
    parse_required_string,
)
from api.i18n import N_, t

MAX_PRODUCER_STAGES = 6
MAX_SORT_ORDER = 10000
# name_en is the producer's OWN English name for his stage. Empty means "not
# translated yet" -- readers fall back to `name`, never to a blank label.
STAGE_CREATE_FIELDS = ("name", "name_en", "kind", "color", "sort_order")
STAGE_UPDATE_FIELDS = ("name", "name_en", "color", "sort_order")

_POLISH_TRANSLIT = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
    "ó": "o", "ś": "s", "ź": "z", "ż": "z",
    "Ą": "a", "Ć": "c", "Ę": "e", "Ł": "l", "Ń": "n",
    "Ó": "o", "Ś": "s", "Ź": "z", "Ż": "z",
})
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    """Return a lowercase, underscore-separated machine key from a label."""
    text = name.lower().translate(_POLISH_TRANSLIT)
    text = _SLUG_RE.sub("_", text).strip("_")
    return text or "etap"


def _generate_unique_key(conn, name: str) -> str:
    """Return a stable key derived from ``name``, unique among existing keys."""
    base = _slugify(name)
    existing = {row[0] for row in conn.execute("SELECT key FROM lead_stages").fetchall()}
    key = base
    suffix = 2
    while key in existing:
        key = f"{base}_{suffix}"
        suffix += 1
    return key


def _validated_stage_create(data):
    clean = parse_allowed_fields(data, STAGE_CREATE_FIELDS, N_("Lead stage"))
    clean["name"] = parse_required_string(clean.get("name"), N_("Stage name"), max_length=100)
    if "name_en" in clean:
        clean["name_en"] = parse_optional_string(
            clean["name_en"], N_("English stage name"), max_length=100
        )
    # 'progress' is the only kind a producer can create: the two terminals
    # are fixed and seeded, and 'intake' stopped existing when registration
    # became its own column.
    clean["kind"] = parse_enum(clean.get("kind", "progress"), "kind", ("progress",))
    if "color" in clean:
        clean["color"] = parse_color(clean["color"], N_("Colour"))
    if "sort_order" in clean:
        clean["sort_order"] = parse_non_negative_integer(
            clean["sort_order"], N_("Order"), maximum=MAX_SORT_ORDER
        )
    return clean


def _validated_stage_update(data):
    clean = parse_allowed_fields(data, STAGE_UPDATE_FIELDS, N_("Lead stage"))
    if "name" in clean:
        clean["name"] = parse_required_string(clean["name"], N_("Stage name"), max_length=100)
    if "name_en" in clean:
        clean["name_en"] = parse_optional_string(
            clean["name_en"], N_("English stage name"), max_length=100
        )
    if "color" in clean:
        clean["color"] = parse_color(clean["color"], N_("Colour"))
    if "sort_order" in clean:
        clean["sort_order"] = parse_non_negative_integer(
            clean["sort_order"], N_("Order"), maximum=MAX_SORT_ORDER
        )
    return clean


def get_all():
    """Return every stage ordered non-terminal-first, then by sort order."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM lead_stages ORDER BY is_terminal ASC, sort_order ASC, id ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_first_pipeline_stage():
    """Return the first non-terminal stage of the sales pipeline, or None.

    Where a lead lands the moment the producer accepts it.
    Reads the live ordering rather than a hardcoded key, so a producer who
    renames or reorders their pipeline moves this entry point with it.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT * FROM lead_stages
                WHERE is_terminal = 0 AND active = 1
                ORDER BY sort_order ASC, id ASC LIMIT 1"""
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_by_id(stage_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM lead_stages WHERE id=?", (stage_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_by_key(key):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM lead_stages WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_active_stage_keys():
    """Return active stage keys, non-terminal first, in sort order.

    The single source of truth other modules (api/leads.py, api/stats.py)
    use instead of querying lead_stages directly.
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT key FROM lead_stages WHERE active=1 "
            "ORDER BY is_terminal ASC, sort_order ASC, id ASC"
        ).fetchall()
        return [r["key"] for r in rows]
    finally:
        conn.close()



def _require_configure(user):
    """Refuse a configuration write before anything is read or written.

    The HTTP handlers check too, but they are not the only callers: the
    assistant's confirmed actions reach these writers directly, and so would
    any future entry point.
    """
    if not can_configure(user):
        raise PermissionError(t("Access denied."))


def create_stage(data, user):
    """Create a producer-defined sales stage within the stage limit."""
    data = _validated_stage_create(data)
    conn = get_conn()
    try:
        producer_count = conn.execute(
            "SELECT COUNT(*) FROM lead_stages WHERE kind = 'progress'"
        ).fetchone()[0]
        if producer_count >= MAX_PRODUCER_STAGES:
            raise ValueError(
                t("The maximum number of stages the vendor can set is {count}.", count=MAX_PRODUCER_STAGES)
            )

        key = _generate_unique_key(conn, data["name"])
        color = data.get("color", "#6246ea")
        sort_order = data.get("sort_order", producer_count)
        now = datetime.utcnow().isoformat()

        c = conn.execute(
            """INSERT INTO lead_stages
               (key, name, name_en, sort_order, kind, is_terminal, color, active, created_at)
               VALUES (?,?,?,?,?,0,?,1,?)""",
            (key, data["name"], data.get("name_en", ""), sort_order, data["kind"],
             color, now),
        )
        conn.commit()
        stage_id = c.lastrowid
        audit.log(user, "stage.create", "lead_stage", str(stage_id), f"{data['name']} ({key})")
        return get_by_id(stage_id)
    finally:
        conn.close()


def update_stage(stage_id, data, user):
    """Update a stage's name, colour or position in the pipeline.

    ``key``, ``kind`` and ``is_terminal`` are immutable after creation --
    they are simply absent from STAGE_UPDATE_FIELDS, so
    ``parse_allowed_fields`` refuses any attempt to send them. The two
    terminals cannot be edited, retyped or deleted.
    """
    _require_configure(user)
    stage_id = parse_integer_id(stage_id, N_("Stage ID"))
    data = _validated_stage_update(data)
    if not data:
        raise ValueError(t("No data to update."))

    audit_detail = str(data)

    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM lead_stages WHERE id=?", (stage_id,)).fetchone()
        if not row:
            raise ValueError(t("The stage does not exist."))
        if row["is_terminal"]:
            raise ValueError(t("Win/Lose stages cannot be edited."))

        updates = []
        params = []
        if "name" in data:
            updates.append("name=?")
            params.append(data["name"])
        if "name_en" in data:
            updates.append("name_en=?")
            params.append(data["name_en"])
        if "color" in data:
            updates.append("color=?")
            params.append(data["color"])
        if "sort_order" in data:
            updates.append("sort_order=?")
            params.append(data["sort_order"])

        if not updates:
            raise ValueError(t("No data to update."))

        params.append(stage_id)
        conn.execute(f"UPDATE lead_stages SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
        audit.log(user, "stage.update", "lead_stage", str(stage_id), audit_detail)
        return get_by_id(stage_id)
    finally:
        conn.close()


def delete_stage(stage_id, user):
    """Delete a producer-defined stage no lead currently occupies."""
    _require_configure(user)
    stage_id = parse_integer_id(stage_id, N_("Stage ID"))
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM lead_stages WHERE id=?", (stage_id,)).fetchone()
        if not row:
            raise ValueError(t("The stage does not exist."))
        if row["is_terminal"]:
            raise ValueError(t("A terminal stage (Win/Lose) cannot be deleted."))

        count = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status=?", (row["key"],)
        ).fetchone()[0]
        if count > 0:
            raise ValueError(t("Cannot delete: {count} leads are in this stage.", count=count))

        conn.execute("DELETE FROM lead_stages WHERE id=?", (stage_id,))
        conn.commit()
        audit.log(user, "stage.delete", "lead_stage", str(stage_id), row["key"])
        return True
    finally:
        conn.close()


# ─── HTTP HANDLERS ────────────────────────────────────────────────────────────

def handle_list(handler, user, qs):
    return 200, {"stages": get_all()}


def handle_create(handler, user, body):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    try:
        stage = create_stage(body, user)
        return 201, {"stage": stage}
    except ValueError as e:
        return 400, {"error": str(e)}


def handle_update(handler, user, stage_id, body):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    try:
        stage = update_stage(stage_id, body, user)
        return 200, {"stage": stage}
    except ValueError as e:
        return 400, {"error": str(e)}


def handle_delete(handler, user, stage_id):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    try:
        delete_stage(stage_id, user)
        return 200, {"ok": True}
    except ValueError as e:
        return 400, {"error": str(e)}
