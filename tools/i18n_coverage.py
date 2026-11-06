"""How complete each translation is, for the interface and for the server.

    python3 -m tools.i18n_coverage            # print coverage per language
    python3 -m tools.i18n_coverage --strict   # exit 1 if a shipped language is incomplete

The interface source is locales/en.json; the server source is every English
text the code marks with t(), N_() or _field_error(). A language counts a key
as translated when its file has it; a missing key still works (the user sees
English), it is just not translated yet. See TRANSLATING.md.
"""
import argparse
import ast
import json
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "locales"
SERVER_DIR = ROOT / "api" / "locale"
MARKERS = ("t", "N_", "_field_error")


def ui_locales():
    """{code: parsed locales/<code>.json}."""
    return {path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(UI_DIR.glob("*.json"))}


def server_catalogs():
    """{code: parsed api/locale/<code>.json}; English has none, it is the source."""
    return {path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(SERVER_DIR.glob("*.json"))}


def server_source_texts():
    """Every English text the server code marks for translation."""
    files = [p for p in (ROOT / "api").rglob("*.py") if "tests" not in p.parts]
    texts = set()
    for path in files + [ROOT / "server.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in MARKERS and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                texts.add(node.args[0].value)
    return texts


def placeholders(value):
    """The {names} in a message, or in every form of a plural message."""
    forms = value.values() if isinstance(value, dict) else [value]
    names = set()
    for form in forms:
        names |= {name for _, name, _, _ in string.Formatter().parse(str(form)) if name}
    return names


def coverage():
    """[(area, code, translated, total)] for every language."""
    rows = []
    ui = ui_locales()
    source = set(ui["en"]["messages"])
    for code, locale in ui.items():
        done = len(source & set(locale["messages"]))
        rows.append(("interface", code, done, len(source)))
    texts = server_source_texts()
    catalogs = server_catalogs()
    for code in ui:
        if code == "en":
            rows.append(("server", code, len(texts), len(texts)))
            continue
        done = len(texts & set(catalogs.get(code, {})))
        rows.append(("server", code, done, len(texts)))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 when any shipped language is below 100%%")
    args = parser.parse_args(argv)
    incomplete = []
    for area, code, done, total in coverage():
        percent = 100 * done // total if total else 100
        print(f"{area:<10} {code:<4} {percent:>3}%  ({done}/{total})")
        if done < total:
            incomplete.append(f"{area} {code}")
    if args.strict and incomplete:
        print("incomplete: " + ", ".join(incomplete), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
