"""The CSV export speaks the producer's own vocabulary.

The "Partner tier" column used to carry `users.level` -- the
bronze/silver/gold/platinum word that predates commission tiers entirely --
so a producer who named their tiers "Diament" or "Srebro" got a spreadsheet
naming levels from a system they never configured. These tests pin the column
to commission_tiers, the one tier system the app actually shows.
"""
import csv
import io

from api.db import get_conn
from api.export import _commissions_csv


def _rows(csv_text):
    return list(csv.reader(io.StringIO(csv_text)))


def _pay(lead_id, amount=700):
    conn = get_conn()
    try:
        conn.execute("UPDATE leads SET commission = ? WHERE id = ?", (amount, lead_id))
        conn.commit()
    finally:
        conn.close()


class TestCommissionsCsvTier:
    def test_column_names_the_producers_tier_not_the_legacy_level(
        self, admin_user, partner_user, sample_lead
    ):
        _pay(sample_lead["id"])
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE commission_tiers SET name = 'Diament' WHERE id = ?",
                (partner_user["tier_id"],),
            )
            conn.commit()
        finally:
            conn.close()

        header, row = _rows(_commissions_csv(admin_user))
        level_column = header.index("Poziom partnera")
        assert row[level_column] == "Diament"
        # The partner's users.level is still 'gold'; it must not reach the file.
        assert "Gold" not in row and "gold" not in row

    def test_partner_without_a_tier_reads_no_tier(
        self, admin_user, partner_user, sample_lead
    ):
        _pay(sample_lead["id"])
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE users SET tier_id = NULL WHERE id = ?", (partner_user["id"],)
            )
            conn.commit()
        finally:
            conn.close()

        header, row = _rows(_commissions_csv(admin_user))
        assert row[header.index("Poziom partnera")] == "Brak poziomu"


class TestFormulaInjection:
    """Partner-typed text must never reach the producer's spreadsheet as
    live formulas."""

    def test_formula_like_text_is_written_as_plain_text(self, admin_user, sample_lead):
        from api.export import _leads_csv

        conn = get_conn()
        try:
            conn.execute(
                "UPDATE leads SET client_name = ?, company = ?, notes = ? WHERE id = ?",
                ('=HYPERLINK("http://evil.example","Kliknij")', "+cmd|' /C calc'!A0",
                 "@SUM(1+1)", sample_lead["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        header, row = _rows(_leads_csv(admin_user))

        for column, planted in (("Klient", '=HYPERLINK("http://evil.example","Kliknij")'),
                                ("Firma klienta", "+cmd|' /C calc'!A0"),
                                ("Uwagi", "@SUM(1+1)")):
            cell = row[header.index(column)]
            assert cell == "'" + planted
            assert not cell.startswith(("=", "+", "-", "@"))

    def test_ordinary_text_and_numbers_are_untouched(self, admin_user, sample_lead):
        from api.export import _safe_cell

        assert _safe_cell("Hurtownia Mebli") == "Hurtownia Mebli"
        assert _safe_cell(-5000.0) == -5000.0
        assert _safe_cell(None) is None


class TestStatusColumn:
    """The column carries readable names, not keys like 'won'."""

    def test_status_is_the_stage_name_or_the_decision(self, admin_user, sample_lead):
        from api.export import _leads_csv

        conn = get_conn()
        try:
            stage = conn.execute("SELECT key, name FROM lead_stages WHERE kind = 'win' LIMIT 1").fetchone()
            conn.execute("UPDATE leads SET status = ?, registration_status = 'accepted' WHERE id = ?",
                         (stage["key"], sample_lead["id"]))
            conn.commit()
        finally:
            conn.close()

        header, row = _rows(_leads_csv(admin_user))
        assert row[header.index("Status")] == stage["name"]

    def test_a_lead_without_a_stage_reads_as_its_decision(self, admin_user, sample_lead):
        from api.export import _leads_csv

        conn = get_conn()
        try:
            conn.execute("UPDATE leads SET status = NULL, registration_status = 'rejected' WHERE id = ?",
                         (sample_lead["id"],))
            conn.commit()
        finally:
            conn.close()

        header, row = _rows(_leads_csv(admin_user))
        assert row[header.index("Status")] == "Odrzucony"
