"""
PartnerType -- Integration tests for the partner duplicate pre-check endpoint.

Drives POST /api/leads/precheck through the real router.dispatch path,
proving a partner can learn whether a client identity is already taken --
and their own prospective queue position and share -- without any other
submitter's data ever appearing in the response.
"""
from api.tests.integration.conftest import lead_payload


def _login_token(call_api, user):
    _, body = call_api(
        "POST",
        "/api/auth/login",
        body={
            "email": user["email"],
            "password": user["plain_password"],
            "role": user["role"],
        },
    )
    return body["token"]


def _submit(call_api, token, **overrides):
    """Create one real lead via POST /api/leads and return its response body."""
    status, body = call_api("POST", "/api/leads", body=lead_payload(**overrides), token=token)
    assert status == 201, body
    return body["lead"]


class TestNoDuplicate:
    def test_unknown_identity_reports_no_duplicate_and_position_one(
        self, call_api, partner_user
    ):
        token = _login_token(call_api, partner_user)
        status, body = call_api(
            "POST",
            "/api/leads/precheck",
            body={"nip": "5260001246", "website": "brand-new-client.pl"},
            token=token,
        )
        assert status == 200, body
        assert body["duplicate"] is False
        assert body["queue_position"] == 1
        assert body["policy"] == "queue"
        assert body["share"] == 100.0


class TestDuplicateReported:
    def test_existing_identity_reports_duplicate_position_two_and_its_share(
        self, call_api, partner_user, partner_user_bronze
    ):
        owner_token = _login_token(call_api, partner_user)
        checker_token = _login_token(call_api, partner_user_bronze)
        _submit(call_api, owner_token, nip="5260001246", website="acme-existing.pl")

        status, body = call_api(
            "POST",
            "/api/leads/precheck",
            body={"nip": "5260001246", "website": "acme-existing.pl"},
            token=checker_token,
        )
        assert status == 200, body
        assert body["duplicate"] is True
        assert body["queue_position"] == 2
        # Default lead_queue_shares = "100,50,25,10,0" -- position 2 is 50%.
        assert body["share"] == 50.0

    def test_response_discloses_no_other_submitters_field(
        self, call_api, partner_user, partner_user_bronze
    ):
        owner_token = _login_token(call_api, partner_user)
        checker_token = _login_token(call_api, partner_user_bronze)
        _submit(
            call_api,
            owner_token,
            nip="5260001246",
            website="acme-existing.pl",
            client_name="Secret Client Name",
            client_email="secret@client.pl",
            company="Secret Co Ltd",
        )

        status, body = call_api(
            "POST",
            "/api/leads/precheck",
            body={"nip": "5260001246", "website": "acme-existing.pl"},
            token=checker_token,
        )
        assert status == 200, body
        forbidden_keys = {
            "client_name", "client_email", "company", "partner_id",
            "partner_name", "id", "lead_id",
        }
        assert forbidden_keys.isdisjoint(body.keys())
        serialized = str(body)
        assert "Secret Client Name" not in serialized
        assert "secret@client.pl" not in serialized
        assert "Secret Co Ltd" not in serialized
        assert str(partner_user["id"]) not in serialized


class TestIdentityNormalization:
    def test_differently_formatted_identity_matches_its_normalized_form(
        self, call_api, partner_user, partner_user_bronze
    ):
        owner_token = _login_token(call_api, partner_user)
        checker_token = _login_token(call_api, partner_user_bronze)
        _submit(call_api, owner_token, nip="5260001246", website="nova-sklep.pl")

        status, body = call_api(
            "POST",
            "/api/leads/precheck",
            body={"nip": "PL 526-000-12-46", "website": "https://www.nova-sklep.pl"},
            token=checker_token,
        )
        assert status == 200, body
        assert body["duplicate"] is True
        assert body["queue_position"] == 2


class TestValidationAndAccess:
    def test_malformed_nip_returns_400_with_polish_message(self, call_api, partner_user):
        token = _login_token(call_api, partner_user)
        status, body = call_api(
            "POST",
            "/api/leads/precheck",
            body={"nip": "not-a-nip", "website": "example.pl"},
            token=token,
        )
        assert status == 400
        assert "NIP" in body["error"]

    def test_non_partner_caller_receives_403(self, call_api, admin_user):
        token = _login_token(call_api, admin_user)
        status, body = call_api(
            "POST",
            "/api/leads/precheck",
            body={"nip": "5260001246", "website": "example.pl"},
            token=token,
        )
        assert status == 403

    def test_unauthenticated_caller_receives_401(self, call_api):
        status, body = call_api(
            "POST",
            "/api/leads/precheck",
            body={"nip": "5260001246", "website": "example.pl"},
            token=None,
        )
        assert status == 401
