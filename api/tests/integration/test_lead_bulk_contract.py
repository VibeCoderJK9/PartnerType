"""Versioned bulk registration through the real HTTP router."""
import pytest

from api.db import get_conn
from api.leads import create_lead, apply_registration_action


def login(call_api, user):
    status, body = call_api("POST", "/api/auth/login", body={
        "email": user["email"], "password": user["plain_password"], "role": user["role"],
    })
    assert status == 200
    return body["token"]


def item(lead, **extra):
    return {"id": lead["id"], "expected_revision": lead["revision"],
            "operation_id": "bulk-" + lead["id"], **extra}


@pytest.mark.parametrize("actor_fixture", ["admin_user", "manager_user"])
def test_bulk_reject_reason_and_retry(call_api, sample_lead, request, actor_fixture, mocker):
    actor = request.getfixturevalue(actor_fixture)
    token = login(call_api, actor)
    audit = mocker.patch("api.audit.log")
    body = {"action": "reject", "items": [item(sample_lead, message="Brak danych")]}
    first = call_api("POST", "/api/leads/bulk", token=token, body=body)
    assert first[0] == 200
    assert first[1]["error"] == []
    assert first[1]["ok"][0]["lead"]["registration_status"] == "rejected"
    assert call_api("POST", "/api/leads/bulk", token=token, body=body) == first
    audit.assert_called_once()
    conn = get_conn()
    try:
        rows = conn.execute("SELECT body,user_id FROM lead_comments WHERE lead_id=?",
                            (sample_lead["id"],)).fetchall()
        assert [tuple(row.values()) for row in rows] == [("Brak danych", actor["id"])]
    finally:
        conn.close()


def test_mixed_success_and_conflict_are_explicit(call_api, sample_lead, partner_user, admin_user):
    other = create_lead(partner_user, {"client_name": "Other", "client_email": "other@test.pl",
                                       "company": "Other", "value": 100})
    apply_registration_action(sample_lead["id"], admin_user, "request_info", "Telefon",
                              sample_lead["revision"], "previous")
    status, result = call_api("POST", "/api/leads/bulk", token=login(call_api, admin_user),
                              body={"action": "accept", "items": [item(sample_lead), item(other)]})
    assert status == 200
    assert [row["id"] for row in result["ok"]] == [other["id"]]
    assert result["error"][0]["id"] == sample_lead["id"]
    assert result["error"][0]["status"] == 409
    assert result["error"][0]["code"] == "stale_revision"


@pytest.mark.parametrize("bad", [None, {}, {"id": "LD-X"},
    {"id": "LD-X", "expected_revision": True, "operation_id": "x"},
    {"id": "LD-X", "expected_revision": 0, "operation_id": ""}])
def test_entire_structure_validated_before_any_write(call_api, sample_lead, admin_user, bad):
    status, _ = call_api("POST", "/api/leads/bulk", token=login(call_api, admin_user),
                         body={"action": "accept", "items": [item(sample_lead), bad]})
    assert status == 400
    conn = get_conn()
    try:
        assert conn.execute("SELECT revision FROM leads WHERE id=?",
                            (sample_lead["id"],)).fetchone()[0] == sample_lead["revision"]
    finally:
        conn.close()


@pytest.mark.parametrize("kind", ["legacy", "duplicate", "no_reason", "empty", "too_many"])
def test_unsafe_batches_rejected(call_api, sample_lead, admin_user, kind):
    bodies = {
        "legacy": {"action": "accept", "ids": [sample_lead["id"]]},
        "duplicate": {"action": "accept", "items": [item(sample_lead), item(sample_lead)]},
        "no_reason": {"action": "reject", "items": [item(sample_lead)]},
        "empty": {"action": "accept", "items": []},
        "too_many": {"action": "accept", "items": [item(sample_lead)] * 1001},
    }
    status, _ = call_api("POST", "/api/leads/bulk", token=login(call_api, admin_user), body=bodies[kind])
    assert status == 400


# ─── (f) THE DEMO SEED IS A WRITER TOO ──────────────────────────────────────
#
# scripts/seed_demo.py calls update_lead directly and ARCHITECTURE.md names it as a
# supported way to rebuild a demo database. When the decision rules moved into
# the domain, this became the one writer that had no test standing behind it:
# every rule it breaks surfaces as a broken demo, minutes before somebody
# shows the product.

class TestSeedDemoObeysTheDecisionContract:
    def test_seeding_a_temporary_database_succeeds_and_states_its_reasons(
        self, test_db_path
    ):
        """
        Runs the real routine against the throwaway fixture database -- never
        the working one, which is why this drives the function rather than
        shelling out to the script.
        """
        import scripts.seed_demo as seed_demo

        seed_demo.main()

        conn = get_conn()
        try:
            decided = conn.execute(
                """SELECT registration_status, COUNT(*) AS n FROM leads
                    WHERE registration_status IN ('rejected', 'needs_info')
                 GROUP BY registration_status"""
            ).fetchall()
            assert decided, "the demo set no longer covers a refusal or a request"

            # Every refusal and request the seed produced left the partner a
            # reason, exactly as a producer doing it by hand would have to.
            for row in conn.execute(
                """SELECT l.id FROM leads l
                    WHERE l.registration_status IN ('rejected', 'needs_info')"""
            ).fetchall():
                comments = conn.execute(
                    "SELECT body FROM lead_comments WHERE lead_id = ? AND is_internal = 0",
                    (row["id"],),
                ).fetchall()
                assert comments, f"{row['id']} was decided with no reason for the partner"
                assert any(c["body"].strip() for c in comments)
        finally:
            conn.close()

    def test_seeding_twice_is_safe(self, test_db_path):
        """A demo is usually rebuilt right before it is shown; doing it twice
        must not double the data or trip the new replay guards."""
        import scripts.seed_demo as seed_demo

        seed_demo.main()
        conn = get_conn()
        try:
            first = conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"]
        finally:
            conn.close()

        seed_demo.main()

        conn = get_conn()
        try:
            second = conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"]
        finally:
            conn.close()
        assert second == first
