"""Redirect URIs and resource identifiers for the OAuth front channel.

Pure functions, no database and no Flask, so every rule here is unit-tested
on its own:

- `validate_redirect_uri` decides what a client may REGISTER: `https://` with a
  host, or plain `http://` only on the loopback interface (Claude Code listens
  there). No fragment, no userinfo, at most 512 characters. Custom schemes,
  `javascript:` and `http://` on a real host are refused -- each of those would
  let a stranger receive authorization codes.
- `redirect_matches` decides whether an authorize request may USE a registered
  URI: exact string equality, except that a loopback URI ignores the port
  (RFC 8252 §7.3 -- the native client picks a free port at run time). Host,
  path and query must still match exactly; `localhost` is not `127.0.0.1`.
- `build_redirect` is the ONLY way this package builds a redirect back to a
  client. It keeps the registered URI's own query, adds the response
  parameters, then `state` (unchanged) and always `iss` (RFC 9207). Metadata
  advertises iss support, and a client that sees it then rejects any response
  without it -- error responses included.
- `canonical_resource` maps an RFC 8707 `resource` parameter onto one of the
  instance's own canonical resource URLs, or None.
"""
import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Hostnames as urllib reports them (`[::1]` -> `::1`).
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

MAX_REDIRECT_URI_CHARS = 512

# A DNS name in ASCII letters, digits and hyphens (punycode for
# internationalised names), as urllib reports it: lower case, no trailing dot.
_LDH_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_LDH_HOST_RE = re.compile(rf"(?=.{{1,253}}$){_LDH_LABEL}(?:\.{_LDH_LABEL})*")


def _split(uri):
    """urlsplit plus the port check, or None when the URI does not parse."""
    if not isinstance(uri, str):
        return None
    try:
        parts = urlsplit(uri)
        parts.port  # noqa: B018 -- raises ValueError on a malformed port
    except ValueError:
        return None
    return parts


def _has_userinfo(parts) -> bool:
    return parts.username is not None or parts.password is not None or "@" in parts.netloc


def _has_unsafe_characters(uri) -> bool:
    return any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in uri)


def _is_ip_literal(host) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _has_plain_ascii_host(parts) -> bool:
    """Is the host something a person can read without being fooled?

    Only ASCII letters, digits, hyphens and dots, or an IP address. Unicode
    look-alikes (a Cyrillic "а" in "claude.ai"), full-width dots that a
    browser turns into ordinary ones and percent-escapes in the host are all
    refused, so the host on the consent screen is exactly where the code goes.
    """
    host = parts.hostname or ""
    if not host or "%" in parts.netloc:
        return False
    return bool(_LDH_HOST_RE.fullmatch(host)) or _is_ip_literal(host)


def is_punycode_host(host) -> bool:
    """Does any label of `host` hold an encoded internationalised name (xn--)?"""
    if not isinstance(host, str):
        return False
    return any(label.startswith("xn--") for label in host.lower().split("."))


def is_loopback(uri) -> bool:
    """Is this an `http://` URI on the loopback interface?"""
    parts = _split(uri)
    if parts is None:
        return False
    return parts.scheme == "http" and (parts.hostname or "") in LOOPBACK_HOSTS


def host_is_allowed(uri, allowed_hosts) -> bool:
    """May a code go to `uri`? Loopback always; https only to an allowed host.

    `allowed_hosts` is a set of exact hostnames, or None for any host.
    """
    if is_loopback(uri):
        return True
    parts = _split(uri)
    if parts is None or parts.scheme != "https":
        return False
    return allowed_hosts is None or (parts.hostname or "") in allowed_hosts


def validate_redirect_uri(uri) -> str:
    """Return `uri` unchanged when a client may register it, else ValueError."""
    if not isinstance(uri, str) or not uri:
        raise ValueError("A redirect URI must be a non-empty string")
    if len(uri) > MAX_REDIRECT_URI_CHARS:
        raise ValueError("A redirect URI may have at most 512 characters")
    if _has_unsafe_characters(uri):
        raise ValueError("A redirect URI must not contain whitespace or control characters")
    if not uri.isascii():
        raise ValueError("A redirect URI must be plain ASCII (use punycode for the host)")
    if "#" in uri:
        raise ValueError("A redirect URI must not contain a fragment")
    parts = _split(uri)
    if parts is None:
        raise ValueError("The redirect URI does not parse")
    if _has_userinfo(parts):
        raise ValueError("A redirect URI must not contain user information")
    if not _has_plain_ascii_host(parts):
        raise ValueError("A redirect URI host must be an ASCII hostname or an IP address")
    host = parts.hostname or ""
    if parts.scheme == "https" and host:
        return uri
    if parts.scheme == "http" and host in LOOPBACK_HOSTS:
        return uri
    raise ValueError("A redirect URI must use https, or http on localhost/127.0.0.1/[::1]")


def redirect_matches(registered, requested) -> bool:
    """May an authorize request with `requested` use the registered URI?"""
    if not isinstance(registered, str) or not isinstance(requested, str) or not requested:
        return False
    if "#" in requested or _has_unsafe_characters(requested):
        return False
    if registered == requested:
        return True
    r, q = _split(registered), _split(requested)
    if r is None or q is None:
        return False
    if r.scheme != "http" or q.scheme != "http":
        return False
    r_host, q_host = r.hostname or "", q.hostname or ""
    if r_host not in LOOPBACK_HOSTS or r_host != q_host:
        return False
    if _has_userinfo(q) or q.fragment:
        return False
    # The port is the one thing a native client may change.
    return (r.path or "/") == (q.path or "/") and r.query == q.query


def build_redirect(redirect_uri, issuer, state, **params) -> str:
    """The client's redirect URI with response parameters, `state` and `iss`.

    Parameters whose value is None are left out; `state` is added only when
    the client sent one, and exactly as it sent it; `iss` is always last.
    """
    parts = urlsplit(redirect_uri)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query += [(name, value) for name, value in params.items() if value is not None]
    if state is not None:
        query.append(("state", state))
    query.append(("iss", issuer))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def canonical_resource(value, allowed):
    """The canonical resource URL `value` names, if it is one of `allowed`.

    Scheme and host compare case-insensitively (the MCP spec's SHOULD); the
    path compares exactly, so `/mcp/` and `/MCP` are other resources. No
    query, fragment or userinfo. Returns the matching entry of `allowed`, so
    what gets stored is always the instance's own spelling.
    """
    if not isinstance(value, str) or not value or len(value) > MAX_REDIRECT_URI_CHARS:
        return None
    if "#" in value or "?" in value or _has_unsafe_characters(value):
        return None
    parts = _split(value)
    if parts is None or _has_userinfo(parts):
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    candidate = f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path}"
    for entry in allowed:
        if candidate == entry:
            return entry
    return None
