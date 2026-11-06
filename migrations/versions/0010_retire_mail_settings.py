"""Mail server settings leave the producer's settings table."""
from alembic import op


revision = '0010_retire_mail_settings'
down_revision = '0009_product_name_en'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # Mail is the operator's, one mailbox for the whole
    # server, handed to each instance as environment variables by
    # tools/instance.py. The smtp_* rows a producer could once edit are read by
    # nothing any more; left behind, an old host or address would sit in every
    # backup looking as if it still mattered.
    connection.exec_driver_sql(
        "DELETE FROM settings WHERE key IN "
        "('smtp_host','smtp_port','smtp_user','smtp_pass','smtp_from','smtp_enabled')")


def downgrade():
    raise RuntimeError('Destructive downgrade forbidden. Restore a backup into a new instance.')
