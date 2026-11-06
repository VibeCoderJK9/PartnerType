"""The instance's own OAuth 2.1 authorization server, front channel.

Hand-rolled on the standard library: every rule below is a few lines
and is pinned by a test, which is easier to audit than a framework configured
around this app's cookie and CSP policy.

What this module serves (front channel, token endpoint, refresh rotation and
revocation):

    GET  /.well-known/oauth-protected-resource[/mcp|/api/v1]   RFC 9728
    GET  /.well-known/oauth-authorization-server               RFC 8414
    POST /oauth/register             dynamic client registration (RFC 7591)
    GET  /oauth/authorize            validates, stores a pending request,
                                     sends the browser to the consent page
    GET  /api/oauth/requests/<id>    consent page data   (legacy router,
    POST /api/oauth/requests/<id>/decision                session cookie)
    POST /oauth/token                code exchange (PKCE) and refresh rotation
    POST /oauth/revoke               RFC 7009 revocation, always 200

The consent page itself is pages/oauth/consent.html + js/oauth-consent.js.

Why the consent step is split this way:

- The browser reaches /oauth/authorize by a cross-site navigation from
  claude.ai, so the `SameSite=Strict` session cookie is NOT sent. Authorize
  therefore never looks at the session; it only validates and
  redirects to a same-origin page, whose `fetch()` calls do carry the cookie.
- `form-action 'self'` would block an HTML form whose answer redirects to
  claude.ai, so the decision is a JSON POST answering `{"redirect_to": …}` and
  the page navigates there itself.
- The decision goes through the legacy router, so the app's existing CSRF
  defence (Origin/Referer check + SameSite=Strict) and MFA gate apply
  unchanged; the request row is locked and spent in one transaction.

Never an open redirect: an unknown client or a redirect URI that does not
match a registered one gets a fixed HTML error page, never a Location header.
Every redirect that does go back to the client is built by
`redirects.build_redirect`, which always carries `state` and `iss`.
"""
import functools
import html
import json
import logging
import re
import unicodedata
import uuid
from urllib.parse import urlsplit

from flask import Response, has_request_context, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from api import audit
from api.config import Config
from api.db import get_conn
from api.postgres import system_context
from api.gateway import auth, limits, redirects, settings, tokens
from api.http_security import client_ip
from api.i18n import N_, language_from_header, t

logger = logging.getLogger(__name__)

AUTH_REQUEST_TTL_SECONDS = 600
CODE_TTL_SECONDS = 600
MAX_REGISTRATION_BYTES = 8192
MAX_REDIRECT_URIS = 5
MAX_STATE_CHARS = 1024
MAX_CLIENT_NAME_CHARS = 100
MAX_APPLICATION_TYPE_CHARS = 32
REGISTER_WINDOW_SECONDS = 3600
# /oauth/authorize needs no login and stores a row per valid request, so it is
# limited per client and per IP, and each insert also clears a batch of
# expired rows: the table stays bounded even between maintenance runs.
AUTHORIZE_PER_CLIENT_PER_MINUTE = 60
AUTHORIZE_PER_IP_PER_MINUTE = 30
AUTHORIZE_WINDOW_SECONDS = 60
AUTHORIZE_CLEANUP_BATCH = 100

# Token endpoint. Access tokens are short; refresh tokens rotate on
# every use, and a rotated one presented again revokes the whole grant --
# except within a short grace window, where it is the benign race of two
# near-simultaneous refreshes by the same client.
ACCESS_TTL_SECONDS = 3600
REFRESH_TTL_DAYS = 30
REFRESH_REUSE_GRACE_SECONDS = 30
TOKEN_RATE_PER_MINUTE = 60
TOKEN_WINDOW_SECONDS = 60
FORM_MIMETYPE = "application/x-www-form-urlencoded"
# RFC 6749 §5.1: token responses must never be cached, by anyone.
NO_STORE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}

SUPPORTED_SCOPE = "read"
# Claude appends offline_access when an AS advertises it; we never do, and a
# client that adds it anyway still gets a refresh token, so it is dropped
# rather than refused.
_IGNORED_SCOPES = frozenset({"offline_access"})

CONSENT_PAGE = "/pages/oauth/consent.html"
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")

def _request_expired():
    return 404, {
        "error": t("This connection request has expired or has already been used."),
        "code": "request_expired",
    }


# ─── helpers ─────────────────────────────────────────────────────────────────

def _issuer() -> str:
    """The instance's canonical base URL: the OAuth issuer, from config only."""
    return Config.load().base_url


def _resources(base):
    return (auth.resource_url(base, "mcp"), auth.resource_url(base, "api_v1"))


def _json(status, payload, headers=None):
    return Response(json.dumps(payload, ensure_ascii=False), status=status,
                    headers=headers or {}, content_type="application/json; charset=utf-8")


def _oauth_error(status, error, description, headers=None):
    return _json(status, {"error": error, "error_description": description}, headers)


