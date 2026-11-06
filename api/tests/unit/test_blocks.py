"""
PartnerType -- Blocks.

A block fits the shared core to one producer's process. These tests pin the
contract every block relies on: off by default, configured only by staff who
may configure, validated numbers, a failing block never breaking the write
that triggered it, and the first real block -- the inactive-partner reminder --
reminding once per stretch of silence.
"""
from datetime import datetime, timedelta

import pytest

from api import blocks
from api import notifications
from api.db import get_conn, run_maintenance
from api.validation import ValidationError


def _set_partner_created(partner, when):
    conn = get_conn()
    try:
        conn.execute("UPDATE users SET created_at = ? WHERE id = ?",
                     (when.strftime("%Y-%m-%d %H:%M:%S"), partner["id"]))
        conn.commit()
    finally:
        conn.close()


def _insert_lead(partner, lead_id, when):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO leads (id, partner_id, client_name, client_email, company, "
            "registration_status, created_at) VALUES (?, ?, 'Klient', 'k@example.test', "
            "'Firma', 'pending', ?)",
            (lead_id, partner["id"], when.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _enable(admin, days=30):
    return blocks.update_block("inactive_partner", {"enabled": True, "config": {"days": days}}, admin)


class TestRegistryAndConfig:
    def test_builtin_block_is_listed_and_off_by_default(self, admin_user):
        listed = {b["id"]: b for b in blocks.list_blocks()}
        block = listed["inactive_partner"]
        assert block["enabled"] is False
        assert block["config"] == {"days": 30}
        assert block["scheduled"] is True

    def test_enabling_persists_with_the_chosen_number(self, admin_user):
        _enable(admin_user, days=14)
        block = {b["id"]: b for b in blocks.list_blocks()}["inactive_partner"]
        assert block["enabled"] is True
        assert block["config"] == {"days": 14}

    def test_toggling_off_keeps_the_numbers(self, admin_user):
        _enable(admin_user, days=14)
        blocks.update_block("inactive_partner", {"enabled": False}, admin_user)
        block = {b["id"]: b for b in blocks.list_blocks()}["inactive_partner"]
        assert block["enabled"] is False
        assert block["config"] == {"days": 14}

    @pytest.mark.parametrize("payload", [
        {"enabled": "yes"},
        {"config": {"days": 3}},
        {"config": {"days": 181}},
        {"config": {"days": "30"}},
        {"config": {"days": True}},
        {"config": {"hours": 5}},
        {"surprise": 1},
        ["not", "an", "object"],
    ])
    def test_rejects_invalid_updates(self, admin_user, payload):
        with pytest.raises(ValidationError):
            blocks.update_block("inactive_partner", payload, admin_user)

    def test_unknown_block_is_a_lookup_error(self, admin_user):
        with pytest.raises(LookupError):
            blocks.update_block("no_such_block", {"enabled": True}, admin_user)


class TestHttpGuards:
    def test_partner_cannot_list_or_change_blocks(self, partner_user):
        assert blocks.handle_list(None, partner_user, {})[0] == 403
        assert blocks.handle_update(None, partner_user, "inactive_partner", {"enabled": True})[0] == 403
        assert blocks.handle_run(None, partner_user, "inactive_partner")[0] == 403

    def test_manager_may_configure(self, manager_user):
        status, body = blocks.handle_update(None, manager_user, "inactive_partner", {"enabled": True})
        assert status == 200 and body["block"]["enabled"] is True

    def test_run_refuses_a_disabled_block(self, admin_user):
        assert blocks.handle_run(None, admin_user, "inactive_partner")[0] == 409

    def test_unknown_block_is_404(self, admin_user):
        assert blocks.handle_update(None, admin_user, "nope_block", {})[0] == 404
        assert blocks.handle_run(None, admin_user, "nope_block")[0] == 404


class TestInactivePartnerReminder:
    def test_disabled_block_does_nothing(self, admin_user, partner_user):
        _set_partner_created(partner_user, datetime.utcnow() - timedelta(days=90))
        assert blocks.run_scheduled() == {}
        notifications.notify_all_admins.assert_not_called()

    def test_reminds_once_about_a_quiet_partner(self, admin_user, partner_user):
        now = datetime.utcnow()
        _set_partner_created(partner_user, now - timedelta(days=40))
        _enable(admin_user)

        assert blocks.run_scheduled(now=now)["inactive_partner"]["reminded"] == 1
        args, kwargs = notifications.notify_all_admins.call_args
        assert args[0] == "warning"
        assert kwargs["title_key"] == "ntf_block_inactive_title"
        assert kwargs["params"]["days"] == 40
        assert args[3] == "partners.html"

        # Same stretch of silence, next day: no second reminder.
        assert blocks.run_scheduled(now=now + timedelta(days=1))["inactive_partner"]["reminded"] == 0
        assert notifications.notify_all_admins.call_count == 1

        # Still quiet a full period later: remind again.
        assert blocks.run_scheduled(now=now + timedelta(days=31))["inactive_partner"]["reminded"] == 1

    def test_a_recent_lead_keeps_the_partner_active(self, admin_user, partner_user):
        now = datetime.utcnow()
        _set_partner_created(partner_user, now - timedelta(days=200))
        _insert_lead(partner_user, "LD-901", now - timedelta(days=5))
        _enable(admin_user)
        assert blocks.run_scheduled(now=now)["inactive_partner"] == {"checked": 1, "reminded": 0}

    def test_new_lead_starts_a_new_stretch(self, admin_user, partner_user):
        now = datetime.utcnow()
        _set_partner_created(partner_user, now - timedelta(days=100))
        _enable(admin_user)
        blocks.run_scheduled(now=now)
        _insert_lead(partner_user, "LD-902", now + timedelta(days=1))
        later = now + timedelta(days=35)
        assert blocks.run_scheduled(now=later)["inactive_partner"]["reminded"] == 1

    def test_run_endpoint_reports_what_it_did(self, admin_user, partner_user):
        _set_partner_created(partner_user, datetime.utcnow() - timedelta(days=60))
        _enable(admin_user)
        status, body = blocks.handle_run(None, admin_user, "inactive_partner")
        assert status == 200
        assert body["result"] == {"checked": 1, "reminded": 1}

    def test_daily_maintenance_runs_enabled_blocks(self, admin_user, partner_user):
        _set_partner_created(partner_user, datetime.utcnow() - timedelta(days=60))
        _enable(admin_user)
        assert run_maintenance()["blocks"]["inactive_partner"]["reminded"] == 1


class TestEvents:
    @pytest.fixture
    def listening_block(self, admin_user):
        heard = []

        def on_event(event_type, lead_id, actor_id, config):
            heard.append((event_type, lead_id))
            if event_type == "boom":
                raise RuntimeError("client data must not reach the log")

        block = blocks.Block(id="test_listener", name_key="x_name", description_key="x_desc",
                             events=("lead.created", "boom"), on_event=on_event)
        blocks.register(block)
        try:
            blocks.update_block("test_listener", {"enabled": True}, admin_user)
            yield heard
        finally:
            blocks.REGISTRY.pop("test_listener", None)

    def test_enabled_block_hears_its_events_only(self, listening_block):
        blocks.emit("lead.created", "LD-001", 1)
        blocks.emit("stage.move", "LD-001", 1)
        assert listening_block == [("lead.created", "LD-001")]

    def test_a_failing_block_never_raises(self, listening_block, caplog):
        blocks.emit("boom", "LD-002", 1)
        assert ("boom", "LD-002") in listening_block
        assert "client data" not in caplog.text

    def test_disabled_block_hears_nothing(self, listening_block, admin_user):
        blocks.update_block("test_listener", {"enabled": False}, admin_user)
        blocks.emit("lead.created", "LD-003", 1)
        assert listening_block == []
