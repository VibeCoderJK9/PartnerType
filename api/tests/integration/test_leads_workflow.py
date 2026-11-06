"""
PartnerType -- Lead lifecycle workflow integration tests.

Every request in this file is driven through api.router.dispatch via the
call_api fixture (api/tests/integration/conftest.py). No test calls
api.leads functions directly -- these tests prove the product's core
workflow (submit -> accept -> commission visible -> win -> commission
recorded) actually works when driven through the real router, not just that
the underlying business functions work in isolation.

A lead has two axes. Accepting a deal is a registration decision that puts it into
the sales pipeline and makes its commission VISIBLE; winning it is what
RECORDS that commission. The tests assert both halves separately: the
commission is visible at every stage but not payable until the win.
"""
from api import notifications as notif
from api.tests.integration.conftest import lead_payload


def _login_token(call_api, user):
    """Log in a fixture user through the router and return the session token."""
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


# ─── THE HEADLINE WORKFLOW ────────────────────────────────────────────────────

class TestHeadlineWorkflow:
    def test_gold_partner_lead_submit_accept_yields_commission_1400(
        self, call_api, partner_user, admin_user
    ):
        partner_token = _login_token(call_api, partner_user)

        create_status, create_body = call_api(
            "POST",
            "/api/leads",
            body=lead_payload(client_name="Acme Corp", value=20000),
            token=partner_token,
        )
        assert create_status == 201
        lead_id = create_body["lead"]["id"]

        admin_token = _login_token(call_api, admin_user)

        accept_status, accept_body = call_api(
            "PATCH",
            f"/api/leads/{lead_id}",
            body={"registration_status": "accepted"},
            token=admin_token,
        )
        assert accept_status == 200
        # Accepting protects the deal and starts the sale. The commission is
        # visible from this moment, but not yet recorded against the lead.
        assert accept_body["lead"]["registration_status"] == "accepted"
        assert accept_body["lead"]["status"] == "contact"
        assert accept_body["lead"]["commission_projected"] == 1400.0
        assert accept_body["lead"]["commission"] is None

        list_status, list_body = call_api("GET", "/api/leads", token=partner_token)
        assert list_status == 200
        assert len(list_body["leads"]) == 1
        lead = list_body["leads"][0]
        assert lead["id"] == lead_id
        assert lead["registration_status"] == "accepted"
        # The partner sees what the deal is worth while they are still
        # working it -- the whole point of separating the two axes.
        assert lead["commission_projected"] == 1400.0

        win_status, win_body = call_api(
            "PATCH",
            f"/api/leads/{lead_id}",
            body={"status": "won"},
            token=admin_token,
        )
        assert win_status == 200
        assert win_body["lead"]["commission"] == 1400.0


# ─── ADDITIONAL LIFECYCLE WORKFLOWS ──────────────────────────────────────────

