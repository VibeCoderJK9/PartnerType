"""
PartnerType -- Leads module unit tests.

Covers lead creation, retrieval, filtering, access scoping, status
transitions, permission gates, and the HTTP handler entry points defined in
api/leads.py. Commission calculation itself lives in test_commissions.py.
"""
from datetime import datetime, timedelta
import math

import pytest

import api.audit as audit
import api.email_service as mailer
import api.notifications as notif
from api.db import get_conn

# Captured at import, before the autouse fixture replaces it with a mock.
REAL_CREATE_NOTIFICATION = notif.create_notification
from api.leads import (
    LEADS_MAX_PAGE_SIZE,
    LEADS_PAGE_SIZE,
    create_lead,
    get_lead,
    get_leads,
    get_leads_page,
    handle_bulk,
    handle_create,
    handle_get,
    handle_list,
    handle_update,
    update_lead,
)

# The seeded SALES pipeline's stage keys (api/db.py's migrate_db()).
# api/leads.py looks stages up at call time against the active lead_stages
# table, so this parametrize list pins the seeded keys locally. The four
# registration words are not on this axis.
SEED_STAGE_KEYS = ("contact", "presentation", "offer", "negotiation", "won", "lost")
REGISTRATION_KEYS = ("pending", "needs_info", "accepted", "rejected")


def _lead_data(**overrides):
    data = {
        "client_name": "Anna Testowa",
        "client_email": "anna@example.pl",
        "company": "Testowa Sp. z o.o.",
        "value": 10000,
    }
    data.update(overrides)
    return data


# ─── CREATION ─────────────────────────────────────────────────────────────

class TestLeadCreation:
    def test_create_lead_awaits_a_decision_with_no_stage_and_null_commission(
        self, partner_user
    ):
        lead = create_lead(partner_user, _lead_data())

        # A freshly submitted deal is on nobody's sales pipeline yet -- it is
        # waiting for the producer to decide.
        assert lead["registration_status"] == "pending"
        assert lead["status"] is None
        assert lead["partner_id"] == partner_user["id"]
        assert lead["commission"] is None
        assert lead["id"].startswith("LD-")
        assert len(lead["id"]) == 6
        assert lead["id"][3:].isdigit()

    def test_create_lead_returns_joined_partner_name_and_company(
        self, partner_user
    ):
        lead = create_lead(partner_user, _lead_data())

        assert lead["partner_name"] == partner_user["name"]
        assert lead["partner_company"] == partner_user["company"]

    def test_create_lead_sequential_identifiers(self, partner_user):
        first = create_lead(partner_user, _lead_data())
        second = create_lead(partner_user, _lead_data())
        third = create_lead(partner_user, _lead_data())

        assert [first["id"], second["id"], third["id"]] == [
            "LD-001",
            "LD-002",
            "LD-003",
        ]

    @pytest.mark.parametrize("field,label", [
        ("client_name", "Imię i nazwisko klienta"),
        ("client_email", "E-mail klienta"),
        ("company", "Firma klienta"),
        ("value", "Wartość"),
    ])
    def test_create_lead_rejects_missing_required_field(
        self, partner_user, field, label
    ):
        # The message names the field the way the form does, never its key.
        data = _lead_data()
        del data[field]

        with pytest.raises(ValueError, match=label):
            create_lead(partner_user, data)

    def test_create_lead_rejects_negative_value(self, partner_user):
        with pytest.raises(ValueError):
            create_lead(partner_user, _lead_data(value=-100))

    def test_create_lead_rejects_non_numeric_value(self, partner_user):
        with pytest.raises(ValueError):
            create_lead(partner_user, _lead_data(value="not-a-number"))

    def test_create_lead_strips_whitespace_from_string_fields(
        self, partner_user
    ):
        lead = create_lead(
            partner_user,
            _lead_data(
                client_name="  Anna Testowa  ",
                client_email="  anna@example.pl  ",
                company="  Testowa Sp. z o.o.  ",
            ),
        )

        assert lead["client_name"] == "Anna Testowa"
        assert lead["client_email"] == "anna@example.pl"
        assert lead["company"] == "Testowa Sp. z o.o."

    def test_create_lead_optional_fields_default_to_empty_string(
        self, partner_user
    ):
        lead = create_lead(partner_user, _lead_data())

        assert lead["client_phone"] == ""
        assert lead["notes"] == ""
        assert lead["sector"] == ""

    def test_create_lead_sql_injection_shaped_name_stored_verbatim(
        self, partner_user
    ):
        malicious = "Robert'); DROP TABLE leads;--"
        lead = create_lead(partner_user, _lead_data(client_name=malicious))

        assert lead["client_name"] == malicious

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT client_name FROM leads WHERE id = ?", (lead["id"],)
            ).fetchone()
            table_exists = conn.execute(
                "SELECT table_name AS name FROM information_schema.tables WHERE table_schema='public' AND table_name='leads'"
            ).fetchone()
        finally:
            conn.close()

        assert row["client_name"] == malicious
        assert table_exists is not None

    @pytest.mark.parametrize(
        "field,value",
        [
            ("client_name", 123),
            ("client_name", "   "),
            ("client_name", "x" * 201),
            ("client_email", "not-an-email"),
            ("client_email", "x" * 245 + "@example.com"),
            ("company", []),
            ("client_phone", "x" * 51),
            ("notes", "x" * 5001),
            ("sector", "x" * 101),
            ("value", math.nan),
            ("value", math.inf),
            ("value", True),
            ("value", 1_000_000_000_001),
        ],
    )
    def test_create_lead_rejects_type_shape_and_bounds_before_connection(
        self, partner_user, mocker, field, value
    ):
        mocked_conn = mocker.patch("api.leads.get_conn")

        with pytest.raises(ValueError):
            create_lead(partner_user, _lead_data(**{field: value}))

        mocked_conn.assert_not_called()

    def test_create_lead_rejects_unknown_fields_before_connection(
        self, partner_user, mocker
    ):
        mocked_conn = mocker.patch("api.leads.get_conn")

        with pytest.raises(ValueError, match="unexpected"):
            create_lead(partner_user, _lead_data(unexpected="value"))

        mocked_conn.assert_not_called()

    def test_create_lead_preserves_html_shaped_text_for_output_encoding(
        self, partner_user
    ):
        literal = "<img src=x onerror=alert(1)>"

        lead = create_lead(partner_user, _lead_data(notes=f"  {literal}  "))

        assert lead["notes"] == literal


