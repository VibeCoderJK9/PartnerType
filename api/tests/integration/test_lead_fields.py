"""
PartnerType -- producer-configurable lead fields.

The producer decides what a lead submission asks for beyond the fixed client
identity. Three things are load-bearing here and each has bitten this codebase
before:

  1. These are NOT the signup fields. `registration_fields` asks a prospective
     PARTNER about themselves and is keyed by user_id, limit five. Lead fields
     ask about a DEAL and are keyed by lead_id, limit fifty. Two leads filed by
     the same partner must never share an answer -- which is exactly what
     reusing the signup table would have caused.

  2. `0` and `false` are answers, not absences. A number field holding zero and
     a checkbox left unticked have to survive a round trip as themselves; the
     obvious `if not value` is wrong for both.

  3. A definition is never deleted, only archived. Answers already given stay
     readable, because a producer tidying up their form must not rewrite the
     history of deals already submitted.
"""
import json

import pytest

from api.db import get_conn


def _login(call_api, user):
    status, body = call_api("POST", "/api/auth/login", body={
        "email": user["email"], "password": user["plain_password"],
        "role": user["role"],
    })
    assert status == 200, body
    return body["token"]


def _create_field(call_api, token, **overrides):
    body = {"label": "Termin wdrożenia", "field_type": "text"}
    body.update(overrides)
    return call_api("POST", "/api/lead-fields", token=token, body=body)


def _values_for(lead_id):
    conn = get_conn()
    try:
        # Stored as JSON so a number comes back a number and a checkbox comes
        # back a boolean -- reparsing bare text on read is how 0 and false
        # turn into "empty".
        return {
            row["field_id"]: json.loads(row["value"])
            for row in conn.execute(
                "SELECT field_id, value FROM lead_field_values WHERE lead_id = ?",
                (lead_id,),
            ).fetchall()
        }
    finally:
        conn.close()


def _lead_payload(**overrides):
    from api.tests.integration.conftest import lead_payload
    return lead_payload(**overrides)


@pytest.fixture
def admin_token(call_api, admin_user):
    return _login(call_api, admin_user)


@pytest.fixture
def partner_token(call_api, partner_user):
    return _login(call_api, partner_user)


# ─── DEFINITIONS ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field_type", ["text", "number", "checkbox"])
def test_a_producer_defines_each_field_type(call_api, admin_token, field_type):
    status, body = _create_field(call_api, admin_token,
                                 label=f"Pole {field_type}", field_type=field_type)
    assert status in (200, 201), body
    assert body["field"]["field_type"] == field_type
    assert body["field"]["partner_editable"] is False, (
        "a new field is not partner-editable until the producer says so"
    )


def test_an_unknown_field_type_is_refused(call_api, admin_token):
    status, body = _create_field(call_api, admin_token, field_type="wykres")
    assert status == 400, body


def test_a_label_longer_than_the_limit_is_refused(call_api, admin_token):
    status, body = _create_field(call_api, admin_token, label="ł" * 121)
    assert status == 400, body


def test_a_label_at_the_limit_is_accepted(call_api, admin_token):
    status, body = _create_field(call_api, admin_token, label="ł" * 120)
    assert status in (200, 201), body


def test_a_field_carries_the_producers_own_english_label(call_api, admin_token):
    """The label is the producer's own wording, so the English version is a
    second column he fills in. Empty means "not translated yet"."""
    status, created = _create_field(call_api, admin_token,
                                    label="Branża", label_en="Industry")
    assert status in (200, 201), created
    assert created["field"]["label_en"] == "Industry"

    field_id = created["field"]["id"]
    status, updated = call_api("PATCH", f"/api/lead-fields/{field_id}",
                               token=admin_token, body={"label_en": ""})
    assert status == 200, updated
    assert updated["field"]["label_en"] == ""
    assert updated["field"]["label"] == "Branża"


def test_a_field_defined_without_an_english_label_reads_back_empty(
    call_api, admin_token
):
    _, created = _create_field(call_api, admin_token, label="Termin")
    assert created["field"]["label_en"] == ""


def test_a_partner_cannot_define_fields(call_api, partner_token):
    status, body = _create_field(call_api, partner_token)
    assert status == 403, body


def test_every_authenticated_role_may_read_the_schema(call_api, partner_token,
                                                      admin_token):
    _create_field(call_api, admin_token, label="Widoczne dla partnera")
    status, body = call_api("GET", "/api/lead-fields", token=partner_token)
    assert status == 200, body
    assert any(f["label"] == "Widoczne dla partnera" for f in body["fields"])


