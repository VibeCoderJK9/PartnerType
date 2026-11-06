"""
PartnerType -- Integration test fixtures.

Drives requests through api.router.dispatch with a mock request handler, so
tests exercise the entire router path (bearer-token extraction and
validation, body parsing, handler dispatch and JSON response encoding)
instead of calling handler or auth functions directly.

The shared database and user fixtures (test_db_path, admin_user,
partner_user, partner_user_bronze, sample_lead, mute_side_effects) come from
the parent api/tests/conftest.py package fixture surface delivered by plan
01-01. This module adds only the request-driving helpers and must not
redefine anything already provided there.
"""
import json
import re
import uuid
from http.cookies import SimpleCookie
from urllib.parse import unquote
from unittest.mock import MagicMock

import pytest

from api.router import dispatch
from api.db import get_conn


@pytest.fixture(autouse=True)
def integration_catalogue_product(test_db_path):
    """Keep legacy router workflow tests on the new required product_id contract."""
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO products
               (name, code, category, base_price, offered, active, sort_order)
               VALUES ('Wdrożenie ERP', 'TEST-ERP', 'Wdrożenie', 10000, 1, 1, 0) ON CONFLICT DO NOTHING"""
        )
        conn.commit()
    finally:
        conn.close()


# A PATCH on a lead must say which version it read, and
# the server refuses to invent one (api/leads.py::handle_update). Dozens of
# tests here are about other things entirely -- commissions, notifications,
# role gates -- and restating the metadata in each of them would bury their
# actual subject.
#
# So the driver supplies it, exactly as a real client does: the current
# revision, read live. It fills in ONLY when a body carries neither field, so
# a test that deliberately sends a partial or stale envelope is left alone --
# which is what keeps test_lead_patch_contract.py, the file that proves the
# requirement itself, honest.
_LEAD_PATCH_PATH = re.compile(r"^/api/leads/(?P<id>[^/?]+)$")


def _with_lead_versioning(method, path, body):
    if method != "PATCH" or not isinstance(body, dict):
        return body
    match = _LEAD_PATCH_PATH.match(path)
    if not match:
        return body
    if "expected_revision" in body or "operation_id" in body:
        return body

    lead_id = unquote(match.group("id"))
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT revision FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return body

    return {
        **body,
        "expected_revision": row["revision"],
        "operation_id": f"test-{lead_id}-{row['revision']}-{uuid.uuid4().hex[:8]}",
    }


def lead_payload(**overrides):
    """
    Return a complete, valid lead-submission body (client_name, client_email,
    client_phone, company, value, notes, sector, nip, website, product,
    quantity), with any keyword overrides merged in.

    Centralizes the leads POST body shape so a future required-field
    addition is a one-line change here instead of an edit to every test
    module that submits a lead.
    """
    body = {
        "client_name": "Test Client",
        "client_email": "client@test.pl",
        "client_phone": "",
        "company": "Test Co",
        "value": 10000,
        "notes": "",
        "sector": "",
        "nip": "1111111111",
        "website": "nova-sklep.pl",
        "product": "Wdrożenie ERP",
        "product_id": 1,
        "quantity": 1,
    }
    body.update(overrides)
    return body


def _build_handler(
    method: str,
    path: str,
    body: dict = None,
    token: str = None,
    headers: dict = None,
    raw_body: bytes = None,
):
    """
    Build a mock HTTP request handler exposing exactly the surface the
    router's dispatch function touches: the headers mapping, the request
    stream read method, the status-sending method, the header-sending and
    end-of-headers methods, and the response stream write method. Everything
    else is auto-created by MagicMock.
    """
    handler = MagicMock()
    handler.command = method
    handler.path = path

    if raw_body is not None:
        raw = raw_body
    elif body is not None:
        raw = json.dumps(body).encode("utf-8")
    else:
        raw = b""

    # headers must be a real dict -- the router calls .get(name, default) on it
    request_headers = {
        "Content-Length": str(len(raw)),
        # Most tests assert the Polish copy, so the test browser asks for
        # Polish; pass headers={"Accept-Language": None} to send none.
        "Accept-Language": "pl",
    }
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        request_headers["Origin"] = "http://localhost:8000"
    if token:
        request_headers["Cookie"] = f"ph_test_test_session={token}"
        # Older tests also send the bearer header; production authenticates
        # through the cookie only.
        request_headers["Authorization"] = f"Bearer {token}"
    for name, value in (headers or {}).items():
        if value is None:
            request_headers.pop(name, None)
        else:
            request_headers[name] = value
    handler.headers = request_headers
    handler.rfile.read.return_value = raw

    return handler


@pytest.fixture
def make_handler():
    """Factory fixture: build a mock request handler for a given request."""
    return _build_handler


@pytest.fixture
def call_api(make_handler):
    """
    Drive one request through the real router dispatch function and return
    (status, body): the status read from the first positional argument of
    the first send_response call, and the body read from the first
    positional argument of the first wfile.write call, decoded as UTF-8 and
    parsed as JSON.
    """

    def _call(
        method: str,
        path: str,
        body: dict = None,
        token: str = None,
        headers: dict = None,
        raw_body: bytes = None,
    ):
        body = _with_lead_versioning(method, path, body)
        handler = make_handler(
            method,
            path,
            body=body,
            token=token,
            headers=headers,
            raw_body=raw_body,
        )
        dispatch(handler, method, path)

        status = handler.send_response.call_args_list[0][0][0]
        raw_response = handler.wfile.write.call_args_list[0][0][0]
        parsed = json.loads(raw_response.decode("utf-8"))
        # Compatibility for older integration callers. The
        # production response contains no token; legacy tests receive the
        # opaque cookie value through the request-driver abstraction only.
        response_headers = dict(call.args for call in handler.send_header.call_args_list)
        set_cookie = response_headers.get("Set-Cookie", "")
        if set_cookie and "token" not in parsed:
            cookie = SimpleCookie()
            cookie.load(set_cookie)
            if "ph_test_test_session" in cookie:
                parsed["token"] = cookie["ph_test_test_session"].value
        return status, parsed

    return _call


@pytest.fixture
def call_api_raw(make_handler):
    """
    Drive one request through the real router dispatch function and return
    the raw mock handler (not a parsed status/body pair), for tests that
    must assert nothing was sent at all -- e.g. an unmatched route.
    """

    def _call(
        method: str,
        path: str,
        body: dict = None,
        token: str = None,
        headers: dict = None,
        raw_body: bytes = None,
    ):
        handler = make_handler(
            method,
            path,
            body=body,
            token=token,
            headers=headers,
            raw_body=raw_body,
        )
        dispatch(handler, method, path)
        return handler

    return _call


@pytest.fixture
def call_api_details(make_handler):
    """Drive dispatch and expose status, parsed body, headers and handler."""

    def _call(method: str, path: str, **kwargs):
        handler = make_handler(method, path, **kwargs)
        dispatch(handler, method, path)
        status = handler.send_response.call_args_list[0][0][0]
        raw_response = handler.wfile.write.call_args_list[0][0][0]
        body = json.loads(raw_response.decode("utf-8"))
        response_headers = dict(call.args for call in handler.send_header.call_args_list)
        return status, body, response_headers, handler

    return _call
