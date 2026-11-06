"""The OAuth front channel end to end.

register (DCR) -> /oauth/authorize -> consent API -> redirect back to the
client with a single-use code, `state` and `iss`. /oauth/* runs through the
real Flask app; the consent calls go through the legacy router with a real
session from /api/auth/login, exactly as the consent page will make them.
"""
from urllib.parse import parse_qs, urlsplit

import api.audit
from api.tests.integration import gateway_helpers as gh


def test_register_authorize_consent_redirects_with_code_state_and_iss(call_api, admin_user):
    gh.set_integration_settings(enabled=True)
    client = gh.client()

    registered = gh.register_client(client)
    assert registered["client_id"].startswith("ptc_")
    assert "client_secret" not in registered
    assert registered["token_endpoint_auth_method"] == "none"
    assert registered["redirect_uris"] == [gh.CLAUDE_CALLBACK]

    _verifier, challenge = gh.pkce_pair()
    response = gh.authorize(client, registered["client_id"], gh.CLAUDE_CALLBACK, challenge,
                            state="xyz")
    assert response.status_code == 302, response.get_data(as_text=True)
    request_id = gh.request_id_from(response)

    token = gh.session_token(call_api, admin_user)
    status, body = call_api("GET", f"/api/oauth/requests/{request_id}", token=token)
    assert status == 200, body
    assert body["client_name"] == "Claude"
    assert body["client_name_self_declared"] is True
    assert body["redirect_host"] == "claude.ai"
    assert body["redirect_host_punycode"] is False
    assert body["scope"] == "read"
    assert body["resource"] == "http://localhost:8000/mcp"
    assert body["resource_kind"] == "mcp"
    assert body["user"] == {"name": admin_user["name"], "role": "admin"}

    status, body = gh.approve(call_api, token, request_id,
                              headers={"Origin": "http://localhost:8000"})
    assert status == 200, body
    redirect_to = body["redirect_to"]
    assert redirect_to.startswith(gh.CLAUDE_CALLBACK + "?")
    query = parse_qs(urlsplit(redirect_to).query)
    assert len(query["code"]) == 1 and len(query["code"][0]) >= 43
    assert query["state"] == ["xyz"]
    assert query["iss"] == ["http://localhost:8000"]

    assert gh.count_rows("oauth_grants") == 1
    assert gh.count_rows("oauth_codes") == 1
    actions = [call.args[1] for call in api.audit.log.call_args_list]
    assert "integration.oauth_consent" in actions


# ─── helpers for the matrix ──────────────────────────────────────────────────

BASE = "http://localhost:8000"
METADATA_PATHS = (
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
    "/.well-known/oauth-protected-resource/api/v1",
    "/.well-known/oauth-authorization-server",
)


def _pending_request(client, redirect_uri=gh.CLAUDE_CALLBACK, registered=None, **kwargs):
    """Register a client, run a valid authorize and return the request id."""
    reg = gh.register_client(client, redirect_uris=registered or (redirect_uri,))
    _verifier, challenge = gh.pkce_pair()
    response = gh.authorize(client, reg["client_id"], redirect_uri, challenge, **kwargs)
    assert response.status_code == 302, response.get_data(as_text=True)
    return gh.request_id_from(response)


def _request_row(request_id):
    from api.db import get_conn
    from api.gateway import tokens

    conn = get_conn()
    try:
        return conn.execute(
            "SELECT * FROM oauth_authorize_requests WHERE request_hash = ?",
            (tokens.token_hash(request_id),)).fetchone()
    finally:
        conn.close()


def _redirect_query(response):
    assert response.status_code == 302, response.get_data(as_text=True)
    location = response.headers["Location"]
    assert location.startswith(gh.CLAUDE_CALLBACK + "?"), location
    return parse_qs(urlsplit(location).query)


