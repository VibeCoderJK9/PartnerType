"""PostgreSQL domain connection and versioned lead workflow."""
import json
import logging
from contextlib import contextmanager
from datetime import datetime
from api.postgres import Connection, system_context
from api.i18n import t

REJECTED_RETENTION_DAYS = 7
SCHEMA_VERSION = '0020_audit_log_append_only'


def get_conn():
    return Connection()


def check_schema():
    with get_conn() as conn:
        row = conn.execute('SELECT version_num FROM alembic_version').fetchone()
        if not row or row[0] != SCHEMA_VERSION:
            raise RuntimeError('Database migration required')


class LeadWorkflowError(Exception):
    """Base for the workflow core's refusals."""


class LeadNotFoundError(LeadWorkflowError, ValueError):
    """
    The lead does not exist. Handlers map this to 404.

    Also a ValueError, so every existing caller that already catches
    ValueError for "The lead does not exist." keeps behaving exactly as before.
    """


class StaleRevisionError(LeadWorkflowError):
    """
    Somebody else wrote first. Handlers map this to 409 and the UI refetches.

    Carries the current revision so a caller can tell "you are behind" from
    "you never sent one".
    """

    def __init__(self, lead_id, expected, actual):
        super().__init__(
            t("Lead {lead} changed in the meantime (expected version {expected}, "
              "found {actual}).", lead=lead_id, expected=expected, actual=actual)
        )
        self.lead_id = lead_id
        self.expected_revision = expected
        self.actual_revision = actual


class OperationConflictError(LeadWorkflowError):
    code = "operation_conflict"

    def __init__(self):
        super().__init__(t("The operation id has already been used for a different change."))


