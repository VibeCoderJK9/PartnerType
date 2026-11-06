"""Row-level security (migration 0016): the database keeps partners apart.

Every check here runs raw SQL that deliberately OMITS the partner filter --
exactly the mistake the policies exist to absorb. Partner A must not read,
change or delete partner B's rows; a connection with no context must see
nothing at all; and the role/identity a partner runs as must not be
something he can widen by writing to his own row.
"""
import contextvars
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from psycopg import connect, errors

from api.db import get_conn
from api.postgres import (
    current_db_context, no_context, system_context, user_context,
)


RLS_TABLES = (
    "leads", "lead_comments", "lead_field_values", "lead_workflow_events",
    "lead_workflow_operations", "commission_payouts", "bonus_awards",
    "partner_bonus_assignments", "notifications", "users",
    "registration_field_values", "audit_log",
    "affiliate_links", "affiliate_clicks", "affiliate_conversions",
)

# table -> (key column, a text column the update test rewrites)
MUTABLE = {
    "leads": ("id", "notes"),
    "lead_comments": ("id", "body"),
    "lead_field_values": ("id", "value"),
    "lead_workflow_events": ("id", "event_type"),
    "lead_workflow_operations": ("id", "result_json"),
    "commission_payouts": ("id", "note"),
    "bonus_awards": ("id", "note"),
    "partner_bonus_assignments": ("id", "custom_value"),
    "notifications": ("id", "title"),
    "users": ("id", "name"),
    "registration_field_values": ("id", "value"),
    "audit_log": ("id", "detail"),
    "affiliate_links": ("id", "code"),
    "affiliate_clicks": ("id", "click_id"),
    "affiliate_conversions": ("id", "ref"),
}

# Tables every signed-in account reads but only staff write. Nothing in them
# belongs to one partner, so they stay out of the per-partner sweeps above.
SHARED_TABLES = ("knowledge_documents",)


