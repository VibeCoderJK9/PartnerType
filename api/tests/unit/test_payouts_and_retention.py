"""
PartnerType -- Payout grouping and rejected-application retention.

Two rules that both turn on a date rather than a status:
  -   the amount to pay out in a given month or quarter
  -   a rejected partner stays restorable for seven days, then goes
"""
from datetime import datetime, timedelta

from api import db
from api.db import REJECTED_RETENTION_DAYS, get_conn
from api.leads import create_lead, update_lead
from api.stats import get_commission_payouts
from api.users import update_user


def _lead(**overrides):
    data = {
        "client_name": "Anna Testowa",
        "client_email": "anna@example.pl",
        "company": "Testowa Sp. z o.o.",
        "value": 10000,
    }
    data.update(overrides)
    return data


def _win(lead_id, admin_user):
    update_lead(lead_id, admin_user, {"registration_status": "accepted"})
    return update_lead(lead_id, admin_user, {"status": "won"})


class TestCommissionPayouts:
    def test_a_won_deal_counts_toward_this_month_and_this_quarter(
        self, admin_user, partner_user_tiered
    ):
        lead = create_lead(partner_user_tiered, _lead(value=10000))
        _win(lead["id"], admin_user)

        payouts = get_commission_payouts()

        assert payouts["month"]["amount"] == 700.0
        assert payouts["month"]["deals"] == 1
        assert payouts["quarter"]["amount"] == 700.0

    def test_an_accepted_but_unsold_deal_is_not_owed_to_anyone(
        self, admin_user, partner_user_tiered
    ):
        """A payout figure that included deals still being sold would not be
        a payout figure."""
        lead = create_lead(partner_user_tiered, _lead(value=10000))
        update_lead(lead["id"], admin_user, {"registration_status": "accepted"})

        payouts = get_commission_payouts()

        assert payouts["month"]["amount"] == 0
        assert payouts["month"]["deals"] == 0

    def test_a_deal_won_last_year_is_not_owed_this_month(
        self, admin_user, partner_user_tiered
    ):
        """The reason this cannot reuse get_monthly_series(): that groups by
        submission date, so a deal filed in March and won in April would be
        reported against March."""
        lead = create_lead(partner_user_tiered, _lead(value=10000))
        _win(lead["id"], admin_user)

        conn = get_conn()
        try:
            conn.execute(
                "UPDATE leads SET won_at = ? WHERE id = ?",
                ((datetime.utcnow() - timedelta(days=400)).isoformat(), lead["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        payouts = get_commission_payouts()

        assert payouts["month"]["amount"] == 0
        assert payouts["quarter"]["amount"] == 0

    def test_winning_a_deal_twice_does_not_restate_the_date_it_was_won(
        self, admin_user, partner_user_tiered
    ):
        """A retried/duplicated "won" write (the scenario this guards, e.g. a
        lost response retried by a caller) must not restate won_at.

        A terminal exit through the generic PATCH is refused
        (api.leads._terminal_exit_attempted): reopening a closed lead is a
        dedicated, audited action. Re-affirming the SAME terminal value the
        lead is already on is a true no-op the guard deliberately leaves
        alone (mirrors move_partner_lead's own same-source/dest no-op), which
        is what this exercises.
        """
        lead = create_lead(partner_user_tiered, _lead(value=10000))
        first = _win(lead["id"], admin_user)
        stamped = first["won_at"]

        again = update_lead(lead["id"], admin_user, {"status": "won"})

        assert again["won_at"] == stamped


class TestRejectedPartnerRetention:
    def _rejected_partner(self, admin_user, partner_user, days_ago):
        update_user(partner_user["id"], {"account_status": "rejected"}, admin_user)
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE users SET rejected_at = ? WHERE id = ?",
                ((datetime.utcnow() - timedelta(days=days_ago)).isoformat(),
                 partner_user["id"]),
            )
            conn.commit()
        finally:
            conn.close()

    def _exists(self, user_id):
        conn = get_conn()
        try:
            return conn.execute(
                "SELECT 1 FROM users WHERE id = ?", (user_id,)
            ).fetchone() is not None
        finally:
            conn.close()

    def test_rejecting_stamps_the_retention_clock(self, admin_user, partner_user):
        result = update_user(
            partner_user["id"], {"account_status": "rejected"}, admin_user
        )

        assert result["account_status"] == "rejected"
        assert result["rejected_at"] is not None

    def test_a_partner_rejected_yesterday_survives_a_restart(
        self, admin_user, partner_user
    ):
        self._rejected_partner(admin_user, partner_user, days_ago=1)

        db.purge_rejected()

        assert self._exists(partner_user["id"])

    def test_a_partner_rejected_past_the_window_is_deleted_by_maintenance(
        self, admin_user, partner_user
    ):
        self._rejected_partner(
            admin_user, partner_user, days_ago=REJECTED_RETENTION_DAYS + 1
        )

        db.purge_rejected()

        assert not self._exists(partner_user["id"])

    def test_restoring_a_partner_stops_the_clock(self, admin_user, partner_user):
        """Restoring has to clear the stamp, or the sweep would delete an
        account somebody deliberately brought back."""
        self._rejected_partner(
            admin_user, partner_user, days_ago=REJECTED_RETENTION_DAYS + 1
        )

        restored = update_user(
            partner_user["id"], {"account_status": "active"}, admin_user
        )
        db.purge_rejected()

        assert restored["rejected_at"] is None
        assert self._exists(partner_user["id"])

    def test_the_sweep_never_touches_a_suspended_or_active_partner(
        self, admin_user, partner_user, partner_user_bronze
    ):
        update_user(partner_user["id"], {"account_status": "suspended"}, admin_user)

        db.purge_rejected()

        assert self._exists(partner_user["id"])
        assert self._exists(partner_user_bronze["id"])
