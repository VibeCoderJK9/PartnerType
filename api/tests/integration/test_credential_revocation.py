""""Sign out everywhere" and a completed password reset kill every gateway credential.

The safer default: a stolen
API key, OAuth access token or 30-day refresh token must not survive the two
moments a user takes their account back. Both flows run through the real
router; the credentials are proven dead by what /api/v1, /mcp and
/oauth/token answer afterwards, not only by the rows. Audit entries are
asserted on the autouse mock of `api.audit.log`.
"""
import pytest

import api.audit
from api.db import get_conn
from api.gateway import keys as gateway_keys
from api.password_reset import request_reset
from api.tests.integration import gateway_helpers as gh

ORIGIN = {"Origin": "http://localhost:8000"}


def _credentials(call_api, user):
    """One OAuth connection (the real dance) and one API key for `user`."""
    dance = gh.oauth_dance(gh.client(), call_api, user)
    raw_key = gh.make_key(user, name="Claude Code")
    return {"key": raw_key, **dance}


def _assert_alive(creds):
    client = gh.client()
    assert client.get("/api/v1/leads", headers=gh.bearer(creds["key"])).status_code == 200
    assert gh.mcp_call(client, creds["access_token"], "tools/list").status_code == 200


def _assert_dead(creds):
    client = gh.client()
    assert client.get("/api/v1/leads", headers=gh.bearer(creds["key"])).status_code == 401
    assert gh.mcp_call(client, creds["access_token"], "tools/list").status_code == 401
    response = gh.refresh(client, creds["client_id"], creds["refresh_token"])
    assert response.status_code == 400, response.get_data(as_text=True)
    assert response.get_json()["error"] == "invalid_grant"


def _rows(user_id):
    conn = get_conn()
    try:
        key_rows = conn.execute("SELECT revoked_at FROM api_keys WHERE user_id = ?",
                                (user_id,)).fetchall()
        grant_rows = conn.execute(
            "SELECT revoked_at, revoked_reason FROM oauth_grants WHERE user_id = ?",
            (user_id,)).fetchall()
        live_tokens = conn.execute(
            """SELECT COUNT(*) AS n FROM oauth_tokens t
               JOIN oauth_grants g ON g.id = t.grant_id
               WHERE g.user_id = ? AND t.revoked_at IS NULL""",
            (user_id,)).fetchone()["n"]
    finally:
        conn.close()
    return [dict(r) for r in key_rows], [dict(r) for r in grant_rows], live_tokens


def _revocation_audits():
    return [c for c in api.audit.log.call_args_list
            if c.args[1] == "integration.credentials_revoked"]


def _complete_reset_via_router(call_api, user, mocker, password="Nowe-Haslo-123!"):
    mocker.patch("api.email_service.send_password_reset")
    token = request_reset(user["email"])
    return call_api("POST", f"/api/auth/password-reset/{token}",
                    body={"password": password}, headers=ORIGIN)


# ─── "Sign out everywhere" ──────────────────────────────────────────────────

def test_logout_everywhere_revokes_key_access_and_refresh_tokens(
        call_api, partner_user, manager_user):
    creds = _credentials(call_api, partner_user)
    other_key = gh.make_key(manager_user, name="cudzy")
    _assert_alive(creds)
    session = gh.session_token(call_api, partner_user)

    status, body = call_api("POST", "/api/auth/logout-all", token=session, headers=ORIGIN)

    assert status == 200, body
    _assert_dead(creds)
    key_rows, grant_rows, live_tokens = _rows(partner_user["id"])
    assert [r["revoked_at"] is not None for r in key_rows] == [True]
    assert [(r["revoked_at"] is not None, r["revoked_reason"]) for r in grant_rows] == [
        (True, "logout_everywhere")]
    assert live_tokens == 0
    assert gh.client().get("/api/v1/leads", headers=gh.bearer(other_key)).status_code == 200

    entries = _revocation_audits()
    assert len(entries) == 1
    assert entries[0].args[0]["id"] == partner_user["id"]
    assert entries[0].args[4] == "klucze: 1, połączenia: 1"


def test_logout_everywhere_without_credentials_behaves_as_before(call_api, partner_user):
    session = gh.session_token(call_api, partner_user)

    status, body = call_api("POST", "/api/auth/logout-all", token=session, headers=ORIGIN)

    assert status == 200 and body["ok"] is True
    actions = [c.args[1] for c in api.audit.log.call_args_list]
    assert "user.logout_all" in actions
    assert not _revocation_audits()


def test_logout_everywhere_revocation_is_in_the_session_transaction(
        call_api, partner_user, mocker):
    from api.auth import get_session_user, logout_everywhere
    creds = _credentials(call_api, partner_user)
    session = gh.session_token(call_api, partner_user)
    real = gateway_keys.revoke_all_for_user

    def revoke_then_fail(conn, user_id, reason):
        real(conn, user_id, reason)
        raise RuntimeError("boom after revocation, before commit")

    mocker.patch("api.gateway.keys.revoke_all_for_user", side_effect=revoke_then_fail)
    with pytest.raises(RuntimeError):
        logout_everywhere(partner_user["id"])

    _assert_alive(creds)
    assert get_session_user(session) is not None


# ─── completed password reset ────────────────────────────────────────────────

