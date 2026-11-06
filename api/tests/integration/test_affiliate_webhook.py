"""The shop's signed webhook: who may report an order, and what is kept.

Requests go through the real Flask app, so the route, its place before the
cookie router and the JSON error shape are tested as a shop would meet them.
"""
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from api import affiliate
from api.db import get_conn
from api.tests.integration.affiliate_helpers import (
    ORDER, conversions, enable, link_for, post_webhook, set_affiliate_settings,
)


@pytest.fixture
def secret(admin_user):
    return enable(admin_user)


def test_a_signed_order_with_a_partner_code_is_pending(secret, partner_user):
    link = link_for(partner_user)
    response = post_webhook(secret, {**ORDER, "ref": link["code"]})
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "pending"
    [row] = conversions()
    assert body["conversion_id"] == row["id"]
    assert row["partner_id"] == partner_user["id"]
    assert row["link_id"] == link["id"]
    assert row["amount"] == 1000
    assert row["currency"] == "PLN"
    assert row["source"] == "webhook"
    assert row["lead_id"] is None


def test_only_the_order_facts_and_a_hash_of_the_body_are_kept(secret, partner_user):
    link = link_for(partner_user)
    # Relative to now: the webhook refuses orders older than a year.
    occurred = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0)
    payload = {**ORDER, "ref": link["code"],
               "occurred_at": occurred.isoformat().replace("+00:00", "Z")}
    raw = json.dumps(payload).encode()
    assert post_webhook(secret, None, raw=raw).status_code == 200
    [row] = conversions()
    assert row["body_sha256"] == hashlib.sha256(raw).hexdigest()
    assert row["occurred_at"] == occurred
    stored = " ".join(str(value) for value in row.values())
    assert raw.decode() not in stored


def test_a_buyer_field_is_refused_and_nothing_is_stored(secret):
    response = post_webhook(secret, {**ORDER, "email": "buyer@example.test"})
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_body"
    assert conversions() == []


def test_a_wrong_signature_is_refused(secret):
    response = post_webhook("ptw_not-the-secret", ORDER)
    assert response.status_code == 401
    assert response.get_json()["code"] == "bad_signature"
    assert conversions() == []


def test_a_missing_signature_is_refused(secret):
    assert post_webhook(secret, ORDER, signature=False).status_code == 401
    assert conversions() == []


@pytest.mark.parametrize("offset", [-301, 301])
def test_an_old_or_future_signature_is_refused(secret, offset):
    response = post_webhook(secret, ORDER, timestamp=int(time.time()) + offset)
    assert response.status_code == 401
    assert response.get_json()["code"] == "stale_signature"
    assert conversions() == []


def test_a_signature_for_another_body_is_refused(secret):
    signature = affiliate.sign(secret, json.dumps({**ORDER, "amount": "1.00"}).encode())
    assert post_webhook(secret, ORDER, signature=signature).status_code == 401
    assert conversions() == []


def test_the_rotated_secret_stops_working_at_once(secret, admin_user):
    affiliate.create_webhook_secret(admin_user)
    assert post_webhook(secret, ORDER).status_code == 401


def test_the_secret_is_stored_encrypted(secret):
    with get_conn() as conn:
        row = conn.execute("SELECT ciphertext, fingerprint FROM affiliate_secrets").fetchone()
    assert secret not in row["ciphertext"]
    assert row["fingerprint"] == secret[:12]


def test_switched_off_answers_404_even_with_a_valid_signature(secret):
    set_affiliate_settings(enabled="0")
    response = post_webhook(secret, ORDER)
    assert response.status_code == 404
    assert response.get_json()["code"] == "affiliate_disabled"
    assert conversions() == []


def test_off_by_default(admin_user):
    raw = json.dumps(ORDER).encode()
    response = post_webhook("ptw_any", None, raw=raw)
    assert response.status_code == 404


def test_a_body_over_64_kb_is_refused(secret):
    raw = json.dumps({**ORDER, "ref": "x" * (65 * 1024)}).encode()
    response = post_webhook(secret, None, raw=raw)
    assert response.status_code == 413
    assert conversions() == []


def test_the_same_order_twice_is_one_conversion(secret, partner_user):
    link = link_for(partner_user)
    first = post_webhook(secret, {**ORDER, "ref": link["code"]}).get_json()
    second = post_webhook(secret, {**ORDER, "ref": link["code"]})
    assert second.status_code == 200
    assert second.get_json() == first
    assert len(conversions()) == 1


def test_the_same_order_with_another_amount_is_a_conflict(secret):
    post_webhook(secret, ORDER)
    response = post_webhook(secret, {**ORDER, "amount": "999.00"})
    assert response.status_code == 409
    assert response.get_json()["code"] == "order_conflict"
    [row] = conversions()
    assert row["amount"] == 1000


def test_another_currency_is_refused_with_a_clear_message(secret):
    response = post_webhook(secret, {**ORDER, "currency": "EUR"})
    assert response.status_code == 422
    assert "PLN" in response.get_json()["error"]
    assert conversions() == []


def test_the_instance_currency_setting_decides(secret):
    with get_conn() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('currency', 'EUR') "
                     "ON CONFLICT (key) DO UPDATE SET value = 'EUR'")
    assert post_webhook(secret, {**ORDER, "currency": "eur"}).status_code == 200
    assert post_webhook(secret, {**ORDER, "order_id": "B-1", "currency": "PLN"}).status_code == 422


