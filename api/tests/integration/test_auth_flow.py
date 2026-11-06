"""
PartnerType -- Authentication flow integration tests.

Every request in this file is driven through api.router.dispatch via the
call_api / call_api_raw fixtures (api/tests/integration/conftest.py). No
test calls api.auth functions directly -- these tests prove the router
actually wires the token gate and the auth handlers together, not just that
the underlying functions work in isolation (that is what
api/tests/unit/test_auth.py already covers).
"""
from datetime import datetime, timedelta

from api.db import get_conn
from api.auth import _hash_session_token
from api.router import dispatch


# ─── LOGIN THROUGH THE ROUTER ────────────────────────────────────────────────

class TestLoginFlow:
    def test_partner_login_returns_token_and_partner_role(self, call_api, partner_user):
        status, body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": partner_user["email"],
                "password": partner_user["plain_password"],
                "role": "partner",
            },
        )

        assert status == 200
        assert "token" in body
        assert body["user"]["role"] == "partner"
        assert body["user"]["email"] == partner_user["email"]

    def test_admin_login_returns_admin_role(self, call_api, admin_user):
        status, body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": admin_user["email"],
                "password": admin_user["plain_password"],
                "role": "admin",
            },
        )

        assert status == 200
        assert body["user"]["role"] == "admin"

    def test_login_correct_credentials_wrong_role_returns_401(self, call_api, partner_user):
        status, body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": partner_user["email"],
                "password": partner_user["plain_password"],
                "role": "admin",
            },
        )

        assert status == 401
        assert "error" in body

    def test_login_wrong_password_returns_401(self, call_api, partner_user):
        status, body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": partner_user["email"],
                "password": "WrongPassword!",
                "role": "partner",
            },
        )

        assert status == 401
        assert "error" in body

    def test_login_missing_password_returns_401(self, call_api, partner_user):
        status, body = call_api(
            "POST",
            "/api/auth/login",
            body={"email": partner_user["email"], "role": "partner"},
        )

        assert status == 401
        assert "error" in body


# ─── THE TWO LOGIN DOORS ────────────────────────

class TestLoginDoors:
    """The login screen offers the Vendor and Partner doors, not one tab per role.

    Everyone on the producer's side of the program signs in through the same
    door and lands wherever their recorded role says they belong. Before this,
    a moderator needed a tab of their own ("channel manager") and an observer -- who
    never had one -- could not sign in through the UI at all.
    """

    def _login(self, call_api, user, door):
        return call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": user["email"],
                "password": user["plain_password"],
                "role": door,
            },
        )

    def test_admin_signs_in_through_the_vendor_door(self, call_api, admin_user):
        status, body = self._login(call_api, admin_user, "producent")

        assert status == 200
        assert body["user"]["role"] == "admin"

    def test_manager_signs_in_through_the_vendor_door(self, call_api, manager_user):
        status, body = self._login(call_api, manager_user, "producent")

        assert status == 200
        assert body["user"]["role"] == "manager"

    def test_observer_signs_in_through_the_vendor_door(self, call_api, admin_user):
        # Created the way the product creates one: the producer invites a
        # person into their own account and picks the role.
        _, login_body = self._login(call_api, admin_user, "producent")
        created_status, created_body = call_api(
            "POST",
            "/api/users",
            token=login_body["token"],
            body={
                "name": "Test Obserwator",
                "email": "obserwator@test.pl",
                "password": "ObserwatorPass123!",
                "company": "PartnerType",
                "role": "observer",
            },
        )
        assert created_status == 201, created_body

        status, body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": "obserwator@test.pl",
                "password": "ObserwatorPass123!",
                "role": "producent",
            },
        )

        assert status == 200
        assert body["user"]["role"] == "observer"

    def test_partner_is_refused_at_the_vendor_door(self, call_api, partner_user):
        status, body = self._login(call_api, partner_user, "producent")

        assert status == 401
        assert "error" in body

    def test_staff_is_refused_at_the_partner_door(self, call_api, manager_user):
        status, body = self._login(call_api, manager_user, "partner")

        assert status == 401
        assert "error" in body

    def test_unknown_door_is_refused_like_a_bad_password(self, call_api, admin_user):
        status, body = self._login(call_api, admin_user, "opiekun")

        assert status == 401
        assert "error" in body


# ─── IDENTITY (/api/auth/me) THROUGH THE ROUTER ──────────────────────────────

