from unittest.mock import MagicMock

import pytest

from api.http_security import MAX_JSON_BODY_BYTES


@pytest.mark.parametrize(
    "origin",
    ["https://evil.example", "null", "not an origin", "http://localhost:8000.evil.test"],
)
def test_unsafe_disallowed_origins_are_rejected_before_dispatch(
    call_api_details, monkeypatch, origin
):
    domain = MagicMock(return_value=(200, {"ok": True}))
    monkeypatch.setattr("api.router.handle_login", domain)
    status, body, _, _ = call_api_details(
        "POST", "/api/auth/login", body={}, headers={"Origin": origin}
    )
    assert status == 403
    assert body == {"error": "Niedozwolone pochodzenie żądania."}
    domain.assert_not_called()


def test_unsafe_request_requires_origin_or_referer(call_api_details, monkeypatch):
    domain = MagicMock(return_value=(200, {"ok": True}))
    monkeypatch.setattr("api.router.handle_login", domain)
    status, _, _, _ = call_api_details(
        "POST", "/api/auth/login", body={}, headers={"Origin": None}
    )
    assert status == 403
    domain.assert_not_called()


def test_allowed_origin_and_referer_reach_domain_dispatch(call_api_details, monkeypatch):
    domain = MagicMock(return_value=(200, {"ok": True}))
    monkeypatch.setattr("api.router.handle_login", domain)
    for headers in (
        {"Origin": "http://localhost:8000"},
        {"Origin": None, "Referer": "http://localhost:8000/login"},
    ):
        status, body, _, _ = call_api_details(
            "POST", "/api/auth/login", body={}, headers=headers
        )
        assert status == 200
        assert body == {"ok": True}
    assert domain.call_count == 2


def test_disallowed_origin_does_not_block_safe_get(call_api_details):
    status, _, _, _ = call_api_details(
        "GET", "/api/auth/me", headers={"Origin": "https://evil.example"}
    )
    assert status == 401


@pytest.mark.parametrize("content_length", [None, "", "abc", "-1", "+2"])
def test_invalid_content_length_is_stable_400_before_domain_dispatch(
    call_api_details, monkeypatch, content_length
):
    domain = MagicMock(return_value=(200, {"ok": True}))
    monkeypatch.setattr("api.router.handle_login", domain)
    status, body, _, _ = call_api_details(
        "POST",
        "/api/auth/login",
        raw_body=b"{}",
        headers={"Content-Length": content_length},
    )
    assert status == 400
    assert body == {"error": "Nieprawidłowe żądanie JSON."}
    domain.assert_not_called()


def test_declared_oversized_body_is_413_before_read_or_dispatch(
    call_api_details, monkeypatch
):
    domain = MagicMock(return_value=(200, {"ok": True}))
    monkeypatch.setattr("api.router.handle_login", domain)
    status, body, _, handler = call_api_details(
        "POST",
        "/api/auth/login",
        raw_body=b"{}",
        headers={"Content-Length": str(MAX_JSON_BODY_BYTES + 1)},
    )
    assert status == 413
    assert body == {"error": "Treść żądania jest zbyt duża."}
    handler.rfile.read.assert_not_called()
    domain.assert_not_called()


def test_valid_body_reads_exactly_the_declared_content_length(
    call_api_details, monkeypatch
):
    domain = MagicMock(return_value=(200, {"ok": True}))
    monkeypatch.setattr("api.router.handle_login", domain)
    raw_body = b'{}'

    status, body, _, handler = call_api_details(
        "POST", "/api/auth/login", raw_body=raw_body
    )

    assert status == 200
    assert body == {"ok": True}
    handler.rfile.read.assert_called_once_with(len(raw_body))


@pytest.mark.parametrize(
    "raw_body",
    [b"\xff", b"{", b"[]", b"null", b'"text"', b"123"],
)
def test_invalid_utf8_json_or_top_level_shape_is_stable_400(
    call_api_details, monkeypatch, raw_body
):
    domain = MagicMock(return_value=(200, {"ok": True}))
    monkeypatch.setattr("api.router.handle_login", domain)
    status, body, _, _ = call_api_details(
        "POST", "/api/auth/login", raw_body=raw_body
    )
    assert status == 400
    assert body == {"error": "Nieprawidłowe żądanie JSON."}
    domain.assert_not_called()
