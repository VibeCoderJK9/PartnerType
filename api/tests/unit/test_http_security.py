import pytest

from api.http_security import (
    MAX_JSON_BODY_BYTES,
    SESSION_COOKIE_NAME,
    build_cors_headers,
    build_session_cookie,
    build_session_cookie_expiry,
    extract_session_cookie,
    is_request_origin_allowed,
    normalize_origin,
    unsafe_request_is_trusted,
)
from server import create_app


ALLOWED = ("http://localhost:8000", "https://partners.example.com")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://localhost:8000", "http://localhost:8000"),
        ("https://partners.example.com/", "https://partners.example.com"),
        ("null", None),
        ("https://partners.example.com/path", None),
        ("javascript:alert(1)", None),
        ("https://user@partners.example.com", None),
        ("", None),
    ],
)
def test_normalize_origin_accepts_only_serialized_http_origins(value, expected):
    assert normalize_origin(value) == expected


def test_cors_headers_echo_only_exact_allowed_origin():
    assert build_cors_headers("https://partners.example.com", ALLOWED) == {
        "Access-Control-Allow-Origin": "https://partners.example.com",
        "Access-Control-Allow-Credentials": "true",
        "Vary": "Origin",
    }
    assert build_cors_headers("https://evil.example", ALLOWED) == {}
    assert build_cors_headers(None, ALLOWED) == {}


def test_origin_decision_rejects_null_and_malformed_values():
    assert is_request_origin_allowed("http://localhost:8000", ALLOWED)
    assert not is_request_origin_allowed("null", ALLOWED)
    assert not is_request_origin_allowed("http://localhost:8000.evil.test", ALLOWED)


def test_session_cookie_is_host_only_http_only_strict_and_bounded():
    header = build_session_cookie("opaque-value", secure=False)
    assert header.startswith(f"{SESSION_COOKIE_NAME}=opaque-value")
    assert "HttpOnly" in header
    assert "SameSite=Strict" in header
    assert "Path=/" in header
    assert "Max-Age=86400" in header
    assert "Secure" not in header

    secure_header = build_session_cookie("opaque-value", secure=True)
    assert "Secure" in secure_header


def test_expiry_cookie_targets_the_same_cookie_and_path():
    header = build_session_cookie_expiry(secure=True)
    assert header.startswith(f"{SESSION_COOKIE_NAME}=")
    assert "Max-Age=0" in header
    assert "Path=/" in header
    assert "Secure" in header


def test_cookie_parser_handles_valid_missing_and_malformed_headers():
    assert extract_session_cookie(f"theme=dark; {SESSION_COOKIE_NAME}=opaque") == "opaque"
    assert extract_session_cookie("theme=dark") == ""
    assert extract_session_cookie("bad-cookie=\"unterminated") == ""


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_do_not_require_origin_proof(method):
    assert unsafe_request_is_trusted(method, {}, ALLOWED)


def test_unsafe_method_accepts_allowed_origin_or_referer_only():
    assert unsafe_request_is_trusted(
        "POST", {"Origin": "http://localhost:8000"}, ALLOWED
    )
    assert unsafe_request_is_trusted(
        "PATCH", {"Referer": "https://partners.example.com/settings"}, ALLOWED
    )
    assert not unsafe_request_is_trusted("POST", {}, ALLOWED)
    assert not unsafe_request_is_trusted(
        "DELETE", {"Origin": "https://evil.example"}, ALLOWED
    )


def test_options_uses_exact_origin_policy(monkeypatch):
    monkeypatch.setenv('PARTNERTYPE_ALLOWED_ORIGINS', ','.join(ALLOWED))
    response = create_app().test_client().options('/api/auth/login', headers={'Origin': 'https://partners.example.com'})
    assert response.status_code == 204
    assert response.headers['Access-Control-Allow-Origin'] == 'https://partners.example.com'
    assert response.headers['Access-Control-Allow-Credentials'] == 'true'


def test_documented_json_cap_is_one_mebibyte():
    assert MAX_JSON_BODY_BYTES == 1024 * 1024
