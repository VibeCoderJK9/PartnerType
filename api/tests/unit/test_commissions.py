"""
PartnerType -- Commission calculation unit tests.

The commission computed inside api.leads.update_lead is the single behaviour
the whole product exists to deliver. Its rate comes only from the partner's
tier plus active percent bonuses, then the persisted duplicate-queue position
scales that rate. Cash bonuses remain separate from per-lead commission.

The arithmetic below is about WHEN the commission lands on the lead.
Accepting a deal only makes the figure visible
(`commission_projected`); winning the sale is what records it
(`commission`). Tests walk both steps through `_accept_and_win`, so they
measure the arithmetic at the point it becomes real money.
"""
import pytest

from api import audit
from api.db import get_conn
from api.leads import (
    LeadWorkflowConflict, create_lead, get_lead, preview_lead_update, update_lead,
)


def _lead_data(**overrides):
    data = {
        "client_name": "Anna Testowa",
        "client_email": "anna@example.pl",
        "company": "Testowa Sp. z o.o.",
        "value": 10000,
    }
    data.update(overrides)
    return data


def _set_setting(key: str, value: str):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def _accept(lead_id: str, admin_user):
    """The producer's registration decision: protect the deal and start the
    sale. Records no commission -- only a forecast."""
    return update_lead(lead_id, admin_user, {"registration_status": "accepted"})


def _accept_and_win(lead_id: str, admin_user, **extra):
    """Accept the deal, then close it as won -- the two-step path that
    records a commission. ``extra`` is merged into the winning update, which
    is where an explicit override has to be supplied to beat the formula."""
    _accept(lead_id, admin_user)
    return update_lead(lead_id, admin_user, {"status": "won", **extra})


def _set_queue_position(lead_id: str, position: int):
    conn = get_conn()
    try:
        conn.execute("UPDATE leads SET queue_position=? WHERE id=?", (position, lead_id))
        conn.commit()
    finally:
        conn.close()


# ─── TIER + QUEUE RATE CASES ──────────────────────────────────────────────

class TestCommissionRates:
    def test_tier_base_rate_at_queue_position_one_yields_700(
        self, admin_user, partner_user_tiered
    ):
        # A deliberately contradictory legacy value proves it is ignored.
        _set_setting("commission_gold", "99")
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))

        updated = _accept_and_win(lead["id"], admin_user)

        assert updated["commission"] == 700.0

    def test_active_percent_bonus_is_added_to_tier_rate(
        self, admin_user, partner_user_tiered, attach_partner_bonus
    ):
        attach_partner_bonus(partner_user_tiered, "percent", 2)
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))

        updated = _accept_and_win(lead["id"], admin_user)

        assert updated["commission"] == 900.0

    def test_cash_bonus_never_enters_per_lead_commission(
        self, admin_user, partner_user_tiered, attach_partner_bonus
    ):
        attach_partner_bonus(partner_user_tiered, "percent", 2)
        attach_partner_bonus(partner_user_tiered, "cash", 500)
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))

        updated = _accept_and_win(lead["id"], admin_user)

        assert updated["commission"] == 900.0

    def test_queue_position_two_is_half_of_same_partners_full_commission(
        self, admin_user, partner_user_tiered, attach_partner_bonus
    ):
        # Two leads by the same partner, the second at queue position 2. A
        # partner can no longer queue behind himself by submitting, so the
        # second position is set directly -- the arithmetic is the subject.
        attach_partner_bonus(partner_user_tiered, "percent", 2)
        first = create_lead(
            partner_user_tiered, _lead_data(value=10000, nip="1111111111")
        )
        second = create_lead(partner_user_tiered, _lead_data(value=10000))
        _set_queue_position(second["id"], 2)

        first_updated = _accept_and_win(first["id"], admin_user)
        second_updated = _accept_and_win(second["id"], admin_user)

        assert first_updated["commission"] == 900.0
        assert second_updated["commission"] == 450.0
        assert second_updated["commission"] == first_updated["commission"] / 2

    def test_last_configured_queue_position_receives_zero(
        self, admin_user, partner_user_tiered
    ):
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))
        _set_queue_position(lead["id"], 5)

        updated = _accept_and_win(lead["id"], admin_user)

        assert updated["commission"] == 0.0

    def test_queue_position_above_maximum_clamps_to_trailing_zero(
        self, admin_user, partner_user_tiered
    ):
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))
        _set_queue_position(lead["id"], 99)
        updated = _accept_and_win(lead["id"], admin_user)

        assert updated["commission"] == 0.0

    def test_commission_rounds_to_two_decimal_places(
        self, admin_user, partner_user_tiered
    ):
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE commission_tiers SET base_rate=7.333 WHERE id=?",
                (partner_user_tiered["tier_id"],),
            )
            conn.commit()
        finally:
            conn.close()
        lead = create_lead(partner_user_tiered, _lead_data(value=999))

        updated = _accept_and_win(lead["id"], admin_user)

        # 999 * 0.07333 = 73.25667 -> rounds to 73.26
        assert updated["commission"] == 73.26

    def test_acceptance_audit_records_tier_rate_and_queue_share(
        self, admin_user, partner_user_tiered
    ):
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))

        _accept_and_win(lead["id"], admin_user)

        detail = audit.log.call_args.args[4]
        assert "poziom: Gold" in detail
        assert "stawka: 7%" in detail
        assert "udział kolejki: 100%" in detail


