"""Integracje settings: admin-only switches, RODO acknowledgement, caps, isolation.

Driven through the real legacy router (`call_api`) with the session cookie and
an allowed Origin, exactly as the Settings screen sends them. Effects on the
gateway are proven through the real Flask app. Audit entries are asserted on
the autouse mock of `api.audit.log` (never on rows).
"""
import pytest

import api.audit
from api.db import get_conn
from api.tests.integration import gateway_helpers as gh

ORIGIN = {"Origin": "http://localhost:8000"}
PATH = "/api/integrations/settings"
SNAPSHOT_KEYS = {"enabled", "partners_enabled", "field_groups", "field_group_catalogue",
                 "limits", "operator_caps", "mcp_url", "api_base", "openapi_url"}


def _actions():
    return [call.args[1] for call in api.audit.log.call_args_list]


def _observer():
    user = gh.create_user("observer", "observer.integrations@test.pl")
    user["plain_password"] = "GatewayPass123!"
    return user


def _patch(call_api, session, body):
    return call_api("PATCH", PATH, token=session, body=body, headers=ORIGIN)


def _setting(key):
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]
    finally:
        conn.close()


# ─── role gates ───────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ["manager", "observer", "partner"])
def test_non_admin_roles_get_403_on_read_and_write(call_api, role, manager_user, partner_user):
    user = {"manager": manager_user, "partner": partner_user}.get(role) or _observer()
    session = gh.session_token(call_api, user)

    status, body = call_api("GET", PATH, token=session)
    assert status == 403, body

    status, body = _patch(call_api, session, {"enabled": True, "acknowledge_eu_transfer": True})
    assert status == 403, body
    assert _setting("integrations_enabled") is None
    assert not [a for a in _actions() if a.startswith("integration.")]


def test_admin_reads_the_snapshot(call_api, admin_user):
    session = gh.session_token(call_api, admin_user)

    status, body = call_api("GET", PATH, token=session)

    assert status == 200, body
    assert set(body) == SNAPSHOT_KEYS
    assert body["enabled"] is False and body["partners_enabled"] is False
    assert body["field_groups"] == []
    assert body["mcp_url"] == "http://localhost:8000/mcp"
    assert body["api_base"] == "http://localhost:8000/api/v1"
    assert body["openapi_url"] == "http://localhost:8000/api/v1/openapi.json"
    assert set(body["limits"]) == {"per_minute", "per_day"}
    assert set(body["operator_caps"]) == {"rate_per_minute", "rate_per_day", "register_per_hour"}
    catalogue = {item["key"]: item["fields"] for item in body["field_group_catalogue"]}
    assert "client_email" in catalogue["contact"]
    assert set(catalogue) == {"base", "contact", "notes", "client_ids", "custom", "commissions"}


# ─── the main switch and its RODO acknowledgement ───────────────

@pytest.mark.parametrize("body", [
    {"enabled": True},
    {"enabled": True, "acknowledge_eu_transfer": False},
])
def test_enabling_without_acknowledgement_is_refused_and_stays_off(call_api, admin_user, body):
    session = gh.session_token(call_api, admin_user)

    status, answer = _patch(call_api, session, body)

    assert status == 400, answer
    assert "Unią Europejską" in answer["error"]
    status, snapshot = call_api("GET", PATH, token=session)
    assert snapshot["enabled"] is False
    assert "integration.enabled" not in _actions()


def test_disabling_keeps_keys_and_grants_and_the_key_is_refused(call_api, admin_user):
    session = gh.session_token(call_api, admin_user)
    _patch(call_api, session, {"enabled": True, "acknowledge_eu_transfer": True})
    raw = gh.make_key(admin_user)
    gh.oauth_dance(gh.client(), call_api, admin_user)
    keys_before, grants_before = gh.count_rows("api_keys"), gh.count_rows("oauth_grants")

    status, body = _patch(call_api, session, {"enabled": False})

    assert status == 200, body
    assert body["enabled"] is False
    assert "integration.disabled" in _actions()
    assert gh.count_rows("api_keys") == keys_before == 1
    assert gh.count_rows("oauth_grants") == grants_before == 1
    response = gh.client().get("/api/v1/leads", headers=gh.bearer(raw))
    assert response.status_code == 403, response.get_data(as_text=True)


def test_same_value_twice_writes_one_audit_entry(call_api, admin_user):
    session = gh.session_token(call_api, admin_user)

    for _ in range(2):
        status, body = _patch(call_api, session,
                              {"enabled": True, "acknowledge_eu_transfer": True})
        assert status == 200, body
    assert _actions().count("integration.enabled") == 1

    for _ in range(2):
        status, body = _patch(call_api, session, {"partners_enabled": True})
        assert status == 200, body
    assert _actions().count("integration.partners_enabled") == 1


