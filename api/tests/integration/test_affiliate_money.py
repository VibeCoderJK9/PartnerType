"""Approving shop orders into commission, and every way back out.

The commission an approved order earns must be exactly what a normal won lead
of the same partner and value earns, because it is computed by the same code;
payouts must then see it without knowing where it came from.
"""
import contextvars
import threading
from datetime import datetime, timedelta, timezone

import pytest

from api import affiliate, payouts
from api.db import get_conn, run_maintenance
from api.leads import create_lead, update_lead
from api.tests.integration.affiliate_helpers import (
    ORDER, conversions, enable, link_for, login, post_webhook, set_affiliate_settings,
)


@pytest.fixture
def secret(admin_user):
    return enable(admin_user)


@pytest.fixture
def order(secret, partner_user):
    """Report one paid order with the partner's code; return its id."""
    link = link_for(partner_user)

    def _report(order_id="A-1001", amount="1000.00", occurred_at=None):
        payload = {**ORDER, "order_id": order_id, "amount": amount, "ref": link["code"]}
        if occurred_at:
            payload["occurred_at"] = occurred_at
        response = post_webhook(secret, payload)
        assert response.status_code == 200, response.get_json()
        return response.get_json()["conversion_id"]
    return _report


def _refund(secret, order_id="A-1001", amount=None):
    payload = {"event": "order.refunded", "order_id": order_id}
    if amount:
        payload["amount"] = amount
    response = post_webhook(secret, payload)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _conversion(conversion_id):
    return next(row for row in conversions() if row["id"] == conversion_id)


def _lead(lead_id):
    with get_conn() as conn:
        return dict(conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone())


def _normal_won_commission(partner, admin, value):
    lead = create_lead(partner, {"client_name": "Anna Example", "client_email": "anna@example.com",
                                 "company": "Example Ltd", "value": value})
    update_lead(lead["id"], admin, {"registration_status": "accepted"})
    return update_lead(lead["id"], admin, {"status": "won"})["commission"]


def test_an_approved_order_becomes_a_won_lead_of_its_partner(order, admin_user, partner_user):
    conversion_id = order()
    [result] = affiliate.approve(admin_user, [conversion_id])
    assert result["result"] == "approved"
    lead = _lead(result["lead_id"])
    assert lead["partner_id"] == partner_user["id"]
    assert lead["source"] == "affiliate"
    assert lead["external_order_id"] == "A-1001"
    assert lead["registration_status"] == "accepted"
    assert lead["value"] == 1000
    assert lead["won_at"] and lead["win_confirmed_at"]
    assert lead["win_confirmed_by"] == admin_user["id"]
    assert lead["settlement_status"] == "unpaid"
    # No buyer data reaches the lead.
    assert (lead["client_email"], lead["company"], lead["nip"], lead["website"]) == ("", "", "", "")
    with get_conn() as conn:
        stage = conn.execute("SELECT kind FROM lead_stages WHERE key = ?",
                             (lead["status"],)).fetchone()
    assert stage["kind"] == "win"
    row = _conversion(conversion_id)
    assert (row["status"], row["lead_id"], row["decided_by"]) == (
        "approved", result["lead_id"], admin_user["id"])


def test_commission_equals_a_normal_won_lead_of_the_same_partner_and_value(
        order, admin_user, partner_user, attach_partner_bonus):
    attach_partner_bonus(partner_user, "percent", 1.5)
    expected = _normal_won_commission(partner_user, admin_user, 1000)
    [result] = affiliate.approve(admin_user, [order()])
    assert expected and expected > 0
    assert _lead(result["lead_id"])["commission"] == expected
    assert result["commission"] == expected


def test_commission_is_stamped_even_with_automatic_commission_off(order, admin_user, partner_user):
    expected = _normal_won_commission(partner_user, admin_user, 1000)
    with get_conn() as conn:
        conn.execute("UPDATE settings SET value = '0' WHERE key = 'lead_auto_commission'")
    [result] = affiliate.approve(admin_user, [order()])
    assert _lead(result["lead_id"])["commission"] == expected


