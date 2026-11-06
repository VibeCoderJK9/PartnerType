"""
PartnerType -- registration before the funnel, and the information exchange
around it.

The shape being pinned: a submitted deal waits on the REGISTRATION axis with
no sales stage at all. The producer has three moves -- accept, reject, ask for
information -- and only accepting puts the deal into the pipeline. A partner
answering a request for information supplies text; they never decide their own
lead.

Everything here runs through the real router, because the guarantees that
matter are about who may call what, and a domain-level test cannot see the
routing and authorization that stand in front of it.
"""
import pytest

from api.db import get_conn
from api import lead_fields


# ─── HELPERS ────────────────────────────────────────────────────────────────

def _login(call_api, user):
    status, body = call_api("POST", "/api/auth/login", body={
        "email": user["email"],
        "password": user["plain_password"],
        "role": user["role"],
    })
    assert status == 200, body
    return body["token"]


def _revision(lead_id):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT revision FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()["revision"]
    finally:
        conn.close()


def _lead_row(lead_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _comments(lead_id):
    conn = get_conn()
    try:
        return [
            (row["body"], row["user_id"], row["is_internal"])
            for row in conn.execute(
                "SELECT body, user_id, is_internal FROM lead_comments "
                "WHERE lead_id = ? ORDER BY id", (lead_id,)
            ).fetchall()
        ]
    finally:
        conn.close()


def _registration(call_api, token, lead_id, **body):
    body.setdefault("expected_revision", _revision(lead_id))
    body.setdefault("operation_id", f"op-{body['action']}-{lead_id}")
    return call_api(
        "POST", f"/api/leads/{lead_id}/registration", token=token, body=body
    )


def _information(call_api, token, lead_id, **body):
    body.setdefault("expected_revision", _revision(lead_id))
    body.setdefault("operation_id", f"op-info-{lead_id}")
    return call_api(
        "POST", f"/api/leads/{lead_id}/information", token=token, body=body
    )


@pytest.fixture
def observer_token(call_api, admin_user):
    """An observer created through the real endpoint, then logged in."""
    admin_token = _login(call_api, admin_user)
    status, body = call_api("POST", "/api/users", token=admin_token, body={
        "email": "obserwator@workflow.test",
        "password": "ObserwatorPass123!",
        "name": "Test Obserwator",
        "role": "observer",
        "company": "Obserwator sp. z o.o.",
    })
    assert status in (200, 201), body
    return _login(call_api, {
        "email": "obserwator@workflow.test",
        "plain_password": "ObserwatorPass123!",
        "role": "observer",
    })


# ─── THE REPORTED INVISIBILITY ──────────────────────────────────────────────

def _details_payload(lead_id, **changes):
    return dict(expected_revision=_revision(lead_id),
                schema_revision=lead_fields.schema_version(),
                operation_id="details-test", **changes)


def test_details_observer_cannot_write(call_api, sample_lead, observer_token):
    status, _ = call_api("PATCH", f"/api/leads/{sample_lead['id']}/details", token=observer_token,
                        body=_details_payload(sample_lead["id"], notes="blocked"))
    assert status == 403


def test_details_domain_rechecks_active_actor_before_replay(sample_lead, partner_user, db_conn):
    from api.leads import update_partner_lead_details
    payload = _details_payload(sample_lead["id"], notes="saved")
    update_partner_lead_details(sample_lead["id"], partner_user, payload)
    db_conn.execute("UPDATE users SET active=0 WHERE id=?", (partner_user["id"],))
    db_conn.commit()
    with pytest.raises(PermissionError):
        update_partner_lead_details(sample_lead["id"], partner_user, payload)


def test_details_stale_and_changed_retry_are_not_partial_writes(call_api, sample_lead, partner_user):
    token = _login(call_api, partner_user)
    payload = _details_payload(sample_lead["id"], notes="first")
    path = f"/api/leads/{sample_lead['id']}/details"
    status, _ = call_api("PATCH", path, token=token, body=payload)
    assert status == 200
    before = _lead_row(sample_lead["id"])
    status, result = call_api("PATCH", path, token=token, body=dict(payload, notes="different"))
    assert status == 409 and result["code"] == "operation_conflict"
    status, result = call_api("PATCH", path, token=token, body=dict(payload, operation_id="other"))
    assert status == 409 and result["code"] == "stale_revision"
    assert _lead_row(sample_lead["id"]) == before


def test_details_roll_back_contact_custom_and_revision_on_history_failure(
    sample_lead, partner_user, admin_user, db_conn, database_failure,
):
    from api.leads import update_partner_lead_details
    from psycopg import IntegrityError
    field = lead_fields.create_field(dict(label="Field", partner_editable=True), admin_user)
    database_failure('fail_details', 'lead_workflow_events', 'test history failure')
    before = _lead_row(sample_lead["id"])
    with pytest.raises(IntegrityError):
        update_partner_lead_details(sample_lead["id"], partner_user,
            _details_payload(sample_lead["id"], notes="not saved",
                             lead_field_values={str(field["id"]): "not saved"}))
    assert _lead_row(sample_lead["id"]) == before
    assert lead_fields.get_values(sample_lead["id"]) == []
    assert db_conn.execute("SELECT COUNT(*) FROM lead_workflow_operations").fetchone()[0] == 0


def test_partner_details_save_contact_and_typed_values_atomically(
    call_api, sample_lead, partner_user, admin_user,
):
    fields = [lead_fields.create_field(dict(label=kind, field_type=kind,
              partner_editable=True), admin_user) for kind in ("text", "number", "checkbox")]
    token = _login(call_api, partner_user)
    payload = _details_payload(sample_lead["id"], client_name="Żaneta 😀",
        client_email="nowy@example.pl", client_phone="123", notes="Notatka 🐈",
        lead_field_values={str(f["id"]): v for f, v in zip(fields, ["Treść", 0, False])})
    status, body = call_api("PATCH", f"/api/leads/{sample_lead['id']}/details", token=token, body=payload)
    assert status == 200, body
    assert body["lead"]["revision"] == payload["expected_revision"] + 1
    assert body["lead"]["permissions"]["edit_details"] is True
    _, refreshed = call_api("GET", f"/api/leads/{sample_lead['id']}", token=token)
    lead = refreshed["lead"]
    assert lead["client_name"] == "Żaneta 😀"
    assert lead["notes"] == "Notatka 🐈"
    assert [v["value"] for v in lead["field_values"]] == ["Treść", 0, False]
    assert lead["registration_status"] == "pending"
    assert lead["status"] is None
    assert lead["schema_revision"] == payload["schema_revision"]
    # Exact retry is not another edit or another history entry.
    retry_status, retry = call_api("PATCH", f"/api/leads/{sample_lead['id']}/details", token=token, body=payload)
    assert retry_status == 200 and retry == body
    assert _revision(sample_lead["id"]) == lead["revision"]


@pytest.mark.parametrize("key,value", [("company", "forged"), ("nip", "1111111111"),
    ("partner_id", 999), ("value", 0), ("product_id", 1), ("quantity", 2),
    ("registration_status", "accepted"), ("status", "won"), ("commission", 100)])
def test_partner_details_reject_protected_columns_without_partial_write(
    call_api, sample_lead, partner_user, key, value,
):
    before = _lead_row(sample_lead["id"])
    token = _login(call_api, partner_user)
    status, _ = call_api("PATCH", f"/api/leads/{sample_lead['id']}/details", token=token,
                        body=_details_payload(sample_lead["id"], notes="not saved", **{key: value}))
    assert status in (400, 403)
    assert _lead_row(sample_lead["id"]) == before


@pytest.mark.parametrize("key", ["expected_revision", "operation_id", "schema_revision"])
def test_partner_details_require_explicit_metadata(call_api, sample_lead, partner_user, key):
    payload = _details_payload(sample_lead["id"], notes="draft")
    del payload[key]
    status, _ = call_api("PATCH", f"/api/leads/{sample_lead['id']}/details",
                        token=_login(call_api, partner_user), body=payload)
    assert status == 400


@pytest.mark.parametrize("role", ["partner_user_bronze", "admin_user", "manager_user"])
def test_details_endpoint_is_only_for_owning_partner(call_api, sample_lead, request, role):
    before = _lead_row(sample_lead["id"])
    status, _ = call_api("PATCH", f"/api/leads/{sample_lead['id']}/details",
        token=_login(call_api, request.getfixturevalue(role)),
        body=_details_payload(sample_lead["id"], notes="not saved"))
    # Staff are refused; another partner cannot even see the lead (RLS, 0016).
    assert status == (404 if role == "partner_user_bronze" else 403)
    assert _lead_row(sample_lead["id"]) == before


@pytest.mark.parametrize("registration,stage,editable", [
    ("needs_info", None, True), ("accepted", "contact", True),
    ("accepted", "won", False), ("accepted", "lost", False),
    ("rejected", None, False), ("accepted", "missing", False),
])
def test_details_editability_uses_current_state(call_api, sample_lead, partner_user,
                                             db_conn, registration, stage, editable):
    db_conn.execute("UPDATE leads SET registration_status=?, status=? WHERE id=?",
                    (registration, stage, sample_lead["id"]))
    db_conn.commit()
    token = _login(call_api, partner_user)
    _, detail = call_api("GET", f"/api/leads/{sample_lead['id']}", token=token)
    assert detail["lead"]["permissions"]["edit_details"] is editable
    before = _lead_row(sample_lead["id"])
    status, _ = call_api("PATCH", f"/api/leads/{sample_lead['id']}/details", token=token,
                        body=_details_payload(sample_lead["id"], notes="new note"))
    assert status == (200 if editable else 409)
    if not editable:
        assert _lead_row(sample_lead["id"]) == before


def test_details_revoked_field_rejects_entire_stale_and_current_schema_patch(
    call_api, sample_lead, partner_user, admin_user,
):
    field = lead_fields.create_field(dict(label="Edit", partner_editable=True), admin_user)
    payload = _details_payload(sample_lead["id"], notes="not saved",
                              lead_field_values={str(field["id"]): "draft"})
    before = _lead_row(sample_lead["id"])
    lead_fields.update_field(field["id"], {"partner_editable": False}, admin_user)
    token = _login(call_api, partner_user)
    status, result = call_api("PATCH", f"/api/leads/{sample_lead['id']}/details", token=token, body=payload)
    assert status == 409 and result["code"] == "stale_lead_fields"
    payload["schema_revision"] = lead_fields.schema_version()
    status, _ = call_api("PATCH", f"/api/leads/{sample_lead['id']}/details", token=token, body=payload)
    assert status == 403
    assert _lead_row(sample_lead["id"]) == before
    assert lead_fields.get_values(sample_lead["id"]) == []


def test_details_null_clears_optional_answer_and_omission_preserves_other_answers(
    call_api, sample_lead, partner_user, admin_user, db_conn, database_failure,
):
    first, second = [lead_fields.create_field(dict(label=label, partner_editable=True), admin_user)
                     for label in ("Clear", "Preserve")]
    lead_fields.save_values_in_transaction(db_conn, sample_lead["id"],
        lead_fields.validate_values({str(first["id"]): "old", str(second["id"]): "keep"}))
    db_conn.commit()
    status, result = call_api("PATCH", f"/api/leads/{sample_lead['id']}/details",
        token=_login(call_api, partner_user), body=_details_payload(sample_lead["id"],
        lead_field_values={str(first["id"]): None}))
    assert status == 200, result
    assert [(v["field_id"], v["value"]) for v in result["lead"]["field_values"]] == [(second["id"], "keep")]


@pytest.mark.parametrize("actor_fixture", ["admin_user", "manager_user"])
def test_all_three_decisions_are_offered_while_a_lead_is_pending(
    call_api, sample_lead, request, actor_fixture
):
    """
    The original report was "the decision buttons are not visible". It has not
    reproduced -- not by the previous agent, and not here. This pins the
    guarantee from the server side for BOTH staff roles, so if the symptom
    returns it is provably a client-side question and nobody re-investigates
    the API.
    """
    actor = request.getfixturevalue(actor_fixture)
    token = _login(call_api, actor)

    assert _lead_row(sample_lead["id"])["registration_status"] == "pending"

    for action, payload in (
        ("request_info", {"message": "Prosimy o numer telefonu."}),
        ("reject", {"message": "Poza obszarem działania."}),
        ("accept", {}),
    ):
        status, body = call_api(
            "POST", f"/api/leads/{sample_lead['id']}/registration",
            token=token,
            body=dict(payload, action=action, expected_revision=0,
                      operation_id=f"probe-{action}"),
        )
        # Whatever each one does to the lead, none of them is refused for
        # being unavailable to this role at this state.
        assert status != 403, f"{actor['role']} was refused {action}: {body}"


@pytest.mark.parametrize("actor_fixture", ["admin_user", "manager_user"])
def test_accept_and_reject_stay_available_after_a_request_for_information(
    call_api, sample_lead, request, actor_fixture
):
    """needs_info is not a dead end: the producer can still decide without
    waiting for the partner to answer."""
    actor = request.getfixturevalue(actor_fixture)
    token = _login(call_api, actor)

    status, _ = _registration(call_api, token, sample_lead["id"],
                              action="request_info", message="Czego brakuje?")
    assert status == 200
    assert _lead_row(sample_lead["id"])["registration_status"] == "needs_info"

    status, body = _registration(call_api, token, sample_lead["id"],
                                 action="accept", operation_id="accept-after-info")
    assert status == 200, body
    assert _lead_row(sample_lead["id"])["registration_status"] == "accepted"


# ─── REGISTRATION STAYS OUT OF THE SALE ─────────────────────────────────────

def test_pending_and_needs_info_carry_no_sales_stage(call_api, sample_lead, admin_user):
    token = _login(call_api, admin_user)

    assert _lead_row(sample_lead["id"])["status"] is None

    _registration(call_api, token, sample_lead["id"],
                  action="request_info", message="Uzupełnij dane.")
    assert _lead_row(sample_lead["id"])["status"] is None


def test_only_acceptance_puts_a_lead_on_the_first_active_stage(
    call_api, sample_lead, admin_user
):
    token = _login(call_api, admin_user)

    status, body = _registration(call_api, token, sample_lead["id"], action="accept")
    assert status == 200, body

    conn = get_conn()
    try:
        first = conn.execute(
            """SELECT key FROM lead_stages
                WHERE is_terminal = 0 AND active = 1
                ORDER BY sort_order ASC, id ASC LIMIT 1"""
        ).fetchone()
    finally:
        conn.close()

    row = _lead_row(sample_lead["id"])
    assert row["registration_status"] == "accepted"
    assert row["status"] == first["key"]


def test_acceptance_without_a_configured_stage_conflicts_and_changes_nothing(
    call_api, sample_lead, admin_user
):
    """
    A producer who deactivated every non-terminal stage has nowhere to put an
    accepted deal. Refusing loudly beats accepting it into a pipeline position
    that does not exist.
    """
    token = _login(call_api, admin_user)
    conn = get_conn()
    try:
        conn.execute("UPDATE lead_stages SET active = 0 WHERE is_terminal = 0")
        conn.commit()
    finally:
        conn.close()

    before = _lead_row(sample_lead["id"])
    status, body = _registration(call_api, token, sample_lead["id"], action="accept")

    assert status == 409, body
    after = _lead_row(sample_lead["id"])
    assert after["registration_status"] == before["registration_status"]
    assert after["status"] == before["status"]
    assert after["revision"] == before["revision"]
    assert _comments(sample_lead["id"]) == []


# ─── MESSAGE VALIDATION ─────────────────────────────────────────────────────

@pytest.mark.parametrize("action", ["reject", "request_info"])
@pytest.mark.parametrize("message", [None, "", "   ", "\n\t "])
def test_empty_message_is_refused_with_no_change(
    call_api, sample_lead, admin_user, action, message
):
    token = _login(call_api, admin_user)
    before = _lead_row(sample_lead["id"])

    body = {"action": action, "expected_revision": before["revision"],
            "operation_id": f"empty-{action}"}
    if message is not None:
        body["message"] = message
    status, response = call_api(
        "POST", f"/api/leads/{sample_lead['id']}/registration",
        token=token, body=body,
    )

    assert status == 400, response
    after = _lead_row(sample_lead["id"])
    assert after["registration_status"] == before["registration_status"]
    assert after["revision"] == before["revision"]
    assert _comments(sample_lead["id"]) == []


@pytest.mark.parametrize("action", ["reject", "request_info"])
def test_a_single_character_is_enough(call_api, sample_lead, admin_user, action):
    token = _login(call_api, admin_user)
    status, body = _registration(call_api, token, sample_lead["id"],
                                 action=action, message="x")
    assert status == 200, body
    assert _comments(sample_lead["id"])[0][0] == "x"


def test_message_length_is_counted_in_code_points_not_utf16_units(
    call_api, sample_lead, admin_user
):
    """
    An emoji is one character to a person and two UTF-16 units to a naive
    length check. 4000 emoji must be accepted; measuring the wrong unit would
    refuse them at 2000.
    """
    token = _login(call_api, admin_user)

    status, body = _registration(call_api, token, sample_lead["id"],
                                 action="request_info", message="😀" * 4000)
    assert status == 200, body
    assert len(_comments(sample_lead["id"])[0][0]) == 4000


def test_message_over_the_limit_is_refused(call_api, sample_lead, admin_user):
    token = _login(call_api, admin_user)
    before = _lead_row(sample_lead["id"])

    status, body = _registration(call_api, token, sample_lead["id"],
                                 action="request_info", message="a" * 4001)
    assert status == 400, body
    assert _lead_row(sample_lead["id"])["revision"] == before["revision"]


def test_text_round_trips_unicode_and_is_stored_trimmed(
    call_api, sample_lead, admin_user
):
    token = _login(call_api, admin_user)
    _registration(call_api, token, sample_lead["id"],
                  action="request_info", message="  Prośba: żółć, ćma — 😀  ")
    assert _comments(sample_lead["id"])[0][0] == "Prośba: żółć, ćma — 😀"


def test_accept_needs_no_message(call_api, sample_lead, admin_user):
    token = _login(call_api, admin_user)
    status, body = _registration(call_api, token, sample_lead["id"], action="accept")
    assert status == 200, body


# ─── THE INFORMATION EXCHANGE ───────────────────────────────────────────────

def test_request_response_then_accept(call_api, sample_lead, admin_user, partner_user):
    """The whole loop: producer asks, partner answers, producer decides."""
    admin_token = _login(call_api, admin_user)
    partner_token = _login(call_api, partner_user)

    status, _ = _registration(call_api, admin_token, sample_lead["id"],
                              action="request_info", message="Podaj termin wdrożenia.")
    assert status == 200

    status, body = _information(call_api, partner_token, sample_lead["id"],
                                message="Klient chce wdrożenie w Q1.")
    assert status == 200, body

    row = _lead_row(sample_lead["id"])
    assert row["registration_status"] == "needs_info", (
        "answering is not deciding -- the lead keeps waiting for the producer"
    )
    assert row["status"] is None

    status, body = _registration(call_api, admin_token, sample_lead["id"],
                                 action="accept", operation_id="accept-after-answer")
    assert status == 200, body
    assert _lead_row(sample_lead["id"])["registration_status"] == "accepted"

    assert [(text, author) for text, author, _ in _comments(sample_lead["id"])] == [
        ("Podaj termin wdrożenia.", admin_user["id"]),
        ("Klient chce wdrożenie w Q1.", partner_user["id"]),
    ]


def test_request_response_then_reject(call_api, sample_lead, admin_user, partner_user):
    admin_token = _login(call_api, admin_user)
    partner_token = _login(call_api, partner_user)

    _registration(call_api, admin_token, sample_lead["id"],
                  action="request_info", message="Jaki jest budżet?")
    _information(call_api, partner_token, sample_lead["id"], message="Budżet 20 tys.")

    status, body = _registration(call_api, admin_token, sample_lead["id"],
                                 action="reject", message="Budżet zbyt niski.",
                                 operation_id="reject-after-answer")
    assert status == 200, body
    row = _lead_row(sample_lead["id"])
    assert row["registration_status"] == "rejected"
    assert row["status"] is None


def test_a_partner_cannot_answer_before_being_asked(
    call_api, sample_lead, partner_user
):
    token = _login(call_api, partner_user)
    status, body = _information(call_api, token, sample_lead["id"], message="cokolwiek")
    assert status == 409, body
    assert _comments(sample_lead["id"]) == []


@pytest.mark.parametrize("message", ["", "   ", None])
def test_an_empty_answer_is_refused(
    call_api, sample_lead, admin_user, partner_user, message
):
    admin_token = _login(call_api, admin_user)
    partner_token = _login(call_api, partner_user)
    _registration(call_api, admin_token, sample_lead["id"],
                  action="request_info", message="Podaj dane.")

    body = {"expected_revision": _revision(sample_lead["id"]),
            "operation_id": "empty-answer"}
    if message is not None:
        body["message"] = message
    status, response = call_api(
        "POST", f"/api/leads/{sample_lead['id']}/information",
        token=partner_token, body=body,
    )
    assert status == 400, response
    assert len(_comments(sample_lead["id"])) == 1


# ─── WHO MAY DO WHAT ────────────────────────────────────────────────────────

def test_a_partner_cannot_decide_their_own_lead(call_api, sample_lead, partner_user):
    """The whole point of the registration axis: the producer decides."""
    token = _login(call_api, partner_user)
    before = _lead_row(sample_lead["id"])

    for action in ("accept", "reject", "request_info"):
        status, body = call_api(
            "POST", f"/api/leads/{sample_lead['id']}/registration",
            token=token,
            body={"action": action, "message": "wpuść mnie",
                  "expected_revision": before["revision"],
                  "operation_id": f"partner-{action}"},
        )
        assert status == 403, f"partner was allowed to {action}: {body}"

    after = _lead_row(sample_lead["id"])
    assert after["registration_status"] == before["registration_status"]
    assert after["revision"] == before["revision"]


def test_an_observer_may_not_decide(call_api, sample_lead, observer_token):
    status, body = call_api(
        "POST", f"/api/leads/{sample_lead['id']}/registration",
        token=observer_token,
        body={"action": "accept", "expected_revision": 0, "operation_id": "obs"},
    )
    assert status == 403, body
    assert _lead_row(sample_lead["id"])["registration_status"] == "pending"


def test_partner_b_cannot_answer_partner_a_lead_even_by_editing_the_url(
    call_api, sample_lead, admin_user, partner_user_bronze
):
    """Ownership is checked in the domain, not inferred from the list query
    that happened to produce the id."""
    admin_token = _login(call_api, admin_user)
    _registration(call_api, admin_token, sample_lead["id"],
                  action="request_info", message="Pytanie do A.")

    intruder_token = _login(call_api, partner_user_bronze)
    status, body = _information(call_api, intruder_token, sample_lead["id"],
                                message="Podszywam się.")

    # Row-level security (0016): another partner's lead does not exist for him.
    assert status == 404, body
    assert len(_comments(sample_lead["id"])) == 1


def test_answering_a_lead_that_does_not_exist_is_a_404(call_api, partner_user):
    token = _login(call_api, partner_user)
    status, body = call_api(
        "POST", "/api/leads/LD-NOPE/information", token=token,
        body={"message": "halo", "expected_revision": 0, "operation_id": "ghost"},
    )
    assert status == 404, body


def test_patching_details_of_a_lead_that_does_not_exist_is_a_404(call_api, partner_user):
    token = _login(call_api, partner_user)
    status, body = call_api(
        "PATCH", "/api/leads/LD-NOPE/details", token=token,
        body={"notes": "halo", "expected_revision": 0,
              "schema_revision": lead_fields.schema_version(), "operation_id": "ghost"},
    )
    assert status == 404, body
    assert body.get("code") == "not_found"


# ─── VERSIONING ─────────────────────────────────────────────────────────────

def test_a_decision_bumps_the_revision_once(call_api, sample_lead, admin_user):
    token = _login(call_api, admin_user)
    before = _revision(sample_lead["id"])
    _registration(call_api, token, sample_lead["id"],
                  action="request_info", message="Pytanie.")
    assert _revision(sample_lead["id"]) == before + 1


def test_a_stale_decision_conflicts_and_changes_nothing(
    call_api, sample_lead, admin_user
):
    """Two producers with the same screen open: the second one to save is told
    to look again rather than silently overwriting the first."""
    token = _login(call_api, admin_user)
    stale = _revision(sample_lead["id"])

    _registration(call_api, token, sample_lead["id"],
                  action="request_info", message="Pierwsza decyzja.")

    status, body = call_api(
        "POST", f"/api/leads/{sample_lead['id']}/registration",
        token=token,
        body={"action": "reject", "message": "Druga decyzja.",
              "expected_revision": stale, "operation_id": "second"},
    )

    assert status == 409, body
    row = _lead_row(sample_lead["id"])
    assert row["registration_status"] == "needs_info"
    assert len(_comments(sample_lead["id"])) == 1


@pytest.mark.parametrize("missing", ["expected_revision", "operation_id"])
def test_a_write_without_its_versioning_metadata_is_refused(
    call_api, sample_lead, admin_user, missing
):
    """The server does not fill these in on a client's behalf -- doing so would
    turn compare-and-swap back into last-write-wins."""
    token = _login(call_api, admin_user)
    body = {"action": "request_info", "message": "Pytanie.",
            "expected_revision": 0, "operation_id": "meta"}
    del body[missing]

    status, response = call_api(
        "POST", f"/api/leads/{sample_lead['id']}/registration",
        token=token, body=body,
    )
    assert status == 400, response
    assert _comments(sample_lead["id"]) == []


# ─── ATOMICITY ──────────────────────────────────────────────────────────────

def test_text_and_state_roll_back_together(
    call_api, sample_lead, admin_user, monkeypatch, database_failure
):
    """
    The decision and the message the partner reads are one fact. A fault while
    writing the comment must not leave a lead that says "rejected" with no
    reason anywhere.
    """
    token = _login(call_api, admin_user)
    before = _lead_row(sample_lead["id"])

    database_failure('boom', 'lead_comments', 'injected comment failure')
    status, body = _registration(call_api, token, sample_lead['id'],
                                 action='reject', message='Powód odrzucenia.')
    assert status >= 500 or status == 409, body

    after = _lead_row(sample_lead["id"])
    assert after["registration_status"] == before["registration_status"]
    assert after["revision"] == before["revision"]
    assert _comments(sample_lead["id"]) == []


# ─── PARTNER KANBAN STAGE MOVE ─────────────────
#
# The partner's own board move: PATCH /api/leads/{id}/stage. Forward/back
# among nonterminal configured stages is unrestricted; entering a terminal
# stage requires confirmed_terminal=true regardless of what the UI showed.

def _accept(call_api, lead_id, admin_token):
    status, body = call_api(
        "PATCH", f"/api/leads/{lead_id}",
        body={"registration_status": "accepted"}, token=admin_token,
    )
    assert status == 200, body
    return body


def _move_stage(call_api, token, lead_id, **body):
    body.setdefault("expected_revision", _revision(lead_id))
    body.setdefault("operation_id", f"op-stage-{lead_id}-{body.get('target_stage', 'x')}-{_revision(lead_id)}")
    return call_api("PATCH", f"/api/leads/{lead_id}/stage", token=token, body=body)


class TestPartnerStageMove:
    def test_moves_forward_and_backward_and_it_survives_a_reload(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _accept(call_api, lead_id, admin_token)
        assert _lead_row(lead_id)["status"] == "contact"

        status, body = _move_stage(call_api, partner_token, lead_id, target_stage="presentation")
        assert status == 200, body
        assert body["lead"]["status"] == "presentation"

        status, body = _move_stage(call_api, partner_token, lead_id, target_stage="contact")
        assert status == 200, body
        assert body["lead"]["status"] == "contact"

        # "Reload": a fresh GET must show exactly what the last move wrote.
        status, body = call_api("GET", f"/api/leads/{lead_id}", token=partner_token)
        assert status == 200, body
        assert body["lead"]["status"] == "contact"

    def test_moving_to_the_current_stage_is_a_no_op_with_no_revision_bump(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _accept(call_api, lead_id, admin_token)
        before = _revision(lead_id)

        status, body = _move_stage(call_api, partner_token, lead_id, target_stage="contact")

        assert status == 200, body
        assert body["lead"]["status"] == "contact"
        assert _revision(lead_id) == before

    def test_entering_a_terminal_stage_requires_explicit_confirmation(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _accept(call_api, lead_id, admin_token)

        status, body = _move_stage(call_api, partner_token, lead_id, target_stage="won")
        assert status == 409, body
        assert body["code"] == "confirmation_required"
        assert _lead_row(lead_id)["status"] == "contact"

        status, body = _move_stage(
            call_api, partner_token, lead_id, target_stage="won", confirmed_terminal=True
        )
        assert status == 200, body
        assert body["lead"]["status"] == "won"

    def test_a_forged_confirmed_terminal_flag_is_the_only_thing_checked_not_ui_state(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        """The plan is explicit: the UI's own popup is not the permission
        boundary, this flag is -- a caller that skips the popup entirely and
        sends confirmed_terminal straight away still succeeds once true."""
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _accept(call_api, lead_id, admin_token)

        status, body = _move_stage(
            call_api, partner_token, lead_id, target_stage="lost", confirmed_terminal=True
        )
        assert status == 200, body
        assert body["lead"]["status"] == "lost"

    def test_a_terminal_lead_is_read_only_for_the_partner(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _accept(call_api, lead_id, admin_token)
        _move_stage(call_api, partner_token, lead_id, target_stage="won", confirmed_terminal=True)

        status, body = _move_stage(call_api, partner_token, lead_id, target_stage="presentation")
        assert status == 409, body
        assert body["code"] == "lead_read_only"

    def test_an_unaccepted_lead_cannot_be_moved(self, call_api, sample_lead, partner_user):
        partner_token = _login(call_api, partner_user)
        status, body = _move_stage(
            call_api, partner_token, sample_lead["id"], target_stage="contact"
        )
        assert status == 409, body
        assert body["code"] == "not_accepted"

    def test_an_unknown_or_inactive_destination_is_refused(
        self, call_api, sample_lead, admin_user, partner_user, db_conn
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _accept(call_api, lead_id, admin_token)

        status, body = _move_stage(
            call_api, partner_token, lead_id, target_stage="nieistniejacy_etap"
        )
        assert status == 409, body
        assert body["code"] == "unknown_stage"

        db_conn.execute("UPDATE lead_stages SET active=0 WHERE key='offer'")
        db_conn.commit()
        status, body = _move_stage(call_api, partner_token, lead_id, target_stage="offer")
        assert status == 409, body
        assert body["code"] == "unknown_stage"
        assert _lead_row(lead_id)["status"] == "contact"

    def test_partner_b_cannot_move_partner_as_lead_even_by_editing_the_url(
        self, call_api, sample_lead, admin_user, partner_user, partner_user_bronze
    ):
        admin_token = _login(call_api, admin_user)
        _accept(call_api, sample_lead["id"], admin_token)
        other_token = _login(call_api, partner_user_bronze)

        status, body = _move_stage(
            call_api, other_token, sample_lead["id"], target_stage="presentation"
        )
        # Row-level security (0016): another partner's lead does not exist for him.
        assert status == 404, body
        assert _lead_row(sample_lead["id"])["status"] == "contact"

    def test_admin_and_observer_cannot_use_the_partner_move_endpoint(
        self, call_api, sample_lead, admin_user, partner_user, observer_token
    ):
        admin_token = _login(call_api, admin_user)
        _accept(call_api, sample_lead["id"], admin_token)

        status, _ = _move_stage(
            call_api, admin_token, sample_lead["id"], target_stage="presentation"
        )
        assert status == 403

        status, _ = _move_stage(
            call_api, observer_token, sample_lead["id"], target_stage="presentation"
        )
        assert status == 403

    def test_a_concurrent_move_from_a_stale_snapshot_conflicts_with_409(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _accept(call_api, lead_id, admin_token)
        stale_revision = _revision(lead_id)

        status, body = _move_stage(call_api, partner_token, lead_id, target_stage="presentation")
        assert status == 200, body

        status, body = call_api(
            "PATCH", f"/api/leads/{lead_id}/stage", token=partner_token,
            body={"target_stage": "offer", "expected_revision": stale_revision,
                  "operation_id": "second-connection"},
        )
        assert status == 409, body
        assert body["code"] == "stale_revision"
        assert _lead_row(lead_id)["status"] == "presentation"

    def test_a_missing_destination_or_versioning_field_is_rejected_before_any_write(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _accept(call_api, lead_id, admin_token)
        before = _lead_row(lead_id)

        for body in (
            {"expected_revision": _revision(lead_id), "operation_id": "no-target"},
            {"target_stage": "presentation", "operation_id": "no-revision"},
            {"target_stage": "presentation", "expected_revision": _revision(lead_id)},
        ):
            status, response = call_api(
                "PATCH", f"/api/leads/{lead_id}/stage", token=partner_token, body=body
            )
            assert status == 400, response
        assert _lead_row(lead_id) == before

    # ── operation_id is a bounded (1..128 char),
    # case-sensitive ASCII string, not a payload hash detail the UI needs
    # to worry about beyond "keep it stable per attempt".

    def test_operation_id_accepts_up_to_128_ascii_characters(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _accept(call_api, lead_id, admin_token)

        status, body = _move_stage(
            call_api, partner_token, lead_id, target_stage="presentation",
            operation_id="x" * 128,
        )
        assert status == 200, body

    def test_operation_id_over_128_characters_is_rejected_before_any_write(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _accept(call_api, lead_id, admin_token)
        before = _lead_row(lead_id)

        status, body = _move_stage(
            call_api, partner_token, lead_id, target_stage="presentation",
            operation_id="x" * 129,
        )
        assert status == 400, body
        assert _lead_row(lead_id) == before

    def test_operation_id_must_be_ascii(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _accept(call_api, lead_id, admin_token)
        before = _lead_row(lead_id)

        status, body = _move_stage(
            call_api, partner_token, lead_id, target_stage="presentation",
            operation_id="opéración-👍",
        )
        assert status == 400, body
        assert _lead_row(lead_id) == before

    def test_operation_id_is_case_sensitive_not_folded_before_comparison(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _accept(call_api, lead_id, admin_token)
        revision = _revision(lead_id)

        status, first = call_api(
            "PATCH", f"/api/leads/{lead_id}/stage", token=partner_token,
            body={"target_stage": "presentation", "expected_revision": revision,
                  "operation_id": "Move-Op-1"},
        )
        assert status == 200, first

        # A different-case id names a DIFFERENT operation, not a replay of
        # the one above -- moving back with it must go through as its own
        # move rather than being silently treated as the same key.
        status, second = call_api(
            "PATCH", f"/api/leads/{lead_id}/stage", token=partner_token,
            body={"target_stage": "contact", "expected_revision": _revision(lead_id),
                  "operation_id": "move-op-1"},
        )
        assert status == 200, second
        assert second["lead"]["status"] == "contact"

    def test_retrying_the_exact_same_operation_replays_without_a_second_effect(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _accept(call_api, lead_id, admin_token)
        revision = _revision(lead_id)
        body = {"target_stage": "presentation", "expected_revision": revision,
                "operation_id": "replay-me"}

        status, first = call_api(
            "PATCH", f"/api/leads/{lead_id}/stage", token=partner_token, body=body
        )
        assert status == 200, first
        after_first = _revision(lead_id)

        status, second = call_api(
            "PATCH", f"/api/leads/{lead_id}/stage", token=partner_token, body=body
        )
        assert status == 200, second
        assert second == first
        assert _revision(lead_id) == after_first

        # A changed payload under the same operation_id is a conflict, not a
        # second silent move.
        status, third = call_api(
            "PATCH", f"/api/leads/{lead_id}/stage", token=partner_token,
            body={**body, "target_stage": "offer"},
        )
        assert status == 409, third
        assert third["code"] == "operation_conflict"

    def test_winning_computes_commission_the_same_way_admin_does_and_stamps_won_at_once(
        self, call_api, sample_lead, admin_user, partner_user_tiered
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user_tiered)
        _, create_body = call_api(
            "POST", "/api/leads",
            body={"client_name": "Klient", "client_email": "k@test.pl", "company": "Co",
                  "value": 10000, "nip": "1111111111", "website": "example.pl",
                  "product_id": 1, "quantity": 1},
            token=partner_token,
        )
        lead_id = create_body["lead"]["id"]
        _accept(call_api, lead_id, admin_token)

        status, body = _move_stage(
            call_api, partner_token, lead_id, target_stage="won", confirmed_terminal=True
        )
        assert status == 200, body
        assert body["lead"]["commission"] == 700.0
        won_at_first = _lead_row(lead_id)["won_at"]
        assert won_at_first is not None

        # A second identical request through the replay key must not restate
        # won_at or the commission; a second DIFFERENT request against a now
        # terminal lead must be refused as read-only, never a second win.
        status, replay = call_api(
            "PATCH", f"/api/leads/{lead_id}/stage", token=partner_token,
            body={"target_stage": "won", "expected_revision": _revision(lead_id),
                  "operation_id": "already-used"},
        )
        assert status == 409, replay
        assert replay["code"] == "lead_read_only"
        assert _lead_row(lead_id)["won_at"] == won_at_first


# ─── SHARED DOMAIN CORE: NO ADAPTER CAN EXIT A TERMINAL STAGE ──────
#
# Reopening a closed lead is a dedicated, audited action. The block belongs
# in the shared domain core (update_lead / apply_registration_action), not
# only behind the partner's own move endpoint -- a generic admin PATCH or a registration decision must not be
# able to quietly pull a won/lost lead back into the pipeline.

class TestTerminalExitBlockedFromEveryAdapter:
    def _win(self, call_api, lead_id, admin_token, partner_token):
        _accept(call_api, lead_id, admin_token)
        status, body = _move_stage(
            call_api, partner_token, lead_id, target_stage="won", confirmed_terminal=True
        )
        assert status == 200, body
        return body

    def test_generic_admin_patch_cannot_move_a_status_off_a_terminal_stage(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        self._win(call_api, lead_id, admin_token, partner_token)
        won_row = _lead_row(lead_id)

        status, body = call_api(
            "PATCH", f"/api/leads/{lead_id}",
            token=admin_token,
            body={"status": "negotiation",
                  "expected_revision": _revision(lead_id), "operation_id": "reopen-1"},
        )
        assert status == 409, body
        assert body["code"] == "lead_read_only"
        assert _lead_row(lead_id) == won_row

    def test_generic_admin_patch_cannot_reject_an_already_won_lead(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        self._win(call_api, lead_id, admin_token, partner_token)
        won_row = _lead_row(lead_id)

        status, body = call_api(
            "PATCH", f"/api/leads/{lead_id}",
            token=admin_token,
            body={"registration_status": "rejected", "registration_message": "Pomyłka.",
                  "expected_revision": _revision(lead_id), "operation_id": "reopen-2"},
        )
        assert status == 409, body
        assert body["code"] == "lead_read_only"
        assert _lead_row(lead_id) == won_row

    def test_the_named_registration_endpoint_cannot_reject_an_already_lost_lead(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _accept(call_api, lead_id, admin_token)
        status, body = _move_stage(
            call_api, partner_token, lead_id, target_stage="lost", confirmed_terminal=True
        )
        assert status == 200, body
        lost_row = _lead_row(lead_id)

        status, body = call_api(
            "POST", f"/api/leads/{lead_id}/registration",
            token=admin_token,
            body={"action": "reject", "message": "Pomyłka.",
                  "expected_revision": _revision(lead_id), "operation_id": "reopen-3"},
        )
        assert status == 409, body
        assert body["code"] == "lead_read_only"
        assert _lead_row(lead_id) == lost_row

    def test_rejecting_an_accepted_lead_still_on_a_nonterminal_stage_is_unaffected(
        self, call_api, sample_lead, admin_user
    ):
        """The guard is scoped to a TERMINAL exit only -- an accepted deal
        still in flight (never won or lost) can still be rejected exactly as
        before this change."""
        admin_token = _login(call_api, admin_user)
        lead_id = sample_lead["id"]
        _accept(call_api, lead_id, admin_token)
        assert _lead_row(lead_id)["status"] == "contact"

        status, body = call_api(
            "PATCH", f"/api/leads/{lead_id}",
            token=admin_token,
            body={"registration_status": "rejected", "registration_message": "Rezygnacja.",
                  "expected_revision": _revision(lead_id), "operation_id": "legit-reject"},
        )
        assert status == 200, body
        assert body["lead"]["registration_status"] == "rejected"
        assert body["lead"]["status"] is None

    def test_a_commission_correction_on_an_already_won_lead_still_works(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        """The guard only freezes the PIPELINE POSITION, not every field --
        a producer must still be able to correct a commission on a deal
        that already closed."""
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        self._win(call_api, lead_id, admin_token, partner_token)

        status, body = call_api(
            "PATCH", f"/api/leads/{lead_id}",
            token=admin_token,
            body={"commission": 1234.5,
                  "expected_revision": _revision(lead_id), "operation_id": "commission-fix"},
        )
        assert status == 200, body
        assert body["lead"]["commission"] == 1234.5
        assert body["lead"]["status"] == "won"

    def test_a_blocked_reopen_attempt_leaves_no_audit_trail_claiming_it_happened(
        self, call_api, sample_lead, admin_user, partner_user, mocker
    ):
        """Financial/actor integrity: a refused write must not
        also leave an audit entry or an email as if it had gone through --
        that would be a record of something that never actually happened."""
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        self._win(call_api, lead_id, admin_token, partner_token)

        audit_log = mocker.patch("api.audit.log")
        mailer = mocker.patch("api.email_service.send_lead_status_changed")

        status, body = call_api(
            "PATCH", f"/api/leads/{lead_id}",
            token=admin_token,
            body={"status": "negotiation",
                  "expected_revision": _revision(lead_id), "operation_id": "reopen-4"},
        )
        assert status == 409, body
        audit_log.assert_not_called()
        mailer.assert_not_called()


# ─── TAKING BACK A REFUSAL ──────────────────────────────────────────────────
#
# "leady powinna byc opcja cofniecia odrzucenia". Rejecting
# is the one decision a producer can make by accident and, until now, could not
# undo: the lead dropped out of the working list and no action led back.
#
# What the undo must and must not do is the whole point of these tests. It
# returns the lead to PENDING -- awaiting a decision. Returning it as accepted
# would be the system deciding on the producer's behalf, and a mis-click would
# silently put a deal into the sale with a commission attached.


def _reject(call_api, token, lead_id, reason="Nie nasz profil."):
    return _registration(call_api, token, lead_id, action="reject", message=reason)


def test_undo_reject_returns_the_lead_to_awaiting_a_decision(
    call_api, sample_lead, admin_user
):
    token = _login(call_api, admin_user)
    lead_id = sample_lead["id"]
    status, _ = _reject(call_api, token, lead_id)
    assert status == 200
    assert _lead_row(lead_id)["registration_status"] == "rejected"

    status, body = _registration(
        call_api, token, lead_id,
        action="undo_reject", operation_id=f"undo-{lead_id}",
    )

    assert status == 200, body
    row = _lead_row(lead_id)
    assert row["registration_status"] == "pending"
    # Never accepted, and never on a sales stage: an undone refusal is an
    # undecided lead, not an approved one.
    assert row["status"] is None


def test_undo_reject_refuses_a_lead_that_was_not_rejected(
    call_api, sample_lead, admin_user
):
    token = _login(call_api, admin_user)
    lead_id = sample_lead["id"]
    assert _lead_row(lead_id)["registration_status"] == "pending"

    status, body = _registration(
        call_api, token, lead_id,
        action="undo_reject", operation_id=f"undo-{lead_id}",
    )

    assert status == 409, body
    assert _lead_row(lead_id)["registration_status"] == "pending"


def test_undo_reject_is_refused_for_a_partner(
    call_api, sample_lead, admin_user, partner_user
):
    admin_token = _login(call_api, admin_user)
    lead_id = sample_lead["id"]
    assert _reject(call_api, admin_token, lead_id)[0] == 200

    partner_token = _login(call_api, partner_user)
    status, _ = _registration(
        call_api, partner_token, lead_id,
        action="undo_reject", operation_id=f"undo-partner-{lead_id}",
    )

    assert status == 403
    assert _lead_row(lead_id)["registration_status"] == "rejected"


def test_undo_reject_then_accept_puts_the_lead_into_the_pipeline(
    call_api, sample_lead, admin_user
):
    """The undo is not a dead end: the normal decisions work again after it."""
    token = _login(call_api, admin_user)
    lead_id = sample_lead["id"]
    assert _reject(call_api, token, lead_id)[0] == 200
    assert _registration(call_api, token, lead_id, action="undo_reject",
                         operation_id=f"undo-{lead_id}")[0] == 200

    status, body = _registration(call_api, token, lead_id, action="accept")

    assert status == 200, body
    row = _lead_row(lead_id)
    assert row["registration_status"] == "accepted"
    assert row["status"] is not None
