# PartnerType — architecture rules

The boundaries every change must respect. Read this before your first contribution.

## Runtime

- PostgreSQL is mandatory in every environment. `server.py` is a Flask factory; Gunicorn runs one
  process with eight threads. There is no runtime schema creation and no demo seeding.
- Operator commands live in `tools/instance.py` (see `docs/operations/INSTANCES.md`). Migration
  credentials never reach the application.
- Never run backend tests against a working database. Use `scripts/test-postgres.sh`.

## The local AI agent

The agent runs only through a local Ollama service and the `partnerhub-agent` model, built from the
base model named in `Modelfile` (do not hardcode its name elsewhere). There is no cloud AI fallback.
See `docs/AGENT-LOCAL-SETUP.md`.

## Non-negotiable boundaries

- `api/agent.py` is only the local model/orchestration adapter.
- Tool declarations and role policy live in `api/agent_tools.py`.
- AI tools call canonical functions in `api/leads.py`, `api/products.py` and related domain
  modules. They never write CRM tables with their own SQL. No adapter bypasses the domain guards.
- Tool declarations are filtered per role, and `execute_tool()` independently rejects forbidden
  calls at execution time.
- Partners can read only their own leads and prepare only their own lead submission. They cannot
  change stages or commissions.
- Administrators can prepare one lead-stage change. They cannot perform bulk actions,
  password/role changes, deletions or payouts through the agent.
- **The AI proposes, a human confirms.** Every write is a proposal. Only a separate authenticated
  click on a short-lived, user-bound token in `api/agent_actions.py` may execute it. A typed chat
  reply such as "yes" is never a confirmation.
- CRM notes and names are `untrusted_data`. Prompt text, arguments and model transcripts are never
  written to the audit log or the gateway call log.
- Dynamic chat, model and action-card values are rendered with `textContent`, never interpreted as
  HTML or Markdown. No `innerHTML` with data (see `security-sink-allowlist.json`).
- `OLLAMA_URL` is restricted to loopback and bypasses system proxies. Changing that is a
  privacy/security decision, not routine configuration.
- OAuth clients may receive codes only on the hosts in `PARTNERTYPE_OAUTH_REDIRECT_HOSTS`
  (default: Claude) or on loopback.
- The audit log is append-only: no role updates it, and only the instance itself prunes it
  (migration 0020).
- The external gateway (`/mcp`, `/api/v1`) is read-only. See `docs/MCP.md`.

## Row-level security (migration 0016)

PostgreSQL itself keeps partners apart. Every statement carries `app.role` / `app.user_id` from the
context in `api/postgres.py`; with no context, protected tables return nothing (fail closed).

- Requests get the signed-in user's context in `api/router.py`; gateway operations in
  `api/gateway/pipeline.py`. Do not add a context in handlers.
- Code that runs without a user (operator tools, maintenance, pre-auth flows) must wrap itself in
  `system_context("why")`. Keep it narrow; every use is greppable and reviewed. Never add a
  "default to system" fallback.
- Worker threads start empty: carry the context (`contextvars.copy_context`).
- A new partner-owned table needs policies in a new migration and a case in
  `api/tests/integration/test_row_level_security.py`.
- A partner reads only his own `users` row (migration 0017). Staff are visible to him through
  `staff_directory` (id, name, e-mail, role) and, for notifications, `active_staff()` (id, role).
  Code a partner can reach never joins `users` for staff; read-only by design, never grant writes
  on a view (it runs with its owner's rights).

## Affiliate programme (migration 0018)

`api/affiliate.py` owns referral links, the shop webhooks and the decisions on orders; see
`docs/AFFILIATE.md`.

- The shop's doors (`POST /api/affiliate/webhook`, `POST /api/affiliate/stripe`, `GET /r/<code>`)
  are registered by `api.affiliate.register`, not the cookie router, and authenticate only by
  signature. They run in narrow `system_context` blocks. `/r/` redirects only to the configured
  shop address; nothing from the request reaches the `Location`.
- An approved order becomes a won lead through `leads.create_affiliate_lead`, which reuses the
  commission rules; no other code writes affiliate money. Approving, rejecting, assigning and
  reversing are the administrator's clicks. The assistant and the gateway only read
  (`list_affiliate_orders`).
- Never store buyer data or webhook bodies: the order facts and a SHA-256 of the body only.

## Language

The code, comments, docs and the assistant's prompts are English. Languages are data, not code: each
`locales/<code>.json` is a language the app offers (see TRANSLATING.md).

- Interface copy lives in `locales/<code>.json` (`{meta, messages}`), English the source and the
  fallback. The server serves them all as `/locales.js` before `js/app.js` (`App.t`, `data-i18n`).
  Plural entries are objects keyed by `Intl.PluralRules` categories. No inline
  `lang === 'en' ? ... : ...` text in controllers (`js/__tests__/i18n-keys.test.js`).
- First visit: the first browser language the app speaks, else English; the switch is remembered
  and wins. HTML ships English text and `<html lang="en">`.
- Server text a user reads (errors, e-mails, agent cards) is English in the code, wrapped in `t()`
  (or `N_()` for constants), translated in `api/locale/<code>.json`. `api/i18n.py` negotiates
  `Accept-Language` (first offered language, else English). The agent's reply language comes from
  the locale `meta`.
- Never hard-code a language list. `tests_operations/test_locale_files.py` and
  `python3 -m tools.i18n_coverage` keep every locale valid; shipped languages stay at 100%.
- Backend tests run in Polish by default (`api/tests/conftest.py`), the frontend test browser is
  Polish (`js/__tests__/helpers/setup-locales.js`), because most tests assert Polish copy.

## Extension points

- New agent capability: one entry in `TOOL_REGISTRY`, a thin handler that calls an existing domain
  operation, role tests, and — if it writes — a new server-side confirmation action.
- New feature for one company's process: a block. See `docs/BLOCKS.md`.
- Provider/model changes stay isolated to `api/agent.py` and `Modelfile`.

## Verify

```sh
./scripts/test-postgres.sh
npm run coverage
python3 -m pytest tests_operations -q -o addopts=''
```