def _one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _seed_partner(conn, partner, tag, field_id, reg_field_id, bonus_def_id):
    """One row per protected table that belongs to `partner`. Returns {table: id}."""
    pid = partner["id"]
    lead_id = f"LD-RLS-{tag}"
    conn.execute(
        """INSERT INTO leads (id, client_name, client_email, company, partner_id, nip)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (lead_id, f"Klient {tag}", f"{tag}@client.test", f"Firma {tag}", pid, f"52600000{tag[-2:]}"),
    )
    ids = {"leads": lead_id, "users": pid}
    ids["lead_comments"] = conn.execute(
        "INSERT INTO lead_comments (lead_id, user_id, body) VALUES (?, ?, ?)",
        (lead_id, pid, f"public {tag}"),
    ).lastrowid
    ids["lead_field_values"] = conn.execute(
        "INSERT INTO lead_field_values (lead_id, field_id, value) VALUES (?, ?, ?)",
        (lead_id, field_id, f"answer {tag}"),
    ).lastrowid
    ids["lead_workflow_events"] = conn.execute(
        """INSERT INTO lead_workflow_events (lead_id, actor_id, event_type, payload_json, created_at)
           VALUES (?, ?, 'lead.created', '{}', '2026-10-31T10:00:00')""",
        (lead_id, pid),
    ).lastrowid
    ids["lead_workflow_operations"] = conn.execute(
        """INSERT INTO lead_workflow_operations
                  (actor_id, operation_id, payload_hash, lead_id, result_json, created_at)
           VALUES (?, ?, 'h', ?, '{}', '2026-10-31T10:00:00')""",
        (pid, f"op-{tag}", lead_id),
    ).lastrowid
    ids["commission_payouts"] = conn.execute(
        """INSERT INTO commission_payouts (partner_id, period_type, period_key, amount, lead_count, paid_at)
           VALUES (?, 'month', '2026-10', 100, 1, '2026-10-31')""",
        (pid,),
    ).lastrowid
    ids["bonus_awards"] = conn.execute(
        "INSERT INTO bonus_awards (partner_id, name, amount, granted_at) VALUES (?, ?, 50, '2026-10-31')",
        (pid, f"bonus {tag}"),
    ).lastrowid
    ids["partner_bonus_assignments"] = conn.execute(
        "INSERT INTO partner_bonus_assignments (partner_id, bonus_def_id, custom_value) VALUES (?, ?, 1)",
        (pid, bonus_def_id),
    ).lastrowid
    ids["notifications"] = conn.execute(
        "INSERT INTO notifications (user_id, title) VALUES (?, ?) RETURNING id", (pid, f"note {tag}"),
    ).fetchone()["id"]
    ids["registration_field_values"] = conn.execute(
        "INSERT INTO registration_field_values (user_id, field_id, value) VALUES (?, ?, ?)",
        (pid, reg_field_id, f"reg {tag}"),
    ).lastrowid
    ids["audit_log"] = conn.execute(
        "INSERT INTO audit_log (user_id, action, detail) VALUES (?, 'lead.create', ?) RETURNING id",
        (pid, f"audit {tag}"),
    ).fetchone()["id"]
    ids["affiliate_links"] = conn.execute(
        "INSERT INTO affiliate_links (partner_id, code) VALUES (?, ?) RETURNING id",
        (pid, f"code{tag.lower()}00"[:10].ljust(10, "0")),
    ).fetchone()["id"]
    ids["affiliate_clicks"] = conn.execute(
        "INSERT INTO affiliate_clicks (click_id, link_id, partner_id) VALUES (?, ?, ?) RETURNING id",
        (f"click-{tag}-0000000000", ids["affiliate_links"], pid),
    ).fetchone()["id"]
    ids["affiliate_conversions"] = conn.execute(
        """INSERT INTO affiliate_conversions
                  (order_id, source, status, amount, currency, occurred_at, ref,
                   partner_id, link_id, body_sha256)
           VALUES (?, 'webhook', 'pending', 100, 'PLN', now(), ?, ?, ?, ?) RETURNING id""",
        (f"order-{tag}", f"ref{tag}", pid, ids["affiliate_links"], "0" * 64),
    ).fetchone()["id"]
    return ids


@pytest.fixture
def observer_user(test_db_path):
    with system_context("test seed"), get_conn() as conn:
        conn.execute(
            "INSERT INTO users (email, password, name, role, level, active, phone, bio) "
            "VALUES ('observer.rls@example.test', 'not-a-real-hash', 'Observer', 'observer', "
            "'bronze', 1, '+48 000 000 999', 'private note')")
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE email = 'observer.rls@example.test'").fetchone()
    return dict(row)


@pytest.fixture
def two_partners(partner_user, partner_user_bronze, admin_user):
    """Partner A and partner B, each owning one row in every protected table."""
    with system_context("test seed"), get_conn() as conn:
        field_id = conn.execute(
            "INSERT INTO lead_fields (label) VALUES ('RLS pytanie')").lastrowid
        reg_field_id = conn.execute(
            "INSERT INTO registration_fields (label) VALUES ('RLS rejestracja')").lastrowid
        bonus_def_id = _one(conn, "SELECT id FROM bonus_definitions ORDER BY id LIMIT 1")
        a = _seed_partner(conn, partner_user, "A01", field_id, reg_field_id, bonus_def_id)
        b = _seed_partner(conn, partner_user_bronze, "B02", field_id, reg_field_id, bonus_def_id)
        # Internal staff note on A's OWN lead: still never his to read.
        a["internal_comment"] = conn.execute(
            "INSERT INTO lead_comments (lead_id, user_id, body, is_internal) VALUES (?, ?, 'internal', 1)",
            (a["leads"], admin_user["id"]),
        ).lastrowid
    return partner_user, partner_user_bronze, a, b


def _visible_ids(table, user=None):
    key = MUTABLE[table][0]
    scope = user_context(user) if user else no_context()
    with scope, get_conn() as conn:
        return {row[0] for row in conn.execute(f"SELECT {key} FROM {table}").fetchall()}


@pytest.mark.parametrize("table", RLS_TABLES)
def test_partner_cannot_read_another_partners_rows(two_partners, table):
    partner_a, _b, a, b = two_partners
    visible = _visible_ids(table, partner_a)
    assert b[table] not in visible
    if table == "audit_log":
        # The trail is staff-only, his own entries included.
        assert visible == set()
    else:
        assert a[table] in visible


@pytest.mark.parametrize("table", RLS_TABLES)
def test_partner_cannot_update_another_partners_rows(two_partners, table):
    partner_a, _b, _a, b = two_partners
    key, column = MUTABLE[table]
    value = 9 if column == "custom_value" else "overwritten"
    with user_context(partner_a), get_conn() as conn:
        changed = conn.execute(
            f"UPDATE {table} SET {column} = ? WHERE {key} = ?", (value, b[table])).rowcount
    assert changed == 0
    with get_conn() as conn:
        assert _one(conn, f"SELECT {column} FROM {table} WHERE {key} = ?", (b[table],)) != value


@pytest.mark.parametrize("table", RLS_TABLES)
def test_partner_cannot_delete_another_partners_rows(two_partners, table):
    partner_a, _b, _a, b = two_partners
    key = MUTABLE[table][0]
    with user_context(partner_a), get_conn() as conn:
        removed = conn.execute(f"DELETE FROM {table} WHERE {key} = ?", (b[table],)).rowcount
    assert removed == 0
    with get_conn() as conn:
        assert _one(conn, f"SELECT count(*) FROM {table} WHERE {key} = ?", (b[table],)) == 1


@pytest.mark.parametrize("table", RLS_TABLES)
def test_missing_context_sees_nothing(two_partners, table):
    assert _visible_ids(table) == set()
    key, column = MUTABLE[table]
    _pa, _pb, a, _b = two_partners
    with no_context(), get_conn() as conn:
        assert conn.execute(f"UPDATE {table} SET {column} = {column} WHERE {key} = ?",
                            (a[table],)).rowcount == 0
        assert conn.execute(f"DELETE FROM {table} WHERE {key} = ?", (a[table],)).rowcount == 0


def test_missing_context_cannot_insert_a_lead(two_partners):
    partner_a, *_ = two_partners
    with no_context(), get_conn() as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(
            "INSERT INTO leads (id, client_name, client_email, company, partner_id) VALUES ('LD-X', 'x', 'x@x.pl', 'x', ?)",
            (partner_a["id"],))


def test_partner_never_reads_an_internal_comment_on_his_own_lead(two_partners):
    partner_a, _b, a, _ = two_partners
    visible = _visible_ids("lead_comments", partner_a)
    assert a["lead_comments"] in visible
    assert a["internal_comment"] not in visible


def test_partner_cannot_write_an_internal_comment_or_comment_on_another_lead(two_partners):
    partner_a, _pb, a, b = two_partners
    with user_context(partner_a), get_conn() as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("INSERT INTO lead_comments (lead_id, user_id, body, is_internal) VALUES (?, ?, 'x', 1)",
                         (a["leads"], partner_a["id"]))
        conn.rollback()
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("INSERT INTO lead_comments (lead_id, user_id, body) VALUES (?, ?, 'x')",
                         (b["leads"], partner_a["id"]))


def test_partner_cannot_create_a_lead_in_another_partners_name(two_partners):
    partner_a, partner_b, *_ = two_partners
    with user_context(partner_a), get_conn() as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(
            "INSERT INTO leads (id, client_name, client_email, company, partner_id) VALUES ('LD-Y', 'y', 'y@y.pl', 'y', ?)",
            (partner_b["id"],))


def test_partner_cannot_move_his_lead_to_another_partner(two_partners):
    partner_a, partner_b, a, _ = two_partners
    with user_context(partner_a), get_conn() as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute("UPDATE leads SET partner_id = ? WHERE id = ?", (partner_b["id"], a["leads"]))


def test_partner_sees_only_himself_in_users(two_partners, admin_user, manager_user):
    partner_a, partner_b, *_ = two_partners
    assert _visible_ids("users", partner_a) == {partner_a["id"]}


# Private columns of a staff account a partner must never read (migration 0017).
STAFF_PRIVATE_COLUMNS = ("password", "phone", "bio", "company", "tier_id", "level",
                         "account_status", "active", "created_at")


@pytest.mark.parametrize("column", STAFF_PRIVATE_COLUMNS)
def test_partner_cannot_read_a_private_staff_column_even_with_raw_sql(
        two_partners, admin_user, manager_user, observer_user, column):
    partner_a, *_ = two_partners
    staff = [admin_user["id"], manager_user["id"], observer_user["id"]]
    with user_context(partner_a), get_conn() as conn:
        rows = conn.execute(
            f"SELECT {column} FROM users WHERE id = ANY(?)", (staff,)).fetchall()
        assert rows == []
        # Not through a join either: the policy filters every reference.
        joined = conn.execute(
            f"SELECT u.{column} FROM leads l JOIN users u ON u.role = 'admin'").fetchall()
        assert joined == []


def test_partner_cannot_read_second_factor_data_of_staff(two_partners, admin_user):
    partner_a, *_ = two_partners
    with user_context(partner_a), get_conn() as conn:
        mfa_columns = [row[0] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'users' AND column_name LIKE '%mfa%'").fetchall()]
        for column in mfa_columns:
            assert conn.execute(f"SELECT {column} FROM users WHERE id = ?",
                                (admin_user["id"],)).fetchall() == []


def test_partner_reads_staff_through_the_directory_and_its_four_columns_only(
        two_partners, admin_user, manager_user, observer_user):
    partner_a, partner_b, *_ = two_partners
    with user_context(partner_a), get_conn() as conn:
        cursor = conn.execute("SELECT * FROM staff_directory ORDER BY id")
        rows = cursor.fetchall()
        assert [column.name for column in cursor.raw.description] == ["id", "name", "email", "role"]
        assert {row["id"] for row in rows} == {admin_user["id"], manager_user["id"], observer_user["id"]}
        assert partner_a["id"] not in {row["id"] for row in rows}
        assert partner_b["id"] not in {row["id"] for row in rows}
        with pytest.raises(errors.UndefinedColumn):
            conn.execute("SELECT password FROM staff_directory")


def test_the_staff_directory_is_empty_without_a_context(admin_user):
    with no_context(), get_conn() as conn:
        assert conn.execute("SELECT count(*) FROM staff_directory").fetchone()[0] == 0


@pytest.mark.parametrize("statement", [
    "UPDATE staff_directory SET email = 'attacker@example.test'",
    "DELETE FROM staff_directory",
    "INSERT INTO staff_directory (id, name, email, role) VALUES (999, 'x', 'x@example.test', 'admin')",
])
def test_the_staff_directory_cannot_be_written_through(two_partners, admin_user, statement):
    """The view runs with its owner's rights; a write through it would get around RLS."""
    partner_a, *_ = two_partners
    with user_context(partner_a), get_conn() as conn, pytest.raises(
            (errors.InsufficientPrivilege, errors.ObjectNotInPrerequisiteState, errors.FeatureNotSupported)):
        conn.execute(statement)
    with system_context("test: check"), get_conn() as conn:
        assert conn.execute("SELECT email FROM users WHERE id = ?",
                            (admin_user["id"],)).fetchone()[0] != "attacker@example.test"


def test_partner_cannot_change_his_own_role(two_partners):
    partner_a, *_ = two_partners
    with user_context(partner_a), get_conn() as conn:
        assert conn.execute("UPDATE users SET phone = '123' WHERE id = ?", (partner_a["id"],)).rowcount == 1
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (partner_a["id"],))


