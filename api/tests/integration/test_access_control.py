"""
PartnerType -- Cross-cutting access control and router boundary tests.

The backend has roughly twenty independently-editable inline admin checks
with no shared helper, so this file asserts each admin-gated route individually rather than trusting
one representative case to stand in for the rest. These tests are the
safety net that catches a check being dropped in a refactor.

Every request in this file is driven through api.router.dispatch via the
call_api / call_api_raw fixtures (api/tests/integration/conftest.py).
"""
import json
from datetime import datetime, timedelta

import pytest

from api import leads
from api.db import get_conn
from api.auth import _hash_session_token
from api.router import dispatch
from api.tests.integration.conftest import lead_payload


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


# ─── AUTHENTICATION GATE (parametrised over every authenticated route) ───────

AUTHENTICATED_ROUTES = [
    ("GET", "/api/leads"),
    ("GET", "/api/leads/LD-001"),
    ("POST", "/api/leads/bulk"),
    ("GET", "/api/partners"),
    ("GET", "/api/partners/1"),
    ("GET", "/api/users"),
    ("GET", "/api/users/1"),
    ("POST", "/api/users/1/password"),
    ("GET", "/api/settings"),
    ("GET", "/api/stats"),
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/logout"),
]


class TestAuthenticationGate:
    @pytest.mark.parametrize("method,path", AUTHENTICATED_ROUTES)
    def test_no_authorization_header_returns_401(self, call_api, method, path):
        status, body = call_api(method, path)

        assert status == 401
        assert "error" in body

    @pytest.mark.parametrize("method,path", AUTHENTICATED_ROUTES)
    def test_unissued_token_returns_401(self, call_api, method, path):
        status, body = call_api(method, path, token="0" * 32)

        assert status == 401
        assert "error" in body

    @pytest.mark.parametrize("method,path", AUTHENTICATED_ROUTES)
    def test_expired_session_token_returns_401(
        self, call_api, partner_user, method, path
    ):
        token = _login_token(call_api, partner_user)

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

        status, body = call_api(method, path, token=token)

        assert status == 401
        assert "error" in body


# ─── AUTHORISATION GATE (admin-gated routes, one test each) ──────────────────

class TestAuthorisationGate:
    def test_partner_hitting_partners_collection_returns_403(
        self, call_api, partner_user
    ):
        token = _login_token(call_api, partner_user)
        status, body = call_api("GET", "/api/partners", token=token)

        assert status == 403
        assert "error" in body

    def test_partner_hitting_single_partner_returns_403(
        self, call_api, partner_user, admin_user
    ):
        token = _login_token(call_api, partner_user)
        status, body = call_api("GET", "/api/partners/1", token=token)

        assert status == 403
        assert "error" in body

    @pytest.mark.parametrize("path", [
        "/api/partner-bonuses",
        "/api/partner-bonuses?scope=roster",
    ])
    def test_partner_cannot_list_every_partners_money_and_email(
        self, call_api, partner_user, partner_user_bronze, path
    ):
        token = _login_token(call_api, partner_user)
        status, body = call_api("GET", path, token=token)

        assert status == 403
        assert "partners" not in body

    def test_partner_still_reads_his_own_bonus_card(self, call_api, partner_user):
        token = _login_token(call_api, partner_user)
        status, body = call_api(
            "GET", f"/api/partner-bonuses/{partner_user['id']}", token=token
        )

        assert status == 200
        assert body["partner"]["id"] == partner_user["id"]

    def test_partner_hitting_users_collection_returns_403(
        self, call_api, partner_user
    ):
        token = _login_token(call_api, partner_user)
        status, body = call_api("GET", "/api/users", token=token)

        assert status == 403
        assert "error" in body

    def test_partner_creating_user_returns_403(self, call_api, partner_user):
        token = _login_token(call_api, partner_user)
        status, body = call_api(
            "POST",
            "/api/users",
            body={
                "name": "Someone",
                "email": "someone@test.pl",
                "password": "SomeonePass123!",
                "company": "Someone Co",
            },
            token=token,
        )

        assert status == 403
        assert "error" in body

    def test_partner_hitting_bulk_lead_path_returns_403(
        self, call_api, partner_user
    ):
        token = _login_token(call_api, partner_user)
        status, body = call_api(
            "POST",
            "/api/leads/bulk",
            body={"action": "accept", "items": [{"id": "LD-001", "expected_revision": 0, "operation_id": "denied"}]},
            token=token,
        )

        assert status == 403
        assert "error" in body

    def test_partner_updating_another_user_returns_403(
        self, call_api, partner_user, partner_user_bronze
    ):
        token = _login_token(call_api, partner_user)
        status, body = call_api(
            "PATCH",
            f"/api/users/{partner_user_bronze['id']}",
            body={"name": "Renamed"},
            token=token,
        )

        assert status == 403
        assert "error" in body


