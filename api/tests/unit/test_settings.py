"""
PartnerType -- Settings module unit tests.

Read, write, coercion and permission behaviour for the settings getters, the
bulk setter, and both HTTP handler entry points. Settings feed the commission
calculation directly, so read/write semantics here are load-bearing for money.
"""
import pytest

from api import audit
from api.db import get_conn
from api.settings_api import DEFAULTS, get, get_all, set_many, handle_get, handle_update

from api import email_service as _email_service

# Captured before the autouse mute replaces send() for the suite.
_REAL_SEND = _email_service.send


def _seed_setting(key, value):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


# ─── READ ─────────────────────────────────────────────────────────────────────

class TestGet:
    def test_returns_stored_value_when_row_exists(self, test_db_path):
        _seed_setting("company_name", "Custom Corp")
        assert get("company_name") == "Custom Corp"

    def test_falls_back_to_default_when_no_row_exists(self, test_db_path):
        assert get("currency") == DEFAULTS["currency"]

    def test_returns_empty_string_for_key_absent_from_both(self, test_db_path):
        assert get("totally_unknown_key") == ""


class TestGetAll:
    def test_overlays_stored_rows_on_top_of_defaults(self, test_db_path):
        _seed_setting("company_name", "Overlaid Corp")

        result = get_all()

        assert result["company_name"] == "Overlaid Corp"
        # An unstored key still appears with its default value.
        assert result["currency"] == DEFAULTS["currency"]

    def test_every_commission_rate_key_present_in_result(self, test_db_path):
        result = get_all()
        commission_keys = [k for k in DEFAULTS if k.startswith("commission_")]
        for key in commission_keys:
            assert key in result


# ─── WRITE ────────────────────────────────────────────────────────────────────

class TestSetMany:
    def test_stores_new_value_and_subsequent_read_returns_it(self, admin_user):
        set_many({"company_name": "Written Corp"}, admin_user)
        assert get("company_name") == "Written Corp"

    def test_updates_existing_key_rather_than_inserting_duplicate(self, admin_user):
        set_many({"company_name": "First Write"}, admin_user)
        set_many({"company_name": "Second Write"}, admin_user)

        conn = get_conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM settings WHERE key = ?", ("company_name",)
            ).fetchone()["c"]
        finally:
            conn.close()

        assert count == 1
        assert get("company_name") == "Second Write"

    def test_silently_discards_key_outside_editable_set(self, admin_user):
        set_many({"not_a_real_setting": "value"}, admin_user)

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM settings WHERE key = ?", ("not_a_real_setting",)
            ).fetchone()
        finally:
            conn.close()

        assert row is None

    def test_stamps_updated_timestamp(self, admin_user):
        set_many({"company_name": "Timestamped Corp"}, admin_user)

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT updated_at FROM settings WHERE key = ?", ("company_name",)
            ).fetchone()
        finally:
            conn.close()

        assert row["updated_at"]

    def test_coerces_non_string_value_to_string_form(self, admin_user):
        set_many({"commission_bronze": 12}, admin_user)
        assert get("commission_bronze") == "12"

    @pytest.mark.parametrize("policy", ["queue", "reject", "allow"])
    def test_accepts_each_duplicate_policy(self, admin_user, policy):
        set_many({"lead_duplicate_policy": policy}, admin_user)
        assert get("lead_duplicate_policy") == policy

    def test_rejects_unknown_duplicate_policy(self, admin_user):
        with pytest.raises(ValueError, match="Polityka duplikatów"):
            set_many({"lead_duplicate_policy": "notify"}, admin_user)

    @pytest.mark.parametrize(
        "shares, message",
        [
            ("100,50,25,10,5", "Ostatni udział"),
            ("100,25,50,10,0", "nie mogą rosnąć"),
            ("100,50,nan,10,0", "skończoną liczbą"),
            ("100,50,101,10,0", "od 0 do 100"),
            ("100,50,25,10,0,0", "od 1 do 5"),
        ],
    )
    def test_rejects_invalid_queue_shares(self, admin_user, shares, message):
        with pytest.raises(ValueError, match=message):
            set_many({"lead_queue_shares": shares}, admin_user)

    def test_accepts_matching_share_list_and_queue_max(self, admin_user):
        set_many({"lead_queue_shares": "100,50,0", "lead_queue_max": "3"}, admin_user)

        assert get("lead_queue_shares") == "100,50,0"
        assert get("lead_queue_max") == "3"

    @pytest.mark.parametrize("maximum", ["0", "6", "2.5", "many"])
    def test_rejects_invalid_queue_max(self, admin_user, maximum):
        with pytest.raises(ValueError, match="Maksymalna liczba"):
            set_many({"lead_queue_max": maximum}, admin_user)

    def test_rejects_queue_max_that_differs_from_share_count(self, admin_user):
        with pytest.raises(ValueError, match="równa liczbie udziałów"):
            set_many({"lead_queue_max": "3"}, admin_user)


