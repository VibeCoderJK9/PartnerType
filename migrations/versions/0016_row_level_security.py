"""Row-level security: the database itself keeps partners apart.

Until now a partner saw only his own rows because every query remembered to
filter on partner_id. One forgotten filter (GET /api/partner-bonuses, found in
review) was enough to hand other partners' e-mails and rates out. From here on
PostgreSQL enforces the same rule for every statement the application role
runs, whatever the SQL says.

The application tells the database whom it acts for through two session
settings, `app.role` and `app.user_id` (api/postgres.py, db_context). The
policies below read them through `app_role()` and `app_user_id()`:

  system            the instance itself: sign-in, registration, maintenance,
                    operator commands, the duplicate-client check. All rows.
  admin, manager    the producer's staff. All rows, as the app already allows.
  observer          read-only staff. Reads the lead side and the people on it;
                    writes nothing here except its own notifications and its
                    own account.
  partner           only rows that belong to him (partner_id / user_id), never
                    an internal comment, and of the users table only himself
                    and the producer's staff (names on comments and leads).
  (empty/unknown)   nothing. A path that forgot to set a context fails closed.

FORCE ROW LEVEL SECURITY keeps the policies on even for a table owner that is
not a superuser. The migration role is a superuser in every deployment and so
still bypasses them, which is what migrations, backups and restores need.
"""
from alembic import op


revision = '0016_row_level_security'
down_revision = '0015_oauth_grant_redirect'
branch_labels = None
depends_on = None


# Role groups, as SQL predicates over the session context.
SEES_ALL = "app_role() IN ('system','admin','manager','observer')"
WRITES_ALL = "app_role() IN ('system','admin','manager')"
KNOWN = "app_role() IN ('system','admin','manager','observer','partner')"
PARTNER = "app_role() = 'partner'"
OWN_LEAD = ("EXISTS (SELECT 1 FROM leads own WHERE own.id = {table}.lead_id "
            "AND own.partner_id = app_user_id())")


