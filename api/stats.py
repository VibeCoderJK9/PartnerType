"""
PartnerType — Stats Module
Dashboard KPI aggregations + monthly breakdown + trends.
"""

from api.db import get_conn
from api.authz import is_staff
# The one list of intake-bucket names; api/leads.py owns the registration axis.
# leads.py does not import stats, so this direction is safe.
from api.leads import OVERDUE_LEAD_CONDITION, REGISTRATION_LABELS
from datetime import datetime, timedelta
from api.i18n import N_, t


def count_overdue_leads(conn):
    """Leads still awaiting a decision past the producer's own deadline.

    "Awaiting a decision" is now a value on the registration axis, not a
    position in the sales pipeline: a lead is waiting while it is 'pending'
    or 'needs_info', independent of how the producer orders their pipeline.
    The window comes from the admin-configurable
    `lead_action_days` setting, so the dashboard card, the leads filter and
    this count can never drift apart.
    """
    from api import settings_api  # imported here to avoid a circular import

    row = conn.execute(f"""
        SELECT COUNT(*) AS overdue
        FROM leads l
        WHERE {OVERDUE_LEAD_CONDITION}
    """, (settings_api.action_days(),)).fetchone()
    return row["overdue"] or 0


def _month_label(year: int, month: int) -> str:
    """A fallback month label.

    The chart builds its own label from the `year`/`month` fields beside this
    one, through Intl, so it follows the interface language. This stays for any
    consumer that does not (the CSV export, an older cached page).
    """
    months = (N_("Jan"), N_("Feb"), N_("Mar"), N_("Apr"), N_("May"), N_("Jun"),
              N_("Jul"), N_("Aug"), N_("Sep"), N_("Oct"), N_("Nov"), N_("Dec"))
    return f"{t(months[month-1])} {str(year)[-2:]}"


def get_monthly_series(partner_id: int = None, months: int = 6):
    """Return per-month lead counts and commission totals for the last N months."""
    conn = get_conn()
    try:
        series = []
        now = datetime.utcnow()
        for i in range(months - 1, -1, -1):
            # First day of month i months ago
            target = (now.replace(day=1) - timedelta(days=i * 28)).replace(day=1)
            y, m = target.year, target.month
            month_start = f"{y}-{m:02d}-01"
            if m == 12:
                month_end = f"{y+1}-01-01"
            else:
                month_end = f"{y}-{m+1:02d}-01"

            if partner_id:
                row = conn.execute("""
                    SELECT COUNT(*) as cnt,
                           SUM(CASE WHEN commission IS NOT NULL THEN commission ELSE 0 END) as comm
                    FROM leads
                    WHERE partner_id=? AND created_at >= ? AND created_at < ?
                """, (partner_id, month_start, month_end)).fetchone()
            else:
                row = conn.execute("""
                    SELECT COUNT(*) as cnt,
                           SUM(CASE WHEN l.commission IS NOT NULL THEN l.commission ELSE 0 END) as comm,
                           SUM(CASE WHEN l.registration_status='accepted' THEN 1 ELSE 0 END) as acc
                    FROM leads l
                    WHERE l.created_at >= ? AND l.created_at < ?
                """, (month_start, month_end)).fetchone()

            series.append({
                "label":      _month_label(y, m),
                # The numbers the chart formats itself, so the month name
                # follows the language the producer is reading in.
                "year":       y,
                "month":      m,
                "leads":      row["cnt"] or 0,
                "commission": round(row["comm"] or 0, 2),
                "accepted":   row["acc"] if not partner_id else 0,
            })
        return series
    finally:
        conn.close()


def get_status_distribution(partner_id: int = None):
    """Counts per SALES STAGE (leads.status), for the donut and the funnel.

    Note the axis. A lead carries two independent answers:
    ``registration_status`` (the producer's decision) and ``status`` (the
    producer-configured sales stage, NULL until the deal is accepted). This
    function reports the second one, so its keys are stage keys -- 'contact',
    'won', 'lost', None -- never 'pending' or 'needs_info'. Anything that wants
    the decision axis must call :func:`get_registration_distribution`.
    """
    conn = get_conn()
    try:
        sql = "SELECT status, COUNT(*) as cnt FROM leads"
        params = []
        if partner_id:
            sql += " WHERE partner_id=?"
            params.append(partner_id)
        sql += " GROUP BY status"
        rows = conn.execute(sql, params).fetchall()
        return {r["status"]: r["cnt"] for r in rows}
    finally:
        conn.close()


