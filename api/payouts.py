"""
PartnerType — Commission payouts

The producer sees how much they owe each partner in total, broken down by
month and quarter, and marks a month or a quarter as paid. A paid month
disappears from what is owed, and the quarter total shrinks with it.

Nothing here calculates a commission. `api/leads.py` owns that maths and
stamps `leads.commission` on a win; this module only records WHEN that earned
amount was handed over, and reports what is still owed.

Two design rules worth keeping:

1.  **One source of truth: `leads.commission_paid_at`.** Outstanding is the sum
    of won leads where it is NULL; paid history groups by it. Because the fact
    lives on the lead, paying a month automatically shrinks the quarter that
    contains it — there is no second total to keep in step, which is exactly
    the cascade the owner described. A `commission_payouts` row is a *receipt*
    for one act of paying, never the figure anything is derived from.

2.  **A period is derived from `won_at`, never from "now".** A deal won in
    March that is paid in June belongs to March's outstanding total and to
    March's payout, or the producer can never reconcile a month again.

Admin only. `AGENT-DESIGN.md:139` forbids payouts to the AI agent outright, and
a moderator handles customers but does not move money.
"""
from datetime import datetime

from api import audit
from api.authz import can_pay_commission, is_staff
from api.db import get_conn
from api import bonus_awards
from api.validation import (
    ValidationError,
    parse_allowed_fields,
    parse_enum,
    parse_integer_id,
    parse_optional_string,
)
from api.i18n import N_, t

PERIOD_TYPES = ("month", "quarter")
MAX_NOTE_LENGTH = 500


# ─── PERIODS ──────────────────────────────────────────────────────────────────

def month_key(won_at: str) -> str:
    """'2026-09-14T...' -> '2026-09'."""
    return str(won_at)[:7]


def quarter_key(won_at: str) -> str:
    """'2026-09-14T...' -> '2026-Q3'."""
    text = str(won_at)
    year = text[:4]
    month = int(text[5:7])
    return "%s-Q%d" % (year, (month - 1) // 3 + 1)


def _parse_period(period_type: str, period_key: str):
    """Validate a period and return (type, key) exactly as stored.

    Rejecting a malformed key here matters more than it looks: the key is what
    selects which leads get stamped, so a typo would otherwise mark the wrong
    set as paid, or none at all while reporting success.
    """
    ptype = parse_enum(period_type, N_("Period type"), PERIOD_TYPES)
    key = str(period_key or "").strip()
    if ptype == "month":
        ok = len(key) == 7 and key[4] == "-" and key[:4].isdigit() and key[5:].isdigit() \
            and 1 <= int(key[5:]) <= 12
    else:
        ok = len(key) == 7 and key[4:6] == "-Q" and key[:4].isdigit() and key[6] in "1234"
    if not ok:
        raise ValidationError(t(
            "The period has an invalid format (expected {format}).",
            format=t("YYYY-MM") if ptype == "month" else t("YYYY-Qn"),
        ))
    return ptype, key


def _period_of(row, period_type: str) -> str:
    return month_key(row["won_at"]) if period_type == "month" else quarter_key(row["won_at"])


# ─── READING WHAT IS OWED ─────────────────────────────────────────────────────

def _unpaid_rows(conn, partner_id: int = None, lock=False):
    """Won leads carrying a recorded commission that has not been paid yet.

    Three conditions, not two. `won_at IS NOT NULL` and `commission IS NOT NULL`
    together are the definition of "earned": a lead can sit on the win stage
    without a recorded commission when the producer has auto-commission switched
    off, and that is not money owed until somebody records an amount.

    `win_confirmed_at IS NOT NULL` is the third. The partner
    moving a lead onto the win stage is a claim about the world, not a fact the
    producer has agreed to -- so an unconfirmed win is NOT money owed, and does
    not appear in any outstanding figure until the producer confirms it.
    """
    sql = (
        "SELECT id, partner_id, commission, won_at FROM leads "
        "WHERE commission_paid_at IS NULL AND commission IS NOT NULL "
        "AND won_at IS NOT NULL AND win_confirmed_at IS NOT NULL"
    )
    params = []
    if partner_id:
        sql += " AND partner_id = ?"
        params.append(partner_id)
    return conn.execute(sql + " ORDER BY id ASC" + (" FOR UPDATE" if lock else ""), params).fetchall()


def get_outstanding_summary():
    """One row per partner who is owed anything, largest debt first.

    Two kinds of debt, deliberately added together: unpaid commission on
    confirmed wins, and one-off cash bonuses that were granted and not yet paid
   . A producer asking "how much do I owe this partner" means both
    -- and a bonus that showed on a card but in no total was exactly the gap
    this closes. `lead_count` still counts deals only; `bonus_count` is
    separate, because a bonus is not a deal.
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT u.id AS partner_id, u.name, u.company,
                      COUNT(l.id)        AS lead_count,
                      SUM(l.commission)  AS amount,
                      MIN(l.won_at)      AS oldest_won_at
                 FROM users u
                 JOIN leads l ON l.partner_id = u.id
                WHERE u.role = 'partner'
                  AND l.commission_paid_at IS NULL
                  AND l.commission IS NOT NULL
                  AND l.won_at IS NOT NULL
                  -- Same three conditions as _unpaid_rows, and this one is easy
                  -- to forget because the summary has its own query: an
                  -- unconfirmed win is a partner's claim, not money owed.
                  AND l.win_confirmed_at IS NOT NULL
             GROUP BY u.id
             ORDER BY amount DESC"""
        ).fetchall()
        owed = {
            r["partner_id"]: {
                "partner_id": r["partner_id"],
                "name": r["name"],
                "company": r["company"],
                "lead_count": r["lead_count"],
                "commission_amount": round(r["amount"] or 0, 2),
                "bonus_amount": 0.0,
                "bonus_count": 0,
                "oldest_won_at": r["oldest_won_at"],
            }
            for r in rows
        }
        bonuses = conn.execute(
            """SELECT a.partner_id, u.name, u.company,
                      COUNT(a.id) AS bonus_count, SUM(a.amount) AS amount
                 FROM bonus_awards a
                 JOIN users u ON u.id = a.partner_id
                WHERE a.paid_at IS NULL
             GROUP BY a.partner_id, u.name, u.company"""
        ).fetchall()
        for r in bonuses:
            entry = owed.setdefault(r["partner_id"], {
                "partner_id": r["partner_id"],
                "name": r["name"],
                "company": r["company"],
                "lead_count": 0,
                "commission_amount": 0.0,
                "bonus_amount": 0.0,
                "bonus_count": 0,
                "oldest_won_at": None,
            })
            entry["bonus_amount"] = round(r["amount"] or 0, 2)
            entry["bonus_count"] = r["bonus_count"]

        result = []
        for entry in owed.values():
            entry["amount"] = round(entry["commission_amount"] + entry["bonus_amount"], 2)
            result.append(entry)
        return sorted(result, key=lambda row: row["amount"], reverse=True)
    finally:
        conn.close()


