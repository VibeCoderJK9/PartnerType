"""
PartnerType — Export Module
CSV export for leads and commissions.
"""

import csv
import io
from api.db import get_conn
from api.i18n import N_, t

# What the "Status" column says for a lead with no sales stage yet: the
# producer's decision, in the words the app shows. The stage itself is written
# by its producer-given name, never its key ("won", "contact"; F11).
_REGISTRATION_WORDS = {"pending": N_("Pending"), "needs_info": N_("Needs information"),
                       "rejected": N_("Rejected")}


def _status_word(row):
    if row["stage_name"]:
        return row["stage_name"]
    word = _REGISTRATION_WORDS.get(row["registration_status"] or "")
    return t(word) if word else (row["status"] or "")


# A cell a spreadsheet would read as a formula. Partners type client names,
# companies and notes, and the producer opens this file in Excel: a partner
# could plant `=HYPERLINK("http://evil…")` or a DDE payload and have it run on
# the producer's machine. OWASP "CSV
# injection": such a text cell is prefixed with an apostrophe, which Excel and
# Sheets show as plain text. Numbers are written as numbers and never touched.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(value):
    if isinstance(value, str) and value.startswith(_FORMULA_TRIGGERS):
        return "'" + value
    return value


class _SafeWriter:
    """csv.writer that neutralises formula-like text cells."""

    def __init__(self, buf):
        self._writer = csv.writer(buf)

    def writerow(self, row):
        self._writer.writerow([_safe_cell(value) for value in row])


def _leads_csv(user: dict, status_filter: str = None) -> str:
    conn = get_conn()
    try:
        sql = """
            SELECT l.id, l.client_name, l.client_email, l.client_phone,
                   l.company, l.value, l.commission, l.status, l.registration_status,
                   s.name AS stage_name,
                   l.sector, l.notes, l.created_at,
                   u.name as partner_name, u.company as partner_company
            FROM leads l
            JOIN users u ON u.id = l.partner_id
            LEFT JOIN lead_stages s ON s.key = l.status
        """
        params = []
        wheres = []
        if user["role"] == "partner":
            wheres.append("l.partner_id = ?")
            params.append(user["id"])
        if status_filter:
            wheres.append("l.status = ?")
            params.append(status_filter)
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        sql += " ORDER BY l.created_at DESC"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    buf = io.StringIO()
    w = _SafeWriter(buf)
    w.writerow([t(h) for h in (
        N_("ID"), N_("Client"), N_("Client e-mail"), N_("Phone"), N_("Client company"),
        N_("Value (PLN)"), N_("Commission (PLN)"), N_("Status"), N_("Sector"),
        N_("Notes"), N_("Partner"), N_("Partner company"), N_("Submission date"))])
    for r in rows:
        w.writerow([
            r["id"], r["client_name"], r["client_email"], r["client_phone"] or "",
            r["company"], r["value"], r["commission"] or "",
            _status_word(r), r["sector"] or "", r["notes"] or "",
            r["partner_name"], r["partner_company"], r["created_at"][:10]
        ])
    return buf.getvalue()


def _commissions_csv(user: dict) -> str:
    conn = get_conn()
    try:
        sql = """
            SELECT l.id, l.client_name, l.company, l.value, l.commission,
                   ROUND((l.commission * 100.0 / NULLIF(l.value, 0))::numeric, 2)::double precision as rate_pct,
                   l.status, l.registration_status, s.name AS stage_name, l.created_at,
                   u.name as partner_name, u.company as partner_company,
                   t.name as tier_name
            FROM leads l
            JOIN users u ON u.id = l.partner_id
            LEFT JOIN lead_stages s ON s.key = l.status
            LEFT JOIN commission_tiers t ON t.id = u.tier_id
            WHERE l.commission IS NOT NULL
        """
        params = []
        if user["role"] == "partner":
            sql += " AND l.partner_id = ?"
            params.append(user["id"])
        sql += " ORDER BY l.created_at DESC"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    buf = io.StringIO()
    w = _SafeWriter(buf)
    w.writerow([t(h) for h in (
        N_("Lead ID"), N_("Client"), N_("Company"), N_("Value (PLN)"), N_("Commission (PLN)"),
        N_("Rate %"), N_("Status"), N_("Partner"), N_("Partner company"),
        N_("Partner tier"), N_("Date"))])
    for r in rows:
        w.writerow([
            r["id"], r["client_name"], r["company"],
            r["value"], r["commission"], r["rate_pct"] or "",
            _status_word(r), r["partner_name"], r["partner_company"],
            # The commission tier the producer configured, not the legacy
            # users.level word (bronze/silver/gold/platinum).
            r["tier_name"] or t("No tier"), r["created_at"][:10]
        ])
    return buf.getvalue()


# ─── HTTP HANDLERS ─────────────────────────────────────────────────────────────

def handle_export_leads(handler, user: dict, qs: dict):
    status_filter = qs.get("status", [None])[0]
    csv_data = _leads_csv(user, status_filter)
    filename = f"{t('leads')}_{__import__('datetime').date.today()}.csv"
    handler.send_response(200)
    handler.send_header("Content-Type", "text/csv; charset=utf-8")
    handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.end_headers()
    handler.wfile.write(("\ufeff" + csv_data).encode("utf-8"))   # BOM for Excel
    return None, None   # already sent


def handle_export_commissions(handler, user: dict, qs: dict):
    csv_data = _commissions_csv(user)
    filename = f"{t('commissions')}_{__import__('datetime').date.today()}.csv"
    handler.send_response(200)
    handler.send_header("Content-Type", "text/csv; charset=utf-8")
    handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.end_headers()
    handler.wfile.write(("\ufeff" + csv_data).encode("utf-8"))
    return None, None