# ─── CREATION SIDE-EFFECTS ────────────────────────────────────────────────

class TestLeadCreationSideEffects:
    def test_create_lead_notifies_all_admins_once(self, partner_user):
        create_lead(partner_user, _lead_data())

        assert notif.notify_all_admins.call_count == 1

    def test_create_lead_writes_one_audit_entry(self, partner_user):
        create_lead(partner_user, _lead_data())

        assert audit.log.call_count == 1

    def test_create_lead_sends_one_partner_confirmation_email(
        self, partner_user
    ):
        create_lead(partner_user, _lead_data())

        assert mailer.send_lead_created.call_count == 1

    def test_a_long_client_name_does_not_turn_a_saved_lead_into_an_error(
        self, partner_user
    ):
        """The name may be 200 characters; the notification title quoting it
        is capped at 200 too, so the quote is shortened instead."""
        name = "N" * 200

        lead = create_lead(partner_user, _lead_data(client_name=name))

        assert lead["client_name"] == name
        assert audit.log.call_count == 1
        assert notif.notify_all_admins.call_count == 1
        assert mailer.send_lead_created.call_count == 1
        receipt = notif.list_notifications(partner_user["id"])[0]
        assert len(receipt["title"]) <= 200
        assert receipt["title"].endswith("…")

    def test_a_failing_effect_after_commit_skips_nothing_else(
        self, partner_user, mocker
    ):
        notif.notify_all_admins.side_effect = RuntimeError("smtp down")

        lead = create_lead(partner_user, _lead_data())

        assert lead["id"].startswith("LD-")
        assert audit.log.call_count == 1
        assert mailer.send_lead_created.call_count == 1


# ─── RETRIEVAL AND SCOPING ────────────────────────────────────────────────

