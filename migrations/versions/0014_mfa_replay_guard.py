"""Second factor: remember the last accepted authenticator time step."""
from alembic import op


revision = '0014_mfa_replay_guard'
down_revision = '0013_api_gateway'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # A six-digit code stays valid for about ninety seconds (one step of clock
    # tolerance either way). Recording the step of the last code accepted at
    # sign-in lets the same code, or an older one, be refused the second time,
    # so a code read over somebody's shoulder cannot be replayed.
    connection.exec_driver_sql(
        'ALTER TABLE user_mfa ADD COLUMN last_used_step BIGINT')


def downgrade():
    raise RuntimeError('Destructive downgrade forbidden. Restore a backup into a new instance.')