def test_the_active_field_limit_is_fifty_and_independent_of_signup(
    call_api, admin_token
):
    """Fifty lead fields, and the signup limit of five is a different rule
    entirely -- conflating them is what the separate scope prevents."""
    for index in range(50):
        status, body = _create_field(call_api, admin_token, label=f"Pole {index}")
        assert status in (200, 201), (index, body)

    status, body = _create_field(call_api, admin_token, label="Pięćdziesiąte pierwsze")
    assert status == 400, body

    # The signup form is untouched by any of that.
    status, body = call_api("POST", "/api/registration-fields", token=admin_token,
                            body={"label": "Pole rejestracji konta"})
    assert status in (200, 201), body


# ─── ANSWERS TRAVEL WITH THE LEAD ───────────────────────────────────────────

def _submit_with_values(call_api, partner_token, values, **lead_overrides):
    payload = _lead_payload(**lead_overrides)
    payload["lead_field_values"] = values
    return call_api("POST", "/api/leads", token=partner_token, body=payload)


def test_a_partner_answers_the_producers_questions_when_submitting(
    call_api, admin_token, partner_token
):
    _, created = _create_field(call_api, admin_token, label="Termin", field_type="text")
    field_id = created["field"]["id"]

    status, body = _submit_with_values(
        call_api, partner_token, {str(field_id): "Q1 2027"}
    )
    assert status == 201, body
    assert _values_for(body["lead"]["id"]) == {field_id: "Q1 2027"}


def test_two_leads_from_one_partner_keep_independent_answers(
    call_api, admin_token, partner_token
):
    """The failure the separate scope exists to prevent: answers keyed by
    partner rather than by deal would make the second submission overwrite the
    first."""
    _, created = _create_field(call_api, admin_token, label="Termin")
    field_id = created["field"]["id"]

    _, first = _submit_with_values(
        call_api, partner_token, {str(field_id): "Pierwszy lead"},
        nip="1111111111", website="pierwszy.pl")
    _, second = _submit_with_values(
        call_api, partner_token, {str(field_id): "Drugi lead"},
        nip="2222222222", website="drugi.pl")

    assert _values_for(first["lead"]["id"]) == {field_id: "Pierwszy lead"}
    assert _values_for(second["lead"]["id"]) == {field_id: "Drugi lead"}


def test_a_zero_and_an_unticked_box_are_answers_not_absences(
    call_api, admin_token, partner_token
):
    """`if not value` is wrong for both of these, which is why they get their
    own test rather than riding along with the text case."""
    _, number = _create_field(call_api, admin_token, label="Liczba", field_type="number")
    _, checkbox = _create_field(call_api, admin_token, label="Zgoda",
                                field_type="checkbox")

    status, body = _submit_with_values(call_api, partner_token, {
        str(number["field"]["id"]): 0,
        str(checkbox["field"]["id"]): False,
    })
    assert status == 201, body

    lead_id = body["lead"]["id"]
    status, detail = call_api("GET", f"/api/leads/{lead_id}", token=partner_token)
    assert status == 200, detail
    answers = {a["field_id"]: a["value"] for a in detail["lead"]["field_values"]}
    assert answers[number["field"]["id"]] == 0
    assert answers[checkbox["field"]["id"]] is False


def test_unicode_answers_round_trip(call_api, admin_token, partner_token):
    _, created = _create_field(call_api, admin_token, label="Notatka")
    field_id = created["field"]["id"]

    _, body = _submit_with_values(
        call_api, partner_token, {str(field_id): "żółć, ćma — 😀"})
    assert _values_for(body["lead"]["id"])[field_id] == "żółć, ćma — 😀"


def test_a_lead_submits_fine_when_the_producer_defined_nothing(
    call_api, partner_token
):
    """The base form has to keep working for a producer who never opened this
    screen."""
    status, body = call_api("POST", "/api/leads", token=partner_token,
                            body=_lead_payload())
    assert status == 201, body


# ─── VALUE VALIDATION ───────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["nie-liczba", float("nan"), float("inf"), True])
def test_a_number_field_refuses_what_is_not_a_finite_number(
    call_api, admin_token, partner_token, bad
):
    _, created = _create_field(call_api, admin_token, label="Liczba",
                               field_type="number")
    status, body = _submit_with_values(
        call_api, partner_token, {str(created["field"]["id"]): bad})
    assert status == 400, body


