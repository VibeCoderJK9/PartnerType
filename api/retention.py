"""What is stored, how old it is, and what a retention period would remove.

Deliberately not a scheduled job. Deciding how long a producer keeps a lost
lead, an audit entry or a comment is a business and legal decision, and the
honest order is: see what is there, decide the period, then delete. This module
covers the first two steps and performs the third only when asked explicitly.

Nothing here runs by itself. `api/db.run_maintenance` sweeps only the
operational leftovers -- expired sessions, spent links -- which are not
business records at all.
"""
from datetime import datetime, timedelta

from api import audit, gdpr
from api.db import get_conn
from api.postgres import system_context
from api.i18n import t

# Every category a producer might eventually want to age out, with the column
# that says how old a row is. No default period: silence means "keep".
CATEGORIES = {
    "leads_lost": {
        "label": "Rejected and lost leads",
        "table": "leads",
        "date_column": "created_at",
        "where": "(registration_status = 'rejected' OR status = 'lost')",
        "note": "No settlement value; deleting them also removes their comments and history.",
    },
    "leads_won_settled": {
        "label": "Won and settled leads",
        "table": "leads",
        "date_column": "won_at",
        "where": "status = 'won' AND settlement_status = 'paid'",
        "note": "ANONYMISATION, not deletion: the amounts must stay because commission settlement relies on them.",
        "anonymise_only": True,
    },
    "audit_log": {
        "label": "Audit log",
        "table": "audit_log",
        "date_column": "created_at",
        "where": "1=1",
        "note": "Evidence of access control. Short retention helps GDPR but makes explaining an incident harder.",
    },
    "notifications": {
        "label": "Notifications",
        "table": "notifications",
        "date_column": "created_at",
        "where": "1=1",
        "note": "Purely auxiliary; the safest category to shorten.",
    },
    "workflow_events": {
        "label": "Lead change history",
        "table": "lead_workflow_events",
        "date_column": "created_at",
        "where": "1=1",
        "note": "Contains snapshots of client data in the payload_json field.",
    },
    "comments": {
        "label": "Lead comments",
        "table": "lead_comments",
        "date_column": "created_at",
        "where": "1=1",
        "note": "Hand-written text: it often holds more personal data than the lead fields themselves.",
    },
}

def _cutoff(days: int) -> str:
    return (datetime.utcnow() - timedelta(days=days)).isoformat()


@system_context("operator retention survey")
def survey():
    """How much of each category exists, how old it is and what it costs to keep.

    The size figure is what makes this a storage conversation as much as a
    privacy one: a producer deciding on a period should see both.
    """
    report = {"generated_at": datetime.utcnow().isoformat(), "categories": {}}
    with get_conn() as conn:
        for key, spec in CATEGORIES.items():
            row = conn.execute(
                f"""SELECT COUNT(*) AS rows,
                           MIN({spec['date_column']}) AS oldest,
                           MAX({spec['date_column']}) AS newest,
                           COUNT(*) FILTER (WHERE {spec['date_column']} < ?) AS older_than_year
                      FROM {spec['table']}
                     WHERE {spec['where']}""",
                (_cutoff(365),),
            ).fetchone()
            size = conn.execute(
                "SELECT pg_size_pretty(pg_total_relation_size(?)) AS size, "
                "pg_total_relation_size(?) AS bytes",
                (spec["table"], spec["table"]),
            ).fetchone()
            report["categories"][key] = {
                "label": spec["label"],
                "note": spec["note"],
                "rows": row["rows"],
                "oldest": row["oldest"],
                "newest": row["newest"],
                "older_than_a_year": row["older_than_year"],
                "table_size": size["size"],
                "table_bytes": size["bytes"],
                "anonymise_only": spec.get("anonymise_only", False),
            }
    return report


@system_context("operator retention preview")
def preview(periods: dict):
    """What a given set of periods would touch, without touching anything.

    `periods` maps a category to a number of days; categories left out are
    kept, which is what makes "no policy" mean "nothing is deleted".
    """
    result = {}
    with get_conn() as conn:
        for key, days in periods.items():
            spec = CATEGORIES[key]
            count = conn.execute(
                f"""SELECT COUNT(*) AS rows FROM {spec['table']}
                     WHERE {spec['where']} AND {spec['date_column']} IS NOT NULL
                       AND {spec['date_column']} < ?""",
                (_cutoff(int(days)),),
            ).fetchone()["rows"]
            result[key] = {
                "label": spec["label"],
                "older_than_days": int(days),
                "rows_affected": count,
                "action": "anonymisation" if spec.get("anonymise_only") else "deletion",
            }
    return result


@system_context("operator retention apply")
def apply(periods: dict, actor=None, confirm: bool = False):
    """Carry out a preview. Refuses unless the caller says so in as many words.

    There is no scheduler calling this and there deliberately is not one yet:
    the producer decides the periods first, and a job that quietly deletes
    records nobody agreed to lose is worse than no job at all.
    """
    if not confirm:
        raise ValueError(
            t("An explicit confirmation is required. Look at the preview first.")
        )
    performed = {}
    with get_conn() as conn:
        conn.begin()
        for key, days in periods.items():
            spec = CATEGORIES[key]
            cutoff = _cutoff(int(days))
            condition = (f"{spec['where']} AND {spec['date_column']} IS NOT NULL "
                         f"AND {spec['date_column']} < ?")
            if spec.get("anonymise_only"):
                # The same routine an erasure request uses, so the history
                # payloads, comments and lead answers go with the columns.
                ids = [row["id"] for row in conn.execute(
                    f"SELECT id FROM {spec['table']} WHERE {condition} "
                    f"AND client_name <> ''", (cutoff,)).fetchall()]
                affected = gdpr.anonymise_settled_leads(
                    conn, ids, client_name="")["leads_anonymised"]
            else:
                affected = conn.execute(
                    f"DELETE FROM {spec['table']} WHERE {condition}", (cutoff,)).rowcount
            performed[key] = affected
        conn.commit()
    audit.log(actor, "data.retention_applied", "system", "",
              ", ".join(f"{key}: {count}" for key, count in performed.items()))
    return performed
