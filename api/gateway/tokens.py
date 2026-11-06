"""Secrets for the API gateway: generation, fingerprints and the PKCE check.

Pure standard library on purpose: every credential the
gateway hands out is an opaque random string with a short type prefix, and the
database only ever keeps its SHA-256 fingerprint -- the same rule as sessions,
invitations and password resets already follow. No JWT, so there is no signing
key to leak or rotate.

Prefixes tell a reader (and the bearer authenticator) what kind of secret it is
looking at without a database round trip:

    ptk_  personal API key (Claude Code, scripts)
    pta_  OAuth access token (1 h)
    ptr_  OAuth refresh token (30 d, rotated, never past the grant's 90 d)
    ptc_  OAuth client id (public, not a secret)
"""
import base64
import hashlib
import hmac
import re
import secrets

KEY_PREFIX = "ptk_"
ACCESS_PREFIX = "pta_"
REFRESH_PREFIX = "ptr_"
CLIENT_PREFIX = "ptc_"

_PREFIXES = (KEY_PREFIX, ACCESS_PREFIX, REFRESH_PREFIX, CLIENT_PREFIX)

# Absolute lifetime of an OAuth connection (grant), counted from consent.
# Refresh rotation never extends it: after this the user connects again, the
# same way a personal key has a fixed expiry.
OAUTH_GRANT_MAX_DAYS = 90

# How much of the secret after its prefix is kept in clear for recognition
# ("ptk_ab12cd34…" on the Integrations screen). 8 url-safe characters are 48
# bits -- enough to tell keys apart, far too little to guess the other 208.
DISPLAY_CHARS = 8

# RFC 7636 §4.1: unreserved characters, 43..128 long.
VERIFIER_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
# A S256 challenge is base64url(sha256) without padding: always 43 characters.
CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def new_secret() -> str:
    """256 random bits, url-safe, no prefix."""
    return secrets.token_urlsafe(32)


def new_token(prefix: str) -> str:
    """A prefixed 256-bit secret, e.g. new_token(KEY_PREFIX) -> 'ptk_…'."""
    if prefix not in _PREFIXES:
        raise ValueError("Unknown token prefix")
    return prefix + new_secret()


def new_client_id() -> str:
    """A public OAuth client identifier. Not a secret, so 128 bits suffice."""
    return CLIENT_PREFIX + secrets.token_urlsafe(16)


def token_hash(raw: str) -> str:
    """The stored fingerprint of a secret: SHA-256, lowercase hex."""
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def display_prefix(raw: str) -> str:
    """The recognisable start of a secret: its type prefix plus 8 characters."""
    raw = raw or ""
    for prefix in _PREFIXES:
        if raw.startswith(prefix):
            return raw[:len(prefix) + DISPLAY_CHARS]
    return raw[:DISPLAY_CHARS]


def pkce_s256_matches(verifier, challenge) -> bool:
    """RFC 7636 §4.6: does base64url(sha256(verifier)) equal the challenge?

    Any input that does not have the shape the RFC requires is simply a
    mismatch -- the token endpoint answers invalid_grant either way, and a
    malformed verifier must never reach the comparison as something else.
    """
    if not isinstance(verifier, str) or not isinstance(challenge, str):
        return False
    if not VERIFIER_RE.fullmatch(verifier) or not CHALLENGE_RE.fullmatch(challenge):
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(computed, challenge)
