"""Retry and authorization regressions for the versioned registration routes."""
from api.tests.context import carry_context
from concurrent.futures import ThreadPoolExecutor

import pytest

from api import leads
from api.db import get_conn


def decision(lead, actor, action="request_info", message="Podaj telefon", key="request-1"):
    return leads.apply_registration_action(
        lead["id"], actor, action, message,
        expected_revision=lead["revision"], operation_id=key,
    )


def counts(lead_id):
    conn = get_conn()
    try:
        return tuple(conn.execute(
            f"SELECT count(*) FROM {table} WHERE lead_id=?", (lead_id,),
        ).fetchone()[0] for table in (
            "lead_comments", "lead_workflow_events", "lead_workflow_operations",
        ))
    finally:
        conn.close()


@pytest.mark.parametrize("action", ["accept", "reject", "request_info"])
def test_exact_retry_returns_saved_result_without_repeating_effects(
    sample_lead, admin_user, mocker, action,
):
    notify = mocker.patch("api.leads._notify_registration")
    audit = mocker.patch("api.audit.log")
    message = None if action == "accept" else "Podaj telefon"
    first = decision(sample_lead, admin_user, action, message)
    before = counts(sample_lead["id"])
    again = decision(sample_lead, admin_user, action, message)
    assert again == first
    assert first["revision"] == sample_lead["revision"] + 1
    assert counts(sample_lead["id"]) == before
    notify.assert_called_once()
    audit.assert_called_once()


@pytest.mark.parametrize("use_new_revision", [False, True])
def test_key_cannot_be_reused_for_different_payload(sample_lead, admin_user, use_new_revision):
    first = decision(sample_lead, admin_user)
    before = counts(sample_lead["id"])
    snapshot = first if use_new_revision else sample_lead
    with pytest.raises(Exception) as failure:
        decision(snapshot, admin_user, message="Inna treść")
    assert getattr(failure.value, "code", None) == "operation_conflict"
    assert counts(sample_lead["id"]) == before


def test_response_retry_and_ownership_checked_before_replay(
    sample_lead, admin_user, partner_user, partner_user_bronze,
):
    requested = decision(sample_lead, admin_user)
    args = (sample_lead["id"], partner_user, "Numer: 123", requested["revision"], "answer-1")
    first = leads.respond_to_information(*args)
    assert leads.respond_to_information(*args) == first
    assert first["registration_status"] == "needs_info"
    assert counts(sample_lead["id"]) == (2, 2, 2)
    conn = get_conn()
    try:
        conn.execute("UPDATE leads SET partner_id=? WHERE id=?",
                     (partner_user_bronze["id"], sample_lead["id"]))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PermissionError):
        leads.respond_to_information(*args)


@pytest.mark.parametrize("change", ["active=0", "role='observer'"])
def test_current_actor_permission_checked_before_replay(sample_lead, admin_user, change):
    decision(sample_lead, admin_user)
    conn = get_conn()
    try:
        conn.execute(f"UPDATE users SET {change} WHERE id=?", (admin_user["id"],))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PermissionError):
        decision(sample_lead, admin_user)


def test_failed_notification_does_not_turn_committed_decision_into_failure(
    sample_lead, admin_user, mocker, caplog,
):
    notify = mocker.patch("api.leads._notify_registration", side_effect=RuntimeError("secret payload"))
    audit = mocker.patch("api.audit.log")
    first = decision(sample_lead, admin_user)
    assert first["registration_status"] == "needs_info"
    assert decision(sample_lead, admin_user) == first
    notify.assert_called_once()
    audit.assert_called_once()
    assert "secret payload" not in caplog.text
    assert "effect" in caplog.text.lower()


def test_result_projection_failure_rolls_back_before_commit(sample_lead, admin_user, mocker):
    mocker.patch("api.leads._read_lead_for_result", side_effect=RuntimeError("projection failed"))
    with pytest.raises(RuntimeError, match="projection failed"):
        decision(sample_lead, admin_user)
    assert counts(sample_lead["id"]) == (0, 0, 0)
    conn = get_conn()
    try:
        row = conn.execute("SELECT revision, registration_status FROM leads WHERE id=?",
                           (sample_lead["id"],)).fetchone()
        assert tuple(row.values()) == (sample_lead["revision"], "pending")
    finally:
        conn.close()


def test_simultaneous_retry_commits_once(sample_lead, admin_user):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(carry_context(lambda _: decision(sample_lead, admin_user)), range(2)))
    assert results[0] == results[1]
    assert counts(sample_lead["id"]) == (1, 1, 1)


def test_retry_returns_original_snapshot_after_a_later_response(
    sample_lead, admin_user, partner_user,
):
    first = decision(sample_lead, admin_user)
    leads.respond_to_information(sample_lead["id"], partner_user, "Telefon",
                                 first["revision"], "answer-later")
    assert decision(sample_lead, admin_user) == first
    assert counts(sample_lead["id"]) == (2, 2, 2)


def test_operation_key_cannot_be_reused_on_another_lead(sample_lead, admin_user, partner_user):
    decision(sample_lead, admin_user)
    other = leads.create_lead(partner_user, {
        "client_name": "Other Client", "company": "Other Co", "value": 100,
        "client_email": "other@example.test",
    })
    with pytest.raises(Exception) as failure:
        decision(other, admin_user)
    assert getattr(failure.value, "code", None) == "operation_conflict"
    assert counts(other["id"]) == (0, 0, 0)


def test_retry_http_returns_200_and_changed_payload_409(call_api, sample_lead, admin_user):
    status, login = call_api("POST", "/api/auth/login", body={
        "email": admin_user["email"], "password": admin_user["plain_password"],
        "role": admin_user["role"],
    })
    assert status == 200
    url = f"/api/leads/{sample_lead['id']}/registration"
    payload = {"action": "request_info", "message": "Telefon",
               "expected_revision": sample_lead["revision"], "operation_id": "http-retry"}
    first = call_api("POST", url, token=login["token"], body=payload)
    assert first[0] == 200
    assert call_api("POST", url, token=login["token"], body=payload) == first
    status, body = call_api("POST", url, token=login["token"], body={**payload, "message": "NIP"})
    assert status == 409
    assert body["code"] == "operation_conflict"
    assert counts(sample_lead["id"]) == (1, 1, 1)


def test_response_notifies_active_admin_and_manager_only(
    sample_lead, admin_user, manager_user, partner_user, mocker,
):
    requested = decision(sample_lead, admin_user)
    notify = mocker.patch("api.notifications.create_notification")
    leads.respond_to_information(sample_lead["id"], partner_user, "Telefon",
                                 requested["revision"], "answer-notify")
    assert {call.args[0] for call in notify.call_args_list} == {
        admin_user["id"], manager_user["id"],
    }
