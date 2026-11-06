"""
PartnerType -- Partner invitation flow integration tests.

Every request in this file is driven through api.router.dispatch via the
call_api fixture (api/tests/integration/conftest.py), proving the router
wires the invite/preview/accept endpoints, the origin/auth gates, and the
handlers together end to end -- not just that api.invitations functions
work in isolation (that is what api/tests/unit/test_invitations.py already
covers).

The fresh-install case -- SMTP unconfigured, as a new install
starts -- is the important one: an invitation must still return a usable
link when mail cannot be sent.
"""
from api.db import get_conn


VALID_INVITE_BODY = {
    "name": "Nowy Partner",
    "email": "invite-flow@test.pl",
    "company": "Zaproszalnia Sp. z o.o.",
}


def _invite(call_api, token, body=None):
    return call_api(
        "POST", "/api/users/invite", body=dict(VALID_INVITE_BODY, **(body or {})), token=token
    )


# ─── FRESH-INSTALL CASE: SMTP UNCONFIGURED ─────────────────────────────────────

class TestInviteOnAFreshInstall:
    def test_admin_invite_returns_201_with_user_link_and_unsent_reason(
        self, call_api, admin_user, monkeypatch
    ):
        """A new install has smtp_enabled='0' and smtp_host='' --
        the day-one state every new customer meets. An invitation must still
        succeed and hand back a usable link."""
        token = self._login(call_api, admin_user)
        # Non-production environments always route mail to Mailpit; only
        # production reads the producer's own (here: disabled) SMTP settings.
        monkeypatch.setenv("PARTNERTYPE_ENV", "production")
        # This case is about SMTP, not the second factor, and production
        # otherwise stops an administrator who has not enrolled yet.
        monkeypatch.setenv("PARTNERTYPE_REQUIRE_MFA", "0")
        status, body = _invite(call_api, token)

        assert status == 201, body
        assert body["user"]["email"] == VALID_INVITE_BODY["email"]
        assert body["user"]["account_status"] == "pending"
        assert body["sent"] is False
        assert body["reason"] == "smtp_disabled"
        assert body["invite_url"].startswith("http://") or body["invite_url"].startswith("https://")
        assert "token=" in body["invite_url"]

    @staticmethod
    def _login(call_api, admin_user):
        _, login_body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": admin_user["email"],
                "password": admin_user["plain_password"],
                "role": "admin",
            },
        )
        return login_body["token"]


# ─── LINK ORIGIN ────────────────────────────────────────────────────────────────

class TestInviteLinkOrigin:
    def test_link_uses_the_base_url_env_var_not_the_request_origin(
        self, call_api, admin_user, monkeypatch
    ):
        monkeypatch.setenv("PARTNERTYPE_BASE_URL", "https://app.partnertype.example")
        _, login_body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": admin_user["email"],
                "password": admin_user["plain_password"],
                "role": "admin",
            },
        )
        token = login_body["token"]

        # The request's own Origin header is http://localhost:8000 (set by
        # the call_api fixture for every unsafe method) -- a materially
        # different host from the configured base URL, proving the link is
        # not derived from the request.
        status, body = _invite(call_api, token, {"email": "origin-check@test.pl"})

        assert status == 201, body
        assert body["invite_url"].startswith("https://app.partnertype.example/")

    def test_unset_base_url_falls_back_to_documented_local_default(
        self, call_api, admin_user, monkeypatch
    ):
        monkeypatch.delenv("PARTNERTYPE_BASE_URL", raising=False)
        _, login_body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": admin_user["email"],
                "password": admin_user["plain_password"],
                "role": "admin",
            },
        )
        token = login_body["token"]

        status, body = _invite(call_api, token, {"email": "fallback-check@test.pl"})

        assert status == 201, body
        assert body["invite_url"].startswith("http://localhost:8000/")


# ─── AUTHORIZATION ──────────────────────────────────────────────────────────────

class TestInviteAuthorization:
    def test_non_admin_invite_returns_403_and_creates_no_user(self, call_api, partner_user):
        _, login_body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": partner_user["email"],
                "password": partner_user["plain_password"],
                "role": "partner",
            },
        )
        status, body = _invite(call_api, login_body["token"], {"email": "blocked@test.pl"})

        assert status == 403, body

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT id FROM users WHERE email = ?", ("blocked@test.pl",)
            ).fetchone()
        finally:
            conn.close()
        assert row is None


# ─── END-TO-END: INVITE TO FIRST LOGIN ─────────────────────────────────────────

class TestInvitationToFirstLogin:
    def _create_invitation(self, call_api, admin_user, email="e2e@test.pl", mocker=None):
        _, login_body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": admin_user["email"],
                "password": admin_user["plain_password"],
                "role": "admin",
            },
        )
        token = login_body["token"]
        status, body = _invite(call_api, token, {"email": email})
        assert status == 201, body
        invite_url = body["invite_url"]
        raw_token = invite_url.split("token=")[1]
        return raw_token, body["user"]

    def test_anonymous_preview_returns_invitee_name_and_email(self, call_api, admin_user):
        raw_token, user = self._create_invitation(call_api, admin_user, "preview-e2e@test.pl")

        status, body = call_api("GET", f"/api/invitations/{raw_token}")

        assert status == 200, body
        assert body["invitation"]["email"] == "preview-e2e@test.pl"
        assert body["invitation"]["name"] == VALID_INVITE_BODY["name"]

    def test_login_before_acceptance_fails(self, call_api, admin_user):
        raw_token, user = self._create_invitation(call_api, admin_user, "beforeaccept@test.pl")

        status, body = call_api(
            "POST",
            "/api/auth/login",
            body={"email": "beforeaccept@test.pl", "password": "WhateverPass123!", "role": "partner"},
        )
        assert status == 401

    def test_accept_then_login_succeeds(self, call_api, admin_user):
        raw_token, user = self._create_invitation(call_api, admin_user, "accept-e2e@test.pl")

        accept_status, accept_body = call_api(
            "POST",
            f"/api/invitations/{raw_token}/accept",
            body={"password": "BrandNewPass123!"},
        )
        assert accept_status == 200, accept_body

        login_status, login_body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": "accept-e2e@test.pl",
                "password": "BrandNewPass123!",
                "role": "partner",
            },
        )
        assert login_status == 200, login_body
        assert login_body["user"]["email"] == "accept-e2e@test.pl"
        assert "token" in login_body

    def test_second_accept_with_same_token_returns_404(self, call_api, admin_user):
        raw_token, user = self._create_invitation(call_api, admin_user, "double-accept@test.pl")

        first_status, _ = call_api(
            "POST",
            f"/api/invitations/{raw_token}/accept",
            body={"password": "BrandNewPass123!"},
        )
        assert first_status == 200

        second_status, second_body = call_api(
            "POST",
            f"/api/invitations/{raw_token}/accept",
            body={"password": "AnotherPass123!"},
        )
        assert second_status == 404, second_body


# ─── MAIL SENT PATH (mocked -- no socket opened) ───────────────────────────────

class TestInviteWithMailConfigured:
    def test_when_send_succeeds_response_reports_sent_true_and_no_reason(
        self, call_api, admin_user, mocker
    ):
        mocker.patch("api.email_service.send", return_value={"sent": True})
        _, login_body = call_api(
            "POST",
            "/api/auth/login",
            body={
                "email": admin_user["email"],
                "password": admin_user["plain_password"],
                "role": "admin",
            },
        )
        token = login_body["token"]

        status, body = _invite(call_api, token, {"email": "mailed@test.pl"})

        assert status == 201, body
        assert body["sent"] is True
        assert "reason" not in body
        assert body["invite_url"]