def get_registration_distribution(partner_id: int = None):
    """Counts per REGISTRATION DECISION (leads.registration_status).

    Added because the dashboard's KPI cards were reading
    :func:`get_status_distribution` and looking up 'pending', 'needs_info' and
    'accepted' in it -- keys that live on the other axis and are therefore never
    present. All three cards rendered a permanent 0 while the leads list showed
    real rows. The four keys here match the CHECK constraint on the column, and
    every one of them is reported even at zero, so a caller never has to tell
    "no leads" apart from "key missing".
    """
    conn = get_conn()
    try:
        sql = "SELECT registration_status, COUNT(*) as cnt FROM leads"
        params = []
        if partner_id:
            sql += " WHERE partner_id=?"
            params.append(partner_id)
        sql += " GROUP BY registration_status"
        rows = conn.execute(sql, params).fetchall()
        counts = {"pending": 0, "needs_info": 0, "accepted": 0, "rejected": 0}
        for row in rows:
            key = row["registration_status"]
            if key in counts:
                counts[key] = row["cnt"]
        return counts
    finally:
        conn.close()


# The three registration buckets that sit BEFORE the funnel. An accepted lead
# is deliberately absent: it is already standing on a pipeline stage, and
# counting it twice would make the slices sum to more than the lead count.
# Colour roles rather than hex, so the donut follows the theme -- a baked
# colour would stay a light-mode colour in dark mode.
# Names and colour roles mirror what the producer already sees on Settings ->
# Lead stages: api/leads.py::REGISTRATION_LABELS and Ui.REGISTRATION_BADGE_CLASSES
# (warning / info / error). The frontend prefers Ui.REGISTRATION_LABELS over the
# name sent here, so these stay a fallback for any non-browser consumer rather
# than a second place to rename a bucket.
_PRE_FUNNEL_BUCKETS = (
    ("pending",    REGISTRATION_LABELS["pending"],    "warning"),
    ("needs_info", REGISTRATION_LABELS["needs_info"], "info"),
    ("rejected",   REGISTRATION_LABELS["rejected"],   "error"),
)


def get_pipeline_distribution():
    """Every lead the producer has, in one ordered list of slices.

    The chart shows everything: how many leads sit at each funnel stage, plus
    the won, lost and not-yet-accepted ones.

    Two axes, one picture, without double counting: a lead awaiting a decision
    has no stage, and a lead on a stage has already been accepted. So the
    pre-funnel slices read the registration axis and the stage slices read the
    stage column, and together they partition the whole set.

    Stage colours come from `lead_stages.color` -- the producer's own choice --
    so a renamed or recoloured funnel moves the chart with it. Stages are
    listed in the producer's own order, terminal win/lose last, which is where
    sort_order already puts them.
    """
    conn = get_conn()
    try:
        registration = {
            row["registration_status"]: row["n"] for row in conn.execute(
                "SELECT registration_status, COUNT(*) AS n FROM leads "
                "GROUP BY registration_status").fetchall()
        }
        by_stage = {
            row["status"]: row["n"] for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM leads "
                "WHERE status IS NOT NULL GROUP BY status").fetchall()
        }

        buckets = [
            {"key": key, "name": name, "color_role": role,
             "count": registration.get(key, 0)}
            for key, name, role in _PRE_FUNNEL_BUCKETS
        ]
        for stage in conn.execute(
            "SELECT key, name, name_en, color, kind, is_terminal FROM lead_stages "
            "WHERE active = 1 ORDER BY sort_order"
        ).fetchall():
            buckets.append({
                "key": stage["key"],
                "name": stage["name"],
                # The producer's own English name travels with the bucket:
                # the chart legend reads it the same way every other screen
                # does, instead of showing the Polish name to an English
                # reader because the statistics took a shortcut.
                "name_en": stage["name_en"],
                "color": stage["color"],
                "kind": stage["kind"],
                "is_terminal": stage["is_terminal"],
                "count": by_stage.get(stage["key"], 0),
            })
        return buckets
    finally:
        conn.close()


