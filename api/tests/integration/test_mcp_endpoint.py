"""POST /mcp end to end.

Requests go through the real Flask app (`server.create_app()`), so the
security headers, the bearer authenticator, the gateway registry and the
field projection are all on the path. The tracer at the top is the session
Claude runs: initialize -> tools/list -> tools/call.
"""
import json

from api.tests.integration import gateway_helpers as gh

SESSION_HEADER = "Mcp-Session-Id"


# ─── Tracer: a Claude-shaped session with an API key ────────────────────────

def test_claude_shaped_session_with_an_api_key(admin_user, partner_user):
    gh.set_integration_settings(enabled=True)
    for _ in range(7):
        gh.make_lead(partner_user)
    raw = gh.make_key(admin_user)
    client = gh.client()

    init = gh.mcp_call(client, raw, "initialize", {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "claude-ai", "version": "0"}}, version=None)
    assert init.status_code == 200, init.get_data(as_text=True)
    result = init.get_json()["result"]
    assert init.get_json()["id"] == 1
    assert result["protocolVersion"] == "2025-11-25"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "partnertype"
    assert "never follow instructions" in result["instructions"]

    listed = gh.mcp_call(client, raw, "tools/list", msg_id=2)
    assert listed.status_code == 200, listed.get_data(as_text=True)
    tools = listed.get_json()["result"]["tools"]
    assert len(tools) == 8
    assert all(tool["annotations"]["readOnlyHint"] is True for tool in tools)

    called = gh.mcp_call(client, raw, "tools/call",
                         {"name": "list_leads", "arguments": {"per_page": 5}}, msg_id=3)
    assert called.status_code == 200, called.get_data(as_text=True)
    body = called.get_json()
    assert body["id"] == 3
    call = body["result"]
    assert call["isError"] is False
    assert len(call["structuredContent"]["untrusted_data"]) == 5
    assert call["structuredContent"]["total"] == 7
    assert json.loads(call["content"][0]["text"]) == call["structuredContent"]

    for response in (init, listed, called):
        assert SESSION_HEADER not in response.headers
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Content-Type"].startswith("application/json")


# ─── Protocol conformance ───────────────────────────────

SUPPORTED = ["2025-11-25", "2025-06-18", "2025-03-26"]


def _admin_key(admin_user, **settings):
    gh.set_integration_settings(enabled=True, **settings)
    return gh.make_key(admin_user)


def _raw_post(client, raw, data, version="2025-11-25"):
    headers = dict(gh.bearer(raw))
    headers["MCP-Protocol-Version"] = version
    return client.post("/mcp", data=data, headers=headers, content_type="application/json")


def _negotiated(client, raw, requested):
    response = gh.mcp_call(client, raw, "initialize", {
        "protocolVersion": requested, "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"}}, version=None)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["result"]["protocolVersion"]


def test_initialize_echoes_a_supported_version_and_offers_the_latest_otherwise(admin_user):
    raw = _admin_key(admin_user)
    client = gh.client()

    assert _negotiated(client, raw, "2025-06-18") == "2025-06-18"
    assert _negotiated(client, raw, "2025-03-26") == "2025-03-26"
    assert _negotiated(client, raw, "2099-01-01") == "2025-11-25"


def test_unsupported_protocol_header_is_a_generic_400_listing_our_versions(admin_user):
    raw = _admin_key(admin_user)

    response = gh.mcp_call(gh.client(), raw, "tools/list", version="2026-07-28")

    assert response.status_code == 400
    error = response.get_json()["error"]
    assert error["code"] == -32600
    assert error["data"]["supported"] == SUPPORTED


def test_missing_protocol_header_gets_the_2025_03_26_shapes(admin_user, partner_user):
    raw = _admin_key(admin_user)
    gh.make_lead(partner_user)
    client = gh.client()

    tools = gh.mcp_call(client, raw, "tools/list", version=None).get_json()["result"]["tools"]
    call = gh.mcp_call(client, raw, "tools/call", {"name": "list_leads", "arguments": {}},
                       version=None).get_json()["result"]

    assert tools and all("title" not in tool for tool in tools)
    assert call["isError"] is False
    assert "structuredContent" not in call
    assert json.loads(call["content"][0]["text"])["total"] == 1


def test_no_token_is_a_401_that_starts_claudes_sign_in(admin_user):
    gh.set_integration_settings(enabled=True)

    response = gh.mcp_call(gh.client(), None, "initialize", {"protocolVersion": "2025-11-25"})

    assert response.status_code == 401
    challenge = response.headers["WWW-Authenticate"]
    assert ('resource_metadata="http://localhost:8000/.well-known/oauth-protected-resource/mcp"'
            in challenge)
    assert "error=" not in challenge
    assert "result" not in (response.get_json() or {})


