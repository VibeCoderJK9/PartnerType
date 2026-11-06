"""
PartnerType — Partner Bonus Assignments Module
Assigns/removes bonus definitions to partners. Supports bulk operations.
"""
from datetime import datetime
import logging
import traceback
import uuid

from api.authz import is_staff
from api.db import get_conn
from api import audit
from api.validation import (
    parse_allowed_fields,
    parse_enum,
    parse_finite_number,
    parse_integer_id,
    parse_integer_id_list,
)
from api.i18n import N_, t


logger = logging.getLogger(__name__)
MAX_BULK_PARTNERS = 500
MAX_CUSTOM_VALUE = 1000000000


def _parse_custom_value(value):
    if value is None:
        return None
    return parse_finite_number(
        value, N_("Custom value"), minimum=0, maximum=MAX_CUSTOM_VALUE
    )


def _unexpected_bulk_item(partner_id, exc, route):
    request_id = uuid.uuid4().hex
    frames = traceback.extract_tb(exc.__traceback__)
    frame_projection = ",".join(
        f"{frame.name}:{frame.lineno}" for frame in frames
    )
    logger.error(
        "unexpected_error request_id=%s exception=%s method=POST route=%s frames=%s",
        request_id,
        type(exc).__name__,
        route,
        frame_projection,
    )
    return {
        "partner_id": partner_id,
        "code": "unexpected_error",
        "request_id": request_id,
    }


def get_partner_summary(partner_id):
    """Return partner's tier + all bonuses + computed totals."""
    partner_id = parse_integer_id(partner_id, N_("Partner ID"))
    conn = get_conn()
    try:
        # Partner with tier
        partner = conn.execute("""
            SELECT u.id, u.name, u.email, u.company, u.tier_id,
                   ct.name AS tier_name, ct.base_rate, ct.color,
                   ct.name_en AS tier_name_en
            FROM users u
            LEFT JOIN commission_tiers ct ON ct.id = u.tier_id
            WHERE u.id = ? AND u.role = 'partner'
        """, (partner_id,)).fetchone()
        if not partner:
            return None

        result = dict(partner)

        # Active bonus assignments
        assignments = conn.execute("""
            SELECT pba.id, pba.bonus_def_id, pba.custom_value, pba.is_active, pba.assigned_at,
                   bd.name, bd.name_en, bd.type, bd.default_value,
                   bd.description, bd.description_en
            FROM partner_bonus_assignments pba
            JOIN bonus_definitions bd ON bd.id = pba.bonus_def_id
            WHERE pba.partner_id = ? AND pba.is_active = 1
            ORDER BY bd.type, bd.sort_order
        """, (partner_id,)).fetchall()

        bonus_percent = []
        bonus_cash    = []
        total_percent = float(result.get("base_rate") or 0)
        total_cash    = 0.0

        for a in assignments:
            row = dict(a)
            eff_value = row["custom_value"] if row["custom_value"] is not None else row["default_value"]
            row["effective_value"] = eff_value
            if row["type"] == "percent":
                bonus_percent.append(row)
                total_percent += eff_value
            else:
                # Unreachable -- assign_bonus refuses a cash
                # bonus and the migration cleared the rows that existed. Kept as
                # a guard so a hand-edited row cannot quietly rejoin the rate.
                bonus_cash.append(row)

        # Cash does not come from assignments. It is what the producer has
        # GRANTED this partner and not yet paid (api/bonus_awards.py), so the
        # card answers "what is still owed".
        for award in conn.execute(
            "SELECT id, name, name_en, amount, granted_at FROM bonus_awards "
            "WHERE partner_id = ? AND paid_at IS NULL ORDER BY granted_at",
            (partner_id,),
        ).fetchall():
            bonus_cash.append({
                "id": award["id"],
                "name": award["name"],
                "name_en": award["name_en"],
                "type": "cash",
                "effective_value": award["amount"],
                "granted_at": award["granted_at"],
            })
            total_cash += award["amount"] or 0

        result["bonus_percent"]  = bonus_percent
        result["bonus_cash"]     = bonus_cash
        result["total_percent"]  = round(total_percent, 4)
        # Money owed, never part of the rate. api/leads.py's commission
        # arithmetic has always excluded it and still does.
        result["total_cash"]     = round(total_cash, 2)
        return result
    finally:
        conn.close()


