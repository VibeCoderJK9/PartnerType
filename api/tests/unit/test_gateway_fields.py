"""The gateway's allowlist projections.

Every record that leaves through MCP or /api/v1 is built here from named
keys. These tests pin the two promises that matter most: a column that is not
on an allowlist never leaks, and money (earnings, payouts) never appears.
"""
import itertools
import json

from api.gateway import fields

ADMIN = {"id": 1, "role": "admin"}
MANAGER = {"id": 2, "role": "manager"}
OBSERVER = {"id": 3, "role": "observer"}
PARTNER = {"id": 7, "role": "partner"}

UNEXPECTED = ("secret_new_column", "settlement_status", "commission_paid_at",
              "commission_override_pct", "total_earnings", "payouts")

LEAD_ROW = {
    "id": "LD-001", "company": "Klient", "value": 1000, "product": "ERP", "quantity": 1,
    "status": "new", "registration_status": "pending", "partner_company": "Partner SA",
    "partner_id": 7, "created_at": "2026-10-18 08:00:00", "updated_at": None, "won_at": None,
    "client_name": "Jan", "client_email": "jan@klient.pl", "client_phone": "600",
    "notes": "n", "sector": "IT", "nip": "123", "website": "x.pl", "commission": 70,
    **{key: "LEAK" for key in UNEXPECTED},
}

STAFF_DATA = {
    "total_leads": 5, "new_leads": 1, "pending_leads": 1, "accepted_leads": 2,
    "rejected_leads": 1, "overdue_leads": 0, "total_commissions": 1234.5,
    "total_partners": 3, "status_dist": {"new": 5}, "registration_dist": {"pending": 1},
    "won_today": 0,
    "trends": {"commissions": {"pct": 10}}, "monthly": [{"commissions": 5}],
    "partner_ranking": [{"total_earnings": 9}], "top_partners": [{"total_earnings": 9}],
    "pipeline_dist": {}, "channel": {}, "payouts": {"paid": 1},
}

PARTNER_DATA = {
    "total_leads": 2, "pending_leads": 1, "accepted_leads": 1, "rejected_leads": 0,
    "total_earnings": 777.0, "status_dist": {"new": 2}, "registration_dist": {"pending": 1},
    "won_today": 0,
    "trends": {"earnings": {"pct": 3}}, "monthly": [{"earnings": 1}],
}


def _all_group_sets():
    groups = sorted(fields.SWITCHABLE_GROUPS)
    for size in range(len(groups) + 1):
        for combo in itertools.combinations(groups, size):
            yield frozenset(combo)


def test_project_lead_never_outputs_an_unexpected_column():
    for groups in _all_group_sets():
        out = fields.project_lead(LEAD_ROW, groups)
        assert not set(out) & set(UNEXPECTED), groups
        assert "LEAK" not in json.dumps(out)


def test_project_stats_staff_without_commissions_group_is_exactly_the_staff_keys():
    out = fields.project_stats(STAFF_DATA, ADMIN, frozenset())
    assert set(out) == set(fields.STAFF_STATS_KEYS)
    assert "total_commissions" not in out


def test_project_stats_staff_with_commissions_group_adds_total_commissions():
    out = fields.project_stats(STAFF_DATA, MANAGER, frozenset({"commissions"}))
    assert set(out) == set(fields.STAFF_STATS_KEYS) | {"total_commissions"}
    assert out["total_commissions"] == 1234.5


def test_project_stats_partner_never_carries_money_or_charts():
    for groups in _all_group_sets():
        out = fields.project_stats(PARTNER_DATA, PARTNER, groups)
        assert set(out) == set(fields.PARTNER_STATS_KEYS), groups
        for key in ("total_earnings", "trends", "monthly", "partner_ranking",
                    "top_partners", "total_commissions"):
            assert key not in out


def test_project_stats_staff_never_carries_earnings_rankings_or_payouts():
    for groups in _all_group_sets():
        out = fields.project_stats(STAFF_DATA, ADMIN, groups)
        text = json.dumps(out)
        assert "total_earnings" not in text
        for key in ("trends", "monthly", "partner_ranking", "top_partners", "payouts"):
            assert key not in out


