"""Second factor for the producer's own roles.

The dangerous failure here is not a weak code; it is locking the owner out of
his own system, so the enrolment path and the backup codes get as much
attention as the verification does.
"""
from api.tests.context import carry_context
import time

import pyotp
import pytest

from api import mfa
from api.auth import complete_mfa_login, handle_login, handle_mfa_login, login, MfaRequired
from api.db import get_conn


class _Handler:
    def __init__(self, ip="198.51.100.20"):
        self.client_address = (ip, 5555)


def _enrol(user):
    """Walk the real enrolment: begin, read the secret, confirm with a code."""
    started = mfa.begin_enrolment(user)
    codes = mfa.confirm_enrolment(user, pyotp.TOTP(started["secret"]).now())
    return started["secret"], codes


class TestEnrolment:
    def test_the_secret_is_not_readable_in_the_database(self, admin_user):
        secret, _codes = _enrol(admin_user)

        conn = get_conn()
        try:
            stored = conn.execute("SELECT secret FROM user_mfa").fetchone()["secret"]
        finally:
            conn.close()

        assert secret not in stored

    def test_the_qr_is_drawn_here_so_the_secret_never_reaches_a_javascript_library(
        self, admin_user
    ):
        started = mfa.begin_enrolment(admin_user)

        assert started["qr_svg"].lstrip().startswith("<?xml") or "<svg" in started["qr_svg"]
        assert started["secret"]

    def test_a_wrong_code_does_not_switch_the_factor_on(self, admin_user):
        mfa.begin_enrolment(admin_user)

        with pytest.raises(ValueError):
            mfa.confirm_enrolment(admin_user, "000000")

        assert mfa.status_for(admin_user["id"])["enabled"] is False

    def test_an_unfinished_enrolment_can_be_started_again(self, admin_user):
        """Somebody whose phone died mid-setup must not need an administrator."""
        first = mfa.begin_enrolment(admin_user)
        second = mfa.begin_enrolment(admin_user)

        assert first["secret"] != second["secret"]
        mfa.confirm_enrolment(admin_user, pyotp.TOTP(second["secret"]).now())
        assert mfa.status_for(admin_user["id"])["enabled"] is True

    def test_confirming_hands_over_backup_codes_exactly_once(self, admin_user):
        _secret, codes = _enrol(admin_user)

        assert len(codes) == mfa.BACKUP_CODE_COUNT
        conn = get_conn()
        try:
            stored = [r["code_hash"] for r in
                      conn.execute("SELECT code_hash FROM user_mfa_backup_codes").fetchall()]
        finally:
            conn.close()
        # Only fingerprints survive; the printed list is the only readable copy.
        assert all(code not in stored for code in codes)


class TestLogin:
    def test_the_password_alone_no_longer_opens_a_session(self, admin_user):
        _enrol(admin_user)

        with pytest.raises(MfaRequired):
            login(admin_user["email"], admin_user["plain_password"], "admin")

        conn = get_conn()
        try:
            assert conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"] == 0
        finally:
            conn.close()

    def test_the_handler_answers_with_a_challenge_and_no_cookie(self, admin_user):
        _enrol(admin_user)
        handler = _Handler()

        status, body = handle_login(handler, {
            "email": admin_user["email"],
            "password": admin_user["plain_password"],
            "role": "admin",
        })

        assert status == 200
        assert body["mfa_required"] is True
        assert body["challenge"]
        assert "user" not in body
        assert not handler.__dict__.get("_response_headers")

    def test_the_right_code_finishes_the_login(self, admin_user):
        secret, _codes = _enrol(admin_user)
        _status, body = handle_login(_Handler(), {
            "email": admin_user["email"],
            "password": admin_user["plain_password"],
            "role": "admin",
        })

        result = complete_mfa_login(body["challenge"], pyotp.TOTP(secret).now())

        assert result is not None
        user, token = result
        assert user["id"] == admin_user["id"]
        assert token

    def test_a_backup_code_works_once_and_then_never_again(self, admin_user):
        _secret, codes = _enrol(admin_user)

        assert mfa.verify_for_login(admin_user["id"], codes[0]) is True
        assert mfa.verify_for_login(admin_user["id"], codes[0]) is False
        assert mfa.verify_for_login(admin_user["id"], codes[1]) is True

    def test_a_spent_challenge_cannot_be_replayed(self, admin_user):
        secret, _codes = _enrol(admin_user)
        _status, body = handle_login(_Handler(), {
            "email": admin_user["email"],
            "password": admin_user["plain_password"],
            "role": "admin",
        })
        complete_mfa_login(body["challenge"], pyotp.TOTP(secret).now())

        with pytest.raises(ValueError):
            complete_mfa_login(body["challenge"], pyotp.TOTP(secret).now())

    def test_a_wrong_code_leaves_the_challenge_usable_for_another_try(self, admin_user):
        secret, _codes = _enrol(admin_user)
        _status, body = handle_login(_Handler(), {
            "email": admin_user["email"],
            "password": admin_user["plain_password"],
            "role": "admin",
        })

        assert complete_mfa_login(body["challenge"], "000000") is None

        # Mistyping six digits must not mean typing the password again.
        assert complete_mfa_login(body["challenge"], pyotp.TOTP(secret).now()) is not None


