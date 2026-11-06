"""
Route access rules: every HTTP route declares who may call it, and the
dispatcher enforces that rule before the handler runs.

These tests drive api.router.dispatch with every handler replaced by a
recorder, so they prove the refusal happens in the dispatcher itself: a role
the rule excludes gets 403 (or 401 when signed out) and the handler is never
called, while a role the rule admits reaches the handler.
"""
import json
import re
from unittest.mock import MagicMock

import pytest

from api import authz, router


ROLES = ("admin", "manager", "observer", "partner")

# Who each rule admits, written out by hand rather than derived from the
# predicates, so a change to a predicate's role set fails here and has to be
# made on purpose.
RULE_ROLES = {
    "authenticated": {"admin", "manager", "observer", "partner"},
    "can_read_leads": {"admin", "manager", "observer", "partner"},
    "is_staff": {"admin", "manager"},
    "can_configure": {"admin", "manager"},
    "is_staff_or_partner": {"admin", "manager", "partner"},
    "can_use_assistant": {"admin", "partner"},
    "is_partner": {"partner"},
    "can_configure_lead_fields": {"admin"},
    "can_manage_users": {"admin"},
    "can_manage_integrations": {"admin"},
    "can_view_audit": {"admin"},
    "can_handle_data_requests": {"admin"},
    "can_pay_commission": {"admin"},
    "can_reopen_lead": {"admin"},
    "can_assign_lead": {"admin"},
}

# Sample values for the named groups the route patterns use, keyed by the
# group's own sub-pattern.
_GROUP_SAMPLES = {
    r"\d+": "7",
    r"[^/]+": "LD-001",
    r"[A-Z0-9-]+": "LD-001",
    r"[A-Za-z0-9_-]{16,256}": "t" * 32,
    r"[A-Za-z0-9_-]{32,64}": "r" * 40,
    r"[a-z][a-z0-9_]{2,47}": "abc",
}
_GROUP_RE = re.compile(r"\(\?P<\w+>(.+?)\)(?=/|\$)")


def _sample_path(pattern):
    def fill(match):
        sub = match.group(1)
        assert sub in _GROUP_SAMPLES, f"no sample value for group pattern {sub!r} in {pattern}"
        return _GROUP_SAMPLES[sub]
    path = _GROUP_RE.sub(fill, pattern)
    assert path.startswith("^") and path.endswith("$"), pattern
    return path[1:-1]


def _route_id(route):
    return f"{route[0]} {route[1]}"


def _first_route_for(method, path):
    for route in router.ROUTES:
        if route[0] == method and re.match(route[1], path):
            return route
    return None


def _handler_names():
    """Names in api.router bound to request handlers from the api package."""
    return sorted(
        name for name, value in vars(router).items()
        if callable(value)
        and getattr(value, "__module__", "").startswith("api.")
        and getattr(value, "__name__", "").startswith("handle")
    )


@pytest.fixture
def recorded(monkeypatch):
    """Replace every handler with a recorder and return the list of calls."""
    calls = []
    for name in _handler_names():
        def recorder(*args, _name=name, **kwargs):
            calls.append(_name)
            return 200, {"ok": True}
        monkeypatch.setattr(router, name, recorder)
    monkeypatch.setattr(router, "mfa_is_required_for", lambda role: False)
    return calls


def _sign_in_as(monkeypatch, role):
    user = None if role is None else {
        "id": 4242, "role": role, "name": "Route Test", "email": "route@test.pl",
    }
    monkeypatch.setattr(router, "get_session_user", lambda token: user)


def _dispatch(method, path, signed_in=True):
    handler = MagicMock()
    raw = b"{}" if method in ("POST", "PUT", "PATCH") else b""
    headers = {"Content-Length": str(len(raw)), "Origin": "http://localhost:8000", "Accept-Language": "pl"}
    if signed_in:
        headers["Cookie"] = "ph_test_test_session=route-test-token"
    handler.headers = headers
    handler.rfile.read.return_value = raw
    handler.__dict__["allowed_origins"] = router.DEFAULT_ALLOWED_ORIGINS
    router.dispatch(handler, method, path)
    status = handler.send_response.call_args_list[0][0][0]
    body = json.loads(handler.wfile.write.call_args_list[0][0][0].decode("utf-8"))
    return status, body


PROTECTED = [r for r in router.ROUTES if r[2] != router.PUBLIC]
PUBLIC_ROUTES = [r for r in router.ROUTES if r[2] == router.PUBLIC]


# ─── The table itself ───────────────────────────────────────────────────────

def test_every_route_has_a_valid_access_rule():
    valid = {router.PUBLIC, router.AUTHENTICATED, *authz.ROUTE_PREDICATES}
    bad = [_route_id(r) for r in router.ROUTES if len(r) != 4 or r[2] not in valid]
    assert not bad, f"routes without a valid access rule: {bad}"
    assert set(router.ACCESS_CHECKS) == {r[3] for r in router.ROUTES}


