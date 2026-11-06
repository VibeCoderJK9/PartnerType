"""
PartnerType -- partner-channel health metrics.

These are the standard channel measures used by mature CRM/PRM systems. Every one of them has a denominator
that could be chosen flatteringly, and these tests pin the honest choice:

* the win rate divides by CLOSED deals -- an open deal has not lost
* approval rate divides by DECIDED registrations -- one still awaiting the
  producer is not a rejection
* activation counts partners who did nothing, because that is the number worth
  knowing
* terminal stages come from lead_stages.kind, never a hardcoded 'won'
"""
import pytest

from api.db import get_conn
from api.stats import get_channel_metrics, get_top_partners
from api.users import create_user


def _partner(email, name="Partner Testowy", created_at=None):
    user = create_user(
        {"name": name, "email": email, "password": "Tajne-Haslo-123",
         "role": "partner", "company": "Firma sp. z o.o."},
    )
    conn = get_conn()
    try:
        conn.execute("UPDATE users SET account_status = 'active', active = 1 WHERE id = ?",
                     (user["id"],))
        if created_at:
            conn.execute("UPDATE users SET created_at = ? WHERE id = ?",
                         (created_at, user["id"]))
        conn.commit()
    finally:
        conn.close()
    return user


def _lead(partner, lead_id, *, registration="accepted", status=None,
          value=10000, commission=None, created_at="2026-08-01T10:00:00"):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO leads (id, partner_id, client_name, client_email, company, "
            "registration_status, status, value, commission, created_at) "
            "VALUES (?, ?, 'Klient', 'k@example.test', 'Klient sp. z o.o.', ?, ?, ?, ?, ?)",
            (lead_id, partner["id"], registration, status, value, commission, created_at),
        )
        conn.commit()
    finally:
        conn.close()


class TestActivation:
    def test_counts_partners_who_have_submitted_nothing(self, partner_user):
        """The whole point of the measure: a partner with an account and no
        activity is the one the producer needs to chase."""
        _partner("drugi@example.test")
        _partner("trzeci@example.test")
        _lead(partner_user, "LD-701")

        metrics = get_channel_metrics()

        assert metrics["partners_signed"] == 3
        assert metrics["partners_activated"] == 1
        assert metrics["activation_rate"] == pytest.approx(33.3, abs=0.1)

    def test_is_none_rather_than_zero_with_no_partners(self):
        """A zero would claim every partner is idle; there are no partners."""
        conn = get_conn()
        try:
            conn.execute("DELETE FROM leads")
            conn.execute("DELETE FROM users WHERE role = 'partner'")
            conn.commit()
        finally:
            conn.close()

        assert get_channel_metrics()["activation_rate"] is None


class TestApprovalRate:
    def test_an_undecided_registration_is_not_counted_as_a_rejection(self, partner_user):
        _lead(partner_user, "LD-710", registration="accepted")
        _lead(partner_user, "LD-711", registration="rejected")
        _lead(partner_user, "LD-712", registration="pending")
        _lead(partner_user, "LD-713", registration="needs_info")

        metrics = get_channel_metrics()

        # Two decided, one accepted -- not one of four.
        assert metrics["registrations_decided"] == 2
        assert metrics["approval_rate"] == 50.0


class TestWinRate:
    def test_open_deals_stay_out_of_the_denominator(self, partner_user):
        """An active pipeline must not read as failure."""
        _lead(partner_user, "LD-720", status="won", value=10000)
        _lead(partner_user, "LD-721", status="lost", value=5000)
        _lead(partner_user, "LD-722", status="contact", value=8000)
        _lead(partner_user, "LD-723", status="offer", value=9000)

        metrics = get_channel_metrics()

        assert metrics["deals_closed"] == 2
        assert metrics["deals_won"] == 1
        assert metrics["win_rate"] == 50.0
        # The two open ones are reported as pipeline instead.
        assert metrics["open_pipeline_deals"] == 2
        assert metrics["open_pipeline_value"] == 17000

    def test_a_renamed_terminal_stage_is_still_a_win(self, partner_user):
        """Read from lead_stages.kind, never a hardcoded key -- the producer may
        rename Wygrany to anything."""
        conn = get_conn()
        try:
            conn.execute("UPDATE lead_stages SET name = 'Sprzedane' WHERE kind = 'win'")
            conn.commit()
        finally:
            conn.close()
        _lead(partner_user, "LD-724", status="won", value=4000)

        assert get_channel_metrics()["deals_won"] == 1


class TestMoney:
    def test_average_deal_and_revenue_per_active_partner(self, partner_user):
        _partner("bierny@example.test")  # no leads: must not dilute the figure
        _lead(partner_user, "LD-730", status="won", value=10000, commission=1000)
        _lead(partner_user, "LD-731", status="won", value=30000, commission=2000)

        metrics = get_channel_metrics()

        assert metrics["won_value"] == 40000
        assert metrics["avg_deal_value"] == 20000
        # Divided by the ONE partner who is active, not by both accounts.
        assert metrics["revenue_per_active_partner"] == 40000

    def test_channel_cost_is_commission_against_won_revenue(self, partner_user):
        _lead(partner_user, "LD-732", status="won", value=100000, commission=7500)

        assert get_channel_metrics()["channel_cost_rate"] == 7.5

    def test_money_figures_are_none_not_zero_when_nothing_was_won(self, partner_user):
        _lead(partner_user, "LD-733", status="contact", value=9000)

        metrics = get_channel_metrics()

        # A zero here would be a claim about performance; there is simply no
        # closed business to report on.
        assert metrics["avg_deal_value"] is None
        assert metrics["channel_cost_rate"] is None
        assert metrics["win_rate"] is None


