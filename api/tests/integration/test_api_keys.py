"""API-04: keys, the bearer authenticator's negative matrix, OAuth access tokens.

Every refusal is driven through the real Flask app so the status, the
`WWW-Authenticate` challenge and the protocol error code are what a client
actually receives. Audit writes are muted globally (conftest), so nothing here
asserts against audit_log rows.
"""
import pytest

from api import auth
from api.db import get_conn
from api.gateway import keys, tokens
from api.tests.integration import gateway_helpers as gh

V1 = gh.BASE + "/api/v1"
MCP = gh.BASE + "/mcp"


def _get(raw=None, path="/api/v1/leads", headers=None):
    request_headers = dict(headers or {})
    if raw is not None:
        request_headers.update(gh.bearer(raw))
    return gh.client().get(path, headers=request_headers)


def _assert_invalid_token(response):
    assert response.status_code == 401, response.get_data(as_text=True)
    assert response.get_json()["error"] == "invalid_token"
    challenge = response.headers["WWW-Authenticate"]
    assert 'error="invalid_token"' in challenge
    assert "resource_metadata=" in challenge


def _assert_no_credential(response):
    assert response.status_code == 401, response.get_data(as_text=True)
    challenge = response.headers["WWW-Authenticate"]
    assert challenge.startswith("Bearer ")
    assert "error=" not in challenge
    assert "resource_metadata=" in challenge


def _assert_forbidden(response, code):
    assert response.status_code == 403, response.get_data(as_text=True)
    assert response.get_json()["error"] == code


# ─── Key storage ────────────────────────────────────────────────────────────

def test_raw_key_is_returned_once_and_never_stored(admin_user):
    gh.set_integration_settings(enabled=True)

    created = keys.create_key(admin_user, "Claude Code")
    raw = created["key"]

    assert raw.startswith("ptk_")
    assert created["display_prefix"] == raw[:12]
    conn = get_conn()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM api_keys").fetchall()]
    finally:
        conn.close()
    assert len(rows) == 1
    assert raw not in [str(v) for v in rows[0].values()]
    assert rows[0]["token_hash"] == tokens.token_hash(raw)
    listed = keys.list_keys(admin_user["id"])
    assert listed and all("token_hash" not in item and "key" not in item for item in listed)
    assert raw not in repr(listed)


@pytest.mark.parametrize("days", [0, 7, 91, 366, None, "90", True])
def test_expiry_outside_the_three_choices_is_refused(admin_user, days):
    gh.set_integration_settings(enabled=True)

    with pytest.raises(ValueError, match="30, 90 albo 365"):
        keys.create_key(admin_user, "k", days)


@pytest.mark.parametrize("days", [30, 90, 365])
def test_three_expiry_choices_are_accepted(admin_user, days):
    gh.set_integration_settings(enabled=True)

    created = keys.create_key(admin_user, "k", days)

    assert (created["expires_at"] - created["created_at"]).days == days


def test_default_expiry_is_90_days(admin_user):
    gh.set_integration_settings(enabled=True)

    created = keys.create_key(admin_user, "k")

    assert (created["expires_at"] - created["created_at"]).days == 90


@pytest.mark.parametrize("name", ["", "   ", "x" * 101, "zła\nnazwa", None])
def test_invalid_key_name_is_refused(admin_user, name):
    gh.set_integration_settings(enabled=True)

    with pytest.raises(ValueError):
        keys.create_key(admin_user, name)


def test_key_cannot_be_created_while_integrations_are_off(admin_user):
    gh.set_integration_settings(enabled=False)

    with pytest.raises(PermissionError):
        keys.create_key(admin_user, "k")


def test_revoke_only_matches_own_key_unless_admin_path(admin_user, manager_user):
    gh.set_integration_settings(enabled=True)
    created = keys.create_key(admin_user, "k")

    assert keys.revoke_key(manager_user, created["id"]) is False
    assert keys.revoke_key(manager_user, created["id"], any_owner=True) is True
    assert keys.revoke_key(admin_user, created["id"]) is False  # already revoked


