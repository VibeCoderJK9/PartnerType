"""/api/integrations/* -- the app's own control surface for the gateway.

Requests go through the real legacy router (`call_api`): session cookie,
Origin check and MFA gate included. Gateway effects are checked through the
real Flask app (`gh.client()`), so a switch flipped here is proven by what
/api/v1 and /mcp actually answer afterwards. Audit entries are asserted on the
autouse mock of `api.audit.log` (api/tests/conftest.py), never on rows.
"""
import json

import pytest

import api.audit
from api.tests.integration import gateway_helpers as gh

ORIGIN = {"Origin": "http://localhost:8000"}
ME_KEYS = {"can_connect", "refusal_code", "mcp_url", "keys", "connections"}
KEY_VIEW_KEYS = {"id", "name", "display_prefix", "scopes", "created_at", "expires_at",
                 "last_used_at", "revoked_at"}
CONNECTION_VIEW_KEYS = {"id", "client_name", "redirect_host", "created_at", "last_used_at",
                        "revoked_at"}


def _actions():
    return [call.args[1] for call in api.audit.log.call_args_list]


def test_tracer_admin_enables_creates_key_once_and_the_key_reads_api_v1(
        call_api, admin_user, partner_user):
    gh.make_lead(partner_user)
    session = gh.session_token(call_api, admin_user)

    status, body = call_api("PATCH", "/api/integrations/settings", token=session,
                            body={"enabled": True, "acknowledge_eu_transfer": True},
                            headers=ORIGIN)
    assert status == 200, body
    assert body["enabled"] is True
    assert body["mcp_url"] == "http://localhost:8000/mcp"
    assert "integration.enabled" in _actions()

    status, created = call_api("POST", "/api/integrations/keys", token=session,
                               body={"name": "Claude Code", "expires_in_days": 90},
                               headers=ORIGIN)
    assert status == 201, created
    raw = created["key"]
    assert raw.startswith("ptk_")
    assert "integration.key_created" in _actions()
    key_audit = [c for c in api.audit.log.call_args_list
                 if c.args[1] == "integration.key_created"][0]
    assert raw not in json.dumps([str(a) for a in key_audit.args])

    response = gh.client().get("/api/v1/leads", headers=gh.bearer(raw))
    assert response.status_code == 200, response.get_data(as_text=True)

    status, me = call_api("GET", "/api/integrations/me", token=session)
    assert status == 200, me
    assert set(me) == ME_KEYS
    assert me["can_connect"] is True and me["refusal_code"] is None
    assert [k["display_prefix"] for k in me["keys"]] == [created["display_prefix"]]
    for item in me["keys"]:
        assert set(item) == KEY_VIEW_KEYS
    assert raw not in json.dumps(me)

    dance = gh.oauth_dance(gh.client(), call_api, admin_user)
    status, me = call_api("GET", "/api/integrations/me", token=session)
    assert status == 200, me
    assert len(me["connections"]) == 1
    connection = me["connections"][0]
    assert set(connection) == CONNECTION_VIEW_KEYS
    assert connection["id"] == dance["grant_id"]
    assert connection["redirect_host"] == "claude.ai"
    assert connection["client_name"] == "Claude"
    assert dance["access_token"] not in json.dumps(me)
    assert dance["refresh_token"] not in json.dumps(me)


# ─── helpers for the role, revocation and overview tests ─────────────────────

def _observer():
    user = gh.create_user("observer", "observer.integrations@test.pl")
    user["plain_password"] = "GatewayPass123!"
    return user


def _all_keys(value):
    if isinstance(value, dict):
        for name, item in value.items():
            yield name
            yield from _all_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_keys(item)


def _key_id(raw):
    from api.db import get_conn
    from api.gateway import tokens
    conn = get_conn()
    try:
        return conn.execute("SELECT id FROM api_keys WHERE token_hash = ?",
                            (tokens.token_hash(raw),)).fetchone()["id"]
    finally:
        conn.close()


def _mcp_status(raw):
    return gh.mcp_call(gh.client(), raw, "tools/list").status_code


# ─── key creation ───────────────────────────────────────────────

def test_key_creation_is_refused_while_integrations_are_off(call_api, admin_user):
    session = gh.session_token(call_api, admin_user)

    status, body = call_api("POST", "/api/integrations/keys", token=session,
                            body={"name": "Claude", "expires_in_days": 90}, headers=ORIGIN)

    assert status == 403, body
    assert gh.count_rows("api_keys") == 0