# ─── OWNERSHIP GATES ──────────────────────────────────────────────────────────

class TestOwnershipGates:
    def test_partner_reads_own_user_record_returns_200(
        self, call_api, partner_user
    ):
        token = _login_token(call_api, partner_user)
        status, body = call_api(
            "GET", f"/api/users/{partner_user['id']}", token=token
        )

        assert status == 200
        assert body["user"]["id"] == partner_user["id"]

    def test_partner_reads_different_user_record_returns_403(
        self, call_api, partner_user, partner_user_bronze
    ):
        token = _login_token(call_api, partner_user)
        status, body = call_api(
            "GET", f"/api/users/{partner_user_bronze['id']}", token=token
        )

        assert status == 403
        assert "error" in body

    def test_partner_lead_list_scoped_to_own_rows(
        self, call_api, partner_user, partner_user_bronze
    ):
        own_token = _login_token(call_api, partner_user)
        other_token = _login_token(call_api, partner_user_bronze)

        call_api("POST", "/api/leads", body=lead_payload(), token=own_token)
        call_api("POST", "/api/leads", body=lead_payload(), token=other_token)
        call_api("POST", "/api/leads", body=lead_payload(), token=other_token)

        status, body = call_api("GET", "/api/leads", token=own_token)

        assert status == 200
        assert len(body["leads"]) == 1
        assert all(
            lead["partner_id"] == partner_user["id"] for lead in body["leads"]
        )

    def test_partner_fetch_other_partners_lead_returns_404(
        self, call_api, partner_user, partner_user_bronze
    ):
        other_token = _login_token(call_api, partner_user_bronze)
        own_token = _login_token(call_api, partner_user)

        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(), token=other_token
        )
        lead_id = create_body["lead"]["id"]

        status, body = call_api("GET", f"/api/leads/{lead_id}", token=own_token)

        assert status == 404
        assert "error" in body

    def test_partner_updating_lead_status_returns_403(
        self, call_api, partner_user
    ):
        token = _login_token(call_api, partner_user)

        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(), token=token
        )
        lead_id = create_body["lead"]["id"]

        status, body = call_api(
            "PATCH",
            f"/api/leads/{lead_id}",
            body={"registration_status": "accepted"},
            token=token,
        )

        assert status == 403
        assert "error" in body


# ─── ROUTER BOUNDARY ───────────────────────────────────────────────────────────

class TestRouterBoundary:
    def test_unmatched_path_returns_none_and_sends_nothing(self, call_api_raw):
        handler = call_api_raw("GET", "/api/does-not-exist")
        result = dispatch(handler, "GET", "/api/does-not-exist")

        assert result is None
        handler.send_response.assert_not_called()
        handler.wfile.write.assert_not_called()

    def test_known_path_unsupported_method_falls_through(self, call_api_raw):
        # /api/leads is only routed for GET and POST -- no DELETE entry
        # exists in the route table for it.
        handler = call_api_raw("DELETE", "/api/leads")
        result = dispatch(handler, "DELETE", "/api/leads")

        assert result is None
        handler.send_response.assert_not_called()

    def test_malformed_json_body_is_treated_as_empty_not_raising(
        self, call_api, make_handler, partner_user
    ):
        token = _login_token(call_api, partner_user)

        handler = make_handler("POST", "/api/leads", token=token)
        malformed = b"{not-valid-json"
        handler.headers["Content-Length"] = str(len(malformed))
        handler.rfile.read.return_value = malformed

        dispatch(handler, "POST", "/api/leads")

        status = handler.send_response.call_args_list[0][0][0]
        raw_response = handler.wfile.write.call_args_list[0][0][0]
        body = json.loads(raw_response.decode("utf-8"))

        # Malformed JSON is swallowed into an empty body dict, so this
        # surfaces as an ordinary validation failure (missing required
        # fields), never a 500.
        assert status == 400
        assert "error" in body

    def test_authenticated_route_handler_exception_returns_500(
        self, call_api, mocker, partner_user
    ):
        token = _login_token(call_api, partner_user)
        mocker.patch("api.router.leads_list", side_effect=RuntimeError("boom"))

        status, body = call_api("GET", "/api/leads", token=token)

        # Pinned as current behaviour: assert only the
        # status and the presence of the error key, never the message text,
        # so a change to the error wording does not break this test.
        assert status == 500
        assert "error" in body


