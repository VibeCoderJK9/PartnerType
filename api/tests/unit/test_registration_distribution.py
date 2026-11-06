"""
PartnerType -- the dashboard's KPI cards count the DECISION axis.

A lead carries two independent answers:
`registration_status` (the producer's decision) and `status` (the
producer-configured sales stage, NULL until the deal is accepted).

The admin dashboard's KPI cards link to `leads.html?registration_status=...`
and were reading `get_status_distribution`, which groups by the SALES STAGE
column. Looking up 'pending', 'needs_info' or 'accepted' in that dict therefore
missed every time and all three cards rendered a permanent 0 while the leads
list showed real rows. These tests pin the two axes apart so the cards can never
silently read the wrong column again.
"""
from api.db import get_conn
from api.stats import get_registration_distribution, get_status_distribution


def _insert_lead(partner, lead_id, registration_status, status=None):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO leads (id, partner_id, client_name, client_email, company, "
            "registration_status, status, created_at) "
            "VALUES (?, ?, 'Klient', 'k@example.test', 'Firma sp. z o.o.', ?, ?, "
            "to_char(timezone('UTC', now()), 'YYYY-MM-DD HH24:MI:SS'))",
            (lead_id, partner["id"], registration_status, status),
        )
        conn.commit()
    finally:
        conn.close()


class TestRegistrationDistribution:
    def test_reports_all_four_decisions_even_at_zero(self, partner_user):
        _insert_lead(partner_user, "LD-901", "pending")

        counts = get_registration_distribution()

        # Every key present: a caller must never have to tell "no leads with
        # this decision" apart from "key missing from the payload".
        assert set(counts) == {"pending", "needs_info", "accepted", "rejected"}
        assert counts["pending"] == 1
        assert counts["needs_info"] == 0
        assert counts["accepted"] == 0
        assert counts["rejected"] == 0

    def test_counts_each_decision_separately(self, partner_user):
        _insert_lead(partner_user, "LD-902", "pending")
        _insert_lead(partner_user, "LD-903", "needs_info")
        _insert_lead(partner_user, "LD-904", "accepted", "won")
        _insert_lead(partner_user, "LD-905", "accepted", "lost")
        _insert_lead(partner_user, "LD-906", "rejected")

        counts = get_registration_distribution()

        assert counts == {"pending": 1, "needs_info": 1, "accepted": 2, "rejected": 1}

    def test_an_accepted_lead_is_counted_by_decision_not_by_its_stage(self, partner_user):
        """The exact shape of the shipped defect.

        An accepted lead sits on a sales stage, so the stage distribution keys
        it under 'won' -- and a card looking for 'accepted' in THAT dict finds
        nothing. The decision distribution still counts it.
        """
        _insert_lead(partner_user, "LD-907", "accepted", "won")

        by_stage = get_status_distribution()
        by_decision = get_registration_distribution()

        assert "accepted" not in by_stage
        assert by_stage["won"] == 1
        assert by_decision["accepted"] == 1

    def test_scopes_to_one_partner_when_asked(self, partner_user, admin_user):
        _insert_lead(partner_user, "LD-908", "pending")
        _insert_lead(admin_user, "LD-909", "rejected")

        mine = get_registration_distribution(partner_user["id"])

        assert mine["pending"] == 1
        assert mine["rejected"] == 0


def test_the_pipeline_buckets_carry_the_producers_english_stage_name():
    """The donut legend is drawn from these buckets, not from /api/lead-stages.
    Without the English name riding along, an English reader saw the producer's
    Polish stage on an otherwise English chart -- which is exactly how it
    behaved in the browser before this was carried through."""
    from api import lead_stages
    from api.stats import get_pipeline_distribution

    stage = next(s for s in lead_stages.get_all() if not s["is_terminal"])
    admin = {"id": 1, "role": "admin", "name": "Test"}
    lead_stages.update_stage(stage["id"], {"name_en": "Negotiation"}, admin)

    bucket = next(
        b for b in get_pipeline_distribution() if b["key"] == stage["key"]
    )

    assert bucket["name"] == stage["name"]
    assert bucket["name_en"] == "Negotiation"
