"""
PartnerType -- Shared pytest fixture surface for the backend test suite.

Every fixture the backend tests share is defined here.
"""
import os
# Block accidental collection-time access to a working instance.
os.environ["PARTNERTYPE_ENV"] = "test"
os.environ["PARTNERTYPE_INSTANCE_ID"] = "test"
os.environ.pop("DATABASE_URL_FILE", None)
os.environ["DATABASE_URL"] = ""

import bcrypt
import pytest

from api.db import get_conn
from api.leads import create_lead
from api.partner_bonuses import assign_bonus


# ─── DATABASE ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def postgres_template():
    from tools.test_database import template
    with template() as name:
        yield name


@pytest.fixture(autouse=True)
def test_db_path(postgres_template, monkeypatch):
    """Every test gets a fresh PostgreSQL database; never the working DB."""
    from tools.test_database import database
    from api.postgres import close_pools
    with database(postgres_template) as (dsn, migration_dsn):
        monkeypatch.delenv('DATABASE_URL_FILE', raising=False)
        monkeypatch.delenv('PARTNERTYPE_SESSION_SECRET_FILE', raising=False)
        monkeypatch.delenv('PARTNERTYPE_SMTP_PASSWORD_FILE', raising=False)
        monkeypatch.delenv('PARTNERTYPE_SMTP_PASSWORD', raising=False)
        monkeypatch.delenv('PARTNERTYPE_SMTP_HOST', raising=False)
        monkeypatch.delenv('PARTNERTYPE_SMTP_PORT', raising=False)
        monkeypatch.delenv('PARTNERTYPE_SMTP_USER', raising=False)
        monkeypatch.delenv('PARTNERTYPE_SMTP_FROM', raising=False)
        monkeypatch.delenv('MIGRATION_DATABASE_URL_FILE', raising=False)
        monkeypatch.delenv('PARTNERTYPE_ALLOWED_ORIGINS', raising=False)
        monkeypatch.setenv('DATABASE_URL', dsn)
        monkeypatch.setenv('MIGRATION_DATABASE_URL', migration_dsn)
        monkeypatch.setenv('PARTNERTYPE_ENV', 'test')
        monkeypatch.setenv('PARTNERTYPE_INSTANCE_ID', 'test')
        monkeypatch.setenv('PARTNERTYPE_INSTANCE_NAME', 'Test producer')
        monkeypatch.setenv('PARTNERTYPE_BASE_URL', 'http://localhost:8000')
        monkeypatch.setenv('PARTNERTYPE_SESSION_SECRET', 'test-secret-' * 4)
        try:
            yield dsn
        finally:
            close_pools()


@pytest.fixture(autouse=True)
def database_context():
    """Fixtures and direct domain calls run with the system database context.

    Production code has no default: a request runs as its signed-in user and
    anything else must name a system context (api/postgres.py). Tests that
    seed rows directly stand in for the operator, so they get the system
    view; requests made through the app still switch to the caller's own
    context in the router, and tests/integration/test_row_level_security.py
    checks the partner and no-context views explicitly.
    """
    from api.postgres import system_context
    with system_context("test fixtures"):
        yield


@pytest.fixture
def db_conn(test_db_path):
    """A real connection into the throwaway test database."""
    conn = get_conn()
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def polish_by_default():
    """Most tests assert the Polish copy; the app's own default is English.

    Code called directly (not through a request) runs in Polish here, as it
    would for a Polish user. api/tests/unit/test_i18n.py checks the real
    default and the negotiation.
    """
    from api.i18n import use_language
    with use_language("pl"):
        yield


# ─── SIDE-EFFECT MUTING ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mute_side_effects(mocker):
    """
    Silence notifications, audit logging and outbound email for every test.

    Patched on the owning module (not the importing module) because callers
    such as api/leads.py bind the module object (`from api import
    notifications as notif`) rather than the function itself, so an attribute
    patch here is visible through that binding.
    """
    mocker.patch("api.notifications.create_notification")
    mocker.patch("api.notifications.notify_all_admins")
    mocker.patch("api.audit.log")
    mocker.patch("api.email_service.send_lead_created")
    mocker.patch("api.email_service.send_lead_status_changed")
    # Work deferred until after the response runs at once in tests, so it
    # never touches a test database that has already been dropped.
    mocker.patch(
        "api.email_service.run_in_background",
        side_effect=lambda task, *args: task(*args),
    )


# ─── USER FACTORIES ─────────────────────────────────────────────────────────

def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


