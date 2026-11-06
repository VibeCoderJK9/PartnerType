"""The audit log is append-only.

Nothing in the application changes an audit entry, so no role may: the update
policy is dropped and, with row-level security forced, an UPDATE matches no
row. Deleting is left only to the instance itself, which is how the operator's
retention command prunes old entries (api/retention.py runs in
`system_context`). An administrator or moderator session can no longer erase
its own tracks, even through a bug in the SQL it runs.
"""
from alembic import op


revision = '0020_audit_log_append_only'
down_revision = '0019_knowledge_base'
branch_labels = None
depends_on = None

WRITES_ALL = "app_role() IN ('system','admin','manager')"
SYSTEM = "app_role() = 'system'"


def upgrade():
    connection = op.get_bind()
    connection.exec_driver_sql('DROP POLICY IF EXISTS audit_log_update ON audit_log')
    connection.exec_driver_sql('DROP POLICY IF EXISTS audit_log_delete ON audit_log')
    connection.exec_driver_sql(
        f'CREATE POLICY audit_log_delete ON audit_log FOR DELETE USING ({SYSTEM})')


def downgrade():
    connection = op.get_bind()
    connection.exec_driver_sql('DROP POLICY IF EXISTS audit_log_delete ON audit_log')
    connection.exec_driver_sql(
        f'CREATE POLICY audit_log_delete ON audit_log FOR DELETE USING ({WRITES_ALL})')
    connection.exec_driver_sql(
        f'CREATE POLICY audit_log_update ON audit_log FOR UPDATE '
        f'USING ({WRITES_ALL}) WITH CHECK ({WRITES_ALL})')