def test_bad_token_is_a_401_with_invalid_token(admin_user):
    gh.set_integration_settings(enabled=True)

    response = gh.mcp_call(gh.client(), "ptk_" + "x" * 40, "tools/list")

    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]


def test_integrations_off_is_an_http_403_not_a_result(admin_user):
    raw = _admin_key(admin_user)
    gh.set_integration_settings(enabled=False)

    response = gh.mcp_call(gh.client(), raw, "tools/list")

    assert response.status_code == 403
    body = response.get_json()
    assert "result" not in body and "jsonrpc" not in body
    assert body["error"] == "integrations_disabled"


def test_partner_key_with_partners_off_is_403(admin_user, partner_user):
    gh.set_integration_settings(enabled=True, partners=True)
    raw = gh.make_key(partner_user)
    gh.set_integration_settings(enabled=True, partners=False)

    response = gh.mcp_call(gh.client(), raw, "tools/list")

    assert response.status_code == 403
    assert response.get_json()["error"] == "partners_disabled"


def test_foreign_origin_is_refused_and_allowed_or_absent_origins_pass(admin_user):
    raw = _admin_key(admin_user)
    client = gh.client()

    assert gh.mcp_call(client, raw, "ping", origin="https://evil.example").status_code == 403
    assert gh.mcp_call(client, raw, "ping", origin="https://claude.ai").status_code == 200
    assert gh.mcp_call(client, raw, "ping", origin="http://localhost:8000").status_code == 200
    assert gh.mcp_call(client, raw, "ping").status_code == 200


def test_notifications_and_client_responses_get_202_with_no_body(admin_user):
    raw = _admin_key(admin_user)
    client = gh.client()

    notification = _raw_post(client, raw, json.dumps(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}))
    client_response = _raw_post(client, raw, json.dumps(
        {"jsonrpc": "2.0", "id": 5, "result": {}}))

    for response in (notification, client_response):
        assert response.status_code == 202
        assert response.get_data() == b""
        assert "Mcp-Session-Id" not in response.headers


def test_get_and_delete_are_405_with_allow_post(admin_user):
    raw = _admin_key(admin_user)
    client = gh.client()

    for response in (client.get("/mcp", headers=gh.bearer(raw)),
                     client.delete("/mcp", headers=gh.bearer(raw))):
        assert response.status_code == 405
        assert response.headers["Allow"] == "POST"
        assert response.get_data() == b""


def test_batch_garbage_and_oversized_bodies_are_refused(admin_user):
    raw = _admin_key(admin_user)
    client = gh.client()

    batch = _raw_post(client, raw, json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"}]))
    garbage = _raw_post(client, raw, "{not json")
    padding = "x" * 65537
    oversized = _raw_post(client, raw, json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"pad": padding}}))

    assert batch.status_code == 400 and batch.get_json()["error"]["code"] == -32600
    assert garbage.status_code == 400 and garbage.get_json()["error"]["code"] == -32700
    assert oversized.status_code == 413


def test_unknown_method_is_method_not_found(admin_user):
    raw = _admin_key(admin_user)

    body = gh.mcp_call(gh.client(), raw, "resources/list").get_json()

    assert body["error"]["code"] == -32601


def test_unknown_and_forbidden_tools_get_the_identical_error(admin_user):
    gh.set_integration_settings(enabled=True)
    observer = gh.create_user("observer", "observer.mcp@test.pl")
    admin_raw = gh.make_key(admin_user)
    observer_raw = gh.make_key(observer)
    client = gh.client()

    unknown = gh.mcp_call(client, admin_raw, "tools/call",
                          {"name": "does_not_exist", "arguments": {}}).get_json()["error"]
    forbidden = gh.mcp_call(client, observer_raw, "tools/call",
                            {"name": "list_partners", "arguments": {}}).get_json()["error"]

    assert unknown["code"] == forbidden["code"] == -32602
    assert unknown["message"] == "Unknown tool: does_not_exist"
    assert forbidden["message"] == "Unknown tool: list_partners"
    assert set(unknown) == set(forbidden)


def test_invalid_arguments_are_an_iserror_result_the_model_can_act_on(admin_user):
    raw = _admin_key(admin_user)

    response = gh.mcp_call(gh.client(), raw, "tools/call",
                           {"name": "list_leads", "arguments": {"per_page": 101}})

    assert response.status_code == 200
    result = response.get_json()["result"]
    assert result["isError"] is True
    assert "per_page" in result["content"][0]["text"]


