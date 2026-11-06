"""Per-connection limits, usage counter, call log and maintenance.

Unit level: the functions are called directly against the per-test
PostgreSQL database. The HTTP-level behaviour (429 on both surfaces, the
JSON-RPC body on /mcp, denial logging) lives in
api/tests/integration/test_gateway_logging.py.
"""
import threading
import time
from datetime import datetime, timezone

import pytest

from api.db import get_conn, run_maintenance
from api.gateway import calllog, limits, settings
from api.gateway.auth import Principal, RateLimited
from api.tests.integration import gateway_helpers as gh

GATEWAY_MAINTENANCE_KEYS = (
    "old_api_call_log", "old_api_rate_windows", "expired_oauth_requests",
    "old_oauth_codes", "expired_oauth_tokens", "unused_oauth_clients", "old_api_usage",
)


def principal_for(user, kind="key", connection_id=1):
    return Principal(user=user, kind=kind, connection_id=connection_id,
                     scopes=frozenset({"read"}), resource="http://localhost:8000/api/v1")


def scalar(sql, params=()):
    conn = get_conn()
    try:
        row = conn.execute(sql, params).fetchone()
        return None if row is None else row[0]
    finally:
        conn.close()


def execute(sql, params=()):
    with get_conn() as conn:
        conn.execute(sql, params)


# ─── Operator caps ───────────────────────────────────────────────────

class TestOperatorCap:
    def test_admin_value_above_the_cap_is_cut_to_the_cap(self, monkeypatch):
        monkeypatch.setenv("PARTNERTYPE_API_RATE_PER_MINUTE", "5")
        gh.set_integration_settings(enabled=True, rate_per_minute=50)
        assert settings.effective_limits()["per_minute"] == 5

    def test_admin_value_below_the_cap_is_kept(self, monkeypatch):
        monkeypatch.setenv("PARTNERTYPE_API_RATE_PER_MINUTE", "5")
        gh.set_integration_settings(enabled=True, rate_per_minute=3)
        assert settings.effective_limits()["per_minute"] == 3

    def test_invalid_operator_value_is_a_configuration_error(self, monkeypatch):
        monkeypatch.setenv("PARTNERTYPE_API_RATE_PER_MINUTE", "abc")
        with pytest.raises(ValueError):
            settings.effective_limits()


# ─── Fixed windows and atomic counting ──────────────────────────────────────

class TestWindows:
    def test_a_full_previous_window_does_not_block_the_next_minute(self):
        execute(
            """INSERT INTO api_rate_windows (bucket, window_start, hits)
               VALUES ('t:prev',
                       to_timestamp(floor(extract(epoch FROM now()) / 60) * 60)
                         - INTERVAL '60 seconds',
                       5)""")
        allowed, retry_after = limits.consume("t:prev", 5, 60)
        assert allowed is True
        assert 1 <= retry_after <= 60

    def test_ten_concurrent_hits_are_all_counted(self):
        results, errors = [], []

        def hit():
            try:
                results.append(limits.consume("t:bucket", 100, 60))
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=hit) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert all(allowed for allowed, _ in results)
        assert len(results) == 10
        assert scalar("SELECT max(hits) FROM api_rate_windows WHERE bucket = 't:bucket'") == 10
        assert scalar("SELECT sum(hits) FROM api_rate_windows WHERE bucket = 't:bucket'") == 10


# ─── Per-user minute limit ──────────────────────────────────────────────────

class TestPerUserMinute:
    def test_more_connections_do_not_multiply_the_minute_limit(self, admin_user, manager_user):
        gh.set_integration_settings(enabled=True, rate_per_minute=2)
        left = 60 - (time.time() % 60)
        if left < 10:  # stay inside one fixed window
            time.sleep(left + 0.5)
        limits.enforce(principal_for(admin_user, connection_id=31))
        limits.enforce(principal_for(admin_user, kind="oauth", connection_id=32))
        with pytest.raises(RateLimited) as caught:
            limits.enforce(principal_for(admin_user, connection_id=33))
        assert 1 <= int(caught.value.headers["Retry-After"]) <= 60
        limits.enforce(principal_for(manager_user, connection_id=34))


# ─── Warsaw midnight (precision edge, DST) ──────────────────────────────────

class TestWarsawMidnight:
    def test_now_is_a_whole_number_of_seconds_within_one_day(self):
        value = limits.seconds_to_warsaw_midnight()
        assert isinstance(value, int)
        # 86400 on an ordinary day; a 25-hour DST day allows up to 90000.
        assert 1 <= value <= 90000

    def test_the_long_autumn_day_has_25_hours(self):
        # 25.10.2026: clocks go back at 03:00 CEST. Warsaw midnight starting
        # that day is 2026-10-24T22:00Z, the next one 2026-10-25T23:00Z.
        start = datetime(2026, 10, 24, 22, 0, 1, tzinfo=timezone.utc)
        assert limits.seconds_to_warsaw_midnight(start) == 25 * 3600 - 1

    def test_the_short_spring_day_has_23_hours(self):
        # 29.03.2026: clocks go forward at 02:00 CET.
        start = datetime(2026, 3, 28, 23, 0, 0, tzinfo=timezone.utc)
        assert limits.seconds_to_warsaw_midnight(start) == 23 * 3600

    def test_a_fraction_of_a_second_rounds_up_and_never_reaches_zero(self):
        almost = datetime(2026, 10, 18, 21, 59, 59, 500000, tzinfo=timezone.utc)
        assert limits.seconds_to_warsaw_midnight(almost) == 1


