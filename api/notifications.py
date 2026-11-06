"""
PartnerType — Notifications Module
Create, list, mark-read, delete notifications.
Auto-creates notifications on lead status changes.
"""

import json
import re

from api.db import get_conn
from api.validation import (
    parse_allowed_fields,
    parse_enum,
    parse_integer_id,
    parse_optional_string,
    parse_required_string,
)
from api.i18n import N_, t

TYPES = ("info", "success", "warning", "error")
_SAFE_LINK_RE = re.compile(
    r"^(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.html(?:[?#][A-Za-z0-9_=&?%.-]*)?$"
)


_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
MAX_PARAMS_LENGTH = 1000
# The longest piece of typed text (a client or partner name) a title quotes.
# Titles are capped at 200 characters, so a long name is shortened instead of
# making the whole notification invalid after the event it reports was saved.
MAX_QUOTED_LENGTH = 120


def quoted(value, limit=MAX_QUOTED_LENGTH):
    """Typed text shortened to fit inside a notification title."""
    text = str(value if value is not None else "")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _validated_keys(title_key, body_key, params):
    """The translatable half of a notification.

    A notification is written when the event happens and read later, possibly
    by somebody who has since switched the app to the other language. A
    finished sentence cannot be translated after the fact, so the sentence is
    stored as a dictionary key plus its values, and js/notifications.js
    composes it in the reader's language. The literal Polish still goes into
    `title`/`body`: it is what rows written before this carried, and it is the
    fallback when a key is missing from the dictionary.
    """
    for key in (title_key, body_key):
        if key and not _KEY_RE.fullmatch(str(key)):
            raise ValueError(t("The translation key must be a dictionary identifier."))
    if params in (None, ""):
        encoded = ""
    else:
        if not isinstance(params, dict):
            raise ValueError(t("The substitution values must be an object."))
        # Values only, and only the simple kinds a sentence can hold. Nesting
        # would be a place for something else to hide.
        for name, value in params.items():
            if not _KEY_RE.fullmatch(str(name)):
                raise ValueError(t("A value name must be an identifier."))
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise ValueError(t("A substitution value must be text or a number."))
        encoded = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > MAX_PARAMS_LENGTH:
            raise ValueError(t("Too many substitution values."))
    return str(title_key or ""), str(body_key or ""), encoded


def _validated_notification(user_id, type_, title, body, link):
    user_id = parse_integer_id(user_id, N_("User ID"))
    type_ = parse_enum(type_, N_("Notification type"), TYPES)
    title = parse_required_string(title, N_("Title"), max_length=200)
    body = parse_optional_string(body, N_("Body"), max_length=1000)
    link = parse_optional_string(link, N_("Link"), max_length=500)
    lowered = link.lower()
    if link and (
        not _SAFE_LINK_RE.fullmatch(link)
        or "%0" in lowered
        or "%1" in lowered
        or "%7f" in lowered
    ):
        raise ValueError(t("Field 'Link' must point to a relative page of the app."))
    return user_id, type_, title, body, link


