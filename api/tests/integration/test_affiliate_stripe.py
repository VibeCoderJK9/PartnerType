"""The Stripe receiver: Checkout sessions in, refunds matched by payment intent."""
import json
import time

import pytest

from api import affiliate
from api.db import get_conn
from api.tests.integration.affiliate_helpers import (
    client, conversions, link_for, login, set_affiliate_settings,
)

STRIPE_SECRET = "whsec_" + "test" * 7  # a made-up fixture, built so scanners do not flag it


@pytest.fixture
def stripe(admin_user, call_api):
    set_affiliate_settings(shop_url="https://shop.example.com/")
    status, _ = call_api("PUT", "/api/affiliate/stripe-secret", body={"secret": STRIPE_SECRET},
                         token=login(call_api, admin_user))
    assert status == 200


def _send(event, secret=STRIPE_SECRET, timestamp=None):
    body = json.dumps(event).encode()
    header = affiliate.sign(secret, body, timestamp or int(time.time()))
    return client().post("/api/affiliate/stripe", data=body, headers={
        "Content-Type": "application/json", "Stripe-Signature": header + ",v0=ignored"})


def _session(ref=None, session_id="cs_test_1", total=24600, tax=4600, currency="pln",
             paid="paid", event_type="checkout.session.completed", metadata=None):
    return {
        "id": "evt_1", "type": event_type,
        "data": {"object": {
            "id": session_id, "object": "checkout.session", "client_reference_id": ref,
            "metadata": metadata or {}, "amount_total": total,
            "total_details": {"amount_tax": tax}, "currency": currency,
            "payment_intent": f"pi_{session_id}", "payment_status": paid,
            "created": int(time.time()) - 60,
            "customer_details": {"email": "buyer@example.test", "name": "Buyer Example"},
        }},
    }


def _refund(session_id="cs_test_1", refunded=24600, amount=24600):
    return {"id": "evt_2", "type": "charge.refunded", "data": {"object": {
        "id": "ch_1", "object": "charge", "payment_intent": f"pi_{session_id}",
        "amount": amount, "amount_refunded": refunded, "currency": "pln"}}}


def test_a_paid_session_with_a_reference_is_pending(stripe, partner_user):
    link = link_for(partner_user)
    response = _send(_session(ref=link["code"]))
    assert response.status_code == 200
    assert response.get_json()["status"] == "pending"
    [row] = conversions()
    assert (row["order_id"], row["source"], row["payment_ref"]) == (
        "cs_test_1", "stripe", "pi_cs_test_1")
    assert row["amount"] == 200
    assert row["partner_id"] == partner_user["id"]


def test_the_buyer_details_in_the_event_are_not_kept(stripe):
    _send(_session())
    [row] = conversions()
    stored = " ".join(str(value) for value in row.values())
    assert "buyer@example.test" not in stored and "Buyer Example" not in stored


def test_the_reference_may_come_from_metadata(stripe, partner_user):
    link = link_for(partner_user)
    assert _send(_session(metadata={"pt_ref": link["code"]})).get_json()["status"] == "pending"


def test_an_odd_reference_leaves_the_order_unattributed(stripe):
    assert _send(_session(ref="user 42 / campaign")).get_json()["status"] == "unattributed"


def test_an_unpaid_session_waits_for_the_async_payment(stripe):
    response = _send(_session(paid="unpaid"))
    assert response.get_json() == {"status": "ignored"}
    assert conversions() == []
    later = _send(_session(event_type="checkout.session.async_payment_succeeded"))
    assert later.get_json()["status"] == "unattributed"


def test_another_event_type_is_acknowledged_and_ignored(stripe):
    response = _send({"id": "evt_3", "type": "customer.created", "data": {"object": {}}})
    assert response.status_code == 200
    assert response.get_json() == {"status": "ignored"}


def test_a_zero_decimal_currency_is_not_divided(stripe):
    with get_conn() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('currency', 'JPY') "
                     "ON CONFLICT (key) DO UPDATE SET value = 'JPY'")
    _send(_session(total=5000, tax=0, currency="jpy"))
    assert conversions()[0]["amount"] == 5000


def test_a_session_in_another_currency_is_acknowledged_and_not_stored(stripe):
    response = _send(_session(currency="eur"))
    assert response.status_code == 200
    assert response.get_json() == {"status": "ignored"}
    assert conversions() == []


def test_a_referral_sale_in_another_currency_is_acknowledged_and_logged(stripe, partner_user,
                                                                       caplog):
    response = _send(_session(ref=link_for(partner_user)["code"], currency="eur"))
    assert response.status_code == 200
    assert response.get_json() == {"status": "unsupported_currency"}
    assert conversions() == []
    assert "affiliate_stripe_unsupported_currency" in caplog.text


def test_a_wrong_or_old_stripe_signature_is_refused(stripe):
    assert _send(_session(), secret="whsec_other").status_code == 401
    assert _send(_session(), timestamp=int(time.time()) - 600).status_code == 401
    assert conversions() == []


def test_without_a_stripe_secret_nothing_is_accepted(admin_user):
    set_affiliate_settings()
    assert _send(_session()).status_code == 401


def test_switched_off_is_404(stripe):
    set_affiliate_settings(enabled="0")
    assert _send(_session()).status_code == 404


def test_a_retried_event_is_one_conversion(stripe):
    _send(_session())
    assert _send(_session()).status_code == 200
    assert len(conversions()) == 1


