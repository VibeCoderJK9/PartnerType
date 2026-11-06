"""Bonus award snapshot gains its English name, empty for every historic row."""
from alembic import op


revision = '0007_bonus_award_name_en'
down_revision = '0006_lead_list_indexes'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # Snapshotted once at grant time, exactly like `name`: a later rename or
    # translation of the definition must never rewrite what a partner was
    # already told they were promised. Historic awards get an
    # empty EN rather than a backfill from the CURRENT definition -- the
    # definition may not have had an English name at grant time at all, or a
    # different one since, and neither is a trustworthy stand-in for history.
    connection.exec_driver_sql(
        "ALTER TABLE bonus_awards ADD COLUMN name_en TEXT NOT NULL DEFAULT ''")


def downgrade():
    raise RuntimeError('Destructive downgrade forbidden. Restore a backup into a new instance.')