def get_partner_outstanding(partner_id: int):
    """What one partner is owed, broken down by month and by quarter.

    Both breakdowns are returned from the same lead rows, so a quarter total is
    always exactly the sum of its months — the producer never has to wonder
    which of two numbers to trust.
    """
    partner_id = parse_integer_id(partner_id, N_("Partner"))
    conn = get_conn()
    try:
        rows = _unpaid_rows(conn, partner_id, lock=True)
        awards = bonus_awards.unpaid_rows(conn, partner_id, lock=True)
        months, quarters = {}, {}

        def bucket_for(key, store):
            return store.setdefault(key, {
                "amount": 0.0, "lead_count": 0, "bonus_amount": 0.0, "bonus_count": 0,
            })

        for row in rows:
            for store, key in ((months, month_key(row["won_at"])),
                               (quarters, quarter_key(row["won_at"]))):
                entry = bucket_for(key, store)
                entry["amount"] += row["commission"] or 0
                entry["lead_count"] += 1
        # A one-off bonus has no deal and no win date, so it is placed in the
        # period it was GRANTED in -- it then clears
        # through that period's payout alongside the commissions, and there is
        # one button to press rather than two ledgers to reconcile.
        for award in awards:
            for store, key in ((months, month_key(award["granted_at"])),
                               (quarters, quarter_key(award["granted_at"]))):
                entry = bucket_for(key, store)
                entry["amount"] += award["amount"] or 0
                entry["bonus_amount"] += award["amount"] or 0
                entry["bonus_count"] += 1

        def listed(store, period_type):
            return [
                {
                    "period_type": period_type,
                    "period_key": key,
                    "amount": round(value["amount"], 2),
                    "lead_count": value["lead_count"],
                    "bonus_amount": round(value["bonus_amount"], 2),
                    "bonus_count": value["bonus_count"],
                }
                for key, value in sorted(store.items(), reverse=True)
            ]

        commission_total = sum(r["commission"] or 0 for r in rows)
        bonus_total = sum(a["amount"] or 0 for a in awards)
        return {
            "partner_id": partner_id,
            "total": round(commission_total + bonus_total, 2),
            "commission_total": round(commission_total, 2),
            "bonus_total": round(bonus_total, 2),
            "lead_count": len(rows),
            "bonus_count": len(awards),
            "bonuses": [
                {"id": a["id"], "name": a["name"], "name_en": a["name_en"],
                 "amount": round(a["amount"], 2), "granted_at": a["granted_at"]}
                for a in awards
            ],
            "months": listed(months, "month"),
            "quarters": listed(quarters, "quarter"),
        }
    finally:
        conn.close()


