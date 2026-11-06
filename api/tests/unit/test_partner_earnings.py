"""
PartnerType — what a partner sees about their own money.

For a chosen month, quarter, year or in total: what was paid, what waits for
payout, what the producer marked as paid, and what wins are still queued.

The load-bearing properties, and why each one is worth a test:

1.  A partner's figures come from the SAME columns api/payouts.py settles
    against. If they ever drifted, the two sides of one deal would quote
    different numbers for it -- the single thing that destroys a partner's
    trust in a commission screen.
2.  A claimed-but-unconfirmed win is NOT money owed. Folding it into
    "czeka na wyplate" would promise a partner money the producer never
    agreed to.
3.  A quarter is exactly the sum of its months, and a year of its quarters.
4.  The endpoint is scoped by the SESSION. A partner asking for another
    partner's id gets his own figures back, never theirs.
"""
from api.db import get_conn
from api.payouts import (
    attach_pipeline_forecast,
    get_partner_earnings,
    handle_my_earnings,
    record_payout,
    year_key,
)


def _lead(partner, lead_id, commission, won_at, confirmed=True, paid_at=None,
          registration="accepted", status="won"):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO leads (id, partner_id, client_name, client_email, company, "
            "registration_status, status, value, commission, won_at, created_at, "
            "win_confirmed_at, commission_paid_at) "
            "VALUES (?, ?, 'Klient', 'k@example.test', 'Firma sp. z o.o.', ?, ?, "
            "10000, ?, ?, ?, ?, ?)",
            (lead_id, partner["id"], registration, status, commission, won_at,
             won_at or "2026-01-01T00:00:00", won_at if confirmed else None, paid_at),
        )
        conn.commit()
    finally:
        conn.close()


def _award(partner, amount, granted_at, paid_at=None):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO bonus_awards (partner_id, bonus_def_id, name, amount, "
            "granted_by, granted_at, paid_at) VALUES (?, NULL, 'Nagroda', ?, 1, ?, ?)",
            (partner["id"], amount, granted_at, paid_at),
        )
        conn.commit()
    finally:
        conn.close()


class TestFourStatesOfMoney:
    def test_a_paid_commission_counts_as_paid_and_nothing_else(self, partner_user):
        _lead(partner_user, "LD-P1", 1000, "2026-02-10T00:00:00",
              paid_at="2026-03-05T00:00:00")

        totals = get_partner_earnings(partner_user["id"])["totals"]

        assert totals["paid"] == 1000
        assert totals["paid_lead_count"] == 1
        assert totals["due"] == 0
        assert totals["awaiting"] == 0

    def test_a_confirmed_unpaid_win_is_money_owed(self, partner_user):
        _lead(partner_user, "LD-P2", 800, "2026-02-10T00:00:00")

        totals = get_partner_earnings(partner_user["id"])["totals"]

        assert totals["due"] == 800
        assert totals["due_lead_count"] == 1
        assert totals["paid"] == 0

    def test_a_win_the_producer_has_not_confirmed_is_never_money_owed(self, partner_user):
        """The partner moving a lead onto the win stage is a claim about
        the world, not a fact the producer has agreed to."""
        _lead(partner_user, "LD-P3", 900, "2026-02-10T00:00:00", confirmed=False)

        totals = get_partner_earnings(partner_user["id"])["totals"]

        assert totals["awaiting"] == 900
        assert totals["awaiting_lead_count"] == 1
        assert totals["due"] == 0, "an unconfirmed win must not be promised as a payout"

    def test_an_unpaid_cash_bonus_is_owed_and_a_paid_one_is_paid(self, partner_user):
        _award(partner_user, 500, "2026-02-01T00:00:00")
        _award(partner_user, 250, "2026-02-01T00:00:00", paid_at="2026-03-01T00:00:00")

        totals = get_partner_earnings(partner_user["id"])["totals"]

        assert totals["due"] == 500
        assert totals["due_bonus"] == 500
        assert totals["due_bonus_count"] == 1
        assert totals["paid"] == 250
        assert totals["paid_bonus"] == 250


