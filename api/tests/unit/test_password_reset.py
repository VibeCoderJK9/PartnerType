"""Self-service recovery: the link must be single use, short-lived, and must
never tell a stranger whether an address has an account here."""
import pytest

from api import password_reset
from api.auth import login
from api.db import get_conn
from api.password_reset import (
    complete_reset, handle_complete, handle_request, request_reset,
)


class _Handler:
    def __init__(self, ip="198.51.100.9"):
        self.client_address = (ip, 4444)


def _reset_rows(user_id):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT used_at, expires_at FROM password_resets WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()


class TestRequesting:
    def test_an_unknown_address_gets_the_same_answer_as_a_known_one(self, partner_user):
        known = handle_request(_Handler(), {"email": partner_user["email"]})
        unknown = handle_request(_Handler(), {"email": "nikt@example.test"})

        assert known == unknown

    def test_only_a_real_account_actually_gets_a_token(self, partner_user):
        assert request_reset("nikt@example.test") is None
        assert request_reset(partner_user["email"]) is not None

    def test_a_deactivated_account_cannot_be_recovered_this_way(self, partner_user):
        conn = get_conn()
        try:
            conn.execute("UPDATE users SET active = 0 WHERE id = ?", (partner_user["id"],))
            conn.commit()
        finally:
            conn.close()

        assert request_reset(partner_user["email"]) is None

    def test_a_flood_of_requests_stops_after_three(self, partner_user, mocker):
        send = mocker.patch("api.email_service.send_password_reset")
        for _ in range(password_reset.MAX_RESETS_PER_HOUR):
            assert request_reset(partner_user["email"]) is not None

        assert request_reset(partner_user["email"]) is None
        assert send.call_count == password_reset.MAX_RESETS_PER_HOUR

    def test_the_link_carries_the_raw_secret_and_the_configured_address(
        self, partner_user, monkeypatch, mocker
    ):
        monkeypatch.setenv("PARTNERTYPE_BASE_URL", "https://a.localhost:8443")
        mocker.patch("api.email_service.send_password_reset")

        token = request_reset(partner_user["email"])

        assert password_reset.build_reset_link(token) == (
            f"https://a.localhost:8443/set-password.html?token={token}&reset=1"
        )


class TestRedeeming:
    def test_the_new_password_works_and_the_old_one_stops_working(self, partner_user, mocker):
        mocker.patch("api.email_service.send_password_reset")
        token = request_reset(partner_user["email"])

        complete_reset(token, "Nowe-Haslo-123")

        assert login(partner_user["email"], "Nowe-Haslo-123", "partner")
        with pytest.raises(ValueError):
            login(partner_user["email"], partner_user["plain_password"], "partner")

    def test_every_existing_session_dies_with_the_reset(self, partner_user, mocker):
        """If somebody else was holding a stolen session, the reset is what
        throws them out -- otherwise changing the password achieves nothing."""
        from api.auth import get_session_user
        mocker.patch("api.email_service.send_password_reset")
        _user, stolen = login(partner_user["email"], partner_user["plain_password"], "partner")
        token = request_reset(partner_user["email"])

        complete_reset(token, "Nowe-Haslo-123")

        assert get_session_user(stolen) is None

    def test_the_link_works_exactly_once(self, partner_user, mocker):
        mocker.patch("api.email_service.send_password_reset")
        token = request_reset(partner_user["email"])
        complete_reset(token, "Nowe-Haslo-123")

        with pytest.raises(ValueError):
            complete_reset(token, "Jeszcze-Inne-123")

    def test_an_older_unused_link_is_spent_by_a_newer_one(self, partner_user, mocker):
        mocker.patch("api.email_service.send_password_reset")
        first = request_reset(partner_user["email"])
        second = request_reset(partner_user["email"])

        complete_reset(second, "Nowe-Haslo-123")

        with pytest.raises(ValueError):
            complete_reset(first, "Trzecie-Haslo-123")

    def test_an_expired_link_is_refused(self, partner_user, mocker):
        mocker.patch("api.email_service.send_password_reset")
        token = request_reset(partner_user["email"])
        conn = get_conn()
        try:
            conn.execute("UPDATE password_resets SET expires_at = '2020-01-01T00:00:00'")
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(ValueError):
            complete_reset(token, "Nowe-Haslo-123")

    def test_a_made_up_token_is_refused_without_saying_why(self, partner_user):
        status, body = handle_complete(_Handler(), "zmyslony-token-ktory-nie-istnieje", {"password": "Nowe-Haslo-123"})

        assert status == 400
        assert "nieprawidłowy" in body["error"].lower()

    def test_a_weak_password_is_refused_and_the_link_survives(self, partner_user, mocker):
        mocker.patch("api.email_service.send_password_reset")
        token = request_reset(partner_user["email"])

        with pytest.raises(ValueError):
            complete_reset(token, "krotkie")

        # The person mistyped; making them ask for a new mail would be cruel.
        assert complete_reset(token, "Poprawne-Haslo-123")

    def test_the_reset_clears_a_lockout_left_by_the_failed_attempts(self, partner_user, mocker):
        """The usual reason for resetting is having failed to log in, so the
        limiter must not keep the door shut afterwards."""
        from api.auth import LOGIN_MAX_FAILURES_PER_EMAIL
        mocker.patch("api.email_service.send_password_reset")
        for _ in range(LOGIN_MAX_FAILURES_PER_EMAIL):
            with pytest.raises(ValueError):
                login(partner_user["email"], "ZleHaslo", "partner", "198.51.100.9")
        token = request_reset(partner_user["email"])

        complete_reset(token, "Nowe-Haslo-123")

        assert login(partner_user["email"], "Nowe-Haslo-123", "partner", "198.51.100.9")


def test_plain_text_mails_keep_polish_characters(monkeypatch):
    # The plain-text parts keep the Polish diacritics: MIME parts are UTF-8.
    from api import email_service

    sent = []
    monkeypatch.setattr(email_service, "send", lambda to, subject, html, text: sent.append((subject, text)) or {"sent": True})

    email_service.send_partner_invitation("p@example.test", "Broker Sklep", "Softwarehouse Testlab",
                                          "https://x.example/set?t=1")
    email_service.send_password_reset("p@example.test", "Broker", "https://x.example/reset")

    assert len(sent) == 2
    invite_subject, invite_text = sent[0]
    # The partner joins the producer's programme, not "PartnerType's".
    assert "Softwarehouse Testlab" in invite_subject
    assert "programu partnerskiego PartnerType" not in invite_text
    for subject, text in sent:
        for ascii_only in ("haslo", "wazny", "wlasne", "otrzymalismy", "Cie "):
            assert ascii_only not in text, (ascii_only, text)
