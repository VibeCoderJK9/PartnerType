# Claude and other programs in PartnerType: the API gateway and MCP

This file explains how to connect Claude (claude.ai, Claude Desktop, the mobile app, Claude Code)
or your own script to a PartnerType instance. It is written for the program owner, the server
operator and their AI agent.

## What it is

- **One door per instance.** Every instance has its own gateway at its own address, for example
  `https://company.partnertype.com`. Claude connects to `…/mcp`, scripts to `…/api/v1`.
- **Read-only.** The gateway has no operation that writes, changes or sends anything. Claude can
  read leads, statistics, products, partners and reminders, nothing more.
- **The same permissions as in the app.** The gateway checks the role when it lists the tools and
  again on every call. A partner sees only their own leads, exactly as after signing in.
- **Off by default.** Until an administrator switches it on, nobody can connect from outside.
- **Two ways to sign in:**
  - **"Connect" (OAuth)** — Claude opens the PartnerType sign-in page, the user signs in (with a
    second factor, if required) and clicks "Allow". The password never reaches Claude.
  - **API key** (`ptk_…`) — for Claude Code and your own scripts. It is shown only once, when it is
    created. Valid for 30, 90 or 365 days. It can be revoked at any time.

## Switching it on (administrator)

Settings › **Integrations**:

1. **GDPR.** Tick the confirmation that data will go to the AI provider the company chose
   (Anthropic processes it outside the EU). Without it the "Enable integrations" switch is locked,
   and the server refuses to enable it anyway. Use a Claude business plan with a data processing
   agreement.
2. **Enable integrations.** The instance's MCP address appears, with a "Copy address" button.
3. **Partners.** The "Partners can connect too" switch is off by default. Through Claude a partner
   sees only their own data.
4. **Data scope.** Always visible: client company, value, product, quantity, stage, registration
   status, partner (the partner's company) and dates. Five groups are off by default: contact
   details (person, e-mail, phone), notes and comments, client company identifiers (industry, tax
   ID, website), custom fields, commissions. Switch on only what the assistant really needs. The
   groups work the same in MCP and in `/api/v1`.
5. **Limits per connection** (minute, day). The administrator can only lower them: the server
   operator sets the upper bound (see "Limits").

Switching integrations off asks for confirmation. Keys and connections stay on the list but stop
working at once, until integrations are switched on again.

Every user who may connect (administrator, manager, observer, and a partner only when the producer
allows it) has a **"Connect to Claude"** section in their profile: the MCP address, a ready command
for Claude Code, and their own keys and connections to revoke.

## Connecting: claude.ai, Claude Desktop, mobile app

1. In Claude: Settings › Connectors › **Add custom connector**.
2. Address: `https://<company>.partnertype.com/mcp` (copy it with the "Copy address" button).
3. Click **Connect**. The PartnerType sign-in page opens. Sign in (+ second factor).
4. The consent screen shows the application name (declared by the application, not verified) and
   **the host the answer returns to** (`claude.ai`) — that is the part that matters. Click
   **Allow**. An application may register only an address whose host name is plain ASCII (or an IP
   address); international names pass only as punycode (`xn--…`), and the consent screen then warns
   that such an address may imitate a well-known domain.
5. Ask: *"How many new leads are there today?"*, *"How many new sales came through partners
   today?"*, *"Check where information is missing."*

**Requirement:** the instance needs a **publicly trusted HTTPS certificate**. Anthropic's cloud
connects to the server itself, so a local `tls internal` certificate is not enough. You need an
edge server with ACME or a tunnel — see `docs/operations/INSTANCES.md`, section "API gateway and
MCP". A connector added in claude.ai also works in Claude Desktop and in the mobile app on the same
account.

## Connecting: Claude Code

**With a key** (simplest; also works with `http://localhost:8000` and with a local certificate):

```sh
export PARTNERTYPE_KEY=ptk_…        # key from Settings › Integrations or from your profile
claude mcp add --transport http partnertype https://<company>.partnertype.com/mcp \
  --header "Authorization: Bearer $PARTNERTYPE_KEY"
```

**Through OAuth** (no key): `claude mcp add --transport http partnertype https://<company>.partnertype.com/mcp`,
then in Claude Code run `/mcp` → choose `partnertype` → sign in in the browser and click "Allow".
The consent screen warns you when the answer returns to an application on this computer
(`localhost`).

## Your own scripts: `/api/v1`

```sh
curl -H "Authorization: Bearer $PARTNERTYPE_KEY" "https://<company>.partnertype.com/api/v1/leads?per_page=10"
```

Full description (OpenAPI 3.1, no sign-in needed): `https://<company>.partnertype.com/api/v1/openapi.json`.
The same document is in the repository as `docs/openapi.json`.

Every list has an envelope: `untrusted_data` (the records), `security_note`, `count`, `total`,
`page`, `per_page` (default 25, at most 100), `today_utc` and `today_warsaw`. Long texts are cut to
500 characters. Errors look like `{"error": "…", "error_description": "…"}`.

## Tools, routes and roles

Eight MCP tools and six `/api/v1` routes use the same functions, the same argument schemas and the
same role matrix. All of them carry `readOnlyHint: true`.

| MCP tool | `/api/v1` route | Returns | admin, manager | observer | partner |
|---|---|---|---|---|---|
| `list_leads` | `GET /leads` | leads with filters (stage, registration status, text, dates, value, overdue, won from–to), paged | ✓ | ✓ | ✓ (own only) |
| `get_lead_details` | `GET /leads/{id}` | one lead with comments (the 100 newest at most) | ✓ | ✓ | ✓ (own only) |
| `get_lead_stats` | `GET /stats` | lead counts within the user's scope, including `won_today` | ✓ | — | ✓ (own) |
| `list_products` | `GET /products` | the program's product catalogue | ✓ | ✓ | ✓ |
| `list_partners` | `GET /partners` | the program's partners | ✓ | — | — |
| `get_partners_count_by_tier` | — (MCP only) | number of partners per tier | ✓ | — | — |
| `list_reminders` | `GET /reminders` | automatic reminders from blocks | ✓ | — | — |
| `list_affiliate_orders` | — (MCP only) | shop orders from referral links (commission only with the "Commissions" group) | ✓ | — | ✓ (own only) |

Someone else's lead looks exactly like a lead that does not exist (404 / "not found"), and a tool
the role may not use gives the same error as an unknown tool. The gateway never shows partner
earnings; lead commissions only when the "Commissions" group is on.

**Dates.** `date_from` / `date_to` filter by the lead's creation date in **UTC days** (that is how it
is stored; between 00:00 and 02:00 Polish time, "today" in UTC is still yesterday).
`won_from` / `won_to` and `won_today` count **Warsaw days**. That way the question *"How many new
sales came through partners today?"* has an exact answer, and every list gives `today_utc` and
`today_warsaw` so the model knows which day is "today".