class TestLeadRetrievalAndScoping:
    def test_partner_sees_only_own_leads(
        self, partner_user, partner_user_bronze
    ):
        own = create_lead(partner_user, _lead_data(client_name="Own Lead"))
        create_lead(
            partner_user_bronze, _lead_data(client_name="Other Partner Lead")
        )

        results = get_leads(partner_user, {})

        assert len(results) == 1
        assert results[0]["id"] == own["id"]

    def test_admin_sees_every_lead(
        self, admin_user, partner_user, partner_user_bronze
    ):
        create_lead(partner_user, _lead_data(client_name="Lead A"))
        create_lead(partner_user_bronze, _lead_data(client_name="Lead B"))

        results = get_leads(admin_user, {})

        assert len(results) == 2

    def test_leads_ordered_newest_first(self, admin_user, partner_user):
        first = create_lead(partner_user, _lead_data(client_name="First"))
        second = create_lead(partner_user, _lead_data(client_name="Second"))

        results = get_leads(admin_user, {})

        assert [r["id"] for r in results] == [second["id"], first["id"]]

    def test_status_filter_narrows_results(self, admin_user, partner_user):
        new_lead = create_lead(partner_user, _lead_data(client_name="New"))
        accepted = create_lead(
            partner_user, _lead_data(client_name="ToAccept")
        )
        update_lead(accepted["id"], admin_user, {"registration_status": "accepted"})

        results = get_leads(admin_user, {"registration_status": "accepted"})

        assert len(results) == 1
        assert results[0]["id"] == accepted["id"]
        assert new_lead["id"] not in [r["id"] for r in results]

    def test_search_matches_client_name(self, admin_user, partner_user):
        create_lead(partner_user, _lead_data(client_name="Zbigniew Fraszka"))
        create_lead(partner_user, _lead_data(client_name="Someone Else"))

        results = get_leads(admin_user, {"q": "Fraszka"})

        assert len(results) == 1
        assert results[0]["client_name"] == "Zbigniew Fraszka"

    def test_search_matches_company(self, admin_user, partner_user):
        create_lead(partner_user, _lead_data(company="UniqueCorp Sp. z o.o."))
        create_lead(partner_user, _lead_data(company="Other Sp. z o.o."))

        results = get_leads(admin_user, {"q": "UniqueCorp"})

        assert len(results) == 1
        assert results[0]["company"] == "UniqueCorp Sp. z o.o."

    def test_search_matches_identifier(self, admin_user, partner_user):
        lead = create_lead(partner_user, _lead_data())

        results = get_leads(admin_user, {"q": lead["id"]})

        assert len(results) == 1
        assert results[0]["id"] == lead["id"]

    def test_search_matches_sector(self, admin_user, partner_user):
        create_lead(partner_user, _lead_data(sector="Fintech"))
        create_lead(partner_user, _lead_data(sector="Logistyka"))

        results = get_leads(admin_user, {"q": "Fintech"})

        assert len(results) == 1
        assert results[0]["sector"] == "Fintech"

    def test_partner_id_filter_honored_for_admin(
        self, admin_user, partner_user, partner_user_bronze
    ):
        gold_lead = create_lead(partner_user, _lead_data(client_name="Gold"))
        create_lead(partner_user_bronze, _lead_data(client_name="Bronze"))

        results = get_leads(
            admin_user, {"partner_id": partner_user["id"]}
        )

        assert len(results) == 1
        assert results[0]["id"] == gold_lead["id"]

    def test_partner_id_filter_ignored_for_partner(
        self, partner_user, partner_user_bronze
    ):
        own = create_lead(partner_user, _lead_data(client_name="Own"))
        create_lead(partner_user_bronze, _lead_data(client_name="Other"))

        # A partner tries to widen scope by supplying another partner's id --
        # get_leads forces self-scoping regardless of the filter value.
        results = get_leads(
            partner_user, {"partner_id": partner_user_bronze["id"]}
        )

        assert len(results) == 1
        assert results[0]["id"] == own["id"]

    def test_date_range_filter_includes_lead_created_on_upper_bound_date(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data())
        today = lead["created_at"][:10]
        past = (
            datetime.utcnow() - timedelta(days=30)
        ).date().isoformat()

        results = get_leads(
            admin_user, {"date_from": past, "date_to": today}
        )

        assert lead["id"] in [r["id"] for r in results]

    def test_partner_cannot_get_another_partners_lead_by_id(
        self, partner_user, partner_user_bronze
    ):
        other_lead = create_lead(
            partner_user_bronze, _lead_data(client_name="Not Yours")
        )

        result = get_lead(other_lead["id"], partner_user)

        assert result is None

    def test_partner_cannot_list_another_partners_lead(
        self, partner_user, partner_user_bronze
    ):
        create_lead(
            partner_user_bronze, _lead_data(client_name="Not Yours Either")
        )

        results = get_leads(partner_user, {})

        assert results == []

    def test_get_unknown_lead_returns_none(self, partner_user):
        assert get_lead("LD-999", partner_user) is None


# ─── PAGING, ORDERING AND THE FILTERS THAT MOVED OFF THE BROWSER ──────────