def test_partner_key_follows_the_partners_switch(call_api, admin_user, partner_user):
    gh.set_integration_settings(enabled=True, partners=False)
    session = gh.session_token(call_api, partner_user)
    request = {"name": "Mój Claude", "expires_in_days": 30}

    status, body = call_api("POST", "/api/integrations/keys", token=session,
                            body=request, headers=ORIGIN)
    assert status == 403, body

    gh.set_integration_settings(enabled=True, partners=True)
    status, body = call_api("POST", "/api/integrations/keys", token=session,
                            body=request, headers=ORIGIN)
    assert status == 201, body
    assert body["key"].startswith("ptk_")


def test_key_expiry_outside_the_choices_is_400(call_api, admin_user):
    gh.set_integration_settings(enabled=True)
    session = gh.session_token(call_api, admin_user)

    status, body = call_api("POST", "/api/integrations/keys", token=session,
                            body={"name": "Claude", "expires_in_days": 7}, headers=ORIGIN)

    assert status == 400, body
    assert gh.count_rows("api_keys") == 0


# ─── key revocation ──────────────────────────

def test_manager_cannot_revoke_someone_elses_key(call_api, admin_user, manager_user):
    gh.set_integration_settings(enabled=True)
    raw = gh.make_key(admin_user)
    session = gh.session_token(call_api, manager_user)

    status, body = call_api("DELETE", f"/api/integrations/keys/{_key_id(raw)}", token=session)

    assert status == 404, body
    assert body["error"] == "Nie znaleziono klucza."
    assert gh.client().get("/api/v1/leads", headers=gh.bearer(raw)).status_code == 200
    assert "integration.key_revoked" not in _actions()


def test_owner_revokes_own_key_and_the_next_call_is_401(call_api, manager_user):
    gh.set_integration_settings(enabled=True)
    raw = gh.make_key(manager_user)
    assert gh.client().get("/api/v1/leads", headers=gh.bearer(raw)).status_code == 200
    session = gh.session_token(call_api, manager_user)

    status, body = call_api("DELETE", f"/api/integrations/keys/{_key_id(raw)}", token=session)

    assert status == 200, body
    assert gh.client().get("/api/v1/leads", headers=gh.bearer(raw)).status_code == 401
    assert "integration.key_revoked" in _actions()


def test_admin_revokes_anyones_key_with_audit(call_api, admin_user, manager_user):
    gh.set_integration_settings(enabled=True)
    raw = gh.make_key(manager_user)
    session = gh.session_token(call_api, admin_user)

    status, body = call_api("DELETE", f"/api/integrations/keys/{_key_id(raw)}", token=session)

    assert status == 200, body
    assert gh.client().get("/api/v1/leads", headers=gh.bearer(raw)).status_code == 401
    entry = [c for c in api.audit.log.call_args_list if c.args[1] == "integration.key_revoked"]
    assert len(entry) == 1
    assert entry[0].args[0]["id"] == admin_user["id"]
    assert raw not in json.dumps([str(a) for a in entry[0].args])


def test_revoking_an_unknown_or_already_revoked_key_is_404(call_api, admin_user):
    gh.set_integration_settings(enabled=True)
    raw = gh.make_key(admin_user)
    session = gh.session_token(call_api, admin_user)
    key_id = _key_id(raw)

    assert call_api("DELETE", f"/api/integrations/keys/{key_id}", token=session)[0] == 200
    assert call_api("DELETE", f"/api/integrations/keys/{key_id}", token=session)[0] == 404
    assert call_api("DELETE", "/api/integrations/keys/999999", token=session)[0] == 404


# ─── OAuth connection revocation ────────────────────────────────

def test_owner_revokes_own_connection_and_its_token_is_401_on_mcp(call_api, partner_user):
    dance = gh.oauth_dance(gh.client(), call_api, partner_user)
    assert _mcp_status(dance["access_token"]) == 200
    session = gh.session_token(call_api, partner_user)

    status, body = call_api("DELETE", f"/api/integrations/connections/{dance['grant_id']}",
                            token=session)

    assert status == 200, body
    assert _mcp_status(dance["access_token"]) == 401
    assert "integration.connection_revoked" in _actions()