@pytest.mark.parametrize("bad", ["true", 1, 0, "tak"])
def test_a_checkbox_refuses_anything_but_a_real_boolean(
    call_api, admin_token, partner_token, bad
):
    """A truthy string is the classic way a "yes" becomes indistinguishable
    from the word "no"."""
    _, created = _create_field(call_api, admin_token, label="Zgoda",
                               field_type="checkbox")
    status, body = _submit_with_values(
        call_api, partner_token, {str(created["field"]["id"]): bad})
    assert status == 400, body


def test_text_over_four_thousand_code_points_is_refused(
    call_api, admin_token, partner_token
):
    _, created = _create_field(call_api, admin_token, label="Notatka")
    field_id = str(created["field"]["id"])

    status, _ = _submit_with_values(call_api, partner_token, {field_id: "😀" * 4000})
    assert status == 201, "four thousand code points is the limit, not 8000 UTF-16 units"

    status, body = _submit_with_values(
        call_api, partner_token, {field_id: "a" * 4001},
        nip="3333333333", website="zadluga.pl")
    assert status == 400, body


def test_a_required_field_must_be_answered(call_api, admin_token, partner_token):
    _, created = _create_field(call_api, admin_token, label="Obowiązkowe",
                               required=True)
    status, body = call_api("POST", "/api/leads", token=partner_token,
                            body=_lead_payload())
    assert status == 400, body


def test_a_required_number_is_satisfied_by_zero(call_api, admin_token, partner_token):
    """Zero answers the question. Treating it as "missing" is the same bug as
    treating false as unset."""
    _, created = _create_field(call_api, admin_token, label="Ile sztuk",
                               field_type="number", required=True)
    status, body = _submit_with_values(
        call_api, partner_token, {str(created["field"]["id"]): 0})
    assert status == 201, body


def test_an_unknown_field_id_is_refused_rather_than_ignored(
    call_api, partner_token
):
    """Silently dropping it would tell the partner their answer was saved."""
    status, body = _submit_with_values(call_api, partner_token, {"9999": "cokolwiek"})
    assert status == 400, body


def test_an_archived_field_cannot_be_answered_on_a_new_lead(
    call_api, admin_token, partner_token
):
    _, created = _create_field(call_api, admin_token, label="Wycofane")
    field_id = created["field"]["id"]
    status, body = call_api("PATCH", f"/api/lead-fields/{field_id}",
                            token=admin_token, body={"active": False})
    assert status == 200, body

    status, body = _submit_with_values(call_api, partner_token,
                                       {str(field_id): "za późno"})
    assert status == 400, body


def test_a_rejected_submission_stores_no_partial_answers(
    call_api, admin_token, partner_token
):
    """The lead and its answers are one write: a bad answer must not leave a
    lead behind with half its form filled in."""
    _, good = _create_field(call_api, admin_token, label="Dobre")
    _, number = _create_field(call_api, admin_token, label="Liczba",
                              field_type="number")

    conn = get_conn()
    try:
        before = conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"]
    finally:
        conn.close()

    status, _ = _submit_with_values(call_api, partner_token, {
        str(good["field"]["id"]): "zapisane?",
        str(number["field"]["id"]): "nie-liczba",
    })
    assert status == 400

    conn = get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"] == before
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM lead_field_values"
        ).fetchone()["n"] == 0
    finally:
        conn.close()


# ─── ARCHIVING KEEPS HISTORY ────────────────────────────────────────────────

def test_archiving_a_field_keeps_answers_already_given(
    call_api, admin_token, partner_token
):
    _, created = _create_field(call_api, admin_token, label="Stare pytanie")
    field_id = created["field"]["id"]
    _, submitted = _submit_with_values(call_api, partner_token,
                                       {str(field_id): "stara odpowiedź"})
    lead_id = submitted["lead"]["id"]

    call_api("PATCH", f"/api/lead-fields/{field_id}", token=admin_token,
             body={"active": False})

    assert _values_for(lead_id) == {field_id: "stara odpowiedź"}
    status, detail = call_api("GET", f"/api/leads/{lead_id}", token=partner_token)
    assert status == 200
    shown = {a["field_id"]: a for a in detail["lead"]["field_values"]}
    assert shown[field_id]["value"] == "stara odpowiedź"
    assert shown[field_id]["label"] == "Stare pytanie", (
        "the historical label has to survive, or the answer loses its question"
    )