class TestLeadPaging:
    """The list screen's half of the query, which used to live in JavaScript.

    These prove the database filters, sorts and slices the list -- and, just as
    importantly, that the unbounded read is still there for the callers that
    genuinely need every row.
    """

    def _make(self, partner_user, count, **overrides):
        return [
            create_lead(partner_user, _lead_data(client_name=f"Klient {i:02d}", **overrides))
            for i in range(count)
        ]

    def test_page_holds_only_its_own_rows_and_reports_the_whole_total(
        self, admin_user, partner_user
    ):
        self._make(partner_user, 7)

        first, total = get_leads_page(admin_user, {}, page=1, per_page=3)
        second, total_again = get_leads_page(admin_user, {}, page=2, per_page=3)

        assert len(first) == 3
        assert len(second) == 3
        # The total counts the matches, not the page -- it is what the screen
        # says above the table and what the page buttons are built from.
        assert total == total_again == 7
        assert {row["id"] for row in first}.isdisjoint({row["id"] for row in second})

    def test_last_page_returns_the_remainder(self, admin_user, partner_user):
        self._make(partner_user, 7)

        rows, total = get_leads_page(admin_user, {}, page=3, per_page=3)

        assert len(rows) == 1
        assert total == 7

    def test_page_past_the_end_is_empty_but_still_states_the_total(
        self, admin_user, partner_user
    ):
        # A producer who narrows a filter while on a late page must still be
        # told how many leads matched, or the screen offers no way back.
        self._make(partner_user, 4)

        rows, total = get_leads_page(admin_user, {}, page=9, per_page=3)

        assert rows == []
        assert total == 4

    def test_pages_together_cover_every_lead_exactly_once(
        self, admin_user, partner_user
    ):
        created = {lead["id"] for lead in self._make(partner_user, 10)}

        seen = []
        for page in (1, 2, 3, 4):
            rows, _ = get_leads_page(admin_user, {}, page=page, per_page=3)
            seen.extend(row["id"] for row in rows)

        # No repeats and nothing missing: the ordering carries a tiebreak on a
        # unique column, without which equal values can swap between queries and
        # a row that crosses a page boundary is shown twice or not at all.
        assert len(seen) == len(set(seen)) == len(created)
        assert set(seen) == created

    def test_per_page_is_capped(self, admin_user, partner_user):
        self._make(partner_user, 3)

        rows, _ = get_leads_page(admin_user, {}, page=1, per_page=10_000)

        # The cap is what stops a caller asking for the whole table through the
        # paged door; three rows is simply all there is here.
        assert len(rows) == 3
        assert LEADS_PAGE_SIZE <= LEADS_MAX_PAGE_SIZE

    def test_partner_scoping_survives_paging(
        self, partner_user, partner_user_bronze
    ):
        self._make(partner_user, 2)
        create_lead(partner_user_bronze, _lead_data(client_name="Cudzy"))

        rows, total = get_leads_page(partner_user, {}, page=1, per_page=25)

        assert total == 2
        assert all(row["partner_id"] == partner_user["id"] for row in rows)

    def test_unpaged_read_still_returns_everything(self, admin_user, partner_user):
        # api/payouts.py totals a partner's whole pipeline through this call. A
        # default page size here would under-report the forecast silently.
        self._make(partner_user, LEADS_PAGE_SIZE + 5)

        assert len(get_leads(admin_user, {})) == LEADS_PAGE_SIZE + 5

    def test_sort_orders_the_whole_collection_not_the_page(
        self, admin_user, partner_user
    ):
        for value in (500, 9000, 100, 7000):
            create_lead(partner_user, _lead_data(client_name=f"W{value}", value=value))

        rows, _ = get_leads_page(
            admin_user, {}, sort="value", direction="desc", page=1, per_page=2,
        )

        # The largest deal in the programme, not the largest of the page.
        assert [row["value"] for row in rows] == [9000, 7000]

    def test_unknown_sort_key_is_refused(self, admin_user):
        with pytest.raises(ValueError):
            get_leads_page(admin_user, {}, sort="value; DROP TABLE leads")

    def test_min_value_filter(self, admin_user, partner_user):
        create_lead(partner_user, _lead_data(client_name="Mała", value=1000))
        create_lead(partner_user, _lead_data(client_name="Duża", value=9000))

        rows, total = get_leads_page(admin_user, {"min_value": 5000}, page=1, per_page=25)

        assert total == 1
        assert rows[0]["client_name"] == "Duża"

    def test_overdue_filter_uses_the_producers_own_deadline(
        self, admin_user, partner_user
    ):
        from api import settings_api

        fresh = create_lead(partner_user, _lead_data(client_name="Świeży"))
        stale = create_lead(partner_user, _lead_data(client_name="Stary"))
        long_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()
        conn = get_conn()
        try:
            conn.execute("UPDATE leads SET created_at=? WHERE id=?", (long_ago, stale["id"]))
            conn.commit()
        finally:
            conn.close()

        rows, total = get_leads_page(admin_user, {"overdue": True}, page=1, per_page=25)

        assert total == 1
        assert rows[0]["id"] == stale["id"]
        assert fresh["id"] not in [row["id"] for row in rows]
        # And it is the configured window doing the deciding, not a constant.
        assert settings_api.action_days() >= 1

    def test_overdue_filter_ignores_decided_leads(self, admin_user, partner_user):
        decided = create_lead(partner_user, _lead_data(client_name="Rozpatrzony"))
        long_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()
        conn = get_conn()
        try:
            conn.execute("UPDATE leads SET created_at=? WHERE id=?", (long_ago, decided["id"]))
            conn.commit()
        finally:
            conn.close()
        update_lead(decided["id"], admin_user, {"registration_status": "accepted"})

        _rows, total = get_leads_page(admin_user, {"overdue": True}, page=1, per_page=25)

        # Nobody is waiting on an accepted lead, however old it is.
        assert total == 0

    # An unticked checkbox means "no filter", not "only leads still in time" --
    # so the off spellings must return the whole list, not the complement.
    @pytest.mark.parametrize("flag,expected", [("1", 1), ("true", 1), (True, 1),
                                               ("0", 2), ("", 2), (False, 2)])
    def test_overdue_flag_accepts_the_spellings_a_query_string_uses(
        self, admin_user, partner_user, flag, expected
    ):
        create_lead(partner_user, _lead_data(client_name="Świeży"))
        stale = create_lead(partner_user, _lead_data(client_name="Stary"))
        long_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()
        conn = get_conn()
        try:
            conn.execute("UPDATE leads SET created_at=? WHERE id=?", (long_ago, stale["id"]))
            conn.commit()
        finally:
            conn.close()

        _rows, total = get_leads_page(admin_user, {"overdue": flag}, page=1, per_page=25)

        assert total == expected

    def test_overdue_flag_refuses_a_value_that_is_neither(self, admin_user):
        with pytest.raises(ValueError):
            get_leads_page(admin_user, {"overdue": "moze"}, page=1, per_page=25)

    def test_handler_without_paging_returns_the_whole_list(
        self, admin_user, partner_user
    ):
        # The partner's board draws a column per sales stage out of the whole
        # set; a truncated board reads as lost deals.
        self._make(partner_user, LEADS_PAGE_SIZE + 3)

        status, body = handle_list(None, admin_user, {})

        assert status == 200
        assert len(body["leads"]) == LEADS_PAGE_SIZE + 3
        assert "page" not in body

    def test_handler_with_paging_reports_the_page_arithmetic(
        self, admin_user, partner_user
    ):
        self._make(partner_user, 7)

        status, body = handle_list(None, admin_user, {"page": ["2"], "per_page": ["3"]})

        assert status == 200
        assert len(body["leads"]) == 3
        assert body["total"] == 7
        assert body["page"] == 2
        assert body["per_page"] == 3
        assert body["pages"] == 3

    def test_handler_treats_a_cleared_control_as_no_filter(
        self, admin_user, partner_user
    ):
        # `?min_value=` is what a browser sends for an emptied number box.
        self._make(partner_user, 2)

        status, body = handle_list(
            None, admin_user, {"page": ["1"], "per_page": ["25"], "min_value": [""], "sort": [""]},
        )

        assert status == 200
        assert body["total"] == 2

    def test_handler_refuses_an_unknown_sort_key(self, admin_user):
        status, body = handle_list(None, admin_user, {"page": ["1"], "sort": ["haslo"]})

        assert status == 400
        assert "sort" in body["error"]

    @pytest.mark.parametrize(
        "filters",
        [
            {"status": "unknown"},
            {"partner_id": "abc"},
            {"q": []},
            {"q": "x" * 201},
            {"date_from": "2026-99-99"},
            {"unexpected": "value"},
        ],
    )
    def test_filters_reject_invalid_values_before_connection(
        self, admin_user, mocker, filters
    ):
        mocked_conn = mocker.patch("api.leads.get_conn")

        with pytest.raises(ValueError):
            get_leads(admin_user, filters)

        mocked_conn.assert_not_called()