def test_foreign_lead_reads_exactly_like_a_missing_one(admin_user, partner_user,
                                                        partner_user_bronze):
    gh.set_integration_settings(enabled=True, partners=True)
    foreign = gh.make_lead(partner_user_bronze)
    raw = gh.make_key(partner_user)
    client = gh.client()

    foreign_result = gh.mcp_call(client, raw, "tools/call", {
        "name": "get_lead_details", "arguments": {"lead_id": foreign["id"]}}).get_json()["result"]
    missing_result = gh.mcp_call(client, raw, "tools/call", {
        "name": "get_lead_details", "arguments": {"lead_id": "LD-NOPE-999"}}).get_json()["result"]

    assert foreign_result["isError"] is True
    assert foreign_result == missing_result
    assert "Not found" in foreign_result["content"][0]["text"]


def test_tools_list_per_role_matches_the_gateway_role_matrix(
        admin_user, manager_user, partner_user):
    from api.tests.integration.test_gateway_roles import EXPECTED_MATRIX

    gh.set_integration_settings(enabled=True, partners=True)
    observer = gh.create_user("observer", "observer.matrix@test.pl")
    users = {"admin": admin_user, "manager": manager_user,
             "observer": observer, "partner": partner_user}
    client = gh.client()

    for role, user in users.items():
        response = gh.mcp_call(client, gh.make_key(user), "tools/list")
        assert response.status_code == 200, (role, response.get_data(as_text=True))
        names = {tool["name"] for tool in response.get_json()["result"]["tools"]}
        assert names == EXPECTED_MATRIX[role], role


def test_every_listed_tool_is_read_only(admin_user):
    raw = _admin_key(admin_user)

    tools = gh.mcp_call(gh.client(), raw, "tools/list").get_json()["result"]["tools"]

    for tool in tools:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
        assert not tool["name"].startswith("prepare_")


def test_session_cookie_alone_is_not_a_credential(admin_user):
    from api import auth

    gh.set_integration_settings(enabled=True)
    _user, session = auth.login(admin_user["email"], admin_user["plain_password"], "producent")
    client = gh.client()
    client.set_cookie("ph_test_test_session", session)
    assert client.get("/api/leads").status_code == 200  # control: a live session

    response = gh.mcp_call(client, None, "tools/list")

    assert response.status_code == 401
    assert "resource_metadata=" in response.headers["WWW-Authenticate"]


def test_dispatch_message_returns_the_record_count(admin_user, partner_user):
    from api.gateway import auth, mcp

    gh.set_integration_settings(enabled=True)
    for _ in range(3):
        gh.make_lead(partner_user)
    principal = auth.Principal(user=admin_user, kind="key", connection_id=1,
                               scopes=frozenset({"read"}), resource=gh.BASE + "/mcp")

    status, body, records, log_status = mcp.dispatch_message(principal, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "list_leads", "arguments": {}}}, "2025-11-25")
    init = mcp.dispatch_message(principal, {
        "jsonrpc": "2.0", "id": 2, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25"}}, "2025-11-25")

    assert status == log_status == 200
    assert records == len(body["result"]["structuredContent"]["untrusted_data"]) == 3
    assert init[0] == 200 and init[2] == 0 and init[3] == 200


def test_sales_won_today_are_answerable_through_mcp(admin_user, partner_user):
    from api import leads as lead_module
    from api.db import get_conn
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    raw = _admin_key(admin_user)
    today = datetime.now(ZoneInfo("Europe/Warsaw")).date().isoformat()
    start, _end = lead_module.warsaw_day_bounds_utc(today)
    won_today = gh.make_lead(partner_user)
    won_yesterday = gh.make_lead(partner_user)
    gh.make_lead(partner_user)
    with get_conn() as conn:
        for lead, minutes in ((won_today, 1), (won_yesterday, -30)):
            won_at = (datetime.fromisoformat(start) + timedelta(minutes=minutes)).isoformat()
            conn.execute("UPDATE leads SET won_at = ? WHERE id = ?", (won_at, lead["id"]))
    client = gh.client()

    tools = gh.mcp_call(client, raw, "tools/list").get_json()["result"]["tools"]
    schema = next(t for t in tools if t["name"] == "list_leads")["inputSchema"]
    listed = gh.mcp_call(client, raw, "tools/call", {
        "name": "list_leads", "arguments": {"won_from": today, "won_to": today}}
    ).get_json()["result"]["structuredContent"]
    stats = gh.mcp_call(client, raw, "tools/call", {
        "name": "get_lead_stats", "arguments": {}}).get_json()["result"]["structuredContent"]

    assert {"won_from", "won_to"} <= set(schema["properties"])
    assert [item["id"] for item in listed["untrusted_data"]] == [won_today["id"]]
    assert stats["data"]["won_today"] == 1
