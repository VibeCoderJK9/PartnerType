# Local environments and producer instances

Requirements: Docker Engine/Desktop with Compose v2 (`--wait`), Python 3.9+ for the CLI, `age` for
backups. The first build and the model installation need network access. Every instance keeps its
own Ollama and model; the 4B model in `Modelfile` needs about 3.5–5 GB of spare RAM.

## The image and the first producer

Build **once** and use exactly the same image ID in staging and production:

```sh
docker build --target release -t partnertype:local .
docker image inspect partnertype:local --format '{{.Id}}'
python3 -m tools.instance --env staging --id vendor-a create \
  --name 'Vendor A' --email owner-a@example.test \
  --base-url https://a.localhost:8443 --port 8443 \
  --image sha256:<64_CHARACTERS_FROM_THE_COMMAND_ABOVE>
```

`create` prepares its own secrets, a private Compose project, persistent volumes, PostgreSQL, the
application role, the Alembic migration and a one-time owner invitation. Then it starts the CRM
behind Caddy and installs `partnerhub-agent` from the `Modelfile`. A failed model download does not
stop the running CRM. `--skip-model` starts the CRM first; `model` finishes the AI installation
later.

The invitation is in `instances/staging/vendor-a/owner-invitation.json` (mode 0600). There is no
shared administrator password. The link expires after 7 days and works once. Delete the local file
with the link after use. Running `create` again with the same configuration does not overwrite
secrets or data and does not issue a new invitation.

`--prepare-only` creates only the configuration, with no containers and no users. Use it to prepare
a restore target.

A second instance (for example a test copy) uses another ID, address and port, e.g. `vendor-b`,
`https://b.localhost:9443`, port 9443. Both sets run independently. Configurations, secrets and project names are also separate between
`development`, `test`, `staging` and `production`. The same application files serve every
environment.

An instance ID is 3–48 characters `a-z`, `0-9` and `-`, starting with a letter. The names `edge`
and `mail` are reserved for the files that live next to the instance directories; `create` and the
other commands refuse them. `create` also refuses, before writing anything, when the host name or
port is already used by another instance of the same environment (including one removed with
`destroy-data`, because it can be brought back).

## Local HTTPS

Caddy issues a local certificate through `tls internal`. The service is bound only to `127.0.0.1`.
There are no public ports for the database, the app or Ollama. If your system does not resolve
`a.localhost`/`b.localhost`, map them to 127.0.0.1 in your local DNS/hosts configuration.

Export the public CA certificate of a given producer:

```sh
docker compose -f instances/staging/vendor-a/compose.json cp \
  proxy:/data/caddy/pki/authorities/local/root.crt /tmp/vendor-a-root.crt
```

Trusting this CA in the browser/system is a separate local step. For CLI checks use
`curl --cacert /tmp/vendor-a-root.crt https://a.localhost:8443/health`. Do not switch off TLS
validation as an application setting.

On a public server, instances are created with `--edge` (and usually `--no-ai`), and HTTPS with a
real certificate comes from `tools/edge.py` or your own reverse proxy. Instructions:
[PUBLIC-SERVER.md](PUBLIC-SERVER.md).

## API gateway and MCP

Instructions for owners and users: [../MCP.md](../MCP.md). This section covers only the operator.

- **Caddy passes paths through unchanged.** Both generators (`caddyfile` in `tools/instance.py` for
  a standalone instance and one behind the edge server, and `tools/edge.py`) have a single bare
  `reverse_proxy`, with no path matching. `/mcp`, `/.well-known/*`, `/oauth/*` and `/api/v1/*`
  reach the app as they came, with their headers. A `tests_operations` test makes sure no `handle`,
  `route` or `path` matcher appears in the generated Caddyfile. API responses are not compressed
  (BREACH), and the Caddy log does not record requests.
- **claude.ai needs a publicly trusted certificate.** The connector connects from Anthropic's cloud,
  which does not trust `tls internal`. You need an instance behind the edge server (`--edge` +
  `tools/edge.py`, ACME certificate) or a tunnel with a public certificate. An instance with
  `tls internal` is enough for Claude Code and `scripts/mcp-smoke.sh` (with
  `SMOKE_INSECURE_TLS=1` or with the instance CA trusted).