def test_payouts_show_the_approved_commission(order, admin_user, partner_user):
    [result] = affiliate.approve(admin_user, [order()])
    outstanding = payouts.get_partner_outstanding(partner_user["id"])
    assert outstanding["lead_count"] == 1
    assert outstanding["commission_total"] == result["commission"]
    summary = payouts.get_outstanding_summary()
    assert any(row["partner_id"] == partner_user["id"] for row in summary)


def test_a_pending_order_is_not_payable(order, partner_user):
    order()
    assert payouts.get_partner_outstanding(partner_user["id"])["lead_count"] == 0


def test_approving_twice_creates_one_lead(order, admin_user):
    conversion_id = order()
    affiliate.approve(admin_user, [conversion_id])
    [again] = affiliate.approve(admin_user, [conversion_id])
    assert again == {"id": conversion_id, "result": "skipped", "status": "approved"}
    with get_conn() as conn:
        assert conn.execute("SELECT count(*) AS n FROM leads WHERE source = 'affiliate'"
                            ).fetchone()["n"] == 1


def test_approval_is_all_or_nothing(order, admin_user):
    first, second = order("A-1"), order("A-2")
    with get_conn() as conn:
        conn.execute("UPDATE lead_stages SET active = 0 WHERE kind = 'win'")
    with pytest.raises(Exception) as caught:
        affiliate.approve(admin_user, [first, second])
    assert getattr(caught.value, "code", None) == "no_win_stage"
    assert {row["status"] for row in conversions()} == {"pending"}


def test_only_the_administrator_decides(order, admin_user, manager_user, partner_user, call_api):
    conversion_id = order()
    for user in (manager_user, partner_user):
        status, _ = call_api("POST", "/api/affiliate/conversions/approve",
                             body={"ids": [conversion_id]}, token=login(call_api, user))
        assert status == 403
    status, body = call_api("POST", "/api/affiliate/conversions/approve",
                            body={"ids": [conversion_id]}, token=login(call_api, admin_user))
    assert status == 200
    assert body["results"][0]["result"] == "approved"
    assert "partner_id" not in body["results"][0]


def test_staff_list_the_orders_but_the_observer_does_not(order, admin_user, manager_user,
                                                        partner_user, call_api):
    order()
    status, body = call_api("GET", "/api/affiliate/conversions?status=pending",
                            token=login(call_api, manager_user))
    assert status == 200
    [item] = body["items"]
    assert item["partner_name"] == partner_user["name"]
    assert item["amount"] == "1000.00"
    assert item["commission"] is not None
    status, _ = call_api("GET", "/api/affiliate/conversions", token=login(call_api, partner_user))
    assert status == 403


def test_a_bad_partner_filter_names_the_partner(admin_user, call_api):
    status, body = call_api("GET", "/api/affiliate/conversions?partner_id=abc",
                            token=login(call_api, admin_user))
    assert status == 400
    assert body["error"] == "Nieprawidłowy partner."


@pytest.mark.parametrize("ids", [[], "1", [0], [True], list(range(1, 102))])
def test_a_bad_selection_is_refused(order, admin_user, ids, call_api):
    status, _ = call_api("POST", "/api/affiliate/conversions/approve", body={"ids": ids},
                         token=login(call_api, admin_user))
    assert status == 400


def test_reject_ends_a_pending_order(order, admin_user):
    conversion_id = order()
    [result] = affiliate.reject(admin_user, [conversion_id])
    assert result["result"] == "rejected"
    assert _conversion(conversion_id)["status"] == "rejected"
    [again] = affiliate.approve(admin_user, [conversion_id])
    assert again["result"] == "skipped"