PARTNER_ROW = {
    "id": 7, "name": "Anna Nowak", "email": "anna@partner.pl", "company": "Partner SA",
    "level": "gold", "tier_id": 2, "tier_name": "Gold", "tier_color": "#fc0",
    "tier_name_en": "Gold", "joined_at": "2026-09-01", "total_leads": 4,
    "accepted_leads": 2, "total_earnings": 999.0, "secret_new_column": "LEAK",
}


def test_project_partner_never_outputs_earnings():
    for groups in _all_group_sets():
        out = fields.project_partner(PARTNER_ROW, groups)
        assert "total_earnings" not in out
        assert "secret_new_column" not in out
        assert "999" not in json.dumps(out)


def test_project_partner_name_and_email_only_with_contact():
    without = fields.project_partner(PARTNER_ROW, frozenset())
    assert "name" not in without and "email" not in without
    assert "Anna Nowak" not in json.dumps(without)
    with_contact = fields.project_partner(PARTNER_ROW, frozenset({"contact"}))
    assert with_contact["name"] == "Anna Nowak"
    assert with_contact["email"] == "anna@partner.pl"


def test_project_partner_company_falls_back_to_partner_number():
    out = fields.project_partner({**PARTNER_ROW, "company": "  "}, frozenset())
    assert out["company"] == "Partner #7"


def test_project_product_is_an_allowlist():
    row = {"id": 1, "name": "ERP", "name_en": "ERP", "category": "software",
           "base_price": 100.0, "offered": 1, "active": 1, "code": "X",
           "secret_new_column": "LEAK"}
    out = fields.project_product(row)
    assert set(out) == {"id", "name", "name_en", "category", "base_price", "offered", "active"}
    assert out["offered"] is True and out["active"] is True


REMINDER_ROW = {
    "id": 11, "user_id": 1, "type": "warning", "is_read": 0,
    "title": "Partner Anna Nowak nie zgłosił leada od 14 dni",
    "body": "Ostatnia aktywność: 01.10.2026. Warto się odezwać.",
    "link": "partners.html", "title_key": "ntf_block_inactive_title",
    "body_key": "ntf_block_inactive_body", "created_at": "2026-10-18 06:00:00",
    "params": {"partner": "Anna Nowak", "days": 14, "date": "01.10.2026", "extra": "LEAK"},
}


def test_project_reminder_default_groups_drop_the_partner_name():
    out = fields.project_reminder(REMINDER_ROW, frozenset())
    assert set(out) == {"id", "kind", "params", "link", "created_at", "is_read"}
    assert out["kind"] == "ntf_block_inactive_title"
    assert out["params"] == {"days": 14, "date": "01.10.2026"}
    assert out["is_read"] is False
    text = json.dumps(out, ensure_ascii=False)
    assert "Anna Nowak" not in text
    assert "LEAK" not in text


def test_project_reminder_contact_group_adds_partner_and_sentence():
    out = fields.project_reminder(REMINDER_ROW, frozenset({"contact"}))
    assert out["params"]["partner"] == "Anna Nowak"
    assert out["title"] == REMINDER_ROW["title"]
    assert "LEAK" not in json.dumps(out)


def test_reminder_params_allowlist():
    assert fields.REMINDER_PARAMS == frozenset({"days", "date"})


def test_project_comment_shows_is_internal_to_staff_only():
    row = {"body": "x" * 600, "created_at": "2026-10-18", "author_name": "Ola",
           "author_role": "admin", "is_internal": 1, "user_id": 5, "lead_id": "LD-1"}
    staff = fields.project_comment(row, ADMIN)
    assert staff["is_internal"] is True
    assert len(staff["body"]) == 500
    for viewer in (OBSERVER, PARTNER):
        out = fields.project_comment(row, viewer)
        assert set(out) == {"body", "created_at", "author_name", "author_role"}


def test_project_custom_fields_keeps_label_value_active():
    out = fields.project_custom_fields([
        {"field_id": 1, "label": "Budżet", "field_type": "text", "value": "y" * 700,
         "active": True, "partner_editable": False},
    ])
    assert out == [{"label": "Budżet", "value": "y" * 499 + "…", "active": True}]


def test_truncate_text_caps_at_500_with_ellipsis():
    out = fields.truncate_text("a" * 600)
    assert len(out) == 500
    assert out.endswith("…")
    assert fields.truncate_text("short") == "short"
    assert fields.truncate_text(42) == 42
    assert fields.truncate_text(None) is None