@pytest.mark.parametrize("ref", [None, "unknowncod", "no-such-click-id-at-all"])
def test_an_unknown_or_missing_code_is_stored_unattributed(secret, ref):
    payload = dict(ORDER)
    if ref:
        payload["ref"] = ref
    response = post_webhook(secret, payload)
    assert response.status_code == 200
    assert response.get_json()["status"] == "unattributed"
    [row] = conversions()
    assert row["partner_id"] is None


def test_a_click_id_attributes_the_order_to_its_partner(secret, partner_user):
    link = link_for(partner_user)
    with get_conn() as conn:
        conn.execute("INSERT INTO affiliate_clicks (click_id, link_id, partner_id) "
                     "VALUES ('click-0123456789abcdef', ?, ?)", (link["id"], partner_user["id"]))
    response = post_webhook(secret, {**ORDER, "ref": "click-0123456789abcdef"})
    assert response.get_json()["status"] == "pending"
    [row] = conversions()
    assert (row["partner_id"], row["click_id"]) == (partner_user["id"], "click-0123456789abcdef")


def test_an_inactive_partner_or_a_revoked_link_attributes_nothing(secret, partner_user,
                                                                   partner_user_bronze):
    inactive, revoked = link_for(partner_user), link_for(partner_user_bronze)
    with get_conn() as conn:
        conn.execute("UPDATE users SET active = 0 WHERE id = ?", (partner_user["id"],))
        conn.execute("UPDATE affiliate_links SET revoked_at = now() WHERE id = ?", (revoked["id"],))
    for number, link in enumerate((inactive, revoked)):
        response = post_webhook(secret, {**ORDER, "order_id": f"X-{number}", "ref": link["code"]})
        assert response.get_json()["status"] == "unattributed"


@pytest.mark.parametrize("payload", [
    {**ORDER, "amount": 1000},
    {**ORDER, "amount": "-1.00"},
    {**ORDER, "amount": "1.234"},
    {key: value for key, value in ORDER.items() if key != "amount"},
    {**ORDER, "order_id": ""},
    {**ORDER, "order_id": "x" * 129},
    {**ORDER, "order_id": "order-\u00fc"},
    {**ORDER, "event": "order.created"},
    {**ORDER, "occurred_at": "yesterday"},
    {**ORDER, "occurred_at": "2099-01-01T00:00:00Z"},
    {**ORDER, "ref": "<script>"},
])
def test_an_invalid_body_is_refused(secret, payload):
    response = post_webhook(secret, payload)
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert conversions() == []


@pytest.mark.parametrize("raw", [b"not json", b"[1, 2]", b"\xff\xfe"])
def test_a_body_that_is_not_a_json_object_is_refused(secret, raw):
    assert post_webhook(secret, None, raw=raw).status_code == 400


def test_a_ping_checks_the_connection_and_stores_nothing(secret):
    response = post_webhook(secret, {"event": "ping"})
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    assert conversions() == []


def test_too_many_calls_are_limited(secret, monkeypatch):
    monkeypatch.setattr(affiliate, "WEBHOOK_PER_MINUTE", 2)
    for number in range(2):
        assert post_webhook(secret, {**ORDER, "order_id": f"R-{number}"}).status_code == 200
    response = post_webhook(secret, {**ORDER, "order_id": "R-3"})
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1


def test_unsigned_traffic_cannot_use_up_the_shops_limit(secret, monkeypatch):
    monkeypatch.setattr(affiliate, "WEBHOOK_PER_MINUTE", 2)
    for _ in range(5):
        assert post_webhook(None, ORDER, signature=False,
                            address="203.0.113.9").status_code in (401, 429)
    for number in range(2):
        response = post_webhook(secret, {**ORDER, "order_id": f"S-{number}"},
                                address="198.51.100.4")
        assert response.status_code == 200, response.get_json()


def test_a_shop_sharing_its_address_with_unsigned_traffic_is_not_locked_out(secret, monkeypatch):
    # Shopify, Zapier and Make deliver many customers' webhooks from the same
    # addresses, so junk from that address must not cost the real shop anything.
    monkeypatch.setattr(affiliate, "WEBHOOK_PER_MINUTE", 2)
    shared = "203.0.113.20"
    refused = [post_webhook(None, ORDER, signature=False, address=shared).status_code
               for _ in range(4)]
    assert refused == [401, 401, 429, 429]
    for number in range(2):
        response = post_webhook(secret, {**ORDER, "order_id": f"Z-{number}"}, address=shared)
        assert response.status_code == 200, response.get_json()


def test_the_stripe_receiver_has_its_own_limit(secret, admin_user, monkeypatch):
    from api.tests.integration.affiliate_helpers import client
    stripe_secret = "whsec_" + "test" * 7
    affiliate.set_stripe_secret(admin_user, stripe_secret)
    monkeypatch.setattr(affiliate, "WEBHOOK_PER_MINUTE", 2)
    for number in range(2):
        assert post_webhook(secret, {**ORDER, "order_id": f"G-{number}"},
                            address=f"198.51.100.{number + 1}").status_code == 200
    assert post_webhook(secret, {**ORDER, "order_id": "G-9"},
                        address="198.51.100.9").status_code == 429
    body = json.dumps({"id": "evt_1", "type": "customer.created",
                       "data": {"object": {}}}).encode()
    response = client().post("/api/affiliate/stripe", data=body, headers={
        "Stripe-Signature": affiliate.sign(stripe_secret, body)})
    assert response.status_code == 200


def test_the_webhook_needs_no_browser_origin_or_session(secret):
    response = post_webhook(secret, ORDER, headers={"Origin": "https://evil.example.com"})
    assert response.status_code == 200


def test_the_webhook_takes_post_only(secret):
    from api.tests.integration.affiliate_helpers import client
    assert client().get("/api/affiliate/webhook").status_code in (404, 405)