def get_partner_history(partner_id: int):
    """Every payout recorded for one partner, plus per-year paid totals.

    The yearly roll-up is computed from the leads themselves rather than from
    the receipts, so a reversed payout drops out of it automatically.
    """
    partner_id = parse_integer_id(partner_id, N_("Partner"))
    conn = get_conn()
    try:
        payouts = conn.execute(
            """SELECT p.*, u.name AS paid_by_name
                 FROM commission_payouts p
                 LEFT JOIN staff_directory u ON u.id = p.paid_by
                WHERE p.partner_id = ?
             ORDER BY p.paid_at DESC""",
            (partner_id,),
        ).fetchall()
        # Commissions and paid bonuses in one per-year total: "ile wyplacilem
        # danemu partnerowi w danym roku" is one number, not two. Both are
        # derived from the paid stamp, so a reversed payout drops out of it on
        # its own.
        per_year = conn.execute(
            """SELECT year, SUM(amount) AS amount, SUM(lead_count) AS lead_count,
                      SUM(bonus_count) AS bonus_count
                 FROM (
                    SELECT substr(commission_paid_at, 1, 4) AS year,
                           SUM(commission) AS amount, COUNT(id) AS lead_count,
                           0 AS bonus_count
                      FROM leads
                     WHERE partner_id = ? AND commission_paid_at IS NOT NULL
                  GROUP BY year
                     UNION ALL
                    SELECT substr(paid_at, 1, 4) AS year,
                           SUM(amount) AS amount, 0 AS lead_count,
                           COUNT(id) AS bonus_count
                      FROM bonus_awards
                     WHERE partner_id = ? AND paid_at IS NOT NULL
                  GROUP BY year
                 )
             GROUP BY year
             ORDER BY year DESC""",
            (partner_id, partner_id),
        ).fetchall()
        return {
            "partner_id": partner_id,
            "payouts": [
                {
                    "id": r["id"],
                    "period_type": r["period_type"],
                    "period_key": r["period_key"],
                    "amount": round(r["amount"] or 0, 2),
                    "lead_count": r["lead_count"],
                    "note": r["note"],
                    "paid_at": r["paid_at"],
                    "paid_by_name": r["paid_by_name"],
                    "reversed_at": r["reversed_at"],
                }
                for r in payouts
            ],
            "years": [
                {
                    "year": r["year"],
                    "amount": round(r["amount"] or 0, 2),
                    "lead_count": r["lead_count"],
                    "bonus_count": r["bonus_count"],
                }
                for r in per_year
            ],
        }
    finally:
        conn.close()


# ─── WHAT ONE PARTNER SEES ABOUT HIS OWN MONEY ────────────────────────────────
#
# A partner sees how much was paid in a chosen month, quarter or year (or in
# total), how much is waiting for payout and how much the producer marked as
# paid.
#
# Every figure below is derived from the SAME columns the producer's own
# payout screens read, so the two sides of one deal can never disagree about
# what is owed. Nothing here calculates a commission and nothing here writes.
#
# The four states a partner's money can be in, kept apart on purpose:
#
#   paid        commission_paid_at IS NOT NULL -- handed over, receipt exists
#   due         confirmed win, amount recorded, not paid -- money owed today
#   awaiting    the partner claimed a win the producer has not confirmed yet:
#a claim about the world, NOT money owed
#   pipeline    accepted leads still in flight -- the forecast only
#
# Periods: `paid` is bucketed by WHEN IT WAS PAID, everything else by when the
# deal was won, exactly as get_partner_history and get_partner_outstanding
# already do. `pipeline` has no meaningful period (nothing has happened to it
# yet), so it is reported as a total only rather than invented into a month.

EARNINGS_PERIOD_TYPES = ("month", "quarter", "year")


def year_key(stamp: str) -> str:
    """'2026-09-14T...' -> '2026'."""
    return str(stamp)[:4]


_PERIOD_KEY_FNS = {"month": month_key, "quarter": quarter_key, "year": year_key}


def _earnings_bucket():
    return {
        "paid": 0.0, "paid_lead_count": 0, "paid_bonus": 0.0, "paid_bonus_count": 0,
        "due": 0.0, "due_lead_count": 0, "due_bonus": 0.0, "due_bonus_count": 0,
        "awaiting": 0.0, "awaiting_lead_count": 0,
    }


def _add_to_periods(periods, stamp, apply_fn):
    """Add one row's money to its month, its quarter and its year at once.

    One row, three buckets, one traversal -- which is what guarantees a
    quarter is always exactly the sum of its months and a year exactly the
    sum of its quarters. Rows with no usable date are dropped from the
    per-period view rather than bucketed under a made-up key; the totals
    still count them, so a partner is never shown a smaller grand total
    than the periods add up to.
    """
    if not stamp:
        return
    for period_type, key_fn in _PERIOD_KEY_FNS.items():
        key = key_fn(stamp)
        if not key:
            continue
        apply_fn(periods[period_type].setdefault(key, _earnings_bucket()))


