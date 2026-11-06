"""
PartnerType -- commission payouts.

The producer marks a month or a quarter as paid and that sum disappears from
what is owed; paying a month shrinks the quarter's sum too.

That cascade is the load-bearing property here, and it only holds because a
single fact -- `leads.commission_paid_at` -- is what every figure is derived
from. These tests pin it, plus the three things a producer cannot be allowed to
get wrong with money: paying the same period twice, a period derived from the
wrong date, and a moderator moving money.
"""
import pytest

from api import bonus_awards
from api.db import get_conn
from api.payouts import (
    confirm_wins,
    get_evidence,
    get_outstanding_summary,
    get_pending_wins,
    get_partner_history,
    get_partner_outstanding,
    handle_record,
    handle_reverse,
    quarter_key,
    record_payout,
    reverse_payout,
)
# ValueError, not ValidationError, is the contract: ValidationError subclasses
# it, the shared parse_* helpers raise either, and api/payouts.py's HTTP
# handlers catch ValueError. Asserting the narrower class here would pass or
# fail on which helper happened to reject the input.


def _won_lead(partner, lead_id, commission, won_at, confirmed=True):
    """A won lead with a recorded commission.

    `confirmed` defaults to True because these tests are about PAYING, and only a win the producer has confirmed is money owed at all. Pass False
    to exercise the gate itself.
    """
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO leads (id, partner_id, client_name, client_email, company, "
            "registration_status, status, value, commission, won_at, created_at, "
            "win_confirmed_at) "
            "VALUES (?, ?, 'Klient', 'k@example.test', 'Firma sp. z o.o.', "
            "'accepted', 'won', 10000, ?, ?, ?, ?)",
            (lead_id, partner["id"], commission, won_at, won_at,
             won_at if confirmed else None),
        )
        conn.commit()
    finally:
        conn.close()


