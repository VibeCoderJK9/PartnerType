"""The assistant's instructions are English; its answers are in the user's language."""
import re

import pytest

from api.agent import _system_instruction
from api.agent_tools import TOOL_REGISTRY

POLISH_LETTERS = re.compile("[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")


@pytest.mark.parametrize("role", ["admin", "partner"])
@pytest.mark.parametrize("lang, language", [("pl", "Polish (Polski)"), ("en", "English")])
def test_the_prompt_names_the_reply_language_first_and_last(role, lang, language):
    prompt = _system_instruction({"role": role}, lang)

    rules = [line for line in prompt.splitlines() if language in line]
    assert prompt.index(f"Always answer in {language}") < prompt.index("PERMISSION CONTEXT")
    assert rules[-1] == f"- Reply language: {language}. Every answer, without exception."
    assert prompt.rstrip().endswith("/no_think")


@pytest.mark.parametrize("lang", ["pl", "en", "unexpected"])
def test_the_prompt_itself_is_english(lang):
    for role in ("admin", "partner"):
        prompt = _system_instruction({"role": role}, lang)
        # "Polish (Polski)" names the language; nothing else is Polish.
        assert not POLISH_LETTERS.search(prompt)


def test_an_unknown_language_asks_for_english():
    assert "Always answer in English," in _system_instruction({"role": "partner"}, "de")


def test_the_reply_language_comes_from_the_locale_files(monkeypatch):
    """A community translation teaches the assistant its language too."""
    from api import agent
    monkeypatch.setattr(agent, "normalize_language", lambda code: code)
    monkeypatch.setattr(agent, "language_meta",
                        lambda code: {"name": "Deutsch", "english_name": "German", "locale": "de-DE"})
    assert "Always answer in German (Deutsch)" in _system_instruction({"role": "admin"}, "de")


def test_tool_declarations_are_english():
    def texts(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from texts(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from texts(item)
        elif isinstance(value, str):
            yield value

    assert not [text for text in texts(TOOL_REGISTRY) if POLISH_LETTERS.search(text)]
