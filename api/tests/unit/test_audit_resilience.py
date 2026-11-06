"""
PartnerType -- writing the audit trail can never take the service down.

A failed audit insert used to keep its database connection out of the pool
for good, and a NUL character in a login form field was enough to make the
insert fail. A few dozen such requests exhausted the pool.
"""
import pytest

from api import audit
from api.auth import handle_login
from api.db import get_conn

_REAL_AUDIT_LOG = audit.log


@pytest.fixture(autouse=True)
def real_audit_log(mocker):
    mocker.patch("api.audit.log", new=_REAL_AUDIT_LOG)


class _Handler:
    def __init__(self, ip="198.51.100.30"):
        self.client_address = (ip, 5555)
        self.headers = {}


def _details():
    conn = get_conn()
    try:
        return [r["detail"] for r in conn.execute(
            "SELECT detail FROM audit_log ORDER BY id").fetchall()]
    finally:
        conn.close()


def test_a_nul_character_is_stripped_and_the_entry_is_still_written(test_db_path):
    audit.log(None, "user.login_failed", "user", "", "a\x00b", "1.2.3.4")

    assert _details() == ["ab"]


def test_a_failed_insert_still_returns_its_connection(test_db_path, mocker):
    closed = []

    class _FailingConnection:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("insert refused")

        def commit(self):
            pass

        def close(self):
            closed.append(True)

    mocker.patch("api.audit.get_conn", return_value=_FailingConnection())

    audit.log(None, "user.login_failed", "user", "", "x")

    assert closed == [True]


def test_a_nul_in_the_login_role_is_an_ordinary_refusal(test_db_path):
    for _ in range(40):
        status, _body = handle_login(
            _Handler(), {"email": "a@b.pl", "password": "x", "role": "producent\x00"})
        assert status == 401

    assert len(_details()) == 40
    assert all("\x00" not in d for d in _details())