def _assert_error_page(response):
    assert response.status_code == 400
    assert response.headers["Content-Type"].startswith("text/html")
    assert "Location" not in response.headers
    assert "<script" not in response.get_data(as_text=True).lower()


# ─── discovery documents ─────────────────────────────────────────────────────

def test_protected_resource_metadata_for_mcp_root_and_path():
    gh.set_integration_settings(enabled=True)
    client = gh.client()

    for path in ("/.well-known/oauth-protected-resource/mcp",
                 "/.well-known/oauth-protected-resource"):
        response = client.get(path)
        assert response.status_code == 200, path
        body = response.get_json()
        assert body["resource"] == BASE + "/mcp"
        assert body["authorization_servers"] == [BASE]
        assert body["scopes_supported"] == ["read"]
        assert body["bearer_methods_supported"] == ["header"]
        assert body["resource_name"] == "PartnerType"
        assert body["resource_documentation"] == BASE + "/api/v1/openapi.json"
        assert response.headers["Access-Control-Allow-Origin"] == "*"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Content-Type"].startswith("application/json")


def test_protected_resource_metadata_for_api_v1():
    gh.set_integration_settings(enabled=True)

    body = gh.client().get("/.well-known/oauth-protected-resource/api/v1").get_json()

    assert body["resource"] == BASE + "/api/v1"
    assert body["authorization_servers"] == [BASE]


