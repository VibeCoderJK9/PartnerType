"""
PartnerType -- The observer role tracer, end to end.

An observer who signs in sees leads and their statuses only; the rest of
the app is greyed out.

Greying out a nav entry is a courtesy the browser performs; it is not the
control. This file asserts the control: an observer session driven through
api.router.dispatch reads leads and is refused every write and every other
surface, whether or not any interface ever offered it. A role that is only
read-only because the buttons are hidden is not read-only.
"""
import pytest

from api.db import get_conn
from api.tests.integration.conftest import lead_payload


def _login_token(call_api, email, password, role):
    _, body = call_api(
        "POST",
        "/api/auth/login",
        body={"email": email, "password": password, "role": role},
    )
    return body["token"]


@pytest.fixture
def observer_token(call_api, admin_user):
    """An observer account created through the real endpoint, then logged in
    through the real endpoint -- never hand-inserted, so the account this
    file exercises is one the product can actually produce."""
    admin_token = _login_token(
        call_api, admin_user["email"], admin_user["plain_password"], "admin"
    )
    status, body = call_api(
        "POST",
        "/api/users",
        body={
            "name": "Test Obserwator",
            "email": "obserwator@test.pl",
            "password": "ObserwatorPass123!",
            "company": "PartnerType",
            "role": "observer",
        },
        token=admin_token,
    )
    assert status == 201, body
    assert body["user"]["role"] == "observer"
    return _login_token(call_api, "obserwator@test.pl", "ObserwatorPass123!", "observer")


class TestObserverCanRead:
    def test_observer_lists_leads(self, call_api, observer_token):
        status, body = call_api("GET", "/api/leads", token=observer_token)

        assert status == 200
        assert "leads" in body

    def test_observer_sees_every_partners_leads_not_a_scoped_subset(
        self, call_api, observer_token, partner_user, partner_user_bronze
    ):
        """The producer-side view: an observer is watching the whole channel,
        so unlike a partner they are not row-scoped to one account."""
        first = _login_token(
            call_api, partner_user["email"], partner_user["plain_password"], "partner"
        )
        second = _login_token(
            call_api,
            partner_user_bronze["email"],
            partner_user_bronze["plain_password"],
            "partner",
        )
        call_api("POST", "/api/leads", body=lead_payload(), token=first)
        call_api("POST", "/api/leads", body=lead_payload(), token=second)

        status, body = call_api("GET", "/api/leads", token=observer_token)

        assert status == 200
        partner_ids = {lead["partner_id"] for lead in body["leads"]}
        assert len(partner_ids) == 2

    def test_observer_reads_a_single_lead_with_its_status(
        self, call_api, observer_token, sample_lead
    ):
        status, body = call_api(
            "GET", f"/api/leads/{sample_lead['id']}", token=observer_token
        )

        assert status == 200
        assert body["lead"]["id"] == sample_lead["id"]
        assert "status" in body["lead"]


class TestObserverCannotWrite:
    def test_observer_updating_a_lead_returns_403_and_changes_nothing(
        self, call_api, observer_token, sample_lead
    ):
        status, body = call_api(
            "PATCH",
            f"/api/leads/{sample_lead['id']}",
            body={"commission": 5000},
            token=observer_token,
        )

        assert status == 403
        assert "error" in body

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT commission FROM leads WHERE id = ?", (sample_lead["id"],)
            ).fetchone()
        finally:
            conn.close()
        assert row["commission"] == sample_lead["commission"]

    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("POST", "/api/leads/bulk", {"action": "accept", "items": [{"id": "LD-001", "expected_revision": 0, "operation_id": "observer-denied"}]}),
            ("POST", "/api/leads", None),
            ("GET", "/api/partners", None),
            ("GET", "/api/users", None),
            ("GET", "/api/audit", None),
            ("PATCH", "/api/settings", {"company_name": "Nope"}),
            ("POST", "/api/products", {"name": "Nope", "category": "Usługa"}),
            ("POST", "/api/tiers", {"name": "Nope", "base_rate": 5.0}),
            ("POST", "/api/bonus-defs", {"name": "Nope", "type": "percent", "default_value": 1.0}),
            ("POST", "/api/lead-stages", {"key": "nope", "name": "Nope"}),
            (
                "POST",
                "/api/users/team-invite",
                {"name": "Nope", "email": "nope@test.pl"},
            ),
        ],
    )
    def test_observer_is_refused_every_non_lead_surface(
        self, call_api, observer_token, method, path, body
    ):
        status, response = call_api(method, path, body=body, token=observer_token)

        assert status == 403, f"{method} {path} returned {status}, expected 403"
        assert "error" in response

    @pytest.mark.parametrize("path", [
        "/api/partner-bonuses",
        "/api/partner-bonuses/{partner_id}",
    ])
    def test_observer_cannot_read_partner_commission_cards(
        self, call_api, observer_token, partner_user, path
    ):
        status, body = call_api(
            "GET", path.format(partner_id=partner_user["id"]), token=observer_token
        )

        assert status == 403, body
        assert "partner" not in body and "partners" not in body

    def test_observer_cannot_export_the_commissions_ledger(self, call_api, observer_token):
        status, body = call_api("GET", "/api/export/commissions", token=observer_token)

        assert status == 403, body

    def test_observer_cannot_comment_on_a_lead(
        self, call_api, observer_token, sample_lead
    ):
        """Reading the statuses is the whole of the access. A comment is a
        write on somebody else's deal."""
        status, body = call_api(
            "POST",
            f"/api/leads/{sample_lead['id']}/comments",
            body={"body": "Nie powinienem móc tego napisać"},
            token=observer_token,
        )

        assert status == 403
        assert "error" in body

    def test_observer_cannot_promote_themselves(self, call_api, observer_token):
        """The containment that makes the role safe to hand out: no path from
        an observer session to a wider role, including through their own
        account."""
        _, me = call_api("GET", "/api/auth/me", token=observer_token)
        observer_id = me["user"]["id"]

        status, body = call_api(
            "PATCH",
            f"/api/users/{observer_id}",
            body={"role": "admin"},
            token=observer_token,
        )

        # Either refused outright, or the role field is dropped as
        # unprivileged -- both are acceptable, a promotion is not.
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT role FROM users WHERE id = ?", (observer_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row["role"] == "observer", body
