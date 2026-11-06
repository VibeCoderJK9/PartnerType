"""
PartnerType -- one-off bonus awards.

One-off bonuses are not assigned to partners: they are granted for payout
and recorded. An assigned cash bonus would read "+500 PLN" on a partner's
card forever while no payout ever cleared it, because the commission
arithmetic deliberately excludes cash. These tests pin the shape -- granted
once, owed from that moment, settled through the period's payout, and visible
in the record afterwards -- plus the closed door to assigning one.
"""
import pytest

from api import bonus_awards, partner_bonuses
from api.db import get_conn
from api.payouts import (
    get_evidence,
    get_outstanding_summary,
    get_partner_history,
    get_partner_outstanding,
    record_payout,
    reverse_payout,
)


def _definition(name="Nagroda", bonus_type="cash", value=500.0, active=1, name_en=""):
    conn = get_conn()
    try:
        cursor = conn.execute(
            "INSERT INTO bonus_definitions (name, name_en, type, default_value, description, "
            "sort_order, active, created_at) VALUES (?, ?, ?, ?, '', 0, ?, '2026-09-01')",
            (name, name_en, bonus_type, value, active),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _granted_at(award_id, when):
    conn = get_conn()
    try:
        conn.execute("UPDATE bonus_awards SET granted_at = ? WHERE id = ?",
                     (when, award_id))
        conn.commit()
    finally:
        conn.close()


class TestGranting:
    def test_a_granted_bonus_is_money_owed_immediately(self, admin_user, partner_user):
        definition = _definition(value=750)

        bonus_awards.grant([partner_user["id"]], definition, admin_user)

        owed = get_partner_outstanding(partner_user["id"])
        assert owed["bonus_total"] == 750
        assert owed["total"] == 750
        # A bonus is not a deal, and the two counts stay apart.
        assert owed["lead_count"] == 0
        assert owed["bonus_count"] == 1

    def test_the_name_is_snapshotted_against_a_later_rename(
        self, admin_user, partner_user
    ):
        """What a partner was told they were getting cannot be rewritten."""
        definition = _definition(name="Nagroda za webinar")
        bonus_awards.grant([partner_user["id"]], definition, admin_user)

        conn = get_conn()
        try:
            conn.execute("UPDATE bonus_definitions SET name = 'Coś innego' WHERE id = ?",
                         (definition,))
            conn.commit()
        finally:
            conn.close()

        assert bonus_awards.list_awards(partner_user["id"])[0]["name"] == "Nagroda za webinar"

    def test_a_percent_bonus_cannot_be_granted_as_a_payment(
        self, admin_user, partner_user
    ):
        """Granting "1%" as a one-off would silently pay 1 zloty."""
        definition = _definition(bonus_type="percent", value=1.0)

        with pytest.raises(ValueError):
            bonus_awards.grant([partner_user["id"]], definition, admin_user)

    def test_a_partner_cannot_grant_themselves_a_bonus(self, partner_user):
        definition = _definition()

        with pytest.raises(PermissionError):
            bonus_awards.grant([partner_user["id"]], definition, partner_user)

    def test_a_moderator_cannot_create_money_owed(self, manager_user, partner_user):
        definition = _definition()

        with pytest.raises(PermissionError):
            bonus_awards.grant([partner_user["id"]], definition, manager_user)

        assert get_partner_outstanding(partner_user["id"])["bonus_total"] == 0

    def test_a_moderator_is_refused_over_http_too(self, manager_user, partner_user):
        definition = _definition()

        status, body = bonus_awards.handle_grant(
            None, manager_user,
            {"partner_ids": [partner_user["id"]], "bonus_def_id": definition},
        )

        assert status == 403
        assert "awards" not in body

    def test_granting_to_several_partners_creates_one_award_each(
        self, admin_user, partner_user, partner_user_bronze
    ):
        definition = _definition(value=200)

        created = bonus_awards.grant(
            [partner_user["id"], partner_user_bronze["id"]], definition, admin_user
        )

        assert len(created) == 2
        assert sum(a["amount"] for a in created) == 400


class TestItIsNotARate:
    def test_a_cash_bonus_can_no_longer_be_assigned_to_a_partner(
        self, admin_user, partner_user
    ):
        """The door that created the problem in the first place."""
        definition = _definition()

        with pytest.raises(ValueError):
            partner_bonuses.assign_bonus(
                partner_user["id"], definition, None, admin_user
            )

    def test_a_granted_bonus_does_not_change_the_commission_rate(
        self, admin_user, partner_user
    ):
        definition = _definition(value=5000)
        before = partner_bonuses.get_partner_summary(partner_user["id"])["total_percent"]

        bonus_awards.grant([partner_user["id"]], definition, admin_user)

        after = partner_bonuses.get_partner_summary(partner_user["id"])
        assert after["total_percent"] == before
        # It shows on the card as money owed, which is a different claim.
        assert after["total_cash"] == 5000


class TestSettlement:
    def test_paying_the_period_clears_the_bonus_and_records_it(
        self, admin_user, partner_user
    ):
        definition = _definition(value=500)
        created = bonus_awards.grant([partner_user["id"]], definition, admin_user)
        _granted_at(created[0]["id"], "2026-09-10T09:00:00")

        payout = record_payout({
            "partner_id": partner_user["id"],
            "period_type": "month",
            "period_key": "2026-09",
        }, admin_user)

        assert payout["bonus_amount"] == 500
        assert payout["amount"] == 500
        assert get_partner_outstanding(partner_user["id"])["total"] == 0
        # "komu, ile, za co i kiedy" -- the bonus is a line in the record.
        entries = [e for e in get_evidence()["entries"] if e["kind"] == "bonus"]
        assert len(entries) == 1
        assert entries[0]["amount"] == 500

    def test_a_bonus_granted_in_another_month_is_not_swept_into_this_payout(
        self, admin_user, partner_user
    ):
        definition = _definition(value=300)
        # Two separate grants: parse_integer_id_list de-duplicates, so the same
        # partner twice in one call is one award, not two.
        august = bonus_awards.grant([partner_user["id"]], definition, admin_user)
        september = bonus_awards.grant([partner_user["id"]], definition, admin_user)
        _granted_at(august[0]["id"], "2026-08-04T09:00:00")
        _granted_at(september[0]["id"], "2026-09-04T09:00:00")

        payout = record_payout({
            "partner_id": partner_user["id"],
            "period_type": "month",
            "period_key": "2026-09",
        }, admin_user)

        assert payout["bonus_amount"] == 300
        assert get_partner_outstanding(partner_user["id"])["bonus_total"] == 300

    def test_reversing_a_payout_puts_the_bonus_back_on_the_books(
        self, admin_user, partner_user
    ):
        definition = _definition(value=500)
        created = bonus_awards.grant([partner_user["id"]], definition, admin_user)
        _granted_at(created[0]["id"], "2026-09-10T09:00:00")
        payout = record_payout({
            "partner_id": partner_user["id"],
            "period_type": "month",
            "period_key": "2026-09",
        }, admin_user)

        result = reverse_payout(payout["id"], {}, admin_user)

        assert result["released_bonuses"] == 1
        assert get_partner_outstanding(partner_user["id"])["bonus_total"] == 500

    def test_the_yearly_total_counts_bonuses_alongside_commissions(
        self, admin_user, partner_user
    ):
        definition = _definition(value=500)
        created = bonus_awards.grant([partner_user["id"]], definition, admin_user)
        _granted_at(created[0]["id"], "2026-09-10T09:00:00")
        record_payout({
            "partner_id": partner_user["id"],
            "period_type": "month",
            "period_key": "2026-09",
        }, admin_user)

        years = get_partner_history(partner_user["id"])["years"]
        assert years[0]["amount"] == 500
        assert years[0]["bonus_count"] == 1

    def test_the_outstanding_roster_lists_a_partner_who_only_has_a_bonus(
        self, admin_user, partner_user
    ):
        """A partner with no sales at all still has to appear once owed money."""
        definition = _definition(value=250)
        bonus_awards.grant([partner_user["id"]], definition, admin_user)

        rows = [r for r in get_outstanding_summary()
                if r["partner_id"] == partner_user["id"]]
        assert len(rows) == 1
        assert rows[0]["amount"] == 250
        assert rows[0]["lead_count"] == 0


class TestEnglishSnapshot:
    """the award carries the definition's English name at the
    moment it was granted, exactly like the Polish `name` already does --
    immune to a later rename or translation, and empty for an award granted
    from a definition that had no English name yet."""

    def test_the_grant_snapshots_the_definitions_current_english_name(
        self, admin_user, partner_user
    ):
        definition = _definition(
            name="Nagroda za webinar", name_en="Webinar award", value=300
        )

        created = bonus_awards.grant([partner_user["id"]], definition, admin_user)

        assert created[0]["name_en"] == "Webinar award"
        assert bonus_awards.list_awards(partner_user["id"])[0]["name_en"] == "Webinar award"

    def test_the_english_snapshot_survives_a_later_rename(
        self, admin_user, partner_user
    ):
        """What a partner was told cannot be rewritten -- in either language."""
        definition = _definition(name="Nagroda za webinar", name_en="Webinar award")
        bonus_awards.grant([partner_user["id"]], definition, admin_user)

        conn = get_conn()
        try:
            conn.execute(
                "UPDATE bonus_definitions SET name = 'Coś innego', "
                "name_en = 'Something else' WHERE id = ?",
                (definition,),
            )
            conn.commit()
        finally:
            conn.close()

        award = bonus_awards.list_awards(partner_user["id"])[0]
        assert award["name"] == "Nagroda za webinar"
        assert award["name_en"] == "Webinar award"

    def test_an_award_from_an_untranslated_definition_carries_an_empty_snapshot(
        self, admin_user, partner_user
    ):
        """The current definition is never a trustworthy source for a
        historical English name -- an award granted before a translation
        existed gets an empty EN snapshot, not a backfill."""
        definition = _definition(name="Nagroda")

        created = bonus_awards.grant([partner_user["id"]], definition, admin_user)

        assert created[0]["name_en"] == ""
        assert bonus_awards.list_awards(partner_user["id"])[0]["name_en"] == ""

    def test_the_partner_card_shows_the_english_snapshot_for_an_unpaid_award(
        self, admin_user, partner_user
    ):
        definition = _definition(
            name="Nagroda kwartalna", name_en="Quarterly award", value=250
        )
        bonus_awards.grant([partner_user["id"]], definition, admin_user)

        cash = partner_bonuses.get_partner_summary(partner_user["id"])["bonus_cash"]

        assert cash[0]["name_en"] == "Quarterly award"

    def test_the_partner_card_falls_back_to_polish_for_an_old_award_with_no_english(
        self, admin_user, partner_user
    ):
        definition = _definition(name="Nagroda")
        bonus_awards.grant([partner_user["id"]], definition, admin_user)

        cash = partner_bonuses.get_partner_summary(partner_user["id"])["bonus_cash"]

        assert cash[0]["name"] == "Nagroda"
        assert cash[0]["name_en"] == ""


class TestRevoking:
    def test_an_unpaid_bonus_can_be_taken_back(self, admin_user, partner_user):
        definition = _definition()
        created = bonus_awards.grant([partner_user["id"]], definition, admin_user)

        bonus_awards.revoke(created[0]["id"], admin_user)

        assert get_partner_outstanding(partner_user["id"])["bonus_total"] == 0

    def test_a_moderator_cannot_take_a_bonus_back(
        self, admin_user, manager_user, partner_user
    ):
        definition = _definition(value=500)
        created = bonus_awards.grant([partner_user["id"]], definition, admin_user)

        with pytest.raises(PermissionError):
            bonus_awards.revoke(created[0]["id"], manager_user)

        assert get_partner_outstanding(partner_user["id"])["bonus_total"] == 500

    def test_a_paid_bonus_is_never_deleted(self, admin_user, partner_user):
        """The ledger has to keep saying what was actually paid."""
        definition = _definition(value=500)
        created = bonus_awards.grant([partner_user["id"]], definition, admin_user)
        _granted_at(created[0]["id"], "2026-09-10T09:00:00")
        record_payout({
            "partner_id": partner_user["id"],
            "period_type": "month",
            "period_key": "2026-09",
        }, admin_user)

        with pytest.raises(ValueError):
            bonus_awards.revoke(created[0]["id"], admin_user)
