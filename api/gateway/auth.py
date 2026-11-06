"""The one bearer authenticator for /mcp and /api/v1.

Every gateway request, on either surface, goes through `authenticate_bearer`
and nothing else. It answers with a `Principal` or raises a `GatewayError`;
there is no partially-authenticated state. The checks run in a fixed order and
fail closed:

1. `Authorization: Bearer <token>` exactly -- one scheme (any case), one space,
   one token. The query string and cookies are never read: a token in a
   URL ends up in logs and browser history, and accepting the session cookie
   here would hand every web page the user visits a way to call the gateway.
2. The prefix routes the lookup (`ptk_` -> api_keys, `pta_` -> oauth_tokens
   of kind 'access' under an unrevoked grant whose `resource` must equal the
   surface's own -- the audience check). The secret is looked up
   by its SHA-256 fingerprint only, so a wrong token costs the same work as a
   right one and no raw secret is ever compared as a string.
3. The owner is re-read from `users` with `active = 1` on every call -- a
   deactivation, a role change, a revocation or an expiry committed before a
   request takes effect on that request. Nothing is cached.
4. `connection_refusal`: the administrator's switches and the MFA rule, also on every call.
5. Scope (`read` only).
6. `last_used_at`, at most once a minute so reading does not turn into a write
   per call.

401 carries `WWW-Authenticate: Bearer resource_metadata="…"` -- the signal that
makes Claude start its OAuth sign-in -- and `error="invalid_token"` only when a
token was actually presented (RFC 6750 §3.1). 403 is a terminal refusal.
"""
import re
from dataclasses import dataclass

from api import authz, mfa
from api.auth import _user_to_dict
from api.db import get_conn
from api.postgres import system_context
from api.gateway import settings, tokens
from api.i18n import t

_SURFACE_PATHS = {"mcp": "/mcp", "api_v1": "/api/v1"}

# RFC 6750 §2.1 b64token, behind a case-insensitive "Bearer" and ONE space.
_BEARER_RE = re.compile(r"^[Bb][Ee][Aa][Rr][Ee][Rr] ([A-Za-z0-9\-._~+/]+=*)$")

_LAST_USED_THROTTLE = "60 seconds"


class GatewayError(Exception):
    """A refusal with the HTTP status, protocol error code and headers to send."""
    status = 500

    def __init__(self, error, description="", *, headers=None, principal=None):
        super().__init__(description or error)
        self.error = error
        self.description = description
        self.headers = dict(headers or {})
        # Set once the credential was identified, so the call log
        # can attribute a refused call to the connection that made it.
        self.principal = principal


class Unauthorized(GatewayError):
    """401: no usable credential. The client may (re)authenticate."""
    status = 401

    def __init__(self, description="Authentication required", *, error=None,
                 resource_metadata_url=""):
        # Header values must be plain ASCII; descriptions here are English.
        parts = []
        if error:
            parts.append(f'error="{error}"')
            parts.append(f'error_description="{description}"')
        if resource_metadata_url:
            parts.append(f'resource_metadata="{resource_metadata_url}"')
        parts.append('scope="read"')
        super().__init__(error or "unauthorized", description,
                         headers={"WWW-Authenticate": "Bearer " + ", ".join(parts)})


class Forbidden(GatewayError):
    """403: the credential is valid but this call is not allowed. Terminal."""
    status = 403


class RateLimited(GatewayError):
    """429: over a per-connection limit."""
    status = 429

    def __init__(self, description="Too many requests", *, retry_after=60, principal=None):
        super().__init__("rate_limited", description,
                         headers={"Retry-After": str(max(1, int(retry_after)))},
                         principal=principal)


@dataclass(frozen=True)
class Principal:
    """Who is calling and through which connection.

    `user` has the shape of api.auth._user_to_dict and is what every domain
    function receives -- the gateway never invents a user of its own.
    `kind` is 'key' or 'oauth'; `connection_id` the api_keys.id or the
    oauth_grants.id.
    """
    user: dict
    kind: str
    connection_id: int
    scopes: frozenset
    resource: str


def resource_url(base_url, surface) -> str:
    """The canonical resource identifier of a surface (RFC 8707 audience)."""
    if surface not in _SURFACE_PATHS:
        raise ValueError("Unknown gateway surface")
    return base_url.rstrip("/") + _SURFACE_PATHS[surface]


def resource_metadata_url(base_url, surface) -> str:
    """RFC 9728 protected-resource metadata URL for a surface."""
    if surface not in _SURFACE_PATHS:
        raise ValueError("Unknown gateway surface")
    return (base_url.rstrip("/") + "/.well-known/oauth-protected-resource"
            + _SURFACE_PATHS[surface])


def _is_partner(user) -> bool:
    return (user or {}).get("role") == "partner"