def assign_bonus(partner_id, bonus_def_id, custom_value, user):
    """Assign a validated bonus to a partner, reactivating an existing row."""
    partner_id = parse_integer_id(partner_id, N_("Partner ID"))
    bonus_def_id = parse_integer_id(bonus_def_id, N_("Bonus ID"))
    custom_value = _parse_custom_value(custom_value)
    conn = get_conn()
    try:
        bonus = conn.execute(
            "SELECT name, type FROM bonus_definitions WHERE id=? AND active=1",
            (bonus_def_id,),
        ).fetchone()
        if not bonus:
            raise ValueError(t("The bonus does not exist or is inactive."))
        # A cash bonus is not something a partner HAS. It is granted once and paid -- api/bonus_awards.py -- so this
        # door is closed rather than left as a second, silent way to promise
        # money that no payout would ever clear.
        if bonus["type"] == "cash":
            raise ValueError(
                t("A cash bonus is awarded for payout, not assigned to a partner.")
            )
        if bonus["type"] == "percent" and custom_value is not None and custom_value > 100:
            raise ValueError(t("A percentage bonus cannot exceed 100."))

        now = datetime.utcnow().isoformat()
        assigned_by = user.get("id")

        existing = conn.execute(
            "SELECT id FROM partner_bonus_assignments WHERE partner_id=? AND bonus_def_id=?",
            (partner_id, bonus_def_id)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE partner_bonus_assignments SET is_active=1, custom_value=?, assigned_by=?, assigned_at=? "
                "WHERE partner_id=? AND bonus_def_id=?",
                (custom_value, assigned_by, now, partner_id, bonus_def_id)
            )
        else:
            conn.execute(
                "INSERT INTO partner_bonus_assignments (partner_id, bonus_def_id, custom_value, is_active, assigned_by, assigned_at) "
                "VALUES (?,?,?,1,?,?)",
                (partner_id, bonus_def_id, custom_value, assigned_by, now)
            )
        conn.commit()

        audit.log(user, "bonus.assign", "partner", str(partner_id),
                  f"Bonus: {bonus['name']}")
        return get_partner_summary(partner_id)
    finally:
        conn.close()


def remove_bonus(partner_id, bonus_def_id, user):
    """Deactivate a bonus assignment (soft delete)."""
    partner_id = parse_integer_id(partner_id, N_("Partner ID"))
    bonus_def_id = parse_integer_id(bonus_def_id, N_("Bonus ID"))
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE partner_bonus_assignments SET is_active=0 WHERE partner_id=? AND bonus_def_id=?",
            (partner_id, bonus_def_id)
        )
        conn.commit()
        audit.log(user, "bonus.remove", "partner", str(partner_id),
                  t("Removed bonus_def_id={bonus}", bonus=bonus_def_id))
        return get_partner_summary(partner_id)
    finally:
        conn.close()


def update_tier(partner_id, tier_id, user):
    """Change partner's commission tier."""
    partner_id = parse_integer_id(partner_id, N_("Partner ID"))
    tier_id = parse_integer_id(tier_id, N_("Tier ID"))
    conn = get_conn()
    try:
        tier = conn.execute("SELECT * FROM commission_tiers WHERE id=?", (tier_id,)).fetchone()
        if not tier:
            raise ValueError(t("The tier does not exist."))
        conn.execute("UPDATE users SET tier_id=? WHERE id=?", (tier_id, partner_id))
        conn.commit()
        audit.log(user, "partner.tier", "partner", str(partner_id),
                  f"Tier → {tier['name']} ({tier['base_rate']}%)")
        return get_partner_summary(partner_id)
    finally:
        conn.close()


