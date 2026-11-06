"""The OAuth back channel end to end.

register -> authorize -> login -> consent -> code -> POST /oauth/token ->
tools/call on /mcp, all through the real endpoints. Then the token
endpoint's negative matrix: single-use codes, PKCE and binding checks,
refresh rotation with reuse detection, revocation and the per-client limit.
"""
from api.db import get_conn
from api.gateway import oauth, tokens
from api.tests.integration import gateway_helpers as gh

MCP_RESOURCE = "http://localhost:8000/mcp"
API_V1_RESOURCE = "http://localhost:8000/api/v1"


def _assert_no_store(response):
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"


def _mcp_ok(client, access):
    response = gh.mcp_call(client, access, "tools/call",
                           {"name": "list_leads", "arguments": {"per_page": 3}})
    return response


# ─── Tracer ─────────────────────────────────────────────────────────

def test_full_oauth_dance_reads_leads_through_mcp(call_api, admin_user, partner_user):
    for _ in range(4):
        gh.make_lead(partner_user)
    client = gh.client()

    dance = gh.oauth_dance(client, call_api, admin_user)
    response = dance["response"]
    body = response.get_json()
    assert set(body) == {"access_token", "token_type", "expires_in", "refresh_token", "scope"}
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["scope"] == "read"
    assert body["access_token"].startswith("pta_")
    assert body["refresh_token"].startswith("ptr_")
    assert body["access_token"] != body["refresh_token"]
    _assert_no_store(response)

    called = _mcp_ok(client, body["access_token"])
    assert called.status_code == 200, called.get_data(as_text=True)
    result = called.get_json()["result"]
    assert result["isError"] is False
    assert len(result["structuredContent"]["untrusted_data"]) == 3
    assert result["structuredContent"]["total"] == 4

    # Audience binding: a token for /mcp is not a token for /api/v1.
    refused = client.get("/api/v1/leads", headers=gh.bearer(body["access_token"]))
    assert refused.status_code == 401
    assert 'error="invalid_token"' in refused.headers["WWW-Authenticate"]


def test_api_v1_dance_works_on_api_v1_and_is_refused_on_mcp(call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user, resource=API_V1_RESOURCE)

    ok = client.get("/api/v1/leads", headers=gh.bearer(dance["access_token"]))
    assert ok.status_code == 200, ok.get_data(as_text=True)

    refused = _mcp_ok(client, dance["access_token"])
    assert refused.status_code == 401
    assert 'error="invalid_token"' in refused.headers["WWW-Authenticate"]