## Limits

| Limit | Default | Who sets it |
|---|---|---|
| calls per minute, per connection | 60 | administrator, at most up to the operator's `PARTNERTYPE_API_RATE_PER_MINUTE` (1–600) |
| calls per day, per connection | 5000 | administrator, at most up to `PARTNERTYPE_API_RATE_PER_DAY` (1–100000) |
| OAuth application registrations per hour, per IP | 20 | operator, `PARTNERTYPE_OAUTH_REGISTER_PER_HOUR` (1–1000) |
| connection requests (`/oauth/authorize`) per minute | 30 per IP, 60 per application | fixed in code; when exceeded, a 429 error page with `Retry-After` |

A "connection" is one key or one OAuth connection. The same two limits also apply **in total per
user**, across all their keys and connections, so another key does not enlarge the pool. One user
may hold at most 10 active keys at a time (a revoked or expired key frees a place). A day ends at midnight Warsaw time. When a limit
is exceeded the gateway answers **429** with a `Retry-After` header (seconds): in `/api/v1`
`{"error":"rate_limited", …}`, in MCP HTTP 429 with JSON-RPC error `-32000`. In the app this shows
as "Limit exceeded" in the call log and in the "Refusals" column.

## Call log (what the gateway records)

Settings › Integrations shows the call log and usage for the last 30 days. One row is: time,
connection (key or OAuth), user, surface (`mcp` / `api_v1`), the operation **name** (tool or route
template, e.g. `/leads/{id}`), response code, outcome (`ok`, `error`, `denied`, `rate_limited`),
number of records returned and duration.

The log **records no content**: no arguments, queries, responses, CRM data, conversation text or
keys. Rows older than 90 days are removed by the daily `maintenance`. Switching integrations on and
off, changes to scope and limits, and creating and revoking a key or connection go to the regular
audit log.

## Security rules

- **No writes.** There is no tool or route that changes anything. If they ever appear, every write
  will be a proposal confirmed by a separate click in the app (as in the PartnerType AI assistant),
  never a "yes" in a chat.
- **CRM data is data, not instructions.** Everything people typed (names, notes, comments) is in
  `untrusted_data`, and the MCP server instructions and `security_note` tell the model never to
  follow instructions found in those fields. PartnerType checks permissions, not the AI.
- **Revocation works at once.** Revoke a key or connection in Settings › Integrations
  (administrator: all of them) or in your profile (your own). "Sign out everywhere", a completed
  password reset and a password change (including a new password set by an administrator)
  **revoke all keys and OAuth connections** of that user.
- **The key goes only in the** `Authorization: Bearer …` **header.** Never in a URL, in code in a
  repository or as a command argument. PartnerType stores only a hash of the key.
- **Separate worlds.** A key or OAuth token works only on `/mcp` and `/api/v1`. It does not open the
  app's regular API (`/api/*`), and the session cookie is not a pass to the gateway.
- **OAuth:** an access token lives 1 hour, a refresh token 30 days and rotates on every use; reusing
  an old refresh token (after 30 s) cuts off the whole connection. The whole OAuth
  connection expires **90 days after consent**; refreshing does not extend it, so click "Connect"
  again after that. PKCE S256 is mandatory.
  Applications register only through DCR (`/oauth/register`).
