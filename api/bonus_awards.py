"""
PartnerType — One-off bonus awards

Owner's rule: *"one-off bonuses (...) we should not assign them to partners, only
give them for payout and record them; that is the rule."*

The distinction this module exists to hold:

* a PERCENT bonus is a property of the partner -- it changes the rate every
  future deal is calculated at, so it belongs on their card and is assigned
  (api/partner_bonuses.py).
* a CASH bonus is an event -- it happens once, for an amount, on a date, and
  from that moment it is money owed. It is never a rate, never part of a
  commission calculation, and it does not live on a card.

An award therefore carries its own money and its own payment state, and is
settled through the same payout that clears the period's commissions
(api/payouts.py). The bonus name is snapshotted at grant time: renaming or
deleting a definition later must not rewrite what a partner was told they
were getting.
"""
from datetime import datetime

from api.authz import can_pay_commission, is_staff
from api.db import get_conn
from api import audit, notifications as notif
from api.validation import (
    parse_allowed_fields,
    parse_finite_number,
    parse_integer_id,
    parse_integer_id_list,
    parse_required_string,
)
from api.i18n import N_, t

MAX_AWARD_AMOUNT = 1_000_000_000
MAX_NOTE = 500
MAX_BULK_PARTNERS = 500


def _amount(value):
    amount = parse_finite_number(value, N_("Bonus amount"), minimum=0.01,
                                 maximum=MAX_AWARD_AMOUNT)
    return round(float(amount), 2)


def _definition(conn, bonus_def_id):
    row = conn.execute(
        "SELECT id, name, name_en, type, default_value, active FROM bonus_definitions "
        "WHERE id = ?", (bonus_def_id,)
    ).fetchone()
    if not row:
        raise ValueError(t("No such bonus was found."))
    # A percent bonus changes a rate; granting it as a one-off payment would
    # silently turn "1%" into "1 PLN".
    if row["type"] != "cash":
        raise ValueError(t("Only a cash bonus can be awarded for payout."))
    if not row["active"]:
        raise ValueError(t("This bonus is switched off."))
    return row


def grant(partner_ids, bonus_def_id, user, amount=None, note=None):
    """Award a one-off cash bonus to one or more partners.

    Returns the awards created. Granting to somebody who already has an unpaid
    award from the same definition is allowed on purpose -- a producer may
    genuinely reward the same thing twice -- so this is not de-duplicated; the
    protection against a double-click belongs in the UI.
    """
    # A cash bonus is money owed from the moment it is granted, so granting
    # or taking one back is the account owner's decision, like paying it.
    if not can_pay_commission(user):
        raise PermissionError(t("Cash bonuses are awarded by an administrator."))

    partner_ids = parse_integer_id_list(
        partner_ids, N_("Partner list"), maximum_items=MAX_BULK_PARTNERS
    )
    if not partner_ids:
        raise ValueError(t("Choose at least one partner."))
    bonus_def_id = parse_integer_id(bonus_def_id, "bonus_def_id")
    text = None
    if note not in (None, ""):
        text = parse_required_string(note, N_("Note"), max_length=MAX_NOTE)

    conn = get_conn()
    try:
        definition = _definition(conn, bonus_def_id)
        value = _amount(amount if amount is not None else definition["default_value"])

        known = {
            row["id"] for row in conn.execute(
                "SELECT id FROM users WHERE role = 'partner' AND id IN (%s)"
                % ",".join("?" * len(partner_ids)), partner_ids
            ).fetchall()
        }
        missing = [pid for pid in partner_ids if pid not in known]
        if missing:
            raise ValueError(t("No partner with the given ID was found."))

        granted_at = datetime.utcnow().isoformat()
        # The English name is snapshotted from the SAME read as the Polish
        # one, at the exact moment of grant -- a definition with no
        # translation yet stores an empty EN rather than one invented later
        # from whatever the definition happens to say by then.
        name_en = definition["name_en"] or ""
        created = []
        for partner_id in partner_ids:
            cursor = conn.execute(
                "INSERT INTO bonus_awards (partner_id, bonus_def_id, name, name_en, "
                "amount, note, granted_by, granted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (partner_id, bonus_def_id, definition["name"], name_en, value,
                 text, user["id"], granted_at),
            )
            created.append({
                "id": cursor.lastrowid,
                "partner_id": partner_id,
                "name": definition["name"],
                "name_en": name_en,
                "amount": value,
                "granted_at": granted_at,
            })
        conn.commit()
    finally:
        conn.close()

    for award in created:
        notif.create_notification(
            award["partner_id"], "success", t("Bonus awarded: {bonus}", bonus=award['name']),
            t("{amount} PLN will be added to the next payout.", amount=f"{award['amount']:.2f}"),
            "earnings.html",
            title_key="ntf_bonus_granted_title",
            body_key="ntf_bonus_granted_body",
            params={"bonus": award["name"], "bonus_en": award["name_en"],
                    "amount": f"{award['amount']:.2f}"})
    audit.log(user, "bonus.grant", "bonus", str(bonus_def_id),
              f"{definition['name']} — {value:.2f} PLN × {len(created)}")
    return created


