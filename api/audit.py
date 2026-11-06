"""
PartnerType — Audit Log Module
Records every significant action with user context, entity, and details.
"""
import json
from datetime import datetime
from api.authz import can_view_audit
from api.db import get_conn
from api.i18n import N_, t


# Fallback names of audit actions (the audit screen prefers its own
# dictionary keys). English here, Polish in api/locale/pl.json.
ACTION_LABELS = {
    "lead.create":                   N_("Lead added"),
    "lead.update":                   N_("Lead updated"),
    "lead.accept":                   N_("Lead accepted"),
    "lead.accepted":                 N_("Lead accepted"),
    "lead.reject":                   N_("Lead rejected"),
    "lead.rejected":                 N_("Lead rejected"),
    "lead.bulk_accept":              N_("Bulk lead acceptance"),
    "lead.bulk_reject":              N_("Bulk lead rejection"),
    "lead.commission":               N_("Commission changed"),
    "lead.commission_rate":          N_("Commission rate corrected"),
    "comment.create":                N_("Comment added"),
    "comment.delete":                N_("Comment deleted"),
    "user.login":                    N_("Sign-in"),
    "user.login_failed":             N_("Failed sign-in"),
    "user.login_mfa_pending":        N_("Sign-in — waiting for the code"),
    "user.mfa_enabled":              N_("Second factor turned on"),
    "user.mfa_disabled":             N_("Second factor turned off"),
    "data.person_exported":          N_("Person's data released on request"),
    "data.person_erased":            N_("Person's data erased on request"),
    "data.retention_applied":        N_("Data retention applied"),
    "user.logout":                   N_("Sign-out"),
    "user.logout_all":               N_("Signed out of all devices"),
    "user.create":                   N_("User added"),
    "user.update":                   N_("Profile updated"),
    "user.password":                 N_("Password changed"),
    "user.password_reset":           N_("New password set with a link"),
    "user.password_reset_requested": N_("Password link requested"),
    "user.password_reset_throttled": N_("Excess password requests refused"),
    "user.delete":                   N_("Account deleted"),
    "user.invitation_accepted":      N_("Invitation accepted"),
    "payout.confirm_win":            N_("Win confirmed"),
    "payout.record":                 N_("Commission payout recorded"),
    "payout.reverse":                N_("Commission payout reversed"),
    "settings.update":               N_("Settings changed"),
    "product.create":                N_("Product added"),
    "product.update":                N_("Product updated"),
    "product.delete":                N_("Product removed from the catalogue"),
    "knowledge.upload":              N_("Document added to the knowledge base"),
    "knowledge.update":              N_("Document details changed"),
    "knowledge.delete":              N_("Document removed from the knowledge base"),
    "stage.update":                  N_("Lead stages changed"),
    "partner.tier":                  N_("Partner tier changed"),
    "bonus.assign":                  N_("Bonus assigned"),
    "export.leads":                  N_("CSV lead export"),
    "export.commissions":            N_("CSV commission export"),
    "agent.chat":                    N_("Message to the AI assistant"),
}


def _decoded_entry(row):
    """Hand the client an object for the values, not a JSON string."""
    record = dict(row)
    raw = record.get("detail_params") or ""
    if raw:
        try:
            decoded = json.loads(raw)
            record["detail_params"] = decoded if isinstance(decoded, dict) else {}
        except (ValueError, TypeError):
            record["detail_params"] = {}
    else:
        record["detail_params"] = {}
    return record


def _encoded_detail_params(params):
    """Values for a translatable detail, or "" when the detail is plain data.

    Most audit details are already language-neutral -- a name, an id, an arrow
    between two values. Only the ones that are SENTENCES need a key, and those
    pass their values through here.
    """
    if not params:
        return ""
    if not isinstance(params, dict):
        return ""
    clean = {
        str(name): value for name, value in params.items()
        if isinstance(value, (str, int, float)) and not isinstance(value, bool)
    }
    try:
        encoded = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return ""
    return encoded if len(encoded) <= 1000 else ""


