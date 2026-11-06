"""Answering a person who asks what you hold about them, or to be forgotten.

Two different people can ask. A partner has an account here. A lead's client
never did -- their name and telephone number are in the system because somebody
else typed them in, which is exactly the case the law cares most about.

Erasure is not a single DELETE. A lead that produced a commission is also an
accounting record, and a producer who deletes it stops being able to explain
what he paid his partner. So money keeps its rows and the person loses their
identity inside them.
"""
from datetime import datetime

from api import audit
from api.db import get_conn
from api.validation import parse_email
from api.i18n import N_, t

# Wiped when a lead is anonymised. Everything else on the row is either a
# business fact (value, commission, stage) or a reference somebody else owns.
LEAD_PERSONAL_COLUMNS = (
    "client_name", "client_email", "client_phone", "notes", "nip", "website",
)
_ANONYMISED = N_("[erased on request]")


def anonymise_settled_leads(conn, lead_ids, client_name=None):
    """Strip the client out of leads that must keep their amounts.

    Shared by erasure on request and by the retention period, so both remove
    the same things: the lead's personal columns, the snapshots of them in the
    history payloads, the comments written about the lead (free text, often
    the richest personal data of all) and the answers to the producer's own
    lead questions. Runs on the caller's connection and does not commit.
    """
    if client_name is None:
        client_name = t(_ANONYMISED)
    removed = {"leads_anonymised": 0, "history_scrubbed": 0,
               "comments_deleted": 0, "field_values_deleted": 0}
    if not lead_ids:
        return removed
    placeholders = ",".join("?" for _ in lead_ids)
    columns = ", ".join(f"{name} = ?" for name in LEAD_PERSONAL_COLUMNS)
    removed["leads_anonymised"] = conn.execute(
        f"UPDATE leads SET {columns} WHERE id IN ({placeholders})",
        [client_name if name == "client_name" else "" for name in LEAD_PERSONAL_COLUMNS]
        + list(lead_ids)).rowcount
    removed["history_scrubbed"] = conn.execute(
        f"""UPDATE lead_workflow_events SET payload_json = '{{}}'
             WHERE lead_id IN ({placeholders})""", list(lead_ids)).rowcount
    removed["comments_deleted"] = conn.execute(
        f"DELETE FROM lead_comments WHERE lead_id IN ({placeholders})",
        list(lead_ids)).rowcount
    removed["field_values_deleted"] = conn.execute(
        f"DELETE FROM lead_field_values WHERE lead_id IN ({placeholders})",
        list(lead_ids)).rowcount
    return removed


def _find(conn, email):
    """Every place this address appears, as an account and as a lead's client.

    `email` is already lower-case. Leads keep the client's address exactly as
    it was typed, so the comparison lower-cases the stored value as well:
    "Jan.Kowalski@Firma.pl" is the same person as "jan.kowalski@firma.pl".
    """
    account = conn.execute(
        "SELECT id, email, name, role, company, phone, bio, created_at "
        "FROM users WHERE email = ?", (email,)
    ).fetchone()
    leads = conn.execute(
        """SELECT id, client_name, client_email, client_phone, company, nip,
                  website, notes, value, commission, status, registration_status,
                  created_at, partner_id
             FROM leads WHERE lower(client_email) = ? ORDER BY created_at""",
        (email,),
    ).fetchall()
    return account, leads