# ─── Day limit and usage counter ────────────────────────────────────────────

class TestDayLimit:
    def test_the_call_over_the_daily_limit_waits_for_warsaw_midnight(self, admin_user):
        gh.set_integration_settings(enabled=True, rate_per_day=2)
        principal = principal_for(admin_user, connection_id=7)
        limits.enforce(principal)
        limits.enforce(principal)
        with pytest.raises(RateLimited) as caught:
            limits.enforce(principal)
        retry_after = int(caught.value.headers["Retry-After"])
        assert retry_after >= 1
        assert abs(retry_after - limits.seconds_to_warsaw_midnight()) <= 2
        assert caught.value.principal is principal
        assert scalar("SELECT calls FROM api_usage_daily WHERE connection_id = 7") == 2

    def test_records_and_denials_add_up_on_one_row_per_day(self, admin_user):
        gh.set_integration_settings(enabled=True)
        principal = principal_for(admin_user, connection_id=8)
        limits.enforce(principal)
        limits.add_records(principal, 4)
        limits.add_records(principal, 0)
        limits.count_denied(principal)
        rows = calllog.usage_summary(connection_kind="key", connection_id=8)
        assert len(rows) == 1
        assert (rows[0]["calls"], rows[0]["denied"], rows[0]["records"]) == (1, 1, 4)

    def test_more_connections_do_not_multiply_the_daily_limit(self, admin_user, manager_user):
        gh.set_integration_settings(enabled=True, rate_per_day=2)
        first = principal_for(admin_user, connection_id=21)
        second = principal_for(admin_user, kind="oauth", connection_id=22)
        limits.enforce(first)
        limits.enforce(second)
        with pytest.raises(RateLimited) as caught:
            limits.enforce(principal_for(admin_user, connection_id=23))
        assert abs(int(caught.value.headers["Retry-After"])
                   - limits.seconds_to_warsaw_midnight()) <= 2
        # The refused call is counted on no connection.
        assert scalar("SELECT COALESCE(sum(calls), 0) FROM api_usage_daily") == 2
        # Another user has their own allowance.
        limits.enforce(principal_for(manager_user, connection_id=24))

    def test_a_connection_that_never_called_has_an_empty_summary(self):
        assert calllog.usage_summary(connection_kind="oauth", connection_id=999) == []


# ─── Call log ───────────────────────────────────────────────────────────────

class TestCallLog:
    def test_list_calls_is_newest_first(self, admin_user):
        execute(
            """INSERT INTO api_call_log (created_at, connection_kind, connection_id, user_id,
                   surface, operation, status_code, outcome)
               VALUES (now() - INTERVAL '2 minutes', 'key', 1, ?, 'mcp', 'older', 200, 'ok'),
                      (now(), 'key', 1, ?, 'mcp', 'same-time-a', 200, 'ok')""",
            (admin_user["id"], admin_user["id"]))
        execute(
            """INSERT INTO api_call_log (created_at, connection_kind, connection_id, user_id,
                   surface, operation, status_code, outcome)
               SELECT created_at, 'key', 1, user_id, 'mcp', 'same-time-b', 200, 'ok'
               FROM api_call_log WHERE operation = 'same-time-a'""")
        operations = [row["operation"] for row in calllog.list_calls()]
        assert operations == ["same-time-b", "same-time-a", "older"]

    def test_list_calls_filters_by_connection_and_bounds_the_page(self, admin_user):
        calllog.record(principal_for(admin_user, connection_id=1), "mcp", "a", 200, "ok")
        calllog.record(principal_for(admin_user, connection_id=2), "mcp", "b", 200, "ok")
        rows = calllog.list_calls(limit=1000, connection_kind="key", connection_id=2)
        assert [row["operation"] for row in rows] == ["b"]
        assert len(calllog.list_calls(limit=0)) == 1

    def test_record_never_raises(self, admin_user):
        broken = principal_for(admin_user, kind="not-a-kind")
        calllog.record(broken, "mcp", "list_leads", 200, "ok")
        assert scalar("SELECT COUNT(*) FROM api_call_log") == 0

    def test_record_cuts_the_operation_to_100_characters(self, admin_user):
        calllog.record(principal_for(admin_user), "api_v1", "x" * 300, 200, "ok")
        assert scalar("SELECT length(operation) FROM api_call_log") == 100


