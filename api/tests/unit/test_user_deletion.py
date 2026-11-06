"""
PartnerType -- deleting a staff account.

Changing a role is PATCH /api/users/{id}, admin only. Deleting is the more
dangerous half: four of these tests are
each a way the producer could lock themselves out or lose history they may have
to answer for.
"""
import pytest

from api.db import get_conn
from api.users import create_user, delete_user, handle_delete


def _staff(email, role="manager", name="Marek Moderator"):
    return create_user(
        {"name": name, "email": email, "password": "Tajne-Haslo-123",
         "role": role, "company": "PartnerType"},
        require_company=False,
    )


def _exists(user_id):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT 1 FROM users WHERE id = ?", (user_id,)
        ).fetchone() is not None
    finally:
        conn.close()


class TestDeletingStaff:
    def test_an_admin_deletes_a_moderator(self, admin_user):
        member = _staff("moderator.do.usuniecia@example.test")

        result = delete_user(member["id"], admin_user)

        assert result == {"deleted": True, "id": member["id"]}
        assert not _exists(member["id"])

    def test_the_session_of_a_deleted_account_goes_with_it(self, admin_user):
        """sessions.user_id CASCADEs. Worth pinning: a live session outliving
        the account it belongs to is an authenticated ghost."""
        member = _staff("moderator.z.sesja@example.test")
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO sessions (token, user_id, expires_at, created_at) "
                "VALUES ('tok-test', ?, to_char(timezone('UTC', now()) + interval '1 day', 'YYYY-MM-DD HH24:MI:SS'), to_char(timezone('UTC', now()), 'YYYY-MM-DD HH24:MI:SS'))",
                (member["id"],),
            )
            conn.commit()
        finally:
            conn.close()

        delete_user(member["id"], admin_user)

        conn = get_conn()
        try:
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM sessions WHERE token = 'tok-test'"
            ).fetchone()["n"] == 0
        finally:
            conn.close()