def _interleave(monkeypatch, other):
    """Run `other` in a second thread right after the decision under test has
    read and locked its rows, as a second click or the nightly job would, and
    give it a moment to finish. Returns the thread, to be joined."""
    original = affiliate._locked
    started = []

    def locked_then_race(conn, ids):
        rows = original(conn, ids)
        if not started:
            thread = threading.Thread(target=contextvars.copy_context().run, args=(other,))
            started.append(thread)
            thread.start()
            thread.join(timeout=1.5)
        return rows
    monkeypatch.setattr(affiliate, "_locked", locked_then_race)
    return started


def _affiliate_leads():
    with get_conn() as conn:
        return conn.execute(
            "SELECT count(*) AS n FROM leads WHERE source = 'affiliate'").fetchone()["n"]


def test_a_reject_racing_an_approval_never_leaves_a_payable_lead(order, admin_user, monkeypatch):
    conversion_id = order()
    outcome = {}
    started = _interleave(
        monkeypatch, lambda: outcome.update(approve=affiliate.approve(admin_user, [conversion_id])))
    [result] = affiliate.reject(admin_user, [conversion_id])
    started[0].join(timeout=15)
    row = _conversion(conversion_id)
    if row["status"] == "rejected":
        assert result["result"] == "rejected"
        assert row["lead_id"] is None and _affiliate_leads() == 0
        assert outcome["approve"][0]["result"] == "skipped"
    else:
        assert row["status"] == "approved" and result["result"] == "skipped"


def test_an_assignment_racing_a_reject_does_not_revive_the_order(secret, admin_user,
                                                                 partner_user, monkeypatch):
    conversion_id = post_webhook(secret, ORDER).get_json()["conversion_id"]
    started = _interleave(monkeypatch, lambda: affiliate.reject(admin_user, [conversion_id]))
    try:
        affiliate.assign(admin_user, conversion_id, partner_user["id"])
    except affiliate.AffiliateError as exc:
        assert exc.status == 409
    started[0].join(timeout=15)
    row = _conversion(conversion_id)
    assert row["status"] == "rejected"
    assert affiliate.approve(admin_user, [conversion_id])[0]["result"] == "skipped"


def test_an_unattributed_order_can_be_assigned_by_hand(secret, admin_user, partner_user,
                                                       call_api):
    conversion_id = post_webhook(secret, ORDER).get_json()["conversion_id"]
    token = login(call_api, admin_user)
    status, body = call_api("POST", f"/api/affiliate/conversions/{conversion_id}/assign",
                            body={"partner_id": partner_user["id"]}, token=token)
    assert status == 200 and body["status"] == "pending"
    status, _ = call_api("POST", f"/api/affiliate/conversions/{conversion_id}/assign",
                         body={"partner_id": partner_user["id"]}, token=token)
    assert status == 409
    [result] = affiliate.approve(admin_user, [conversion_id])
    assert _lead(result["lead_id"])["partner_id"] == partner_user["id"]


def test_an_order_cannot_be_assigned_to_staff_or_an_inactive_partner(
        secret, admin_user, partner_user, call_api):
    conversion_id = post_webhook(secret, ORDER).get_json()["conversion_id"]
    with get_conn() as conn:
        conn.execute("UPDATE users SET active = 0 WHERE id = ?", (partner_user["id"],))
    token = login(call_api, admin_user)
    for partner_id in (partner_user["id"], admin_user["id"], 99999):
        status, _ = call_api("POST", f"/api/affiliate/conversions/{conversion_id}/assign",
                             body={"partner_id": partner_id}, token=token)
        assert status == 400
    assert _conversion(conversion_id)["status"] == "unattributed"


def test_a_refund_before_approval_ends_the_order_and_creates_nothing(secret, order, admin_user):
    conversion_id = order()
    assert _refund(secret)["status"] == "refunded"
    assert affiliate.approve(admin_user, [conversion_id])[0]["result"] == "skipped"
    with get_conn() as conn:
        assert conn.execute("SELECT count(*) AS n FROM leads WHERE source = 'affiliate'"
                            ).fetchone()["n"] == 0
    assert _refund(secret)["status"] == "refunded"


