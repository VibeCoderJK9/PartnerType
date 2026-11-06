"""Unit contracts for the shared API input-validation primitives."""

import math

import pytest

from api import bonus_defs, commission_tiers, partner_bonuses
from api.validation import (
    parse_boolean_flag,
    parse_allowed_fields,
    parse_color,
    parse_email,
    parse_enum,
    parse_finite_number,
    parse_integer_id,
    parse_integer_id_list,
    parse_non_negative_integer,
    parse_optional_string,
    parse_password,
    parse_required_string,
)


@pytest.mark.parametrize("value", [None, "", "   ", 12, [], {}])
def test_required_string_rejects_missing_blank_and_wrong_types(value):
    with pytest.raises(ValueError, match="Nazwa"):
        parse_required_string(value, "Nazwa", max_length=20)


def test_string_helpers_trim_without_html_stripping():
    literal = "  <b>Anna & Syn</b>  "

    assert parse_required_string(literal, "Nazwa", max_length=30) == "<b>Anna & Syn</b>"
    assert parse_optional_string(None, "Opis", max_length=30) == ""
    assert parse_optional_string("  tekst  ", "Opis", max_length=30) == "tekst"


def test_string_helpers_enforce_length_after_trimming():
    with pytest.raises(ValueError, match="maksymalnie 4"):
        parse_required_string(" abcde ", "Nazwa", max_length=4)
    with pytest.raises(ValueError, match="maksymalnie 4"):
        parse_optional_string("abcde", "Opis", max_length=4)


@pytest.mark.parametrize("value", ["a@b.pl", "USER+tag@example.com"])
def test_email_accepts_normalized_valid_addresses(value):
    assert parse_email(f"  {value}  ", "Email") == value


@pytest.mark.parametrize(
    "value", [None, 123, "plain", "@example.com", "a@", "a b@example.com", "a@b..pl"]
)
def test_email_rejects_wrong_type_or_invalid_shape(value):
    with pytest.raises(ValueError, match="Email"):
        parse_email(value, "Email")


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.2, "1.2", "abc", [], None])
def test_integer_id_rejects_bool_non_positive_and_wrong_types(value):
    with pytest.raises(ValueError, match="ID"):
        parse_integer_id(value, "ID")


def test_integer_id_accepts_positive_int_with_optional_upper_bound():
    assert parse_integer_id(7, "ID", maximum=10) == 7
    assert parse_integer_id(" 7 ", "ID", maximum=10) == 7
    with pytest.raises(ValueError, match="ID"):
        parse_integer_id(11, "ID", maximum=10)


@pytest.mark.parametrize("value", [None, 1, "owner", "unknown"])
def test_enum_rejects_missing_wrong_type_and_unknown_values(value):
    with pytest.raises(ValueError, match="Rola"):
        parse_enum(value, "Rola", ("admin", "partner"))


def test_enum_returns_trimmed_allowlisted_value():
    assert parse_enum(" partner ", "Rola", ("admin", "partner")) == "partner"


@pytest.mark.parametrize(
    "value", [True, False, None, [], {}, "not-a-number", math.nan, math.inf, -math.inf]
)
def test_finite_number_rejects_bool_wrong_types_and_non_finite_values(value):
    with pytest.raises(ValueError, match="Kwota"):
        parse_finite_number(value, "Kwota", minimum=0, maximum=100)


def test_finite_number_accepts_numeric_strings_and_enforces_bounds():
    assert parse_finite_number(" 12.50 ", "Kwota", minimum=0, maximum=100) == 12.5
    for value in (-0.01, 100.01):
        with pytest.raises(ValueError, match="Kwota"):
            parse_finite_number(value, "Kwota", minimum=0, maximum=100)


def test_allowed_fields_returns_copy_and_rejects_non_dict_or_unknown_keys():
    source = {"name": "Anna"}
    assert parse_allowed_fields(source, ("name",), "Dane") == source
    assert parse_allowed_fields(source, ("name",), "Dane") is not source

    with pytest.raises(ValueError, match="Dane"):
        parse_allowed_fields([], ("name",), "Dane")
    with pytest.raises(ValueError, match="unexpected"):
        parse_allowed_fields({"name": "Anna", "unexpected": 1}, ("name",), "Dane")