- **Origin.** A browser request to `/mcp` passes only from the instance's own address or from the
  operator's list (default `https://claude.ai`, `https://claude.com`). Requests without an `Origin`
  header (Claude Code, scripts, Anthropic's cloud) pass normally.
- **MCP protocol versions:** `2025-11-25`, `2025-06-18`, `2025-03-26` (Streamable HTTP transport,
  POST only, JSON responses, no sessions). GET and DELETE on `/mcp` get 405.

## When something does not work

| Symptom | Cause | What to do |
|---|---|---|
| 401, Claude asks to reconnect | key expired or revoked; connection revoked or older than 90 days; "Sign out everywhere", password reset or change | create a new key or click "Connect" again |
| 403 `integrations_disabled`; `/.well-known/…` documents give 404 | integrations are off | administrator: Settings › Integrations › Enable integrations |
| 403 `partners_disabled` | a partner, and the producer does not let partners connect | administrator: the partners switch in Integrations |
| 403 `mfa_setup_required` | the role requires two-factor sign-in and the user has not set it up | set up the second factor in the profile, then connect again |
| 403 `forbidden_tool` (`/api/v1`) or "Unknown tool" (MCP) | operation not available to the role | see the role table above |
| 403 on a browser request to `/mcp` | `Origin` header not on the list | operator: `PARTNERTYPE_MCP_EXTRA_ORIGINS` (the list **replaces** the default, so also add `https://claude.ai,https://claude.com`) |
| 400 `invalid_redirect_uri` when connecting another AI client | the client's return address is not on the operator's list | operator: `PARTNERTYPE_OAUTH_REDIRECT_HOSTS` (default `claude.ai,claude.com`; loopback is always allowed) |
| 429 | per-minute or per-day limit exceeded | wait for `Retry-After`; the administrator can raise the limit up to the operator's limit |
| 400, error `-32600` in MCP | the client sent an unsupported protocol version | update the client; supported versions are in the error |
| claude.ai: "cannot connect" with a correct address | the certificate is not publicly trusted (`tls internal`) | an edge server with ACME or a tunnel (`docs/operations/INSTANCES.md`) |
| after sign-in the consent screen goes back to sign-in | the browser did not send the session cookie | try a regular window, not private mode, and report it |

## Quick test of a running instance: `scripts/mcp-smoke.sh`

The script checks the gateway with plain `curl`, read-only, and changes nothing:

```sh
PARTNERTYPE_KEY=ptk_… scripts/mcp-smoke.sh https://company.partnertype.com
# local instance with a `tls internal` certificate:
PARTNERTYPE_KEY=ptk_… SMOKE_INSECURE_TLS=1 scripts/mcp-smoke.sh https://<local-instance-address>
```

Steps: `/health`, both discovery documents, 401 with `resource_metadata` without a key,
`initialize`, `tools/list`, `tools/call list_leads`, GET `/mcp` → 405, unsupported version → 400,
`/api/v1/leads`, `/api/v1/openapi.json`. Every step prints `PASS …` or `FAIL … (got <code>)`; the
first `FAIL` ends the script with exit code 1.

Give the key **only in the `PARTNERTYPE_KEY` variable** — the script refuses a key passed as an
argument and will not send it over plain `http://` outside `localhost`. The key reaches curl
through a header file with mode 600, removed on exit; the script never prints it. Use a separate
key valid for 30 days for the test and revoke it afterwards.

## Old stdio demonstrator (legacy)

`tools/mcp/partnertype_mcp.py` is an earlier demo that runs on the producer's computer. It signs in
as an administrator **with a password stored in the AI client's configuration file** and has two
tools (`list_reminders`, `list_partners`). It is kept for offline demos; for real work use the
`/mcp` gateway with a key or "Connect".
**The demo bypasses every gateway control:** it works with integrations switched off, applies no
field groups (it returns, for example, the partner contact person's name), has no limits, writes
nothing to the call log and does not mark CRM data as `untrusted_data`. Run it only locally, on a
test instance with demo data. The demo sends the password only over `https://`: plain `http://` and
switching off certificate checks (`PARTNERTYPE_INSECURE_TLS=1`) work only for `localhost`,
`127.0.0.1` and `::1`.

## Implementation notes

- **Own authorization server.** OAuth is built on the standard library (no Authlib). Swapping in a
  library would leave the storage, consent and MCP unchanged.
- **CIMD later.** Applications register through DCR (Claude supports it). Registration by client
  metadata document (CIMD) would be a separate step, with a list of allowed hosts.
- **Default field scope.** The contact person's name and e-mail are in the "Contact details" group,
  and industry, tax ID and website in "Client company identifiers"; both groups are off by default.
  A scenario like "write to Michael" needs contact details switched on.
- **Observer tools.** An observer sees only leads and products, without statistics, partners and
  reminders (the same as in the app).