- **`PARTNERTYPE_BASE_URL` is the OAuth issuer** and the `…/mcp` resource address. It must be the
  public `https://` address exactly as the client sees it, without a port behind the edge server.
  `Config.load` refuses an address without HTTPS in staging/production. Changing the address
  invalidates OAuth connections (the token is bound to the resource address).
- **`PARTNERTYPE_PROXY_HOPS`** must match the number of proxies (`1` for a standalone instance, `2`
  behind the edge server; `deploy/compose.py` sets it from `edge`). Otherwise the per-IP limits
  (OAuth application registration, the token endpoint) count all clients as one address.
- **Gateway variables** (all optional; the defaults work without setting anything):

  | Variable | Key in `config.json` | Default | Range |
  |---|---|---|---|
  | `PARTNERTYPE_API_RATE_PER_MINUTE` | `api.rate_per_minute` | 60 | 1–600 |
  | `PARTNERTYPE_API_RATE_PER_DAY` | `api.rate_per_day` | 5000 | 1–100000 |
  | `PARTNERTYPE_OAUTH_REGISTER_PER_HOUR` | `api.register_per_hour` | 20 | 1–1000 |
  | `PARTNERTYPE_MCP_EXTRA_ORIGINS` | `api.mcp_extra_origins` (a list or comma-separated text) | `https://claude.ai,https://claude.com` | `https://` addresses |
  | `PARTNERTYPE_OAUTH_REDIRECT_HOSTS` | `api.oauth_redirect_hosts` (a list or comma-separated text) | `claude.ai,claude.com` | hostnames, or `*` for any https host |

  The first two are the operator's ceiling: the administrator in Settings › Integrations can only
  set less. The `MCP_EXTRA_ORIGINS` list **replaces** the default, so if you set it, add the Claude
  addresses too. `OAUTH_REDIRECT_HOSTS` lists where an OAuth client may receive its codes; loopback
  (Claude Code) is always allowed. Add a host only for an AI client you trust: an open list lets a
  stranger register a look-alike connector and ask your users to approve it. A value out of range is not guessed: the gateway refuses to work until the operator
  fixes it. To set values, add an `"api": {"rate_per_minute": 30}` object to
  `instances/<env>/<id>/config.json` and regenerate `compose.json` with
  `upgrade --image <current image ID>` (without the `api` key the variables do not reach the
  container).
- **Traffic from Anthropic** comes from `160.79.104.0/21`. Do not restrict the firewall to this
  range: Claude Code and scripts connect from users' addresses.
- **Maintenance.** The daily `maintenance` removes call log rows older than 90 days and expired OAuth
  consent requests, codes and tokens. The log holds no request content.
- **Quick test after deployment:** `PARTNERTYPE_KEY=ptk_… scripts/mcp-smoke.sh https://<address>`
  (key only in the variable; the script only reads). Use a 30-day test key and revoke it after the
  test.

## Operator commands

The environment and producer are always explicit:

```sh
python3 -m tools.instance --env staging --id vendor-a stop
python3 -m tools.instance --env staging --id vendor-a start
python3 -m tools.instance --env staging --id vendor-a migrate
python3 -m tools.instance --env staging --id vendor-a model
python3 -m tools.instance --env staging --id vendor-a maintenance
python3 -m tools.instance --env staging --id vendor-a upgrade --image sha256:NEW_IMAGE
```

`stop` runs `compose down` **without** removing volumes. `upgrade` stops the app, switches the
immutable image, runs the migration and starts the services. Verify the same image on staging and
take a backup first. A failed migration leaves the app stopped. Do not roll the schema back
automatically; restore a backup into a new instance. Do not run several operator commands at once
for the same instance.

`maintenance` also applies the seven-day retention of rejected partner applications. Retention is
separate from migrations and `/health`; the operator should run it daily, on a schedule.

The operator sets the space for documents in the knowledge base (1024 MB by default, one
file up to 10 MB):

```sh
python3 -m tools.instance --env production --id vendor-a knowledge-quota --mb 500
python3 -m tools.instance --env production --id vendor-a knowledge-quota --default
```

The command writes `knowledge_quota_mb` to `config.json` and recreates the app with
`PARTNERTYPE_KNOWLEDGE_QUOTA_MB`. The files live in the database, so backup and restore cover them
with no extra step. An invalid value in the environment stops uploads instead of guessing a limit.