def _redirect(location):
    return Response(status=302, headers={"Location": location})


def _clean_text(value, limit):
    """Drop control and format characters (incl. bidi overrides), trim, cap."""
    cleaned = "".join(ch for ch in value if unicodedata.category(ch) not in ("Cc", "Cf"))
    return cleaned.strip()[:limit]


def _registered_uris(row):
    try:
        value = json.loads(row["redirect_uris"] or "[]")
    except (TypeError, ValueError):
        return []
    return [uri for uri in value if isinstance(uri, str)] if isinstance(value, list) else []


def _load_client(client_id):
    if not isinstance(client_id, str) or not client_id.startswith(tokens.CLIENT_PREFIX) \
            or len(client_id) > 64:
        return None
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT client_id, client_name, redirect_uris FROM oauth_clients WHERE client_id = ?",
            (client_id,),
        ).fetchone()
    finally:
        conn.close()


# ─── discovery documents ─────────────────────────────────────────────────────

def protected_resource_metadata(base_url, surface) -> dict:
    """RFC 9728 metadata of one protected resource ("mcp" or "api_v1").

    `resource` must equal, character for character, the URL the user types
    into Claude, so it comes from the same helper the bearer
    authenticator uses for the audience check.
    """
    base = base_url.rstrip("/")
    return {
        "resource": auth.resource_url(base, surface),
        "authorization_servers": [base],
        "scopes_supported": [SUPPORTED_SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": "PartnerType",
        "resource_documentation": base + "/api/v1/openapi.json",
    }


def authorization_server_metadata(base_url) -> dict:
    """RFC 8414 metadata of this instance's authorization server.

    Deliberately absent: `offline_access` (Claude would append it)
    and `client_id_metadata_document_supported` -- CIMD would make this server
    fetch URLs chosen by strangers. Advertising iss support commits every authorization response
    to carry `iss`, which `redirects.build_redirect` guarantees.
    """
    base = base_url.rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": base + "/oauth/authorize",
        "token_endpoint": base + "/oauth/token",
        "registration_endpoint": base + "/oauth/register",
        "revocation_endpoint": base + "/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [SUPPORTED_SCOPE],
        "authorization_response_iss_parameter_supported": True,
        "service_documentation": base + "/api/v1/openapi.json",
    }


def _metadata_response(build):
    """Public, non-credentialed JSON -- or 404 while integrations are off."""
    if not settings.integrations_enabled():
        return _json(404, {"error": "not_found"})
    # Safe because nothing here depends on who asks; nowhere else gets `*`.
    return _json(200, build(), headers={"Access-Control-Allow-Origin": "*"})


# ─── error page ──────────────────────────────────────────────────────────────

# Fixed sentences, chosen by kind. Nothing from the request is ever echoed:
# this page is reached precisely when the request cannot be trusted. The page
# shows them in Polish and in English (api/locale/pl.json holds the Polish).
_ERROR_TEXTS = {
    "invalid_client": (
        N_("Unknown application"),
        N_("The application trying to connect is not registered with this PartnerType "
           "instance. Go back to it and start connecting again."),
    ),
    "invalid_redirect_uri": (
        N_("Invalid return address"),
        N_("The address the application wants to return to does not match the one it "
           "registered. For your safety you will not be redirected."),
    ),
    "invalid_request": (
        N_("Invalid connection request"),
        N_("The connection request is incomplete or malformed. Go back to the "
           "application and start connecting again."),
    ),
    "too_many_requests": (
        N_("Too many connection attempts"),
        N_("Too many connection requests arrived from this address or for this application "
           "in a short time. Wait a minute and try again."),
    ),
    "server_error": (
        N_("Server error"),
        N_("The connection request could not be handled. Please try again shortly."),
    ),
}


def oauth_error_page(kind, status=400) -> Response:
    """A static page for errors that must not redirect.

    Written in the browser's language (Accept-Language), with the English
    underneath when that is another language: this page is reached from an
    outside application, so it cannot know the app's language switch.
    """
    title_en, text_en = _ERROR_TEXTS.get(kind, _ERROR_TEXTS["invalid_request"])
    lang = language_from_header(request.headers.get("Accept-Language") if has_request_context() else None)
    title, text = t(title_en, lang=lang), t(text_en, lang=lang)
    e = html.escape
    english = (f'<h2 lang="en">{e(title_en)}</h2><p lang="en">{e(text_en)}</p>'
               if lang != "en" else "")
    body = (
        "<!doctype html>\n"
        f'<html lang="{e(lang)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f"<title>PartnerType — {e(title)}</title></head>"
        "<body><main>"
        f"<h1>{e(title)}</h1><p>{e(text)}</p>"
        f"{english}"
        "</main></body></html>\n"
    )
    return Response(body, status=status, content_type="text/html; charset=utf-8")


# ─── POST /oauth/register ────────────────────────────────────────────────────

