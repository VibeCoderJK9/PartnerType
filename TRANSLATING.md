# Translating PartnerType

PartnerType speaks English and Polish today. You can add your language without writing code: a
language is two JSON files. This guide walks you through it.

## How it works

| File | What it holds |
|---|---|
| `locales/en.json` | The interface (screens, buttons, messages). **English is the source.** |
| `locales/<code>.json` | The same keys, translated. One file per language. |
| `api/locale/<code>.json` | Server messages (errors, e-mails, assistant confirmations), keyed by the English text. |

`<code>` is the two-letter (or three-letter) language code: `de`, `fr`, `uk`, `es`.

When the app starts, every `locales/<code>.json` becomes a language: it appears in the language
switch, the server answers in it, and the AI assistant is told to reply in it. A visitor's browser
language picks the language on the first visit; the language switch overrides it and is remembered.

Anything you have not translated yet shows the English text, so a partial translation already
works.

## Add a language, step by step

German (`de`) is used as the example.

1. **Copy the interface file.**

   ```sh
   cp locales/en.json locales/de.json
   ```

2. **Fill in `meta`** at the top of `locales/de.json`:

   ```json
   "meta": {
     "name": "Deutsch",
     "english_name": "German",
     "locale": "de-DE"
   }
   ```

   `name` is your language's name **in your language** (shown in the switch). `english_name` tells
   the AI assistant which language to answer in. `locale` sets number, date and currency formats.

3. **Translate every value under `messages`.** Keep the keys as they are.

4. **Plurals.** Some messages are objects instead of text, for example:

   ```json
   "unit_lead": { "one": "lead", "other": "leads" }
   ```

   Use the plural categories your language has in
   [Unicode CLDR](https://www.unicode.org/cldr/charts/latest/supplemental/language_plural_rules.html):
   `zero`, `one`, `two`, `few`, `many`, `other`. Always include `other`. German needs `one` and
   `other`; Polish uses `one`, `few`, `many` and `other`.

5. **Translate the server messages.** Create `api/locale/de.json`. Start from the Polish catalog
   to get every key, then replace the values:

   ```sh
   cp api/locale/pl.json api/locale/de.json
   ```

   Here the key is the English sentence and the value is your translation:

   ```json
   "Access denied.": "Zugriff verweigert."
   ```

6. **Check your work** (next section), then open a pull request.

## Rules

- **Do not translate keys.** Only values. In `api/locale/*.json` the key is English text; leave it
  exactly as it is.
- **Keep every `{placeholder}`** exactly as written, for example `{name}` or `{amount}`. You may
  move it within the sentence; do not translate or remove it.
- **No markup in the interface.** `locales/<code>.json` values must not contain `<` or `>`.
- **Keep HTML tags exactly** where a few e-mail sentences in `api/locale/` have them
  (`<strong>{name}</strong>`); do not add any. Both rules are tested, because these texts end up
  in web pages and e-mails.
- **Do not add keys** that are not in the English file. Tests reject unknown keys.
- **Keep the file valid JSON**: double quotes, no trailing commas, UTF-8.
- Use the tone of the English text: short, direct and polite. Address the user the way your
  language normally does in business software.

## Test it locally

You need Python 3. The coverage report uses nothing else; the checks need pytest
(`pip install -r requirements-dev.txt`). Neither needs a database or Docker.

How complete is each language:

```sh
python3 -m tools.i18n_coverage
```

```text
interface  en   100%  (1711/1711)
interface  pl   100%  (1711/1711)
interface  de    62%  (1061/1711)
server     de    40%  (258/644)
```

The checks every pull request must pass (valid files, same placeholders as English, no unknown
keys):

```sh
python3 -m pytest tests_operations/test_locale_files.py -o addopts=''
```

See it in the app: start the demo (`docker compose -f compose.demo.yaml up --build`), open
http://localhost:8000 and pick your language in the switch at the top. The files are read when the
server starts, so restart it after you change them.

## Send a pull request

1. Fork the repository and create a branch, for example `translation-de`.
2. Add `locales/de.json` and `api/locale/de.json`.
3. Run the two commands above.
4. Open a pull request titled "Translation: German". Say how complete it is and whether you are a
   native speaker. Partial translations are welcome; the missing parts show in English.

Questions? Open an issue. Thank you for helping people use PartnerType in their own language.