def log(user, action: str, entity: str = "", entity_id: str = "", detail: str = "",
        ip: str = "", *, detail_key: str = "", detail_params=None):
    """Write one audit log entry. Silently absorbs DB errors so it never breaks flow.

    `detail` is the text as written at the time. `detail_key` + `detail_params`
    are the same thing as a dictionary key the reader's browser composes in
    his own language -- pass them when the detail is a SENTENCE. A detail made
    only of names, ids and values needs neither: it reads the same either way.
    """
    try:
        conn = get_conn()
    except Exception:
        return   # audit must never break the main flow
    try:
        now = datetime.utcnow().isoformat()
        uid   = user.get("id")   if user else None
        uname = user.get("name") if user else "System"
        urole = user.get("role") if user else ""
        conn.execute(
            """INSERT INTO audit_log
               (user_id, user_name, user_role, action, entity, entity_id, detail, ip,
                created_at, detail_key, detail_params)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, _text(uname), _text(urole), _text(action),
             _text(entity), _text(entity_id), _text(detail), _text(ip), now,
             _text(detail_key), _text(_encoded_detail_params(detail_params)))
        )
        conn.commit()
    except Exception:
        pass   # audit must never break the main flow
    finally:
        # Always back to the pool: a connection kept after a failed insert
        # would never be returned, and enough of them stop the whole service.
        try:
            conn.close()
        except Exception:
            pass


def _text(value) -> str:
    """A value PostgreSQL will store as text.

    Free text here often comes straight from a request, and a NUL character
    makes PostgreSQL refuse the whole row.
    """
    return str(value if value is not None else "").replace("\x00", "")


# Signing in and out, and the attempts that failed. Real security history, but
# on a fresh account it is all there is, and it buries the entries a producer
# opens the log for. The screen hides these unless asked.
SESSION_ACTIONS = ("user.login", "user.login_failed", "user.login_mfa_pending",
                   "user.logout", "user.logout_all")
SCOPES = ("all", "activity", "sessions")


def get_logs(limit: int = 200, offset: int = 0, action_filter: str = "",
             user_filter: str = "", date_from: str = "", date_to: str = "",
             scope: str = "all"):
    conn = get_conn()
    try:
        clauses, params = [], []
        if scope in ("activity", "sessions"):
            marks = ",".join("?" * len(SESSION_ACTIONS))
            clauses.append(f"a.action {'NOT IN' if scope == 'activity' else 'IN'} ({marks})")
            params.extend(SESSION_ACTIONS)
        if action_filter:
            clauses.append("a.action ILIKE ?")
            params.append(f"%{action_filter}%")
        if user_filter:
            clauses.append("(a.user_name ILIKE ? OR a.user_role = ?)")
            params.extend([f"%{user_filter}%", user_filter])
        if date_from:
            clauses.append("a.created_at >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("a.created_at <= ?")
            params.append(date_to + "T23:59:59")

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        total = conn.execute(
            f"SELECT COUNT(*) FROM audit_log a {where}", params
        ).fetchone()[0]

        rows = conn.execute(
            f"""SELECT a.*, u.email as user_email
                FROM audit_log a
                LEFT JOIN users u ON u.id = a.user_id
                {where}
                ORDER BY a.created_at DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset]
        ).fetchall()

        return total, [_decoded_entry(r) for r in rows]
    finally:
        conn.close()


# ─── HTTP HANDLERS ────────────────────────────────────────────────────────────

def handle_list(handler, user, qs):
    # Admin only. A moderator handling customers must not be able to read the
    # tamper-evidence trail of their own actions.
    if not can_view_audit(user):
        return 403, {"error": t("Access denied.")}
    limit  = int(qs.get("limit", ["100"])[0])
    offset = int(qs.get("offset", ["0"])[0])
    action = qs.get("action", [""])[0]
    usr    = qs.get("user",   [""])[0]
    dfrom  = qs.get("from",   [""])[0]
    dto    = qs.get("to",     [""])[0]
    # The screen's default is the work done on the data; sign-ins on request.
    scope  = qs.get("scope",  ["activity"])[0]
    if scope not in SCOPES:
        return 400, {"error": t("Unknown log scope.")}
    total, rows = get_logs(limit, offset, action, usr, dfrom, dto, scope)
    return 200, {
        "total": total,
        "logs":  rows,
        "labels": {action: t(label) for action, label in ACTION_LABELS.items()},
    }
