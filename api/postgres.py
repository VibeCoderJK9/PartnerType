"""Bounded Psycopg pool and narrow DB-API adapter for existing domain modules.

Preserves qmark parameters, named/positional rows, lastrowid and commit/close
contracts. SQL itself is PostgreSQL. No SQLite connection or fallback exists.

Row-level security (migration 0016). Every connection tells PostgreSQL on
whose behalf it runs, through two settings the policies read: `app.role` and
`app.user_id`. They come from `db_context`, a context variable set by the
request pipeline (`user_context`) or, deliberately and narrowly, by code that
runs without a signed-in user (`system_context`). With no context the
settings are empty and every protected table answers with nothing: a code
path that forgot to say who it acts for fails closed, it never sees all rows.
"""
import contextvars
import json
import os
import re
import threading
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from psycopg import IntegrityError
from psycopg.pq import TransactionStatus
from psycopg_pool import ConnectionPool
from api.config import secret

IDENTITY_TABLES = frozenset(json.loads(Path(__file__).with_name('identity_tables.json').read_text()))
# Tables some contexts may append to but not read back: a partner notifies the
# producer, every role writes the audit trail. INSERT ... RETURNING is checked
# against the SELECT policy too, so the adapter must not add RETURNING id here
# (row-level security, migration 0016); lastrowid stays None for them.
WRITE_ONLY_TABLES = frozenset({'notifications', 'audit_log'})
_pools = {}
_lock = threading.Lock()

# ─── ROW-LEVEL SECURITY CONTEXT ─────────────────────────────────────────────

SYSTEM_ROLE = 'system'
_USER_ROLES = frozenset({'admin', 'manager', 'observer', 'partner'})
_NO_CONTEXT = ('', '')
_UNAPPLIED = object()
_db_context = contextvars.ContextVar('partnertype_db_context', default=_NO_CONTEXT)


def current_db_context():
    """The (role, user_id) pair the next statement will run under."""
    return _db_context.get()


@contextmanager
def _use(value):
    token = _db_context.set(value)
    try:
        yield
    finally:
        _db_context.reset(token)


def user_context(user):
    """Run the block as `user`: the database shows only what that role may see.

    A user without a known role or a positive integer id is refused here
    rather than mapped to anything: a typo must not become a wider view.
    """
    role = (user or {}).get('role')
    user_id = (user or {}).get('id')
    if role not in _USER_ROLES:
        raise ValueError('Unknown role for the database context')
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise ValueError('A database user context needs a positive integer id')
    return _use((role, str(user_id)))


