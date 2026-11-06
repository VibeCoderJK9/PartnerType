"""Server-side translations (api/i18n.py, api/locale/pl.json).

The code carries English; the Polish catalog is keyed by that English text.
These checks keep the two in step: every translatable literal has a Polish
entry, every entry is still used, both sides name the same placeholders, and
nothing is translated at import time (which would freeze one language for the
life of the process).
"""
import ast
import json
import re
import string
from pathlib import Path

import pytest

from api import i18n
from api.i18n import language_from_header, t, use_language

ROOT = Path(__file__).resolve().parents[3]
CATALOG = json.loads((ROOT / "api" / "locale" / "pl.json").read_text(encoding="utf-8"))
# t() and N_() mark text for the catalog; _field_error() is the validation
# helper that passes its message straight to t().
_MARKERS = ("t", "N_", "_field_error")


def _source_files():
    files = [p for p in (ROOT / "api").rglob("*.py") if "tests" not in p.parts]
    return files + [ROOT / "server.py"]


def _marked_literals():
    """(file, line, text) for every literal passed to a translation marker."""
    found = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in _MARKERS and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                found.append((path.relative_to(ROOT), node.lineno, node.args[0].value))
    return found


def _placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_translatable_literal_has_a_polish_entry():
    missing = [f"{path}:{line}: {text!r}" for path, line, text in _marked_literals()
               if text not in CATALOG]
    assert not missing, "Add these to api/locale/pl.json:\n" + "\n".join(missing)


def test_every_polish_entry_is_still_used():
    used = {text for _, _, text in _marked_literals()}
    unused = sorted(set(CATALOG) - used)
    assert not unused, f"Unused entries in api/locale/pl.json: {unused}"


def test_both_languages_name_the_same_placeholders():
    mismatched = {
        english: polish for english, polish in CATALOG.items()
        if _placeholders(english) != _placeholders(polish)
    }
    assert not mismatched


def test_the_catalog_is_sorted_for_reviewable_diffs():
    keys = list(CATALOG)
    assert keys == sorted(keys)


def test_nothing_is_translated_at_import_time():
    """t() outside a function runs once, in the default language, for good."""
    offenders = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        inside = set()
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                inside.update(id(node) for node in ast.walk(fn))
        offenders += [
            f"{path.relative_to(ROOT)}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "t" and id(node) not in inside
        ]
    assert not offenders, "Use N_() for constants and t() where they are used: " + ", ".join(offenders)


@pytest.mark.parametrize("header, expected", [
    ("en", "en"),
    ("pl", "pl"),
    ("pl-PL,pl;q=0.9", "pl"),
    ("en-GB,en;q=0.9,pl;q=0.8", "en"),
    # The first language the app speaks wins, not the first tag.
    ("de-DE,de;q=0.9,pl;q=0.8", "pl"),
    # q-values outrank the written order.
    ("en;q=0.5, pl", "pl"),
    ("pl;q=0, en", "en"),
    ("de-DE", "en"),
    ("", "en"),
    (None, "en"),
    ("*", "en"),
])
def test_language_from_header(header, expected):
    assert language_from_header(header) == expected


def test_the_languages_are_the_locale_files():
    on_disk = {path.stem for path in (ROOT / "locales").glob("*.json")}
    assert set(i18n.languages()) == on_disk | {"en"}
    assert i18n.languages()[0] == "en"


def test_every_language_names_itself(tmp_path):
    for code in i18n.languages():
        meta = i18n.language_meta(code)
        assert meta.get("name") and meta.get("english_name") and meta.get("locale"), code


def test_english_is_the_default_without_a_request_language():
    with use_language(None):
        assert i18n.current_language() == "en"
        assert t("Access denied.") == "Access denied."


def test_polish_comes_from_the_catalog():
    with use_language("pl"):
        assert t("Access denied.") == "Brak dostępu."


def test_values_are_filled_in_both_languages():
    message = "Lead {lead} does not exist."
    assert t(message, lang="pl", lead="LD-007") == "Lead LD-007 nie istnieje."
    assert t(message, lang="en", lead="LD-007") == "Lead LD-007 does not exist."


def test_an_unknown_language_falls_back_to_english():
    with use_language("de"):
        assert i18n.current_language() == "en"


def test_a_language_without_a_server_catalog_shows_english(monkeypatch):
    """A community UI translation may come before its server catalog."""
    monkeypatch.setattr(i18n, "languages", lambda: ("en", "pl", "xx"))
    with use_language("xx"):
        assert i18n.current_language() == "xx"
        assert t("Access denied.") == "Access denied."


def test_use_language_restores_the_previous_language():
    with use_language("en"):
        with use_language("pl"):
            assert i18n.current_language() == "pl"
        assert i18n.current_language() == "en"


def test_the_locales_script_carries_every_locale_and_cannot_close_its_tag():
    script = i18n.locales_script()
    assert script.startswith("window.PT_LOCALES = {")
    for code in i18n.languages():
        assert f'"{code}":' in script
    assert "</" not in script


def test_polish_entries_are_polish_and_english_keys_are_not():
    """A cheap guard against swapping the two sides when adding an entry."""
    polish_letters = re.compile("[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
    assert not [english for english in CATALOG if polish_letters.search(english)]


def test_a_new_language_needs_only_its_locale_files(tmp_path, monkeypatch):
    """Drop in locales/de.json and api/locale/de.json: nothing else changes."""
    ui_dir, server_dir = tmp_path / "locales", tmp_path / "server"
    ui_dir.mkdir()
    server_dir.mkdir()
    for path in (ROOT / "locales").glob("*.json"):
        (ui_dir / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    (ui_dir / "de.json").write_text(json.dumps({
        "meta": {"name": "Deutsch", "english_name": "German", "locale": "de-DE"},
        "messages": {"login_title": "Willkommen zurück"},
    }), encoding="utf-8")
    (server_dir / "de.json").write_text(json.dumps({"Access denied.": "Zugriff verweigert."}),
                                        encoding="utf-8")
    monkeypatch.setattr(i18n, "UI_LOCALE_DIR", ui_dir)
    monkeypatch.setattr(i18n, "_CATALOG_DIR", server_dir)
    i18n.ui_locales.cache_clear()
    i18n.catalog.cache_clear()
    try:
        assert "de" in i18n.languages()
        assert language_from_header("de-AT,de;q=0.9,en;q=0.5") == "de"
        assert t("Access denied.", lang="de") == "Zugriff verweigert."
        assert t("Invalid request.", lang="de") == "Invalid request."  # not translated yet
        assert '"de":' in i18n.locales_script()

        from api.agent import _system_instruction
        assert "Always answer in German (Deutsch)" in _system_instruction({"role": "partner"}, "de")
    finally:
        i18n.ui_locales.cache_clear()
        i18n.catalog.cache_clear()