def test_authorization_server_metadata():
    gh.set_integration_settings(enabled=True)

    response = gh.client().get("/.well-known/oauth-authorization-server")

    assert response.status_code == 200
    body = response.get_json()
    assert body["issuer"] == BASE
    assert body["authorization_endpoint"] == BASE + "/oauth/authorize"
    assert body["token_endpoint"] == BASE + "/oauth/token"
    assert body["registration_endpoint"] == BASE + "/oauth/register"
    assert body["revocation_endpoint"] == BASE + "/oauth/revoke"
    assert body["response_types_supported"] == ["code"]
    assert body["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["token_endpoint_auth_methods_supported"] == ["none"]
    assert body["revocation_endpoint_auth_methods_supported"] == ["none"]
    assert body["authorization_response_iss_parameter_supported"] is True
    assert body["scopes_supported"] == ["read"]
    assert "offline_access" not in body["scopes_supported"]
    # CIMD is deferred: no advertisement at all.
    assert "client_id_metadata_document_supported" not in body
    assert body["service_documentation"] == BASE + "/api/v1/openapi.json"
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert response.headers["Cache-Control"] == "no-store"


def test_openid_configuration_is_not_served():
    gh.set_integration_settings(enabled=True)

    assert gh.client().get("/.well-known/openid-configuration").status_code == 404


def test_metadata_disappears_while_integrations_are_off():
    gh.set_integration_settings(enabled=False)
    client = gh.client()

    for path in METADATA_PATHS:
        assert client.get(path).status_code == 404, path


def test_host_header_does_not_change_any_metadata_url():
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    headers = {"X-Forwarded-Host": "evil.example", "Host": "evil.example"}

    for path in METADATA_PATHS:
        response = client.get(path, base_url="http://evil.example", headers=headers)
        assert response.status_code == 200, path
        text = response.get_data(as_text=True)
        assert "evil.example" not in text, path
        assert BASE in text, path


def test_cors_wildcard_is_only_on_metadata():
    gh.set_integration_settings(enabled=True)
    client = gh.client()

    response = client.post("/oauth/register", json={"redirect_uris": [gh.CLAUDE_CALLBACK]})

    assert response.status_code == 201
    assert "Access-Control-Allow-Origin" not in response.headers


# ─── dynamic client registration ─────────────────────────────────────────────

def test_registration_refused_while_integrations_are_off():
    gh.set_integration_settings(enabled=False)

    response = gh.client().post("/oauth/register",
                                json={"redirect_uris": [gh.CLAUDE_CALLBACK]})

    assert response.status_code == 403
    assert response.get_json()["error"] == "access_denied"
    assert gh.count_rows("oauth_clients") == 0


def test_registration_requires_json():
    gh.set_integration_settings(enabled=True)

    response = gh.client().post("/oauth/register", data="redirect_uris=x",
                                content_type="application/x-www-form-urlencoded")

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_client_metadata"


def test_registration_rejects_a_non_object_body():
    gh.set_integration_settings(enabled=True)

    response = gh.client().post("/oauth/register", data="[1, 2]",
                                content_type="application/json")

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_client_metadata"


def test_registration_body_is_capped_at_8_kb():
    gh.set_integration_settings(enabled=True)
    body = {"redirect_uris": [gh.CLAUDE_CALLBACK], "client_name": "x" * 9000}

    response = gh.client().post("/oauth/register", json=body)

    assert response.status_code in (400, 413)
    assert response.get_json()["error"] == "invalid_client_metadata"
    assert gh.count_rows("oauth_clients") == 0


def test_registration_allows_at_most_five_redirect_uris():
    gh.set_integration_settings(enabled=True)
    uris = [f"https://claude.ai/cb{i}" for i in range(6)]

    response = gh.client().post("/oauth/register", json={"redirect_uris": uris})

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_redirect_uri"


def test_registration_rejects_http_on_a_real_host():
    gh.set_integration_settings(enabled=True)

    response = gh.client().post("/oauth/register",
                                json={"redirect_uris": ["http://example.com/cb"]})

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_redirect_uri"


def test_registration_rejects_a_look_alike_host():
    gh.set_integration_settings(enabled=True)
    client = gh.client()

    for uri in ("https://cl\u0430ude.ai/api/mcp/auth_callback",
                "https://claude.ai\u3002evil.com/cb",
                "https://claude.ai%2eevil.com/cb"):
        response = client.post("/oauth/register", json={"client_name": "Claude",
                                                         "redirect_uris": [uri]})
        assert response.status_code == 400, uri
        assert response.get_json()["error"] == "invalid_redirect_uri"
    assert gh.count_rows("oauth_clients") == 0


def test_authorize_refuses_a_stored_look_alike_redirect():
    """A client registered before the host rules existed gets no consent screen."""
    import json as _json

    from api.db import get_conn
    from api.gateway import tokens

    gh.set_integration_settings(enabled=True)
    uri = "https://cl\u0430ude.ai/api/mcp/auth_callback"
    client_id = tokens.new_client_id()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO oauth_clients (client_id, client_name, redirect_uris) VALUES (?, ?, ?)",
            (client_id, "Claude", _json.dumps([uri])))
    _verifier, challenge = gh.pkce_pair()

    response = gh.authorize(gh.client(), client_id, uri, challenge)

    _assert_error_page(response)
    assert gh.count_rows("oauth_authorize_requests") == 0


def test_consent_flags_a_punycode_redirect_host(call_api, admin_user, monkeypatch):
    monkeypatch.setenv("PARTNERTYPE_OAUTH_REDIRECT_HOSTS", "*")
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    uri = "https://xn--clude-6ve.ai/api/mcp/auth_callback"
    reg = gh.register_client(client, redirect_uris=(uri,))
    _verifier, challenge = gh.pkce_pair()
    request_id = gh.request_id_from(gh.authorize(client, reg["client_id"], uri, challenge))

    token = gh.session_token(call_api, admin_user)
    status, body = call_api("GET", f"/api/oauth/requests/{request_id}", token=token)

    assert status == 200, body
    assert body["redirect_host"] == "xn--clude-6ve.ai"
    assert body["redirect_host_punycode"] is True


def test_registration_refuses_an_unknown_redirect_host():
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    response = client.post("/oauth/register", json={
        "client_name": "Claude", "redirect_uris": ["https://partnertype-connect.example/cb"]})
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_redirect_uri"
    assert gh.count_rows("oauth_clients") == 0


