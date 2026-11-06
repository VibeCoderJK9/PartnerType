"""
PartnerType -- Partners module unit tests.

Aggregation, ordering, scoping and admin-only access for get_partners,
get_partner and their two HTTP handler entry points.
"""
from api.db import get_conn
from api.partners import get_partners, get_partner, handle_list, handle_get


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _insert_lead(partner_id, lead_id, status="new", commission=None, created_at=None,
                 registration_status="pending"):
    """Insert a lead row directly, bypassing create_lead, so status and
    commission can be pinned exactly regardless of the admin-only update path."""
    conn = get_conn()
    try:
        params = [
            lead_id, "Test Client", "client@test.pl", "Test Co", 10000,
            status, partner_id, commission, registration_status,
        ]
        sql = """INSERT INTO leads
                 (id, client_name, client_email, company, value, status, partner_id, commission,
                  registration_status{extra})
                 VALUES (?,?,?,?,?,?,?,?,?{extra_q})"""
        if created_at:
            sql = sql.format(extra=", created_at, updated_at", extra_q=",?,?")
            params.extend([created_at, created_at])
        else:
            sql = sql.format(extra="", extra_q="")
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


# ─── AGGREGATION ──────────────────────────────────────────────────────────────

class TestGetPartnersAggregation:
    def test_partner_with_no_leads_has_zero_counts(self, partner_user):
        partners = get_partners()

        row = next(p for p in partners if p["id"] == partner_user["id"])
        assert row["total_leads"] == 0
        assert row["accepted_leads"] == 0
        assert row["total_earnings"] == 0

    def test_three_leads_one_accepted_reports_counts(self, partner_user):
        # Acceptance is the registration axis; `status` is the sales stage.
        _insert_lead(partner_user["id"], "LD-T-001", status="won", commission=500,
                     registration_status="accepted")
        _insert_lead(partner_user["id"], "LD-T-002", status=None)
        _insert_lead(partner_user["id"], "LD-T-003", status=None, registration_status="rejected")

        row = next(p for p in get_partners() if p["id"] == partner_user["id"])
        assert row["total_leads"] == 3
        assert row["accepted_leads"] == 1

    def test_total_earnings_sums_only_non_null_commissions(self, partner_user):
        _insert_lead(partner_user["id"], "LD-T-010", status="accepted", commission=300)
        _insert_lead(partner_user["id"], "LD-T-011", status="accepted", commission=700)
        _insert_lead(partner_user["id"], "LD-T-012", status="new", commission=None)

        row = next(p for p in get_partners() if p["id"] == partner_user["id"])
        assert row["total_earnings"] == 1000

    def test_listing_ordered_by_total_earnings_descending(
        self, partner_user, partner_user_bronze
    ):
        _insert_lead(partner_user["id"], "LD-T-020", status="accepted", commission=100)
        _insert_lead(partner_user_bronze["id"], "LD-T-021", status="accepted", commission=900)

        partners = get_partners()
        ids_in_order = [p["id"] for p in partners]

        assert ids_in_order.index(partner_user_bronze["id"]) < ids_in_order.index(
            partner_user["id"]
        )

    def test_inactive_partner_excluded_from_listing(self, test_db_path):
        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO users (email, password, name, role, level, active)
                   VALUES (?,?,?,?,?,0)""",
                ("inactive.partner@test.pl", "x", "Inactive Partner", "partner", "bronze"),
            )
            conn.commit()
            inactive_id = conn.execute(
                "SELECT id FROM users WHERE email = ?", ("inactive.partner@test.pl",)
            ).fetchone()["id"]
        finally:
            conn.close()

        ids = [p["id"] for p in get_partners()]
        assert inactive_id not in ids

    def test_admin_accounts_never_appear_in_listing(self, admin_user):
        _insert_lead(admin_user["id"], "LD-T-030", status="accepted", commission=999)

        ids = [p["id"] for p in get_partners()]
        assert admin_user["id"] not in ids


# ─── SINGLE PARTNER ───────────────────────────────────────────────────────────

class TestGetPartner:
    def test_known_partner_returns_aggregate_and_leads(self, partner_user):
        _insert_lead(
            partner_user["id"], "LD-T-040", status="new",
            created_at="2020-01-01T00:00:00",
        )
        _insert_lead(
            partner_user["id"], "LD-T-041", status="new",
            created_at="2020-01-02T00:00:00",
        )

        result = get_partner(partner_user["id"])

        assert result["id"] == partner_user["id"]
        assert result["total_leads"] == 2
        assert "leads" in result
        assert [l["id"] for l in result["leads"]] == ["LD-T-041", "LD-T-040"]

    def test_unknown_identifier_returns_none(self, test_db_path):
        assert get_partner(999999) is None

    def test_admin_identifier_returns_none(self, admin_user):
        assert get_partner(admin_user["id"]) is None

    def test_inactive_partner_is_hidden_by_function_and_handler(
        self, test_db_path, admin_user
    ):
        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO users (email, password, name, role, level, active)
                   VALUES (?,?,?,?,?,0)""",
                ("inactive2.partner@test.pl", "x", "Inactive Partner Two", "partner", "bronze"),
            )
            conn.commit()
            inactive_id = conn.execute(
                "SELECT id FROM users WHERE email = ?", ("inactive2.partner@test.pl",)
            ).fetchone()["id"]
        finally:
            conn.close()

        assert get_partner(inactive_id) is None
        status, body = handle_get(None, admin_user, inactive_id)
        assert status == 404
        assert body == {"error": "Partner nie znaleziony."}


# ─── ACCESS CONTROL ───────────────────────────────────────────────────────────

class TestHandlerAccess:
    def test_handle_list_refuses_partner(self, partner_user):
        status, body = handle_list(None, partner_user)
        assert status == 403
        assert "error" in body

    def test_handle_get_refuses_partner(self, partner_user):
        status, body = handle_get(None, partner_user, partner_user["id"])
        assert status == 403
        assert "error" in body

    def test_handle_get_returns_404_for_unknown_id(self, admin_user):
        status, body = handle_get(None, admin_user, 999999)
        assert status == 404
        assert "error" in body

    def test_handle_list_success_returns_200_with_partners_key(self, admin_user):
        status, body = handle_list(None, admin_user)
        assert status == 200
        assert "partners" in body

    def test_handle_get_success_returns_200_with_partner_key(
        self, admin_user, partner_user
    ):
        status, body = handle_get(None, admin_user, partner_user["id"])
        assert status == 200
        assert "partner" in body


# ─── DISCLOSURE ───────────────────────────────────────────────────────────────

class TestNoPasswordDisclosure:
    def test_get_partners_never_includes_password(self, partner_user):
        for row in get_partners():
            assert "password" not in row

    def test_get_partner_never_includes_password(self, partner_user):
        result = get_partner(partner_user["id"])
        assert "password" not in result