def test_a_partial_refund_is_recorded_and_the_approval_pays_on_the_rest(secret, order,
                                                                        admin_user):
    conversion_id = order()
    assert _refund(secret, amount="250.00")["status"] == "pending"
    assert _conversion(conversion_id)["refunded_amount"] == 250
    [result] = affiliate.approve(admin_user, [conversion_id])
    assert _lead(result["lead_id"])["value"] == 750


def test_a_refund_after_approval_is_flagged_and_changes_no_amount(secret, order, admin_user,
                                                                  mocker):
    notify = mocker.patch("api.notifications.notify_all_admins")
    [result] = affiliate.approve(admin_user, [order()])
    before = _lead(result["lead_id"])
    assert _refund(secret)["status"] == "refund_after_approval"
    after = _lead(result["lead_id"])
    assert (after["commission"], after["value"], after["status"]) == (
        before["commission"], before["value"], before["status"])
    notify.assert_called_once()


def test_reversing_an_unpaid_commission_takes_the_order_out_of_payouts(
        secret, order, admin_user, partner_user, call_api):
    conversion_id = order()
    [result] = affiliate.approve(admin_user, [conversion_id])
    _refund(secret)
    status, body = call_api("POST", f"/api/affiliate/conversions/{conversion_id}/reverse",
                            body={}, token=login(call_api, admin_user))
    assert status == 200 and body["status"] == "reversed"
    lead = _lead(result["lead_id"])
    assert (lead["commission"], lead["won_at"], lead["win_confirmed_at"]) == (None, None, None)
    with get_conn() as conn:
        kind = conn.execute("SELECT kind FROM lead_stages WHERE key = ?",
                            (lead["status"],)).fetchone()["kind"]
        events = [row["event_type"] for row in conn.execute(
            "SELECT event_type FROM lead_workflow_events WHERE lead_id = ?",
            (result["lead_id"],)).fetchall()]
    assert kind == "lose"
    assert events == ["affiliate.approve", "affiliate.reverse"]
    assert payouts.get_partner_outstanding(partner_user["id"])["lead_count"] == 0


def test_a_paid_commission_is_never_touched(secret, order, admin_user, partner_user):
    conversion_id = order()
    [result] = affiliate.approve(admin_user, [conversion_id])
    period = payouts.month_key(_lead(result["lead_id"])["won_at"])
    payouts.record_payout({"partner_id": partner_user["id"], "period_type": "month",
                           "period_key": period}, admin_user)
    paid = _lead(result["lead_id"])
    assert paid["commission_paid_at"]
    assert _refund(secret)["status"] == "refund_after_approval"
    with pytest.raises(affiliate.AffiliateError) as caught:
        affiliate.reverse(admin_user, conversion_id)
    assert (caught.value.status, caught.value.code) == (409, "settlement_paid")
    assert _lead(result["lead_id"]) == paid
    assert _conversion(conversion_id)["status"] == "refund_after_approval"


def test_only_a_refunded_approval_can_be_reversed(order, admin_user):
    conversion_id = order()
    affiliate.approve(admin_user, [conversion_id])
    with pytest.raises(affiliate.AffiliateError) as caught:
        affiliate.reverse(admin_user, conversion_id)
    assert caught.value.status == 409


def test_a_refund_before_its_order_is_kept_and_the_order_starts_refunded(secret, order,
                                                                       admin_user):
    set_affiliate_settings(auto_approve="1", auto_approve_days="14")
    assert _refund(secret, "EARLY-1") == {"status": "refund_before_order", "conversion_id": None}
    conversion_id = order("EARLY-1", occurred_at=_days_ago(20))
    _backdate(conversion_id, 20)
    row = _conversion(conversion_id)
    assert (row["status"], row["refunded_amount"]) == ("refunded", 1000)
    assert row["refunded_at"] is not None
    assert affiliate.auto_approve() == 0
    assert affiliate.approve(admin_user, [conversion_id])[0]["result"] == "skipped"
    assert _affiliate_leads() == 0