@pytest.mark.parametrize("assignment", [
    "tier_id = NULL", "level = 'platinum'", "account_status = 'suspended'",
    "active = 0", "rejected_at = '2026-01-01'",
])
def test_partner_cannot_change_his_own_account_columns(two_partners, assignment):
    partner_a, *_ = two_partners
    with user_context(partner_a), get_conn() as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(f"UPDATE users SET {assignment} WHERE id = ?", (partner_a["id"],))


def test_staff_still_change_account_columns(two_partners, admin_user):
    _pa, partner_b, *_ = two_partners
    with user_context(admin_user), get_conn() as conn:
        assert conn.execute("UPDATE users SET level = 'silver' WHERE id = ?",
                            (partner_b["id"],)).rowcount == 1


def test_staff_and_observer_see_every_partner(two_partners, admin_user, manager_user, test_db_path):
    _pa, _pb, a, b = two_partners
    for staff in (admin_user, manager_user):
        assert {a["leads"], b["leads"]} <= _visible_ids("leads", staff)
        assert {a["commission_payouts"], b["commission_payouts"]} <= _visible_ids("commission_payouts", staff)
    with get_conn() as conn:
        observer_id = conn.execute(
            "INSERT INTO users (email, password, name, role) VALUES ('obs@test.pl', 'x', 'Obs', 'observer')"
        ).lastrowid
    observer = {"id": observer_id, "role": "observer"}
    assert {a["leads"], b["leads"]} <= _visible_ids("leads", observer)
    # Read-only and leads only: no money, and no writing to a lead.
    assert _visible_ids("commission_payouts", observer) == set()
    assert _visible_ids("affiliate_conversions", observer) == set()
    with user_context(observer), get_conn() as conn:
        assert conn.execute("UPDATE leads SET notes = 'x' WHERE id = ?", (a["leads"],)).rowcount == 0