def revoke(award_id, user):
    """Take back an award that has not been paid yet.

    A PAID award is never deleted: it is part of the payout record, and the
    ledger has to keep saying what was actually paid. Undoing money that has
    already left is `reverse_payout`'s job, not this one.
    """
    # A cash bonus is money owed from the moment it is granted, so granting
    # or taking one back is the account owner's decision, like paying it.
    if not can_pay_commission(user):
        raise PermissionError(t("Cash bonuses are awarded by an administrator."))
    award_id = parse_integer_id(award_id, "award_id")

    conn = get_conn()
    try:
        conn.begin()
        row = conn.execute(
            "SELECT id, name, amount, paid_at FROM bonus_awards WHERE id = ? FOR UPDATE",
            (award_id,)
        ).fetchone()
        if not row:
            raise ValueError(t("No such bonus was found."))
        if row["paid_at"]:
            raise ValueError(t("This bonus cannot be reversed because it has already been "
                               "paid out."))
        conn.execute("DELETE FROM bonus_awards WHERE id = ?", (award_id,))
        conn.commit()
    finally:
        conn.close()

    audit.log(user, "bonus.revoke", "bonus", str(award_id),
              f"{row['name']} — {row['amount']:.2f} PLN")
    return {"id": award_id}


def list_awards(partner_id=None, unpaid_only=False):
    """Awards, newest first, with the partner they belong to."""
    where = []
    params = []
    if partner_id:
        where.append("a.partner_id = ?")
        params.append(parse_integer_id(partner_id, "partner_id"))
    if unpaid_only:
        where.append("a.paid_at IS NULL")
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    conn = get_conn()
    try:
        rows = conn.execute(f"""
            SELECT a.*, u.name AS partner_name, u.company AS partner_company
              FROM bonus_awards a
              JOIN users u ON u.id = a.partner_id
              {clause}
          ORDER BY a.granted_at DESC, a.id DESC
        """, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def unpaid_rows(conn, partner_id=None, lock=False):
    """Awards that are money owed right now. Shared with api/payouts.py."""
    sql = ("SELECT id, partner_id, amount, granted_at, name, name_en FROM bonus_awards "
           "WHERE paid_at IS NULL")
    params = []
    if partner_id:
        sql += " AND partner_id = ?"
        params.append(partner_id)
    return conn.execute(sql + " ORDER BY id ASC" + (" FOR UPDATE" if lock else ""), params).fetchall()


# ─── HTTP ────────────────────────────────────────────────────────────────

def handle_list(handler, user, qs):
    if not is_staff(user):
        return 403, {"error": t("Not allowed.")}
    partner_id = (qs.get("partner_id") or [None])[0]
    unpaid = (qs.get("unpaid") or ["0"])[0] == "1"
    try:
        return 200, {"awards": list_awards(partner_id, unpaid_only=unpaid)}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_grant(handler, user, body):
    try:
        body = parse_allowed_fields(
            body, ("partner_ids", "bonus_def_id", "amount", "note"),
            N_("Bonus data"),
        )
        awards = grant(
            body.get("partner_ids"), body.get("bonus_def_id"), user,
            amount=body.get("amount"), note=body.get("note"),
        )
        return 201, {"awards": awards}
    except PermissionError as exc:
        return 403, {"error": str(exc)}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_revoke(handler, user, award_id):
    try:
        return 200, revoke(award_id, user)
    except PermissionError as exc:
        return 403, {"error": str(exc)}
    except ValueError as exc:
        return 400, {"error": str(exc)}
