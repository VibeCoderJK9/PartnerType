"""
PartnerType -- Partner-bonuses roster tests.

get_all_partners_roster() is the staff-gated management read path, sibling
to and independent of get_all_partners_summary()'s active-only statistics
query. These tests pin state derivation, the empty database, ordering, the
guard on the roster scope, and the fact the pre-existing statistics path and
default (no-scope) handler branch are byte-identical to before this change.
"""
import uuid
from datetime import datetime, timedelta

from api.db import get_conn
from api.partner_bonuses import (
    get_all_partners_roster,
    get_all_partners_summary,
    handle_list_partners,
)


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _insert_partner(email, name, account_status="pending", active=0, tier_id=None):
    """Insert a partner row directly so account_status/active/tier_id can be
    pinned exactly, independent of the admin-only create/update paths."""
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO users
               (email, password, name, role, level, tier_id, account_status, active)
               VALUES (?,?,?,?,?,?,?,?)""",
            (email, "x", name, "partner", "bronze", tier_id, account_status, active),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        return row["id"]
    finally:
        conn.close()


def _insert_invitation(user_id, accepted=False, expired=False):
    """Insert a partner_invitations row. Each call gets a fresh unique
    token_hash so a partner can hold more than one invitation row."""
    conn = get_conn()
    try:
        now = datetime.utcnow()
        expires_at = (now - timedelta(days=1)) if expired else (now + timedelta(days=7))
        accepted_at = now.isoformat() if accepted else None
        conn.execute(
            """INSERT INTO partner_invitations
               (token_hash, user_id, expires_at, accepted_at)
               VALUES (?,?,?,?)""",
            (uuid.uuid4().hex, user_id, expires_at.isoformat(), accepted_at),
        )
        conn.commit()
    finally:
        conn.close()


def _tier_id(name):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM commission_tiers WHERE name = ?", (name,)
        ).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def _roster_row(roster, partner_id):
    return next(r for r in roster if r["id"] == partner_id)


# ─── STATE DERIVATION ─────────────────────────────────────────────────────────

class TestStateDerivation:
    def test_active_partner_resolves_to_active(self, test_db_path):
        pid = _insert_partner(
            "active@test.pl", "Active Partner", account_status="active", active=1
        )
        assert _roster_row(get_all_partners_roster(), pid)["state"] == "active"

    def test_suspended_partner_resolves_to_suspended(self, test_db_path):
        pid = _insert_partner(
            "suspended@test.pl", "Suspended Partner", account_status="suspended", active=0
        )
        assert _roster_row(get_all_partners_roster(), pid)["state"] == "suspended"

    def test_pending_partner_with_open_invitation_resolves_to_invited_pending(
        self, test_db_path
    ):
        pid = _insert_partner("invited@test.pl", "Invited Partner")
        _insert_invitation(pid)
        assert _roster_row(get_all_partners_roster(), pid)["state"] == "invited_pending"

    def test_pending_partner_with_no_invitation_resolves_to_self_registered(
        self, test_db_path
    ):
        pid = _insert_partner("selfreg@test.pl", "Self Registered Partner")
        assert _roster_row(get_all_partners_roster(), pid)["state"] == "self_registered"

    def test_pending_partner_with_accepted_invitation_resolves_to_self_registered(
        self, test_db_path
    ):
        pid = _insert_partner("accepted-inv@test.pl", "Accepted Invitation Partner")
        _insert_invitation(pid, accepted=True)
        assert _roster_row(get_all_partners_roster(), pid)["state"] == "self_registered"

    def test_pending_partner_with_expired_invitation_resolves_to_self_registered(
        self, test_db_path
    ):
        pid = _insert_partner("expired-inv@test.pl", "Expired Invitation Partner")
        _insert_invitation(pid, expired=True)
        assert _roster_row(get_all_partners_roster(), pid)["state"] == "self_registered"

    def test_partner_with_accepted_and_open_invitation_appears_once_as_invited_pending(
        self, test_db_path
    ):
        pid = _insert_partner("double-inv@test.pl", "Double Invitation Partner")
        _insert_invitation(pid, accepted=True)
        _insert_invitation(pid)

        matches = [r for r in get_all_partners_roster() if r["id"] == pid]
        assert len(matches) == 1
        assert matches[0]["state"] == "invited_pending"


# ─── EMPTY DATABASE + ORDERING ─────────────────────────────────────────────────

class TestEmptyAndOrdering:
    def test_empty_database_returns_empty_list(self, test_db_path):
        assert get_all_partners_roster() == []

    def test_identical_names_and_tiers_come_back_in_ascending_id_order(
        self, test_db_path
    ):
        tier_id = _tier_id("Gold")
        first_id = _insert_partner(
            "dup1@test.pl", "Duplicate Name", account_status="active", active=1,
            tier_id=tier_id,
        )
        second_id = _insert_partner(
            "dup2@test.pl", "Duplicate Name", account_status="active", active=1,
            tier_id=tier_id,
        )

        for _ in range(3):
            ids = [
                r["id"] for r in get_all_partners_roster() if r["name"] == "Duplicate Name"
            ]
            assert ids == [first_id, second_id]

    def test_tier_less_partners_precede_tiered_partners(self, test_db_path):
        tiered_id = _insert_partner(
            "tiered@test.pl", "Tiered Partner", account_status="active", active=1,
            tier_id=_tier_id("Gold"),
        )
        tierless_id = _insert_partner(
            "tierless@test.pl", "Tierless Partner", account_status="active", active=1,
            tier_id=None,
        )

        ids_in_order = [r["id"] for r in get_all_partners_roster()]
        assert ids_in_order.index(tierless_id) < ids_in_order.index(tiered_id)


# ─── STATISTICS PATH UNTOUCHED ─────────────────────────────────────────────────

class TestStatisticsUntouched:
    def test_roster_includes_inactive_accounts_summary_still_excludes_them(
        self, test_db_path
    ):
        pid = _insert_partner(
            "inactive@test.pl", "Inactive Account", account_status="pending", active=0
        )

        roster_ids = [r["id"] for r in get_all_partners_roster()]
        summary_ids = [r["id"] for r in get_all_partners_summary()]

        assert pid in roster_ids
        assert pid not in summary_ids


# ─── GUARD + DEFAULT-BRANCH COMPATIBILITY ──────────────────────────────────────

class TestHandlerGuard:
    def test_roster_scope_refuses_partner_admits_admin_and_manager(
        self, test_db_path, admin_user, manager_user, partner_user
    ):
        status, body = handle_list_partners(None, partner_user, {"scope": ["roster"]})
        assert status == 403
        assert "error" in body

        status, body = handle_list_partners(None, admin_user, {"scope": ["roster"]})
        assert status == 200
        assert "partners" in body

        status, body = handle_list_partners(None, manager_user, {"scope": ["roster"]})
        assert status == 200
        assert "partners" in body

    def test_no_scope_parameter_returns_the_summary_to_staff_only(
        self, test_db_path, admin_user, manager_user, partner_user
    ):
        # The summary carries every partner's e-mail, rates and unpaid cash.
        status, body = handle_list_partners(None, partner_user, {})
        assert status == 403
        assert "partners" not in body

        for staff in (admin_user, manager_user):
            status, body = handle_list_partners(None, staff, {})
            assert status == 200
            assert body == {"partners": get_all_partners_summary()}


# ─── THE PRODUCER'S ENGLISH WORDING REACHES THE PARTNER ──────────────────────
# A bonus name is his own invention, so it travels as data with a second
# column beside it. The partner's screens read both and choose per field; the
# API's job is only to project them and to keep the scope intact.

class TestPartnerSeesBothLanguagesOfHisBonuses:
    def _percent_definition(self, admin_user, **overrides):
        from api.bonus_defs import create_bonus

        payload = {
            "name": "Bonus za webinar",
            "name_en": "Webinar bonus",
            "description": "Prowadzenie webinaru",
            "description_en": "Running a webinar",
            "type": "percent",
            "default_value": 2.0,
        }
        payload.update(overrides)
        return create_bonus(payload, admin_user)

    def test_summary_projects_both_languages_of_name_and_description(
        self, admin_user, partner_user
    ):
        from api.partner_bonuses import assign_bonus, get_partner_summary

        definition = self._percent_definition(admin_user)
        assign_bonus(partner_user["id"], definition["id"], None, admin_user)

        summary = get_partner_summary(partner_user["id"])

        assigned = next(
            b for b in summary["bonus_percent"] if b["bonus_def_id"] == definition["id"]
        )
        assert assigned["name"] == "Bonus za webinar"
        assert assigned["name_en"] == "Webinar bonus"
        assert assigned["description"] == "Prowadzenie webinaru"
        assert assigned["description_en"] == "Running a webinar"

    def test_an_untranslated_description_arrives_empty_not_missing(
        self, admin_user, partner_user
    ):
        # The reader falls back per FIELD, so a translated name with an
        # untranslated description must still carry the empty column.
        from api.partner_bonuses import assign_bonus, get_partner_summary

        definition = self._percent_definition(admin_user, description_en="")
        assign_bonus(partner_user["id"], definition["id"], None, admin_user)

        summary = get_partner_summary(partner_user["id"])

        assigned = next(
            b for b in summary["bonus_percent"] if b["bonus_def_id"] == definition["id"]
        )
        assert assigned["name_en"] == "Webinar bonus"
        assert assigned["description_en"] == ""

    def test_the_percentage_total_is_unchanged_by_the_translation(
        self, admin_user, partner_user
    ):
        from api.partner_bonuses import assign_bonus, get_partner_summary

        before = get_partner_summary(partner_user["id"])["total_percent"]
        definition = self._percent_definition(admin_user)
        assign_bonus(partner_user["id"], definition["id"], None, admin_user)

        after = get_partner_summary(partner_user["id"])["total_percent"]

        assert round(after - before, 4) == 2.0

    def test_a_partner_cannot_read_another_partners_bonuses(
        self, admin_user, partner_user
    ):
        from api.partner_bonuses import assign_bonus, handle_get_partner

        definition = self._percent_definition(admin_user)
        assign_bonus(partner_user["id"], definition["id"], None, admin_user)
        intruder = _insert_partner("intruder@test.pl", "Intruder", "active", 1)

        status, payload = handle_get_partner(
            None, {"id": intruder, "role": "partner"}, partner_user["id"]
        )

        assert status == 403
        assert "bonus_percent" not in payload


def test_roster_lists_rejected_accounts_after_every_other_partner(partner_user, partner_user_bronze):
    # Rejected bots have no tier, and the
    # tier-NULLS-FIRST order put them above every real partner.
    from api.db import get_conn
    from api.partner_bonuses import get_all_partners_roster

    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO users (email, password, name, role, account_status, active, rejected_at)
               VALUES ('aaa-bot@spam.example', 'x', 'AAA Bot', 'partner', 'rejected', 0, now()::text)"""
        )
        conn.commit()
    finally:
        conn.close()

    roster = get_all_partners_roster()

    assert roster[-1]["email"] == "aaa-bot@spam.example"
    assert {p["email"] for p in roster[:-1]} >= {partner_user["email"], partner_user_bronze["email"]}
