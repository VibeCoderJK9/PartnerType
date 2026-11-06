"""Indexes for the paged lead list, which now orders and slices in the database."""
from alembic import op


revision = '0006_lead_list_indexes'
down_revision = '0005_mfa'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # The default order of every lead read: newest first, with the same unique
    # tiebreak the query applies (api/leads.py::_LEAD_SORT_TIEBREAK). Without
    # it, answering "the first twenty-five" means sorting every lead in the
    # programme first -- ten thousand rows of work to return twenty-five.
    connection.exec_driver_sql(
        'CREATE INDEX idx_leads_created_at ON leads (created_at DESC, id DESC)')
    # The producer's own queue view: everything still awaiting their decision,
    # oldest first. A partial index, because the rows it leaves out are the
    # ones this ordering pushes to the bottom anyway, and leaving them out
    # keeps it small on a programme where most leads have been decided.
    connection.exec_driver_sql("""
        CREATE INDEX idx_leads_awaiting_decision ON leads (created_at, id)
        WHERE registration_status IN ('pending', 'needs_info')
    """)
    # The two filters that reach for one value out of many: the producer's
    # decision, and one partner's leads in date order.
    connection.exec_driver_sql(
        'CREATE INDEX idx_leads_registration_status ON leads (registration_status)')
    connection.exec_driver_sql(
        'CREATE INDEX idx_leads_partner_created ON leads (partner_id, created_at DESC)')


def downgrade():
    raise RuntimeError('Destructive downgrade forbidden. Restore a backup into a new instance.')