def test_live_keys_per_user_are_capped(admin_user, manager_user):
    gh.set_integration_settings(enabled=True)
    created = [keys.create_key(admin_user, f"k{i}") for i in range(keys.MAX_LIVE_KEYS_PER_USER)]

    with pytest.raises(ValueError):
        keys.create_key(admin_user, "one too many")
    # Another user is not affected.
    keys.create_key(manager_user, "own")

    # Revoked and expired keys free their slot.
    assert keys.revoke_key(admin_user, created[0]["id"]) is True
    keys.create_key(admin_user, "after revoke")
    with get_conn() as conn:
        conn.execute("""UPDATE api_keys SET created_at = now() - INTERVAL '2 days',
                                            expires_at = now() - INTERVAL '1 day'
                        WHERE id = ?""", (created[1]["id"],))
    keys.create_key(admin_user, "after expiry")
    with pytest.raises(ValueError):
        keys.create_key(admin_user, "still capped")


def test_key_cap_is_a_400_on_the_integrations_api(admin_user, call_api):
    gh.set_integration_settings(enabled=True)
    for i in range(keys.MAX_LIVE_KEYS_PER_USER):
        keys.create_key(admin_user, f"k{i}")
    session = gh.session_token(call_api, admin_user)

    status, body = call_api("POST", "/api/integrations/keys", body={"name": "extra"},
                            token=session, headers={"Origin": gh.BASE})

    assert status == 400, body
    assert "error" in body


# ─── Invalid credentials -> 401 ─────────────────────────────────────────────

def test_revoked_key_gets_401(admin_user):
    gh.set_integration_settings(enabled=True)
    created = keys.create_key(admin_user, "k")
    assert _get(created["key"]).status_code == 200

    keys.revoke_key(admin_user, created["id"])

    _assert_invalid_token(_get(created["key"]))


def test_expired_key_gets_401(admin_user):
    gh.set_integration_settings(enabled=True)
    created = keys.create_key(admin_user, "k")
    with get_conn() as conn:
        conn.execute(
            """UPDATE api_keys SET created_at = now() - INTERVAL '2 days',
                                   expires_at = now() - INTERVAL '1 second'
               WHERE id = ?""", (created["id"],))

    _assert_invalid_token(_get(created["key"]))


def test_unknown_key_gets_401(admin_user):
    gh.set_integration_settings(enabled=True)

    _assert_invalid_token(_get(tokens.new_token(tokens.KEY_PREFIX)))


@pytest.mark.parametrize("value", [
    "Bearer ptk_abc ptk_def",
    "Bearer  ptk_abc",
    "Basic dXNlcjpwYXNz",
    "Bearer ",
    "Bearer",
    "",
    "ptk_abc",
])
def test_malformed_authorization_header_gets_401_without_error_code(admin_user, value):
    gh.set_integration_settings(enabled=True)

    _assert_no_credential(_get(headers={"Authorization": value}))


def test_key_in_query_string_is_ignored(admin_user):
    gh.set_integration_settings(enabled=True)
    raw = gh.make_key(admin_user)

    _assert_no_credential(_get(path=f"/api/v1/leads?access_token={raw}"))


def test_deactivated_owner_gets_401(admin_user):
    gh.set_integration_settings(enabled=True)
    raw = gh.make_key(admin_user)

    gh.deactivate(admin_user)

    _assert_invalid_token(_get(raw))


# ─── Valid credential, refused call -> 403 ──────────────────────────────────

def test_integrations_off_gets_403_and_keeps_the_key(admin_user):
    gh.set_integration_settings(enabled=True)
    raw = gh.make_key(admin_user)

    gh.set_integration_settings(enabled=False)

    _assert_forbidden(_get(raw), "integrations_disabled")
    conn = get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM api_keys").fetchone()["n"] == 1
    finally:
        conn.close()


def test_partner_key_refused_while_partner_switch_is_off(admin_user, partner_user):
    gh.set_integration_settings(enabled=True, partners=True)
    raw = gh.make_key(partner_user)

    gh.set_integration_settings(enabled=True, partners=False)

    _assert_forbidden(_get(raw), "partners_disabled")


def test_partner_key_needs_both_switches(admin_user, partner_user):
    gh.set_integration_settings(enabled=True, partners=True)
    raw = gh.make_key(partner_user)

    gh.set_integration_settings(enabled=False, partners=True)

    _assert_forbidden(_get(raw), "integrations_disabled")


def test_partner_cannot_create_key_while_partner_switch_is_off(partner_user):
    gh.set_integration_settings(enabled=True, partners=False)

    with pytest.raises(PermissionError):
        keys.create_key(partner_user, "k")


def test_required_mfa_without_enrolment_gets_403(admin_user, monkeypatch):
    gh.set_integration_settings(enabled=True)
    raw = gh.make_key(admin_user)

    monkeypatch.setenv("PARTNERTYPE_REQUIRE_MFA", "1")

    _assert_forbidden(_get(raw), "mfa_setup_required")