def _register_view():
    if not settings.integrations_enabled():
        return _oauth_error(403, "access_denied",
                            "Integrations are disabled on this instance")

    allowed, retry_after = limits.consume(
        "register:ip:" + (request.remote_addr or "unknown"),
        settings.operator_caps()["register_per_hour"], REGISTER_WINDOW_SECONDS)
    if not allowed:
        return _oauth_error(429, "too_many_requests", "Too many client registrations",
                            headers={"Retry-After": str(retry_after)})

    if request.mimetype != "application/json":
        return _oauth_error(400, "invalid_client_metadata",
                            "Content-Type must be application/json")
    if (request.content_length or 0) > MAX_REGISTRATION_BYTES:
        return _oauth_error(413, "invalid_client_metadata", "Registration body too large")
    raw = request.get_data(cache=False)
    if len(raw) > MAX_REGISTRATION_BYTES:
        return _oauth_error(413, "invalid_client_metadata", "Registration body too large")
    try:
        metadata = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _oauth_error(400, "invalid_client_metadata", "Body is not valid JSON")
    if not isinstance(metadata, dict):
        return _oauth_error(400, "invalid_client_metadata", "Body must be a JSON object")

    uris = metadata.get("redirect_uris")
    if not isinstance(uris, list) or not 1 <= len(uris) <= MAX_REDIRECT_URIS:
        return _oauth_error(400, "invalid_redirect_uri",
                            f"redirect_uris must list 1 to {MAX_REDIRECT_URIS} URIs")
    validated = []
    for uri in uris:
        try:
            uri = redirects.validate_redirect_uri(uri)
        except ValueError as exc:
            return _oauth_error(400, "invalid_redirect_uri", str(exc))
        if not redirects.host_is_allowed(uri, settings.oauth_redirect_hosts()):
            return _oauth_error(400, "invalid_redirect_uri",
                                "This instance does not accept that redirect host")
        if uri not in validated:
            validated.append(uri)

    name = metadata.get("client_name", "")
    if name is None:
        name = ""
    if not isinstance(name, str):
        return _oauth_error(400, "invalid_client_metadata", "client_name must be a string")
    name = _clean_text(name, MAX_CLIENT_NAME_CHARS)

    app_type = metadata.get("application_type", "")
    if not isinstance(app_type, str) or len(app_type) > MAX_APPLICATION_TYPE_CHARS:
        app_type = ""
    app_type = _clean_text(app_type, MAX_APPLICATION_TYPE_CHARS)

    # Whatever token_endpoint_auth_method the client asked for, it is a public
    # client: no secret is ever issued.
    client_id = tokens.new_client_id()
    with get_conn() as conn:
        row = conn.execute(
            """INSERT INTO oauth_clients
                   (client_id, client_name, redirect_uris, application_type, registered_ip)
               VALUES (?, ?, ?, ?, ?)
               RETURNING created_at""",
            (client_id, name, json.dumps(validated), app_type,
             (request.remote_addr or "")[:64]),
        ).fetchone()
    issued_at = int(row["created_at"].timestamp())

    return _json(201, {
        "client_id": client_id,
        "client_id_issued_at": issued_at,
        "client_name": name,
        "redirect_uris": validated,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    })


# ─── GET /oauth/authorize ────────────────────────────────────────────────────

_REPEATED = object()


def _single(args, name):
    values = args.getlist(name)
    if len(values) > 1:
        return _REPEATED
    return values[0] if values else None


def _normalize_scope(raw):
    """'read' when acceptable, None when the request asks for anything else."""
    requested = [part for part in (raw or "").split(" ") if part]
    requested = [part for part in requested if part not in _IGNORED_SCOPES]
    if any(part != SUPPORTED_SCOPE for part in requested):
        return None
    return SUPPORTED_SCOPE