# ─── GATING CASES ──────────────────────────────────────────────────────────

class TestCommissionGating:
    def test_auto_commission_toggle_off_leaves_commission_null(
        self, admin_user, partner_user
    ):
        _set_setting("lead_auto_commission", "0")
        lead = create_lead(partner_user, _lead_data(value=10000))

        updated = _accept_and_win(lead["id"], admin_user)

        assert updated["commission"] is None

    def test_partner_cannot_trigger_acceptance_commission(
        self, partner_user_tiered
    ):
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))

        with pytest.raises(PermissionError):
            _accept_and_win(lead["id"], partner_user_tiered)

    def test_existing_commission_is_not_overwritten_on_acceptance(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data(value=10000))
        update_lead(lead["id"], admin_user, {"commission": 250})

        updated = _accept_and_win(lead["id"], admin_user)

        assert updated["commission"] == 250.0

    # "pending" is not in the parameters: a lead cannot
    # be written back to it, so the case is covered by the refusal test in
    # test_leads.py rather than here. Both remaining decisions owe the partner
    # a reason, which is why they now carry one.
    @pytest.mark.parametrize("registration_status", ["needs_info", "rejected"])
    def test_no_registration_decision_short_of_a_win_records_a_commission(
        self, admin_user, partner_user, registration_status
    ):
        lead = create_lead(partner_user, _lead_data(value=10000))

        updated = update_lead(lead["id"], admin_user, {
            "registration_status": registration_status,
            "registration_message": "Powód decyzji dla partnera.",
        })

        assert updated["commission"] is None

    @pytest.mark.parametrize("stage_key", ["contact", "presentation", "offer", "negotiation"])
    def test_no_sales_stage_short_of_the_win_records_a_commission(
        self, admin_user, partner_user_tiered, stage_key
    ):
        """The forecast is visible from the moment the deal is accepted, but
        nothing is recorded against the lead until the sale is actually won
        -- visible at every stage, not yet payable."""
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))
        _accept(lead["id"], admin_user)

        updated = update_lead(lead["id"], admin_user, {"status": stage_key})

        assert updated["commission"] is None
        assert updated["commission_projected"] == 700.0

    def test_losing_the_sale_records_no_commission(
        self, admin_user, partner_user_tiered
    ):
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))
        _accept(lead["id"], admin_user)

        updated = update_lead(lead["id"], admin_user, {"status": "lost"})

        assert updated["commission"] is None


# ─── OVERRIDE CASES ─────────────────────────────────────────────────────────

