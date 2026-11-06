"""Product catalogue gains its own English name, mirroring commission_tiers.name_en."""
from alembic import op


revision = '0009_product_name_en'
down_revision = '0008_commission_tier_name_en'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # The producer names his own products (e.g. "ERP implementation", "CRM licence");
    # name_en is his own English wording of the same product, following the
    # *_en + Ui.producerLabel + Settings switch pattern already shipped for
    # bonus_definitions, commission_tiers and lead_stages.
    # Empty means "not translated yet" -- Ui.producerLabel falls
    # back to the Polish original rather than showing a blank name, so a
    # plain ADD COLUMN with an empty default is correct for every existing
    # row, not a placeholder to backfill. The product's CATEGORY stays the
    # existing closed enum of stored category values (see api/products.py) -- this
    # column never touches it.
    connection.exec_driver_sql(
        "ALTER TABLE products ADD COLUMN name_en TEXT NOT NULL DEFAULT ''")


def downgrade():
    raise RuntimeError('Destructive downgrade forbidden. Restore a backup into a new instance.')