def test_token_rows_store_only_hashes_with_their_lifetimes(call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user)
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT token_hash, kind,
                      extract(epoch FROM (expires_at - created_at)) AS ttl
               FROM oauth_tokens WHERE grant_id = ? ORDER BY kind""",
            (dance["grant_id"],)).fetchall()
        client_row = conn.execute(
            "SELECT last_used_at FROM oauth_clients WHERE client_id = ?",
            (dance["client_id"],)).fetchone()
    finally:
        conn.close()
    by_kind = {row["kind"]: row for row in rows}
    assert set(by_kind) == {"access", "refresh"}
    assert by_kind["access"]["token_hash"] == tokens.token_hash(dance["access_token"])
    assert by_kind["refresh"]["token_hash"] == tokens.token_hash(dance["refresh_token"])
    assert abs(float(by_kind["access"]["ttl"]) - oauth.ACCESS_TTL_SECONDS) < 5
    assert abs(float(by_kind["refresh"]["ttl"]) - oauth.REFRESH_TTL_DAYS * 86400) < 5
    assert client_row["last_used_at"] is not None


# ─── The token endpoint's negative matrix ────────────────────────────

def _token_row(raw):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT kind, rotated_at, revoked_at FROM oauth_tokens WHERE token_hash = ?",
            (tokens.token_hash(raw),)).fetchone()
    finally:
        conn.close()


def _grant_row(grant_id):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT revoked_at, revoked_reason, revoked_by FROM oauth_grants WHERE id = ?",
            (grant_id,)).fetchone()
    finally:
        conn.close()


def _sql(statement, params):
    with get_conn() as conn:
        conn.execute(statement, params)


def _error(response):
    return response.status_code, response.get_json()["error"]


def test_second_redemption_is_invalid_grant_and_revokes_the_first_tokens(call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user)
    assert _mcp_ok(client, dance["access_token"]).status_code == 200

    again = gh.exchange_code(client, dance["client_id"], dance["code"], dance["verifier"],
                             gh.CLAUDE_CALLBACK)
    assert _error(again) == (400, "invalid_grant")
    _assert_no_store(again)
    assert set(again.get_json()) == {"error", "error_description"}

    assert _mcp_ok(client, dance["access_token"]).status_code == 401
    assert _grant_row(dance["grant_id"])["revoked_reason"] == "code_reuse"
    assert _error(gh.refresh(client, dance["client_id"], dance["refresh_token"])) \
        == (400, "invalid_grant")


def _assert_code_is_spent(client, client_id, code, verifier):
    retry = gh.exchange_code(client, client_id, code, verifier, gh.CLAUDE_CALLBACK)
    assert _error(retry) == (400, "invalid_grant")


def test_wrong_verifier_is_invalid_grant_and_spends_the_code(call_api, admin_user):
    client = gh.client()
    client_id, verifier, code = gh.consent_code(client, call_api, admin_user)
    other_verifier, _challenge = gh.pkce_pair()
    response = gh.exchange_code(client, client_id, code, other_verifier, gh.CLAUDE_CALLBACK)
    assert _error(response) == (400, "invalid_grant")
    _assert_code_is_spent(client, client_id, code, verifier)


def test_different_redirect_uri_is_invalid_grant_and_spends_the_code(call_api, admin_user):
    client = gh.client()
    client_id, verifier, code = gh.consent_code(client, call_api, admin_user)
    response = gh.exchange_code(client, client_id, code, verifier,
                                gh.CLAUDE_CALLBACK + "?x=1")
    assert _error(response) == (400, "invalid_grant")
    _assert_code_is_spent(client, client_id, code, verifier)


def test_another_clients_id_is_invalid_grant_and_spends_the_code(call_api, admin_user):
    client = gh.client()
    client_id, verifier, code = gh.consent_code(client, call_api, admin_user)
    other = gh.register_client(client)["client_id"]
    response = gh.exchange_code(client, other, code, verifier, gh.CLAUDE_CALLBACK)
    assert _error(response) == (400, "invalid_grant")
    _assert_code_is_spent(client, client_id, code, verifier)


def test_expired_code_is_invalid_grant_and_spends_the_code(call_api, admin_user):
    client = gh.client()
    client_id, verifier, code = gh.consent_code(client, call_api, admin_user)
    _sql("UPDATE oauth_codes SET expires_at = now() - INTERVAL '1 second' WHERE code_hash = ?",
         (tokens.token_hash(code),))
    response = gh.exchange_code(client, client_id, code, verifier, gh.CLAUDE_CALLBACK)
    assert _error(response) == (400, "invalid_grant")
    _assert_code_is_spent(client, client_id, code, verifier)


def test_resource_other_than_the_codes_is_invalid_target_and_spends_the_code(
        call_api, admin_user):
    client = gh.client()
    client_id, verifier, code = gh.consent_code(client, call_api, admin_user)
    response = gh.exchange_code(client, client_id, code, verifier, gh.CLAUDE_CALLBACK,
                                resource=API_V1_RESOURCE)
    assert _error(response) == (400, "invalid_target")
    _assert_code_is_spent(client, client_id, code, verifier)


def test_matching_resource_in_canonical_form_is_accepted(call_api, admin_user):
    client = gh.client()
    client_id, verifier, code = gh.consent_code(client, call_api, admin_user)
    response = gh.exchange_code(client, client_id, code, verifier, gh.CLAUDE_CALLBACK,
                                resource=MCP_RESOURCE)
    assert response.status_code == 200, response.get_data(as_text=True)


def test_malformed_token_requests(call_api, admin_user):
    client = gh.client()
    client_id, verifier, code = gh.consent_code(client, call_api, admin_user)

    as_json = client.post("/oauth/token", json={
        "grant_type": "authorization_code", "client_id": client_id, "code": code,
        "redirect_uri": gh.CLAUDE_CALLBACK, "code_verifier": verifier})
    assert _error(as_json) == (400, "invalid_request")
    _assert_no_store(as_json)

    password = gh.token_request(client, {"grant_type": "password", "client_id": client_id,
                                         "username": "a", "password": "b"})
    assert _error(password) == (400, "unsupported_grant_type")

    unknown = gh.token_request(client, {"grant_type": "authorization_code",
                                        "client_id": "ptc_unknownclient000000",
                                        "code": code, "redirect_uri": gh.CLAUDE_CALLBACK,
                                        "code_verifier": verifier})
    assert _error(unknown) == (401, "invalid_client")
    _assert_no_store(unknown)

    no_client = gh.token_request(client, {"grant_type": "authorization_code", "code": code})
    assert _error(no_client) == (401, "invalid_client")

    missing = gh.token_request(client, {"grant_type": "authorization_code",
                                        "client_id": client_id, "code": code})
    assert _error(missing) == (400, "invalid_request")

    repeated = client.post(
        "/oauth/token", content_type="application/x-www-form-urlencoded",
        data=f"grant_type=authorization_code&client_id={client_id}&code=a&code=b"
             f"&redirect_uri=x&code_verifier={verifier}")
    assert _error(repeated) == (400, "invalid_request")

    # None of the refused requests spent the code.
    ok = gh.exchange_code(client, client_id, code, verifier, gh.CLAUDE_CALLBACK)
    assert ok.status_code == 200, ok.get_data(as_text=True)


# ─── refresh rotation ──────────────────────────────────────

def test_refresh_rotates_both_tokens(call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user)

    response = gh.refresh(client, dance["client_id"], dance["refresh_token"])
    assert response.status_code == 200, response.get_data(as_text=True)
    _assert_no_store(response)
    body = response.get_json()
    assert set(body) == {"access_token", "token_type", "expires_in", "refresh_token", "scope"}
    assert body["token_type"] == "Bearer" and body["expires_in"] == 3600
    assert body["scope"] == "read"
    assert body["access_token"].startswith("pta_")
    assert body["refresh_token"].startswith("ptr_")
    assert body["access_token"] != dance["access_token"]
    assert body["refresh_token"] != dance["refresh_token"]

    assert _mcp_ok(client, body["access_token"]).status_code == 200
    assert _token_row(dance["refresh_token"])["rotated_at"] is not None
    assert _token_row(body["refresh_token"])["rotated_at"] is None
    assert gh.grant_id_of(body["access_token"]) == dance["grant_id"]


def test_rotated_refresh_within_grace_is_refused_without_revoking(call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user)
    new = gh.refresh(client, dance["client_id"], dance["refresh_token"]).get_json()

    replay = gh.refresh(client, dance["client_id"], dance["refresh_token"])
    assert _error(replay) == (400, "invalid_grant")

    assert _mcp_ok(client, new["access_token"]).status_code == 200
    assert _grant_row(dance["grant_id"])["revoked_at"] is None
    again = gh.refresh(client, dance["client_id"], new["refresh_token"])
    assert again.status_code == 200, again.get_data(as_text=True)


def test_rotated_refresh_after_grace_revokes_the_whole_family(call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user)
    new = gh.refresh(client, dance["client_id"], dance["refresh_token"]).get_json()
    _sql("UPDATE oauth_tokens SET rotated_at = now() - INTERVAL '31 seconds'"
         " WHERE token_hash = ?", (tokens.token_hash(dance["refresh_token"]),))

    replay = gh.refresh(client, dance["client_id"], dance["refresh_token"])
    assert _error(replay) == (400, "invalid_grant")

    grant = _grant_row(dance["grant_id"])
    assert grant["revoked_at"] is not None
    assert grant["revoked_reason"] == "refresh_reuse"
    for raw in (dance["access_token"], new["access_token"]):
        assert _mcp_ok(client, raw).status_code == 401
    assert _error(gh.refresh(client, dance["client_id"], new["refresh_token"])) \
        == (400, "invalid_grant")


def test_refresh_by_another_client_is_invalid_grant(call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user)
    other = gh.register_client(client)["client_id"]
    assert _error(gh.refresh(client, other, dance["refresh_token"])) == (400, "invalid_grant")
    assert gh.refresh(client, dance["client_id"], dance["refresh_token"]).status_code == 200


def test_refresh_with_wrong_scope_or_resource_is_refused_and_keeps_the_token(
        call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user)
    base = {"grant_type": "refresh_token", "client_id": dance["client_id"],
            "refresh_token": dance["refresh_token"]}
    assert _error(gh.token_request(client, dict(base, scope="write"))) \
        == (400, "invalid_scope")
    assert _error(gh.token_request(client, dict(base, resource=API_V1_RESOURCE))) \
        == (400, "invalid_target")
    ok = gh.token_request(client, dict(base, scope="read", resource=MCP_RESOURCE))
    assert ok.status_code == 200, ok.get_data(as_text=True)


def test_refresh_with_an_access_token_or_unknown_token_is_invalid_grant(call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user)
    assert _error(gh.refresh(client, dance["client_id"], dance["access_token"])) \
        == (400, "invalid_grant")
    assert _error(gh.refresh(client, dance["client_id"], "ptr_" + "x" * 43)) \
        == (400, "invalid_grant")
    missing = gh.token_request(client, {"grant_type": "refresh_token",
                                        "client_id": dance["client_id"]})
    assert _error(missing) == (400, "invalid_request")


def test_refresh_after_deactivation_is_invalid_grant(call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user)
    gh.deactivate(admin_user)
    assert _error(gh.refresh(client, dance["client_id"], dance["refresh_token"])) \
        == (400, "invalid_grant")


def test_refresh_after_revoke_grant_is_invalid_grant(call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user)
    assert oauth.revoke_grant(dance["grant_id"], "admin", actor_id=admin_user["id"]) is True
    assert oauth.revoke_grant(dance["grant_id"], "again") is False  # idempotent
    grant = _grant_row(dance["grant_id"])
    assert grant["revoked_reason"] == "admin" and grant["revoked_by"] == admin_user["id"]
    assert _error(gh.refresh(client, dance["client_id"], dance["refresh_token"])) \
        == (400, "invalid_grant")
    assert _mcp_ok(client, dance["access_token"]).status_code == 401


def test_expired_refresh_token_is_invalid_grant(call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user)
    _sql("UPDATE oauth_tokens SET expires_at = now() - INTERVAL '1 second'"
         " WHERE token_hash = ?", (tokens.token_hash(dance["refresh_token"]),))
    assert _error(gh.refresh(client, dance["client_id"], dance["refresh_token"])) \
        == (400, "invalid_grant")


def _age_grant(grant_id, days):
    _sql("UPDATE oauth_grants SET created_at = now() - make_interval(days => CAST(? AS integer))"
         " WHERE id = ?", (days, grant_id))


def test_refresh_after_the_grant_lifetime_is_refused_and_ends_the_connection(
        call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user)
    _age_grant(dance["grant_id"], tokens.OAUTH_GRANT_MAX_DAYS + 1)

    assert _error(gh.refresh(client, dance["client_id"], dance["refresh_token"])) \
        == (400, "invalid_grant")
    grant = _grant_row(dance["grant_id"])
    assert grant["revoked_at"] is not None
    assert grant["revoked_reason"] == "expired"


def test_access_token_of_a_grant_past_its_lifetime_gets_401(call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user)
    _age_grant(dance["grant_id"], tokens.OAUTH_GRANT_MAX_DAYS + 1)

    response = gh.mcp_call(client, dance["access_token"], "tools/list")

    assert response.status_code == 401, response.get_data(as_text=True)


def test_rotation_never_extends_a_connection_past_its_lifetime(call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user)
    _age_grant(dance["grant_id"], tokens.OAUTH_GRANT_MAX_DAYS - 10)

    response = gh.refresh(client, dance["client_id"], dance["refresh_token"])

    assert response.status_code == 200, response.get_data(as_text=True)
    fresh = response.get_json()["refresh_token"]
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT extract(epoch FROM (t.expires_at - (g.created_at
                          + make_interval(days => CAST(? AS integer))))) AS past_grant_end,
                      extract(epoch FROM (t.expires_at - now())) AS left_seconds
               FROM oauth_tokens t JOIN oauth_grants g ON g.id = t.grant_id
               WHERE t.token_hash = ?""",
            (tokens.OAUTH_GRANT_MAX_DAYS, tokens.token_hash(fresh))).fetchone()
    finally:
        conn.close()
    assert abs(float(row["past_grant_end"])) < 5
    assert float(row["left_seconds"]) < 11 * 86400


