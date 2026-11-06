"""Settings › Integrations › Affiliate: who configures it, and the secret shown once."""
import pytest

from api.db import get_conn
from api.tests.integration.affiliate_helpers import ORDER, login, post_webhook


@pytest.fixture
def admin_token(admin_user, call_api):
    return login(call_api, admin_user)


def test_everything_is_off_by_default(admin_token, call_api):
    status, body = call_api("GET", "/api/affiliate/settings", token=admin_token)
    assert status == 200
    assert body["enabled"] is False
    assert body["auto_approve"] is False
    assert (body["cookie_days"], body["auto_approve_days"]) == (30, 14)
    assert body["shop_url"] == ""
    assert body["secret"] is None
    assert body["currency"] == "PLN"
    assert body["webhook_url"] == "http://localhost:8000/api/affiliate/webhook"
    assert "PartnerTypeRef" in body["snippet"]
    assert "window.PartnerTypeRefDays = 30;" in body["snippet"]


def test_the_administrator_changes_the_settings(admin_token, call_api):
    status, body = call_api("PATCH", "/api/affiliate/settings", token=admin_token, body={
        "enabled": True, "shop_url": "https://shop.example.com/", "cookie_days": 45,
        "auto_approve": True, "auto_approve_days": 21})
    assert status == 200
    assert (body["enabled"], body["shop_url"], body["cookie_days"],
            body["auto_approve"], body["auto_approve_days"]) == (
        True, "https://shop.example.com/", 45, True, 21)
    assert "window.PartnerTypeRefDays = 45;" in body["snippet"]


@pytest.mark.parametrize("who", ["manager_user", "partner_user"])
def test_nobody_else_configures_it(who, request, admin_user, call_api):
    token = login(call_api, request.getfixturevalue(who))
    for method, path, body in (("GET", "/api/affiliate/settings", None),
                               ("PATCH", "/api/affiliate/settings", {"enabled": True}),
                               ("POST", "/api/affiliate/secret", {}),
                               ("POST", "/api/affiliate/test-event", {})):
        status, _ = call_api(method, path, body=body, token=token)
        assert status == 403, path


@pytest.mark.parametrize("body", [
    {},
    {"enabled": "yes"},
    {"cookie_days": 0},
    {"cookie_days": 366},
    {"cookie_days": True},
    {"auto_approve_days": "14"},
    {"currency": "EUR"},
    {"shop_url": "http://shop.example.com/"},
    {"shop_url": "javascript:alert(1)"},
    {"shop_url": "https://user:pass@shop.example.com/"},
    {"shop_url": "https://shop.example.com/#top"},
    {"shop_url": "https:///path"},
    {"shop_url": "https://shop.example.com/a b"},
    {"shop_url": "https://shop.example.com/" + "a" * 500},
])
def test_invalid_settings_are_refused(admin_token, call_api, body):
    status, response = call_api("PATCH", "/api/affiliate/settings", token=admin_token, body=body)
    assert status == 400
    assert response["error"]


def test_a_local_shop_may_use_plain_http(admin_token, call_api):
    status, body = call_api("PATCH", "/api/affiliate/settings", token=admin_token,
                            body={"shop_url": "http://localhost:3000/"})
    assert status == 200 and body["shop_url"] == "http://localhost:3000/"


def test_the_secret_is_shown_once_and_rotating_it_stops_the_old_one(admin_token, call_api):
    call_api("PATCH", "/api/affiliate/settings", token=admin_token, body={"enabled": True})
    status, created = call_api("POST", "/api/affiliate/secret", body={}, token=admin_token)
    assert status == 200
    secret = created["secret"]
    assert secret.startswith("ptw_") and len(secret) > 40
    _, view = call_api("GET", "/api/affiliate/settings", token=admin_token)
    assert view["secret"]["fingerprint"] == created["fingerprint"]
    assert secret not in str(view)
    assert post_webhook(secret, ORDER).status_code == 200
    _, rotated = call_api("POST", "/api/affiliate/secret", body={}, token=admin_token)
    assert rotated["secret"] != secret
    assert post_webhook(secret, {**ORDER, "order_id": "B-2"}).status_code == 401
    assert post_webhook(rotated["secret"], {**ORDER, "order_id": "B-2"}).status_code == 200


def test_the_test_event_runs_the_real_webhook(admin_token, call_api):
    status, body = call_api("POST", "/api/affiliate/test-event", body={}, token=admin_token)
    assert status == 409 and body["code"] == "no_secret"
    _, created = call_api("POST", "/api/affiliate/secret", body={}, token=admin_token)
    # The guide tests the connection before switching the programme on.
    _, body = call_api("POST", "/api/affiliate/test-event", body={}, token=admin_token)
    assert body == {"ok": True, "status": 200, "response": {"status": "ok"}}
    # The shop's own requests still meet a closed door until then.
    assert post_webhook(created["secret"], {"event": "ping"}).status_code == 404
    call_api("PATCH", "/api/affiliate/settings", token=admin_token, body={"enabled": True})
    _, body = call_api("POST", "/api/affiliate/test-event", body={}, token=admin_token)
    assert body == {"ok": True, "status": 200, "response": {"status": "ok"}}
    with get_conn() as conn:
        assert conn.execute("SELECT count(*) AS n FROM affiliate_conversions").fetchone()["n"] == 0


def test_settings_changes_are_audited(admin_token, call_api, mocker):
    log = mocker.patch("api.audit.log")
    call_api("PATCH", "/api/affiliate/settings", token=admin_token,
             body={"enabled": True, "cookie_days": 10})
    actions = [call.args[1] for call in log.call_args_list]
    assert actions.count("affiliate.settings") == 2