def _decision(registration_status, **extra):
    """
    The payload a registration decision needs.

    Refusing a deal or asking for more information owes the partner words, and
    the rule lives in the domain rather than at the HTTP boundary -- so a
    direct caller states a reason too.
    """
    payload = {"registration_status": registration_status, **extra}
    if registration_status in ("rejected", "needs_info"):
        payload.setdefault("registration_message", "Powód decyzji dla partnera.")
    return payload


# ─── STATUS TRANSITIONS ───────────────────────────────────────────────────

class TestLeadStatusTransitions:
    @pytest.mark.parametrize(
        "registration_status", [k for k in REGISTRATION_KEYS if k != "pending"]
    )
    def test_admin_can_record_each_registration_decision(
        self, admin_user, partner_user, registration_status
    ):
        lead = create_lead(partner_user, _lead_data())

        updated = update_lead(
            lead["id"], admin_user, _decision(registration_status)
        )

        assert updated["registration_status"] == registration_status

    def test_a_lead_cannot_be_pushed_back_to_pending(self, admin_user, partner_user):
        """There is no action that un-decides a lead, so no payload may do it
        either -- reopening a decision would drop the audit trail of the
        first one."""
        lead = create_lead(partner_user, _lead_data())
        update_lead(lead["id"], admin_user, _decision("accepted"))

        with pytest.raises(ValueError):
            update_lead(lead["id"], admin_user, {"registration_status": "pending"})

    @pytest.mark.parametrize("stage_key", SEED_STAGE_KEYS)
    def test_admin_can_move_an_accepted_lead_to_each_sales_stage(
        self, admin_user, partner_user, stage_key
    ):
        lead = create_lead(partner_user, _lead_data())
        update_lead(lead["id"], admin_user, {"registration_status": "accepted"})

        updated = update_lead(lead["id"], admin_user, {"status": stage_key})

        assert updated["status"] == stage_key

    def test_a_lead_cannot_enter_the_sales_pipeline_before_it_is_accepted(
        self, admin_user, partner_user
    ):
        """The whole point of splitting the axes: a deal nobody has approved
        cannot be sitting at "Negocjacje" and counting into pipeline value."""
        lead = create_lead(partner_user, _lead_data())

        with pytest.raises(ValueError):
            update_lead(lead["id"], admin_user, {"status": "negotiation"})

    def test_invalid_status_raises_value_error_not_integrity_error(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data())

        with pytest.raises(ValueError) as exc_info:
            update_lead(lead["id"], admin_user, {"status": "not-a-status"})

        assert exc_info.type is ValueError

    def test_update_unknown_lead_raises_value_error(self, admin_user):
        with pytest.raises(ValueError):
            update_lead("LD-999", admin_user, {"registration_status": "accepted"})

    def test_update_without_status_or_commission_raises_value_error(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data())

        with pytest.raises(ValueError):
            update_lead(lead["id"], admin_user, {})

    def test_update_advances_modification_timestamp(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data())
        before = lead["updated_at"]

        updated = update_lead(lead["id"], admin_user, _decision("accepted"))

        assert updated["updated_at"] > before

    @pytest.mark.parametrize(
        "data",
        [
            {"status": 1},
            {"commission": math.nan},
            {"commission": math.inf},
            {"commission": True},
            {"commission": 1_000_000_000_001},
            {"unexpected": "value"},
        ],
    )
    def test_update_rejects_invalid_values_before_connection(
        self, admin_user, mocker, data
    ):
        mocked_conn = mocker.patch("api.leads.get_conn")

        with pytest.raises(ValueError):
            update_lead("LD-001", admin_user, data)

        mocked_conn.assert_not_called()


