"""
PartnerType -- the producer assigns a lead to a partner.

The shape being pinned: a producer fills in the ordinary lead form, picks an
active partner, and confirms "Utwórz i zaakceptuj". What comes out is that
partner's lead, already in the sale at the first configured stage -- but with
the producer recorded as the actor who did it. The partner's own submission is
untouched by this: it still lands pending, outside the pipeline.

Everything runs through the real router, because the guarantees are about who
may call this, under which schema version and replay key, and what happens
when the pipeline, the partner or the catalogue is not in the state the form
was drawn from.
"""
from api.tests.context import carry_context
import json
import pytest

from api.db import get_conn
from api.tests.integration.conftest import lead_payload


# ─── HELPERS ────────────────────────────────────────────────────────────────

def _login(call_api, user):
    status, body = call_api("POST", "/api/auth/login", body={
        "email": user["email"],
        "password": user["plain_password"],
        "role": user["role"],
    })
    assert status == 200, body
    return body["token"]


def _schema_revision():
    from api import lead_fields
    return lead_fields.schema_version()


def assign_payload(partner_id, **overrides):
    """A complete, valid assignment envelope with overrides merged in."""
    body = lead_payload(
        client_name="Klient Producenta",
        client_email="klient.producenta@test.pl",
        company="Firma Producenta",
        nip="5252248481",
        website="klient-producenta.pl",
    )
    body.update({
        "partner_id": partner_id,
        "schema_revision": _schema_revision(),
        "confirm_accept": True,
        "operation_id": "assign-op-1",
    })
    body.update(overrides)
    return body


def _assign(call_api, token, partner_id, **overrides):
    return call_api("POST", "/api/leads/assign",
                    body=assign_payload(partner_id, **overrides), token=token)


def _lead_count():
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"]
    finally:
        conn.close()


