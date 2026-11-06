"""Test-only helpers for the API gateway.

Plain functions, not fixtures, so a test can call them in whatever order its
scenario needs. Everything here writes to the per-test PostgreSQL database
that `api/tests/conftest.py::test_db_path` provides -- never the working DB.
`set_integration_settings` writes the settings rows directly.
"""
import base64
import hashlib
import json
import os
import uuid
from urllib.parse import urlencode

import bcrypt

from api import leads
from api.db import get_conn
from api.gateway import keys, settings, tokens

BASE = "http://localhost:8000"


def _upsert(conn, key, value):
    conn.execute(
        """INSERT INTO settings (key, value) VALUES (?, ?)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
        (key, value),
    )


def set_integration_settings(enabled=True, partners=False, field_groups=(),
                             rate_per_minute=None, rate_per_day=None):
    with get_conn() as conn:
        _upsert(conn, settings.KEY_ENABLED, "1" if enabled else "0")
        _upsert(conn, settings.KEY_PARTNERS, "1" if partners else "0")
        _upsert(conn, settings.KEY_FIELD_GROUPS, json.dumps(list(field_groups)))
        if rate_per_minute is not None:
            _upsert(conn, settings.KEY_RATE_MINUTE, str(rate_per_minute))
        if rate_per_day is not None:
            _upsert(conn, settings.KEY_RATE_DAY, str(rate_per_day))


def create_user(role, email, company="Firma Testowa", active=1):
    """An extra account of any role (the shared conftest has no observer)."""
    password = bcrypt.hashpw(b"GatewayPass123!", bcrypt.gensalt()).decode()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO users (email, password, name, role, company, level, active)
               VALUES (?, ?, ?, ?, ?, 'bronze', ?)""",
            (email, password, f"Test {role}", role, company, active),
        )
    conn = get_conn()
    try:
        return dict(conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone())
    finally:
        conn.close()


def make_key(user, name="test", days=90) -> str:
    """Create a key through the real path and return the raw secret."""
    return keys.create_key(user, name, days)["key"]


def client():
    import server
    return server.create_app().test_client()


def bearer(raw) -> dict:
    return {"Authorization": f"Bearer {raw}"}


def deactivate(user):
    with get_conn() as conn:
        conn.execute("UPDATE users SET active = 0 WHERE id = ?", (user["id"],))


def insert_oauth_access(user, resource, *, expires_in_seconds=3600, revoked=False,
                        client_name="Test client", kind="access") -> str:
    """Seed a client, a grant and one token for it; return the raw token.

    Test-only stand-in for the real issuance path (/oauth/token):
    it writes the same rows, fingerprints included, through `tokens`.
    `revoked` revokes the GRANT, which must kill every token it issued.
    """
    prefix = tokens.ACCESS_PREFIX if kind == "access" else tokens.REFRESH_PREFIX
    raw = tokens.new_token(prefix)
    client_id = tokens.new_client_id()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO oauth_clients (client_id, client_name, redirect_uris)
               VALUES (?, ?, ?)""",
            (client_id, client_name, json.dumps(["http://localhost:33418/callback"])),
        )
        grant_id = conn.execute(
            """INSERT INTO oauth_grants (user_id, client_id, scope, resource, revoked_at)
               VALUES (?, ?, 'read', ?, CASE WHEN ? THEN now() ELSE NULL END)""",
            (user["id"], client_id, resource, bool(revoked)),
        ).lastrowid
        conn.execute(
            """INSERT INTO oauth_tokens (token_hash, grant_id, kind, created_at, expires_at)
               VALUES (?, ?, ?, now() - INTERVAL '2 hours',
                       now() + make_interval(secs => CAST(? AS integer)))""",
            (tokens.token_hash(raw), grant_id, kind, expires_in_seconds),
        )
    return raw


def grant_last_used(raw):
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT g.last_used_at FROM oauth_tokens t
               JOIN oauth_grants g ON g.id = t.grant_id WHERE t.token_hash = ?""",
            (tokens.token_hash(raw),),
        ).fetchone()
        return row["last_used_at"]
    finally:
        conn.close()


def catalogue_product_id():
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT id FROM products WHERE code = 'TEST-ERP'").fetchone()["id"]
    finally:
        conn.close()


def make_lead(partner, **overrides):
    """A real lead for `partner`, created by the domain's own create_lead."""
    suffix = uuid.uuid4().hex[:8]
    body = {
        "client_name": "Jan Kontaktowy",
        "client_email": f"kontakt-{suffix}@klient.pl",
        "client_phone": "+48 600 000 000",
        "company": f"Klient {suffix}",
        "value": 10000,
        "notes": "Notatka wewnętrzna",
        "sector": "",
        "nip": "",
        "website": "",
        "product": "Wdrożenie ERP",
        "product_id": catalogue_product_id(),
        "quantity": 1,
    }
    body.update(overrides)
    return leads.create_lead(partner, body)


# ─── OAuth front channel ────────────────────────────────────────

CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"


