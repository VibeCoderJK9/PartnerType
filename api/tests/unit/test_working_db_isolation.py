"""Database fixtures must never fall back to the working instance."""
from urllib.parse import urlsplit
import os
from api.db import get_conn


def test_uses_unique_postgres_database(test_db_path):
    assert test_db_path == os.environ['DATABASE_URL']
    assert urlsplit(test_db_path).path.startswith('/ph_test_')
    with get_conn() as conn:
        assert conn.execute('SELECT current_database()').fetchone()[0].startswith('ph_test_')
        assert conn.execute('SELECT count(*) FROM users').fetchone()[0] == 0


def test_app_role_cannot_change_schema():
    import pytest
    from psycopg.errors import InsufficientPrivilege
    with get_conn() as conn:
        with pytest.raises(InsufficientPrivilege):
            conn.execute('CREATE TABLE forbidden(id int)')