def test_a_full_refund_before_approval_ends_the_order(stripe):
    _send(_session())
    response = _send(_refund())
    assert response.get_json()["status"] == "refunded"


def test_a_partial_refund_is_converted_to_the_net_amount(stripe, partner_user):
    _send(_session(ref=link_for(partner_user)["code"]))
    response = _send(_refund(refunded=12300))
    assert response.get_json()["status"] == "pending"
    assert conversions()[0]["refunded_amount"] == 100


def _held():
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT source, ref FROM affiliate_early_refunds").fetchall()]


def test_a_refund_of_an_unknown_payment_is_acknowledged(stripe):
    response = _send(_refund(session_id="cs_never"))
    assert response.status_code == 200
    assert response.get_json() == {"status": "ignored"}


def test_a_refund_before_its_session_makes_the_order_start_refunded(stripe, partner_user):
    assert _send(_refund()).get_json() == {"status": "ignored"}
    response = _send(_session(ref=link_for(partner_user)["code"]))
    assert response.get_json()["status"] == "refunded"
    [row] = conversions()
    assert row["refunded_amount"] == 200
    assert _held() == []


def test_a_partial_refund_before_its_session_is_kept_in_proportion(stripe, partner_user):
    _send(_refund(refunded=12300))
    response = _send(_session(ref=link_for(partner_user)["code"]))
    assert response.get_json()["status"] == "pending"
    assert conversions()[0]["refunded_amount"] == 100


def test_an_order_uses_up_every_refund_held_for_its_payment(stripe, partner_user):
    # A session with an invoice names both the payment intent and the invoice,
    # and so does its refund; the order must use up the refund under both.
    refund = _refund(refunded=12300)
    refund["data"]["object"]["invoice"] = "in_one_off_1"
    _send(refund)
    assert len(_held()) == 2
    session = _session(ref=link_for(partner_user)["code"])
    session["data"]["object"]["invoice"] = "in_one_off_1"
    assert _send(session).get_json()["status"] == "pending"
    assert conversions()[0]["refunded_amount"] == 100
    assert _held() == []


def test_a_refund_in_another_currency_is_not_kept(stripe):
    event = _refund(session_id="cs_eur")
    event["data"]["object"]["currency"] = "eur"
    assert _send(event).get_json() == {"status": "ignored"}
    assert _held() == []


@pytest.mark.parametrize("raw", [b"not json", b"[]", b'{"type": 5}'])
def test_a_malformed_event_is_refused(stripe, raw):
    header = affiliate.sign(STRIPE_SECRET, raw)
    response = client().post("/api/affiliate/stripe", data=raw,
                             headers={"Stripe-Signature": header})
    assert response.status_code == 400


@pytest.mark.parametrize("secret", ["", "ptw_not_stripe", "whsec_", "whsec_" + "a" * 300, 42])
def test_the_stripe_secret_must_look_like_one(admin_user, call_api, secret):
    status, _ = call_api("PUT", "/api/affiliate/stripe-secret", body={"secret": secret},
                         token=login(call_api, admin_user))
    assert status == 400


def test_only_the_administrator_sets_the_stripe_secret(manager_user, call_api):
    status, _ = call_api("PUT", "/api/affiliate/stripe-secret", body={"secret": STRIPE_SECRET},
                         token=login(call_api, manager_user))
    assert status == 403


def test_the_settings_show_the_stripe_address_and_fingerprint_only(stripe, admin_user, call_api):
    _, body = call_api("GET", "/api/affiliate/settings", token=login(call_api, admin_user))
    assert body["stripe_webhook_url"] == "http://localhost:8000/api/affiliate/stripe"
    assert body["stripe_secret"]["fingerprint"] == STRIPE_SECRET[:12]
    assert STRIPE_SECRET not in json.dumps(body)


def _subscription(ref=None):
    event = _session(ref=ref, session_id="cs_sub_1")
    event["data"]["object"].update(mode="subscription", payment_intent=None, invoice="in_sub_1")
    return event


def _invoice_refund(refunded=24600):
    event = _refund(session_id="invoice_charge", refunded=refunded)
    event["data"]["object"]["invoice"] = "in_sub_1"
    return event


def test_a_subscription_refund_is_matched_through_its_invoice(stripe, partner_user):
    _send(_subscription(ref=link_for(partner_user)["code"]))
    [row] = conversions()
    assert row["payment_ref"] == "in_sub_1"
    assert _send(_invoice_refund()).get_json()["status"] == "refunded"


def test_a_subscription_refund_before_its_session_is_kept(stripe, partner_user):
    _send(_invoice_refund(refunded=12300))
    response = _send(_subscription(ref=link_for(partner_user)["code"]))
    assert response.get_json()["status"] == "pending"
    assert conversions()[0]["refunded_amount"] == 100


def test_a_subscription_order_is_never_approved_automatically(stripe, partner_user):
    set_affiliate_settings(auto_approve="1", auto_approve_days="14")
    _send(_subscription(ref=link_for(partner_user)["code"]))
    [row] = conversions()
    with get_conn() as conn:
        conn.execute("UPDATE affiliate_conversions SET occurred_at = now() - interval '20 days', "
                     "received_at = now() - interval '20 days'")
    assert affiliate.auto_approve() == 0
    assert conversions()[0]["status"] == "pending"