class TestAdditionalWorkflows:
    def test_admin_rejects_lead_shows_null_commission(
        self, call_api, partner_user, admin_user
    ):
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(), token=partner_token
        )
        lead_id = create_body["lead"]["id"]

        status, body = call_api(
            "PATCH",
            f"/api/leads/{lead_id}",
            body={"registration_status": "rejected",
                  "registration_message": "Powód odrzucenia."},
            token=admin_token,
        )
        assert status == 200
        assert body["lead"]["commission"] is None

        _, list_body = call_api("GET", "/api/leads", token=partner_token)
        lead = next(l for l in list_body["leads"] if l["id"] == lead_id)
        assert lead["registration_status"] == "rejected"
        # A refused deal is not anywhere in the sales pipeline.
        assert lead["status"] is None
        assert lead["commission"] is None

    def test_lead_asked_for_information_then_accepted_records_commission_only_on_win(
        self, call_api, partner_user, admin_user
    ):
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(value=10000), token=partner_token
        )
        lead_id = create_body["lead"]["id"]

        info_status, info_body = call_api(
            "PATCH",
            f"/api/leads/{lead_id}",
            body={"registration_status": "needs_info",
                  "registration_message": "Prosimy o uzupełnienie."},
            token=admin_token,
        )
        assert info_status == 200
        assert info_body["lead"]["commission"] is None
        assert info_body["lead"]["status"] is None

        accepted_status, accepted_body = call_api(
            "PATCH",
            f"/api/leads/{lead_id}",
            body={"registration_status": "accepted"},
            token=admin_token,
        )
        assert accepted_status == 200
        # Visible now, recorded only once the sale is actually won.
        assert accepted_body["lead"]["commission_projected"] == 700.0
        assert accepted_body["lead"]["commission"] is None

        won_status, won_body = call_api(
            "PATCH", f"/api/leads/{lead_id}", body={"status": "won"}, token=admin_token
        )
        assert won_status == 200
        assert won_body["lead"]["commission"] == 700.0

    def test_admin_sets_explicit_commission_matches_stored_value(
        self, call_api, partner_user, admin_user
    ):
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(), token=partner_token
        )
        lead_id = create_body["lead"]["id"]

        status, body = call_api(
            "PATCH",
            f"/api/leads/{lead_id}",
            body={"commission": 999.5},
            token=admin_token,
        )
        assert status == 200
        assert body["lead"]["commission"] == 999.5

    def test_admin_bulk_accepts_two_leads_both_enter_the_pipeline(
        self, call_api, partner_user, admin_user
    ):
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        _, lead1 = call_api(
            "POST", "/api/leads", body=lead_payload(value=5000), token=partner_token
        )
        _, lead2 = call_api(
            "POST", "/api/leads",
            body=lead_payload(value=8000, nip="2222222222", website="drugi-klient.pl"),
            token=partner_token,
        )
        id1, id2 = lead1["lead"]["id"], lead2["lead"]["id"]

        status, body = call_api(
            "POST",
            "/api/leads/bulk",
            body={"action": "accept", "items": [
                {"id": lead["id"], "expected_revision": lead["revision"], "operation_id": lead["id"]}
                for lead in (lead1["lead"], lead2["lead"])]},
            token=admin_token,
        )
        assert status == 200
        assert len(body["ok"]) == 2
        assert body["error"] == []

        _, get1 = call_api("GET", f"/api/leads/{id1}", token=admin_token)
        _, get2 = call_api("GET", f"/api/leads/{id2}", token=admin_token)
        # Bulk accept is a registration decision: both deals enter the
        # pipeline with a visible forecast, and neither has a recorded
        # commission until it is actually won.
        assert get1["lead"]["registration_status"] == "accepted"
        assert get2["lead"]["registration_status"] == "accepted"
        assert get1["lead"]["commission_projected"] > 0
        assert get2["lead"]["commission_projected"] > 0
        assert get1["lead"]["commission"] is None
        assert get2["lead"]["commission"] is None

    def test_admin_bulk_rejects_mixed_batch_reports_one_processed_one_error(
        self, call_api, partner_user, admin_user
    ):
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(), token=partner_token
        )
        lead_id = create_body["lead"]["id"]

        status, body = call_api(
            "POST",
            "/api/leads/bulk",
            body={"action": "reject", "items": [
                {"id": lead_id, "expected_revision": create_body["lead"]["revision"],
                 "operation_id": lead_id, "message": "Brak danych"},
                {"id": "LD-UNKNOWN", "expected_revision": 0,
                 "operation_id": "missing", "message": "Brak danych"}]},
            token=admin_token,
        )
        assert status == 200
        assert len(body["ok"]) == 1
        assert len(body["error"]) == 1


# ─── NOTIFICATION WORKFLOWS ──────────────────────────────────────────────────

class TestNotificationWorkflows:
    def test_accepting_lead_produces_one_partner_notification(
        self, call_api, partner_user, admin_user
    ):
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(), token=partner_token
        )
        lead_id = create_body["lead"]["id"]

        status, _ = call_api(
            "PATCH",
            f"/api/leads/{lead_id}",
            body={"registration_status": "accepted"},
            token=admin_token,
        )
        assert status == 200

        assert notif.create_notification.call_count == 1
        call_args = notif.create_notification.call_args[0]
        assert call_args[0] == partner_user["id"]
        assert call_args[1] == "success"

    def test_submitting_lead_produces_one_admin_broadcast(
        self, call_api, partner_user
    ):
        partner_token = _login_token(call_api, partner_user)

        status, _ = call_api(
            "POST", "/api/leads", body=lead_payload(), token=partner_token
        )
        assert status == 201
        assert notif.notify_all_admins.call_count == 1

    def test_rejecting_lead_produces_one_error_notification(
        self, call_api, partner_user, admin_user
    ):
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(), token=partner_token
        )
        lead_id = create_body["lead"]["id"]

        status, _ = call_api(
            "PATCH",
            f"/api/leads/{lead_id}",
            body={"registration_status": "rejected",
                  "registration_message": "Powód odrzucenia."},
            token=admin_token,
        )
        assert status == 200

        assert notif.create_notification.call_count == 1
        call_args = notif.create_notification.call_args[0]
        assert call_args[1] == "error"


