"""
Which list surfaces show a freshly self-registered pending partner and
which omit it.

Each test is named after the surface it describes.
"""


def _register_pending_partner(call_api, name="Marek Zgłaszający", email="marek@test.pl"):
    status, body = call_api(
        "POST",
        "/api/auth/register",
        body={
            "name": name,
            "email": email,
            "password": "MarekPass123!",
            "company": "Zgłaszalnia Sp. z o.o.",
                "phone": "+48 600 100 200",
        },
    )
    assert status == 201, body
    return body


def _login_token(call_api, user):
    status, body = call_api(
        "POST",
        "/api/auth/login",
        body={
            "email": user["email"],
            "password": user["plain_password"],
            "role": user["role"],
        },
    )
    assert status == 200, body
    return body["token"]


def test_get_users_shows_the_pending_registration(call_api, admin_user):
    """GET /api/users carries no active filter -- the pending account must be on it."""
    _register_pending_partner(call_api)
    admin_token = _login_token(call_api, admin_user)

    status, body = call_api("GET", "/api/users", token=admin_token)

    assert status == 200
    emails = [u["email"] for u in body["users"]]
    assert "marek@test.pl" in emails
    marek = next(u for u in body["users"] if u["email"] == "marek@test.pl")
    assert marek["account_status"] == "pending"
    assert marek["active"] == 0


def test_get_users_pending_filter_shows_the_pending_registration(call_api, admin_user):
    """GET /api/users?status=pending is the surface the plan names as the correct one."""
    _register_pending_partner(call_api)
    admin_token = _login_token(call_api, admin_user)

    status, body = call_api("GET", "/api/users?status=pending", token=admin_token)

    assert status == 200
    emails = [u["email"] for u in body["users"]]
    assert "marek@test.pl" in emails


def test_get_partners_omits_the_pending_registration(call_api, admin_user):
    """
    GET /api/partners filters WHERE role='partner' AND active=1
    (api/partners.py::get_partners). A pending, inactive account is
    structurally excluded -- recorded here, not assumed.
    """
    _register_pending_partner(call_api)
    admin_token = _login_token(call_api, admin_user)

    status, body = call_api("GET", "/api/partners", token=admin_token)

    assert status == 200
    emails = [p["email"] for p in body["partners"]]
    assert "marek@test.pl" not in emails


def test_get_partner_bonuses_omits_the_pending_registration(call_api, admin_user):
    """
    GET /api/partner-bonuses -- the query behind the Partners screen
    (api/partner_bonuses.py::get_all_partners_summary) -- carries the
    identical WHERE role='partner' AND active=1 filter. This is the request
    js/admin-partners.js actually issues to populate the Partnerzy table, so
    this is the surface the producer looks at.
    """
    _register_pending_partner(call_api)
    admin_token = _login_token(call_api, admin_user)

    status, body = call_api("GET", "/api/partner-bonuses", token=admin_token)

    assert status == 200
    emails = [p["email"] for p in body["partners"]]
    assert "marek@test.pl" not in emails
