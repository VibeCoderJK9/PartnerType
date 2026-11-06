"""
PartnerType -- "the vendor adds the first partner" integration flow.

Every request in this file is driven through api.router.dispatch via the
call_api fixture (api/tests/integration/conftest.py). This proves the exact
body the browser's "Nowy uzytkownik" modal builds
(js/users.js:413-421 -- name, company, phone, bio, role, level,
account_status, email, password) survives the full router path: bearer/cookie
auth, body parsing, the create allowlist in api/users.py, the database write,
and the response shape -- not just that create_user() works in isolation
(that is what api/tests/unit/test_users.py already covers).
"""
from api.db import get_conn


def _login_token(call_api, user):
    """Log in a fixture user through the router and return the session token."""
    _, body = call_api(
        "POST",
        "/api/auth/login",
        body={
            "email": user["email"],
            "password": user["plain_password"],
            "role": user["role"],
        },
    )
    return body["token"]


def browser_create_body(**overrides):
    """
    Return the exact key set the "Nowy uzytkownik" modal sends on create, so
    every test in this module states the browser's payload shape once. Any
    change to js/users.js's saveUser() create body must be mirrored here.
    """
    body = {
        "name": "Anna Testowa",
        "company": "Testowa Sp. z o.o.",
        "phone": "",
        "bio": "",
        "role": "partner",
        "level": "bronze",
        "account_status": "active",
        "email": "anna.testowa@test.pl",
        "password": "AnnaTestowa123!",
    }
    body.update(overrides)
    return body


class TestUserCreateFlow:
    def test_admin_create_with_exact_browser_body_returns_201(self, call_api, admin_user):
        admin_token = _login_token(call_api, admin_user)

        status, body = call_api(
            "POST",
            "/api/users",
            body=browser_create_body(),
            token=admin_token,
        )

        assert status == 201
        assert "user" in body
        assert body["user"]["name"] == "Anna Testowa"
        assert body["user"]["email"] == "anna.testowa@test.pl"

    def test_account_status_pending_stores_pending_and_inactive(self, call_api, admin_user):
        admin_token = _login_token(call_api, admin_user)

        status, body = call_api(
            "POST",
            "/api/users",
            body=browser_create_body(email="pending.partner@test.pl", account_status="pending"),
            token=admin_token,
        )

        assert status == 201
        assert body["user"]["account_status"] == "pending"

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT account_status, active FROM users WHERE email = ?",
                ("pending.partner@test.pl",),
            ).fetchone()
        finally:
            conn.close()

        assert row["account_status"] == "pending"
        assert row["active"] == 0

    def test_account_status_active_stores_active_and_active_flag_one(self, call_api, admin_user):
        admin_token = _login_token(call_api, admin_user)

        status, body = call_api(
            "POST",
            "/api/users",
            body=browser_create_body(email="active.partner@test.pl", account_status="active"),
            token=admin_token,
        )

        assert status == 201
        assert body["user"]["account_status"] == "active"

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT account_status, active FROM users WHERE email = ?",
                ("active.partner@test.pl",),
            ).fetchone()
        finally:
            conn.close()

        assert row["account_status"] == "active"
        assert row["active"] == 1

    def test_invalid_account_status_returns_400_naming_the_field(self, call_api, admin_user):
        admin_token = _login_token(call_api, admin_user)

        status, body = call_api(
            "POST",
            "/api/users",
            body=browser_create_body(email="nonsense.partner@test.pl", account_status="nonsense"),
            token=admin_token,
        )

        assert status == 400
        assert "error" in body
        assert "account_status" in body["error"]

    def test_unknown_extra_key_still_rejected(self, call_api, admin_user):
        admin_token = _login_token(call_api, admin_user)

        status, body = call_api(
            "POST",
            "/api/users",
            body=browser_create_body(email="extra.key@test.pl", nickname="Ania"),
            token=admin_token,
        )

        assert status == 400
        assert "error" in body

    def test_partner_created_active_can_then_log_in(self, call_api, admin_user):
        admin_token = _login_token(call_api, admin_user)

        create_status, _ = call_api(
            "POST",
            "/api/users",
            body=browser_create_body(
                email="login.after.create@test.pl",
                password="LoginAfterCreate123!",
                account_status="active",
            ),
            token=admin_token,
        )
        assert create_status == 201

        login_status, login_body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": "login.after.create@test.pl",
                "password": "LoginAfterCreate123!",
                "role": "partner",
            },
        )

        assert login_status == 200
        assert "token" in login_body
        assert login_body["user"]["email"] == "login.after.create@test.pl"