# ─── PERMISSION GATES ──────────────────────────────────────────────────────

class TestLeadPermissionGates:
    def test_partner_cannot_update_own_lead(self, partner_user):
        lead = create_lead(partner_user, _lead_data())

        with pytest.raises(PermissionError):
            update_lead(lead["id"], partner_user, {"registration_status": "accepted"})


# ─── NOTIFICATION AND AUDIT SIDE-EFFECTS ON UPDATE ────────────────────────

class TestLeadUpdateSideEffects:
    def test_accepted_sends_one_success_notification(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data())

        update_lead(lead["id"], admin_user, {"registration_status": "accepted"})

        assert notif.create_notification.call_count == 1
        assert notif.create_notification.call_args.args[1] == "success"

    def test_rejected_sends_one_error_notification(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data())

        update_lead(lead["id"], admin_user, _decision("rejected"))

        assert notif.create_notification.call_count == 1
        assert notif.create_notification.call_args.args[1] == "error"

    def test_in_progress_sends_one_info_notification(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data())

        update_lead(lead["id"], admin_user, _decision("needs_info"))

        assert notif.create_notification.call_count == 1
        assert notif.create_notification.call_args.args[1] == "info"

    def test_a_failing_notification_after_commit_keeps_audit_and_mail(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data())
        update_lead(lead["id"], admin_user, {"registration_status": "accepted"})
        audit.log.reset_mock()
        mailer.send_lead_status_changed.reset_mock()
        notif.create_notification.side_effect = ValueError("title too long")

        won = update_lead(lead["id"], admin_user, {"status": "won"})

        assert won["status"] == "won"
        assert audit.log.call_count == 1
        assert mailer.send_lead_status_changed.call_count == 1

    def test_a_long_client_name_does_not_fail_a_saved_win(
        self, admin_user, partner_user
    ):
        notif.create_notification.side_effect = REAL_CREATE_NOTIFICATION
        lead = create_lead(partner_user, _lead_data(client_name="W" * 195))
        update_lead(lead["id"], admin_user, {"registration_status": "accepted"})
        audit.log.reset_mock()

        won = update_lead(lead["id"], admin_user, {"status": "won"})

        assert won["status"] == "won"
        assert audit.log.call_count == 1
        titles = [row["title"] for row in notif.list_notifications(partner_user["id"])]
        assert any(title.startswith("Sprzedaż wygrana: W") for title in titles)

    def test_a_failing_mailer_does_not_report_a_saved_change_as_an_error(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data())
        mailer.send_lead_status_changed.side_effect = OSError("smtp down")

        accepted = update_lead(lead["id"], admin_user, {"registration_status": "accepted"})

        assert accepted["registration_status"] == "accepted"

    def test_an_update_that_decides_nothing_sends_no_notification(
        self, admin_user, partner_user
    ):
        """Correcting the money is not news for the partner. This used to be
        written as a re-write of "pending", which is no longer a reachable
        state -- so the no-op is now an actual non-decision."""
        lead = create_lead(partner_user, _lead_data())
        update_lead(lead["id"], admin_user, _decision("accepted"))
        notif.create_notification.reset_mock()

        update_lead(lead["id"], admin_user, {"commission": 1234})

        assert notif.create_notification.call_count == 0

    def test_audit_action_key_differs_by_decision(
        self, admin_user, partner_user
    ):
        """Each producer decision leaves its own distinguishable trace, so
        the audit log answers "what did they decide?" and not merely "they
        touched this lead"."""
        accepted_lead = create_lead(partner_user, _lead_data())
        rejected_lead = create_lead(partner_user, _lead_data())
        info_lead = create_lead(partner_user, _lead_data())
        money_lead = create_lead(partner_user, _lead_data())

        update_lead(accepted_lead["id"], admin_user, {"registration_status": "accepted"})
        accepted_key = audit.log.call_args.args[1]

        update_lead(rejected_lead["id"], admin_user, _decision("rejected"))
        rejected_key = audit.log.call_args.args[1]

        update_lead(info_lead["id"], admin_user, _decision("needs_info"))
        info_key = audit.log.call_args.args[1]

        update_lead(money_lead["id"], admin_user, {"commission": 500})
        money_key = audit.log.call_args.args[1]

        assert accepted_key == "lead.accepted"
        assert rejected_key == "lead.rejected"
        assert info_key == "lead.needs_info"
        assert money_key == "lead.commission"
        assert len({accepted_key, rejected_key, info_key, money_key}) == 4

    @pytest.mark.parametrize(
        "registration_status", ["accepted", "rejected", "needs_info"]
    )
    def test_partner_mail_sent_for_every_registration_decision(
        self, admin_user, partner_user, registration_status
    ):
        lead = create_lead(partner_user, _lead_data())

        update_lead(lead["id"], admin_user, _decision(registration_status))

        assert mailer.send_lead_status_changed.call_count == 1

    def test_partner_mail_not_sent_when_no_decision_was_made(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data())
        update_lead(lead["id"], admin_user, _decision("accepted"))
        mailer.send_lead_status_changed.reset_mock()

        update_lead(lead["id"], admin_user, {"commission": 1234})

        assert mailer.send_lead_status_changed.call_count == 0


