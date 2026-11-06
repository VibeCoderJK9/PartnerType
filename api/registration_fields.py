"""
PartnerType — Configurable registration fields

The producer decides what a prospective partner is asked beyond the four
mandatory identity fields (name, company, e-mail, phone). Up to five extra
free-text questions, each up to 200 characters, each switchable on and off
without losing what has already been answered.

Mirrors the shape of api/bonus_defs.py and api/lead_stages.py: a dedicated
table, a MAX_* constant enforced in the create path, admin-gated REST CRUD,
and an audit entry on every mutation.
"""
from datetime import datetime

from api.authz import can_configure
from api.db import get_conn
from api import audit
from api.validation import (
    parse_allowed_fields,
    parse_boolean_flag,
    parse_integer_id,
    parse_non_negative_integer,
    parse_optional_string,
    parse_required_string,
)
from api.i18n import N_, t

# At most five. A limit rather than an unbounded list because a
# registration form is a thing a stranger has to finish.
MAX_FIELDS = 5
MAX_ANSWER_LENGTH = 200

# label_en is the producer's OWN English wording of the question he wrote.
# Empty means "not translated yet": the reader gets the Polish question rather
# than a blank one.
CREATE_FIELDS = ("label", "label_en", "max_length", "sort_order")
UPDATE_FIELDS = ("label", "label_en", "max_length", "sort_order", "active")


def _validated(data, allowed):
    clean = parse_allowed_fields(data, allowed, N_("Registration field"))
    if "label" in clean:
        clean["label"] = parse_required_string(clean["label"], N_("Field name"), max_length=100)
    if "label_en" in clean:
        clean["label_en"] = parse_optional_string(
            clean["label_en"], N_("English field name"), max_length=100
        )
    if "max_length" in clean:
        clean["max_length"] = parse_non_negative_integer(
            clean["max_length"], N_("Maximum length"), maximum=MAX_ANSWER_LENGTH
        ) or MAX_ANSWER_LENGTH
    if "sort_order" in clean:
        clean["sort_order"] = parse_non_negative_integer(
            clean["sort_order"], N_("Order"), maximum=100
        )
    if "active" in clean:
        clean["active"] = 1 if parse_boolean_flag(clean["active"], N_("Active")) else 0
    return clean


def get_all():
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM registration_fields ORDER BY sort_order ASC, id ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_active():
    """The questions a registration form should actually render."""
    return [f for f in get_all() if f["active"]]


def _require_configure(user):
    """Refuse a configuration write before anything is read or written.

    The HTTP handlers check too, but they are not the only callers: the
    assistant's confirmed actions reach these writers directly, and so would
    any future entry point.
    """
    if not can_configure(user):
        raise PermissionError(t("Access denied."))


def create_field(data, user):
    data = _validated(data, CREATE_FIELDS)
    if "label" not in data:
        raise ValueError(t("The field name is required."))
    conn = get_conn()
    try:
        count = conn.execute("SELECT COUNT(*) FROM registration_fields").fetchone()[0]
        if count >= MAX_FIELDS:
            raise ValueError(
                t("The maximum number of extra registration fields is {count}.", count=MAX_FIELDS)
            )
        cursor = conn.execute(
            """INSERT INTO registration_fields
               (label, label_en, max_length, sort_order, active, created_at)
               VALUES (?,?,?,?,0,?)""",
            (
                data["label"],
                data.get("label_en", ""),
                data.get("max_length", MAX_ANSWER_LENGTH),
                data.get("sort_order", count),
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        field_id = cursor.lastrowid
    finally:
        conn.close()
    audit.log(user, "reg_field.create", "registration_field", str(field_id), data["label"])
    return get_by_id(field_id)


def get_by_id(field_id):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM registration_fields WHERE id=?", (field_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_field(field_id, data, user):
    _require_configure(user)
    field_id = parse_integer_id(field_id, N_("Field ID"))
    data = _validated(data, UPDATE_FIELDS)
    if not data:
        raise ValueError(t("No data to update."))
    conn = get_conn()
    try:
        if not conn.execute(
            "SELECT 1 FROM registration_fields WHERE id=?", (field_id,)
        ).fetchone():
            raise ValueError(t("The field does not exist."))
        assignments = ", ".join(f"{key}=?" for key in data)
        conn.execute(
            f"UPDATE registration_fields SET {assignments} WHERE id=?",
            (*data.values(), field_id),
        )
        conn.commit()
    finally:
        conn.close()
    audit.log(user, "reg_field.update", "registration_field", str(field_id), str(data))
    return get_by_id(field_id)


def delete_field(field_id, user):
    _require_configure(user)
    field_id = parse_integer_id(field_id, N_("Field ID"))
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT label FROM registration_fields WHERE id=?", (field_id,)
        ).fetchone()
        if not row:
            raise ValueError(t("The field does not exist."))
        # ON DELETE CASCADE takes the answers with it: keeping answers to a
        # question nobody can see any more would be data with no purpose.
        conn.execute("DELETE FROM registration_fields WHERE id=?", (field_id,))
        conn.commit()
    finally:
        conn.close()
    audit.log(user, "reg_field.delete", "registration_field", str(field_id), row["label"])
    return True


def save_answers(user_id, answers):
    """Persist a registrant's answers. ``answers`` maps field id -> text.

    Answers to inactive or unknown fields are ignored rather than refused:
    a producer switching a question off between a form being rendered and
    submitted must not cost a stranger their registration.
    """
    if not answers:
        return
    active = {f["id"]: f for f in get_active()}
    conn = get_conn()
    try:
        for raw_id, raw_value in answers.items():
            try:
                field_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            field = active.get(field_id)
            if not field:
                continue
            value = parse_optional_string(
                raw_value, field["label"], max_length=field["max_length"]
            )
            if not value:
                continue
            conn.execute(
                """INSERT INTO registration_field_values (user_id, field_id, value)
                   VALUES (?,?,?)
                   ON CONFLICT(user_id, field_id) DO UPDATE SET value=excluded.value""",
                (user_id, field_id, value),
            )
        conn.commit()
    finally:
        conn.close()


def get_answers(user_id):
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT rf.id, rf.label, v.value
                 FROM registration_field_values v
                 JOIN registration_fields rf ON rf.id = v.field_id
                WHERE v.user_id = ?
                ORDER BY rf.sort_order, rf.id""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─── HTTP HANDLERS ────────────────────────────────────────────────────────────

def handle_list(handler, user, qs):
    return 200, {"fields": get_all()}


def handle_public_list(handler, qs):
    """The registration form is reached by people with no account, so this
    one read is unauthenticated. It exposes only the questions themselves —
    never an answer, and never an inactive question."""
    return 200, {"fields": [
        {"id": f["id"], "label": f["label"], "label_en": f["label_en"],
         "max_length": f["max_length"]}
        for f in get_active()
    ]}


def handle_create(handler, user, body):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    try:
        return 201, {"field": create_field(body, user)}
    except ValueError as e:
        return 400, {"error": str(e)}


def handle_update(handler, user, field_id, body):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    try:
        return 200, {"field": update_field(field_id, body, user)}
    except ValueError as e:
        return 400, {"error": str(e)}


def handle_delete(handler, user, field_id):
    if not can_configure(user):
        return 403, {"error": t("Access denied.")}
    try:
        delete_field(field_id, user)
        return 200, {"deleted": True}
    except ValueError as e:
        return 400, {"error": str(e)}