def test_a_partial_refund_before_its_order_waits_for_a_person(secret, order, admin_user):
    set_affiliate_settings(auto_approve="1", auto_approve_days="14")
    _refund(secret, "EARLY-2", amount="100.00")
    _refund(secret, "EARLY-2", amount="250.00")
    conversion_id = order("EARLY-2", occurred_at=_days_ago(20))
    _backdate(conversion_id, 20)
    row = _conversion(conversion_id)
    assert (row["status"], row["refunded_amount"]) == ("pending", 250)
    assert affiliate.auto_approve() == 0
    [result] = affiliate.approve(admin_user, [conversion_id])
    assert _lead(result["lead_id"])["value"] == 750


def test_a_refund_held_for_an_order_that_never_came_is_swept(secret):
    _refund(secret, "NEVER-1")
    with get_conn() as conn:
        conn.execute("UPDATE affiliate_early_refunds SET received_at = now() - interval '366 days'")
    assert run_maintenance()["old_affiliate_early_refunds"] == 1


def test_a_refund_waits_as_long_as_its_order_may_still_be_reported(secret, partner_user):
    # A shop may re-send a backlog up to a year late; the refund that came
    # first must still be there, or the order would look payable.
    _refund(secret, "LATE-1")
    with get_conn() as conn:
        conn.execute("UPDATE affiliate_early_refunds SET received_at = now() - interval '200 days'")
    assert run_maintenance()["old_affiliate_early_refunds"] == 0
    response = post_webhook(secret, {**ORDER, "order_id": "LATE-1",
                                     "ref": link_for(partner_user)["code"],
                                     "occurred_at": _days_ago(250)})
    assert response.get_json()["status"] == "refunded"


def _days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _backdate(conversion_id, days):
    """Pretend the order also reached PartnerType `days` ago."""
    with get_conn() as conn:
        conn.execute("UPDATE affiliate_conversions SET received_at = now() - make_interval("
                     "days => ?) WHERE id = ?", (days, conversion_id))


def test_auto_approval_is_off_by_default(order):
    order(occurred_at=_days_ago(30))
    assert affiliate.auto_approve() == 0
    assert {row["status"] for row in conversions()} == {"pending"}


def test_auto_approval_waits_for_the_refund_window(secret, order):
    set_affiliate_settings(auto_approve="1", auto_approve_days="14")
    old, recent, refunded = (order("OLD", occurred_at=_days_ago(15)),
                             order("NEW", occurred_at=_days_ago(13)),
                             order("PART", occurred_at=_days_ago(20)))
    for conversion_id, days in ((old, 15), (recent, 13), (refunded, 20)):
        _backdate(conversion_id, days)
    _refund(secret, "PART", amount="10.00")
    assert run_maintenance()["affiliate_auto_approved"] == 1
    statuses = {row["id"]: row["status"] for row in conversions()}
    assert statuses == {old: "approved", recent: "pending", refunded: "pending"}
    lead = _lead(_conversion(old)["lead_id"])
    assert lead["win_confirmed_at"] and lead["win_confirmed_by"] is None


def test_a_late_reported_order_still_gets_the_whole_refund_window(order):
    set_affiliate_settings(auto_approve="1", auto_approve_days="14")
    conversion_id = order(occurred_at=_days_ago(13 + 30))
    _backdate(conversion_id, 13)
    assert affiliate.auto_approve() == 0
    _backdate(conversion_id, 15)
    assert affiliate.auto_approve() == 1
    assert _conversion(conversion_id)["status"] == "approved"