# ─── Maintenance ────────────────────────

def _seed_gateway_rows(user):
    uid = user["id"]
    with get_conn() as conn:
        for client_id, age in (("ptc_lonely", "2 days"), ("ptc_granted", "2 days"),
                               ("ptc_fresh", "1 hour")):
            conn.execute(
                f"""INSERT INTO oauth_clients (client_id, client_name, redirect_uris, created_at)
                    VALUES (?, 'c', '[]', now() - INTERVAL '{age}')""", (client_id,))
        grant_id = conn.execute(
            """INSERT INTO oauth_grants (user_id, client_id, resource, created_at)
               VALUES (?, 'ptc_granted', 'http://localhost:8000/mcp', now() - INTERVAL '2 days')""",
            (uid,)).lastrowid
        for name, expires in (("req-old", "now() - INTERVAL '1 minute'"),
                              ("req-live", "now() + INTERVAL '5 minutes'")):
            conn.execute(
                f"""INSERT INTO oauth_authorize_requests
                    (request_hash, client_id, redirect_uri, code_challenge, scope, resource,
                     expires_at)
                    VALUES (?, 'ptc_granted', 'x', 'y', 'read', 'r', {expires})""", (name,))
        for name, age in (("code-old", "2 days"), ("code-new", "1 minute")):
            conn.execute(
                f"""INSERT INTO oauth_codes (code_hash, grant_id, client_id, redirect_uri,
                        code_challenge, resource, scope, created_at, expires_at)
                    VALUES (?, ?, 'ptc_granted', 'x', 'y', 'r', 'read',
                            now() - INTERVAL '{age}', now() - INTERVAL '{age}'
                                                      + INTERVAL '10 minutes')""",
                (name, grant_id))
        for name, kind, expires in (
                ("tok-access-old", "access", "now() - INTERVAL '2 days'"),
                ("tok-refresh-old", "refresh", "now() - INTERVAL '2 days'"),
                ("tok-access-recent", "access", "now() - INTERVAL '1 hour'"),
                ("tok-refresh-live", "refresh", "now() + INTERVAL '30 days'")):
            conn.execute(
                f"""INSERT INTO oauth_tokens (token_hash, grant_id, kind, expires_at)
                    VALUES (?, ?, ?, {expires})""", (name, grant_id, kind))
        for operation, age in (("log-91", "91 days"), ("log-89", "89 days")):
            conn.execute(
                f"""INSERT INTO api_call_log (created_at, connection_kind, connection_id,
                        user_id, surface, operation, status_code, outcome)
                    VALUES (now() - INTERVAL '{age}', 'key', 1, ?, 'mcp', ?, 200, 'ok')""",
                (uid, operation))
        for bucket, age in (("w-old", "3 days"), ("w-new", "1 hour")):
            conn.execute(
                f"""INSERT INTO api_rate_windows (bucket, window_start, hits)
                    VALUES (?, now() - INTERVAL '{age}', 1)""", (bucket,))
        for connection_id, age in ((401, 401), (399, 399)):
            conn.execute(
                """INSERT INTO api_usage_daily (connection_kind, connection_id, day, calls)
                   VALUES ('key', ?, current_date - CAST(? AS integer), 1)""",
                (connection_id, age))


class TestMaintenance:
    def test_it_removes_only_old_and_expired_gateway_rows(self, admin_user):
        _seed_gateway_rows(admin_user)

        removed = run_maintenance()

        assert {key: removed[key] for key in GATEWAY_MAINTENANCE_KEYS} == {
            "old_api_call_log": 1,
            "old_api_rate_windows": 1,
            "expired_oauth_requests": 1,
            "old_oauth_codes": 1,
            "expired_oauth_tokens": 2,
            "unused_oauth_clients": 1,
            "old_api_usage": 1,
        }
        assert scalar("SELECT string_agg(operation, ',') FROM api_call_log") == "log-89"
        assert scalar("SELECT string_agg(bucket, ',') FROM api_rate_windows") == "w-new"
        assert scalar("SELECT string_agg(request_hash, ',') FROM oauth_authorize_requests") \
            == "req-live"
        assert scalar("SELECT string_agg(code_hash, ',') FROM oauth_codes") == "code-new"
        assert scalar("SELECT string_agg(token_hash, ',' ORDER BY token_hash) FROM oauth_tokens") \
            == "tok-access-recent,tok-refresh-live"
        assert scalar("SELECT string_agg(client_id, ',' ORDER BY client_id) FROM oauth_clients") \
            == "ptc_fresh,ptc_granted"
        assert scalar("SELECT string_agg(connection_id::text, ',') FROM api_usage_daily") == "399"

    def test_a_second_run_right_after_removes_nothing(self, admin_user):
        _seed_gateway_rows(admin_user)
        run_maintenance()

        again = run_maintenance()

        assert all(again[key] == 0 for key in GATEWAY_MAINTENANCE_KEYS), again