def _events(lead_id, event_type=None):
    conn = get_conn()
    try:
        if event_type:
            rows = conn.execute(
                "SELECT * FROM lead_workflow_events WHERE lead_id=? AND event_type=? "
                "ORDER BY id ASC", (lead_id, event_type)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM lead_workflow_events WHERE lead_id=? ORDER BY id ASC",
                (lead_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _first_stage_key():
    from api import lead_stages
    stage = lead_stages.get_first_pipeline_stage()
    return stage["key"] if stage else None


@pytest.fixture
def observer_token(call_api, admin_user):
    admin_token = _login(call_api, admin_user)
    status, body = call_api("POST", "/api/users", body={
        "name": "Test Obserwator",
        "email": "obserwator.assign@test.pl",
        "password": "ObserwatorPass123!",
        "company": "PartnerType",
        "role": "observer",
    }, token=admin_token)
    assert status == 201, body
    _, login = call_api("POST", "/api/auth/login", body={
        "email": "obserwator.assign@test.pl",
        "password": "ObserwatorPass123!",
        "role": "observer",
    })
    return login["token"]


# ─── THE TRACER ─────────────────────────────────────────────────────────────

def test_producer_assignment_creates_accepted_lead_owned_by_chosen_partner(
    call_api, admin_user, partner_user, partner_user_bronze,
):
    token = _login(call_api, admin_user)
    status, body = _assign(call_api, token, partner_user["id"])
    assert status == 201, body

    lead = body["lead"]
    assert lead["partner_id"] == partner_user["id"]
    assert lead["registration_status"] == "accepted"
    assert lead["status"] == _first_stage_key()

    # The owning partner sees it in the sale; the other partner sees nothing.
    owner_token = _login(call_api, partner_user)
    status, owned = call_api("GET", "/api/leads", token=owner_token)
    assert status == 200, owned
    assert [item["id"] for item in owned["leads"]] == [lead["id"]]

    other_token = _login(call_api, partner_user_bronze)
    status, other = call_api("GET", "/api/leads", token=other_token)
    assert status == 200, other
    assert other["leads"] == []


def test_assignment_records_the_producer_as_the_actual_actor(
    call_api, admin_user, partner_user,
):
    token = _login(call_api, admin_user)
    status, body = _assign(call_api, token, partner_user["id"])
    assert status == 201, body
    lead_id = body["lead"]["id"]

    events = _events(lead_id, "lead.assign")
    assert len(events) == 1, _events(lead_id)
    assert events[0]["actor_id"] == admin_user["id"]
    payload = json.loads(events[0]["payload_json"])
    assert payload["owner_id"] == partner_user["id"]
    assert payload["stage"] == _first_stage_key()


def test_partner_submission_still_lands_pending_outside_the_pipeline(
    call_api, partner_user,
):
    token = _login(call_api, partner_user)
    status, body = call_api("POST", "/api/leads", body=lead_payload(), token=token)
    assert status == 201, body
    assert body["lead"]["registration_status"] == "pending"
    assert body["lead"]["status"] is None


# ─── WHO MAY DO IT ──────────────────────────────────────────────────────────

def test_manager_partner_and_observer_cannot_assign(
    call_api, admin_user, manager_user, partner_user, observer_token,
):
    before = _lead_count()
    for token in (
        _login(call_api, manager_user),
        _login(call_api, partner_user),
        observer_token,
    ):
        status, body = _assign(call_api, token, partner_user["id"])
        assert status == 403, body
    assert _lead_count() == before


# ─── WHAT IT REFUSES ────────────────────────────────────────────────────────

@pytest.mark.parametrize("field,value", [
    ("partner_id", None),
    ("confirm_accept", False),
    ("operation_id", ""),
])
def test_incomplete_envelope_creates_nothing(
    call_api, admin_user, partner_user, field, value,
):
    token = _login(call_api, admin_user)
    before = _lead_count()
    body = assign_payload(partner_user["id"])
    body[field] = value
    status, response = call_api("POST", "/api/leads/assign", body=body, token=token)
    assert status == 400, response
    assert _lead_count() == before


def test_inactive_partner_is_refused(call_api, admin_user, partner_user):
    conn = get_conn()
    try:
        conn.execute("UPDATE users SET active=0 WHERE id=?", (partner_user["id"],))
        conn.commit()
    finally:
        conn.close()
    token = _login(call_api, admin_user)
    before = _lead_count()
    status, body = _assign(call_api, token, partner_user["id"])
    assert status == 400, body
    assert _lead_count() == before


def test_a_staff_account_cannot_be_made_the_owner(
    call_api, admin_user, manager_user,
):
    token = _login(call_api, admin_user)
    before = _lead_count()
    for owner_id in (admin_user["id"], manager_user["id"]):
        status, body = _assign(call_api, token, owner_id)
        assert status == 400, body
    assert _lead_count() == before


def test_product_that_is_not_offered_is_refused(
    call_api, admin_user, partner_user,
):
    conn = get_conn()
    try:
        conn.execute("UPDATE products SET offered=0 WHERE id=1")
        conn.commit()
    finally:
        conn.close()
    token = _login(call_api, admin_user)
    before = _lead_count()
    status, body = _assign(call_api, token, partner_user["id"])
    assert status == 400, body
    assert _lead_count() == before


def test_assignment_without_an_active_pipeline_stage_is_refused(
    call_api, admin_user, partner_user,
):
    conn = get_conn()
    try:
        conn.execute("UPDATE lead_stages SET active=0 WHERE is_terminal=0")
        conn.commit()
    finally:
        conn.close()
    token = _login(call_api, admin_user)
    before = _lead_count()
    status, body = _assign(call_api, token, partner_user["id"])
    assert status == 409, body
    assert body["code"] == "no_pipeline_stage"
    assert _lead_count() == before


def test_system_owned_columns_cannot_be_smuggled_in(
    call_api, admin_user, partner_user,
):
    token = _login(call_api, admin_user)
    before = _lead_count()
    for forbidden in ({"registration_status": "accepted"}, {"status": "contact"},
                      {"commission": 999}, {"revision": 5}):
        status, body = _assign(call_api, token, partner_user["id"], **forbidden)
        assert status == 400, body
    assert _lead_count() == before


def test_a_form_drawn_from_an_older_schema_is_refused(
    call_api, admin_user, partner_user,
):
    token = _login(call_api, admin_user)
    before = _lead_count()
    status, body = _assign(call_api, token, partner_user["id"],
                           schema_revision="0-0-stale")
    assert status == 409, body
    assert body["code"] == "stale_lead_fields"
    assert _lead_count() == before


# ─── ANSWERS AND REPLAY ─────────────────────────────────────────────────────

def _define_field(call_api, token, **overrides):
    body = {"label": "Termin wdrożenia", "field_type": "text", "required": True}
    body.update(overrides)
    status, created = call_api("POST", "/api/lead-fields", body=body, token=token)
    assert status == 201, created
    return created["field"]


def test_producer_answers_are_stored_with_the_assigned_lead(
    call_api, admin_user, partner_user,
):
    token = _login(call_api, admin_user)
    field = _define_field(call_api, token)
    status, body = _assign(
        call_api, token, partner_user["id"],
        schema_revision=_schema_revision(),
        lead_field_values={str(field["id"]): "Q1 2027"},
    )
    assert status == 201, body

    owner_token = _login(call_api, partner_user)
    status, detail = call_api("GET", f"/api/leads/{body['lead']['id']}",
                              token=owner_token)
    assert status == 200, detail
    answers = {item["label"]: item["value"] for item in detail["lead"]["field_values"]}
    assert answers["Termin wdrożenia"] == "Q1 2027"


def test_a_missing_required_answer_creates_nothing(
    call_api, admin_user, partner_user,
):
    token = _login(call_api, admin_user)
    _define_field(call_api, token)
    before = _lead_count()
    status, body = _assign(call_api, token, partner_user["id"],
                           schema_revision=_schema_revision())
    assert status == 400, body
    assert _lead_count() == before


def test_an_identical_retry_returns_the_same_lead_once(
    call_api, admin_user, partner_user,
):
    token = _login(call_api, admin_user)
    before = _lead_count()
    status, first = _assign(call_api, token, partner_user["id"])
    assert status == 201, first
    status, second = _assign(call_api, token, partner_user["id"])
    assert status == 201, second
    assert second["lead"]["id"] == first["lead"]["id"]
    assert _lead_count() == before + 1


def test_the_same_key_with_a_different_payload_is_a_conflict(
    call_api, admin_user, partner_user,
):
    token = _login(call_api, admin_user)
    status, first = _assign(call_api, token, partner_user["id"])
    assert status == 201, first
    status, body = _assign(call_api, token, partner_user["id"],
                           client_name="Ktoś zupełnie inny")
    assert status == 409, body
    assert body["code"] == "operation_conflict"
    assert _lead_count() == 1


# ─── SHARED STATE HELPERS ───────────────────────

def _set_setting(key, value):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value), "2026-09-10T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _lead_rows():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM leads ORDER BY id ASC").fetchall()]
    finally:
        conn.close()