def _authorize_view(base):
    args = request.args

    # 1. Who is asking and where would we send the answer? Until both are
    #    proven, nothing may redirect anywhere.
    client_id = _single(args, "client_id")
    redirect_uri = _single(args, "redirect_uri")
    if client_id is _REPEATED or redirect_uri is _REPEATED:
        return oauth_error_page("invalid_request")
    client = _load_client(client_id)
    if client is None:
        return oauth_error_page("invalid_client")
    if not redirect_uri or not any(
            redirects.redirect_matches(registered, redirect_uri)
            for registered in _registered_uris(client)):
        return oauth_error_page("invalid_redirect_uri")
    # A client stored before the registration rules tightened may still hold
    # a URI they would refuse today; it must not reach the consent screen.
    try:
        redirects.validate_redirect_uri(redirect_uri)
    except ValueError:
        return oauth_error_page("invalid_redirect_uri")
    if not redirects.host_is_allowed(redirect_uri, settings.oauth_redirect_hosts()):
        return oauth_error_page("invalid_redirect_uri")

    # `state` goes back verbatim on every redirect below, so an oversized or
    # repeated one is refused before anything could reflect it.
    state = _single(args, "state")
    if state is _REPEATED or (state is not None and len(state) > MAX_STATE_CHARS):
        return oauth_error_page("invalid_request")

    def fail(error, description):
        return _redirect(redirects.build_redirect(
            redirect_uri, base, state, error=error, error_description=description))

    # 2. Errors the client is told about, with state and iss.
    for name in ("response_type", "code_challenge", "code_challenge_method",
                 "scope", "resource"):
        if len(args.getlist(name)) > 1:
            return fail("invalid_request", f"Parameter given more than once: {name}")
    if args.get("response_type") != "code":
        return fail("unsupported_response_type", "Only response_type=code is supported")
    challenge = args.get("code_challenge")
    if not challenge or not tokens.CHALLENGE_RE.fullmatch(challenge):
        return fail("invalid_request", "A PKCE code_challenge (S256) is required")
    if args.get("code_challenge_method") != "S256":
        return fail("invalid_request", "code_challenge_method must be S256")
    scope = _normalize_scope(args.get("scope"))
    if scope is None:
        return fail("invalid_scope", "Only the read scope is available")
    allowed_resources = _resources(base)
    if "resource" in args:
        resource = redirects.canonical_resource(args.get("resource"), allowed_resources)
        if resource is None:
            return fail("invalid_target", "Unknown resource")
    else:
        resource = allowed_resources[0]  # older clients: the MCP endpoint
    if not settings.integrations_enabled():
        return fail("access_denied", "Integrations are disabled on this instance")

    # 3. Rate limits, only for requests that would be stored.
    for bucket, limit in (
            ("authorize:client:" + client["client_id"], AUTHORIZE_PER_CLIENT_PER_MINUTE),
            ("authorize:ip:" + (request.remote_addr or "unknown"), AUTHORIZE_PER_IP_PER_MINUTE)):
        allowed, retry_after = limits.consume(bucket, limit, AUTHORIZE_WINDOW_SECONDS)
        if not allowed:
            page = oauth_error_page("too_many_requests", status=429)
            page.headers["Retry-After"] = str(retry_after)
            return page

    # 4. Park the request for the consent click; only its fingerprint is kept.
    request_id = tokens.new_secret()
    with get_conn() as conn:
        conn.execute(
            """DELETE FROM oauth_authorize_requests
               WHERE request_hash IN (SELECT request_hash FROM oauth_authorize_requests
                                      WHERE expires_at < now()
                                      LIMIT CAST(? AS integer))""",
            (AUTHORIZE_CLEANUP_BATCH,),
        )
        conn.execute(
            """INSERT INTO oauth_authorize_requests
                   (request_hash, client_id, redirect_uri, code_challenge, scope,
                    resource, state, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?,
                       now() + make_interval(secs => CAST(? AS integer)))""",
            (tokens.token_hash(request_id), client["client_id"], redirect_uri, challenge,
             scope, resource, state, AUTH_REQUEST_TTL_SECONDS),
        )

    # 5. Same-origin consent page, where the session cookie is available.
    return _redirect(f"{CONSENT_PAGE}?req={request_id}")


# ─── consent API (legacy router, session cookie) ─────────────────────────────

_REQUEST_SQL = """
    SELECT r.request_hash, r.client_id, r.redirect_uri, r.code_challenge, r.scope,
           r.resource, r.state, r.expires_at, c.client_name, c.redirect_uris
    FROM oauth_authorize_requests r
    JOIN oauth_clients c ON c.client_id = r.client_id
    WHERE r.request_hash = ?
      AND r.used_at IS NULL
      AND r.expires_at > now()
"""


def _load_request(conn, request_id, *, lock=False):
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        return None
    sql = _REQUEST_SQL + (" FOR UPDATE OF r" if lock else "")
    return conn.execute(sql, (tokens.token_hash(request_id),)).fetchone()


def _refusal_response(refusal):
    return 403, {"error": refusal.description, "code": refusal.error}


def handle_request_get(handler, user, request_id):
    """What the consent page shows. The client name is self-declared."""
    conn = get_conn()
    try:
        row = _load_request(conn, request_id)
    finally:
        conn.close()
    if row is None:
        return _request_expired()

    refusal = auth.connection_refusal(user)
    if refusal is not None:
        return _refusal_response(refusal)

    base = _issuer()
    registered = _registered_uris(row)
    expires_at = row["expires_at"]
    redirect_host = urlsplit(row["redirect_uri"]).hostname or ""
    return 200, {
        "client_name": row["client_name"] or "",
        "client_name_self_declared": True,
        "redirect_host": redirect_host,
        # An encoded international name (xn--) can stand for a look-alike of a
        # familiar domain, so the page warns about it.
        "redirect_host_punycode": redirects.is_punycode_host(redirect_host),
        "redirect_is_loopback": redirects.is_loopback(row["redirect_uri"]),
        "all_redirects_loopback": bool(registered) and all(
            redirects.is_loopback(uri) for uri in registered),
        "scope": SUPPORTED_SCOPE,
        "resource": row["resource"],
        "resource_kind": "mcp" if row["resource"] == auth.resource_url(base, "mcp") else "api_v1",
        "user": {"name": user.get("name", ""), "role": user.get("role", "")},
        "expires_at": expires_at.isoformat() if hasattr(expires_at, "isoformat") else str(expires_at),
    }