# ─── MANAGER (OPIEKUN) COLUMN ────────────────────────────
#
# The manager role is admitted to leads/partners/comments/audit while
# every configuration-owning (Settings) surface and general user
# administration stays admin-only. This class asserts both directions: every
# ADMIT endpoint must not 403 a manager session, and every EXCLUDE endpoint
# must still 403 one, exactly as it already does for a partner.


class TestManagerAuthorisation:
    # ── ADMIT: leads, partners, comments, audit, stats ────────────────────

    def test_manager_hitting_partners_collection_does_not_get_403(
        self, call_api, manager_user
    ):
        token = _login_token(call_api, manager_user)
        status, body = call_api("GET", "/api/partners", token=token)

        assert status != 403

    def test_manager_hitting_single_partner_does_not_get_403(
        self, call_api, manager_user, partner_user
    ):
        token = _login_token(call_api, manager_user)
        status, body = call_api(
            "GET", f"/api/partners/{partner_user['id']}", token=token
        )

        assert status != 403

    def test_manager_updating_lead_status_does_not_get_403(
        self, call_api, manager_user, sample_lead
    ):
        token = _login_token(call_api, manager_user)
        status, body = call_api(
            "PATCH",
            f"/api/leads/{sample_lead['id']}",
            body={"commission": 500},
            token=token,
        )

        assert status != 403

    def test_manager_hitting_bulk_lead_path_does_not_get_403(
        self, call_api, manager_user, sample_lead
    ):
        token = _login_token(call_api, manager_user)
        status, body = call_api(
            "POST",
            "/api/leads/bulk",
            body={"action": "accept", "items": [{"id": sample_lead["id"],
                  "expected_revision": sample_lead["revision"], "operation_id": "manager-bulk"}]},
            token=token,
        )

        assert status == 200
        assert len(body["ok"]) == 1
        assert body["error"] == []

    def test_manager_previews_lead_update_without_permission_error(
        self, manager_user, sample_lead
    ):
        """preview_lead_update has no HTTP route of its own -- it is called
        only from the AI-agent surface (api/agent_tools.py,
        api/agent_actions.py) -- so it is exercised directly at the function
        level rather than through call_api, matching how it is actually
        invoked in production."""
        result = leads.preview_lead_update(
            sample_lead["id"], manager_user, {"commission": 250}
        )

        assert result["preview"]["lead_id"] == sample_lead["id"]

    def test_manager_lists_comments_including_internal(
        self, call_api, manager_user, sample_lead
    ):
        token = _login_token(call_api, manager_user)
        status, body = call_api(
            "GET", f"/api/leads/{sample_lead['id']}/comments", token=token
        )

        assert status != 403

    def test_manager_creates_internal_comment(
        self, call_api, manager_user, sample_lead
    ):
        token = _login_token(call_api, manager_user)
        status, body = call_api(
            "POST",
            f"/api/leads/{sample_lead['id']}/comments",
            body={"body": "Wewnętrzna notatka zespołu", "is_internal": True},
            token=token,
        )

        assert status != 403
        assert body["comment"]["is_internal"] == 1

    def test_manager_deletes_another_users_comment(
        self, call_api, manager_user, partner_user, sample_lead
    ):
        partner_token = _login_token(call_api, partner_user)
        _, create_body = call_api(
            "POST",
            f"/api/leads/{sample_lead['id']}/comments",
            body={"body": "Komentarz partnera"},
            token=partner_token,
        )
        comment_id = create_body["comment"]["id"]

        manager_token = _login_token(call_api, manager_user)
        status, body = call_api(
            "DELETE", f"/api/comments/{comment_id}", token=manager_token
        )

        assert status == 200

    def test_manager_reading_audit_log_returns_403(self, call_api, manager_user):
        """A moderator handling customers must not be able to read the
        tamper-evidence trail of their own actions --- the audit log is one
        of the two things withheld from the role."""
        token = _login_token(call_api, manager_user)
        status, body = call_api("GET", "/api/audit", token=token)

        assert status == 403
        assert "error" in body

    def test_manager_reads_stats(self, call_api, manager_user):
        token = _login_token(call_api, manager_user)
        status, body = call_api("GET", "/api/stats", token=token)

        assert status != 403

    # ── ADMIT: every Settings-tab mutation ────────────────────
    #
    # The moderator gets everything except inviting users and reading logs.
    # These assert `!= 403` rather than a success code because an empty body
    # legitimately fails validation with 400 --- what is being pinned is that
    # the request is no longer refused on the grounds of the caller's role.

    def test_manager_updating_settings_is_not_refused(self, call_api, manager_user):
        token = _login_token(call_api, manager_user)
        status, body = call_api(
            "PATCH", "/api/settings", body={"company_name": "x"}, token=token
        )

        assert status != 403

    def test_manager_creating_product_is_not_refused(self, call_api, manager_user):
        token = _login_token(call_api, manager_user)
        status, body = call_api("POST", "/api/products", body={}, token=token)

        assert status != 403

    def test_manager_creating_tier_is_not_refused(self, call_api, manager_user):
        token = _login_token(call_api, manager_user)
        status, body = call_api("POST", "/api/tiers", body={}, token=token)

        assert status != 403

    def test_manager_creating_bonus_def_is_not_refused(self, call_api, manager_user):
        token = _login_token(call_api, manager_user)
        status, body = call_api("POST", "/api/bonus-defs", body={}, token=token)

        assert status != 403

    def test_manager_creating_lead_stage_is_not_refused(self, call_api, manager_user):
        token = _login_token(call_api, manager_user)
        status, body = call_api("POST", "/api/lead-stages", body={}, token=token)

        assert status != 403

    def test_manager_listing_users_returns_403(self, call_api, manager_user):
        token = _login_token(call_api, manager_user)
        status, body = call_api("GET", "/api/users", token=token)

        assert status == 403
        assert "error" in body

    def test_manager_creating_user_returns_403(self, call_api, manager_user):
        token = _login_token(call_api, manager_user)
        status, body = call_api(
            "POST",
            "/api/users",
            body={
                "name": "Someone",
                "email": "someone-manager-test@test.pl",
                "password": "SomeonePass123!",
                "company": "Someone Co",
            },
            token=token,
        )

        assert status == 403
        assert "error" in body

    # ── The partner-invite door must not mint an escalated role ──

    def test_manager_inviting_an_admin_through_the_partner_door_returns_403_and_creates_no_account(
        self, call_api, manager_user
    ):
        token = _login_token(call_api, manager_user)
        email = "partner-door-admin@test.pl"
        status, body = call_api(
            "POST",
            "/api/users/invite",
            body={
                "name": "Escalated Admin",
                "email": email,
                "company": "Escalation Co",
                "role": "admin",
            },
            token=token,
        )

        assert status == 403
        assert "error" in body
        conn = get_conn()
        try:
            user_row = conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
            invite_row = conn.execute(
                """SELECT pi.id FROM partner_invitations pi
                   JOIN users u ON u.id = pi.user_id
                   WHERE u.email = ?""",
                (email,),
            ).fetchone()
        finally:
            conn.close()
        assert user_row is None
        assert invite_row is None

    def test_manager_inviting_a_manager_through_the_partner_door_returns_403_and_creates_no_account(
        self, call_api, manager_user
    ):
        token = _login_token(call_api, manager_user)
        email = "partner-door-manager@test.pl"
        conn = get_conn()
        try:
            invitations_before = conn.execute(
                "SELECT COUNT(*) AS c FROM partner_invitations"
            ).fetchone()["c"]
        finally:
            conn.close()

        status, body = call_api(
            "POST",
            "/api/users/invite",
            body={
                "name": "Escalated Manager",
                "email": email,
                "company": "Escalation Co",
                "role": "manager",
            },
            token=token,
        )

        assert status == 403
        assert "error" in body
        conn = get_conn()
        try:
            user_row = conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
            invitations_after = conn.execute(
                "SELECT COUNT(*) AS c FROM partner_invitations"
            ).fetchone()["c"]
        finally:
            conn.close()
        assert user_row is None
        assert invitations_after == invitations_before

    def test_manager_inviting_a_partner_through_the_partner_door_is_still_allowed(
        self, call_api, manager_user
    ):
        token = _login_token(call_api, manager_user)
        email = "partner-door-explicit-partner@test.pl"
        status, body = call_api(
            "POST",
            "/api/users/invite",
            body={
                "name": "Allowed Partner",
                "email": email,
                "company": "Allowed Co",
                "role": "partner",
            },
            token=token,
        )

        assert status == 201, body
        conn = get_conn()
        try:
            user_row = conn.execute(
                "SELECT role, account_status FROM users WHERE email = ?", (email,)
            ).fetchone()
        finally:
            conn.close()
        assert user_row is not None
        assert user_row["role"] == "partner"
        assert user_row["account_status"] == "pending"

    def test_manager_inviting_with_no_role_field_still_creates_a_partner(
        self, call_api, manager_user
    ):
        token = _login_token(call_api, manager_user)
        email = "partner-door-no-role@test.pl"
        status, body = call_api(
            "POST",
            "/api/users/invite",
            body={
                "name": "Default Role Partner",
                "email": email,
                "company": "Default Co",
            },
            token=token,
        )

        assert status == 201, body
        conn = get_conn()
        try:
            user_row = conn.execute(
                "SELECT role, account_status FROM users WHERE email = ?", (email,)
            ).fetchone()
        finally:
            conn.close()
        assert user_row is not None
        assert user_row["role"] == "partner"
        assert user_row["account_status"] == "pending"