class LeadWorkflowContext:
    """
    The handle a caller gets inside `lead_workflow_transaction`.

    Deliberately small: change the row, record what happened, and queue the
    effects that must not run until the write is durable.
    """

    def __init__(self, conn, lead_id, row, actor_id, operation_id):
        self.conn = conn
        self.lead_id = lead_id
        self.row = row
        self.actor_id = actor_id
        self.operation_id = operation_id
        self.result = {}
        self.result_builder = None
        self.replayed = False
        self._effects = []
        self._events = []
        self._touched = False

    def update(self, values):
        """
        Write columns on the lead and bump its revision.

        The revision moves once per transaction no matter how many times this
        is called, so a caller that writes contact details and status in two
        steps still produces one version.
        """
        if not values:
            return
        columns = ", ".join(f"{name} = ?" for name in values)
        params = list(values.values()) + [self.lead_id]
        self.conn.execute(f"UPDATE leads SET {columns} WHERE id = ?", params)
        self._touched = True

    def record_event(self, event_type, payload):
        """Append to the lead's immutable history."""
        self._events.append((event_type, payload))
        self.conn.execute(
            """INSERT INTO lead_workflow_events
                      (lead_id, actor_id, event_type, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (self.lead_id, self.actor_id, event_type,
             json.dumps(payload, ensure_ascii=False), datetime.utcnow().isoformat()),
        )
        self._touched = True

    def touch(self):
        """
        Declare that this transaction wrote the lead through raw SQL.

        The revision bump is driven by whether anything changed, and a caller
        doing its own UPDATE has to say so. Callers using update() never need
        this.
        """
        self._touched = True

    def after_commit(self, effect):
        """
        Queue a side effect -- mail, notification, audit -- for after the
        commit. Nothing here runs inside the transaction, so a mail server
        having a bad afternoon can never turn a saved decision into a "not
        saved" message on the producer's screen.
        """
        self._effects.append(effect)


@contextmanager
def lead_workflow_transaction(conn, lead_id, actor_id, expected_revision,
                              operation_id, owns_transaction=True,
                              payload_hash="", authorize=None):
    """
    Run one lead mutation as a single unit: lock, version check, write,
    history, operation record -- then commit and fire the queued effects.

    A failure anywhere inside rolls back all of it together, which is the
    whole reason the state change and its public message share this scope.
    """
    if owns_transaction:
        conn.begin()

    try:
        if operation_id:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                         (f"operation:{actor_id}:{operation_id}",))
        row = conn.execute(
            "SELECT * FROM leads WHERE id = ? FOR UPDATE", (lead_id,)
        ).fetchone()
        if row is None:
            raise LeadNotFoundError(t("Lead {lead} does not exist.", lead=lead_id))

        # Permission is current, even when returning a previously saved result.
        # Do not expose a stale revision or replay to a former owner/reviewer.
        if authorize is not None:
            authorize(row)
        ctx = LeadWorkflowContext(conn, lead_id, row, actor_id, operation_id)
        if operation_id and payload_hash:
            previous = conn.execute(
                """SELECT lead_id, payload_hash, result_json
                   FROM lead_workflow_operations WHERE actor_id=? AND operation_id=?""",
                (actor_id, operation_id),
            ).fetchone()
            if previous is not None:
                if previous["lead_id"] != lead_id or previous["payload_hash"] != payload_hash:
                    raise OperationConflictError()
                ctx.replayed = True
                ctx.result = json.loads(previous["result_json"])

        current = row["revision"] if "revision" in row.keys() else 0
        if not ctx.replayed and expected_revision is not None and int(expected_revision) != int(current):
            raise StaleRevisionError(lead_id, expected_revision, current)

        yield ctx

        if ctx.replayed and (ctx._touched or ctx._effects):
            raise RuntimeError("Replayed operations must not mutate or queue effects.")
        if ctx._touched:
            # Exactly one bump per transaction, guarded by the revision we
            # verified above -- so two connections racing from the same
            # snapshot cannot both land.
            conn.execute(
                "UPDATE leads SET revision = revision + 1 WHERE id = ? AND revision = ?",
                (lead_id, current),
            )
            # Build the exact HTTP outcome while failure can still roll back.
            # Its revision is already the committed revision, not the snapshot.
            if ctx.result_builder is not None:
                ctx.result = ctx.result_builder()
            if operation_id:
                conn.execute(
                    """INSERT INTO lead_workflow_operations
                              (actor_id, operation_id, payload_hash, lead_id,
                               result_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (actor_id, operation_id, payload_hash, lead_id,
                     json.dumps(ctx.result, ensure_ascii=False),
                     datetime.utcnow().isoformat()),
                )

        if owns_transaction:
            conn.commit()
    except Exception:
        if owns_transaction:
            conn.rollback()
        raise

    # Past this line the write is durable. Effects run outside the
    # transaction and are the caller's to make resilient.
    if owns_transaction:
        for effect in ctx._effects:
            try:
                effect()
            except Exception as exc:
                # Never log exception text: it may contain mail/customer data.
                # The durable event remains the evidence of the committed action.
                logging.getLogger(__name__).warning(
                    "Lead workflow effect failed after commit: lead=%s type=%s",
                    lead_id, type(exc).__name__,
                )
        # Blocks hear every committed event last; emit() never raises.
        from api import blocks
        for event_type, _payload in ctx._events:
            blocks.emit(event_type, lead_id, actor_id)



def next_lead_id(conn):
    number = conn.execute("SELECT nextval('lead_number_seq')").fetchone()[0]
    return f"LD-{number:03d}"


@system_context("maintenance: purge rejected partners")
def purge_rejected():
    """Explicit maintenance, separate from schema migration and readiness.

    A rejected partner who already has leads or payouts is kept: those rows
    reference the account without a cascade, so deleting it would fail, and
    they are financial history that must not disappear. The same list of
    references that blocks deleting an account by hand decides it here.
    """
    from datetime import timedelta
    from api.users import _DELETE_BLOCKERS
    history = " ".join(
        f"AND NOT EXISTS (SELECT 1 FROM {table} h WHERE h.{column} = users.id)"
        for table, column, _reason in _DELETE_BLOCKERS
    )
    with get_conn() as conn:
        return conn.execute(f"""DELETE FROM users WHERE role='partner'
            AND account_status='rejected' AND rejected_at IS NOT NULL
            AND rejected_at <= ? {history}""",
            ((datetime.utcnow()-timedelta(days=REJECTED_RETENTION_DAYS)).isoformat(),)).rowcount


@system_context("maintenance: daily sweep")
def run_maintenance():
    """Everything the daily operator command should sweep, in one place.

    Expired sessions and spent recovery links used to be cleared only as a side
    effect of somebody logging in, so an instance with rare logins kept them
    indefinitely -- dead rows that are still credentials-shaped.
    """
    from datetime import timedelta
    now = datetime.utcnow().isoformat()
    # One failing sweep must not stop the others: before this, a single
    # undeletable partner aborted the whole daily run, every day.
    try:
        removed = {'rejected_partners': purge_rejected()}
    except Exception as exc:
        logging.getLogger(__name__).error(
            "maintenance_step_failed step=rejected_partners exception=%s", type(exc).__name__)
        removed = {'rejected_partners': 0}
    with get_conn() as conn:
        removed['expired_sessions'] = conn.execute(
            'DELETE FROM sessions WHERE expires_at < ? OR absolute_expires_at < ?',
            (now, now)).rowcount
        removed['spent_password_resets'] = conn.execute(
            'DELETE FROM password_resets WHERE used_at IS NOT NULL OR expires_at < ?',
            (now,)).rowcount
        removed['old_login_attempts'] = conn.execute(
            "DELETE FROM login_attempts WHERE created_at < now() - INTERVAL '1 day'").rowcount
        removed['expired_invitations'] = conn.execute(
            'DELETE FROM partner_invitations WHERE accepted_at IS NULL AND expires_at < ?',
            ((datetime.utcnow()-timedelta(days=30)).isoformat(),)).rowcount
        # API gateway. TIMESTAMPTZ columns, so the
        # database clock decides; every expiring table stays bounded.
        from api.gateway import calllog, limits
        removed['old_api_call_log'] = conn.execute(
            'DELETE FROM api_call_log WHERE created_at < now() - make_interval(days => ?)',
            (int(calllog.RETENTION_DAYS),)).rowcount
        removed['old_api_rate_windows'] = conn.execute(
            'DELETE FROM api_rate_windows WHERE window_start < now() - make_interval(days => ?)',
            (int(limits.WINDOW_RETENTION_DAYS),)).rowcount
        removed['expired_oauth_requests'] = conn.execute(
            'DELETE FROM oauth_authorize_requests WHERE expires_at < now()').rowcount
        removed['old_oauth_codes'] = conn.execute(
            "DELETE FROM oauth_codes WHERE created_at < now() - INTERVAL '1 day'").rowcount
        removed['expired_oauth_tokens'] = conn.execute(
            "DELETE FROM oauth_tokens WHERE expires_at < now() - INTERVAL '1 day'").rowcount
        # Claude registers a fresh client on every new connection; one that
        # never got a grant is noise after a day.
        removed['unused_oauth_clients'] = conn.execute(
            """DELETE FROM oauth_clients c
               WHERE c.created_at < now() - INTERVAL '1 day'
                 AND NOT EXISTS (SELECT 1 FROM oauth_grants g WHERE g.client_id = c.client_id)"""
        ).rowcount
        removed['old_api_usage'] = conn.execute(
            'DELETE FROM api_usage_daily WHERE day < current_date - CAST(? AS integer)',
            (int(limits.USAGE_RETENTION_DAYS),)).rowcount
    from api import affiliate
    with get_conn() as conn:
        removed['old_affiliate_clicks'] = affiliate.sweep_clicks(conn)
        removed['old_affiliate_early_refunds'] = affiliate.sweep_early_refunds(conn)
    try:
        removed['affiliate_auto_approved'] = affiliate.auto_approve()
    except Exception as exc:
        logging.getLogger(__name__).error(
            "maintenance_step_failed step=affiliate_auto_approve exception=%s", type(exc).__name__)
        removed['affiliate_auto_approved'] = 0
    # Scheduled blocks ride the same daily command (only the enabled ones run).
    from api import blocks
    removed['blocks'] = blocks.run_scheduled()
    return removed
