# Public server: your PartnerType on the internet

This guide takes you from a rented server to an address like `https://prm.your-company.example`,
where you and your partners sign in from anywhere. It assumes you know [INSTANCES.md](INSTANCES.md).

## How it is built

```
internet ──443──▶ Caddy (tools/edge.py) ──▶ 127.0.0.1:18001 ─▶ your PartnerType instance
                  real certificate
```

- **Caddy from `tools/edge.py` is the only service visible from the internet.**
  - It has ports 80 and 443.
  - It gets a Let's Encrypt certificate itself.
  - It passes requests for your address to the instance.
- **An address that is not on the list gets no answer.**
- **The instance listens only on `127.0.0.1`.**
- **Caddy's admin API is not on a port.** Instead of the default `localhost:2019` (on the host
  network, reachable by every local process) it listens on the socket `/data/admin.sock` in the
  container's volume; `tools.edge … reload` uses it through `docker compose exec`.
- **Already run a reverse proxy (nginx, Traefik, your own Caddy)?** Skip `tools/edge.py`: create the
  instance with `--edge`, which makes it speak plain HTTP on its localhost port, and point your
  proxy at that port. Your proxy must terminate TLS and pass `X-Forwarded-For` and
  `X-Forwarded-Proto`.

## What you need before you start

1. **A Linux server with Docker and Docker Compose.**
   - Without the AI assistant, about 1–1.5 GB RAM is enough, plus headroom for the system.
   - With the assistant add about 4 GB.
2. **A domain and an A record** (e.g. `prm.your-company.example`) pointing to the server's IP address.
3. **Open ports 80 and 443.** The instance port (e.g. 18001) stays closed from outside.
4. **A transactional mail provider**, e.g. Brevo, Postmark or Amazon SES.
   - Put its details in `instances/production/mail.json` and `mail_password`, as in INSTANCES.md.
   - Set SPF, DKIM and DMARC for the sender domain.
5. **Backup storage off the server**, e.g. a storage box at your hosting provider.

## Setting it up

```sh
# 1. The instance behind Caddy. Add --no-ai to run without the AI assistant.
python3 -m tools.instance --env production --id my-company create \
  --name "My Company" --email owner@your-company.example \
  --base-url https://prm.your-company.example --port 18001 \
  --image sha256:... --edge

# 2. Caddy: "start" the first time, "reload" after changing the instance's address.
python3 -m tools.edge --env production --email you@your-company.example start

# 3. Test mail, then open the link from owner-invitation.json.
python3 -m tools.instance --env production --id my-company mail-test --to you@your-company.example
```

- **The invitation link works once and is valid for 7 days.**
- **Partners register through a link** the owner finds in Settings → General → "Partner links".

## What this guide does not cover

- **Backups are manual.** Schedule the `backup` command from INSTANCES.md in cron and send the file
  off the server.
- **Monitoring is manual too.** The simplest option is an uptime checker that calls
  `https://prm.your-company.example/health`.
- **GDPR:** a privacy policy and, where you process partners' data, the agreements your law requires.
