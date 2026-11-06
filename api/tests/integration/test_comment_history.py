"""Comments that carry a workflow decision stay; every deletion is audited;
comment input is bounded and type-checked."""
import pytest

from api import audit, comments, leads
from api.db import get_conn


def _comment_rows(lead_id):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, body, user_id FROM lead_comments WHERE lead_id = ? ORDER BY id",
            (lead_id,),
        ).fetchall()]
    finally:
        conn.close()


def _revision(lead_id):
    conn = get_conn()
    try:
        return conn.execute("SELECT revision FROM leads WHERE id = ?", (lead_id,)).fetchone()[0]
    finally:
        conn.close()


def test_the_reason_for_a_rejection_cannot_be_deleted(sample_lead, admin_user, manager_user):
    leads.apply_registration_action(
        sample_lead["id"], admin_user, "reject", message="Duplikat zgłoszenia.",
        expected_revision=_revision(sample_lead["id"]), operation_id="reject-1",
    )
    reason = _comment_rows(sample_lead["id"])[0]

    status, body = comments.handle_delete(None, manager_user, reason["id"])

    assert status == 403, body
    assert _comment_rows(sample_lead["id"]) == [reason]


def test_a_partner_cannot_delete_his_answer_to_an_information_request(
    sample_lead, admin_user, partner_user
):
    leads.apply_registration_action(
        sample_lead["id"], admin_user, "request_info", message="Podaj NIP.",
        expected_revision=_revision(sample_lead["id"]), operation_id="info-1",
    )
    leads.respond_to_information(
        sample_lead["id"], partner_user, message="NIP 1234567890.",
        expected_revision=_revision(sample_lead["id"]), operation_id="answer-1",
    )
    answer = next(row for row in _comment_rows(sample_lead["id"])
                  if row["user_id"] == partner_user["id"])

    with pytest.raises(PermissionError):
        comments.delete_comment(answer["id"], partner_user)

    assert answer in _comment_rows(sample_lead["id"])


def test_an_ordinary_comment_is_deleted_with_an_audit_entry(sample_lead, partner_user):
    created = comments.create_comment(sample_lead["id"], partner_user, "Zwykła notatka")

    comments.delete_comment(created["id"], partner_user)

    assert _comment_rows(sample_lead["id"]) == []
    action = audit.log.call_args.args[1]
    detail = audit.log.call_args.args[4]
    assert action == "comment.delete"
    assert "Zwykła notatka" not in detail


@pytest.mark.parametrize("body", [
    {"body": "x" * (comments.MAX_COMMENT_LENGTH + 1)},
    {"body": 12345},
    {"body": ["lista"]},
    ["not", "an", "object"],
])
def test_comment_input_is_bounded_and_type_checked(sample_lead, partner_user, body):
    status, response = comments.handle_create(None, partner_user, sample_lead["id"], body)

    assert status == 400, response
    assert _comment_rows(sample_lead["id"]) == []
