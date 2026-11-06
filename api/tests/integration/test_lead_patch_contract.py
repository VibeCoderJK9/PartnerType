"""
PartnerType -- the generic PATCH closes its bypasses.

Two loopholes are shut here.

The first is the decision bypass: PATCH used to write `registration_status`
with its own SQL, so a producer could reject a deal with no reason, or push an
accepted lead back to `pending` -- neither of which the named endpoints allow.
It now delegates to the same canonical operation, which means one set of rules
rather than two.

The second is the versioning bypass: an HTTP write that omits its version was
last-write-wins, silently discarding whatever somebody else saved in the
meantime. The server does NOT fill the field in on the client's behalf, because
doing so would restore exactly the behaviour being removed.
"""
import pytest

from api.db import get_conn


def _login(call_api, user):
    status, body = call_api("POST", "/api/auth/login", body={
        "email": user["email"], "password": user["plain_password"],
        "role": user["role"],
    })
    assert status == 200, body
    return body["token"]


def _row(lead_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def _comments(lead_id):
    conn = get_conn()
    try:
        return [r["body"] for r in conn.execute(
            "SELECT body FROM lead_comments WHERE lead_id = ? ORDER BY id", (lead_id,)
        ).fetchall()]
    finally:
        conn.close()


def _patch(call_api, token, lead_id, body):
    return call_api("PATCH", f"/api/leads/{lead_id}", token=token, body=body)


def _meta(lead_id, key="patch"):
    return {"expected_revision": _row(lead_id)["revision"], "operation_id": key}


# ─── (a) THE DECISION BYPASS ────────────────────────────────────────────────

def test_patch_rejecting_without_a_reason_is_refused(call_api, sample_lead, admin_user):
    """The named endpoint requires a reason, so this one does too. Otherwise
    the rule is decoration: anybody could reject through PATCH instead."""
    token = _login(call_api, admin_user)
    before = _row(sample_lead["id"])

    status, body = _patch(call_api, token, sample_lead["id"], dict(
        _meta(sample_lead["id"]), registration_status="rejected"))

    assert status == 400, body
    after = _row(sample_lead["id"])
    assert after["registration_status"] == before["registration_status"]
    assert after["revision"] == before["revision"]
    assert _comments(sample_lead["id"]) == []


def test_patch_rejecting_with_a_reason_behaves_like_the_named_endpoint(
    call_api, sample_lead, admin_user
):
    token = _login(call_api, admin_user)
    status, body = _patch(call_api, token, sample_lead["id"], dict(
        _meta(sample_lead["id"]),
        registration_status="rejected",
        registration_message="Poza obszarem działania.",
    ))

    assert status == 200, body
    row = _row(sample_lead["id"])
    assert row["registration_status"] == "rejected"
    assert row["status"] is None
    assert _comments(sample_lead["id"]) == ["Poza obszarem działania."]


def test_patch_cannot_push_a_lead_back_to_pending(call_api, sample_lead, admin_user):
    """There is no 'undecide' action, so there must be no PATCH that performs
    one -- a reopened decision would silently drop the audit trail of the
    first."""
    token = _login(call_api, admin_user)
    _patch(call_api, token, sample_lead["id"], dict(
        _meta(sample_lead["id"]), registration_status="accepted"))

    before = _row(sample_lead["id"])
    status, body = _patch(call_api, token, sample_lead["id"], dict(
        _meta(sample_lead["id"], "back"), registration_status="pending"))

    assert status in (400, 409), body
    assert _row(sample_lead["id"])["registration_status"] == before["registration_status"]


def test_patch_mixing_a_decision_with_a_stage_change_is_refused_entirely(
    call_api, sample_lead, admin_user
):
    """Two different questions in one request: which one won would depend on
    statement order. Refusing outright is the only answer that cannot be
    half-applied."""
    token = _login(call_api, admin_user)
    before = _row(sample_lead["id"])

    status, body = _patch(call_api, token, sample_lead["id"], dict(
        _meta(sample_lead["id"]),
        registration_status="accepted",
        status="contact",
    ))

    assert status == 400, body
    after = _row(sample_lead["id"])
    assert after["registration_status"] == before["registration_status"]
    assert after["status"] == before["status"]
    assert after["revision"] == before["revision"]


def test_patch_mixing_a_decision_with_a_commission_change_is_refused_entirely(
    call_api, sample_lead, admin_user
):
    token = _login(call_api, admin_user)
    before = _row(sample_lead["id"])

    status, body = _patch(call_api, token, sample_lead["id"], dict(
        _meta(sample_lead["id"]),
        registration_status="accepted",
        commission=999,
    ))

    assert status == 400, body
    after = _row(sample_lead["id"])
    assert after["registration_status"] == before["registration_status"]
    assert after["commission"] == before["commission"]
    assert after["revision"] == before["revision"]


def test_patch_accepting_lands_on_the_first_active_stage(
    call_api, sample_lead, admin_user
):
    token = _login(call_api, admin_user)
    status, body = _patch(call_api, token, sample_lead["id"], dict(
        _meta(sample_lead["id"]), registration_status="accepted"))

    assert status == 200, body
    conn = get_conn()
    try:
        first = conn.execute(
            """SELECT key FROM lead_stages WHERE is_terminal = 0 AND active = 1
                ORDER BY sort_order ASC, id ASC LIMIT 1"""
        ).fetchone()["key"]
    finally:
        conn.close()
    assert _row(sample_lead["id"])["status"] == first


# ─── (b) THE VERSIONING BYPASS ──────────────────────────────────────────────

@pytest.mark.parametrize("missing", ["expected_revision", "operation_id"])
def test_a_patch_without_its_versioning_metadata_is_refused(
    call_api, sample_lead, admin_user, missing
):
    token = _login(call_api, admin_user)
    before = _row(sample_lead["id"])
    body = dict(_meta(sample_lead["id"]), status=None, commission=500)
    body.pop("status")
    del body[missing]

    status, response = _patch(call_api, token, sample_lead["id"], body)

    assert status == 400, response
    assert _row(sample_lead["id"])["revision"] == before["revision"]
    assert _row(sample_lead["id"])["commission"] == before["commission"]


def test_a_stale_patch_conflicts_and_changes_nothing(call_api, sample_lead, admin_user):
    token = _login(call_api, admin_user)
    stale = _row(sample_lead["id"])["revision"]

    status, body = _patch(call_api, token, sample_lead["id"], dict(
        _meta(sample_lead["id"], "first"), registration_status="accepted"))
    assert status == 200, body

    accepted = _row(sample_lead["id"])
    status, body = _patch(call_api, token, sample_lead["id"], {
        "expected_revision": stale, "operation_id": "second", "commission": 4242,
    })

    assert status == 409, body
    after = _row(sample_lead["id"])
    assert after["commission"] == accepted["commission"]
    assert after["revision"] == accepted["revision"]


def test_a_commission_patch_bumps_the_revision(call_api, sample_lead, admin_user):
    token = _login(call_api, admin_user)
    _patch(call_api, token, sample_lead["id"], dict(
        _meta(sample_lead["id"], "accept"), registration_status="accepted"))

    before = _row(sample_lead["id"])["revision"]
    status, body = _patch(call_api, token, sample_lead["id"], dict(
        _meta(sample_lead["id"], "money"), commission=3000))

    assert status == 200, body
    assert _row(sample_lead["id"])["revision"] == before + 1
    assert _row(sample_lead["id"])["commission"] == 3000


def test_a_stage_patch_bumps_the_revision(call_api, sample_lead, admin_user):
    token = _login(call_api, admin_user)
    _patch(call_api, token, sample_lead["id"], dict(
        _meta(sample_lead["id"], "accept"), registration_status="accepted"))

    before = _row(sample_lead["id"])["revision"]
    status, body = _patch(call_api, token, sample_lead["id"], dict(
        _meta(sample_lead["id"], "stage"), status="presentation"))

    assert status == 200, body
    assert _row(sample_lead["id"])["revision"] == before + 1


def test_a_partner_still_cannot_patch_a_lead(call_api, sample_lead, partner_user):
    """Closing the bypasses must not accidentally open the door wider."""
    token = _login(call_api, partner_user)
    status, body = _patch(call_api, token, sample_lead["id"], dict(
        _meta(sample_lead["id"]), commission=1))
    assert status == 403, body


# ─── REPLAY, LIVE PERMISSION AND HISTORY ───────────────────────────────────

def _accepted(call_api, token, lead_id):
    status, body = _patch(call_api, token, lead_id, dict(
        _meta(lead_id, "accept"), registration_status="accepted"))
    assert status == 200, body


def test_a_retried_patch_returns_its_saved_result(call_api, sample_lead, admin_user):
    """A retry after a lost response is the same operation, not a conflict."""
    token = _login(call_api, admin_user)
    _accepted(call_api, token, sample_lead["id"])
    request = dict(_meta(sample_lead["id"], "money-retry"), commission=3000)

    first_status, first = _patch(call_api, token, sample_lead["id"], dict(request))
    second_status, second = _patch(call_api, token, sample_lead["id"], dict(request))

    assert first_status == 200, first
    assert second_status == 200, second
    assert second["lead"]["revision"] == first["lead"]["revision"]
    assert _row(sample_lead["id"])["revision"] == first["lead"]["revision"]


def test_an_operation_id_reused_for_another_change_conflicts(
    call_api, sample_lead, admin_user
):
    token = _login(call_api, admin_user)
    _accepted(call_api, token, sample_lead["id"])
    status, body = _patch(call_api, token, sample_lead["id"], dict(
        _meta(sample_lead["id"], "money-once"), commission=3000))
    assert status == 200, body

    status, body = _patch(call_api, token, sample_lead["id"], dict(
        _meta(sample_lead["id"], "money-once"), commission=4000))

    assert status == 409, body
    assert body["code"] == "operation_conflict"
    assert _row(sample_lead["id"])["commission"] == 3000


def test_a_deactivated_staff_session_cannot_edit_the_lead(sample_lead, admin_user):
    from api.leads import update_lead
    conn = get_conn()
    try:
        conn.execute("UPDATE users SET active = 0 WHERE id = ?", (admin_user["id"],))
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(PermissionError):
        update_lead(sample_lead["id"], admin_user, {"commission": 5000})

    assert _row(sample_lead["id"])["commission"] is None


def test_a_patch_is_recorded_in_the_lead_history(call_api, sample_lead, admin_user):
    token = _login(call_api, admin_user)
    _accepted(call_api, token, sample_lead["id"])

    status, body = _patch(call_api, token, sample_lead["id"], dict(
        _meta(sample_lead["id"], "stage-history"), status="presentation"))

    assert status == 200, body
    conn = get_conn()
    try:
        events = conn.execute(
            "SELECT event_type, payload_json FROM lead_workflow_events "
            "WHERE lead_id = ? AND event_type = 'lead.update'",
            (sample_lead["id"],),
        ).fetchall()
    finally:
        conn.close()
    assert len(events) == 1
    assert '"to": "presentation"' in events[0]["payload_json"]
