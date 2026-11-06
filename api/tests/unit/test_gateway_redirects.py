"""Redirect URI rules of the OAuth front channel.

Pure functions: what a client may register, which requested URI matches a
registered one, how every redirect back to a client is built, and which
`resource` values name this instance.
"""
from urllib.parse import parse_qsl, urlsplit

import pytest

from api.gateway import redirects

ISS = "http://localhost:8000"


# ─── redirect_matches ────────────────────────────────────────────────────────

def test_exact_string_matches():
    uri = "https://claude.ai/api/mcp/auth_callback"
    assert redirects.redirect_matches(uri, uri) is True


def test_loopback_ignores_the_port():
    assert redirects.redirect_matches("http://127.0.0.1/callback",
                                      "http://127.0.0.1:53111/callback") is True
    assert redirects.redirect_matches("http://localhost/callback",
                                      "http://localhost:4000/callback") is True
    assert redirects.redirect_matches("http://[::1]/callback",
                                      "http://[::1]:4000/callback") is True


def test_localhost_is_not_127_0_0_1():
    assert redirects.redirect_matches("http://127.0.0.1/callback",
                                      "http://localhost:1234/callback") is False


@pytest.mark.parametrize("requested", [
    "http://127.0.0.1:5000/other",
    "http://127.0.0.1:5000/callback?x=1",
    "http://127.0.0.1:5000/callback/",
])
def test_loopback_path_and_query_must_match(requested):
    assert redirects.redirect_matches("http://127.0.0.1/callback", requested) is False


def test_https_trailing_slash_is_another_uri():
    assert redirects.redirect_matches("https://claude.ai/x", "https://claude.ai/x/") is False


def test_https_port_is_not_ignored():
    assert redirects.redirect_matches("https://claude.ai/x", "https://claude.ai:8443/x") is False


@pytest.mark.parametrize("requested", [
    "http://127.0.0.1:5000/callback#frag",
    "http://user:pw@127.0.0.1:5000/callback",
    "http://user@127.0.0.1/callback",
])
def test_requested_with_fragment_or_userinfo_never_matches(requested):
    assert redirects.redirect_matches("http://127.0.0.1/callback", requested) is False


def test_fragment_never_matches_even_when_equal():
    uri = "https://claude.ai/cb#frag"
    assert redirects.redirect_matches(uri, uri) is False


@pytest.mark.parametrize("requested", [None, "", 42])
def test_missing_requested_never_matches(requested):
    assert redirects.redirect_matches("https://claude.ai/cb", requested) is False


def test_http_on_a_real_host_gets_no_port_leniency():
    assert redirects.redirect_matches("http://example.com/cb",
                                      "http://example.com:81/cb") is False


# ─── validate_redirect_uri ───────────────────────────────────────────────────

@pytest.mark.parametrize("uri", [
    "http://example.com/cb",
    "javascript:alert(1)",
    "myapp://cb",
    "https://user:pw@claude.ai/cb",
    "https://claude.ai/cb#frag",
    "https://claude.ai/" + "a" * 600,
    "https:///nohost",
    "ftp://claude.ai/cb",
    "https://claude.ai/c b",
    "https://claude.ai:99999/cb",
    "",
    None,
    123,
])
def test_validate_rejects(uri):
    with pytest.raises(ValueError):
        redirects.validate_redirect_uri(uri)


@pytest.mark.parametrize("uri", [
    "https://claude.ai/api/mcp/auth_callback",
    "http://localhost/callback",
    "http://127.0.0.1/callback",
    "http://[::1]/callback",
    "http://localhost:33418/callback",
    "https://claude.ai/cb?tenant=1",
])
def test_validate_accepts(uri):
    assert redirects.validate_redirect_uri(uri) == uri


@pytest.mark.parametrize("uri", [
    "https://cl\u0430ude.ai/api/mcp/auth_callback",   # Cyrillic "а" in the host
    "https://claude.ai\u3002evil.com/cb",              # ideographic full stop
    "https://claude.ai\uff0eevil.com/cb",              # full-width full stop
    "https://claude.ai%2eevil.com/cb",                 # percent-escape in the host
    "https://claude.ai%2Eevil.com/cb",
    "https://cl%C3%A1ude.ai/cb",
    "https://claude.ai./cb",                           # trailing dot
    "https://-claude.ai/cb",
    "https://claude_ai.example/cb",
    "https://[fe80::1%25en0]/cb",
    "https://claude.ai/caf\u00e9",                     # non-ASCII anywhere
])
def test_validate_rejects_hosts_that_can_pass_for_another(uri):
    with pytest.raises(ValueError):
        redirects.validate_redirect_uri(uri)