@pytest.mark.parametrize("value", [None, 123, [], "short", "x" * 129])
def test_password_rejects_wrong_types_and_length_bounds(value):
    with pytest.raises(ValueError, match="Hasło"):
        parse_password(value, "Hasło", min_length=8, max_length=128)


def test_password_default_cap_is_bcrypts_72_bytes():
    assert parse_password("x" * 72, "Hasło") == "x" * 72
    with pytest.raises(ValueError, match="Hasło"):
        parse_password("x" * 73, "Hasło")
    with pytest.raises(ValueError, match="Hasło"):
        parse_password("ą" * 37, "Hasło")


def test_password_preserves_significant_whitespace():
    value = "  Secret123!  "
    assert parse_password(value, "Hasło", min_length=8, max_length=128) == value


@pytest.mark.parametrize("value", [True, False, 1, 0])
def test_boolean_flag_accepts_only_explicit_boolean_values(value):
    assert parse_boolean_flag(value, "Aktywny") == int(bool(value))


@pytest.mark.parametrize("value", [None, "true", "0", 2, -1, [], {}])
def test_boolean_flag_rejects_truthy_and_out_of_range_coercions(value):
    with pytest.raises(ValueError, match="Aktywny"):
        parse_boolean_flag(value, "Aktywny")


def test_non_negative_integer_accepts_zero_and_enforces_bound():
    assert parse_non_negative_integer(0, "Kolejność", maximum=10000) == 0
    assert parse_non_negative_integer(" 12 ", "Kolejność", maximum=10000) == 12
    for value in (True, -1, 1.5, "1.5", 10001, [], {}):
        with pytest.raises(ValueError, match="Kolejność"):
            parse_non_negative_integer(value, "Kolejność", maximum=10000)


@pytest.mark.parametrize("value", ["#6246ea", " #ABCDEF "])
def test_color_accepts_normalized_hex_values(value):
    assert parse_color(value, "Kolor") == value.strip()


@pytest.mark.parametrize("value", [None, 12, "red", "#abcd", "#gggggg", [], {}])
def test_color_rejects_non_hex_and_wrong_types(value):
    with pytest.raises(ValueError, match="Kolor"):
        parse_color(value, "Kolor")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"name": []},
        {"name": "x" * 101},
        {"base_rate": float("nan")},
        {"base_rate": float("inf")},
        {"base_rate": 100.01},
        {"color": "javascript:alert(1)"},
        {"sort_order": -1},
        {"active": "false"},
        {"unexpected": 1},
    ],
)
def test_commission_tier_create_rejects_invalid_data_before_database(
    monkeypatch, payload
):
    monkeypatch.setattr(
        commission_tiers,
        "get_conn",
        lambda: pytest.fail("database opened before validation"),
    )

    with pytest.raises(ValueError):
        commission_tiers.create_tier(payload, {"id": 1, "role": "admin"})


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"name": {}},
        {"name": "x" * 101},
        {"type": []},
        {"type": "credit"},
        {"default_value": float("nan")},
        {"default_value": float("inf")},
        {"default_value": -0.01},
        {"sort_order": -1},
        {"description": []},
        {"active": "yes"},
        {"unexpected": 1},
    ],
)
def test_bonus_definition_create_rejects_invalid_data_before_database(
    monkeypatch, payload
):
    monkeypatch.setattr(
        bonus_defs,
        "get_conn",
        lambda: pytest.fail("database opened before validation"),
    )

    with pytest.raises(ValueError):
        bonus_defs.create_bonus(payload, {"id": 1, "role": "admin"})


@pytest.mark.parametrize("invalid_id", [0, -1, True, "1.5", [], {}])
def test_tier_and_bonus_mutations_reject_invalid_ids_before_database(
    monkeypatch, invalid_id
):
    monkeypatch.setattr(
        commission_tiers,
        "get_conn",
        lambda: pytest.fail("tier database opened before validation"),
    )
    monkeypatch.setattr(
        bonus_defs,
        "get_conn",
        lambda: pytest.fail("bonus database opened before validation"),
    )

    # A configuring role, because the bonus and tier writers refuse an
    # unauthorised caller before they look at anything at all, including the
    # id. This case is about the ID check, so every call gets past the role
    # check first; the refusal itself is pinned right below.
    admin = {"id": 1, "role": "admin"}
    with pytest.raises(ValueError):
        commission_tiers.update_tier(invalid_id, {"name": "Gold"}, admin)
    with pytest.raises(ValueError):
        commission_tiers.delete_tier(invalid_id, admin)
    with pytest.raises(ValueError):
        bonus_defs.update_bonus(invalid_id, {"name": "Start"}, admin)
    with pytest.raises(ValueError):
        bonus_defs.delete_bonus(invalid_id, admin)