class TestTimeToFirstLead:
    def test_reports_the_median_not_the_mean(self):
        """One partner who took a year to start would drag a mean far away from
        what everybody else experienced."""
        conn = get_conn()
        try:
            conn.execute("DELETE FROM leads")
            conn.execute("DELETE FROM users WHERE role = 'partner'")
            conn.commit()
        finally:
            conn.close()

        slow = _partner("wolny@example.test", created_at="2026-01-01T10:00:00")
        quick_a = _partner("szybki1@example.test", created_at="2026-08-01T10:00:00")
        quick_b = _partner("szybki2@example.test", created_at="2026-08-01T10:00:00")
        _lead(slow, "LD-740", created_at="2026-12-27T10:00:00")     # 360 days
        _lead(quick_a, "LD-741", created_at="2026-08-03T10:00:00")  # 2 days
        _lead(quick_b, "LD-742", created_at="2026-08-05T10:00:00")  # 4 days

        metrics = get_channel_metrics()

        # The mean of 2, 4 and 360 is 122. The median is the middle partner's
        # actual experience, which is the number worth acting on.
        assert metrics["median_days_to_first_lead"] == 4

    def test_is_none_when_nobody_has_submitted_anything(self, partner_user):
        conn = get_conn()
        try:
            conn.execute("DELETE FROM leads")
            conn.commit()
        finally:
            conn.close()

        assert get_channel_metrics()["median_days_to_first_lead"] is None


class TestTopPartnersRanking:
    """
    The partner ranking shows correct values, the tier from the database
    (not hardcoded bronze/gold labels), and no empty widget cells.
    """

    def test_a_partner_with_no_leads_reports_zero_rather_than_nothing(self):
        """SUM over zero rows is NULL, not 0.

        Over the LEFT JOIN a partner who has submitted nothing produces no rows
        to aggregate, so both sums would come back NULL and the ranking would
        render blank cells.
        """
        _partner("bez.leadow@example.test")

        row = next(r for r in get_top_partners(10) if r["name"] == "Partner Testowy")

        assert row["total"] == 0
        assert row["accepted"] == 0
        assert row["earnings"] == 0

    def test_reports_the_configured_tier_not_the_legacy_level(self, partner_user):
        """users.level is a bronze/silver/gold column predating commission tiers.

        The ranking showed a word from a different system than the one the
        producer configures in Settings and every other screen displays.
        """
        conn = get_conn()
        try:
            tier = conn.execute(
                "INSERT INTO commission_tiers (name, base_rate, color, sort_order, active) "
                "VALUES ('Złoty', 12.5, '#d4a017', 9, 1) RETURNING id"
            ).fetchone()["id"]
            conn.execute("UPDATE users SET tier_id = ?, level = 'bronze' WHERE id = ?",
                         (tier, partner_user["id"]))
            conn.commit()
        finally:
            conn.close()
        _lead(partner_user, "LD-780", status="won", value=1000, commission=125)

        row = next(r for r in get_top_partners(10) if r["id"] == partner_user["id"])

        assert row["tier_name"] == "Złoty"
        assert row["tier_color"] == "#d4a017"
        assert row["tier_rate"] == 12.5
        # The legacy column is not reported at all any more.
        assert "level" not in row

    def test_a_partner_with_no_tier_is_reported_as_having_none(self, partner_user):
        conn = get_conn()
        try:
            conn.execute("UPDATE users SET tier_id = NULL WHERE id = ?", (partner_user["id"],))
            conn.commit()
        finally:
            conn.close()

        row = next(r for r in get_top_partners(10) if r["id"] == partner_user["id"])

        # None, not an invented default: the screen says "No tier".
        assert row["tier_name"] is None


class TestRankingTierTranslation:
    """The ranking names the tier the producer wrote, in the reader's language.

    Both ranking queries join commission_tiers for the tier's name, colour and
    rate. The tier has its own English column, read through Ui.producerLabel
    on every screen, so these two queries must carry it too.
    """

    def _rename(self, tier_id, english, admin_user):
        from api.commission_tiers import update_tier
        update_tier(tier_id, {"name_en": english}, admin_user)

    def _polish_name(self, tier_id):
        from api.commission_tiers import get_by_id
        return get_by_id(tier_id)["name"]

    def test_top_partners_carries_the_tier_english_name(self, partner_user, admin_user):
        # Read the seeded Polish name rather than hardcoding it: the seed is
        # data, and a test that asserts its wording fails the day someone
        # renames a default tier, for a reason that has nothing to do with
        # what this test is about.
        polish = self._polish_name(partner_user["tier_id"])
        self._rename(partner_user["tier_id"], "Golden tier", admin_user)

        row = next(r for r in get_top_partners(10) if r["id"] == partner_user["id"])

        assert row["tier_name"] == polish
        assert row["tier_name_en"] == "Golden tier"

    def test_partner_ranking_carries_the_tier_english_name(self, partner_user, admin_user):
        from api.stats import get_partner_ranking
        polish = self._polish_name(partner_user["tier_id"])
        self._rename(partner_user["tier_id"], "Golden tier", admin_user)

        row = next(r for r in get_partner_ranking(50) if r["id"] == partner_user["id"])

        assert row["tier_name"] == polish
        assert row["tier_name_en"] == "Golden tier"

    def test_an_untranslated_tier_reports_empty_not_missing(self, partner_user):
        """Empty means "not translated yet" and the reader gets the Polish
        original; a MISSING key would make the frontend fall back silently for
        the wrong reason, and hide a query that forgot the column."""
        row = next(r for r in get_top_partners(10) if r["id"] == partner_user["id"])

        assert "tier_name_en" in row
        assert not row["tier_name_en"]
