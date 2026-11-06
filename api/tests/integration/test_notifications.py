"""Executable contracts for NOTF-01..05 and notification trust boundaries."""

import json
import logging
import re
from email import message_from_string
from unittest.mock import MagicMock

import pytest

from api import email_service, notifications
from api.db import get_conn
from api.notifications import _SAFE_LINK_RE
from api.tests.integration.conftest import lead_payload


# Capture implementations before the autouse fixture replaces public side effects.
REAL_CREATE_NOTIFICATION = notifications.create_notification
REAL_NOTIFY_ALL_ADMINS = notifications.notify_all_admins
REAL_SEND_LEAD_CREATED = email_service.send_lead_created

# Copied from js/utils.js::SAFE_DESTINATION -- the client-side half of the
# same link contract. Kept as a literal (not imported; it is JS) so a drift
# between the two patterns fails this test instead of a producer's browser.
_CLIENT_SAFE_DESTINATION_RE = re.compile(
    r"^(?:\.\./){0,2}(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.html(?:[?#][^\s\\]*)?$"
)

_REGISTER_BODY = {
    "name": "Marek Zgłaszający",
    "email": "marek@test.pl",
    "password": "MarekPass123!",
    "company": "Zgłaszalnia Sp. z o.o.",
                "phone": "+48 600 100 200",
}


def _login_token(call_api, user):
    status, body = call_api(
        "POST",
        "/api/auth/login",
        body={
            "email": user["email"],
            "password": user["plain_password"],
            "role": user["role"],
        },
    )
    assert status == 200, body
    return body["token"]


def _notification_rows():
    conn = get_conn()
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM notifications ORDER BY id"
            ).fetchall()
        ]
    finally:
        conn.close()


def test_lead_submission_notifies_partner_active_admins_and_email(
    call_api, partner_user, admin_user, monkeypatch
):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO users (email, password, name, role, active) VALUES (?,?,?,?,0)",
            ("inactive-admin@example.pl", "unused", "Inactive", "admin"),
        )
        conn.commit()
    finally:
        conn.close()

    notifications.notify_all_admins.side_effect = REAL_NOTIFY_ALL_ADMINS
    token = _login_token(call_api, partner_user)

    status, body = call_api("POST", "/api/leads", body=lead_payload(), token=token)

    assert status == 201
    rows = _notification_rows()
    assert [(row["user_id"], row["type"]) for row in rows] == [
        (partner_user["id"], "info"),
        (admin_user["id"], "info"),
    ]
    assert body["lead"]["client_name"] in rows[0]["title"]
    assert rows[0]["link"] == "leads.html"
    email_service.send_lead_created.assert_called_once()


@pytest.mark.parametrize(
    "registration_value, expected_type, expected_fragment",
    [
        ("accepted", "success", "zaakceptowany"),
        ("rejected", "error", "odrzucony"),
        # The "supply more information" nudge is a registration value, not a
        # stage key, so deleting or reordering stages cannot silence it.
        ("needs_info", "info", "uzupełnij"),
    ],
)
def test_lead_registration_decision_notifies_owner_and_invokes_email(
    call_api,
    partner_user,
    admin_user,
    registration_value,
    expected_type,
    expected_fragment,
):
    notifications.create_notification.side_effect = REAL_CREATE_NOTIFICATION
    partner_token = _login_token(call_api, partner_user)
    admin_token = _login_token(call_api, admin_user)
    _, created = call_api(
        "POST", "/api/leads", body=lead_payload(), token=partner_token
    )

    status, _ = call_api(
        "PATCH",
        f"/api/leads/{created['lead']['id']}",
        body={
            "registration_status": registration_value,
            # Refusing or asking for more owes the partner words; acceptance
            # does not, and an ignored extra field would hide that rule.
            **({"registration_message": "Powód decyzji."}
               if registration_value in ("rejected", "needs_info") else {}),
        },
        token=admin_token,
    )

    assert status == 200
    owner_rows = [
        row for row in _notification_rows() if row["user_id"] == partner_user["id"]
    ]
    assert owner_rows[-1]["type"] == expected_type
    rendered = f"{owner_rows[-1]['title']} {owner_rows[-1]['body']}".lower()
    assert expected_fragment in rendered
    email_service.send_lead_status_changed.assert_called_once()