def test_context_switches_take_effect_on_an_open_connection(two_partners):
    partner_a, _pb, a, b = two_partners
    with user_context(partner_a), get_conn() as conn:
        conn.begin()
        assert _one(conn, "SELECT count(*) FROM leads") == 1
        with system_context("test: narrow lookup"):
            assert _one(conn, "SELECT count(*) FROM leads") >= 2
        assert _one(conn, "SELECT count(*) FROM leads") == 1
        conn.rollback()
        # The rollback undid the settings made inside it; the next statement
        # must set the partner context again rather than trust a stale cache.
        assert _one(conn, "SELECT count(*) FROM leads") == 1


def test_commit_of_a_failed_transaction_does_not_leave_a_stale_context(two_partners):
    partner_a, _pb, _a, _b = two_partners
    with system_context("test: begin wide"), get_conn() as conn:
        conn.begin()
        with user_context(partner_a):
            assert _one(conn, "SELECT count(*) FROM leads") == 1
            with pytest.raises(errors.DivisionByZero):
                conn.execute("SELECT 1/0")
            # COMMIT of a failed transaction rolls back, and with it the
            # partner context set inside it. The next statement must set the
            # partner context again, not trust the cache and run as system.
            conn.commit()
            assert _one(conn, "SELECT count(*) FROM leads") == 1