def _insert_notification(user_id, type_, title, body, link,
                        title_key="", body_key="", params=""):
    conn = get_conn()
    try:
        cursor = conn.execute(
            "INSERT INTO notifications "
            "(user_id, type, title, body, link, title_key, body_key, params) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (user_id, type_, title, body, link, title_key, body_key, params),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


# ─── CORE ────────────────────────────────────────────────────────────────────

def create_notification(user_id: int, type_: str, title: str, body: str = "",
                       link: str = "", *, title_key="", body_key="", params=None):
    """Validate and insert one ownership-scoped notification.

    `title`/`body` are the finished sentence in the writer's language. `title_key`,
    `body_key` and `params` are the same sentence in a form the reader's
    browser can compose in either language -- pass them and the literal both,
    so the row reads correctly whichever end is doing the reading.
    """
    return _insert_notification(
        *_validated_notification(user_id, type_, title, body, link),
        *_validated_keys(title_key, body_key, params),
    )


def notify_partner_lead_submitted(user_id, lead_id, client_name):
    """Record the partner's in-app confirmation for a submitted lead."""
    # Writes through _insert_notification, not through create_notification:
    # the test suite replaces the latter to mute side effects, and this
    # confirmation is the partner's own receipt for the write that just
    # happened -- it has to land even in a muted run.
    return _insert_notification(
        *_validated_notification(
            user_id,
            "info",
            t("Lead submitted: {client}", client=quoted(client_name)),
            t("Submission {lead} was accepted for review.", lead=lead_id),
            "leads.html",
        ),
        *_validated_keys(
            "ntf_lead_submitted_title",
            "ntf_lead_submitted_body",
            {"client": client_name, "lead": lead_id},
        ),
    )


def notify_all_admins(type_: str, title: str, body: str = "", link: str = "",
                      *, title_key="", body_key="", params=None):
    """Send a notification to every admin user."""
    _, type_, title, body, link = _validated_notification(
        1, type_, title, body, link
    )
    title_key, body_key, encoded = _validated_keys(title_key, body_key, params)
    conn = get_conn()
    try:
        # Not from users: a partner's own action notifies the producer, and a
        # partner context cannot read staff rows (migration 0017).
        admins = conn.execute("SELECT id FROM active_staff() WHERE role = 'admin'").fetchall()
        for a in admins:
            conn.execute(
                "INSERT INTO notifications "
                "(user_id, type, title, body, link, title_key, body_key, params) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (a["id"], type_, title, body, link, title_key, body_key, encoded)
            )
        conn.commit()
    finally:
        conn.close()


def _decoded_notification(row):
    """Hand the client an object, not a JSON string it has to parse itself.

    A malformed `params` degrades to no substitutions rather than breaking the
    list: the stored Polish sentence is still there to fall back on.
    """
    record = dict(row)
    raw = record.get("params") or ""
    if raw:
        try:
            decoded = json.loads(raw)
            record["params"] = decoded if isinstance(decoded, dict) else {}
        except (ValueError, TypeError):
            record["params"] = {}
    else:
        record["params"] = {}
    return record


def list_notifications(user_id: int, limit: int = 30, unread_only: bool = False):
    user_id = parse_integer_id(user_id, N_("User ID"))
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError(t("The notification limit must be a number from 1 to 100."))
    if not isinstance(unread_only, bool):
        raise ValueError(t("The unread filter must be a boolean."))
    conn = get_conn()
    try:
        sql = "SELECT * FROM notifications WHERE user_id = ?"
        params = [user_id]
        if unread_only:
            sql += " AND is_read = 0"
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [_decoded_notification(r) for r in rows]
    finally:
        conn.close()


def count_unread(user_id: int) -> int:
    user_id = parse_integer_id(user_id, N_("User ID"))
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",
            (user_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def mark_read(user_id: int, notif_id=None):
    """Mark one or all notifications as read."""
    user_id = parse_integer_id(user_id, N_("User ID"))
    if notif_id is not None:
        notif_id = parse_integer_id(notif_id, N_("Notification ID"))
    conn = get_conn()
    try:
        if notif_id:
            conn.execute(
                "UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?",
                (notif_id, user_id)
            )
        else:
            conn.execute(
                "UPDATE notifications SET is_read=1 WHERE user_id=?",
                (user_id,)
            )
        conn.commit()
    finally:
        conn.close()


def delete_notification(user_id: int, notif_id: int):
    user_id = parse_integer_id(user_id, N_("User ID"))
    notif_id = parse_integer_id(notif_id, N_("Notification ID"))
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM notifications WHERE id=? AND user_id=?",
            (notif_id, user_id)
        )
        conn.commit()
    finally:
        conn.close()


# ─── HTTP HANDLERS ─────────────────────────────────────────────────────────────

def handle_list(handler, user: dict, qs: dict):
    try:
        unread_value = qs.get("unread", [None])[0]
        if unread_value not in (None, "0", "1"):
            raise ValueError(t("Filter 'unread' must be 0 or 1."))
        unread_only = unread_value == "1"
        notifs = list_notifications(user["id"], unread_only=unread_only)
        unread = count_unread(user["id"])
        return 200, {"notifications": notifs, "unread": unread}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_mark_read(handler, user: dict, qs: dict, body: dict):
    try:
        body = parse_allowed_fields(body, ("id",), N_("Notification read state"))
        notif_id = body.get("id")  # None = mark all
        mark_read(user["id"], notif_id)
        return 200, {"ok": True, "unread": count_unread(user["id"])}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_delete(handler, user: dict, notif_id: int):
    try:
        delete_notification(user["id"], notif_id)
        return 200, {"ok": True}
    except ValueError as exc:
        return 400, {"error": str(exc)}
