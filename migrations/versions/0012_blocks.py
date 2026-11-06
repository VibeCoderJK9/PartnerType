"""Blocks: per-instance switches and the small memory each block keeps."""
from alembic import op


revision = '0012_blocks'
down_revision = '0011_terminal_stage_colours'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # Every producer runs its partner channel differently,
    # so the product is a shared core plus blocks that fit it to one company's
    # process. A block is off until this producer's admin switches it on, and
    # its settings live here rather than in `settings`, whose keys are a fixed
    # list the Settings form edits as a whole.
    connection.exec_driver_sql("""
        CREATE TABLE block_config (
            block_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
            config_json TEXT NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # What a block remembers between runs ("already reminded about partner 7
    # for this stretch of silence"), keyed by whatever the block calls its
    # subject. Ids and dates only: no names, no client data.
    connection.exec_driver_sql("""
        CREATE TABLE block_state (
            block_id TEXT NOT NULL,
            subject TEXT NOT NULL,
            value_json TEXT NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (block_id, subject)
        )
    """)


def downgrade():
    raise RuntimeError('Destructive downgrade forbidden. Restore a backup into a new instance.')