def system_context(reason):
    """Run the block with the whole database visible, for code with no user.

    Only for paths that act for the instance itself -- sign-in before the
    account is known, registration, operator commands, scheduled maintenance,
    the duplicate-client check. `reason` is required so every use names why
    it needs the wider view; grep for `system_context(` to audit them.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('system_context needs a reason')
    return _use((SYSTEM_ROLE, ''))


def no_context():
    """Run the block as nobody: protected tables answer with nothing."""
    return _use(_NO_CONTEXT)


class Row(dict):
    def __getitem__(self, key):
        return tuple(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def row_factory(cursor):
    names = [c.name for c in (cursor.description or [])]
    return lambda values: Row(zip(names, (int(v) if isinstance(v, Decimal) and v == v.to_integral_value() else float(v) if isinstance(v, Decimal) else v for v in values)))


def parameters(sql):
    # Replace qmarks only outside SQL literals; escape literal percent signs
    # for Psycopg's parameter protocol (including LIKE patterns).
    tokens = re.split(r"('(?:''|[^'])*')", sql)
    return ''.join(s.replace('%', '%%') if i % 2 else s.replace('%', '%%').replace('?', '%s')
                   for i, s in enumerate(tokens))


def pool():
    dsn = secret('DATABASE_URL')
    if not dsn.startswith(('postgresql://', 'postgres://')):
        raise ValueError('PostgreSQL DATABASE_URL required; no fallback')
    with _lock:
        key = (os.getpid(), dsn)
        if key not in _pools:
            size = int(os.environ.get('PARTNERTYPE_DB_POOL_SIZE', '32'))
            if not 1 <= size <= 32:
                raise ValueError('Pool size must be 1..32')
            _pools[key] = ConnectionPool(dsn, min_size=0, max_size=size, timeout=5,
                max_waiting=32, reset=_reset_context, kwargs={'autocommit': True, 'row_factory': row_factory,
                    'connect_timeout': 5, 'options': '-c timezone=UTC -c statement_timeout=30000 -c lock_timeout=10000'})
        return _pools[key]


def _apply_context(raw, value):
    raw.execute("SELECT set_config('app.role', %s, false), set_config('app.user_id', %s, false)",
                value)


def _reset_context(raw):
    """A pooled connection goes back empty, whoever used it last."""
    _apply_context(raw, _NO_CONTEXT)


def close_pools():
    with _lock:
        for p in _pools.values():
            p.close()
        _pools.clear()


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.raw = connection.raw.cursor()
        self.lastrowid = None

    def execute(self, sql, params=()):
        stripped = sql.lstrip()
        self.connection.sync_context()
        if stripped.upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
            self.connection.begin()
        match = re.match(r'\s*INSERT\s+INTO\s+(\w+)', sql, re.I)
        returning = bool(match and match[1] in IDENTITY_TABLES
                         and match[1].lower() not in WRITE_ONLY_TABLES
                         and 'RETURNING' not in sql.upper())
        if returning:
            sql = sql.rstrip().rstrip(';') + ' RETURNING id'
        self.raw.execute(parameters(sql), params)
        self.lastrowid = None
        if returning:
            row = self.raw.fetchone()
            self.lastrowid = row['id'] if row else None
        return self

    def executemany(self, sql, values):
        for value in values:
            self.execute(sql, value)
        return self

    @property
    def rowcount(self):
        return self.raw.rowcount

    def fetchone(self):
        return self.raw.fetchone()

    def fetchall(self):
        return self.raw.fetchall()

    def __iter__(self):
        return iter(self.raw)

    def close(self):
        self.raw.close()


class Connection:
    def __init__(self):
        self.pool = pool()
        self.raw = self.pool.getconn()
        self.closed = False
        # Whatever the pooled connection carried, this wrapper applies its own
        # context before the first statement.
        self._applied = _UNAPPLIED

    def sync_context(self):
        """Make the session settings match the current db_context.

        Session-level (not SET LOCAL) because statements outside an explicit
        transaction autocommit one by one. Re-checked before every statement,
        so a context entered or left while the connection is open takes effect
        at once. A rollback can undo a setting made inside the transaction, so
        rollback() forgets what was applied and the next statement sets it
        again.
        """
        value = _db_context.get()
        if value != self._applied:
            _apply_context(self.raw, value)
            self._applied = value

    @property
    def in_transaction(self):
        return self.raw.info.transaction_status != TransactionStatus.IDLE

    def begin(self):
        if not self.in_transaction:
            self.sync_context()
            self.raw.execute('BEGIN')

    def cursor(self):
        return Cursor(self)

    def execute(self, sql, params=()):
        return self.cursor().execute(sql, params)

    def executemany(self, sql, values):
        return self.cursor().executemany(sql, values)

    def commit(self):
        # COMMIT of a failed transaction is a rollback, and that reverts any
        # context set inside it: forget what was applied, as rollback() does.
        if self.raw.info.transaction_status == TransactionStatus.INERROR:
            self._applied = _UNAPPLIED
        self.raw.commit()

    def rollback(self):
        self._applied = _UNAPPLIED
        self.raw.rollback()

    def close(self):
        if not self.closed:
            try:
                self._applied = _UNAPPLIED
                self.raw.rollback()
            finally:
                self.pool.putconn(self.raw)
                self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, kind, value, tb):
        try:
            self.rollback() if kind else self.commit()
        finally:
            self.close()