def get_partner_ranking(limit: int = 50):
    """Every partner, ordered by the money their deals brought in.

    Revenue first, commission alongside. Two different figures, and the
    distinction is the point:

    * `revenue` -- the value of deals this partner WON. The producer's own top
      line from that partner.
    * `commission` -- what the producer owes them out of it.

    Won deals only, read from `lead_stages.kind = 'win'` rather than a
    hardcoded key, so a renamed terminal stage still counts. Commission is
    summed over every lead that carries one, which is the same basis the
    Commissions screen and the dashboard KPI use.

    Kept separate from get_top_partners(): that one orders by accepted leads
    and feeds the dashboard, and silently reordering it would change a chart
    on another screen.

    Bounded by `limit`: this rides on every /api/stats response, including the
    dashboard's, which never reads it. An unbounded ranking would grow one
    eight-column row per partner on every page load for the life of the
    programme.
    """
    conn = get_conn()
    try:
        win_keys = [r["key"] for r in conn.execute(
            "SELECT key FROM lead_stages WHERE kind = 'win'").fetchall()]
        placeholders = ",".join("?" * len(win_keys)) if win_keys else "NULL"
        rows = conn.execute(f"""
            SELECT u.id, u.name, u.company,
                   ct.name  AS tier_name,
                   COALESCE(ct.name_en, '') AS tier_name_en,
                   ct.color AS tier_color,
                   ct.base_rate AS tier_rate,
                   COUNT(l.id) AS total,
                   COALESCE(SUM(CASE WHEN l.registration_status='accepted'
                                     THEN 1 ELSE 0 END), 0) AS accepted,
                   COALESCE(SUM(CASE WHEN l.status IN ({placeholders})
                                     THEN l.value ELSE 0 END), 0) AS revenue,
                   COALESCE(SUM(CASE WHEN l.commission IS NOT NULL
                                     THEN l.commission ELSE 0 END), 0) AS commission
              FROM users u
              LEFT JOIN leads l ON l.partner_id = u.id
              LEFT JOIN commission_tiers ct ON ct.id = u.tier_id
             WHERE u.role = 'partner'
          GROUP BY u.id, ct.id
          ORDER BY revenue DESC, commission DESC, total DESC
             LIMIT ?
        """, (*win_keys, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_top_partners(limit: int = 5):
    """Top partners by accepted leads count, with their real commission tier.

    Two things this query used to get wrong, both visible on the Raporty ranking.

    It selected ``u.level`` -- the legacy bronze/silver/gold/platinum column that
    predates commission tiers. The programme has been run on ``users.tier_id`` ->
    ``commission_tiers`` for a long time now, so the ranking was showing a word
    from a different system than the one the producer configures and the one every
    other screen displays. The tier is joined properly here, with its own name,
    colour and rate.

    And the two SUMs had no COALESCE. Over a LEFT JOIN, a partner with no leads
    produces zero rows to aggregate, and SUM over zero rows is NULL rather than 0
    -- so every partner who had not yet submitted anything rendered with blank
    cells instead of zeroes. That is the owner's "sa puste pola w widzetach".
    """
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT u.id, u.name, u.company,
                   ct.name  AS tier_name,
                   COALESCE(ct.name_en, '') AS tier_name_en,
                   ct.color AS tier_color,
                   ct.base_rate AS tier_rate,
                   COUNT(l.id) as total,
                   COALESCE(SUM(CASE WHEN l.registration_status='accepted' THEN 1 ELSE 0 END), 0) as accepted,
                   COALESCE(SUM(CASE WHEN l.commission IS NOT NULL THEN l.commission ELSE 0 END), 0) as earnings
            FROM users u
            LEFT JOIN leads l ON l.partner_id = u.id
            LEFT JOIN commission_tiers ct ON ct.id = u.tier_id
            WHERE u.role='partner'
            GROUP BY u.id, ct.id
            ORDER BY accepted DESC, total DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _calc_trend(current, previous):
    """Return percent change and direction string."""
    if previous == 0:
        return {"pct": 0, "dir": "neutral"}
    pct = round(((current - previous) / previous) * 100, 1)
    return {"pct": abs(pct), "dir": "up" if pct >= 0 else "down"}


def get_admin_stats():
    """Aggregate stats for admin dashboard with trends vs last month."""
    conn = get_conn()
    try:
        now = datetime.utcnow()
        this_m = now.replace(day=1)
        prev_m = (this_m - timedelta(days=1)).replace(day=1)
        this_start = this_m.strftime("%Y-%m-%d")
        prev_start = prev_m.strftime("%Y-%m-%d")

        # Counts read the registration axis, which is fixed and total:
        # every lead is in exactly one of the four, so the buckets always sum
        # to `total`. The previous stage-derived version left a hole --- a
        # lead sitting on a win stage matched none of them and vanished from
        # every per-bucket figure while still counting in the total.
        # count_new:      awaiting the producer's decision
        # count_pending:  the producer asked for more information
        # count_accepted: approved, and in the sales pipeline
        # count_rejected: refused at registration
        def leads_in_range(start, end_dt):
            end = end_dt.strftime("%Y-%m-%d")
            return conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN l.registration_status='pending' THEN 1 ELSE 0 END) as count_new,
                       SUM(CASE WHEN l.registration_status='needs_info' THEN 1 ELSE 0 END) as count_pending,
                       SUM(CASE WHEN l.registration_status='accepted' THEN 1 ELSE 0 END) as count_accepted,
                       SUM(CASE WHEN l.registration_status='rejected' THEN 1 ELSE 0 END) as count_rejected,
                       SUM(CASE WHEN l.commission IS NOT NULL THEN l.commission ELSE 0 END) as total_comm
                FROM leads l
                WHERE l.created_at >= ? AND l.created_at < ?
            """, (start, end)).fetchone()

        cur = leads_in_range(this_start, now)
        prv = leads_in_range(prev_start, this_m)

        # All-time totals
        all_time = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN l.registration_status='pending' THEN 1 ELSE 0 END) as count_new,
                   SUM(CASE WHEN l.registration_status='needs_info' THEN 1 ELSE 0 END) as count_pending,
                   SUM(CASE WHEN l.registration_status='accepted' THEN 1 ELSE 0 END) as count_accepted,
                   SUM(CASE WHEN l.registration_status='rejected' THEN 1 ELSE 0 END) as count_rejected,
                   SUM(CASE WHEN l.commission IS NOT NULL THEN l.commission ELSE 0 END) as total_comm
            FROM leads l
        """).fetchone()

        partners = conn.execute(
            "SELECT COUNT(*) AS count FROM users WHERE role='partner' AND active=1"
        ).fetchone()

        return {
            "total_leads":       all_time["total"],
            "new_leads":         all_time["count_new"],
            "pending_leads":     all_time["count_pending"],
            "accepted_leads":    all_time["count_accepted"],
            "rejected_leads":    all_time["count_rejected"],
            "overdue_leads":     count_overdue_leads(conn),
            "total_commissions": all_time["total_comm"] or 0,
            "total_partners":    partners["count"],
            # Trends vs last month
            "trends": {
                "leads":       _calc_trend(cur["total"] or 0,         prv["total"] or 0),
                "accepted":    _calc_trend(cur["count_accepted"] or 0, prv["count_accepted"] or 0),
                "commissions": _calc_trend(cur["total_comm"] or 0,     prv["total_comm"] or 0),
            },
            # Chart data
            "monthly":     get_monthly_series(months=6),
            "status_dist": get_status_distribution(),
            "pipeline_dist": get_pipeline_distribution(),
            "partner_ranking": get_partner_ranking(),
            "registration_dist": get_registration_distribution(),
            "channel": get_channel_metrics(),
            "top_partners":get_top_partners(5),
        }
    finally:
        conn.close()


