"""Won and Lost get fixed colours of their own."""
from alembic import op


revision = '0011_terminal_stage_colours'
down_revision = '0010_retire_mail_settings'
branch_labels = None
depends_on = None

WIN, LOSE = '#c026d3', '#334155'


def upgrade():
    # Green and red terminal stages would match the Accepted and Rejected
    # registration decisions, so on
    # the lead list and the kanban a won deal and an accepted one looked alike.
    # Fuchsia and dark slate belong to neither axis, nor to a default stage.
    # Terminal colours are not editable (api/lead_stages.py refuses), so every
    # instance gets these.
    connection = op.get_bind()
    connection.exec_driver_sql("UPDATE lead_stages SET color = %s WHERE kind = 'win'", (WIN,))
    connection.exec_driver_sql("UPDATE lead_stages SET color = %s WHERE kind = 'lose'", (LOSE,))


def downgrade():
    connection = op.get_bind()
    connection.exec_driver_sql("UPDATE lead_stages SET color = '#10b981' WHERE kind = 'win'")
    connection.exec_driver_sql("UPDATE lead_stages SET color = '#ef4444' WHERE kind = 'lose'")
