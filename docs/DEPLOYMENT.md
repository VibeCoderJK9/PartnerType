# Running and preparing a deployment

PartnerType runs on PostgreSQL with an explicitly set environment (`api/config.py`); it refuses to
start without them. The detailed instructions are in
**[docs/operations/INSTANCES.md](operations/INSTANCES.md)** (setting up an instance,
upgrading it, backing it up and restoring it) and
**[docs/operations/PUBLIC-SERVER.md](operations/PUBLIC-SERVER.md)** (putting instances on the
internet).

## The shortest way to a running instance

```sh
docker build --target release -t partnertype:local .
docker image inspect partnertype:local --format '{{.Id}}'
python3 -m tools.instance --env staging --id vendor-a create \
  --name 'Vendor A' --email owner@example.test \
  --base-url https://a.localhost:8443 --port 8443 \
  --image sha256:<IMAGE_ID_FROM_THE_COMMAND_ABOVE>
```

The rest — the AI model, the owner invitation, HTTPS, upgrades — is described in [`INSTANCES.md`](operations/INSTANCES.md).
Every instance has its own database, its own secrets and its own networks, isolated physically,
not by filtering queries.

## Health check

```sh
curl --fail --cacert /PATH/TO/root.crt https://a.localhost:8443/health
```

| Result | Meaning |
|---|---|
| HTTP 200, `{"status":"ok"}` | The process answers, the database is reachable and has the schema version this image expects. |
| HTTP 503, `{"status":"unavailable"}` | The database is unreachable **or has not been migrated** to the version the image needs. |

The endpoint needs no sign-in and reveals no paths or data. It runs no migrations and creates no
data — it checks, it does not repair. It does not confirm that mail, the AI model or backups work;
those are separate checks.

## Before you let real data in

A built image is not a green light to open the app to partners. Built in: sign-in attempt limits,
password recovery, a second sign-in factor for producer roles, a session policy, browser security
headers and vulnerability scanning. Your job as the operator: off-server backups on a schedule,
monitoring with alerts, host disk encryption, and the GDPR documents (data processing agreement,
record of processing activities).

## Daily operator tasks

```sh
python3 -m tools.instance --env staging --id vendor-a maintenance
python3 -m tools.instance --env production --id vendor-a backup \
  /SAFE/PATH/backup.age --recipient age1PUBLIC_RECIPIENT
python3 -m tools.restore_drill --env production --id vendor-a
./scripts/security-scan.sh
```

In order: clean-up of expired sessions and used links, an encrypted backup, a check that the backup
can be restored, and a scan for known vulnerabilities. Run the first two on a schedule (for example
cron).

The scanners in `security-scan.sh` are pinned (the `pip-audit` version, and the version and `sha256`
digest of the trivy image); trivy reads an image saved with `docker save` and gets no Docker socket.
Change a scanner version deliberately, together with its digest.

Keep the instance's `secrets/session_secret` file together with the backup: without it, users'
authenticator apps (TOTP) stop working after a restore. Details and the workaround are in
`operations/INSTANCES.md`, in the section on restoring.

`restore_drill` restores the backup into its own new directory
`instances/<env>/.drill-<id>-<random suffix>` and afterwards removes only that directory and its
volumes. The name starts with a dot, so no real instance can be the target of a drill. With `--keep`
the copy stays for inspection (the path is in the report, field `drill_directory`); remove it by hand
afterwards: `docker compose -f <directory>/compose.json down --volumes` and `rm -r <directory>`.

Without `--from-backup` the drill takes a fresh copy and counts rows and amounts in the same database
snapshot `pg_dump` reads, so writes during the dump cause no false differences; the verdict is `OK`
or `DIFFERENCES` (exit code 1). With `--from-backup` there is no record of the database at the time the
backup was taken, and today's database is not that record: the drill checks that the file decrypts
and restores in full, shows the counts and gives the verdict `RESTORED` (exit code 0) without a
comparison.