# ─── PERMISSIONS ──────────────────────────────────────────────────────────────

class TestPermissions:
    def test_set_many_raises_permission_error_for_partner(self, partner_user):
        with pytest.raises(PermissionError):
            set_many({"company_name": "Hack Corp"}, partner_user)

    def test_handle_update_maps_permission_error_to_403(self, partner_user):
        """handle_update must not raise PermissionError directly,
        which api.router.dispatch has no except clause for -- a non-admin
        PATCH /api/settings request would surface as an opaque 500 instead of
        a 403. In line with every other HTTP handler in this codebase
        (api/leads.py, api/users.py, api/comments.py, ...), which all catch
        PermissionError locally and return (403, {"error": ...})."""
        status, body = handle_update(None, partner_user, {"company_name": "Hack Corp"})
        assert status == 403
        assert "error" in body

    def test_a_moderator_cannot_switch_the_staff_mfa_requirement(self, admin_user, manager_user):
        set_many({"mfa_required_for_staff": "1"}, admin_user)

        status, body = handle_update(None, manager_user, {"mfa_required_for_staff": "0"})

        assert status == 403
        assert get("mfa_required_for_staff") == "1"

    def test_a_moderator_cannot_switch_it_on_either(self, manager_user):
        with pytest.raises(PermissionError):
            set_many({"mfa_required_for_staff": "1"}, manager_user)
        assert get("mfa_required_for_staff") == "0"

    def test_a_moderator_saving_the_form_with_the_value_unchanged_still_saves(
        self, manager_user
    ):
        set_many({"company_name": "Nowa nazwa", "mfa_required_for_staff": "0"}, manager_user)
        assert get("company_name") == "Nowa nazwa"

    def test_the_owner_can_switch_it(self, admin_user):
        set_many({"mfa_required_for_staff": "1"}, admin_user)
        assert get("mfa_required_for_staff") == "1"

    @pytest.mark.parametrize("value", ["yes please", "2", "", "on"])
    def test_only_zero_or_one_is_stored(self, admin_user, value):
        from api.validation import ValidationError
        with pytest.raises(ValidationError):
            set_many({"mfa_required_for_staff": value}, admin_user)
        assert get("mfa_required_for_staff") == "0"

    def test_handle_get_returns_full_map_to_admin(self, admin_user):
        status, body = handle_get(None, admin_user, {})
        assert status == 200
        assert set(DEFAULTS.keys()).issubset(set(body["settings"].keys()))
        assert not [key for key in body["settings"] if key.startswith("smtp_")]

    def test_handle_get_returns_only_public_queue_settings_to_partner(self, partner_user):
        status, body = handle_get(None, partner_user, {})
        assert status == 200
        assert body["settings"] == {
            "company_name": DEFAULTS["company_name"],
            "currency": DEFAULTS["currency"],
            "lead_queue_shares": DEFAULTS["lead_queue_shares"],
            "lead_queue_max": DEFAULTS["lead_queue_max"],
        }


# ─── DISCLOSURE ───────────────────────────────────────────────────────────────

class TestNoMailCredentialDisclosure:
    def test_partner_facing_response_excludes_mail_transport_keys(self, partner_user):
        _seed_setting("smtp_pass", "SYNTHETIC-NOT-A-REAL-SECRET")

        status, body = handle_get(None, partner_user, {})

        mail_keys = ("smtp_host", "smtp_port", "smtp_user", "smtp_pass", "smtp_from", "smtp_enabled")
        for key in mail_keys:
            assert key not in body["settings"]
        assert "SYNTHETIC-NOT-A-REAL-SECRET" not in body["settings"].values()


