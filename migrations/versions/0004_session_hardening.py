"""Hashed session tokens and an absolute lifetime for every session."""
from alembic import op


revision = '0004_session_hardening'
down_revision = '0003_password_resets'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # Until now the sliding 24h window had no ceiling, so a session that was
    # used daily never ended -- including one somebody else was holding.
    connection.exec_driver_sql(
        'ALTER TABLE sessions ADD COLUMN absolute_expires_at TEXT')
    # The column now holds sha256(token), the way invitation and reset secrets
    # are already stored. Existing rows hold raw tokens that would never match
    # a hashed lookup again, so they are removed rather than left as litter:
    # everyone signs in once more, and a stolen database stops being a set of
    # working session keys.
    connection.exec_driver_sql('DELETE FROM sessions')
    connection.exec_driver_sql(
        "UPDATE sessions SET absolute_expires_at = expires_at WHERE absolute_expires_at IS NULL")


def downgrade():
    raise RuntimeError('Destructive downgrade forbidden. Restore a backup into a new instance.')
