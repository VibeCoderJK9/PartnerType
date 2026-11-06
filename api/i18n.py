"""Server-side translations for text a user reads (errors, e-mails, agent cards).

The source text is English and lives in the code. Each other language has a
catalog `api/locale/<code>.json`, keyed by the English text, so the code reads
as one language and the catalog is the one place a translator edits.

Which languages exist is decided by the files, not by code: every
`locales/<code>.json` (the interface dictionaries, which also carry each
language's own name) is a language the app offers. A language without a
server catalog simply shows the server's English. See TRANSLATING.md.

The language is chosen per request: the web app sends its language switch in
the `Accept-Language` header and `api/router.py` runs the handler inside
`use_language()`. The first language in that header the app speaks wins;
anything else gets English. Background mail keeps the caller's language because
`email_service.run_in_background` copies the context.

`t()` translates at call time, so a module-level constant must hold the English
text and call `t()` where the text is used. `N_()` only marks a literal for the
catalog check (`api/tests/unit/test_i18n.py`) without translating it, for text
such as field labels that is translated later by the code that shows it.
"""
import contextvars
import json
import re
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

DEFAULT_LANGUAGE = "en"
ROOT = Path(__file__).resolve().parent.parent
UI_LOCALE_DIR = ROOT / "locales"
_CATALOG_DIR = Path(__file__).with_name("locale")
_CODE_RE = re.compile(r"^[a-z]{2,3}$")


@lru_cache(maxsize=None)
def ui_locales():
    """Every interface locale file, by language code: {"meta": ..., "messages": ...}."""
    locales = {}
    for path in sorted(UI_LOCALE_DIR.glob("*.json")):
        if _CODE_RE.match(path.stem):
            locales[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return locales


def languages():
    """Codes of every language the app offers, English first."""
    codes = set(ui_locales()) | {DEFAULT_LANGUAGE}
    return tuple(sorted(codes, key=lambda code: (code != DEFAULT_LANGUAGE, code)))


def language_meta(code):
    """The `meta` block of a language: its own name, English name and locale."""
    return dict(ui_locales().get(code, {}).get("meta") or {})


_language = contextvars.ContextVar("partnertype_language", default=DEFAULT_LANGUAGE)


@lru_cache(maxsize=None)
def catalog(lang):
    """The translations for `lang` (English source text -> translated text)."""
    path = _CATALOG_DIR / f"{lang}.json"
    if lang == DEFAULT_LANGUAGE or not _CODE_RE.match(lang or "") or not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(lang):
    """A supported language code, or the default for anything else."""
    return lang if lang in languages() else DEFAULT_LANGUAGE


def current_language():
    return _language.get()


@contextmanager
def use_language(lang):
    """Run the block with `lang` as the language of every `t()` call."""
    token = _language.set(normalize(lang))
    try:
        yield
    finally:
        _language.reset(token)


def language_from_header(value):
    """The language of an `Accept-Language` header.

    Tags are taken in order of preference (their q-values, then their order);
    the first whose language the app speaks wins, so "de-DE, pl;q=0.8" gives
    Polish when there is no German. Nothing usable gives English.
    """
    if not value or not isinstance(value, str):
        return DEFAULT_LANGUAGE
    ranked = []
    for index, part in enumerate(value.split(",")):
        pieces = part.strip().split(";")
        tag = pieces[0].strip().lower()
        quality = 1.0
        for param in pieces[1:]:
            name, _, number = param.strip().partition("=")
            if name.strip() == "q":
                try:
                    quality = float(number)
                except ValueError:
                    quality = 0.0
        if tag and quality > 0:
            ranked.append((-quality, index, tag.split("-", 1)[0]))
    offered = languages()
    for _, _, code in sorted(ranked):
        if code in offered:
            return code
    return DEFAULT_LANGUAGE


def locales_script():
    """Every interface locale as one classic script for the browser.

    Served at /locales.js and loaded before js/app.js, so the dictionaries are
    there synchronously with no build step: a new locales/<code>.json shows up
    after a restart.
    """
    payload = json.dumps(ui_locales(), ensure_ascii=False, separators=(",", ":"))
    # "</" cannot end the script element even inside a string.
    payload = payload.replace("</", "<\\/")
    return f"window.PT_LOCALES = {payload};\n"


def t(text, lang=None, **values):
    """`text` (English) in the given or current language, with `{name}` values filled in."""
    lang = normalize(lang) if lang else _language.get()
    template = catalog(lang).get(text, text)
    return template.format(**values) if values else template


def N_(text):
    """Mark `text` for the catalog without translating it now."""
    return text
