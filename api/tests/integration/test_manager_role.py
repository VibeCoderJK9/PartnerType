"""
PartnerType -- The manager (channel manager) role tracer, end to end.

Proves the single thin path, driven through
api.router.dispatch exactly like api/tests/integration/test_access_control.py:
an admin creates a manager account through the real user-creation endpoint,
that account authenticates through the real login endpoint, its session
reaches the admin-shaped stats payload, and that same session is admitted to
every Settings-tab mutation while staying refused the two carve-outs
(the audit log and user administration).
"""


def _login_token(call_api, email, password, role):
    _, body = call_api(
        "POST",
        "/api/auth/login",
        body={"email": email, "password": password, "role": role},
    )
    return body["token"]


def test_admin_can_create_a_manager_account(call_api, admin_user):
    admin_token = _login_token(
        call_api, admin_user["email"], admin_user["plain_password"], "admin"
    )

    status, body = call_api(
        "POST",
        "/api/users",
        body={
            "name": "Test Opiekun",
            "email": "opiekun@test.pl",
            "password": "OpiekunPass123!",
            "company": "PartnerType",
            "role": "manager",
        },
        token=admin_token,
    )

    assert status == 201
    assert body["user"]["role"] == "manager"


def test_manager_account_logs_in_and_receives_a_session(call_api, admin_user):
    admin_token = _login_token(
        call_api, admin_user["email"], admin_user["plain_password"], "admin"
    )
    call_api(
        "POST",
        "/api/users",
        body={
            "name": "Test Opiekun",
            "email": "opiekun-login@test.pl",
            "password": "OpiekunPass123!",
            "company": "PartnerType",
            "role": "manager",
        },
        token=admin_token,
    )

    status, body = call_api(
        "POST",
        "/api/auth/login",
        body={
            "email": "opiekun-login@test.pl",
            "password": "OpiekunPass123!",
            "role": "manager",
        },
    )

    assert status == 200
    assert body["user"]["role"] == "manager"
    assert body["token"]


def _create_and_login_manager(call_api, admin_token, email="opiekun-stats@test.pl"):
    call_api(
        "POST",
        "/api/users",
        body={
            "name": "Test Opiekun",
            "email": email,
            "password": "OpiekunPass123!",
            "company": "PartnerType",
            "role": "manager",
        },
        token=admin_token,
    )
    return _login_token(call_api, email, "OpiekunPass123!", "manager")


def test_manager_session_gets_the_same_stats_shape_as_admin(call_api, admin_user):
    admin_token = _login_token(
        call_api, admin_user["email"], admin_user["plain_password"], "admin"
    )
    manager_token = _create_and_login_manager(call_api, admin_token)

    admin_status, admin_body = call_api("GET", "/api/stats", token=admin_token)
    manager_status, manager_body = call_api("GET", "/api/stats", token=manager_token)

    assert admin_status == 200
    assert manager_status == 200
    assert set(manager_body.keys()) == set(admin_body.keys())


# The moderator OWNS the Settings surface. What stays refused is the pair
# --- the audit log and user administration --- asserted
# separately below.
SETTINGS_MUTATIONS = [
    ("PATCH", "/api/settings", {"key": "company_name", "value": "Should Not Save"}),
    (
        "POST",
        "/api/products",
        {"name": "Should Not Create", "category": "Usługa"},
    ),
    ("POST", "/api/tiers", {"name": "Should Not Create", "base_rate": 5.0}),
    (
        "POST",
        "/api/bonus-defs",
        {"name": "Should Not Create", "type": "percent", "default_value": 1.0},
    ),
    (
        "POST",
        "/api/lead-stages",
        {"key": "should_not_create", "name": "Should Not Create"},
    ),
]


def test_manager_session_is_admitted_to_every_settings_mutation(call_api, admin_user):
    admin_token = _login_token(
        call_api, admin_user["email"], admin_user["plain_password"], "admin"
    )
    manager_token = _create_and_login_manager(
        call_api, admin_token, email="opiekun-settings@test.pl"
    )

    for method, path, body in SETTINGS_MUTATIONS:
        status, response_body = call_api(method, path, body=body, token=manager_token)
        assert status != 403, (
            f"{method} {path} returned 403 -- the moderator gets the "
            "whole Settings surface"
        )


def test_manager_session_is_still_refused_the_two_carve_outs(call_api, admin_user):
    """The exact boundary of the moderator role: everything EXCEPT the audit log and user
    administration. If either of these ever starts returning 200, the role
    has quietly become a second administrator --- a moderator who can invite
    users can mint themselves an admin account."""
    admin_token = _login_token(
        call_api, admin_user["email"], admin_user["plain_password"], "admin"
    )
    manager_token = _create_and_login_manager(
        call_api, admin_token, email="opiekun-carveouts@test.pl"
    )

    carve_outs = [
        ("GET", "/api/audit", None),
        ("GET", "/api/users", None),
        (
            "POST",
            "/api/users",
            {
                "name": "Should Not Create",
                "email": "should-not-create@test.pl",
                "password": "ShouldNotCreate123!",
                "company": "Nope",
            },
        ),
        (
            "POST",
            "/api/users/team-invite",
            {"name": "Should Not Invite", "email": "should-not-invite@test.pl"},
        ),
    ]

    for method, path, body in carve_outs:
        status, response_body = call_api(method, path, body=body, token=manager_token)
        assert status == 403, f"{method} {path} returned {status}, expected 403"
        assert "error" in response_body