def bulk_assign(partner_ids, bonus_def_id, custom_value, user):
    """Assign bonus to multiple partners at once."""
    partner_ids = parse_integer_id_list(
        partner_ids, N_("Partners"), maximum_items=MAX_BULK_PARTNERS
    )
    bonus_def_id = parse_integer_id(bonus_def_id, N_("Bonus ID"))
    custom_value = _parse_custom_value(custom_value)
    ok = []
    errors = []
    for pid in partner_ids:
        try:
            assign_bonus(pid, bonus_def_id, custom_value, user)
            ok.append(pid)
        except (ValueError, PermissionError) as exc:
            errors.append({"partner_id": pid, "error": str(exc)})
        except Exception as exc:
            errors.append(_unexpected_bulk_item(pid, exc, "partner_bonus_bulk_assign"))
    audit.log(user, "bonus.bulk_assign", "partner", "",
              t("{count} partners, bonus_def_id={bonus}", count=len(ok), bonus=bonus_def_id),
              detail_key="audit_detail_bulk_bonus",
              detail_params={"count": len(ok), "bonus_id": bonus_def_id})
    return {"ok": ok, "errors": errors, "processed": len(ok)}


def bulk_remove(partner_ids, bonus_def_id, user):
    """Remove bonus from multiple partners at once."""
    partner_ids = parse_integer_id_list(
        partner_ids, N_("Partners"), maximum_items=MAX_BULK_PARTNERS
    )
    bonus_def_id = parse_integer_id(bonus_def_id, N_("Bonus ID"))
    ok = []
    errors = []
    for pid in partner_ids:
        try:
            remove_bonus(pid, bonus_def_id, user)
            ok.append(pid)
        except (ValueError, PermissionError) as exc:
            errors.append({"partner_id": pid, "error": str(exc)})
        except Exception as exc:
            errors.append(_unexpected_bulk_item(pid, exc, "partner_bonus_bulk_remove"))
    audit.log(user, "bonus.bulk_remove", "partner", "",
              t("{count} partners, bonus_def_id={bonus}", count=len(ok), bonus=bonus_def_id),
              detail_key="audit_detail_bulk_bonus",
              detail_params={"count": len(ok), "bonus_id": bonus_def_id})
    return {"ok": ok, "errors": errors, "processed": len(ok)}


def bulk_set_tier(partner_ids, tier_id, user):
    """Set same tier for multiple partners at once."""
    partner_ids = parse_integer_id_list(
        partner_ids, N_("Partners"), maximum_items=MAX_BULK_PARTNERS
    )
    tier_id = parse_integer_id(tier_id, N_("Tier ID"))
    conn = get_conn()
    try:
        tier = conn.execute("SELECT * FROM commission_tiers WHERE id=?", (tier_id,)).fetchone()
        if not tier:
            raise ValueError(t("The tier does not exist."))
        conn.execute(
            f"UPDATE users SET tier_id=? WHERE id IN ({','.join('?' for _ in partner_ids)})",
            [tier_id] + partner_ids
        )
        conn.commit()
        audit.log(user, "partner.bulk_tier", "partner", "",
                  t("{count} partners → {tier}", count=len(partner_ids), tier=tier['name']),
                  detail_key="audit_detail_bulk_tier",
                  detail_params={"count": len(partner_ids), "tier": tier["name"]})
        return {"processed": len(partner_ids)}
    finally:
        conn.close()