class TestCommissionOverride:
    def test_explicit_commission_with_accepted_status_wins_over_computed_rate(
        self, admin_user, partner_user_tiered
    ):
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))

        updated = _accept_and_win(lead["id"], admin_user, commission=999)

        assert updated["commission"] == 999.0

    def test_explicit_commission_alone_updates_lead_without_status_change(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data(value=10000))

        updated = update_lead(lead["id"], admin_user, {"commission": 321})

        assert updated["commission"] == 321.0
        # Correcting the money is not a decision about the deal: the
        # registration axis and the sales stage both stay where they were.
        assert updated["registration_status"] == "pending"
        assert updated["status"] is None

    def test_negative_explicit_commission_raises_value_error(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data(value=10000))

        with pytest.raises(ValueError):
            update_lead(lead["id"], admin_user, {"commission": -50})

    def test_non_numeric_explicit_commission_raises_value_error(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data(value=10000))

        with pytest.raises(ValueError):
            update_lead(lead["id"], admin_user, {"commission": "not-a-number"})

    def test_explicit_zero_commission_is_accepted_not_treated_as_absent(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data(value=10000))

        updated = update_lead(lead["id"], admin_user, {"commission": 0})

        assert updated["commission"] == 0.0


# ─── RELATIONSHIP CASE ──────────────────────────────────────────────────────

class TestCommissionRelationship:
    def test_commission_is_readable_back_through_get_lead(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data(value=10000))
        _accept_and_win(lead["id"], admin_user)

        refetched = get_lead(lead["id"], admin_user)

        assert refetched["commission"] == 700.0


# ─── PERCENTAGE CORRECTION ──────────────────────────────

class TestPercentageCorrection:
    """A correction in percent, not only in PLN, available before the sale
    closes rather than only at payout time."""

    def test_a_rate_correction_replaces_the_tier_rate_in_the_forecast(
        self, admin_user, partner_user_tiered
    ):
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))
        assert get_lead(lead["id"], admin_user)["commission_projected"] == 700.0

        updated = update_lead(
            lead["id"], admin_user, {"commission_override_pct": 10}
        )

        # 10% of 10 000, not 7% + 10%.
        assert updated["commission_projected"] == 1000.0
        assert updated["commission_rate"] == 10

    def test_the_correction_is_available_before_the_deal_is_even_accepted(
        self, admin_user, partner_user_tiered
    ):
        """"So that I can make a correction at a stage that is not sold
        yet" (the owner) -- the whole point of moving it off the payout moment."""
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))

        updated = update_lead(
            lead["id"], admin_user, {"commission_override_pct": 12.5}
        )

        assert updated["registration_status"] == "pending"
        assert updated["commission_projected"] == 1250.0
        assert updated["commission"] is None

    def test_a_corrected_rate_is_what_gets_recorded_when_the_deal_is_won(
        self, admin_user, partner_user_tiered
    ):
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))
        update_lead(lead["id"], admin_user, {"commission_override_pct": 10})

        updated = _accept_and_win(lead["id"], admin_user)

        assert updated["commission"] == 1000.0

    def test_clearing_the_correction_returns_the_deal_to_its_tier_rate(
        self, admin_user, partner_user_tiered
    ):
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))
        update_lead(lead["id"], admin_user, {"commission_override_pct": 10})

        updated = update_lead(
            lead["id"], admin_user, {"commission_override_pct": None}
        )

        assert updated["commission_override_pct"] is None
        assert updated["commission_projected"] == 700.0

    def test_the_queue_share_still_scales_a_corrected_rate(
        self, admin_user, partner_user_tiered
    ):
        """A correction changes the rate, not the duplicate-queue rule --- a
        second submission of the same client still earns its smaller share."""
        _set_setting("lead_queue_shares", "100,50")
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))
        _set_queue_position(lead["id"], 2)

        updated = update_lead(
            lead["id"], admin_user, {"commission_override_pct": 10}
        )

        assert updated["commission_projected"] == 500.0

    @pytest.mark.parametrize("bad", [-1, 101, "dużo", []])
    def test_an_impossible_rate_is_refused(
        self, admin_user, partner_user_tiered, bad
    ):
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))

        with pytest.raises(ValueError):
            update_lead(lead["id"], admin_user, {"commission_override_pct": bad})

    def test_the_audit_entry_names_the_rate_it_replaced(
        self, admin_user, partner_user_tiered
    ):
        """A correction whose log cannot state the "before" is not much of an
        audit trail --- and this is money."""
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))
        update_lead(lead["id"], admin_user, {"commission_override_pct": 10})

        update_lead(lead["id"], admin_user, {"commission_override_pct": 8})

        action, detail = audit.log.call_args.args[1], audit.log.call_args.args[4]
        assert action == "lead.commission_rate"
        assert "10%" in detail and "8%" in detail

    def test_the_pln_correction_audit_also_names_the_amount_it_replaced(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data(value=10000))
        update_lead(lead["id"], admin_user, {"commission": 200})

        update_lead(lead["id"], admin_user, {"commission": 350})

        detail = audit.log.call_args.args[4]
        assert "200" in detail and "350" in detail


