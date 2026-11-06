"""
PartnerType -- Partner invitation module unit tests.

Covers storage, hashing, expiry, single-use redemption and status
transitions of api/invitations.py directly, without going through the
router or touching outbound mail. The full router-driven flow -- the
invite link, the mail outcome on an unconfigured system, and login
immediately after acceptance -- lives in
api/tests/integration/test_invitation_flow.py.
"""
from datetime import datetime, timedelta

import bcrypt
import pytest

from api.auth import login
from api.db import get_conn
from api.invitations import (
    accept_invitation,
    create_invitation,
    get_invitation,
    handle_accept,
    handle_create,
    handle_create_team_invite,
    handle_preview,
)
from api.stats import handle_stats


VALID_INVITE_DATA = {
    "name": "Nowy Partner",
    "email": "invite@test.pl",
    "company": "Zaproszalnia Sp. z o.o.",
}


def _expire(token_hash_row_id=None, **kwargs):
    """Force an invitation's expiry into the past, directly in the DB."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE partner_invitations SET expires_at = ? WHERE id = ?",
            ((datetime.utcnow() - timedelta(days=1)).isoformat(), token_hash_row_id),
        )
        conn.commit()
    finally:
        conn.close()


def _invitation_id_for_user(user_id):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM partner_invitations WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


# ─── CREATE ───────────────────────────────────────────────────────────────────

class TestCreateInvitation:
    def test_creates_pending_inactive_user_and_exactly_one_invitation_row(
        self, admin_user, test_db_path
    ):
        user, raw_token = create_invitation(VALID_INVITE_DATA, admin_user)

        assert user["account_status"] == "pending"
        assert user["active"] == 0
        assert isinstance(raw_token, str) and len(raw_token) > 20

        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM partner_invitations WHERE user_id = ?", (user["id"],)
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1

    def test_invite_pins_the_partner_to_the_chosen_commission_tier(
        self, admin_user, test_db_path
    ):
        """The invite form offers the producer's own levels and sends an id.

        It used to send the legacy bronze/silver/gold WORD, which was then
        matched against tier names -- so a producer whose levels are named
        otherwise invited every partner onto no tier at all.
        """
        conn = get_conn()
        try:
            tier_id = conn.execute(
                "SELECT id FROM commission_tiers WHERE name = 'Gold'"
            ).fetchone()["id"]
        finally:
            conn.close()

        user, _ = create_invitation(
            dict(VALID_INVITE_DATA, tier_id=tier_id), admin_user
        )

        assert user["tier_id"] == tier_id

    def test_invite_without_a_tier_is_allowed(self, admin_user, test_db_path):
        """"No tier" is a real answer: the producer assigns one later."""
        user, _ = create_invitation(VALID_INVITE_DATA, admin_user)
        assert user["account_status"] == "pending"

    def test_invite_with_an_unknown_tier_is_refused(self, admin_user, test_db_path):
        with pytest.raises(ValueError):
            create_invitation(dict(VALID_INVITE_DATA, tier_id=9999), admin_user)

    def test_non_admin_cannot_create_invitation(self, partner_user):
        with pytest.raises(PermissionError):
            create_invitation(VALID_INVITE_DATA, partner_user)

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT id FROM users WHERE email = ?", (VALID_INVITE_DATA["email"],)
            ).fetchone()
        finally:
            conn.close()
        assert row is None

    def test_invitation_row_never_holds_the_raw_secret(self, admin_user, test_db_path):
        user, raw_token = create_invitation(VALID_INVITE_DATA, admin_user)

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM partner_invitations WHERE user_id = ?", (user["id"],)
            ).fetchone()
        finally:
            conn.close()

        values = [str(v) for v in dict(row).values() if v is not None]
        assert not any(raw_token in v for v in values)

    def test_second_invitation_for_same_pending_user_invalidates_first(
        self, admin_user, test_db_path
    ):
        user1, token_a = create_invitation(VALID_INVITE_DATA, admin_user)
        user2, token_b = create_invitation(VALID_INVITE_DATA, admin_user)

        assert user1["id"] == user2["id"]
        assert get_invitation(token_a) is None
        assert get_invitation(token_b) is not None

        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM partner_invitations WHERE user_id = ?", (user1["id"],)
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1

    def test_reinviting_an_active_account_email_is_rejected(self, admin_user, test_db_path):
        user, token = create_invitation(VALID_INVITE_DATA, admin_user)
        accept_invitation(token, "ValidNewPass123!")

        with pytest.raises(ValueError):
            create_invitation(VALID_INVITE_DATA, admin_user)


# ─── PREVIEW ──────────────────────────────────────────────────────────────────

class TestGetInvitation:
    def test_fresh_secret_returns_invitee_name_and_email(self, admin_user, test_db_path):
        user, raw_token = create_invitation(VALID_INVITE_DATA, admin_user)

        invite = get_invitation(raw_token)
        assert invite == {"name": VALID_INVITE_DATA["name"], "email": VALID_INVITE_DATA["email"]}

    def test_unknown_token_returns_none(self, test_db_path):
        assert get_invitation("this-token-was-never-issued") is None

    def test_expired_token_returns_none(self, admin_user, test_db_path):
        user, raw_token = create_invitation(VALID_INVITE_DATA, admin_user)
        _expire(_invitation_id_for_user(user["id"]))

        assert get_invitation(raw_token) is None

    def test_already_accepted_token_returns_none(self, admin_user, test_db_path):
        user, raw_token = create_invitation(VALID_INVITE_DATA, admin_user)
        accept_invitation(raw_token, "ValidNewPass123!")

        assert get_invitation(raw_token) is None

    def test_unknown_expired_and_accepted_tokens_fail_the_preview_handler_identically(
        self, admin_user, test_db_path
    ):
        user, raw_token = create_invitation(VALID_INVITE_DATA, admin_user)
        accepted_status, accepted_body = None, None

        unknown_status, unknown_body = handle_preview(None, "never-issued-token")

        user2, raw_token2 = create_invitation(
            dict(VALID_INVITE_DATA, email="second@test.pl"), admin_user
        )
        _expire(_invitation_id_for_user(user2["id"]))
        expired_status, expired_body = handle_preview(None, raw_token2)

        accept_invitation(raw_token, "ValidNewPass123!")
        accepted_status, accepted_body = handle_preview(None, raw_token)

        assert unknown_status == expired_status == accepted_status == 404
        assert unknown_body == expired_body == accepted_body


# ─── ACCEPT ───────────────────────────────────────────────────────────────────

class TestAcceptInvitation:
    def test_fresh_secret_with_valid_password_activates_the_account(
        self, admin_user, test_db_path
    ):
        user, raw_token = create_invitation(VALID_INVITE_DATA, admin_user)

        accept_invitation(raw_token, "ValidNewPass123!")

        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
            invitation = conn.execute(
                "SELECT * FROM partner_invitations WHERE user_id = ?", (user["id"],)
            ).fetchone()
        finally:
            conn.close()

        assert row["account_status"] == "active"
        assert row["active"] == 1
        assert bcrypt.checkpw(b"ValidNewPass123!", row["password"].encode())
        assert invitation["accepted_at"] is not None

    def test_accept_deletes_existing_sessions_for_that_user(self, admin_user, test_db_path):
        user, raw_token = create_invitation(VALID_INVITE_DATA, admin_user)

        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO sessions (token, user_id, expires_at) VALUES (?,?,?)",
                ("stale-session-token", user["id"], "2099-01-01T00:00:00"),
            )
            conn.commit()
        finally:
            conn.close()

        accept_invitation(raw_token, "ValidNewPass123!")

        conn = get_conn()
        try:
            remaining = conn.execute(
                "SELECT COUNT(*) AS c FROM sessions WHERE user_id = ?", (user["id"],)
            ).fetchone()["c"]
        finally:
            conn.close()
        assert remaining == 0

    def test_accepting_the_same_secret_twice_fails_the_second_time(
        self, admin_user, test_db_path
    ):
        user, raw_token = create_invitation(VALID_INVITE_DATA, admin_user)

        accept_invitation(raw_token, "ValidNewPass123!")

        with pytest.raises(ValueError):
            accept_invitation(raw_token, "AnotherValidPass123!")

    def test_unknown_token_raises_value_error(self, test_db_path):
        with pytest.raises(ValueError):
            accept_invitation("never-issued-token", "ValidNewPass123!")

    def test_expired_token_raises_value_error(self, admin_user, test_db_path):
        user, raw_token = create_invitation(VALID_INVITE_DATA, admin_user)
        _expire(_invitation_id_for_user(user["id"]))

        with pytest.raises(ValueError):
            accept_invitation(raw_token, "ValidNewPass123!")

    def test_short_password_fails_with_password_message_and_leaves_account_pending(
        self, admin_user, test_db_path
    ):
        user, raw_token = create_invitation(VALID_INVITE_DATA, admin_user)

        with pytest.raises(ValueError, match="8 znak"):
            accept_invitation(raw_token, "short")

        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        finally:
            conn.close()
        assert row["account_status"] == "pending"
        assert row["active"] == 0

        # The secret itself is still fresh and redeemable afterwards.
        assert get_invitation(raw_token) is not None


# ─── HTTP HANDLERS ─────────────────────────────────────────────────────────────

class TestHandlerBoundaries:
    def test_handle_create_refuses_non_admin(self, partner_user):
        status, body = handle_create(None, partner_user, VALID_INVITE_DATA)
        assert status == 403
        assert "error" in body

    def test_handle_create_maps_value_error_to_400(self, admin_user):
        status, body = handle_create(None, admin_user, {"name": "", "email": "", "company": ""})
        assert status == 400
        assert "error" in body

    def test_handle_create_returns_201_on_success(self, admin_user):
        status, body = handle_create(
            None, admin_user, dict(VALID_INVITE_DATA, email="handlercreate@test.pl")
        )
        assert status == 201
        assert "user" in body
        assert body["user"]["email"] == "handlercreate@test.pl"

    def test_handle_preview_returns_404_for_unknown_token(self, test_db_path):
        status, body = handle_preview(None, "never-issued-token")
        assert status == 404
        assert "error" in body

    def test_handle_preview_returns_200_for_fresh_token(self, admin_user, test_db_path):
        user, raw_token = create_invitation(
            dict(VALID_INVITE_DATA, email="preview@test.pl"), admin_user
        )
        status, body = handle_preview(None, raw_token)
        assert status == 200
        assert body["invitation"]["email"] == "preview@test.pl"

    def test_handle_accept_returns_200_on_success(self, admin_user, test_db_path):
        user, raw_token = create_invitation(
            dict(VALID_INVITE_DATA, email="accepthandler@test.pl"), admin_user
        )
        status, body = handle_accept(None, raw_token, {"password": "ValidNewPass123!"})
        assert status == 200
        assert body.get("ok") is True

    def test_handle_accept_returns_404_for_unknown_token(self, test_db_path):
        status, body = handle_accept(None, "never-issued-token", {"password": "ValidNewPass123!"})
        assert status == 404
        assert "error" in body

    def test_handle_accept_returns_400_for_short_password(self, admin_user, test_db_path):
        user, raw_token = create_invitation(
            dict(VALID_INVITE_DATA, email="shortpwhandler@test.pl"), admin_user
        )
        status, body = handle_accept(None, raw_token, {"password": "short"})
        assert status == 400
        assert "error" in body


# ─── PARTNER-INVITE ROLE RESTRICTION ────────────────────────────────
# create_invitation's guard is the shared is_staff predicate, which admits
# both an administrator and a manager (channel manager) caller. This class pins the
# second guard added alongside it: a non-administrator staff caller may only
# ever request role="partner" through this door -- the dedicated admin-only
# team-invite door is the only way a manager or admin account is ever
# created. Both guards are exercised directly at the create_invitation /
# handle_create boundary, without going through the router (the router-driven
# proof of the exact bypass lives in
# api/tests/integration/test_access_control.py::TestManagerAuthorisation).


class TestPartnerInviteRoleRestriction:
    def test_manager_requesting_admin_role_raises_permission_error_and_creates_no_account(
        self, manager_user, test_db_path
    ):
        email = "unit-partner-door-admin@test.pl"
        with pytest.raises(PermissionError):
            create_invitation(
                dict(VALID_INVITE_DATA, email=email, role="admin"), manager_user
            )
        conn = get_conn()
        try:
            row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        finally:
            conn.close()
        assert row is None

    def test_manager_requesting_manager_role_raises_permission_error_and_creates_no_account(
        self, manager_user, test_db_path
    ):
        """A caller requesting the role it already holds is refused too --
        sameness is not an exemption."""
        email = "unit-partner-door-manager@test.pl"
        with pytest.raises(PermissionError):
            create_invitation(
                dict(VALID_INVITE_DATA, email=email, role="manager"), manager_user
            )
        conn = get_conn()
        try:
            row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        finally:
            conn.close()
        assert row is None

    def test_manager_requesting_no_role_returns_partner(self, manager_user, test_db_path):
        data = dict(VALID_INVITE_DATA, email="unit-partner-door-no-role@test.pl")
        data.pop("role", None)
        user, raw_token = create_invitation(data, manager_user)
        assert user["role"] == "partner"

    def test_manager_requesting_explicit_partner_role_returns_partner(
        self, manager_user, test_db_path
    ):
        user, raw_token = create_invitation(
            dict(
                VALID_INVITE_DATA,
                email="unit-partner-door-explicit-partner@test.pl",
                role="partner",
            ),
            manager_user,
        )
        assert user["role"] == "partner"

    def test_admin_caller_may_still_create_a_manager_invitation(self, admin_user, test_db_path):
        user, raw_token = create_invitation(
            dict(
                VALID_INVITE_DATA,
                email="unit-admin-still-invites-manager@test.pl",
                role="manager",
            ),
            admin_user,
        )
        assert user["role"] == "manager"

    def test_admin_caller_may_still_create_an_admin_invitation(self, admin_user, test_db_path):
        user, raw_token = create_invitation(
            dict(
                VALID_INVITE_DATA,
                email="unit-admin-still-invites-admin@test.pl",
                role="admin",
            ),
            admin_user,
        )
        assert user["role"] == "admin"

    def test_partner_caller_is_refused_by_the_staff_predicate_not_the_new_clause(
        self, partner_user, test_db_path
    ):
        """The two guards compose: a partner acting user requesting
        role="partner" is still refused, which can only come from the
        pre-existing is_staff predicate -- proving the new clause did not
        replace it."""
        with pytest.raises(PermissionError):
            create_invitation(
                dict(
                    VALID_INVITE_DATA,
                    email="unit-partner-door-partner-caller@test.pl",
                    role="partner",
                ),
                partner_user,
            )

    def test_handle_create_maps_the_new_permission_error_to_403(
        self, manager_user, test_db_path
    ):
        status, body = handle_create(
            None,
            manager_user,
            dict(
                VALID_INVITE_DATA,
                email="unit-partner-door-handler-403@test.pl",
                role="admin",
            ),
        )
        assert status == 403
        assert "error" in body


class TestClosedAccountsStayClosed:
    """An invitation link must never reopen an account the producer closed."""

    @staticmethod
    def _set_status(user_id, status):
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE users SET account_status = ?, active = 0 WHERE id = ?",
                (status, user_id),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _account(user_id):
        conn = get_conn()
        try:
            return conn.execute(
                "SELECT account_status, active FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        finally:
            conn.close()

    @pytest.mark.parametrize("status", ["suspended", "rejected"])
    def test_a_link_for_a_closed_account_is_refused_even_if_the_row_survives(
        self, admin_user, test_db_path, status
    ):
        user, raw_token = create_invitation(VALID_INVITE_DATA, admin_user)
        self._set_status(user["id"], status)

        assert get_invitation(raw_token) is None
        with pytest.raises(ValueError):
            accept_invitation(raw_token, "ValidNewPass123!")
        assert self._account(user["id"])["account_status"] == status
        assert self._account(user["id"])["active"] == 0

    @pytest.mark.parametrize("status", ["suspended", "rejected"])
    def test_closing_an_account_withdraws_its_open_invitation(
        self, admin_user, test_db_path, status
    ):
        from api.users import update_user

        user, raw_token = create_invitation(VALID_INVITE_DATA, admin_user)
        update_user(user["id"], {"account_status": status}, admin_user)

        assert _invitation_id_for_user(user["id"]) is None
        with pytest.raises(ValueError):
            accept_invitation(raw_token, "ValidNewPass123!")

    def test_an_erased_account_cannot_be_brought_back_by_its_link(
        self, admin_user, test_db_path
    ):
        from api import gdpr

        user, raw_token = create_invitation(VALID_INVITE_DATA, admin_user)
        gdpr.erase_person(VALID_INVITE_DATA["email"], admin_user, confirm=True)

        assert get_invitation(raw_token) is None
        with pytest.raises(ValueError):
            accept_invitation(raw_token, "ValidNewPass123!")
        assert self._account(user["id"])["active"] == 0

    def test_an_account_the_producer_already_approved_can_still_set_its_password(
        self, admin_user, test_db_path
    ):
        from api.users import update_user

        user, raw_token = create_invitation(VALID_INVITE_DATA, admin_user)
        update_user(user["id"], {"account_status": "active"}, admin_user)

        accept_invitation(raw_token, "ValidNewPass123!")

        assert self._account(user["id"])["account_status"] == "active"


class TestPendingAccountReuseKeepsItsRole:
    """Re-inviting a pending account must never hand out a link for an
    account of a different role than the one requested."""

    @pytest.mark.parametrize("pending_role", ["admin", "manager", "observer"])
    def test_manager_cannot_reinvite_a_pending_staff_account_as_partner(
        self, admin_user, manager_user, test_db_path, pending_role
    ):
        email = f"pending-{pending_role}@test.pl"
        pending, original_token = create_invitation(
            dict(VALID_INVITE_DATA, email=email, role=pending_role), admin_user
        )
        assert pending["role"] == pending_role

        status, body = handle_create(
            None, manager_user, dict(VALID_INVITE_DATA, email=email, role="partner")
        )

        assert status in (400, 403)
        assert "invite_url" not in body
        # The real invitee's link still works and nothing new was minted.
        assert get_invitation(original_token) is not None
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT created_by FROM partner_invitations WHERE user_id = ?",
                (pending["id"],),
            ).fetchall()
            role = conn.execute(
                "SELECT role FROM users WHERE id = ?", (pending["id"],)
            ).fetchone()["role"]
        finally:
            conn.close()
        assert [r["created_by"] for r in rows] == [admin_user["id"]]
        assert role == pending_role

    def test_admin_cannot_reinvite_a_pending_admin_under_another_role(
        self, admin_user, test_db_path
    ):
        email = "pending-admin-other-role@test.pl"
        create_invitation(dict(VALID_INVITE_DATA, email=email, role="admin"), admin_user)
        with pytest.raises(ValueError):
            create_invitation(
                dict(VALID_INVITE_DATA, email=email, role="partner"), admin_user
            )

    def test_manager_may_still_reinvite_a_pending_partner(
        self, admin_user, manager_user, test_db_path
    ):
        email = "pending-partner-reinvite@test.pl"
        first, _ = create_invitation(dict(VALID_INVITE_DATA, email=email), admin_user)
        again, token = create_invitation(dict(VALID_INVITE_DATA, email=email), manager_user)
        assert again["id"] == first["id"]
        assert get_invitation(token) is not None


# ─── TEAM INVITATION ─────────────────────────────────
# The Team tab's own entry point: role is fixed server-side to "manager"
# regardless of what the caller posts, and only an administrator may open
# this door -- a channel manager cannot mint another one. Every other
# guarantee (hashed single-use token, TTL, works-without-mail link,
# pending-account reuse-on-reinvite) comes from create_invitation, the exact
# core the partner path above already exercises.

# Do not add a "company" key to this fixture. js/settings.js's Team
# modal never sends one -- it posts only name and email -- so these tests
# send exactly what the real button sends.
VALID_TEAM_INVITE_DATA = {
    "name": "Nowy Opiekun",
    "email": "team-invite@test.pl",
}


class TestCreateTeamInvite:
    def test_admin_creates_pending_manager_account_with_link(self, admin_user, test_db_path):
        status, body = handle_create_team_invite(None, admin_user, VALID_TEAM_INVITE_DATA)

        assert status == 201, body
        assert body["user"]["role"] == "manager"
        assert body["user"]["account_status"] == "pending"
        assert body["invite_url"]
        assert "token=" in body["invite_url"]

    def test_admin_role_posted_in_body_is_refused_and_creates_no_account(
        self, admin_user, test_db_path
    ):
        """The team door accepts a role, but only from
        users.TEAM_ROLES. "admin" is refused outright rather than silently
        downgraded --- a caller asking for an escalated role should hear
        "no", and no half-created pending row should survive the attempt."""
        status, body = handle_create_team_invite(
            None, admin_user, dict(VALID_TEAM_INVITE_DATA, role="admin")
        )

        assert status == 400, body
        assert "error" in body

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM users WHERE email = ?",
                (VALID_TEAM_INVITE_DATA["email"].lower(),),
            ).fetchone()
        finally:
            conn.close()
        assert row is None

    def test_observer_role_posted_in_body_is_honoured(self, admin_user, test_db_path):
        """Observer is a real team role and the door must mint it, or the
        role is unreachable in practice."""
        status, body = handle_create_team_invite(
            None, admin_user, dict(VALID_TEAM_INVITE_DATA, role="observer")
        )

        assert status == 201, body
        assert body["user"]["role"] == "observer"
        assert body["user"]["account_status"] == "pending"

    def test_role_omitted_from_body_still_defaults_to_manager(
        self, admin_user, test_db_path
    ):
        """Every caller written before observers existed omits `role`."""
        status, body = handle_create_team_invite(
            None, admin_user, dict(VALID_TEAM_INVITE_DATA)
        )

        assert status == 201, body
        assert body["user"]["role"] == "manager"

    def test_admin_with_empty_company_can_still_invite(self, admin_user, test_db_path):
        """A bootstrapped owner's company is ''. A derivation
        that only works for a non-empty acting company would not have fixed
        the real defect this plan closes."""
        assert admin_user["company"] == ""

        status, body = handle_create_team_invite(
            None, admin_user, dict(VALID_TEAM_INVITE_DATA, email="empty-company-admin@test.pl")
        )

        assert status == 201, body
        assert body["user"]["company"] == ""

    def test_client_supplied_company_is_ignored_derived_from_acting_admin(
        self, admin_user, test_db_path
    ):
        """Regression guard for the derivation being bypassable: even when
        the acting admin has a real company on file, a client-supplied
        company in the request body must never reach the created account."""
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE users SET company = ? WHERE id = ?",
                ("Prawdziwa Firma Producenta", admin_user["id"]),
            )
            conn.commit()
            # The router re-fetches the acting user from the session on
            # every request (api.auth.get_session_user); re-read here too so
            # this test exercises the same fresh-dict shape production code
            # actually sees, not the fixture's now-stale snapshot.
            acting_admin = dict(
                conn.execute(
                    "SELECT * FROM users WHERE id = ?", (admin_user["id"],)
                ).fetchone()
            )
        finally:
            conn.close()

        status, body = handle_create_team_invite(
            None,
            acting_admin,
            dict(
                VALID_TEAM_INVITE_DATA,
                email="client-company-ignored@test.pl",
                company="Zmyślona Firma Klienta",
            ),
        )

        assert status == 201, body
        assert body["user"]["company"] == "Prawdziwa Firma Producenta"

    def test_manager_session_is_refused_and_creates_no_account(
        self, manager_user, test_db_path
    ):
        status, body = handle_create_team_invite(
            None, manager_user, dict(VALID_TEAM_INVITE_DATA, email="opiekun-blocked@test.pl")
        )

        assert status == 403
        assert "error" in body
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT id FROM users WHERE email = ?", ("opiekun-blocked@test.pl",)
            ).fetchone()
        finally:
            conn.close()
        assert row is None

    def test_partner_session_is_refused_and_creates_no_account(
        self, partner_user, test_db_path
    ):
        status, body = handle_create_team_invite(
            None, partner_user, dict(VALID_TEAM_INVITE_DATA, email="partner-blocked@test.pl")
        )

        assert status == 403
        assert "error" in body
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT id FROM users WHERE email = ?", ("partner-blocked@test.pl",)
            ).fetchone()
        finally:
            conn.close()
        assert row is None

    def test_existing_active_account_address_is_refused_no_second_account(
        self, admin_user, partner_user
    ):
        status, body = handle_create_team_invite(
            None, admin_user, dict(VALID_TEAM_INVITE_DATA, email=partner_user["email"])
        )

        assert status == 400
        assert "error" in body
        conn = get_conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE email = ?", (partner_user["email"],)
            ).fetchone()["c"]
        finally:
            conn.close()
        assert count == 1

    def test_pending_partner_address_is_refused_role_unchanged(self, admin_user, test_db_path):
        pending_partner, _raw_token = create_invitation(
            {
                "name": "Pending Partner",
                "email": "pending-partner@test.pl",
                "company": "Some Co",
            },
            admin_user,
        )
        assert pending_partner["role"] == "partner"
        assert pending_partner["account_status"] == "pending"

        status, body = handle_create_team_invite(
            None, admin_user, dict(VALID_TEAM_INVITE_DATA, email="pending-partner@test.pl")
        )

        assert status == 400
        assert "error" in body
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT role FROM users WHERE email = ?", ("pending-partner@test.pl",)
            ).fetchone()
        finally:
            conn.close()
        assert row["role"] == "partner"

    def test_pending_unredeemed_opiekun_address_reuses_account_and_invalidates_prior_invitation(
        self, admin_user, test_db_path
    ):
        first_status, first_body = handle_create_team_invite(
            None, admin_user, dict(VALID_TEAM_INVITE_DATA, email="reinvite-opiekun@test.pl")
        )
        assert first_status == 201, first_body
        first_token = first_body["invite_url"].split("token=")[1]
        first_user_id = first_body["user"]["id"]

        second_status, second_body = handle_create_team_invite(
            None, admin_user, dict(VALID_TEAM_INVITE_DATA, email="reinvite-opiekun@test.pl")
        )
        assert second_status == 201, second_body
        assert second_body["user"]["id"] == first_user_id

        assert get_invitation(first_token) is None

        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM partner_invitations WHERE user_id = ?", (first_user_id,)
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1

    def test_mail_unconfigured_still_returns_usable_link_and_reports_unsent(
        self, admin_user, test_db_path
    ):
        status, body = handle_create_team_invite(
            None, admin_user, dict(VALID_TEAM_INVITE_DATA, email="mailless-opiekun@test.pl")
        )

        assert status == 201, body
        assert body["sent"] is False
        assert body["invite_url"]

    def test_stored_token_is_a_hash_never_the_raw_secret(self, admin_user, test_db_path):
        status, body = handle_create_team_invite(
            None, admin_user, dict(VALID_TEAM_INVITE_DATA, email="hash-check-opiekun@test.pl")
        )
        assert status == 201, body
        raw_token = body["invite_url"].split("token=")[1]

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM partner_invitations WHERE user_id = ?", (body["user"]["id"],)
            ).fetchone()
        finally:
            conn.close()

        values = [str(v) for v in dict(row).values() if v is not None]
        assert not any(raw_token in v for v in values)


# ─── END-TO-END: TEAM INVITE TO A WORKING OPIEKUN LOGIN ─────────
# Proven without a browser; the Team tab's interaction tests live in
# js/__tests__.

class TestTeamInviteEndToEnd:
    def test_invite_redeem_login_and_reach_admin_shaped_stats(self, admin_user, test_db_path):
        status, body = handle_create_team_invite(
            None, admin_user, dict(VALID_TEAM_INVITE_DATA, email="e2e-opiekun@test.pl")
        )
        assert status == 201, body
        raw_token = body["invite_url"].split("token=")[1]

        preview_status, preview_body = handle_preview(None, raw_token)
        assert preview_status == 200
        assert preview_body["invitation"]["email"] == "e2e-opiekun@test.pl"

        accept_status, accept_body = handle_accept(
            None, raw_token, {"password": "OpiekunPass123!"}
        )
        assert accept_status == 200, accept_body

        user, token = login("e2e-opiekun@test.pl", "OpiekunPass123!", "manager")
        assert user["role"] == "manager"
        assert token is not None

        stats_status, stats_body = handle_stats(None, user)
        assert stats_status == 200
        # The admin-shaped payload carries total_partners -- a key the
        # partner-shaped payload does not have.
        assert "total_partners" in stats_body

    def test_same_team_token_cannot_be_redeemed_twice(self, admin_user, test_db_path):
        status, body = handle_create_team_invite(
            None, admin_user, dict(VALID_TEAM_INVITE_DATA, email="double-redeem-opiekun@test.pl")
        )
        raw_token = body["invite_url"].split("token=")[1]

        first_status, first_body = handle_accept(
            None, raw_token, {"password": "OpiekunPass123!"}
        )
        assert first_status == 200, first_body

        second_status, second_body = handle_accept(
            None, raw_token, {"password": "AnotherPass123!"}
        )
        assert second_status == 404, second_body

    def test_expired_team_token_cannot_be_redeemed(self, admin_user, test_db_path):
        status, body = handle_create_team_invite(
            None, admin_user, dict(VALID_TEAM_INVITE_DATA, email="expired-opiekun@test.pl")
        )
        raw_token = body["invite_url"].split("token=")[1]
        _expire(_invitation_id_for_user(body["user"]["id"]))

        accept_status, accept_body = handle_accept(
            None, raw_token, {"password": "OpiekunPass123!"}
        )
        assert accept_status == 404
        assert "error" in accept_body

    def test_redemption_leaves_role_at_manager_never_promotes(self, admin_user, test_db_path):
        status, body = handle_create_team_invite(
            None, admin_user, dict(VALID_TEAM_INVITE_DATA, email="no-promote-opiekun@test.pl")
        )
        raw_token = body["invite_url"].split("token=")[1]

        handle_accept(None, raw_token, {"password": "OpiekunPass123!"})

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT role FROM users WHERE email = ?", ("no-promote-opiekun@test.pl",)
            ).fetchone()
        finally:
            conn.close()
        assert row["role"] == "manager"

    def test_raw_token_never_persisted_in_the_invitation_row(self, admin_user, test_db_path):
        status, body = handle_create_team_invite(
            None, admin_user, dict(VALID_TEAM_INVITE_DATA, email="no-raw-token-opiekun@test.pl")
        )
        raw_token = body["invite_url"].split("token=")[1]

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM partner_invitations WHERE user_id = ?", (body["user"]["id"],)
            ).fetchone()
        finally:
            conn.close()

        values = [str(v) for v in dict(row).values() if v is not None]
        assert not any(raw_token in v for v in values)
