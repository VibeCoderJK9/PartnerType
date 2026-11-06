# Contributing to PartnerType

Thank you for helping. Bug reports, fixes, documentation, translations and new plugins ("blocks") are
all welcome. To translate PartnerType into your language, follow [TRANSLATING.md](TRANSLATING.md).

## Before you start

- **Security problems:** do not open a public issue; follow [SECURITY.md](SECURITY.md).
- **Bigger changes:** open an issue first so we can agree on the approach before you write code.
- **Architecture rules:** read [ARCHITECTURE.md](ARCHITECTURE.md) before changing the code.

## Run it locally

The fastest way to see the app:

```sh
docker compose -f compose.demo.yaml up --build
```

Then open http://localhost:8000. The demo accounts are printed in the log.

## Run the tests

Requirements: Docker with Compose v2, Python 3.12, Node.js 22.

```sh
pip install -r requirements-dev.txt
npm ci
./run-tests.sh                                   # backend (Docker + throwaway PostgreSQL) and frontend
python -m pytest tests_operations -o addopts=''  # operator tooling
node scripts/security-sink-audit.mjs             # frontend HTML-sink gate
```

All of these run in CI on every pull request and must pass. While you work, run only the tests
you touched: `./scripts/test-focused.sh api/tests/unit/test_users.py`.

## Rules for code

- **Every fix and feature comes with a test.** A bug fix needs a regression test that fails without
  the fix.
- **Permissions are checked on the server.** The UI may hide a button, but the API must refuse the
  action for a role that may not do it.
- **Text typed by people is rendered with `textContent`, never `innerHTML`.** New HTML sinks need a
  review entry in `security-sink-allowlist.json`.
- **SQL uses parameters only.** Never build SQL from user input.
- **The AI proposes, a human confirms.** Anything the AI assistant changes goes through a separate
  confirmation click (see ARCHITECTURE.md).
- **Database changes go through Alembic migrations** in `migrations/versions/`, with one head.
- Comments describe behaviour in plain English. User-facing text goes into the locale files, never
  into code: add the key to `locales/en.json` and `locales/pl.json` (screens) or wrap the English in
  `t()` and add the Polish to `api/locale/pl.json` (server). See [TRANSLATING.md](TRANSLATING.md).

## Pull requests

1. Fork, create a branch, make focused commits.
2. Run the tests above.
3. Open a pull request and fill in the template.

By contributing you agree that your contribution is licensed under the
[GNU Affero General Public License v3.0](LICENSE), the licence of this project.