class TestRefusals:
    def test_you_cannot_delete_yourself(self, admin_user):
        with pytest.raises(ValueError, match="własnego"):
            delete_user(admin_user["id"], admin_user)
        assert _exists(admin_user["id"])

    def test_the_last_administrator_survives(self, admin_user):
        """can_manage_users is admin-only, so losing the final admin makes user
        management unreachable for good."""
        other_admin = _staff("drugi.admin@example.test", role="admin", name="Druga Ada")

        # With two admins, one may go.
        delete_user(other_admin["id"], admin_user)

        # Now only one is left, and it is the acting account -- refused twice
        # over, so assert the message names the reason a producer can act on.
        with pytest.raises(ValueError):
            delete_user(admin_user["id"], admin_user)
        assert _exists(admin_user["id"])

    def test_a_partner_is_refused_and_pointed_at_their_own_screen(
        self, admin_user, partner_user
    ):
        """Partners have a lifecycle of their own: reject, seven days to
        restore, then the retention sweep. Deleting one here goes around it."""
        with pytest.raises(ValueError, match="Partnerzy"):
            delete_user(partner_user["id"], admin_user)
        assert _exists(partner_user["id"])

    def test_an_account_that_recorded_a_payout_is_refused_with_the_reason(
        self, admin_user, partner_user
    ):
        """commission_payouts.paid_by is NO ACTION, so SQLite would refuse this
        anyway -- with a foreign-key error the screen cannot explain."""
        member = _staff("moderator.wyplacal@example.test")
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO commission_payouts (partner_id, period_type, period_key, "
                "amount, lead_count, paid_by, paid_at) "
                "VALUES (?, 'month', '2026-07', 100, 1, ?, to_char(timezone('UTC', now()), 'YYYY-MM-DD HH24:MI:SS'))",
                (partner_user["id"], member["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(ValueError, match="wypłat"):
            delete_user(member["id"], admin_user)
        assert _exists(member["id"])

    def test_the_refusal_offers_deactivation_instead(self, admin_user, partner_user):
        member = _staff("moderator.z-historia@example.test")
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO commission_payouts (partner_id, period_type, period_key, "
                "amount, lead_count, paid_by, paid_at) "
                "VALUES (?, 'month', '2026-08', 100, 1, ?, to_char(timezone('UTC', now()), 'YYYY-MM-DD HH24:MI:SS'))",
                (partner_user["id"], member["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(ValueError, match="dezaktywować"):
            delete_user(member["id"], admin_user)


class TestAuthorization:
    def test_a_moderator_may_not_delete_anybody(self, manager_user):
        member = _staff("moderator.ofiara@example.test")

        with pytest.raises(PermissionError):
            delete_user(member["id"], manager_user)
        assert _exists(member["id"])

    def test_the_handler_turns_a_refusal_into_403_and_400(
        self, admin_user, manager_user, partner_user
    ):
        member = _staff("moderator.handler@example.test")

        assert handle_delete(None, manager_user, member["id"])[0] == 403
        assert handle_delete(None, admin_user, partner_user["id"])[0] == 400
        assert handle_delete(None, admin_user, 999999)[0] == 400
        assert handle_delete(None, admin_user, member["id"])[0] == 200

    def test_an_observer_may_not_delete_anybody(self, admin_user):
        observer = _staff("obserwator@example.test", role="observer", name="Olek Obserwator")
        member = _staff("moderator.cel@example.test")

        assert handle_delete(None, observer, member["id"])[0] == 403
        assert _exists(member["id"])


class TestTheProgrammeKeepsAnOwner:
    """Demoting, suspending or erasing an administrator must never leave the
    instance without an active one, and nobody may do it to themselves."""

    @pytest.mark.parametrize("change", [
        {"role": "manager"}, {"role": "partner"},
        {"account_status": "suspended"}, {"account_status": "rejected"},
    ])
    def test_an_admin_cannot_demote_or_close_their_own_account(self, admin_user, change):
        from api.users import update_user

        _staff("druga.ada@example.test", role="admin", name="Druga Ada")
        with pytest.raises(ValueError):
            update_user(admin_user["id"], change, admin_user)
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT role, active FROM users WHERE id = ?", (admin_user["id"],)
            ).fetchone()
        finally:
            conn.close()
        assert (row["role"], row["active"]) == ("admin", 1)

    def test_an_admin_may_still_edit_their_own_profile(self, admin_user):
        from api.users import update_user

        updated = update_user(
            admin_user["id"], {"name": "Nowe Imię", "role": "admin"}, admin_user)
        assert updated["name"] == "Nowe Imię"

    @pytest.mark.parametrize("change", [{"role": "manager"}, {"account_status": "suspended"}])
    def test_the_last_active_admin_cannot_be_demoted_or_closed(self, admin_user, change):
        from api.users import update_user

        other = _staff("jedyny.aktywny@example.test", role="admin", name="Jedyny")
        conn = get_conn()
        try:
            conn.execute("UPDATE users SET active = 0, account_status = 'suspended' "
                         "WHERE id = ?", (admin_user["id"],))
            conn.commit()
        finally:
            conn.close()
        acting = dict(admin_user)  # a session that is still being honoured

        with pytest.raises(ValueError):
            update_user(other["id"], change, acting)

    def test_another_admin_can_be_demoted_while_one_remains(self, admin_user):
        from api.users import update_user

        other = _staff("druga.ada2@example.test", role="admin", name="Druga Ada")
        assert update_user(other["id"], {"role": "manager"}, admin_user)["role"] == "manager"

    def test_gdpr_erasure_refuses_the_callers_own_account(self, admin_user):
        from api import gdpr

        _staff("druga.ada3@example.test", role="admin", name="Druga Ada")
        with pytest.raises(ValueError):
            gdpr.erase_person(admin_user["email"], admin_user, confirm=True)
        assert _exists(admin_user["id"])

    def test_gdpr_erasure_refuses_the_last_active_admin(self, admin_user):
        from api import gdpr

        with pytest.raises(ValueError):
            gdpr.erase_person(admin_user["email"], None, confirm=True)
        conn = get_conn()
        try:
            assert conn.execute(
                "SELECT active FROM users WHERE id = ?", (admin_user["id"],)
            ).fetchone()["active"] == 1
        finally:
            conn.close()

    def test_gdpr_erasure_of_another_admin_works_while_one_remains(self, admin_user):
        from api import gdpr

        other = _staff("druga.ada4@example.test", role="admin", name="Druga Ada")
        assert gdpr.erase_person(other["email"], admin_user, confirm=True)[
            "account_anonymised"] is True


class TestHistoryThatBlocksDeletion:
    def test_an_account_that_confirmed_a_win_is_refused_not_crashed(
        self, admin_user, sample_lead
    ):
        member = _staff("moderator.potwierdzal@example.test")
        conn = get_conn()
        try:
            conn.execute("UPDATE leads SET win_confirmed_by = ? WHERE id = ?",
                         (member["id"], sample_lead["id"]))
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(ValueError, match="potwierdzał wygrane"):
            delete_user(member["id"], admin_user)
        assert _exists(member["id"])

    def test_an_account_that_wrote_comments_is_refused_and_the_comments_survive(
        self, admin_user, sample_lead
    ):
        member = _staff("moderator.komentowal@example.test")
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO lead_comments (lead_id, user_id, body) VALUES (?, ?, 'Decyzja: ok')",
                (sample_lead["id"], member["id"]))
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(ValueError, match="komentarze"):
            delete_user(member["id"], admin_user)
        conn = get_conn()
        try:
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM lead_comments WHERE user_id = ?", (member["id"],)
            ).fetchone()["n"] == 1
        finally:
            conn.close()