def test_other_non_admin_gets_404_for_a_connection(call_api, partner_user, manager_user):
    dance = gh.oauth_dance(gh.client(), call_api, partner_user)
    session = gh.session_token(call_api, manager_user)

    status, body = call_api("DELETE", f"/api/integrations/connections/{dance['grant_id']}",
                            token=session)

    assert status == 404, body
    assert _mcp_status(dance["access_token"]) == 200
    assert "integration.connection_revoked" not in _actions()


def test_admin_revokes_any_connection_with_audit(call_api, admin_user, partner_user):
    dance = gh.oauth_dance(gh.client(), call_api, partner_user)
    session = gh.session_token(call_api, admin_user)

    status, body = call_api("DELETE", f"/api/integrations/connections/{dance['grant_id']}",
                            token=session)

    assert status == 200, body
    assert _mcp_status(dance["access_token"]) == 401
    entry = [c for c in api.audit.log.call_args_list
             if c.args[1] == "integration.connection_revoked"]
    assert len(entry) == 1
    assert entry[0].args[4] == "Claude"
    from api.db import get_conn
    conn = get_conn()
    try:
        reason = conn.execute("SELECT revoked_reason FROM oauth_grants WHERE id = ?",
                              (dance["grant_id"],)).fetchone()["revoked_reason"]
    finally:
        conn.close()
    assert reason == "admin_revoked"


# ─── the administrator's overview ────────

def test_admin_overview_is_empty_arrays_when_nothing_exists(call_api, admin_user):
    session = gh.session_token(call_api, admin_user)

    status, body = call_api("GET", "/api/integrations/admin/connections", token=session)

    assert status == 200, body
    assert body == {"keys": [], "connections": []}


def test_admin_overview_lists_everything_newest_first_without_secrets(
        call_api, admin_user, partner_user):
    from api.db import get_conn
    gh.set_integration_settings(enabled=True, partners=True)
    older = gh.make_key(partner_user, name="starszy")
    newer = gh.make_key(admin_user, name="nowszy")
    with get_conn() as conn:
        conn.execute("UPDATE api_keys SET created_at = now() - INTERVAL '1 second' WHERE id = ?",
                     (_key_id(older),))
    dance = gh.oauth_dance(gh.client(), call_api, partner_user)
    session = gh.session_token(call_api, admin_user)

    status, body = call_api("GET", "/api/integrations/admin/connections", token=session)

    assert status == 200, body
    assert [k["name"] for k in body["keys"]] == ["nowszy", "starszy"]
    assert body["keys"][1]["user_name"] == partner_user["name"]
    assert body["keys"][1]["user_role"] == "partner"
    assert body["keys"][0]["user_role"] == "admin"
    assert len(body["connections"]) == 1
    connection = body["connections"][0]
    assert connection["id"] == dance["grant_id"]
    assert connection["client_name"] == "Claude"
    assert connection["redirect_hosts"] == ["claude.ai"]
    assert connection["user_name"] == partner_user["name"]
    assert connection["user_role"] == "partner"
    for name in ("created_at", "last_used_at", "revoked_at"):
        assert name in connection
    assert not {"token_hash", "key", "code"} & set(_all_keys(body))
    text = json.dumps(body)
    for secret in (older, newer, dance["access_token"], dance["refresh_token"]):
        assert secret not in text


def test_observer_gets_403_on_the_admin_overview(call_api, admin_user):
    session = gh.session_token(call_api, _observer())

    for path in ("/api/integrations/admin/connections", "/api/integrations/admin/log",
                 "/api/integrations/admin/usage"):
        status, body = call_api("GET", path, token=session)
        assert status == 403, (path, body)


# ─── call log and usage read-outs ─────────────────────────────

def test_admin_log_lists_gateway_calls_newest_first(call_api, admin_user):
    gh.set_integration_settings(enabled=True)
    raw = gh.make_key(admin_user)
    for path in ("/api/v1/leads", "/api/v1/products", "/api/v1/stats"):
        assert gh.client().get(path, headers=gh.bearer(raw)).status_code == 200
    session = gh.session_token(call_api, admin_user)

    status, body = call_api("GET", "/api/integrations/admin/log", token=session)

    assert status == 200, body
    calls = body["calls"]
    assert len(calls) == 3
    assert [c["id"] for c in calls] == sorted((c["id"] for c in calls), reverse=True)
    assert all(c["user_name"] == admin_user["name"] for c in calls)
    assert all(c["operation"] for c in calls)
    assert calls[0]["connection_kind"] == "key"

    status, body = call_api("GET", "/api/integrations/admin/log?limit=1", token=session)
    assert status == 200 and len(body["calls"]) == 1


