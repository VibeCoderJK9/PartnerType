"""
PartnerType — Partners Module
Partner list, stats, and profile.
"""

from api.authz import is_staff
from api.db import get_conn
from api.i18n import t


def get_partners():
    """Return all partners with aggregated lead stats.

    Acceptance lives on the registration axis (`registration_status`);
    `status` holds the sales stage. Counting `status='accepted'` -- a value no
    lead carries any more -- showed every partner with 0 accepted leads on the
    dashboard's top partners.
    """
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT
                u.id,
                u.name,
                u.email,
                u.company,
                u.level,
                u.tier_id,
                t.name  AS tier_name,
                t.color AS tier_color,
                t.name_en AS tier_name_en,
                u.created_at AS joined_at,
                COUNT(l.id)                                     AS total_leads,
                SUM(CASE WHEN l.registration_status='accepted' THEN 1 ELSE 0 END) AS accepted_leads,
                SUM(CASE WHEN l.commission IS NOT NULL THEN l.commission ELSE 0 END) AS total_earnings
            FROM users u
            LEFT JOIN commission_tiers t ON t.id = u.tier_id
            LEFT JOIN leads l ON l.partner_id = u.id
            WHERE u.role = 'partner' AND u.active = 1
            GROUP BY u.id, t.id
            ORDER BY total_earnings DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_partner(partner_id: int):
    """Return a single partner with leads."""
    conn = get_conn()
    try:
        user = conn.execute("""
            SELECT
                u.id, u.name, u.email, u.company, u.level, u.created_at AS joined_at,
                COUNT(l.id)                                     AS total_leads,
                SUM(CASE WHEN l.registration_status='accepted' THEN 1 ELSE 0 END) AS accepted_leads,
                SUM(CASE WHEN l.commission IS NOT NULL THEN l.commission ELSE 0 END) AS total_earnings
            FROM users u
            LEFT JOIN leads l ON l.partner_id = u.id
            WHERE u.id = ? AND u.role = 'partner' AND u.active = 1
            GROUP BY u.id
        """, (partner_id,)).fetchone()

        if user is None:
            return None

        leads = conn.execute(
            "SELECT * FROM leads WHERE partner_id = ? ORDER BY created_at DESC",
            (partner_id,)
        ).fetchall()

        result = dict(user)
        result["leads"] = [dict(l) for l in leads]
        return result
    finally:
        conn.close()


# ─── HTTP HANDLERS ─────────────────────────────────────────────────────────────

def handle_list(handler, user: dict):
    if not is_staff(user):
        return 403, {"error": t("Only an administrator can see the partner list.")}
    partners = get_partners()
    return 200, {"partners": partners}


def handle_get(handler, user: dict, partner_id: int):
    if not is_staff(user):
        return 403, {"error": t("Access denied.")}
    partner = get_partner(partner_id)
    if partner is None:
        return 404, {"error": t("Partner not found.")}
    return 200, {"partner": partner}
