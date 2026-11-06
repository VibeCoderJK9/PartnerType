"""
Block: remind the producer about a partner who has gone quiet.

Producers told us they wanted to
be told when a partner stopped doing anything, so somebody picks up the phone
before the relationship cools. This is a rule, not AI -- it works for every
producer, with no data leaving the instance.

A partner is quiet when neither his account nor any lead he submitted is newer
than `days`. The producer's admins get one in-app notification per stretch of
silence; it repeats only if the partner stays quiet for another `days`.

This file is also the template for new blocks: one `Block(...)` at the bottom,
the work in plain functions above it.
"""

from datetime import datetime, timedelta

from api import notifications as notif
# api.blocks imports this module at its very end, after these names exist.
from api.blocks import Block, ConfigField, get_state, set_state
from api.db import get_conn
from api.i18n import t

BLOCK_ID = "inactive_partner"


def _parse(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "").split("+")[0])
    except ValueError:
        return None


def _quiet_partners(conn):
    """Active partners with the moment they were last active."""
    rows = conn.execute(
        """SELECT u.id, u.name, u.created_at, MAX(l.created_at) AS last_lead
             FROM users u
             LEFT JOIN leads l ON l.partner_id = u.id
            WHERE u.role = 'partner' AND u.account_status = 'active' AND u.active = 1
            GROUP BY u.id, u.name, u.created_at
            ORDER BY u.id"""
    ).fetchall()
    for row in rows:
        moments = [m for m in (_parse(row["created_at"]), _parse(row["last_lead"])) if m]
        if moments:
            yield row["id"], row["name"], max(moments)


def run(config, now):
    days = int(config["days"])
    threshold = timedelta(days=days)
    checked = reminded = 0
    conn = get_conn()
    try:
        for partner_id, name, last_active in list(_quiet_partners(conn)):
            checked += 1
            quiet_for = now - last_active
            if quiet_for < threshold:
                continue
            subject = f"partner:{partner_id}"
            state = get_state(conn, BLOCK_ID, subject)
            same_stretch = state.get("last_active") == last_active.isoformat()
            reminded_at = _parse(state.get("reminded_at"))
            if same_stretch and reminded_at and now - reminded_at < threshold:
                continue
            quiet_days = quiet_for.days
            notif.notify_all_admins(
                "warning",
                t("Partner {partner} has not submitted a lead for {days} days",
                  partner=notif.quoted(name), days=quiet_days),
                t("Last activity: {date}. It is worth getting in touch to ask whether they "
                  "need anything.", date=f"{last_active:%d.%m.%Y}"),
                "partners.html",
                title_key="ntf_block_inactive_title",
                body_key="ntf_block_inactive_body",
                params={"partner": name, "days": quiet_days,
                        "date": f"{last_active:%d.%m.%Y}"},
            )
            set_state(conn, BLOCK_ID, subject, {
                "last_active": last_active.isoformat(),
                "reminded_at": now.isoformat(),
            })
            conn.commit()
            reminded += 1
    finally:
        conn.close()
    return {"checked": checked, "reminded": reminded}


BLOCK = Block(
    id=BLOCK_ID,
    name_key="block_inactive_name",
    description_key="block_inactive_desc",
    config_fields=(
        ConfigField(name="days", label_key="block_inactive_days",
                    default=30, minimum=7, maximum=180),
    ),
    run=run,
)
