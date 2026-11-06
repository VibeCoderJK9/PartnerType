"""
PartnerType — Lead Fields

What the producer asks about a DEAL, beyond the fixed client identity.

Deliberately a separate module from `api/registration_fields.py`, which asks a
prospective PARTNER about themselves. The two look alike and are not the same
thing: signup answers are keyed by user_id and capped at five, lead answers are
keyed by lead_id and capped at fifty. One partner filing two deals must be able
to answer the same question differently for each — which is precisely what a
shared, user-keyed table would have made impossible.
"""
import json

from api.authz import can_configure_lead_fields
from api.db import get_conn
from api.validation import (
    parse_allowed_fields,
    parse_boolean_flag,
    parse_enum,
    parse_finite_number,
    parse_integer_id,
    parse_optional_string,
    parse_required_string,
)
from api.i18n import N_, t

FIELD_TYPES = ("text", "number", "checkbox")

# Fifty questions about a deal. Unrelated to the signup form's limit of five:
# different scope, different rule.
MAX_LEAD_FIELDS = 50
MAX_LABEL_LENGTH = 120

# Counted in Unicode code points, which is what a person means by "characters".
MAX_TEXT_LENGTH = 4000

# A number a business would actually record, with room for grosze. Outside
# this it is a typo or an attack, not an answer.
NUMBER_LIMIT = 1e12
MAX_DECIMAL_PLACES = 6

# label_en is the producer's OWN English label for the field he invented.
# Empty means "not translated yet": readers fall back to `label`, never to a
# blank caption above an input.
_CREATE_FIELDS = ("label", "label_en", "field_type", "required",
                  "partner_editable", "active", "sort_order")
_UPDATE_FIELDS = _CREATE_FIELDS