def handle_request_decision(handler, user, request_id, body):
    """Spend the pending request once: deny, or grant + single-use code."""
    approve = (body or {}).get("approve")
    if not isinstance(approve, bool):
        return 400, {"error": t("Field 'approve' must be true or false."),
                     "code": "invalid_decision"}

    base = _issuer()
    conn = get_conn()
    try:
        conn.begin()
        row = _load_request(conn, request_id, lock=True)
        if row is None:
            conn.commit()
            return _request_expired()
        # Spent now, whatever happens next: a request is answered once.
        conn.execute("UPDATE oauth_authorize_requests SET used_at = now() WHERE request_hash = ?",
                     (row["request_hash"],))
        if not approve:
            conn.commit()
            return 200, {"redirect_to": redirects.build_redirect(
                row["redirect_uri"], base, row["state"], error="access_denied")}

        refusal = auth.connection_refusal(user)
        if refusal is not None:
            conn.commit()
            return _refusal_response(refusal)

        # The approved redirect URI is kept on the grant, so the connection
        # lists show where this consent really sent the code.
        grant_id = conn.execute(
            """INSERT INTO oauth_grants (user_id, client_id, scope, resource, redirect_uri)
               VALUES (?, ?, ?, ?, ?)""",
            (user["id"], row["client_id"], row["scope"], row["resource"],
             row["redirect_uri"]),
        ).lastrowid
        code = tokens.new_secret()
        conn.execute(
            """INSERT INTO oauth_codes
                   (code_hash, grant_id, client_id, redirect_uri, code_challenge,
                    resource, scope, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?,
                       now() + make_interval(secs => CAST(? AS integer)))""",
            (tokens.token_hash(code), grant_id, row["client_id"], row["redirect_uri"],
             row["code_challenge"], row["resource"], row["scope"], CODE_TTL_SECONDS),
        )
        conn.commit()
    finally:
        conn.close()

    # Who connected which client -- never the state, the code or the URI.
    audit.log(user, "integration.oauth_consent", "oauth_client", row["client_id"],
              (row["client_name"] or "")[:MAX_CLIENT_NAME_CHARS], client_ip(handler))
    return 200, {"redirect_to": redirects.build_redirect(
        row["redirect_uri"], base, row["state"], code=code)}


# ─── grant revocation ──────

def revoke_grant(grant_id, reason, actor_id=None, *, conn=None) -> bool:
    """Revoke a grant and every token it issued. Idempotent.

    The first revocation wins: a later call keeps the original time, reason
    and actor, so the record says why the connection really ended. Returns
    True when this call is the one that revoked the grant.

    Pass `conn` to revoke inside the caller's transaction (the token endpoint
    does, so a replayed code or refresh token and the revocation it triggers
    commit together); without it the revocation commits on its own.
    """
    if conn is None:
        with get_conn() as own:
            return revoke_grant(grant_id, reason, actor_id, conn=own)
    changed = conn.execute(
        """UPDATE oauth_grants
           SET revoked_at = now(), revoked_reason = ?, revoked_by = ?
           WHERE id = ? AND revoked_at IS NULL
           RETURNING id""",
        (str(reason)[:64], actor_id, grant_id),
    ).fetchone()
    conn.execute(
        "UPDATE oauth_tokens SET revoked_at = now() WHERE grant_id = ? AND revoked_at IS NULL",
        (grant_id,),
    )
    return changed is not None


# ─── POST /oauth/token ───────────────────────────────────────────────────────

class _TokenRefusal(Exception):
    """An OAuth error answer from inside a token transaction."""

    def __init__(self, error, description, status=400):
        super().__init__(error)
        self.error = error
        self.description = description
        self.status = status


def _token_error(status, error, description, headers=None):
    merged = dict(NO_STORE_HEADERS)
    merged.update(headers or {})
    return _oauth_error(status, error, description, merged)


def _form_single(form, name):
    """One form value; a repeated parameter is a malformed request (RFC 6749 §3.2)."""
    values = form.getlist(name)
    if len(values) > 1:
        raise _TokenRefusal("invalid_request", f"Parameter given more than once: {name}")
    return values[0] if values else None


# When the grant itself ends; no token of it may outlive that.
_GRANT_END_SQL = """(SELECT created_at + make_interval(days => CAST(? AS integer))
                     FROM oauth_grants WHERE id = ?)"""


