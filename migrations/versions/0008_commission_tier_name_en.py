"""Commission tier gains its own English name, mirroring bonus_definitions.name_en."""
from alembic import op


revision = '0008_commission_tier_name_en'
down_revision = '0007_bonus_award_name_en'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # The producer names his own commission tiers ("Diament", "Partner
    # Premium"); name_en is his own English wording of the same tier,
    # following the *_en + Ui.producerLabel + Settings switch pattern
    # already shipped for bonus_definitions, lead_stages and the
    # registration/lead question fields. Empty means "not
    # translated yet" -- Ui.producerLabel falls back to the Polish original
    # rather than showing a blank badge, so a plain ADD COLUMN with an empty
    # default is correct for every existing row, not a placeholder to backfill.
    connection.exec_driver_sql(
        "ALTER TABLE commission_tiers ADD COLUMN name_en TEXT NOT NULL DEFAULT ''")


def downgrade():
    raise RuntimeError('Destructive downgrade forbidden. Restore a backup into a new instance.')