@pytest.mark.parametrize("uri", [
    "https://xn--clude-6ve.ai/cb",
    "https://sub.example-company.pl/oauth/cb",
    "https://10.0.0.5/cb",
    "https://[2001:db8::1]/cb",
    "https://claude.ai/cb?next=%2Fhome",
])
def test_validate_accepts_plain_ascii_hosts(uri):
    assert redirects.validate_redirect_uri(uri) == uri


@pytest.mark.parametrize("host, expected", [
    ("xn--clude-6ve.ai", True),
    ("login.xn--clude-6ve.ai", True),
    ("XN--CLUDE-6VE.AI", True),
    ("claude.ai", False),
    ("axn--b.com", False),
    ("", False),
    (None, False),
])
def test_is_punycode_host(host, expected):
    assert redirects.is_punycode_host(host) is expected


# ─── build_redirect ──────────────────────────────────────────────────────────

def test_build_redirect_keeps_query_encodes_state_and_appends_iss_last():
    url = redirects.build_redirect("https://c.ai/cb?x=1", ISS, state="a&b=c", code="K")

    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path, parts.fragment) == ("https", "c.ai", "/cb", "")
    query = parse_qsl(parts.query, keep_blank_values=True)
    assert query == [("x", "1"), ("code", "K"), ("state", "a&b=c"), ("iss", ISS)]
    assert "state=a%26b%3Dc" in parts.query
    assert parts.query.endswith("iss=http%3A%2F%2Flocalhost%3A8000")


def test_build_redirect_without_state_still_has_iss():
    url = redirects.build_redirect("https://c.ai/cb", ISS, None, error="access_denied")

    query = dict(parse_qsl(urlsplit(url).query))
    assert "state" not in query
    assert query == {"error": "access_denied", "iss": ISS}


def test_build_redirect_leaves_out_none_params():
    url = redirects.build_redirect("https://c.ai/cb", ISS, "s", error="invalid_request",
                                   error_description=None)

    assert "error_description" not in dict(parse_qsl(urlsplit(url).query))


def test_build_redirect_keeps_an_empty_state():
    url = redirects.build_redirect("https://c.ai/cb", ISS, "", code="K")

    assert ("state", "") in parse_qsl(urlsplit(url).query, keep_blank_values=True)


# ─── canonical_resource / is_loopback ────────────────────────────────────────

ALLOWED = ("http://localhost:8000/mcp", "http://localhost:8000/api/v1")


@pytest.mark.parametrize("value,expected", [
    ("http://localhost:8000/mcp", "http://localhost:8000/mcp"),
    ("HTTP://LOCALHOST:8000/mcp", "http://localhost:8000/mcp"),
    ("http://localhost:8000/api/v1", "http://localhost:8000/api/v1"),
    ("http://localhost:8000/MCP", None),
    ("http://localhost:8000/mcp/", None),
    ("http://localhost:8000/mcp?x=1", None),
    ("http://localhost:8000/mcp#f", None),
    ("https://evil.example/mcp", None),
    ("http://user@localhost:8000/mcp", None),
    ("", None),
    (None, None),
])
def test_canonical_resource(value, expected):
    assert redirects.canonical_resource(value, ALLOWED) == expected


@pytest.mark.parametrize("uri,expected", [
    ("http://localhost/callback", True),
    ("http://127.0.0.1:5000/callback", True),
    ("http://[::1]/callback", True),
    ("https://localhost/callback", False),
    ("https://claude.ai/cb", False),
    ("http://example.com/cb", False),
    (None, False),
])
def test_is_loopback(uri, expected):
    assert redirects.is_loopback(uri) is expected


# ─── host_is_allowed ─────────────────────────────────────────────────────────

KNOWN_HOSTS = frozenset({"claude.ai", "claude.com"})


@pytest.mark.parametrize("uri", [
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    "http://127.0.0.1:53111/callback",
    "http://localhost/callback",
])
def test_known_hosts_and_loopback_are_allowed(uri):
    assert redirects.host_is_allowed(uri, KNOWN_HOSTS) is True


@pytest.mark.parametrize("uri", [
    "https://partnertype-connect.example/cb",
    "https://evil.claude.ai.example/cb",
    "https://sub.claude.ai/cb",
    "http://claude.ai/cb",
])
def test_other_hosts_are_refused(uri):
    assert redirects.host_is_allowed(uri, KNOWN_HOSTS) is False


def test_wildcard_allows_any_https_host():
    assert redirects.host_is_allowed("https://partnertype-connect.example/cb", None) is True
    assert redirects.host_is_allowed("http://partnertype-connect.example/cb", None) is False