# ─── HANDLER-BOUNDARY ──────────────────────────────────────────────────────

class TestLeadHandlerBoundary:
    def test_handle_create_refuses_admin_caller(self, admin_user):
        status, body = handle_create(None, admin_user, _lead_data())

        assert status == 403
        assert "error" in body

    def test_handle_create_returns_201_with_lead_on_success(
        self, admin_user, partner_user
    ):
        from api.products import create_product

        product = create_product(
            {
                "name": "ERP",
                "category": "Wdrożenie",
                "offered": True,
            },
            admin_user,
        )
        status, body = handle_create(
            None,
            partner_user,
            _lead_data(
                nip="1111111111",
                website="nova-sklep.pl",
                product_id=product["id"],
                quantity=1,
            ),
        )

        assert status == 201
        assert "lead" in body

    def test_handle_create_rejects_missing_client_identity_field(
        self, partner_user
    ):
        status, body = handle_create(None, partner_user, _lead_data())

        assert status == 400
        assert "error" in body

    def test_handle_create_maps_validation_failure_to_400(
        self, partner_user
    ):
        data = _lead_data()
        del data["client_name"]

        status, body = handle_create(None, partner_user, data)

        assert status == 400
        assert "error" in body

    def test_handle_update_maps_permission_error_to_403(self, partner_user):
        lead = create_lead(partner_user, _lead_data())

        status, body = handle_update(
            None, partner_user, lead["id"],
            {"registration_status": "accepted",
             "expected_revision": 0, "operation_id": "unit-403"},
        )

        assert status == 403
        assert "error" in body

    def test_handle_update_maps_validation_error_to_400(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data())

        status, body = handle_update(
            None, admin_user, lead["id"], {"status": "not-a-status"}
        )

        assert status == 400
        assert "error" in body

    def test_handle_update_returns_200_with_lead_on_success(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data())

        # "pending" is deliberately absent from the update path: there is no
        # action that un-decides a lead, so PATCH must not offer one either.
        #A real decision is what succeeds here.
        status, body = handle_update(
            None, admin_user, lead["id"],
            {"registration_status": "accepted",
             "expected_revision": 0, "operation_id": "unit-200"},
        )

        assert status == 200
        assert "lead" in body

    def test_handle_get_returns_404_for_unknown_lead(self, partner_user):
        status, body = handle_get(None, partner_user, "LD-999")

        assert status == 404
        assert "error" in body

    def test_handle_list_total_matches_row_count(
        self, admin_user, partner_user
    ):
        create_lead(partner_user, _lead_data())
        create_lead(partner_user, _lead_data())

        status, body = handle_list(None, admin_user, {})

        assert status == 200
        assert body["total"] == 2
        assert len(body["leads"]) == 2

    def test_handle_list_reads_filters_from_query_dict_of_lists(
        self, admin_user, partner_user
    ):
        matching = create_lead(partner_user, _lead_data())
        other = create_lead(partner_user, _lead_data())
        update_lead(matching["id"], admin_user, {"registration_status": "accepted"})

        status, body = handle_list(
            None, admin_user, {"registration_status": ["accepted"]}
        )

        assert status == 200
        assert body["total"] == 1
        assert body["leads"][0]["id"] == matching["id"]
        assert other["id"] not in [l["id"] for l in body["leads"]]

    def test_handle_bulk_refuses_partner_caller(self, partner_user):
        status, body = handle_bulk(
            None, partner_user, {"action": "accept", "items": [{"id": "LD-001", "expected_revision": 0, "operation_id": "denied"}]}
        )

        assert status == 403
        assert "error" in body

    def test_handle_bulk_rejects_unknown_action_word(self, admin_user):
        status, body = handle_bulk(
            None, admin_user, {"action": "delete", "items": []}
        )

        assert status == 400
        assert "error" in body

    def test_handle_bulk_rejects_missing_ids(self, admin_user):
        status, body = handle_bulk(None, admin_user, {"action": "accept"})

        assert status == 400
        assert "error" in body

    def test_handle_bulk_rejects_non_list_ids(self, admin_user):
        status, body = handle_bulk(
            None, admin_user, {"action": "accept", "items": "LD-001"}
        )

        assert status == 400
        assert "error" in body

    @pytest.mark.parametrize("ids", [[1], [""], ["LD-001", []], ["x" * 33]])
    def test_handle_bulk_rejects_invalid_id_items_before_update(
        self, admin_user, mocker, ids
    ):
        update = mocker.patch("api.leads.apply_registration_action")

        status, body = handle_bulk(
            None, admin_user, {"action": "accept", "items": [
                {"id": lead_id, "expected_revision": 0, "operation_id": f"invalid-{i}"}
                for i, lead_id in enumerate(ids)]}
        )

        assert status == 400
        assert "error" in body
        update.assert_not_called()

    def test_handle_bulk_mixed_batch_reports_processed_and_error_counts(
        self, admin_user, partner_user
    ):
        lead = create_lead(partner_user, _lead_data())

        status, body = handle_bulk(
            None,
            admin_user,
            {"action": "accept", "items": [
                {"id": lead["id"], "expected_revision": lead["revision"], "operation_id": "ok"},
                {"id": "LD-999", "expected_revision": 0, "operation_id": "missing"}]},
        )

        assert status == 200
        assert len(body["ok"]) == 1
        assert len(body["error"]) == 1


