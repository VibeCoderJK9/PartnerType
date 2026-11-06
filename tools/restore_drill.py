"""Prove that a backup can actually be restored, and say so with numbers.

A backup nobody has restored is a belief, not a safeguard. This makes a copy
(or takes one the operator already holds), restores it into a brand-new
instance, compares what came back against the original, and throws the
temporary instance away.

Run it on a schedule once there is a server, and by hand before anything that
could go wrong -- an upgrade, a migration, a move to another machine.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

from deploy.compose import document
from tools.instance import addresses_in_use, check_id, compose, create, database, write_new

ROOT = Path(__file__).resolve().parents[1]
# Compared table by table. Counting rows catches a truncated restore; summing
# the money catches the subtler failure where rows survive but values do not.
COUNTED_TABLES = (
    "users", "leads", "lead_comments", "lead_workflow_events",
    "commission_payouts", "bonus_awards", "products", "commission_tiers",
    "bonus_definitions", "partner_bonus_assignments", "audit_log",
)
MONEY = (
    ("leads", "value"), ("leads", "commission"),
    ("commission_payouts", "amount"), ("bonus_awards", "amount"),
)


def age_binary():
    """Prefer the operator's local copy over whatever is on PATH."""
    local = ROOT / "tools/bin/age"
    return str(local) if local.exists() else "age"


def psql(directory, query):
    result = compose(directory, "exec", "-T", "db", "psql", "-U", "partnerhub_owner",
                     "-d", database_name(directory),
                     "-Atc", query, capture_output=True, text=True)
    return result.stdout.strip()


def fingerprint(directory, ask=None):
    """Row counts and money totals, as one comparable dictionary.

    `ask` answers one query; by default each query is its own psql call.
    """
    ask = ask or (lambda query: psql(directory, query))
    counts = {}
    for table in COUNTED_TABLES:
        counts[table] = int(ask(f"SELECT COUNT(*) FROM {table}") or 0)
    totals = {}
    for table, column in MONEY:
        value = ask(f"SELECT COALESCE(SUM({column}),0) FROM {table}")
        totals[f"{table}.{column}"] = round(float(value or 0), 2)
    return {"rows": counts, "money": totals}


def database_name(directory):
    return json.loads((directory / "config.json").read_text()).get("db_name", "partnerhub")