def export_person(email: str, actor=None):
    """Everything held about one address, in one document.

    Deliberately generous: an export that omits a table is worse than useless,
    because the person cannot tell it is incomplete.
    """
    email = parse_email(email, N_("E-mail")).lower()
    with get_conn() as conn:
        account, leads = _find(conn, email)
        document = {
            "generated_at": datetime.utcnow().isoformat(),
            "subject": email,
            "account": dict(account) if account else None,
            "leads": [dict(row) for row in leads],
            "lead_comments": [],
            "lead_history": [],
            "lead_answers": [],
            "registration_answers": [],
            "notifications": [],
            "audit_entries": [],
            "login_attempts": [],
            "affiliate_links": [],
            "affiliate_clicks": [],
            "affiliate_orders": [],
        }
        lead_ids = [row["id"] for row in leads]
        if lead_ids:
            placeholders = ",".join("?" for _ in lead_ids)
            document["lead_comments"] = [dict(r) for r in conn.execute(
                f"SELECT id, lead_id, user_id, body, created_at FROM lead_comments "
                f"WHERE lead_id IN ({placeholders}) ORDER BY id", lead_ids).fetchall()]
            document["lead_history"] = [dict(r) for r in conn.execute(
                f"SELECT id, lead_id, event_type, payload_json, created_at "
                f"FROM lead_workflow_events WHERE lead_id IN ({placeholders}) ORDER BY id",
                lead_ids).fetchall()]
            document["lead_answers"] = [dict(r) for r in conn.execute(
                f"""SELECT v.lead_id, f.label, v.value FROM lead_field_values v
                     JOIN lead_fields f ON f.id = v.field_id
                    WHERE v.lead_id IN ({placeholders}) ORDER BY v.lead_id, f.sort_order, f.id""",
                lead_ids).fetchall()]
        if account:
            document["registration_answers"] = [dict(r) for r in conn.execute(
                """SELECT f.label, v.value FROM registration_field_values v
                   JOIN registration_fields f ON f.id = v.field_id
                   WHERE v.user_id = ?""", (account["id"],)).fetchall()]
            document["notifications"] = [dict(r) for r in conn.execute(
                "SELECT id, title, body, created_at FROM notifications WHERE user_id = ?",
                (account["id"],)).fetchall()]
            document["affiliate_links"] = [dict(r) for r in conn.execute(
                "SELECT id, code, created_at, revoked_at FROM affiliate_links "
                "WHERE partner_id = ? ORDER BY id", (account["id"],)).fetchall()]
            document["affiliate_clicks"] = [dict(r) for r in conn.execute(
                "SELECT click_id, link_id, created_at FROM affiliate_clicks "
                "WHERE partner_id = ? ORDER BY id", (account["id"],)).fetchall()]
            document["affiliate_orders"] = [dict(r) for r in conn.execute(
                """SELECT id, order_id, status, amount, refunded_amount, currency,
                          occurred_at, lead_id
                     FROM affiliate_conversions WHERE partner_id = ? ORDER BY id""",
                (account["id"],)).fetchall()]
        # Entries written by the account, and entries that name the address
        # (refused sign-ins, deletions, earlier data requests), whoever wrote
        # them. strpos rather than LIKE: an address may contain "_".
        document["audit_entries"] = [dict(r) for r in conn.execute(
            """SELECT id, action, entity, entity_id, detail, created_at
                 FROM audit_log
                WHERE user_id = ? OR lower(entity_id) = ? OR strpos(lower(detail), ?) > 0
                ORDER BY id""",
            (account["id"] if account else None, email, email)).fetchall()]
        document["login_attempts"] = [dict(r) for r in conn.execute(
            "SELECT ip, succeeded, created_at FROM login_attempts WHERE email = ? ORDER BY id",
            (email,)).fetchall()]
    audit.log(actor, "data.person_exported", "person", email)
    return document


def erasure_preview(email: str):
    """What erasing this address would remove and what it would keep.

    The second half matters more: a producer has to be able to tell the person
    truthfully that some rows survive, and why.
    """
    email = parse_email(email, N_("E-mail")).lower()
    with get_conn() as conn:
        account, leads = _find(conn, email)
        settled = [row["id"] for row in leads if row["commission"] is not None]
        erasable = [row["id"] for row in leads if row["commission"] is None]
    return {
        "subject": email,
        "account_found": bool(account),
        "account_role": account["role"] if account else None,
        "leads_to_delete": erasable,
        "leads_to_anonymise": settled,
        "keeps": t(
            "The amounts and commissions of settled leads stay: without them nobody "
            "could explain what was paid to whom. The person's data in those rows is erased."
        ),
    }


def erased_email(user_id):
    """The placeholder address an erased account keeps; other code reads it
    to tell an erased account from one that was only deactivated."""
    return f"erased-{user_id}@invalid.local"