def test_partners_switch_is_audited_both_ways(call_api, admin_user):
    session = gh.session_token(call_api, admin_user)

    _patch(call_api, session, {"partners_enabled": True})
    status, body = _patch(call_api, session, {"partners_enabled": False})

    assert status == 200, body
    assert body["partners_enabled"] is False
    assert "integration.partners_enabled" in _actions()
    assert "integration.partners_disabled" in _actions()


@pytest.mark.parametrize("body", [
    {"enabled": "1", "acknowledge_eu_transfer": True},
    {"partners_enabled": 1},
    {"integrations_enabled": "1"},
    {"rate_per_minute": "5"},
    {"rate_per_minute": True},
    {"rate_per_day": 0},
    {"field_groups": "contact"},
    {"field_groups": ["contact", "contact"]},
])
def test_wrong_types_and_unknown_keys_are_400(call_api, admin_user, body):
    session = gh.session_token(call_api, admin_user)

    status, answer = _patch(call_api, session, body)

    assert status == 400, answer
    assert not [a for a in _actions() if a.startswith("integration.")]


# ─── limits within the operator caps ──────────────────────────────────

def test_limit_above_operator_cap_is_refused_naming_the_cap(call_api, admin_user, monkeypatch):
    monkeypatch.setenv("PARTNERTYPE_API_RATE_PER_MINUTE", "10")
    session = gh.session_token(call_api, admin_user)

    status, body = _patch(call_api, session, {"rate_per_minute": 11})

    assert status == 400, body
    assert "10" in body["error"]
    assert _setting("integrations_rate_per_minute") is None


def test_limits_within_caps_are_stored_and_audited(call_api, admin_user, monkeypatch):
    monkeypatch.setenv("PARTNERTYPE_API_RATE_PER_MINUTE", "10")
    session = gh.session_token(call_api, admin_user)

    status, body = _patch(call_api, session, {"rate_per_minute": 10, "rate_per_day": 300})

    assert status == 200, body
    assert body["limits"] == {"per_minute": 10, "per_day": 300}
    assert body["operator_caps"]["rate_per_minute"] == 10
    assert _actions().count("integration.limits_changed") == 2
    details = [c.args[4] for c in api.audit.log.call_args_list
               if c.args[1] == "integration.limits_changed"]
    assert sorted(details) == ["10", "300"]


# ─── field groups ─────────────────────────────────────────────────────

def test_unknown_field_group_is_refused(call_api, admin_user):
    session = gh.session_token(call_api, admin_user)

    status, body = _patch(call_api, session, {"field_groups": ["contact", "bogus"]})

    assert status == 400, body
    assert "bogus" not in body["error"]
    assert _setting("integrations_field_groups") is None


def test_enabled_field_group_reaches_api_v1(call_api, admin_user, partner_user):
    session = gh.session_token(call_api, admin_user)
    _patch(call_api, session, {"enabled": True, "acknowledge_eu_transfer": True})
    gh.make_lead(partner_user)
    raw = gh.make_key(admin_user)
    before = gh.client().get("/api/v1/leads", headers=gh.bearer(raw)).get_json()
    assert "client_email" not in before["untrusted_data"][0]

    status, body = _patch(call_api, session, {"field_groups": ["contact"]})

    assert status == 200, body
    assert body["field_groups"] == ["contact"]
    assert "integration.fields_changed" in _actions()
    after = gh.client().get("/api/v1/leads", headers=gh.bearer(raw)).get_json()
    assert "client_email" in after["untrusted_data"][0]


# ─── isolation from the general Settings form ────────────────────

def test_general_settings_read_never_shows_integration_rows(call_api, admin_user):
    session = gh.session_token(call_api, admin_user)
    _patch(call_api, session, {"enabled": True, "acknowledge_eu_transfer": True,
                               "partners_enabled": True, "field_groups": ["contact"],
                               "rate_per_minute": 5})

    status, body = call_api("GET", "/api/settings", token=session)

    assert status == 200, body
    assert not [key for key in body["settings"] if key.startswith("integrations_")]


def test_manager_general_settings_patch_cannot_switch_integrations_on(call_api, manager_user):
    session = gh.session_token(call_api, manager_user)

    status, body = call_api("PATCH", "/api/settings", token=session,
                            body={"integrations_enabled": "1"}, headers=ORIGIN)

    assert status == 200, body
    assert _setting("integrations_enabled") is None
    from api.gateway import settings as gateway_settings
    assert gateway_settings.integrations_enabled() is False
