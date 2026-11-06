"""
PartnerType -- Lead stage pipeline unit tests.

Covers the lead_stages table, the seeded starting pipeline, stage CRUD,
terminal protection and the six-stage producer limit.
"""
import pytest

from api.db import get_conn
from api import lead_stages


# The seeded SALES pipeline. Registration words live in
# leads.registration_status, and the terminals are named so winning a deal is
# never conflated with paying its commission.
SEED_KEYS = ("contact", "presentation", "offer", "negotiation", "won", "lost")


def _create_producer_stage(name="Nowy etap", user=None, **overrides):
    payload = {"name": name}
    payload.update(overrides)
    return lead_stages.create_stage(payload, user)


class TestStageListing:
    def test_listing_returns_sort_order_with_terminals_last(self, admin_user):
        stages = lead_stages.get_all()

        keys = [s["key"] for s in stages]
        terminal_flags = [bool(s["is_terminal"]) for s in stages]

        # Every non-terminal stage precedes every terminal stage.
        first_terminal = terminal_flags.index(True)
        assert all(not flag for flag in terminal_flags[:first_terminal])
        assert all(flag for flag in terminal_flags[first_terminal:])
        assert set(keys) == set(SEED_KEYS)


class TestStageCreationLimits:
    def test_creating_a_seventh_producer_stage_is_refused(self, admin_user):
        # Seed already carries 4 producer-defined stages (new/pending/
        # in_progress/accepted); two more reach the limit of six.
        _create_producer_stage("Weryfikacja", admin_user)
        _create_producer_stage("Negocjacje", admin_user)

        with pytest.raises(ValueError, match="6"):
            _create_producer_stage("Siódmy etap", admin_user)

    def test_creating_a_win_or_lose_stage_is_refused(self, admin_user):
        with pytest.raises(ValueError):
            lead_stages.create_stage({"name": "Nowy Win", "kind": "win"}, admin_user)

        with pytest.raises(ValueError):
            lead_stages.create_stage({"name": "Nowy Lose", "kind": "lose"}, admin_user)


class TestStageMutation:
    def test_renaming_recolouring_or_reordering_a_terminal_stage_is_refused(
        self, admin_user
    ):
        terminal = next(s for s in lead_stages.get_all() if s["is_terminal"])

        for patch in (
            {"name": "Zamknięty Wygrany"},
            {"color": "#00ff00"},
            {"sort_order": 99},
        ):
            with pytest.raises(ValueError, match="Win/Lose"):
                lead_stages.update_stage(terminal["id"], patch, admin_user)

        unchanged = lead_stages.get_by_id(terminal["id"])
        assert unchanged["name"] == terminal["name"]
        assert unchanged["color"] == terminal["color"]
        assert unchanged["sort_order"] == terminal["sort_order"]

    def test_the_producer_can_name_his_own_stage_in_english_too(self, admin_user):
        """The English name is a second column he fills in, not a translation
        the product can guess -- and leaving it empty has to stay legal,
        because empty is what "not translated yet" looks like."""
        stage = _create_producer_stage(
            "Negocjacje", admin_user, name_en="Negotiation"
        )
        assert stage["name_en"] == "Negotiation"

        renamed = lead_stages.update_stage(
            stage["id"], {"name_en": "Contract talks"}, admin_user
        )
        assert renamed["name_en"] == "Contract talks"
        assert renamed["name"] == "Negocjacje"

        cleared = lead_stages.update_stage(stage["id"], {"name_en": ""}, admin_user)
        assert cleared["name_en"] == ""

    def test_a_stage_created_without_an_english_name_stores_an_empty_one(
        self, admin_user
    ):
        stage = _create_producer_stage("Prezentacja", admin_user)
        assert stage["name_en"] == ""

    def test_changing_a_stage_key_is_refused(self, admin_user):
        stage = _create_producer_stage("Do zmiany", admin_user)

        with pytest.raises(ValueError):
            lead_stages.update_stage(stage["id"], {"key": "nowy_klucz"}, admin_user)


class TestStageDeletion:
    def test_deleting_a_terminal_stage_is_refused(self, admin_user):
        terminal = next(s for s in lead_stages.get_all() if s["is_terminal"])

        with pytest.raises(ValueError):
            lead_stages.delete_stage(terminal["id"], admin_user)

    def test_deleting_an_unoccupied_producer_stage_succeeds(self, admin_user):
        stage = _create_producer_stage("Etap tymczasowy", admin_user)

        assert lead_stages.delete_stage(stage["id"], admin_user) is True
        assert lead_stages.get_by_id(stage["id"]) is None

    def test_deleting_an_occupied_producer_stage_is_refused_naming_the_count(
        self, admin_user, partner_user
    ):
        from api.leads import create_lead

        stage = _create_producer_stage("Etap zajęty", admin_user)
        create_lead(
            partner_user,
            {
                "client_name": "Klient",
                "client_email": "klient@test.pl",
                "company": "Klient sp. z o.o.",
                "value": 1000,
            },
        )
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE leads SET status=? WHERE partner_id=?",
                (stage["key"], partner_user["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(ValueError, match="1"):
            lead_stages.delete_stage(stage["id"], admin_user)


class TestCommissionTriggerReassignment:
    def test_the_commission_trigger_field_is_refused_by_the_update_allowlist(
        self, admin_user
    ):
        """The flag is gone from the schema; an update still naming it must
        be refused outright rather than silently ignored, or a caller
        written against the old model would believe it had configured
        something."""
        stage = _create_producer_stage("Etap sprzedazowy", admin_user)

        with pytest.raises(ValueError):
            lead_stages.update_stage(
                stage["id"], {"is_commission_trigger": True}, admin_user
            )


class TestStageMutationPermissions:
    def test_non_admin_refused_on_every_mutation(self, admin_user, partner_user):
        stage = _create_producer_stage("Etap admina", admin_user)

        # HTTP-handler level checks (mirrors api/commission_tiers.py's pattern):
        status, _ = lead_stages.handle_create(None, partner_user, {"name": "Nielegalny"})
        assert status == 403

        status, _ = lead_stages.handle_update(None, partner_user, stage["id"], {"name": "x"})
        assert status == 403

        status, _ = lead_stages.handle_delete(None, partner_user, stage["id"])
        assert status == 403