Rolling the database back past migration `0019_knowledge_base` drops the `knowledge_documents`
table, and with it every document the producer has uploaded. Take a backup first; upgrading again
does not bring the files back, only restoring that backup does.

Deleting data is separate and requires repeating the identifier:

```sh
python3 -m tools.instance --env development --id experiment \
  destroy-data --confirm-id experiment
```

This removes the volumes of **only** that project. Secrets stay on disk; a new `create` with the
same configuration can initialise an empty set again. Only when that succeeds (or when
`create --prepare-only` prepares the configuration) does the instance stop being marked as removed;
then refresh the edge server (`tools.edge … reload`) if you use it. Until then `start`, `upgrade`,
`mail`, `migrate` and `restore` refuse to work on the removed instance.

## Database roles and migrations

`api/postgres.py` uses Psycopg 3 and at most 32 connections. One Gunicorn process runs eight threads
and one AI generation at a time. Threads waiting for the pool have a timeout; connections never grow
without bound.

The application has DML, schema version reads and sequence usage; it has no DDL. Only the `migrate`
service receives the database owner's credentials. An advisory lock covers reading the version, the
migration and writing the new Alembic version. `/health` returns 503 when the database is
unreachable or the version does not match. It runs no migrations or seeds.

A thin adapter handles `?` parameters, row access and `lastrowid`, so the business modules need no
ORM. Queries are PostgreSQL SQL. Amounts use floating point (`DOUBLE PRECISION`); moving to exact
decimal amounts would be a separate step.

Row locks protect lead versions, invitation acceptance and settlements. A short advisory lock
serialises lead registration with editing the field definitions; another serialises the same retried
operation identifier. There is no shared customer schema and no `tenant_id` filtering: the database
is the producer boundary.

## AI and mail