def get_partner_earnings(partner_id: int):
    """Everything the partner's own Earnings screen needs, in one read."""
    partner_id = parse_integer_id(partner_id, N_("Partner"))
    conn = get_conn()
    try:
        periods = {period_type: {} for period_type in EARNINGS_PERIOD_TYPES}
        totals = _earnings_bucket()
        totals.update({"pipeline": 0.0, "pipeline_lead_count": 0})

        paid_leads = conn.execute(
            "SELECT id, commission, commission_paid_at FROM leads "
            "WHERE partner_id = ? AND commission_paid_at IS NOT NULL "
            "ORDER BY commission_paid_at DESC",
            (partner_id,),
        ).fetchall()
        for row in paid_leads:
            amount = round(row["commission"] or 0, 2)
            totals["paid"] += amount
            totals["paid_lead_count"] += 1

            def apply(bucket, amount=amount):
                bucket["paid"] += amount
                bucket["paid_lead_count"] += 1
            _add_to_periods(periods, row["commission_paid_at"], apply)

        paid_awards = conn.execute(
            "SELECT id, amount, paid_at FROM bonus_awards "
            "WHERE partner_id = ? AND paid_at IS NOT NULL",
            (partner_id,),
        ).fetchall()
        for row in paid_awards:
            amount = round(row["amount"] or 0, 2)
            totals["paid"] += amount
            totals["paid_bonus"] += amount
            totals["paid_bonus_count"] += 1

            def apply(bucket, amount=amount):
                bucket["paid"] += amount
                bucket["paid_bonus"] += amount
                bucket["paid_bonus_count"] += 1
            _add_to_periods(periods, row["paid_at"], apply)

        for row in _unpaid_rows(conn, partner_id):
            amount = round(row["commission"] or 0, 2)
            totals["due"] += amount
            totals["due_lead_count"] += 1

            def apply(bucket, amount=amount):
                bucket["due"] += amount
                bucket["due_lead_count"] += 1
            _add_to_periods(periods, row["won_at"], apply)

        # A one-off cash bonus belongs to the period it was GRANTED in --
        # the same rule get_partner_outstanding uses, so the partner's
        # "czeka na wyplate" and the producer's "do wyplaty" are one number.
        for row in bonus_awards.unpaid_rows(conn, partner_id):
            amount = round(row["amount"] or 0, 2)
            totals["due"] += amount
            totals["due_bonus"] += amount
            totals["due_bonus_count"] += 1

            def apply(bucket, amount=amount):
                bucket["due"] += amount
                bucket["due_bonus"] += amount
                bucket["due_bonus_count"] += 1
            _add_to_periods(periods, row["granted_at"], apply)

        # Claimed, not yet agreed. Deliberately its own column: calling
        # it "do wyplaty" would promise the partner money the producer has not
        # confirmed he owes.
        for row in conn.execute(
            "SELECT id, commission, won_at FROM leads "
            "WHERE partner_id = ? AND won_at IS NOT NULL "
            "AND win_confirmed_at IS NULL AND commission_paid_at IS NULL",
            (partner_id,),
        ).fetchall():
            amount = round(row["commission"] or 0, 2)
            totals["awaiting"] += amount
            totals["awaiting_lead_count"] += 1

            def apply(bucket, amount=amount):
                bucket["awaiting"] += amount
                bucket["awaiting_lead_count"] += 1
            _add_to_periods(periods, row["won_at"], apply)

        payouts = conn.execute(
            """SELECT period_type, period_key, amount, lead_count, paid_at, note
                 FROM commission_payouts
                WHERE partner_id = ? AND reversed_at IS NULL
             ORDER BY paid_at DESC""",
            (partner_id,),
        ).fetchall()

        for key in ("paid", "due", "awaiting", "paid_bonus", "due_bonus"):
            totals[key] = round(totals[key], 2)

        return {
            "partner_id": partner_id,
            "totals": totals,
            "periods": {
                period_type: [
                    dict(bucket, period_type=period_type, period_key=key,
                         **{money: round(bucket[money], 2)
                            for money in ("paid", "due", "awaiting",
                                          "paid_bonus", "due_bonus")})
                    for key, bucket in sorted(buckets.items(), reverse=True)
                ]
                for period_type, buckets in periods.items()
            },
            "payouts": [
                {
                    "period_type": r["period_type"],
                    "period_key": r["period_key"],
                    "amount": round(r["amount"] or 0, 2),
                    "lead_count": r["lead_count"],
                    "paid_at": r["paid_at"],
                    "note": r["note"],
                }
                for r in payouts
            ],
        }
    finally:
        conn.close()


def _lose_stage_keys():
    """Keys of every stage that closes a deal as lost, active or not."""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT key FROM lead_stages WHERE kind = 'lose'").fetchall()
        return {row["key"] for row in rows}
    finally:
        conn.close()


