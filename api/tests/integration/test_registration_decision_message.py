"""Atomic status+public message on the existing staff update boundary."""
from psycopg import IntegrityError

import pytest

from api.db import get_conn
from api.leads import update_lead


@pytest.mark.parametrize("actor_fixture", ["admin_user", "manager_user"])
def test_decision_http_contract(call_api, sample_lead, request, actor_fixture):
    actor = request.getfixturevalue(actor_fixture)
    status, login = call_api("POST", "/api/auth/login", body={
        "email": actor["email"], "password": actor["plain_password"],
        "role": actor["role"],
    })
    assert status == 200
    endpoint = f"/api/leads/{sample_lead['id']}"
    status, body = call_api("PATCH", endpoint, token=login["token"], body={
        "registration_status": "needs_info", "registration_message": "Podaj telefon",
    })
    assert status == 200, body
    assert body["lead"]["registration_status"] == "needs_info"
    assert body["lead"]["status"] is None
    status, body = call_api("GET", endpoint + "/comments", token=login["token"])
    assert status == 200
    assert [(row["body"], row["user_id"]) for row in body["comments"]] == [
        ("Podaj telefon", actor["id"]),
    ]


@pytest.mark.parametrize("status", ["needs_info", "rejected"])
@pytest.mark.parametrize("role", ["admin", "manager"])
def test_decision_saves_message_with_state(sample_lead, request, status, role):
    # A real account of each role: update_lead re-reads the actor's role
    # under the lock, so a session relabelled in memory is refused.
    actor = request.getfixturevalue(f"{role}_user")
    result = update_lead(sample_lead["id"], actor, {
        "registration_status": status,
        "registration_message": "  Proszę o kontakt — żółć 😀  ",
    })
    assert result["registration_status"] == status
    assert result["status"] is None
    conn = get_conn()
    try:
        comments = conn.execute(
            "SELECT body, user_id, is_internal FROM lead_comments WHERE lead_id=?",
            (sample_lead["id"],),
        ).fetchall()
        assert [tuple(row.values()) for row in comments] == [
            ("Proszę o kontakt — żółć 😀", actor["id"], 0),
        ]
    finally:
        conn.close()


def test_comment_insert_failure_rolls_back_decision(sample_lead, admin_user, mocker, database_failure):
    database_failure('reject_decision_comment', 'lead_comments', 'injected comment failure')
    notify = mocker.patch("api.notifications.create_notification")
    audit = mocker.patch("api.audit.log")
    mail = mocker.patch("api.email_service.send_lead_status_changed")
    with pytest.raises(IntegrityError, match="injected comment failure"):
        update_lead(sample_lead["id"], admin_user, {
            "registration_status": "needs_info", "registration_message": "More details",
        })
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM leads WHERE id=?", (sample_lead["id"],)).fetchone()
        assert row["registration_status"] == sample_lead["registration_status"]
        assert row["updated_at"] == sample_lead["updated_at"]
        assert conn.execute("SELECT count(*) FROM lead_comments").fetchone()[0] == 0
    finally:
        conn.close()
    notify.assert_not_called()
    audit.assert_not_called()
    mail.assert_not_called()


@pytest.mark.parametrize("message", [None, "", "  ", True, 1, "x" * 4001])
def test_invalid_message_changes_nothing(sample_lead, admin_user, message):
    with pytest.raises(ValueError):
        update_lead(sample_lead["id"], admin_user, {
            "registration_status": "needs_info", "registration_message": message,
        })


def test_partner_cannot_make_registration_decision(sample_lead, partner_user):
    with pytest.raises(PermissionError):
        update_lead(sample_lead["id"], partner_user, {
            "registration_status": "rejected", "registration_message": "Not allowed",
        })


def test_message_requires_information_or_rejection_decision(sample_lead, admin_user):
    with pytest.raises(ValueError):
        update_lead(sample_lead["id"], admin_user, {"registration_message": "orphan"})
    with pytest.raises(ValueError):
        update_lead(sample_lead["id"], admin_user, {
            "registration_status": "accepted", "registration_message": "orphan",
        })