def test_notification_routes_are_caller_scoped_for_list_read_and_delete(
    call_api, partner_user, partner_user_bronze
):
    first_token = _login_token(call_api, partner_user)
    second_token = _login_token(call_api, partner_user_bronze)
    REAL_CREATE_NOTIFICATION(partner_user["id"], "info", "First", link="leads.html")
    REAL_CREATE_NOTIFICATION(
        partner_user_bronze["id"], "warning", "Second", link="leads.html"
    )
    rows = _notification_rows()
    first_id = next(row["id"] for row in rows if row["user_id"] == partner_user["id"])

    status, body = call_api("GET", "/api/notifications", token=second_token)
    assert status == 200
    assert [row["title"] for row in body["notifications"]] == ["Second"]

    status, _ = call_api(
        "POST", "/api/notifications/read", body={"id": first_id}, token=second_token
    )
    assert status == 200
    assert next(row for row in _notification_rows() if row["id"] == first_id)["is_read"] == 0

    status, _ = call_api(
        "DELETE", f"/api/notifications/{first_id}", token=second_token
    )
    assert status == 200
    assert any(row["id"] == first_id for row in _notification_rows())

    call_api("POST", "/api/notifications/read", body={"id": first_id}, token=first_token)
    assert next(row for row in _notification_rows() if row["id"] == first_id)["is_read"] == 1


@pytest.mark.parametrize(
    "type_, title, body, link",
    [
        ("unknown", "Title", "Body", "leads.html"),
        ("info", "x" * 201, "Body", "leads.html"),
        ("info", "Title", "x" * 1001, "leads.html"),
        ("info", "Title", "Body", "https://evil.example/steal"),
        ("info", "Title", "Body", "javascript:alert(1)"),
        ("info", "Title", "Body", "../leads.html"),
        ("info", "Title", "Body", "leads.html%0ajavascript:alert(1)"),
    ],
)
def test_notification_payload_rejects_invalid_fields_before_database(
    monkeypatch, type_, title, body, link
):
    monkeypatch.setattr(
        notifications,
        "get_conn",
        lambda: pytest.fail("notification database opened before validation"),
    )

    with pytest.raises(ValueError):
        REAL_CREATE_NOTIFICATION(1, type_, title, body, link)


def _operator_mail(monkeypatch, password="smtp-password"):
    """The operator's mailbox, as tools/instance.py hands it to an instance."""
    monkeypatch.setenv("PARTNERTYPE_ENV", "production")
    for key, value in (("HOST", "smtp.example.test"), ("PORT", "587"), ("USER", "mailer"),
                       ("PASSWORD", password), ("FROM", "noreply@example.test")):
        monkeypatch.setenv("PARTNERTYPE_SMTP_" + key, value)
    monkeypatch.delenv("PARTNERTYPE_SMTP_PASSWORD_FILE", raising=False)
    monkeypatch.setattr(email_service, "get", lambda key: "")


def test_smtp_enabled_uses_adapter_without_external_network(monkeypatch):
    _operator_mail(monkeypatch)
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    monkeypatch.setattr(email_service.smtplib, "SMTP", MagicMock(return_value=smtp))

    result = REAL_SEND_LEAD_CREATED(
        "partner@example.pl", "<Partner>", "LD-TEST", "<Client>"
    )

    assert result == {"sent": True}
    email_service.smtplib.SMTP.assert_called_once_with(
        "smtp.example.test", 587, timeout=10
    )
    smtp.login.assert_called_once_with("mailer", "smtp-password")
    message = message_from_string(smtp.sendmail.call_args.args[2])
    # Found by type: the message is multipart/related with
    # the logo as its last part (api/email_layout.py).
    html_part = next(part for part in message.walk() if part.get_content_type() == "text/html")
    html_part = html_part.get_payload(decode=True).decode("utf-8")
    assert "&lt;Partner&gt;" in html_part
    assert "&lt;Client&gt;" in html_part


def test_smtp_disabled_is_deterministic_and_does_not_open_adapter(monkeypatch):
    monkeypatch.setenv("PARTNERTYPE_ENV", "production")
    monkeypatch.delenv("PARTNERTYPE_SMTP_HOST", raising=False)
    smtp = MagicMock()
    monkeypatch.setattr(email_service.smtplib, "SMTP", smtp)

    result = email_service.send("partner@example.pl", "Subject", "<p>Body</p>")

    assert result == {"sent": False, "reason": "smtp_disabled"}
    smtp.assert_not_called()


