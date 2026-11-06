# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.1.0] - 2026-11-06

First public release.

### Added

- Partner portal and vendor dashboard: lead registration with duplicate checks and a client queue,
  custom deal stages, partner tiers, commission rules, bonuses, commissions and payouts.
- Local AI assistant (Ollama) that proposes changes; every write needs a separate human
  confirmation.
- Read-only API gateway: MCP endpoint (`/mcp`) with OAuth 2.1 for Claude, and REST `/api/v1` with API
  keys, field scopes, rate limits and a call log.
- Plugins ("blocks") for process-specific features.
- Knowledge base: the vendor's administrators and managers add PDF, Word, Excel, PowerPoint, PNG
  and JPG documents (up to 10 MB each, recognised by their bytes); every signed-in account,
  partners included, lists, searches and downloads them from a new Knowledge base page. The files
  live in PostgreSQL behind row-level security, so backups cover them; the operator sets the
  space per instance with `tools/instance.py knowledge-quota` (1024 MB by default). See
  `docs/KNOWLEDGE_BASE.md`.
- Operator tooling: an isolated Docker Compose project per installation, encrypted backups, restore
  drill and an optional public HTTPS proxy (Let's Encrypt).
- One-command local demo (`compose.demo.yaml`).
- English and Polish interface. The first visit follows the browser's language (anything the app
  does not speak gets English); the language switch is remembered. Server messages, e-mails and
  the assistant's answers follow the same choice.
- Community translations: a language is two JSON files (`locales/<code>.json`,
  `api/locale/<code>.json`) and no code. See `TRANSLATING.md`; `python3 -m tools.i18n_coverage`
  shows how complete each language is.
- Affiliate programme (off by default): each partner gets a personal referral link to the
  vendor's shop; the shop reports paid orders with a signed webhook (generic HMAC, or Stripe
  Checkout), and an approved order becomes the partner's won lead, so its commission follows the
  existing rules and reaches Payouts. Orders wait for the administrator's approval (optionally
  automatic after a refund window); refunds before and after approval are handled, and an unpaid
  commission of a refunded order can be reversed. A consent-first snippet for the shop, no buyer
  data stored. See `docs/AFFILIATE.md`.

### Changed

- Environment variables are renamed from `PARTNERHUB_*` to `PARTNERTYPE_*`. The old names still
  work and log one deprecation warning line.
- Mail has no default sender address any more: with a mail host but no `PARTNERTYPE_SMTP_FROM`,
  nothing is sent and the log says why.

### Security

- PostgreSQL row-level security (migration `0016_row_level_security`): the database itself refuses
  to show a partner other partners' leads, comments, payouts, bonuses, notifications or accounts,
  even if an application query forgets its filter. Partners never see internal comments. The vendor
  and their team keep the full view. Without a user or system context, protected tables return
  nothing.
- Partners see the vendor's staff accounts only as name, e-mail and role (migration
  `0017_staff_directory`); password hashes, second-factor data and other private fields of staff
  are out of reach of a partner context, even with raw SQL.
- Affiliate tables (migration `0018_affiliate`) are under row-level security: a partner sees only
  his own referral link, clicks and orders; the shop's signing secrets are readable by the instance
  and the administrator only, and are stored encrypted.
- A partner who opens another partner's lead by its number now gets 404 (not found) instead of
  403, so the response does not reveal that the lead exists.
- Security review of the whole codebase before release: every critical finding and all but one
  warning fixed, each with a regression test. Open: an attacker who knows an e-mail address can
  lock that account's password sign-in for 15 minutes (a password reset still works).