def get_all_partners_summary():
    """Return all partners with their tier and bonus summary."""
    conn = get_conn()
    try:
        partners = conn.execute("""
            SELECT u.id, u.name, u.email, u.company, u.tier_id, u.account_status,
                   u.created_at,
                   ct.name AS tier_name, ct.base_rate, ct.color,
                   ct.name_en AS tier_name_en
            FROM users u
            LEFT JOIN commission_tiers ct ON ct.id = u.tier_id
            WHERE u.role = 'partner' AND u.active = 1
            ORDER BY ct.sort_order, u.name
        """).fetchall()

        result = []
        for p in partners:
            row = dict(p)
            # Aggregate bonus percent sum
            bonus_data = conn.execute("""
                SELECT bd.type,
                       COALESCE(pba.custom_value, bd.default_value) AS eff_val
                FROM partner_bonus_assignments pba
                JOIN bonus_definitions bd ON bd.id = pba.bonus_def_id
                WHERE pba.partner_id = ? AND pba.is_active = 1
            """, (p["id"],)).fetchall()

            bonus_pct  = sum(r["eff_val"] for r in bonus_data if r["type"] == "percent")
            # Cash is awarded-and-unpaid, not assigned -- same source as
            # get_partner_summary, so the roster and a partner's own card can
            # never disagree about what is owed.
            bonus_cash = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM bonus_awards "
                "WHERE partner_id = ? AND paid_at IS NULL", (p["id"],)
            ).fetchone()["total"]
            row["bonus_percent"]    = round(bonus_pct, 4)
            row["bonus_cash"]       = round(bonus_cash, 2)
            row["total_percent"]    = round((row["base_rate"] or 0) + bonus_pct, 4)
            row["active_bonus_count"] = len(bonus_data)
            result.append(row)
        return result
    finally:
        conn.close()


def get_all_partners_roster():
    """Return every partner account, any state, with a computed 'state'
    column -- the management list, a sibling to and
    independent of get_all_partners_summary()'s active-only statistics
    query, which must stay byte-identical.

    A partner may hold more than one partner_invitations row over their
    lifetime (reinvited after an earlier one expired or was accepted); the
    subquery collapses those to at most one candidate row per partner
    before the outer join, so a re-invited partner is never duplicated."""
    conn = get_conn()
    try:
        partners = conn.execute("""
            SELECT u.id, u.name, u.email, u.phone, u.company, u.bio,
                   u.tier_id, u.account_status, u.active, u.created_at,
                   u.rejected_at,
                   ct.name AS tier_name, ct.base_rate, ct.color,
                   ct.name_en AS tier_name_en,
                   pi.invitation_id AS pending_invitation_id
            FROM users u
            LEFT JOIN commission_tiers ct ON ct.id = u.tier_id
            LEFT JOIN (
                SELECT user_id, MAX(id) AS invitation_id
                FROM partner_invitations
                WHERE accepted_at IS NULL AND expires_at > to_char(timezone('UTC', now()), 'YYYY-MM-DD HH24:MI:SS')
                GROUP BY user_id
            ) pi ON pi.user_id = u.id
            WHERE u.role = 'partner'
            -- Rejected accounts last: they carry no tier, and NULLS FIRST put
            -- 30 rejected bots above every real partner.
            ORDER BY (u.account_status = 'rejected'), ct.sort_order NULLS FIRST, lower(u.name), u.id
        """).fetchall()

        result = []
        for p in partners:
            row = dict(p)
            # Same per-partner bonus aggregation shape as get_all_partners_summary,
            # so the roster's rows carry the same bonus/percent fields the table
            # already renders.
            bonus_data = conn.execute("""
                SELECT bd.type,
                       COALESCE(pba.custom_value, bd.default_value) AS eff_val
                FROM partner_bonus_assignments pba
                JOIN bonus_definitions bd ON bd.id = pba.bonus_def_id
                WHERE pba.partner_id = ? AND pba.is_active = 1
            """, (row["id"],)).fetchall()

            bonus_pct  = sum(r["eff_val"] for r in bonus_data if r["type"] == "percent")
            # Cash is awarded-and-unpaid, not assigned -- same source as
            # get_partner_summary, so the roster and a partner's own card can
            # never disagree about what is owed.
            bonus_cash = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM bonus_awards "
                "WHERE partner_id = ? AND paid_at IS NULL", (p["id"],)
            ).fetchone()["total"]
            row["bonus_percent"]      = round(bonus_pct, 4)
            row["bonus_cash"]         = round(bonus_cash, 2)
            row["total_percent"]      = round((row["base_rate"] or 0) + bonus_pct, 4)
            row["active_bonus_count"] = len(bonus_data)

            if row["account_status"] == "active":
                row["state"] = "active"
            elif row["account_status"] == "suspended":
                row["state"] = "suspended"
            elif row["account_status"] == "rejected":
                # Restorable until the retention sweep in api/db.py deletes
                # the account; rejected_at carries the deadline the row
                # renders as a countdown.
                row["state"] = "rejected"
            elif row["pending_invitation_id"] is not None:
                row["state"] = "invited_pending"
            else:
                row["state"] = "self_registered"
            result.append(row)
        return result
    finally:
        conn.close()