class TestPeriods:
    def test_paid_is_bucketed_by_the_date_it_was_paid_not_the_date_it_was_won(self, partner_user):
        """A deal won in February and paid in June is June's income to the
        partner: "ile w danym miesiacu dostalem" is a question about the
        payout, and the column hint on the screen says so."""
        _lead(partner_user, "LD-P4", 400, "2026-02-10T00:00:00",
              paid_at="2026-06-20T00:00:00")

        months = get_partner_earnings(partner_user["id"])["periods"]["month"]
        by_key = {row["period_key"]: row for row in months}

        assert by_key["2026-06"]["paid"] == 400
        assert "2026-02" not in by_key

    def test_due_is_bucketed_by_the_win_date_matching_the_producers_own_payout_periods(self, partner_user):
        _lead(partner_user, "LD-P5", 600, "2026-02-10T00:00:00")

        months = get_partner_earnings(partner_user["id"])["periods"]["month"]

        assert [row["period_key"] for row in months] == ["2026-02"]
        assert months[0]["due"] == 600

    def test_a_quarter_is_exactly_the_sum_of_its_months(self, partner_user):
        _lead(partner_user, "LD-P6", 100, "2026-01-10T00:00:00")
        _lead(partner_user, "LD-P7", 200, "2026-02-10T00:00:00")
        _lead(partner_user, "LD-P8", 300, "2026-03-10T00:00:00")

        periods = get_partner_earnings(partner_user["id"])["periods"]
        months = sum(row["due"] for row in periods["month"])
        quarters = {row["period_key"]: row["due"] for row in periods["quarter"]}

        assert months == 600
        assert quarters == {"2026-Q1": 600}

    def test_a_year_is_exactly_the_sum_of_its_quarters(self, partner_user):
        _lead(partner_user, "LD-P9", 100, "2026-01-10T00:00:00")
        _lead(partner_user, "LD-P10", 700, "2026-11-10T00:00:00")

        periods = get_partner_earnings(partner_user["id"])["periods"]
        quarters = sum(row["due"] for row in periods["quarter"])
        years = {row["period_key"]: row["due"] for row in periods["year"]}

        assert quarters == 800
        assert years == {"2026": 800}

    def test_period_rows_come_back_newest_first(self, partner_user):
        _lead(partner_user, "LD-P11", 100, "2026-01-10T00:00:00")
        _lead(partner_user, "LD-P12", 100, "2026-05-10T00:00:00")

        months = get_partner_earnings(partner_user["id"])["periods"]["month"]

        assert [row["period_key"] for row in months] == ["2026-05", "2026-01"]

    def test_year_key_reads_the_first_four_characters(self):
        assert year_key("2026-09-14T10:00:00") == "2026"


class TestAgreementWithTheProducersOwnLedger:
    def test_recording_a_payout_moves_the_partners_figure_from_due_to_paid(self, partner_user, admin_user):
        """The one property that matters most: the partner's screen and the
        producer's payout screen are reading the same fact, so pressing
        "wyplacono" on one side is visible on the other with no second ledger."""
        _lead(partner_user, "LD-P13", 1500, "2026-04-10T00:00:00")
        before = get_partner_earnings(partner_user["id"])["totals"]

        record_payout({"partner_id": partner_user["id"], "period_type": "month",
                       "period_key": "2026-04"}, admin_user)
        after = get_partner_earnings(partner_user["id"])["totals"]

        assert before["due"] == 1500 and before["paid"] == 0
        assert after["due"] == 0 and after["paid"] == 1500

    def test_the_receipt_the_producer_wrote_is_readable_by_the_partner(self, partner_user, admin_user):
        _lead(partner_user, "LD-P14", 200, "2026-04-10T00:00:00")
        record_payout({"partner_id": partner_user["id"], "period_type": "month",
                       "period_key": "2026-04"}, admin_user)

        payouts = get_partner_earnings(partner_user["id"])["payouts"]

        assert len(payouts) == 1
        assert payouts[0]["period_key"] == "2026-04"
        assert payouts[0]["amount"] == 200


class TestPipelineForecast:
    def test_only_leads_still_in_flight_are_counted_as_pipeline(self, partner_user):
        summary = get_partner_earnings(partner_user["id"])
        leads = [
            {"registration_status": "accepted", "commission_projected": 300},
            {"registration_status": "accepted", "commission_projected": 700},
            # already won and priced -- that is `due` or `paid`, not a forecast
            {"registration_status": "accepted", "commission": 100,
             "won_at": "2026-01-01T00:00:00", "commission_projected": 100},
            # never accepted -- it will never earn anything
            {"registration_status": "rejected", "commission_projected": 999},
        ]

        totals = attach_pipeline_forecast(summary, leads)["totals"]

        assert totals["pipeline"] == 1000
        assert totals["pipeline_lead_count"] == 2

    def test_a_lost_lead_is_not_money_on_the_way(self, partner_user):
        # A lost deal carries no won_at and no commission: only its stage
        # closes it, so it leaves the forecast.
        summary = get_partner_earnings(partner_user["id"])
        leads = [
            {"registration_status": "accepted", "status": "negotiation", "commission_projected": 500},
            {"registration_status": "accepted", "status": "lost", "commission_projected": 16800},
        ]

        totals = attach_pipeline_forecast(summary, leads)["totals"]

        assert totals["pipeline"] == 500
        assert totals["pipeline_lead_count"] == 1


class TestScoping:
    def test_a_partner_reads_his_own_figures_whatever_partner_id_he_asks_for(self, partner_user, partner_user_bronze):
        _lead(partner_user, "LD-P15", 111, "2026-02-10T00:00:00")
        _lead(partner_user_bronze, "LD-P16", 999, "2026-02-10T00:00:00")

        status, body = handle_my_earnings(
            None, partner_user, {"partner_id": [str(partner_user_bronze["id"])]}
        )

        assert status == 200
        assert body["partner_id"] == partner_user["id"]
        assert body["totals"]["due"] == 111, "another partner's money must not leak"

    def test_staff_may_name_a_partner(self, admin_user, partner_user):
        _lead(partner_user, "LD-P17", 222, "2026-02-10T00:00:00")

        status, body = handle_my_earnings(
            None, admin_user, {"partner_id": [str(partner_user["id"])]}
        )

        assert status == 200
        assert body["totals"]["due"] == 222

    def test_staff_asking_for_nobody_is_a_bad_request_not_an_empty_answer(self, admin_user):
        status, body = handle_my_earnings(None, admin_user, {})

        assert status == 400
        assert "error" in body