@pytest.mark.parametrize("query", ["limit=1000", "limit=0", "limit=abc", "offset=-1",
                                   "limit=5&limit=6"])
def test_admin_log_rejects_bad_query_values(call_api, admin_user, query):
    session = gh.session_token(call_api, admin_user)

    status, body = call_api("GET", f"/api/integrations/admin/log?{query}", token=session)

    assert status == 400, body


def test_admin_usage_has_rows_per_connection_and_day(call_api, admin_user):
    gh.set_integration_settings(enabled=True)
    raw = gh.make_key(admin_user, name="Claude Code")
    for _ in range(2):
        assert gh.client().get("/api/v1/leads", headers=gh.bearer(raw)).status_code == 200
    session = gh.session_token(call_api, admin_user)

    status, body = call_api("GET", "/api/integrations/admin/usage?days=30", token=session)

    assert status == 200, body
    assert len(body["usage"]) == 1
    row = body["usage"][0]
    assert set(row) == {"day", "connection_kind", "connection_id", "label", "calls",
                        "denied", "records"}
    assert row["connection_kind"] == "key"
    assert row["connection_id"] == _key_id(raw)
    assert row["calls"] == 2
    assert "Claude Code" in row["label"]

    for query in ("days=0", "days=401", "days=x"):
        assert call_api("GET", f"/api/integrations/admin/usage?{query}",
                        token=session)[0] == 400


def test_manager_gets_403_on_log_and_usage(call_api, manager_user):
    session = gh.session_token(call_api, manager_user)

    assert call_api("GET", "/api/integrations/admin/log", token=session)[0] == 403
    assert call_api("GET", "/api/integrations/admin/usage", token=session)[0] == 403


# ─── /me for a partner while partners may not connect ───────────

def test_me_for_partner_while_partners_are_off(call_api, admin_user, partner_user):
    gh.set_integration_settings(enabled=True, partners=False)
    session = gh.session_token(call_api, partner_user)

    status, body = call_api("GET", "/api/integrations/me", token=session)

    assert status == 200, body
    assert set(body) == ME_KEYS
    assert body["can_connect"] is False
    assert body["refusal_code"] == "partners_disabled"
    assert body["keys"] == [] and body["connections"] == []


# ─── the host shown is the one the user approved ─────────────────────────────

def _two_uri_connection(call_api, user):
    """A client registers claude.ai first but the consent uses its second URI."""
    collector = "https://claude.com/api/mcp/auth_callback"
    gh.consent_code(gh.client(), call_api, user, redirect_uri=collector,
                    registered=(gh.CLAUDE_CALLBACK, collector))


def test_own_connection_shows_the_approved_redirect_host(call_api, admin_user):
    _two_uri_connection(call_api, admin_user)
    session = gh.session_token(call_api, admin_user)

    status, me = call_api("GET", "/api/integrations/me", token=session)

    assert status == 200, me
    assert [c["redirect_host"] for c in me["connections"]] == ["claude.com"]


def test_admin_overview_shows_the_approved_redirect_host(call_api, admin_user):
    _two_uri_connection(call_api, admin_user)
    session = gh.session_token(call_api, admin_user)

    status, body = call_api("GET", "/api/integrations/admin/connections", token=session)

    assert status == 200, body
    assert [c["redirect_hosts"] for c in body["connections"]] == [["claude.com"]]


def test_a_grant_without_a_stored_redirect_lists_every_registered_host(call_api, admin_user):
    from api.db import get_conn

    _two_uri_connection(call_api, admin_user)
    with get_conn() as conn:
        conn.execute("UPDATE oauth_grants SET redirect_uri = NULL")
    session = gh.session_token(call_api, admin_user)

    status, me = call_api("GET", "/api/integrations/me", token=session)
    assert status == 200, me
    assert me["connections"][0]["redirect_host"] == "claude.ai, claude.com"
    status, body = call_api("GET", "/api/integrations/admin/connections", token=session)
    assert status == 200, body
    assert body["connections"][0]["redirect_hosts"] == ["claude.ai", "claude.com"]