def test_pooled_connection_does_not_carry_the_previous_context(two_partners):
    with system_context("test: privileged first user"), get_conn() as conn:
        assert _one(conn, "SELECT count(*) FROM leads") >= 2
    with no_context(), get_conn() as conn:
        assert _one(conn, "SELECT count(*) FROM leads") == 0
        assert conn.execute("SELECT current_setting('app.role', true)").fetchone()[0] == ""


def test_new_threads_start_without_a_context(two_partners):
    with ThreadPoolExecutor(1) as pool:
        assert pool.submit(current_db_context).result() == ("", "")
        assert pool.submit(contextvars.copy_context().run, current_db_context).result()[0] == "system"


def test_background_mail_task_keeps_the_request_context(two_partners, mocker):
    partner_a, *_ = two_partners
    mocker.stopall()  # the real run_in_background, not the synchronous test stand-in
    from api import email_service
    seen = []
    with user_context(partner_a):
        email_service.run_in_background(lambda: seen.append(current_db_context())).result(timeout=5)
    assert seen == [("partner", str(partner_a["id"]))]


def test_user_context_refuses_unknown_roles_and_ids():
    for bad in ({"role": "system", "id": 1}, {"role": "owner", "id": 1}, {"role": "partner", "id": 0},
                {"role": "partner", "id": "1"}, {"role": "partner"}, None):
        with pytest.raises(ValueError):
            user_context(bad)
    with pytest.raises(ValueError):
        system_context("")


def test_malformed_user_id_setting_matches_nothing(two_partners):
    with no_context(), get_conn() as conn:
        conn.execute("SELECT set_config('app.role', 'partner', false), set_config('app.user_id', '1 OR 1=1', false)")
        assert conn.execute("SELECT app_user_id()").fetchone()[0] is None
        assert conn.raw.execute("SELECT count(*) AS n FROM leads").fetchone()["n"] == 0


