"""Every locale file is one a translator can trust.

The interface files are locales/<code>.json; the server catalogs are
api/locale/<code>.json. English is the source of both. Checked for every file,
so a community translation is held to the same rules as Polish (TRANSLATING.md).
"""
import json
import re

import pytest

from tools import i18n_coverage

UI = i18n_coverage.ui_locales()
SERVER = i18n_coverage.server_catalogs()
ENGLISH = UI["en"]["messages"]
SOURCE_TEXTS = i18n_coverage.server_source_texts()
# The languages this repository ships; each must stay complete.
SHIPPED = ("en", "pl")


def test_english_is_shipped_and_every_file_is_valid_json():
    assert "en" in UI
    for directory in (i18n_coverage.UI_DIR, i18n_coverage.SERVER_DIR):
        for path in directory.glob("*.json"):
            json.loads(path.read_text(encoding="utf-8"))
            assert re.fullmatch(r"[a-z]{2,3}", path.stem), f"{path.name}: use a language code"


@pytest.mark.parametrize("code", sorted(UI))
def test_interface_file_shape(code):
    locale = UI[code]
    assert set(locale) == {"meta", "messages"}, code
    meta = locale["meta"]
    assert meta.get("name"), f"{code}: meta.name is the language's own name, e.g. Deutsch"
    assert meta.get("english_name"), f"{code}: meta.english_name, e.g. German"
    assert re.fullmatch(r"[a-z]{2,3}(-[A-Z]{2})?", meta.get("locale", "")), f"{code}: meta.locale, e.g. de-DE"
    assert meta["locale"].split("-")[0] == code


@pytest.mark.parametrize("code", sorted(UI))
def test_interface_file_has_no_unknown_keys(code):
    unknown = sorted(set(UI[code]["messages"]) - set(ENGLISH))
    assert not unknown, f"{code}: keys not in locales/en.json: {unknown}"


@pytest.mark.parametrize("code", sorted(UI))
def test_interface_messages_keep_the_english_placeholders_and_shape(code):
    problems = []
    for key, value in UI[code]["messages"].items():
        source = ENGLISH[key]
        if isinstance(source, dict) != isinstance(value, dict):
            problems.append(f"{key}: plural in one language, plain text in the other")
            continue
        if isinstance(value, dict) and "other" not in value:
            problems.append(f"{key}: a plural needs an 'other' form")
        if not isinstance(value, (str, dict)):
            problems.append(f"{key}: must be text")
        if i18n_coverage.placeholders(value) != i18n_coverage.placeholders(source):
            problems.append(f"{key}: placeholders differ from English")
    assert not problems, f"{code}: " + "; ".join(problems)


@pytest.mark.parametrize("code", sorted(SERVER))
def test_server_catalog_matches_the_code(code):
    assert code in UI, f"api/locale/{code}.json needs locales/{code}.json (the language's name lives there)"
    catalog = SERVER[code]
    unknown = sorted(set(catalog) - SOURCE_TEXTS)
    assert not unknown, f"{code}: texts the code no longer uses: {unknown[:5]}"
    differ = [text for text, value in catalog.items()
              if i18n_coverage.placeholders(value) != i18n_coverage.placeholders(text)]
    assert not differ, f"{code}: placeholders differ: {differ[:5]}"


def test_shipped_languages_are_complete():
    rows = i18n_coverage.coverage()
    incomplete = [f"{area} {code} {done}/{total}" for area, code, done, total in rows
                  if code in SHIPPED and done < total]
    assert not incomplete
    assert {code for _, code, _, _ in rows} >= set(SHIPPED)


def test_the_coverage_script_prints_a_line_per_language(capsys):
    assert i18n_coverage.main([]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^interface\s+pl\s+100%", out, re.M)
    assert re.search(r"^server\s+pl\s+100%", out, re.M)


@pytest.mark.parametrize("code", sorted(UI))
def test_interface_messages_carry_no_markup(code):
    """Some screens build HTML around a message, so a message must never bring its own:
    a translation with markup would run in every user's browser."""
    with_markup = [key for key, value in UI[code]["messages"].items()
                   if re.search(r"[<>]", json.dumps(value, ensure_ascii=False))]
    assert not with_markup, f"{code}: '<' or '>' in {with_markup[:5]}"


@pytest.mark.parametrize("code", sorted(SERVER))
def test_server_messages_keep_exactly_the_english_markup(code):
    """A few e-mail sentences carry <strong>; a translation keeps those tags and adds none."""
    tags = re.compile(r"</?[a-zA-Z][^>]*>")
    differ = [text for text, value in SERVER[code].items()
              if sorted(tags.findall(value)) != sorted(tags.findall(text))
              or value.count("<") != text.count("<") or value.count(">") != text.count(">")]
    assert not differ, f"{code}: markup differs from English in {differ[:5]}"