def test_smtp_error_is_correlated_and_redacts_exception_and_credentials(
    monkeypatch, caplog
):
    secret = "smtp-password-secret"
    _operator_mail(monkeypatch, password=secret)
    monkeypatch.setattr(
        email_service.smtplib,
        "SMTP",
        MagicMock(side_effect=RuntimeError(f"login failed password={secret}")),
    )

    with caplog.at_level(logging.ERROR):
        result = email_service.send("partner@example.pl", "Subject", "<p>Body</p>")

    rendered = " ".join(record.getMessage() for record in caplog.records)
    assert result["sent"] is False
    assert result["reason"] == "smtp_error"
    assert result["request_id"]
    assert secret not in json.dumps(result)
    assert secret not in rendered
    assert f"request_id={result['request_id']}" in rendered


# ─── Self-registration notifies the producer ────────────────


def test_registration_notifies_every_active_admin_and_no_partner(
    call_api, admin_user, partner_user
):
    """One notification row per active admin, none for a partner (mute_side_effects/conftest)."""
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO users (email, password, name, role, active) VALUES (?,?,?,?,0)",
            ("inactive-admin@example.pl", "unused", "Inactive Admin", "admin"),
        )
        conn.commit()
    finally:
        conn.close()

    notifications.notify_all_admins.side_effect = REAL_NOTIFY_ALL_ADMINS

    status, body = call_api("POST", "/api/auth/register", body=_REGISTER_BODY)

    assert status == 201, body
    rows = _notification_rows()
    admin_rows = [row for row in rows if row["user_id"] == admin_user["id"]]
    partner_rows = [row for row in rows if row["user_id"] == partner_user["id"]]
    assert len(admin_rows) == 1
    assert partner_rows == []


def test_registration_notification_names_registrant_and_company(call_api, admin_user):
    notifications.notify_all_admins.side_effect = REAL_NOTIFY_ALL_ADMINS

    status, body = call_api("POST", "/api/auth/register", body=_REGISTER_BODY)

    assert status == 201, body
    row = next(r for r in _notification_rows() if r["user_id"] == admin_user["id"])
    assert "partner" in row["title"].lower() or "oczekuje" in row["title"].lower()
    assert _REGISTER_BODY["name"] in row["body"]
    assert _REGISTER_BODY["company"] in row["body"]


def test_registration_notification_link_satisfies_server_and_client_patterns(
    call_api, admin_user
):
    notifications.notify_all_admins.side_effect = REAL_NOTIFY_ALL_ADMINS

    status, body = call_api("POST", "/api/auth/register", body=_REGISTER_BODY)

    assert status == 201, body
    row = next(r for r in _notification_rows() if r["user_id"] == admin_user["id"])
    link = row["link"]
    assert "status=pending" in link
    assert _SAFE_LINK_RE.fullmatch(link)
    assert _CLIENT_SAFE_DESTINATION_RE.fullmatch(link)


def test_registration_notification_type_is_warning(call_api, admin_user):
    notifications.notify_all_admins.side_effect = REAL_NOTIFY_ALL_ADMINS

    status, body = call_api("POST", "/api/auth/register", body=_REGISTER_BODY)

    assert status == 201, body
    row = next(r for r in _notification_rows() if r["user_id"] == admin_user["id"])
    assert row["type"] == "warning"


def test_registration_survives_a_failing_notification(call_api, admin_user):
    notifications.notify_all_admins.side_effect = RuntimeError("boom")

    status, body = call_api("POST", "/api/auth/register", body=_REGISTER_BODY)

    assert status == 201, body
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (_REGISTER_BODY["email"],)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["account_status"] == "pending"


def test_registration_with_no_active_admin_succeeds_and_notifies_nobody(call_api):
    notifications.notify_all_admins.side_effect = REAL_NOTIFY_ALL_ADMINS

    status, body = call_api("POST", "/api/auth/register", body=_REGISTER_BODY)

    assert status == 201, body
    assert _notification_rows() == []


