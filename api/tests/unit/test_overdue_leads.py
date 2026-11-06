"""
PartnerType -- "Po terminie" (overdue leads) unit tests.

The dashboard KPI card and the leads-list filter must always agree, and the
window is admin-configurable via `lead_action_days`. These tests pin the
counting rule so the card can never claim a number the filtered list does not
show -- the exact defect this feature was built to fix.
"""
import pytest

from api.db import get_conn
from api.settings_api import DEFAULTS, set_many, _parse_action_days
from api.validation import ValidationError
from api.stats import count_overdue_leads


def _insert_lead(partner, registration_status, days_old, lead_id):
    """Insert a lead directly on the REGISTRATION axis.

    "Awaiting a decision" stopped being a position in the sales pipeline and
    became a value of its own, so this helper names that value rather than a
    stage key.
    """
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO leads (id, partner_id, client_name, client_email, company, "
            "registration_status, created_at) "
            "VALUES (?, ?, 'Klient', 'k@example.test', 'Firma sp. z o.o.', ?, to_char(timezone('UTC', now()) + ?::interval, 'YYYY-MM-DD HH24:MI:SS'))",
            (lead_id, partner["id"], registration_status, f"-{days_old} days"),
        )
        conn.commit()
    finally:
        conn.close()


def _count():
    conn = get_conn()
    try:
        return count_overdue_leads(conn)
    finally:
        conn.close()


class TestActionDaysSetting:
    def test_default_matches_the_previously_hardcoded_48h_window(self):
        assert DEFAULTS["lead_action_days"] == "2"

    @pytest.mark.parametrize("value", ["1", "2", "30", "90"])
    def test_accepts_whole_days_in_range(self, value):
        assert _parse_action_days(value) == int(value)

    @pytest.mark.parametrize("value", ["0", "-1", "91", "2.5", "", "dwa"])
    def test_rejects_values_outside_the_allowed_range(self, value):
        with pytest.raises(ValueError):
            _parse_action_days(value)

    def test_set_many_rejects_an_out_of_range_value(self, partner_user, admin_user):
        with pytest.raises(ValueError):
            set_many({"lead_action_days": "0"}, admin_user)

    def test_raises_the_type_the_router_turns_into_a_400(self, partner_user, admin_user):
        # api/router.py maps ValidationError to 400 with the message attached.
        # A plain ValueError would fall through to the generic 500 and the admin
        # would see "unexpected server error" with nothing to act on.
        with pytest.raises(ValidationError) as excinfo:
            set_many({"lead_action_days": "0"}, admin_user)
        assert "1 do 90" in str(excinfo.value)


class TestCountOverdueLeads:
    def test_counts_a_lead_awaiting_a_decision_past_the_deadline(self, partner_user):
        _insert_lead(partner_user, "pending", days_old=5, lead_id="LD-901")
        assert _count() == 1

    def test_ignores_a_lead_still_inside_the_deadline(self, partner_user):
        _insert_lead(partner_user, "pending", days_old=0, lead_id="LD-902")
        assert _count() == 0

    def test_counts_needs_info_because_it_is_still_undecided(self, partner_user):
        # A lead the producer has asked questions about still has to be
        # accepted or rejected -- it is not off their desk.
        _insert_lead(partner_user, "needs_info", days_old=5, lead_id="LD-903")
        assert _count() == 1

    @pytest.mark.parametrize("registration_status", ["accepted", "rejected"])
    def test_ignores_leads_that_already_have_a_decision(
        self, partner_user, registration_status
    ):
        _insert_lead(
            partner_user, registration_status, days_old=99,
            lead_id=f"LD-{registration_status[:3]}",
        )
        assert _count() == 0

    def test_widening_the_window_removes_leads_from_the_count(self, partner_user, admin_user):
        _insert_lead(partner_user, "pending", days_old=10, lead_id="LD-904")
        assert _count() == 1

        set_many({"lead_action_days": "30"}, admin_user)
        assert _count() == 0

    def test_narrowing_the_window_adds_leads_to_the_count(self, partner_user, admin_user):
        _insert_lead(partner_user, "pending", days_old=3, lead_id="LD-905")
        set_many({"lead_action_days": "30"}, admin_user)
        assert _count() == 0

        set_many({"lead_action_days": "1"}, admin_user)
        assert _count() == 1
