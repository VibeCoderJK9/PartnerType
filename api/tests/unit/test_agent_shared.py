"""What a busy Ollama engine means to the person asking."""
import io
import urllib.error

import pytest

from api import agent


class Captured:
    def __init__(self, error=None):
        self.error, self.request = error, None

    def open(self, request, timeout):
        self.request = request
        if self.error:
            raise self.error
        return io.BytesIO(b'{"models": []}')


def test_a_busy_engine_is_busy_not_broken(monkeypatch):
    busy = urllib.error.HTTPError("http://127.0.0.1:11434/api/chat", 429, "busy", {}, io.BytesIO(b"{}"))
    monkeypatch.setattr(agent, "_local_opener", Captured(busy))
    with pytest.raises(agent.OllamaBusyError):
        agent._request_json("/api/chat", {"model": "partnerhub-agent"})


def test_the_person_is_told_to_try_again_and_the_question_is_not_kept(monkeypatch, partner_user):
    monkeypatch.setattr(agent, "ollama_status", lambda: {"available": True, "model": "partnerhub-agent", "message": ""})

    def busy(user, prompt, lang="pl"):
        agent._messages_for(user, prompt, lang)
        raise agent.OllamaBusyError("busy")

    monkeypatch.setattr(agent, "_ollama_chat", busy)
    status, body = agent.handle_chat(None, partner_user, {"prompt": "Pokaż moje leady"})

    assert status == 429 and body["code"] == "local_model_busy"
    session = agent._sessions.get(agent._session_key(partner_user))
    assert all(message.get("content") != "Pokaż moje leady" for message in session["messages"])