def test_every_protected_table_forces_row_level_security(test_db_path):
    with connect(os.environ["MIGRATION_DATABASE_URL"]) as conn:
        rows = dict(conn.execute(
            "SELECT relname, relrowsecurity AND relforcerowsecurity FROM pg_class "
            "WHERE relname = ANY(%s) AND relkind = 'r'", (list(RLS_TABLES + SHARED_TABLES),)).fetchall())
    assert rows == {table: True for table in RLS_TABLES + SHARED_TABLES}


def test_application_role_cannot_bypass_row_level_security(test_db_path):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()
    assert (row["rolsuper"], row["rolbypassrls"]) == (False, False)


def test_partner_http_requests_run_in_his_context(two_partners, mocker, make_handler):
    """The router hands the handler the signed-in partner's context."""
    from api import router
    partner_a, _pb, a, b = two_partners
    seen = {}

    def spy(handler, user, qs):
        seen["context"] = current_db_context()
        with get_conn() as conn:
            seen["leads"] = {r[0] for r in conn.execute("SELECT id FROM leads").fetchall()}
        return 200, {}

    mocker.patch.object(router, "leads_list", spy)
    mocker.patch.object(router, "get_session_user", return_value=dict(partner_a))
    mocker.patch.object(router, "mfa_is_required_for", return_value=False)
    router.dispatch(make_handler("GET", "/api/leads", token="x"), "GET", "/api/leads")
    assert seen["context"] == ("partner", str(partner_a["id"]))
    assert seen["leads"] == {a["leads"]}


def test_downgrade_removes_the_policies_and_upgrade_restores_them(test_db_path):
    from alembic import command
    from alembic.config import Config as AlembicConfig

    def forced():
        with connect(os.environ["MIGRATION_DATABASE_URL"]) as conn:
            return conn.execute(
                "SELECT count(*) FROM pg_class WHERE relname = ANY(%s) AND relrowsecurity",
                (list(RLS_TABLES + SHARED_TABLES),)).fetchone()[0]

    config = AlembicConfig("alembic.ini")
    command.downgrade(config, "0015_oauth_grant_redirect")
    assert forced() == 0
    command.upgrade(config, "head")
    assert forced() == len(RLS_TABLES + SHARED_TABLES)


@pytest.mark.parametrize("role", ["partner", "observer"])
def test_write_only_tables_accept_appends_they_cannot_read(two_partners, admin_user, mocker, role):
    """A partner's lead notifies the producer and every action is audited,
    though neither role may read those rows back (no RETURNING id for them)."""
    partner_a, *_ = two_partners
    mocker.stopall()  # the real audit.log and notify_all_admins
    from api import audit, notifications
    if role == "partner":
        actor = partner_a
    else:
        with get_conn() as conn:
            actor_id = conn.execute(
                "INSERT INTO users (email, password, name, role) VALUES ('obs2@test.pl', 'x', 'Obs', 'observer')"
            ).lastrowid
        actor = {"id": actor_id, "role": "observer", "name": "Obs"}

    def counts():
        with get_conn() as conn:
            return (_one(conn, "SELECT count(*) FROM audit_log"),
                    _one(conn, "SELECT count(*) FROM notifications WHERE user_id = ?", (admin_user["id"],)))

    before = counts()
    with user_context(actor):
        audit.log(actor, "lead.view", "lead", "LD-RLS-A01", "rls")
        notifications.notify_all_admins("info", "Nowy lead", "rls")
    assert counts() == (before[0] + 1, before[1] + 1)


def test_a_partner_can_notify_active_admins_without_seeing_them(two_partners, admin_user):
    partner_a, *_ = two_partners
    with system_context("test seed"), get_conn() as conn:
        conn.execute(
            "INSERT INTO users (email, password, name, role, level, active) "
            "VALUES ('gone.admin@example.test', 'x', 'Gone', 'admin', 'bronze', 0)")
        conn.commit()
    with user_context(partner_a), get_conn() as conn:
        ids = {row[0] for row in conn.execute("SELECT id FROM active_staff() WHERE role = 'admin'").fetchall()}
    assert admin_user["id"] in ids
    with no_context(), get_conn() as conn:
        assert conn.execute("SELECT count(*) FROM active_staff()").fetchone()[0] == 0
    with system_context("test: check"), get_conn() as conn:
        gone = conn.execute("SELECT id FROM users WHERE email = 'gone.admin@example.test'").fetchone()[0]
    assert gone not in ids