class TestMailIsTheOperators:
    """Mail server settings are the system's, not something
    a producer configures. The operator sets one mailbox per server and each
    instance receives it as environment variables."""

    def test_admin_sees_no_mail_server_settings_even_from_an_older_instance(self, admin_user):
        for key, value in (("smtp_host", "smtp.legacy.test"), ("smtp_enabled", "1"),
                           ("smtp_pass", "SYNTHETIC-NOT-A-REAL-SECRET")):
            _seed_setting(key, value)

        status, body = handle_get(None, admin_user, {})

        assert status == 200
        assert not [key for key in body["settings"] if key.startswith("smtp_")]
        assert "SYNTHETIC-NOT-A-REAL-SECRET" not in str(body)

    def test_patch_cannot_store_mail_server_settings(self, admin_user):
        audit.log.reset_mock()
        handle_update(None, admin_user, {"smtp_host": "evil.test", "smtp_pass": "must-not-be-stored"})
        assert not audit.log.called
        with get_conn() as conn:
            assert conn.execute("SELECT 1 FROM settings WHERE key LIKE 'smtp_%'").fetchone() is None

    def test_production_reads_the_operator_environment(self, monkeypatch):
        monkeypatch.setenv('PARTNERTYPE_ENV', 'production')
        monkeypatch.setenv('PARTNERTYPE_SMTP_HOST', 'smtp.provider.test')
        monkeypatch.setenv('PARTNERTYPE_SMTP_PORT', '2525')
        monkeypatch.setenv('PARTNERTYPE_SMTP_USER', 'mailer')
        monkeypatch.setenv('PARTNERTYPE_SMTP_PASSWORD', 'synthetic-secret')
        monkeypatch.setenv('PARTNERTYPE_SMTP_FROM', 'no-reply@inpartner.test')
        from api.email_service import _get_smtp_config
        config = _get_smtp_config()
        assert (config['host'], config['port'], config['user'], config['passwd'], config['from'], config['enabled']) == (
            'smtp.provider.test', 2525, 'mailer', 'synthetic-secret', 'no-reply@inpartner.test', True)

    def test_production_without_a_sender_has_no_made_up_default(self, monkeypatch):
        monkeypatch.setenv('PARTNERTYPE_ENV', 'production')
        monkeypatch.setenv('PARTNERTYPE_SMTP_HOST', 'smtp.provider.test')
        monkeypatch.delenv('PARTNERTYPE_SMTP_FROM', raising=False)
        from api.email_service import _get_smtp_config
        assert _get_smtp_config()['from'] == ''

    def test_a_host_without_a_sender_refuses_to_send_and_says_why(self, monkeypatch, caplog):
        import smtplib
        from api import email_service
        monkeypatch.setenv('PARTNERTYPE_ENV', 'production')
        monkeypatch.setenv('PARTNERTYPE_SMTP_HOST', 'smtp.provider.test')
        monkeypatch.delenv('PARTNERTYPE_SMTP_FROM', raising=False)
        monkeypatch.setattr(smtplib, 'SMTP', lambda *a, **k: pytest.fail('no connection may be opened'))
        with caplog.at_level('ERROR', logger=email_service.logger.name):
            result = _REAL_SEND('client@example.test', 'Subject', '<p>Body</p>', 'Body')
        assert result == {'sent': False, 'reason': 'sender_not_configured'}
        assert 'PARTNERTYPE_SMTP_FROM' in caplog.text

    def test_production_without_a_host_is_off(self, monkeypatch):
        monkeypatch.setenv('PARTNERTYPE_ENV', 'production')
        monkeypatch.delenv('PARTNERTYPE_SMTP_HOST', raising=False)
        from api.email_service import _get_smtp_config
        assert _get_smtp_config()['enabled'] is False

    def test_the_producer_name_heads_the_from_line(self):
        _seed_setting("company_name", "Acme\r\nBcc: x@evil.test")
        from api.email_service import _sender
        sender = _sender("no-reply@inpartner.test")
        assert sender.endswith("<no-reply@inpartner.test>")
        assert "\r" not in sender and "\n" not in sender

    def test_staging_mail_is_forced_to_capture(self, monkeypatch):
        monkeypatch.setenv('PARTNERTYPE_ENV', 'staging')
        from api.email_service import _get_smtp_config
        config = _get_smtp_config()
        assert (config['host'], config['port'], config['capture']) == ('mail',1025,True)


