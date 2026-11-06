"""The owner's whole gateway journey in one ordered test.

This is the README path of a producer, end to end, through the real
endpoints only: the admin switches integrations on and creates a key through
the app's own API (`/api/integrations/*` via the legacy router, Origin check
and session included), Claude discovers the server (`/.well-known/*`), gets a
401 that starts its sign-in, runs an MCP session with the key, meets the
transport rules (GET 405, unsupported protocol version 400), the same key
reads `/api/v1`, the public OpenAPI document answers, the „Połącz” OAuth
flow issues an access token that also works on `/mcp`, and every
authenticated call lands in the call log under the right surface and
connection kind.

Every step is covered in more detail by its own module (test_integrations_api,
test_oauth_flow, test_mcp_endpoint, test_api_v1, test_gateway_logging); this
test proves the pieces fit together in the order the owner meets them.
`scripts/mcp-smoke.sh` walks the same path against a live instance with curl.
"""
import json

from api.gateway import calllog
from api.tests.integration import gateway_helpers as gh

ORIGIN = {"Origin": gh.BASE}
PRM_PATH = "/.well-known/oauth-protected-resource/mcp"


def _initialize(client, raw, msg_id=1):
    return gh.mcp_call(client, raw, "initialize", {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "smoke", "version": "0"}}, msg_id=msg_id, version=None)


def _list_leads(client, raw, msg_id, per_page=1):
    return gh.mcp_call(client, raw, "tools/call",
                       {"name": "list_leads", "arguments": {"per_page": per_page}},
                       msg_id=msg_id)


def test_owner_journey_end_to_end(call_api, admin_user, partner_user):
    for _ in range(3):
        gh.make_lead(partner_user)
    client = gh.client()

    # 1. Admin signs in and switches integrations on (RODO acknowledgement).
    session = gh.session_token(call_api, admin_user)
    status, body = call_api("PATCH", "/api/integrations/settings", token=session,
                            body={"enabled": True, "acknowledge_eu_transfer": True},
                            headers=ORIGIN)
    assert status == 200, body
    assert body["enabled"] is True
    assert body["mcp_url"] == gh.BASE + "/mcp"

    # 2. Admin creates a key; the raw secret is shown once.
    status, created = call_api("POST", "/api/integrations/keys", token=session,
                               body={"name": "Smoke", "expires_in_days": 30},
                               headers=ORIGIN)
    assert status == 201, created
    raw = created["key"]
    assert raw.startswith("ptk_")

    # 3. Discovery: the protected-resource and authorization-server documents.
    prm = client.get(PRM_PATH)
    assert prm.status_code == 200
    assert prm.get_json()["resource"] == gh.BASE + "/mcp"
    assert prm.get_json()["authorization_servers"] == [gh.BASE]
    asm = client.get("/.well-known/oauth-authorization-server")
    assert asm.status_code == 200
    assert asm.get_json()["issuer"] == gh.BASE
    assert asm.get_json()["token_endpoint"] == gh.BASE + "/oauth/token"

    # 4. POST /mcp without a token: 401 whose challenge points at the PRM document.
    anonymous = gh.mcp_call(client, None, "initialize", {"protocolVersion": "2025-11-25"})
    assert anonymous.status_code == 401
    assert (f'resource_metadata="{gh.BASE}{PRM_PATH}"'
            in anonymous.headers["WWW-Authenticate"])

    # 5. An MCP session with the key: initialize -> tools/list -> tools/call.
    init = _initialize(client, raw)
    assert init.status_code == 200, init.get_data(as_text=True)
    assert init.get_json()["result"]["serverInfo"]["name"] == "partnertype"
    listed = gh.mcp_call(client, raw, "tools/list", msg_id=2)
    assert listed.status_code == 200, listed.get_data(as_text=True)
    tools = listed.get_json()["result"]["tools"]
    assert "list_leads" in {tool["name"] for tool in tools}
    assert all(tool["annotations"]["readOnlyHint"] is True for tool in tools)
    called = _list_leads(client, raw, msg_id=3)
    assert called.status_code == 200, called.get_data(as_text=True)
    result = called.get_json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["total"] == 3
    assert len(result["structuredContent"]["untrusted_data"]) == 1

    # 6. Transport rules: GET is 405, an unsupported protocol version is 400.
    get = client.get("/mcp", headers=gh.bearer(raw))
    assert get.status_code == 405
    assert get.headers["Allow"] == "POST"
    future = gh.mcp_call(client, raw, "tools/list", msg_id=4, version="2026-07-28")
    assert future.status_code == 400
    assert future.get_json()["error"]["code"] == -32600

    # 7. The same key reads /api/v1; the OpenAPI document is public.
    rest = client.get("/api/v1/leads?per_page=1", headers=gh.bearer(raw))
    assert rest.status_code == 200, rest.get_data(as_text=True)
    assert rest.get_json()["total"] == 3
    openapi = client.get("/api/v1/openapi.json")
    assert openapi.status_code == 200
    assert openapi.get_json()["servers"][0]["url"] == gh.BASE + "/api/v1"

    # 8. "Connect": the OAuth flow, then the access token works on /mcp too.
    dance = gh.oauth_dance(client, call_api, admin_user)
    access = dance["access_token"]
    assert _initialize(client, access, msg_id=10).status_code == 200
    via_oauth = _list_leads(client, access, msg_id=11)
    assert via_oauth.status_code == 200, via_oauth.get_data(as_text=True)
    assert via_oauth.get_json()["result"]["structuredContent"]["total"] == 3

    # 9. The call log has the calls, by surface and connection kind -- and no content.
    rows = calllog.list_calls(limit=200)
    seen = {(row["surface"], row["connection_kind"]) for row in rows}
    assert ("mcp", "key") in seen
    assert ("api_v1", "key") in seen
    assert ("mcp", "oauth") in seen
    oauth_rows = [row for row in rows if row["connection_kind"] == "oauth"]
    assert {row["connection_id"] for row in oauth_rows} == {dance["grant_id"]}
    assert any(row["operation"] == "list_leads" and row["records"] == 1
               for row in rows if row["surface"] == "mcp")
    dumped = json.dumps(rows, default=str)
    for secret in (raw, access, dance["refresh_token"]):
        assert secret not in dumped