def test_auto_approval_never_takes_an_order_refunded_while_it_runs(secret, order):
    set_affiliate_settings(auto_approve="1", auto_approve_days="14")
    conversion_id = order(occurred_at=_days_ago(15))
    _backdate(conversion_id, 15)
    refund = get_conn()
    try:
        refund.begin()
        refund.execute("UPDATE affiliate_conversions SET refunded_amount = 10 WHERE id = ?",
                       (conversion_id,))
        outcome = {}
        thread = threading.Thread(target=contextvars.copy_context().run, args=(
            lambda: outcome.update(approved=affiliate.auto_approve()),))
        thread.start()
        thread.join(timeout=1.5)
        refund.commit()
    finally:
        refund.close()
    thread.join(timeout=15)
    assert outcome["approved"] == 0
    assert _conversion(conversion_id)["status"] == "pending"


@pytest.mark.parametrize("change", ["UPDATE users SET active = 0 WHERE id = ?",
                                    "UPDATE users SET role = 'manager' WHERE id = ?"])
def test_an_order_of_a_partner_who_left_waits_with_a_reason(order, admin_user, partner_user,
                                                            change, call_api):
    set_affiliate_settings(auto_approve="1", auto_approve_days="14")
    conversion_id = order(occurred_at=_days_ago(20))
    _backdate(conversion_id, 20)
    with get_conn() as conn:
        conn.execute(change, (partner_user["id"],))
    assert affiliate.auto_approve() == 0
    [result] = affiliate.approve(admin_user, [conversion_id])
    assert result == {"id": conversion_id, "result": "partner_inactive", "status": "pending"}
    assert _conversion(conversion_id)["status"] == "pending"
    assert _affiliate_leads() == 0
    _, body = call_api("GET", "/api/affiliate/conversions", token=login(call_api, admin_user))
    [item] = body["items"]
    assert item["partner_active"] is False
    assert item["partner_state"] == "inactive"


def test_an_erased_partners_order_is_never_approved(order, admin_user, partner_user, call_api):
    from api import gdpr
    conversion_id = order()
    gdpr.erase_person(partner_user["email"], admin_user, confirm=True)
    assert affiliate.approve(admin_user, [conversion_id])[0]["result"] == "partner_inactive"
    assert _affiliate_leads() == 0
    # The screen must not suggest bringing an erased account back to pay it.
    _, body = call_api("GET", "/api/affiliate/conversions", token=login(call_api, admin_user))
    [item] = body["items"]
    assert item["partner_active"] is False
    assert item["partner_state"] == "erased"
    assert "partner_email" not in item


def test_an_order_whose_partner_was_deleted_says_so(order, admin_user, partner_user, call_api):
    conversion_id = order()
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (partner_user["id"],))
    [result] = affiliate.approve(admin_user, [conversion_id])
    assert result == {"id": conversion_id, "result": "partner_inactive", "status": "pending"}
    assert _affiliate_leads() == 0
    status, body = call_api("GET", "/api/affiliate/conversions", token=login(call_api, admin_user))
    assert status == 200, body
    [item] = body["items"]
    assert item["status"] == "pending"
    assert item["partner_id"] is None
    assert item["partner_active"] is False
    assert item["partner_state"] is None


def test_the_list_says_whose_partner_is_active(order, admin_user, call_api):
    order()
    _, body = call_api("GET", "/api/affiliate/conversions", token=login(call_api, admin_user))
    assert body["items"][0]["partner_active"] is True
    assert body["items"][0]["partner_state"] == "active"


def test_the_partner_sees_his_pending_and_approved_commission(order, admin_user, partner_user,
                                                             call_api):
    first, _second = order("A-1"), order("A-2", amount="500.00")
    [approved] = affiliate.approve(admin_user, [first])
    status, body = call_api("GET", "/api/affiliate/me", token=login(call_api, partner_user))
    assert status == 200
    assert body["purchases"] == 2 and body["pending_count"] == 1
    assert body["approved_commission"] == f"{approved['commission']:.2f}"
    assert float(body["pending_commission"]) == pytest.approx(approved["commission"] / 2)


