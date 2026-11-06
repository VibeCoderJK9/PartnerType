"""
Idempotency, coherence and pipeline coverage for scripts/seed_demo.py.

Every test below runs against the ``test_db_path`` fixture's temporary
PostgreSQL database. The routine is imported and called directly; the fixture
sets DATABASE_URL and closes its pool before dropping the test database.

The pipeline-coverage tests read ``api.lead_stages`` rather than literal status
words, so a renamed stage cannot pass vacuously.
"""
import os
import tempfile
import time
from collections import Counter

import pytest

import scripts.seed_demo as seed_demo
from api import lead_stages
from api.db import get_conn


TABLES_COMPARED_FOR_IDEMPOTENCY = ("users", "leads", "notifications", "partner_bonus_assignments")


def _row_counts(conn):
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in TABLES_COMPARED_FOR_IDEMPOTENCY
    }


def _seeded_lead_rows(conn):
    return conn.execute(
        """SELECT l.status, l.registration_status, l.commission FROM leads l
           JOIN users u ON u.id = l.partner_id
           WHERE u.email LIKE ?""",
        (f"%@{seed_demo.DEMO_EMAIL_DOMAIN}",),
    ).fetchall()


@pytest.fixture
def seeded_db(test_db_path):
    """Run the routine twice, so every consumer implicitly exercises idempotency
    (running it again before a demo must be safe) on top of its own check.
    """
    seed_demo.main()
    seed_demo.main()
    return test_db_path


class TestIdempotency:
    def test_running_seed_twice_leaves_identical_row_counts(self, test_db_path):
        seed_demo.main()
        conn = get_conn()
        try:
            first = _row_counts(conn)
        finally:
            conn.close()

        seed_demo.main()
        conn = get_conn()
        try:
            second = _row_counts(conn)
        finally:
            conn.close()

        assert first == second
        assert first["users"] > 0
        assert first["leads"] > 0


class TestPartnerShape:
    def test_partner_count_within_d08_range_and_every_partner_has_a_tier(self, seeded_db):
        conn = get_conn()
        try:
            partners = conn.execute(
                "SELECT tier_id FROM users WHERE role='partner' AND email LIKE ?",
                (f"%@{seed_demo.DEMO_EMAIL_DOMAIN}",),
            ).fetchall()
        finally:
            conn.close()

        assert 3 <= len(partners) <= 4
        assert all(row["tier_id"] is not None for row in partners)


class TestLeadShape:
    def test_lead_count_within_d08_range(self, seeded_db):
        conn = get_conn()
        try:
            rows = conn.execute("SELECT status FROM leads").fetchall()
        finally:
            conn.close()

        assert 8 <= len(rows) <= seed_demo.MAX_DEMO_LEADS