def _issue_token_pair(conn, grant_id):
    """A fresh access + refresh token for a grant; only their hashes are stored.

    Neither expires later than the grant's absolute lifetime.
    """
    access = tokens.new_token(tokens.ACCESS_PREFIX)
    refresh = tokens.new_token(tokens.REFRESH_PREFIX)
    conn.execute(
        f"""INSERT INTO oauth_tokens (token_hash, grant_id, kind, expires_at)
            VALUES (?, ?, 'access',
                    LEAST(now() + make_interval(secs => CAST(? AS integer)), {_GRANT_END_SQL}))""",
        (tokens.token_hash(access), grant_id, ACCESS_TTL_SECONDS,
         tokens.OAUTH_GRANT_MAX_DAYS, grant_id),
    )
    conn.execute(
        f"""INSERT INTO oauth_tokens (token_hash, grant_id, kind, expires_at)
            VALUES (?, ?, 'refresh',
                    LEAST(now() + make_interval(days => CAST(? AS integer)), {_GRANT_END_SQL}))""",
        (tokens.token_hash(refresh), grant_id, REFRESH_TTL_DAYS,
         tokens.OAUTH_GRANT_MAX_DAYS, grant_id),
    )
    return access, refresh


def _token_success(access, refresh, scope):
    return _json(200, {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL_SECONDS,
        "refresh_token": refresh,
        "scope": scope or SUPPORTED_SCOPE,
    }, headers=dict(NO_STORE_HEADERS))


def _check_resource(requested, expected, base):
    """An optional `resource` must name the same audience as the grant (RFC 8707)."""
    if requested is None:
        return
    if redirects.canonical_resource(requested, _resources(base)) != expected:
        raise _TokenRefusal("invalid_target", "The resource does not match this grant")


_CODE_SQL = """
    SELECT c.code_hash, c.grant_id, c.client_id, c.redirect_uri, c.code_challenge,
           c.resource, c.scope, c.used_at, (c.expires_at <= now()) AS expired,
           g.revoked_at AS grant_revoked_at, u.active AS user_active,
           (g.created_at + make_interval(days => CAST(? AS integer)) <= now()) AS grant_expired
    FROM oauth_codes c
    JOIN oauth_grants g ON g.id = c.grant_id
    JOIN users u ON u.id = g.user_id
    WHERE c.code_hash = ?
    FOR UPDATE OF c
"""


def _exchange_code(client_id, form, base):
    code = _form_single(form, "code")
    redirect_uri = _form_single(form, "redirect_uri")
    verifier = _form_single(form, "code_verifier")
    resource = _form_single(form, "resource")
    if not code or not redirect_uri or not verifier:
        raise _TokenRefusal("invalid_request",
                            "code, redirect_uri and code_verifier are required")

    conn = get_conn()
    try:
        conn.begin()
        row = conn.execute(_CODE_SQL, (tokens.OAUTH_GRANT_MAX_DAYS,
                                       tokens.token_hash(code))).fetchone()
        if row is None:
            conn.commit()
            raise _TokenRefusal("invalid_grant", "The authorization code is invalid")
        if row["used_at"] is not None:
            # A code redeemed twice means someone else may hold it: the grant
            # it created is no longer trustworthy (RFC 9700 §4.2.4).
            revoke_grant(row["grant_id"], "code_reuse", conn=conn)
            conn.commit()
            logger.warning("oauth_code_reuse grant_id=%s", row["grant_id"])
            raise _TokenRefusal("invalid_grant", "The authorization code was already used")

        # Spent before any other check: one code is never guessed at twice.
        conn.execute("UPDATE oauth_codes SET used_at = now() WHERE code_hash = ?",
                     (row["code_hash"],))
        try:
            if row["expired"]:
                raise _TokenRefusal("invalid_grant", "The authorization code has expired")
            if row["client_id"] != client_id:
                raise _TokenRefusal("invalid_grant",
                                    "The authorization code was issued to another client")
            if row["redirect_uri"] != redirect_uri:
                raise _TokenRefusal("invalid_grant", "redirect_uri does not match")
            if not tokens.pkce_s256_matches(verifier, row["code_challenge"]):
                raise _TokenRefusal("invalid_grant", "PKCE verification failed")
            _check_resource(resource, row["resource"], base)
            if (row["grant_revoked_at"] is not None or not row["user_active"]
                    or row["grant_expired"]):
                raise _TokenRefusal("invalid_grant", "The grant is no longer valid")
        except _TokenRefusal:
            conn.commit()
            raise

        access, refresh = _issue_token_pair(conn, row["grant_id"])
        conn.execute("UPDATE oauth_clients SET last_used_at = now() WHERE client_id = ?",
                     (client_id,))
        conn.commit()
    finally:
        conn.close()
    return _token_success(access, refresh, row["scope"])