def _policies():
    """(table, policy name, command, USING, WITH CHECK) for every policy."""
    lead = OWN_LEAD.format(table='lead_comments')
    field = OWN_LEAD.format(table='lead_field_values')
    event = OWN_LEAD.format(table='lead_workflow_events')
    rows = [
        # Leads: a partner reads, submits and edits only his own; deleting a
        # lead is a staff act (GDPR erasure).
        ('leads', 'leads_select', 'SELECT',
         f"{SEES_ALL} OR ({PARTNER} AND partner_id = app_user_id())", None),
        ('leads', 'leads_insert', 'INSERT', None,
         f"{WRITES_ALL} OR ({PARTNER} AND partner_id = app_user_id())"),
        ('leads', 'leads_update', 'UPDATE',
         f"{WRITES_ALL} OR ({PARTNER} AND partner_id = app_user_id())",
         f"{WRITES_ALL} OR ({PARTNER} AND partner_id = app_user_id())"),
        ('leads', 'leads_delete', 'DELETE', WRITES_ALL, None),

        # Comments: never an internal one for a partner, only on his own lead,
        # and he may write or remove only what he wrote himself.
        ('lead_comments', 'lead_comments_select', 'SELECT',
         f"{SEES_ALL} OR ({PARTNER} AND is_internal = 0 AND {lead})", None),
        ('lead_comments', 'lead_comments_insert', 'INSERT', None,
         f"{WRITES_ALL} OR ({PARTNER} AND user_id = app_user_id() AND is_internal = 0 AND {lead})"),
        ('lead_comments', 'lead_comments_update', 'UPDATE',
         f"{WRITES_ALL} OR ({PARTNER} AND user_id = app_user_id() AND is_internal = 0 AND {lead})",
         f"{WRITES_ALL} OR ({PARTNER} AND user_id = app_user_id() AND is_internal = 0 AND {lead})"),
        ('lead_comments', 'lead_comments_delete', 'DELETE',
         f"{WRITES_ALL} OR ({PARTNER} AND user_id = app_user_id() AND is_internal = 0 AND {lead})",
         None),

        # Answers to the producer's lead questions follow their lead.
        ('lead_field_values', 'lead_field_values_select', 'SELECT',
         f"{SEES_ALL} OR ({PARTNER} AND {field})", None),
        ('lead_field_values', 'lead_field_values_insert', 'INSERT', None,
         f"{WRITES_ALL} OR ({PARTNER} AND {field})"),
        ('lead_field_values', 'lead_field_values_update', 'UPDATE',
         f"{WRITES_ALL} OR ({PARTNER} AND {field})",
         f"{WRITES_ALL} OR ({PARTNER} AND {field})"),
        ('lead_field_values', 'lead_field_values_delete', 'DELETE',
         f"{WRITES_ALL} OR ({PARTNER} AND {field})", None),

        # A lead's history: readable with the lead, appended only by its own
        # partner acting as himself, never rewritten by him.
        ('lead_workflow_events', 'lead_workflow_events_select', 'SELECT',
         f"{SEES_ALL} OR ({PARTNER} AND {event})", None),
        ('lead_workflow_events', 'lead_workflow_events_insert', 'INSERT', None,
         f"{WRITES_ALL} OR ({PARTNER} AND actor_id = app_user_id() AND {event})"),
        ('lead_workflow_events', 'lead_workflow_events_update', 'UPDATE',
         WRITES_ALL, WRITES_ALL),
        ('lead_workflow_events', 'lead_workflow_events_delete', 'DELETE', WRITES_ALL, None),

        # Idempotency records hold a result snapshot; each actor sees his own.
        ('lead_workflow_operations', 'lead_workflow_operations_select', 'SELECT',
         f"{SEES_ALL} OR ({PARTNER} AND actor_id = app_user_id())", None),
        ('lead_workflow_operations', 'lead_workflow_operations_insert', 'INSERT', None,
         f"{WRITES_ALL} OR ({PARTNER} AND actor_id = app_user_id())"),
        ('lead_workflow_operations', 'lead_workflow_operations_update', 'UPDATE',
         WRITES_ALL, WRITES_ALL),
        ('lead_workflow_operations', 'lead_workflow_operations_delete', 'DELETE', WRITES_ALL, None),
    ]

    # Money: payouts, granted cash bonuses and assigned percent bonuses. The
    # partner reads his own; only staff write. The read-only observer has no
    # business here (api/authz.py: leads and nothing else).
    for table in ('commission_payouts', 'bonus_awards', 'partner_bonus_assignments'):
        rows += [
            (table, f'{table}_select', 'SELECT',
             f"{WRITES_ALL} OR ({PARTNER} AND partner_id = app_user_id())", None),
            (table, f'{table}_insert', 'INSERT', None, WRITES_ALL),
            (table, f'{table}_update', 'UPDATE', WRITES_ALL, WRITES_ALL),
            (table, f'{table}_delete', 'DELETE', WRITES_ALL, None),
        ]

    rows += [
        # Notifications: everyone reads and manages his own. Any signed-in
        # action may notify somebody else (a partner's lead notifies the
        # producer), so inserting is open to every known context.
        ('notifications', 'notifications_select', 'SELECT',
         f"{WRITES_ALL} OR ({KNOWN} AND user_id = app_user_id())", None),
        ('notifications', 'notifications_insert', 'INSERT', None, KNOWN),
        ('notifications', 'notifications_update', 'UPDATE',
         f"{WRITES_ALL} OR ({KNOWN} AND user_id = app_user_id())",
         f"{WRITES_ALL} OR ({KNOWN} AND user_id = app_user_id())"),
        ('notifications', 'notifications_delete', 'DELETE',
         f"{WRITES_ALL} OR ({KNOWN} AND user_id = app_user_id())", None),

        # People. A partner sees himself and the producer's staff (authors of
        # comments, the names in his notifications), never another partner.
        # Anyone may edit his own account but cannot change its role.
        ('users', 'users_select', 'SELECT',
         f"{SEES_ALL} OR ({KNOWN} AND id = app_user_id()) "
         f"OR ({PARTNER} AND role IN ('admin','manager'))", None),
        ('users', 'users_insert', 'INSERT', None, WRITES_ALL),
        ('users', 'users_update', 'UPDATE',
         f"{WRITES_ALL} OR ({KNOWN} AND id = app_user_id())",
         f"{WRITES_ALL} OR ({KNOWN} AND id = app_user_id() AND role = app_role())"),
        ('users', 'users_delete', 'DELETE', WRITES_ALL, None),

        # Answers given at registration belong to that account.
        ('registration_field_values', 'registration_field_values_select', 'SELECT',
         f"{SEES_ALL} OR ({KNOWN} AND user_id = app_user_id())", None),
        ('registration_field_values', 'registration_field_values_insert', 'INSERT', None,
         f"{WRITES_ALL} OR ({KNOWN} AND user_id = app_user_id())"),
        ('registration_field_values', 'registration_field_values_update', 'UPDATE',
         f"{WRITES_ALL} OR ({KNOWN} AND user_id = app_user_id())",
         f"{WRITES_ALL} OR ({KNOWN} AND user_id = app_user_id())"),
        ('registration_field_values', 'registration_field_values_delete', 'DELETE',
         WRITES_ALL, None),

        # The audit trail: every action may append to it, only staff read it
        # (the app narrows reading to the admin) and only staff prune it.
        ('audit_log', 'audit_log_select', 'SELECT', WRITES_ALL, None),
        ('audit_log', 'audit_log_insert', 'INSERT', None, KNOWN),
        ('audit_log', 'audit_log_update', 'UPDATE', WRITES_ALL, WRITES_ALL),
        ('audit_log', 'audit_log_delete', 'DELETE', WRITES_ALL, None),
    ]
    return rows