def connection_refusal(user):
    """Why `user` may not use the gateway right now, or None if they may.

    Shared by the authenticator (every call) and by key creation, so a key is
    never minted for someone the gateway would turn away.
    """
    if not settings.integrations_enabled():
        return Forbidden("integrations_disabled",
                         t("Integrations are switched off in this PartnerType instance."))
    if not authz.can_read_leads(user):
        return Forbidden("role_not_allowed",
                         t("This role cannot connect external tools."))
    if _is_partner(user) and not settings.partners_enabled():
        return Forbidden("partners_disabled",
                         t("The administrator has not allowed partners to connect their own AI."))
    if mfa.is_required_for(user["role"]) and not mfa.is_enabled(user["id"]):
        return Forbidden("mfa_setup_required",
                         t("Turn on the second sign-in factor first."))
    return None


def _header(headers, name):
    value = headers.get(name)
    if value is None and isinstance(headers, dict):
        lowered = name.lower()
        for key, item in headers.items():
            if isinstance(key, str) and key.lower() == lowered:
                return item
    return value


def _lookup_key(raw):
    with system_context("gateway key lookup"):
        return _lookup_key_row(raw)


def _lookup_key_row(raw):
    conn = get_conn()
    try:
        return conn.execute(
            """SELECT k.id AS _connection_id, k.scopes AS _scopes, u.*
               FROM api_keys k
               JOIN users u ON u.id = k.user_id AND u.active = 1
               WHERE k.token_hash = ?
                 AND k.revoked_at IS NULL
                 AND k.expires_at > now()""",
            (tokens.token_hash(raw),),
        ).fetchone()
    finally:
        conn.close()


def _lookup_access_token(raw):
    """An unexpired, unrevoked access token of an unrevoked grant, with its owner.

    The grant must also be within its absolute lifetime. The grant's
    `resource` comes back so the caller can enforce the audience (RFC 8707):
    a token issued for /mcp is not a token for /api/v1.
    """
    with system_context("gateway access token lookup"):
        return _lookup_access_token_row(raw)


def _lookup_access_token_row(raw):
    conn = get_conn()
    try:
        return conn.execute(
            """SELECT g.id AS _connection_id, g.scope AS _scopes,
                      g.resource AS _resource, u.*
               FROM oauth_tokens t
               JOIN oauth_grants g ON g.id = t.grant_id AND g.revoked_at IS NULL
               JOIN users u ON u.id = g.user_id AND u.active = 1
               WHERE t.token_hash = ?
                 AND t.kind = 'access'
                 AND t.revoked_at IS NULL
                 AND t.expires_at > now()
                 AND g.created_at > now() - make_interval(days => CAST(? AS integer))""",
            (tokens.token_hash(raw), tokens.OAUTH_GRANT_MAX_DAYS),
        ).fetchone()
    finally:
        conn.close()


def _touch(principal):
    table = "api_keys" if principal.kind == "key" else "oauth_grants"
    with get_conn() as conn:
        conn.execute(
            f"""UPDATE {table} SET last_used_at = now()
                WHERE id = ?
                  AND (last_used_at IS NULL
                       OR last_used_at < now() - INTERVAL '{_LAST_USED_THROTTLE}')""",
            (principal.connection_id,),
        )


def authenticate_bearer(headers, resource, resource_metadata_url) -> Principal:
    """Authenticate one gateway request from its headers alone."""
    match = _BEARER_RE.fullmatch(_header(headers, "Authorization") or "")
    if not match:
        raise Unauthorized(resource_metadata_url=resource_metadata_url)
    raw = match.group(1)

    def invalid():
        return Unauthorized("The access token is invalid, expired or revoked",
                            error="invalid_token",
                            resource_metadata_url=resource_metadata_url)

    if raw.startswith(tokens.KEY_PREFIX):
        row = _lookup_key(raw)
        kind = "key"
    elif raw.startswith(tokens.ACCESS_PREFIX):
        row = _lookup_access_token(raw)
        kind = "oauth"
        # Audience binding: exact canonical string, no
        # prefix or case games. Refused like an unknown token, so a caller
        # learns nothing about tokens minted for another surface.
        if row is not None and row["_resource"] != resource:
            row = None
    else:
        # Refresh tokens (ptr_), client ids and anything else are never
        # bearer credentials.
        row = None
        kind = None
    if row is None:
        raise invalid()

    user = _user_to_dict(row)
    principal = Principal(
        user=user,
        kind=kind,
        connection_id=row["_connection_id"],
        scopes=frozenset((row["_scopes"] or "").split()),
        resource=resource,
    )

    refusal = connection_refusal(user)
    if refusal is not None:
        refusal.principal = principal
        raise refusal
    if "read" not in principal.scopes:
        raise Forbidden("insufficient_scope", t("This key has no read permission."),
                        principal=principal)

    _touch(principal)
    return principal