class TestSettingsChangesAreAudited:
    """A queue-share or duplicate-policy change moves money between partners,
    so it must leave the same who/what/old->new trail as any other action.

    The autouse mute_side_effects fixture patches api.audit.log, so these
    assert against that call rather than against audit_log rows.
    """

    def _settings_calls(self):
        return [
            call.args
            for call in audit.log.call_args_list
            if call.args and call.args[1] == "settings.update"
        ]

    def test_queue_share_change_records_the_actor_and_both_values(self, admin_user):
        set_many({"lead_queue_shares": "100,50,25,10,0"}, admin_user)
        audit.log.reset_mock()

        set_many({"lead_queue_shares": "100,40,20,10,0"}, admin_user)

        calls = self._settings_calls()
        assert len(calls) == 1
        actor, _action, entity, entity_id, detail = calls[0]
        assert actor is admin_user
        assert entity == "settings"
        assert entity_id == "lead_queue_shares"
        assert "100,50,25,10,0" in detail
        assert "100,40,20,10,0" in detail

    def test_duplicate_policy_change_is_recorded(self, admin_user):
        audit.log.reset_mock()

        set_many({"lead_duplicate_policy": "reject"}, admin_user)

        calls = self._settings_calls()
        assert len(calls) == 1
        assert calls[0][3] == "lead_duplicate_policy"
        assert "queue" in calls[0][4] and "reject" in calls[0][4]

    def test_writing_an_unchanged_value_records_nothing(self, admin_user):
        set_many({"company_name": "Stable Corp"}, admin_user)
        audit.log.reset_mock()

        set_many({"company_name": "Stable Corp"}, admin_user)

        assert self._settings_calls() == []

    def test_a_rejected_change_leaves_no_audit_entry(self, admin_user):
        audit.log.reset_mock()

        with pytest.raises(ValueError):
            set_many({"lead_queue_shares": "not,a,number"}, admin_user)

        assert self._settings_calls() == []

    def test_one_entry_per_changed_key_in_a_multi_key_write(self, admin_user):
        audit.log.reset_mock()

        set_many(
            {"lead_duplicate_policy": "allow", "company_name": DEFAULTS["company_name"]},
            admin_user,
        )

        # company_name is written at its existing value, so only the policy
        # counts as a change.
        assert [c[3] for c in self._settings_calls()] == ["lead_duplicate_policy"]


# ─── BONUS DEFINITIONS: THE PRODUCER'S OWN ENGLISH WORDING ───────────────────
# A bonus name and its description are words the PRODUCER invents ("Bonus za
# webinar"), so the app's dictionary can never cover them. Same contract as
# lead_stages.name_en and registration_fields.label_en: a second column he
# fills in himself, empty meaning "not translated yet" rather than blank copy.

class TestBonusDefinitionEnglishColumns:
    def test_migration_adds_both_english_columns(self, test_db_path):
        conn = get_conn()
        try:
            columns = {
                row[1]
                for row in conn.execute("SELECT ordinal_position, column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='bonus_definitions'").fetchall()
            }
        finally:
            conn.close()

        assert "name_en" in columns
        assert "description_en" in columns

    def test_existing_rows_get_an_empty_translation_never_a_guessed_one(self, test_db_path):
        # The seeded factory bonuses are Polish. Nothing may invent English for
        # them -- an empty column is what makes Ui.producerLabel fall back.
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT name_en, description_en FROM bonus_definitions"
            ).fetchall()
        finally:
            conn.close()

        assert rows, "the factory catalogue should be seeded in a fresh test db"
        assert all(r["name_en"] == "" and r["description_en"] == "" for r in rows)