# ─── READING WORKFLOWS ────────────────────────────────────────────────────────

class TestReadingWorkflows:
    def test_admin_listing_leads_sees_leads_from_every_partner(
        self, call_api, partner_user, partner_user_bronze, admin_user
    ):
        partner_token = _login_token(call_api, partner_user)
        bronze_token = _login_token(call_api, partner_user_bronze)
        admin_token = _login_token(call_api, admin_user)

        call_api("POST", "/api/leads", body=lead_payload(), token=partner_token)
        call_api("POST", "/api/leads", body=lead_payload(), token=bronze_token)

        status, body = call_api("GET", "/api/leads", token=admin_token)
        assert status == 200
        partner_ids = {lead["partner_id"] for lead in body["leads"]}
        assert partner_user["id"] in partner_ids
        assert partner_user_bronze["id"] in partner_ids

    def test_total_field_matches_number_of_returned_rows(
        self, call_api, partner_user, admin_user
    ):
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        call_api("POST", "/api/leads", body=lead_payload(), token=partner_token)
        call_api("POST", "/api/leads", body=lead_payload(), token=partner_token)

        status, body = call_api("GET", "/api/leads", token=admin_token)
        assert status == 200
        assert body["total"] == len(body["leads"])

    def test_registration_status_query_filter_narrows_results(
        self, call_api, partner_user, admin_user
    ):
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        _, lead1 = call_api(
            "POST", "/api/leads", body=lead_payload(), token=partner_token
        )
        call_api("POST", "/api/leads", body=lead_payload(), token=partner_token)
        id1 = lead1["lead"]["id"]

        call_api(
            "PATCH",
            f"/api/leads/{id1}",
            body={"registration_status": "accepted"},
            token=admin_token,
        )

        status, body = call_api(
            "GET", "/api/leads?registration_status=accepted", token=admin_token
        )
        assert status == 200
        assert len(body["leads"]) == 1
        assert body["leads"][0]["id"] == id1

    def test_free_text_query_filter_narrows_results(
        self, call_api, partner_user, admin_user
    ):
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        call_api(
            "POST",
            "/api/leads",
            body=lead_payload(client_name="Unique Corp Name"),
            token=partner_token,
        )
        call_api(
            "POST",
            "/api/leads",
            body=lead_payload(client_name="Other Client"),
            token=partner_token,
        )

        status, body = call_api("GET", "/api/leads?q=Unique", token=admin_token)
        assert status == 200
        assert len(body["leads"]) == 1
        assert "Unique" in body["leads"][0]["client_name"]

    def test_fetch_single_lead_returns_joined_partner_name(
        self, call_api, partner_user
    ):
        partner_token = _login_token(call_api, partner_user)

        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(), token=partner_token
        )
        lead_id = create_body["lead"]["id"]

        status, body = call_api("GET", f"/api/leads/{lead_id}", token=partner_token)
        assert status == 200
        assert body["lead"]["partner_name"] == partner_user["name"]


# ─── VALIDATION WORKFLOW ──────────────────────────────────────────────────────

class TestValidationWorkflow:
    def test_missing_required_field_returns_400_and_writes_no_row(
        self, call_api, partner_user
    ):
        partner_token = _login_token(call_api, partner_user)

        status, body = call_api(
            "POST",
            "/api/leads",
            body={
                "client_email": "client@test.pl",
                "company": "Test Co",
                "value": 1000,
            },
            token=partner_token,
        )
        assert status == 400
        assert "error" in body

        _, list_body = call_api("GET", "/api/leads", token=partner_token)
        assert list_body["leads"] == []

    def test_missing_nip_returns_400_and_writes_no_row(
        self, call_api, partner_user
    ):
        partner_token = _login_token(call_api, partner_user)

        status, body = call_api(
            "POST",
            "/api/leads",
            body=lead_payload(nip=""),
            token=partner_token,
        )
        assert status == 400
        assert "error" in body

        _, list_body = call_api("GET", "/api/leads", token=partner_token)
        assert list_body["leads"] == []
