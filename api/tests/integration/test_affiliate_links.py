"""A partner's personal referral link."""
import pytest

from api.tests.integration.affiliate_helpers import login, set_affiliate_settings


@pytest.fixture
def enabled(admin_user):
    set_affiliate_settings(shop_url="https://shop.example.com/")


def test_a_partner_gets_one_link_and_keeps_it(enabled, partner_user, call_api):
    token = login(call_api, partner_user)
    status, first = call_api("POST", "/api/affiliate/me/link", body={}, token=token)
    assert status == 200
    code = first["link"]["code"]
    assert len(code) == 10 and code.isalnum() and code.islower()
    _, again = call_api("POST", "/api/affiliate/me/link", body={}, token=token)
    assert again["link"]["code"] == code
    status, summary = call_api("GET", "/api/affiliate/me", token=token)
    assert status == 200
    assert summary["link"]["code"] == code
    assert summary["enabled"] is True
    assert (summary["clicks"], summary["purchases"]) == (0, 0)


def test_two_partners_get_different_links(enabled, partner_user, partner_user_bronze, call_api):
    codes = set()
    for partner in (partner_user, partner_user_bronze):
        _, body = call_api("POST", "/api/affiliate/me/link", body={},
                           token=login(call_api, partner))
        codes.add(body["link"]["code"])
    assert len(codes) == 2


def test_no_link_while_the_programme_is_off(admin_user, partner_user, call_api):
    status, body = call_api("POST", "/api/affiliate/me/link", body={},
                            token=login(call_api, partner_user))
    assert status == 404
    assert body["code"] == "affiliate_disabled"


@pytest.mark.parametrize("who", ["admin_user", "manager_user"])
def test_staff_have_no_referral_link(enabled, who, request, call_api):
    user = request.getfixturevalue(who)
    status, _ = call_api("POST", "/api/affiliate/me/link", body={}, token=login(call_api, user))
    assert status == 403


def test_signed_out_gets_nothing(enabled, call_api):
    status, _ = call_api("GET", "/api/affiliate/me")
    assert status == 401


def test_a_code_already_taken_by_another_partner_is_drawn_again(
        enabled, partner_user, partner_user_bronze, call_api, monkeypatch):
    from api import affiliate
    from api.tests.integration.affiliate_helpers import link_for
    taken = link_for(partner_user)["code"]
    draws = iter([taken, "freshcode1"])
    monkeypatch.setattr(affiliate, "_new_code", lambda: next(draws))
    # The partner's own context cannot see the other partner's code, so only
    # the database's uniqueness can tell the two apart.
    status, body = call_api("POST", "/api/affiliate/me/link", body={},
                            token=login(call_api, partner_user_bronze))
    assert status == 200, body
    assert body["link"]["code"] == "freshcode1"