def test_the_type_cannot_change_once_answers_exist(
    call_api, admin_token, partner_token
):
    """Reinterpreting stored text as a number would silently rewrite what
    partners already said. A new field is the honest way to change the
    question."""
    _, created = _create_field(call_api, admin_token, label="Kiedyś tekst")
    field_id = created["field"]["id"]
    _submit_with_values(call_api, partner_token, {str(field_id): "tekstowa odpowiedź"})

    status, body = call_api("PATCH", f"/api/lead-fields/{field_id}",
                            token=admin_token, body={"field_type": "number"})
    assert status in (400, 409), body

    conn = get_conn()
    try:
        assert conn.execute(
            "SELECT field_type FROM lead_fields WHERE id = ?", (field_id,)
        ).fetchone()["field_type"] == "text"
    finally:
        conn.close()


def test_the_type_may_change_while_no_answer_exists(call_api, admin_token):
    _, created = _create_field(call_api, admin_token, label="Jeszcze nieużyte")
    status, body = call_api("PATCH", f"/api/lead-fields/{created['field']['id']}",
                            token=admin_token, body={"field_type": "number"})
    assert status == 200, body


def test_renaming_a_field_keeps_answers_attached(
    call_api, admin_token, partner_token
):
    """Answers are linked by id, never by label."""
    _, created = _create_field(call_api, admin_token, label="Stara nazwa")
    field_id = created["field"]["id"]
    _, submitted = _submit_with_values(call_api, partner_token,
                                       {str(field_id): "odpowiedź"})

    call_api("PATCH", f"/api/lead-fields/{field_id}", token=admin_token,
             body={"label": "Nowa nazwa"})

    assert _values_for(submitted["lead"]["id"]) == {field_id: "odpowiedź"}


# ─── SCHEMA VERSION ─────────────────────────────────────────────────────────

def test_a_submission_against_a_stale_schema_is_refused(
    call_api, admin_token, partner_token
):
    """The producer changed the form in another tab while the partner was
    filling it in. Saving against the version they actually saw is the only
    way a required field added meanwhile cannot be silently skipped."""
    status, schema = call_api("GET", "/api/lead-fields", token=partner_token)
    assert status == 200
    stale_version = schema["version"]

    _create_field(call_api, admin_token, label="Dodane w międzyczasie",
                  required=True)

    payload = _lead_payload()
    payload["lead_fields_version"] = stale_version
    status, body = call_api("POST", "/api/leads", token=partner_token, body=payload)

    assert status == 409, body
    assert body.get("code") == "stale_lead_fields"


def test_a_submission_with_the_current_version_succeeds(
    call_api, admin_token, partner_token
):
    _create_field(call_api, admin_token, label="Pytanie")
    _, schema = call_api("GET", "/api/lead-fields", token=partner_token)

    payload = _lead_payload()
    payload["lead_fields_version"] = schema["version"]
    status, body = call_api("POST", "/api/leads", token=partner_token, body=payload)
    assert status == 201, body


def test_the_version_changes_when_the_schema_changes(call_api, admin_token):
    _, before = call_api("GET", "/api/lead-fields", token=admin_token)
    _create_field(call_api, admin_token, label="Nowe")
    _, after = call_api("GET", "/api/lead-fields", token=admin_token)
    assert before["version"] != after["version"]


# ─── SIGNUP SCOPE IS UNTOUCHED ──────────────────────────────────────────────

def test_lead_answers_are_not_exposed_through_the_public_signup_endpoint(
    call_api, admin_token, partner_token
):
    _, created = _create_field(call_api, admin_token, label="Poufne pytanie")
    _submit_with_values(call_api, partner_token,
                        {str(created["field"]["id"]): "poufna odpowiedź"})

    status, body = call_api("GET", "/api/registration-fields/public")
    assert status == 200, body
    rendered = str(body)
    assert "poufna odpowiedź" not in rendered
    assert "Poufne pytanie" not in rendered


def test_signup_answers_and_lead_answers_live_in_different_tables(
    call_api, admin_token, partner_token
):
    _, created = _create_field(call_api, admin_token, label="Pytanie o leada")
    _submit_with_values(call_api, partner_token,
                        {str(created["field"]["id"]): "o leadzie"})

    conn = get_conn()
    try:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM registration_field_values"
        ).fetchone()["n"] == 0, "a lead answer leaked into the signup table"
    finally:
        conn.close()