Ollama binds to `127.0.0.1:11434`; the app shares its network namespace
(`network_mode: service:ollama`). The loopback policy in the AI adapter stays active. The model
status is checked by the authenticated `/api/agent/status`; `/health` stays independent. When busy it
returns 429 with `local_model_busy`. App logs record the error type and request ID, without URLs,
tokens or content. Caddy removes the request object and response headers from its logs, and
PostgreSQL does not log parameter values of failed queries.
[Caddy log filter](https://caddyserver.com/docs/caddyfile/directives/log#filter). A restart clears
the in-memory conversation history, not CRM data. Actions still need a separate click and fresh
authorisation.

### Assistant modes

| Mode | `create` | What happens |
|---|---|---|
| own model | (default) | the instance has its own Ollama, about 3.5–5 GB RAM |
| no assistant | `--no-ai` | no model, the `ollama` container is only a network namespace (256 MB); the assistant button disappears |

Development/test/staging **always** send through `mail:1025` (Mailpit), whatever is set in the CRM.
Mailpit has no relay and no public port. You can temporarily expose the message viewer with a
Compose override, on localhost only, on a separate port. Do not copy real data to staging.

Production mail is **one for the whole server** and belongs to the operator, not the producer. The
producer sees no SMTP fields in Settings. The operator stores it once per environment, next to the
instance directories:

```sh
cat > instances/production/mail.json <<'JSON'
{"host": "smtp.provider.example", "port": 587, "user": "apikey", "from": "no-reply@your-domain.example"}
JSON
printf '%s' 'PASSWORD_OR_API_KEY' > instances/production/mail_password
chmod 600 instances/production/mail.json instances/production/mail_password
```

`create` copies these settings into every new instance: the address goes to the `PARTNERTYPE_SMTP_*`
variables and the password to the `secrets/smtp_password` secret. In the "From" field the recipient
sees the producer's company name and the shared sender address. After changing the file, refresh an
existing instance and test sending:

```sh
python3 -m tools.instance --env production --id vendor-a mail
python3 -m tools.instance --env production --id vendor-a mail-test --to you@example.com
```

Without `mail.json` an instance sends no mail; invitations still show a link to pass on by hand. The
`from` address is required: there is no default sender, and an instance with a mail host but no
sender logs `smtp_sender_not_configured` and sends nothing. Use an address on a domain you own and set
SPF, DKIM and DMARC for it at your DNS provider.

Environment variables are named `PARTNERTYPE_*`. Instances created before the rename carry
`PARTNERHUB_*` names; they keep working, and the app logs one warning line listing them until you
rename them: `python3 -m tools.instance --env ... --id ... mail` rewrites the instance's compose file with the new names.

## Backup and restore

```sh
age-keygen -o /SAFE/PATH/backup-identity.txt
python3 -m tools.instance --env production --id vendor-a backup \
  /SAFE/PATH/vendor-a.dump.age --recipient age1PUBLIC_RECIPIENT
```

The `pg_dump -Fc` stream goes straight into `age`, with no plain temporary file. The file is never
overwritten. Until `pg_dump` and `age` both succeed the copy has a temporary name
`<file>.*.partial`; a failed backup leaves no `.age` file, so a job that ships `*.age` off the
server never takes a truncated copy. Keep the private key outside the instance directory and outside
the app server. The backup covers the CRM and the schema; instance secrets, CA certificates and the
model need to be stored or recreated separately. **Keep the instance's `secrets/session_secret`
file together with the backup** (protected like the `age` key): the secrets of users' authenticator
apps (TOTP) are stored in the database encrypted with a key derived from it.

Create a **new** instance `vendor-a-restore` with `create --prepare-only`, a different address/port
and its own secrets. Then:

```sh
python3 -m tools.instance --env production --id vendor-a-restore restore \
  /SAFE/PATH/vendor-a.dump.age --identity /SAFE/PATH/backup-identity.txt \
  --session-secret-from /SAFE/PATH/vendor-a.session_secret
python3 -m tools.instance --env production --id vendor-a-restore start
```

**Second factor after a restore.** A new instance has its own `session_secret`. Without
`--session-secret-from` (the original instance's `secrets/session_secret` file) the codes from
authenticator apps stop working for everyone who set one up: an administrator, manager or observer
can then sign in only with a backup code, and once those run out, or when a second factor is
required, not at all. The refusal is safe (nobody gets in without a code) but it locks people out.
`restore` without this option prints the list of affected accounts. Before `start`, either copy the
original's secret to the target's `secrets/session_secret`, or remove the second factor from
everyone on the list with `mfa-reset --email ADDRESS` (the person signs in with their password and
sets up the app again).

Restore refuses to run if the target has tables. `pg_restore` runs in one transaction; afterwards all
sessions and invitation/AI tokens are cleared, and the migrator restores the app permissions. Other
producers are not touched. Install the model separately with `model`.

Store the `.age` files in private storage off the host, with versioning/retention, separate
permissions per producer and regular test restores (`python3 -m tools.restore_drill`). Encryption is
mandatory in the CLI; the schedule, retention and alerts depend on your hosting.

## Demo data

`python3 scripts/seed_demo.py` rebuilds a small demo data set, in development/test only. It creates
everything through the real application functions, so queue positions and commissions are exactly
what the running app would produce.

The `release` image **does not contain** the demo seeders (they create an administrator with a
fixed password), the tests (`api/tests`, `deploy/test-init.sh`) or the test-only tools
(`tools/test_database.py`); run seeding from the source tree or from an
image built with `--target test`.

## Tests and load measurement

```sh
./scripts/test-postgres.sh
npm run coverage
python3 -m pytest tests_operations -q -o addopts=''
```

Backend tests get a separate PostgreSQL process on tmpfs. Every test clones its own
`ph_test_<uuid>` database from a template and uses a restricted application role. Without an explicit
test admin DSN the tests fail, never touching a working database.

Measure 20 clients / 10,000 leads on a separate empty staging instance `load` prepared with
`create --prepare-only`, `migrate`, `start` and `model`, **without** running `create` again (no user
may exist).

```sh
python3 -m tools.instance --env staging --id load load-test \
  --report .artifacts/load-report.json
```

The report has p50/p95/p99, errors, a run with the real AI and a run without it, and `docker stats`
samples for the containers. It measures Gunicorn/API/DB HTTP without the TLS proxy overhead. An AI
status other than 200 means the "during AI" criterion failed. The tool leaves synthetic data behind;
remove it only with `destroy-data`.

## Technical sources

[Flask + Gunicorn](https://flask.palletsprojects.com/en/stable/deploying/gunicorn/),
[Compose services](https://docs.docker.com/reference/compose-file/services/),
[Psycopg pools](https://www.psycopg.org/psycopg3/docs/advanced/pool.html).