# ─── POST /oauth/revoke (RFC 7009) ───────────────────────────────────────────

def _revoke(client, token, **extra):
    form = {"token": token}
    form.update(extra)
    from urllib.parse import urlencode
    return client.post("/oauth/revoke", data=urlencode(form),
                       content_type="application/x-www-form-urlencoded")


def test_revoking_an_unknown_token_is_still_200(call_api, admin_user):
    gh.set_integration_settings(enabled=True)
    client = gh.client()
    response = _revoke(client, "ptr_" + "y" * 43)
    assert response.status_code == 200
    assert response.get_json() == {}
    _assert_no_store(response)
    assert client.post("/oauth/revoke", json={"token": "x"}).status_code == 200


def test_revoking_the_refresh_token_revokes_the_grant(call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user)
    response = _revoke(client, dance["refresh_token"], token_type_hint="refresh_token",
                       client_id=dance["client_id"])
    assert response.status_code == 200 and response.get_json() == {}
    assert _mcp_ok(client, dance["access_token"]).status_code == 401
    assert _grant_row(dance["grant_id"])["revoked_reason"] == "client_revoked"


def test_revoking_an_access_token_revokes_only_that_token(call_api, admin_user):
    client = gh.client()
    dance = gh.oauth_dance(client, call_api, admin_user)
    response = _revoke(client, dance["access_token"])
    assert response.status_code == 200 and response.get_json() == {}
    assert _mcp_ok(client, dance["access_token"]).status_code == 401
    assert _grant_row(dance["grant_id"])["revoked_at"] is None
    refreshed = gh.refresh(client, dance["client_id"], dance["refresh_token"])
    assert refreshed.status_code == 200, refreshed.get_data(as_text=True)
    assert _mcp_ok(client, refreshed.get_json()["access_token"]).status_code == 200


# ─── per-client limit ────────────────────────────────────────────────────────

def test_token_requests_are_limited_per_client(call_api, admin_user, monkeypatch):
    client = gh.client()
    client_id, verifier, code = gh.consent_code(client, call_api, admin_user)
    monkeypatch.setattr(oauth, "TOKEN_RATE_PER_MINUTE", 2)
    # A long window so the test cannot straddle a window boundary.
    monkeypatch.setattr(oauth, "TOKEN_WINDOW_SECONDS", 86400)
    for _ in range(2):
        response = gh.refresh(client, client_id, "ptr_" + "z" * 43)
        assert _error(response) == (400, "invalid_grant")
    limited = gh.refresh(client, client_id, "ptr_" + "z" * 43)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
    assert limited.get_json()["error"] == "too_many_requests"
    _assert_no_store(limited)

    # Another client has its own allowance.
    other = gh.register_client(client)["client_id"]
    assert gh.refresh(client, other, "ptr_" + "z" * 43).status_code == 400