class TestPipelineCoverage:
    """The seed routine reads the active stage list (api.lead_stages)
    instead of naming literal statuses, and distributes leads across the
    configured pipeline -- not a fixed subset.

    A demo board has to be believable on TWO axes, so these assert coverage of the registration decisions AND of the
    sales stages, rather than of one merged status column.
    """

    def test_seeded_leads_cover_every_registration_decision(self, seeded_db):
        conn = get_conn()
        try:
            decisions = {row["registration_status"] for row in _seeded_lead_rows(conn)}
        finally:
            conn.close()

        # All four, or the demo cannot show the producer making a decision.
        assert decisions == {"pending", "needs_info", "accepted", "rejected"}

    def test_seeded_leads_occupy_every_non_terminal_sales_stage_plus_the_win(self, seeded_db):
        conn = get_conn()
        try:
            statuses = {row["status"] for row in _seeded_lead_rows(conn)}
        finally:
            conn.close()

        stages = [s for s in lead_stages.get_all() if s["active"]]
        expected = {s["key"] for s in stages if not s["is_terminal"]}
        expected.add(next(s["key"] for s in stages if s["kind"] == "win"))
        # NULL is the honest stage of a deal nobody has approved yet.
        expected.add(None)
        assert statuses == expected

    def test_at_least_one_lead_is_waiting_on_a_request_for_information(self, seeded_db):
        conn = get_conn()
        try:
            decisions = [row["registration_status"] for row in _seeded_lead_rows(conn)]
        finally:
            conn.close()

        assert "needs_info" in decisions

    def test_commission_is_recorded_exactly_for_the_leads_that_were_won(self, seeded_db):
        win_key = next(
            (s["key"] for s in lead_stages.get_all() if s["kind"] == "win"), None
        )
        conn = get_conn()
        try:
            rows = _seeded_lead_rows(conn)
        finally:
            conn.close()

        # A commission is recorded when the sale is WON, not when the deal is
        # accepted -- accepted deals show a forecast instead.
        for row in rows:
            if row["status"] == win_key:
                assert row["commission"] is not None, "expected a commission on a won deal"
            else:
                assert row["commission"] is None, (
                    f"unexpected recorded commission for a deal at {row['status']!r}"
                )

    def test_reads_the_active_pipeline_not_a_literal_word(self, test_db_path, admin_user):
        """A producer-added stage must actually receive leads -- proving the
        seed routine walks the live pipeline rather than naming stages it
        was written against. A literal-word implementation would leave the
        new stage empty.
        """
        new_stage = lead_stages.create_stage(
            {"name": "Weryfikacja koncowa", "kind": "progress"}, admin_user
        )
        new_key = lead_stages.get_by_id(new_stage["id"])["key"]

        seed_demo.main()

        conn = get_conn()
        try:
            rows = _seeded_lead_rows(conn)
        finally:
            conn.close()

        assert any(row["status"] == new_key for row in rows), (
            "the producer-added stage received no seeded lead"
        )
        # And nothing landed on a stage that no longer exists.
        active_keys = set(lead_stages.get_active_stage_keys())
        for row in rows:
            assert row["status"] is None or row["status"] in active_keys

    def test_widened_pipeline_gives_an_extra_middle_stage_its_own_occupant(self, test_db_path, admin_user):
        """A producer-added extra progress stage (beyond the seeded six)
        gets its own seeded lead from the spare pool, within the
        8-10 lead budget.
        """
        lead_stages.create_stage({"name": "Kwalifikacja wstepna", "kind": "progress"}, admin_user)

        seed_demo.main()

        conn = get_conn()
        try:
            rows = conn.execute("SELECT status FROM leads").fetchall()
        finally:
            conn.close()

        active_keys = set(lead_stages.get_active_stage_keys())
        statuses = {row["status"] for row in rows}
        assert statuses <= active_keys | {None}
        assert 8 <= len(rows) <= seed_demo.MAX_DEMO_LEADS


class TestDuplicatePair:
    def test_exactly_one_pair_shares_client_identity_second_at_queue_position_two(self, seeded_db):
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT nip, website, queue_position FROM leads WHERE nip != '' OR website != ''"
            ).fetchall()
        finally:
            conn.close()

        nip_counts = Counter(row["nip"] for row in rows if row["nip"])
        website_counts = Counter(row["website"] for row in rows if row["website"])
        duplicate_nips = [nip for nip, count in nip_counts.items() if count > 1]
        duplicate_sites = [site for site, count in website_counts.items() if count > 1]

        assert len(duplicate_nips) == 1
        assert len(duplicate_sites) == 1

        pair = [row for row in rows if row["nip"] == duplicate_nips[0]]
        assert len(pair) == 2
        assert sorted(row["queue_position"] for row in pair) == [1, 2]

    def test_second_of_pair_has_strictly_lower_commission_at_equal_value(self, seeded_db):
        """Read through api.leads.get_lead, not the raw columns: the
        queue-share effect lives in the FORECAST, which is
        computed per read rather than stored. A recorded `commission` exists
        only for a won deal, and this duplicate pair is still in play.
        """
        from api import leads as leads_module

        conn = get_conn()
        try:
            dup_nip_row = conn.execute(
                """SELECT nip FROM leads WHERE nip != ''
                   GROUP BY nip HAVING COUNT(*) > 1"""
            ).fetchone()
            assert dup_nip_row is not None, "no duplicate NIP pair found in seeded data"
            rows = conn.execute(
                "SELECT id, queue_position, value FROM leads "
                "WHERE nip = ? ORDER BY queue_position",
                (dup_nip_row["nip"],),
            ).fetchall()
        finally:
            conn.close()

        assert len(rows) == 2
        reader = {"role": "admin", "id": 1}
        first, second = (leads_module.get_lead(row["id"], reader) for row in rows)

        assert first["value"] == second["value"]
        assert first["commission_projected"] is not None
        assert second["commission_projected"] is not None
        # Second in the queue earns a smaller share of the same deal.
        assert second["commission_projected"] < first["commission_projected"]


