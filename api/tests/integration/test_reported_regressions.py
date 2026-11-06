"""
PartnerType -- server-side regression tests for two reported problems:
logging in as a partner and submitting a lead as a partner. The DOM
click-delegation cases live in js/__tests__/reported-regressions.test.js. Every request here goes through
api.router.dispatch via the call_api fixture (api/tests/integration/conftest.py),
never through api.leads/api.auth functions directly, matching this
directory's existing router-driven convention.

This file deliberately does NOT repeat assertions that already exist:

- api/tests/integration/test_auth_flow.py::TestAuthFlow::
  test_partner_login_returns_token_and_partner_role already proves an
  active partner can log in through the router.
- api/tests/integration/test_auth_flow.py::TestRegisterFlow (or equivalent
  class)::test_registered_account_cannot_login_while_inactive already
  proves a pending (self-registered) account is refused login with a 401.
- api/tests/integration/test_leads_workflow.py::TestHeadlineWorkflow::
  test_gold_partner_lead_submit_accept_yields_commission_1400 already
  proves a partner can submit a lead through the router -- but only because
  this directory's autouse `integration_catalogue_product` fixture
  (api/tests/integration/conftest.py) always seeds product id 1 first.

The gap those leave, and the one this file fills: on a fresh system with
*zero* products (a first customer's starting state), the identical lead
submission is refused with the "Product is required" message -- and the
identical submission succeeds the moment one product exists.
TestPartnerLeadSubmissionReport
below reproduces both halves of it in one place.
"""
from api.db import get_conn
from api.tests.integration.conftest import lead_payload


def _login(call_api, email, password, role):
    return call_api(
        "POST",
        "/api/auth/login",
        body={"email": email, "password": password, "role": role},
    )


class TestPartnerLoginReport:
    """A partner reported they could not log in.

    Also covered by test_auth_flow.py (see this module's docstring). This
    test ties both halves -- an active account logging in, and a pending one
    being refused -- to a single assertion, rather than asking a reader to cross-reference two other
    files to see the whole report answered.
    """

    def test_active_partner_logs_in_pending_partner_is_refused(
        self, call_api, partner_user
    ):
        active_status, active_body = _login(
            call_api, partner_user["email"], partner_user["plain_password"], "partner"
        )
        assert active_status == 200
        assert active_body["user"]["role"] == "partner"

        register_status, _ = call_api(
            "POST",
            "/api/auth/register",
            body={
                "name": "Zgloszony Partner",
                "email": "zgloszony.partner@test.pl",
                "password": "ZgloszonyPass123!",
                "company": "Zgloszony Sp. z o.o.",
            "phone": "+48 600 100 200",
            },
        )
        assert register_status == 201

        pending_status, pending_body = _login(
            call_api, "zgloszony.partner@test.pl", "ZgloszonyPass123!", "partner"
        )
        assert pending_status == 401
        assert "error" in pending_body


class TestPartnerLeadSubmissionReport:
    """A partner reported they could not add a lead.

    Reproduces -- but the cause is a catalogue of zero products, not a bug
    in the lead form or its submission endpoint. This test is a permanent
    regression gate.
    """

    def test_submission_requires_a_product_and_succeeds_once_one_exists(
        self, call_api, partner_user
    ):
        _, login_body = _login(
            call_api, partner_user["email"], partner_user["plain_password"], "partner"
        )
        partner_token = login_body["token"]

        # conftest.py's autouse `integration_catalogue_product` fixture
        # always seeds product id 1 for every integration test in this
        # directory. Remove it here to reproduce a first customer's
        # starting state -- a brand-new database has no
        # products until the producer adds one -- rather than the
        # already-populated catalogue every other test in this suite runs
        # against.
        conn = get_conn()
        try:
            conn.execute("DELETE FROM products")
            conn.commit()
        finally:
            conn.close()

        blocked_status, blocked_body = call_api(
            "POST",
            "/api/leads",
            body=lead_payload(product_id=None, product=None),
            token=partner_token,
        )
        assert blocked_status == 400
        assert blocked_body["error"] == "Pole 'Produkt' jest wymagane."

        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO products
                   (id, name, code, category, base_price, offered, active, sort_order)
                   VALUES (1, 'Wdrożenie ERP', 'TEST-ERP', 'Wdrożenie', 10000, 1, 1, 0)"""
            )
            conn.commit()
        finally:
            conn.close()

        ok_status, ok_body = call_api(
            "POST",
            "/api/leads",
            body=lead_payload(),
            token=partner_token,
        )
        assert ok_status == 201
        assert ok_body["lead"]["partner_id"] == partner_user["id"]
