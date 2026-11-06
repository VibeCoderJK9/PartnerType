"""Answering a person's request about their own data.

The hard part is not deleting rows; it is deleting exactly the rows that can
afford to go, and being able to say truthfully what survived and why.
"""
import json

import pytest

from api import gdpr
from api.db import get_conn


def _lead(lead_id, email, *, commission=None, partner_id=None, status=None):
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO leads (id, client_name, client_email, client_phone,
                                  company, nip, website, notes, value, commission,
                                  partner_id, status, registration_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'accepted')""",
            (lead_id, "Jan Klient", email, "+48 500 600 700", "Firma Klienta",
             "5260250274", "https://klient.example", "Notatka handlowca",
             5000.0, commission, partner_id, status),
        )
        conn.execute(
            """INSERT INTO lead_workflow_events (lead_id, actor_id, event_type,
                                                 payload_json, created_at)
               VALUES (?,?,?,?,?)""",
            (lead_id, partner_id, "lead.created",
             json.dumps({"client_email": email, "client_phone": "+48 500 600 700"}),
             "2026-01-01T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()


class TestExport:
    def test_it_gathers_the_person_from_every_table_that_mentions_them(self, partner_user):
        _lead("LD-801", "klient@example.test", partner_id=partner_user["id"])

        document = gdpr.export_person("klient@example.test")

        assert document["subject"] == "klient@example.test"
        assert [row["id"] for row in document["leads"]] == ["LD-801"]
        assert document["lead_history"], "historia zmian niesie migawki danych klienta"

    def test_a_partner_asking_about_their_own_account_gets_it_too(self, partner_user):
        document = gdpr.export_person(partner_user["email"])

        assert document["account"]["email"].lower() == partner_user["email"].lower()

    def test_an_address_nobody_knows_yields_an_empty_but_honest_document(self):
        document = gdpr.export_person("nieznany@example.test")

        assert document["account"] is None
        assert document["leads"] == []


class TestPreview:
    def test_it_separates_what_goes_from_what_has_to_stay(self, partner_user):
        _lead("LD-802", "klient@example.test", partner_id=partner_user["id"])
        _lead("LD-803", "klient@example.test", commission=500.0, status="won",
              partner_id=partner_user["id"])

        preview = gdpr.erasure_preview("klient@example.test")

        assert preview["leads_to_delete"] == ["LD-802"]
        assert preview["leads_to_anonymise"] == ["LD-803"]
        assert "prowizj" in preview["keeps"].lower()

    def test_the_preview_changes_nothing(self, partner_user):
        _lead("LD-804", "klient@example.test", partner_id=partner_user["id"])

        gdpr.erasure_preview("klient@example.test")

        conn = get_conn()
        try:
            assert conn.execute("SELECT COUNT(*) AS c FROM leads").fetchone()["c"] == 1
        finally:
            conn.close()


class TestErasure:
    def test_erasing_without_confirmation_is_refused(self, partner_user):
        _lead("LD-805", "klient@example.test", partner_id=partner_user["id"])

        with pytest.raises(ValueError):
            gdpr.erase_person("klient@example.test")

    def test_an_unsettled_lead_is_removed_with_its_comments_and_history(self, partner_user):
        _lead("LD-806", "klient@example.test", partner_id=partner_user["id"])

        removed = gdpr.erase_person("klient@example.test", confirm=True)

        assert removed["leads_deleted"] == 1
        conn = get_conn()
        try:
            assert conn.execute("SELECT COUNT(*) AS c FROM leads").fetchone()["c"] == 0
            assert conn.execute(
                "SELECT COUNT(*) AS c FROM lead_workflow_events").fetchone()["c"] == 0
        finally:
            conn.close()

    def test_a_settled_lead_keeps_its_money_and_loses_the_person(self, partner_user):
        _lead("LD-807", "klient@example.test", commission=500.0, status="won",
              partner_id=partner_user["id"])

        gdpr.erase_person("klient@example.test", confirm=True)

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT client_name, client_email, client_phone, notes, nip, "
                "value, commission FROM leads WHERE id = 'LD-807'").fetchone()
        finally:
            conn.close()
        assert row["value"] == 5000.0
        assert row["commission"] == 500.0
        for column in ("client_email", "client_phone", "notes", "nip"):
            assert row[column] == "", column
        assert row["client_name"] == "[usunięto na żądanie]"

    def test_the_history_payload_is_scrubbed_too(self, partner_user):
        """Clearing the lead row alone would leave the same e-mail and phone
        number sitting one table away, inside the event payload."""
        _lead("LD-808", "klient@example.test", commission=500.0, status="won",
              partner_id=partner_user["id"])

        gdpr.erase_person("klient@example.test", confirm=True)

        conn = get_conn()
        try:
            payloads = [r["payload_json"] for r in conn.execute(
                "SELECT payload_json FROM lead_workflow_events").fetchall()]
        finally:
            conn.close()
        assert all("klient@example.test" not in payload for payload in payloads)

    def test_a_partner_account_is_anonymised_and_locked_out(self, partner_user):
        removed = gdpr.erase_person(partner_user["email"], confirm=True)

        assert removed["account_anonymised"] is True
        conn = get_conn()
        try:
            row = conn.execute("SELECT email, name, active, phone FROM users WHERE id = ?",
                               (partner_user["id"],)).fetchone()
        finally:
            conn.close()
        assert partner_user["email"].lower() not in row["email"].lower()
        assert row["active"] == 0
        assert row["phone"] == ""

    def test_nothing_of_the_person_survives_a_search(self, partner_user):
        """The test a producer would actually run when asked to prove it."""
        _lead("LD-809", "klient@example.test", partner_id=partner_user["id"])
        _lead("LD-810", "klient@example.test", commission=500.0, status="won",
              partner_id=partner_user["id"])

        gdpr.erase_person("klient@example.test", confirm=True)

        conn = get_conn()
        try:
            hits = conn.execute(
                """SELECT COUNT(*) AS c FROM leads
                    WHERE client_email = 'klient@example.test'
                       OR client_phone = '+48 500 600 700'
                       OR nip = '5260250274'""").fetchone()["c"]
        finally:
            conn.close()
        assert hits == 0


class TestMixedCaseClientAddress:
    """Leads keep the client's address as it was typed; the request may use
    any spelling of the same address."""

    MIXED = "Jan.Kowalski@Firma.example"

    def test_the_export_finds_a_lead_typed_with_capitals(self, partner_user):
        _lead("LD-821", self.MIXED, partner_id=partner_user["id"])

        document = gdpr.export_person("jan.kowalski@firma.example")

        assert [row["id"] for row in document["leads"]] == ["LD-821"]

    def test_the_preview_lists_it(self, partner_user):
        _lead("LD-822", self.MIXED, partner_id=partner_user["id"])
        _lead("LD-823", self.MIXED, commission=500.0, status="won",
              partner_id=partner_user["id"])

        preview = gdpr.erasure_preview("JAN.KOWALSKI@FIRMA.EXAMPLE")

        assert preview["leads_to_delete"] == ["LD-822"]
        assert preview["leads_to_anonymise"] == ["LD-823"]

    def test_erasure_removes_and_anonymises_it(self, partner_user):
        _lead("LD-824", self.MIXED, partner_id=partner_user["id"])
        _lead("LD-825", self.MIXED, commission=500.0, status="won",
              partner_id=partner_user["id"])

        removed = gdpr.erase_person("jan.kowalski@firma.example", confirm=True)

        assert removed["leads_deleted"] == 1
        assert removed["leads_anonymised"] == 1
        conn = get_conn()
        try:
            hits = conn.execute(
                "SELECT COUNT(*) AS c FROM leads WHERE lower(client_email) = ?",
                ("jan.kowalski@firma.example",)).fetchone()["c"]
        finally:
            conn.close()
        assert hits == 0


def _comment_and_answer(lead_id, user_id):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO lead_comments (lead_id, user_id, body) VALUES (?, ?, ?)",
            (lead_id, user_id, "Klient prosi o kontakt na prywatny numer 500 600 700"))
        field_id = conn.execute(
            "INSERT INTO lead_fields (label) VALUES ('Stanowisko kontaktu') RETURNING id"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO lead_field_values (lead_id, field_id, value) VALUES (?, ?, ?)",
            (lead_id, field_id, "Dyrektor finansowy"))
        conn.commit()
    finally:
        conn.close()


class TestCompleteness:
    """Every table that names the person is in the export, and nothing that
    names them survives an erasure."""

    def test_a_settled_lead_loses_its_comments_and_answers_too(self, partner_user):
        _lead("LD-831", "klient@example.test", commission=500.0, status="won",
              partner_id=partner_user["id"])
        _comment_and_answer("LD-831", partner_user["id"])

        gdpr.erase_person("klient@example.test", confirm=True)

        conn = get_conn()
        try:
            comments = conn.execute(
                "SELECT COUNT(*) AS n FROM lead_comments WHERE lead_id = 'LD-831'").fetchone()["n"]
            answers = conn.execute(
                "SELECT COUNT(*) AS n FROM lead_field_values WHERE lead_id = 'LD-831'"
            ).fetchone()["n"]
        finally:
            conn.close()
        assert (comments, answers) == (0, 0)

    def test_the_export_carries_lead_answers(self, partner_user):
        _lead("LD-832", "klient@example.test", partner_id=partner_user["id"])
        _comment_and_answer("LD-832", partner_user["id"])

        document = gdpr.export_person("klient@example.test")

        assert [(a["lead_id"], a["label"], a["value"]) for a in document["lead_answers"]] == [
            ("LD-832", "Stanowisko kontaktu", "Dyrektor finansowy")]

    def test_the_export_carries_audit_rows_and_sign_in_attempts_naming_the_address(
        self, partner_user, admin_user
    ):
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO audit_log (user_id, user_name, user_role, action, entity, "
                "entity_id, detail, ip, created_at) VALUES (NULL, 'System', '', "
                "'user.login_failed', 'user', '', ?, '1.2.3.4', '2026-01-01T00:00:00')",
                (f"{partner_user['email'].upper()} (partner)",))
            conn.execute(
                "INSERT INTO login_attempts (email, ip, succeeded) VALUES (?, '1.2.3.4', false)",
                (partner_user["email"],))
            conn.commit()
        finally:
            conn.close()

        document = gdpr.export_person(partner_user["email"])

        assert [e["action"] for e in document["audit_entries"]] == ["user.login_failed"]
        assert [a["ip"] for a in document["login_attempts"]] == ["1.2.3.4"]

    def test_erasure_removes_backup_codes_and_revokes_api_keys(self, partner_user):
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO user_mfa_backup_codes (user_id, code_hash) VALUES (?, 'x')",
                (partner_user["id"],))
            conn.execute(
                "INSERT INTO api_keys (user_id, name, display_prefix, token_hash, expires_at) "
                "VALUES (?, 'k', 'pt_', 'hash-1', now() + interval '30 days')",
                (partner_user["id"],))
            conn.commit()
        finally:
            conn.close()

        gdpr.erase_person(partner_user["email"], confirm=True)

        conn = get_conn()
        try:
            codes = conn.execute(
                "SELECT COUNT(*) AS n FROM user_mfa_backup_codes WHERE user_id = ?",
                (partner_user["id"],)).fetchone()["n"]
            live_keys = conn.execute(
                "SELECT COUNT(*) AS n FROM api_keys WHERE user_id = ? AND revoked_at IS NULL",
                (partner_user["id"],)).fetchone()["n"]
        finally:
            conn.close()
        assert (codes, live_keys) == (0, 0)


class TestAccess:
    @pytest.mark.parametrize("role", ["partner", "manager", "observer"])
    def test_only_the_owner_may_handle_data_requests(self, role):
        user = {"id": 99, "role": role}

        for handler in (gdpr.handle_export, gdpr.handle_erasure_preview, gdpr.handle_erase):
            status, _body = handler(None, user, {"email": "ktos@example.test"})
            assert status == 403, (handler.__name__, role)