def test_key_without_read_scope_gets_403(admin_user):
    gh.set_integration_settings(enabled=True)
    created = keys.create_key(admin_user, "k")
    with get_conn() as conn:
        conn.execute("UPDATE api_keys SET scopes = 'propose' WHERE id = ?", (created["id"],))

    _assert_forbidden(_get(created["key"]), "insufficient_scope")


def test_observer_key_reads_leads(admin_user, partner_user):
    gh.set_integration_settings(enabled=True)
    observer = gh.create_user("observer", "observer.gateway@test.pl")
    gh.make_lead(partner_user)

    response = _get(gh.make_key(observer))

    assert response.status_code == 200
    assert response.get_json()["total"] == 1


# ─── Doors stay separate ─────────────────────────────────────────────

def test_valid_key_on_legacy_api_is_ignored(admin_user, call_api):
    gh.set_integration_settings(enabled=True)
    raw = gh.make_key(admin_user)

    status, body = call_api("GET", "/api/leads", headers={"Authorization": f"Bearer {raw}"})

    assert status == 401


def test_valid_session_cookie_on_v1_is_ignored(admin_user):
    gh.set_integration_settings(enabled=True)
    _user, session = auth.login(admin_user["email"], admin_user["plain_password"], "producent")
    client = gh.client()
    # Through the client's cookie jar: Werkzeug's test client drops a
    # hand-written Cookie header in favour of its jar.
    client.set_cookie("ph_test_test_session", session)
    # Control: the cookie really is a live session on the app's own API.
    assert client.get("/api/leads").status_code == 200

    _assert_no_credential(client.get("/api/v1/leads"))


# ─── OAuth access tokens (pta_) ─────────────────────────────────────────────

def test_oauth_access_token_for_v1_reads_leads(admin_user):
    gh.set_integration_settings(enabled=True)
    raw = gh.insert_oauth_access(admin_user, V1)

    response = _get(raw)

    assert response.status_code == 200, response.get_data(as_text=True)
    assert "untrusted_data" in response.get_json()


def test_access_token_of_revoked_grant_gets_401(admin_user):
    gh.set_integration_settings(enabled=True)

    _assert_invalid_token(_get(gh.insert_oauth_access(admin_user, V1, revoked=True)))


def test_expired_access_token_gets_401(admin_user):
    gh.set_integration_settings(enabled=True)

    _assert_invalid_token(_get(gh.insert_oauth_access(admin_user, V1, expires_in_seconds=-5)))


def test_access_token_for_another_resource_gets_401(admin_user):
    gh.set_integration_settings(enabled=True)

    _assert_invalid_token(_get(gh.insert_oauth_access(admin_user, MCP)))


def test_refresh_token_is_not_a_bearer_credential(admin_user):
    gh.set_integration_settings(enabled=True)

    _assert_invalid_token(_get(gh.insert_oauth_access(admin_user, V1, kind="refresh")))


def test_access_token_of_deactivated_user_gets_401(admin_user):
    gh.set_integration_settings(enabled=True)
    raw = gh.insert_oauth_access(admin_user, V1)

    gh.deactivate(admin_user)

    _assert_invalid_token(_get(raw))


def test_access_token_refused_while_integrations_are_off(admin_user):
    gh.set_integration_settings(enabled=False)

    _assert_forbidden(_get(gh.insert_oauth_access(admin_user, V1)), "integrations_disabled")


# ─── last_used_at throttling ────────────────────────────────────────────────

def _key_last_used(key_id):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT last_used_at FROM api_keys WHERE id = ?", (key_id,)).fetchone()["last_used_at"]
    finally:
        conn.close()


def test_key_last_used_is_set_once_per_minute(admin_user):
    gh.set_integration_settings(enabled=True)
    created = keys.create_key(admin_user, "k")
    assert _key_last_used(created["id"]) is None

    assert _get(created["key"]).status_code == 200
    first = _key_last_used(created["id"])
    assert _get(created["key"]).status_code == 200

    assert first is not None
    assert _key_last_used(created["id"]) == first


def test_grant_last_used_is_set_once_per_minute(admin_user):
    gh.set_integration_settings(enabled=True)
    raw = gh.insert_oauth_access(admin_user, V1)

    assert _get(raw).status_code == 200
    first = gh.grant_last_used(raw)
    assert _get(raw).status_code == 200

    assert first is not None
    assert gh.grant_last_used(raw) == first
