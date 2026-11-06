"""The OpenAPI description of /api/v1 is generated from the code it describes.

The document is built from `rest_v1.V1_ROUTES`
and the gateway tool registry's input schemas -- the same schemas MCP and the
/api/v1 query validation use -- so a route without a description, a parameter
the docs do not know or a stale committed `docs/openapi.json` turns this file
red.
"""
import json
from pathlib import Path

import pytest

from api import agent_tools
from api.gateway import openapi, rest_v1
from api.tests.integration import gateway_helpers as gh

BASE = "https://example.test"
REPO = Path(__file__).resolve().parents[3]
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}


@pytest.fixture
def doc():
    return openapi.build_document(BASE)


def _operations(document):
    for path, item in document.get("paths", {}).items():
        for method, operation in item.items():
            if method in HTTP_METHODS:
                yield method, path, operation


def _resolve(document, node):
    ref = node.get("$ref")
    if not ref:
        return node
    target = document
    for part in ref.lstrip("#/").split("/"):
        target = target[part]
    return target


def test_document_paths_equal_the_route_table(doc):
    in_document = {(method.upper(), path) for method, path, _ in _operations(doc)}
    in_code = {(route.method, route.path) for route in rest_v1.V1_ROUTES}
    assert in_code and in_document == in_code


def test_every_operation_is_secured_and_declares_the_error_answers(doc):
    operations = list(_operations(doc))
    assert len(operations) == len(rest_v1.V1_ROUTES)
    for method, path, operation in operations:
        assert operation["security"] == [{"bearer": []}, {"oauth2": ["read"]}], path
        assert {"200", "400", "401", "403", "429"} <= set(operation["responses"]), path
        rate_limited = _resolve(doc, operation["responses"]["429"])
        assert "Retry-After" in rate_limited["headers"], path
        assert rate_limited["headers"]["Retry-After"]["schema"]["type"] == "integer"
    detail = doc["paths"]["/leads/{id}"]["get"]
    assert "404" in detail["responses"]


def test_lead_list_query_parameters_are_the_registry_schema(doc):
    schema = agent_tools.GATEWAY_TOOL_REGISTRY["list_leads"]["input_schema"]
    params = doc["paths"]["/leads"]["get"]["parameters"]
    assert all(param["in"] == "query" for param in params)
    assert [param["name"] for param in params] == list(schema["properties"])
    for param in params:
        assert param["schema"] == schema["properties"][param["name"]]
        assert param["required"] is (param["name"] in schema.get("required", ()))
    partner_id = next(param for param in params if param["name"] == "partner_id")
    assert "partner" in partner_id["description"].lower()
    assert "ignored" in partner_id["description"].lower() or \
        "not available" in partner_id["description"].lower()


def test_detail_route_takes_the_lead_id_from_the_path(doc):
    params = doc["paths"]["/leads/{id}"]["get"]["parameters"]
    lead_id = agent_tools.GATEWAY_TOOL_REGISTRY["get_lead_details"]["input_schema"][
        "properties"]["lead_id"]
    assert params == [{"name": "id", "in": "path", "required": True,
                       "description": lead_id["description"], "schema": lead_id}]


def test_reminders_document_the_100_notification_window(doc):
    description = doc["paths"]["/reminders"]["get"]["description"]
    assert "100" in description and "notification" in description


def test_servers_security_schemes_and_version(doc):
    assert doc["openapi"] == "3.1.0"
    assert doc["servers"] == [{"url": BASE + "/api/v1"}]
    schemes = doc["components"]["securitySchemes"]
    assert schemes["bearer"]["type"] == "http" and schemes["bearer"]["scheme"] == "bearer"
    flow = schemes["oauth2"]["flows"]["authorizationCode"]
    assert schemes["oauth2"]["type"] == "oauth2"
    assert flow["authorizationUrl"] == BASE + "/oauth/authorize"
    assert flow["tokenUrl"] == BASE + "/oauth/token"
    assert flow["refreshUrl"] == BASE + "/oauth/token"
    assert set(flow["scopes"]) == {"read"}
    assert "S256" in schemes["oauth2"]["description"]
    assert "untrusted_data" in doc["info"]["description"]


def test_render_is_deterministic_and_needs_no_database(mocker):
    first = openapi.render(openapi.DOCS_BASE_URL)
    mocker.patch("api.db.get_conn", side_effect=AssertionError("no database access"))
    second = openapi.render(openapi.DOCS_BASE_URL)
    assert first == second
    assert first.startswith("{") and first.endswith("}\n")
    assert json.loads(first)["servers"] == [{"url": "https://firma.partnertype.com/api/v1"}]


def test_committed_docs_file_matches_the_generator():
    path = REPO / "docs" / "openapi.json"
    assert path.exists(), "docs/openapi.json missing -- python -m api.gateway.openapi > docs/openapi.json"
    assert path.read_text(encoding="utf-8") == openapi.render(openapi.DOCS_BASE_URL), (
        "docs/openapi.json is stale -- regenerate with python -m api.gateway.openapi")


def test_served_document_is_public_and_not_logged():
    response = gh.client().get("/api/v1/openapi.json")

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.headers["Content-Type"].startswith("application/json")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["servers"][0]["url"] == "http://localhost:8000/api/v1"
    assert gh.count_rows("api_call_log") == 0