def attach_pipeline_forecast(summary, leads, lose_keys=None):
    """Add the forecast on leads still in flight to an earnings summary.

    Kept out of get_partner_earnings because the forecast is api/leads.py's
    maths, not this module's: the caller passes already-projected lead dicts
    (with_commission_projection) and this only sums them. A lead that is won,
    lost, rejected or already paid is not "in flight" and is excluded.

    A lost lead has no won_at and no commission, so only its stage says it is
    closed -- without this check every lost deal would inflate the partner's
    forecast.
    """
    if lose_keys is None:
        lose_keys = _lose_stage_keys()
    total, count = 0.0, 0
    for lead in leads or []:
        if lead.get("registration_status") != "accepted":
            continue
        if lead.get("won_at") or lead.get("commission") is not None:
            continue
        if lead.get("status") in lose_keys:
            continue
        total += float(lead.get("commission_projected") or 0)
        count += 1
    summary["totals"]["pipeline"] = round(total, 2)
    summary["totals"]["pipeline_lead_count"] = count
    return summary


# ─── CONFIRMING A WIN ─────────────────────────────────────────────────────────

def get_pending_wins():
    """Wins a partner has claimed and the producer has not yet confirmed.

    A partner moving a lead to the win stage does not prove the deal was won,
    so the producer confirms it.

    Ordered oldest first: a claim sitting here is a partner waiting to be paid,
    so the queue should be worked from the front.
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT l.id, l.company, l.client_name, l.value, l.commission,
                      l.won_at, l.partner_id, u.name AS partner_name,
                      u.company AS partner_company
                 FROM leads l
                 JOIN users u ON u.id = l.partner_id
                 JOIN lead_stages s ON s.key = l.status
                WHERE s.kind = 'win'
                  AND l.win_confirmed_at IS NULL
             ORDER BY l.won_at ASC, l.id ASC"""
        ).fetchall()
        return [
            {
                "id": r["id"],
                "company": r["company"],
                "client_name": r["client_name"],
                "value": round(r["value"] or 0, 2),
                "commission": None if r["commission"] is None else round(r["commission"], 2),
                "won_at": r["won_at"],
                "partner_id": r["partner_id"],
                "partner_name": r["partner_name"],
                "partner_company": r["partner_company"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def confirm_wins(lead_ids, user):
    """Agree that these deals really were won, making their commission payable.

    All or nothing, and idempotent per lead: confirming a lead that is already
    confirmed leaves its original timestamp alone rather than moving the date the
    producer agreed to it.
    """
    if not isinstance(lead_ids, (list, tuple)) or not lead_ids:
        raise ValidationError(t("Choose which wins you are confirming."))
    if len(lead_ids) > 200:
        raise ValidationError(t("Too many items at once (at most 200)."))
    ids = [str(value).strip() for value in lead_ids if str(value).strip()]
    if not ids:
        raise ValidationError(t("Choose which wins you are confirming."))

    confirmed_at = datetime.utcnow().isoformat()
    placeholders = ",".join("?" * len(ids))
    conn = get_conn()
    try:
        conn.begin()
        # Only leads actually standing on a win stage may be confirmed -- there
        # is nothing to agree to on a deal that is still being sold.
        eligible = conn.execute(
            "SELECT l.id FROM leads l JOIN lead_stages s ON s.key = l.status "
            "WHERE s.kind = 'win' AND l.win_confirmed_at IS NULL "
            "AND l.id IN (%s) ORDER BY l.id FOR UPDATE OF l" % placeholders,
            ids,
        ).fetchall()
        eligible_ids = [r["id"] for r in eligible]
        if not eligible_ids:
            raise ValidationError(t("There is nothing to confirm here."))
        conn.executemany(
            "UPDATE leads SET revision = revision + 1, win_confirmed_at = ?, win_confirmed_by = ? "
            "WHERE id = ? AND win_confirmed_at IS NULL",
            [(confirmed_at, (user or {}).get("id"), lead_id) for lead_id in eligible_ids],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    audit.log(
        user, "payout.confirm_win", "lead", ",".join(eligible_ids[:10]),
        detail="confirmed=%d" % len(eligible_ids),
    )
    return {"confirmed": len(eligible_ids), "ids": eligible_ids}


# ─── EVIDENCE ─────────────────────────────────────────────────────────────────

def get_evidence(year=None):
    """Who was paid, how much, for which deals, and when.

    A record of who was paid how much, for what and when, with month and
    quarter totals.

    Built from the LEADS, not from the payout receipts: the leads carry what each
    payment was actually for, and a reversed payout drops out on its own because
    reversing clears the stamp. Grouped by the month the payment was made, since
    that is the question being asked (how much in a month), while each line still
    names the deal and when it was won.
    """
    conn = get_conn()
    try:
        sql = (
            "SELECT l.id, l.company, l.client_name, l.value, l.commission, "
            "l.won_at, l.commission_paid_at, u.id AS partner_id, u.name AS partner_name, "
            "u.company AS partner_company "
            "FROM leads l JOIN users u ON u.id = l.partner_id "
            "WHERE l.commission_paid_at IS NOT NULL"
        )
        params = []
        if year:
            sql += " AND substr(l.commission_paid_at, 1, 4) = ?"
            params.append(str(year))
        rows = conn.execute(sql + " ORDER BY l.commission_paid_at DESC", params).fetchall()

        bonus_sql = (
            "SELECT a.id, a.name, a.name_en, a.amount, a.granted_at, a.paid_at, "
            "u.id AS partner_id, u.name AS partner_name, u.company AS partner_company "
            "FROM bonus_awards a JOIN users u ON u.id = a.partner_id "
            "WHERE a.paid_at IS NOT NULL"
        )
        bonus_params = []
        if year:
            bonus_sql += " AND substr(a.paid_at, 1, 4) = ?"
            bonus_params.append(str(year))
        bonus_rows = conn.execute(
            bonus_sql + " ORDER BY a.paid_at DESC", bonus_params).fetchall()

        years = sorted({r["year"] for r in conn.execute(
            "SELECT DISTINCT substr(commission_paid_at, 1, 4) AS year FROM leads "
            "WHERE commission_paid_at IS NOT NULL"
        ).fetchall()} | {r["year"] for r in conn.execute(
            "SELECT DISTINCT substr(paid_at, 1, 4) AS year FROM bonus_awards "
            "WHERE paid_at IS NOT NULL"
        ).fetchall()}, reverse=True)

        months, quarters, partners = {}, {}, {}
        entries = []
        for r in rows:
            amount = round(r["commission"] or 0, 2)
            paid_month = month_key(r["commission_paid_at"])
            paid_quarter = quarter_key(r["commission_paid_at"])
            entries.append({
                "kind": "commission",
                "lead_id": r["id"],
                "company": r["company"],
                "client_name": r["client_name"],
                "value": round(r["value"] or 0, 2),
                "amount": amount,
                "won_at": r["won_at"],
                "paid_at": r["commission_paid_at"],
                "period_key": paid_month,
                "partner_id": r["partner_id"],
                "partner_name": r["partner_name"],
                "partner_company": r["partner_company"],
            })
            for bucket, key in ((months, paid_month), (quarters, paid_quarter)):
                entry = bucket.setdefault(key, {"amount": 0.0, "lead_count": 0, "partners": set()})
                entry["amount"] += amount
                entry["lead_count"] += 1
                entry["partners"].add(r["partner_id"])
            who = partners.setdefault(r["partner_id"], {
                "partner_id": r["partner_id"],
                "name": r["partner_name"],
                "company": r["partner_company"],
                "amount": 0.0,
                "lead_count": 0,
            })
            who["amount"] += amount
            who["lead_count"] += 1

        # A paid one-off bonus is part of "komu ile wyplacilem za co i kiedy"
        # just as much as a commission is. It carries `kind` so the screen can
        # say what the line was for; a commission line carries a lead, a bonus
        # line carries the bonus name and the date it was granted.
        for r in bonus_rows:
            amount = round(r["amount"] or 0, 2)
            paid_month = month_key(r["paid_at"])
            paid_quarter = quarter_key(r["paid_at"])
            entries.append({
                "kind": "bonus",
                "bonus_id": r["id"],
                "lead_id": None,
                # A bonus line has no customer -- its "company" cell is the
                # bonus name itself, and it is the one line in this table
                # allowed a company_en: it is the PRODUCER's own label, snap-
                # shotted at grant time, never a customer's company name.
                "company": r["name"],
                "company_en": r["name_en"],
                "client_name": None,
                "value": None,
                "amount": amount,
                "won_at": r["granted_at"],
                "paid_at": r["paid_at"],
                "period_key": paid_month,
                "partner_id": r["partner_id"],
                "partner_name": r["partner_name"],
                "partner_company": r["partner_company"],
            })
            for bucket, key in ((months, paid_month), (quarters, paid_quarter)):
                entry = bucket.setdefault(
                    key, {"amount": 0.0, "lead_count": 0, "partners": set()})
                entry.setdefault("bonus_count", 0)
                entry["amount"] += amount
                entry["bonus_count"] += 1
                entry["partners"].add(r["partner_id"])
            who = partners.setdefault(r["partner_id"], {
                "partner_id": r["partner_id"],
                "name": r["partner_name"],
                "company": r["partner_company"],
                "amount": 0.0,
                "lead_count": 0,
            })
            who["amount"] += amount

        entries.sort(key=lambda row: row["paid_at"], reverse=True)

        def listed(bucket, period_type):
            return [
                {
                    "period_type": period_type,
                    "period_key": key,
                    "amount": round(value["amount"], 2),
                    "lead_count": value["lead_count"],
                    "bonus_count": value.get("bonus_count", 0),
                    "partner_count": len(value["partners"]),
                }
                for key, value in sorted(bucket.items(), reverse=True)
            ]

        return {
            "years": years,
            "year": str(year) if year else None,
            "total": round(sum(entry["amount"] for entry in entries), 2),
            "entries": entries,
            "months": listed(months, "month"),
            "quarters": listed(quarters, "quarter"),
            "partners": sorted(
                ({**who, "amount": round(who["amount"], 2)} for who in partners.values()),
                key=lambda row: row["amount"], reverse=True,
            ),
        }
    finally:
        conn.close()


# ─── PAYING ───────────────────────────────────────────────────────────────────

def record_payout(data, user):
    """Mark one partner's period as paid, and write the receipt.

    One transaction: either every lead in the period is stamped and the receipt
    exists, or nothing happened. A half-applied payout would leave the producer
    with a total he cannot reconcile against anything.
    """
    clean = parse_allowed_fields(
        data, ("partner_id", "period_type", "period_key", "note"), N_("Payout")
    )
    partner_id = parse_integer_id(clean.get("partner_id"), N_("Partner"))
    period_type, period_key = _parse_period(
        clean.get("period_type"), clean.get("period_key")
    )
    note = parse_optional_string(clean.get("note"), N_("Note"), max_length=MAX_NOTE_LENGTH)

    conn = get_conn()
    try:
        conn.begin()
        partner = conn.execute(
            "SELECT id, name FROM users WHERE id = ? AND role = 'partner' FOR UPDATE", (partner_id,)
        ).fetchone()
        if not partner:
            raise ValidationError(t("The partner was not found."))

        due = [r for r in _unpaid_rows(conn, partner_id, lock=True)
               if _period_of(r, period_type) == period_key]
        # One-off bonuses settle in the period they were granted in, through
        # this same payout: the producer pays a partner for a period, not a
        # commission and then separately a bonus.
        period_of_award = month_key if period_type == "month" else quarter_key
        due_awards = [a for a in bonus_awards.unpaid_rows(conn, partner_id, lock=True)
                      if period_of_award(a["granted_at"]) == period_key]
        if not due and not due_awards:
            # Not an error the producer can fix by retrying, and not silent
            # success either: the period is already settled or never had
            # anything in it, and saying so is the honest answer.
            raise ValidationError(t("There is nothing to pay out in this period."))

        paid_at = datetime.utcnow().isoformat()
        commission_amount = round(sum(r["commission"] or 0 for r in due), 2)
        bonus_amount = round(sum(a["amount"] or 0 for a in due_awards), 2)
        amount = round(commission_amount + bonus_amount, 2)
        conn.executemany(
            "UPDATE leads SET revision = revision + 1, commission_paid_at = ? WHERE id = ? AND commission_paid_at IS NULL",
            [(paid_at, r["id"]) for r in due],
        )
        cursor = conn.execute(
            """INSERT INTO commission_payouts
                      (partner_id, period_type, period_key, amount, lead_count,
                       note, paid_by, paid_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (partner_id, period_type, period_key, amount, len(due), note,
             (user or {}).get("id"), paid_at),
        )
        payout_id = cursor.lastrowid
        # Stamped with the payout that settled them, so reversing that payout
        # releases exactly these awards and not a later one's.
        conn.executemany(
            "UPDATE bonus_awards SET paid_at = ?, payout_id = ? "
            "WHERE id = ? AND paid_at IS NULL",
            [(paid_at, payout_id, a["id"]) for a in due_awards],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    audit.log(
        user, "payout.record", "commission_payout", str(payout_id),
        detail="partner=%s %s=%s amount=%s leads=%s bonuses=%s"
               % (partner_id, period_type, period_key, amount, len(due),
                  len(due_awards)),
    )
    return {
        "id": payout_id,
        "partner_id": partner_id,
        "period_type": period_type,
        "period_key": period_key,
        "amount": amount,
        "commission_amount": commission_amount,
        "bonus_amount": bonus_amount,
        "lead_count": len(due),
        "bonus_count": len(due_awards),
        "paid_at": paid_at,
    }


def reverse_payout(payout_id: int, data, user):
    """Undo one payout: clear the stamps it set, keep the receipt.

    The receipt is marked reversed rather than deleted. Money that was handed
    over and then clawed back is a fact about the relationship, and a producer
    reconciling a quarter needs to see that it happened.
    """
    payout_id = parse_integer_id(payout_id, N_("Payout"))
    clean = parse_allowed_fields(data or {}, ("note",), N_("Payout"))
    note = parse_optional_string(clean.get("note"), N_("Note"), max_length=MAX_NOTE_LENGTH)

    conn = get_conn()
    try:
        conn.begin()
        row = conn.execute(
            "SELECT * FROM commission_payouts WHERE id = ? FOR UPDATE", (payout_id,)
        ).fetchone()
        if not row:
            raise ValidationError(t("Payout not found."))
        if row["reversed_at"]:
            raise ValidationError(t("This payout has already been reversed."))

        reversed_at = datetime.utcnow().isoformat()
        # Scoped by the payout's own timestamp, not by recomputing the period:
        # only the leads THIS payout stamped are released, so a later payout
        # for an overlapping period is left alone.
        cleared = conn.execute(
            "UPDATE leads SET revision = revision + 1, commission_paid_at = NULL "
            "WHERE partner_id = ? AND commission_paid_at = ?",
            (row["partner_id"], row["paid_at"]),
        ).rowcount
        released_bonuses = conn.execute(
            "UPDATE bonus_awards SET paid_at = NULL, payout_id = NULL "
            "WHERE payout_id = ?", (payout_id,),
        ).rowcount
        # Only replace the note when one was actually supplied. COALESCE is no
        # help here: parse_optional_string returns "" rather than None for a
        # missing field, and COALESCE accepts an empty string happily -- so
        # reversing without a note would erase the note that recorded how the
        # money was sent.
        if note:
            conn.execute(
                "UPDATE commission_payouts SET reversed_at = ?, reversed_by = ?, "
                "note = ? WHERE id = ?",
                (reversed_at, (user or {}).get("id"), note, payout_id),
            )
        else:
            conn.execute(
                "UPDATE commission_payouts SET reversed_at = ?, reversed_by = ? "
                "WHERE id = ?",
                (reversed_at, (user or {}).get("id"), payout_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    audit.log(
        user, "payout.reverse", "commission_payout", str(payout_id),
        detail="partner=%s released_leads=%s released_bonuses=%s"
               % (row["partner_id"], cleared, released_bonuses),
    )
    return {"id": payout_id, "reversed_at": reversed_at, "released_leads": cleared,
            "released_bonuses": released_bonuses}


# ─── HTTP HANDLERS ────────────────────────────────────────────────────────────

def handle_outstanding(handler, user):
    if not is_staff(user):
        return 403, {"error": t("Access denied.")}
    return 200, {"partners": get_outstanding_summary()}


def handle_partner_outstanding(handler, user, partner_id):
    if not is_staff(user):
        return 403, {"error": t("Access denied.")}
    try:
        return 200, get_partner_outstanding(partner_id)
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_partner_history(handler, user, partner_id):
    if not is_staff(user):
        return 403, {"error": t("Access denied.")}
    try:
        return 200, get_partner_history(partner_id)
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_pending_wins(handler, user):
    if not is_staff(user):
        return 403, {"error": t("Access denied.")}
    return 200, {"wins": get_pending_wins()}


def handle_confirm_wins(handler, user, body):
    # Same gate as recording a payout: confirming a win is what makes money
    # become owed, so it is the account owner's decision, not a moderator's.
    if not can_pay_commission(user):
        return 403, {"error": t("Only an administrator can confirm a win.")}
    try:
        body = body if isinstance(body, dict) else {}
        return 200, confirm_wins(body.get("lead_ids"), user)
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_evidence(handler, user, qs):
    if not is_staff(user):
        return 403, {"error": t("Access denied.")}
    year = (qs or {}).get("year", [None])[0]
    if year is not None and not (str(year).isdigit() and len(str(year)) == 4):
        return 400, {"error": t("The year has an invalid format.")}
    return 200, get_evidence(year)


def handle_record(handler, user, body):
    if not can_pay_commission(user):
        return 403, {"error": t("Only an administrator can record a payout.")}
    try:
        return 201, {"payout": record_payout(body, user)}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_reverse(handler, user, payout_id, body):
    if not can_pay_commission(user):
        return 403, {"error": t("Only an administrator can reverse a payout.")}
    try:
        return 200, {"payout": reverse_payout(payout_id, body, user)}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_my_earnings(handler, user, qs):
    """A partner's own money, or one partner's money for staff.

    Scoped by the SESSION, never by a parameter, for the partner path: a
    partner asking for somebody else's `partner_id` gets his own figures,
    not a 403 and not the other partner's. Staff may name a partner
    explicitly, which is what the producer's own screens already do.
    """
    partner_id = ((qs or {}).get("partner_id") or [None])[0]
    if user.get("role") == "partner":
        partner_id = user["id"]
    elif not is_staff(user):
        return 403, {"error": t("Access denied.")}
    elif not partner_id:
        return 400, {"error": t("Choose a partner.")}

    try:
        summary = get_partner_earnings(partner_id)
    except ValueError as exc:
        return 400, {"error": str(exc)}

    # The forecast on leads still in flight is api/leads.py's maths. Imported
    # here rather than at module scope so this module stays importable on its
    # own (api/leads.py is the larger dependency graph of the two).
    from api.leads import get_leads
    leads = get_leads({"id": partner_id, "role": "partner"})
    return 200, attach_pipeline_forecast(summary, leads)