def test_bonus_mutations_refuse_an_unauthorised_caller_before_the_database(monkeypatch):
    """The role check comes first, and it never reaches the database either.

    The HTTP handlers answer 403 on their own, but api/agent_actions.py and any
    future caller reach these functions directly -- so the canonical writer is
    where the refusal has to live.
    """
    monkeypatch.setattr(
        bonus_defs,
        "get_conn",
        lambda: pytest.fail("bonus database opened before the role check"),
    )
    partner = {"id": 7, "role": "partner"}

    with pytest.raises(PermissionError):
        bonus_defs.create_bonus(
            {"name": "Mój bonus", "type": "percent", "default_value": 99}, partner
        )
    with pytest.raises(PermissionError):
        bonus_defs.update_bonus(1, {"name_en": "Mine now"}, partner)
    with pytest.raises(PermissionError):
        bonus_defs.delete_bonus(1, partner)


def test_integer_id_list_is_bounded_normalized_and_deduplicated():
    assert parse_integer_id_list([" 2 ", 1, 2, "1"], "Partnerzy", maximum_items=4) == [2, 1]

    for value in (None, {}, "1,2", [], [1, 2, 3]):
        with pytest.raises(ValueError, match="Partnerzy"):
            parse_integer_id_list(value, "Partnerzy", maximum_items=2)


@pytest.mark.parametrize("invalid_id", [0, -1, True, "1.5", [], {}])
def test_partner_bonus_single_mutations_reject_invalid_ids_before_database(
    monkeypatch, invalid_id
):
    monkeypatch.setattr(
        partner_bonuses,
        "get_conn",
        lambda: pytest.fail("partner bonus database opened before validation"),
    )

    with pytest.raises(ValueError):
        partner_bonuses.assign_bonus(invalid_id, 1, None, {"id": 1})
    with pytest.raises(ValueError):
        partner_bonuses.assign_bonus(1, invalid_id, None, {"id": 1})
    with pytest.raises(ValueError):
        partner_bonuses.remove_bonus(invalid_id, 1, {"id": 1})
    with pytest.raises(ValueError):
        partner_bonuses.update_tier(1, invalid_id, {"id": 1})


@pytest.mark.parametrize(
    "value", [True, [], {}, "not-a-number", float("nan"), float("inf"), -0.01, 1000000001]
)
def test_partner_bonus_custom_value_rejects_invalid_values_before_database(
    monkeypatch, value
):
    monkeypatch.setattr(
        partner_bonuses,
        "get_conn",
        lambda: pytest.fail("partner bonus database opened before validation"),
    )

    with pytest.raises(ValueError):
        partner_bonuses.assign_bonus(1, 1, value, {"id": 1})


def test_partner_bonus_bulk_deduplicates_ids_before_assigning(monkeypatch):
    calls = []
    monkeypatch.setattr(
        partner_bonuses,
        "assign_bonus",
        lambda partner_id, bonus_id, value, user: calls.append(
            (partner_id, bonus_id, value)
        ),
    )
    monkeypatch.setattr(partner_bonuses.audit, "log", lambda *args, **kwargs: None)

    result = partner_bonuses.bulk_assign([2, "2", 1, 2], 3, "4.5", {"id": 1})

    assert calls == [(2, 3, 4.5), (1, 3, 4.5)]
    assert result == {"ok": [2, 1], "errors": [], "processed": 2}


def test_partner_bonus_bulk_rejects_oversized_arrays_before_item_work(monkeypatch):
    monkeypatch.setattr(
        partner_bonuses,
        "assign_bonus",
        lambda *args, **kwargs: pytest.fail("item work started before array validation"),
    )

    with pytest.raises(ValueError, match="Partnerzy"):
        partner_bonuses.bulk_assign(list(range(1, 502)), 3, None, {"id": 1})