# ─── PARTNER COVERAGE FOR THE SAME EXCLUDE SET ─────────────────────────────
#
# TestAuthorisationGate above already covers /api/partners, /api/users,
# leads/bulk and cross-partner user updates for a partner session. The
# endpoints below prove every excluded endpoint in both directions (manager
# AND partner refused).


class TestPartnerRefusedConfigurationSurface:
    def test_partner_reading_audit_log_returns_403(self, call_api, partner_user):
        token = _login_token(call_api, partner_user)
        status, body = call_api("GET", "/api/audit", token=token)

        assert status == 403
        assert "error" in body

    def test_partner_updating_settings_returns_403(self, call_api, partner_user):
        token = _login_token(call_api, partner_user)
        status, body = call_api(
            "PATCH", "/api/settings", body={"company_name": "x"}, token=token
        )

        assert status == 403
        assert "error" in body

    def test_partner_creating_product_returns_403(self, call_api, partner_user):
        token = _login_token(call_api, partner_user)
        status, body = call_api("POST", "/api/products", body={}, token=token)

        assert status == 403
        assert "error" in body

    def test_partner_creating_tier_returns_403(self, call_api, partner_user):
        token = _login_token(call_api, partner_user)
        status, body = call_api("POST", "/api/tiers", body={}, token=token)

        assert status == 403
        assert "error" in body

    def test_partner_creating_bonus_def_returns_403(self, call_api, partner_user):
        token = _login_token(call_api, partner_user)
        status, body = call_api("POST", "/api/bonus-defs", body={}, token=token)

        assert status == 403
        assert "error" in body

    def test_partner_creating_lead_stage_returns_403(self, call_api, partner_user):
        token = _login_token(call_api, partner_user)
        status, body = call_api("POST", "/api/lead-stages", body={}, token=token)

        assert status == 403
        assert "error" in body

    def test_nobody_can_send_a_test_mail_from_the_app(self):
        # Mail is the operator's, so the producer has no test-send route.
        from api.router import ROUTES
        assert not [route for route in ROUTES if "email" in route[1]]

    def test_partner_creating_internal_comment_is_refused(
        self, call_api, partner_user, sample_lead
    ):
        token = _login_token(call_api, partner_user)
        status, body = call_api(
            "POST",
            f"/api/leads/{sample_lead['id']}/comments",
            body={"body": "próba wewnętrznej notatki", "is_internal": True},
            token=token,
        )

        assert status == 403
        assert "error" in body

    def test_partner_deleting_another_users_comment_is_refused(
        self, call_api, admin_user, partner_user, sample_lead
    ):
        admin_token = _login_token(call_api, admin_user)
        _, create_body = call_api(
            "POST",
            f"/api/leads/{sample_lead['id']}/comments",
            body={"body": "Komentarz administratora"},
            token=admin_token,
        )
        comment_id = create_body["comment"]["id"]

        partner_token = _login_token(call_api, partner_user)
        status, body = call_api(
            "DELETE", f"/api/comments/{comment_id}", token=partner_token
        )

        assert status == 403


# ─── TEAM INVITE: THE MODAL'S EXACT PAYLOAD ─────────
#
# js/settings.js's sendTeamInvite posts exactly {name, email} -- no company
# key at all. The partner path's shared validator requires company, so the
# team invite must not reuse it as is. This test drives
# the real router with that exact payload so a future contract drift
# between the modal and the handler fails the suite, not the producer.


class TestTeamInviteRouterContract:
    def test_modal_payload_with_empty_acting_company_creates_pending_manager(
        self, call_api, admin_user
    ):
        # admin_user's company column is left at the schema default (''),
        # matching the owner account this defect was found on -- a company-derivation fix that only works for a
        # non-empty acting company would not have fixed the real bug.
        assert admin_user["company"] == ""
        token = _login_token(call_api, admin_user)

        status, body = call_api(
            "POST",
            "/api/users/team-invite",
            body={"name": "Nowy Opiekun", "email": "router-team-invite@test.pl"},
            token=token,
        )

        assert status == 201, body
        assert body["invite_url"]
        assert body["user"]["role"] == "manager"
        assert body["user"]["account_status"] == "pending"
