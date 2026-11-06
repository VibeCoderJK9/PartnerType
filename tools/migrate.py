"""Operator-only migrations; the application role never receives DDL rights."""
import os
from alembic import command
from alembic.config import Config
from psycopg import connect, sql
from api.config import secret


def main():
    dsn = secret('MIGRATION_DATABASE_URL')
    if not dsn:
        raise ValueError('MIGRATION_DATABASE_URL required')
    command.upgrade(Config('alembic.ini'), 'head')
    role = os.environ.get('PARTNERTYPE_DB_ROLE', 'partnerhub_app')
    with connect(dsn) as conn:
        conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
        conn.execute(sql.SQL('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(role)))
        conn.execute(sql.SQL('REVOKE ALL ON alembic_version FROM {}').format(sql.Identifier(role)))
        conn.execute(sql.SQL('GRANT SELECT ON alembic_version TO {}').format(sql.Identifier(role)))
        conn.execute(sql.SQL('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(sql.Identifier(role)))
        # Views run with their owner's rights: read-only for the application,
        # or a write through one would get around row-level security.
        for (view,) in conn.execute("SELECT viewname FROM pg_views WHERE schemaname = 'public'"):
            conn.execute(sql.SQL('REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {} FROM {}').format(
                sql.Identifier(view), sql.Identifier(role)))


if __name__ == '__main__':
    main()