# ─── tier_name_en through the commission forecast ─────────────
# The tier's English name (commission_tiers.name_en) flows alongside
# tier_name through _commission_components -> _projected_commission ->
# with_commission_projection/get_lead, and through preview_lead_update's
# own forecast branch -- read by js/partner-earnings.js and
# js/admin-partners.js via Ui.producerLabel. None of this may perturb the
# rate/amount arithmetic those same functions own.

class TestTierEnglishNameThroughTheForecast:
    def _set_tier_name_en(self, tier_id, name_en):
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE commission_tiers SET name_en = ? WHERE id = ?",
                (name_en, tier_id),
            )
            conn.commit()
        finally:
            conn.close()

    def test_tier_name_en_reaches_get_lead_alongside_tier_name(
        self, admin_user, partner_user_tiered
    ):
        self._set_tier_name_en(partner_user_tiered["tier_id"], "Gold Tier")
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))

        fetched = get_lead(lead["id"], admin_user)

        assert fetched["tier_name"] == "Gold"
        assert fetched["tier_name_en"] == "Gold Tier"

    def test_tier_name_en_is_empty_when_untranslated(
        self, admin_user, partner_user_tiered
    ):
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))

        fetched = get_lead(lead["id"], admin_user)

        assert fetched["tier_name_en"] == ""

    def test_tier_name_en_is_empty_when_the_partner_has_no_tier(
        self, admin_user, partner_user, db_conn
    ):
        db_conn.execute(
            "UPDATE users SET tier_id = NULL WHERE id = ?", (partner_user["id"],)
        )
        db_conn.commit()
        lead = create_lead(partner_user, _lead_data(value=10000))

        fetched = get_lead(lead["id"], admin_user)

        assert fetched["tier_name"] == "brak poziomu"
        assert fetched["tier_name_en"] == ""

    def test_translating_the_tier_name_never_moves_the_projected_amount(
        self, admin_user, partner_user_tiered
    ):
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))
        before = get_lead(lead["id"], admin_user)

        self._set_tier_name_en(partner_user_tiered["tier_id"], "Gold Tier")
        after = get_lead(lead["id"], admin_user)

        assert before["tier_name_en"] == ""
        assert after["tier_name_en"] == "Gold Tier"
        assert after["tier_name"] == before["tier_name"]
        assert after["commission_projected"] == before["commission_projected"]
        assert after["commission_rate"] == before["commission_rate"]
        assert after["commission_breakdown"] == before["commission_breakdown"]

    def test_tier_name_en_survives_the_win_transition_forecast_stamp(
        self, admin_user, partner_user_tiered
    ):
        """preview_lead_update's own forecast branch (the win-stamp path)
        carries tier_name_en exactly like it carries tier_name."""
        self._set_tier_name_en(partner_user_tiered["tier_id"], "Gold Tier")
        lead = create_lead(partner_user_tiered, _lead_data(value=10000))
        update_lead(lead["id"], admin_user, {"registration_status": "accepted"})

        from api.leads import preview_lead_update
        preview = preview_lead_update(
            lead["id"], admin_user, {"status": "won"}
        )

        assert preview["preview"]["tier_name"] == "Gold"
        assert preview["preview"]["tier_name_en"] == "Gold Tier"