class TestNotificationsAreTranslatable:
    """A notification is written when the event happens and read later --
    possibly by somebody who has since switched the app to English. So the
    row carries the sentence twice: the Polish text as written, and a
    dictionary key with its values for the browser to compose."""

    def test_a_notification_carries_its_key_and_values(self, call_api, partner_user,
                                                       admin_user, monkeypatch):
        notifications.create_notification.side_effect = REAL_CREATE_NOTIFICATION
        notifications.create_notification(
            partner_user["id"], "success", "Lead zaakceptowany: Anna",
            "Przewidywana prowizja: 700 PLN", "leads.html",
            title_key="ntf_lead_accepted_title",
            body_key="ntf_lead_accepted_body",
            params={"client": "Anna", "amount": "700"},
        )

        rows = notifications.list_notifications(partner_user["id"])

        assert rows[0]["title_key"] == "ntf_lead_accepted_title"
        assert rows[0]["params"] == {"client": "Anna", "amount": "700"}
        # The literal is still there: it is what a row written before this
        # migration has, and the only thing to fall back on.
        assert rows[0]["title"] == "Lead zaakceptowany: Anna"

    def test_a_row_written_before_this_still_reads(self, partner_user):
        """No key, no values -- the stored sentence is the whole record."""
        notifications._insert_notification(
            partner_user["id"], "info", "Stare powiadomienie", "Bez kluczy", "leads.html"
        )

        rows = notifications.list_notifications(partner_user["id"])

        assert rows[0]["title_key"] == ""
        assert rows[0]["params"] == {}
        assert rows[0]["title"] == "Stare powiadomienie"

    @pytest.mark.parametrize("params", [
        {"client": {"nested": "object"}},
        {"client": ["list"]},
        {"bad name": "value"},
        {"client": True},
    ])
    def test_values_that_are_not_simple_text_are_refused(self, partner_user, params):
        notifications.create_notification.side_effect = REAL_CREATE_NOTIFICATION
        with pytest.raises(ValueError):
            notifications.create_notification(
                partner_user["id"], "info", "Tytuł", "Treść", "leads.html",
                title_key="ntf_lead_accepted_title", params=params,
            )

    def test_a_key_that_is_not_an_identifier_is_refused(self, partner_user):
        notifications.create_notification.side_effect = REAL_CREATE_NOTIFICATION
        with pytest.raises(ValueError):
            notifications.create_notification(
                partner_user["id"], "info", "Tytuł", "Treść", "leads.html",
                title_key="<script>alert(1)</script>",
            )


class TestBonusGrantNotificationSnapshot:
    """a bonus grant notification carries `bonus` AND `bonus_en`
    from the SAME definition read the award itself snapshots -- so a partner
    reading the notification later, in either language, sees exactly what
    they were told at the moment it was granted, never a rename that
    happened afterwards."""

    def _definition(self, name="Nagroda", name_en="", value=500.0):
        conn = get_conn()
        try:
            cursor = conn.execute(
                "INSERT INTO bonus_definitions (name, name_en, type, default_value, "
                "description, sort_order, active, created_at) "
                "VALUES (?, ?, 'cash', ?, '', 0, 1, '2026-09-01')",
                (name, name_en, value),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def test_the_notification_carries_both_language_snapshots(
        self, admin_user, partner_user
    ):
        from api import bonus_awards
        notifications.create_notification.side_effect = REAL_CREATE_NOTIFICATION
        definition = self._definition(
            name="Nagroda kwartalna", name_en="Quarterly award", value=250
        )

        bonus_awards.grant([partner_user["id"]], definition, admin_user)

        rows = notifications.list_notifications(partner_user["id"])
        assert rows[0]["title_key"] == "ntf_bonus_granted_title"
        assert rows[0]["params"]["bonus"] == "Nagroda kwartalna"
        assert rows[0]["params"]["bonus_en"] == "Quarterly award"

    def test_an_untranslated_definition_snapshots_an_empty_english_name(
        self, admin_user, partner_user
    ):
        from api import bonus_awards
        notifications.create_notification.side_effect = REAL_CREATE_NOTIFICATION
        definition = self._definition(name="Nagroda")

        bonus_awards.grant([partner_user["id"]], definition, admin_user)

        rows = notifications.list_notifications(partner_user["id"])
        assert rows[0]["params"]["bonus_en"] == ""

    def test_renaming_the_definition_afterwards_does_not_change_the_notification(
        self, admin_user, partner_user
    ):
        from api import bonus_awards
        notifications.create_notification.side_effect = REAL_CREATE_NOTIFICATION
        definition = self._definition(
            name="Nagroda za webinar", name_en="Webinar award"
        )

        bonus_awards.grant([partner_user["id"]], definition, admin_user)

        conn = get_conn()
        try:
            conn.execute(
                "UPDATE bonus_definitions SET name = 'Coś innego', "
                "name_en = 'Something else' WHERE id = ?",
                (definition,),
            )
            conn.commit()
        finally:
            conn.close()

        rows = notifications.list_notifications(partner_user["id"])
        assert rows[0]["params"]["bonus"] == "Nagroda za webinar"
        assert rows[0]["params"]["bonus_en"] == "Webinar award"
