"""Retention is a decision, not a default.

These tests exist mostly to prove the negative: nothing ages out on its own,
nothing goes without an explicit period and an explicit confirmation, and the
one category that carries money is anonymised rather than deleted.
"""
import pytest

from api import retention
from api.db import get_conn, run_maintenance


def _lead(lead_id, *, created_at, status=None, registration_status="accepted",
          won_at=None, settlement_status=None, partner_id=None):
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO leads (id, client_name, client_email, company, value,
                                  partner_id, created_at, status,
                                  registration_status, won_at, settlement_status,
                                  commission)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (lead_id, "Jan Klient", "jan@example.test", "Firma", 1000.0,
             partner_id, created_at, status, registration_status, won_at,
             settlement_status, 100.0),
        )
        conn.commit()
    finally:
        conn.close()


class TestSurvey:
    def test_it_reports_volume_age_and_what_the_storage_costs(self, partner_user):
        _lead("LD-901", created_at="2020-01-01T00:00:00", status="lost",
              partner_id=partner_user["id"])

        report = retention.survey()

        lost = report["categories"]["leads_lost"]
        assert lost["rows"] == 1
        assert lost["older_than_a_year"] == 1
        assert lost["oldest"].startswith("2020")
        # The size is what turns this into a storage conversation as well.
        assert lost["table_bytes"] > 0
        assert lost["table_size"]

    def test_every_category_explains_what_deleting_it_would_cost(self):
        report = retention.survey()

        for key, spec in retention.CATEGORIES.items():
            assert report["categories"][key]["note"], key


class TestNothingHappensByItself:
    def test_the_daily_maintenance_never_touches_business_records(self, partner_user):
        """run_maintenance sweeps sessions and spent links. A lost lead from
        2020 is a business record and must survive it untouched."""
        _lead("LD-902", created_at="2020-01-01T00:00:00", status="lost",
              partner_id=partner_user["id"])

        run_maintenance()

        conn = get_conn()
        try:
            assert conn.execute(
                "SELECT COUNT(*) AS c FROM leads WHERE id = 'LD-902'"
            ).fetchone()["c"] == 1
        finally:
            conn.close()

    def test_a_preview_changes_nothing(self, partner_user):
        _lead("LD-903", created_at="2020-01-01T00:00:00", status="lost",
              partner_id=partner_user["id"])

        result = retention.preview({"leads_lost": 365})

        assert result["leads_lost"]["rows_affected"] == 1
        conn = get_conn()
        try:
            assert conn.execute(
                "SELECT COUNT(*) AS c FROM leads WHERE id = 'LD-903'"
            ).fetchone()["c"] == 1
        finally:
            conn.close()

    def test_applying_without_saying_so_is_refused(self, partner_user):
        _lead("LD-904", created_at="2020-01-01T00:00:00", status="lost",
              partner_id=partner_user["id"])

        with pytest.raises(ValueError):
            retention.apply({"leads_lost": 365})

    def test_a_category_left_out_of_the_policy_is_kept(self, partner_user):
        _lead("LD-905", created_at="2020-01-01T00:00:00", status="lost",
              partner_id=partner_user["id"])

        retention.apply({"notifications": 30}, confirm=True)

        conn = get_conn()
        try:
            assert conn.execute(
                "SELECT COUNT(*) AS c FROM leads WHERE id = 'LD-905'"
            ).fetchone()["c"] == 1
        finally:
            conn.close()


