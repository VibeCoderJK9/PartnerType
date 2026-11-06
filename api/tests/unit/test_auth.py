"""
PartnerType -- Auth module unit tests.

Full branch-coverage suite for api/auth.py: login credential gating, session
validation and expiry handling, logout, multi-session behavior, and the
HTTP handler entry point.
"""
from datetime import datetime, timedelta

import bcrypt
import pytest

from api.auth import login, logout, get_session_user, handle_login
from api.db import get_conn


# ─── LOGIN: TRACER SLICE (proven end-to-end) ────────────────────────

class TestLoginTracer:
    def test_login_success_returns_user_and_32_char_token(self, partner_user):
        user, token = login(
            partner_user["email"], partner_user["plain_password"], "partner"
        )

        assert user["role"] == "partner"
        assert user["email"] == partner_user["email"]
        assert len(token) == 64

    def test_login_rejects_wrong_role(self, partner_user):
        with pytest.raises(ValueError, match="rola"):
            login(partner_user["email"], partner_user["plain_password"], "admin")


# ─── LOGIN: CREDENTIAL VALIDATION ────────────────────────────────────────────

class TestLoginValidation:
    def test_login_rejects_empty_email(self, partner_user):
        with pytest.raises(ValueError):
            login("", partner_user["plain_password"], "partner")

    def test_login_rejects_empty_password(self, partner_user):
        with pytest.raises(ValueError):
            login(partner_user["email"], "", "partner")

    def test_login_rejects_wrong_password(self, partner_user):
        with pytest.raises(ValueError, match="rola"):
            login(partner_user["email"], "WrongPassword!", "partner")

    def test_login_treats_an_over_long_password_as_a_wrong_one(self, partner_user):
        with pytest.raises(ValueError, match="rola"):
            login(partner_user["email"], "x" * 100, "partner")

    def test_login_rejects_inactive_user(self, test_db_path):
        password = "InactivePass123!"
        email = "inactive.partner@test.pl"
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO users (email, password, name, role, level, active)
                   VALUES (?,?,?,?,?,0)""",
                (email, hashed, "Inactive Partner", "partner", "bronze"),
            )
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(ValueError, match="rola"):
            login(email, password, "partner")


# ─── SESSION VALIDATION ──────────────────────────────────────────────────────

class TestSessionValidation:
    def test_session_token_round_trip_resolves_user(self, partner_user):
        _, token = login(
            partner_user["email"], partner_user["plain_password"], "partner"
        )

        resolved = get_session_user(token)

        assert resolved is not None
        assert resolved["email"] == partner_user["email"]

    def test_get_session_user_rejects_empty_token(self, test_db_path):
        assert get_session_user("") is None

    def test_get_session_user_rejects_unissued_token(self, test_db_path):
        assert get_session_user("0" * 32) is None

    def test_get_session_user_rejects_expired_session(self, partner_user):
        token = "expiredtoken" + "0" * 20
        conn = get_conn()
        try:
            past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
            conn.execute(
                "INSERT INTO sessions (token, user_id, expires_at) VALUES (?,?,?)",
                (token, partner_user["id"], past),
            )
            conn.commit()
        finally:
            conn.close()

        assert get_session_user(token) is None

    def test_get_session_user_slides_expiry_forward(self, partner_user):
        from api.auth import _hash_session_token
        _, raw = login(
            partner_user["email"], partner_user["plain_password"], "partner"
        )
        # The row is keyed by the fingerprint; the raw value only exists in
        # the caller's cookie.
        token = _hash_session_token(raw)

        conn = get_conn()
        try:
            before = conn.execute(
                "SELECT expires_at FROM sessions WHERE token = ?", (token,)
            ).fetchone()["expires_at"]
        finally:
            conn.close()

        get_session_user(raw)

        conn = get_conn()
        try:
            after = conn.execute(
                "SELECT expires_at FROM sessions WHERE token = ?", (token,)
            ).fetchone()["expires_at"]
        finally:
            conn.close()

        assert after > before


# ─── LOGOUT ───────────────────────────────────────────────────────────────────

class TestLogout:
    def test_logout_invalidates_token(self, partner_user):
        _, token = login(
            partner_user["email"], partner_user["plain_password"], "partner"
        )

        logout(token)

        assert get_session_user(token) is None


# ─── MULTIPLE SESSIONS ────────────────────────────────────────────────────────

class TestMultipleSessions:
    def test_two_logins_yield_distinct_tokens_both_valid(self, partner_user):
        _, token_a = login(
            partner_user["email"], partner_user["plain_password"], "partner"
        )
        _, token_b = login(
            partner_user["email"], partner_user["plain_password"], "partner"
        )

        assert token_a != token_b
        assert get_session_user(token_a) is not None
        assert get_session_user(token_b) is not None

    def test_login_purges_other_users_expired_sessions(
        self, partner_user, partner_user_bronze
    ):
        stale_token = "stalesession" + "0" * 20
        conn = get_conn()
        try:
            past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
            conn.execute(
                "INSERT INTO sessions (token, user_id, expires_at) VALUES (?,?,?)",
                (stale_token, partner_user_bronze["id"], past),
            )
            conn.commit()
            before_count = conn.execute(
                "SELECT COUNT(*) AS c FROM sessions"
            ).fetchone()["c"]
        finally:
            conn.close()

        assert before_count == 1

        login(partner_user["email"], partner_user["plain_password"], "partner")

        conn = get_conn()
        try:
            stale_row = conn.execute(
                "SELECT 1 FROM sessions WHERE token = ?", (stale_token,)
            ).fetchone()
            after_count = conn.execute(
                "SELECT COUNT(*) AS c FROM sessions"
            ).fetchone()["c"]
        finally:
            conn.close()

        # Stale session for the other user was purged; the new session for
        # partner_user was inserted, so the net count is unchanged at 1.
        assert stale_row is None
        assert after_count == 1


# ─── HTTP HANDLER LEVEL ──────────────────────────────────────────────────────

class TestHandleLogin:
    def test_handle_login_bad_credentials_returns_401(self, partner_user):
        status, body = handle_login(
            None,
            {
                "email": partner_user["email"],
                "password": "WrongPassword!",
                "role": "partner",
            },
        )

        assert status == 401
        assert "error" in body

    def test_handle_login_good_credentials_returns_200(self, partner_user):
        status, body = handle_login(
            None,
            {
                "email": partner_user["email"],
                "password": partner_user["plain_password"],
                "role": "partner",
            },
        )

        assert status == 200
        assert "token" in body
        assert "user" in body


class TestLoginLeavesAnAuditTrail:
    """Without these entries an attack on the login form is invisible after the
    fact, and there is no evidence of who got in for anyone auditing access.
    Audit writes are muted suite-wide (conftest), so the call is the assertion."""

    class _Handler:
        def __init__(self, ip):
            self.client_address = (ip, 51234)

    @staticmethod
    def _entries(action):
        from api import audit
        return [call.args for call in audit.log.call_args_list
                if len(call.args) > 1 and call.args[1] == action]

    def test_a_refused_attempt_records_the_address_but_never_the_password(self, partner_user):
        status, _ = handle_login(
            self._Handler("203.0.113.7"),
            {"email": partner_user["email"], "password": "ZupelnieZle-Haslo", "role": "partner"},
        )

        assert status == 401
        entries = self._entries("user.login_failed")
        assert len(entries) == 1
        user, _action, _entity, _entity_id, detail, ip = entries[0]
        assert user is None
        assert ip == "203.0.113.7"
        assert partner_user["email"] in detail
        assert "ZupelnieZle-Haslo" not in detail

    def test_a_successful_login_records_the_user_and_the_address(self, partner_user):
        status, _ = handle_login(
            self._Handler("203.0.113.8"),
            {"email": partner_user["email"], "password": partner_user["plain_password"],
             "role": "partner"},
        )

        assert status == 200
        entries = self._entries("user.login")
        assert len(entries) == 1
        user, _action, _entity, entity_id, _detail, ip = entries[0]
        assert user["id"] == partner_user["id"]
        assert entity_id == partner_user["id"]
        assert ip == "203.0.113.8"


class TestLoginRateLimit:
    """A password form with no limit is a password form an attacker can grind
    through at machine speed, however strong the hashing is."""

    @staticmethod
    def _fail(email, ip="198.51.100.5", password="ZleHaslo"):
        from api.auth import login
        try:
            login(email, password, "partner", ip)
        except ValueError as exc:
            return exc
        raise AssertionError("logowanie miało zostać odrzucone")

    def test_the_sixth_failure_for_one_account_is_refused_before_the_password_is_checked(
        self, partner_user, mocker
    ):
        from api.auth import TooManyAttemptsError, LOGIN_MAX_FAILURES_PER_EMAIL
        for _ in range(LOGIN_MAX_FAILURES_PER_EMAIL):
            assert not isinstance(self._fail(partner_user["email"]), TooManyAttemptsError)

        checkpw = mocker.patch("api.auth.bcrypt.checkpw")
        refusal = self._fail(partner_user["email"])

        assert isinstance(refusal, TooManyAttemptsError)
        checkpw.assert_not_called()

    def test_a_blocked_account_answers_429_not_401(self, partner_user):
        from api.auth import LOGIN_MAX_FAILURES_PER_EMAIL
        for _ in range(LOGIN_MAX_FAILURES_PER_EMAIL):
            self._fail(partner_user["email"])

        status, body = handle_login(
            TestLoginLeavesAnAuditTrail._Handler("198.51.100.5"),
            {"email": partner_user["email"], "password": partner_user["plain_password"],
             "role": "partner"},
        )

        assert status == 429
        assert body["code"] == "too_many_attempts"

    def test_a_correct_password_clears_the_counter(self, partner_user):
        from api.auth import login, LOGIN_MAX_FAILURES_PER_EMAIL
        for _ in range(LOGIN_MAX_FAILURES_PER_EMAIL - 1):
            self._fail(partner_user["email"])

        login(partner_user["email"], partner_user["plain_password"], "partner", "198.51.100.5")

        # The next mistype starts from zero again, so an ordinary person who
        # fumbles and then succeeds is never locked out later in the day.
        for _ in range(LOGIN_MAX_FAILURES_PER_EMAIL - 1):
            self._fail(partner_user["email"])

    @pytest.mark.parametrize("padding", [" {} ", "{} ", "\t{}\n"])
    def test_spaces_around_the_address_do_not_dodge_the_account_limit(
        self, partner_user, mocker, padding
    ):
        from api.auth import TooManyAttemptsError, LOGIN_MAX_FAILURES_PER_EMAIL
        padded = padding.format(partner_user["email"])
        for index in range(LOGIN_MAX_FAILURES_PER_EMAIL):
            # A different address each time, so only the account limit counts.
            self._fail(padded, ip=f"198.51.100.{60 + index}")

        checkpw = mocker.patch("api.auth.bcrypt.checkpw")
        refusal = self._fail(partner_user["email"], ip="198.51.100.99")

        assert isinstance(refusal, TooManyAttemptsError)
        checkpw.assert_not_called()

    @pytest.mark.parametrize("body", [
        {"email": [], "password": "x", "role": "partner"},
        {"email": "a@b.pl", "password": 123, "role": "partner"},
        {"email": "a@b.pl", "password": "x", "role": 1},
    ])
    def test_non_text_fields_are_an_ordinary_refusal(self, test_db_path, body):
        status, response = handle_login(TestLoginLeavesAnAuditTrail._Handler("198.51.100.7"), body)

        assert status == 401
        assert "error" in response

    def test_one_address_grinding_many_accounts_is_stopped_too(self, partner_user):
        from api.auth import TooManyAttemptsError, LOGIN_MAX_FAILURES_PER_IP
        for index in range(LOGIN_MAX_FAILURES_PER_IP):
            self._fail(f"nieistniejacy-{index}@example.test")

        refusal = self._fail("kolejny@example.test")

        assert isinstance(refusal, TooManyAttemptsError)


class TestSessionHardening:
    """A session is a credential. It must not be readable from the database,
    and it must not be able to live forever by simply being used."""

    def test_the_database_stores_a_fingerprint_not_the_cookie_value(self, partner_user):
        _user, token = login(partner_user["email"], partner_user["plain_password"], "partner")

        conn = get_conn()
        try:
            stored = conn.execute("SELECT token FROM sessions").fetchall()
        finally:
            conn.close()

        assert len(stored) == 1
        assert stored[0]["token"] != token
        assert get_session_user(token) is not None

    def test_a_session_dies_at_its_absolute_limit_however_active_it_was(self, partner_user):
        from api.auth import SESSION_ABSOLUTE_DAYS
        _user, token = login(partner_user["email"], partner_user["plain_password"], "partner")
        assert get_session_user(token) is not None

        conn = get_conn()
        try:
            # The sliding window is still wide open; only the wall has moved.
            conn.execute(
                "UPDATE sessions SET absolute_expires_at = ?",
                ((datetime.utcnow() - timedelta(minutes=1)).isoformat(),),
            )
            conn.commit()
        finally:
            conn.close()

        assert get_session_user(token) is None
        assert SESSION_ABSOLUTE_DAYS >= 1

    def test_logging_out_everywhere_ends_every_device_including_this_one(self, partner_user):
        from api.auth import logout_everywhere
        _user, phone = login(partner_user["email"], partner_user["plain_password"], "partner")
        _user, laptop = login(partner_user["email"], partner_user["plain_password"], "partner")

        logout_everywhere(partner_user["id"])

        assert get_session_user(phone) is None
        assert get_session_user(laptop) is None

    def test_maintenance_sweeps_expired_sessions_and_spent_reset_links(self, partner_user, mocker):
        from api.db import run_maintenance
        from api.password_reset import complete_reset, request_reset
        mocker.patch("api.email_service.send_password_reset")
        _user, token = login(partner_user["email"], partner_user["plain_password"], "partner")
        complete_reset(request_reset(partner_user["email"]), "Nowe-Haslo-123")
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO sessions (token, user_id, expires_at, absolute_expires_at) VALUES (?,?,?,?)",
                ("wygasly", partner_user["id"], "2020-01-01T00:00:00", "2020-01-01T00:00:00"),
            )
            conn.commit()
        finally:
            conn.close()

        removed = run_maintenance()

        assert removed["expired_sessions"] >= 1
        assert removed["spent_password_resets"] >= 1
