"""With the requirement switched on, a producer account owing a second factor
gets no further than setting one up. Anything softer would make "required" a
word in a document rather than a rule in the code."""
import pyotp
import pytest

from api import mfa


@pytest.fixture(autouse=True)
def enforced(monkeypatch):
    monkeypatch.setenv("PARTNERTYPE_REQUIRE_MFA", "1")


def _token(call_api, user):
    status, body = call_api("POST", "/api/auth/login", body={
        "email": user["email"], "password": user["plain_password"], "role": "admin",
    })
    assert status == 200, body
    assert "mfa_required" not in body, "konto bez drugiego składnika loguje się zwykłą drogą"
    return body["token"]


class TestGate:
    def test_the_producer_cannot_reach_his_data_before_enrolling(self, call_api, admin_user):
        token = _token(call_api, admin_user)

        status, body = call_api("GET", "/api/leads", token=token)

        assert status == 403
        assert body["code"] == "mfa_setup_required"

    def test_but_he_can_still_enrol_and_see_who_he_is(self, call_api, admin_user):
        token = _token(call_api, admin_user)

        assert call_api("GET", "/api/auth/me", token=token)[0] == 200
        status, body = call_api("GET", "/api/auth/mfa", token=token)
        assert status == 200
        assert body["required"] is True
        assert call_api("POST", "/api/auth/mfa/begin", token=token)[0] == 200

    def test_once_enrolled_the_rest_of_the_application_opens_up(self, call_api, admin_user):
        token = _token(call_api, admin_user)
        _status, started = call_api("POST", "/api/auth/mfa/begin", token=token)

        status, body = call_api("POST", "/api/auth/mfa/confirm", token=token,
                                body={"code": pyotp.TOTP(started["secret"]).now()})

        assert status == 200
        assert len(body["backup_codes"]) == mfa.BACKUP_CODE_COUNT
        assert call_api("GET", "/api/leads", token=token)[0] == 200

    def test_a_partner_is_never_stopped_by_the_gate(self, call_api, partner_user):
        status, body = call_api("POST", "/api/auth/login", body={
            "email": partner_user["email"], "password": partner_user["plain_password"],
            "role": "partner",
        })

        assert call_api("GET", "/api/leads", token=body["token"])[0] == 200

    def test_a_required_role_cannot_switch_the_factor_off_again(self, call_api, admin_user):
        token = _token(call_api, admin_user)
        _status, started = call_api("POST", "/api/auth/mfa/begin", token=token)
        call_api("POST", "/api/auth/mfa/confirm", token=token,
                 body={"code": pyotp.TOTP(started["secret"]).now()})

        status, _body = call_api("POST", "/api/auth/mfa/disable", token=token,
                                 body={"code": pyotp.TOTP(started["secret"]).now()})

        assert status == 403


class TestOptionalByDefault:
    """The other half of the switch, which is now the default.

    With the requirement off, a producer reaches their data without a second
    factor and can take one off again, for example to set it up again on
    their own phone.
    """

    @pytest.fixture(autouse=True)
    def not_enforced(self, monkeypatch):
        monkeypatch.setenv("PARTNERTYPE_REQUIRE_MFA", "0")

    def test_the_producer_reaches_his_data_without_a_second_factor(self, call_api, admin_user):
        token = _token(call_api, admin_user)

        status, _body = call_api("GET", "/api/leads", token=token)

        assert status == 200

    def test_and_can_take_the_factor_off_again(self, call_api, admin_user):
        token = _token(call_api, admin_user)
        _status, started = call_api("POST", "/api/auth/mfa/begin", token=token)
        call_api("POST", "/api/auth/mfa/confirm", token=token,
                 body={"code": pyotp.TOTP(started["secret"]).now()})

        status, _body = call_api("POST", "/api/auth/mfa/disable", token=token,
                                 body={"code": pyotp.TOTP(started["secret"]).now()})

        assert status == 200
        status, body = call_api("GET", "/api/auth/mfa", token=token)
        assert body["enabled"] is False
        # And the way back in is open: a fresh enrolment may be started.
        assert call_api("POST", "/api/auth/mfa/begin", token=token)[0] == 200

    def test_a_wrong_code_does_not_take_the_factor_off(self, call_api, admin_user):
        token = _token(call_api, admin_user)
        _status, started = call_api("POST", "/api/auth/mfa/begin", token=token)
        call_api("POST", "/api/auth/mfa/confirm", token=token,
                 body={"code": pyotp.TOTP(started["secret"]).now()})

        status, _body = call_api("POST", "/api/auth/mfa/disable", token=token,
                                 body={"code": "000000"})

        assert status == 400
        assert call_api("GET", "/api/auth/mfa", token=token)[1]["enabled"] is True
