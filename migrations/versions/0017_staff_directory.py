"""Partners see the producer's staff only through a directory of safe columns.

Migration 0016 let a partner read whole admin and manager rows of `users`
(he needs their names on comments and notifications), which included the
password hash, the second-factor flags, phone numbers and notes. Column
privileges cannot fix that: they belong to a database role, and every request
uses the same application role. So:

  * users_select now lets a partner read only his own row;
  * staff_directory exposes id, name, e-mail and role of every staff account
    (admin, manager, observer) and nothing else. It runs with its owner's
    rights, so it is the one road from a partner context to staff rows, and it
    returns nothing without a known context (fail closed, as every policy);
  * active_staff() gives only id and role of the active staff accounts, for
    notifying the producer about a partner's action.
"""
from alembic import op


revision = '0017_staff_directory'
down_revision = '0016_row_level_security'
branch_labels = None
depends_on = None

SEES_ALL = "app_role() IN ('system','admin','manager','observer')"
KNOWN = "app_role() IN ('system','admin','manager','observer','partner')"
PARTNER = "app_role() = 'partner'"

OLD_USING = (f"{SEES_ALL} OR ({KNOWN} AND id = app_user_id()) "
             f"OR ({PARTNER} AND role IN ('admin','manager'))")
NEW_USING = f"{SEES_ALL} OR ({KNOWN} AND id = app_user_id())"


def _select_policy(connection, using):
    connection.exec_driver_sql('DROP POLICY IF EXISTS users_select ON users')
    connection.exec_driver_sql(f'CREATE POLICY users_select ON users FOR SELECT USING ({using})')


def upgrade():
    connection = op.get_bind()
    _select_policy(connection, NEW_USING)
    connection.exec_driver_sql(f"""
        CREATE VIEW staff_directory WITH (security_barrier) AS
        SELECT DISTINCT id, name, email, role
          FROM users
         WHERE role IN ('admin', 'manager', 'observer')
           AND {KNOWN}
    """)
    # Who to notify about a partner's action: id and role of each active
    # staff account, nothing else (which accounts are inactive stays private).
    connection.exec_driver_sql(f"""
        CREATE FUNCTION active_staff() RETURNS TABLE (id integer, role text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
        AS $$ SELECT u.id, u.role FROM users u
              WHERE u.role IN ('admin', 'manager', 'observer') AND u.active = 1 AND {KNOWN} $$
    """)
    # The view runs with its owner's rights, so it must never be writable:
    # DISTINCT makes it non-updatable, and tools/migrate.py, which grants the
    # application role its table rights after each upgrade, gives it SELECT
    # only. (A plain single-table view would be automatically updatable.)


def downgrade():
    connection = op.get_bind()
    connection.exec_driver_sql('DROP FUNCTION IF EXISTS active_staff()')
    connection.exec_driver_sql('DROP VIEW IF EXISTS staff_directory')
    _select_policy(connection, OLD_USING)