def _count(table):
    conn = get_conn()
    try:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    finally:
        conn.close()


_DUP_IDENTITY = {"nip": "5252248481", "website": "wspolny-klient.pl"}


def _assign_direct(admin, partner_id, operation_id, **overrides):
    """The assignment entry point called as the router calls it."""
    from api import leads
    body = assign_payload(partner_id, operation_id=operation_id, **overrides)
    return leads.create_assigned_lead(admin, body)


def _submit_direct(partner, **overrides):
    """The partner's own submission entry point, same core primitive."""
    from api import leads
    payload = lead_payload(**overrides)
    return leads.create_lead(partner, payload)


# ─── REAL-SQLITE CONCURRENCY ────────────────────────────────────────
#
# Two writers that both read "there is one lead with this NIP" before either
# of them wrote must not both conclude "so I am number two". The lock the
# shared primitive takes is what makes the ID and the queue position facts
# rather than guesses, and the only way to show that is two real connections
# racing on a real file.

def test_two_assignments_from_one_snapshot_get_distinct_ids_and_positions(
    test_db_path, admin_user, partner_user, partner_user_bronze,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    owners = {"a": partner_user["id"], "b": partner_user_bronze["id"]}
    barrier = Barrier(2)

    def run(key):
        barrier.wait(timeout=10)
        return _assign_direct(admin_user, owners[key], f"race-assign-{key}",
                              **_DUP_IDENTITY)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(carry_context(run), ["a", "b"]))

    assert len({lead["id"] for lead in results}) == 2, results
    assert sorted(lead["queue_position"] for lead in results) == [1, 2]
    rows = _lead_rows()
    assert len(rows) == 2
    assert sorted(row["queue_position"] for row in rows) == [1, 2]
    assert all(row["registration_status"] == "accepted" for row in rows)