def _wrong_code(secret):
    """Six digits that no current step of this secret accepts."""
    totp = pyotp.TOTP(secret)
    valid = {totp.at(time.time(), offset) for offset in (-1, 0, 1)}
    return next(c for c in ("000000", "111111", "222222", "333333") if c not in valid)


def _challenge(user):
    _status, body = handle_login(_Handler(), {
        "email": user["email"],
        "password": user["plain_password"],
        "role": user["role"],
    })
    return body["challenge"]


class TestWrongCodeLimit:
    """Six digits are only a second factor if they cannot all be tried."""

    def test_after_too_many_wrong_codes_even_the_right_one_is_refused(self, admin_user):
        secret, _codes = _enrol(admin_user)
        challenge = _challenge(admin_user)

        for _ in range(mfa.MFA_MAX_FAILURES - 1):
            assert complete_mfa_login(challenge, _wrong_code(secret)) is None
        with pytest.raises(mfa.TooManyCodeAttempts):
            complete_mfa_login(challenge, _wrong_code(secret))

        with pytest.raises(ValueError):
            complete_mfa_login(challenge, pyotp.TOTP(secret).now())
        conn = get_conn()
        try:
            assert conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"] == 0
            assert conn.execute("SELECT COUNT(*) AS c FROM mfa_challenges").fetchone()["c"] == 0
        finally:
            conn.close()

    def test_a_fresh_challenge_from_the_password_does_not_reset_the_count(self, admin_user):
        secret, _codes = _enrol(admin_user)
        for _ in range(mfa.MFA_MAX_FAILURES - 1):
            assert complete_mfa_login(_challenge(admin_user), _wrong_code(secret)) is None
        with pytest.raises(mfa.TooManyCodeAttempts):
            complete_mfa_login(_challenge(admin_user), _wrong_code(secret))

        with pytest.raises(mfa.TooManyCodeAttempts):
            complete_mfa_login(_challenge(admin_user), pyotp.TOTP(secret).now())

    def test_the_login_handler_answers_429_once_the_codes_are_used_up(self, admin_user):
        secret, _codes = _enrol(admin_user)
        challenge = _challenge(admin_user)
        statuses = [
            handle_mfa_login(_Handler(), {"challenge": challenge, "code": _wrong_code(secret)})[0]
            for _ in range(mfa.MFA_MAX_FAILURES)
        ]
        assert statuses == [401] * (mfa.MFA_MAX_FAILURES - 1) + [429]

    def test_switching_the_factor_off_shares_the_same_limit(self, partner_user):
        secret, codes = _enrol(partner_user)
        for _ in range(mfa.MFA_MAX_FAILURES):
            mfa.handle_disable(None, partner_user, {"code": _wrong_code(secret)})

        status, body = mfa.handle_disable(None, partner_user, {"code": codes[0]})

        assert status == 429
        assert mfa.status_for(partner_user["id"])["enabled"] is True

    def test_a_right_code_clears_the_count(self, admin_user):
        secret, codes = _enrol(admin_user)
        for _ in range(mfa.MFA_MAX_FAILURES - 1):
            assert mfa.verify_for_login(admin_user["id"], _wrong_code(secret)) is False
        assert mfa.verify_for_login(admin_user["id"], codes[0]) is True
        for _ in range(mfa.MFA_MAX_FAILURES - 1):
            assert mfa.verify_for_login(admin_user["id"], _wrong_code(secret)) is False

    def test_a_non_text_code_is_a_wrong_code_not_a_crash(self, admin_user):
        _enrol(admin_user)
        assert mfa.verify_for_login(admin_user["id"], 123456) is False