def test_completed_password_reset_revokes_every_credential(
        call_api, partner_user, manager_user, mocker):
    creds = _credentials(call_api, partner_user)
    other_key = gh.make_key(manager_user, name="cudzy")
    _assert_alive(creds)

    status, body = _complete_reset_via_router(call_api, partner_user, mocker)

    assert status == 200, body
    _assert_dead(creds)
    key_rows, grant_rows, live_tokens = _rows(partner_user["id"])
    assert all(r["revoked_at"] is not None for r in key_rows)
    assert [r["revoked_reason"] for r in grant_rows] == ["password_reset"]
    assert live_tokens == 0
    assert gh.client().get("/api/v1/leads", headers=gh.bearer(other_key)).status_code == 200

    entries = _revocation_audits()
    assert len(entries) == 1
    assert entries[0].args[0] is None
    assert entries[0].args[3] == str(partner_user["id"])
    assert entries[0].args[4] == "klucze: 1, połączenia: 1"


def test_failed_reset_revokes_nothing(call_api, partner_user):
    creds = _credentials(call_api, partner_user)

    status, body = call_api("POST",
                            "/api/auth/password-reset/zmyslony-token-ktory-nie-istnieje",
                            body={"password": "Nowe-Haslo-123!"}, headers=ORIGIN)

    assert status == 400, body
    _assert_alive(creds)
    key_rows, grant_rows, live_tokens = _rows(partner_user["id"])
    assert all(r["revoked_at"] is None for r in key_rows)
    assert all(r["revoked_at"] is None for r in grant_rows)
    assert live_tokens == 2
    assert not _revocation_audits()


def test_reset_that_raises_before_commit_keeps_the_credentials(
        call_api, partner_user, mocker):
    from api.password_reset import complete_reset
    creds = _credentials(call_api, partner_user)
    mocker.patch("api.email_service.send_password_reset")
    token = request_reset(partner_user["email"])
    real = gateway_keys.revoke_all_for_user

    def revoke_then_fail(conn, user_id, reason):
        real(conn, user_id, reason)
        raise RuntimeError("boom after revocation, before commit")

    mocker.patch("api.gateway.keys.revoke_all_for_user", side_effect=revoke_then_fail)
    with pytest.raises(RuntimeError):
        complete_reset(token, "Nowe-Haslo-123!")

    _assert_alive(creds)
    assert not _revocation_audits()


def test_completed_reset_without_credentials_writes_no_revocation_audit(
        call_api, partner_user, mocker):
    status, body = _complete_reset_via_router(call_api, partner_user, mocker)

    assert status == 200, body
    actions = [c.args[1] for c in api.audit.log.call_args_list]
    assert "user.password_reset" in actions
    assert not _revocation_audits()


# ─── password change ──────────────────────────────────────────

def test_own_password_change_revokes_every_credential(call_api, partner_user, manager_user):
    creds = _credentials(call_api, partner_user)
    other_key = gh.make_key(manager_user, name="cudzy")
    _assert_alive(creds)
    session = gh.session_token(call_api, partner_user)

    status, body = call_api("POST", f"/api/users/{partner_user['id']}/password",
                            body={"current_password": partner_user["plain_password"],
                                  "new_password": "Nowe-Haslo-123!"},
                            token=session, headers=ORIGIN)

    assert status == 200, body
    _assert_dead(creds)
    key_rows, grant_rows, live_tokens = _rows(partner_user["id"])
    assert all(r["revoked_at"] is not None for r in key_rows)
    assert [r["revoked_reason"] for r in grant_rows] == ["password_change"]
    assert live_tokens == 0
    assert gh.client().get("/api/v1/leads", headers=gh.bearer(other_key)).status_code == 200

    entries = _revocation_audits()
    assert len(entries) == 1
    assert entries[0].args[0]["id"] == partner_user["id"]
    assert entries[0].args[3] == str(partner_user["id"])
    assert entries[0].args[4] == "klucze: 1, połączenia: 1"


def test_admin_setting_a_new_password_revokes_the_targets_credentials(
        call_api, admin_user, manager_user):
    creds = _credentials(call_api, manager_user)
    admin_key = gh.make_key(admin_user, name="administrator")
    _assert_alive(creds)
    session = gh.session_token(call_api, admin_user)

    status, body = call_api("POST", f"/api/users/{manager_user['id']}/password",
                            body={"new_password": "Nowe-Haslo-123!"},
                            token=session, headers=ORIGIN)

    assert status == 200, body
    _assert_dead(creds)
    _key_rows, grant_rows, live_tokens = _rows(manager_user["id"])
    assert [r["revoked_reason"] for r in grant_rows] == ["password_change"]
    assert live_tokens == 0
    assert gh.client().get("/api/v1/leads", headers=gh.bearer(admin_key)).status_code == 200

    entries = _revocation_audits()
    assert len(entries) == 1
    assert entries[0].args[0]["id"] == admin_user["id"]
    assert entries[0].args[3] == str(manager_user["id"])


def test_rejected_password_change_revokes_nothing(call_api, partner_user):
    creds = _credentials(call_api, partner_user)
    session = gh.session_token(call_api, partner_user)

    status, body = call_api("POST", f"/api/users/{partner_user['id']}/password",
                            body={"current_password": "Zle-Haslo-999!",
                                  "new_password": "Nowe-Haslo-123!"},
                            token=session, headers=ORIGIN)

    assert status == 400, body
    _assert_alive(creds)
    assert not _revocation_audits()


def test_password_change_revocation_is_in_the_password_transaction(
        call_api, partner_user, mocker):
    from api.users import change_password
    creds = _credentials(call_api, partner_user)
    real = gateway_keys.revoke_all_for_user

    def revoke_then_fail(conn, user_id, reason):
        real(conn, user_id, reason)
        raise RuntimeError("boom after revocation, before commit")

    mocker.patch("api.gateway.keys.revoke_all_for_user", side_effect=revoke_then_fail)
    with pytest.raises(RuntimeError):
        change_password(partner_user["id"], partner_user["plain_password"],
                        "Nowe-Haslo-123!", partner_user)

    _assert_alive(creds)
    assert not _revocation_audits()