@pytest.fixture
def admin_user(test_db_path):
    """An active admin user, with plaintext credentials attached for login."""
    password = "AdminPass123!"
    email = "admin@test.pl"

    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO users (email, password, name, role, level, active)
               VALUES (?,?,?,?,?,1)""",
            (email, _hash(password), "Test Admin", "admin", "bronze"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    finally:
        conn.close()

    user = dict(row)
    user["plain_password"] = password
    return user


@pytest.fixture
def manager_user(test_db_path):
    """An active manager (channel manager) user, with plaintext credentials attached for login."""
    password = "ManagerPass123!"
    email = "manager@test.pl"

    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO users (email, password, name, role, level, active)
               VALUES (?,?,?,?,?,1)""",
            (email, _hash(password), "Test Manager", "manager", "bronze"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    finally:
        conn.close()

    user = dict(row)
    user["plain_password"] = password
    return user


@pytest.fixture
def partner_user(test_db_path):
    """
    An active gold-level partner, with the gold commission rate and the
    auto-commission toggle seeded explicitly (not relying on app defaults).
    """
    password = "PartnerPass123!"
    email = "partner.gold@test.pl"

    conn = get_conn()
    try:
        tier_id = conn.execute(
            "SELECT id FROM commission_tiers WHERE name = 'Gold'"
        ).fetchone()["id"]
        conn.execute(
            """INSERT INTO users (email, password, name, role, level, tier_id, active)
               VALUES (?,?,?,?,?,?,1)""",
            (email, _hash(password), "Test Gold Partner", "partner", "gold", tier_id),
        )
        conn.execute("INSERT INTO settings (key, value) VALUES ('commission_gold', '7')")
        conn.execute("INSERT INTO settings (key, value) VALUES ('lead_auto_commission', '1')")
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    finally:
        conn.close()

    user = dict(row)
    user["plain_password"] = password
    return user


@pytest.fixture
def partner_user_bronze(test_db_path):
    """A second active partner at bronze level, with the bronze rate seeded."""
    password = "PartnerBronze123!"
    email = "partner.bronze@test.pl"

    conn = get_conn()
    try:
        tier_id = conn.execute(
            "SELECT id FROM commission_tiers WHERE name = 'Bronze'"
        ).fetchone()["id"]
        conn.execute(
            """INSERT INTO users (email, password, name, role, level, tier_id, active)
               VALUES (?,?,?,?,?,?,1)""",
            (email, _hash(password), "Test Bronze Partner", "partner", "bronze", tier_id),
        )
        conn.execute("INSERT INTO settings (key, value) VALUES ('commission_bronze', '5')")
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    finally:
        conn.close()

    user = dict(row)
    user["plain_password"] = password
    return user


@pytest.fixture
def partner_user_tiered(partner_user):
    """An active partner pinned to the seeded 7% Gold tier."""
    assert partner_user["tier_id"] is not None
    return partner_user


@pytest.fixture
def attach_partner_bonus(admin_user):
    """Give a partner a seeded bonus through the production API.

    Two mechanisms, and the fixture routes by type rather than
    hiding the difference: a percent bonus is ASSIGNED (it changes the rate), a
    cash bonus is GRANTED (it is money owed, settled by a payout). Calling
    assign_bonus with a cash definition now raises, which is the point.
    """
    def _attach(partner, bonus_type, value):
        conn = get_conn()
        try:
            bonus = conn.execute(
                "SELECT id FROM bonus_definitions WHERE type=? ORDER BY sort_order LIMIT 1",
                (bonus_type,),
            ).fetchone()
        finally:
            conn.close()
        if bonus_type == "cash":
            from api.bonus_awards import grant
            return grant([partner["id"]], bonus["id"], admin_user, amount=value)
        return assign_bonus(partner["id"], bonus["id"], value, admin_user)

    return _attach


# ─── LEADS ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_lead(partner_user):
    """One real lead of value 10000, created for partner_user via the real path."""
    return create_lead(
        partner_user,
        {
            "client_name": "Test Client",
            "client_email": "client@test.pl",
            "company": "Test Co",
            "value": 10000,
        },
    )


@pytest.fixture
def database_failure():
    """Inject a real PostgreSQL constraint failure with operator credentials."""
    import os
    from psycopg import connect, sql
    def install(name, table, message):
        with connect(os.environ['MIGRATION_DATABASE_URL']) as conn:
            conn.execute(sql.SQL("CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS {} ").format(
                sql.Identifier(name+'_function'),
                sql.Literal("BEGIN RAISE EXCEPTION '%', "+sql.Literal(message).as_string(conn)+" USING ERRCODE='23514'; END;")))
            conn.execute(sql.SQL('CREATE TRIGGER {} BEFORE INSERT ON {} FOR EACH ROW EXECUTE FUNCTION {}()').format(sql.Identifier(name),sql.Identifier(table),sql.Identifier(name+'_function')))
    return install
