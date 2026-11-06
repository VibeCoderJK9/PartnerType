"""
PartnerType -- the audit screen shows the work done on the data by default.

A brand-new account should not open its Dziennik onto a list of sign-ins
and failed sign-in attempts. Those stay recorded -- they are the
security history -- but the screen asks for them explicitly.
"""
import pytest

from api import audit

_REAL_AUDIT_LOG = audit.log


@pytest.fixture(autouse=True)
def real_audit_log(mocker):
    mocker.patch("api.audit.log", new=_REAL_AUDIT_LOG)


@pytest.fixture
def mixed_log(admin_user):
    audit.log(None, "user.login_failed", "user", "", "")
    audit.log(admin_user, "user.login", "user", admin_user["id"], "")
    audit.log(admin_user, "settings.update", "settings", "currency", "currency: PLN → EUR")
    audit.log(admin_user, "user.logout", "user", admin_user["id"], "")


def _actions(status_body):
    status, body = status_body
    assert status == 200
    return sorted(entry["action"] for entry in body["logs"])


def test_the_screen_default_leaves_out_sign_ins(mixed_log, admin_user):
    assert _actions(audit.handle_list(None, admin_user, {})) == ["settings.update"]


def test_sign_ins_are_one_choice_away(mixed_log, admin_user):
    assert _actions(audit.handle_list(None, admin_user, {"scope": ["sessions"]})) == [
        "user.login", "user.login_failed", "user.logout"]


def test_everything_is_still_there(mixed_log, admin_user):
    assert len(_actions(audit.handle_list(None, admin_user, {"scope": ["all"]}))) == 4


def test_an_unknown_scope_is_refused(admin_user):
    status, _ = audit.handle_list(None, admin_user, {"scope": ["everything"]})
    assert status == 400