# ─── SETTLED COMMISSIONS ARE NOT REWRITTEN ──────────────────────────────────

def _stamp(lead_id: str, column: str):
    conn = get_conn()
    try:
        conn.execute(f"UPDATE leads SET {column} = '2026-09-01T10:00:00' WHERE id = ?",
                     (lead_id,))
        conn.commit()
    finally:
        conn.close()


class TestSettledCommissionCorrections:
    @pytest.mark.parametrize("change", [{"commission": 5000},
                                        {"commission_override_pct": 50}])
    def test_a_paid_commission_cannot_be_corrected(
        self, admin_user, partner_user, change
    ):
        lead = create_lead(partner_user, _lead_data(value=10000))
        won = _accept_and_win(lead["id"], admin_user, commission=1000)
        _stamp(lead["id"], "win_confirmed_at")
        _stamp(lead["id"], "commission_paid_at")

        with pytest.raises(LeadWorkflowConflict) as refused:
            update_lead(lead["id"], admin_user, change)
        with pytest.raises(LeadWorkflowConflict):
            preview_lead_update(lead["id"], admin_user, change)

        assert refused.value.code == "settlement_paid"
        assert get_lead(lead["id"], admin_user)["commission"] == won["commission"]

    def test_a_moderator_cannot_change_a_confirmed_win(
        self, admin_user, manager_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data(value=10000))
        _accept_and_win(lead["id"], admin_user, commission=1000)
        _stamp(lead["id"], "win_confirmed_at")

        with pytest.raises(PermissionError):
            update_lead(lead["id"], manager_user, {"commission": 10 ** 12})
        with pytest.raises(PermissionError):
            preview_lead_update(lead["id"], manager_user, {"commission": 10 ** 12})

        assert get_lead(lead["id"], admin_user)["commission"] == 1000

    def test_the_administrator_still_corrects_a_confirmed_unpaid_win(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data(value=10000))
        _accept_and_win(lead["id"], admin_user, commission=1000)
        _stamp(lead["id"], "win_confirmed_at")

        updated = update_lead(lead["id"], admin_user, {"commission": 1200})

        assert updated["commission"] == 1200

    def test_a_moderator_still_corrects_an_unconfirmed_win(
        self, admin_user, manager_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data(value=10000))
        _accept_and_win(lead["id"], admin_user, commission=1000)

        updated = update_lead(lead["id"], manager_user, {"commission": 900})

        assert updated["commission"] == 900


# ─── THE COMMISSION BREAKDOWN IS REAL NOW ───────────────────────────────────

class TestCommissionBreakdown:
    def test_the_breakdown_splits_the_tier_rate_from_the_percent_bonus(
        self, admin_user, partner_user_tiered
    ):
        """The producer's lead detail has always had three lines for this,
        reading fields no backend code populated --- so the whole amount
        rendered as "stawka poziomu" and the other two rows sat at zero."""
        lead = get_lead(
            create_lead(partner_user_tiered, _lead_data(value=10000))["id"],
            admin_user,
        )

        breakdown = lead["commission_breakdown"]
        assert breakdown["tier"] + breakdown["recurring"] == lead["commission_projected"]
        assert breakdown["tier"] > 0

    def test_a_corrected_rate_reports_no_invented_bonus_split(
        self, admin_user, partner_user_tiered
    ):
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))
        update_lead(lead["id"], admin_user, {"commission_override_pct": 10})

        breakdown = get_lead(lead["id"], admin_user)["commission_breakdown"]

        # Under a correction the whole figure IS the agreed rate; splitting
        # it across lines nobody agreed to would be fiction.
        assert breakdown["tier"] == 1000.0
        assert breakdown["recurring"] == 0
