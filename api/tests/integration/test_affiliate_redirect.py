"""GET /r/<code>: count the click and send the visitor to the shop, and only there."""
from urllib.parse import parse_qs, urlsplit

import pytest

from api import affiliate
from api.db import get_conn, run_maintenance
from api.tests.integration.affiliate_helpers import (
    ORDER, client, enable, link_for, post_webhook, set_affiliate_settings,
)

SHOP = "https://shop.example.com/"


@pytest.fixture
def link(admin_user, partner_user):
    set_affiliate_settings(shop_url=SHOP)
    return link_for(partner_user)


def _clicks():
    with get_conn() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM affiliate_clicks").fetchall()]


def test_a_click_is_counted_and_sent_to_the_shop(link, partner_user):
    response = client().get(f"/r/{link['code']}")
    assert response.status_code == 302
    target = urlsplit(response.headers["Location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == SHOP
    query = parse_qs(target.query)
    [click] = _clicks()
    assert query == {"pt_ref": [link["code"]], "pt_click": [click["click_id"]]}
    assert click["partner_id"] == partner_user["id"]
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_the_shop_address_keeps_its_own_query(admin_user, partner_user):
    set_affiliate_settings(shop_url="https://shop.example.com/promo?utm_source=partners&pt_ref=old")
    code = link_for(partner_user)["code"]
    target = urlsplit(client().get(f"/r/{code}").headers["Location"])
    assert target.path == "/promo"
    query = parse_qs(target.query)
    assert query["utm_source"] == ["partners"]
    assert query["pt_ref"] == [code]


@pytest.mark.parametrize("suffix", [
    "?next=https://evil.example.com",
    "?url=//evil.example.com&pt_ref=evil",
    "/../../evil",
    "%2F%2Fevil.example.com",
])
def test_nothing_from_the_request_reaches_the_address(link, suffix):
    response = client().get(f"/r/{link['code']}{suffix}")
    if response.status_code == 302:
        target = urlsplit(response.headers["Location"])
        assert target.netloc == "shop.example.com"
        assert "evil" not in response.headers["Location"]
    else:
        assert response.status_code == 404


@pytest.mark.parametrize("code", ["UPPERCASE1", "short", "abc-def-gh", "a" * 11, "zzzzzzzzzz"])
def test_an_unknown_or_malformed_code_is_404(link, code):
    assert client().get(f"/r/{code}").status_code == 404
    assert _clicks() == []


def test_an_inactive_partner_link_does_not_work(link, partner_user):
    with get_conn() as conn:
        conn.execute("UPDATE users SET active = 0 WHERE id = ?", (partner_user["id"],))
    assert client().get(f"/r/{link['code']}").status_code == 404
    assert _clicks() == []


def test_a_revoked_link_does_not_work(link):
    with get_conn() as conn:
        conn.execute("UPDATE affiliate_links SET revoked_at = now() WHERE id = ?", (link["id"],))
    assert client().get(f"/r/{link['code']}").status_code == 404


def test_switched_off_or_without_a_shop_the_link_does_not_work(link):
    set_affiliate_settings(enabled="0")
    assert client().get(f"/r/{link['code']}").status_code == 404
    set_affiliate_settings(enabled="1", shop_url="")
    assert client().get(f"/r/{link['code']}").status_code == 404
    assert _clicks() == []


def test_one_visitor_clicking_too_often_still_reaches_the_shop_uncounted(link, monkeypatch):
    # Many real buyers share one address (mobile carriers, offices, public
    # Wi-Fi): the limit stops inflated clicks, never a visit to the shop.
    monkeypatch.setattr(affiliate, "CLICKS_PER_MINUTE_PER_VISITOR", 2)
    app = client()
    responses = [app.get(f"/r/{link['code']}") for _ in range(3)]
    assert [response.status_code for response in responses] == [302, 302, 302]
    target = urlsplit(responses[-1].headers["Location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == SHOP
    assert parse_qs(target.query) == {"pt_ref": [link["code"]]}
    assert len(_clicks()) == 2


def test_one_visitor_cannot_use_up_everyone_elses_clicks(link, monkeypatch):
    monkeypatch.setattr(affiliate, "CLICKS_PER_MINUTE_PER_VISITOR", 2)
    monkeypatch.setattr(affiliate, "CLICKS_PER_MINUTE", 3)
    app = client()
    flood = [app.get(f"/r/{link['code']}", environ_base={"REMOTE_ADDR": "203.0.113.9"}
                     ).status_code for _ in range(6)]
    assert flood == [302] * 6
    buyer = app.get(f"/r/{link['code']}", environ_base={"REMOTE_ADDR": "198.51.100.4"})
    assert buyer.status_code == 302
    assert "pt_click" in parse_qs(urlsplit(buyer.headers["Location"]).query)
    assert len(_clicks()) == 3


def test_a_buyer_still_reaches_the_shop_when_the_instance_is_busy(link, monkeypatch):
    monkeypatch.setattr(affiliate, "CLICKS_PER_MINUTE", 1)
    app = client()
    assert app.get(f"/r/{link['code']}", environ_base={"REMOTE_ADDR": "198.51.100.1"}
                   ).status_code == 302
    busy = app.get(f"/r/{link['code']}", environ_base={"REMOTE_ADDR": "198.51.100.2"})
    assert busy.status_code == 302
    target = urlsplit(busy.headers["Location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == SHOP
    # The code alone still attributes the order; only the click is not counted.
    assert parse_qs(target.query) == {"pt_ref": [link["code"]]}
    assert len(_clicks()) == 1


def test_one_ipv6_network_counts_as_one_visitor(link, monkeypatch):
    # One IPv6 host normally holds a whole /64 and can change its address at
    # will, so a limit per full address would limit nothing.
    monkeypatch.setattr(affiliate, "CLICKS_PER_MINUTE_PER_VISITOR", 2)
    app = client()
    for address in ("2001:db8:1:2::1", "2001:db8:1:2::2", "2001:db8:1:2:ffff:ffff:ffff:3"):
        assert app.get(f"/r/{link['code']}", environ_base={"REMOTE_ADDR": address}
                       ).status_code == 302
    assert len(_clicks()) == 2
    neighbour = app.get(f"/r/{link['code']}", environ_base={"REMOTE_ADDR": "2001:db8:1:3::1"})
    assert "pt_click" in parse_qs(urlsplit(neighbour.headers["Location"]).query)


def test_visitor_buckets_follow_the_network_not_the_written_address(link):
    bucket = affiliate._visitor_bucket
    assert bucket("2001:db8::1") == bucket("2001:0db8:0000:0000:ffff::9")
    assert bucket("2001:db8::1") != bucket("2001:db8:0:1::1")
    # An IPv4 address seen through an IPv6 socket is still that IPv4 sender.
    assert bucket("::ffff:192.0.2.1") == bucket("192.0.2.1")
    assert bucket("192.0.2.1") != bucket("192.0.2.2")
    assert bucket("192.0.2.1") != bucket("192.0.2.1", "webhook:fail")


def test_the_rate_limit_does_not_keep_the_ip_address(link):
    client().get(f"/r/{link['code']}", environ_base={"REMOTE_ADDR": "203.0.113.7"})
    with get_conn() as conn:
        buckets = [row["bucket"] for row in conn.execute(
            "SELECT bucket FROM api_rate_windows").fetchall()]
    assert buckets and not any("203.0.113.7" in bucket for bucket in buckets)


def test_a_click_id_from_the_redirect_attributes_the_order(admin_user, link, partner_user):
    secret = enable(admin_user)
    location = client().get(f"/r/{link['code']}").headers["Location"]
    click_id = parse_qs(urlsplit(location).query)["pt_click"][0]
    response = post_webhook(secret, {**ORDER, "ref": click_id})
    assert response.get_json()["status"] == "pending"


def test_old_clicks_are_swept(link):
    client().get(f"/r/{link['code']}")
    with get_conn() as conn:
        conn.execute("UPDATE affiliate_clicks SET created_at = now() - interval '401 days'")
    assert run_maintenance()["old_affiliate_clicks"] == 1
    assert _clicks() == []


def test_a_stored_shop_address_that_is_no_longer_valid_is_not_followed(link):
    with get_conn() as conn:
        conn.execute("UPDATE settings SET value = 'javascript:alert(1)' "
                     "WHERE key = 'affiliate_shop_url'")
    assert client().get(f"/r/{link['code']}").status_code == 404
    assert _clicks() == []
