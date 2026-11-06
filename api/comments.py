"""
PartnerType — Lead Comments Module
CRUD for threaded comments on leads.
"""
from datetime import datetime
from api import audit
from api.authz import can_read_leads, is_staff
from api.db import get_conn
from api.validation import parse_required_string
from api.i18n import N_, t

# The same bound as a decision message: a comment is read on the same screen.
MAX_COMMENT_LENGTH = 4000


# Who can have written a comment a partner may read: the producer's staff
# (names and roles only) and the partner himself. Bound parameter: his id.
_PARTNER_AUTHORS = (
    "SELECT id, name, role, '' AS company FROM staff_directory "
    "UNION ALL SELECT id, name, role, company FROM users "
    "WHERE id = ? AND role NOT IN ('admin', 'manager', 'observer')"
)

def list_comments(lead_id: str, user: dict):
    if not can_read_leads(user):
        raise PermissionError(t("No access to comments."))
    conn = get_conn()
    try:
        lead = conn.execute(
            "SELECT partner_id FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        if lead is None:
            raise ValueError(t("The lead does not exist."))
        if user["role"] == "partner" and lead["partner_id"] != user.get("id"):
            raise PermissionError(t("No access to this lead."))
        if is_staff(user):
            rows = conn.execute("""
                SELECT lc.*, u.name as author_name, u.role as author_role, u.company as author_company
                FROM lead_comments lc
                JOIN users u ON u.id = lc.user_id
                WHERE lc.lead_id = ?
                ORDER BY lc.created_at ASC
            """, (lead_id,)).fetchall()
        else:
            # Partners see only non-internal comments, and the producer's staff
            # only through staff_directory (migration 0017): the authors are
            # the staff and the partner himself.
            rows = conn.execute(f"""
                SELECT lc.*, a.name as author_name, a.role as author_role, a.company as author_company
                FROM lead_comments lc
                JOIN ({_PARTNER_AUTHORS}) a ON a.id = lc.user_id
                WHERE lc.lead_id = ? AND lc.is_internal = 0
                ORDER BY lc.created_at ASC
            """, (user["id"], lead_id)).fetchall()

        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_comment(lead_id: str, user: dict, body: str, is_internal: bool = False):
    if not isinstance(body, str) or not body.strip():
        raise ValueError(t("The comment text is required."))
    body = parse_required_string(body, N_("Comment"), max_length=MAX_COMMENT_LENGTH)
    if is_internal and not is_staff(user):
        raise PermissionError(t("Only an administrator can add internal comments."))
    # Fail closed on who may write a comment at all: the observer can see a
    # lead but is neither staff nor the deal's own partner, and reading the
    # statuses is the whole of its access. Stated as an allowlist so a future role has to be admitted
    # deliberately rather than inheriting write access by omission.
    if not is_staff(user) and user.get("role") != "partner":
        raise PermissionError(t("This role cannot add comments."))

    # Verify lead exists and user has access
    conn = get_conn()
    try:
        lead = conn.execute("SELECT partner_id FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead:
            raise ValueError(t("The lead does not exist."))
        if user["role"] == "partner" and lead["partner_id"] != user["id"]:
            raise PermissionError(t("No access to this lead."))

        now = datetime.utcnow().isoformat()
        cursor = conn.execute(
            "INSERT INTO lead_comments (lead_id, user_id, body, is_internal, created_at) VALUES (?,?,?,?,?)",
            (lead_id, user["id"], body.strip(), 1 if is_internal else 0, now)
        )
        conn.commit()
        row = conn.execute("""
            SELECT lc.*, u.name as author_name, u.role as author_role, u.company as author_company
            FROM lead_comments lc
            JOIN users u ON u.id = lc.user_id
            WHERE lc.id = ?
        """, (cursor.lastrowid,)).fetchone()  # the author is the caller: his own row
        return dict(row)
    finally:
        conn.close()


def delete_comment(comment_id: int, user: dict):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM lead_comments WHERE id=?", (comment_id,)).fetchone()
        if not row:
            raise ValueError(t("The comment does not exist."))
        if not is_staff(user) and row["user_id"] != user["id"]:
            raise PermissionError(t("You cannot delete this comment."))
        # A registration decision's reason and a partner's answer to a
        # request for information are part of the lead's history: the
        # workflow event that wrote them names the comment, and such a
        # comment stays.
        recorded = conn.execute(
            """SELECT 1 FROM lead_workflow_events
                WHERE lead_id = ?
                  AND CAST(payload_json AS jsonb) ->> 'comment_id' = ?
                LIMIT 1""",
            (row["lead_id"], str(row["id"])),
        ).fetchone()
        if recorded:
            raise PermissionError(
                t("This comment is part of the decision history and cannot be deleted.")
            )
        conn.execute("DELETE FROM lead_comments WHERE id=?", (comment_id,))
        conn.commit()
    finally:
        conn.close()
    # The id and visibility only: the text may carry customer data.
    audit.log(user, "comment.delete", "lead", row["lead_id"],
              t("comment #{id}", id=row['id'])
              + (t(" (internal)") if row["is_internal"] else ""))


# ─── HTTP HANDLERS ────────────────────────────────────────────────────────────

def handle_list(handler, user, lead_id):
    try:
        comments = list_comments(lead_id, user)
        return 200, {"comments": comments}
    except PermissionError as e:
        return 403, {"error": str(e)}
    except ValueError as e:
        return 404, {"error": str(e)}


def handle_create(handler, user, lead_id, body):
    if not isinstance(body, dict):
        return 400, {"error": t("Invalid comment data.")}
    text        = body.get("body", "")
    is_internal = bool(body.get("is_internal", False))
    try:
        comment = create_comment(lead_id, user, text, is_internal)
        return 201, {"comment": comment}
    except PermissionError as e:
        return 403, {"error": str(e)}
    except ValueError as e:
        return 400, {"error": str(e)}


def handle_delete(handler, user, comment_id):
    try:
        delete_comment(comment_id, user)
        return 200, {"ok": True}
    except PermissionError as e:
        return 403, {"error": str(e)}
    except ValueError as e:
        return 404, {"error": str(e)}
