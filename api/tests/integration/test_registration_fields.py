"""
PartnerType -- Configurable registration fields.

Besides the mandatory name, company, e-mail and phone, the producer can
switch on up to five extra fields of at most 200 characters each for a
prospective partner to fill in.
"""
import pytest

from api import registration_fields
from api.db import get_conn
from api.registration_fields import MAX_FIELDS


def _login_token(call_api, user):
    _, body = call_api(
        "POST", "/api/auth/login",
        body={"email": user["email"], "password": user["plain_password"],
              "role": user["role"]},
    )
    return body["token"]


def _registration(**overrides):
    body = {
        "name": "Marek Zgłaszający",
        "email": "marek@test.pl",
        "password": "MarekPass123!",
        "company": "Zgłaszalnia Sp. z o.o.",
        "phone": "+48 600 100 200",
    }
    body.update(overrides)
    return body


class TestMandatoryIdentityFields:
    @pytest.mark.parametrize("missing", ["name", "company", "email", "phone"])
    def test_registration_without_one_of_the_four_is_refused(self, call_api, missing):
        """H1. `phone` is the one that changed: it used to be optional, which
        left a producer with no way to reach a partner who mistyped their
        address."""
        body = _registration()
        body.pop(missing)

        status, response = call_api("POST", "/api/auth/register", body=body)

        assert status == 400, response
        assert "error" in response


class TestFieldConfiguration:
    def test_an_admin_can_configure_up_to_five_and_no_more(self, call_api, admin_user):
        token = _login_token(call_api, admin_user)

        for index in range(MAX_FIELDS):
            status, body = call_api(
                "POST", "/api/registration-fields",
                body={"label": f"Pytanie {index + 1}"}, token=token,
            )
            assert status == 201, body

        status, body = call_api(
            "POST", "/api/registration-fields",
            body={"label": "Jedno za dużo"}, token=token,
        )
        assert status == 400
        assert str(MAX_FIELDS) in body["error"]

    def test_a_moderator_may_configure_them_and_a_partner_may_not(
        self, call_api, admin_user, partner_user
    ):
        partner_token = _login_token(call_api, partner_user)
        status, _ = call_api(
            "POST", "/api/registration-fields",
            body={"label": "Nielegalne"}, token=partner_token,
        )
        assert status == 403

    def test_a_field_starts_switched_off_so_configuring_it_never_surprises_a_registrant(
        self, call_api, admin_user
    ):
        token = _login_token(call_api, admin_user)

        _, created = call_api(
            "POST", "/api/registration-fields",
            body={"label": "NIP firmy"}, token=token,
        )

        assert created["field"]["active"] == 0
        assert registration_fields.get_active() == []


class TestPublicForm:
    def test_the_form_reads_only_active_questions_and_needs_no_account(
        self, call_api, admin_user
    ):
        token = _login_token(call_api, admin_user)
        _, on = call_api("POST", "/api/registration-fields",
                         body={"label": "Widoczne"}, token=token)
        call_api("POST", "/api/registration-fields",
                 body={"label": "Ukryte"}, token=token)
        call_api("PATCH", f"/api/registration-fields/{on['field']['id']}",
                 body={"active": True}, token=token)

        # No token: a person registering has no account yet, by definition.
        status, body = call_api("GET", "/api/registration-fields/public")

        assert status == 200
        assert [f["label"] for f in body["fields"]] == ["Widoczne"]

    def test_the_english_wording_travels_with_the_question_to_the_public_form(
        self, call_api, admin_user
    ):
        """A partner registering in English reads the producer's own question.
        There is nowhere else the English could come from, so it rides on the
        same row -- and an empty one means the Polish question stands."""
        token = _login_token(call_api, admin_user)
        _, created = call_api(
            "POST", "/api/registration-fields",
            body={"label": "Co Cię skłoniło do rejestracji?",
                  "label_en": "What brought you here?"},
            token=token,
        )
        call_api("PATCH", f"/api/registration-fields/{created['field']['id']}",
                 body={"active": True}, token=token)

        status, body = call_api("GET", "/api/registration-fields/public")

        assert status == 200
        assert body["fields"][0]["label_en"] == "What brought you here?"

        call_api("PATCH", f"/api/registration-fields/{created['field']['id']}",
                 body={"label_en": ""}, token=token)
        _, cleared = call_api("GET", "/api/registration-fields/public")
        assert cleared["fields"][0]["label_en"] == ""

    def test_the_public_read_exposes_no_answers(self, call_api, admin_user):
        token = _login_token(call_api, admin_user)
        _, created = call_api("POST", "/api/registration-fields",
                              body={"label": "Widoczne"}, token=token)
        field_id = created["field"]["id"]
        call_api("PATCH", f"/api/registration-fields/{field_id}",
                 body={"active": True}, token=token)
        call_api("POST", "/api/auth/register",
                 body=_registration(custom_fields={str(field_id): "Sekret"}))

        _, body = call_api("GET", "/api/registration-fields/public")

        assert all("value" not in f for f in body["fields"])


class TestAnswers:
    def _active_field(self, call_api, admin_user, label="NIP firmy"):
        token = _login_token(call_api, admin_user)
        _, created = call_api("POST", "/api/registration-fields",
                              body={"label": label}, token=token)
        field_id = created["field"]["id"]
        call_api("PATCH", f"/api/registration-fields/{field_id}",
                 body={"active": True}, token=token)
        return field_id

    def _answers_for(self, email):
        conn = get_conn()
        try:
            row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        finally:
            conn.close()
        return registration_fields.get_answers(row["id"])

    def test_an_answer_is_stored_against_the_new_account(self, call_api, admin_user):
        field_id = self._active_field(call_api, admin_user)

        status, _ = call_api(
            "POST", "/api/auth/register",
            body=_registration(custom_fields={str(field_id): "1234567890"}),
        )

        assert status == 201
        assert [a["value"] for a in self._answers_for("marek@test.pl")] == ["1234567890"]

    def test_an_answer_to_a_question_switched_off_mid_form_is_ignored_not_refused(
        self, call_api, admin_user
    ):
        """A producer changing their mind between a form being rendered and
        submitted must not cost a stranger their registration."""
        token = _login_token(call_api, admin_user)
        _, created = call_api("POST", "/api/registration-fields",
                              body={"label": "Wyłączone"}, token=token)
        field_id = created["field"]["id"]

        status, _ = call_api(
            "POST", "/api/auth/register",
            body=_registration(custom_fields={str(field_id): "cokolwiek"}),
        )

        assert status == 201
        assert self._answers_for("marek@test.pl") == []

    def test_an_answer_longer_than_the_field_allows_is_refused(
        self, call_api, admin_user
    ):
        field_id = self._active_field(call_api, admin_user)

        status, _ = call_api(
            "POST", "/api/auth/register",
            body=_registration(custom_fields={str(field_id): "x" * 201}),
        )

        # The account is still created --- the answer is what fails, and a
        # producer's optional question must never block registration.
        assert status == 201
        assert self._answers_for("marek@test.pl") == []

    def test_deleting_a_field_takes_its_answers_with_it(self, call_api, admin_user):
        field_id = self._active_field(call_api, admin_user)
        call_api("POST", "/api/auth/register",
                 body=_registration(custom_fields={str(field_id): "1234567890"}))
        token = _login_token(call_api, admin_user)

        call_api("DELETE", f"/api/registration-fields/{field_id}", token=token)

        # Keeping answers to a question nobody can see any more would be
        # personal data with no purpose left.
        assert self._answers_for("marek@test.pl") == []
