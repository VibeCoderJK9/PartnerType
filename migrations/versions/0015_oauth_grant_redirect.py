"""OAuth grants remember the redirect URI the user actually approved."""
from alembic import op


revision = '0015_oauth_grant_redirect'
down_revision = '0014_mfa_replay_guard'
branch_labels = None
depends_on = None


def upgrade():
    # A client may register several redirect URIs. The connection lists must
    # show where the user's consent really sent the code, not whichever URI
    # the client happened to register first. Older grants are filled from
    # their code while it still exists; the rest stay NULL and the lists fall
    # back to every registered host.
    connection = op.get_bind()
    connection.exec_driver_sql("ALTER TABLE oauth_grants ADD COLUMN redirect_uri TEXT")
    connection.exec_driver_sql("""
        UPDATE oauth_grants g SET redirect_uri = c.redirect_uri
        FROM oauth_codes c
        WHERE c.grant_id = g.id AND g.redirect_uri IS NULL
    """)


def downgrade():
    raise RuntimeError('Destructive downgrade forbidden. Restore a backup into a new instance.')