class TestBonusDefinitionEnglishRoundtrip:
    def test_create_then_read_returns_both_languages_exactly(self, admin_user):
        from api.bonus_defs import create_bonus, get_by_id

        created = create_bonus(
            {
                "name": "Bonus za webinar",
                "name_en": "Webinar bonus",
                "description": "Prowadzenie webinaru",
                "description_en": "Running a webinar",
                "type": "percent",
                "default_value": 2.0,
            },
            admin_user,
        )

        stored = get_by_id(created["id"])
        assert stored["name"] == "Bonus za webinar"
        assert stored["name_en"] == "Webinar bonus"
        assert stored["description"] == "Prowadzenie webinaru"
        assert stored["description_en"] == "Running a webinar"

    def test_create_without_english_stores_empty_not_null(self, admin_user):
        from api.bonus_defs import create_bonus

        created = create_bonus(
            {"name": "Bonus kwartalny", "type": "percent", "default_value": 1.0},
            admin_user,
        )

        assert created["name_en"] == ""
        assert created["description_en"] == ""

    def test_patch_without_the_field_keeps_the_previous_translation(self, admin_user):
        from api.bonus_defs import create_bonus, update_bonus

        created = create_bonus(
            {"name": "Bonus za event", "name_en": "Event bonus",
             "type": "percent", "default_value": 2.5},
            admin_user,
        )

        updated = update_bonus(created["id"], {"default_value": 3.0}, admin_user)

        assert updated["name_en"] == "Event bonus"
        assert updated["default_value"] == 3.0

    def test_empty_string_clears_a_translation(self, admin_user):
        from api.bonus_defs import create_bonus, update_bonus

        created = create_bonus(
            {"name": "Bonus za event", "name_en": "Event bonus",
             "description_en": "Attend an event", "type": "percent",
             "default_value": 2.5},
            admin_user,
        )

        updated = update_bonus(
            created["id"], {"name_en": "  ", "description_en": ""}, admin_user
        )

        assert updated["name_en"] == ""
        assert updated["description_en"] == ""
        # Clearing the translation never touches the Polish original.
        assert updated["name"] == "Bonus za event"

    def test_translation_is_trimmed(self, admin_user):
        from api.bonus_defs import create_bonus

        created = create_bonus(
            {"name": "Bonus", "name_en": "  Webinar bonus  ",
             "type": "percent", "default_value": 1.0},
            admin_user,
        )

        assert created["name_en"] == "Webinar bonus"

    def test_english_name_over_one_hundred_characters_is_refused(self, admin_user):
        from api.bonus_defs import create_bonus

        with pytest.raises(ValueError):
            create_bonus(
                {"name": "Bonus", "name_en": "x" * 101,
                 "type": "percent", "default_value": 1.0},
                admin_user,
            )

    def test_english_description_over_one_thousand_characters_is_refused(self, admin_user):
        from api.bonus_defs import create_bonus

        with pytest.raises(ValueError):
            create_bonus(
                {"name": "Bonus", "description_en": "x" * 1001,
                 "type": "percent", "default_value": 1.0},
                admin_user,
            )

    def test_english_columns_do_not_change_the_rate_or_the_limit(self, admin_user):
        from api.bonus_defs import MAX_CASH, create_bonus, get_all

        conn = get_conn()
        try:
            conn.execute("DELETE FROM bonus_definitions")
            conn.commit()
        finally:
            conn.close()

        for i in range(MAX_CASH):
            create_bonus(
                {"name": f"Nagroda {i}", "name_en": f"Award {i}", "type": "cash",
                 "default_value": 500.0},
                admin_user,
            )

        with pytest.raises(ValueError):
            create_bonus(
                {"name": "Jeszcze jedna", "name_en": "One more", "type": "cash",
                 "default_value": 500.0},
                admin_user,
            )

        cash = [b for b in get_all() if b["type"] == "cash"]
        assert len(cash) == MAX_CASH
        assert all(b["default_value"] == 500.0 for b in cash)


class TestBonusDefinitionWritesRequireConfigureRights:
    """The role check lives in the canonical writer, not only in the HTTP
    handler: api/agent_actions.py and any future caller reach these functions
    directly, and a translation field must not be the way in."""

    def test_partner_cannot_create_a_bonus_definition(self, partner_user):
        from api.bonus_defs import create_bonus, get_all

        before = len(get_all())
        with pytest.raises(PermissionError):
            create_bonus(
                {"name": "Mój bonus", "type": "percent", "default_value": 99.0},
                partner_user,
            )
        assert len(get_all()) == before

    def test_partner_cannot_translate_a_bonus_definition(self, admin_user, partner_user):
        from api.bonus_defs import create_bonus, get_by_id, update_bonus

        created = create_bonus(
            {"name": "Bonus za webinar", "type": "percent", "default_value": 2.0},
            admin_user,
        )

        with pytest.raises(PermissionError):
            update_bonus(created["id"], {"name_en": "Mine now"}, partner_user)

        assert get_by_id(created["id"])["name_en"] == ""

    def test_manager_may_configure_bonuses(self, manager_user):
        from api.bonus_defs import create_bonus

        created = create_bonus(
            {"name": "Bonus opiekuna", "name_en": "Manager bonus",
             "type": "percent", "default_value": 1.0},
            manager_user,
        )

        assert created["name_en"] == "Manager bonus"