_REFRESH_SQL = """
    SELECT t.token_hash, t.grant_id, t.rotated_at, t.revoked_at,
           (t.expires_at <= now()) AS expired,
           extract(epoch FROM (now() - t.rotated_at)) AS rotated_seconds_ago,
           g.client_id, g.scope, g.resource, g.revoked_at AS grant_revoked_at,
           u.active AS user_active,
           (g.created_at + make_interval(days => CAST(? AS integer)) <= now()) AS grant_expired
    FROM oauth_tokens t
    JOIN oauth_grants g ON g.id = t.grant_id
    JOIN users u ON u.id = g.user_id
    WHERE t.token_hash = ? AND t.kind = 'refresh'
    FOR UPDATE OF t
"""


def _refresh(client_id, form, base):
    raw = _form_single(form, "refresh_token")
    scope = _form_single(form, "scope")
    resource = _form_single(form, "resource")
    if not raw:
        raise _TokenRefusal("invalid_request", "refresh_token is required")
    if scope is not None and _normalize_scope(scope) != SUPPORTED_SCOPE:
        raise _TokenRefusal("invalid_scope", "Only the read scope is available")

    conn = get_conn()
    try:
        conn.begin()
        row = conn.execute(_REFRESH_SQL, (tokens.OAUTH_GRANT_MAX_DAYS,
                                          tokens.token_hash(raw))).fetchone()
        try:
            if row is None or row["client_id"] != client_id:
                raise _TokenRefusal("invalid_grant", "The refresh token is invalid")
            if row["rotated_at"] is not None:
                # Presented again after it was exchanged. Within the grace
                # window this is Claude's proactive and reactive refresh racing
                # each other; later it is a replay, and the family dies.
                age = float(row["rotated_seconds_ago"] or 0)
                if age > REFRESH_REUSE_GRACE_SECONDS:
                    revoked = revoke_grant(row["grant_id"], "refresh_reuse", conn=conn)
                    logger.warning("oauth_refresh_reuse grant_id=%s revoked=%s",
                                   row["grant_id"], revoked)
                else:
                    logger.info("oauth_refresh_race grant_id=%s", row["grant_id"])
                raise _TokenRefusal("invalid_grant", "The refresh token was already used")
            if row["grant_expired"] and row["grant_revoked_at"] is None:
                # The connection reached its absolute lifetime: end it for good,
                # so every list shows it as closed.
                revoke_grant(row["grant_id"], "expired", conn=conn)
                raise _TokenRefusal("invalid_grant",
                                    "The connection has expired; connect again")
            if (row["revoked_at"] is not None or row["expired"]
                    or row["grant_revoked_at"] is not None or not row["user_active"]):
                raise _TokenRefusal("invalid_grant", "The refresh token is no longer valid")
            _check_resource(resource, row["resource"], base)
        except _TokenRefusal:
            conn.commit()
            raise

        conn.execute("UPDATE oauth_tokens SET rotated_at = now() WHERE token_hash = ?",
                     (row["token_hash"],))
        # The new pair goes out in the same response that retires the old
        # refresh token, so a client never holds nothing.
        access, refresh = _issue_token_pair(conn, row["grant_id"])
        conn.execute("UPDATE oauth_clients SET last_used_at = now() WHERE client_id = ?",
                     (client_id,))
        conn.commit()
    finally:
        conn.close()
    return _token_success(access, refresh, row["scope"])


_GRANT_HANDLERS = {
    "authorization_code": _exchange_code,
    "refresh_token": _refresh,
}


def handle_token(base=None) -> Response:
    """POST /oauth/token for public clients: PKCE is the only proof.

    The form body and the URL are never logged: they carry codes, verifiers
    and refresh tokens.
    """
    base = base or _issuer()
    if request.mimetype != FORM_MIMETYPE:
        return _token_error(400, "invalid_request",
                            "Content-Type must be application/x-www-form-urlencoded")
    form = request.form
    try:
        client_id = _form_single(form, "client_id")
        if _load_client(client_id) is None:
            return _token_error(401, "invalid_client", "Unknown client")

        allowed, retry_after = limits.consume(
            "token:client:" + client_id, TOKEN_RATE_PER_MINUTE, TOKEN_WINDOW_SECONDS)
        if not allowed:
            return _token_error(429, "too_many_requests", "Too many token requests",
                                headers={"Retry-After": str(retry_after)})

        grant_type = _form_single(form, "grant_type")
        if not grant_type:
            return _token_error(400, "invalid_request", "grant_type is required")
        handler = _GRANT_HANDLERS.get(grant_type)
        if handler is None:
            return _token_error(400, "unsupported_grant_type",
                                "Only authorization_code and refresh_token are supported")
        return handler(client_id, form, base)
    except _TokenRefusal as refusal:
        return _token_error(refusal.status, refusal.error, refusal.description)


# ─── POST /oauth/revoke ──────────────────────────────────────────────────────