def consistent_dump(source, handle, recipient):
    """Encrypted dump of `source` into `handle`, and the fingerprint of that very dump.

    Both come from one exported snapshot: the counts are taken inside the
    transaction that exports it and pg_dump reads through it, so writes that
    land while the dump runs change neither. Counting the live database
    afterwards would compare the copy with a database that has moved on.
    """
    exec_db = ["docker", "compose", "-f", str(source / "compose.json"), "exec", "-T", "db"]
    session = subprocess.Popen(
        [*exec_db, "psql", "-U", "partnerhub_owner", "-d", database_name(source),
         "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def ask(query):
        session.stdin.write(query + ";\n")
        session.stdin.flush()
        line = session.stdout.readline()
        if not line:
            raise SystemExit("Could not read the database state to compare")
        return line.strip()

    try:
        session.stdin.write("BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY;\n")
        snapshot = ask("SELECT pg_export_snapshot()")
        if not re.fullmatch(r"[0-9A-Fa-f-]+", snapshot):
            raise SystemExit("Could not make the backup")
        original = fingerprint(source, ask)
        dump = subprocess.Popen(
            [*exec_db, "pg_dump", "-U", "partnerhub_owner", "-d", database_name(source),
             "--snapshot=" + snapshot, "-Fc", "--no-owner", "--no-acl"], stdout=subprocess.PIPE)
        try:
            encrypted = subprocess.run([age_binary(), "-r", recipient], stdin=dump.stdout, stdout=handle)
            dump.stdout.close()
            if dump.wait() or encrypted.returncode:
                raise SystemExit("Could not make the backup")
        finally:
            if dump.poll() is None:
                dump.kill()
                dump.wait()
        return original
    finally:
        try:
            session.stdin.write("COMMIT;\n")
            session.stdin.close()
        except (BrokenPipeError, ValueError):
            pass
        try:
            session.wait(timeout=30)
        except subprocess.TimeoutExpired:
            session.kill()
            session.wait()


def compare(original, restored):
    """Where the copy disagrees with the original, in both directions.

    Separate from the orchestration so the comparison can be tested without
    Docker -- the part that decides pass or fail deserves that.
    """
    return {
        key: {"original": original[kind][key], "restored": restored[kind][key]}
        for kind in ("rows", "money")
        for key in original[kind]
        if original[kind][key] != restored[kind][key]
    }


def drill_target(env, instance_id):
    """A fresh directory for this run's temporary copy.

    The name starts with a dot, which no instance ID can, and carries a random
    suffix: no real instance -- and no copy left by an earlier run -- can ever
    be the directory this run creates and later deletes.
    """
    return ROOT / "instances" / env / f".drill-{instance_id}-{secrets.token_hex(4)}"


def drill(env, instance_id, target, keep=False, source_backup=None, identity=None):
    source = ROOT / "instances" / env / instance_id
    if not (source / "config.json").exists():
        raise SystemExit(f"Unknown instance: {env}/{instance_id}")

    # A valid instance ID of its own, whatever the length of the original's.
    target_id = "drill-" + secrets.token_hex(4)
    if target.exists():
        raise SystemExit(f"The drill directory already exists: {target}")

    report = {"started_at": datetime.utcnow().isoformat(), "instance": f"{env}/{instance_id}",
              "drill_directory": str(target)}
    workspace = Path(tempfile.mkdtemp(prefix="restore-drill-"))
    try:
        if source_backup:
            backup_file, identity_file = Path(source_backup), Path(identity)
            report["backup"] = {"source": "existing file", "path": str(backup_file)}
        else:
            # An ephemeral key proves the mechanism without touching the real
            # backup key. Pass --from-backup to drill the real thing instead.
            identity_file = workspace / "drill-identity.txt"
            subprocess.run([age_binary() + "-keygen", "-o", str(identity_file)],
                           check=True, capture_output=True)
            recipient = subprocess.run([age_binary() + "-keygen", "-y", str(identity_file)],
                                       check=True, capture_output=True, text=True).stdout.strip()
            backup_file = workspace / "drill.dump.age"
            with open(backup_file, "xb") as handle:
                report["original"] = consistent_dump(source, handle, recipient)
            report["backup"] = {"source": "fresh backup", "bytes": backup_file.stat().st_size}

        config = json.loads((source / "config.json").read_text())
        # The copy never starts its proxy, but create still refuses a port
        # another instance holds; take the first free one.
        _, ports = addresses_in_use(target.parent)
        port = config["port"] + 1000
        while port in ports:
            port += 1
        arguments = argparse.Namespace(
            id=target_id, env=env, name=config["name"], email="drill@example.test",
            base_url=f"https://{target_id}.localhost:{port}",
            port=port, image=config["image"], prepare_only=True)
        create(arguments, target)
        # Same order as tools/instance.py restore: an empty database first,
        # the dump second, the migrator last. Migrating before the restore
        # leaves tables in the way and pg_restore refuses the whole thing.
        database(target)

        decrypt = subprocess.Popen([age_binary(), "-d", "-i", str(identity_file), str(backup_file)],
                                   stdout=subprocess.PIPE)
        try:
            compose(target, "exec", "-T", "db", "pg_restore", "-U", "partnerhub_owner",
                    "-d", database_name(target),
                    "--no-owner", "--no-acl", "--exit-on-error", "--single-transaction",
                    stdin=decrypt.stdout)
            decrypt.stdout.close()
            if decrypt.wait():
                raise SystemExit("Could not decrypt the backup")
        finally:
            # A failed restore stops reading; the decryptor must not be left behind.
            if decrypt.poll() is None:
                decrypt.kill()
                decrypt.wait()
        # Restores the application role's grants, exactly as the real command does.
        compose(target, "run", "--rm", "migrate")

        report["restored"] = fingerprint(target)
        if "original" not in report:
            # An existing backup has no record of what the database held when
            # it was taken, and today's live database is not that. The drill
            # still proves the file decrypts and restores in full (one
            # transaction, stopping at the first error); it shows the counts
            # and claims no comparison it cannot make.
            report["differences"] = None
            report["verdict"] = "RESTORED"
            return report
        differences = compare(report["original"], report["restored"])
        report["differences"] = differences
        report["verdict"] = "OK" if not differences else "DIFFERENCES"
        return report
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def cleanup(target):
    """Remove this run's temporary copy: its containers, volumes and directory."""
    try:
        compose(target, "down", "--volumes")
    except Exception:
        pass
    shutil.rmtree(target, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True,
                        choices=["development", "test", "staging", "production"])
    parser.add_argument("--id", required=True)
    parser.add_argument("--from-backup", help="restore an existing backup instead of making a new one")
    parser.add_argument("--identity", help="private key for --from-backup")
    parser.add_argument("--keep", action="store_true",
                        help="keep the drill instance for inspection")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if bool(args.from_backup) != bool(args.identity):
        parser.error("--from-backup wymaga --identity")
    try:
        check_id(args.id)
    except ValueError as error:
        parser.error(str(error))

    target = drill_target(args.env, args.id)
    if target.exists():
        parser.error(f"{target} already exists; refusing to touch it")
    try:
        report = drill(args.env, args.id, target, args.keep, args.from_backup, args.identity)
    finally:
        # Cleanup belongs here rather than inside the drill: a run that failed
        # halfway is exactly the one that would otherwise leave a second copy
        # of the producer's data sitting on disk. The target did not exist
        # before this run (checked above, random name), so whatever is there
        # now is this run's own copy and nothing else.
        if target.exists() and not args.keep:
            cleanup(target)

    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.report:
        args.report.write_text(text + "\n")
    print(text)
    return 0 if report["verdict"] in ("OK", "RESTORED") else 1


if __name__ == "__main__":
    sys.exit(main())