TABLES = (
    'leads', 'lead_comments', 'lead_field_values', 'lead_workflow_events',
    'lead_workflow_operations', 'commission_payouts', 'bonus_awards',
    'partner_bonus_assignments', 'notifications', 'users',
    'registration_field_values', 'audit_log',
)


def upgrade():
    connection = op.get_bind()
    # The context readers. An unset, empty or malformed setting yields ''
    # and NULL, which no policy accepts.
    connection.exec_driver_sql("""
        CREATE FUNCTION app_role() RETURNS text
        LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT COALESCE(current_setting('app.role', true), '') $$
    """)
    connection.exec_driver_sql("""
        CREATE FUNCTION app_user_id() RETURNS integer
        LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT CASE WHEN current_setting('app.user_id', true) ~ '^[1-9][0-9]{0,8}$'
                          THEN current_setting('app.user_id', true)::integer END $$
    """)
    for table in TABLES:
        connection.exec_driver_sql(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        connection.exec_driver_sql(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
    for table, name, command, using, check in _policies():
        sql = f'CREATE POLICY {name} ON {table} FOR {command}'
        if using:
            sql += f' USING ({using})'
        if check:
            sql += f' WITH CHECK ({check})'
        connection.exec_driver_sql(sql)
    # Policies are per row; a partner or observer may edit his own row, but
    # not the columns that decide what he is and earns. Only staff and the
    # system set those (api/users.py already drops them; this is the floor).
    connection.exec_driver_sql("""
        CREATE FUNCTION users_guard_account_columns() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            -- Like the policies, the migration/operator superuser is exempt.
            IF app_role() NOT IN ('system','admin','manager')
               AND NOT (SELECT rolsuper OR rolbypassrls FROM pg_roles
                        WHERE rolname = current_user) AND (
                   NEW.role IS DISTINCT FROM OLD.role
                OR NEW.tier_id IS DISTINCT FROM OLD.tier_id
                OR NEW.level IS DISTINCT FROM OLD.level
                OR NEW.account_status IS DISTINCT FROM OLD.account_status
                OR NEW.active IS DISTINCT FROM OLD.active
                OR NEW.rejected_at IS DISTINCT FROM OLD.rejected_at
                OR NEW.created_at IS DISTINCT FROM OLD.created_at) THEN
                RAISE EXCEPTION 'account columns are staff-only'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END $$
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER users_guard_account_columns BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION users_guard_account_columns()
    """)


def downgrade():
    connection = op.get_bind()
    connection.exec_driver_sql('DROP TRIGGER IF EXISTS users_guard_account_columns ON users')
    connection.exec_driver_sql('DROP FUNCTION IF EXISTS users_guard_account_columns()')
    for table, name, _command, _using, _check in _policies():
        connection.exec_driver_sql(f'DROP POLICY IF EXISTS {name} ON {table}')
    for table in TABLES:
        connection.exec_driver_sql(f'ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY')
        connection.exec_driver_sql(f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY')
    connection.exec_driver_sql('DROP FUNCTION IF EXISTS app_user_id()')
    connection.exec_driver_sql('DROP FUNCTION IF EXISTS app_role()')
