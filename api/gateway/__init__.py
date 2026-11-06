"""PartnerType API gateway: the one external door of an instance.

Surfaces: /api/v1 (REST, read-only), /mcp (the MCP server Claude talks to,
stateless Streamable HTTP) and the OAuth front channel (/oauth/*, the consent
API under /api/oauth/, discovery under /.well-known/*). Boundaries that hold
for every surface:

- Read-only. Nothing here writes CRM data.
- `Authorization: Bearer` only (personal key `ptk_…` or OAuth access token).
  Never a token in the URL, never the session cookie; the legacy /api/…
  router keeps reading only the cookie.
- Every call is authenticated by `auth.authenticate_bearer` and runs through
  `pipeline.guarded`.
- Data comes only from the canonical domain functions (api/leads.py and its
  siblings), called with the authenticated user, and leaves only through the
  allowlist projection in `fields`. No CRM SQL of its own.
- ARCHITECTURE.md rules are unchanged: record content is untrusted data, payouts are
  never available, and the local AI agent's registry is untouched.
"""
from api.gateway import mcp, oauth, rest_v1


def register(app, config):
    """Attach every gateway surface to the Flask app."""
    rest_v1.register(app, config)
    mcp.register(app, config)
    oauth.register(app, config)