def test_affiliate_secrets_are_for_the_instance_and_the_admin_only(
        two_partners, admin_user, manager_user, observer_user):
    partner_a, _b, _a, _bb = two_partners
    with get_conn() as conn:
        conn.execute("INSERT INTO affiliate_secrets (kind, ciphertext, fingerprint) "
                     "VALUES ('webhook', 'cipher', 'ptw_abc')")
    for user in (partner_a, manager_user, observer_user):
        with user_context(user), get_conn() as conn:
            assert _one(conn, "SELECT count(*) FROM affiliate_secrets") == 0
            assert conn.execute("UPDATE affiliate_secrets SET ciphertext = 'x'").rowcount == 0
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute("INSERT INTO affiliate_secrets (kind, ciphertext, fingerprint) "
                             "VALUES ('stripe', 'x', 'x')")
    with user_context(admin_user), get_conn() as conn:
        assert _one(conn, "SELECT count(*) FROM affiliate_secrets") == 1
    with no_context(), get_conn() as conn:
        assert _one(conn, "SELECT count(*) FROM affiliate_secrets") == 0


def test_partner_cannot_count_clicks_or_report_orders_himself(two_partners):
    partner_a, _b, a, _bb = two_partners
    with user_context(partner_a), get_conn() as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO affiliate_clicks (click_id, link_id, partner_id) VALUES (?, ?, ?)",
                ("click-forged-000000000", a["affiliate_links"], partner_a["id"]))
    with user_context(partner_a), get_conn() as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                """INSERT INTO affiliate_conversions
                          (order_id, source, status, amount, currency, occurred_at,
                           partner_id, body_sha256)
                   VALUES ('forged', 'webhook', 'approved', 1, 'PLN', now(), ?, ?)""",
                (partner_a["id"], "0" * 64))
    with user_context(partner_a), get_conn() as conn:
        changed = conn.execute(
            "UPDATE affiliate_conversions SET status = 'approved' WHERE id = ?",
            (a["affiliate_conversions"],)).rowcount
    assert changed == 0


def test_held_refunds_are_for_the_instance_only(two_partners, admin_user, manager_user):
    partner_a, *_ = two_partners
    with get_conn() as conn:
        conn.execute("INSERT INTO affiliate_early_refunds (source, ref) VALUES ('webhook', 'A-1')")
    for user in (partner_a, manager_user, admin_user):
        with user_context(user), get_conn() as conn:
            assert _one(conn, "SELECT count(*) FROM affiliate_early_refunds") == 0
            assert conn.execute("DELETE FROM affiliate_early_refunds").rowcount == 0
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute("INSERT INTO affiliate_early_refunds (source, ref) "
                             "VALUES ('webhook', 'A-2')")
    with no_context(), get_conn() as conn:
        assert _one(conn, "SELECT count(*) FROM affiliate_early_refunds") == 0
    with get_conn() as conn:
        assert _one(conn, "SELECT count(*) FROM affiliate_early_refunds") == 1


@pytest.mark.parametrize("assignment", ["code = 'rewritten0'", "revoked_at = NULL"])
def test_partner_cannot_rewrite_or_revive_his_own_link(two_partners, assignment):
    partner_a, _b, a, _bb = two_partners
    with get_conn() as conn:
        conn.execute("UPDATE affiliate_links SET revoked_at = now() WHERE id = ?",
                     (a["affiliate_links"],))
    with user_context(partner_a), get_conn() as conn:
        changed = conn.execute(f"UPDATE affiliate_links SET {assignment} WHERE id = ?",
                               (a["affiliate_links"],)).rowcount
    assert changed == 0
    with get_conn() as conn:
        row = conn.execute("SELECT code, revoked_at FROM affiliate_links WHERE id = ?",
                           (a["affiliate_links"],)).fetchone()
    assert row["code"] != "rewritten0" and row["revoked_at"] is not None