class TestCodeReuse:
    """An app code opens one door once, even while it is still fresh."""

    def test_the_same_app_code_is_accepted_only_once(self, admin_user):
        secret, _codes = _enrol(admin_user)
        code = pyotp.TOTP(secret).now()

        assert mfa.verify_for_login(admin_user["id"], code) is True
        assert mfa.verify_for_login(admin_user["id"], code) is False

    def test_an_older_code_is_refused_after_a_newer_one(self, admin_user):
        secret, _codes = _enrol(admin_user)
        totp = pyotp.TOTP(secret)
        now = time.time()

        assert mfa.verify_for_login(admin_user["id"], totp.at(now)) is True
        assert mfa.verify_for_login(admin_user["id"], totp.at(now, -1)) is False

    def test_a_code_seen_at_one_login_cannot_finish_the_next(self, admin_user):
        secret, _codes = _enrol(admin_user)
        code = pyotp.TOTP(secret).now()

        assert complete_mfa_login(_challenge(admin_user), code) is not None
        assert complete_mfa_login(_challenge(admin_user), code) is None

    def test_one_challenge_cannot_be_redeemed_twice_at_the_same_time(self, admin_user):
        import threading

        secret, codes = _enrol(admin_user)
        challenge = _challenge(admin_user)
        barrier = threading.Barrier(2)
        results = []

        def redeem(code):
            barrier.wait()
            try:
                results.append(complete_mfa_login(challenge, code))
            except ValueError:
                results.append(None)

        # Two different valid codes, so only the challenge lock can stop the
        # second request.
        threads = [threading.Thread(target=carry_context(redeem), args=(c,)) for c in (codes[0], codes[1])]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sum(1 for r in results if r is not None) == 1
        conn = get_conn()
        try:
            assert conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"] == 1
        finally:
            conn.close()


class TestScope:
    def test_producer_roles_are_the_ones_that_must_hold_it(self):
        assert set(mfa.MFA_REQUIRED_ROLES) == {"admin", "manager", "observer"}
        assert "partner" not in mfa.MFA_REQUIRED_ROLES

    @pytest.mark.parametrize("environment", ["production", "staging", "development", "test"])
    def test_nobody_is_forced_by_default_in_any_environment(self, monkeypatch, environment):
        """The producer decides, not the environment name.

        A compulsory factor would stop the account owner from taking their own
        factor off to set it up again on their own phone.
        """
        monkeypatch.delenv("PARTNERTYPE_REQUIRE_MFA", raising=False)
        monkeypatch.setenv("PARTNERTYPE_ENV", environment)

        assert mfa.enforcement_enabled() is False
        assert mfa.is_required_for("admin") is False

    def test_the_producer_can_require_it_from_his_team(self, monkeypatch, admin_user):
        from api import settings_api

        monkeypatch.delenv("PARTNERTYPE_REQUIRE_MFA", raising=False)
        settings_api.set_many({"mfa_required_for_staff": "1"}, admin_user)

        assert mfa.enforcement_enabled() is True
        assert mfa.is_required_for("admin") is True
        assert mfa.is_required_for("manager") is True
        # Never partners: they see only their own work, and demanding an
        # authenticator app to join a programme costs more sign-ups than it
        # prevents incidents.
        assert mfa.is_required_for("partner") is False

    def test_a_broken_stored_value_leaves_the_owner_able_to_sign_in(self, monkeypatch, admin_user):
        from api import settings_api

        monkeypatch.delenv("PARTNERTYPE_REQUIRE_MFA", raising=False)
        # The form refuses such a value now, so the broken row is written
        # directly, as an older instance or a hand edit could have left it.
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('mfa_required_for_staff', 'moze')")
            conn.commit()
        finally:
            conn.close()
        assert settings_api.get("mfa_required_for_staff") == "moze"

        # A setting that gates sign-in fails towards letting people in, not
        # towards locking the account owner out of their own instance.
        assert mfa.enforcement_enabled() is False

    def test_the_environment_variable_still_wins_over_the_setting(self, monkeypatch, admin_user):
        from api import settings_api

        settings_api.set_many({"mfa_required_for_staff": "1"}, admin_user)
        monkeypatch.setenv("PARTNERTYPE_REQUIRE_MFA", "0")

        # The operator's way to lift the requirement on an instance whose
        # settings table cannot be reached from a browser.
        assert mfa.enforcement_enabled() is False

    def test_an_operator_can_force_it_on_anywhere(self, monkeypatch):
        monkeypatch.setenv("PARTNERTYPE_ENV", "development")
        monkeypatch.setenv("PARTNERTYPE_REQUIRE_MFA", "1")

        assert mfa.is_required_for("admin") is True

    def test_a_partner_can_switch_it_off_with_a_valid_code(self, partner_user):
        _secret, codes = _enrol(partner_user)

        mfa.disable(partner_user, codes[0])

        assert mfa.status_for(partner_user["id"])["enabled"] is False

    def test_switching_it_off_needs_proof_of_holding_it(self, partner_user):
        _enrol(partner_user)

        with pytest.raises(ValueError):
            mfa.disable(partner_user, "000000")

        assert mfa.status_for(partner_user["id"])["enabled"] is True


