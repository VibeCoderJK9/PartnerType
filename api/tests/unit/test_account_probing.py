"""
PartnerType -- the public forms do not say which addresses have an account.

Login, password reset and partner registration all promise that probing them
tells an attacker nothing. That has to hold for the response time and the
message, not only for the status code.
"""
import threading

import pytest

from api import email_service, password_reset
from api.auth import login
from api.db import get_conn
from api.users import handle_register

_REAL_RUN_IN_BACKGROUND = email_service.run_in_background


class _Handler:
    def __init__(self, ip="198.51.100.40"):
        self.client_address = (ip, 5555)


def test_an_unknown_address_costs_the_same_bcrypt_check_as_a_known_one(test_db_path, mocker):
    checkpw = mocker.spy(__import__("bcrypt"), "checkpw")

    with pytest.raises(ValueError):
        login("nikt-taki@example.test", "Jakies-Haslo-1", "partner", "198.51.100.41")

    assert checkpw.call_count == 1


def test_the_reset_answer_does_not_wait_for_the_lookup_or_the_mail(test_db_path, mocker):
    mocker.patch("api.email_service.run_in_background", new=_REAL_RUN_IN_BACKGROUND)
    release, finished = threading.Event(), threading.Event()

    def slow_request(email, ip=""):
        release.wait(timeout=10)
        finished.set()

    mocker.patch("api.password_reset.request_reset", side_effect=slow_request)

    status, body = password_reset.handle_request(_Handler(), {"email": "ktos@example.test"})

    # Answered while the account work is still blocked.
    assert status == 200
    assert body == {"message": password_reset.t(password_reset._GENERIC_ANSWER)}
    assert not finished.is_set()
    release.set()
    assert finished.wait(timeout=10)


REGISTRATION = {
    "name": "Nowy Partner", "email": "zajety@example.test", "password": "ValidPass123!",
    "company": "Acme", "phone": "+48 600 100 200",
}


def test_registering_a_taken_address_answers_like_a_new_registration(test_db_path, mocker):
    notice = mocker.patch("api.email_service.send_existing_account_notice")
    first = handle_register(_Handler("198.51.100.42"), dict(REGISTRATION))
    second = handle_register(_Handler("198.51.100.43"), dict(REGISTRATION, name="Ktos Inny"))

    assert first == second
    assert first[0] == 201
    # The owner of the address is told instead, and no second account exists.
    notice.assert_called_once()
    assert notice.call_args.args[0] == "zajety@example.test"
    conn = get_conn()
    try:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE email = ?", ("zajety@example.test",)
        ).fetchone()["n"] == 1
    finally:
        conn.close()


def test_the_existing_account_notice_names_the_sign_in_page(monkeypatch):
    sent = []
    monkeypatch.setattr(
        email_service, "send",
        lambda to, subject, html, text: sent.append((to, subject, text)) or {"sent": True})

    email_service.send_existing_account_notice(
        "p@example.test", "Broker", "https://x.example/index.html")

    (to, subject, text), = sent
    assert to == "p@example.test"
    assert "https://x.example/index.html" in text