def test_assignment_racing_a_partner_submission_shares_the_queue(
    test_db_path, admin_user, partner_user, partner_user_bronze,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def run(which):
        barrier.wait(timeout=10)
        if which == "assign":
            return _assign_direct(admin_user, partner_user["id"],
                                  "race-mixed-assign", **_DUP_IDENTITY)
        return _submit_direct(partner_user_bronze, **_DUP_IDENTITY)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = dict(zip(["assign", "submit"],
                           pool.map(carry_context(run), ["assign", "submit"])))

    assert results["assign"]["id"] != results["submit"]["id"]
    assert sorted(r["queue_position"] for r in results.values()) == [1, 2]
    # The submission stays outside the sale; only the assignment enters it.
    assert results["submit"]["registration_status"] == "pending"
    assert results["submit"]["status"] is None
    assert results["assign"]["registration_status"] == "accepted"


def test_racing_writers_never_breach_the_configured_queue_maximum(
    test_db_path, admin_user, partner_user, partner_user_bronze,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    _set_setting("lead_queue_max", 2)
    _submit_direct(partner_user, **_DUP_IDENTITY)  # position 1 is taken
    barrier = Barrier(3)

    def run(index):
        barrier.wait(timeout=10)
        if index == 2:
            return _submit_direct(partner_user_bronze, **_DUP_IDENTITY)
        return _assign_direct(admin_user, partner_user["id"],
                              f"race-cap-{index}", **_DUP_IDENTITY)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(carry_context(run), [0, 1, 2]))

    rows = _lead_rows()
    assert len(rows) == 4
    assert len({row["id"] for row in rows}) == 4
    assert max(row["queue_position"] for row in rows) <= 2
    assert sorted(row["queue_position"] for row in rows) == [1, 2, 2, 2]


# ─── THE DUPLICATE POLICY IS THE SAME FOR BOTH DOORS ────────────────

@pytest.mark.parametrize("policy,expected_positions", [
    ("allow", [1, 1]),
    ("queue", [1, 2]),
])
def test_assignment_and_submission_follow_the_same_queue_arithmetic(
    test_db_path, admin_user, partner_user, partner_user_bronze,
    policy, expected_positions,
):
    _set_setting("lead_duplicate_policy", policy)
    submitted = _submit_direct(partner_user, **_DUP_IDENTITY)
    assigned = _assign_direct(admin_user, partner_user_bronze["id"],
                              f"policy-{policy}", **_DUP_IDENTITY)
    assert [submitted["queue_position"], assigned["queue_position"]] == \
        expected_positions


def test_confirm_accept_cannot_bypass_a_reject_policy(
    call_api, test_db_path, admin_user, partner_user,
):
    _set_setting("lead_duplicate_policy", "reject")
    _submit_direct(partner_user, **_DUP_IDENTITY)
    before = _lead_count()

    token = _login(call_api, admin_user)
    status, body = _assign(call_api, token, partner_user["id"],
                           confirm_accept=True, **_DUP_IDENTITY)
    assert status == 400, body
    assert _lead_count() == before

    # And the partner's own second attempt is refused in exactly the same way.
    partner_token = _login(call_api, partner_user)
    status, partner_body = call_api(
        "POST", "/api/leads",
        body=lead_payload(**_DUP_IDENTITY), token=partner_token)
    assert status == 400, partner_body
    assert _lead_count() == before


def test_a_reject_policy_still_admits_a_brand_new_client(
    call_api, test_db_path, admin_user, partner_user,
):
    _set_setting("lead_duplicate_policy", "reject")
    token = _login(call_api, admin_user)
    status, body = _assign(call_api, token, partner_user["id"])
    assert status == 201, body
    assert body["lead"]["queue_position"] == 1


# ─── NOTHING HALF-WRITTEN ───────────────────────────────────────────

def test_a_failure_after_the_lead_row_leaves_no_trace(
    call_api, test_db_path, admin_user, partner_user, monkeypatch,
):
    from api import lead_fields, leads
    token = _login(call_api, admin_user)
    field = _define_field(call_api, token)

    def boom(conn, lead_id, prepared):
        raise RuntimeError("answers store unavailable")

    monkeypatch.setattr(lead_fields, "save_values_in_transaction", boom)

    with pytest.raises(RuntimeError):
        leads.create_assigned_lead(admin_user, assign_payload(
            partner_user["id"],
            schema_revision=_schema_revision(),
            lead_field_values={str(field["id"]): "Q1 2027"},
        ))

    assert _lead_count() == 0
    assert _count("lead_field_values") == 0
    assert _count("lead_workflow_events") == 0
    assert _count("lead_workflow_operations") == 0


def test_the_same_rollback_holds_for_a_partner_submission(
    test_db_path, partner_user, monkeypatch,
):
    from api import lead_fields, leads

    def boom(conn, lead_id, prepared):
        raise RuntimeError("answers store unavailable")

    monkeypatch.setattr(lead_fields, "save_values_in_transaction", boom)
    with pytest.raises(RuntimeError):
        leads.create_lead(partner_user, lead_payload())

    assert _lead_count() == 0
    assert _count("lead_field_values") == 0


def test_a_field_added_after_the_form_was_drawn_blocks_the_save(
    call_api, test_db_path, admin_user, partner_user,
):
    """The form was drawn, then the producer edited the schema. The answers
    in flight were never checked against the new rules, so they are refused
    rather than saved against a form the producer never showed."""
    token = _login(call_api, admin_user)
    drawn_revision = _schema_revision()
    body = assign_payload(partner_user["id"], schema_revision=drawn_revision)

    _define_field(call_api, token, label="Budżet klienta")
    assert _schema_revision() != drawn_revision

    before = _lead_count()
    status, response = call_api("POST", "/api/leads/assign", body=body, token=token)
    assert status == 409, response
    assert response["code"] == "stale_lead_fields"
    assert _lead_count() == before
    assert _count("lead_workflow_events") == 0
    assert _count("lead_workflow_operations") == 0


# ─── THE SESSION IS RE-READ AT WRITE TIME ───────────────────────────

@pytest.mark.parametrize("mutation", [
    "UPDATE users SET active=0 WHERE id=?",
    "UPDATE users SET role='manager' WHERE id=?",
])
def test_a_deactivated_or_demoted_producer_session_cannot_assign(
    call_api, test_db_path, admin_user, partner_user, mutation,
):
    token = _login(call_api, admin_user)
    conn = get_conn()
    try:
        conn.execute(mutation, (admin_user["id"],))
        conn.commit()
    finally:
        conn.close()

    before = _lead_count()
    status, body = call_api("POST", "/api/leads/assign",
                            body=assign_payload(partner_user["id"]), token=token)
    assert status in (401, 403), body
    assert _lead_count() == before


def test_the_domain_function_refuses_a_demoted_actor_even_without_the_router(
    test_db_path, admin_user, partner_user,
):
    from api import leads
    conn = get_conn()
    try:
        conn.execute("UPDATE users SET active=0 WHERE id=?", (admin_user["id"],))
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(PermissionError):
        leads.create_assigned_lead(admin_user, assign_payload(partner_user["id"]))
    assert _lead_count() == 0


# ─── EFFECTS: OWNER IS TOLD, PRODUCER IS RECORDED ───────────────────

def test_the_owner_is_notified_while_the_producer_stays_the_audited_actor(
    call_api, test_db_path, admin_user, partner_user, mocker,
):
    notify = mocker.patch("api.notifications.create_notification")
    audit_log = mocker.patch("api.audit.log")
    send_mail = mocker.patch("api.email_service.send_lead_created")

    token = _login(call_api, admin_user)
    status, body = _assign(call_api, token, partner_user["id"])
    assert status == 201, body
    lead_id = body["lead"]["id"]

    assert notify.call_args.args[0] == partner_user["id"]
    assert send_mail.call_args.args[0] == partner_user["email"]
    assert send_mail.call_args.args[2] == lead_id
    actor, action = audit_log.call_args.args[0], audit_log.call_args.args[1]
    assert actor["id"] == admin_user["id"]
    assert action == "lead.assign"
    # History records the producer, not the partner who now owns the row.
    assert _events(lead_id, "lead.assign")[0]["actor_id"] == admin_user["id"]


def test_effects_run_after_the_commit_and_a_failing_one_does_not_undo_it(
    call_api, test_db_path, admin_user, partner_user, mocker,
):
    seen = {}

    def observe(*args, **kwargs):
        seen["rows"] = _lead_count()
        raise RuntimeError("notification bus down")

    mocker.patch("api.notifications.create_notification", side_effect=observe)
    mocker.patch("api.email_service.send_lead_created",
                 side_effect=RuntimeError("smtp down"))

    token = _login(call_api, admin_user)
    status, body = _assign(call_api, token, partner_user["id"])
    assert status == 201, body
    # The effect could already see the committed row: it ran after the commit.
    assert seen["rows"] == 1
    assert _lead_count() == 1

    owner_token = _login(call_api, partner_user)
    status, detail = call_api("GET", f"/api/leads/{body['lead']['id']}",
                              token=owner_token)
    assert status == 200, detail