# ─── HTTP HANDLERS ────────────────────────────────────────────────────────────

def handle_list_partners(handler, user, qs):
    # Both views carry every partner's e-mail, commission rate and unpaid
    # cash bonuses, so only staff may read them. A partner reads only his
    # own card through handle_get_partner.
    if not is_staff(user):
        return 403, {"error": t("Access denied.")}
    scope = qs.get("scope", [None])[0]
    if scope == "roster":
        return 200, {"partners": get_all_partners_roster()}
    return 200, {"partners": get_all_partners_summary()}


def handle_get_partner(handler, user, partner_id):
    try:
        pid = parse_integer_id(partner_id, N_("Partner ID"))
        # A partner sees only his own card; of the producer's side only staff
        # read commission rates and unpaid bonuses (an observer reads leads).
        if user["role"] == "partner":
            if user["id"] != pid:
                return 403, {"error": t("Access denied.")}
        elif not is_staff(user):
            return 403, {"error": t("Access denied.")}
        summary = get_partner_summary(pid)
        if not summary:
            return 404, {"error": t("Partner not found.")}
        return 200, {"partner": summary}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_assign(handler, user, partner_id, body):
    if not is_staff(user):
        return 403, {"error": t("Access denied.")}
    try:
        body = parse_allowed_fields(
            body, ("bonus_def_id", "custom_value"), N_("Bonus assignment")
        )
        summary = assign_bonus(
            partner_id, body.get("bonus_def_id"), body.get("custom_value"), user
        )
        return 200, {"partner": summary}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_remove(handler, user, partner_id, bonus_def_id):
    if not is_staff(user):
        return 403, {"error": t("Access denied.")}
    try:
        summary = remove_bonus(partner_id, bonus_def_id, user)
        return 200, {"partner": summary}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_update_tier(handler, user, partner_id, body):
    if not is_staff(user):
        return 403, {"error": t("Access denied.")}
    try:
        body = parse_allowed_fields(body, ("tier_id",), N_("Tier change"))
        summary = update_tier(partner_id, body.get("tier_id"), user)
        return 200, {"partner": summary}
    except ValueError as e:
        return 400, {"error": str(e)}


def handle_bulk(handler, user, body):
    if not is_staff(user):
        return 403, {"error": t("Access denied.")}
    try:
        body = parse_allowed_fields(
            body,
            ("action", "partner_ids", "bonus_def_id", "custom_value", "tier_id"),
            N_("Bulk operation"),
        )
        action = parse_enum(
            body.get("action"),
            N_("Action"),
            ("assign_bonus", "remove_bonus", "set_tier"),
        )
        partner_ids = body.get("partner_ids")

        if action == "assign_bonus":
            result = bulk_assign(
                partner_ids,
                body.get("bonus_def_id"),
                body.get("custom_value"),
                user,
            )
            return 200, result

        if action == "remove_bonus":
            result = bulk_remove(partner_ids, body.get("bonus_def_id"), user)
            return 200, result

        if action == "set_tier":
            result = bulk_set_tier(partner_ids, body.get("tier_id"), user)
            return 200, result
    except ValueError as exc:
        return 400, {"error": str(exc)}