def get_partner_stats(user_id: int):
    """Aggregate stats for partner dashboard."""
    conn = get_conn()
    try:
        now = datetime.utcnow()
        this_m = now.replace(day=1)
        prev_m = (this_m - timedelta(days=1)).replace(day=1)
        this_start = this_m.strftime("%Y-%m-%d")
        prev_start = prev_m.strftime("%Y-%m-%d")

        def leads_range(start, end_dt):
            end = end_dt.strftime("%Y-%m-%d")
            return conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN l.registration_status='accepted' THEN 1 ELSE 0 END) as accepted,
                       SUM(CASE WHEN l.commission IS NOT NULL THEN l.commission ELSE 0 END) as earnings
                FROM leads l
                WHERE l.partner_id=? AND l.created_at >= ? AND l.created_at < ?
            """, (user_id, start, end)).fetchone()

        cur = leads_range(this_start, now)
        prv = leads_range(prev_start, this_m)

        # The partner's own view of the same registration axis: pending =
        # still awaiting or answering the producer; accepted = approved;
        # rejected = refused. A deal lost later in the sales pipeline is NOT
        # counted as rejected here --- it was approved, and rolling the two
        # together would misreport how often the producer says no.
        all_time = conn.execute("""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN l.registration_status IN ('pending','needs_info')
                            THEN 1 ELSE 0 END) AS pending,
                   SUM(CASE WHEN l.registration_status='accepted' THEN 1 ELSE 0 END) AS accepted,
                   SUM(CASE WHEN l.registration_status='rejected' THEN 1 ELSE 0 END) AS rejected,
                   SUM(CASE WHEN l.commission IS NOT NULL THEN l.commission ELSE 0 END) AS earnings
            FROM leads l
            WHERE l.partner_id = ?
        """, (user_id,)).fetchone()

        return {
            "total_leads":    all_time["total"],
            "pending_leads":  all_time["pending"],
            "accepted_leads": all_time["accepted"],
            "rejected_leads": all_time["rejected"],
            "total_earnings": all_time["earnings"] or 0,
            "trends": {
                "leads":    _calc_trend(cur["total"] or 0,    prv["total"] or 0),
                "accepted": _calc_trend(cur["accepted"] or 0, prv["accepted"] or 0),
                "earnings": _calc_trend(cur["earnings"] or 0, prv["earnings"] or 0),
            },
            "monthly":     get_monthly_series(partner_id=user_id, months=6),
            "status_dist": get_status_distribution(user_id),
            "registration_dist": get_registration_distribution(user_id),
        }
    finally:
        conn.close()


# ─── HTTP HANDLERS ─────────────────────────────────────────────────────────────

def handle_stats(handler, user: dict):
    if is_staff(user):
        stats = get_admin_stats()
        # The payout figures ride along with the producer's stats rather
        # than on a route of their own: the Commissions screen already fetches
        # this, and a second request for two numbers is a second thing that
        # can be forgotten.
        stats["payouts"] = get_commission_payouts()
        return 200, stats
    else:
        return 200, get_partner_stats(user["id"])


def get_channel_metrics():
    """The partner-channel health figures a mature PRM reports.

    The definitions below are the standard channel ones
    (partner activation, deal-registration approval, win rate, average deal
    size, revenue per active partner, time to first deal, channel cost);
    everything they need is already in this schema, and anything needing data we
    do not hold -- a revenue quota for pipeline coverage, a program budget for
    cost per sourced dollar -- is deliberately absent rather than guessed.

    Three judgement calls worth stating, because each one could be computed a
    flattering way instead:

    * terminal stages are read from `lead_stages.kind`, never from hardcoded
      'won'/'lost' keys -- a producer may rename them.
    * the win rate divides by CLOSED deals, not by everything ever submitted.
      An open deal has not lost; counting it as one would make an active
      pipeline look like failure.
    * approval rate divides by DECIDED registrations, for the same reason -- a
      submission still awaiting the producer is not a rejection.
    """
    conn = get_conn()
    try:
        win_keys = [r["key"] for r in conn.execute(
            "SELECT key FROM lead_stages WHERE kind = 'win'").fetchall()]
        lose_keys = [r["key"] for r in conn.execute(
            "SELECT key FROM lead_stages WHERE kind = 'lose'").fetchall()]
        open_keys = [r["key"] for r in conn.execute(
            "SELECT key FROM lead_stages WHERE is_terminal = 0").fetchall()]

        def _in(keys):
            return "(" + ",".join("?" * len(keys)) + ")" if keys else "(NULL)"

        signed = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'partner' "
            "AND account_status = 'active'").fetchone()["n"]
        activated = conn.execute(
            "SELECT COUNT(DISTINCT l.partner_id) AS n FROM leads l "
            "JOIN users u ON u.id = l.partner_id "
            "WHERE u.role = 'partner' AND u.account_status = 'active'").fetchone()["n"]

        decided = conn.execute(
            "SELECT registration_status, COUNT(*) AS n FROM leads "
            "WHERE registration_status IN ('accepted','rejected') "
            "GROUP BY registration_status").fetchall()
        decided_counts = {r["registration_status"]: r["n"] for r in decided}
        accepted = decided_counts.get("accepted", 0)
        decided_total = accepted + decided_counts.get("rejected", 0)

        won = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(value), 0) AS value, "
            "COALESCE(SUM(commission), 0) AS commission FROM leads "
            "WHERE status IN %s" % _in(win_keys), win_keys).fetchone()
        lost_count = conn.execute(
            "SELECT COUNT(*) AS n FROM leads WHERE status IN %s" % _in(lose_keys),
            lose_keys).fetchone()["n"]
        open_pipeline = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(value), 0) AS value FROM leads "
            "WHERE status IN %s" % _in(open_keys), open_keys).fetchone()

        closed = won["n"] + lost_count

        # Days from a partner's account being created to their first submission.
        # The MEDIAN, not the mean: one partner who took a year to start would
        # drag an average far away from the experience of everybody else.
        first_leads = conn.execute(
            """SELECT CAST(TRUNC(EXTRACT(EPOCH FROM (MIN(l.created_at)::timestamp - u.created_at::timestamp))/86400)
                           AS INTEGER) AS days
                 FROM users u JOIN leads l ON l.partner_id = u.id
                WHERE u.role = 'partner'
             GROUP BY u.id
               HAVING MIN(l.created_at)::timestamp >= u.created_at::timestamp"""
        ).fetchall()
        day_values = sorted(r["days"] for r in first_leads)
        if day_values:
            middle = len(day_values) // 2
            median_days = (day_values[middle] if len(day_values) % 2
                           else (day_values[middle - 1] + day_values[middle]) / 2)
        else:
            median_days = None

        def ratio(numerator, denominator):
            return round(numerator / denominator * 100, 1) if denominator else None

        return {
            "partners_signed": signed,
            "partners_activated": activated,
            "activation_rate": ratio(activated, signed),
            "registrations_decided": decided_total,
            "approval_rate": ratio(accepted, decided_total),
            "deals_closed": closed,
            "deals_won": won["n"],
            "win_rate": ratio(won["n"], closed),
            "won_value": round(won["value"], 2),
            "avg_deal_value": round(won["value"] / won["n"], 2) if won["n"] else None,
            "revenue_per_active_partner": round(won["value"] / activated, 2) if activated else None,
            "commission_recorded": round(won["commission"], 2),
            "channel_cost_rate": ratio(won["commission"], won["value"]),
            "median_days_to_first_lead": median_days,
            "open_pipeline_deals": open_pipeline["n"],
            "open_pipeline_value": round(open_pipeline["value"], 2),
        }
    finally:
        conn.close()


def get_commission_payouts():
    """What the producer owes, grouped by the month and quarter the sale
    was WON.

    The amount to pay out in a given month or quarter. get_monthly_series()
    cannot answer it: it groups by
    created_at, so a deal submitted in March and won in April would be
    reported against March. This groups by leads.won_at and counts only
    commissions actually recorded, never forecasts --- a payout figure that
    included deals still being sold would not be a payout figure.

    NOT the payout ledger, and do not wire a "to pay" label to it. This
    answers "how much was EARNED in the current month/quarter", ignoring
    whether any of it has since been paid, so it overstates the debt from the
    first payout onwards. What is still owed lives in
    api/payouts.py::get_outstanding_summary / get_partner_outstanding, derived
    from leads.commission_paid_at. Kept because it is the honest answer to a
    different question (period revenue), and it has its own tests.
    """
    now = datetime.utcnow()
    month_start = now.replace(day=1).strftime("%Y-%m-%d")
    quarter_first_month = 3 * ((now.month - 1) // 3) + 1
    quarter_start = now.replace(month=quarter_first_month, day=1).strftime("%Y-%m-%d")

    conn = get_conn()
    try:
        def total_since(start):
            row = conn.execute(
                """SELECT COALESCE(SUM(commission), 0) AS amount,
                          COUNT(*) AS deals
                     FROM leads
                    WHERE commission IS NOT NULL
                      AND won_at IS NOT NULL
                      AND won_at >= ?""",
                (start,),
            ).fetchone()
            return {"amount": row["amount"] or 0, "deals": row["deals"] or 0}

        return {
            "month": {**total_since(month_start), "since": month_start},
            "quarter": {**total_since(quarter_start), "since": quarter_start},
        }
    finally:
        conn.close()
