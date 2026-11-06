"""
PartnerType -- Client-identity normalization and duplicate-queue unit tests.

Covers the NIP/website normalizers in api/validation.py and the
duplicate-identity queueing behavior added to api/leads.py::create_lead. Uses the shared partner_user fixture from api/tests/conftest.py.
"""
import pytest

from api.db import get_conn
from api.leads import create_lead, precheck_duplicate
from api.validation import parse_nip, parse_website


def _third_partner():
    """A third active partner, for queues longer than the two shared fixtures."""
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO users (email, password, name, role, level, active)
               VALUES ('partner.third@test.pl', 'x', 'Third Partner', 'partner', 'bronze', 1)"""
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM users WHERE email = 'partner.third@test.pl'").fetchone())
    finally:
        conn.close()


def _reject(lead_id):
    conn = get_conn()
    try:
        conn.execute("UPDATE leads SET registration_status = 'rejected' WHERE id = ?", (lead_id,))
        conn.commit()
    finally:
        conn.close()


def _lead_data(**overrides):
    data = {
        "client_name": "Anna Testowa",
        "client_email": "anna@example.pl",
        "company": "Testowa Sp. z o.o.",
        "value": 10000,
    }
    data.update(overrides)
    return data


# ─── parse_nip ──────────────────────────────────────────────────────────────

class TestParseNip:
    def test_bare_ten_digit_nip_is_accepted_unchanged(self):
        assert parse_nip("1111111111") == "1111111111"

    def test_formatted_nip_with_pl_prefix_spaces_and_hyphens_normalizes_the_same(self):
        assert parse_nip(" pl 11-111-111-11 ") == "1111111111"

    def test_uppercase_pl_prefix_is_also_accepted(self):
        assert parse_nip("PL1111111111") == "1111111111"

    def test_invalid_checksum_is_rejected_with_polish_message(self):
        with pytest.raises(ValueError, match="NIP"):
            parse_nip("1234567890")

    @pytest.mark.parametrize("value", [None, 123, "123456789", "12345678901", "abcdefghij"])
    def test_wrong_type_or_length_is_rejected(self, value):
        with pytest.raises(ValueError):
            parse_nip(value)


# ─── parse_website ──────────────────────────────────────────────────────────

class TestParseWebsite:
    def test_bare_domain_is_accepted_unchanged(self):
        assert parse_website("nova-sklep.pl") == "nova-sklep.pl"

    def test_scheme_www_and_path_are_stripped_and_case_is_folded(self):
        assert parse_website("https://WWW.Nova-Sklep.PL/kontakt") == "nova-sklep.pl"

    def test_http_scheme_is_also_stripped(self):
        assert parse_website("http://nova-sklep.pl") == "nova-sklep.pl"

    @pytest.mark.parametrize("value", [None, 123, "not a domain", "no-dot", ""])
    def test_wrong_type_or_shape_is_rejected(self, value):
        with pytest.raises(ValueError):
            parse_website(value)


# ─── Duplicate detection + queue_position (create_lead) ─────────────────────

class TestDuplicateQueueing:
    def test_second_submission_with_same_nip_gets_queue_position_two(
        self, partner_user, partner_user_bronze
    ):
        first = create_lead(partner_user, _lead_data(nip="1111111111"))
        second = create_lead(
            partner_user_bronze, _lead_data(client_name="Other Contact", nip=" pl 11-111-111-11 ")
        )

        assert first["queue_position"] == 1
        assert second["queue_position"] == 2
        assert first["nip"] == second["nip"] == "1111111111"

    def test_second_submission_with_same_website_variant_gets_queue_position_two(
        self, partner_user, partner_user_bronze
    ):
        first = create_lead(partner_user, _lead_data(website="nova-sklep.pl"))
        second = create_lead(
            partner_user_bronze,
            _lead_data(client_name="Other Contact", website="https://WWW.Nova-Sklep.PL/kontakt"),
        )

        assert first["queue_position"] == 1
        assert second["queue_position"] == 2
        assert first["website"] == second["website"] == "nova-sklep.pl"

    def test_third_submission_for_the_same_client_gets_queue_position_three(
        self, partner_user, partner_user_bronze
    ):
        create_lead(partner_user, _lead_data(nip="1111111111"))
        create_lead(partner_user_bronze, _lead_data(nip="1111111111"))
        third = create_lead(_third_partner(), _lead_data(nip="1111111111"))

        assert third["queue_position"] == 3

    def test_a_rejected_submission_takes_no_place_in_the_queue(
        self, partner_user, partner_user_bronze
    ):
        # A rejected duplicate must not push the next partner from
        # position 2 (50 %) to position 3 (25 %).
        create_lead(partner_user, _lead_data(nip="1111111111"))
        rejected = create_lead(partner_user_bronze, _lead_data(nip="1111111111"))
        _reject(rejected["id"])

        third = create_lead(_third_partner(), _lead_data(nip="1111111111"))

        assert third["queue_position"] == 2

    def test_a_partner_cannot_queue_behind_his_own_submission(self, partner_user):
        first = create_lead(partner_user, _lead_data(nip="1111111111"))

        with pytest.raises(ValueError, match=first["id"]):
            create_lead(partner_user, _lead_data(client_name="Again", nip="1111111111"))

    def test_precheck_names_the_partners_own_lead_instead_of_a_queue_place(self, partner_user):
        first = create_lead(partner_user, _lead_data(nip="1111111111"))

        result = precheck_duplicate(partner_user, {"nip": "1111111111"})

        assert result["duplicate"] is True
        assert result["own_lead_id"] == first["id"]
        assert result["queue_position"] is None

    def test_a_partner_may_resubmit_a_client_after_his_lead_was_rejected(self, partner_user):
        first = create_lead(partner_user, _lead_data(nip="1111111111"))
        _reject(first["id"])

        again = create_lead(partner_user, _lead_data(nip="1111111111"))

        assert again["queue_position"] == 1

    def test_unrelated_leads_each_get_queue_position_one(self, partner_user):
        first = create_lead(partner_user, _lead_data(nip="1111111111"))
        second = create_lead(partner_user, _lead_data(nip="5260250274", website="inny-sklep.pl"))

        assert first["queue_position"] == 1
        assert second["queue_position"] == 1

    def test_leads_without_nip_or_website_default_to_empty_identity_and_queue_position_one(
        self, partner_user
    ):
        first = create_lead(partner_user, _lead_data())
        second = create_lead(partner_user, _lead_data())

        assert first["nip"] == first["website"] == ""
        assert first["queue_position"] == 1
        assert second["queue_position"] == 1

    def test_invalid_nip_on_create_is_rejected_with_polish_message(
        self, partner_user
    ):
        with pytest.raises(ValueError, match="NIP"):
            create_lead(partner_user, _lead_data(nip="1234567890"))


class TestHumanFieldNamesInErrors:
    """Errors name fields the way a person reads them, never "Field 'value'"
    or "Pole 'client_email'", and a quantity of 0 gets a correct message."""

    def _submit(self, partner_user, **overrides):
        from api.leads import submit_lead
        data = {"client_name": "Jan", "client_email": "jan@firma.pl", "company": "Firma",
                "value": 1000, "nip": "5260250274", "website": "firma.pl",
                "product_id": 1, "quantity": 1}
        data.update(overrides)
        return submit_lead(partner_user, data)

    def test_quantity_zero_says_at_least_one(self, partner_user):
        with pytest.raises(ValueError, match="co najmniej 1"):
            self._submit(partner_user, quantity=0)

    @pytest.mark.parametrize("field,bad,label", [
        ("value", -5, "Wartość"),
        ("client_email", "to-nie-mail", "E-mail klienta"),
    ])
    def test_messages_name_the_field_the_way_the_form_does(self, partner_user, field, bad, label):
        with pytest.raises(ValueError) as caught:
            self._submit(partner_user, **{field: bad})
        assert label in str(caught.value)
        assert f"'{field}'" not in str(caught.value)