class LeadFieldConflict(Exception):
    """The definition cannot change in the way asked. Handlers map this to 409."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


# ─── DEFINITIONS ────────────────────────────────────────────────────────────

def _row_to_field(row):
    return {
        "id": row["id"],
        "label": row["label"],
        "label_en": row["label_en"],
        "field_type": row["field_type"],
        "required": bool(row["required"]),
        "partner_editable": bool(row["partner_editable"]),
        "active": bool(row["active"]),
        "sort_order": row["sort_order"],
        "revision": row["revision"],
    }


def _validated_definition(data, allowed, partial):
    values = parse_allowed_fields(data, allowed, N_("Lead field definition"))
    normalized = {}

    if "label" in values:
        normalized["label"] = parse_required_string(
            values["label"], "label", max_length=MAX_LABEL_LENGTH
        )
    elif not partial:
        raise ValueError(t("Field 'label' is required."))

    if "label_en" in values:
        normalized["label_en"] = parse_optional_string(
            values["label_en"], "label_en", max_length=MAX_LABEL_LENGTH
        )

    if "field_type" in values:
        normalized["field_type"] = parse_enum(
            values["field_type"], "field_type", FIELD_TYPES
        )

    for flag in ("required", "partner_editable", "active"):
        if flag in values:
            normalized[flag] = parse_boolean_flag(values[flag], flag)

    if "sort_order" in values:
        normalized["sort_order"] = int(parse_finite_number(
            values["sort_order"], "sort_order", minimum=0, maximum=10_000
        ))

    if not normalized:
        raise ValueError(t("No data to save."))
    return normalized


def list_fields(include_archived=True, *, conn=None):
    """
    Every definition, ordered as the producer arranged them.

    Archived ones are included by default because an answer already given
    still needs its question: a lead detail that hid retired fields would show
    values with no labels.
    """
    owns_connection = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM lead_fields ORDER BY sort_order ASC, id ASC"
        ).fetchall()
    finally:
        if owns_connection:
            conn.close()
    fields = [_row_to_field(row) for row in rows]
    if include_archived:
        return fields
    return [field for field in fields if field["active"]]


def schema_version(*, conn=None):
    """
    A token that changes whenever any definition does.

    Sent with a submission so a form filled in before the producer edited it
    is refused rather than saved against rules the partner never saw — a
    required field added meanwhile would otherwise be silently skipped.
    """
    owns_connection = conn is None
    conn = conn or get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(revision), 0) AS r FROM lead_fields"
        ).fetchone()
    finally:
        if owns_connection:
            conn.close()
    return f"{row['n']}-{row['r']}"


def create_field(data, user):
    if not can_configure_lead_fields(user):
        raise PermissionError(t("Lead fields are set up by the vendor."))
    values = _validated_definition(data, _CREATE_FIELDS, partial=False)
    values.setdefault("field_type", "text")

    conn = get_conn()
    try:
        conn.begin()
        conn.execute("SELECT pg_advisory_xact_lock(734102620)")
        active = conn.execute(
            "SELECT COUNT(*) AS n FROM lead_fields WHERE active = 1"
        ).fetchone()["n"]
        if values.get("active", 1) and active >= MAX_LEAD_FIELDS:
            raise ValueError(
                t("You can have at most {count} active lead fields.", count=MAX_LEAD_FIELDS)
            )
        if "sort_order" not in values:
            values["sort_order"] = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next FROM lead_fields"
            ).fetchone()["next"]

        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        cursor = conn.execute(
            f"INSERT INTO lead_fields ({columns}) VALUES ({placeholders})",
            list(values.values()),
        )
        row = conn.execute(
            "SELECT * FROM lead_fields WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        conn.commit()
        return _row_to_field(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_field(field_id, data, user):
    if not can_configure_lead_fields(user):
        raise PermissionError(t("Lead fields are set up by the vendor."))
    field_id = parse_integer_id(field_id, "field_id")
    values = _validated_definition(data, _UPDATE_FIELDS, partial=True)

    conn = get_conn()
    try:
        conn.begin()
        conn.execute("SELECT pg_advisory_xact_lock(734102620)")
        row = conn.execute(
            "SELECT * FROM lead_fields WHERE id = ?", (field_id,)
        ).fetchone()
        if row is None:
            raise ValueError(t("The field does not exist."))

        if "field_type" in values and values["field_type"] != row["field_type"]:
            # Reinterpreting stored text as a number would silently rewrite
            # what partners already said. Changing the question means a new
            # field, not a new reading of the old answers.
            answered = conn.execute(
                "SELECT COUNT(*) AS n FROM lead_field_values WHERE field_id = ?",
                (field_id,),
            ).fetchone()["n"]
            if answered:
                raise LeadFieldConflict(
                    t("The type of a field that already has answers cannot change. Archive "
                      "it and create a new one."),
                    "type_locked",
                )

        if values.get("active") and not row["active"]:
            active = conn.execute(
                "SELECT COUNT(*) AS n FROM lead_fields WHERE active = 1"
            ).fetchone()["n"]
            if active >= MAX_LEAD_FIELDS:
                raise ValueError(
                    t("You can have at most {count} active lead fields.", count=MAX_LEAD_FIELDS)
                )

        assignments = ", ".join(f"{name} = ?" for name in values)
        conn.execute(
            f"UPDATE lead_fields SET {assignments}, revision = revision + 1 "
            f"WHERE id = ?",
            list(values.values()) + [field_id],
        )
        updated = conn.execute(
            "SELECT * FROM lead_fields WHERE id = ?", (field_id,)
        ).fetchone()
        conn.commit()
        return _row_to_field(updated)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── ANSWERS ────────────────────────────────────────────────────────────────

def _validate_one(field, raw):
    """Validate a single answer against its definition, returning storage text.

    Stored as JSON rather than bare text so a number comes back a number and a
    checkbox comes back a boolean. The alternative — reparsing text on read —
    is how `0` and `false` turn into "empty".
    """
    kind = field["field_type"]

    if kind == "checkbox":
        # A truthy string is exactly how a "no" becomes indistinguishable from
        # a "yes", so only a real JSON boolean counts.
        if not isinstance(raw, bool):
            raise ValueError(
                t("Field '{field}' accepts only true or false.", field=field['label'])
            )
        return json.dumps(raw)

    if kind == "number":
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(t("Field '{field}' must be a number.", field=field['label']))
        number = parse_finite_number(
            raw, field["label"], minimum=-NUMBER_LIMIT, maximum=NUMBER_LIMIT
        )
        if round(number, MAX_DECIMAL_PLACES) != number:
            raise ValueError(
                t("Field '{field}' has too many decimal places.", field=field['label'])
            )
        return json.dumps(number)

    if not isinstance(raw, str):
        raise ValueError(t("Field '{field}' must be text.", field=field['label']))
    text = raw.strip()
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(
            t("Field '{field}' can have at most {max_length} characters.",
              field=field['label'], max_length=MAX_TEXT_LENGTH)
        )
    return json.dumps(text)


def _is_answered(field, stored):
    """Whether a required field counts as answered. `0` and `false` do."""
    if stored is None:
        return False
    value = json.loads(stored)
    if isinstance(value, str):
        return bool(value.strip())
    return True


def validate_values(raw_values, fields=None):
    """
    Turn a submitted `{field_id: value}` map into storage rows, or raise.

    The whole map is checked before anything is written, so a single bad
    answer cannot leave a half-filled form behind.
    """
    if raw_values is None:
        raw_values = {}
    if not isinstance(raw_values, dict):
        raise ValueError(t("Field 'lead_field_values' must be an object."))

    if fields is None:
        fields = list_fields()
    by_id = {field["id"]: field for field in fields}

    prepared = {}
    for key, raw in raw_values.items():
        try:
            field_id = int(key)
        except (TypeError, ValueError):
            raise ValueError(t("Unknown lead field: {key}", key=key))
        field = by_id.get(field_id)
        # Refused rather than dropped: silently ignoring an answer tells the
        # partner it was saved.
        if field is None:
            raise ValueError(t("Unknown lead field: {key}", key=key))
        if not field["active"]:
            raise ValueError(t("Field '{field}' is no longer collected.", field=field['label']))
        if raw is None:
            continue
        prepared[field_id] = _validate_one(field, raw)

    for field in fields:
        if field["active"] and field["required"]:
            if not _is_answered(field, prepared.get(field["id"])):
                raise ValueError(t("Field '{field}' is required.", field=field['label']))

    return prepared


def save_values_in_transaction(conn, lead_id, prepared):
    """Write prepared answers on the caller's own connection and transaction,
    so the lead and its answers commit or fail as one."""
    for field_id, stored in prepared.items():
        conn.execute(
            """INSERT INTO lead_field_values (lead_id, field_id, value)
               VALUES (?, ?, ?)
               ON CONFLICT(lead_id, field_id) DO UPDATE SET value = excluded.value""",
            (lead_id, field_id, stored),
        )


def get_values(lead_id, *, conn=None):
    """Answers for one lead, each carrying its question -- including questions
    the producer has since retired."""
    owns_connection = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(
            """SELECT v.field_id, v.value, f.label, f.field_type, f.active,
                      f.partner_editable
                 FROM lead_field_values v
                 JOIN lead_fields f ON f.id = v.field_id
                WHERE v.lead_id = ?
             ORDER BY f.sort_order ASC, f.id ASC""",
            (lead_id,),
        ).fetchall()
    finally:
        if owns_connection:
            conn.close()
    return [
        {
            "field_id": row["field_id"],
            "label": row["label"],
            "field_type": row["field_type"],
            "value": json.loads(row["value"]),
            "active": bool(row["active"]),
            "partner_editable": bool(row["partner_editable"]),
        }
        for row in rows
    ]


# ─── HTTP HANDLERS ──────────────────────────────────────────────────────────

def validate_partner_patch(conn, raw_values):
    """Partial edit: absent preserves, null clears. Never validate with a
    definition read outside the caller's locked transaction."""
    if not isinstance(raw_values, dict):
        raise ValueError(t("Field 'lead_field_values' must be an object."))
    definitions = {str(f["id"]): f for f in list_fields(conn=conn)}
    prepared, cleared = {}, []
    for key, raw in raw_values.items():
        field = definitions.get(str(key))
        if field is None:
            raise ValueError(t("Unknown lead field."))
        if not field["active"] or not field["partner_editable"]:
            raise PermissionError(t("The vendor has not made this field editable."))
        stored = None if raw is None else _validate_one(field, raw)
        if field["required"] and not _is_answered(field, stored):
            raise ValueError(t("Field '{field}' is required.", field=field['label']))
        if stored is None:
            cleared.append(field["id"])
        else:
            prepared[field["id"]] = stored
    return prepared, cleared

def handle_list(handler, user, qs):
    return 200, {"fields": list_fields(), "version": schema_version()}


def handle_create(handler, user, body):
    try:
        return 201, {"field": create_field(body, user)}
    except PermissionError as exc:
        return 403, {"error": str(exc)}
    except LeadFieldConflict as exc:
        return 409, {"error": str(exc), "code": exc.code}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_update(handler, user, field_id, body):
    try:
        return 200, {"field": update_field(field_id, body, user)}
    except PermissionError as exc:
        return 403, {"error": str(exc)}
    except LeadFieldConflict as exc:
        return 409, {"error": str(exc), "code": exc.code}
    except ValueError as exc:
        return 400, {"error": str(exc)}
