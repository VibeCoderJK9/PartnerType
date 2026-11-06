"""
PartnerType -- foundation for the versioned lead workflow.

Three things are pinned here, and nothing else. This task deliberately adds
no endpoint and changes no API contract; the workflow tests build on what
this file proves.

  1. The migration is ADDITIVE and survives the two leads-table rebuilds that
     already live in migrate_db(). This is not a formality: `api/db.py`
     rebuilds `leads` twice from a hard-coded column list and drops the old
     table, so an ALTER placed before either rebuild is silently erased and
     re-added with DEFAULT 0 -- resetting every lead's revision and breaking
     compare-and-swap with no error anywhere. The existence of the column
     proves nothing; only a preserved non-zero value does.

  2. Compare-and-swap actually arbitrates between two real connections. A
     stale writer loses, and loses without leaving a partial change behind.

  3. A failure before commit rolls back the state change, its workflow event
     and its operation record together -- the caller owns the transaction, so
     the whole unit lives or dies as one.
"""
from psycopg import IntegrityError

import pytest

import api.db as db
from api.db import get_conn


def test_operation_id_is_unique_per_actor(test_db_path):
    """Two different actors may reuse a key; one actor may not."""
    db.check_schema()

    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO lead_workflow_operations
                      (actor_id, operation_id, payload_hash, lead_id, result_json, created_at)
               VALUES (1, 'op-1', 'h', 'LD-001', '{}', '2026-09-08T10:00:00')"""
        )
        conn.execute(
            """INSERT INTO lead_workflow_operations
                      (actor_id, operation_id, payload_hash, lead_id, result_json, created_at)
               VALUES (2, 'op-1', 'h', 'LD-001', '{}', '2026-09-08T10:00:00')"""
        )
        conn.commit()

        with pytest.raises(IntegrityError):
            conn.execute(
                """INSERT INTO lead_workflow_operations
                          (actor_id, operation_id, payload_hash, lead_id, result_json, created_at)
                   VALUES (1, 'op-1', 'h2', 'LD-002', '{}', '2026-09-08T10:00:00')"""
            )
    finally:
        conn.close()


# ─── 2. COMPARE-AND-SWAP ────────────────────────────────────────────────────

@pytest.fixture
def lead_for_cas(test_db_path):
    """One accepted lead owned by one partner, revision 0."""
    db.check_schema()
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO users (email, password, name, role)
                    VALUES ('cas@partner.pl', 'x', 'CAS Partner', 'partner')"""
        )
        partner_id = conn.execute(
            "SELECT id FROM users WHERE email = 'cas@partner.pl'"
        ).fetchone()["id"]
        conn.execute(
            """INSERT INTO leads (id, client_name, client_email, client_phone, company,
                                  value, notes, sector, nip, website, product, quantity,
                                  queue_position, partner_id, created_at, updated_at,
                                  registration_status)
                    VALUES ('LD-CAS', 'Klient CAS', 'cas@example.pl', '+48 600 100 100',
                            'CAS sp. z o.o.', 10000, 'notatka', 'IT', '1234567890', '',
                            'Produkt', 1, 1, ?, '2026-09-08', '2026-09-08', 'accepted')""",
            (partner_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return "LD-CAS"


def test_stale_writer_loses_and_leaves_no_partial_change(lead_for_cas):
    """
    Two real connections read the same snapshot; both try to write. Exactly
    one succeeds, and the loser changes nothing at all.
    """
    from api.db import lead_workflow_transaction, StaleRevisionError

    winner = get_conn()
    loser = get_conn()
    try:
        snapshot = winner.execute(
            "SELECT revision FROM leads WHERE id = ?", (lead_for_cas,)
        ).fetchone()["revision"]

        with lead_workflow_transaction(
            winner, lead_for_cas, actor_id=1,
            expected_revision=snapshot, operation_id="op-winner",
        ) as ctx:
            ctx.update({"notes": "zapis zwycięzcy"})
            ctx.record_event("test.win", {})

        with pytest.raises(StaleRevisionError):
            with lead_workflow_transaction(
                loser, lead_for_cas, actor_id=1,
                expected_revision=snapshot, operation_id="op-loser",
            ) as ctx:
                ctx.update({"notes": "zapis przegranego"})
                ctx.record_event("test.lose", {})

        row = winner.execute(
            "SELECT notes, revision FROM leads WHERE id = ?", (lead_for_cas,)
        ).fetchone()
        assert row["notes"] == "zapis zwycięzcy"
        assert row["revision"] == snapshot + 1, "the loser must not bump the revision"

        events = winner.execute(
            "SELECT event_type FROM lead_workflow_events WHERE lead_id = ?",
            (lead_for_cas,),
        ).fetchall()
        assert [e["event_type"] for e in events] == ["test.win"]

        ops = winner.execute(
            "SELECT operation_id FROM lead_workflow_operations WHERE lead_id = ?",
            (lead_for_cas,),
        ).fetchall()
        assert [o["operation_id"] for o in ops] == ["op-winner"]
    finally:
        winner.close()
        loser.close()


def test_revision_increments_once_per_committed_write(lead_for_cas):
    from api.db import lead_workflow_transaction

    conn = get_conn()
    try:
        for i, expected in enumerate([0, 1, 2]):
            with lead_workflow_transaction(
                conn, lead_for_cas, actor_id=1,
                expected_revision=expected, operation_id=f"op-{i}",
            ) as ctx:
                ctx.update({"notes": f"zapis {i}"})
            row = conn.execute(
                "SELECT revision FROM leads WHERE id = ?", (lead_for_cas,)
            ).fetchone()
            assert row["revision"] == expected + 1
    finally:
        conn.close()


def test_missing_lead_is_reported_without_opening_a_write(test_db_path):
    from api.db import lead_workflow_transaction, LeadNotFoundError

    db.check_schema()
    conn = get_conn()
    try:
        with pytest.raises(LeadNotFoundError):
            with lead_workflow_transaction(
                conn, "LD-NOPE", actor_id=1,
                expected_revision=0, operation_id="op-x",
            ):
                pass
    finally:
        conn.close()


# ─── 3. ROLLBACK ────────────────────────────────────────────────────────────

def test_failure_before_commit_rolls_back_state_event_and_operation_together(lead_for_cas):
    """
    The caller owns the transaction, so a fault anywhere inside it takes the
    whole unit with it -- not just the row that happened to fail.
    """
    from api.db import lead_workflow_transaction

    conn = get_conn()
    try:
        before = conn.execute(
            "SELECT notes, revision FROM leads WHERE id = ?", (lead_for_cas,)
        ).fetchone()

        with pytest.raises(RuntimeError):
            with lead_workflow_transaction(
                conn, lead_for_cas, actor_id=1,
                expected_revision=before["revision"], operation_id="op-boom",
            ) as ctx:
                ctx.update({"notes": "tego nie powinno być widać"})
                ctx.record_event("test.boom", {})
                raise RuntimeError("wstrzyknięta awaria przed commit")

        after = conn.execute(
            "SELECT notes, revision FROM leads WHERE id = ?", (lead_for_cas,)
        ).fetchone()
        assert after["notes"] == before["notes"]
        assert after["revision"] == before["revision"]

        assert conn.execute(
            "SELECT COUNT(*) c FROM lead_workflow_events WHERE lead_id = ?",
            (lead_for_cas,),
        ).fetchone()["c"] == 0
        assert conn.execute(
            "SELECT COUNT(*) c FROM lead_workflow_operations WHERE operation_id = 'op-boom'"
        ).fetchone()["c"] == 0
    finally:
        conn.close()


def test_post_commit_effects_are_handed_to_the_caller_not_run_inside(lead_for_cas):
    """
    Mail and notifications must fire after commit, so the core only collects
    them. A failing effect can then never turn a committed write into a
    "not saved" message.
    """
    from api.db import lead_workflow_transaction

    conn = get_conn()
    try:
        fired = []
        with lead_workflow_transaction(
            conn, lead_for_cas, actor_id=1,
            expected_revision=0, operation_id="op-effects",
        ) as ctx:
            ctx.update({"notes": "z efektem"})
            ctx.after_commit(lambda: fired.append("notified"))
            assert fired == [], "effects must not run inside the transaction"

        assert fired == ["notified"], "effects must run once the write is committed"
    finally:
        conn.close()