def handle_revoke() -> Response:
    """RFC 7009 revocation: always 200 `{}`, so it is never an oracle.

    Possession of a token is the authority to revoke it (public clients have
    no secret to check). A refresh token takes its whole grant with it; an
    access token only itself. Anything unknown, malformed or not form-encoded
    changes nothing and still gets 200. The body is never logged.
    """
    raw = None
    if request.mimetype == FORM_MIMETYPE:
        values = request.form.getlist("token")
        raw = values[0] if len(values) == 1 else None
    if isinstance(raw, str) and raw.startswith((tokens.ACCESS_PREFIX, tokens.REFRESH_PREFIX)):
        with get_conn() as conn:
            row = conn.execute(
                "SELECT token_hash, grant_id, kind FROM oauth_tokens WHERE token_hash = ?",
                (tokens.token_hash(raw),),
            ).fetchone()
            if row is not None and row["kind"] == "refresh":
                revoke_grant(row["grant_id"], "client_revoked", conn=conn)
            elif row is not None:
                conn.execute(
                    """UPDATE oauth_tokens SET revoked_at = now()
                       WHERE token_hash = ? AND revoked_at IS NULL""",
                    (row["token_hash"],),
                )
    return _json(200, {}, headers=dict(NO_STORE_HEADERS))


def _no_store(view):
    """Token-endpoint answers, error and 500 included, are never cached."""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        response = view(*args, **kwargs)
        response.headers.update(NO_STORE_HEADERS)
        return response
    return wrapper


# ─── Flask wiring ────────────────────────────────────────────────────────────

def _protocol_errors(view, *, page=False):
    """Never the app's Polish 500 JSON on /oauth/* or /.well-known/*.

    An unexpected exception becomes `{"error": "server_error"}` (or, for the
    browser-facing authorize endpoint, the static error page -- never a
    redirect), and the log line names only the endpoint, the exception type
    and an opaque request id: the URL may carry a state or a code.
    """
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except RequestEntityTooLarge:
            return _oauth_error(413, "invalid_client_metadata", "Request body too large")
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 -- the boundary of the surface
            request_id = uuid.uuid4().hex
            logger.error("oauth_error request_id=%s endpoint=%s exception=%s",
                         request_id, request.endpoint, type(exc).__name__)
            if page:
                return oauth_error_page("server_error", status=500)
            return _json(500, {"error": "server_error",
                               "error_description": "Unexpected server error",
                               "request_id": request_id})
    return wrapper


def register(app, config):
    """Attach /.well-known/* and /oauth/* to the Flask app.

    Every URL in every document is built from `config.base_url` -- never from
    the request's Host header, which an attacker controls.
    """
    base = config.base_url

    def mcp_resource_metadata():
        return _metadata_response(lambda: protected_resource_metadata(base, "mcp"))

    def api_v1_resource_metadata():
        return _metadata_response(lambda: protected_resource_metadata(base, "api_v1"))

    def as_metadata():
        return _metadata_response(lambda: authorization_server_metadata(base))

    def openid_configuration():
        # Not an OpenID provider; clients try RFC 8414 first.
        return _json(404, {"error": "not_found"})

    def register_client():
        return _register_view()

    def authorize():
        return _authorize_view(base)

    # The back channel authenticates a client and a code or refresh token, not
    # a person, and has to read whose grant it is: system database context,
    # here and nowhere else in this module.
    def token():
        with system_context("oauth token endpoint"):
            return handle_token(base)

    def revoke():
        with system_context("oauth revocation endpoint"):
            return handle_revoke()

    rules = (
        # The root form also describes /mcp: it is where Claude falls back to.
        ("/.well-known/oauth-protected-resource", "oauth_prm_root",
         mcp_resource_metadata, ["GET"], False),
        ("/.well-known/oauth-protected-resource/mcp", "oauth_prm_mcp",
         mcp_resource_metadata, ["GET"], False),
        ("/.well-known/oauth-protected-resource/api/v1", "oauth_prm_api_v1",
         api_v1_resource_metadata, ["GET"], False),
        ("/.well-known/oauth-authorization-server", "oauth_as_metadata",
         as_metadata, ["GET"], False),
        ("/.well-known/openid-configuration", "oauth_openid_configuration",
         openid_configuration, ["GET"], False),
        ("/oauth/register", "oauth_register", register_client, ["POST"], False),
        ("/oauth/authorize", "oauth_authorize", authorize, ["GET"], True),
    )
    for path, endpoint, view, methods, page in rules:
        app.add_url_rule(path, endpoint=endpoint, methods=methods,
                         view_func=_protocol_errors(view, page=page))

    # Token responses carry Pragma: no-cache as well, even when they fail.
    app.add_url_rule("/oauth/token", endpoint="oauth_token", methods=["POST"],
                     view_func=_no_store(_protocol_errors(token)))
    app.add_url_rule("/oauth/revoke", endpoint="oauth_revoke", methods=["POST"],
                     view_func=_no_store(_protocol_errors(revoke)))
