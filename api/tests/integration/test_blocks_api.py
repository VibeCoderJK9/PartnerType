"""Router-level contract for Settings › Bloki."""


def _login_token(call_api, user):
    _, body = call_api("POST", "/api/auth/login", body={
        "email": user["email"], "password": user["plain_password"], "role": user["role"]})
    return body["token"]


def test_admin_configures_and_runs_a_block_partner_is_refused(call_api, admin_user, partner_user):
    admin = _login_token(call_api, admin_user)
    partner = _login_token(call_api, partner_user)

    status, body = call_api("GET", "/api/blocks", token=admin)
    assert status == 200
    assert any(b["id"] == "inactive_partner" for b in body["blocks"])
    assert call_api("GET", "/api/blocks", token=partner)[0] == 403

    status, _ = call_api("POST", "/api/blocks/inactive_partner/run", body={}, token=admin)
    assert status == 409

    status, body = call_api("PATCH", "/api/blocks/inactive_partner",
                            body={"enabled": True, "config": {"days": 3}}, token=admin)
    assert status == 400

    status, body = call_api("PATCH", "/api/blocks/inactive_partner",
                            body={"enabled": True, "config": {"days": 21}}, token=admin)
    assert status == 200 and body["block"]["config"] == {"days": 21}

    status, body = call_api("POST", "/api/blocks/inactive_partner/run", body={}, token=admin)
    assert status == 200 and set(body["result"]) == {"checked", "reminded"}

    assert call_api("PATCH", "/api/blocks/inactive_partner",
                    body={"enabled": False}, token=partner)[0] == 403