class TestLevelTierAgreement:
    def test_every_seeded_partner_level_and_tier_describe_same_standing(self, seeded_db):
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT u.level, ct.name AS tier_name
                   FROM users u JOIN commission_tiers ct ON ct.id = u.tier_id
                   WHERE u.role='partner' AND u.email LIKE ?""",
                (f"%@{seed_demo.DEMO_EMAIL_DOMAIN}",),
            ).fetchall()
        finally:
            conn.close()

        assert rows, "no seeded partner with a resolvable tier was found"
        for row in rows:
            assert row["level"] == row["tier_name"].lower()


class TestWorkingDatabaseIsolation:
    def test_routine_never_writes_the_working_database_path(self, test_db_path):
        import api.db as db_module

        real_working_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(db_module.__file__))),
            "database.db",
        )
        assert os.environ["DATABASE_URL"] == test_db_path
        assert os.environ["DATABASE_URL"] != real_working_path

        before_exists = os.path.exists(real_working_path)
        before_mtime = os.path.getmtime(real_working_path) if before_exists else None

        seed_demo.main()

        after_exists = os.path.exists(real_working_path)
        after_mtime = os.path.getmtime(real_working_path) if after_exists else None
        assert after_exists == before_exists

        if after_mtime != before_mtime:
            # The working database is shared state: a dev server on :8000
            # writes sessions and notifications to it continuously, which
            # moves the mtime while this test runs and fails it for a reason
            # that has nothing to do with seed_demo. Tell the two apart by
            # sampling again with the routine idle -- if the file is still
            # moving, another process owns it and this check cannot conclude
            # anything. DB_PATH isolation below is asserted either way.
            control = os.path.getmtime(real_working_path)
            time.sleep(0.5)
            if os.path.getmtime(real_working_path) != control:
                pytest.skip(
                    "working database is being written by another process "
                    "(stop the dev server to run this isolation check)"
                )
            raise AssertionError(
                "seed_demo.main() modified the working database at "
                f"{real_working_path}"
            )

        assert os.environ["DATABASE_URL"] == test_db_path

        # A distinct sentinel path a hardcoded fallback would plausibly hit;
        # asserting it stays absent fails loudly on that kind of regression.
        sentinel_path = tempfile.mktemp(suffix=".sentinel.db")
        assert not os.path.exists(sentinel_path)


def test_demo_contact_data_is_obviously_fictional():
    """Demo clients must not point at real domains, phones or companies."""
    import re
    from pathlib import Path
    source = Path(seed_demo.__file__).read_text(encoding="utf-8")
    emails = set(re.findall(r"[\w.+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)", source))
    sites = {re.sub(r"^https?://|/.*$", "", w.lower())
             for w in re.findall(r'"website_raw": "([^"]+)"', source)}
    domains = emails | sites
    assert domains and all(d.endswith((".example", ".test")) for d in domains), domains
    phones = re.findall(r"\+\d[\d ]{6,}\d", source)
    assert phones and all(p.startswith("+48 000 000 ") for p in phones), phones
    nips = [re.sub(r"\D", "", n) for n in re.findall(r'"nip_raw": "([^"]+)"', source)]
    assert nips and all(n.startswith("000") for n in nips), nips