def _bonus_definition(name="Nagroda", name_en="", value=500.0):
    conn = get_conn()
    try:
        cursor = conn.execute(
            "INSERT INTO bonus_definitions (name, name_en, type, default_value, "
            "description, sort_order, active, created_at) "
            "VALUES (?, ?, 'cash', ?, '', 0, 1, '2026-09-01')",
            (name, name_en, value),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _paid_at(lead_id):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT commission_paid_at FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        return row["commission_paid_at"]
    finally:
        conn.close()


class TestPeriodKeys:
    @pytest.mark.parametrize("won_at,expected", [
        ("2026-01-05T00:00:00", "2026-Q1"),
        ("2026-03-31T23:59:00", "2026-Q1"),
        ("2026-04-01T00:00:00", "2026-Q2"),
        ("2026-09-14T10:00:00", "2026-Q3"),
        ("2026-12-31T23:00:00", "2026-Q4"),
    ])
    def test_quarter_boundaries(self, won_at, expected):
        assert quarter_key(won_at) == expected


class TestOutstanding:
    def test_groups_one_partner_by_month_and_quarter_from_the_same_leads(
        self, partner_user
    ):
        _won_lead(partner_user, "LD-801", 1000, "2026-07-10T10:00:00")
        _won_lead(partner_user, "LD-802", 500,  "2026-08-02T10:00:00")
        _won_lead(partner_user, "LD-803", 250,  "2026-08-20T10:00:00")

        owed = get_partner_outstanding(partner_user["id"])

        assert owed["total"] == 1750
        assert owed["lead_count"] == 3
        # Newest period first: the producer looks at the recent one.
        # bonus_amount/bonus_count are zero here on purpose: this partner has
        # no one-off bonuses, and a period made only of commissions must not
        # start reporting bonus figures it does not have.
        assert owed["months"] == [
            {"period_type": "month", "period_key": "2026-08", "amount": 750,
             "lead_count": 2, "bonus_amount": 0.0, "bonus_count": 0},
            {"period_type": "month", "period_key": "2026-07", "amount": 1000,
             "lead_count": 1, "bonus_amount": 0.0, "bonus_count": 0},
        ]
        # One quarter holding both months, and its amount is exactly their sum.
        assert owed["quarters"] == [
            {"period_type": "quarter", "period_key": "2026-Q3", "amount": 1750,
             "lead_count": 3, "bonus_amount": 0.0, "bonus_count": 0},
        ]

    def test_a_win_with_no_recorded_commission_is_not_money_owed(self, partner_user):
        """Auto-commission can be switched off, leaving a win with no amount.

        Reporting that as a debt would invent a number the producer never
        agreed to.
        """
        _won_lead(partner_user, "LD-804", 1000, "2026-07-10T10:00:00")
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO leads (id, partner_id, client_name, client_email, company, "
                "registration_status, status, value, commission, won_at, created_at) "
                "VALUES ('LD-805', ?, 'K', 'k@example.test', 'F', 'accepted', 'won', "
                "10000, NULL, '2026-07-11T10:00:00', '2026-07-11T10:00:00')",
                (partner_user["id"],),
            )
            conn.commit()
        finally:
            conn.close()

        assert get_partner_outstanding(partner_user["id"])["total"] == 1000

    def test_summary_lists_only_partners_who_are_owed_something(
        self, partner_user, partner_user_bronze
    ):
        _won_lead(partner_user, "LD-806", 400, "2026-07-10T10:00:00")

        summary = get_outstanding_summary()

        assert [row["partner_id"] for row in summary] == [partner_user["id"]]
        assert summary[0]["amount"] == 400
        assert summary[0]["lead_count"] == 1


class TestPayingAPeriod:
    def test_paying_a_month_shrinks_the_quarter_that_contains_it(
        self, partner_user, admin_user
    ):
        """The payout cascade, and the reason nothing is cached."""
        _won_lead(partner_user, "LD-810", 1000, "2026-07-10T10:00:00")
        _won_lead(partner_user, "LD-811", 600,  "2026-08-02T10:00:00")
        assert get_partner_outstanding(partner_user["id"])["quarters"][0]["amount"] == 1600

        record_payout(
            {"partner_id": partner_user["id"], "period_type": "month",
             "period_key": "2026-07"},
            admin_user,
        )

        owed = get_partner_outstanding(partner_user["id"])
        assert owed["total"] == 600
        assert [m["period_key"] for m in owed["months"]] == ["2026-08"]
        # Nobody updated the quarter: it is derived, so it simply is smaller.
        assert owed["quarters"][0]["amount"] == 600

    def test_paying_a_quarter_settles_every_month_inside_it(
        self, partner_user, admin_user
    ):
        _won_lead(partner_user, "LD-812", 1000, "2026-07-10T10:00:00")
        _won_lead(partner_user, "LD-813", 600,  "2026-09-02T10:00:00")

        result = record_payout(
            {"partner_id": partner_user["id"], "period_type": "quarter",
             "period_key": "2026-Q3"},
            admin_user,
        )

        assert result["amount"] == 1600
        assert result["lead_count"] == 2
        owed = get_partner_outstanding(partner_user["id"])
        assert owed["total"] == 0
        assert owed["months"] == []

    def test_a_period_is_derived_from_when_the_deal_was_won(
        self, partner_user, admin_user
    ):
        """Not from today. A March deal paid in June belongs to March.

        Deriving from the payment date instead would make a month impossible to
        reconcile afterwards.
        """
        _won_lead(partner_user, "LD-814", 900, "2026-03-15T10:00:00")

        record_payout(
            {"partner_id": partner_user["id"], "period_type": "month",
             "period_key": "2026-03"},
            admin_user,
        )

        assert get_partner_outstanding(partner_user["id"])["total"] == 0

    def test_paying_the_same_period_twice_is_refused(self, partner_user, admin_user):
        _won_lead(partner_user, "LD-815", 900, "2026-07-15T10:00:00")
        body = {"partner_id": partner_user["id"], "period_type": "month",
                "period_key": "2026-07"}
        record_payout(body, admin_user)

        with pytest.raises(ValueError):
            record_payout(body, admin_user)

    def test_an_empty_period_is_refused_rather_than_recorded_as_zero(
        self, partner_user, admin_user
    ):
        with pytest.raises(ValueError):
            record_payout(
                {"partner_id": partner_user["id"], "period_type": "month",
                 "period_key": "2026-07"},
                admin_user,
            )

    @pytest.mark.parametrize("period_type,period_key", [
        ("month", "2026-13"),
        ("month", "2026-9"),
        ("month", "2026-Q3"),
        ("quarter", "2026-Q5"),
        ("quarter", "2026-09"),
        ("week", "2026-W1"),
    ])
    def test_a_malformed_period_is_refused(
        self, partner_user, admin_user, period_type, period_key
    ):
        """A typo must not silently stamp the wrong set of leads."""
        _won_lead(partner_user, "LD-816", 100, "2026-09-15T10:00:00")

        with pytest.raises(ValueError):
            record_payout(
                {"partner_id": partner_user["id"], "period_type": period_type,
                 "period_key": period_key},
                admin_user,
            )

    def test_nothing_is_stamped_when_the_payout_is_refused(
        self, partner_user, admin_user
    ):
        """All or nothing: a half-applied payout cannot be reconciled."""
        _won_lead(partner_user, "LD-817", 100, "2026-09-15T10:00:00")

        with pytest.raises(ValueError):
            record_payout(
                {"partner_id": partner_user["id"], "period_type": "month",
                 "period_key": "2026-08"},
                admin_user,
            )

        assert _paid_at("LD-817") is None


class TestHistory:
    def test_history_reports_the_receipt_and_the_year_total(
        self, partner_user, admin_user
    ):
        _won_lead(partner_user, "LD-820", 1200, "2026-07-10T10:00:00")
        record_payout(
            {"partner_id": partner_user["id"], "period_type": "month",
             "period_key": "2026-07", "note": "Przelew 05.09"},
            admin_user,
        )

        history = get_partner_history(partner_user["id"])

        assert len(history["payouts"]) == 1
        receipt = history["payouts"][0]
        assert receipt["period_key"] == "2026-07"
        assert receipt["amount"] == 1200
        assert receipt["note"] == "Przelew 05.09"
        assert receipt["reversed_at"] is None
        assert history["years"][0]["amount"] == 1200


class TestReversing:
    def test_reversing_returns_the_money_to_outstanding_and_keeps_the_receipt(
        self, partner_user, admin_user
    ):
        _won_lead(partner_user, "LD-830", 700, "2026-07-10T10:00:00")
        payout = record_payout(
            {"partner_id": partner_user["id"], "period_type": "month",
             "period_key": "2026-07"},
            admin_user,
        )

        result = reverse_payout(payout["id"], {}, admin_user)

        assert result["released_leads"] == 1
        assert get_partner_outstanding(partner_user["id"])["total"] == 700
        # The receipt survives, marked: money handed over and clawed back is a
        # fact about the relationship.
        history = get_partner_history(partner_user["id"])
        assert len(history["payouts"]) == 1
        assert history["payouts"][0]["reversed_at"] is not None
        # ...and it no longer counts toward what was paid this year.
        assert history["years"] == []

    def test_reversing_touches_only_the_leads_that_payout_stamped(
        self, partner_user, admin_user
    ):
        _won_lead(partner_user, "LD-831", 300, "2026-07-10T10:00:00")
        _won_lead(partner_user, "LD-832", 400, "2026-08-10T10:00:00")
        july = record_payout(
            {"partner_id": partner_user["id"], "period_type": "month",
             "period_key": "2026-07"},
            admin_user,
        )
        record_payout(
            {"partner_id": partner_user["id"], "period_type": "month",
             "period_key": "2026-08"},
            admin_user,
        )

        reverse_payout(july["id"], {}, admin_user)

        assert _paid_at("LD-831") is None
        assert _paid_at("LD-832") is not None
        assert get_partner_outstanding(partner_user["id"])["total"] == 300

    def test_reversing_without_a_note_keeps_the_one_the_payout_carried(
        self, partner_user, admin_user
    ):
        """How the money was sent is the note's job, and undoing the payout is
        not a reason to forget it. COALESCE did not protect this: the shared
        parse_optional_string helper returns "" for a missing field, and an
        empty string is a perfectly good COALESCE argument."""
        _won_lead(partner_user, "LD-834", 300, "2026-07-10T10:00:00")
        payout = record_payout(
            {"partner_id": partner_user["id"], "period_type": "month",
             "period_key": "2026-07", "note": "Przelew 05.09"},
            admin_user,
        )

        reverse_payout(payout["id"], {}, admin_user)

        assert get_partner_history(partner_user["id"])["payouts"][0]["note"] == "Przelew 05.09"

    def test_reversing_twice_is_refused(self, partner_user, admin_user):
        _won_lead(partner_user, "LD-833", 300, "2026-07-10T10:00:00")
        payout = record_payout(
            {"partner_id": partner_user["id"], "period_type": "month",
             "period_key": "2026-07"},
            admin_user,
        )
        reverse_payout(payout["id"], {}, admin_user)

        with pytest.raises(ValueError):
            reverse_payout(payout["id"], {}, admin_user)


class TestAuthorization:
    def test_a_moderator_may_read_but_not_pay(self, partner_user, manager_user, admin_user):
        _won_lead(partner_user, "LD-840", 500, "2026-07-10T10:00:00")

        status, _ = handle_record(
            None, manager_user,
            {"partner_id": partner_user["id"], "period_type": "month",
             "period_key": "2026-07"},
        )
        assert status == 403

        # Reading is fine -- a moderator chases partners and needs the figures.
        from api.payouts import handle_partner_outstanding
        status, body = handle_partner_outstanding(None, manager_user, partner_user["id"])
        assert status == 200
        assert body["total"] == 500

    def test_a_partner_may_not_read_the_payout_ledger(self, partner_user):
        from api.payouts import handle_outstanding
        status, _ = handle_outstanding(None, partner_user)
        assert status == 403

    def test_a_moderator_may_not_reverse_a_payout(
        self, partner_user, admin_user, manager_user
    ):
        _won_lead(partner_user, "LD-841", 500, "2026-07-10T10:00:00")
        payout = record_payout(
            {"partner_id": partner_user["id"], "period_type": "month",
             "period_key": "2026-07"},
            admin_user,
        )

        status, _ = handle_reverse(None, manager_user, payout["id"], {})

        assert status == 403
        assert _paid_at("LD-841") is not None


class TestHttpBoundary:
    """The handlers turn a rejected write into a 400 the screen can show.

    Worth pinning separately from the domain functions: an unhandled ValueError
    here would reach the router's generic 500 branch, and the producer would be
    told "something went wrong" instead of "there is nothing to pay for that
    month".
    """

    def test_an_empty_period_is_a_400_with_a_readable_reason(
        self, partner_user, admin_user
    ):
        status, body = handle_record(
            None, admin_user,
            {"partner_id": partner_user["id"], "period_type": "month",
             "period_key": "2026-07"},
        )

        assert status == 400
        assert "wypłaty" in body["error"]

    def test_an_unknown_partner_is_a_400(self, admin_user):
        status, body = handle_record(
            None, admin_user,
            {"partner_id": 999999, "period_type": "month", "period_key": "2026-07"},
        )

        assert status == 400
        assert "partnera" in body["error"]

    def test_reversing_something_that_does_not_exist_is_a_400(self, admin_user):
        status, body = handle_reverse(None, admin_user, 999999, {})

        assert status == 400
        assert "wypłaty" in body["error"]

    def test_a_bad_partner_id_on_a_read_is_a_400(self, admin_user):
        from api.payouts import handle_partner_history

        status, _ = handle_partner_history(None, admin_user, "nie-liczba")

        assert status == 400

    def test_a_successful_payout_is_a_201_carrying_the_receipt(
        self, partner_user, admin_user
    ):
        _won_lead(partner_user, "LD-850", 250, "2026-07-10T10:00:00")

        status, body = handle_record(
            None, admin_user,
            {"partner_id": partner_user["id"], "period_type": "month",
             "period_key": "2026-07"},
        )

        assert status == 201
        assert body["payout"]["amount"] == 250
        assert body["payout"]["lead_count"] == 1


class TestConfirmingAWin:
    """
    The partner moving a lead onto the win stage is a claim; the producer
    confirms the win. Otherwise the partner would decide when money becomes
    due.
    """

    def test_an_unconfirmed_win_is_not_money_owed(self, partner_user):
        _won_lead(partner_user, "LD-860", 900, "2026-07-10T10:00:00", confirmed=False)

        assert get_partner_outstanding(partner_user["id"])["total"] == 0
        assert get_outstanding_summary() == []

    def test_confirming_makes_it_payable(self, partner_user, admin_user):
        _won_lead(partner_user, "LD-861", 900, "2026-07-10T10:00:00", confirmed=False)

        confirm_wins(["LD-861"], admin_user)

        assert get_partner_outstanding(partner_user["id"])["total"] == 900

    def test_the_queue_lists_only_claims_still_waiting(self, partner_user, admin_user):
        _won_lead(partner_user, "LD-862", 300, "2026-07-10T10:00:00", confirmed=False)
        _won_lead(partner_user, "LD-863", 400, "2026-07-11T10:00:00", confirmed=True)

        pending = get_pending_wins()

        assert [win["id"] for win in pending] == ["LD-862"]
        assert pending[0]["partner_name"] == partner_user["name"]

    def test_a_deal_still_being_sold_cannot_be_confirmed(self, partner_user, admin_user):
        """There is nothing to agree to on a lead that is not claimed as won."""
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO leads (id, partner_id, client_name, client_email, company, "
                "registration_status, status, value, created_at) "
                "VALUES ('LD-864', ?, 'K', 'k@example.test', 'F', 'accepted', 'offer', "
                "9000, '2026-07-10T10:00:00')",
                (partner_user["id"],),
            )
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(ValueError):
            confirm_wins(["LD-864"], admin_user)

    def test_confirming_twice_keeps_the_original_date(self, partner_user, admin_user):
        _won_lead(partner_user, "LD-865", 500, "2026-07-10T10:00:00", confirmed=False)
        confirm_wins(["LD-865"], admin_user)
        conn = get_conn()
        try:
            first = conn.execute(
                "SELECT win_confirmed_at FROM leads WHERE id = 'LD-865'"
            ).fetchone()["win_confirmed_at"]
        finally:
            conn.close()

        # Nothing left to confirm, so this is refused rather than silently
        # moving the date the producer agreed to it.
        with pytest.raises(ValueError):
            confirm_wins(["LD-865"], admin_user)

        conn = get_conn()
        try:
            assert conn.execute(
                "SELECT win_confirmed_at FROM leads WHERE id = 'LD-865'"
            ).fetchone()["win_confirmed_at"] == first
        finally:
            conn.close()

    def test_a_moderator_may_see_the_queue_but_not_confirm(
        self, partner_user, manager_user
    ):
        from api.payouts import handle_confirm_wins, handle_pending_wins
        _won_lead(partner_user, "LD-866", 500, "2026-07-10T10:00:00", confirmed=False)

        assert handle_pending_wins(None, manager_user)[0] == 200
        # Confirming is what makes money become owed, so it is the owner's call.
        assert handle_confirm_wins(None, manager_user, {"lead_ids": ["LD-866"]})[0] == 403


class TestEvidence:
    """A record of who was paid how much, for what and when, with month and
    quarter totals."""

    def test_records_what_each_payment_was_for(self, partner_user, admin_user):
        _won_lead(partner_user, "LD-870", 1200, "2026-07-10T10:00:00")
        record_payout(
            {"partner_id": partner_user["id"], "period_type": "month",
             "period_key": "2026-07"},
            admin_user,
        )

        evidence = get_evidence()

        assert evidence["total"] == 1200
        assert len(evidence["entries"]) == 1
        entry = evidence["entries"][0]
        assert entry["lead_id"] == "LD-870"
        assert entry["amount"] == 1200
        assert entry["partner_id"] == partner_user["id"]
        # Both dates: what it was for, and when it was settled.
        assert entry["won_at"].startswith("2026-07-10")
        assert entry["paid_at"]

    def test_sums_by_month_and_by_quarter_of_the_payment(self, partner_user, admin_user):
        _won_lead(partner_user, "LD-871", 600, "2026-07-10T10:00:00")
        _won_lead(partner_user, "LD-872", 400, "2026-08-10T10:00:00")
        record_payout({"partner_id": partner_user["id"], "period_type": "quarter",
                       "period_key": "2026-Q3"}, admin_user)

        evidence = get_evidence()

        # One payout, so one payment month -- and the quarter holding it agrees.
        assert len(evidence["months"]) == 1
        assert evidence["months"][0]["amount"] == 1000
        assert evidence["months"][0]["lead_count"] == 2
        assert evidence["quarters"][0]["amount"] == 1000

    def test_a_reversed_payout_leaves_the_record(self, partner_user, admin_user):
        _won_lead(partner_user, "LD-873", 700, "2026-07-10T10:00:00")
        payout = record_payout({"partner_id": partner_user["id"], "period_type": "month",
                                "period_key": "2026-07"}, admin_user)

        reverse_payout(payout["id"], {}, admin_user)

        # Built from the leads, so reversing clears the stamp and the entry drops
        # out on its own -- the evidence is of money that is actually out.
        assert get_evidence()["total"] == 0
        assert get_evidence()["entries"] == []

    def test_totals_per_partner_rank_the_biggest_first(
        self, partner_user, partner_user_bronze, admin_user
    ):
        _won_lead(partner_user, "LD-874", 300, "2026-07-10T10:00:00")
        _won_lead(partner_user_bronze, "LD-875", 900, "2026-07-11T10:00:00")
        for partner in (partner_user, partner_user_bronze):
            record_payout({"partner_id": partner["id"], "period_type": "month",
                           "period_key": "2026-07"}, admin_user)

        partners = get_evidence()["partners"]

        assert [row["amount"] for row in partners] == [900, 300]


class TestBonusNamesCarryTheirEnglishSnapshot:
    """outstanding breakdowns and the paid ledger show the SAME
    historical name a partner saw when the bonus was granted -- never a JOIN
    to whatever the definition says today, and never the customer's own
    company name translated (that field belongs to the customer, not the
    producer)."""

    def test_the_outstanding_breakdown_carries_the_awards_english_snapshot(
        self, admin_user, partner_user
    ):
        definition = _bonus_definition(
            name="Nagroda kwartalna", name_en="Quarterly award", value=250
        )
        bonus_awards.grant([partner_user["id"]], definition, admin_user)

        owed = get_partner_outstanding(partner_user["id"])

        assert owed["bonuses"][0]["name"] == "Nagroda kwartalna"
        assert owed["bonuses"][0]["name_en"] == "Quarterly award"

    def test_a_paid_bonus_line_in_the_evidence_carries_its_english_snapshot(
        self, admin_user, partner_user
    ):
        definition = _bonus_definition(
            name="Nagroda za webinar", name_en="Webinar award", value=500
        )
        created = bonus_awards.grant([partner_user["id"]], definition, admin_user)
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE bonus_awards SET granted_at = ? WHERE id = ?",
                ("2026-09-10T09:00:00", created[0]["id"]),
            )
            conn.commit()
        finally:
            conn.close()
        record_payout(
            {"partner_id": partner_user["id"], "period_type": "month",
             "period_key": "2026-09"}, admin_user,
        )

        entries = [e for e in get_evidence()["entries"] if e["kind"] == "bonus"]

        assert entries[0]["company"] == "Nagroda za webinar"
        assert entries[0]["company_en"] == "Webinar award"

    def test_a_customer_company_name_in_the_evidence_has_no_english_snapshot(
        self, admin_user, partner_user
    ):
        """A commission line names the CUSTOMER's company. It is never a
        producer-owned label and must never carry a company_en field."""
        _won_lead(partner_user, "LD-880", 400, "2026-07-10T10:00:00")
        record_payout(
            {"partner_id": partner_user["id"], "period_type": "month",
             "period_key": "2026-07"}, admin_user,
        )

        entries = [e for e in get_evidence()["entries"] if e["kind"] == "commission"]

        assert entries[0]["company"] == "Firma sp. z o.o."
        assert entries[0].get("company_en") in (None, "")
