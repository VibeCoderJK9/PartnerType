"""/api/v1 -- the read-only REST surface of the gateway.

One Flask rule, `/api/v1/<path:path>`, registered explicitly so the legacy
`/api/<path:path>` router (session cookie, regex routes) can never swallow it. The route table `V1_ROUTES` is the single list of what exists;
the OpenAPI document (api/gateway/openapi.py) is generated from it and served,
without credentials, at GET /api/v1/openapi.json.

Authentication is `Authorization: Bearer` only, through
`auth.authenticate_bearer`. The session cookie is never read here, which is
also why /api/v1 needs no CSRF defence: a browser cannot attach a bearer
header on its own. Every call runs through `pipeline.guarded`, the seam where
limits and the call log live; the operation it logs is the route template
(`/leads/{id}`), never the request path or its query string.

Errors are protocol-shaped JSON (`{"error": code, "error_description": …}`)
and the 500 path logs only the exception type and a request id -- never the
URL, which on this surface may carry nothing secret today but must not
become a habit (server.py `unexpected` follows the same rule).
"""
import json
import logging
import re
import uuid
from collections import namedtuple

from flask import Response, request

from api import agent_tools
from api.gateway import auth, pipeline

logger = logging.getLogger(__name__)

SURFACE = "api_v1"

# Every route resolves to ONE tool of agent_tools.GATEWAY_TOOL_REGISTRY:
#   operation  the tool name -- the registry decides who may call it and what
#              arguments it takes, so /api/v1 and MCP share one role matrix and
#              one schema.
#   handler    None for tool routes (the view runs execute_gateway_tool); a
#              callable `(principal, args, path_params) -> (status, payload,
#              records)` only for a route that is not a tool.
#   params     path placeholder -> tool argument name, e.g. {"id": "lead_id"}.
# Query parameters are the tool's schema properties minus the path-mapped
# arguments; any other parameter, or one given twice, is a 400.
V1Route = namedtuple("V1Route", "method path operation handler summary params",
                     defaults=(None,))

V1_ROUTES = [
    V1Route("GET", "/leads", "list_leads", None,
            "List leads visible to the caller"),
    V1Route("GET", "/leads/{id}", "get_lead_details", None,
            "One lead visible to the caller", {"id": "lead_id"}),
    V1Route("GET", "/stats", "get_lead_stats", None,
            "Lead counts in the caller's scope, including deals won today"),
    V1Route("GET", "/products", "list_products", None,
            "Products in the programme catalogue"),
    V1Route("GET", "/partners", "list_partners", None,
            "Partners of the programme (staff only)"),
    V1Route("GET", "/reminders", "list_reminders", None,
            "The caller's automatic programme reminders (staff only)"),
]


class _BadRequest(ValueError):
    pass


def _template_regex(template):
    parts = re.split(r"(\{[a-z_]+\})", template)
    pattern = "".join(
        f"(?P<{p[1:-1]}>[^/]+)" if p.startswith("{") else re.escape(p) for p in parts)
    return re.compile("^" + pattern + "$")


_COMPILED = [(route, _template_regex(route.path)) for route in V1_ROUTES]


def _match(method, path):
    """(route, path params) for a request, or (None, 'not_found'|'method')."""
    found_path = False
    for route, regex in _COMPILED:
        m = regex.match(path)
        if not m:
            continue
        found_path = True
        if route.method == method:
            return route, m.groupdict()
    return None, ("method" if found_path else "not_found")


def _query_names(route):
    """Query parameters a tool route accepts: its schema minus path arguments."""
    spec = agent_tools.GATEWAY_TOOL_REGISTRY.get(route.operation)
    if spec is None:
        return set()
    path_args = set((route.params or {}).values())
    return set(spec["input_schema"].get("properties", {})) - path_args


def _parse_args(route, multidict, path_params):
    """Tool arguments from the query string and the path, as raw text.

    Types are coerced and checked by the registry schema
    (execute_gateway_tool(..., from_query=True)), the same check MCP uses.
    """
    allowed = _query_names(route)
    args = {}
    for key in multidict.keys():
        values = multidict.getlist(key)
        if key not in allowed:
            raise _BadRequest(f"Unknown query parameter: {key}")
        if len(values) != 1:
            raise _BadRequest(f"Query parameter given more than once: {key}")
        args[key] = values[0]
    for placeholder, argument in (route.params or {}).items():
        args[argument] = path_params[placeholder]
    return args


_TOOL_ERROR_STATUS = {
    "invalid_arguments": (400, "invalid_request"),
    "not_found": (404, "not_found"),
}


def _tool_error(exc):
    if exc.forbidden:
        return _error(403, "forbidden_tool", "This operation is not available for your role")
    if exc.code == "unknown_tool":
        return _error(404, "not_found", "Not found")
    status, error = _TOOL_ERROR_STATUS.get(exc.code, (400, "invalid_request"))
    return _error(status, error, exc.message)


def _json(status, payload, headers=None):
    return Response(json.dumps(payload, ensure_ascii=False, default=str),
                    status=status, headers=headers or {},
                    content_type="application/json; charset=utf-8")


def _error(status, error, description, headers=None):
    return _json(status, {"error": error, "error_description": description}, headers)


def register(app, config):
    # Imported here: openapi reads V1_ROUTES from this module.
    from api.gateway import openapi

    base = config.base_url
    resource = auth.resource_url(base, SURFACE)
    metadata_url = auth.resource_metadata_url(base, SURFACE)

    def api_v1_openapi():
        # Public and outside the seam: route shapes only, built without the
        # database, so there is no connection to limit or log.
        return Response(openapi.render(base), status=200,
                        content_type="application/json; charset=utf-8")

    def api_v1(path):
        route, params = _match(request.method, "/" + path)
        if route is None:
            if params == "method":
                return _error(405, "method_not_allowed", "Method not allowed")
            return _error(404, "not_found", "Not found")
        try:
            principal = auth.authenticate_bearer(
                request.headers, resource=resource, resource_metadata_url=metadata_url)

            def run():
                # Inside the seam: a bad query string is still a call the
                # connection made, so it is limited and logged like any other.
                args = _parse_args(route, request.args, params)
                if route.handler is not None:
                    return route.handler(principal, args, params)
                out = agent_tools.execute_gateway_tool(
                    principal.user, route.operation, args,
                    scopes=principal.scopes, from_query=True)
                return 200, out["result"], out["records"]

            status, payload, _records = pipeline.guarded(principal, SURFACE, route.path, run)
            return _json(status, payload)
        except agent_tools.GatewayToolError as exc:
            return _tool_error(exc)
        except auth.GatewayError as exc:
            # A 403 after the token was identified is logged against the
            # connection; the operation is the route template, never the URL.
            pipeline.record_denied(exc, SURFACE, route.path)
            return _error(exc.status, exc.error, exc.description, exc.headers)
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except ValueError as exc:
            return _error(400, "invalid_request", str(exc))
        except Exception as exc:  # noqa: BLE001 -- the boundary of the surface
            request_id = uuid.uuid4().hex
            logger.error("gateway_error request_id=%s surface=%s exception=%s",
                         request_id, SURFACE, type(exc).__name__)
            return _json(500, {"error": "server_error", "request_id": request_id})

    app.add_url_rule("/api/v1/openapi.json", endpoint="gateway_api_v1_openapi",
                     view_func=api_v1_openapi, methods=["GET"])
    app.add_url_rule("/api/v1/<path:path>", endpoint="gateway_api_v1", view_func=api_v1,
                     methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
