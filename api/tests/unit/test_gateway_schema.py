"""The gateway's one argument validator and the registry it guards."""
import pytest

from api import agent_tools
from api.gateway.schema import ArgumentError, validate_arguments

LIST_SCHEMA = agent_tools.GATEWAY_TOOL_REGISTRY["list_leads"]["input_schema"]
DETAIL_SCHEMA = agent_tools.GATEWAY_TOOL_REGISTRY["get_lead_details"]["input_schema"]


def test_unknown_key_is_refused():
    with pytest.raises(ArgumentError, match="unknown argument: colour"):
        validate_arguments(LIST_SCHEMA, {"colour": "red"})


def test_per_page_above_100_is_refused():
    with pytest.raises(ArgumentError, match="per_page"):
        validate_arguments(LIST_SCHEMA, {"per_page": 101})


def test_query_text_is_coerced_to_integer():
    assert validate_arguments(LIST_SCHEMA, {"per_page": "25"}, from_query=True) == {"per_page": 25}


def test_query_text_is_not_coerced_without_from_query():
    with pytest.raises(ArgumentError, match="per_page must be an integer"):
        validate_arguments(LIST_SCHEMA, {"per_page": "25"})


def test_boolean_is_not_an_integer():
    with pytest.raises(ArgumentError, match="page must be an integer"):
        validate_arguments(LIST_SCHEMA, {"page": True})


def test_query_booleans_and_numbers_are_coerced():
    out = validate_arguments(LIST_SCHEMA, {"overdue": "true", "min_value": "12.5"},
                             from_query=True)
    assert out == {"overdue": True, "min_value": 12.5}
    assert validate_arguments(LIST_SCHEMA, {"overdue": "0"}, from_query=True) == {"overdue": False}
    with pytest.raises(ArgumentError):
        validate_arguments(LIST_SCHEMA, {"overdue": "maybe"}, from_query=True)


def test_enum_and_pattern_are_enforced():
    with pytest.raises(ArgumentError, match="registration_status"):
        validate_arguments(LIST_SCHEMA, {"registration_status": "won"})
    with pytest.raises(ArgumentError, match="date_from"):
        validate_arguments(LIST_SCHEMA, {"date_from": "18.10.2026"})
    with pytest.raises(ArgumentError, match="lead_id"):
        validate_arguments(DETAIL_SCHEMA, {"lead_id": "ld-1; drop"})


def test_required_argument_is_enforced():
    with pytest.raises(ArgumentError, match="missing required argument: lead_id"):
        validate_arguments(DETAIL_SCHEMA, {})


def test_optional_null_is_treated_as_absent():
    assert validate_arguments(LIST_SCHEMA, {"status": None, "page": 2}) == {"page": 2}


def test_non_object_arguments_are_refused():
    with pytest.raises(ArgumentError):
        validate_arguments(LIST_SCHEMA, ["page", 1])


def test_string_length_is_enforced():
    with pytest.raises(ArgumentError, match="q must be at most 200"):
        validate_arguments(LIST_SCHEMA, {"q": "x" * 201})


def test_registry_declares_exactly_eight_read_tools():
    assert set(agent_tools.GATEWAY_TOOL_REGISTRY) == {
        "list_leads", "get_lead_details", "get_lead_stats", "list_products",
        "list_partners", "get_partners_count_by_tier", "list_reminders",
        "list_affiliate_orders",
    }
    for name, spec in agent_tools.GATEWAY_TOOL_REGISTRY.items():
        assert spec["risk"] == "read", name
        assert isinstance(spec["roles"], tuple), name
        assert spec["input_schema"]["additionalProperties"] is False, name


def test_every_registry_schema_stays_inside_the_supported_subset():
    supported = {"string", "integer", "number", "boolean"}
    for name, spec in agent_tools.GATEWAY_TOOL_REGISTRY.items():
        for prop, prop_spec in spec["input_schema"]["properties"].items():
            assert prop_spec["type"] in supported, (name, prop)
        if not spec["input_schema"]["required"]:
            assert validate_arguments(spec["input_schema"], {}) == {}


def test_local_agent_registry_is_untouched():
    assert set(agent_tools.TOOL_REGISTRY) == {
        "list_leads", "get_lead_details", "get_lead_stats", "list_products",
        "get_partners_count_by_tier", "list_affiliate_orders", "list_custom_labels",
        "prepare_translate_labels",
        "prepare_create_lead", "prepare_update_lead_status",
    }