class TestBackupCodeRenewal:
    """Eight codes run out. Taking the factor off and putting it back on just
    to get more is a strange thing to ask of somebody whose phone still works."""

    def test_a_fresh_set_replaces_the_old_one(self, admin_user):
        secret, old_codes = _enrol(admin_user)

        new_codes = mfa.regenerate_backup_codes(admin_user, pyotp.TOTP(secret).now())

        assert len(new_codes) == mfa.BACKUP_CODE_COUNT
        assert set(new_codes).isdisjoint(old_codes)
        assert mfa.status_for(admin_user["id"])["backup_codes_left"] == mfa.BACKUP_CODE_COUNT

    def test_the_old_codes_stop_working(self, admin_user):
        secret, old_codes = _enrol(admin_user)

        mfa.regenerate_backup_codes(admin_user, pyotp.TOTP(secret).now())

        assert mfa.verify_for_login(admin_user["id"], old_codes[0]) is False

    def test_a_wrong_code_gets_nothing(self, admin_user):
        secret, old_codes = _enrol(admin_user)

        with pytest.raises(ValueError):
            mfa.regenerate_backup_codes(admin_user, "000000")

        # And the set somebody is still carrying is left alone.
        assert mfa.verify_for_login(admin_user["id"], old_codes[0]) is True

    def test_a_spent_backup_code_can_buy_a_new_set(self, admin_user):
        _secret, codes = _enrol(admin_user)

        new_codes = mfa.regenerate_backup_codes(admin_user, codes[0])

        # Somebody down to their last printed code, with the phone gone, is
        # exactly who needs this -- they are not locked out for lack of an app.
        assert len(new_codes) == mfa.BACKUP_CODE_COUNT


class TestProducerReset:
    """The way back for somebody who lost the phone AND the printed codes."""

    def test_the_producer_reopens_a_partners_account(self, admin_user, partner_user):
        _enrol(partner_user)

        mfa.reset_for(admin_user, partner_user["id"])

        assert mfa.status_for(partner_user["id"])["enabled"] is False

    def test_and_the_partner_can_set_a_new_one_up(self, admin_user, partner_user):
        _enrol(partner_user)
        mfa.reset_for(admin_user, partner_user["id"])

        started = mfa.begin_enrolment(partner_user)

        assert mfa.confirm_enrolment(partner_user, pyotp.TOTP(started["secret"]).now())

    def test_the_producer_cannot_reset_his_own(self, admin_user):
        _enrol(admin_user)

        with pytest.raises(PermissionError):
            mfa.reset_for(admin_user, admin_user["id"])

        # A session in the wrong hands would otherwise strip the factor with one
        # click, which is the thing the factor exists to prevent.
        assert mfa.status_for(admin_user["id"])["enabled"] is True

    def test_a_partner_cannot_reset_anybody(self, partner_user, admin_user):
        _enrol(admin_user)

        with pytest.raises(PermissionError):
            mfa.reset_for(partner_user, admin_user["id"])

    def test_an_unknown_account_is_refused_rather_than_silently_accepted(self, admin_user):
        with pytest.raises(ValueError):
            mfa.reset_for(admin_user, 999999)