def test_a_partial_refund_after_approval_is_information_only(secret, order, admin_user):
    conversion_id = order()
    [result] = affiliate.approve(admin_user, [conversion_id])
    before = _lead(result["lead_id"])
    assert _refund(secret, amount="100.00")["status"] == "refund_after_approval"
    with pytest.raises(affiliate.AffiliateError) as caught:
        affiliate.reverse(admin_user, conversion_id)
    assert (caught.value.status, caught.value.code) == (409, "partial_refund")
    assert _lead(result["lead_id"]) == before


def test_a_refund_of_nothing_changes_nothing(secret, order, admin_user, mocker):
    notify = mocker.patch("api.notifications.notify_all_admins")
    pending, approved = order("Z-1"), order("Z-2")
    affiliate.approve(admin_user, [approved])
    assert _refund(secret, "Z-1", amount="0.00")["status"] == "pending"
    assert _refund(secret, "Z-2", amount="0.00")["status"] == "approved"
    assert _conversion(pending)["refunded_amount"] is None
    assert _conversion(approved)["refunded_amount"] is None
    notify.assert_not_called()
    late = order("Z-4")
    _refund(secret, "Z-4", amount="100.00")
    affiliate.approve(admin_user, [late])
    assert _refund(secret, "Z-4", amount="100.00")["status"] == "approved"
    notify.assert_not_called()
    _refund(secret, "Z-3", amount="0.00")
    with get_conn() as conn:
        assert conn.execute("SELECT count(*) AS n FROM affiliate_early_refunds"
                            ).fetchone()["n"] == 0


def test_a_repeated_refund_keeps_the_first_refund_time(secret, order):
    conversion_id = order()
    _refund(secret, amount="100.00")
    first = _conversion(conversion_id)["refunded_at"]
    _refund(secret, amount="200.00")
    row = _conversion(conversion_id)
    assert row["refunded_at"] == first and row["refunded_amount"] == 200


def test_auto_approval_is_audited_as_the_system(secret, order, mocker):
    log = mocker.patch("api.audit.log")
    set_affiliate_settings(auto_approve="1", auto_approve_days="14")
    _backdate(order(occurred_at=_days_ago(15)), 15)
    assert affiliate.auto_approve() == 1
    [call] = [c for c in log.call_args_list if c.args[1] == "affiliate.approve"]
    assert call.args[0] is None


def test_statistics_count_an_approved_order_like_any_won_lead(order, admin_user, partner_user):
    from api import stats
    before = stats.get_channel_metrics()["deals_won"]
    [result] = affiliate.approve(admin_user, [order()])
    assert stats.get_channel_metrics()["deals_won"] == before + 1
    mine = stats.get_partner_stats(partner_user["id"])
    assert mine["accepted_leads"] == 1
    assert mine["total_earnings"] == result["commission"]


def test_a_partners_data_export_and_erasure_cover_his_affiliate_data(order, admin_user,
                                                                    partner_user):
    from api import gdpr
    order()
    with get_conn() as conn:
        link = conn.execute("SELECT id FROM affiliate_links WHERE partner_id = ?",
                            (partner_user["id"],)).fetchone()
        conn.execute("INSERT INTO affiliate_clicks (click_id, link_id, partner_id) "
                     "VALUES ('click-export-00000000', ?, ?)", (link["id"], partner_user["id"]))
    document = gdpr.export_person(partner_user["email"], admin_user)
    assert len(document["affiliate_links"]) == 1
    assert [c["click_id"] for c in document["affiliate_clicks"]] == ["click-export-00000000"]
    assert [o["order_id"] for o in document["affiliate_orders"]] == ["A-1001"]
    gdpr.erase_person(partner_user["email"], admin_user, confirm=True)
    with get_conn() as conn:
        assert conn.execute("SELECT count(*) AS n FROM affiliate_links WHERE partner_id = ? "
                            "AND revoked_at IS NULL", (partner_user["id"],)).fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM affiliate_clicks WHERE partner_id = ?",
                            (partner_user["id"],)).fetchone()["n"] == 0
    assert len(conversions()) == 1