def _knowledge_document(conn, title="Price list"):
    return conn.execute(
        """INSERT INTO knowledge_documents (title, filename, kind, size_bytes, sha256, content)
           VALUES (?, 'price-list.pdf', 'pdf', 5, ?, ?)""",
        (title, "0" * 64, b"%PDF-")).lastrowid


def test_every_signed_in_role_reads_the_knowledge_base(
        two_partners, admin_user, manager_user, observer_user):
    partner_a, partner_b, *_ = two_partners
    with get_conn() as conn:
        document = _knowledge_document(conn)
    for user in (partner_a, partner_b, observer_user, manager_user, admin_user):
        with user_context(user), get_conn() as conn:
            ids = {row[0] for row in conn.execute("SELECT id FROM knowledge_documents").fetchall()}
        assert ids == {document}, user["role"]
    with no_context(), get_conn() as conn:
        assert _one(conn, "SELECT count(*) FROM knowledge_documents") == 0


@pytest.mark.parametrize("reader", ["partner", "observer", None])
def test_only_staff_write_the_knowledge_base(two_partners, observer_user, reader):
    partner_a, *_ = two_partners
    with get_conn() as conn:
        document = _knowledge_document(conn)
    scope = {"partner": lambda: user_context(partner_a),
             "observer": lambda: user_context(observer_user),
             None: no_context}[reader]
    with scope(), get_conn() as conn, pytest.raises(errors.InsufficientPrivilege):
        _knowledge_document(conn, title="Forged")
    with scope(), get_conn() as conn:
        assert conn.execute("UPDATE knowledge_documents SET title = 'Changed' WHERE id = ?",
                            (document,)).rowcount == 0
        assert conn.execute("DELETE FROM knowledge_documents WHERE id = ?", (document,)).rowcount == 0
    with get_conn() as conn:
        rows = conn.execute("SELECT title FROM knowledge_documents").fetchall()
    assert [row["title"] for row in rows] == ["Price list"]


@pytest.mark.parametrize("staff", ["admin", "manager"])
def test_staff_add_change_and_remove_knowledge_documents(admin_user, manager_user, staff):
    user = admin_user if staff == "admin" else manager_user
    with user_context(user), get_conn() as conn:
        document = _knowledge_document(conn)
        assert conn.execute("UPDATE knowledge_documents SET title = 'Price list 2027' WHERE id = ?",
                            (document,)).rowcount == 1
    with user_context(user), get_conn() as conn:
        assert _one(conn, "SELECT title FROM knowledge_documents WHERE id = ?", (document,)) == "Price list 2027"
        assert conn.execute("DELETE FROM knowledge_documents WHERE id = ?", (document,)).rowcount == 1
    with get_conn() as conn:
        assert _one(conn, "SELECT count(*) FROM knowledge_documents") == 0


# ─── the audit log is append-only (migration 0020) ─────────────────────────

@pytest.mark.parametrize("staff", ["admin_user", "manager_user"])
def test_staff_cannot_rewrite_or_erase_the_audit_log(two_partners, request, staff):
    user = request.getfixturevalue(staff)
    _pa, _pb, a, _b = two_partners
    entry = a["audit_log"]
    with user_context(user), get_conn() as conn:
        assert conn.execute("UPDATE audit_log SET detail = 'rewritten' WHERE id = ?",
                            (entry,)).rowcount == 0
        assert conn.execute("DELETE FROM audit_log WHERE id = ?", (entry,)).rowcount == 0
    with get_conn() as conn:
        assert _one(conn, "SELECT detail FROM audit_log WHERE id = ?", (entry,)) != "rewritten"


def test_only_the_instance_itself_prunes_the_audit_log(two_partners):
    _pa, _pb, a, _b = two_partners
    entry = a["audit_log"]
    with system_context("retention test"), get_conn() as conn:
        assert conn.execute("UPDATE audit_log SET detail = 'rewritten' WHERE id = ?",
                            (entry,)).rowcount == 0
        assert conn.execute("DELETE FROM audit_log WHERE id = ?", (entry,)).rowcount == 1