def erase_person(email: str, actor=None, confirm: bool = False):
    """Remove the person from every record that can afford to lose them."""
    if not confirm:
        raise ValueError(t("An explicit confirmation of the data erasure is required."))
    email = parse_email(email, N_("E-mail")).lower()
    removed = {"leads_deleted": 0, "leads_anonymised": 0, "comments_deleted": 0,
               "history_scrubbed": 0, "account_anonymised": False}

    from api.authz import can_manage_users
    from api.gateway import keys as gateway_keys
    from api.users import ensure_another_admin_remains

    with get_conn() as conn:
        conn.begin()
        account, leads = _find(conn, email)
        if account:
            # Erasing your own account, or the last administrator, would leave
            # nobody able to manage users or answer the next request.
            if actor and actor.get("id") == account["id"]:
                raise ValueError(t("You cannot erase the data of your own account."))
            if can_manage_users(account):
                ensure_another_admin_remains(conn, account["id"])
        settled = [row["id"] for row in leads if row["commission"] is not None]
        erasable = [row["id"] for row in leads if row["commission"] is None]

        if erasable:
            placeholders = ",".join("?" for _ in erasable)
            removed["comments_deleted"] = conn.execute(
                f"DELETE FROM lead_comments WHERE lead_id IN ({placeholders})",
                erasable).rowcount
            conn.execute(
                f"DELETE FROM lead_workflow_events WHERE lead_id IN ({placeholders})",
                erasable)
            conn.execute(
                f"DELETE FROM lead_field_values WHERE lead_id IN ({placeholders})",
                erasable)
            conn.execute(
                f"DELETE FROM lead_workflow_operations WHERE lead_id IN ({placeholders})",
                erasable)
            removed["leads_deleted"] = conn.execute(
                f"DELETE FROM leads WHERE id IN ({placeholders})", erasable).rowcount

        if settled:
            # The history carries snapshots of the same fields in its payload
            # and the comments carry free text about the client, so clearing
            # only the lead row would leave the data one table away.
            anonymised = anonymise_settled_leads(conn, settled)
            removed["leads_anonymised"] = anonymised["leads_anonymised"]
            removed["history_scrubbed"] = anonymised["history_scrubbed"]
            removed["comments_deleted"] += anonymised["comments_deleted"]

        if account:
            # The account row itself is kept only where it has to be: deleting
            # a partner with settled commissions would orphan those payouts.
            conn.execute(
                """UPDATE users SET name = ?, email = ?, phone = '', bio = '',
                       company = '', active = 0, account_status = 'rejected'
                   WHERE id = ?""",
                (t(_ANONYMISED), erased_email(account["id"]), account["id"]),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (account["id"],))
            conn.execute("DELETE FROM notifications WHERE user_id = ?", (account["id"],))
            conn.execute("DELETE FROM registration_field_values WHERE user_id = ?",
                         (account["id"],))
            conn.execute("DELETE FROM user_mfa WHERE user_id = ?", (account["id"],))
            conn.execute("DELETE FROM user_mfa_backup_codes WHERE user_id = ?", (account["id"],))
            conn.execute("DELETE FROM login_attempts WHERE email = ?", (email,))
            # API keys and OAuth connections check only that the account is
            # active; revoking them as well means a later reactivation by
            # mistake cannot bring them back.
            gateway_keys.revoke_all_for_user(conn, account["id"], "gdpr_erasure")
            conn.execute("DELETE FROM password_resets WHERE user_id = ?", (account["id"],))
            # An open invitation would let its holder set a password on the
            # anonymised account and sign in again.
            conn.execute("DELETE FROM partner_invitations WHERE user_id = ?", (account["id"],))
            # His referral link stops and his clicks go; the orders stay, like
            # settled leads, because they are the record of commission.
            conn.execute("UPDATE affiliate_links SET revoked_at = now() "
                         "WHERE partner_id = ? AND revoked_at IS NULL", (account["id"],))
            conn.execute("DELETE FROM affiliate_clicks WHERE partner_id = ?", (account["id"],))
            removed["account_anonymised"] = True
        conn.commit()

    # The audit entry names the address on purpose: proving a request was
    # carried out is itself an obligation, and it cannot be proved anonymously.
    audit.log(actor, "data.person_erased", "person", email,
              ", ".join(f"{key}: {value}" for key, value in removed.items()))
    return removed


# ─── HTTP HANDLERS ────────────────────────────────────────────────────────────

def _refuse_unless_owner(user):
    from api.authz import can_handle_data_requests
    return None if can_handle_data_requests(user) else (403, {"error": t("Access denied.")})


def handle_export(handler, user, body):
    refusal = _refuse_unless_owner(user)
    if refusal:
        return refusal
    if not isinstance(body, dict):
        return 400, {"error": t("Invalid request.")}
    try:
        return 200, export_person(body.get("email"), user)
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_erasure_preview(handler, user, body):
    refusal = _refuse_unless_owner(user)
    if refusal:
        return refusal
    if not isinstance(body, dict):
        return 400, {"error": t("Invalid request.")}
    try:
        return 200, erasure_preview(body.get("email"))
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_erase(handler, user, body):
    refusal = _refuse_unless_owner(user)
    if refusal:
        return refusal
    if not isinstance(body, dict):
        return 400, {"error": t("Invalid request.")}
    try:
        return 200, erase_person(body.get("email"), user, confirm=bool(body.get("confirm")))
    except ValueError as exc:
        return 400, {"error": str(exc)}