def test_operator_can_allow_another_redirect_host(monkeypatch):
    monkeypatch.setenv("PARTNERTYPE_OAUTH_REDIRECT_HOSTS", "claude.ai,chatgpt.com")
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    reg = gh.register_client(client, redirect_uris=("https://chatgpt.com/connector/oauth/cb",))
    assert reg["client_id"]


def test_authorize_refuses_a_stored_client_outside_the_allowlist(monkeypatch):
    monkeypatch.setenv("PARTNERTYPE_OAUTH_REDIRECT_HOSTS", "*")
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    uri = "https://partnertype-connect.example/cb"
    reg = gh.register_client(client, redirect_uris=(uri,))
    monkeypatch.delenv("PARTNERTYPE_OAUTH_REDIRECT_HOSTS")
    _verifier, challenge = gh.pkce_pair()
    response = gh.authorize(client, reg["client_id"], uri, challenge)
    _assert_error_page(response)
    assert gh.count_rows("oauth_authorize_requests") == 0


def test_registration_rejects_missing_redirect_uris():
    gh.set_integration_settings(enabled=True)

    response = gh.client().post("/oauth/register", json={"client_name": "x"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_redirect_uri"


def test_registration_is_public_client_only_and_cleans_the_name():
    gh.set_integration_settings(enabled=True)

    response = gh.client().post("/oauth/register", json={
        "redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"],
        "client_name": "Claude‮ Code\x07" + "x" * 200,
        "token_endpoint_auth_method": "client_secret_basic",
        "application_type": "native",
    })

    assert response.status_code == 201, response.get_data(as_text=True)
    body = response.get_json()
    assert "client_secret" not in body
    assert body["token_endpoint_auth_method"] == "none"
    assert body["grant_types"] == ["authorization_code", "refresh_token"]
    assert body["response_types"] == ["code"]
    assert isinstance(body["client_id_issued_at"], int)
    assert body["client_name"].startswith("Claude Code")
    assert "‮" not in body["client_name"] and "\x07" not in body["client_name"]
    assert len(body["client_name"]) <= 100
    assert response.headers["Cache-Control"] == "no-store"


def test_registration_is_limited_per_ip(monkeypatch):
    gh.set_integration_settings(enabled=True)
    monkeypatch.setenv("PARTNERTYPE_OAUTH_REGISTER_PER_HOUR", "2")
    client = gh.client()
    body = {"redirect_uris": [gh.CLAUDE_CALLBACK]}

    assert client.post("/oauth/register", json=body).status_code == 201
    assert client.post("/oauth/register", json=body).status_code == 201
    response = client.post("/oauth/register", json=body)

    assert response.status_code == 429
    retry_after = int(response.headers["Retry-After"])
    assert 1 <= retry_after <= 3600
    assert gh.count_rows("oauth_clients") == 2


# ─── authorize: rate limits and a bounded table ──────────────────────────────

def test_authorize_is_limited_per_ip(monkeypatch):
    from api.gateway import oauth

    monkeypatch.setattr(oauth, "AUTHORIZE_PER_IP_PER_MINUTE", 2)
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    first = gh.register_client(client)["client_id"]
    second = gh.register_client(client)["client_id"]
    _verifier, challenge = gh.pkce_pair()

    assert gh.authorize(client, first, gh.CLAUDE_CALLBACK, challenge).status_code == 302
    assert gh.authorize(client, second, gh.CLAUDE_CALLBACK, challenge).status_code == 302
    response = gh.authorize(client, first, gh.CLAUDE_CALLBACK, challenge)

    assert response.status_code == 429
    assert response.headers["Content-Type"].startswith("text/html")
    assert "Location" not in response.headers
    assert 1 <= int(response.headers["Retry-After"]) <= 60
    assert gh.count_rows("oauth_authorize_requests") == 2


def test_authorize_is_limited_per_client(monkeypatch):
    from api.gateway import oauth

    monkeypatch.setattr(oauth, "AUTHORIZE_PER_CLIENT_PER_MINUTE", 1)
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    first = gh.register_client(client)["client_id"]
    second = gh.register_client(client)["client_id"]
    _verifier, challenge = gh.pkce_pair()

    assert gh.authorize(client, first, gh.CLAUDE_CALLBACK, challenge).status_code == 302
    assert gh.authorize(client, first, gh.CLAUDE_CALLBACK, challenge).status_code == 429
    assert gh.authorize(client, second, gh.CLAUDE_CALLBACK, challenge).status_code == 302
    assert gh.count_rows("oauth_authorize_requests") == 2


def test_invalid_authorize_requests_store_nothing_and_count_nothing():
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    _verifier, challenge = gh.pkce_pair()

    for _ in range(3):
        gh.authorize(client, "ptc_" + "x" * 30, gh.CLAUDE_CALLBACK, challenge)

    assert gh.count_rows("oauth_authorize_requests") == 0
    assert gh.count_rows("api_rate_windows") == 0


def test_authorize_clears_expired_requests():
    from api.db import get_conn

    gh.set_integration_settings(enabled=True)
    client = gh.client()
    for _ in range(3):
        _pending_request(client)
    with get_conn() as conn:
        conn.execute("""UPDATE oauth_authorize_requests
                        SET created_at = now() - INTERVAL '1 hour',
                            expires_at = now() - INTERVAL '30 minutes'""")

    _pending_request(client)

    assert gh.count_rows("oauth_authorize_requests") == 1


# ─── authorize: error page vs redirect ───────────────────────────────────────

def test_authorize_unknown_client_renders_error_page():
    gh.set_integration_settings(enabled=True)
    _verifier, challenge = gh.pkce_pair()

    response = gh.authorize(gh.client(), "ptc_unknownclient000000", gh.CLAUDE_CALLBACK,
                            challenge)

    _assert_error_page(response)
    assert "xyz" not in response.get_data(as_text=True)


def test_authorize_unregistered_redirect_renders_error_page():
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    reg = gh.register_client(client)
    _verifier, challenge = gh.pkce_pair()

    response = gh.authorize(client, reg["client_id"], "https://evil.example/cb", challenge)

    _assert_error_page(response)
    assert "evil.example" not in response.get_data(as_text=True)


def test_authorize_missing_redirect_renders_error_page():
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    reg = gh.register_client(client)
    _verifier, challenge = gh.pkce_pair()

    _assert_error_page(gh.authorize(client, reg["client_id"], None, challenge))


def test_authorize_oversized_state_renders_error_page():
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    reg = gh.register_client(client)
    _verifier, challenge = gh.pkce_pair()

    response = gh.authorize(client, reg["client_id"], gh.CLAUDE_CALLBACK, challenge,
                            state="s" * 1025)

    _assert_error_page(response)


def test_authorize_missing_challenge_redirects_invalid_request_with_state_and_iss():
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    reg = gh.register_client(client)

    query = _redirect_query(gh.authorize(client, reg["client_id"], gh.CLAUDE_CALLBACK, None))

    assert query["error"] == ["invalid_request"]
    assert query["state"] == ["xyz"]
    assert query["iss"] == [BASE]
    assert "code" not in query


def test_authorize_plain_method_is_invalid_request():
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    reg = gh.register_client(client)
    _verifier, challenge = gh.pkce_pair()

    query = _redirect_query(gh.authorize(client, reg["client_id"], gh.CLAUDE_CALLBACK,
                                         challenge, code_challenge_method="plain"))

    assert query["error"] == ["invalid_request"]
    assert query["iss"] == [BASE]


def test_authorize_malformed_challenge_is_invalid_request():
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    reg = gh.register_client(client)

    query = _redirect_query(gh.authorize(client, reg["client_id"], gh.CLAUDE_CALLBACK,
                                         "too-short"))

    assert query["error"] == ["invalid_request"]


def test_authorize_unsupported_response_type():
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    reg = gh.register_client(client)
    _verifier, challenge = gh.pkce_pair()

    query = _redirect_query(gh.authorize(client, reg["client_id"], gh.CLAUDE_CALLBACK,
                                         challenge, response_type="token"))

    assert query["error"] == ["unsupported_response_type"]
    assert query["state"] == ["xyz"] and query["iss"] == [BASE]


def test_authorize_unknown_scope_is_invalid_scope():
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    reg = gh.register_client(client)
    _verifier, challenge = gh.pkce_pair()

    query = _redirect_query(gh.authorize(client, reg["client_id"], gh.CLAUDE_CALLBACK,
                                         challenge, scope="read write"))

    assert query["error"] == ["invalid_scope"]
    assert query["state"] == ["xyz"] and query["iss"] == [BASE]


def test_authorize_offline_access_is_dropped_not_refused():
    gh.set_integration_settings(enabled=True)
    client = gh.client()

    request_id = _pending_request(client, scope="read offline_access")

    assert _request_row(request_id)["scope"] == "read"


def test_authorize_foreign_resource_is_invalid_target():
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    reg = gh.register_client(client)
    _verifier, challenge = gh.pkce_pair()

    query = _redirect_query(gh.authorize(client, reg["client_id"], gh.CLAUDE_CALLBACK,
                                         challenge, resource="https://evil.example/mcp"))

    assert query["error"] == ["invalid_target"]
    assert query["iss"] == [BASE]


def test_authorize_while_integrations_are_off_is_access_denied():
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    reg = gh.register_client(client)
    gh.set_integration_settings(enabled=False)
    _verifier, challenge = gh.pkce_pair()

    query = _redirect_query(gh.authorize(client, reg["client_id"], gh.CLAUDE_CALLBACK,
                                         challenge))

    assert query["error"] == ["access_denied"]
    assert query["state"] == ["xyz"] and query["iss"] == [BASE]
    assert gh.count_rows("oauth_authorize_requests") == 0


def test_authorize_resource_api_v1_is_stored_canonically(call_api, admin_user):
    gh.set_integration_settings(enabled=True)
    client = gh.client()

    request_id = _pending_request(client, resource="HTTP://LOCALHOST:8000/api/v1")

    row = _request_row(request_id)
    assert row["resource"] == BASE + "/api/v1"
    assert row["state"] == "xyz"
    token = gh.session_token(call_api, admin_user)
    status, body = call_api("GET", f"/api/oauth/requests/{request_id}", token=token)
    assert status == 200 and body["resource_kind"] == "api_v1"


def test_loopback_redirect_with_ephemeral_port_round_trips(call_api, admin_user):
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    requested = "http://127.0.0.1:53111/callback"

    request_id = _pending_request(client, redirect_uri=requested,
                                  registered=("http://127.0.0.1/callback",))

    token = gh.session_token(call_api, admin_user)
    status, body = call_api("GET", f"/api/oauth/requests/{request_id}", token=token)
    assert status == 200
    assert body["redirect_host"] == "127.0.0.1"
    assert body["redirect_is_loopback"] is True and body["all_redirects_loopback"] is True
    status, body = gh.approve(call_api, token, request_id)
    assert status == 200
    assert body["redirect_to"].startswith(requested + "?code=")


def test_authorize_never_needs_a_session():
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    client.set_cookie("ph_test_test_session", "not-a-session", domain="localhost")

    request_id = _pending_request(client)

    assert _request_row(request_id) is not None


# ─── consent API gates ───────────────────────────────────────────────────────

def test_consent_get_without_session_is_401(call_api):
    gh.set_integration_settings(enabled=True)
    request_id = _pending_request(gh.client())

    status, _body = call_api("GET", f"/api/oauth/requests/{request_id}")

    assert status == 401


def test_partner_cannot_consent_while_partners_are_off(call_api, partner_user):
    gh.set_integration_settings(enabled=True, partners=False)
    request_id = _pending_request(gh.client())
    token = gh.session_token(call_api, partner_user)

    status, body = call_api("GET", f"/api/oauth/requests/{request_id}", token=token)
    assert status == 403 and body["code"] == "partners_disabled"

    status, body = gh.approve(call_api, token, request_id)
    assert status == 403 and body["code"] == "partners_disabled"
    assert gh.count_rows("oauth_grants") == 0


def test_partner_may_consent_when_partners_are_on(call_api, partner_user):
    gh.set_integration_settings(enabled=True, partners=True)
    request_id = _pending_request(gh.client())
    token = gh.session_token(call_api, partner_user)

    status, body = gh.approve(call_api, token, request_id)

    assert status == 200 and "code=" in body["redirect_to"]
    assert gh.count_rows("oauth_grants") == 1


def test_consent_requires_mfa_when_enforced(call_api, admin_user, monkeypatch):
    gh.set_integration_settings(enabled=True)
    request_id = _pending_request(gh.client())
    token = gh.session_token(call_api, admin_user)
    monkeypatch.setenv("PARTNERTYPE_REQUIRE_MFA", "1")

    status, body = call_api("GET", f"/api/oauth/requests/{request_id}", token=token)
    assert status == 403 and body["code"] == "mfa_setup_required"

    status, body = gh.approve(call_api, token, request_id)
    assert status == 403 and body["code"] == "mfa_setup_required"
    assert _request_row(request_id)["used_at"] is None


def test_expired_request_is_404(call_api, admin_user):
    from api.db import get_conn
    from api.gateway import tokens

    gh.set_integration_settings(enabled=True)
    request_id = _pending_request(gh.client())
    with get_conn() as conn:
        conn.execute(
            """UPDATE oauth_authorize_requests SET expires_at = now() - INTERVAL '1 second'
               WHERE request_hash = ?""", (tokens.token_hash(request_id),))
    token = gh.session_token(call_api, admin_user)

    status, body = call_api("GET", f"/api/oauth/requests/{request_id}", token=token)
    assert status == 404 and body["code"] == "request_expired"

    status, body = gh.approve(call_api, token, request_id)
    assert status == 404 and body["code"] == "request_expired"


def test_unknown_request_id_is_404(call_api, admin_user):
    gh.set_integration_settings(enabled=True)
    token = gh.session_token(call_api, admin_user)

    status, body = call_api("GET", "/api/oauth/requests/" + "A" * 43, token=token)

    assert status == 404 and body["code"] == "request_expired"


def test_decision_from_a_foreign_origin_is_refused(call_api, admin_user):
    gh.set_integration_settings(enabled=True)
    request_id = _pending_request(gh.client())
    token = gh.session_token(call_api, admin_user)

    status, _body = gh.approve(call_api, token, request_id,
                               headers={"Origin": "https://evil.example"})

    assert status == 403
    assert _request_row(request_id)["used_at"] is None
    assert gh.count_rows("oauth_grants") == 0


def test_decision_requires_a_boolean(call_api, admin_user):
    gh.set_integration_settings(enabled=True)
    request_id = _pending_request(gh.client())
    token = gh.session_token(call_api, admin_user)

    status, body = call_api("POST", f"/api/oauth/requests/{request_id}/decision",
                            body={"approve": "yes"}, token=token)

    assert status == 400
    assert _request_row(request_id)["used_at"] is None


def test_deny_redirects_access_denied_and_creates_nothing(call_api, admin_user):
    gh.set_integration_settings(enabled=True)
    request_id = _pending_request(gh.client(), state="st-1")
    token = gh.session_token(call_api, admin_user)

    status, body = gh.approve(call_api, token, request_id, approve=False)

    assert status == 200
    query = parse_qs(urlsplit(body["redirect_to"]).query)
    assert query["error"] == ["access_denied"]
    assert query["state"] == ["st-1"]
    assert query["iss"] == [BASE]
    assert "code" not in query
    assert gh.count_rows("oauth_grants") == 0
    assert gh.count_rows("oauth_codes") == 0
    assert _request_row(request_id)["used_at"] is not None


def test_a_request_is_answered_once(call_api, admin_user):
    gh.set_integration_settings(enabled=True)
    request_id = _pending_request(gh.client())
    token = gh.session_token(call_api, admin_user)

    first, _ = gh.approve(call_api, token, request_id)
    second, body = gh.approve(call_api, token, request_id)
    again, _ = call_api("GET", f"/api/oauth/requests/{request_id}", token=token)

    assert first == 200
    assert second == 404 and body["code"] == "request_expired"
    assert again == 404
    assert gh.count_rows("oauth_grants") == 1
    assert gh.count_rows("oauth_codes") == 1


def test_integrations_switched_off_before_the_click_refuses_and_spends(call_api, admin_user):
    gh.set_integration_settings(enabled=True)
    request_id = _pending_request(gh.client())
    token = gh.session_token(call_api, admin_user)
    gh.set_integration_settings(enabled=False)

    status, body = gh.approve(call_api, token, request_id)

    assert status == 403 and body["code"] == "integrations_disabled"
    assert gh.count_rows("oauth_grants") == 0
    assert _request_row(request_id)["used_at"] is not None


def test_code_row_is_bound_to_client_redirect_challenge_and_resource(call_api, admin_user):
    from api.db import get_conn

    gh.set_integration_settings(enabled=True)
    client = gh.client()
    reg = gh.register_client(client)
    _verifier, challenge = gh.pkce_pair()
    request_id = gh.request_id_from(
        gh.authorize(client, reg["client_id"], gh.CLAUDE_CALLBACK, challenge))
    token = gh.session_token(call_api, admin_user)

    status, _body = gh.approve(call_api, token, request_id)

    assert status == 200
    conn = get_conn()
    try:
        code = conn.execute(
            """SELECT c.*, g.user_id, (c.expires_at - c.created_at) AS ttl
               FROM oauth_codes c JOIN oauth_grants g ON g.id = c.grant_id""").fetchone()
    finally:
        conn.close()
    assert code["client_id"] == reg["client_id"]
    assert code["redirect_uri"] == gh.CLAUDE_CALLBACK
    assert code["code_challenge"] == challenge
    assert code["resource"] == BASE + "/mcp"
    assert code["scope"] == "read"
    assert code["user_id"] == admin_user["id"]
    assert code["used_at"] is None
    assert code["ttl"].total_seconds() == 600


# ─── unexpected errors stay protocol-shaped ──────────────────────────────────

def test_register_unexpected_error_is_oauth_shaped(monkeypatch):
    gh.set_integration_settings(enabled=True)

    def boom():
        raise RuntimeError("secret detail")

    monkeypatch.setattr("api.gateway.tokens.new_client_id", boom)
    response = gh.client().post("/oauth/register", json={"redirect_uris": [gh.CLAUDE_CALLBACK]})

    assert response.status_code == 500
    body = response.get_json()
    assert body["error"] == "server_error"
    assert "secret detail" not in response.get_data(as_text=True)


def test_authorize_unexpected_error_is_a_page_not_a_redirect(monkeypatch):
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    reg = gh.register_client(client)
    _verifier, challenge = gh.pkce_pair()

    def boom():
        raise RuntimeError("secret detail")

    monkeypatch.setattr("api.gateway.tokens.new_secret", boom)
    response = gh.authorize(client, reg["client_id"], gh.CLAUDE_CALLBACK, challenge)

    assert response.status_code == 500
    assert "Location" not in response.headers
    assert response.headers["Content-Type"].startswith("text/html")
    assert "secret detail" not in response.get_data(as_text=True)