def register_client(client, redirect_uris=(CLAUDE_CALLBACK,), name="Claude") -> dict:
    """Register a public client through the real POST /oauth/register."""
    response = client.post("/oauth/register",
                           json={"client_name": name, "redirect_uris": list(redirect_uris)})
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()


def pkce_pair():
    """(verifier, S256 challenge), computed here independently of api.gateway.tokens."""
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def session_token(call_api, user) -> str:
    """Log `user` in through the real /api/auth/login and return the session token."""
    status, body = call_api("POST", "/api/auth/login", body={
        "email": user["email"], "password": user["plain_password"], "role": user["role"]})
    assert status == 200, body
    return body["token"]


def authorize(client, client_id, redirect_uri, challenge, state="xyz", resource=None,
              scope=None, **extra):
    """GET /oauth/authorize with the usual parameters; None values are left out."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": resource,
        "scope": scope,
    }
    params.update(extra)
    query = urlencode({k: v for k, v in params.items() if v is not None})
    return client.get("/oauth/authorize?" + query)


def request_id_from(response) -> str:
    """The pending request id from authorize's redirect to the consent page."""
    location = response.headers["Location"]
    assert location.startswith("/pages/oauth/consent.html?req="), location
    return location.split("req=", 1)[1]


def approve(call_api, token, request_id, approve=True, headers=None):
    """POST the consent decision through the legacy router (Origin is set by call_api)."""
    return call_api("POST", f"/api/oauth/requests/{request_id}/decision",
                    body={"approve": approve}, token=token, headers=headers)


def count_rows(table) -> int:
    conn = get_conn()
    try:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    finally:
        conn.close()


# ─── MCP endpoint ───────────────────────────────────────────────

def mcp_call(client, raw_token, method, params=None, *, msg_id=1, version="2025-11-25",
             origin=None):
    """POST one JSON-RPC request to /mcp the way Claude does.

    `raw_token=None` sends no Authorization header, `version=None` no
    MCP-Protocol-Version header, `msg_id=None` makes it a notification.
    """
    message = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        message["id"] = msg_id
    if params is not None:
        message["params"] = params
    headers = {"Accept": "application/json, text/event-stream"}
    if version is not None:
        headers["MCP-Protocol-Version"] = version
    if origin is not None:
        headers["Origin"] = origin
    if raw_token is not None:
        headers.update(bearer(raw_token))
    return client.post("/mcp", data=json.dumps(message), headers=headers,
                       content_type="application/json")


# ─── OAuth token endpoint ───────────────────────────────────────

def token_request(client, form, content_type="application/x-www-form-urlencoded"):
    """POST /oauth/token with a form body (None values are left out)."""
    data = urlencode({k: v for k, v in form.items() if v is not None})
    return client.post("/oauth/token", data=data, content_type=content_type)


def exchange_code(client, client_id, code, verifier, redirect_uri, resource=None):
    """grant_type=authorization_code, exactly as Claude sends it."""
    return token_request(client, {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
        "resource": resource,
    })


def refresh(client, client_id, refresh_token):
    """grant_type=refresh_token."""
    return token_request(client, {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    })


def code_from(redirect_to) -> str:
    """The `code` query parameter of the consent decision's redirect_to."""
    from urllib.parse import parse_qs, urlsplit
    return parse_qs(urlsplit(redirect_to).query)["code"][0]


def grant_id_of(raw_token) -> int:
    conn = get_conn()
    try:
        return conn.execute("SELECT grant_id FROM oauth_tokens WHERE token_hash = ?",
                            (tokens.token_hash(raw_token),)).fetchone()["grant_id"]
    finally:
        conn.close()


def consent_code(client, call_api, user, *, resource=None, redirect_uri=CLAUDE_CALLBACK,
                 registered=None):
    """register -> authorize -> login -> approve; return (client_id, verifier, code).

    Stops just before the token endpoint, for tests that redeem the code
    themselves (wrong verifier, second redemption, ...).
    """
    set_integration_settings(enabled=True, partners=True)
    reg = register_client(client, redirect_uris=registered or (redirect_uri,))
    verifier, challenge = pkce_pair()
    response = authorize(client, reg["client_id"], redirect_uri, challenge, resource=resource)
    assert response.status_code == 302, response.get_data(as_text=True)
    request_id = request_id_from(response)
    session = session_token(call_api, user)
    status, body = approve(call_api, session, request_id,
                           headers={"Origin": BASE})
    assert status == 200, body
    return reg["client_id"], verifier, code_from(body["redirect_to"])


def oauth_dance(client, call_api, user, *, resource=None) -> dict:
    """The whole "Connect" flow through the real endpoints; return the tokens and ids."""
    client_id, verifier, code = consent_code(client, call_api, user, resource=resource)
    response = exchange_code(client, client_id, code, verifier, CLAUDE_CALLBACK)
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    return {
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "client_id": client_id,
        "grant_id": grant_id_of(body["access_token"]),
        "response": response,
        "verifier": verifier,
        "code": code,
    }
