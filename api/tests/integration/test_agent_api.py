"""Authenticated router coverage for local agent and confirmation endpoints."""

from api.agent_tools import execute_tool
from api.db import get_conn


def _login(call_api, user):
    status, body = call_api(
        "POST",
        "/api/auth/login",
        body={
            "email": user["email"],
            "password": user["plain_password"],
            "role": user["role"],
        },
    )
    assert status == 200
    return body["token"]


def _submission():
    return {
        "client_name": "Agent API Client",
        "client_email": "agent-api@test.pl",
        "client_phone": "",
        "company": "Agent API Co",
        "value": 12000,
        "notes": "",
        "sector": "IT",
        "nip": "2222222222",
        "website": "agent-api.pl",
        "product_id": 1,
        "quantity": 1,
    }


def test_status_and_chat_are_available_to_partner_without_cloud(
    call_api, partner_user, monkeypatch
):
    token = _login(call_api, partner_user)
    monkeypatch.setattr(
        "api.agent.ollama_status",
        lambda: {
            "available": True,
            "engine": "ollama",
            "model": "partnerhub-agent",
            "message": "Lokalny model jest gotowy.",
        },
    )
    seen = {}

    def _chat(user, prompt, lang="pl"):
        seen["lang"] = lang
        return {"response": "To są Twoje dane.", "action": None}

    monkeypatch.setattr("api.agent._ollama_chat", _chat)

    status_code, status = call_api("GET", "/api/agent/status", token=token)
    chat_code, chat = call_api(
        "POST", "/api/agent/chat", body={"prompt": "Pokaż moje leady"}, token=token
    )

    assert status_code == 200
    assert status["available"] is True
    assert chat_code == 200
    assert chat["provider"] == "ollama-local"
    # No language asked for means the app's default, not a refusal.
    assert seen["lang"] == "pl"
    assert chat["response"] == "To są Twoje dane."


def test_confirm_route_executes_prepared_partner_action_once(
    call_api, partner_user
):
    token = _login(call_api, partner_user)
    prepared = execute_tool(partner_user, "prepare_create_lead", _submission())
    action_id = prepared["action"]["id"]

    before = get_conn()
    try:
        assert before.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 0
    finally:
        before.close()

    status, body = call_api(
        "POST", f"/api/agent/actions/{action_id}/confirm", body={}, token=token
    )
    replay_status, replay = call_api(
        "POST", f"/api/agent/actions/{action_id}/confirm", body={}, token=token
    )

    assert status == 200
    assert body["result"]["queue_position"] == 1
    assert replay_status == 404
    assert replay["code"] == "not_found"


def test_agent_routes_still_require_authentication(call_api):
    status, body = call_api("GET", "/api/agent/status")
    assert status == 401
    assert "error" in body


def test_chat_fails_closed_when_local_model_is_unavailable(
    call_api, partner_user, monkeypatch
):
    token = _login(call_api, partner_user)
    monkeypatch.setattr(
        "api.agent.ollama_status",
        lambda: {
            "available": False,
            "engine": "ollama",
            "model": "partnerhub-agent",
            "message": "Ollama nie działa na tym komputerze.",
        },
    )

    status, body = call_api(
        "POST", "/api/agent/chat", body={"prompt": "Pokaż leady"}, token=token
    )

    assert status == 503
    assert body["code"] == "local_model_unavailable"


def test_an_instance_without_the_assistant_says_so_and_never_calls_a_model(
    call_api, partner_user, monkeypatch
):
    # An instance can run without the assistant (PARTNERTYPE_AI=off).
    monkeypatch.setenv("PARTNERTYPE_AI", "off")
    token = _login(call_api, partner_user)

    def _never(*args, **kwargs):
        raise AssertionError("no model may be asked on an instance without one")

    monkeypatch.setattr("api.agent._ollama_chat", _never)
    monkeypatch.setattr("api.agent._request_json", _never)

    status_code, status = call_api("GET", "/api/agent/status", token=token)
    chat_code, chat = call_api(
        "POST", "/api/agent/chat", body={"prompt": "Pokaż leady"}, token=token
    )

    assert status_code == 200
    assert status["disabled"] is True and status["available"] is False
    assert chat_code == 404
    assert chat["code"] == "assistant_disabled"
