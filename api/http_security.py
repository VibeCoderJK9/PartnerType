"""HTTP transport security helpers for cookies, CORS, CSRF and JSON limits."""

import os
import re
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit


SESSION_COOKIE_NAME = "ph_" + os.environ.get("PARTNERTYPE_ENV", "test") + "_" + os.environ.get("PARTNERTYPE_INSTANCE_ID", "test") + "_session"
SESSION_MAX_AGE_SECONDS = 24 * 60 * 60
MAX_JSON_BODY_BYTES = 1024 * 1024

DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:8000",
    "http://127.0.0.1:8000",
)

_ORIGIN_RE = re.compile(r"^https?://[^/?#]+/?$")
_SAFE_METHODS = frozenset(("GET", "HEAD", "OPTIONS"))


def client_ip(handler):
    """The caller's address as the transport reports it.

    Behind the reverse proxy this is only the real client once ProxyFix is in
    place (see create_app); without it every request would look like the proxy.
    """
    address = getattr(handler, "client_address", None) or ()
    return str(address[0]) if address and address[0] else ""


def normalize_origin(value):
    """Return the serialized HTTP(S) origin, or ``None`` for unsafe input."""
    if not isinstance(value, str) or not value or value == "null":
        return None
    if not _ORIGIN_RE.fullmatch(value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        port = parsed.port
    except ValueError:
        return None
    host = parsed.hostname.lower()
    if ":" in host:
        host = "[{}]".format(host)
    default_port = (parsed.scheme == "http" and port == 80) or (
        parsed.scheme == "https" and port == 443
    )
    port_part = "" if port is None or default_port else ":{}".format(port)
    return "{}://{}{}".format(parsed.scheme.lower(), host, port_part)



def is_request_origin_allowed(origin, allowed_origins):
    normalized = normalize_origin(origin)
    return normalized is not None and normalized in tuple(allowed_origins)


def build_cors_headers(origin, allowed_origins):
    normalized = normalize_origin(origin)
    if normalized is None or normalized not in tuple(allowed_origins):
        return {}
    return {
        "Access-Control-Allow-Origin": normalized,
        "Access-Control-Allow-Credentials": "true",
        "Vary": "Origin",
    }


def unsafe_request_is_trusted(method, headers, allowed_origins):
    if method.upper() in _SAFE_METHODS:
        return True
    origin = headers.get("Origin")
    if origin is not None:
        return is_request_origin_allowed(origin, allowed_origins)
    referer = headers.get("Referer")
    if not referer:
        return False
    try:
        parsed = urlsplit(referer)
        referer_origin = "{}://{}".format(parsed.scheme, parsed.netloc)
    except (TypeError, ValueError):
        return False
    return is_request_origin_allowed(referer_origin, allowed_origins)


def extract_session_cookie(cookie_header):
    if not cookie_header:
        return ""
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except (CookieError, TypeError, ValueError):
        return ""
    morsel = cookie.get(SESSION_COOKIE_NAME)
    return morsel.value if morsel is not None else ""


def cookie_secure_for_environment():
    """Use insecure cookies only for an explicit local development runtime."""
    environment = os.environ.get("PARTNERTYPE_ENV", "").strip().lower()
    return environment not in ("development", "dev", "local", "test")


def build_session_cookie(token, secure=None):
    if secure is None:
        secure = cookie_secure_for_environment()
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE_NAME] = token
    morsel = cookie[SESSION_COOKIE_NAME]
    morsel["path"] = "/"
    morsel["httponly"] = True
    morsel["samesite"] = "Strict"
    morsel["max-age"] = str(SESSION_MAX_AGE_SECONDS)
    if secure:
        morsel["secure"] = True
    return morsel.OutputString()


def build_session_cookie_expiry(secure=None):
    if secure is None:
        secure = cookie_secure_for_environment()
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE_NAME] = ""
    morsel = cookie[SESSION_COOKIE_NAME]
    morsel["path"] = "/"
    morsel["httponly"] = True
    morsel["samesite"] = "Strict"
    morsel["max-age"] = "0"
    if secure:
        morsel["secure"] = True
    return morsel.OutputString()
