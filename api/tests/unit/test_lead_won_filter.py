"""won_from / won_to lead filters in Europe/Warsaw days.

"How many new sales came through partners today?" is a question about the
day a deal was WON, in Polish time. won_at is stored as UTC text
(datetime.utcnow().isoformat()), so a Warsaw calendar day becomes a UTC
[start, end) range before it reaches SQL.
"""
import uuid
from datetime import datetime

import pytest

from api import leads
from api.db import get_conn


def _lead(partner):
    suffix = uuid.uuid4().hex[:8]
    return leads.create_lead(partner, {
        "client_name": "Test Client",
        "client_email": f"client-{suffix}@test.pl",
        "company": f"Test Co {suffix}",
        "value": 10000,
    })


def _won(partner, won_at):
    lead = _lead(partner)
    with get_conn() as conn:
        conn.execute("UPDATE leads SET won_at = ? WHERE id = ?", (won_at, lead["id"]))
    return lead["id"]


def _ids(user, filters):
    rows, total = leads.get_leads_page(user, filters, per_page=100)
    assert total == len(rows)
    return {row["id"] for row in rows}


# ─── Day bounds ─────────────────────────────────────────────────────────────

def test_summer_day_is_utc_plus_two():
    assert leads.warsaw_day_bounds_utc("2026-10-18") == ("2026-10-17T22:00:00",
                                                         "2026-10-18T22:00:00")


def test_winter_day_is_utc_plus_one():
    assert leads.warsaw_day_bounds_utc("2026-11-02") == ("2026-11-01T23:00:00",
                                                         "2026-11-02T23:00:00")


def test_the_dst_change_day_spans_twenty_five_hours():
    start, end = leads.warsaw_day_bounds_utc("2026-10-25")
    assert (start, end) == ("2026-10-24T22:00:00", "2026-10-25T23:00:00")
    span = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    assert span.total_seconds() == 25 * 3600


@pytest.mark.parametrize("bad", ["2026-13-01", "18.10.2026", "", None, 20261018])
def test_invalid_day_is_refused(bad):
    with pytest.raises(ValueError):
        leads.warsaw_day_bounds_utc(bad)


# ─── Filters ────────────────────────────────────────────────────────────────

def test_win_just_after_warsaw_midnight_belongs_to_that_warsaw_day(admin_user, partner_user):
    lead_id = _won(partner_user, "2026-10-17T22:30:00")  # 00:30 on 18.10 in Warsaw

    assert lead_id in _ids(admin_user, {"won_from": "2026-10-18", "won_to": "2026-10-18"})
    assert lead_id not in _ids(admin_user, {"won_from": "2026-10-17", "won_to": "2026-10-17"})


def test_end_bound_is_exclusive(admin_user, partner_user):
    last_second = _won(partner_user, "2026-10-18T21:59:59.999999")
    next_day = _won(partner_user, "2026-10-18T22:00:00")

    found = _ids(admin_user, {"won_from": "2026-10-18", "won_to": "2026-10-18"})
    assert last_second in found
    assert next_day not in found


def test_legacy_space_separated_won_at_is_compared_as_the_same_instant(admin_user, partner_user):
    lead_id = _won(partner_user, "2026-10-17 23:30:00")  # imported text, 01:30 on 18.10 Warsaw

    assert lead_id in _ids(admin_user, {"won_from": "2026-10-18", "won_to": "2026-10-18"})


def test_open_ranges(admin_user, partner_user):
    early = _won(partner_user, "2026-09-01T10:00:00")
    late = _won(partner_user, "2026-10-20T10:00:00")

    assert _ids(admin_user, {"won_from": "2026-10-01"}) == {late}
    assert _ids(admin_user, {"won_to": "2026-10-01"}) == {early}


def test_leads_never_won_never_match(admin_user, partner_user):
    _lead(partner_user)  # won_at NULL

    assert _ids(admin_user, {"won_to": "2099-12-31"}) == set()
    assert _ids(admin_user, {"won_from": "2000-01-01"}) == set()


def test_won_to_before_won_from_is_refused(admin_user):
    with pytest.raises(ValueError):
        leads.get_leads_page(admin_user, {"won_from": "2026-10-18", "won_to": "2026-10-17"})


def test_invalid_won_date_is_refused(admin_user):
    with pytest.raises(ValueError):
        leads.get_leads_page(admin_user, {"won_from": "2026-13-01"})


def test_partner_stays_row_scoped(partner_user, partner_user_bronze):
    own = _won(partner_user, "2026-10-18T08:00:00")
    _won(partner_user_bronze, "2026-10-18T08:00:00")

    assert _ids(partner_user, {"won_from": "2026-10-18", "won_to": "2026-10-18"}) == {own}