def test_every_route_predicate_has_an_expected_role_set():
    """A new predicate offered to the route table must be added to RULE_ROLES,
    so the matrix below knows which roles it admits."""
    assert set(authz.ROUTE_PREDICATES) | {"authenticated"} == set(RULE_ROLES)


def test_predicates_admit_exactly_the_expected_roles():
    for name, predicate in authz.ROUTE_PREDICATES.items():
        admitted = {role for role in ROLES if predicate({"id": 1, "role": role})}
        assert admitted == RULE_ROLES[name], name
        assert not predicate(None), name
        assert not predicate({"id": 1, "role": "unknown"}), name


@pytest.mark.parametrize("table, message", [
    ([("GET", r"^/api/x$", "x")], "must be"),
    ([("GET", r"^/api/x$", None, "x")], "no access rule"),
    ([("GET", r"^/api/x$", "", "x")], "no access rule"),
    ([("GET", r"^/api/x$", True, "x")], "no access rule"),
    ([("GET", r"^/api/x$", "staff_maybe", "x")], "unknown access rule"),
    ([("GET", r"^/api/x$", "public", "x"), ("POST", r"^/api/x$", "public", "x")], "more than once"),
])
def test_a_malformed_route_table_is_refused_at_import(table, message):
    with pytest.raises(router.RouteTableError, match=message):
        router._compile_access_rules(table)


def test_every_sample_path_reaches_its_own_route():
    """The matrix below is only meaningful if each sample path is dispatched
    to the route it was built from, not to an earlier one."""
    for route in router.ROUTES:
        path = _sample_path(route[1])
        assert _first_route_for(route[0], path) is route, _route_id(route)


@pytest.mark.parametrize("path", [
    "/mcp", "/api/v1/leads", "/api/v1/openapi.json", "/oauth/token", "/oauth/authorize",
])
def test_gateway_paths_are_not_in_the_session_route_table(path):
    """The bearer-token gateway authenticates on its own and is registered
    before the cookie router; no session route may claim its paths."""
    for method in ("GET", "POST", "PATCH", "PUT", "DELETE"):
        assert _first_route_for(method, path) is None, (method, path)


# ─── The dispatcher enforces it ─────────────────────────────────────────────

@pytest.mark.parametrize("route", PROTECTED, ids=_route_id)
def test_signed_out_request_gets_401_without_reaching_the_handler(route, recorded, monkeypatch):
    _sign_in_as(monkeypatch, None)
    status, body = _dispatch(route[0], _sample_path(route[1]), signed_in=False)
    assert status == 401
    assert body == {"error": "Nieautoryzowany dostęp. Zaloguj się."}
    assert recorded == []


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("route", PROTECTED, ids=_route_id)
def test_each_role_is_admitted_or_refused_by_the_route_rule(route, role, recorded, monkeypatch):
    _sign_in_as(monkeypatch, role)
    status, body = _dispatch(route[0], _sample_path(route[1]))
    if role in RULE_ROLES[route[2]]:
        assert status == 200, f"{role} refused by {_route_id(route)}: {body}"
        assert len(recorded) == 1
    else:
        assert status == 403, f"{role} admitted by {_route_id(route)}"
        assert body == {"error": "Brak dostępu."}
        assert recorded == []


@pytest.mark.parametrize("route", PUBLIC_ROUTES, ids=_route_id)
def test_public_routes_reach_the_handler_without_a_session(route, recorded, monkeypatch):
    _sign_in_as(monkeypatch, None)
    status, _ = _dispatch(route[0], _sample_path(route[1]), signed_in=False)
    assert status == 200
    assert len(recorded) == 1


@pytest.mark.parametrize("method, path, refused", [
    ("GET", "/api/partner-bonuses", {"partner", "observer"}),
    ("GET", "/api/partner-bonuses/7", {"observer"}),
    ("GET", "/api/audit", {"manager", "observer", "partner"}),
    ("GET", "/api/users", {"manager", "observer", "partner"}),
    ("POST", "/api/payouts", {"manager", "observer", "partner"}),
    ("PATCH", "/api/settings", {"observer", "partner"}),
    ("POST", "/api/leads", {"admin", "manager", "observer"}),
    ("PATCH", "/api/leads/LD-001", {"observer", "partner"}),
    ("POST", "/api/agent/chat", {"manager", "observer"}),
    ("GET", "/api/knowledge", set()),
    ("GET", "/api/knowledge/7/file", set()),
    ("POST", "/api/knowledge", {"observer", "partner"}),
    ("PATCH", "/api/knowledge/7", {"observer", "partner"}),
    ("DELETE", "/api/knowledge/7", {"observer", "partner"}),
])
def test_known_sensitive_routes_refuse_the_expected_roles(method, path, refused, recorded, monkeypatch):
    route = _first_route_for(method, path)
    assert route is not None
    assert {r for r in ROLES if r not in RULE_ROLES[route[2]]} == refused