class TestIdentityFlow:
    def test_identity_with_valid_token_returns_same_user(self, call_api, partner_user):
        _, login_body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": partner_user["email"],
                "password": partner_user["plain_password"],
                "role": "partner",
            },
        )
        token = login_body["token"]

        status, body = call_api("GET", "/api/auth/me", token=token)

        assert status == 200
        assert body["user"]["id"] == login_body["user"]["id"]
        assert body["user"]["email"] == partner_user["email"]
        assert body["user"]["role"] == "partner"

    def test_identity_with_no_token_returns_401(self, call_api, partner_user):
        status, body = call_api("GET", "/api/auth/me")

        assert status == 401
        assert "error" in body

    def test_identity_with_unissued_token_returns_401(self, call_api, partner_user):
        status, body = call_api("GET", "/api/auth/me", token="0" * 32)

        assert status == 401
        assert "error" in body

    def test_identity_with_non_bearer_scheme_returns_401(
        self, call_api, make_handler, partner_user
    ):
        _, login_body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": partner_user["email"],
                "password": partner_user["plain_password"],
                "role": "partner",
            },
        )
        token = login_body["token"]

        handler = make_handler("GET", "/api/auth/me")
        # Authorization header present, but not using the "Bearer " scheme.
        handler.headers["Authorization"] = f"Token {token}"
        dispatch(handler, "GET", "/api/auth/me")

        status = handler.send_response.call_args_list[0][0][0]
        assert status == 401

    def test_identity_slides_session_expiry_forward(self, call_api, partner_user):
        _, login_body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": partner_user["email"],
                "password": partner_user["plain_password"],
                "role": "partner",
            },
        )
        token = login_body["token"]

        conn = get_conn()
        try:
            before = conn.execute(
                "SELECT expires_at FROM sessions WHERE token = ?",
                (_hash_session_token(token),),
            ).fetchone()["expires_at"]
        finally:
            conn.close()

        status, _ = call_api("GET", "/api/auth/me", token=token)
        assert status == 200

        conn = get_conn()
        try:
            after = conn.execute(
                "SELECT expires_at FROM sessions WHERE token = ?",
                (_hash_session_token(token),),
            ).fetchone()["expires_at"]
        finally:
            conn.close()

        assert after > before


# ─── LOGOUT THROUGH THE ROUTER ───────────────────────────────────────────────

class TestLogoutFlow:
    def test_logout_then_identity_with_same_token_returns_401(self, call_api, partner_user):
        _, login_body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": partner_user["email"],
                "password": partner_user["plain_password"],
                "role": "partner",
            },
        )
        token = login_body["token"]

        logout_status, logout_body = call_api("POST", "/api/auth/logout", token=token)
        assert logout_status == 200
        assert logout_body["ok"] is True

        status, body = call_api("GET", "/api/auth/me", token=token)

        assert status == 401
        assert "error" in body


# ─── EXPIRED SESSION ──────────────────────────────────────────────────────────

class TestSessionExpiry:
    def test_identity_with_expired_session_returns_401(self, call_api, partner_user):
        _, login_body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": partner_user["email"],
                "password": partner_user["plain_password"],
                "role": "partner",
            },
        )
        token = login_body["token"]

        # Push the session's expiry into the past directly, rather than
        # waiting on the TTL.
        conn = get_conn()
        try:
            past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
            conn.execute(
                "UPDATE sessions SET expires_at = ? WHERE token = ?",
                (past, _hash_session_token(token)),
            )
            conn.commit()
        finally:
            conn.close()

        status, body = call_api("GET", "/api/auth/me", token=token)

        assert status == 401
        assert "error" in body


# ─── SELF-SERVICE REGISTRATION THROUGH THE ROUTER ────────────────────────────

class TestRegistrationFlow:
    def test_register_returns_201(self, call_api, test_db_path):
        status, body = call_api(
            "POST",
            "/api/auth/register",
            body={
                "name": "New Partner",
                "email": "new.partner@test.pl",
                "password": "NewPartnerPass123!",
                "company": "New Partner Co",
                "phone": "+48 600 100 200",
            },
        )

        assert status == 201
        assert body["ok"] is True

    def test_registered_account_cannot_login_while_inactive(self, call_api, test_db_path):
        register_status, _ = call_api(
            "POST",
            "/api/auth/register",
            body={
                "name": "Pending Partner",
                "email": "pending.partner@test.pl",
                "password": "PendingPartnerPass123!",
                "company": "Pending Partner Co",
                "phone": "+48 600 100 200",
            },
        )
        assert register_status == 201

        login_status, login_body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": "pending.partner@test.pl",
                "password": "PendingPartnerPass123!",
                "role": "partner",
            },
        )

        assert login_status == 401
        assert "error" in login_body
