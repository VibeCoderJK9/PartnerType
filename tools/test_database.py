"""Test-only database factory: strict admin DSN, UUID names, template cloning."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from contextlib import contextmanager
from psycopg import connect, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo as _make_conninfo
from urllib.parse import quote

def make_conninfo(base, **kwargs):
    p=conninfo_to_dict(_make_conninfo(base,**kwargs))
    return "postgresql://{}:{}@{}:{}/{}".format(quote(p["user"],safe=""),quote(p.get("password",""),safe=""),p["host"],p.get("port","5432"),p["dbname"])


def admin_dsn():
    value=os.environ.get('PARTNERTYPE_TEST_ADMIN_URL','')
    if not value:
        raise RuntimeError('Set PARTNERTYPE_TEST_ADMIN_URL to the isolated test server; SQLite is not supported')
    parsed=conninfo_to_dict(value)
    if parsed.get('dbname') != 'ph_test_control' or parsed.get('user') != 'ph_test_owner':
        raise RuntimeError('Refusing non-test admin database/user')
    return value


def migrate(dsn):
    env={**os.environ,'MIGRATION_DATABASE_URL':dsn,'PARTNERTYPE_DB_ROLE':'ph_test_app'}
    env.pop('MIGRATION_DATABASE_URL_FILE',None)
    subprocess.run([sys.executable,'-m','tools.migrate'],env=env,check=True,capture_output=True)


def name():return 'ph_test_'+uuid.uuid4().hex


def drop(database):
    if not database.startswith('ph_test_') or database=='ph_test_control':
        raise RuntimeError('Unsafe drop refused')
    with connect(admin_dsn(),autocommit=True) as conn:
        conn.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(database)))


@contextmanager
def template():
    database=name()
    dsn=make_conninfo(admin_dsn(),dbname=database)
    with connect(admin_dsn(),autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    try:
        migrate(dsn)
        with connect(dsn) as conn:
            for table,rows in json.loads(Path('api/tests/factory_defaults.json').read_text()).items():
                for row in rows:
                    if row.get('key')=='smtp_pass':continue
                    conn.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,row)),sql.SQL(',').join(sql.Placeholder() for _ in row)),list(row.values()))
            for table in ('commission_tiers','bonus_definitions'):
                conn.execute(sql.SQL("SELECT setval(pg_get_serial_sequence(%s,'id'),MAX(id)) FROM {}").format(sql.Identifier(table)),(table,))
        yield database
    finally:drop(database)


@contextmanager
def database(template_name):
    database_name=name()
    with connect(admin_dsn(),autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(sql.Identifier(database_name),sql.Identifier(template_name)))
    try:
        yield make_conninfo(admin_dsn(),dbname=database_name,user='ph_test_app',password=os.environ['PARTNERTYPE_TEST_APP_PASSWORD']),make_conninfo(admin_dsn(),dbname=database_name)
    finally:drop(database_name)