class TestApplying:
    def test_old_lost_leads_go_and_recent_ones_stay(self, partner_user):
        from datetime import datetime
        _lead("LD-906", created_at="2020-01-01T00:00:00", status="lost",
              partner_id=partner_user["id"])
        _lead("LD-907", created_at=datetime.utcnow().isoformat(), status="lost",
              partner_id=partner_user["id"])

        performed = retention.apply({"leads_lost": 365}, confirm=True)

        assert performed["leads_lost"] == 1
        conn = get_conn()
        try:
            remaining = [r["id"] for r in
                         conn.execute("SELECT id FROM leads ORDER BY id").fetchall()]
        finally:
            conn.close()
        assert remaining == ["LD-907"]

    def test_a_settled_won_lead_loses_the_person_but_keeps_the_money(self, partner_user):
        """Deleting it would take the commission with it and the books would
        stop adding up, so this category is anonymised instead."""
        _lead("LD-908", created_at="2020-01-01T00:00:00", status="won",
              won_at="2020-02-01T00:00:00", settlement_status="paid",
              partner_id=partner_user["id"])

        performed = retention.apply({"leads_won_settled": 365}, confirm=True)

        assert performed["leads_won_settled"] == 1
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT client_name, client_email, value, commission FROM leads "
                "WHERE id = 'LD-908'").fetchone()
        finally:
            conn.close()
        assert row["client_name"] == ""
        assert row["client_email"] == ""
        assert row["value"] == 1000.0
        assert row["commission"] == 100.0


def test_anonymising_settled_leads_also_clears_history_comments_and_answers(partner_user):
    _lead("LD-950", created_at="2020-01-01T00:00:00", status="won",
          won_at="2020-02-01T00:00:00", settlement_status="paid",
          partner_id=partner_user["id"])
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO lead_workflow_events (lead_id, actor_id, event_type, payload_json, "
            "created_at) VALUES ('LD-950', ?, 'lead.created', ?, '2020-01-01T00:00:00')",
            (partner_user["id"], '{"client_email": "jan@example.test"}'))
        conn.execute(
            "INSERT INTO lead_comments (lead_id, user_id, body) VALUES ('LD-950', ?, ?)",
            (partner_user["id"], "Jan prosi o telefon po 18, prywatny 500 600 700"))
        field_id = conn.execute(
            "INSERT INTO lead_fields (label) VALUES ('PESEL kontaktu') RETURNING id"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO lead_field_values (lead_id, field_id, value) VALUES ('LD-950', ?, ?)",
            (field_id, "90010112345"))
        conn.commit()
    finally:
        conn.close()

    retention.apply({"leads_won_settled": 365}, confirm=True)

    conn = get_conn()
    try:
        payloads = [r["payload_json"] for r in conn.execute(
            "SELECT payload_json FROM lead_workflow_events WHERE lead_id = 'LD-950'")]
        comments = conn.execute(
            "SELECT COUNT(*) AS n FROM lead_comments WHERE lead_id = 'LD-950'").fetchone()["n"]
        answers = conn.execute(
            "SELECT COUNT(*) AS n FROM lead_field_values WHERE lead_id = 'LD-950'").fetchone()["n"]
    finally:
        conn.close()
    assert all("jan@example.test" not in p for p in payloads)
    assert comments == 0
    assert answers == 0


def _reject_long_ago(user_id):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE users SET account_status = 'rejected', active = 0, "
            "rejected_at = '2020-01-01T00:00:00' WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def _user_exists(user_id):
    conn = get_conn()
    try:
        return conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is not None
    finally:
        conn.close()


def test_a_rejected_partner_with_leads_is_kept_and_the_rest_of_maintenance_runs(
    partner_user, partner_user_bronze
):
    _lead("LD-960", created_at="2026-01-01T00:00:00", partner_id=partner_user["id"])
    _reject_long_ago(partner_user["id"])
    _reject_long_ago(partner_user_bronze["id"])
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at, created_at) "
            "VALUES ('stary-token', ?, '2020-01-01T00:00:00', '2020-01-01T00:00:00')",
            (partner_user_bronze["id"],))
        conn.commit()
    finally:
        conn.close()

    removed = run_maintenance()

    assert _user_exists(partner_user["id"])          # has leads: kept
    assert not _user_exists(partner_user_bronze["id"])  # no history: purged
    assert removed["rejected_partners"] == 1
    assert "expired_sessions" in removed
